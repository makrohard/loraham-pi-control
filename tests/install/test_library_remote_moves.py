"""C3 (DEC-W41): a pinned library whose manifest REMOTE moves (libch341: pine64's repository -> makrohard's fork,
daemon 1.4.0's lock) is followed by `lhpc update --source pinned`. Every box cloned the library from pine64; the
fork's pin is not in pine64's repository, so a box that cannot follow the remote stalls until someone removes
the tree by hand.

Through the public path, cli.main(["update", ...]). The two remotes are local bare repositories behind git's own
`insteadOf` rewrite (the remote policy admits https:// only, so the URLs stay the manifest's).
  (1) an lhpc-adopted tree from pine64 ends on origin = the fork, HEAD = the fork's pin, the record's remote the fork;
  (i)-(iii) stay refused with the tree untouched: an unregistered tree with a foreign origin, a local.toml remote
  override that differs, a recorded commit the tree no longer matches."""
import pytest

from lhpc.adapters.cli import main as cli
from lhpc.core import source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes import backends
from lhpc.core.probes.backends import RealSystem
from lhpc.core.services import ControllerService

PINE = "https://github.com/pine64/libch341-spi-userspace"
FORK = "https://github.com/makrohard/libch341-spi-userspace"
OTHER = "https://example.invalid/someone/libch341-spi-userspace"
REL = "src/libch341-spi-userspace"


def _manifest(path, remote, pin, comp_pin):
    path.write_text(
        '[[stack]]\nid = "s"\nname = "s"\nmain = "c"\n'
        '[[stack.component]]\nid = "c"\nname = "c"\nkind = "service"\n'
        'run_argv = ["true"]\nreadiness = "process"\n'
        f'[stack.component.source]\npath = "src/comp"\nlocal_dir = "comp"\npin_commit = "{comp_pin}"\n'
        '[[stack.component]]\nid = "libch341"\nname = "libch341-spi-userspace"\nkind = "library"\n'
        'tx_capable = false\n'                     # not optional: whether it is selected is not the question here
        f'[stack.component.source]\npath = "{REL}"\npin_commit = "{pin}"\nremote = "{remote}"\nbranch = "main"\n')


@pytest.fixture
def box(tmp_path, git, make_repo, monkeypatch):
    """Two remotes (pine64 with X, the fork with X then Y), a runtime with the service's local source, and
    `run(remote, pin)` = `lhpc update s --source pinned --yes` with the manifest naming that remote and pin."""
    pine_work = tmp_path / "pine-work"
    x = make_repo(pine_work, {"libpinedio-usb.c": "x\n"})
    pine = tmp_path / "pine.git"
    git(tmp_path, "clone", "-q", "--bare", str(pine_work), str(pine))
    fork_work = tmp_path / "fork-work"
    git(tmp_path, "clone", "-q", str(pine), str(fork_work))
    (fork_work / "libpinedio-usb.c").write_text("y\n")
    git(fork_work, "commit", "-qam", "the fork's fix")
    y = git(fork_work, "rev-parse", "HEAD")
    fork = tmp_path / "fork.git"
    git(tmp_path, "clone", "-q", "--bare", str(fork_work), str(fork))
    cfg = tmp_path / "gitconfig"
    cfg.write_text(f'[url "file://{pine}"]\n\tinsteadOf = {PINE}\n[url "file://{fork}"]\n\tinsteadOf = {FORK}\n\tinsteadOf = {OTHER}\n'
                   '[protocol "file"]\n\tallow = always\n')
    monkeypatch.setitem(backends._FIXED_ENV, "GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setitem(backends._FIXED_ENV, "GIT_CONFIG_SYSTEM", "/dev/null")
    rt = tmp_path / "rt"
    comp_head = make_repo(rt / "local" / "comp")
    (rt / "config").mkdir(parents=True)
    local_toml = rt / "config" / "local.toml"
    local_toml.write_text(f'[install]\nadopt_search_root = "{rt / "local"}"\n')
    manifest = tmp_path / "m.toml"

    def run(remote, pin):
        _manifest(manifest, remote, pin, comp_head)
        monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: ControllerService(
            manifest_path=manifest, system=RealSystem(), paths=Paths(runtime_root=rt)))
        return cli.main(["update", "s", "--source", "pinned", "--yes"])

    class Box:
        pass
    b = Box()
    b.x, b.y, b.rt, b.dest, b.run, b.local_toml = x, y, rt, rt / REL, run, local_toml
    b.record = lambda: source_registry.read_record(Paths(runtime_root=rt), REL)
    return b


def test_an_adopted_pine64_tree_follows_the_remote_to_the_fork(box, git):
    assert box.run(PINE, box.x) == 0
    assert git(box.dest, "remote", "get-url", "origin") == PINE and git(box.dest, "rev-parse", "HEAD") == box.x
    assert box.run(FORK, box.y) == 0
    assert git(box.dest, "remote", "get-url", "origin") == FORK
    assert git(box.dest, "rev-parse", "HEAD") == box.y
    rec = box.record()
    assert rec is not None and rec.remote == FORK and rec.resolved_commit == box.y


def test_an_unregistered_tree_with_a_foreign_origin_stays_refused(box, git, make_repo):
    make_repo(box.dest)                                           # not lhpc's: no record
    git(box.dest, "remote", "add", "origin", PINE)
    before = git(box.dest, "rev-parse", "HEAD")
    assert box.run(FORK, box.y) != 0
    assert git(box.dest, "rev-parse", "HEAD") == before and git(box.dest, "remote", "get-url", "origin") == PINE


def test_a_differing_local_remote_override_stays_refused(box, git):
    # OTHER is reachable (the fork's repository behind it): the refusal is the override's, not a failed clone.
    assert box.run(PINE, box.x) == 0
    box.local_toml.write_text(box.local_toml.read_text() + f'[remotes]\nlibch341 = "{OTHER}"\n')
    assert box.run(FORK, box.y) != 0
    assert git(box.dest, "rev-parse", "HEAD") == box.x and git(box.dest, "remote", "get-url", "origin") == PINE


def test_a_tree_moved_off_its_recorded_commit_stays_refused(box, git):
    assert box.run(PINE, box.x) == 0
    (box.dest / "libpinedio-usb.c").write_text("hand edit\n")
    git(box.dest, "commit", "-qam", "moved by hand")              # clean, but not the recorded commit
    moved = git(box.dest, "rev-parse", "HEAD")
    assert box.run(FORK, box.y) != 0
    assert git(box.dest, "rev-parse", "HEAD") == moved and git(box.dest, "remote", "get-url", "origin") == PINE


@pytest.mark.parametrize("field", ["remote", "resolved_commit"])
def test_a_record_without_its_remote_or_commit_does_not_follow_the_remote(box, git, field):
    # A record naming no remote (a local adoption) or no commit proves no clone of the old remote: refused.
    import dataclasses
    assert box.run(PINE, box.x) == 0
    assert source_registry.write_record(Paths(runtime_root=box.rt), dataclasses.replace(box.record(), **{field: ""}))
    assert box.run(FORK, box.y) != 0
    assert git(box.dest, "rev-parse", "HEAD") == box.x and git(box.dest, "remote", "get-url", "origin") == PINE

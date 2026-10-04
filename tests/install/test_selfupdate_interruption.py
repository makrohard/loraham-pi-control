"""A self-update interrupted at every durable write is recovered by the next apply.

The self-update transaction (`service_selfupdate._self_update_locked`, `selfupdate.py`) takes the
controller admission marker, binds the migration intent to a git anchor
(`refs/lhpc/selfupdate/<txid>`) and the journal `state/selfupdate-migrate.json` BEFORE the
checkout moves, advances the checkout, refreshes the status cache, promotes the journal, migrates
the config value that still equals the old default, clears the journal, deletes the anchor and
marks the firewall post-update check. The journal, cache and config go through `runtime_fs`;
the anchor writes and the advance are git calls, gated as one point each. Each case fails ONE
write (disk full, I/O error, Ctrl-C) during an update whose new manifest changes a default the
saved config still holds, and proves: what is left is never a journal the gate blocks on
(`classify_journal`), and the next apply — the recovery path — ends at the new commit with the
value migrated, no journal and no anchor.
"""
from __future__ import annotations

import types

import pytest

import gitrepo
from interrupts import FAILURES, durable_writes, run_interrupted
from lhpc.core import config as cfgmod
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.safety("selfupdate-transaction")

MANIFEST = ('[[stack]]\nid="s"\nname="S"\nmain="c"\n'
            '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\n'
            'readiness="process"\n'
            '  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{}"\n')
# The checkout's advance (`merge --ff-only`) is a git call among many reads: routed through its
# own name so the harness gates it, and only it, as one point.
GIT = types.SimpleNamespace(__name__="git", advance=None)
RAW = [(selfupdate, "create_anchor", lambda e: False),   # reports False, never raises
       (selfupdate, "delete_anchor", lambda e: None),    # best effort, never raises
       (GIT, "advance")]
# The apply's durable writes, in order.
POINTS = [
    ("write_marker", "state/locks/controller-task-admission.owner"),
    ("selfupdate.create_anchor", "*"),                       # the git anchor of the intent
    ("write_marker", "state/selfupdate-migrate.json"),       # prepared
    ("git.advance", "merge"),                                # the checkout moves
    ("write_marker", "state/selfupdate.json"),               # status cache
    ("write_marker", "state/selfupdate-migrate.json"),       # completed
    ("atomic_write", "config/stacks/s.toml"),                # the migration
    ("unlink", "state/selfupdate-migrate.json"),
    ("selfupdate.delete_anchor", "*"),
    ("write_marker", "state/firewall-postupdate.pending"),
    ("unlink", "state/locks/controller-task-admission.owner"),
]


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A self-hosted checkout at a manifest whose `ropt` defaults to OLD, the saved config holding
    OLD, and an upstream commit changing that default to NEW. Returns (service factory, work)."""
    _origin, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    (work / "manifest.toml").write_text(MANIFEST.format("OLD"))
    gitrepo.git(work, "add", "manifest.toml")
    gitrepo.git(work, "commit", "-qm", "manifest")
    gitrepo.git(work, "push", "-q", "origin", "main")
    gitrepo.git(up, "fetch", "-q", "origin")
    gitrepo.git(up, "reset", "-q", "--hard", "origin/main")
    (up / "manifest.toml").write_text(MANIFEST.format("NEW"))
    gitrepo.git(up, "commit", "-qam", "new default")
    gitrepo.git(up, "push", "-q", "origin", "HEAD:main")

    real_git = selfupdate._git
    monkeypatch.setattr(GIT, "advance", real_git)
    monkeypatch.setattr(selfupdate, "_git", lambda system, root, args, timeout: (
        GIT.advance if args[0] == "merge" else real_git)(system, root, args, timeout))
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True)

    def svc():          # a fresh service: what the next process sees
        return ControllerService(manifest_path=work / "manifest.toml", system=RealSystem(),
                                 paths=Paths(runtime_root=rt))
    cfgmod.save_stack_config(svc()._paths, "s", {"ropt": "OLD"})
    return svc, work, rt


def test_the_apply_writes_exactly_the_pinned_points(box):
    svc, _work, rt = box
    with durable_writes(rt, raw=RAW) as log:
        assert svc().self_update_apply().ok
    assert [(w, "merge" if w == "git.advance" else p) for w, p in log] == POINTS


CASES = [pytest.param(k, f, id=f"{w}:{p}-{f}")
         for k, (w, p) in enumerate(POINTS) for f in sorted(FAILURES)]


@pytest.mark.parametrize(("point", "failure"), CASES)
def test_an_interrupted_apply_is_finished_by_the_next(box, point, failure):
    svc, work, rt = box
    first = svc()
    run_interrupted(rt, first.self_update_apply, fail_at=point, exc=FAILURES[failure], raw=RAW)

    # (a) never a journal the gate blocks on
    assert selfupdate.classify_journal(first._paths, first._system)[0] != "blocked"
    # (b)+(c) the next apply recovers and finishes: new commit, value migrated, no journal/anchor
    nxt = svc()
    assert nxt.self_update_apply().ok
    assert gitrepo.git(work, "rev-parse", "HEAD") == gitrepo.git(work, "rev-parse", "origin/main")
    assert "ropt" not in cfgmod.load_stack_config(nxt._paths, "s")
    assert svc().stack_config("s")["ropt"] == "NEW"              # follows the new default
    assert selfupdate.read_migration_journal(nxt._paths) == (None, False)
    assert gitrepo.git(work, "for-each-ref", "refs/lhpc") == ""
    assert not (rt / "state" / "locks" / "controller-task-admission.owner").exists()

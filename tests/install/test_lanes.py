"""U1 (PLAN-UPDATE-v6, D8): ONE stable target for install, update and freshness: the newest of the version-shaped
tags and the upstream's latest GitHub release, by its leading version; an update of an installed stable source
only to a VERIFIED target that is not older than the installed commit.

Through the public path, cli.main(["install" | "update" | "source-check", ...]) and the web's Update form. The
upstream is a local bare repository behind git's own `insteadOf` rewrite (the remote policy admits https:// only,
so the URL stays a GitHub one); the GitHub releases API is the one faked command (`curl`), nothing reaches the
network. Red before U1: the install took the plain tag, the check compared with the branch tip, and a failed
release check updated anyway."""
import json

import pytest

from lhpc.adapters.cli import main as cli
from lhpc.core import stackupdates as su
from lhpc.core.paths import Paths
from lhpc.core.probes import backends
from lhpc.core.probes.backends import CommandResult, RealSystem
from lhpc.core.services import ControllerService

GH = "https://github.com/up/app"
OTHER = "https://example.invalid/up/app"          # not GitHub: tags only, no releases API
REL = "src/app"


@pytest.fixture
def box(tmp_path, git, make_repo, monkeypatch):
    """An upstream with three commits: c1 tagged v2.7.0, c2 tagged v2.8.0.7239fe8 (a build-suffixed tag, a
    release only when GitHub says so), c3 the untagged tip. `b.release` is what the releases API answers:
    a tag name (200), 404 (no release) or 429 (rate-limited). `b.cli(*argv)` runs lhpc on the box."""
    work = tmp_path / "up"
    c1 = make_repo(work, {"f": "1\n"})
    git(work, "branch", "-M", "main")
    git(work, "tag", "v2.7.0")
    commits = [c1]
    for n in (2, 3):
        (work / "f").write_text(f"{n}\n")
        git(work, "commit", "-qam", str(n))
        commits.append(git(work, "rev-parse", "HEAD"))
    git(work, "tag", "-a", "-m", "r", "v2.8.0.7239fe8", commits[1])      # annotated: the peeled commit counts
    bare = tmp_path / "up.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    git(work, "remote", "add", "origin", str(bare))
    cfg = tmp_path / "gitconfig"
    cfg.write_text(f'[url "file://{bare}"]\n\tinsteadOf = {GH}\n\tinsteadOf = {OTHER}\n'
                   '[protocol "file"]\n\tallow = always\n')
    monkeypatch.setitem(backends._FIXED_ENV, "GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setitem(backends._FIXED_ENV, "GIT_CONFIG_SYSTEM", "/dev/null")

    class Box:
        release = "404"
        api_calls: list = []
    b = Box()
    real_run = backends.RealCommandRunner.run

    def run(self, argv, timeout, cwd=None, env=None):
        if argv[0] != "curl":
            return real_run(self, argv, timeout, cwd=cwd, env=env)
        b.api_calls.append(argv)
        if b.release in ("404", "429"):
            return CommandResult(0, f'{{"message": "x"}}\n{b.release}', "")
        return CommandResult(0, json.dumps({"tag_name": b.release}) + "\n200", "")
    monkeypatch.setattr(backends.RealCommandRunner, "run", run)

    rt = tmp_path / "rt"
    (rt / "config").mkdir(parents=True)
    manifest = tmp_path / "m.toml"

    def use(remote):
        manifest.write_text(
            '[[stack]]\nid = "s"\nname = "s"\nmain = "app"\n'
            '[[stack.component]]\nid = "app"\nname = "app"\nkind = "service"\n'
            'run_argv = ["true"]\nreadiness = "process"\n'
            f'[stack.component.source]\npath = "{REL}"\nremote = "{remote}"\nbranch = "main"\n'
            f'pin_commit = "{c1}"\n')
    use(GH)
    svc = lambda: ControllerService(manifest_path=manifest, system=RealSystem(), paths=Paths(runtime_root=rt))  # noqa: E731
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc())
    b.c, b.work, b.rt, b.dest, b.use, b.svc, b.manifest = commits, work, rt, rt / REL, use, svc, manifest
    b.cli = lambda *argv: cli.main(list(argv))
    b.head = lambda: git(b.dest, "rev-parse", "HEAD")
    b.status = lambda: su.view(Paths(runtime_root=rt))["components"]["app"]["status"]

    def push_tag(name, at):
        git(work, "tag", name, at)
        git(work, "push", "-q", "origin", name)
    b.push_tag = push_tag
    return b


def test_the_latest_release_beats_an_older_plain_tag_for_install_and_check(box):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]                    # RED before: v2.7.0 (c1), the release was not read
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE             # RED before: the tip c3 decided -> behind


def test_a_plain_tag_behind_the_tip_is_current(box):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # 404: no release, tags verified
    assert box.head() == box.c[0]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE             # RED before: behind the tip


def test_a_failed_release_check_refuses_the_update_and_changes_nothing(box, capsys, web, csrf):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    box.release = "429"
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert "releases could not be checked — try again later" in capsys.readouterr().out
    assert box.head() == box.c[0]
    box.cli("source-check", "s")
    assert box.status() == su.ERROR                  # "could not check", not "update available"
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    page = client.post("/action", data={"_csrf": csrf(client), "op": "update", "target": "s",
                                        "source": "stable"}).get_data(as_text=True)
    assert "releases could not be checked — try again later" in page
    assert box.head() == box.c[0]


def test_control_a_verified_newer_tag_is_offered_and_installed(box):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]


def test_a_non_github_remote_reads_tags_only_and_updates(box):
    box.use(OTHER)
    box.release = "429"                              # would refuse, were the API asked
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.8.1", box.c[2])
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2] and box.api_calls == []


def test_a_verified_but_older_target_is_refused_and_reads_current(box, capsys):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.release = "v2.7.0"                           # the upstream's latest release moved back
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert "older than the installed commit" in capsys.readouterr().out
    assert box.head() == box.c[1]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE


def test_an_install_from_the_default_branch_head_is_offered_a_new_tag(box, git):
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]                    # neither tag nor release: the default-branch HEAD
    (box.work / "f").write_text("4\n")
    git(box.work, "commit", "-qam", "4")
    c4 = git(box.work, "rev-parse", "HEAD")
    git(box.work, "push", "-q", "origin", "main")
    box.push_tag("v3.0", c4)
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == c4


@pytest.mark.parametrize("names, release, expected", [
    (["v2.7.0", "v2.8.0.7239fe8"], "v2.8.0.7239fe8", "v2.8.0.7239fe8"),   # a newer release beats the plain tag
    (["v2.8.0", "v2.8.0.7239fe8"], "v2.8.0.7239fe8", "v2.8.0"),   # same leading version: the plain tag
    (["v1.2", "v1.1.9"], "", "v1.2"),                     # v1.2 = v1.2.0
    (["v2.8.0", "v2.8.0.9"], "", "v2.8.0.9"),             # a fourth number is a version too
    (["v2.7.0", "release-2.8.0"], "release-2.8.0", "v2.7.0"),   # no leading version: not a candidate
    (["v2.7.0"], "v9.0", "v2.7.0"),                       # a release tag the remote does not list
    (["nightly", "1.8.2-pre"], "", ""),                   # no candidate: the default-branch HEAD
])
def test_the_stable_key_table(names, release, expected):
    from lhpc.core import stable_lane         # new in U1: imported here, so the red-before runs the rest
    assert stable_lane.stable_tag(names, release) == expected
    assert stable_lane.version_key("v1.2") == stable_lane.version_key("v1.2.0") == (1, 2)


@pytest.mark.parametrize("name, key", [("v2.8.0.7239fe8", (2, 8)), ("v2.8.0.9", (2, 8, 0, 9)), ("1.8.2-pre", (1, 8, 2)),
                                       ("v112", (112,)), ("release-2.8.0", None), ("v1.2.0", (1, 2))])
def test_the_leading_version_is_whole_numeric_segments(name, key):
    from lhpc.core import stable_lane
    assert stable_lane.version_key(name) == key

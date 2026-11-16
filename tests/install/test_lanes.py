"""The stack lanes (PLAN-UPDATE-v6).
U1 (D8): ONE stable target for install, update and freshness: the newest of the version-shaped tags and the
upstream's latest GitHub release, by its leading version; an update of an installed stable source only to a
VERIFIED target that is not older than the installed commit.
U2: a bare update stays on the installed lane (`stack_lane`); a mixed stack updates each source on its own
recorded selector.

Through the public path, cli.main(["install" | "update" | "source-check", ...]) and the web's Update form. The
upstream is a local bare repository behind git's own `insteadOf` rewrite (the remote policy admits https:// only,
so the URL stays a GitHub one); the GitHub releases API is the one faked command (`curl`), nothing reaches the
network. Red before U1: the install took the plain tag, the check compared with the branch tip, and a failed
release check updated anyway. Red before U2: a bare update moved every source to the pin."""
import json
import shutil

import htmlq
import pytest

from lhpc.adapters.cli import main as cli
from lhpc.core import source_registry
from lhpc.core import stackupdates as su
from lhpc.core.paths import Paths
from lhpc.core.probes import backends
from lhpc.core.probes.backends import CommandResult, RealSystem
from lhpc.core.services import ControllerService

GH = "https://github.com/up/app"
OTHER = "https://example.invalid/up/app"          # not GitHub: tags only, no releases API
REL = "src/app"
LIB = "src/lib"                                   # a second source of the same stack (the mixed-stack case)


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
        fail_local_tags = False                       # the checkout's `tag --points-at` exits 128
        fail_mirror_tags = False                      # the history mirror's `tag --points-at` exits 128
        fail_head = False
        fail_fetch = False
        fail_ancestry = False                         # the history mirror's `merge-base` exits 128
        fail_ancestry_to = ""                         # ... only `merge-base --is-ancestor <x> <this commit>`
        api_calls: list = []
        git_calls: list = []
    b = Box()
    real_run = backends.RealCommandRunner.run

    def run(self, argv, timeout, cwd=None, env=None):
        if argv[0] == "git":
            b.git_calls.append(argv)
        if argv[:2] == ["git", "-C"] and argv[3:5] == ["tag", "--points-at"] and (
                b.fail_mirror_tags if "stable-history" in argv[2] else b.fail_local_tags):
            return CommandResult(128, "", "fatal: unable to read tags")
        if b.fail_head and argv[:2] == ["git", "-C"] and argv[3:5] == ["rev-parse", "HEAD"]:
            return CommandResult(128, "", "fatal: not a git repository")
        if b.fail_ancestry and argv[:2] == ["git", "-C"] and "stable-history" in argv[2] and argv[3] == "merge-base":
            return CommandResult(128, "", "fatal: could not read the history")
        if b.fail_ancestry_to and argv[3:5] == ["merge-base", "--is-ancestor"] and argv[-1] == b.fail_ancestry_to:
            return CommandResult(128, "", "fatal: could not read the history")
        if b.fail_fetch and argv[:2] == ["git", "-C"] and argv[3] == "fetch":
            return CommandResult(128, "", "fatal: unable to access the remote")
        if argv[0] != "curl":
            return real_run(self, argv, timeout, cwd=cwd, env=env)
        b.api_calls.append(argv)
        if b.release in ("404", "429", "503"):
            return CommandResult(0, f'{{"message": "x"}}\n{b.release}', "")
        if b.release.startswith("{"):                 # a raw 200 body (no or an unusable tag_name)
            return CommandResult(0, b.release + "\n200", "")
        return CommandResult(0, json.dumps({"tag_name": b.release}) + "\n200", "")
    monkeypatch.setattr(backends.RealCommandRunner, "run", run)

    rt = tmp_path / "rt"
    (rt / "config").mkdir(parents=True)
    manifest = tmp_path / "m.toml"

    def use(remote, lib=False, lib_path=LIB, lib_artifact=False, lib_first=False):
        app = ('[[stack.component]]\nid = "app"\nname = "app"\nkind = "service"\n'
               'run_argv = ["true"]\nreadiness = "process"\n'
               f'[stack.component.source]\npath = "{REL}"\nremote = "{remote}"\nbranch = "main"\n'
               f'pin_commit = "{c1}"\n')
        other = ('[[stack.component]]\nid = "lib"\nname = "lib"\nkind = "library"\ntx_capable = false\n'
                 f'[stack.component.source]\npath = "{lib_path}"\nremote = "{remote}"\nbranch = "main"\n'
                 f'pin_commit = "{c1}"\n' + ("artifact = true\n" if lib_artifact else "") if lib else "")
        manifest.write_text('[[stack]]\nid = "s"\nname = "s"\nmain = "app"\n' + (other + app if lib_first else app + other))
    use(GH)
    svc = lambda: ControllerService(manifest_path=manifest, system=RealSystem(), paths=Paths(runtime_root=rt))  # noqa: E731
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc())
    b.c, b.work, b.rt, b.dest, b.use, b.svc, b.manifest = commits, work, rt, rt / REL, use, svc, manifest
    b.cli = lambda *argv: cli.main(list(argv))
    b.head = lambda rel=REL: git(rt / rel, "rev-parse", "HEAD")
    b.status = lambda cid="app": su.view(Paths(runtime_root=rt))["components"][cid]["status"]
    b.selector = lambda rel=REL: source_registry.read_record(Paths(runtime_root=rt), rel).selector

    def advance():
        """A new commit on the upstream's main; its sha."""
        (work / "f").write_text(f"{len(b.c) + 1}\n")
        git(work, "commit", "-qam", "next")
        git(work, "push", "-q", "origin", "main")
        b.c.append(git(work, "rev-parse", "HEAD"))
        return b.c[-1]
    b.advance = advance

    def push_tag(name, at):
        git(work, "tag", name, at)
        git(work, "push", "-q", "origin", name)
    b.push_tag = push_tag

    def side(at, name="side"):
        """A commit on a new branch `name` from `at`, pushed (beside main, in nobody's line); its sha."""
        git(work, "checkout", "-q", "-b", name, at)
        (work / "g").write_text(name + "\n")
        git(work, "add", "g")
        git(work, "commit", "-qm", name)
        sha = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", name)
        git(work, "checkout", "-q", "main")
        return sha
    b.side = side

    def drop_tags(*names):
        """Delete the tags upstream AND in the working repository it is pushed from."""
        git(work, "push", "-q", "origin", "--delete", *names)
        git(work, "tag", "-d", *names)
    b.drop_tags = drop_tags
    b.mirror = lambda: next((rt / "state" / "stable-history").glob("*.git"))
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
    assert "is not newer than the installed version: refused" in capsys.readouterr().out
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


@pytest.mark.parametrize("name, key", [("v2.8.0.7239fe8", (2, 8)), ("v2.8.0.9", None), ("v2.8.0.1234567", (2, 8)),
                                       ("v2.8.7239fe8", None), ("v1.2.3.4.5", None), ("1.8.2-pre", None),
                                       ("v112", (112,)), ("release-2.8.0", None), ("v1.2.0", (1, 2)),
                                       ("v99.0-dev", None), (["v3.0"], None), (None, None)])
def test_one_strict_version_parser(name, key):
    from lhpc.core import stable_lane
    assert stable_lane.version_of(name) == key


def test_a_snapshot_tag_on_the_installed_commit_does_not_hide_a_newer_release(box):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v99.0-dev", box.c[0])               # a snapshot name on the installed v2.7.0 commit
    box.push_tag("v2.8.0", box.c[2])
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # RED before: the snapshot made the install look newer
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]


def test_a_snapshot_tag_beside_a_release_only_name_does_not_hide_a_newer_tag(box):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]
    box.push_tag("v99.0-dev", box.c[1])               # the installed commit: ["v2.8.0.7239fe8", "v99.0-dev"]
    box.push_tag("v2.9.0", box.c[2])
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # RED before: the snapshot made the install look newer
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]


@pytest.mark.parametrize("body", ["{}", '{"tag_name": ["v3.0"]}', '{"tag_name": ""}'])   # no usable tag name
def test_a_release_answer_without_a_usable_tag_name_is_not_verified(box, capsys, body):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    box.release = body
    box.cli("source-check", "s")
    assert box.status() == su.ERROR                   # RED before: verified, "behind"
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert "releases could not be checked — try again later" in capsys.readouterr().out
    assert box.head() == box.c[0]


def test_a_deleted_upstream_tag_still_blocks_a_downgrade(box, git, capsys):
    box.push_tag("v3.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]
    git(box.work, "push", "-q", "origin", "--delete", "v3.0")      # upstream withdraws v3.0 ...
    box.push_tag("v2.9", box.c[1])
    box.release = "v2.9"                                           # ... and its latest release is older
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: downgraded to v2.9
    assert "is not newer than the installed version: refused" in capsys.readouterr().out
    assert box.head() == box.c[2]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE


def test_a_lower_numbered_release_after_the_installed_commit_is_a_forward_move(box, capsys):
    box.push_tag("v2.6.0", box.c[1])                   # the installed commit: ["v2.6.0", "v2.8.0.7239fe8"]
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]
    box.push_tag("v2.7.1", box.c[2])
    box.release = "v2.7.1"                             # a lower number, on a commit AFTER the installed one
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                   # slice A: the history decides (U1 refused it by numbers)
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE


def test_an_unreadable_installed_tag_list_does_not_matter_the_history_refuses_the_downgrade(box, git, capsys):
    box.push_tag("v3.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    git(box.work, "push", "-q", "origin", "--delete", "v3.0")      # only the checkout still knows v3.0
    box.push_tag("v2.9", box.c[1])
    box.release = "v2.9"
    box.fail_local_tags = True                                     # ... and it cannot be read now (not needed)
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE                           # slice A: c2 is in c3's past (was ERROR)
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before U1: downgraded to v2.9
    assert "is not newer than the installed version: refused" in capsys.readouterr().out
    assert box.head() == box.c[2]


def test_a_commit_with_only_unparseable_tags_refuses_a_stable_downgrade(box, git, capsys):
    box.push_tag("v3.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # within the stable lane (a switch is exempt)
    assert box.head() == box.c[2]
    git(box.work, "push", "-q", "origin", "--delete", "v3.0")
    git(box.dest, "tag", "-d", "v3.0")
    box.push_tag("v3.0-rc1", box.c[2])                # the installed commit's only tag does not parse
    box.push_tag("v2.9", box.c[1])
    box.release = "v2.9"
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE                              # slice A: the history orders it (was ERROR)
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before U1: downgraded to v2.9
    assert "is not newer than the installed version: refused" in capsys.readouterr().out
    assert box.head() == box.c[2]


def test_an_all_digit_commit_suffix_is_no_version_number(box):
    box.push_tag("v2.8.0.1234567", box.c[2])          # a build-suffixed snapshot, not published as the release
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[0]                     # RED before: read as the plain version 2.8.0.1234567 (c3)
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE
# ---- U2: the lane sticks ---------------------------------------------------------------------------------------

def test_a_bare_update_keeps_a_dev_stack_on_dev(box, web, csrf):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    tip = box.advance()
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    page = client.post("/action", data={"_csrf": csrf(client), "op": "update", "target": "s"}).get_data(as_text=True)
    assert "fetches the dev version" in page          # RED before: the web's bare update planned pinned
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == tip and box.selector() == "dev"   # RED before: moved to the pin (c1), recorded pinned


def test_a_mixed_stack_updates_each_source_on_its_own_lane(box):
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.cli("update", "lib", "--source", "stable", "--yes") == 0
    assert box.head(LIB) == box.c[0] and box.selector(LIB) == "stable"
    tip = box.advance()
    box.push_tag("v2.9.0", tip)
    assert box.cli("update", "s", "--yes") == 0
    assert (box.head(), box.selector()) == (tip, "dev")             # RED before: both to the pin
    assert (box.head(LIB), box.selector(LIB)) == (tip, "stable")


def test_control_a_mixed_stack_with_one_stable_update_offers_the_update(box, web, csrf):
    """Each source is checked on its own lane (U1's probe): a current dev source does not hide the stable one's."""
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.cli("update", "lib", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])                  # the dev tip, now also the newest tag
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    assert client.post("/source-check/s", data={"_csrf": csrf(client)}).status_code == 302
    assert (box.status("app"), box.status("lib")) == (su.UP_TO_DATE, su.BEHIND)
    doc = htmlq.parse(client.get("/stacks").get_data(as_text=True))
    assert [a for a in doc.find("a", class_="update-link") if "open=s" in (a["href"] or "")]


def test_a_bare_update_of_a_stable_source_keeps_the_release_gate(box, capsys, web, csrf):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    box.release = "429"
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    page = client.post("/action", data={"_csrf": csrf(client), "op": "update", "target": "s"}).get_data(as_text=True)
    assert "releases could not be checked — try again later" in page
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0      # RED before: a bare update moved it to the pin
    assert "releases could not be checked — try again later" in capsys.readouterr().out
    assert (box.head(), box.selector()) == (box.c[0], "stable")


def test_a_bare_update_of_a_mixed_stack_keeps_the_stable_sources_release_gate(box, capsys, web, csrf):
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.cli("update", "lib", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.advance())
    box.release = "429"
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    page = client.post("/action", data={"_csrf": csrf(client), "op": "update", "target": "s"}).get_data(as_text=True)
    assert "lib: releases could not be checked — try again later" in page
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0
    assert "lib: releases could not be checked — try again later" in capsys.readouterr().out
    assert (box.head(), box.head(LIB)) == (box.c[2], box.c[0])


def test_a_source_without_a_valid_record_is_planned_on_the_pin_beside_a_dev_source(box, capsys):
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    source_registry.record_path(Paths(runtime_root=box.rt), LIB).unlink()   # lib: its record is gone
    capsys.readouterr()
    assert box.cli("update", "s") == 0                                     # the plan, per source
    lanes = {ln.split(":")[0].strip(): ln for ln in capsys.readouterr().out.splitlines() if "this update fetches" in ln}
    assert "fetches the dev version" in lanes["app"]
    assert "fetches the pinned version" in lanes["lib"]                   # RED before: lib followed app onto dev


@pytest.mark.parametrize("release_then", ["404", "429"])
def test_an_update_finishing_before_the_locks_is_seen_under_them(box, monkeypatch, capsys, release_then):
    import contextlib
    import dataclasses
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.cli("update", "lib", "--source", "stable", "--yes") == 0
    tip = box.advance()
    box.push_tag("v2.9.0", tip)
    real_guard, real_update, applying = ControllerService._source_operation_guard, ControllerService.update, []
    paths = Paths(runtime_root=box.rt)

    def update(self, *a, **k):
        applying[:] = [k.get("apply", False)]
        return real_update(self, *a, **k)

    @contextlib.contextmanager
    def guard(self, *a, **k):
        """Update B finishes just before this apply takes its locks: app recorded stable."""
        if applying == [True] and source_registry.read_record(paths, REL).selector == "dev":
            source_registry.write_record(paths, dataclasses.replace(source_registry.read_record(paths, REL),
                                                                    selector="stable"))
            box.release = release_then
        with real_guard(self, *a, **k):
            yield
    monkeypatch.setattr(ControllerService, "update", update)
    monkeypatch.setattr(ControllerService, "_source_operation_guard", guard)
    capsys.readouterr()
    rc = box.cli("update", "s", "--yes")
    if release_then == "404":                          # B's stable selection is kept and verified under the locks
        assert rc == 0 and (box.head(), box.selector()) == (tip, "stable")
    else:                                              # ... or its release check fails: refused, nothing changed
        assert rc != 0 and "releases could not be checked" in capsys.readouterr().out
        assert (box.head(), box.selector()) == (box.c[2], "stable")
    assert box.head(LIB) == (tip if release_then == "404" else box.c[0])

def test_a_bare_update_keeps_a_stable_install_on_its_release(box):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[0]                     # v2.7.0
    box.push_tag("v2.9.0", box.c[2])
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]
    assert box.cli("update", "s", "--yes") == 0       # RED with U1 alone: the bare update went back to the pin
    assert (box.head(), box.selector()) == (box.c[2], "stable")


def test_a_target_without_a_version_is_offered_beside_a_tagged_install(box, git):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[0]                     # v2.7.0, kept in the checkout
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")   # the upstream drops its tags
    tip = box.advance()                               # stable target: the default-branch HEAD (no version)
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # RED before: read as current (a HEAD target "older")
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0   # RED before: refused as older
    assert box.head() == tip


# ---- gate 2 (G2-UPD-CODE): commits decide where a version is missing; the apply's one gate; commands ---------------

def test_an_older_head_target_refuses_the_downgrade(box, git, capsys):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]                     # its tag v2.8.0.7239fe8 stays in the checkout
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")
    git(box.work, "push", "-q", "-f", "origin", f"{box.c[0]}:main")      # the default branch moved back
    box.release = "404"                               # no release: the target is the default-branch HEAD (c1)
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: downgraded to c1's parent
    out = capsys.readouterr().out                     # proven not forward: refused, not "try again later"
    assert "is not in the upstream's published history: refused" in out and "try again later" not in out
    assert box.head() == box.c[1]                     # slice A: c2 is on no branch or tag any more


def test_an_unreadable_ancestry_reads_could_not_be_checked(box, git, capsys):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")
    box.advance()                                     # the target: a commit the checkout does not have yet ...
    box.release = "404"
    box.fail_fetch = True                             # ... and cannot fetch now
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    out = capsys.readouterr().out
    assert "releases could not be checked — try again later" in out and "is not newer" not in out
    assert box.head() == box.c[1]


def test_a_failed_head_read_refuses_the_update(box, git, capsys):
    box.push_tag("v3.0.0.abcdef3", box.c[2])
    box.release = "v3.0.0.abcdef3"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]
    git(box.dest, "tag", "-d", "v3.0.0.abcdef3")      # only the upstream still names the installed version
    box.push_tag("v2.9", box.c[1])
    box.release = "v2.9"
    box.fail_head = True
    box.git_calls.clear()
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: downgraded to v2.9
    assert "could not be checked" in capsys.readouterr().out
    assert not [a for a in box.git_calls                       # refused before any comparison (the history
                if ("merge-base" in a or "fetch" in a) and "stable-history" not in a[2]]   # mirror is the target's read)
    assert not [a for a in box.git_calls if "stable-history" in a[2] and a[3] in ("cat-file", "for-each-ref")
                and "--count=1" in a]
    box.fail_head = False
    assert box.head() == box.c[2]


def test_the_apply_reads_each_release_once_and_under_its_locks(box, monkeypatch):
    import contextlib

    from lhpc.core import stable_lane
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    real_guard, real_resolve, real_update = (ControllerService._source_operation_guard, stable_lane.resolve,
                                            ControllerService.update)
    state, reads = {"apply": False, "locked": False}, []

    @contextlib.contextmanager
    def guard(self, *a, **k):
        with real_guard(self, *a, **k):
            state["locked"] = True
            try:
                yield
            finally:
                state["locked"] = False

    def update(self, *a, **k):
        state["apply"] = k.get("apply", False)
        return real_update(self, *a, **k)

    def resolve(run, remote, paths):
        if state["apply"]:
            reads.append(state["locked"])
        return real_resolve(run, remote, paths)
    monkeypatch.setattr(ControllerService, "_source_operation_guard", guard)
    monkeypatch.setattr(ControllerService, "update", update)
    monkeypatch.setattr(stable_lane, "resolve", resolve)
    assert box.cli("update", "s", "--yes") == 0
    assert reads == [True]                            # RED before: [False, True] (a gate before the locks too)
    assert box.head() == box.c[2]


def test_a_suggested_command_keeps_an_explicit_pinned_source(box, monkeypatch, capsys):
    from lhpc.core import reslock
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "pinned") == 0       # the plan
    assert "lhpc update s --source pinned --yes" in capsys.readouterr().out   # RED before: lhpc update s --yes

    def busy(self, *a, **k):
        raise reslock.ResourceBusy("source:src/app", {"operation": "update", "target": "s", "pid": 1})
    monkeypatch.setattr(ControllerService, "_source_operation_guard", busy)
    assert box.cli("update", "s", "--source", "pinned", "--yes") != 0
    assert "lhpc update s --source pinned --yes" in capsys.readouterr().out   # RED before: lhpc update s --yes


def test_a_selector_less_run_action_update_keeps_the_installed_lane(box):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    tip = box.advance()
    assert box.svc().run_action("update", "s", apply=True).ok
    assert (box.head(), box.selector()) == (tip, "dev")        # RED before: moved to the pin (c1)


def test_an_explicit_switch_from_dev_to_stable_moves_back_to_the_release(box):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.head() == box.c[2]                     # the dev tip, ahead of v2.7.0 and without a version
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0   # control: a switch is the user's choice
    assert (box.head(), box.selector()) == (box.c[0], "stable")


def test_a_switch_to_stable_without_a_release_version_is_refused(box, git, capsys):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")   # no version upstream at all
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert "could not be checked" in capsys.readouterr().out
    assert (box.head(), box.selector()) == (box.c[2], "dev")


def test_a_release_the_tag_list_does_not_show_yet_is_not_verified(box, capsys):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[0]                     # v2.7.0
    box.push_tag("v2.9.0", box.c[2])
    box.release = "v4.0.0.abcdef4"                    # published after the tag listing: not in it yet
    box.cli("source-check", "s")
    assert box.status() == su.ERROR                   # RED before: behind, verified on v2.9.0
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: updated to v2.9.0
    assert "releases could not be checked — try again later" in capsys.readouterr().out
    assert box.head() == box.c[0]


def test_one_source_path_is_resolved_once_per_gate(box):
    from lhpc.core import stable_lane
    box.use(GH, lib=True, lib_path=REL)               # two components share one checkout
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    svc = box.svc()
    items = [(svc.stack("s"), c) for c in svc.stack("s").components]
    box.api_calls.clear()
    sel, refs, refused = stable_lane.update_gate(svc._system.runner.run, svc._paths, items, "stable",
                                                 svc._effective_remote)
    assert len(box.api_calls) == 1 and list(refs) == [REL] and refused == []   # RED before: 2 calls


def test_control_a_release_less_source_still_installs_its_default_branch_head(box, git):
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")
    box.release = "404"                               # no release at all: the default-branch HEAD (L6)
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]


# ---- MESHCOM-STABLE slice A: the history decides (the LoRaHAM_Daemon `v112` class) -----------------------------

NOT_IN_LINE = "is not in the history of the stable target"
UNPUBLISHED = "is not in the upstream's published history: refused"
NOT_NEWER = "is not newer than the installed version: refused; use --source pinned to go back to LHPC's tested versions"
NOT_ONE_LINE = "its release and version tags are not one line of history; refused; use --source pinned or dev"


def test_a_tag_in_the_releases_past_is_never_the_stable_target(box):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v112", box.c[0])                    # an older numbering, in the release's past
    box.push_tag("v1.4.0", box.c[1])
    box.release = "v1.4.0"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]                     # RED before: c1 (v112 read as version 112)
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE


def test_an_old_tag_pushed_into_the_installed_releases_past_moves_nothing(box, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v1.4.0", box.c[1])
    box.release = "v1.4.0"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v112", box.c[0])
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE              # RED before: behind (a "newer" v112)
    assert box.cli("update", "s", "--yes") == 0
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert (box.head(), box.selector()) == (box.c[1], "stable")   # RED before: moved back to c1


def test_a_box_on_an_old_scheme_tag_is_offered_the_release_in_its_future(box):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v112", box.c[0])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # no release yet: v112 is the newest tag
    assert box.head() == box.c[0]
    box.push_tag("v1.4.0", box.c[1])
    box.release = "v1.4.0"
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # RED before: up to date (112 > 1.4)
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[1]


def test_a_higher_tag_in_the_installed_commits_past_is_not_a_stable_update(box, capsys, web, csrf):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v1.4.0", box.c[1])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # no release: the tags decide
    box.push_tag("v1.5.0", box.c[0])                  # a higher number, on an ancestor of the installed commit
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE              # RED before: behind
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]                     # RED before: moved back to c1
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    client.post("/action", data={"_csrf": csrf(client), "op": "update", "target": "s", "source": "stable"})
    assert box.head() == box.c[1]


def test_a_release_beside_the_installed_line_is_refused_by_name(box, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v2.8.0", box.c[1])
    box.release = "v2.8.0"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    d = box.side(box.c[0])                            # diverged: neither ancestor nor descendant of c2
    box.push_tag("v3.0", d)
    box.release = "v3.0"
    box.cli("source-check", "s")
    assert box.status() == su.ERROR                   # RED before: behind
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    out = capsys.readouterr().out
    assert NOT_IN_LINE in out and "try again later" not in out
    assert box.head() == box.c[1]                     # RED before: moved to the side branch


def test_an_unpublished_installed_commit_is_refused_by_name(box, git, capsys):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # v2.7.0 (c1)
    git(box.dest, "commit", "-q", "--allow-empty", "-m", "local")        # a commit only this box has
    local = box.head()
    box.push_tag("v2.9.0", box.c[2])
    box.cli("source-check", "s")
    assert box.status() == su.ERROR                   # RED before: up to date
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert f"the installed commit {local[:9]} {UNPUBLISHED}" in capsys.readouterr().out   # RED before: "not newer"
    assert box.head() == local


def test_a_commit_only_a_tag_reaches_is_published_and_moves_forward(box, git):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    s1 = box.side(box.c[0], "old")                    # a branch the upstream later deletes
    box.push_tag("v0.1.0", s1)
    git(box.work, "checkout", "-q", "old")
    (box.work / "g").write_text("2\n")
    git(box.work, "commit", "-qam", "old 2")
    s2 = git(box.work, "rev-parse", "HEAD")
    git(box.work, "checkout", "-q", "main")
    git(box.work, "push", "-q", "origin", "--delete", "old")    # only the tag reaches s1 (LoRaHAM_Daemon: 5 of 32)
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == s1
    box.push_tag("v0.2.0", s2)
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == s2


def test_a_commit_the_upstream_rewrote_away_is_not_published_although_the_mirror_holds_it(box, git, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v3.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # c3: the mirror now holds its object
    box.drop_tags("v3.0")
    git(box.work, "reset", "-q", "--hard", box.c[1])
    (box.work / "f").write_text("rewritten\n")
    git(box.work, "commit", "-qam", "rewritten")
    new = git(box.work, "rev-parse", "HEAD")
    git(box.work, "push", "-q", "-f", "origin", "main")      # c3 is no longer on any branch or tag
    box.push_tag("v3.1", new)
    box.release = "v3.1"
    box.cli("source-check", "s")
    git(box.mirror(), "cat-file", "-e", box.c[2])            # the old object is still there
    assert box.status() == su.ERROR                   # RED before: behind
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert UNPUBLISHED in capsys.readouterr().out
    assert box.head() == box.c[2]                     # RED before: moved onto the rewritten history


def test_a_rebuilt_mirror_never_fetches_a_commit_the_upstream_rewrote_away(box, git, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v3.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # c3
    box.drop_tags("v3.0")
    git(box.work, "reset", "-q", "--hard", box.c[1])
    git(box.work, "commit", "-q", "--allow-empty", "-m", "rewritten")
    git(box.work, "push", "-q", "-f", "origin", "main")      # the upstream still holds c3's object, no ref reaches it
    shutil.rmtree(box.mirror())                              # the mirror is rebuilt from the current refs only
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert UNPUBLISHED in capsys.readouterr().out
    assert box.head() == box.c[2]
    held = git(box.mirror(), "cat-file", "--batch-all-objects", "--batch-check=%(objectname)",
               env={"GIT_NO_LAZY_FETCH": "1"}).split()
    assert box.c[1] in held and box.c[2] not in held  # the read asked the upstream for no object (A-M14)


def test_a_once_shallow_dev_checkout_on_the_stable_lane_moves_forward_only(box, git, capsys):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert git(box.dest, "rev-parse", "--is-shallow-repository") == "true"
    box.release = "v2.8.0.7239fe8"                    # c2, behind the dev tip c3
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0   # the switch: the user's choice (landed U2)
    assert (box.head(), box.selector()) == (box.c[1], "stable")
    c4 = box.advance()
    box.push_tag("v2.9.0", c4)
    box.release = "v2.9.0"
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == c4
    box.release = "v2.8.0.7239fe8"                    # the marker moved back, the newer tag is gone upstream
    box.drop_tags("v2.9.0")
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0
    assert NOT_NEWER in capsys.readouterr().out
    assert box.head() == c4


def test_a_switch_onto_stable_from_an_unpublished_commit_is_refused(box, git, capsys, web, csrf):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    git(box.dest, "commit", "-q", "--allow-empty", "-m", "local")
    local = box.head()
    box.release = "v2.8.0.7239fe8"
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: switched back to c2
    assert UNPUBLISHED in capsys.readouterr().out
    assert (box.head(), box.selector()) == (local, "dev")
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    page = client.post("/action", data={"_csrf": csrf(client), "op": "update", "target": "s",
                                        "source": "stable"}).get_data(as_text=True)
    assert "published history: refused" in page and (box.head(), box.selector()) == (local, "dev")


@pytest.mark.parametrize("lane", ["stable", "dev"])
def test_a_failed_ancestry_read_refuses_and_reads_could_not_check(box, capsys, lane):
    assert box.cli("install", "s", "--source", lane, "--yes") == 0
    before = box.head()
    box.push_tag("v2.9.0", box.advance())
    box.fail_ancestry = True                          # the mirror's merge-base exits 128
    if lane == "stable":
        box.cli("source-check", "s")
        assert box.status() == su.ERROR
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert "releases could not be checked — try again later" in capsys.readouterr().out
    assert (box.head(), box.selector()) == (before, lane)


def test_a_shallow_history_mirror_refuses_and_reads_could_not_check(box, capsys):
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    (box.mirror() / "shallow").write_text(box.c[0] + "\n")    # the mirror's history would be cut at c1
    box.push_tag("v2.9.0", box.c[2])
    box.cli("source-check", "s")
    assert box.status() == su.ERROR
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert "releases could not be checked — try again later" in capsys.readouterr().out
    assert box.head() == box.c[0]


def test_two_paths_of_one_remote_read_one_target_in_one_gate(box):
    from lhpc.core import stable_lane
    box.use(GH, lib=True)                             # src/app and src/lib, one remote
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    svc = box.svc()
    items = [(svc.stack("s"), c) for c in svc.stack("s").components]
    box.api_calls.clear()
    box.git_calls.clear()
    sel, refs, refused = stable_lane.update_gate(svc._system.runner.run, svc._paths, items, "stable",
                                                 svc._effective_remote)
    fetches = [a for a in box.git_calls if a[3:4] == ["fetch"]]
    assert len(box.api_calls) == 1 and len(fetches) == 1      # RED before: 2 API calls
    assert sorted(refs) == sorted([LIB, REL]) and refs[REL] is refs[LIB] and refused == []
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.head(LIB) == box.c[2]


def test_two_paths_of_one_remote_read_one_target_in_one_source_check(box):
    box.use(GH, lib=True)                             # src/app and src/lib, one remote
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])
    box.api_calls.clear()
    box.git_calls.clear()
    box.cli("source-check", "s")
    fetches = [a for a in box.git_calls if a[3:4] == ["fetch"]]
    assert len(box.api_calls) == 1 and len(fetches) == 1      # RED before: 2 API calls, 2 mirror fetches
    assert box.status("app") == box.status("lib") == su.BEHIND


def test_two_paths_of_one_remote_read_one_target_in_one_install(box):
    box.use(GH, lib=True)
    box.release = "v2.8.0.7239fe8"
    box.api_calls.clear()
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert len(box.api_calls) == 1                    # RED before: one releases read per path
    assert box.head() == box.head(LIB) == box.c[1]


def test_an_update_installing_a_second_path_of_one_remote_reads_only_the_gates_target(box, monkeypatch):
    from lhpc.core import stable_lane
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # src/app on the release
    box.use(GH, lib=True)                             # src/lib of the same remote, not installed
    gates = []
    real_gate = stable_lane.update_gate
    monkeypatch.setattr(stable_lane, "update_gate", lambda *a, **k: (gates.append(1), real_gate(*a, **k))[1])
    box.api_calls.clear()
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert gates and len(box.api_calls) == len(gates)  # RED before: the plan read the remote again for src/lib
    assert box.head() == box.head(LIB) == box.c[1]


def test_a_failed_release_read_never_makes_tags_beside_each_other_a_final_refusal(box, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v1.0", box.c[1])
    box.push_tag("v0.9", box.side(box.c[0]))         # a tag beside v1.0's line
    assert box.cli("install", "s", "--source", "pinned", "--yes") == 0
    box.release = "503"                               # the releases API fails: only verified facts may refuse
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    out = capsys.readouterr().out
    assert "try again later" in out and NOT_ONE_LINE not in out      # RED before: the final refusal
    assert (box.head(), box.selector()) == (box.c[0], "pinned")
    box.release = "v1.0"                              # the read recovers: the release names the target
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]


def test_one_line_of_many_tags_is_placed_with_one_history_read(box):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    for n in range(8):
        box.push_tag(f"v1.{n}", box.advance())
    box.git_calls.clear()
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[-1]
    reads = [a for a in box.git_calls if "stable-history" in a[2] and a[3] == "merge-base"]
    assert len(reads) == 1                            # RED before: one read per pair of tagged commits (64)


def test_tags_that_are_not_one_line_of_history_refuse_the_stable_lane_by_name(box, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v1.0", box.c[1])
    box.push_tag("v0.9", box.side(box.c[0]))         # no release; a tag beside v1.0's line
    capsys.readouterr()
    assert box.cli("install", "s", "--source", "stable", "--yes") != 0   # RED before: installed v1.0
    assert NOT_ONE_LINE in capsys.readouterr().out
    assert not (box.dest / ".git").exists()
    assert box.cli("install", "s", "--source", "pinned", "--yes") == 0
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    out = capsys.readouterr().out
    assert NOT_ONE_LINE in out and "try again later" not in out       # final: a retry cannot change it
    assert (box.head(), box.selector()) == (box.c[0], "pinned")


def test_the_release_names_its_commit_beside_a_higher_plain_tag_on_it(box, capsys):
    box.push_tag("v3.0", box.c[2])
    box.release = "v3.0"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # c3
    box.drop_tags("v3.0")
    box.push_tag("v2.9.0", box.c[1])                  # the same commit as the release v2.8.0.7239fe8
    box.release = "v2.8.0.7239fe8"                    # the release moves back to an ancestor
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    assert f"the stable target (stable v2.8.0.7239fe8 @ {box.c[1][:9]}) {NOT_NEWER}" in capsys.readouterr().out
    assert box.head() == box.c[2]


def test_an_installed_commit_with_only_an_unparseable_tag_reads_behind_a_release_in_its_future(box, git):
    box.drop_tags("v2.7.0")
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # c2
    box.drop_tags("v2.8.0.7239fe8")
    git(box.dest, "tag", "-d", "v2.8.0.7239fe8")
    box.push_tag("v3.0-rc1", box.c[1])                # the installed commit's only tag does not parse
    box.push_tag("v3.0", box.c[2])
    box.release = "v3.0"
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # RED before: ERROR ("could not check")
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[2]


@pytest.mark.parametrize("shapes, expected", [
    # each pinned upstream's real tag shapes, OLDEST first on main, every tag annotated (its peeled commit must be
    # taken); no release; moved here from test_source.py / test_auto_install.py (they faked `ls-remote --tags`,
    # which has no history): the index of the commit the rule must pick, None = the default-branch HEAD
    (["v0.4.0", "v112"], 1),                          # loraham-daemon before its first release: a bare number counts
    (["v0.5.1"], 0),                                  # kiss-tnc
    (["1.5.2", "1.8.2-pre"], 0),                      # reticulum: a prerelease name is no version
    (["v1.6.2", "v1.6.3"], 1),                        # meshcore-cli
    (["v2.7.26.54e0d8d", "v2.8.0.7239fe8"], None),    # meshtastic: build-suffixed snapshots count only as a release
    (["v4.35p.08.29", "v4.35s"], None),               # meshcom-firmware: no version shape (plan B: its release)
    ([], None),                                       # no tags at all
], ids=["loraham-daemon", "kiss-tnc", "reticulum", "meshcore-cli", "meshtastic", "meshcom-firmware", "no-tags"])
def test_the_real_manifest_tag_shapes_resolve_on_their_history(box, git, shapes, expected):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    for i, name in enumerate(shapes):
        git(box.work, "tag", "-a", "-m", name, name, box.c[i])
        git(box.work, "push", "-q", "origin", name)
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == (box.c[2] if expected is None else box.c[expected])


# ---- MESHCOM-STABLE slice B: the release of any name (MeshCom's `v4.40a.11.14`) -------------------------------

def _meshcom_tags(box, git, *pairs):
    """Tag each (name, commit index) annotated on main and push it; the fixture's own tags go first."""
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    for name, i in pairs:
        git(box.work, "tag", "-a", "-m", name, name, box.c[i])
        git(box.work, "push", "-q", "origin", name)


def test_a_release_without_a_version_shape_is_the_stable_target(box, git):
    _meshcom_tags(box, git, ("v4.40a.11.07", 0), ("v4.40a.11.14", 1))
    box.release = "v4.40a.11.14"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # RED before: refused NO_VERSION
    assert box.head() == box.c[1]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE


def test_a_dated_build_after_the_installed_one_is_a_stable_update(box, git):
    _meshcom_tags(box, git, ("v4.40a.11.07", 0), ("v4.40a.11.14", 1))
    box.release = "v4.40a.11.07"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # RED before: refused NO_VERSION
    assert box.head() == box.c[0]
    box.release = "v4.40a.11.14"
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[1]


def test_dated_builds_across_a_new_year_follow_the_history_not_the_date(box, git, capsys):
    _meshcom_tags(box, git, ("v4.40a.12.30", 0), ("v4.40a.01.05", 1))   # 01.05 is the NEWER build
    box.release = "v4.40a.12.30"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # RED before: refused NO_VERSION
    box.release = "v4.40a.01.05"
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[1]
    box.release = "v4.40a.12.30"                      # the marker moved back
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0
    assert "is not newer than the installed version: refused" in capsys.readouterr().out
    assert box.head() == box.c[1]


def test_a_switch_from_pinned_onto_a_release_without_a_version_shape(box, git):
    _meshcom_tags(box, git, ("v4.40a.11.14", 1))
    assert box.cli("install", "s", "--source", "pinned", "--yes") == 0   # the pin: c1
    box.release = "v4.40a.11.14"
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0   # RED before: refused NO_VERSION
    assert (box.head(), box.selector()) == (box.c[1], "stable")


@pytest.mark.parametrize("where, expected", [("future", "v5.0"), ("past", "v4.40a.11.14"), ("beside", "v4.40a.11.14")])
def test_a_version_tag_beside_a_release_without_a_version_shape_is_placed_by_history(box, git, where, expected):
    _meshcom_tags(box, git, ("v4.40a.11.14", 1))
    at = {"future": box.c[2], "past": box.c[0], "beside": box.side(box.c[0])}[where]
    git(box.work, "tag", "-a", "-m", "v5.0", "v5.0", at)
    git(box.work, "push", "-q", "origin", "v5.0")
    box.release = "v4.40a.11.14"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # RED before: refused NO_VERSION
    assert box.head() == (at if expected == "v5.0" else box.c[1])


def test_a_release_named_by_a_word_is_the_upstreams_statement(box, git):
    _meshcom_tags(box, git, ("v2.7.0", 0), ("release-2.8.0", 1))
    box.release = "release-2.8.0"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # RED before: refused NO_VERSION
    assert box.head() == box.c[1]


# ---- G2 group 11: the release and the artifact's HEAD, the switch's dry run, an unchecked target ----------------

@pytest.mark.parametrize("lib_first", [False, True], ids=["release-first", "artifact-first"])
def test_a_release_and_an_artifacts_head_on_one_remote_never_share_a_target(box, lib_first):
    box.release = "v2.8.0.7239fe8"                    # S = c2; the default-branch HEAD H = c3
    box.use(GH, lib=True, lib_artifact=True, lib_first=lib_first)   # src/lib declares source.artifact
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert (box.head(), box.head(LIB)) == (box.c[1], box.c[2])   # RED before: both on S, or both on H
    h = box.advance()                                 # a new HEAD, no new tag
    assert box.cli("update", "s", "--yes") == 0       # the gate's S is seeded for src/app only
    assert (box.head(), box.head(LIB)) == (box.c[1], h)          # RED before: src/lib rolled back to S


def test_a_switch_to_stable_checks_freshness_against_its_target_not_the_recorded_lane(box, capsys):
    box.release = "v2.8.0.7239fe8"                    # S = c2, an ancestor of the dev tip D = c3
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.head() == box.c[2]
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable") == 0       # the dry run
    out = capsys.readouterr().out
    assert "app: upstream update-available" in out    # RED before: up-to-date (D against the dev branch's tip)
    assert box.head() == box.c[2]
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]


def test_an_unchecked_target_beside_the_installed_release_reads_try_again_later(box, capsys):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v3.0", box.side(box.c[0]))          # a plain tag beside the installed release's line
    box.release = "503"                               # the releases API fails: the tags alone name v3.0
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0
    out = capsys.readouterr().out
    assert "try again later" in out and "is not in the history" not in out   # RED before: a divergence
    assert box.head() == box.c[1]
    box.release = "v2.8.0.7239fe8"                    # the read recovers: the release is the target, current
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[1]


@pytest.mark.parametrize("how", ["tag-upstream", "tag-withdrawn", "tagged-after-install"])
def test_a_lower_numbered_plain_tag_after_the_installed_version_is_refused_like_before(box, capsys, how):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    if how != "tagged-after-install":
        box.push_tag("v2.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # on v2.0, or on the default-branch HEAD
    assert box.head() == box.c[2]
    if how == "tag-withdrawn":
        box.drop_tags("v2.0")                         # the checkout's own tag still counts
    if how == "tagged-after-install":
        box.push_tag("v2.0", box.c[2])                # only the history mirror knows it
    box.push_tag("v1.0", box.advance())               # the installed commit's child, a lower number, no release
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0       # RED before: moved down to v1.0
    assert NOT_NEWER in capsys.readouterr().out
    assert box.head() == box.c[2]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE
    box.release = "v1.0"                              # GitHub's latest release after it: forward (plan case C2)
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[3]


@pytest.mark.parametrize("where", ["local", "mirror"], ids=["checkout", "history-mirror"])
def test_an_unread_tag_list_of_the_installed_commit_reads_could_not_check_before_a_plain_tag(box, capsys, where):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v2.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    box.push_tag("v3.0", box.advance())               # a higher plain tag after it: forward once its tags are read
    setattr(box, f"fail_{where}_tags", True)
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0       # the floor cannot be read: unknown, never a move
    assert "try again later" in capsys.readouterr().out
    assert box.head() == box.c[2]
    box.cli("source-check", "s")
    assert box.status() == su.ERROR
    setattr(box, f"fail_{where}_tags", False)
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[3]


def test_an_equal_version_after_the_installed_one_is_a_forward_move(box):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v2.0", box.c[1])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]
    box.push_tag("2.0", box.c[2])                     # the same version on a later commit, no release
    assert box.cli("update", "s", "--yes") == 0       # the floor refuses only a LOWER version, as before
    assert box.head() == box.c[2]


def test_a_plain_tag_after_an_older_release_still_meets_the_floor(box, capsys):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v0.5", box.c[0])
    box.release = "v0.5"                              # an older release, an ancestor
    box.push_tag("v2.0", box.c[1])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]                     # v2.0, a plain tag in the release's future
    box.push_tag("v1.0", box.c[2])                    # a lower-numbered plain tag after it
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0       # a release elsewhere never lifts the floor
    assert NOT_NEWER in capsys.readouterr().out
    assert box.head() == box.c[1]


def _v112_then_a_release_and_a_plain_tag_after_it(box):
    box.drop_tags("v2.7.0", "v2.8.0.7239fe8")
    box.push_tag("v112", box.c[0])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0   # only v112 exists: c1
    box.push_tag("v1.4.0", box.c[1])
    box.release = "v1.4.0"
    box.push_tag("v1.4.1", box.c[2])                  # a tag after the release, not released yet: the target


def test_a_box_the_floor_holds_back_is_offered_the_release_in_its_future(box, capsys):
    _v112_then_a_release_and_a_plain_tag_after_it(box)
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # RED before: up to date (1.4.1 < 112, the floor)
    assert su.view(Paths(runtime_root=box.rt))["components"]["app"]["upstream_head"] == box.c[1]   # THIS box's target
    capsys.readouterr()
    assert box.cli("update", "s") == 0                # the dry run
    assert "app: upstream update-available" in capsys.readouterr().out
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[1]                     # the release, not the plain tag the floor refused
    box.cli("source-check", "s")
    assert box.status() == su.BEHIND                  # on v1.4.0, v1.4.1 is forward
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[2]


def test_the_release_one_checkout_falls_back_to_is_not_the_target_of_another_path(box):
    _v112_then_a_release_and_a_plain_tag_after_it(box)
    box.use(GH, lib=True)                             # src/lib of the same remote, not installed
    assert box.cli("update", "s", "--source", "stable", "--yes") == 0   # RED before: refused, not newer
    assert (box.head(), box.head(LIB)) == (box.c[1], box.c[2])   # src/app the release, src/lib the remote's target


def test_an_unread_ancestry_of_the_release_fallback_reads_could_not_check(box, capsys):
    _v112_then_a_release_and_a_plain_tag_after_it(box)
    box.fail_ancestry_to = box.c[1]                   # only "is the release in the installed commit's future?" fails
    capsys.readouterr()
    assert box.cli("update", "s", "--yes") != 0
    out = capsys.readouterr().out
    assert "try again later" in out and "is not newer" not in out
    assert box.head() == box.c[0]
    box.cli("source-check", "s")
    assert box.status() == su.ERROR
    box.fail_ancestry_to = ""
    assert box.cli("update", "s", "--yes") == 0
    assert box.head() == box.c[1]


# ---- U6: one lane verdict per source (current / update available / update LHPC first / could not check) ----

def _versions_line(box, capsys, cid="app"):
    """The component's line of `lhpc status --versions` (local only)."""
    capsys.readouterr()
    box.cli("status", "--versions")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.split()[:1] == [cid]]
    assert len(lines) == 1, lines
    return lines[0]


def _check_detail(box, capsys, cid="app"):
    """The component's detail of a public `lhpc source-check s`, and the whole output."""
    capsys.readouterr()
    box.cli("source-check", "s")
    out = capsys.readouterr().out
    lines = [ln.split(": ", 1)[1] for ln in out.splitlines() if ln.strip().startswith(f"{cid}: ")]
    assert len(lines) == 1, out
    return lines[0], out


def _install_pill(box, web):
    """The Installed-version pill of stack s, from the rendered stack body."""
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    doc = htmlq.parse(client.get("/stacks/s/body").get_data(as_text=True))
    pills = doc.within(doc.by_id("stack-install-s")).find("span", class_="ver")
    return " ".join(p.text.strip() for p in pills)


def test_a_dev_source_at_its_tip_reads_current_in_status_and_check(box, capsys):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.head() == box.c[2]                    # the tip, not the pin
    detail = _check_detail(box, capsys)[0]
    line = _versions_line(box, capsys)
    assert "differs" not in line and "current" in line.split(), line   # RED before: status said "differs"
    assert detail == "current"


def test_a_stable_source_at_its_resolved_tag_reads_current_in_status_and_check(box, capsys):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]
    detail = _check_detail(box, capsys)[0]
    line = _versions_line(box, capsys)
    assert "differs" not in line and "current" in line.split(), line
    assert detail == "current"


def test_control_a_pinned_source_at_its_pin_reads_current_in_both(box, capsys):
    assert box.cli("install", "s", "--yes") == 0
    assert box.head() == box.c[0]
    assert _check_detail(box, capsys)[0] == "current"
    assert "current" in _versions_line(box, capsys).split()


def _status_src(box, capsys, cid="app"):
    """The `src` word of the component's line in `lhpc status s`."""
    capsys.readouterr()
    box.cli("status", "s")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.split()[:1] == [cid]]
    assert len(lines) == 1, lines
    return lines[0].split(" src ", 1)[1].strip()


def _info_card(box, web):
    """The "Source / profile" row of stack s's info card, from the rendered stack body."""
    client = web(system=RealSystem(), manifest=box.manifest, paths=Paths(runtime_root=box.rt))
    doc = htmlq.parse(client.get("/stacks/s/body").get_data(as_text=True))
    card = doc.within(doc.by_id("stack-info-s"))
    rows = [card.within(tr).find("td")[0].text for tr in card.find("tr")
            if [th.text for th in card.within(tr).find("th")] == ["Source / profile"]]
    assert len(rows) == 1, rows
    return rows[0]


@pytest.mark.parametrize("lane", ["dev", "stable"])
def test_a_source_at_its_lane_target_reads_current_in_status_and_on_the_info_card(box, capsys, web, lane):
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", lane, "--yes") == 0
    assert box.head() != box.c[0]                    # not at the pin
    assert _check_detail(box, capsys)[0] == "current"
    assert _status_src(box, capsys) == "current"                         # RED before: "differs"
    assert _info_card(box, web) == "current / installed-unvalidated"     # RED before: "differs / candidate-available"


def test_a_dev_source_behind_its_tip_offers_the_candidate_on_every_surface(box, capsys, web):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    box.advance()
    assert _check_detail(box, capsys)[0] == "update available"
    assert "update available" in _versions_line(box, capsys)
    assert _status_src(box, capsys) == "update available"                # RED before: "differs"
    assert _info_card(box, web) == "update available / candidate-available"
    assert _install_pill(box, web) == "update available"


def test_a_source_not_yet_checked_reads_unchecked_not_a_candidate(box, capsys, web):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0      # no source-check ran
    assert _status_src(box, capsys) == "unchecked"                       # RED before: "differs"
    assert _info_card(box, web) == "unchecked / installed-unvalidated"   # RED before: "differs / candidate-available"


def test_control_a_dirty_checkout_keeps_its_probe_fact_on_every_surface(box, capsys, web):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    _check_detail(box, capsys)
    (box.dest / "f").write_text("local edit\n")
    assert _status_src(box, capsys) == "dirty"
    assert "dirty" in _versions_line(box, capsys).split()
    assert _info_card(box, web) == "dirty / locally-modified"


def _fail_branch_probe(monkeypatch):
    """`git ls-remote <remote> main` (the dev and pinned probe) fails; the tag reads of the stable lane do not."""
    inner = backends.RealCommandRunner.run

    def run(self, argv, timeout, cwd=None, env=None):
        if argv[:2] == ["git", "ls-remote"] and argv[-1] == "main":
            return CommandResult(128, "", "fatal: unable to access the remote")
        return inner(self, argv, timeout, cwd=cwd, env=env)
    monkeypatch.setattr(backends.RealCommandRunner, "run", run)


def test_a_probe_that_fails_reads_could_not_check(box, capsys, web, monkeypatch):
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    _fail_branch_probe(monkeypatch)
    assert _check_detail(box, capsys)[0] == "could not check"     # RED before: "error"
    assert _install_pill(box, web) == "could not check"           # RED before: "check failed"


def test_an_update_beside_a_failed_check_leads_the_summary(box, capsys, monkeypatch):
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--source", "dev", "--yes") == 0
    assert box.cli("update", "lib", "--source", "stable", "--yes") == 0
    box.push_tag("v2.9.0", box.c[2])                 # lib (stable) has a verified update
    _fail_branch_probe(monkeypatch)                  # app (dev) cannot be checked
    detail, out = _check_detail(box, capsys, "lib")
    summary = out.splitlines()[0]
    # RED before: the summary led with "could not be checked" (errors first)
    assert 0 <= summary.find("update") < summary.find("could not be checked"), summary
    assert detail == "update available"


def _lhpc_upstream(box, git, make_repo, monkeypatch, pin=None, text=None, ahead=True):
    """A local LHPC checkout (HEAD ships this LHPC's manifest) and the self-update cache naming the head its last
    check fetched: a commit AHEAD of HEAD on a side branch whose shipped manifest pins `app` at `pin` (or holds
    `text`; `text=""` removes the file). `ahead=False`: the fetched head IS HEAD, its manifest pinning `pin`
    (a box whose own manifest differs from the shipped one). Returns the fetched head."""
    from lhpc.core import selfupdate
    rel, n = "lhpc/data/manifest.example.toml", 0
    while (box.rt.parent / f"lhpc-checkout-{n}").exists():          # each call: its own checkout
        n += 1
    repo = box.lhpc_repo = box.rt.parent / f"lhpc-checkout-{n}"
    pinned = lambda c: box.manifest.read_text().replace(f'pin_commit = "{box.c[0]}"', f'pin_commit = "{c}"')  # noqa: E731
    shipped = pinned(pin) if text is None else text
    if not ahead:
        head = make_repo(repo, {rel: shipped})
    else:
        make_repo(repo, {rel: pinned(box.c[0])})
        git(repo, "checkout", "-q", "-b", "up")
        (repo / "NEXT").write_text("the next LHPC\n")                  # never an empty commit
        git(repo, "add", "NEXT")
        if shipped == "":
            git(repo, "rm", "-q", rel)
        else:
            (repo / rel).write_text(shipped)
            git(repo, "add", rel)
        git(repo, "commit", "-qm", "the next LHPC")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "-")
    monkeypatch.setattr(selfupdate, "repo_root", lambda: repo)
    selfupdate.write_cache(Paths(runtime_root=box.rt),
                           {"schema_version": selfupdate.CACHE_SCHEMA_VERSION,
                            "upstream": {"ok": True, "upstream_head": head, "upstream_head_short": head[:9]}})
    return head


def test_a_pinned_source_whose_next_lhpc_pins_another_commit_reads_update_lhpc_first(box, capsys, git,
                                                                                    make_repo, monkeypatch, web):
    assert box.cli("install", "s", "--yes") == 0                 # at this LHPC's pin c1
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    assert _check_detail(box, capsys)[0] == "update LHPC first"   # RED before: "up to date"
    # the same verdict on every reader while its evidence holds (today's pin and fetched LHPC passed to the rule)
    assert "update LHPC first" in _versions_line(box, capsys)
    assert _install_pill(box, web) == "update LHPC first"


def test_a_pinned_source_behind_its_own_pin_reads_update_available_beside_a_next_lhpc(box, capsys, git, make_repo,
                                                                                     monkeypatch):
    assert box.cli("install", "s", "--yes") == 0                 # at c1
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[2])   # the next LHPC pins c3
    box.manifest.write_text(box.manifest.read_text().replace(f'pin_commit = "{box.c[0]}"', f'pin_commit = "{box.c[1]}"'))
    assert _check_detail(box, capsys)[0] == "update available"   # this LHPC pins c2: the update is here, not next


def test_a_summary_without_an_update_leads_with_lhpc_first_before_could_not_check(box, capsys, git, make_repo,
                                                                                   monkeypatch):
    box.use(GH, lib=True)
    assert box.cli("install", "s", "--yes") == 0                 # app and lib pinned at c1
    assert box.cli("update", "lib", "--source", "dev", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])   # app: update LHPC first
    _fail_branch_probe(monkeypatch)                              # lib (dev): could not check
    first = _check_detail(box, capsys, "lib")[1].splitlines()[0]
    assert "need LHPC updated first" in first and first.find("LHPC") < first.find("could not be checked"), first


def test_control_the_same_pin_upstream_gives_no_hint(box, capsys, git, make_repo, monkeypatch):
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[0])
    assert _check_detail(box, capsys)[0] == "current"


def test_an_unreadable_upstream_manifest_gives_no_hint(box, capsys, git, make_repo, monkeypatch):
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, text="")    # the fetched head ships no manifest
    assert _check_detail(box, capsys)[0] == "current"            # no hint, never "could not check"


def test_a_cached_head_not_ahead_of_this_checkout_gives_no_hint(box, capsys, git, make_repo, monkeypatch):
    """A self-update check older than the running checkout (it moved on since) names no next LHPC."""
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    repo = box.lhpc_repo
    (repo / "later.txt").write_text("x\n")
    git(repo, "add", "later.txt")
    git(repo, "commit", "-qm", "the running checkout moved on, not over the cached head")
    assert _check_detail(box, capsys)[0] == "current"


def test_a_cached_head_equal_to_this_checkout_gives_no_hint(box, capsys, git, make_repo, monkeypatch):
    """G1 B4: the fetched head is the running checkout itself (nothing newer) while this box's manifest pins
    differently from the shipped one: no next LHPC exists, so no hint."""
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1], ahead=False)
    assert _check_detail(box, capsys)[0] == "current"            # RED before: "update LHPC first"


def test_the_update_plan_of_a_source_waiting_for_lhpc_says_nothing_to_fetch(box, capsys, git, make_repo,
                                                                            monkeypatch):
    """`lhpc update s` (the plan) reads "update LHPC first" as up to date: the source is at this LHPC's pin."""
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    capsys.readouterr()
    box.cli("update", "s")
    assert "app: upstream up-to-date" in capsys.readouterr().out


def test_a_malformed_upstream_manifest_gives_no_hint(box, capsys, git, make_repo, monkeypatch):
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, text="[[stack]\nid = broken\n")
    assert _check_detail(box, capsys)[0] == "current"


def test_a_second_check_of_one_service_reads_the_newer_self_update_check(box, git, make_repo, monkeypatch):
    """G1 B2: one service checks twice; the self-update check between them fetched an LHPC with another pin.
    The second check must read it (a service the web keeps for its background checks lives that long)."""
    assert box.cli("install", "s", "--yes") == 0
    svc = box.svc()
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[0])
    svc.source_check("s")
    assert box.status() == su.UP_TO_DATE
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    svc.source_check("s")
    assert box.status() == su.LHPC_FIRST                          # RED before: the first read was kept


def test_a_moved_manifest_pin_turns_a_stored_verdict_unchecked(box, capsys, git, make_repo, monkeypatch, web):
    """G1 B3: after the LHPC update applied the new pin (this checkout unchanged), the verdict stored against the
    old pin no longer describes the source: `status --versions` and the page read "unchecked" until a check."""
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    assert _check_detail(box, capsys)[0] == "update LHPC first"
    box.manifest.write_text(box.manifest.read_text().replace(f'pin_commit = "{box.c[0]}"', f'pin_commit = "{box.c[1]}"'))
    line = _versions_line(box, capsys)
    assert "unchecked" in line.split(), line                      # RED before: "update LHPC first"
    assert _install_pill(box, web) == ""                          # the page: no lane pill, "unchecked"


def test_a_pinned_source_needs_no_remote_read(box, capsys, git, make_repo, monkeypatch):
    """G1 N1: the pinned lane's target is this LHPC's manifest pin, so its verdict is local: a remote that
    cannot be read does not hide "update LHPC first" (or "current") behind "could not check"."""
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    _fail_branch_probe(monkeypatch)
    assert _check_detail(box, capsys)[0] == "update LHPC first"   # RED before: "could not check"


# ---- U6 gate-1 round 2: one rule for every stored verdict (head, manifest pin, the fetched LHPC) ----

def test_a_stored_verdict_reads_unchecked_once_the_self_update_check_fetched_another_lhpc(box, capsys, git,
                                                                                       make_repo, monkeypatch, web):
    """G1 R2 B1(A): "update LHPC first" stands on the self-update check that produced it. A later self-update check
    that fetched another LHPC (this one pins the same commit) leaves the stored verdict without its evidence:
    `status --versions` and the page read "unchecked" until a check, never the old hint."""
    assert box.cli("install", "s", "--yes") == 0
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[1])
    assert _check_detail(box, capsys)[0] == "update LHPC first"
    _lhpc_upstream(box, git, make_repo, monkeypatch, box.c[0])   # the self-update check fetched again
    line = _versions_line(box, capsys)
    assert "unchecked" in line.split(), line                      # RED before: "update LHPC first"
    assert _install_pill(box, web) == ""                          # the page: no lane pill, "unchecked"
    assert _check_detail(box, capsys)[0] == "current"             # a check computes it again


def test_a_verdict_stored_before_u6_reads_unchecked(box, capsys, web):
    """G1 R2 B1(B): an entry without the evidence its verdict needs (written before one verdict per lane: no
    manifest pin, no fetched LHPC) is not shown as a verdict: "unchecked" until the next check."""
    assert box.cli("install", "s", "--yes") == 0
    su.record(Paths(runtime_root=box.rt), {"app": {"remote": GH, "source_path": REL, "local_head_at_check": box.c[0],
                                                   "upstream_head": box.c[0], "status": su.UP_TO_DATE}})
    line = _versions_line(box, capsys)
    assert "unchecked" in line.split(), line                      # RED before: "current"
    assert _install_pill(box, web) == ""                          # RED before: "current"


def test_a_component_without_a_source_is_listed_by_status_versions(box, capsys):
    """G1 R2 B2 (pinned, green before): `status --versions` reads a component's pin only behind its source check;
    a component without a source is skipped, the command succeeds."""
    box.manifest.write_text(box.manifest.read_text() + '[[stack.component]]\nid = "tool"\nname = "tool"\n'
                            'kind = "service"\nrun_argv = ["true"]\nreadiness = "process"\n')
    assert box.cli("install", "s", "--yes") == 0
    capsys.readouterr()
    assert box.cli("status", "--versions") == 0
    out = capsys.readouterr().out
    assert any(ln.split()[:1] == ["app"] for ln in out.splitlines()) and "tool " not in out, out

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
        fail_local_tags = False
        fail_head = False
        fail_fetch = False
        api_calls: list = []
        git_calls: list = []
    b = Box()
    real_run = backends.RealCommandRunner.run

    def run(self, argv, timeout, cwd=None, env=None):
        if argv[0] == "git":
            b.git_calls.append(argv)
        if b.fail_local_tags and argv[:2] == ["git", "-C"] and argv[3:5] == ["tag", "--points-at"]:
            return CommandResult(128, "", "fatal: unable to read tags")
        if b.fail_head and argv[:2] == ["git", "-C"] and argv[3:5] == ["rev-parse", "HEAD"]:
            return CommandResult(128, "", "fatal: not a git repository")
        if b.fail_fetch and argv[:2] == ["git", "-C"] and argv[3] == "fetch":
            return CommandResult(128, "", "fatal: unable to access the remote")
        if argv[0] != "curl":
            return real_run(self, argv, timeout, cwd=cwd, env=env)
        b.api_calls.append(argv)
        if b.release in ("404", "429"):
            return CommandResult(0, f'{{"message": "x"}}\n{b.release}', "")
        if b.release.startswith("{"):                 # a raw 200 body (no or an unusable tag_name)
            return CommandResult(0, b.release + "\n200", "")
        return CommandResult(0, json.dumps({"tag_name": b.release}) + "\n200", "")
    monkeypatch.setattr(backends.RealCommandRunner, "run", run)

    rt = tmp_path / "rt"
    (rt / "config").mkdir(parents=True)
    manifest = tmp_path / "m.toml"

    def use(remote, lib=False, lib_path=LIB):
        manifest.write_text(
            '[[stack]]\nid = "s"\nname = "s"\nmain = "app"\n'
            '[[stack.component]]\nid = "app"\nname = "app"\nkind = "service"\n'
            'run_argv = ["true"]\nreadiness = "process"\n'
            f'[stack.component.source]\npath = "{REL}"\nremote = "{remote}"\nbranch = "main"\n'
            f'pin_commit = "{c1}"\n'
            + ('[[stack.component]]\nid = "lib"\nname = "lib"\nkind = "library"\ntx_capable = false\n'
               f'[stack.component.source]\npath = "{lib_path}"\nremote = "{remote}"\nbranch = "main"\n'
               f'pin_commit = "{c1}"\n' if lib else ""))
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


@pytest.mark.parametrize("names, release, expected", [
    (["v2.7.0", "v2.8.0.7239fe8"], "v2.8.0.7239fe8", "v2.8.0.7239fe8"),   # a newer release beats the plain tag
    (["v2.8.0", "v2.8.0.7239fe8"], "v2.8.0.7239fe8", "v2.8.0"),   # same leading version: the plain tag
    (["v1.2", "v1.1.9"], "", "v1.2"),                     # v1.2 = v1.2.0
    (["v2.8.0", "v2.8.0.9"], "", "v2.8.0"),               # a fourth part that is no commit suffix: no version
    (["v2.8.0", "v2.8.0.1234567"], "", "v2.8.0"),         # an all-digit commit suffix: no plain tag
    (["v2.7.0", "release-2.8.0"], "release-2.8.0", "v2.7.0"),   # no leading version: not a candidate
    (["v2.7.0"], "v9.0", "v2.7.0"),                       # a release tag the remote does not list
    (["nightly", "1.8.2-pre"], "", ""),                   # no candidate: the default-branch HEAD
])
def test_the_stable_key_table(names, release, expected):
    from lhpc.core import stable_lane         # new in U1: imported here, so the red-before runs the rest
    assert stable_lane.stable_tag(names, release) == expected
    assert stable_lane.version_of("v1.2") == stable_lane.version_of("v1.2.0") == (1, 2)


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


@pytest.mark.parametrize("body", ["{}", '{"tag_name": ["v3.0"]}', '{"tag_name": ""}'])   # a non-version name: NO_VERSION
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


def test_an_older_plain_tag_beside_a_newer_release_only_name_still_blocks_a_downgrade(box, capsys):
    box.push_tag("v2.6.0", box.c[1])                   # the installed commit: ["v2.6.0", "v2.8.0.7239fe8"]
    box.release = "v2.8.0.7239fe8"
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[1]
    box.push_tag("v2.7.1", box.c[2])
    box.release = "v2.7.1"                             # the latest release moved back below 2.8.0
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: downgraded to v2.7.1
    assert "is not newer than the installed version: refused" in capsys.readouterr().out
    assert box.head() == box.c[1]
    box.cli("source-check", "s")
    assert box.status() == su.UP_TO_DATE


def test_an_unreadable_installed_tag_list_refuses_the_update(box, git, capsys):
    box.push_tag("v3.0", box.c[2])
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    git(box.work, "push", "-q", "origin", "--delete", "v3.0")      # only the checkout still knows v3.0
    box.push_tag("v2.9", box.c[1])
    box.release = "v2.9"
    box.fail_local_tags = True                                     # ... and it cannot be read now
    box.cli("source-check", "s")
    assert box.status() == su.ERROR                                # RED before: up to date read as no tags
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: downgraded to v2.9
    assert "could not be checked — try again later" in capsys.readouterr().out
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
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0   # RED before: downgraded to v2.9
    assert "could not be checked — try again later" in capsys.readouterr().out
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
    assert "is not newer than the installed version: refused" in out and "try again later" not in out
    assert box.head() == box.c[1]


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
    assert not [a for a in box.git_calls if "merge-base" in a or "fetch" in a]   # refused before any comparison
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

    def resolve(run, remote):
        if state["apply"]:
            reads.append(state["locked"])
        return real_resolve(run, remote)
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


# ---- a stable lane without version-shaped releases (MeshCom's `v4.40a.11.14`) ----------------------------------

NO_VERSION = "up/app publishes no version-shaped releases; use --source pinned or dev"


def test_a_stable_install_of_a_source_without_version_shaped_releases_is_refused(box, capsys):
    box.release = "v4.40a.11.14"                      # the upstream's latest release does not parse
    capsys.readouterr()
    assert box.cli("install", "s", "--source", "stable", "--yes") != 0   # RED before: installed unverified
    assert NO_VERSION in capsys.readouterr().out
    assert not (box.dest / ".git").exists()


def test_a_stable_update_of_a_source_without_version_shaped_releases_names_pinned_or_dev(box, capsys):
    assert box.cli("install", "s", "--source", "pinned", "--yes") == 0
    box.release = "v4.40a.11.14"
    capsys.readouterr()
    assert box.cli("update", "s", "--source", "stable", "--yes") != 0
    out = capsys.readouterr().out
    assert NO_VERSION in out and "try again later" not in out   # RED before: "could not be checked — try again later"
    assert (box.head(), box.selector()) == (box.c[0], "pinned")


def test_control_a_release_less_source_still_installs_its_default_branch_head(box, git):
    git(box.work, "push", "-q", "origin", "--delete", "v2.7.0", "v2.8.0.7239fe8")
    box.release = "404"                               # no release at all: the default-branch HEAD (L6)
    assert box.cli("install", "s", "--source", "stable", "--yes") == 0
    assert box.head() == box.c[2]

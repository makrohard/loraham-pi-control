"""The /stacks page and the source check: a GET renders the CACHED verdict and never probes a
remote; the POST /source-check/<target> route is the one place the console probes, and it lands
the operator on the stack's Install section. It is not a lifecycle action."""

import pytest

import htmlq
from lhpc.core import stackupdates as su
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
from lhpc.core.services import ControllerService

DAEMON_REMOTE = "https://github.com/makrohard/LoRaHAM_Daemon.git"
DAEMON_BRANCH = "main"
RADIOLIB_REMOTE = "https://github.com/jgromes/RadioLib"
A = "a" * 40
B = "b" * 40


def _ls_remote(remote, ref, sha):
    return {("git", "ls-remote", remote, ref): CR(0, f"{sha}\trefs/heads/{ref}\n", "")}


def _git_src(src, sha):
    """A clean git checkout at `sha` (both the head read and the status the probe needs)."""
    a = str(src)
    return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
            ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}


def _repo(tmp_path, rel):
    """An installed git source: a real dir (the probe's `is_dir()` guard) that `FakeSystem.paths`
    also knows (`fs.exists`, consulted before a head is read)."""
    d = tmp_path / rel
    (d / ".git").mkdir(parents=True)
    return d


def _fs_paths(*dirs):
    return {p for d in dirs for p in (str(d), f"{d}/.git")}


def _entry_for(status, at):
    return {"remote": DAEMON_REMOTE, "source_path": "src/x",
            "local_head_at_check": at, "upstream_head": B, "status": status}


def _probed(fake):
    return any("ls-remote" in " ".join(call) for call in fake.calls)


def test_every_top_level_row_has_an_actions_overlay(web):
    # The logs / "Update" links live in a .row-actions overlay OUTSIDE each row's <summary>
    # (a11y). Every top-level row (controller + each stack) has one.
    doc = htmlq.parse(web().get("/stacks").get_data(as_text=True))
    assert len(doc.find("div", class_="row-actions")) >= 2      # controller row + at least one stack


def test_get_stacks_never_probes_even_with_a_populated_cache(web, tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    su.record(Paths(runtime_root=tmp_path), {"loraham-daemon": _entry_for(su.BEHIND, A)}, now=1000)
    fake = FakeSystem(commands=_git_src(ds, A), paths=_fs_paths(ds))
    web(system=fake.system).get("/stacks")
    assert not _probed(fake)


@pytest.mark.contract
def test_source_check_post_does_probe_and_lands_on_install(web, csrf, tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    rl = _repo(tmp_path, "src/RadioLib")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(ds, A),
            **_ls_remote(RADIOLIB_REMOTE, "master", A), **_git_src(rl, A)}
    fake = FakeSystem(commands=cmds, paths=_fs_paths(ds, rl))
    c = web(system=fake.system)
    r = c.post("/source-check/daemon", data={"_csrf": csrf(c)})
    assert r.status_code == 302
    assert "open=daemon" in r.headers["Location"] and "inst=daemon" in r.headers["Location"]
    assert r.headers["Location"].endswith("#stack-install-daemon")
    assert _probed(fake)                                                # it DID probe
    assert su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]["status"] == su.BEHIND


def test_source_check_component_target_returns_to_its_stack(web, csrf, tmp_path):
    _repo(tmp_path, "src/RadioLib")
    c = web()
    r = c.post("/source-check/radiolib", data={"_csrf": csrf(c)})
    assert r.status_code == 302 and r.headers["Location"].endswith("#stack-install-daemon")


@pytest.mark.contract
def test_source_check_requires_csrf_and_a_known_target(web, csrf):
    fake = FakeSystem()
    c = web(system=fake.system)
    assert c.post("/source-check/daemon").status_code == 400          # no CSRF token
    assert c.post("/source-check/nope", data={"_csrf": csrf(c)}).status_code == 404
    assert not _probed(fake)


def test_source_check_is_not_an_action_op():
    # It mutates nothing but the cache marker; it must not enter the lifecycle dispatch.
    assert "source-check" not in ControllerService.WEB_ACTIONS
    assert "check" not in ControllerService.WEB_ACTIONS

"""Applying a self-update: the cleanup an overwrite runs, the firewall follow-up it reports, and
the self-update lock it holds against a concurrent apply or check.

The update never changes saved configuration: a value saved equal to an old default keeps that
value (tests/install/test_selfupdate_interruption.py pins the apply's durable writes).
"""
from __future__ import annotations

import fcntl

import pytest
import stores_fixture

import gitrepo
from lhpc.core import runtime_fs as _rfs
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, RealSystem


def _seed_manifest(work, up, text) -> str:
    """Commit `text` as work/manifest.toml and resync the disposable upstream clone. Returns the new
    work HEAD. Call BEFORE any `gitrepo.upstream_commit(up)`."""
    (work / "manifest.toml").write_text(text)
    gitrepo.git(work, "add", "manifest.toml"); gitrepo.git(work, "commit", "-m", "manifest")
    gitrepo.git(work, "push", "-q", "origin", "main")
    gitrepo.git(up, "fetch", "-q", "origin"); gitrepo.git(up, "reset", "-q", "--hard", "origin/main")
    return gitrepo.git(work, "rev-parse", "HEAD")


def _svc(tmp_path, work):
    from lhpc.core.services import ControllerService
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (rt / "config" / "files").mkdir(parents=True, exist_ok=True)
    man = work / "manifest.toml"
    return ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt)), man, rt


def _rf_manifest(ropt="OLD", fopt="FOLD"):
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{ropt}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [stack.component.config_file]\n  path="{runtime}/config/files/x.conf"\n  fmt="env"\n'
        f'    [[stack.component.config_file.param]]\n    name="fopt"\n    key="FOPT"\n    default="{fopt}"\n')


def _svc_rf(tmp_path, work, monkeypatch, *, ropt="OLD", fopt="FOLD"):
    """A single-component stack 's' with a RUN param `ropt` (+ `keep`) and a FILE param `fopt`. The
    manifest is SOURCE-tracked in `work` (commit BEFORE any `gitrepo.upstream_commit(up)`)."""
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, tmp_path / "up", _rf_manifest(ropt, fopt))
    return _svc(tmp_path, work)


# --- a nested untracked repository, and a cleanup command that fails --------------------------

def test_nested_untracked_repo_blocks_then_overwrite_removes(env):
    new_head = gitrepo.upstream_commit(env["up"])
    w = env["work"]
    (w / "nested").mkdir()                                        # a NESTED untracked git repo
    gitrepo.git(w / "nested", "init", "-b", "main", ".")
    (w / "nested" / "f.txt").write_text("x")
    assert selfupdate.local_state(env["sys"])["dirty"] is True    # nested repo -> untracked -> dirty
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"] is False   # default: refuse
    assert (w / "nested").exists()
    res = selfupdate.apply_update(env["sys"], env["paths"], force=True)       # clean -ffd removes it
    assert res["ok"] and not res.get("cleanup_failed")
    assert not (w / "nested").exists() and selfupdate.local_state(env["sys"])["head"] == new_head


def _fake_commands(root, *, clean_rc):
    def R(rc=0, out="", err=""):
        return CommandResult(returncode=rc, stdout=out, stderr=err)
    r = str(root)
    return {
        ("git", "-C", r, "rev-parse", "HEAD"): R(out="aaaaaaaaa\n"),
        ("git", "-C", r, "rev-parse", "--abbrev-ref", "HEAD"): R(out="main\n"),
        ("git", "-C", r, "status", "--porcelain"): R(out="?? junk\n"),         # dirty -> needs force
        ("git", "-C", r, "config", "--get", "remote.origin.url"): R(out="https://example.invalid/lhpc.git\n"),
        ("git", "-C", r, "remote", "get-url", "origin"): R(out="https://example.invalid/lhpc.git\n"),
        ("git", "-C", r, "fetch", "--quiet", "--force", "origin", "--",
         "refs/heads/main:refs/remotes/origin/main"): R(),
        ("git", "-C", r, "rev-parse", "origin/main"): R(out="bbbbbbbbb\n"),
        ("git", "-C", r, "show", "origin/main:lhpc/version.py"): R(out='__version__ = "0.1.2"\n'),
        ("git", "-C", r, "diff", "--name-only", "HEAD..origin/main", "--", "pyproject.toml"): R(),
        ("git", "-C", r, "reset", "--hard", "origin/main"): R(),               # reset SUCCEEDS
        ("git", "-C", r, "clean", "-ffd"): R(rc=clean_rc, err="cannot unlink 'x': Permission denied"),
    }


@pytest.mark.contract
def test_apply_cleanup_failure_is_truthful_partial(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    root = tmp_path / "repo"; root.mkdir()
    monkeypatch.setattr(selfupdate, "repo_root", lambda: root)
    fs = FakeSystem(commands=_fake_commands(root, clean_rc=1))
    res = selfupdate.apply_update(fs.system, gitrepo.runtime_paths(tmp_path), force=True)
    assert res["ok"] is True and res["cleanup_failed"] is True    # reset worked, cleanup did not
    assert "Permission denied" in res["cleanup_error"]


def test_service_maps_cleanup_failure_to_partial(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    monkeypatch.setattr(selfupdate, "apply_update", lambda *a, **k: {
        "ok": True, "cleanup_failed": True, "cleanup_error": "cannot unlink 'x'",
        "message": "aligned, cleanup incomplete", "deps_changed": False})
    res = svc.self_update_apply(force=True)
    assert res.ok is False and res.data.get("cleanup_failed") is True   # partial -> not a plain success
    assert res.next_commands, res                                  # the restart instructions
    assert any("cannot unlink" in d for d in res.details)         # the cleanup error passes through


def _status_reads(svc, monkeypatch, reason):
    # Stubbed: the firewall status (the root-written receipt under /run and the units under /etc
    # cannot be set up here); `reason` is what it reads after the advance ("update-required": a fresh
    # verified receipt of the old helper, `helper_stale`).
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"reason": reason, "helper_stale": reason == "update-required"})


@pytest.mark.parametrize("reason, remote", [("update-required", True),
                                            ("update-required", False), ("active", True)])
def test_an_update_leaving_the_installed_helper_stale_says_reapply_before_reboot(
        tmp_path, monkeypatch, reason, remote):
    """An advance after which the installed firewall helper is not the packaged one (the status
    reads update-required) leaves the next boot starting the console loopback-only: the update
    result says so, with the commands, while the operator can still act. A status that reads
    active adds nothing (the ("active", True) case is a control: no red-before)."""
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0" if remote else "127.0.0.1",
                                 port=8443, remote_exposed=remote)
    svc._invalidate_config()
    _status_reads(svc, monkeypatch, reason)
    monkeypatch.setattr(selfupdate, "apply_update", lambda *a, **k: {
        "ok": True, "message": "updated", "deps_changed": False})
    # The installed integration lives under /etc/lhpc; stub its presence.
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    res = svc.self_update_apply()
    changed = reason == "update-required"
    assert res.ok and res.data["firewall_reapply_required"] is changed
    notes = [d for d in res.details if "installed firewall helper is not" in d]
    assert bool(notes) is changed
    if changed:
        apply_sh = svc._paths.under("config/files/firewall/firewall-apply.sh")
        assert "before you reboot" in notes[0] and f"sudo bash {apply_sh}" in notes[0]
        assert "lhpc firewall --script" in notes[0] and "lhpc webserver apply" in notes[0]
        assert ("LOOPBACK-ONLY" in notes[0]) is remote


def test_the_update_and_the_status_agree_on_the_firewall_reapply(tmp_path, monkeypatch):
    """Finding 134: the update changes the packaged helper, but the installed one is already that
    build (re-applied, or left by a move of the checkout), so the status reads active with no
    notice — the update result must not ask for a re-apply either."""
    from lhpc.core import firewall as fwm
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch)
    gitrepo.upstream_commit(up)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "firewall_helper.py").write_text("# helper OLD\n")
    monkeypatch.setattr(fwm, "__file__", str(pkg / "firewall.py"))   # helper_source() reads pkg/

    def advance(*a, **k):
        (pkg / "firewall_helper.py").write_text("# helper NEW\n")
        return {"ok": True, "message": "Update applied.", "deps_changed": False}
    monkeypatch.setattr(selfupdate, "apply_update", advance)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    _status_reads(svc, monkeypatch, "active")
    res = svc.self_update_apply()
    assert res.ok and svc.firewall_reapply_notice() is None
    assert res.data["firewall_reapply_required"] is False
    assert not any("re-apply" in d for d in res.details)


def test_a_refusing_firewall_preflight_stops_the_apply_before_the_checkout_moves(tmp_path, monkeypatch):
    """FW P1-2C through the public apply: when the nginx preflight refuses, the apply returns that
    refusal, the checkout stays where it was and no post-update follow-up is recorded."""
    from lhpc.core import proxy_rerender
    from lhpc.core.service_base import ActionResult
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch)
    gitrepo.upstream_commit(up)
    before = _head(work)
    refusal = ActionResult(False, "Refusing to self-update: the nginx boot gate cannot be brought current.")
    monkeypatch.setattr(svc, "firewall_update_nginx_preflight", lambda: refusal)
    marks = []
    monkeypatch.setattr(svc, "_fw_mark_post_update", lambda: marks.append("fw") or "")
    monkeypatch.setattr(proxy_rerender, "mark", lambda paths: marks.append("proxy") or "")
    res = svc.self_update_apply()
    # that refusal, ending with where updates come from (the source line every apply answer carries)
    assert (res.ok, res.summary, res.details[:-1]) == (False, refusal.summary, []), res
    assert res.details[-1].startswith("updates from "), res
    assert _head(work) == before
    assert marks == []


@pytest.mark.parametrize("path", ["one-click", "one-click-sync-failed", "one-click-units-failed",
                                  "cli", "cli-cleanup-failed"])
def test_a_helper_changing_update_keeps_the_reapply_warning_on_every_path(tmp_path, monkeypatch, path):
    """The one-click helper records only the SUMMARY (the console shows that after the restart), so
    the re-apply warning must be in it — also when the venv sync or the unit refresh fails after the
    advance; the CLI keeps the full line with the commands in details — also on the partial result
    of a failed cleanup. The status reads update-required after the advance."""
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch)
    gitrepo.upstream_commit(up)
    _status_reads(svc, monkeypatch, "update-required")

    def advance(*a, **k):
        return {"ok": True, "message": "Update applied.", "deps_changed": False,
                **({"cleanup_failed": True, "cleanup_error": "cannot unlink x"}
                   if path == "cli-cleanup-failed" else {})}
    monkeypatch.setattr(selfupdate, "apply_update", advance)
    # Stubs the collaborator, systemd: the firewall integration is present, and the unit refresh
    # succeeds or fails as the path needs.
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    units_ok = path != "one-click-units-failed"
    monkeypatch.setattr(svc, "_refresh_units_post_update",
                        lambda: (units_ok, "ok" if units_ok else "daemon-reload failed"))
    pip_rc = 1 if path == "one-click-sync-failed" else 0
    real_run = svc._system.runner.run
    monkeypatch.setattr(svc._system.runner, "run", lambda argv, *a, **k: (
        CommandResult(pip_rc if "pip" in argv else 1, "", "") if argv[0] == "systemctl" or "pip" in argv
        else real_run(argv, *a, **k)))                     # no real pip install, no systemd
    if path.startswith("one-click"):
        monkeypatch.setenv("INVOCATION_ID", "x")
        (rt / "state").mkdir(parents=True, exist_ok=True)
        (rt / "state" / "selfupdate.request").write_text("normal\n")
        res = svc.self_update_run_service()
        recorded = selfupdate.status_view(svc._paths)["last_apply"]
        failed = path != "one-click"
        assert res.ok is not failed and recorded["ok"] is not failed
        assert res.data.get("venv_sync_failed", False) is (path == "one-click-sync-failed")
        assert res.data.get("units_refreshed", True) is units_ok
        assert not failed or res.next_commands or any("nothing to run here" in d
                                                      for d in res.details), res
        assert "re-apply the firewall before you reboot" in recorded["summary"].lower()
    elif path == "cli-cleanup-failed":
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        res = svc.self_update_apply_operator()
        assert not res.ok and res.data["cleanup_failed"] is True
        assert res.data["firewall_reapply_required"] is True
        assert any("installed firewall helper is not" in d and "lhpc webserver apply" in d
                   for d in res.details)
    else:
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        res = svc.self_update_apply_operator()
        assert res.ok and res.data["firewall_reapply_required"] is True
        assert any("installed firewall helper is not" in d and "lhpc webserver apply" in d
                   for d in res.details)


def _head(work):
    return gitrepo.git(work, "rev-parse", "HEAD")


def test_concurrent_apply_returns_busy_without_git(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"}, inst=stores_fixture.inst_of(svc, "s"))
    before = _head(work)
    held = _rfs.open_lock(svc._paths, svc._paths.under("state", "locks", "selfupdate.lock"))
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)                            # another process owns the op
    try:
        res = svc.self_update_apply()
        assert res.ok is False and res.data.get("busy") is True
        assert _head(work) == before                                    # no git ran
        assert cfgmod.load_stack_config(svc._paths, "s", inst=stores_fixture.inst_of(svc, "s")).get("ropt") == "OLD"  # config unchanged
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()


def test_check_defers_while_apply_lock_held(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)                    # a pre-existing cached status
    before = selfupdate.read_cache(svc._paths).get("checked_at")
    held = _rfs.open_lock(svc._paths, svc._paths.under("state", "locks", "selfupdate.lock"))
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)                            # an apply owns the lock
    try:
        # the explicit check (also the code path the startup freshness thread uses) must DEFER
        res = svc.self_update_check()
        assert res.ok and res.data.get("deferred") is True
        assert selfupdate.read_cache(svc._paths).get("checked_at") == before   # no competing fetch/cache write
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()

"""Web console: apply/config/nginx/evidence/gui/service/serve/blockers/corrections/hardening/cli."""


from __future__ import annotations
import os
import pytest
import sys
from lhpc.core import webserver, pki, runtime_fs, validators, config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem, Listener, CommandResult as CR
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService
from lhpc.adapters.web.app import create_app, run_server
from pathlib import Path
from lhpc.adapters.cli.main import main
from lhpc.core.config import ConfigError, WebserverConfig, load_config, save_webserver_config
from lhpc.adapters.web import app as webapp


# ===== merged from test_webserver_apply.py =====
def _svc(tmp_path, fake=None):
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def _conf(paths):
    return str(paths.under(*webserver.NGINX_CONF))            # live (reload target)


def _staged(paths):
    return str(paths.under(*webserver.NGINX_CONF_STAGED))     # nginx -t validates the staged file


def test_apply_repair_required_when_no_master(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(svc0._paths)): CommandResult(0, "", "successful"),
    })
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    r = svc.webserver_apply()
    assert not r.ok and "repair required" in r.summary          # no pidfile -> never starts
    # web path must not have attempted `systemctl` or an nginx start
    assert not any("systemctl" in " ".join(c) for c in fake.calls)
    assert not any(c[:2] == ["nginx", "-s"] and "reload" not in c for c in fake.calls)


def test_apply_reloads_running_master(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    # Simulate a live LHPC-owned master: pidfile -> this (alive) test process.
    from lhpc.core import runtime_fs
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    conf = _conf(paths)
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(paths)): CommandResult(0, "", "successful"),
        ("nginx", "-s", "reload", "-c", conf): CommandResult(0, "", ""),
    }, listeners=[Listener("ipv4", "127.0.0.1", 8443, 1)])  # live loopback console (exact scope)
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_apply()
    assert r.ok and "reloaded" in r.summary
    assert ["nginx", "-s", "reload", "-c", conf] in fake.calls


class _RestartFlipsFake(FakeSystem):
    """Effective console listener stays loopback until the nginx UNIT is restarted, then flips to
    0.0.0.0 — models the reload-cannot-rebind-a-held-socket reality behind F3."""
    def tcp_listeners(self):
        restarted = ["systemctl", "--user", "restart", "lhpc-nginx.service"] in self.calls
        ip = "0.0.0.0" if restarted else "127.0.0.1"
        return [Listener(family="ipv4", ip=ip, port=8443, inode=1)]


def _seed_exposed(svc):
    from lhpc.core import config as cfgmod
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["192.168.0.0/24"], access_mode="auth-everywhere")


def _live_master(paths):
    from lhpc.core import runtime_fs
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))


def _apply_cmds(paths):
    return {
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(paths)): CommandResult(0, "", "successful"),
        ("nginx", "-s", "reload", "-c", _conf(paths)): CommandResult(0, "", ""),
        ("systemctl", "--user", "restart", "lhpc-nginx.service"): CommandResult(0, "", ""),
    }


def test_apply_bind_change_restarts_when_reload_leaves_loopback(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_exposed(svc0)
    _live_master(paths)
    fake = _RestartFlipsFake(commands=_apply_cmds(paths))
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    # reload left the master on loopback -> apply restarts the unit and re-verifies exposed
    assert r.ok and "restarted" in r.summary
    assert ["systemctl", "--user", "restart", "lhpc-nginx.service"] in fake.calls
    assert r.data["effective"]["remote_listener"] is True


def test_apply_bind_change_fails_closed_when_restart_does_not_rebind(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_exposed(svc0)
    _live_master(paths)
    # restart returns rc0 but the listener STAYS loopback (bind never widened) -> fail closed
    fake = FakeSystem(commands=_apply_cmds(paths),
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1)])
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert not r.ok and "did not take effect" in r.summary
    assert ["systemctl", "--user", "restart", "lhpc-nginx.service"] in fake.calls
    assert r.data["effective"]["remote_listener"] is False       # never a false OK


class _StackRestartFlipsFake(FakeSystem):
    """Console loopback:8443 (desired loopback, matching). The meshcom PROXY listener :8444 stays on
    the OLD loopback bind until the nginx UNIT restarts, then flips to 0.0.0.0 — models the
    reload-cannot-rebind-a-held-socket reality for a local -> public proxy transition."""
    def tcp_listeners(self):
        restarted = ["systemctl", "--user", "restart", "lhpc-nginx.service"] in self.calls
        ip = "0.0.0.0" if restarted else "127.0.0.1"
        return [Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1),
                Listener(family="ipv4", ip=ip, port=8444, inode=2)]


def _seed_meshcom_public(paths):
    from lhpc.core import config as cfgmod
    cfgmod.save_stackweb_config(paths, "meshcom", mode="public", port=8444)


def test_apply_stack_proxy_public_transition_restarts_automatically(tmp_path):
    # loopback -> public on the meshcom proxy: reload leaves :8444 on loopback; apply must detect
    # the mismatch on the PROXY listener (console matches fine), restart the unit AUTOMATICALLY (no
    # operator action), re-verify, and only then report success.
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths                                          # console stays loopback-desired
    _seed_meshcom_public(paths)
    _live_master(paths)
    fake = _StackRestartFlipsFake(commands=_apply_cmds(paths))
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert r.ok and "restarted" in r.summary, r.summary
    assert ["systemctl", "--user", "restart", "lhpc-nginx.service"] in fake.calls
    assert r.data["checks"]["stack_listener_matches"] == "ok"
    mesh = [p for p in r.data["stack_proxies"] if p["stack_id"] == "meshcom"][0]
    assert mesh["listener_scope"] == "exposed" and mesh["listener_matches"] == "ok"


def test_apply_web_context_without_hatch_units_falls_back_typed(tmp_path, monkeypatch):
    # Web context (INVOCATION_ID) on a deployment WITHOUT the canonical nginx-restart hatch units
    # (old install / tampered): apply must NOT attempt a doomed bus restart NOR write a request
    # nobody consumes — it returns the typed boundary message with both remedies.
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_meshcom_public(paths)
    _live_master(paths)
    monkeypatch.setenv("INVOCATION_ID", "abc123")             # we ARE the managed web unit
    fake = FakeSystem(commands=_apply_cmds(paths),
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1),
                                 Listener(family="ipv4", ip="127.0.0.1", port=8444, inode=2)])
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert not r.ok and "privilege boundary" in r.summary
    assert "lhpc self-update --repair-integration" in (r.next_commands or [])
    assert "lhpc webserver apply" in (r.next_commands or [])
    assert not any(c[:1] == ["systemctl"] for c in fake.calls)   # the doomed restart is never tried
    from lhpc.core import updater_units as U
    assert not paths.under(*U.NGINX_RESTART_REQUEST_REL).exists()   # no orphan request written


def _seed_hatch_units(tmp_path, monkeypatch, paths):
    """A tmp HOME whose user-unit dir carries the CANONICAL nginx-restart units for THIS root —
    the precondition for the web branch to use the escape hatch."""
    from lhpc.core import updater_units as U
    home = tmp_path / "home"
    ud = home / ".config" / "systemd" / "user"
    ud.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root = str(paths.runtime_root)
    _, checkout, venv = U.deployment_paths(root)
    for k in (U.RESTART_UNIT, U.RESTART_PATH_UNIT):
        (ud / k).write_text(U.render(k, root, checkout, venv))
    return paths.under(*U.NGINX_RESTART_REQUEST_REL)


def _watcher(req_path, *, claim=True, on_claim=None):
    """A background 'path unit': waits for the request marker, optionally claims (deletes) it and
    runs `on_claim` (e.g. flip the fake's listeners). Returns the started thread."""
    import threading
    import time as _t

    def run():
        for _ in range(200):                       # <= 10 s safety bound
            if req_path.exists():
                if claim:
                    req_path.unlink()
                    if on_claim:
                        on_claim()
                return
            _t.sleep(0.05)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


class _FlippableFake(FakeSystem):
    """Listeners stay loopback until `.flipped` is set (by the fake watcher's on_claim) — models
    the declarative stop/start rebinding the proxy listener."""
    flipped = False
    def tcp_listeners(self):
        ip = "0.0.0.0" if self.flipped else "127.0.0.1"
        return [Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1),
                Listener(family="ipv4", ip=ip, port=8444, inode=2)]


def test_apply_web_context_completes_via_restart_watcher(tmp_path, monkeypatch):
    # The full hatch happy path: web-context apply writes the request; the (simulated) path unit
    # claims it and the fresh nginx rebinds; apply re-verifies and reports the watcher success —
    # never touching systemctl.
    from lhpc.core import service_webserver as SW
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_meshcom_public(paths)
    _live_master(paths)
    req = _seed_hatch_units(tmp_path, monkeypatch, paths)
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    monkeypatch.setattr(SW, "_RESTART_WATCH_WAIT_S", 5.0)
    monkeypatch.setattr(SW, "_RESTART_WATCH_POLL_S", 0.05)
    fake = _FlippableFake(commands=_apply_cmds(paths))
    _watcher(req, claim=True, on_claim=lambda: setattr(fake, "flipped", True))
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert r.ok, r.summary
    assert "restart watcher" in r.summary
    assert not req.exists()                                       # consumed
    assert not any(c[:1] == ["systemctl"] for c in fake.calls)    # no bus, ever
    mesh = [p for p in r.data["stack_proxies"] if p["stack_id"] == "meshcom"][0]
    assert mesh["listener_matches"] == "ok"


def test_apply_web_context_timeout_unclaimed_names_the_watcher(tmp_path, monkeypatch):
    # Timeout split (a): the request is NEVER claimed -> the WATCHER is dead/not enabled. The stale
    # request is removed and the failure points at the integration remedies, not at nginx.
    from lhpc.core import service_webserver as SW
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_meshcom_public(paths)
    _live_master(paths)
    req = _seed_hatch_units(tmp_path, monkeypatch, paths)
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    monkeypatch.setattr(SW, "_RESTART_WATCH_WAIT_S", 0.8)
    monkeypatch.setattr(SW, "_RESTART_WATCH_POLL_S", 0.05)
    fake = FakeSystem(commands=_apply_cmds(paths),
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1),
                                 Listener(family="ipv4", ip="127.0.0.1", port=8444, inode=2)])
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert not r.ok and "never picked up the request" in r.summary
    assert "lhpc self-update --repair-integration" in (r.next_commands or [])
    assert not req.exists()                                       # OUR stale request was removed
    assert "lhpc-nginx-restart.log" not in r.summary              # integration remedy, not nginx's


def test_apply_web_context_timeout_claimed_names_nginx_evidence(tmp_path, monkeypatch):
    # Timeout split (b): the request WAS claimed but the listeners never came good -> the
    # integration worked; the failure points at the nginx-side evidence and removes nothing.
    from lhpc.core import service_webserver as SW
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_meshcom_public(paths)
    _live_master(paths)
    req = _seed_hatch_units(tmp_path, monkeypatch, paths)
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    monkeypatch.setattr(SW, "_RESTART_WATCH_WAIT_S", 1.0)
    monkeypatch.setattr(SW, "_RESTART_WATCH_POLL_S", 0.05)
    fake = FakeSystem(commands=_apply_cmds(paths),                # listeners NEVER flip
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1),
                                 Listener(family="ipv4", ip="127.0.0.1", port=8444, inode=2)])
    _watcher(req, claim=True)                                     # watcher claims, nginx stays bad
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert not r.ok and "lhpc-nginx-restart.log" in r.summary
    assert "restart watcher ran" in r.summary
    assert "repair-integration" not in " ".join(r.next_commands or [])   # NOT an integration remedy


def test_restart_claim_consumes_request_once_and_refuses_stray(tmp_path):
    # Startup-recovery + claim discipline: a (possibly stale) request is consumed exactly once —
    # the declarative restart then proceeds; a second start with no request is a clean stray no-op.
    from lhpc.core import updater_units as U
    svc = _svc(tmp_path)
    paths = svc._paths
    req = paths.under(*U.NGINX_RESTART_REQUEST_REL)
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text("restart\n")                                   # stale request (crash survivor)
    r = svc.webserver_run_restart_service()
    assert r.ok and r.data.get("consumed") is True
    assert not req.exists()
    assert not paths.under(*U.NGINX_RESTART_INFLIGHT_REL).exists()   # breadcrumb cleaned
    r2 = svc.webserver_run_restart_service()                      # stray start
    assert r2.ok and r2.data.get("noop") is True


def test_restart_claim_recovers_a_stale_inflight_breadcrumb(tmp_path):
    # A crashed prior agent left an in-flight breadcrumb: unlike self-update (multi-step, needs
    # recovery) a restart is idempotent — the stale breadcrumb is cleared and the claim retried.
    from lhpc.core import updater_units as U
    svc = _svc(tmp_path)
    paths = svc._paths
    req = paths.under(*U.NGINX_RESTART_REQUEST_REL)
    inflight = paths.under(*U.NGINX_RESTART_INFLIGHT_REL)
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text("restart\n")
    inflight.write_text("crashed\n")
    r = svc.webserver_run_restart_service()
    assert r.ok and r.data.get("consumed") is True
    assert not req.exists() and not inflight.exists()


def test_apply_stack_proxy_stuck_listener_fails_closed_naming_stack(tmp_path):
    # Even the automatic restart cannot rebind (listener pinned to loopback) -> apply must FAIL,
    # name the stuck stack, and never report the exposure as effective.
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    _seed_meshcom_public(paths)
    _live_master(paths)
    fake = FakeSystem(commands=_apply_cmds(paths),
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1),
                                 Listener(family="ipv4", ip="127.0.0.1", port=8444, inode=2)])
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert not r.ok and "did not take effect" in r.summary
    assert "meshcom" in r.summary                                # the stuck listener is NAMED
    assert ["systemctl", "--user", "restart", "lhpc-nginx.service"] in fake.calls
    assert r.data["checks"]["stack_listener_matches"] == "failed"


def test_apply_no_restart_when_scope_already_matches(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths                                          # loopback desired (default)
    _live_master(paths)
    fake = FakeSystem(commands=_apply_cmds(paths),
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8443, inode=1)])
    r = ControllerService(system=fake.system, paths=paths).webserver_apply()
    assert r.ok and "reloaded" in r.summary
    assert not any(c[:1] == ["systemctl"] for c in fake.calls)    # reload sufficed, no restart


def test_restart_primitive_calls_systemctl(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    ok = FakeSystem(commands={
        ("systemctl", "--user", "restart", "lhpc-nginx.service"): CommandResult(0, "", "")})
    assert webserver.restart(ok.system, paths)[0] == "restarted"
    assert ["systemctl", "--user", "restart", "lhpc-nginx.service"] in ok.calls
    bad = FakeSystem(commands={
        ("systemctl", "--user", "restart", "lhpc-nginx.service"): CommandResult(1, "", "boom")})
    assert webserver.restart(bad.system, paths)[0] == "failed"
    # Live-found: under sudo/root the user bus answers EPERM and the generic advice misled — the
    # failure must name the actual remedy (re-run as the operator, without sudo).
    eperm = FakeSystem(commands={
        ("systemctl", "--user", "restart", "lhpc-nginx.service"): CommandResult(
            1, "", "Failed to connect to user scope bus via local transport: Operation not permitted")})
    state, msg = webserver.restart(eperm.system, paths)
    assert state == "failed" and "not sudo/root, not the web console" in msg


def test_restart_without_output_names_the_reason_and_the_resulting_state(tmp_path):
    """A silent systemctl failure used to print the literal "restart failed", so our own expired
    budget looked like an nginx fault and sent the operator to the nginx log (live-found on a
    Zero). Name which of the two happened, and what the unit ended up as."""
    timed = FakeSystem(commands={
        ("systemctl", "--user", "restart", "lhpc-nginx.service"):
            CommandResult(124, "", "", timed_out=True),
        ("systemctl", "--user", "is-active", "lhpc-nginx.service"): CommandResult(0, "active\n", "")})
    state, msg = webserver.restart(timed.system, Paths(runtime_root=tmp_path))
    assert state == "failed"
    assert "budget of 60s expired" in msg and "the unit is now active" in msg
    assert "lhpc webserver apply" in msg               # active -> a retry proves the listener

    quiet = FakeSystem(commands={
        ("systemctl", "--user", "restart", "lhpc-nginx.service"): CommandResult(3, "", ""),
        ("systemctl", "--user", "is-active", "lhpc-nginx.service"): CommandResult(3, "failed\n", "")})
    state, msg = webserver.restart(quiet.system, Paths(runtime_root=tmp_path))
    assert state == "failed"
    assert "exited 3 without a message" in msg and "the unit is now failed" in msg
    assert "lhpc webserver apply" not in msg           # not active -> no misleading retry hint


def test_apply_refuses_invalid_config(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(svc0._paths)): CommandResult(1, "", "emerg: bad"),
    })
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    r = svc.webserver_apply()
    assert not r.ok and "previous proven configuration remains active" in r.summary
    assert not any(c[:3] == ["nginx", "-s", "reload"] for c in fake.calls)   # never reloaded


def test_apply_refuses_when_nginx_not_installed(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    # FakeSystem with no nginx command mapping -> `nginx -v` returns not_found.
    svc = ControllerService(system=FakeSystem().system, paths=svc0._paths)
    r = svc.webserver_apply()
    assert not r.ok and "nginx is not installed" in r.summary
    assert any("sudo apt install -y nginx" in d for d in r.details)
    assert "sudo apt install -y nginx" in " ".join(r.next_commands)


def test_monitor_lists_nginx_as_system_dependency(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    # before verify -> status unknown; nginx declared as a system dep with its install command
    mon = svc0.webserver_monitor().data
    deps = {d["name"]: d for d in mon["system_deps"]}
    assert deps["nginx"]["install"] == "sudo apt install -y nginx"
    assert deps["nginx"]["status"] == "unknown"
    # after a verify that finds nginx absent -> status 'absent' + a warning
    svc = ControllerService(system=FakeSystem().system, paths=svc0._paths)   # no nginx
    svc.webserver_verify()
    mon2 = svc.webserver_monitor().data
    assert {d["name"]: d["status"] for d in mon2["system_deps"]}["nginx"] == "absent"
    assert any("nginx" in w["text"] and "apt install" in w["text"] for w in mon2["warnings"])


def test_controller_component_isolation(tmp_path):
    # correction #10: controller is NOT a managed stack, and every generic verb refuses it.
    svc = _svc(tmp_path)
    stack_ids = {s.id for s in svc.stacks()}
    assert "loraham-pi-control" not in stack_ids            # not in the managed set
    for verb in ("install", "update", "uninstall", "clean", "build", "test", "start", "stop"):
        r = getattr(svc, verb)("loraham-pi-control")
        assert not r.ok and "self-update" in " ".join(r.next_commands)


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_configure_apply_remote_no_auth_needs_elevated_confirmation(tmp_path):
    # Single Apply must STILL refuse remote no-auth without the elevated typed confirmation, and save
    # nothing (the safety invariant survives the Save+Apply merge).
    svc = _svc(tmp_path)
    r = svc.webserver_configure_apply(bind="0.0.0.0", access_mode="no-auth",
                                      allowed_cidrs=["0.0.0.0/0"])          # no confirmation
    assert not r.ok and "elevated confirmation" in r.summary
    assert svc.config().webserver.remote_exposed is False                  # nothing written


def test_configure_apply_saves_remote_with_elevated_confirmation(tmp_path):
    # WITH the elevated confirmation it saves ALL fields in one write (incl. remote_exposed derived
    # from bind + allowed_cidrs), then applies (apply itself may repair-require without nginx here).
    svc = _svc(tmp_path)
    svc.webserver_configure_apply(bind="0.0.0.0", access_mode="no-auth",
                                  allowed_cidrs=["0.0.0.0/0"], confirm=True, confirm_public=True)
    cfg = svc.config().webserver
    assert cfg.bind == "0.0.0.0" and cfg.remote_exposed is True and cfg.access_mode == "no-auth"
    assert list(cfg.allowed_cidrs) == ["0.0.0.0/0"]


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_configure_apply_loopback_needs_no_confirmation(tmp_path):
    # A loopback config derives remote_exposed=False and applies with no confirmation gate.
    svc = _svc(tmp_path)
    svc.webserver_configure_apply(bind="127.0.0.1", port=8443)
    cfg = svc.config().webserver
    assert cfg.bind == "127.0.0.1" and cfg.remote_exposed is False


def test_plan_exposure_elevates_and_flags_cleartext_http():
    from lhpc.core.config import WebserverConfig
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                                                allowed_cidrs=("192.168.0.0/24",),
                                                scheme="http", access_mode="no-auth"))
    assert p["remote"] and p["danger"] == "elevated" and p["cleartext"] is True


def test_monitor_view_exposes_live_scope(tmp_path):
    from lhpc.core.config import WebserverConfig
    v = webserver.monitor_view(Paths(runtime_root=tmp_path), WebserverConfig(),
                               live_listener_scope="loopback")
    assert v["live_scope"] == "loopback" and v["pending"] is False


def test_posture_security_is_tri_state():
    def sec(local, public, mode):
        return webserver.posture(local=local, public=public, access_mode=mode)["sec_level"]
    # Loopback is ALWAYS green — even with no-auth (nothing remote reaches it).
    assert sec(True, False, "no-auth") == "ok"
    assert sec(True, False, "local-open-remote-auth") == "ok"
    # Off-loopback + no-auth = RED (unauthenticated remote), whether LAN- or public-scoped.
    assert sec(False, False, "no-auth") == "bad"
    assert sec(False, True, "no-auth") == "bad"
    # Off-loopback + auth, restricted to a LAN (not public) = GREEN.
    assert sec(False, False, "auth-everywhere") == "ok"
    assert sec(False, False, "local-open-remote-auth") == "ok"
    # Off-loopback + auth but PUBLIC (all source addresses, 0.0.0.0/0) = YELLOW.
    assert sec(False, True, "auth-everywhere") == "warn"
    assert sec(False, True, "local-open-remote-auth") == "warn"
    # An UNKNOWN access mode off loopback is treated as UNAUTHENTICATED (fail-closed) — never a green pill.
    assert sec(False, False, "bogus-mode") == "bad"
    assert sec(True, False, "bogus-mode") == "ok"          # loopback stays green (nothing remote reaches it)
    # Off-loopback + auth but NO allowed CIDRs at all is an UNAPPLIABLE desired state -> YELLOW + iface
    # "unset", so the pill AGREES with the "no allowed source CIDR" warning shown right below it.
    nocidr = webserver.posture(local=False, public=False, access_mode="auth-everywhere", has_cidrs=False)
    assert nocidr["sec_level"] == "warn" and nocidr["iface"] == "unset"
    withcidr = webserver.posture(local=False, public=False, access_mode="auth-everywhere", has_cidrs=True)
    assert withcidr["sec_level"] == "ok" and withcidr["iface"] == "LAN"      # a real CIDR -> LAN-green
    # Labels unchanged.
    p = webserver.posture(local=False, public=True, access_mode="local-open-remote-auth")
    assert p["iface"] == "All interfaces" and p["auth"] == "remote-auth"


def test_posture_scheme_indicator_and_worst_wins():
    from lhpc.core import webserver
    # scheme is echoed for the leading indicator; https default keeps the existing colours.
    assert webserver.posture(local=True, public=False, access_mode="auth-everywhere")["scheme"] == "https"
    # http on loopback -> YELLOW; http off loopback (remote cleartext) -> RED; https -> stays green.
    assert webserver.posture(local=True, public=False, access_mode="auth-everywhere", scheme="http")["sec_level"] == "warn"
    assert webserver.posture(local=False, public=False, access_mode="auth-everywhere", scheme="http")["sec_level"] == "bad"
    assert webserver.posture(local=True, public=False, access_mode="auth-everywhere", scheme="https")["sec_level"] == "ok"
    # worst-wins: an already-RED auth posture (remote no-auth) is NOT downgraded by an https scheme.
    assert webserver.posture(local=False, public=False, access_mode="no-auth", scheme="https")["sec_level"] == "bad"


def test_posture_per_item_levels_for_individual_pills():
    from lhpc.core import webserver
    # Each summary item is coloured on its OWN dimension (auth / iface / scheme), so a green item
    # never masks a red neighbour.
    # Remote, unauthenticated, cleartext, public bind: auth RED, iface YELLOW, scheme RED.
    p = webserver.posture(local=False, public=True, access_mode="no-auth", scheme="http")
    assert p["auth_level"] == "bad" and p["iface_level"] == "warn" and p["scheme_level"] == "bad"
    # Loopback open http: auth GREEN (local open is safe), iface GREEN (Local), scheme YELLOW (http local).
    p = webserver.posture(local=True, public=False, access_mode="no-auth", scheme="http")
    assert p["auth_level"] == "ok" and p["iface_level"] == "ok" and p["scheme_level"] == "warn"
    # Remote auth, restricted CIDRs, https: every dimension GREEN.
    p = webserver.posture(local=False, public=False, access_mode="local-open-remote-auth",
                          has_cidrs=True, scheme="https")
    assert p["auth_level"] == "ok" and p["iface_level"] == "ok" and p["scheme_level"] == "ok"
    # Off-loopback with no CIDRs (unappliable): iface YELLOW (unset) even though authed+https.
    p = webserver.posture(local=False, public=False, access_mode="auth-everywhere",
                          has_cidrs=False, scheme="https")
    assert p["auth_level"] == "ok" and p["iface_level"] == "warn" and p["scheme_level"] == "ok"


def test_monitor_view_running_pill_is_nginx_or_lhpc_web(tmp_path):
    from lhpc.core.config import WebserverConfig
    p = Paths(runtime_root=tmp_path)
    # This session proxied through nginx -> green "nginx"; served directly by lhpc-web -> yellow.
    up = webserver.monitor_view(p, WebserverConfig(), live_listener_scope="loopback", served_via_nginx=True)
    assert up["posture"]["run"] == "nginx" and up["posture"]["run_level"] == "ok"
    down = webserver.monitor_view(p, WebserverConfig(), live_listener_scope="absent", served_via_nginx=False)
    assert down["posture"]["run"] == "lhpc-web" and down["posture"]["run_level"] == "warn"


# ===== merged from test_webserver_blockers.py =====
def _svc_webserver_blockers(tmp_path, fake=None):
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def _app(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(lambda: svc), svc


def _csrf(c):
    with c.session_transaction() as s:
        s["_csrf"] = "tok"
    return "tok"


def _staged_webserver_blockers(paths):
    return str(paths.under(*webserver.NGINX_CONF_STAGED))     # nginx -t validates the staged file


def test_reset_unproven_without_master_preserves_pki(tmp_path):
    svc0 = _svc_webserver_blockers(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    svc0.webserver_cert_issue("laptop", "pw")
    svc0.webserver_expose(["192.168.0.0/24"], confirm=True)      # desired: exposed
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged_webserver_blockers(svc0._paths)): CR(0, "", "ok")})
    svc = ControllerService(system=fake.system, paths=svc0._paths)   # no nginx master (no pidfile)
    r = svc.webserver_reset_defaults()
    assert not r.ok and "UNPROVEN" in r.summary                  # config valid but no master
    cfg = svc.config().webserver
    assert cfg.remote_exposed is False and cfg.bind == "127.0.0.1" and cfg.allowed_cidrs == ()
    # PKI + client inventory preserved by reset
    assert pki.pki_status(svc._paths)["server_ca"]["present"]
    assert any(c["label"] == "laptop" for c in pki.list_client_certs(svc._paths))


def test_reset_proven_with_running_master(tmp_path):
    svc0 = _svc_webserver_blockers(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    conf = _conf(paths)
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged_webserver_blockers(paths)): CR(0, "", "ok"),
                                ("nginx", "-s", "reload", "-c", conf): CR(0, "", "")})
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_reset_defaults()
    assert r.ok and "ceased" in r.summary
    assert svc.webserver_monitor().data["effective"].get("remote_cessation_proven") is True


def test_init_recreate_requires_confirmation(tmp_path):
    svc = _svc_webserver_blockers(tmp_path)
    assert svc.webserver_init(dns_sans=["pi.local"]).ok          # fresh PKI: no confirm needed
    assert not svc.webserver_init().ok                           # exists -> destructive -> refuse
    assert svc.webserver_init(confirm=True).ok                   # explicit confirm recreates


def test_gui_expose_uses_typed_phrase(tmp_path):
    # The unified Apply (/webserver/configure) enforces the same typed-phrase ladder the dedicated
    # expose form used to: wrong phrase refuses, 'enable-remote' clears a private range, and a public
    # range (0.0.0.0/0) additionally demands the elevated 'enable-remote-danger'.
    app, svc = _app(tmp_path)
    c = app.test_client(); tok = _csrf(c)
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "192.168.0.0/24",
                                         "confirm_phrase": "nope"})
    assert svc.config().webserver.remote_exposed is False        # wrong phrase -> not exposed
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "192.168.0.0/24",
                                         "confirm_phrase": "enable-remote"})
    assert svc.config().webserver.remote_exposed is True
    # a public range needs the elevated phrase
    svc.webserver_disable_remote()
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "0.0.0.0/0",
                                         "confirm_phrase": "enable-remote"})
    assert svc.config().webserver.remote_exposed is False        # normal phrase insufficient
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "0.0.0.0/0",
                                         "confirm_phrase": "enable-remote-danger"})
    assert svc.config().webserver.remote_exposed is True


def test_gui_revoke_requires_typed_label(tmp_path):
    app, svc = _app(tmp_path)
    c = app.test_client(); tok = _csrf(c)
    svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    c.post("/webserver/cert", data={"_csrf": tok, "op": "revoke", "label": "laptop",
                                    "confirm_phrase": "wrong"})
    assert all(x["state"] == "active" for x in pki.list_client_certs(svc._paths)
               if x["label"] == "laptop")                        # not revoked
    c.post("/webserver/cert", data={"_csrf": tok, "op": "revoke", "label": "laptop",
                                    "confirm_phrase": "laptop"})
    assert any(x["state"] == "revoked" for x in pki.list_client_certs(svc._paths)
               if x["label"] == "laptop")


def test_trusted_host_enforced_in_all_modes(tmp_path):
    # Item 1: the trusted-host policy is enforced in EVERY serving mode — including the interactive
    # loopback console (Secure cookies off), not only productive/HTTPS.
    app, _ = _app(tmp_path)
    c = app.test_client()
    # interactive (Secure cookies off) -> an unknown/rebinding Host is STILL rejected; loopback allowed
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200
    # productive HTTPS -> identical policy
    app.config["SESSION_COOKIE_SECURE"] = True
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200


def _productive(tmp_path, **ws):
    """A productive-mode client whose [webserver] config is `ws`."""
    from lhpc.core import config as _config
    if ws:
        _config.save_webserver_config(Paths(runtime_root=tmp_path), **ws)
    app, svc = _app(tmp_path)
    svc._invalidate_config()
    app.config["SESSION_COOKIE_SECURE"] = True
    return app.test_client()


def test_exposed_console_accepts_its_lan_ip_as_host(tmp_path):
    # THE REPORTED BUG: bind=0.0.0.0 + remote_exposed, empty ip_sans -> every remote request 400'd,
    # because `bind` ("0.0.0.0") is the only IP in the allowlist and no browser ever sends it.
    c = _productive(tmp_path, bind="0.0.0.0", remote_exposed=True, allowed_cidrs=["0.0.0.0/0"])
    assert c.get("/stacks", headers={"Host": "192.168.178.66:8443"}).status_code == 200


def test_exposed_console_still_rejects_a_name_so_rebinding_stays_blocked(tmp_path):
    # The whole relaxation rests on this: DNS rebinding needs a NAME. Only IP literals are relaxed.
    c = _productive(tmp_path, bind="0.0.0.0", remote_exposed=True, allowed_cidrs=["0.0.0.0/0"])
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "pi.local"}).status_code == 400   # name, not in dns_sans


def test_loopback_only_console_rejects_a_lan_ip_host(tmp_path):
    c = _productive(tmp_path)                       # remote_exposed defaults to False
    assert c.get("/stacks", headers={"Host": "192.168.178.66"}).status_code == 400


def test_wildcard_bind_is_never_a_valid_host(tmp_path):
    c = _productive(tmp_path, bind="0.0.0.0")       # bind set, but NOT exposed
    assert c.get("/stacks", headers={"Host": "0.0.0.0"}).status_code == 400


def test_ipv6_loopback_host_is_accepted(tmp_path):
    # `[::1]:8443`.split(":")[0] == "[" -> the hardcoded ::1 entry was unreachable before.
    c = _productive(tmp_path)
    assert c.get("/stacks", headers={"Host": "[::1]:8443"}).status_code == 200


def test_ipv6_literal_is_accepted_only_while_exposed(tmp_path):
    exposed = tmp_path / "exposed"
    loopback = tmp_path / "loopback"
    exposed.mkdir()
    loopback.mkdir()
    c_exposed = _productive(exposed, bind="0.0.0.0", remote_exposed=True,
                            allowed_cidrs=["0.0.0.0/0"])
    assert c_exposed.get("/stacks", headers={"Host": "[2001:db8::1]:8443"}).status_code == 200
    c_loopback = _productive(loopback)
    assert c_loopback.get("/stacks", headers={"Host": "[2001:db8::1]:8443"}).status_code == 400


def test_ip_sans_match_by_parsed_value_not_string(tmp_path):
    # An ip_sans entry of the compressed form must match a request for the expanded form.
    c = _productive(tmp_path, ip_sans=["2001:db8::1"])
    assert c.get("/stacks", headers={"Host": "[2001:db8:0:0:0:0:0:1]:8443"}).status_code == 200


def test_rejection_is_plain_text_actionable_and_logged(tmp_path, caplog):
    import logging
    c = _productive(tmp_path)
    with caplog.at_level(logging.WARNING):
        r = c.get("/stacks", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert r.mimetype == "text/plain"                    # nothing to inject into
    body = r.get_data(as_text=True)
    assert '"evil.example"' in body                      # names the rejected host
    assert "dns_sans" in body and "ip_sans" in body      # and the fix
    assert "<" not in body
    # the FULL diagnostic (raw header, allowlist, exposure) lands in the log, not the response
    rec = [r for r in caplog.records if "trusted-host: rejected Host" in r.message]
    assert rec and "127.0.0.1" not in body               # allowlist is not enumerated to the client


def test_host_echo_can_never_reflect_markup():
    # Werkzeug already blanks a Host containing illegal characters, so `<` cannot reach the body via
    # a real request. `_host_echo` is the belt to that braces — assert it directly.
    from lhpc.adapters.web.app import _host_echo
    assert _host_echo("<script>alert(1)</script>") == "scriptalert1script"
    assert "<" not in _host_echo("<b>")
    assert _host_echo("!!!") == "(unprintable)"
    assert _host_echo("pi.local") == "pi.local"
    assert len(_host_echo("a" * 500)) <= 80


def test_illegal_host_header_is_safe_200_or_400(tmp_path):
    # An unparseable Host must be SAFE either way and must never 500. Werkzeug's behaviour varies across
    # 3.1.x: 3.1.7 fail-closes by RAISING SecurityError (a BadRequest, .code == 400) from the test client;
    # 3.1.8 blanks request.host to "" -> 200. Both are fine — nothing downstream trusts a Host claim, and a
    # raised 400 is the same fail-closed outcome as a returned 400. (A fresh install resolves the newest
    # Werkzeug flask allows, so this is 200 in practice; the pinned floor werkzeug>=3.1 also covers 3.1.7.)
    from werkzeug.exceptions import HTTPException
    c = _productive(tmp_path)
    try:
        r = c.get("/stacks", headers={"Host": "a<b.com"})
    except HTTPException as exc:                  # 3.1.7 raises SecurityError(code=400) instead of returning it
        assert exc.code == 400, f"illegal Host raised a non-400 HTTP error: {exc!r}"
        return
    except Exception as exc:                      # anything else (e.g. a 500-class crash) is a real failure
        raise AssertionError(f"illegal Host raised a non-HTTP error: {exc!r}") from exc
    assert r.status_code in (200, 400)            # 200 = blanked, 400 = fail-closed; never 500


def test_webserver_modules_and_page_present_from_installed_package(tmp_path):
    # The exact 59f00de defect: these modules/template were referenced but missing. Importing
    # them + rendering the page proves they ship.
    import importlib
    importlib.import_module("lhpc.core.webserver")
    importlib.import_module("lhpc.core.pki")
    app, _ = _app(tmp_path)
    assert app.test_client().get("/stacks").status_code == 200   # template renders


def test_no_key_or_passphrase_leak_in_status_or_evidence(tmp_path):
    import json
    svc = _svc_webserver_blockers(tmp_path)
    svc.webserver_init(dns_sans=["pi.local"])
    svc.webserver_cert_issue("laptop", "sup3r-secret-pass")
    blob = json.dumps(svc.webserver_monitor().data)
    assert "BEGIN" not in blob and "PRIVATE KEY" not in blob and "sup3r-secret-pass" not in blob
    svc.webserver_verify()
    ev = (tmp_path / "state" / "webserver.json").read_text()
    assert "BEGIN" not in ev and "PRIVATE KEY" not in ev and "sup3r-secret-pass" not in ev


# ===== merged from test_webserver_cli.py =====
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "config").mkdir(exist_ok=True)


def test_cli_cert_list_empty(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "cert", "list"]) == 0
    assert "no client certificates" in capsys.readouterr().out


def test_cli_init_status_issue_flow(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "init", "--dns", "pi.local"]) == 0
    assert main(["webserver", "status"]) == 0
    out = capsys.readouterr().out
    assert "remote_exposed False" in out
    # The exposure-status line now reflects the REAL listener (live /proc): "disabled — loopback only"
    # when nothing is bound off-loopback, or "disabled in desired config, but the live listener … is
    # still exposed" on a machine that happens to have :8443 bound. Both share this stable prefix.
    assert "Remote exposure is disabled" in out
    # issue prints a one-time passphrase, never persisted
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    out = capsys.readouterr().out
    assert "ONE-TIME bundle passphrase" in out
    assert main(["webserver", "cert", "list"]) == 0
    assert "laptop" in capsys.readouterr().out


def test_cli_webserver_logs(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    logs = tmp_path / "logs"; logs.mkdir(exist_ok=True)
    (logs / "nginx-error.log").write_text("E-line [emerg]\n")
    (logs / "nginx-access.log").write_text("A-line GET /\n")
    assert main(["webserver", "logs"]) == 0
    out = capsys.readouterr().out
    assert "nginx-error.log" in out and "E-line [emerg]" in out
    assert main(["webserver", "logs", "--access"]) == 0
    out = capsys.readouterr().out
    assert "nginx-access.log" in out and "A-line GET /" in out


def test_cli_expose_requires_confirmation(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    # no phrase -> refused (non-zero); nothing persisted as exposed
    assert main(["webserver", "expose", "--cidr", "192.168.0.0/24"]) == 1
    assert main(["webserver", "status"]) == 0
    assert "remote_exposed False" in capsys.readouterr().out
    # public route with only the normal phrase -> refused (needs the danger phrase)
    assert main(["webserver", "expose", "--cidr", "0.0.0.0/0",
                 "--confirm-phrase", "enable-remote"]) == 1
    # normal LAN range with the typed phrase -> enabled
    assert main(["webserver", "expose", "--cidr", "192.168.0.0/24",
                 "--confirm-phrase", "enable-remote"]) == 0
    assert main(["webserver", "status"]) == 0
    assert "remote_exposed True" in capsys.readouterr().out


def test_cli_configure_validation(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "configure", "--access-mode", "auth-everywhere"]) == 0


def test_cli_cert_export_safe_by_default(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "init", "--dns", "pi.local"]) == 0
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    capsys.readouterr()
    dest = tmp_path / "out" / "laptop.p12"
    dest.parent.mkdir()
    # write once -> file at 0600, and stdout carries NO bundle bytes / passphrase
    assert main(["webserver", "cert", "export", "laptop", str(dest)]) == 0
    out = capsys.readouterr().out
    assert dest.exists() and (dest.stat().st_mode & 0o777) == 0o600
    assert "bytes to" in out and "PRIVATE KEY" not in out and "passphrase" not in out.lower()
    body = dest.read_bytes()
    # refuse to overwrite without --force; the original file is untouched
    assert main(["webserver", "cert", "export", "laptop", str(dest)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().out
    assert dest.read_bytes() == body
    # --force overwrites
    assert main(["webserver", "cert", "export", "laptop", str(dest), "--force"]) == 0
    assert (dest.stat().st_mode & 0o777) == 0o600


def test_cli_cert_export_missing_bundle(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["webserver", "init", "--dns", "pi.local"]) == 0
    capsys.readouterr()
    assert main(["webserver", "cert", "export", "ghost", str(tmp_path / "x.p12")]) == 1
    assert "no export bundle" in capsys.readouterr().out


def test_cli_proxy_confirmation_parity(monkeypatch, tmp_path, capsys):
    # A stack web-UI proxy must keep the web UI's confirmation semantics: an exposure-increasing
    # mode without the phrase is refused; the elevated case needs the danger phrase.
    _env(monkeypatch, tmp_path)
    # lan exposure without a phrase -> refused (no write)
    assert main(["webserver", "proxy", "meshcom", "--mode", "lan", "--port", "8090",
                 "--cidr", "192.168.0.0/24"]) == 1
    capsys.readouterr()
    # lan with the normal phrase -> saved
    assert main(["webserver", "proxy", "meshcom", "--mode", "lan", "--port", "8090",
                 "--cidr", "192.168.0.0/24", "--confirm-phrase", "enable-remote"]) == 0
    # public default route with only the normal phrase -> refused (needs danger)
    assert main(["webserver", "proxy", "meshcom", "--mode", "public", "--port", "8090",
                 "--cidr", "0.0.0.0/0", "--confirm-phrase", "enable-remote"]) == 1
    # with the danger phrase -> saved
    assert main(["webserver", "proxy", "meshcom", "--mode", "public", "--port", "8090",
                 "--cidr", "0.0.0.0/0", "--confirm-phrase", "enable-remote-danger"]) == 0


def test_cli_proxy_rejects_bad_enum(monkeypatch, tmp_path):
    import pytest
    _env(monkeypatch, tmp_path)
    for bad in (["--mode", "remote"], ["--scheme", "ftp"], ["--access-mode", "nope"]):
        with pytest.raises(SystemExit):                       # argparse choices= -> exit 2
            main(["webserver", "proxy", "meshcom", *bad])


# ===== merged from test_webserver_config.py =====
def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


def _write_local(tmp_path: Path, body: str) -> None:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(body)


def test_webserver_defaults(tmp_path):
    ws = load_config(_paths(tmp_path)).webserver
    assert ws.bind == "127.0.0.1" and ws.port == 8443
    assert ws.access_mode == "local-open-remote-auth"
    assert ws.remote_exposed is False
    assert ws.allowed_cidrs == () and ws.dns_sans == () and ws.ip_sans == ()
    assert ws.server_cert_days == 825 and ws.client_cert_days == 825


def test_webserver_overrides_and_list_parsing(tmp_path):
    _write_local(
        tmp_path,
        '[webserver]\n'
        'bind = "0.0.0.0"\nport = 9443\n'
        'access_mode = "auth-everywhere"\nremote_exposed = true\n'
        'allowed_cidrs = "192.168.0.5/24, 10.0.0.0/8, 192.168.0.9/24"\n'  # host bits + dup net
        'dns_sans = "pi.local, lhpc.example"\nip_sans = "192.168.0.10"\n',
    )
    ws = load_config(_paths(tmp_path)).webserver
    assert ws.bind == "0.0.0.0" and ws.port == 9443
    assert ws.access_mode == "auth-everywhere" and ws.remote_exposed is True
    # normalized to network form + de-duplicated (both 192.168.0.x/24 collapse to one)
    assert ws.allowed_cidrs == ("192.168.0.0/24", "10.0.0.0/8")
    assert ws.dns_sans == ("pi.local", "lhpc.example")
    assert ws.ip_sans == ("192.168.0.10",)


def test_webserver_malformed_is_diagnostic_not_crash(tmp_path):
    _write_local(
        tmp_path,
        '[webserver]\nport = 70000\naccess_mode = "bogus"\nremote_exposed = "yes"\n'
        'allowed_cidrs = "not-a-cidr, fd00::/8, 192.168.1.0/24"\n'  # bad + IPv6 dropped; good kept
        'ip_sans = "0.0.0.0, 10.1.2.3"\n',                          # 0.0.0.0 dropped
    )
    cfg = load_config(_paths(tmp_path))
    ws = cfg.webserver
    assert ws.port == 8443                                # bad port -> default
    assert ws.access_mode == "local-open-remote-auth"    # unknown -> default
    assert ws.remote_exposed is False                    # non-bool -> false
    assert ws.allowed_cidrs == ("192.168.1.0/24",)       # only the valid IPv4 CIDR survived
    assert ws.ip_sans == ("10.1.2.3",)
    assert cfg.diagnostics                                # problems surfaced, never crashed


def test_webserver_non_table_section_uses_defaults(tmp_path):
    _write_local(tmp_path, 'webserver = "oops"\n')
    cfg = load_config(_paths(tmp_path))
    assert cfg.webserver == WebserverConfig()
    assert any("webserver" in d for d in cfg.diagnostics)


def test_cidr_validator_normalizes_and_rejects():
    assert validators.cidr("192.168.0.5/24") == "192.168.0.0/24"   # host bits masked away
    assert validators.cidr("0.0.0.0/0") == "0.0.0.0/0"             # syntactically valid (danger gated elsewhere)
    for bad in ("192.168.0.1", "", "1.2.3.4/33", "999.1.1.1/24", "10.0.0.0/8; rm -rf", "x/24"):
        with pytest.raises(validators.ValidationError):
            validators.cidr(bad)
    with pytest.raises(validators.ValidationError):
        validators.cidr("fd00::/8")                                # IPv6 rejected for remote use
    assert validators.cidr("fd00::/8", allow_ipv6=True) == "fd00::/8"


def test_save_webserver_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    save_webserver_config(
        paths, bind="0.0.0.0", port=9443, access_mode="auth-everywhere",
        remote_exposed=True, allowed_cidrs=["192.168.0.0/24"], dns_sans=["pi.local"],
        ip_sans=["192.168.0.10"], server_cert_days=90,
    )
    ws = load_config(paths).webserver
    assert ws.bind == "0.0.0.0" and ws.port == 9443
    assert ws.access_mode == "auth-everywhere" and ws.remote_exposed is True
    assert ws.allowed_cidrs == ("192.168.0.0/24",)
    assert ws.dns_sans == ("pi.local",) and ws.ip_sans == ("192.168.0.10",)
    assert ws.server_cert_days == 90 and ws.client_cert_days == 825  # untouched key keeps default


def test_save_webserver_fail_closed(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(validators.ValidationError):
        save_webserver_config(paths, allowed_cidrs=["nonsense"])
    with pytest.raises(ConfigError):
        save_webserver_config(paths, access_mode="bogus")
    with pytest.raises(ConfigError):
        save_webserver_config(paths, ip_sans=["0.0.0.0"])
    with pytest.raises(validators.ValidationError):
        save_webserver_config(paths, allowed_cidrs=["fd00::/8"])   # IPv6 remote refused at save
    # nothing was persisted -> effective load still equals defaults
    assert load_config(paths).webserver == WebserverConfig()


# ===== merged from test_webserver_corrections.py =====
def _svc_webserver_corrections(tmp_path, fake=None):
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def _staged_webserver_corrections(paths):
    return str(paths.under(*webserver.NGINX_CONF_STAGED))


def _live(tmp_path):
    return tmp_path / "config" / "nginx" / "lhpc.conf"


def test_apply_invalid_config_leaves_live_intact(tmp_path):
    svc0 = _svc_webserver_corrections(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "config", "nginx")
    runtime_fs.atomic_write(paths, paths.under(*webserver.NGINX_CONF), "SENTINEL-LIVE\n", 0o644)
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged_webserver_corrections(paths)): CR(1, "", "emerg: bad")})
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_apply()
    assert not r.ok and "remains active" in r.summary
    assert _live(tmp_path).read_text() == "SENTINEL-LIVE\n"          # untouched


def test_apply_valid_promotes_staged(tmp_path):
    svc0 = _svc_webserver_corrections(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    live = str(paths.under(*webserver.NGINX_CONF))
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged_webserver_corrections(paths)): CR(0, "", "ok"),
                                ("nginx", "-s", "reload", "-c", live): CR(0, "", "")},
                      listeners=[Listener("ipv4", "127.0.0.1", 8443, 1)])
    svc = ControllerService(system=fake.system, paths=paths)
    assert svc.webserver_apply().ok
    assert _live(tmp_path).exists() and "server unix:" in _live(tmp_path).read_text()


def test_verify_does_not_touch_live_config(tmp_path):
    svc0 = _svc_webserver_corrections(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "config", "nginx")
    runtime_fs.atomic_write(paths, paths.under(*webserver.NGINX_CONF), "SENTINEL\n", 0o644)
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged_webserver_corrections(paths)): CR(0, "", "ok")})
    ControllerService(system=fake.system, paths=paths).webserver_verify()
    assert _live(tmp_path).read_text() == "SENTINEL\n"


def test_init_persists_sans_for_trusted_host_and_renew(tmp_path):
    svc = _svc_webserver_corrections(tmp_path)
    svc.webserver_init(dns_sans=["pi.local"], ip_sans=["192.168.0.10"])
    cfg = svc.config().webserver
    assert cfg.dns_sans == ("pi.local",) and cfg.ip_sans == ("192.168.0.10",)
    # tls-renew uses the saved SANs (no empty-SAN failure)
    assert ControllerService(system=FakeSystem().system, paths=svc._paths).webserver_tls_renew().ok
    # productive trusted-host accepts the SANs
    app = create_app(lambda: ControllerService(system=FakeSystem().system, paths=svc._paths))
    app.config["SESSION_COOKIE_SECURE"] = True
    c = app.test_client()
    assert c.get("/stacks", headers={"Host": "pi.local"}).status_code == 200
    assert c.get("/stacks", headers={"Host": "192.168.0.10"}).status_code == 200


def test_revoke_crl_failure_keeps_cert_active(tmp_path, monkeypatch):
    svc = _svc_webserver_corrections(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    monkeypatch.setattr(pki, "_build_and_write_crl",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    r = svc.webserver_cert_revoke("laptop")
    assert not r.ok and "ACTIVE" in r.summary                       # not falsely revoked
    assert all(c["state"] == "active" for c in pki.list_client_certs(svc._paths)
               if c["label"] == "laptop")


def test_revoke_index_save_failure_is_pending_not_active(tmp_path, monkeypatch):
    # Correction B: CRL written but inventory commit fails -> 'revocation-pending', not active.
    svc = _svc_webserver_corrections(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    monkeypatch.setattr(pki, "_save_index",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    r = svc.webserver_cert_revoke("laptop")
    assert not r.ok and "REVOCATION-PENDING" in r.summary
    states = {c["state"] for c in pki.list_client_certs(svc._paths) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}                          # never ordinary active/revoked
    assert (tmp_path / "config/tls/client-ca/crl.pem").exists()      # CRL was written


def _revoke_serial_in_crl_only(paths):
    """Write a CRL that revokes 'laptop' WITHOUT committing the inventory or a pending marker,
    returning the serial. Mimics: CRL write succeeded, inventory commit + marker both lost."""
    import copy
    idx = pki._load_index(paths)
    cand = copy.deepcopy(idx)
    cand["crl_number"] = int(cand.get("crl_number", 0)) + 1
    hit = next(e for e in cand["certs"] if e.get("label") == "laptop" and e.get("state") == "active")
    hit["state"] = "revoked"
    pki._build_and_write_crl(paths, cand)          # CRL now revokes the serial; index untouched
    return hit["serial"]


def test_revoke_pending_marker_and_index_both_fail_still_pending(tmp_path, monkeypatch):
    # Worst case: CRL written, but BOTH the inventory commit and the pending-marker write fail.
    # The CRL is authoritative -> the cert is still surfaced as revocation-pending, never active.
    svc = _svc_webserver_corrections(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    monkeypatch.setattr(pki, "_save_index",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(pki, "_add_pending",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("marker write failed")))
    r = svc.webserver_cert_revoke("laptop")
    assert not r.ok and "REVOCATION-PENDING" in r.summary          # truthful even when marker fails
    p = svc._paths
    assert not (tmp_path / "config/tls/client-ca/revocation-pending.json").exists()   # marker absent
    assert (tmp_path / "config/tls/client-ca/crl.pem").exists()                       # CRL written
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}                        # never ordinary active/revoked


def test_crl_is_truth_source_when_pending_marker_missing_or_malformed(tmp_path):
    svc = _svc_webserver_corrections(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    p = svc._paths
    serial = _revoke_serial_in_crl_only(p)         # CRL revokes serial; inventory still 'active'
    marker = tmp_path / "config/tls/client-ca/revocation-pending.json"
    assert not marker.exists()                     # missing marker
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}        # CRL alone drives the truthful state
    # malformed marker: still pending (CRL wins; malformed marker tolerated as no-evidence)
    marker.write_text("}{ not json")
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}
    # the raw inventory on disk was never mutated -> the CRL is genuinely the overlay source
    assert any(c["state"] == "active" and c["serial"] == serial
               for c in pki._load_index(p)["certs"])


def test_active_cert_not_in_crl_stays_active(tmp_path):
    # A cert whose serial is NOT in the CRL must remain ordinary active (no false positives).
    svc = _svc_webserver_corrections(tmp_path); svc.webserver_init()
    svc.webserver_cert_issue("keep", "pw"); svc.webserver_cert_issue("gone", "pw")
    p = svc._paths
    assert svc.webserver_cert_revoke("gone").ok     # clean revoke -> committed
    by_label = {c["label"]: c["state"] for c in pki.list_client_certs(p)}
    assert by_label["keep"] == "active"             # not in CRL -> untouched
    assert by_label["gone"] == "revoked"            # committed revoked stays revoked


def test_committed_revoked_stays_revoked(tmp_path):
    svc = _svc_webserver_corrections(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    p = svc._paths
    assert svc.webserver_cert_revoke("laptop").ok
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revoked"}                    # never downgraded to pending by the CRL overlay


def test_init_fails_closed_on_san_persist_failure(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc = _svc_webserver_corrections(tmp_path)
    monkeypatch.setattr(cfgmod, "save_webserver_config",
                        lambda *a, **k: (_ for _ in ()).throw(cfgmod.ConfigError("save failed")))
    r = svc.webserver_init(dns_sans=["pi.local"], ip_sans=["192.168.0.10"])
    assert not r.ok and "no PKI was created" in r.summary            # failed, no success message
    st = pki.pki_status(svc._paths)
    assert not st["server_ca"]["present"] and not st["client_ca"]["present"]
    assert not st["server_cert"]["present"]                          # nothing created/replaced


def test_cli_revoke_requires_confirm_label(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path)); (tmp_path / "config").mkdir(exist_ok=True)
    assert main(["webserver", "init"]) == 0
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    capsys.readouterr()
    assert main(["webserver", "cert", "revoke", "laptop"]) == 1     # no --confirm-label -> refused
    p = Paths(runtime_root=tmp_path)
    assert all(c["state"] == "active" for c in pki.list_client_certs(p) if c["label"] == "laptop")
    assert main(["webserver", "cert", "revoke", "laptop", "--confirm-label", "laptop"]) == 0
    assert any(c["state"] == "revoked" for c in pki.list_client_certs(p) if c["label"] == "laptop")


def test_start_service_refuses_from_managed_unit(monkeypatch, tmp_path):
    monkeypatch.setenv("INVOCATION_ID", "managed")
    r = _svc_webserver_corrections(tmp_path).webserver_start_service()
    assert not r.ok and "managed unit" in r.summary


def test_start_service_prereqs(monkeypatch, tmp_path):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    r = _svc_webserver_corrections(tmp_path).webserver_start_service()          # no nginx (FakeSystem) -> refused
    assert not r.ok and "nginx is not installed" in r.summary


def test_start_service_enables_and_starts(monkeypatch, tmp_path):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    svc0 = _svc_webserver_corrections(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    fake = FakeSystem(commands={
        ("nginx", "-v"): CR(0, "", ""),
        ("nginx", "-t", "-c", _staged_webserver_corrections(paths)): CR(0, "", "ok"),
        ("systemctl", "--user", "enable", "--now", "lhpc-nginx.service"): CR(0, "", ""),
    })
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_start_service()
    assert r.ok and "https://" in r.summary
    assert ["systemctl", "--user", "enable", "--now", "lhpc-nginx.service"] in fake.calls
    assert _live(tmp_path).exists()                        # config promoted
    assert (tmp_path / "state" / "run" / "nginx").is_dir()  # rootless temp-path parent created
    assert (tmp_path / "logs").is_dir()                     # nginx error/access log parent


# ===== merged from test_webserver_evidence.py =====
def test_evidence_absent_and_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    assert webserver.read_evidence(paths) == {}
    webserver.write_evidence(paths, {"checked_at": "T", "effective": {"remote_listener": True}})
    ev = webserver.read_evidence(paths)
    assert ev["effective"]["remote_listener"] is True and ev["schema"] == webserver.EVIDENCE_SCHEMA


def test_evidence_malformed_is_empty(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "webserver.json").write_text("{ not json")
    assert webserver.read_evidence(paths) == {}


def test_monitor_reports_local_ip(tmp_path):
    view = webserver.monitor_view(_paths(tmp_path), WebserverConfig())
    assert "local_ip" in view
    ip = view["local_ip"]
    assert isinstance(ip, str) and not ip.startswith("127.")   # '' or a real LAN IPv4, never loopback


def test_local_ip_is_failsoft_string():
    ip = webserver.local_ip()
    assert isinstance(ip, str)                                  # never raises; '' when undeterminable


def test_monitor_never_infers_active_from_desired(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True, allowed_cidrs=("192.168.0.0/24",))
    view = webserver.monitor_view(paths, cfg)             # no evidence, no live scope
    assert view["desired"]["remote_exposed"] is True
    assert view["effective"] == {}                        # unknown, NOT inferred active
    # No proof of a listener (scope None) -> the honest 'absent' branch, prompting an activation step.
    assert any("no listener is active" in w["text"] and w["level"] == "warn"
               for w in view["warnings"])


def test_monitor_no_auth_remote_has_persistent_danger_warning(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                          allowed_cidrs=("192.168.0.0/24",), access_mode="no-auth")
    warns = webserver.monitor_view(paths, cfg)["warnings"]
    assert any(w["level"] == "danger" and "without client authentication" in w["text"]
               for w in warns)


def _exposed_cfg():
    return WebserverConfig(bind="0.0.0.0", remote_exposed=True, allowed_cidrs=("192.168.0.0/24",))


def test_monitor_live_scope_exposed_shows_active_and_no_false_warning(tmp_path):
    # The reported bug: a working, exposed console must NOT warn "not active" / "unproven".
    view = webserver.monitor_view(_paths(tmp_path), _exposed_cfg(), live_listener_scope="exposed")
    texts = [w["text"] for w in view["warnings"]]
    assert any(w["level"] == "ok" and "Remote listener active on 0.0.0.0:8443" in w["text"]
               for w in view["warnings"])
    assert not any("not active" in t or "unproven" in t or "loopback-only" in t
                   or "no listener is active" in t for t in texts)
    assert view["effective"]["remote_listener"] is True
    assert view["effective"]["listener_scope"] == "exposed"


def test_monitor_live_scope_loopback_prompts_apply(tmp_path):
    view = webserver.monitor_view(_paths(tmp_path), _exposed_cfg(), live_listener_scope="loopback")
    assert any(w["level"] == "warn" and "loopback-only" in w["text"] and "Apply" in w["text"]
               for w in view["warnings"])
    assert view["effective"]["remote_listener"] is False


def test_monitor_live_scope_absent_prompts_start_service(tmp_path):
    # 'absent' is distinct from 'loopback' — a bool would have mislabelled a not-running nginx.
    view = webserver.monitor_view(_paths(tmp_path), _exposed_cfg(), live_listener_scope="absent")
    assert any(w["level"] == "warn" and "no listener is active" in w["text"]
               and "start-service" in w["text"] for w in view["warnings"])
    assert not any("loopback-only" in w["text"] for w in view["warnings"])


def test_monitor_not_exposed_is_a_single_disabled_info(tmp_path):
    view = webserver.monitor_view(_paths(tmp_path), WebserverConfig(),  # loopback default
                                  live_listener_scope="loopback")
    exposure = [w for w in view["warnings"] if "exposure" in w["text"] or "listener" in w["text"]]
    assert exposure == [{"level": "info",
                         "text": "Remote exposure is disabled — listening on loopback only."}]


def test_monitor_desired_disabled_but_live_listener_exposed_warns(tmp_path):
    # P2: `webserver_disable_remote` writes intent only (no reload). If the old nginx still binds
    # 0.0.0.0, the panel must NOT say "disabled — loopback only" — that is what is actually reachable.
    cfg = WebserverConfig(remote_exposed=False)     # desired disabled…
    view = webserver.monitor_view(_paths(tmp_path), cfg, live_listener_scope="exposed")  # …live exposed
    assert any(w["level"] == "warn" and "disabled in desired config" in w["text"]
               and "still exposed" in w["text"] for w in view["warnings"])
    assert not any("listens on loopback only" in w["text"] for w in view["warnings"])
    assert view["effective"]["remote_listener"] is True     # honest about what is reachable


def test_monitor_falls_back_to_cached_scope_without_a_live_arg(tmp_path):
    paths = _paths(tmp_path)
    webserver.write_evidence(paths, {"checked_at": "T",
                                     "effective": {"remote_listener": True,
                                                   "listener_scope": "exposed"}})
    view = webserver.monitor_view(paths, _exposed_cfg())     # no live arg -> use cached scope
    assert any(w["level"] == "ok" and "Remote listener active" in w["text"] for w in view["warnings"])


def _sys_listen(*listeners, nginx=True, nginx_t_ok=True, conf_path=""):
    cmds = {}
    if nginx:
        cmds[("nginx", "-v")] = CommandResult(0, "", "nginx version: nginx/1.24")
        if conf_path:
            cmds[("nginx", "-t", "-c", conf_path)] = CommandResult(
                0 if nginx_t_ok else 1, "", "ok" if nginx_t_ok else "emerg")
    return FakeSystem(commands=cmds, listeners=[Listener(**l) for l in listeners])


def test_verify_records_exposed_scope_for_a_wildcard_listener(tmp_path):
    paths = _paths(tmp_path)
    for fn in (pki.init_server_ca, pki.init_client_ca):
        fn(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)
    pki.build_crl(paths)
    cfg = _exposed_cfg()
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    sys = _sys_listen({"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 1},
                      conf_path=conf).system
    ev = webserver.verify(sys, paths, cfg)
    assert ev["effective"]["remote_listener"] is True
    assert ev["effective"]["listener_scope"] == "exposed"


def test_verify_records_loopback_and_absent_scopes(tmp_path):
    paths = _paths(tmp_path)
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    loop = _sys_listen({"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1},
                       conf_path=conf).system
    ev = webserver.verify(loop, paths, WebserverConfig())
    assert ev["effective"]["listener_scope"] == "loopback" and ev["effective"]["remote_listener"] is False
    none = _sys_listen(conf_path=conf).system                 # nothing listening
    ev2 = webserver.verify(none, paths, WebserverConfig())
    assert ev2["effective"]["listener_scope"] == "absent" and ev2["effective"]["remote_listener"] is False


def test_verify_fails_when_desired_exposed_but_listener_loopback(tmp_path):
    # A bind change applied via `nginx -s reload` can leave the master on the old loopback socket
    # while reload returns success. verify() must FAIL that, not report OK (the F3 remote-403 bug).
    paths = _paths(tmp_path)
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    sys = _sys_listen({"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1},
                      conf_path=conf).system                    # effective: still loopback
    ev = webserver.verify(sys, paths, _exposed_cfg())           # desired: remote_exposed on 0.0.0.0
    assert ev["checks"]["remote_listener_matches"] == "failed"
    assert ev["effective"]["remote_listener"] is False


def test_verify_remote_listener_matches_both_directions(tmp_path):
    paths = _paths(tmp_path)
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    def scope_check(cfg, *listeners):
        sys = _sys_listen(*listeners, conf_path=conf).system
        return webserver.verify(sys, paths, cfg)["checks"]["remote_listener_matches"]
    loop = {"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1}
    wild = {"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 2}
    # EXACT scope required. loopback desired: only a LOOPBACK listener matches — ABSENT is a dead
    # front-end (a failed restart), never a "successful local bind".
    assert scope_check(WebserverConfig(), loop) == "ok"
    assert scope_check(WebserverConfig()) == "failed"           # absent = no frontend at all
    # loopback desired but still exposed -> residual exposure FAILS
    assert scope_check(WebserverConfig(), wild) == "failed"
    # exposed desired + exposed effective -> MATCH; absent fails this direction too
    assert scope_check(_exposed_cfg(), wild) == "ok"
    assert scope_check(_exposed_cfg()) == "failed"


def _fake(conf_path: str, *, nginx=True, nginx_t_ok=True) -> FakeSystem:
    cmds = {}
    if nginx:
        cmds[("nginx", "-v")] = CommandResult(0, "", "nginx version: nginx/1.24")
        cmds[("nginx", "-t", "-c", conf_path)] = CommandResult(
            0 if nginx_t_ok else 1, "", "configuration test is successful" if nginx_t_ok else "emerg")
    return FakeSystem(commands=cmds)


def test_verify_static_checks_and_persists_evidence(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    pki.init_client_ca(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)
    pki.build_crl(paths)
    cfg = WebserverConfig()
    conf_path = str(paths.under(*webserver.NGINX_CONF_STAGED))
    ev = webserver.verify(_fake(conf_path).system, paths, cfg)
    c = ev["checks"]
    assert c["nginx_present"] == "ok" and c["nginx_config_valid"] == "ok"
    assert c["server_ca"] == "ok" and c["server_cert"] == "ok"
    assert c["client_ca"] == "ok" and c["crl"] == "ok"
    # live effective checks are honestly unproven; remote is NOT reported active
    assert ev["effective"]["remote_listener"] is False
    assert ev["effective"]["revocation_enforced"] is None
    # persisted + surfaced through monitor
    assert webserver.read_evidence(paths)["checked_at"] == ev["checked_at"]
    assert webserver.monitor_view(paths, cfg)["last_verified"] == ev["checked_at"]


def test_verify_reports_missing_nginx_and_certs(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig()
    conf_path = str(paths.under(*webserver.NGINX_CONF_STAGED))
    ev = webserver.verify(_fake(conf_path, nginx=False).system, paths, cfg)
    assert ev["checks"]["nginx_present"] == "failed"
    assert ev["checks"]["server_cert"] == "failed"      # no CA/cert issued


# ===== merged from test_webserver_gui.py =====
def _app_svc(tmp_path: Path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(lambda: svc), svc


def _csrf_webserver_gui(client):
    with client.session_transaction() as s:
        s["_csrf"] = "tok"
    return "tok"


def test_webserver_component_inline_on_stacks_cached_only(tmp_path):
    # The Webserver component is rendered INLINE in the controller row on /stacks (no separate
    # page); GET must not probe/mutate.
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    r = c.get("/stacks")
    assert r.status_code == 200
    body = r.data.decode()
    assert 'id="webserver-row"' in body
    assert "Webserver" in body and "Monitor" in body and "Certificates" in body and "Settings" in body
    assert "Local IP address" in body                          # first Monitor line
    assert not (tmp_path / "state" / "webserver.json").exists()


def test_console_running_pill_is_request_scoped(tmp_path):
    # The console running pill reflects HOW THIS SESSION arrived, not whether some nginx is running:
    # a direct dev-server request (no X-LHPC-Peer) reads yellow "lhpc-web"; a request proxied through
    # nginx (which sets X-LHPC-Peer) reads green "nginx".
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    assert ">lhpc-web</span>" in c.get("/stacks").get_data(as_text=True)
    proxied = c.get("/stacks", headers={"X-LHPC-Peer": "loopback"}).get_data(as_text=True)
    assert ">nginx</span>" in proxied and ">lhpc-web</span>" not in proxied


def test_console_pill_reattaches_port_behind_nginx(tmp_path):
    # nginx forwards a PORTLESS Host ($host), so the console pill must reattach the nginx console port;
    # the raw dev server carries the port in Host directly. A behind-nginx REMOTE peer means the console
    # is remote-exposed, so the bare IP-literal Host is legitimately accepted by the trusted-host policy.
    from lhpc.core import config as _config
    _config.save_webserver_config(Paths(runtime_root=tmp_path), remote_exposed=True)
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    proxied = c.get("/stacks", headers={"X-LHPC-Peer": "remote", "Host": "192.168.1.5"}).get_data(as_text=True)
    assert "192.168.1.5:8443" in proxied                     # host + nginx console port
    direct = c.get("/stacks", headers={"Host": "127.0.0.1:8770"}).get_data(as_text=True)
    assert "127.0.0.1:8770" in direct                        # dev server: port already in Host


def test_old_webserver_path_redirects_to_stacks(tmp_path):
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    r = c.get("/stacks/loraham-pi-control")
    assert r.status_code == 302 and r.headers["Location"].endswith("#webserver-row")


def test_webserver_logs_page_and_component_link(tmp_path):
    from lhpc.core import runtime_fs
    app, svc = _app_svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-error.log"),
                            "boom [emerg] mkdir failed\n", 0o644)
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-access.log"),
                            "GET / 200\n", 0o644)
    c = app.test_client()
    body = c.get("/stacks").data.decode()
    # each of the three webserver sub-section headers (Settings/Monitor/Certificates) carries its own
    # "logs" affordance, laid out like the main stack rows (overlay OUTSIDE the summary).
    assert body.count('aria-label="webserver logs"') == 3
    # component on /stacks links to the logs page
    assert "/webserver/logs" in body
    # error log (default + explicit) and access log render their tails
    assert "[emerg] mkdir failed" in c.get("/webserver/logs").data.decode()
    assert "[emerg] mkdir failed" in c.get("/webserver/logs?src=error").data.decode()
    assert "GET / 200" in c.get("/webserver/logs?src=access").data.decode()
    # unknown src falls back to the error log, never traverses
    assert "[emerg] mkdir failed" in c.get("/webserver/logs?src=../etc").data.decode()


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_expose_failure_without_cidr_is_refused_and_not_exposed(tmp_path):
    # A remote-exposure (bind off-loopback) with a valid phrase but no CIDR is refused via the unified
    # Apply: the failure detail is shown and, critically, the listener is NOT exposed.
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf_webserver_gui(c)
    r = c.post("/webserver/configure",
               data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "", "confirm_phrase": "enable-remote"},
               follow_redirects=True)
    assert "at least one allowed source CIDR" in r.data.decode()  # the actual failure detail
    assert svc.config().webserver.remote_exposed is False         # not exposed (the safety fact)


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_post_requires_csrf(tmp_path):
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    assert c.post("/webserver/configure", data={"access_mode": "no-auth"}).status_code == 400


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_configure_via_post(tmp_path):
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf_webserver_gui(c)
    r = c.post("/webserver/configure", data={"_csrf": tok, "access_mode": "auth-everywhere"})
    assert r.status_code == 302
    assert svc.config().webserver.access_mode == "auth-everywhere"


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_expose_requires_confirmation(tmp_path):
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf_webserver_gui(c)
    # bind off-loopback (remote) with a CIDR but no confirmation phrase -> refused, not exposed.
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "192.168.0.0/24"})
    assert svc.config().webserver.remote_exposed is False


@pytest.mark.contract
@pytest.mark.safety("exposure-fail-closed")
def test_p12_download_is_loopback_only(tmp_path):
    app, svc = _app_svc(tmp_path)
    c = app.test_client()
    tok = _csrf_webserver_gui(c)
    svc.webserver_init()
    c.post("/webserver/cert", data={"_csrf": tok, "op": "issue", "label": "laptop"})
    # remote peer (nginx-set header) -> refused
    assert c.get("/webserver/cert/laptop/download",
                 headers={"X-LHPC-Peer": "remote"}).status_code == 403
    # loopback (no nginx header) -> served as a pkcs12 attachment
    r = c.get("/webserver/cert/laptop/download")
    assert r.status_code == 200 and r.mimetype == "application/x-pkcs12"
    assert r.headers["Content-Disposition"].endswith('laptop.p12"')


def test_webserver_reachable_from_stacks_and_not_a_managed_stack(tmp_path):
    # Reachable inline under the controller row on /stacks; the controller id is NOT a managed
    # stack (a bogus stack-detail 404s; it never enters build_snapshot).
    app, _ = _app_svc(tmp_path)
    c = app.test_client()
    body = c.get("/stacks").data.decode()
    assert 'id="webserver-row"' in body and "Webserver (HTTPS / mTLS)" in body
    assert c.get("/stacks/loraham-pi-control").status_code == 302        # old path -> redirect
    assert c.get("/stacks/loraham-pi-control-bogus").status_code == 404


# ===== merged from test_webserver_hardening.py =====
def test_session_secret_persists_and_rotates(tmp_path):
    paths = _paths(tmp_path)
    s1 = config.web_session_secret(paths)
    assert len(s1) >= 32
    assert config.web_session_secret(paths) == s1        # stable across calls (survives restart)
    import os, stat
    mode = stat.S_IMODE(os.stat(tmp_path / "config/secrets/web_session.key").st_mode)
    assert mode == 0o600
    s2 = config.rotate_web_session_secret(paths)
    assert s2 != s1 and config.web_session_secret(paths) == s2   # explicit rotation changed it


def test_create_app_uses_persistent_secret(tmp_path):
    paths = _paths(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    app1 = webapp.create_app(lambda: svc)
    app2 = webapp.create_app(lambda: ControllerService(system=FakeSystem().system, paths=paths))
    assert app1.secret_key == app2.secret_key == config.web_session_secret(paths)
    # cookie hardening defaults (Secure enabled only on the productive socket path)
    assert app1.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app1.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app1.config["SESSION_COOKIE_SECURE"] is False


def test_peer_is_loopback_trusts_only_nginx_header(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path))
    app = webapp.create_app(lambda: svc)
    with app.test_request_context(headers={"X-LHPC-Peer": "remote"}):
        assert webapp.peer_is_loopback() is False
    with app.test_request_context(headers={"X-LHPC-Peer": "loopback"}):
        assert webapp.peer_is_loopback() is True
    # A client-supplied spoof cannot help: only the nginx-set value is read; absent => not remote.
    with app.test_request_context():
        assert webapp.peer_is_loopback() is True


# ===== merged from test_webserver_nginx.py =====
def _render(tmp_path, **kw):
    cfg = WebserverConfig(**kw)
    return webserver.render_nginx_config(_paths(tmp_path), cfg)


def test_mode_local_open_remote_auth(tmp_path):
    conf = _render(tmp_path, access_mode="local-open-remote-auth")
    assert "ssl_verify_client optional;" in conf
    assert 'map "$lhpc_peer:$ssl_client_verify" $lhpc_need_auth' in conf
    assert '"~^loopback:" 0;' in conf and '"~^remote:SUCCESS$" 0;' in conf
    assert "ssl_client_certificate" in conf and "ssl_crl" in conf   # mTLS material present
    assert "if ($lhpc_need_auth) { return 403; }" in conf


def test_mode_auth_everywhere_is_mandatory(tmp_path):
    conf = _render(tmp_path, access_mode="auth-everywhere")
    assert "ssl_verify_client on;" in conf                          # handshake-mandatory
    assert 'map "$ssl_client_verify" $lhpc_need_auth' in conf


def test_mode_no_auth_has_no_mtls_material(tmp_path):
    conf = _render(tmp_path, access_mode="no-auth")
    assert "ssl_verify_client off;" in conf
    assert "ssl_client_certificate" not in conf and "ssl_crl" not in conf
    assert "$lhpc_need_auth {\n        default 0;" in conf          # never rejects on cert


def test_not_exposed_forces_loopback_listen_even_with_stale_bind(tmp_path):
    conf = _render(tmp_path, bind="0.0.0.0", remote_exposed=False, port=8443)
    assert "listen 127.0.0.1:8443 ssl;" in conf
    assert "listen 0.0.0.0" not in conf                             # no remote listener


def test_exposed_binds_wildcard_and_gates_cidrs(tmp_path):
    conf = _render(tmp_path, bind="0.0.0.0", remote_exposed=True, port=8443,
                   allowed_cidrs=("192.168.0.0/24",), access_mode="local-open-remote-auth")
    assert "listen 0.0.0.0:8443 ssl;" in conf
    assert "allow 127.0.0.1;" in conf and "allow 192.168.0.0/24;" in conf
    assert "deny all;" in conf


def test_headers_are_stripped_and_evidence_set(tmp_path):
    conf = _render(tmp_path)
    assert 'proxy_set_header X-Forwarded-For "";' in conf
    assert 'proxy_set_header Forwarded "";' in conf
    assert "proxy_set_header X-LHPC-Peer $lhpc_peer;" in conf
    assert "proxy_set_header X-LHPC-Client-Verify $ssl_client_verify;" in conf
    assert "geo $lhpc_peer {" in conf and "127.0.0.0/8 loopback;" in conf
    assert "server unix:" in conf and "lhpc-web.sock" in conf       # backend is the unix socket


def test_plan_exposure_defaults_local():
    p = webserver.plan_exposure(WebserverConfig())
    assert p["remote"] is False and p["problems"] == []


def test_plan_exposure_requires_cidr():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True))
    assert p["remote"] is True and any("at least one allowed source CIDR" in x for x in p["problems"])


def test_plan_exposure_public_route_is_elevated():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                                                allowed_cidrs=("0.0.0.0/0",)))
    assert p["public"] is True and p["danger"] == "elevated"


def test_plan_exposure_no_auth_remote_is_elevated():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                                                allowed_cidrs=("192.168.0.0/24",),
                                                access_mode="no-auth"))
    assert p["danger"] == "elevated" and p["no_auth"] is True


def test_plan_exposure_flags_contradictory_remote_bind_when_not_exposed():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=False))
    assert p["remote"] is False and any("non-loopback" in x for x in p["problems"])


def test_validate_config_ok(tmp_path):
    fake = FakeSystem(commands={
        ("nginx", "-t", "-c", "/x/lhpc.conf"): CommandResult(0, "", "nginx: configuration test is successful"),
    })
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert ok and "successful" in msg
    assert ["nginx", "-t", "-c", "/x/lhpc.conf"] in fake.calls


def test_validate_config_failure(tmp_path):
    fake = FakeSystem(commands={
        ("nginx", "-t", "-c", "/x/lhpc.conf"): CommandResult(1, "", "nginx: [emerg] bad thing"),
    })
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert not ok and "bad thing" in msg


def test_validate_config_surfaces_emerg_cause_not_generic_tail(tmp_path):
    # Real rootless failure: the '[emerg] mkdir…' CAUSE precedes a generic 'test failed' tail. The
    # message must carry the cause, not the useless last line (the bug behind the opaque error).
    stderr = ('nginx: [emerg] mkdir() "/r/state/run/nginx/body" failed (2: No such file or directory)\n'
              "nginx: configuration file /r/config/nginx/lhpc.conf.staged test failed")
    fake = FakeSystem(commands={("nginx", "-t", "-c", "/x/lhpc.conf"): CommandResult(1, "", stderr)})
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert not ok
    assert "[emerg]" in msg and "mkdir()" in msg          # the actual cause
    assert "test failed" not in msg                       # not the generic tail


def test_validate_config_nginx_absent(tmp_path):
    fake = FakeSystem()      # unknown command -> not_found default
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert not ok and "not installed" in msg and "sudo apt install -y nginx" in msg


def test_stage_and_validate_creates_rootless_runtime_dirs(tmp_path):
    # The temp-path parent state/run/nginx (and logs/) must exist BEFORE nginx -t, else rootless
    # nginx's single-level mkdir of body/proxy/… fails. stage_and_validate ensures them.
    paths = _paths(tmp_path)
    staged = paths.under(*webserver.NGINX_CONF_STAGED)
    fake = FakeSystem(commands={
        ("nginx", "-t", "-c", str(staged)): CommandResult(0, "", "configuration test is successful"),
    })
    ok, msg, out = webserver.stage_and_validate(fake.system, paths, WebserverConfig())
    assert ok and out == staged
    assert (tmp_path / "state" / "run" / "nginx").is_dir()   # temp-path parent nginx needs
    assert (tmp_path / "logs").is_dir()                       # error/access log parent
    assert staged.exists()                                    # config was staged for the -t


def test_console_urls_loopback_only_never_offers_the_lan_address(tmp_path, monkeypatch):
    # Not exposed -> the LAN address would NOT answer. Offering it would be a lie.
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.1.50")
    assert webserver.console_urls(WebserverConfig(port=8443)) == ["https://127.0.0.1:8443/"]


def test_console_urls_exposed_puts_the_lan_address_first(tmp_path, monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.1.50")
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True, port=8443,
                          allowed_cidrs=("192.168.1.0/24",))
    assert webserver.console_urls(cfg) == ["https://192.168.1.50:8443/", "https://127.0.0.1:8443/"]


def test_console_urls_degrade_when_local_ip_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "")        # loopback-only host / failure
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True, port=9443,
                          allowed_cidrs=("10.0.0.0/8",))
    assert webserver.console_urls(cfg) == ["https://127.0.0.1:9443/"]


def test_nginx_serves_static_updating_page_on_502(tmp_path):
    # On a 502/503/504 (e.g. the Waitress upstream gone mid self-update) nginx serves a branded
    # static page from disk (no upstream, no JS) instead of the raw "502 Bad Gateway".
    paths = _paths(tmp_path)
    conf = webserver.render_nginx_config(paths, WebserverConfig())
    assert "error_page 502 503 504 /_lhpc_updating.html;" in conf
    assert "location = /_lhpc_updating.html" in conf and "internal;" in conf and "alias " in conf
    # stage_and_validate must WRITE the actual static file at the served path.
    staged = paths.under(*webserver.NGINX_CONF_STAGED)
    fake = FakeSystem(commands={("nginx", "-t", "-c", str(staged)): CommandResult(0, "", "ok")})
    webserver.stage_and_validate(fake.system, paths, WebserverConfig())
    page = tmp_path / "config" / "nginx" / "_lhpc_updating.html"
    assert page.is_file()
    html = page.read_text()
    assert "Return to the console" in html and "<script" not in html


# ===== merged from test_webserver_serve.py =====
def test_tcp_mode_still_refuses_non_loopback():
    # Interactive TCP path keeps the loopback-only guard (regression of existing behavior).
    assert run_server(host="1.2.3.4", port=8770, socket=False) == 1


def test_socket_mode_fail_closed_without_waitress(monkeypatch, tmp_path: Path):
    # Simulate waitress absent: productive (socket) serving must FAIL CLOSED, never fall back
    # to the Flask dev server.
    monkeypatch.setitem(sys.modules, "waitress", None)   # `from waitress import serve` -> ImportError
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "state" / "locks").mkdir(parents=True)
    rc = run_server(socket=True)
    assert rc == 1        # refused; no dev-server fallback on the productive path


# ===== merged from test_webserver_service.py =====
def _svc_webserver_service(tmp_path: Path, fake: FakeSystem | None = None) -> ControllerService:
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def test_init_bootstraps_pki(tmp_path):
    svc = _svc_webserver_service(tmp_path)
    r = svc.webserver_init(dns_sans=["pi.local"])
    assert r.ok
    st = pki.pki_status(svc._paths)
    assert st["server_ca"]["present"] and st["client_ca"]["present"] and st["server_cert"]["present"]
    assert pki.cas_are_distinct(svc._paths)


def test_webserver_log_tail_reads_nginx_logs(tmp_path):
    from lhpc.core import runtime_fs
    svc = _svc_webserver_service(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-error.log"),
                            "e1\ne2\n", 0o644)
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-access.log"),
                            "a1\na2\n", 0o644)
    ep, el = svc.webserver_log_tail("error")
    ap, al = svc.webserver_log_tail("access")
    assert ep.endswith("logs/nginx-error.log") and el == ["e1", "e2"]
    assert ap.endswith("logs/nginx-access.log") and al == ["a1", "a2"]
    # unknown selector degrades to the error log (never an arbitrary path)
    up, ul = svc.webserver_log_tail("../../etc/passwd")
    assert up.endswith("logs/nginx-error.log") and ul == ["e1", "e2"]


def test_controller_log_tail_files(tmp_path):
    # The controller's own logs are on-disk FILES (StandardOutput=append:), read like the nginx logs.
    from lhpc.core import runtime_fs
    svc = _svc_webserver_service(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "lhpc-web.log"), "w1\nw2\n", 0o644)
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "lhpc-selfupdate.log"), "s1\n", 0o644)
    wp, wl = svc.controller_log_tail("web")
    sp, sl = svc.controller_log_tail("selfupdate")
    assert wp.endswith("logs/lhpc-web.log") and wl == ["w1", "w2"]
    assert sp.endswith("logs/lhpc-selfupdate.log") and sl == ["s1"]
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "lhpc-boot-restore.log"),
                            "b1\n", 0o644)
    bp, bl = svc.controller_log_tail("boot-restore")
    assert bp.endswith("logs/lhpc-boot-restore.log") and bl == ["b1"]
    # unknown source -> ("", []): an explicit immutable map, never aliased to another unit's log
    # (the web layer normalizes unknown selectors to "web" BEFORE calling).
    assert svc.controller_log_tail("bogus") == ("", [])
    assert svc.controller_log_tail("web", 10 ** 9)[1] == ["w1", "w2"]
    assert svc.controller_log_tail("web", "oops")[1] == ["w1", "w2"]


def test_controller_log_tail_missing_and_symlink(tmp_path):
    import os
    from lhpc.core import runtime_fs
    svc = _svc_webserver_service(tmp_path)
    p, lines = svc.controller_log_tail("web")                 # missing -> resolved path + empty
    assert p.endswith("logs/lhpc-web.log") and lines == []
    runtime_fs.mkdir(svc._paths, "logs")
    (tmp_path / "secret.txt").write_text("TOP SECRET\n")
    os.symlink(tmp_path / "secret.txt", tmp_path / "logs" / "lhpc-web.log")
    assert svc.controller_log_tail("web")[1] == []            # symlink not followed


def test_webserver_init_default_sans_match_endpoint(tmp_path):
    # First-run init with NO SANs must produce a cert whose SANs match the advertised
    # https://127.0.0.1:8443/ endpoint: DNS 'localhost' + IP '127.0.0.1', persisted to desired config.
    from cryptography import x509
    svc = _svc_webserver_service(tmp_path)
    assert svc.webserver_init().ok
    cfg = svc.config().webserver
    assert cfg.dns_sans == ("localhost",) and cfg.ip_sans == ("127.0.0.1",)     # persisted
    cert = x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server" / "server.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert "127.0.0.1" in [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    # tls-renew preserves both SANs
    assert svc.webserver_tls_renew().ok
    cert2 = x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server" / "server.crt").read_bytes())
    san2 = cert2.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san2.get_values_for_type(x509.DNSName)
    assert "127.0.0.1" in [str(i) for i in san2.get_values_for_type(x509.IPAddress)]


def test_webserver_log_tail_line_count_is_clamped(tmp_path):
    from lhpc.core import runtime_fs
    svc = _svc_webserver_service(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-error.log"), "x\n", 0o644)
    assert svc.webserver_log_tail("error", 10 ** 9)[1] == ["x"]     # absurd count clamped, no crash
    assert svc.webserver_log_tail("error", -5)[1] == ["x"]          # negative clamped to >=1
    assert svc.webserver_log_tail("error", "oops")[1] == ["x"]      # non-int -> default, no raise


def test_webserver_log_tail_missing_and_symlink(tmp_path):
    import os
    from lhpc.core import runtime_fs
    svc = _svc_webserver_service(tmp_path)
    # missing file -> resolved path, empty tail (no crash)
    p, lines = svc.webserver_log_tail("error")
    assert p.endswith("logs/nginx-error.log") and lines == []
    # a symlinked log leaf is refused (empty), never followed
    runtime_fs.mkdir(svc._paths, "logs")
    (tmp_path / "secret.txt").write_text("TOP SECRET\n")
    os.symlink(tmp_path / "secret.txt", tmp_path / "logs" / "nginx-error.log")
    p2, lines2 = svc.webserver_log_tail("error")
    assert lines2 == []                                   # symlink not followed


def test_configure_validates(tmp_path):
    svc = _svc_webserver_service(tmp_path)
    assert not svc.webserver_configure(access_mode="bogus").ok
    assert not svc.webserver_configure(allowed_cidrs=["nope"]).ok
    ok = svc.webserver_configure(dns_sans=["pi.local"], port=8443)
    assert ok.ok and svc.config().webserver.dns_sans == ("pi.local",)


def _capture_certs(monkeypatch):
    """Record every issue_server_cert(**kwargs) instead of doing real crypto."""
    from lhpc.core import pki as _pki
    calls = []
    monkeypatch.setattr(_pki, "issue_server_cert",
                        lambda paths, **kw: calls.append(kw) or {"ok": True})
    return calls


def test_expose_adds_the_lan_ip_san_and_reissues_the_cert(tmp_path, monkeypatch):
    # THE FIX: nothing used to persist the LAN IP, so a remote browser got a 400 (unknown Host) and
    # a certificate name mismatch.
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.168.178.66")
    calls = _capture_certs(monkeypatch)
    svc = _svc_webserver_service(tmp_path)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok
    assert svc.config().webserver.ip_sans == ("192.168.178.66",)
    assert calls and calls[-1]["ip_sans"] == ["192.168.178.66"]
    assert any("192.168.178.66 added to ip_sans" in d for d in res.details)


def test_expose_issues_the_cert_from_disk_not_an_in_memory_union(tmp_path, monkeypatch):
    """The cert must be issued from config RE-READ after the SAN write, never from the in-memory
    list we just built. `self.config()` is memoized, so the second `_invalidate_config()` is what
    makes the certificate describe what is actually persisted.

    Discriminator: a concurrent writer lands an extra ip_sans entry immediately after our SAN write.
    An implementation that passes its own `[*cfg.ip_sans, ip]` to issue_server_cert loses it; one
    that re-reads disk does not.
    """
    from lhpc.core import config as _config, webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "10.0.0.9")
    calls = _capture_certs(monkeypatch)
    svc = _svc_webserver_service(tmp_path)
    real_save = _config.save_webserver_config
    state = {"injected": False}

    def _save(paths, **kw):
        out = real_save(paths, **kw)
        if kw.get("ip_sans") and not state["injected"]:      # right after OUR san write
            state["injected"] = True
            real_save(paths, ip_sans=[*kw["ip_sans"], "172.16.0.5"])
        return out

    monkeypatch.setattr(_config, "save_webserver_config", _save)
    assert svc.webserver_expose(["192.168.0.0/24"], confirm=True).ok
    assert set(calls[-1]["ip_sans"]) == {"10.0.0.9", "172.16.0.5"}     # read from disk
    assert set(svc.config().webserver.ip_sans) == {"10.0.0.9", "172.16.0.5"}


def test_expose_is_a_no_op_when_the_ip_is_already_a_san(tmp_path, monkeypatch):
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.168.178.66")
    calls = _capture_certs(monkeypatch)
    svc = _svc_webserver_service(tmp_path)
    svc.webserver_configure(ip_sans=["192.168.178.66"])
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok and not calls                       # no pointless cert churn
    assert any("already an IP SAN" in d for d in res.details)


def test_expose_discloses_when_the_lan_ip_is_unknown(tmp_path, monkeypatch):
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "")   # loopback-only host / undeterminable
    calls = _capture_certs(monkeypatch)
    svc = _svc_webserver_service(tmp_path)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok and not calls
    assert svc.config().webserver.ip_sans == ()
    assert any("could not be determined" in d for d in res.details)


def test_expose_survives_an_uninitialized_pki(tmp_path, monkeypatch):
    # The exposure config is already persisted; a cert reissue failure must NOT fail it, and must
    # never be silent.
    from lhpc.core import pki as _pki, webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.168.178.66")
    def _boom(paths, **kw):
        raise _pki.PKIError("server TLS CA not initialized")
    monkeypatch.setattr(_pki, "issue_server_cert", _boom)
    svc = _svc_webserver_service(tmp_path)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok                                      # exposure stands
    assert svc.config().webserver.remote_exposed is True
    assert svc.config().webserver.ip_sans == ("192.168.178.66",)
    assert any("NOT reissued" in d for d in res.details)
    assert any("lhpc webserver init" in d for d in res.details)


def test_expose_gating(tmp_path):
    svc = _svc_webserver_service(tmp_path)
    assert not svc.webserver_expose([], confirm=True).ok                       # no CIDR
    assert not svc.webserver_expose(["192.168.0.0/24"]).ok                     # no confirm
    # public route needs elevated confirmation
    assert not svc.webserver_expose(["0.0.0.0/0"], confirm=True).ok
    assert svc.webserver_expose(["0.0.0.0/0"], confirm=True, confirm_public=True).ok
    assert svc.config().webserver.remote_exposed is True
    # no-auth remote also needs elevated confirmation
    svc.webserver_disable_remote()
    assert not svc.webserver_expose(["192.168.0.0/24"], access_mode="no-auth", confirm=True).ok
    assert svc.webserver_expose(["192.168.0.0/24"], access_mode="no-auth",
                                confirm=True, confirm_public=True).ok


def test_disable_remote_and_reset_preserve_pki(tmp_path):
    svc = _svc_webserver_service(tmp_path)
    svc.webserver_init(dns_sans=["pi.local"])
    svc.webserver_cert_issue("laptop", "pw")
    svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert svc.config().webserver.remote_exposed is True
    r = svc.webserver_reset_defaults()
    # Without a running nginx master (FakeSystem has no nginx) cessation cannot be proven, so
    # reset is truthfully NOT ok — but the DESIRED reset is applied and PKI preserved (below).
    assert not r.ok and "UNPROVEN" in r.summary
    cfg = svc.config().webserver
    assert cfg.remote_exposed is False and cfg.bind == "127.0.0.1" and cfg.allowed_cidrs == ()
    # PKI + client inventory preserved by reset
    assert pki.pki_status(svc._paths)["server_ca"]["present"]
    assert any(c["label"] == "laptop" for c in pki.list_client_certs(svc._paths))


def test_cert_lifecycle(tmp_path):
    svc = _svc_webserver_service(tmp_path)
    svc.webserver_init()
    issued = svc.webserver_cert_issue("tablet", "pw")
    assert issued.ok and issued.data["label"] == "tablet"
    assert any(c["label"] == "tablet" for c in svc.webserver_cert_list().data["certs"])
    rev = svc.webserver_cert_revoke("tablet")
    assert rev.ok and "RECORDED" in rev.summary
    assert svc.webserver_cert_discard_export("tablet").ok


def test_verify_uses_runner(tmp_path):
    svc0 = _svc_webserver_service(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    conf_path = str(svc0._paths.under(*webserver.NGINX_CONF_STAGED))
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", conf_path): CommandResult(0, "", "successful"),
    }, listeners=[Listener("ipv4", "127.0.0.1", 8443, 1)])   # live loopback console (exact scope)
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    r = svc.webserver_verify()
    assert r.ok and r.data["checks"]["nginx_config_valid"] == "ok"
    # cached-only monitor reflects it, never inferring active
    mon = svc.webserver_monitor().data
    assert mon["last_verified"] == r.data["checked_at"]
    assert mon["effective"]["remote_listener"] is False


def test_no_auth_elevation_offers_the_authenticated_alternative(tmp_path):
    """Refusing a no-auth remote listener must not leave the danger phrase as the only visible way
    forward: the operator usually wants the listener AUTHENTICATED (live-found — the documented
    proxy recipe hit this, and waiving it would have published meshtasticd's UI unauthenticated)."""
    plan = {"remote": True, "danger": "elevated", "public": False, "no_auth": True,
            "cleartext": False, "problems": []}
    miss = ControllerService._exposure_missing(plan, confirm=False, confirm_public=False,
                                               cidr_flag="--cidr <net>")
    assert any("enable-remote-danger" in m for m in miss)
    assert any("--auth local-open-remote-auth" in m for m in miss)

    public = {**plan, "no_auth": False, "public": True}
    miss = ControllerService._exposure_missing(public, confirm=False, confirm_public=False,
                                               cidr_flag="--cidr <net>")
    assert not any("--auth" in m for m in miss)      # a public range is not an auth problem


def test_allowed_gate_warning_survives_every_outcome(tmp_path, monkeypatch):
    """An ALLOWED gate can still warn (exposure reduced, firewall scripts not regenerated). That
    warning and its remedy must reach the operator on whatever the operation returns — success or
    failure — or they act on a stale apply script with no hint why (audit P2a)."""
    svc = _svc(tmp_path)
    warn = "exposure reduced, but the firewall scripts could not be regenerated (EACCES)"
    monkeypatch.setattr(ControllerService, "firewall_gate_activation",
                        lambda self, ports, hint="x": (True, warn, ["lhpc firewall --script"]))
    monkeypatch.setattr("lhpc.core.webserver.nginx_installed", lambda system: True)

    for inner in (ActionResult(True, "applied and reloaded"),
                  ActionResult(False, "nginx reload failed", next_commands=["lhpc webserver logs"])):
        monkeypatch.setattr(ControllerService, "_webserver_apply_after_gate",
                            lambda self, cfg, r=inner: r)
        res = svc.webserver_apply()
        assert res.ok is inner.ok                                  # verdict untouched
        assert warn in res.details                                 # warning carried
        assert "lhpc firewall --script" in res.next_commands       # remedy carried
        assert all(c in res.next_commands for c in inner.next_commands)   # originals kept

    monkeypatch.setattr(ControllerService, "firewall_gate_activation",
                        lambda self, ports, hint="x": (True, "", []))
    monkeypatch.setattr(ControllerService, "_webserver_apply_after_gate",
                        lambda self, cfg: ActionResult(True, "applied and reloaded"))
    res = svc.webserver_apply()
    assert res.ok and res.details == [] and res.next_commands == []   # silent gate stays silent


def test_verify_fails_when_the_console_is_not_serving(tmp_path):
    """`verify` validated config, nginx presence, PKI and `nginx -t` — but never that
    the thing nginx proxies to is alive. With the console unit failed and every page
    answering 502 it still reported "webserver verified": a live-probing command
    handing out a false green."""
    svc0 = _svc_webserver_corrections(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    staged = _staged_webserver_corrections(paths)
    unit = ("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service")
    base = {("nginx", "-v"): CR(0, "", ""), ("nginx", "-t", "-c", staged): CR(0, "", "ok")}

    dead = FakeSystem(commands={**base, unit: CR(3, "", "")})
    res = ControllerService(system=dead.system, paths=paths).webserver_verify()
    assert res.ok is False and "console_running" in res.summary
    assert res.data["checks"]["console_running"] == "failed"

    alive = FakeSystem(commands={**base, unit: CR(0, "", "")})
    res2 = ControllerService(system=alive.system, paths=paths).webserver_verify()
    assert res2.data["checks"]["console_running"] == "ok"


def test_a_cidr_set_covering_the_whole_internet_is_public_not_lan():
    # `0.0.0.0/1` + `128.0.0.0/1` IS the whole IPv4 internet, but neither entry is a default
    # route — so a per-entry `/0` test called it a restricted LAN, skipping the elevated
    # confirmation and letting it read as safely scoped. The SET has to be judged.
    from lhpc.core.webserver import cidr_set_is_public
    assert cidr_set_is_public(["0.0.0.0/1", "128.0.0.0/1"]) is True
    assert cidr_set_is_public(["::/1", "8000::/1"]) is True          # IPv6 complement
    assert cidr_set_is_public(["0.0.0.0/0"]) is True                 # still caught
    assert cidr_set_is_public(["10.42.0.0/24"]) is False
    assert cidr_set_is_public(["192.168.1.0/24", "10.0.0.0/8"]) is False
    assert cidr_set_is_public([]) is False
    # A family is judged on its OWN: all of IPv6 is public even beside a narrow IPv4 entry.
    assert cidr_set_is_public(["10.0.0.0/8", "::/0"]) is True


def test_whole_internet_cidr_set_demands_the_elevated_confirmation():
    # The policy consequence of the above: `danger` must elevate, as it does for 0.0.0.0/0.
    from lhpc.core.webserver import plan_exposure
    from lhpc.core.config import WebserverConfig
    import dataclasses as dc
    base = WebserverConfig()
    cfg = dc.replace(base, remote_exposed=True, bind="0.0.0.0", scheme="https",
                     access_mode="auth-everywhere",
                     allowed_cidrs=("0.0.0.0/1", "128.0.0.0/1"))
    plan = plan_exposure(cfg)
    assert plan["public"] is True and plan["danger"] == "elevated"
    lan = dc.replace(cfg, allowed_cidrs=("192.168.0.0/24",))
    assert plan_exposure(lan)["public"] is False


# ---- DESIRED vs APPLIED nginx policy ------------------------------------------------------
#
# Saving is not applying. Every field the console pill colours — bind, port, scheme, access mode,
# allowed CIDRs — is written by a Save and only reaches nginx at Apply, while the OLD listener
# keeps serving the OLD policy. Rendering the saved policy against that live listener turned
# "I intend to lock this down" into "it is locked down".

def _console_apply(tmp_path, *, port=8443, listen_port=None, **ws):
    """Apply an exposed console config and return the service, with the applied snapshot recorded.
    The live listener is 0.0.0.0:`listen_port or port`, so the apply's listener gate passes."""
    from lhpc.core import config as cfgmod
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    fields = {"access_mode": "no-auth", "allowed_cidrs": ["192.168.1.0/24"], "scheme": "https"}
    cfgmod.save_webserver_config(paths, bind="0.0.0.0", port=port, remote_exposed=True,
                                 **{**fields, **ws})
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(paths)): CommandResult(0, "", "successful"),
        ("nginx", "-s", "reload", "-c", _conf(paths)): CommandResult(0, "", ""),
    }, listeners=[Listener("ipv4", "0.0.0.0", listen_port or port, 1)])
    svc = ControllerService(system=fake.system, paths=paths)
    assert svc.webserver_apply().ok
    return svc


def _sec(svc):
    return svc.webserver_monitor().data["posture"]["sec_level"]


def test_apply_records_the_activated_policy_and_verify_never_advances_it(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _console_apply(tmp_path)
    applied = webserver.read_applied(svc._paths)["console"]
    assert applied["access_mode"] == "no-auth" and applied["port"] == 8443
    assert applied["scheme"] == "https" and applied["allowed_cidrs"] == ["192.168.1.0/24"]
    # Saving a stronger policy and VERIFYING it must not move the applied record: verify proves
    # the desired config is VALID, which says nothing about nginx having loaded it.
    cfgmod.save_webserver_config(svc._paths, access_mode="auth-everywhere")
    svc._invalidate_config()
    svc.webserver_verify()
    assert webserver.read_applied(svc._paths)["console"]["access_mode"] == "no-auth"


@pytest.mark.parametrize("field,value", [
    ("access_mode", "auth-everywhere"),          # stronger auth, not applied
    ("scheme", "http"),                          # (http over a live https listener)
    ("allowed_cidrs", ["192.168.1.5/32"]),       # narrower sources, not applied
    ("bind", "127.0.0.1"),                       # narrower bind, not applied
])
def test_a_saved_narrowing_cannot_improve_the_live_console(tmp_path, field, value):
    from lhpc.core import config as cfgmod
    svc = _console_apply(tmp_path)
    assert _sec(svc) == "bad"                    # applied: exposed + no-auth
    cfgmod.save_webserver_config(svc._paths, **{field: value})
    svc._invalidate_config()
    svc.webserver_verify()                       # even an explicit verify changes nothing
    assert _sec(svc) == "bad", f"saving {field} improved a listener nginx never rebound"


def test_saving_https_over_a_live_http_console_does_not_improve_it(tmp_path):
    # `http` cannot do client-cert auth, so a cleartext console is necessarily no-auth. Save the
    # WHOLE green policy at once (https + auth-everywhere): desired is now impeccable and the live
    # listener is still cleartext and unauthenticated, which is what the pill must say.
    from lhpc.core import config as cfgmod
    svc = _console_apply(tmp_path, scheme="http")
    assert _sec(svc) == "bad" and svc.webserver_monitor().data["posture"]["scheme"] == "http"
    cfgmod.save_webserver_config(svc._paths, scheme="https", access_mode="auth-everywhere")
    svc._invalidate_config()
    post = svc.webserver_monitor().data["posture"]
    assert post["sec_level"] == "bad"            # the live listener still speaks cleartext http
    assert post["scheme"] == "http"              # ...and the pill names the APPLIED scheme


def test_only_a_successful_apply_lets_the_console_posture_improve(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _console_apply(tmp_path)
    cfgmod.save_webserver_config(svc._paths, access_mode="auth-everywhere")
    svc._invalidate_config()
    assert _sec(svc) == "bad"
    assert svc.webserver_apply().ok               # NOW it is really loaded
    assert webserver.read_applied(svc._paths)["console"]["access_mode"] == "auth-everywhere"
    assert _sec(svc) == "ok"


def test_a_saved_port_move_does_not_lose_the_still_live_old_listener(tmp_path):
    # The worst shape of the same bug: after saving a new port the monitor probed ONLY the new
    # port, found nothing, and reported "absent" — the old exposed no-auth listener vanished from
    # the panel entirely instead of being represented as the exposure it still is.
    from lhpc.core import config as cfgmod
    svc = _console_apply(tmp_path, port=8443)
    cfgmod.save_webserver_config(svc._paths, port=8500, bind="127.0.0.1", remote_exposed=False)
    svc._invalidate_config()
    view = svc.webserver_monitor().data
    assert view["live_scope"] == "exposed"        # NOT "absent"
    assert view["live_port"] == 8443              # the port that is actually bound
    assert view["posture"]["sec_level"] == "bad"  # still an unauthenticated remote console


def test_a_live_exposed_console_with_no_applied_record_is_never_green(tmp_path):
    # Missing / old / malformed applied snapshot means the protecting policy is UNKNOWN. An
    # unknown policy behind a live exposed listener must fail closed, not inherit whatever the
    # desired config happens to say.
    from lhpc.core import config as cfgmod
    svc = _console_apply(tmp_path, access_mode="auth-everywhere")
    assert _sec(svc) == "ok"
    ev = webserver.read_evidence(svc._paths)
    webserver.write_evidence(svc._paths, {**{k: v for k, v in ev.items() if k != "schema"},
                                          "applied_snapshot": {"console": "not-a-dict"}})
    assert webserver.read_applied(svc._paths) == {}      # malformed => unknown
    assert _sec(svc) == "bad"
    cfgmod.save_webserver_config(svc._paths, allowed_cidrs=["192.168.1.0/24"])
    svc._invalidate_config()
    assert _sec(svc) == "bad"                            # desired cannot fill the gap


def test_a_live_loopback_console_is_still_local(tmp_path):
    # The containment rule must not turn every unapplied change red: a loopback socket cannot be
    # reached off-box whatever any policy says.
    from lhpc.core import config as cfgmod
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    fake = FakeSystem(listeners=[Listener("ipv4", "127.0.0.1", 8443, 1)])
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    assert svc.webserver_monitor().data["live_scope"] == "loopback"
    assert _sec(svc) == "ok"
    cfgmod.save_webserver_config(svc._paths, access_mode="no-auth")   # saved, not applied
    svc._invalidate_config()
    assert _sec(svc) == "ok"                     # loopback: still local, still green


# ===== copy-paste fetch commands for the trust material (Linux PC host) =========================

def test_ws_fetch_commands_build_real_paste_ready_scp_lines(tmp_path, monkeypatch):
    """The Certificates section offers scp commands for the server trust and each issued .p12,
    built from the viewer-reached host and the REAL runtime root/user — paste, don't edit. A
    shell-hostile Host header yields NO commands, and an on-disk export whose name fails the
    label rule is never echoed into a shell-bound string."""
    import getpass

    from lhpc.adapters.web import app as web_app
    user = getpass.getuser()

    d = tmp_path / "config" / "tls" / "exports"
    d.mkdir(parents=True)
    (d / "handy.p12").write_bytes(b"x")
    (d / "bad name!.p12").write_bytes(b"x")            # fails the label rule -> excluded

    out = web_app._ws_fetch_commands("10.42.0.1", str(tmp_path))
    assert out["ca"] == (f"scp {user}@10.42.0.1:{tmp_path}/config/tls/server-ca/ca.crt "
                         "lhpc-server-ca.crt")
    assert list(out["p12"]) == ["handy"]
    assert out["p12"]["handy"] == f"scp {user}@10.42.0.1:{tmp_path}/config/tls/exports/handy.p12 ."

    # Host allow-list: anything shell-hostile -> no commands at all.
    for bad in ("evil;rm -rf", "a b", "$(x)", ""):
        assert web_app._ws_fetch_commands(bad, str(tmp_path)) == {}
    # IPv6 literals are fine (bracketed by _url_host upstream).
    assert "ca" in web_app._ws_fetch_commands("[::1]", str(tmp_path))


def test_certificates_section_renders_the_fetch_boxes(tmp_path, monkeypatch):
    """Placement contract: the server-trust box sits below the PKI forms, the per-cert boxes
    below the Issue form — each below where the thing is created."""
    import pathlib

    tpl = pathlib.Path("lhpc/adapters/web/templates/_webserver.html").read_text()
    certs_at = tpl.index("===== Certificates")
    ca_at = tpl.index("ws-fetch-ca")
    p12_at = tpl.index("ws-fetch-p12-")
    assert certs_at < tpl.index("Renew server certificate") < ca_at < \
        tpl.index("Issue client cert", ca_at) < p12_at
    # Both use the shared cmdbox macro (copy button semantics come with it).
    assert tpl.count("cmdbox('ws-fetch-") == 2


def test_fetch_commands_gate_on_the_applied_policy(tmp_path, monkeypatch):
    """ROUND-3 REVIEW: the headline security gate had no test. A remote viewer sees the fetch
    commands ONLY when the APPLIED policy already enforces client certs — never in the
    saved-but-not-applied window, and never when the applied policy is unknown."""
    from lhpc.adapters.web.app import create_app
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "tls" / "exports").mkdir(parents=True)
    (tmp_path / "config" / "tls" / "exports" / "handy.p12").write_bytes(b"x")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    applied = {"mode": ""}

    # Patch the ONE source the gate must derive from — the real monitor then computes
    # `applied_access_mode` from it, so the whole real path is under test.
    from lhpc.core import webserver as wsmod
    monkeypatch.setattr(wsmod, "read_applied", lambda paths: (
        {"console": {"access_mode": applied["mode"], "port": 8443,
                     "bind": "127.0.0.1", "scheme": "https", "allowed_cidrs": [],
                     "public": False}, "proxies": []} if applied["mode"] else {}))
    c = create_app(lambda: svc).test_client()

    def page(remote):
        # remote-vs-loopback is decided by the nginx-set X-LHPC-Peer header, not the
        # socket address (see peer_is_loopback) — absent header = bare loopback mode.
        hdrs = {"X-LHPC-Peer": "remote"} if remote else {}
        return c.get("/stacks", headers=hdrs).get_data(as_text=True)

    # (probe: the per-cert .p12 box — the CA box is additionally gated on a PKI being
    # present, which this bare runtime does not have)
    # Remote + applied UNKNOWN (the saved-but-not-applied window; desired IS auth): withheld.
    assert "ws-fetch-p12-handy" not in page(remote=True)
    # Remote + applied open: withheld.
    applied["mode"] = "no-auth"
    assert "ws-fetch-p12-handy" not in page(remote=True)
    # Remote + applied cert-enforcing: shown (nginx already authenticated this viewer).
    applied["mode"] = "local-open-remote-auth"
    assert "ws-fetch-p12-handy" in page(remote=True)
    # Loopback: always shown, whatever is applied.
    applied["mode"] = ""
    assert "ws-fetch-p12-handy" in page(remote=False)

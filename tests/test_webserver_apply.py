"""M8: apply/reload orchestration — validate before activate, reload an already-running
LHPC-owned master (never systemctl/never start), typed repair-required when absent.
Plus M14 correction #10: the Webserver component is visible but stays out of generic
stack/component machinery."""

from __future__ import annotations

import os

from lhpc.core import webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem, Listener
from lhpc.core.services import ControllerService


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


# --- F3: a bind change (loopback <-> 0.0.0.0) needs a RESTART, not a reload ------------------

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


# --- the SAME F3 rule for STACK-PROXY listeners (live report: `webserver proxy meshcom --mode
# --- public` reloaded, nginx logged bind() 98 Address already in use, kept serving the OLD loopback
# --- listener on the proxy port, and apply reported success) ---------------------------------

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


# --- the claim verb (lhpc webserver --run-restart-service) ------------------------------------

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


# --- unified "Apply" (configure + apply in one action) + exposure gate + monitor live_scope ----------

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

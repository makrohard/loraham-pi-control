"""M8: apply/reload orchestration — validate before activate, reload an already-running
LHPC-owned master (never systemctl/never start), typed repair-required when absent.
Plus M14 correction #10: the Webserver component is visible but stays out of generic
stack/component machinery."""

from __future__ import annotations

import os
from pathlib import Path

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
    })
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
    from lhpc.core import webserver
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


def test_monitor_view_running_pill_is_nginx_or_lhpc_web(tmp_path):
    from lhpc.core.config import WebserverConfig
    p = Paths(runtime_root=tmp_path)
    # This session proxied through nginx -> green "nginx"; served directly by lhpc-web -> yellow.
    up = webserver.monitor_view(p, WebserverConfig(), live_listener_scope="loopback", served_via_nginx=True)
    assert up["posture"]["run"] == "nginx" and up["posture"]["run_level"] == "ok"
    down = webserver.monitor_view(p, WebserverConfig(), live_listener_scope="absent", served_via_nginx=False)
    assert down["posture"]["run"] == "lhpc-web" and down["posture"]["run_level"] == "warn"

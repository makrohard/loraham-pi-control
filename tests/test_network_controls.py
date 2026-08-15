"""The Network panel (Wi-Fi client mode with AP fallback): capability + authorization
gates, UUID-only addressing, PSK hygiene, the network-op lock + op_id pending record,
the detached finalize helper, the console-CIDR extension, prefer/forget/retry, the
watchdog tick, and the web flow (hidden-unless-supported + dedicated confirm)."""

from __future__ import annotations

import json
import re

from lhpc.core import deps as deps_mod
from lhpc.core import lifecycle as lcmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

NM = "/usr/bin/nmcli"
AP_ROW = "AP-UUID-1:lhpc-ap:802-11-wireless:yes:0\n"
CL_ROW = "CL-UUID-2:HomeNet:802-11-wireless:no:0\n"
PERMS_YES = ("org.freedesktop.NetworkManager.network-control:yes\n"
             "org.freedesktop.NetworkManager.settings.modify.system:yes\n")


def _svc(tmp_path, files=None, uptime="100.0 200.0\n"):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    base = {NM: "x", "/proc/uptime": uptime}
    return ControllerService(system=FakeSystem(files={**base, **(files or {})}).system,
                             paths=Paths(runtime_root=tmp_path))


def _fake_nmcli(svc, replies):
    """Substring-keyed nmcli fake; every call is recorded. Non-nmcli calls pass through."""
    orig = svc._system.runner.run
    calls: list = []

    def run(argv, timeout=None):
        calls.append(list(argv))
        if argv and argv[0] == "nmcli":
            key = " ".join(argv[1:])
            for k, (rc, out, err) in replies.items():
                if k in key:
                    class R:
                        pass
                    R.returncode, R.stdout, R.stderr = rc, out, err
                    return R
            class R0:
                returncode = 1
                stdout = ""
                stderr = "no fake nmcli reply"
            return R0
        return orig(argv, timeout)
    svc._system.runner.run = run
    return calls


def _std_replies(conns=AP_ROW + CL_ROW, active="AP-UUID-1:lhpc-ap:802-11-wireless:wlan0\n",
                 addr="10.42.0.1/24\n", perms=PERMS_YES):
    return {
        "UUID,NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show": (0, conns, ""),
        "connection show --active": (0, active, ""),
        "general permissions": (0, perms, ""),
        "IP4.ADDRESS device show": (0, addr, ""),
    }


def _pend(svc, op_id="tok1", boot="boot-1", uptime0=100.0, uuid="CL-UUID-2",
          allow_console=False, pwfile=""):
    svc._net_pending_path().write_text(json.dumps(
        {"op": "connect", "uuid": uuid, "ssid": "HomeNet", "boot_id": boot,
         "requested_uptime": uptime0, "op_id": op_id,
         "allow_console": allow_console, "pwfile": pwfile}))


# --- gates ----------------------------------------------------------------------------------------


def test_capability_gate_requires_the_ap_profile(tmp_path):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, _std_replies(conns=CL_ROW))          # no lhpc-ap -> desktop-ish box
    assert svc.network_supported() is False
    svc2 = _svc(tmp_path / "b")
    _fake_nmcli(svc2, _std_replies())
    assert svc2.network_supported() is True
    # nmcli absent -> unsupported before any probe
    svc3 = ControllerService(system=FakeSystem(files={"/proc/uptime": "1 2\n"}).system,
                             paths=Paths(runtime_root=tmp_path / "c"))
    (tmp_path / "c" / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    assert svc3.network_supported() is False


def test_authorization_verdict_cached_both_ways(tmp_path):
    svc = _svc(tmp_path)
    calls = _fake_nmcli(svc, _std_replies(perms=PERMS_YES.replace("system:yes",
                                                                  "system:auth")))
    assert svc._network_authorized() is False             # one action not yes
    n = sum(1 for c in calls if "permissions" in " ".join(c))
    assert svc._network_authorized() is False             # cached inside the TTL
    assert sum(1 for c in calls if "permissions" in " ".join(c)) == n
    import time as _t
    svc._net_auth_cache = (False, _t.monotonic() - 61.0)  # TTL passed -> re-probe
    svc._system.runner.run = None                          # would crash if not re-probing…
    _fake_nmcli(svc, _std_replies())                       # …now both yes
    assert svc._network_authorized() is True


def test_dependency_entry_only_on_ap_boxes(tmp_path):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, _std_replies())
    titles = [g["title"] for g in svc.controller_system_deps()]
    assert "Network controls" in titles
    d = next(g for g in svc.controller_system_deps()
             if g["title"] == "Network controls")["deps"][0]
    assert d["bootstrap"] is False and d["required"] is False
    assert "49-lhpc-network.rules" in d["install"]
    core, gui, gps = svc._declared_dep_scopes()
    assert not any("49-lhpc-network" in c for c in core + gui + gps)   # never in bootstrap
    svc2 = _svc(tmp_path / "b")
    _fake_nmcli(svc2, _std_replies(conns=CL_ROW))         # no lhpc-ap
    assert "Network controls" not in [g["title"] for g in svc2.controller_system_deps()]


def test_rule_helpers_and_single_install_site(tmp_path):
    txt = deps_mod.network_rule_text("makro")
    assert 'subject.user == "makro"' in txt
    for act in ("network-control", "settings.modify.system"):
        assert f"org.freedesktop.NetworkManager.{act}" in txt
    cmd = deps_mod.network_rule_install_cmd("makro")
    assert f"sudo install -D -m 0644 /dev/stdin {deps_mod.NETWORK_RULE_PATH}" in cmd
    svc = _svc(tmp_path)
    script = svc.deps_script()
    assert script.count("49-lhpc-network.rules <<NETWORKRULE") == 1
    import getpass
    assert getpass.getuser() not in script


# --- connect + PSK hygiene ------------------------------------------------------------------------


def test_scan_empty_in_ap_mode_explains_and_manual_form_renders(tmp_path, monkeypatch):
    """LIVE-FOUND: hosting the AP blinds the radio — a 0-result scan must say why, and the
    panel must offer manual SSID entry as the commissioning path."""
    from unittest.mock import patch
    svc = _svc(tmp_path)
    _fake_nmcli(svc, {**_std_replies(),
                      "802-11-wireless.ssid connection show": (0, "lhpc-e293\n", ""),
                      "device wifi list": (0, "lhpc-e293:0:WPA2\n", "")})
    res = svc.network_scan()
    assert res.ok and "scanning is limited" in res.summary
    from lhpc.adapters.web.app import create_app
    c = create_app(lambda: svc).test_client()
    with patch.object(ControllerService, "network_view", lambda self: dict(VIEW)), \
         patch.object(ControllerService, "network_supported", lambda self: True):
        body = c.get("/stacks").get_data(as_text=True)
        assert 'placeholder="network name (SSID)"' in body     # manual entry always there
        assert "Scan for networks" not in body                 # no dead button in AP mode
        client = {**VIEW, "mode": "client",
                  "active": {"uuid": "U1", "name": "HomeNet", "address": "10.0.0.2/24"}}
        with patch.object(ControllerService, "network_view", lambda self: dict(client)):
            body2 = c.get("/stacks").get_data(as_text=True)
            assert "Scan for networks" in body2                # client mode keeps the scan


def test_connect_dry_run_text(tmp_path):
    svc = _svc(tmp_path)
    res = svc.network_connect(ssid="HomeNet", psk="x", allow_console=True, apply=False)
    assert res.ok
    assert any("AP goes DOWN" in d for d in res.details)
    assert any(".local:8443" in d for d in res.details)
    off = svc.network_connect(ssid="HomeNet", psk="x", allow_console=False, apply=False)
    assert any("NOT allowed" in d and "SSH port 22" in d for d in off.details)


def test_connect_apply_uuid_addressing_and_psk_never_in_argv(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    calls = _fake_nmcli(svc, {**_std_replies(),
        "connection add": (0, "Connection 'HomeNet2' "
                              "(33333333-4444-5555-6666-777777777777) successfully added.\n",
                           "")})
    spawned = []
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn",
                        lambda self, argv, log, cwd=None, env=None:
                        (spawned.append(list(argv)) or 4242))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    res = svc.network_connect(ssid="HomeNet2", psk="s3cret!", allow_console=True,
                              apply=True)
    assert res.ok, res.summary
    # PSK hygiene: never in any recorded argv (nmcli or the spawned helper)
    assert not any("s3cret!" in " ".join(c) for c in calls)
    assert not any("s3cret!" in " ".join(a) for a in spawned)
    assert "s3cret!" not in res.summary + "".join(res.details)
    # the helper is addressed by UUID + op_id token, with the 0600 secrets file
    argv = spawned[0]
    assert "_network-finalize" in argv
    assert "33333333-4444-5555-6666-777777777777" in argv
    rec = json.loads(svc._net_pending_path().read_text())
    assert rec["uuid"] == "33333333-4444-5555-6666-777777777777"
    assert rec["op_id"] in argv
    pw = list((tmp_path / "state").glob("network-psk-*"))
    assert pw and (pw[0].stat().st_mode & 0o777) == 0o600
    assert pw[0].read_text() == "802-11-wireless-security.psk:s3cret!\n"
    # a second click is refused typed while the record is fresh
    res2 = svc.network_connect(ssid="X", psk="y", apply=True)
    assert not res2.ok and "in progress" in res2.summary
    # …and prefer/forget refuse too
    assert not svc.network_prefer("CL-UUID-2", True).ok
    assert not svc.network_forget("CL-UUID-2").ok


def test_connect_spawn_failure_cleans_record_and_secret(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, {**_std_replies(),
        "connection add": (0, "Connection 'N' "
                              "(33333333-4444-5555-6666-777777777777) successfully added.\n",
                           "")})
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn",
                        lambda self, argv, log, cwd=None, env=None:
                        (_ for _ in ()).throw(OSError("no fds")))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    res = svc.network_connect(ssid="N", psk="pw", apply=True)
    assert not res.ok and "could not be spawned" in res.summary
    assert not svc._net_pending_path().exists()
    assert not list((tmp_path / "state").glob("network-psk-*"))


# --- finalize helper ------------------------------------------------------------------------------


def _finalize_svc(tmp_path, *, up_rc=0):
    svc = _svc(tmp_path)
    order = []
    pw = tmp_path / "state" / "network-psk-tok1"

    def run(argv, timeout=None):
        if argv and argv[0] == "nmcli":
            key = " ".join(argv[1:])
            class R:
                returncode = 0
                stderr = ""
            if "connection up" in key:
                order.append(("up", "passwd-file" in key, pw.exists()))
                R.returncode = up_rc
                R.stdout = "activated\n"
                R.stderr = "" if up_rc == 0 else "no secrets"
            elif "--active" in key:
                R.stdout = "CL-UUID-2:HomeNet:802-11-wireless:wlan0\n"
            elif "IP4.ADDRESS" in key:
                R.stdout = "192.168.178.42/24\n"
            elif "UUID,NAME,TYPE" in key:
                R.stdout = AP_ROW + CL_ROW
            else:
                R.stdout = ""
            return R
        class R1:
            returncode = 127
            stdout = ""
            stderr = "x"
            not_found = True
        return R1
    svc._system.runner.run = run
    return svc, order, pw


def test_finalize_happy_path_unlinks_secret_after_up_and_cleans_record(tmp_path,
                                                                       monkeypatch):
    svc, order, pw = _finalize_svc(tmp_path)
    pw.write_text("802-11-wireless-security.psk:s3c\n")
    pw.chmod(0o600)
    _pend(svc, op_id="tok1", allow_console=True, pwfile=str(pw))
    ext = []
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    monkeypatch.setattr(ControllerService, "_network_extend_console",
                        lambda self, cidr, ip="", extra_dns=(): (ext.append(cidr)
                                            or ("pending",
                                                "sudo bash /x/firewall-apply.sh",
                                                "gated")))
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1",
                              pwfile="/tmp/BOGUS-ignored", allow_console=False,
                              delay=0.0)                   # argv values are IGNORED
    assert rc == 0
    assert order == [("up", True, True)]                  # record pwfile used during up
    assert not pw.exists()                                # …and is gone afterwards
    out = json.loads(svc._net_outcome_path().read_text())
    assert out["ok"] and out["cidr"] == "192.168.178.0/24"
    assert out["console"] == "pending" and "firewall-apply" in out["firewall_cmd"]
    assert ext == ["192.168.178.0/24"]
    assert not svc._net_pending_path().exists()           # finally-cleanup, no TTL wait


def test_finalize_activation_failure_still_unlinks_and_cleans(tmp_path, monkeypatch):
    svc, _order, pw = _finalize_svc(tmp_path, up_rc=1)
    pw.write_text("802-11-wireless-security.psk:s3c\n")
    _pend(svc, op_id="tok1", pwfile=str(pw))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", delay=0.0)
    assert rc == 1
    assert not pw.exists()                                # failure path unlinks too
    out = json.loads(svc._net_outcome_path().read_text())
    assert not out["ok"] and "activation failed" in out["error"]
    assert "s3c" not in json.dumps(out)                   # PSK never in state
    assert not svc._net_pending_path().exists()


def test_finalize_wrong_token_touches_nothing(tmp_path, monkeypatch):
    svc, _order, _pw = _finalize_svc(tmp_path)
    _pend(svc, op_id="tok1")
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="WRONG", delay=0.0)
    assert rc == 1
    assert svc._net_pending_path().exists()               # not its record — kept
    assert not svc._net_outcome_path().exists()           # and no outcome written


def test_helper_budget_strictly_below_record_ttl():
    assert ControllerService.NET_HELPER_BUDGET_S < ControllerService.NET_PENDING_TTL_S


def test_pending_record_fail_closed_validation(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    # malformed: refuses and is KEPT
    svc._net_pending_path().write_text("{not json")
    blocked = svc._net_pending_blocked()
    assert blocked is not None and "unreadable" in blocked[0]
    assert svc._net_pending_path().exists()
    svc._net_pending_path().unlink()
    # other boot: pruned
    _pend(svc, boot="other-boot")
    assert svc._net_pending_blocked() is None
    assert not svc._net_pending_path().exists()
    # unreadable current boot id: refuses and is kept
    _pend(svc)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "")
    blocked = svc._net_pending_blocked()
    assert blocked is not None and svc._net_pending_path().exists()


# --- console extension ----------------------------------------------------------------------------


def test_extend_console_unions_under_real_lock_and_applies_outside(tmp_path, monkeypatch):
    """Runs with the REAL config-lock machinery (no fakes): the union save must not
    self-contend, the saved list must be the union, and the apply happens after."""
    from lhpc.core import config as _config
    svc = _svc(tmp_path)
    _config.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True,
                                  allowed_cidrs=["10.42.0.0/24"],
                                  access_mode="local-open-remote-auth")
    applied = []

    def fake_apply(self):
        from lhpc.core.services import ActionResult
        applied.append(True)
        return ActionResult(False, "gated", next_commands=["sudo bash /rt/firewall-apply.sh"])
    monkeypatch.setattr(ControllerService, "webserver_apply", fake_apply)
    state, cmd, _msg = svc._network_extend_console("192.168.178.0/24")
    assert state == "pending" and "firewall-apply" in cmd
    assert applied == [True]
    cfg = _config.load_config(svc._paths).webserver
    assert list(cfg.allowed_cidrs) == ["10.42.0.0/24", "192.168.178.0/24"]


def test_extend_console_refuses_no_auth_without_elevation(tmp_path, monkeypatch):
    from lhpc.core import config as _config
    svc = _svc(tmp_path)
    _config.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True,
                                  allowed_cidrs=["10.42.0.0/24"], access_mode="no-auth")
    called = []
    monkeypatch.setattr(ControllerService, "webserver_apply",
                        lambda self: called.append(True))
    state, _cmd, _msg = svc._network_extend_console("192.168.178.0/24")
    assert state == "refused" and not called              # refusal, nothing applied
    cfg = _config.load_config(svc._paths).webserver
    assert "192.168.178.0/24" not in cfg.allowed_cidrs    # nothing saved either


# --- prefer / forget / watchdog -------------------------------------------------------------------


def test_prefer_sets_one_and_clears_others(tmp_path):
    svc = _svc(tmp_path)
    two = (AP_ROW + CL_ROW
           + "CL-UUID-3:CafeNet:802-11-wireless:yes:10\n")   # stale second autoconnect
    calls = _fake_nmcli(svc, {**_std_replies(conns=two),
                              "connection modify": (0, "", "")})
    res = svc.network_prefer("CL-UUID-2", True)
    assert res.ok, res.summary
    mods = [" ".join(c) for c in calls if "connection modify" in " ".join(c)]
    assert any("CL-UUID-2" in m and "autoconnect yes" in m and
               "autoconnect-priority 10" in m for m in mods)
    assert any("CL-UUID-3" in m and "autoconnect no" in m for m in mods)
    assert not any("AP-UUID-1" in m for m in mods)         # the AP profile is never touched
    assert svc._net_preferred()["uuid"] == "CL-UUID-2"


def test_forget_refuses_the_ap_profile(tmp_path):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, _std_replies())
    res = svc.network_forget("AP-UUID-1")
    assert not res.ok and "never removable" in res.summary


def test_watchdog_tick_retries_preferred_on_ap_and_respects_interval(tmp_path,
                                                                     monkeypatch):
    svc = _svc(tmp_path)
    calls = _fake_nmcli(svc, {**_std_replies(),
                              "connection up": (0, "activated\n", ""),
                              "connection modify": (0, "", "")})
    svc._net_preferred_path().write_text(json.dumps({"uuid": "CL-UUID-2",
                                                     "ssid": "HomeNet"}))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    ok, msg = svc._network_watch_tick()                    # first attempt: interval empty
    assert ok and "reconnected" in msg
    ups = [c for c in calls if "up" in c and "CL-UUID-2" in c]
    assert len(ups) == 1
    ok, msg = svc._network_watch_tick()                    # inside the interval: no attempt
    assert ok and "interval" in msg
    assert len([c for c in calls if "up" in c and "CL-UUID-2" in c]) == 1
    assert svc.network_retry_now().ok                      # force ignores the interval
    assert len([c for c in calls if "up" in c and "CL-UUID-2" in c]) == 2


def test_watchdog_reads_preference_fresh_and_skips_without_one(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, {**_std_replies(), "connection modify": (0, "", "")})
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    ok, msg = svc._network_watch_tick()
    assert ok and "no preferred network" in msg
    # a preference set AFTER the worker started is picked up on the next tick
    svc._net_preferred_path().write_text(json.dumps({"uuid": "CL-UUID-2",
                                                     "ssid": "HomeNet"}))
    _fake_nmcli(svc, {**_std_replies(), "connection modify": (0, "", ""),
                      "connection up": (0, "ok\n", "")})
    ok, msg = svc._network_watch_tick()
    assert ok and "reconnected" in msg


def test_watchdog_skips_while_pending_and_completes_console_apply(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, _std_replies())
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    _pend(svc)                                             # fresh foreign record
    ok, msg = svc._network_watch_tick()
    assert not ok and "skipped" in msg
    svc._net_pending_path().unlink()
    # pending console outcome + firewall now ok -> the tick finishes the apply
    svc._net_outcome_path().write_text(json.dumps(
        {"ok": True, "uuid": "U", "console": "pending",
         "firewall_cmd": "sudo bash x"}))
    monkeypatch.setattr(ControllerService, "firewall_status",
                        lambda self: {"config_ok": True, "live_ok": True})
    done = []

    def fake_apply(self):
        from lhpc.core.services import ActionResult
        done.append(True)
        return ActionResult(True, "applied")
    monkeypatch.setattr(ControllerService, "webserver_apply", fake_apply)
    svc._network_watch_tick()
    assert done == [True]
    out = json.loads(svc._net_outcome_path().read_text())
    assert out["console"] == "applied" and "firewall_cmd" not in out


def test_service_construction_spawns_no_threads(tmp_path):
    import threading
    before = {t.name for t in threading.enumerate()}
    _svc(tmp_path)
    after = {t.name for t in threading.enumerate()}
    assert before == after


# --- web ------------------------------------------------------------------------------------------


VIEW = {"supported": True, "authorized": True, "mode": "ap", "active": {},
        "stored": [{"uuid": "U1", "name": "HomeNet", "type": "802-11-wireless",
                    "autoconnect": False, "priority": "0", "preferred": False}],
        "preferred": {}, "hostname": "lhpc-e293", "outcome": {},
        "scan": [{"ssid": "Other", "signal": 60, "security": "WPA2"}], "pending": False}


def _web(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    return svc, create_app(lambda: svc).test_client()


def _tok(c):
    body = c.get("/stacks").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_web_hidden_and_404_when_unsupported(tmp_path):
    _, c = _web(tmp_path)
    body = c.get("/stacks").get_data(as_text=True)
    assert "controller-network" not in body
    assert c.post("/network/scan", data={"_csrf": _tok(c)}).status_code == 404
    assert c.post("/network/scan").status_code == 400      # CSRF first


def test_web_panel_confirm_and_apply(tmp_path, monkeypatch):
    from unittest.mock import patch
    _, c = _web(tmp_path)
    tok = _tok(c)
    with patch.object(ControllerService, "network_view", lambda self: dict(VIEW)), \
         patch.object(ControllerService, "network_supported", lambda self: True):
        body = c.get("/stacks").get_data(as_text=True)
        for probe in ("controller-network", "HomeNet", "Reconnect",
                      "allow console from that network", "lhpc-e293.local"):
            assert probe in body, probe
        # unknown op 404s even when supported
        assert c.post("/network/nope", data={"_csrf": tok}).status_code == 404
        # connect stage 1: dedicated confirm page, posting back to the connect route,
        # PSK present only as a password-typed value (never cleartext)
        r = c.post("/network/connect", data={"_csrf": tok, "ssid": "Other",
                                             "psk": "pw123", "allow_console": "1"})
        page = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "Join Wi-Fi network" in page
        assert 'action="/network/connect"' in page
        assert ">pw123<" not in page
        # stage 2 -> service spy with the exact kwargs
        seen = {}

        def fake_connect(self, **kw):
            from lhpc.core.services import ActionResult
            seen.update(kw)
            return ActionResult(True, "joining")
        with patch.object(ControllerService, "network_connect", fake_connect):
            r2 = c.post("/network/connect", data={"_csrf": tok, "confirmed": "yes",
                                                  "ssid": "Other", "psk": "pw123",
                                                  "allow_console": "1"})
        assert r2.status_code in (302, 303)
        assert seen["ssid"] == "Other" and seen["apply"] is True
        assert seen["allow_console"] is True and seen["psk"] == "pw123"


# --- CRL clock-jump heal --------------------------------------------------------------------------


def _write_crl(svc, next_update_delta_days: int):
    """A real, parseable CRL with nextUpdate now+delta (negative = expired)."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")])
    now = datetime.datetime.now(datetime.UTC)
    crl = (x509.CertificateRevocationListBuilder()
           .issuer_name(name)
           .last_update(now - datetime.timedelta(days=30))
           .next_update(now + datetime.timedelta(days=next_update_delta_days))
           .sign(key, hashes.SHA256()))
    from cryptography.hazmat.primitives.serialization import Encoding
    p = svc._paths.under("config", "tls", "client-ca", "crl.pem")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(crl.public_bytes(Encoding.PEM))


def test_crl_refresh_only_when_expired(tmp_path, monkeypatch):
    """LIVE-FOUND: the box's first NTP sync (arriving with the joined WLAN) jumped the clock
    past the CRL's nextUpdate and nginx rejected EVERY client cert. The heal rebuilds the
    CRL and reloads nginx — and never touches a still-valid one."""
    from lhpc.core import pki as pki_mod
    svc = _svc(tmp_path)
    from lhpc.core.services import ActionResult
    rebuilt, reloaded = [], []
    apply_ok = {"v": True}

    def fake_apply(self):
        reloaded.append(True)
        return ActionResult(apply_ok["v"], "x")
    monkeypatch.setattr(pki_mod, "build_crl", lambda paths: rebuilt.append(True))
    monkeypatch.setattr(ControllerService, "webserver_apply", fake_apply)
    assert svc.crl_refresh_if_expired() is False           # no CRL at all -> no-op
    _write_crl(svc, +30)
    assert svc.crl_refresh_if_expired() is False           # valid -> untouched
    assert not rebuilt and not reloaded
    _write_crl(svc, -7)
    apply_ok["v"] = False                                  # rebuild but reload FAILS
    assert svc.crl_refresh_if_expired() is True
    assert rebuilt == [True] and reloaded == [True]
    marker = svc._paths.under("state", "crl-reload-pending")
    assert marker.exists()                                 # AUDIT: retry marker persisted
    _write_crl(svc, +30)                                   # file is fresh again...
    apply_ok["v"] = True
    assert svc.crl_refresh_if_expired() is True            # ...but the marker retries
    assert len(reloaded) == 2 and not marker.exists()      # cleared only on success


def test_watchdog_tick_heals_expired_crl(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _fake_nmcli(svc, {**_std_replies(), "connection modify": (0, "", "")})
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    healed = []
    monkeypatch.setattr(ControllerService, "crl_refresh_if_expired",
                        lambda self: healed.append(True) or True)
    svc._network_watch_tick()
    assert healed == [True]


# --- back to AP mode ------------------------------------------------------------------------------


def test_ap_now_clears_preference_and_spawns_finalize(tmp_path, monkeypatch):
    """Operator ruling: client mode needs a way home. The switch clears the preferred flag
    (else the watchdog would yank the box back) and rides the SAME finalize helper."""
    svc = _svc(tmp_path)
    calls = _fake_nmcli(svc, {**_std_replies(
        conns=AP_ROW + "CL-UUID-2:HomeNet:802-11-wireless:yes:10\n",
        active="CL-UUID-2:HomeNet:802-11-wireless:wlan0\n"),
        "connection modify": (0, "", "")})
    svc._net_preferred_path().write_text(json.dumps({"uuid": "CL-UUID-2",
                                                     "ssid": "HomeNet"}))
    # dry-run names the AP and the cleared preference
    dr = svc.network_ap_now(apply=False)
    assert dr.ok and any("10.42.0.1:8443" in d for d in dr.details)
    assert any("CLEARED" in d for d in dr.details)
    spawned = []
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn",
                        lambda self, argv, log, cwd=None, env=None:
                        (spawned.append(list(argv)) or 4242))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    res = svc.network_ap_now(apply=True)
    assert res.ok, res.summary
    assert not svc._net_preferred_path().exists()          # preference cleared
    mods = [" ".join(c) for c in calls if "connection modify" in " ".join(c)]
    assert any("CL-UUID-2" in m and "autoconnect no" in m for m in mods)
    argv = spawned[0]
    assert "_network-finalize" in argv and "AP-UUID-1" in argv   # the AP profile's uuid
    rec = json.loads(svc._net_pending_path().read_text())
    assert rec["op"] == "ap" and rec["uuid"] == "AP-UUID-1"


def test_web_ap_button_and_confirm(tmp_path, monkeypatch):
    from unittest.mock import patch
    _, c = _web(tmp_path)
    tok = _tok(c)
    client_view = {**VIEW, "mode": "client",
                   "active": {"uuid": "U1", "name": "HomeNet",
                              "address": "192.168.178.106/24"}}
    with patch.object(ControllerService, "network_view", lambda self: dict(client_view)), \
         patch.object(ControllerService, "network_supported", lambda self: True):
        body = c.get("/stacks").get_data(as_text=True)
        assert 'action="/network/ap"' in body               # button in client mode
        # ...but NOT in AP mode
        with patch.object(ControllerService, "network_view", lambda self: dict(VIEW)):
            assert 'action="/network/ap"' not in c.get("/stacks").get_data(as_text=True)
        # stage 1 -> confirm; stage 2 -> service spy
        r = c.post("/network/ap", data={"_csrf": tok})
        page = r.get_data(as_text=True)
        assert r.status_code == 200 and "Switch back to AP mode?" in page
        assert 'action="/network/ap"' in page
        seen = []

        def fake(self, apply=False):
            from lhpc.core.services import ActionResult
            seen.append(apply)
            return ActionResult(True, "switching")
        with patch.object(ControllerService, "network_ap_now", fake):
            r2 = c.post("/network/ap", data={"_csrf": tok, "confirmed": "yes"})
        assert r2.status_code in (302, 303) and seen == [True]


# --- SANs + router-DNS discovery ------------------------------------------------------------------


def test_finalize_derives_fqdn_and_extends_sans(tmp_path, monkeypatch):
    """LIVE-FOUND: a phone reaching the box by LAN IP / router name got a cert with no
    matching SAN. The finalize learns the router's DHCP domain, records the phone-friendly
    fqdn in the outcome, and hands address + names into the console extension as SANs."""
    import socket as _socket
    svc, _order, _pw = _finalize_svc(tmp_path)
    orig_run = svc._system.runner.run

    def run(argv, timeout=None):
        if argv and argv[0] == "nmcli" and "DHCP4.OPTION" in " ".join(argv):
            class R:
                returncode = 0
                stdout = ("requested_domain_search = yes\n"
                          "domain_name = fritz.box\n")
                stderr = ""
            return R
        return orig_run(argv, timeout)
    svc._system.runner.run = run
    _pend(svc, op_id="tok1", allow_console=True)
    seen = {}
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    monkeypatch.setattr(ControllerService, "_network_extend_console",
                        lambda self, cidr, ip="", extra_dns=():
                        (seen.update(cidr=cidr, ip=ip, dns=list(extra_dns))
                         or ("applied", "", "ok")))
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", allow_console=True,
                              delay=0.0)
    assert rc == 0
    host = _socket.gethostname()
    out = json.loads(svc._net_outcome_path().read_text())
    assert out["fqdn"] == f"{host}.fritz.box"
    assert seen["ip"] == "192.168.178.42"
    assert f"{host}.local" in seen["dns"] and f"{host}.fritz.box" in seen["dns"]


def test_extend_console_saves_sans_and_reissues(tmp_path, monkeypatch):
    from lhpc.core import config as _config
    from lhpc.core import pki as pki_mod
    svc = _svc(tmp_path)
    _config.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True,
                                  allowed_cidrs=["10.42.0.0/24"],
                                  access_mode="local-open-remote-auth")
    issued = {}
    monkeypatch.setattr(pki_mod, "issue_server_cert",
                        lambda paths, dns_sans, ip_sans, days: issued.update(
                            dns=list(dns_sans), ip=list(ip_sans)))
    monkeypatch.setattr(ControllerService, "webserver_apply", lambda self: __import__(
        "lhpc.core.services", fromlist=["ActionResult"]).ActionResult(True, "applied"))
    # an enabled remote STACK PROXY must be extended too (live-found: 8444 answered 403)
    _config.save_stackweb_config(svc._paths, "meshcom", mode="lan", port=8444,
                                 access_mode="local-open-remote-auth",
                                 allowed_cidrs=["10.42.0.0/24"])
    state, _cmd, _msg = svc._network_extend_console("192.168.178.0/24",
                                                    ip="192.168.178.42",
                                                    extra_dns=["h", "h.local",
                                                               "h.fritz.box"])
    assert state == "applied"
    cfg = _config.load_config(svc._paths).webserver
    assert "192.168.178.42" in cfg.ip_sans
    assert "h.fritz.box" in cfg.dns_sans and "h.local" in cfg.dns_sans
    assert issued["ip"] and "192.168.178.42" in issued["ip"]     # cert reissued with them
    swc = _config.load_config(svc._paths).stackweb.get("meshcom")
    assert swc is not None and swc.enabled and swc.remote        # fixture really is exposable
    assert "192.168.178.0/24" in swc.allowed_cidrs               # proxy allowlist extended
    assert "10.42.0.0/24" in swc.allowed_cidrs                   # existing scope kept


# --- audit round: parser, preflight, finalize gate, retry stamp -----------------------------------


def test_nm_split_handles_escaped_colons():
    """AUDIT: NM escapes literal colons as \\: in terse output — names/SSIDs with colons
    must survive parsing."""
    s = ControllerService._nm_split
    assert s("UUID-1:Cafe\\: Lounge:802-11-wireless:yes:10") == \
        ["UUID-1", "Cafe: Lounge", "802-11-wireless", "yes", "10"]
    assert s("plain:no-escape") == ["plain", "no-escape"]
    assert s("tail\\\\:x") == ["tail\\", "x"]


def test_connect_refuses_while_old_firewall_rules_loaded(tmp_path, monkeypatch):
    """AUDIT (lockout): an existing install still running the old AP-scoped nft rules must
    NOT drop its AP — the join is refused with the migration command until the one-time
    firewall apply ran. Checkbox-off joins skip the gate (SSH-only accepted)."""
    svc = _svc(tmp_path)
    _fake_nmcli(svc, _std_replies())
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    monkeypatch.setattr(ControllerService, "firewall_gate_activation",
                        lambda self, ports, action_hint="":
                        (False, "firewall pending", ["sudo bash /rt/firewall-apply.sh"]))
    res = svc.network_connect(ssid="HomeNet2", psk="x", allow_console=True, apply=True)
    assert not res.ok and "firewall" in res.summary
    assert any("firewall-apply" in c for c in res.next_commands)
    assert not svc._net_pending_path().exists()            # AP untouched, nothing armed
    # checkbox off -> the gate is skipped (operator accepted SSH-only)
    spawned = []
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn",
                        lambda self, argv, log, cwd=None, env=None:
                        (spawned.append(argv) or 4242))
    _fake_nmcli(svc, {**_std_replies(),
        "connection add": (0, "Connection 'X' "
                              "(33333333-4444-5555-6666-777777777777) successfully added.\n",
                           "")})
    res2 = svc.network_connect(ssid="X", psk="x", allow_console=False, apply=True)
    assert res2.ok and spawned


def test_finalize_requires_a_matching_record(tmp_path, monkeypatch):
    """AUDIT (fail-open): NO record, or a record for a DIFFERENT uuid, must refuse — and
    the record's own allow_console is authoritative over the argv."""
    svc, _order, _pw = _finalize_svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    # no record at all -> refuse, nothing activated, no outcome
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", delay=0.0)
    assert rc == 1 and not svc._net_outcome_path().exists()
    # record for ANOTHER uuid with the same token -> refuse
    _pend(svc, op_id="tok1", uuid="OTHER-UUID")
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", delay=0.0)
    assert rc == 1 and svc._net_pending_path().exists()
    svc._net_pending_path().unlink()
    # record says allow_console FALSE -> the argv's --allow-console is ignored
    _pend(svc, op_id="tok1", allow_console=False)
    called = []
    monkeypatch.setattr(ControllerService, "_network_extend_console",
                        lambda self, cidr, ip="", extra_dns=():
                        (called.append(True) or ("applied", "", "x")))
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", allow_console=True,
                              delay=0.0)
    assert rc == 0 and not called                          # record wins: console off


def test_retry_stamp_is_boot_and_uuid_bound(tmp_path, monkeypatch):
    """AUDIT: a stamp from the previous boot (large uptime) must not postpone the retry —
    boot mismatch, uuid mismatch, or a negative delta all mean 'attempt now'."""
    svc = _svc(tmp_path)                                   # uptime 100s this boot
    _fake_nmcli(svc, {**_std_replies(),
                      "connection up": (0, "ok\n", ""),
                      "connection modify": (0, "", "")})
    svc._net_preferred_path().write_text(json.dumps({"uuid": "CL-UUID-2",
                                                     "ssid": "HomeNet"}))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-2")
    # previous boot's stamp: huge uptime, old boot id
    svc._net_retry_path().write_text(json.dumps(
        {"attempt_uptime": 500000.0, "boot_id": "boot-1", "uuid": "CL-UUID-2"}))
    ok, msg = svc._network_watch_tick()
    assert ok and "reconnected" in msg                     # attempted despite the stamp
    rec = json.loads(svc._net_retry_path().read_text())
    assert rec["boot_id"] == "boot-2" and rec["uuid"] == "CL-UUID-2"


def test_finalize_refuses_stale_and_other_boot_records_even_with_token(tmp_path,
                                                                       monkeypatch):
    """RE-AUDIT: a revived helper past the TTL, or one from another boot, must never act
    even with a matching token — freshness is checked in finalize itself (never via the
    pruning gate), and the record is left alone."""
    svc, _order, _pw = _finalize_svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    # stale same-boot: uptime 100 now, requested at -200 => age 300 > TTL 180
    _pend(svc, op_id="tok1", uptime0=-200.0)
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", delay=0.0)
    assert rc == 1
    assert svc._net_pending_path().exists()                # untouched
    assert not svc._net_outcome_path().exists()
    # other boot, matching token
    _pend(svc, op_id="tok1", boot="other-boot")
    rc = svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", delay=0.0)
    assert rc == 1 and not svc._net_outcome_path().exists()


def test_connect_preflight_exception_fails_closed(tmp_path, monkeypatch):
    """RE-AUDIT: an unverifiable firewall refuses the join — never allowed=True."""
    svc = _svc(tmp_path)
    _fake_nmcli(svc, _std_replies())
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")

    def boom(self, ports, action_hint=""):
        raise RuntimeError("receipt unreadable")
    monkeypatch.setattr(ControllerService, "firewall_gate_activation", boom)
    res = svc.network_connect(ssid="HomeNet2", psk="x", allow_console=True, apply=True)
    assert not res.ok and any("unverifiable" in d for d in res.details)
    assert not svc._net_pending_path().exists()


def test_finalize_refuses_malformed_record_shape(tmp_path, monkeypatch):
    """RE-AUDIT: the record is the sole authority, so its shape is strict — truthy
    non-bool allow_console, a non-canonical pwfile path, an unknown op, and an ap record
    claiming console/secret are all refused without acting."""
    svc, order, _pw = _finalize_svc(tmp_path)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")

    def rec(**over):
        base = {"op": "connect", "uuid": "CL-UUID-2", "ssid": "HomeNet",
                "boot_id": "boot-1", "requested_uptime": 100.0, "op_id": "tok1",
                "allow_console": False, "pwfile": ""}
        base.update(over)
        svc._net_pending_path().write_text(json.dumps(base))

    for bad in ({"allow_console": "false"},              # bool("false") is True
                {"pwfile": "/etc/shadow"},               # record-supplied path
                {"op": "bogus"},
                {"op": "ap", "allow_console": True},
                {"op": "ap", "pwfile": str(tmp_path / "state" / "network-psk-tok1")}):
        rec(**bad)
        assert svc.network_finalize(uuid="CL-UUID-2", op_id="tok1", delay=0.0) == 1, bad
        assert order == [] and not svc._net_outcome_path().exists(), bad

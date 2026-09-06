"""Test-lab mode: the two-key activation latch, the ControllerService injection seam
(production byte-path when off; explicit injection always wins), the LabRunner's
deny/simulate/passthrough dispatch, scenario-driven simulators, the LabFs overlay, the
spawn guard, and the simulated boot identity."""
from __future__ import annotations

import json

import lhpc_testlab as testlab
import pytest
from labsupport import make_lab_root
from lhpc_testlab import nm, ops, provider, rules, scenarios, supervisor
from lhpc_testlab.system import LabRunner, build_lab_system

from lhpc.core import lifecycle as lcmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, RealCommandRunner
from lhpc.core.services import ControllerService

# --- activation latch -------------------------------------------------------------------


def test_latch_needs_both_keys(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    monkeypatch.delenv("LHPC_TESTLAB", raising=False)
    assert testlab.active(paths) is False
    # marker alone (production process pointed at a lab root) -> off
    (tmp_path / "state" / "testlab").mkdir(parents=True)
    (tmp_path / "state" / "testlab" / "enabled").write_text("lab\n")
    assert testlab.active(paths) is False
    # env alone (operator intent against a production root) -> off
    monkeypatch.setenv("LHPC_TESTLAB", "1")
    other = Paths(runtime_root=tmp_path / "prod")
    assert testlab.active(other) is False
    # both -> on
    assert testlab.active(paths) is True


def test_service_off_path_is_production(tmp_path, monkeypatch):
    monkeypatch.delenv("LHPC_TESTLAB", raising=False)
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    assert type(svc._system.runner) is RealCommandRunner
    assert svc._ext is None and svc._manifest_path is None


def test_service_lab_path_and_explicit_injection_wins(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    svc = ControllerService(paths=paths)
    assert type(svc._system.runner) is LabRunner and svc._ext is not None
    # explicit system injection (every existing test) bypasses the probe entirely
    svc2 = ControllerService(system=FakeSystem().system, paths=paths)
    assert type(svc2._system.runner) is FakeSystem and svc2._ext is None


def test_manifest_overlay_used_only_when_present(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    assert ControllerService(paths=paths)._manifest_path is None
    overlay = tmp_path / "state" / "testlab" / "manifest.toml"
    overlay.write_text("# lab overlay\n")
    assert ControllerService(paths=paths)._manifest_path == overlay


# --- rules / runner dispatch --------------------------------------------------------------


def test_classify_deny_simulate_pass():
    assert rules.classify(["sudo", "true"])[0] == "deny"
    assert rules.classify(["/usr/sbin/nft", "-f", "x"])[0] == "deny"
    assert rules.classify(["dpkg", "-i", "x.deb"])[0] == "deny"
    assert rules.classify(["dpkg-query", "-W", "x"])[0] == "pass"
    assert rules.classify(["nmcli", "general"])[0] == "simulate"
    assert rules.classify(["busctl", "call"])[0] == "simulate"
    assert rules.classify(["systemctl", "--user", "show", "u"])[0] == "simulate"
    assert rules.classify(["gcc", "-o", "x", "x.c"])[0] == "pass"


def test_runner_denies_and_logs(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    runner = build_lab_system(paths).runner
    r = runner.run(["sudo", "reboot"], 5.0)
    assert r.returncode == 1 and "refused host-mutating" in r.stderr
    assert "DENIED" in (tmp_path / "state" / "testlab" / "events.log").read_text()


def test_runner_passthrough_runs_and_audits(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    runner = build_lab_system(paths).runner
    r = runner.run(["echo", "lab"], 5.0)
    assert r.returncode == 0 and r.stdout.strip() == "lab"
    assert "echo lab" in (tmp_path / "state" / "testlab" / "commands.log").read_text()


# --- simulators -----------------------------------------------------------------------


def test_nmcli_sim_profiles_scan_and_join(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    runner = build_lab_system(paths).runner
    out = runner.run(["nmcli", "-t", "-f",
                      "UUID,NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY",
                      "connection", "show"], 5.0)
    assert out.returncode == 0 and "lhpc-ap:802-11-wireless:yes:0" in out.stdout
    # healthy scenario: a client profile is active
    act = runner.run(["nmcli", "-t", "-f", "UUID,NAME,TYPE,DEVICE",
                      "connection", "show", "--active"], 5.0)
    assert "LabNet" in act.stdout
    # join flow: add creates a profile whose UUID rides the reply; up with the secrets
    # file present succeeds and persists the secret (NM keyfile semantics)
    add = runner.run(["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
                      "con-name", "CoffeeShop", "ssid", "CoffeeShop",
                      "connection.autoconnect", "no",
                      "connection.autoconnect-priority", "0",
                      "802-11-wireless-security.key-mgmt", "wpa-psk"], 5.0)
    assert add.returncode == 0
    uid = add.stdout.split("(")[1].split(")")[0]
    up_nosecret = runner.run(["nmcli", "connection", "up", uid, "ifname", "wlan0"], 5.0)
    assert up_nosecret.returncode == 4 and "Secrets" in up_nosecret.stderr
    pw = tmp_path / "pw"
    pw.write_text("802-11-wireless-security.psk:s3c\n")
    up = runner.run(["nmcli", "connection", "up", uid, "ifname", "wlan0",
                     "passwd-file", str(pw)], 5.0)
    assert up.returncode == 0
    again = runner.run(["nmcli", "connection", "up", uid, "ifname", "wlan0"], 5.0)
    assert again.returncode == 0                       # secret persisted


def test_nmcli_sim_scenarios_wrong_password_and_unreachable(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    runner = build_lab_system(paths).runner
    scenarios.apply(paths, "wrong-password")
    labnet = nm._uid("LabNet")
    # reseeded for the scenario: no client profile while on AP fallback
    conns = runner.run(["nmcli", "-t", "-f",
                        "UUID,NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY",
                        "connection", "show"], 5.0)
    assert "LabNet" not in conns.stdout
    add = runner.run(["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
                      "con-name", "LabNet", "ssid", "LabNet",
                      "connection.autoconnect", "no",
                      "connection.autoconnect-priority", "0",
                      "802-11-wireless-security.key-mgmt", "wpa-psk"], 5.0)
    uid = add.stdout.split("(")[1].split(")")[0]
    pw = tmp_path / "pw"
    pw.write_text("802-11-wireless-security.psk:wrong\n")
    up = runner.run(["nmcli", "connection", "up", uid, "passwd-file", str(pw)], 5.0)
    assert up.returncode == 4 and "Secrets" in up.stderr
    scenarios.apply(paths, "disconnected")
    add2 = runner.run(["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
                       "con-name", "Open", "ssid", "Open",
                       "connection.autoconnect", "no",
                       "connection.autoconnect-priority", "0"], 5.0)
    uid2 = add2.stdout.split("(")[1].split(")")[0]
    up2 = runner.run(["nmcli", "connection", "up", uid2], 5.0)
    assert up2.returncode == 4 and "No suitable" in up2.stderr
    assert labnet not in (uid, uid2)


def test_busctl_and_systemctl_sim(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    runner = build_lab_system(paths).runner
    can = runner.run(["busctl", "--timeout=5", "call", "org.freedesktop.login1",
                      "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                      "CanRebootToFirmwareSetup" if False else "CanReboot"], 8.0)
    assert can.returncode == 0 and can.stdout.strip() == 's "yes"'
    # user-unit state transitions and reads back
    assert runner.run(["systemctl", "--user", "is-active", "lhpc-web.service"],
                      5.0).returncode == 3
    en = runner.run(["systemctl", "--user", "enable", "--now", "lhpc-web.service"], 5.0)
    assert en.returncode == 0
    assert runner.run(["systemctl", "--user", "is-active", "lhpc-web.service"],
                      5.0).returncode == 0
    show = runner.run(["systemctl", "--user", "show", "lhpc-web.service",
                       "--property", "ActiveState,SubState,LoadState,UnitFileState"],
                      5.0)
    assert "ActiveState=active" in show.stdout and "UnitFileState=enabled" in show.stdout
    # system scope: honest "no such unit" + mutations refused
    sys_show = runner.run(["systemctl", "show", "loraham-daemon@433.service",
                           "--property", "ActiveState,SubState,LoadState,UnitFileState"],
                          5.0)
    assert "LoadState=not-found" in sys_show.stdout
    assert runner.run(["systemctl", "start", "sshd"], 5.0).returncode == 1


def test_power_auth_scenario_flag(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    runner = build_lab_system(paths).runner
    scenarios.apply(paths, "healthy")
    st = json.loads((tmp_path / "state" / "testlab" / "scenario.json").read_text())
    st["flags"]["power_auth"] = "no"
    (tmp_path / "state" / "testlab" / "scenario.json").write_text(json.dumps(st))
    can = runner.run(["busctl", "call", "org.freedesktop.login1",
                      "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                      "CanPowerOff"], 8.0)
    assert can.stdout.strip() == 's "no"'


# --- scenarios / LabFs ------------------------------------------------------------------


def test_scenarios_apply_load_and_auto_revert(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        scenarios.apply(paths, "bogus")
    scenarios.apply(paths, "degraded")
    assert scenarios.effective_state(paths)["radio_868"] == "FAILED"
    # recovery reverts to healthy once auto_revert_s elapsed
    scenarios.apply(paths, "recovery")
    rec = json.loads((tmp_path / "state" / "testlab" / "scenario.json").read_text())
    rec["applied_boottime"] = 0.0                      # long ago
    (tmp_path / "state" / "testlab" / "scenario.json").write_text(json.dumps(rec))
    eff = scenarios.effective_state(paths)
    assert eff["wifi"] == "connected" and "reverted" in eff["_name"]
    # malformed file falls back healthy, never raises
    (tmp_path / "state" / "testlab" / "scenario.json").write_text("{broken")
    assert scenarios.effective_state(paths)["_name"] == "healthy"


def test_labfs_overlay_missing_present_and_uptime(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    fs = build_lab_system(paths).fs
    scenarios.apply(paths, "hardware-missing")
    assert fs.exists("/usr/include/lgpio.h") is False
    assert fs.exists("/usr/bin/nmcli") is True         # forced-present tool probe
    supervisor.advance_boot(paths, reason="test")
    up = float(fs.read_text("/proc/uptime", 64).split()[0])
    assert 0.0 <= up < 60.0                            # simulated epoch, just reset


# --- spawn guard / boot identity ----------------------------------------------------------


def test_spawn_guard_power_deny_and_passthrough(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    seen = []

    def real(argv, log_path, cwd=None, env=None):
        seen.append(list(argv))
        return 4242
    guarded = provider.build(paths).wrap_spawn(real)
    trigger = ["sh", "-c", "sleep 1.5; exec timeout -k 5s 90s systemctl --no-block "
                           "reboot"]
    assert guarded(trigger, tmp_path / "logs" / "t.log") == 4242
    assert seen[-1][-2:] == ["--kind", "reboot"] and "_power" in seen[-1]
    assert guarded(["sudo", "rm", "-rf", "/"], tmp_path / "logs" / "t.log") is None
    assert guarded(["socat", "-V"], tmp_path / "logs" / "t.log") == 4242
    assert seen[-1] == ["socat", "-V"]


def test_current_boot_id_lab_override(tmp_path, monkeypatch):
    bf = tmp_path / "boot_id"
    bf.write_text("lab-boot-7\n")
    monkeypatch.setenv("LHPC_BOOT_ID_FILE", str(bf))
    assert lcmod.current_boot_id() == "lab-boot-7"
    # a set-but-missing file falls back to the REAL boot id, never "".
    bf.unlink()
    real = lcmod.current_boot_id()
    monkeypatch.delenv("LHPC_BOOT_ID_FILE")
    assert lcmod.current_boot_id() == real and real != "lab-boot-7"


def test_advance_boot_changes_identity_and_uptime(tmp_path, monkeypatch):
    paths = make_lab_root(tmp_path, monkeypatch)
    b1 = supervisor.ensure_boot_identity(paths)
    b2 = supervisor.advance_boot(paths)
    assert b1 and b2 and b1 != b2
    assert supervisor.sim_uptime(paths) < 60.0


# --- web surface ------------------------------------------------------------------------


def _web(tmp_path, monkeypatch, lab=True):
    # The panel + banner are added by the LAB launcher (labweb) onto a real lhpc app —
    # lhpc's own app.py has no testlab code. The /testlab blueprint constructs its own
    # ControllerService, which enters lab mode via the provider env when the root is a
    # lab root.
    from lhpc_testlab.labweb import build_app
    if lab:
        make_lab_root(tmp_path, monkeypatch)
        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    else:
        monkeypatch.delenv("LHPC_TESTLAB", raising=False)
        (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    return build_app().test_client(), None


def test_web_testlab_404_when_off_and_banner_when_on(tmp_path, monkeypatch):
    c, _svc = _web(tmp_path, monkeypatch, lab=False)
    assert c.get("/testlab").status_code == 404
    assert c.post("/testlab/reset").status_code in (400, 404)
    body = c.get("/").get_data(as_text=True)
    assert "TEST LAB" not in body
    c2, _svc2 = _web(tmp_path / "lab", monkeypatch, lab=True)
    body2 = c2.get("/").get_data(as_text=True)
    assert "TEST LAB — SIMULATED HARDWARE" in body2
    page = c2.get("/testlab").get_data(as_text=True)
    assert "Switch scenario" in page and "Inject" in page


def test_web_testlab_actions_csrf_and_scenario_switch(tmp_path, monkeypatch):
    import re as _re
    c, _svc = _web(tmp_path, monkeypatch, lab=True)
    # missing CSRF refused
    assert c.post("/testlab/scenario", data={"name": "degraded"}).status_code == 400
    page = c.get("/testlab").get_data(as_text=True)
    tok = _re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    r = c.post("/testlab/scenario", data={"_csrf": tok, "name": "degraded"},
               follow_redirects=False)
    assert r.status_code == 303 or r.status_code == 302
    assert scenarios.effective_state(Paths(runtime_root=tmp_path))["radio_868"] \
        == "FAILED"
    # unknown op 404s even with the token
    assert c.post("/testlab/bogus", data={"_csrf": tok}).status_code == 404


# --- {multiarch} require token ------------------------------------------------------------


def test_resolve_req_path_multiarch(tmp_path, monkeypatch):
    """The {multiarch} token resolves to the arch triple (a module constant, computed
    once): Pi -> the aarch64 literal (behavior unchanged), x86 -> truthful."""
    from lhpc.core.config import load_config
    from lhpc.core.lifecycle import Lifecycle
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    lc = Lifecycle(Paths(runtime_root=tmp_path), (), load_config(Paths(runtime_root=tmp_path)),
                   FakeSystem().system)
    for triple in ("aarch64-linux-gnu", "x86_64-linux-gnu"):
        monkeypatch.setattr(lcmod, "_MULTIARCH", triple)
        assert lc._resolve_req_path("/usr/lib/{multiarch}/libslirp.so.0") \
            == f"/usr/lib/{triple}/libslirp.so.0"
    assert lc._resolve_req_path("{runtime}/build/x") == f"{tmp_path}/build/x"
    # the module constant itself is a known triple (unknown arch -> aarch64 fallback)
    assert lcmod._MULTIARCH.endswith("-linux-gnu")


# --- fake daemon protocol conformance ----------------------------------------------------


def _spawn_fake_daemon(tmp_path, band="433", radio="READY"):
    import subprocess
    sockdir = tmp_path / "socks"
    sockdir.mkdir(exist_ok=True)
    root = tmp_path / "droot"
    (root / "state" / "testlab" / "rx-queue" / band).mkdir(parents=True, exist_ok=True)
    (root / "state" / "testlab" / "scenario.json").write_text(json.dumps(
        {"name": "t", "applied_boottime": 0, "auto_revert_s": None,
         "flags": {f"radio_{band}": radio}}))
    env = dict(__import__("os").environ, LORAHAM_SOCKET_DIR=str(sockdir),
               LORAHAM_RUNTIME_DIR=str(root / "state" / "loraham"))
    from lhpc_testlab import data_path
    script = str(data_path("loraham-daemon-fake", "loraham_daemon", "loraham_daemon"))
    proc = subprocess.Popen([__import__("sys").executable, script, "--radio", band],
                            env=env)
    import time as _t
    deadline = _t.monotonic() + 5
    while _t.monotonic() < deadline:
        if (sockdir / f"loraconf{band}.sock").exists():
            break
        _t.sleep(0.05)
    return proc, sockdir, root


def _conf(sockdir, band, cmd: bytes) -> str:
    import socket as _s
    c = _s.socket(_s.AF_UNIX)
    c.settimeout(5)
    c.connect(str(sockdir / f"loraconf{band}.sock"))
    c.sendall(cmd + b"\n")
    out = c.recv(4096).decode()
    c.close()
    return out


@pytest.mark.slow
def test_fake_daemon_conf_lines_satisfy_daemon_control(tmp_path):
    """Every GET reply is one line of non-empty KEY=VALUE tokens (lhpc's _query fails
    closed on bare/duplicate tokens); SET grammar answers OK/ERR per the v112 daemon."""
    proc, sockdir, _root = _spawn_fake_daemon(tmp_path)
    try:
        for cmd, prefix in ((b"GET STATUS", "STATUS"), (b"GET STATS", "STATS"),
                            (b"GET CHANNEL", "CHANNEL")):
            line = _conf(sockdir, "433", cmd)
            assert line.endswith("\n") and line.count("\n") == 1
            head, *tokens = line.strip().split(" ")
            assert head == prefix and tokens
            keys = [t.split("=")[0] for t in tokens]
            assert all("=" in t and t.split("=", 1)[1] != "" for t in tokens), tokens
            assert len(keys) == len(set(keys))                 # no duplicates
        assert "RADIO=READY" in _conf(sockdir, "433", b"GET STATUS")
        assert _conf(sockdir, "433", b"SET TXRESULT=1") == "OK\n"
        assert "TXRESULT=1" in _conf(sockdir, "433", b"GET STATUS")
        assert _conf(sockdir, "433", b"SET TXRESULT=9") == "ERR INVALID\n"
        assert _conf(sockdir, "433", b"SET NOPE=1") == "ERR UNKNOWN\n"
        assert _conf(sockdir, "433", b"SET MODE=") == "ERR MALFORMED\n"
        assert _conf(sockdir, "433",
                     b"SET MODE=LORA FREQ=433.775 SF=12 BW=125") == "OK\n"
        assert "MODE=LORA" in _conf(sockdir, "433", b"GET CHANNEL")
        assert _conf(sockdir, "433", b"SET CADRSSI=-90") == "OK\n"
        assert _conf(sockdir, "433", b"SET CADWAIT=10") == "ERR INVALID\n"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.slow
def test_fake_daemon_framed_tx_result_and_rx_injection(tmp_path):
    """Framed 0x02 TX -> TX_RESULT 0x04 (status/flags/seq, only after SET TXRESULT=1),
    TXOK visible in GET STATS; queued RX arrives as one 0x01 frame with the 4-byte
    metadata header and the 3C FF 01 TNC2 payload; TX is captured to tx.jsonl."""
    import socket as _s
    import struct as _st
    proc, sockdir, root = _spawn_fake_daemon(tmp_path)
    try:
        f = _s.socket(_s.AF_UNIX)
        f.settimeout(8)
        f.connect(str(sockdir / "lora433f.sock"))
        _conf(sockdir, "433", b"SET TXRESULT=1")
        f.sendall(b"\x02" + _st.pack("<H", 5) + b"hello")
        resp = f.recv(64)
        assert resp[0] == 0x04 and _st.unpack("<H", resp[1:3])[0] == 4
        status, flags, seq = _st.unpack("<BBH", resp[3:7])
        assert status == 0 and flags & 0x01 and seq == 1
        assert "TXOK=1" in _conf(sockdir, "433", b"GET STATS")
        (root / "state" / "testlab" / "rx-queue" / "433" / "0001.json").write_text(
            json.dumps({"preset": "aprs-position"}))
        rx = f.recv(512)
        assert rx[0] == 0x01
        ln = _st.unpack("<H", rx[1:3])[0]
        rssi, snr = _st.unpack("<hh", rx[3:7])
        assert rssi == -9500 and snr == 800
        assert rx[7:10] == b"\x3c\xff\x01"
        assert b"DL0LAB-9>APDR16" in rx[10:3 + ln]
        assert not list((root / "state" / "testlab" / "rx-queue" / "433").glob("*"))
        tx = (root / "state" / "testlab" / "tx.jsonl").read_text()
        assert '"result": "OK"' in tx and '"band": "433"' in tx
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.slow
def test_fake_daemon_radio_failed_refuses_tx(tmp_path):
    import socket as _s
    import struct as _st
    proc, sockdir, _root = _spawn_fake_daemon(tmp_path, radio="FAILED")
    try:
        assert "RADIO=FAILED" in _conf(sockdir, "433", b"GET STATUS")
        assert _conf(sockdir, "433", b"SET FREQ=433.775") == "ERR RADIO_NOT_READY\n"
        f = _s.socket(_s.AF_UNIX)
        f.settimeout(5)
        f.connect(str(sockdir / "lora433f.sock"))
        _conf(sockdir, "433", b"SET TXRESULT=1")
        f.sendall(b"\x02" + _st.pack("<H", 2) + b"hi")
        resp = f.recv(64)
        assert resp[0] == 0x04 and resp[3] == 3                # RADIO_NOT_READY
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# --- re-review regressions ----------------------------------------------------------------


def test_stale_process_refuses_lab_surfaces(tmp_path, monkeypatch):
    """RE-REVIEW: a service built BEFORE `testlab init` (lab context not latched) must
    refuse every lab surface even after the marker appears — its mutators would run
    against the real host while the UI claimed simulation."""
    monkeypatch.setenv("LHPC_TESTLAB", "1")
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))   # marker absent
    assert svc._ext is None
    (tmp_path / "state" / "testlab").mkdir(parents=True)
    (tmp_path / "state" / "testlab" / "enabled").write_text("lab\n")
    assert ops.is_active(svc) is False
    r = ops.reset(svc)
    assert not r.ok and "restart" in r.summary
    fresh = ControllerService(paths=Paths(runtime_root=tmp_path))
    assert fresh._ext is not None and ops.is_active(fresh) is True


def test_explicit_injection_never_gets_the_overlay(tmp_path, monkeypatch):
    """RE-REVIEW: system= OR manifest_path= injected -> NO lab defaults at all (a
    half-lab service — overlay manifest with a non-lab system — must be impossible)."""
    paths = make_lab_root(tmp_path, monkeypatch)
    overlay = tmp_path / "state" / "testlab" / "manifest.toml"
    overlay.write_text("# lab overlay\n")
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    assert svc._ext is None and svc._manifest_path is None
    other = tmp_path / "other-manifest.toml"
    other.write_text("# explicit\n")
    svc2 = ControllerService(manifest_path=other, paths=paths)
    assert svc2._ext is None and svc2._manifest_path == other


def test_power_trigger_guard_locked_to_production_argv():
    """RE-REVIEW: the guard matches EXACTLY the argv service_maintenance composes —
    locked together via power_trigger_argv, no substring heuristics."""
    from lhpc.core.service_maintenance import power_trigger_argv
    for kind in ("reboot", "poweroff"):
        assert rules.power_kind_in(power_trigger_argv(kind)) == kind
    assert rules.power_kind_in(["sh", "-c", "echo systemctl reboot manual"]) == ""
    assert rules.power_kind_in(["bash", "-c", "sleep 1.5; exec timeout -k 5s 90s "
                                              "systemctl --no-block reboot"]) == ""


# --- audit fixes: reboot restore, check honesty, reset cleanliness ------------------------


def test_check_fails_when_installed_stack_not_ready(tmp_path, monkeypatch):
    """RE-AUDIT: a reported missing requirement must not coexist with 'passed'. An
    uninstalled stack is 'not installed' (fine); a dead fake fails the check."""
    paths = make_lab_root(tmp_path, monkeypatch)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "state" / "testlab" / "gpsd.pid").write_text("2147480000")  # dead
    svc = ControllerService(paths=paths)
    r = ops.check(svc)
    assert not r.ok and "problem" in r.summary
    # uninstalled stacks are reported as such, never as a false 'ready'
    assert any("not installed" in d for d in r.details)


def test_reset_clears_accumulated_state(tmp_path, monkeypatch):
    """RE-AUDIT: reset is a CLEAN baseline — simulated NM profiles, unit state and the
    TX log do not survive it."""
    paths = make_lab_root(tmp_path, monkeypatch)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    sd = tmp_path / "state" / "testlab"
    (sd / "nm.json").write_text('{"profiles":[{"name":"MyHomeWifi"}]}')
    (sd / "units.json").write_text('{"lhpc-web.service":{"active":true}}')
    (sd / "tx.jsonl").write_text("stale\n")
    ops._clear_runtime_state(ControllerService(paths=paths))
    assert not (sd / "nm.json").exists()
    assert not (sd / "units.json").exists()
    assert not (sd / "tx.jsonl").exists()


def test_power_reboot_does_not_tombstone(tmp_path, monkeypatch):
    """RE-AUDIT: a simulated reboot terminates running stacks WITHOUT the operator-stop
    tombstone (which would make boot-restore skip them), and advances the boot id. No
    stop-intent is written. (The restore-running-stacks path is verified end-to-end in
    the acceptance chain.)"""
    paths = make_lab_root(tmp_path, monkeypatch)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    supervisor.ensure_boot_identity(paths)
    boot1 = supervisor.boot_file(paths).read_text().strip()
    svc = ControllerService(paths=paths)
    assert ops.power(svc, "reboot") == 0            # no running stacks -> just reboots
    assert supervisor.boot_file(paths).read_text().strip() != boot1
    assert not (tmp_path / "state" / "stop-intent").exists()


def _populate_stub(paths, *, installed=(), binary=(), fail_install=()):
    """A ControllerService stand-in for populate(): records install/build calls and lets a
    test choose which stacks are pre-installed, binary-backed, or fail to install."""
    from types import SimpleNamespace
    seen = {"install": [], "build": []}
    done = set(installed)

    class Stub:
        _paths = paths
        BINARY_CHANNEL = "binary"

        def stack(self, sid):
            return object()

        def is_installed(self, sid):
            return sid in done

        def binary_available(self, sid):
            return (sid in binary, "")

        def install(self, sid, apply=False, source="pinned"):
            seen["install"].append((sid, source))
            if sid in fail_install:
                return SimpleNamespace(ok=False, summary="install boom", details=[])
            done.add(sid)
            return SimpleNamespace(ok=True, summary="", details=[])

        def build(self, sid, apply=False):
            seen["build"].append(sid)
            return SimpleNamespace(ok=True, summary="", details=[])

    return Stub(), seen


def test_populate_installs_headless_stacks(tmp_path, monkeypatch):
    """populate(): pre-installed source stack is still BUILT (readiness needs a build);
    binary-backed stack is installed from binary and NOT built (lhpc refuses that); a
    failed install yields no build and no readiness; the completion marker is withheld
    while a stack is still missing."""
    paths = make_lab_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ops, "is_active", lambda svc: True)
    svc, seen = _populate_stub(paths, installed=("kiss",), binary=("graywolf",),
                               fail_install=("reticulum",))
    r = ops.populate(svc)
    assert r.ok
    installed = dict(seen["install"])
    assert "kiss" not in installed                # already installed -> no install call
    assert "kiss" in seen["build"]                # ...but still built to be READY
    assert installed["graywolf"] == "binary"      # binary channel where available
    assert "graywolf" not in seen["build"]        # binary install is NOT built (would refuse)
    assert installed["meshcore"] == "pinned"      # source otherwise
    assert "meshcore" in seen["build"]
    assert "reticulum" not in seen["build"]       # failed install -> no build, non-fatal
    # reticulum still missing -> no completion marker; progress records the ready set
    assert not ops.populate_marker_path(paths).exists()
    prog = json.loads(ops.populate_progress_path(paths).read_text())
    assert "reticulum" not in prog["ready"]
    assert {"kiss", "graywolf", "meshcore"} <= set(prog["ready"])


def test_populate_withholds_marker_on_durable_failure(tmp_path, monkeypatch):
    """A durably-failing stack NEVER writes the completion marker — so start.sh keeps
    re-running populate on later boots and the stack self-heals once its cause is fixed.
    Ready stacks are not re-installed across passes."""
    paths = make_lab_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ops, "is_active", lambda svc: True)
    svc, seen = _populate_stub(paths, fail_install=("reticulum",))
    for _ in range(4):                                # several passes (mimics several boots)
        ops.populate(svc)
        # while reticulum keeps failing the box is never marked "done" (no baked give-up marker)
        assert not ops.populate_marker_path(paths).exists()
    # the healthy stacks are installed exactly once, not re-attempted every pass
    assert sum(1 for sid, _ in seen["install"] if sid == "meshcore") == 1
    assert "reticulum" not in json.loads(ops.populate_progress_path(paths).read_text())["ready"]


def test_graywolf_upstream_forced_to_sink_in_overlay(tmp_path, monkeypatch):
    """SAFETY: the manifest overlay renders graywolf-provision.py with a LITERAL
    --igate-server 127.0.0.1 (the operator-controlled {param:igate_server}/{param:igate_port}
    tokens are removed), so graywolf can NEVER be pointed at the live APRS-IS network — no
    matter the saved config, through the real argv-render/launcher path. Tested against the
    REAL packaged manifest, so a manifest line drift breaks this test (and reset fails closed)."""
    from lhpc_testlab import manifest_overlay
    paths = make_lab_root(tmp_path, monkeypatch)
    overlay = manifest_overlay.generate(
        paths, {"loraham-daemon-fake": "abc123", "radiolib-fake": "def456"})
    text = overlay.read_text()
    assert manifest_overlay.GRAYWOLF_SINK_TO in text
    assert '"--igate-server", "127.0.0.1", "--igate-port", "14580",' in text
    assert "{param:igate_server}" not in text and "{param:igate_port}" not in text


def test_provider_fails_closed_when_active_but_broken(tmp_path, monkeypatch):
    """SAFETY: an ACTIVE lab whose backend construction fails must PROPAGATE (fail closed),
    never return None -> RealSystem under a 'SIMULATED HARDWARE' banner."""
    paths = make_lab_root(tmp_path, monkeypatch)    # engages the two-key latch
    assert testlab.active(paths) is True
    import lhpc_testlab.system as _sys

    def _boom(_paths):
        raise RuntimeError("lab backend broke")
    monkeypatch.setattr(_sys, "build_lab_system", _boom)
    with pytest.raises(RuntimeError):
        provider.build(paths)
    # latch NOT engaged -> None (real system, byte-for-byte), never raises
    monkeypatch.delenv("LHPC_TESTLAB", raising=False)
    assert provider.build(paths) is None


def test_meshtastic_dashboard_link_forwards_to_bridge(monkeypatch):
    """SAFETY/UX: the lab link-rewriter must point meshtastic's self-signed-HTTPS :9443 link
    at the plain-HTTP :9080 socat bridge in the Codespace URL — else the console's Meshtastic
    link 502s. Other lab ports rewrite to their own forwarded URL unchanged."""
    from flask import Flask
    from lhpc_testlab import web as labweb
    monkeypatch.setenv("CODESPACE_NAME", "demo-cs")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    app = Flask(__name__)

    @app.get("/x")
    def _x():
        return ('<a href="https://127.0.0.1:9443/">mesh</a>'
                '<a href="https://127.0.0.1:8080/">gw</a>'), 200, {"Content-Type": "text/html"}
    labweb.install(app)
    body = app.test_client().get("/x").get_data(as_text=True)
    assert "https://demo-cs-9080.app.github.dev/" in body      # 9443 -> 9080 bridge
    assert "demo-cs-9443" not in body                          # NOT the broken self-signed URL
    assert "https://demo-cs-8080.app.github.dev/" in body      # other lab ports unchanged

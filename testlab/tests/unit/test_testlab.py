"""Test-lab mode: the two-key activation latch, the ControllerService injection seam
(production byte-path when off; explicit injection always wins), the LabRunner's
deny/simulate/passthrough dispatch, scenario-driven simulators, the LabFs overlay, the
spawn guard, and the simulated boot identity."""
from __future__ import annotations

import json
import re
import subprocess
import sys

import lhpc_testlab as testlab
import pytest
from lhpc_testlab import nm, ops, provider, rules, scenarios, supervisor
from lhpc_testlab.system import LabRunner, build_lab_system
from lhpc_testlab.testing import make_lab_root

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
    with c.session_transaction() as sess:          # a VALID token, so the request gets past
        sess["_csrf"] = "t"                       # CSRF and actually reaches the lab-off gate
    assert c.post("/testlab/reset", data={"_csrf": "t"}).status_code == 404
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


def _frame(sock):
    """Read exactly ONE length-prefixed frame: kind byte, 2-byte length, then the body.

    A unix SOCK_STREAM may split or coalesce writes, so a bare `recv(n)` can return half a
    frame or two at once. Every assertion below indexes into a whole frame, so read one.
    """
    import struct as _st
    head = b""
    while len(head) < 3:
        chunk = sock.recv(3 - len(head))
        assert chunk, "socket closed mid-frame"
        head += chunk
    body = b""
    want = _st.unpack("<H", head[1:3])[0]
    while len(body) < want:
        chunk = sock.recv(want - len(body))
        assert chunk, "socket closed mid-frame"
        body += chunk
    return head + body


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
        resp = _frame(f)
        assert resp[0] == 0x04 and _st.unpack("<H", resp[1:3])[0] == 4
        status, flags, seq = _st.unpack("<BBH", resp[3:7])
        assert status == 0 and flags & 0x01 and seq == 1
        assert "TXOK=1" in _conf(sockdir, "433", b"GET STATS")
        (root / "state" / "testlab" / "rx-queue" / "433" / "0001.json").write_text(
            json.dumps({"preset": "aprs-position"}))
        rx = _frame(f)
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
        resp = _frame(f)
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
    """RE-REVIEW: the guard matches EXACTLY the argv lhpc.core.power composes —
    locked together via power_trigger_argv, no substring heuristics."""
    from lhpc.core.power import power_trigger_argv
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


# --- the release lane's required cases --------------------------------------------------


def test_a_missing_required_release_case_is_detected():
    """The automated release requires NAMES, not a count. It used to accept any fourteen
    passing cases called `test_release_*`, so dropping or renaming one — the MeshCom case here
    — kept the count intact while that stack stopped being proved."""
    from lhpc_testlab.release import missing_required_cases, required_release_cases

    required = list(required_release_cases())
    assert "test_release_meshcom" in required, "the list no longer names the MeshCom case"
    assert missing_required_cases(required) == []
    reported = [n for n in required if n != "test_release_meshcom"]
    assert missing_required_cases([*reported, "test_release_meshcom_renamed"]) == [
        "test_release_meshcom"]


def test_the_required_cases_are_the_names_the_release_lane_defines():
    """The list and the lane are one thing.

    The lane's own first case asserts this same equality, but only when the lane RUNS — which
    is during a release, an hour of installs later. This is its cheap twin, so a rename is
    caught in CI instead. It reads the file rather than importing it because a test module may
    not import a sibling test module, and the names are the contract the release automation
    consumes.
    """
    import ast
    from pathlib import Path

    from lhpc_testlab.release import required_release_cases

    lane = Path(__file__).resolve().parents[1] / "release" / "test_release_verify.py"
    tree = ast.parse(lane.read_text())
    defined = {n.name for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_release_")}
    assert defined == set(required_release_cases())


# --- the stack-regression marker ---------------------------------------------------------

# The grammar exactly as `lhpc_testlab.release.stack_regression` documents it, written out here
# the way the release automation's own parser has to write it — a program in another repository
# reads this line out of the lane's JUnit and freezes the stack it names.
STACK_REGRESSION_LINE = re.compile(
    r"^STACK-REGRESSION stack=([a-z0-9][a-z0-9_-]*) phase=(install|build|start|readiness)$")
# The same grammar as the automation must SEARCH for it in a JUnit `<failure>`: pytest puts
# `E   ` in front of an explanation line and `AssertionError: ` in front of the message, so the
# start of the line is not the start of the marker. Nothing follows the phase on its line.
STACK_REGRESSION_IN_TEXT = re.compile(
    r"STACK-REGRESSION stack=([a-z0-9][a-z0-9_-]*) "
    r"phase=(install|build|start|readiness)(?=\s|$)")


def test_the_stack_regression_marker_is_the_documented_line():
    """One line, three fields, parseable by the published regex — the marker IS the contract."""
    from lhpc_testlab.release import stack_regression

    assert stack_regression("kiss", "readiness") == \
        "STACK-REGRESSION stack=kiss phase=readiness"
    m = STACK_REGRESSION_LINE.match(stack_regression("meshcore", "build"))
    assert m and m.group(1) == "meshcore" and m.group(2) == "build"


@pytest.mark.parametrize("stack,phase", [
    ("kiss", "shutdown"),        # a phase the parser does not know
    ("kiss", "Install"),         # the phases are lower-case
    ("MeshCore", "start"),       # a stack id is lower-case
    ("meshcore cli", "start"),   # a space would end the field early
    ("", "start"),
])
def test_the_marker_refuses_what_the_grammar_does_not_cover(stack, phase):
    """It raises instead of emitting a line the automation would silently fail to match — a
    marker that does not parse is worse than none, because the failure looks attributed."""
    from lhpc_testlab.release import stack_regression

    with pytest.raises(ValueError):
        stack_regression(stack, phase)


def _junit_entries(tmp_path, lane: str, *extra_args) -> list:
    """Run `lane` as its own pytest process and return every reported entry as
    `(case name, "failure"|"error", text)` — message attribute and body joined, exactly what the
    release automation searches. A case that FAILS and whose teardown then ERRORS is two entries
    under one name, which is what pytest hands the automation."""
    import xml.etree.ElementTree as ET

    (tmp_path / "test_marker_lane.py").write_text(lane)
    xml = tmp_path / "junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    *extra_args, "test_marker_lane.py", f"--junitxml={xml}"],
                   cwd=tmp_path, capture_output=True, text=True, timeout=300, check=False)
    out = []
    # Untrusted only in the abstract: this XML is the file pytest just wrote here.
    for case in ET.parse(xml).getroot().iter("testcase"):  # noqa: S314
        for kind in ("failure", "error"):
            for f in case.iter(kind):
                out.append((case.get("name"), kind,
                            (f.get("message") or "") + "\n" + (f.text or "")))
    return out


def _junit_failures(tmp_path, lane: str, *extra_args) -> dict:
    """{case name: its JUnit failure text}, for lanes that report one entry per case."""
    return {n: text for n, kind, text in _junit_entries(tmp_path, lane, *extra_args)
            if kind == "failure"}


# Each stubbed `lhpc` run is as long as the real thing's output, so a marker is read out of a
# failure the size the automation actually meets. The MeshCore cases run against the REAL recipe:
# its steps, its declarations, its log names.
_MARKER_LANE = """
import lhpc_testlab.release as rel
from lhpc.core.manifest import default_manifest_path, load_manifest

class _R:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out + "boom\\n" * 400, "boom\\n" * 400

STACKS = {s.id: s for s in load_manifest(default_manifest_path())}

class SVC:
    def stack(self, sid):
        return STACKS[sid]

# The MeshCore node's REAL recipe: a step that reaches the network (pip, installing openHop's
# dependency closure) and a step the recipe declares its own (the LHPC patch). LHPC types both
# failures identically, and the log name is what tells them apart.
NODE = next(c for c in STACKS["meshcore"].components if c.id == "meshcore-node")
NET_STEP = next(i for i, s in enumerate(NODE.build_steps)
                if str(s["argv"][0]).endswith("pip") and "install" in s["argv"])
OWN_STEP = next(i for i, s in enumerate(NODE.build_steps) if s.get("attributable"))

def typed(step):
    return (f"  [failed] build meshcore-node (rc 1, log /x/logs/build-meshcore-node-{step}.log)"
            "\\n")

# What pip prints when the package index is unreachable — the failure this rule exists to keep
# off a stack's pins.
PIP_DNS = ("WARNING: Retrying (Retry(total=0)) after connection broken by "
           "'NewConnectionError(...: Temporary failure in name resolution)': /simple/pynacl/\\n"
           "ERROR: Could not find a version that satisfies the requirement pynacl\\n")

# What `lhpc build` prints when LHPC ITSELF typed a component's build as failed, and the two
# shapes that are NOT that: a build the runner ran out of time on, and a refusal that executed
# no build step at all.
TYPED = "  [failed] build loraham-kiss-tnc (rc 1, log /x/logs/build-loraham-kiss-tnc.log)\\n"
TIMED_OUT = "  [timeout] build loraham-kiss-tnc (rc 124, log /x/logs/build-x.log)\\n"
REFUSED = "ERR   Refusing to build 'kiss': loraham-kiss-tnc is not installed.\\n"
CLONE = "  [failed] clone loraham-kiss-tnc — fatal: unable to access: Could not resolve host\\n"

def _script(*results):
    it = iter(results)
    rel.run_lhpc = lambda env, *a, **kw: next(it)

ENV = {'LHPC_RUNTIME_ROOT': '/nonexistent'}

def test_a_build_lhpc_typed_as_the_components_own():
    _script(_R(0), _R(1, TYPED))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_build_that_ran_out_of_time():
    _script(_R(0), _R(1, TIMED_OUT))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_build_refused_before_a_step_ran():
    _script(_R(0), _R(1, REFUSED))
    rel.install_build(ENV, SVC(), 'kiss')

def test_an_install_that_could_not_reach_the_remote():
    _script(_R(1, CLONE))
    rel.install_build(ENV, SVC(), 'kiss')

def test_a_package_network_failure_during_a_build_step():
    _script(_R(0), _R(1, PIP_DNS + typed(NET_STEP)))
    rel.install_build(ENV, SVC(), 'meshcore')

def test_a_build_step_the_recipe_declares_its_own():
    _script(_R(0), _R(1, "error: patch does not apply\\n" + typed(OWN_STEP)))
    rel.install_build(ENV, SVC(), 'meshcore')

def test_a_prerequisite_another_case_should_have_left_running():
    _script(_R(0, "[kiss] LoRaHAM KISS TNC  (stopped)\\n"))
    rel.require_prerequisite(ENV, 'kiss', left_by='test_release_kiss')

def test_stopping_another_stack():
    _script(_R(1))
    rel.stop(ENV, 'graywolf')
"""


def test_only_a_build_step_the_recipe_declares_its_own_is_attributable(tmp_path):
    """THE attribution rule, proved where the automation reads it: the lane's JUnit.

    Every case here fails through the REAL release helpers with the executable stubbed out. Only
    one of them is this stack's own regression — a build LHPC typed as failed AT A STEP THE
    RECIPE DECLARES ITS OWN — and only that one may carry a marker, because a marker freezes the
    stack's pins until someone lifts it.

    The MeshCore pair is the counterexample that made the rule: its real recipe installs openHop's
    dependency closure with pip, so an unreachable package index produces exactly the same typed
    `[failed] build …` line as a patch that no longer applies. Attributing on the typed line alone
    froze all four MeshCore pins over a name-resolution failure. An install that could not reach
    the remote, a build the runner ran out of time on, a refusal that executed no build step, a
    prerequisite an earlier case should have left running and a stop of ANOTHER stack are the same
    kind of "may equally be the environment" failure, and carry none either: an unattributed
    failure is reported as an ordinary failure and freezes nothing.
    """
    failures = _junit_failures(tmp_path, _MARKER_LANE)
    attributable = {"test_a_build_lhpc_typed_as_the_components_own",
                    "test_a_build_step_the_recipe_declares_its_own"}
    assert set(failures) == attributable | {
        "test_a_build_that_ran_out_of_time",
        "test_a_build_refused_before_a_step_ran",
        "test_an_install_that_could_not_reach_the_remote",
        "test_a_package_network_failure_during_a_build_step",
        "test_a_prerequisite_another_case_should_have_left_running",
        "test_stopping_another_stack"}, failures

    for name, stack in (("test_a_build_lhpc_typed_as_the_components_own", "kiss"),
                        ("test_a_build_step_the_recipe_declares_its_own", "meshcore")):
        marked = failures[name]
        hits = STACK_REGRESSION_IN_TEXT.findall(marked)
        assert set(hits) == {(stack, "build")}, f"unattributable JUnit failure:\n{marked}"
        # Every occurrence parsed: a marker the automation cannot read is worse than none.
        assert marked.count("STACK-REGRESSION") == len(hits)

    for name, text in failures.items():
        if name in attributable:
            continue
        assert "STACK-REGRESSION" not in text, f"{name} must not be attributable to a stack"


def test_the_prerequisite_failure_says_it_is_not_this_stacks_regression(tmp_path):
    """A reader — and the maintainer triaging the run — has to see WHY it failed. An unmarked
    failure that merely said "graywolf's web UI never answered" looked exactly like graywolf
    breaking, when kiss had simply never come up."""
    failures = _junit_failures(tmp_path, _MARKER_LANE)
    text = failures["test_a_prerequisite_another_case_should_have_left_running"]
    assert "prerequisite not met: kiss is not running" in text
    assert "test_release_kiss" in text


# --- the lane releases what a FAILING case took -----------------------------------------

_CLEANUP_LANE = """
import json, pathlib
import lhpc_testlab.release as rel
import pytest

LOG = pathlib.Path("calls.json")
# What a FRESH lab root actually reports, and with the display names the product ships.
# Both details were wrong here before and each hid a defect: "stopped" hid a cleanup that fired
# for nothing, and a name with no parentheses hid a status parser that could not see three of
# the eight stacks at all.
STATE = {"kiss": "not-installed", "graywolf": "stopped",
         "reticulum": "not-installed", "meshcore": "not-installed"}
NAMES = {"kiss": "KISS TNC", "graywolf": "Graywolf",
         "reticulum": "Reticulum (RNS)", "meshcore": "MeshCore (OpenHop)"}

def _run(env, *a, **kw):
    if a[:1] == ("status",):
        out = "".join(f"[{s}] {NAMES[s]}  ({st})\\n" for s, st in STATE.items())
        out += "[controller] loraham-pi-control  (up to date)\\n"
    else:
        assert a[:2] == ("stack", "stop"), a
        STATE[a[2]] = "stopped"
        LOG.write_text(json.dumps(json.loads(LOG.read_text() or "[]") + [a[2]]))
        out = ""
    return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()

rel.run_lhpc = _run
LOG.write_text("[]")

@pytest.fixture
def env():
    return {}

def test_a_case_that_fails_after_starting_its_stack(env):
    STATE["kiss"] = "running"
    assert False, "the case aborted before its trailing stop()"

def test_a_passing_case_keeps_what_it_started_for_the_next_one(env):
    STATE["graywolf"] = "running"

def test_the_next_case_sees_what_was_left(env):
    assert STATE["kiss"] == "stopped", STATE
    assert STATE["graywolf"] == "running", STATE
"""


def test_a_failing_case_releases_its_stack_and_a_passing_one_does_not(tmp_path):
    """A case that aborts before its trailing `stop()` used to leave its stack holding the lab's
    one radio pair, so the NEXT case failed at its own MARKED readiness assertion and an
    automated release froze an innocent stack. A FAILING case now releases everything holding,
    because once it has failed the chain is broken and there is nothing left to preserve. A
    PASSING case is still left exactly as it is, which is what keeps the chaining intact.
    """
    import json

    failures = _junit_failures(tmp_path, _CLEANUP_LANE, "-p", "lhpc_testlab.release_lane")
    assert set(failures) == {"test_a_case_that_fails_after_starting_its_stack"}, failures
    assert json.loads((tmp_path / "calls.json").read_text()) == ["kiss"]


# The lane as a RELEASE runs it: `-x`, and a stop that cannot stop anything. One radio pair,
# two stacks that both want it.
_FAILFAST_LANE = """
import lhpc_testlab.release as rel
import pytest

STATE = {"kiss": "running", "reticulum": "stopped"}
NAMES = {"kiss": "KISS TNC", "reticulum": "Reticulum (RNS)"}

def _run(env, *a, **kw):
    if a[:1] == ("status",):
        out = "".join(f"[{s}] {NAMES[s]}  ({st})\\n" for s, st in STATE.items())
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    assert a[:2] == ("stack", "stop"), a
    # The stop FAILS: the process would not go away, so the band stays held.
    return type("R", (), {"returncode": 1, "stdout": "could not stop the process",
                          "stderr": ""})()

rel.run_lhpc = _run

@pytest.fixture
def env():
    return {}

def test_release_kiss(env):
    assert False, rel.stack_regression("kiss", "readiness") + "\\nkiss never served KISS/TCP"

def test_release_reticulum(env):
    STATE["reticulum"] = "running"
    assert False, rel.stack_regression("reticulum", "readiness") + "\\nrnstatus listed nothing"
"""


def test_a_stopped_run_reports_its_failed_cleanup_and_never_reaches_the_next_stack(tmp_path):
    """The two sequences that made continuing after a failure unsafe in BOTH directions.

    A cleanup that SUCCEEDS used to be followed by the next case failing UNMARKED on its
    prerequisite, and the consumer — right to refuse attribution when a run also carries an
    unexplained failure — then dropped the genuine freeze the first case had earned. A cleanup
    that FAILS was worse: the band stayed held, the next case hit it, earned its OWN valid
    marker, and an innocent stack was frozen with nothing in the JUnit saying the lab had not
    cleaned up.

    `-x` and a raising teardown answer both. The run stops at the first genuine regression, which
    is the only one this lane can still judge; the failed stop is reported as a teardown ERROR on
    that same case, with no marker on it — so a run whose lab is broken cannot attribute anything
    at all. Nothing about the second stack is claimed either way, and a stopped run can never
    satisfy the publication gate, which requires every required case to have passed.
    """
    entries = _junit_entries(tmp_path, _FAILFAST_LANE, "-x", "-p", "lhpc_testlab.release_lane")
    assert [(n, k) for n, k, _ in entries] == [("test_release_kiss", "failure"),
                                               ("test_release_kiss", "error")], entries
    failure, error = entries[0][2], entries[1][2]
    assert set(STACK_REGRESSION_IN_TEXT.findall(failure)) == {("kiss", "readiness")}
    assert "STACK-REGRESSION" not in error, error
    assert "could not release" in error, error


def test_the_release_verify_job_runs_the_lane_with_x():
    """`-x` IS the correction above, and it lives in the invocation rather than in the lane's
    code — so nothing in the lane notices when it is dropped. The automated release dispatches
    exactly this job and reads exactly this JUnit, which makes the flag part of the attribution
    contract and not a preference."""
    from pathlib import Path

    wf = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "testlab.yml")
    invocations = [ln for ln in wf.read_text().splitlines()
                   if "pytest testlab/tests/release" in ln]
    assert invocations, "the release-verify job no longer runs the release lane"
    for ln in invocations:
        assert " -x" in ln, f"the release lane must stop at the first failure: {ln.strip()}"


def test_the_lane_refuses_to_fake_a_box_without_a_display(tmp_path):
    """The terminal Voice variant is only offered where LHPC's own predicate sees no graphical
    session, so the lane takes ITS OWN X server down for that case. Asked to remove a session it
    did not start, it must refuse and say so — proving that variant needs a box without a
    display, not a pretence that there is none."""
    from lhpc_testlab.release_lane import LabDisplay

    d = LabDisplay(":98")
    assert d.proc is None
    with pytest.raises(AssertionError, match="did not start"):
        d.down()


def test_a_stack_whose_name_carries_parentheses_is_still_seen():
    """`MeshCore (OpenHop)`, `Reticulum (RNS)` and `MeshCom (QEMU)` are three of the eight. A
    parser that forbade `(` before the state read them as absent, so a prerequisite check on any
    of them failed on every single run — and the lane's own controller line must still not be
    mistaken for a stack."""
    from lhpc_testlab.release import _STACK_STATE

    text = ("[daemon] LoRaHAM daemon  (stopped)\n"
            "[meshcore] MeshCore (OpenHop)  (running)\n"
            "[reticulum] Reticulum (RNS)  (degraded)\n"
            "[meshcom] MeshCom (QEMU)  (not-installed)\n"
            "[controller] loraham-pi-control  (up to date)\n"
            "   loraham-chat  (running)\n")
    seen = dict(_STACK_STATE.findall(text))
    assert seen == {"daemon": "stopped", "meshcore": "running",
                    "reticulum": "degraded", "meshcom": "not-installed"}


def test_only_a_stack_that_holds_something_counts_as_holding():
    """A fresh lab root reports every stack `not-installed`. Counting that as "holding" put the
    stack a case was about into the before-set, so the diff came out empty and the cleanup
    stopped nothing — for every first case of every chain."""
    import lhpc_testlab.release as rel

    states = {"a": "not-installed", "b": "stopped", "c": "running",
              "d": "degraded", "e": "failed", "f": "not-applicable"}
    rel.stack_states = lambda env: states
    assert rel.holding_stacks({}) == {"c", "d", "e"}

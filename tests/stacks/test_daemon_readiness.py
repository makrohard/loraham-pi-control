"""1.2 — daemon readiness is RADIO=READY, not mere CONF-socket reachability.

A reachable daemon reporting RADIO=FAILED or RADIO=UNINITIALIZED never serves a
usable radio band: it must not permit a dependent launch, a TX-mode/CADIDLE apply,
a verified band-up, or a TX test. (The daemon reference states RADIO ∈
{READY, FAILED, UNINITIALIZED} — radio_health.cpp.)"""

import pytest

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core import daemon_control


def _svc(tmp_path, status: bytes):
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": status}).system
    return ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))


def test_view_ready_requires_radio_ready(tmp_path):
    sys = FakeSystem(unix_replies={
        "/tmp/loraconf433.sock": b"STATUS RADIO=READY TXMODE=MANAGED\n"}).system
    v = daemon_control.read_view(sys, "433")
    assert v.reachable and v.ready and v.radio_state == "READY"


@pytest.mark.parametrize("state", [b"FAILED", b"UNINITIALIZED"])
def test_reachable_but_not_ready_is_not_ready(tmp_path, state):
    sys = FakeSystem(unix_replies={
        "/tmp/loraconf433.sock": b"STATUS RADIO=" + state + b" TXMODE=MANAGED\n"}).system
    v = daemon_control.read_view(sys, "433")
    assert v.reachable and not v.ready and v.radio_state == state.decode()


def test_unreachable_is_not_ready(tmp_path):
    v = daemon_control.read_view(FakeSystem().system, "433")   # no reply -> unreachable
    assert not v.reachable and not v.ready and v.radio_state == ""


def test_apply_tx_mode_refuses_when_not_ready(tmp_path):
    svc = _svc(tmp_path, b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    ok, detail = svc._apply_tx_mode("433", "MANAGED")
    assert not ok and "not READY" in detail and "FAILED" in detail


def test_apply_cadidle_refuses_when_not_ready(tmp_path):
    svc = _svc(tmp_path, b"STATUS RADIO=UNINITIALIZED CADIDLE=250\n")
    ok, detail = svc._apply_conf_param("433", "CADIDLE", "0")
    assert not ok and "not READY" in detail


def test_verify_band_up_requires_ready(tmp_path):
    # autouse conftest sets DAEMON_VERIFY_TIMEOUT_S=0 -> single bounded check.
    assert _svc(tmp_path, b"STATUS RADIO=FAILED TXMODE=MANAGED\n")._verify_band_up("433") is False
    assert _svc(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED\n")._verify_band_up("433") is True


def test_failed_radio_blocks_dependent_launch(short_tmp_path, set_call):
    # meshcom depends on the daemon; a reachable-but-FAILED daemon must block the
    # dependent (no false success, no second daemon instance started).
    d = short_tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    d.mkdir(parents=True)
    (d / "loraham_daemon").write_text("#bin")
    svc = _svc(short_tmp_path, b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    set_call(svc)
    res = svc.start("meshcom", apply=True)
    assert not res.ok
    assert any("not READY" in dt or "RADIO=FAILED" in dt for dt in res.details)


@pytest.mark.contract
@pytest.mark.safety("RF-TX-opt-in")
def test_tx_test_refuses_when_radio_not_ready(tmp_path):
    # A TX test transmits real RF -> requires RADIO=READY, not mere reachability.
    d = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    d.mkdir(parents=True)
    (d / "loraham_daemon").write_text("#bin")
    svc = _svc(tmp_path, b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    # operator identity present so the block is on readiness, not identity
    from lhpc.core.config import save_operator_config
    save_operator_config(svc._paths, "XX0XXA")
    res = svc.test("daemon", tx=True, apply=True)
    assert not res.ok and ("READY" in res.summary or any("READY" in d for d in res.details))


@pytest.mark.safety("RF-TX-opt-in")
def test_tx_test_never_moves_a_client_to_another_band(tmp_path, set_call):
    # meshcom is a 433 stack; with only 868 served, its TX test refuses rather than transmitting on
    # 868. Only the daemon itself (no band of its own) falls back to the bands it is serving.
    sys = FakeSystem(unix_replies={"/tmp/loraconf868.sock": b"STATUS RADIO=READY TXMODE=MANAGED\n"}).system
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "uputronics-868"\n')
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    assert svc.active_bands() == ("868",)
    assert not svc.test("meshcom", tx=True, apply=False).ok
    assert svc.test("daemon", tx=True, apply=False).ok


@pytest.mark.safety("RF-TX-opt-in")
def test_tx_test_refused_on_a_band_another_stack_uses(tmp_path, set_call):
    # kiss (433 or 868) runs on 868 next to meshcom on 433: kiss's TX test must not transmit on
    # meshcom's band. Refused with meshcom named; the daemon's own TX test is unchanged.
    ready = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    sys = FakeSystem(cmdlines_data={300: ["meshcom-loraham-bridge"], 301: ["qemu-system-xtensa"],
                                    4242: ["loraham-kiss-tnc", "--config", "X"]},
                     unix_replies={"/tmp/loraconf433.sock": ready,
                                   "/tmp/loraconf868.sock": ready}).system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    assert svc._set_running_band("kiss", "868")
    res = svc.test("kiss", tx=True, apply=False)
    assert not res.ok and "meshcom" in res.summary
    assert svc.test("daemon", tx=True, apply=False).ok


def _competing_start(svc, monkeypatch, band):
    """A competing start of meshcore on `band`, run right AFTER each conflict check returns: it
    takes `claim.loraham.radio.<band>` like a real start, and once it has it, meshcore uses the
    band. Returns the list of starts that got the claim."""
    import threading

    from lhpc.core import reslock
    started: list = []

    def start():
        try:
            with reslock.operation_lock(svc._paths, f"claim.loraham.radio.{band}", "start", "meshcore"):
                started.append("meshcore")
        except reslock.ResourceBusy:
            pass

    def used(target, b):
        out = list(started) if b == band else []
        t = threading.Thread(target=start)
        t.start()
        t.join()
        return out
    monkeypatch.setattr(svc, "_band_used_by_others", used)
    return started


@pytest.mark.safety("RF-TX-opt-in")
def test_tx_test_holds_the_band_claim_against_a_competing_start(tmp_path, set_call, monkeypatch):
    # The conflict check and the transmit are one critical section under the band's claim: a start
    # that takes the band after the plan check is seen by the re-check, refused, nothing transmitted;
    # one attempted while the claim is held is refused.
    from lhpc.core.lifecycle import Lifecycle, TxTestResult
    sent = []
    monkeypatch.setattr(Lifecycle, "run_daemon_tx_test", lambda self, band, payload: (
        sent.append(band) or TxTestResult(ok=True, band=band, txok_before=0, txok_after=1,
                                          detail="stub")))
    ready = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    sys = FakeSystem(cmdlines_data={4242: ["loraham-kiss-tnc", "--config", "X"]},
                     unix_replies={"/tmp/loraconf868.sock": ready}).system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    assert svc._set_running_band("kiss", "868")
    started = _competing_start(svc, monkeypatch, "868")
    res = svc.test("kiss", tx=True, apply=True)
    assert not res.ok and "meshcore" in res.summary
    assert sent == []                                   # never transmitted over the start
    assert started == ["meshcore"]                      # the start under the held claim was refused


# --- D: dashboard state is truthful (occupied vs usable) ----------------------

def test_radio_conflict_ignores_a_stack_that_reaches_rf_through_another(tmp_path):
    """Sharing a band is not competing for it.

    graywolf transmits by handing frames to loraham-kiss-tnc, which owns the tuning — so the two
    running together is the NORMAL configuration, not a conflict. Reporting it red on the
    dashboard is both wrong and the kind of warning an operator learns to ignore. Two genuinely
    independent stacks on one band (chat + kiss) must still be flagged.
    """
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))

    def names(entries):
        return svc._radio_competitors(entries)

    gw = {"id": "graywolf", "name": "Graywolf APRS"}
    kiss = {"id": "kiss", "name": "LoRaHAM KISS TNC"}
    chat = {"id": "chat", "name": "LoRaHAM Chat"}

    assert names([kiss, gw]) == []                 # client + its provider: not a conflict
    assert names([gw]) == [] and names([]) == []   # nothing to fight over
    assert sorted(names([chat, kiss])) == ["LoRaHAM Chat", "LoRaHAM KISS TNC"]   # real rivals
    # A client does not mask a genuine rival sharing the same radio.
    assert "LoRaHAM Chat" in names([kiss, gw, chat])


def test_radio_overview_occupied_vs_usable(tmp_path):
    def _daemon(status):
        sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": status}).system
        ov = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path)).radio_overview()
        return next(r["daemon"] for r in ov if r["band"] == "433")

    d = _daemon(b"STATUS RADIO=READY TXMODE=MANAGED\n")
    assert d["usable"] and d["occupied"] and d["state_label"] == "usable"

    d = _daemon(b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    assert d["occupied"] and not d["usable"] and d["state_label"] == "occupied"

    d = _daemon(b"STATUS RADIO=UNINITIALIZED TXMODE=MANAGED\n")
    assert d["occupied"] and not d["usable"] and d["state_label"] == "occupied"

    off = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path)).radio_overview()
    d = next(r["daemon"] for r in off if r["band"] == "433")
    assert not d["occupied"] and not d["usable"] and d["state_label"] == "offline"


def test_radio_overview_reports_binary_installed_daemon_as_installed(tmp_path, monkeypatch):
    # Operator report: a daemon installed from the BINARY channel (source-LESS) and running fine
    # rendered "Daemon not installed" on the dashboard, because `installed` was derived from
    # source_state alone. `binary_covers` is the canonical "provided by a binary artifact"
    # primitive; a binary-covered daemon must read as installed even with no source tree.
    monkeypatch.setattr(ControllerService, "binary_covers",
                        lambda self, cid: cid == self.DAEMON_ID)
    ov = ControllerService(system=FakeSystem().system,
                           paths=Paths(runtime_root=tmp_path)).radio_overview()
    assert ov and all(r["daemon"]["installed"] is True for r in ov)


# --- #5: served/usable summaries exclude FAILED/UNINITIALIZED -----------------

def test_served_summary_excludes_failed(tmp_path):
    def _daemon(status):
        sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": status}).system
        ov = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path)).radio_overview()
        return next(r["daemon"] for r in ov if r["band"] == "433")

    d = _daemon(b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    assert "433" in d["occupied_bands"]                 # occupied (may hold SPI)
    assert "433" not in d["usable_bands"] and "433" not in d["served"]   # NOT usable/served

    d = _daemon(b"STATUS RADIO=READY TXMODE=MANAGED\n")
    assert "433" in d["occupied_bands"] and "433" in d["usable_bands"] and "433" in d["served"]

    off = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path)).radio_overview()
    d = next(r["daemon"] for r in off if r["band"] == "433")
    assert "433" not in d["occupied_bands"] and "433" not in d["usable_bands"]


def test_dash_signature_D_segment_excludes_failed(tmp_path):
    sys = FakeSystem(unix_replies={
        "/tmp/loraconf433.sock": b"STATUS RADIO=FAILED TXMODE=MANAGED\n"}).system
    sig = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path)).dash_signature()
    dseg = next(s for s in sig.split(";") if s.startswith("D:"))
    assert "433" not in dseg                            # a FAILED band is not "served"


def test_radio_not_ready_hint_names_a_running_hardware_claimant(tmp_path, monkeypatch):
    """A bare RADIO=FAILED gave the operator nothing to act on (live-found: a MeshCom start blocked
    on it). Name the running process that shares the radio hardware when there is one, else point
    at the power cycle a wedged front-end needs — a warm reboot does not clear it."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))

    monkeypatch.setattr(ControllerService, "build_snapshot",
                        lambda self, fresh=False: (_ for _ in ()).throw(RuntimeError("no snap")))
    hint = svc._radio_not_ready_hint("433")
    assert "POWER-CYCLE" in hint and "433" in hint          # nothing observable -> the real remedy

    class _St:
        from lhpc.core.model import RunState as _R
        run_state = _R.RUNNING
    class _Comp:
        from lhpc.core.model import ResourceClaim, ResourceKind
        id = "meshtastic"
        resources = (ResourceClaim(key="loraham.radio.868", kind=ResourceKind.RADIO_BAND),)
    class _Stack:
        components = (_Comp(),)
    class _SS:
        stack = _Stack()
        components = {"meshtastic": _St()}
    class _Snap:
        stacks = (_SS(),)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, fresh=False: _Snap())
    hint = svc._radio_not_ready_hint("433")
    assert "meshtastic" in hint and "shared SPI bus" in hint

    # DEGRADED means "running, but an endpoint/readiness is missing" — the process still holds the
    # radio, so it must be named too. Missing it sent the operator to power-cycle the Pi over a
    # process they could simply stop.
    from lhpc.core.model import RunState as _RS
    _St.run_state = _RS.DEGRADED
    hint = svc._radio_not_ready_hint("433")
    assert "meshtastic" in hint and "POWER-CYCLE" not in hint

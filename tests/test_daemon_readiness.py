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


def test_failed_radio_blocks_dependent_launch(tmp_path):
    # meshcom depends on the daemon; a reachable-but-FAILED daemon must block the
    # dependent (no false success, no second daemon instance started).
    d = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    d.mkdir(parents=True)
    (d / "loraham_daemon").write_text("#bin")
    svc = _svc(tmp_path, b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    res = svc.start("meshcom", apply=True)
    assert not res.ok
    assert any("not READY" in dt or "RADIO=FAILED" in dt for dt in res.details)


def test_tx_test_refuses_when_radio_not_ready(tmp_path):
    # A TX test transmits real RF -> requires RADIO=READY, not mere reachability.
    d = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    d.mkdir(parents=True)
    (d / "loraham_daemon").write_text("#bin")
    svc = _svc(tmp_path, b"STATUS RADIO=FAILED TXMODE=MANAGED\n")
    # operator identity present so the block is on readiness, not identity
    from lhpc.core.config import save_operator_config
    save_operator_config(svc._paths, "DJ0CHE", "")
    res = svc.test("daemon", tx=True, apply=True)
    assert not res.ok and ("READY" in res.summary or any("READY" in d for d in res.details))


# --- D: dashboard state is truthful (occupied vs usable) ----------------------

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

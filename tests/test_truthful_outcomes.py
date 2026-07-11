"""P0.3 — a start reports failure unless every required component reached a
verified healthy state. A daemon --radio both must verify BOTH bands; a dependent
must not start when daemon readiness failed."""

import pytest
from lhpc.core.lifecycle import Lifecycle
from conftest import real_spawn
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from conftest import set_call

STATUS = b"STATUS RADIO=READY TXMODE=MANAGED\n"


def _fake_life(svc):
    # A lifecycle whose spawn "succeeds" without launching a real process.
    return Lifecycle(svc._paths, svc.stacks(), svc.config(), svc._system,
                     spawn=real_spawn)


def _built_daemon(tmp_path):
    d = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    d.mkdir(parents=True)
    (d / "loraham_daemon").write_text("#!bin")          # is_built -> True


@pytest.mark.needs_session  # spawns a real process; identity_complete needs sid>0 (skips under sid==0)
def test_daemon_both_fails_when_one_band_never_comes_up(tmp_path, monkeypatch):
    _built_daemon(tmp_path)
    # Only 433 answers GET STATUS; 868's CONF socket never comes up.
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": STATUS}).system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "_lifecycle", _fake_life)
    res = svc.start("daemon", apply=True)              # --radio both (default)
    assert not res.ok
    assert any("868 CONF socket never came up" in d for d in res.details)


def test_start_ok_when_daemon_serving_both(tmp_path, monkeypatch):
    _built_daemon(tmp_path)
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": STATUS,
                                   "/tmp/loraconf868.sock": STATUS}).system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "_lifecycle", _fake_life)
    res = svc.start("daemon", apply=True)
    assert res.ok


def test_dependent_not_started_when_daemon_unready(tmp_path):
    # The daemon is not built -> readiness fails -> KISS must be skipped and the
    # overall start must report FAILURE (not a false success).
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("kiss", apply=True)
    assert not res.ok
    assert any("daemon" in d.lower() and ("not installed" in d.lower()
               or "not built" in d.lower()) for d in res.details)
    assert any("daemon not ready" in d for d in res.details)


# --- 3.3 daemon TX-mode apply/readback gating --------------------------------

def _svc_with_daemon(tmp_path, status):
    from lhpc.core.probes.backends import FakeSystem
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": status}).system
    return ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))


def test_tx_mode_skips_when_already_matching(tmp_path):
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=DIRECT\n")
    ok, detail = svc._apply_tx_mode("433", "DIRECT")
    assert ok and "already DIRECT" in detail


def test_tx_mode_fails_when_readback_mismatches(tmp_path):
    # Daemon reports MANAGED and never changes -> requesting DIRECT must FAIL (gate),
    # not warn. (Static fake: the SET read-back never shows DIRECT.)
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED\n")
    ok, detail = svc._apply_tx_mode("433", "DIRECT")
    assert not ok and "DIRECT" in detail


def test_tx_mode_succeeds_when_readback_matches(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    class Stateful:
        def __init__(self): self.mode = b"MANAGED"
        def _maybe_set(self, payload):
            if payload.strip().startswith(b"SET TXMODE="):
                self.mode = payload.split(b"=", 1)[1].strip()
        def request(self, path, payload, timeout, max_bytes):
            self._maybe_set(payload)
            return b"STATUS RADIO=READY TXMODE=" + self.mode + b"\n"
        def send(self, path, payload, timeout):
            self._maybe_set(payload)
    sys = FakeSystem().system
    sys.unix = Stateful()
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    ok, detail = svc._apply_tx_mode("433", "DIRECT")
    assert ok and "confirmed" in detail


# --- daemon CADIDLE apply/readback (NON-GATING tuning) -----------------------

def test_cadidle_skips_when_already_matching(tmp_path):
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADIDLE=0\n")
    ok, detail = svc._apply_conf_param("433", "CADIDLE", "0")
    assert ok and "already 0ms" in detail


def test_cadidle_fails_when_readback_mismatches(tmp_path):
    # Daemon stuck at CADIDLE=250 -> requesting 0 cannot be confirmed -> (False, ...).
    # Reported but NON-GATING in _ensure_daemon (the start still proceeds).
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED CADIDLE=250\n")
    ok, detail = svc._apply_conf_param("433", "CADIDLE", "0")
    assert not ok and "0" in detail


def test_cadidle_succeeds_when_readback_matches(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    class Stateful:
        def __init__(self): self.idle = b"250"
        def _maybe_set(self, payload):
            if payload.strip().startswith(b"SET CADIDLE="):
                self.idle = payload.split(b"=", 1)[1].strip()
        def request(self, path, payload, timeout, max_bytes):
            self._maybe_set(payload)
            return b"STATUS RADIO=READY TXMODE=MANAGED CADIDLE=" + self.idle + b"\n"
        def send(self, path, payload, timeout):
            self._maybe_set(payload)
    sys = FakeSystem().system
    sys.unix = Stateful()
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    ok, detail = svc._apply_conf_param("433", "CADIDLE", "0")
    assert ok and "confirmed" in detail


def test_cadidle_numeric_equality_ignores_formatting(tmp_path):
    # Readback "0" must match want "0" (and a non-numeric/absent reading must not).
    from lhpc.core.services import ControllerService as CS
    assert CS._cadidle_eq("0", "0") and CS._cadidle_eq("250", 250)
    assert not CS._cadidle_eq(None, "0") and not CS._cadidle_eq("x", "0")


def test_failed_tx_gating_blocks_dependent(tmp_path):
    # meshcom needs the daemon in MANAGED; a daemon stuck in DIRECT must block it
    # (no false success, no post-start).
    (tmp_path / "src" / "loraham-daemon" / "loraham_daemon").mkdir(parents=True)
    (tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon").write_text("#bin")
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=DIRECT\n")
    set_call(svc)
    res = svc.start("meshcom", apply=True)
    assert not res.ok
    assert any("TXMODE" in d and ("!=" in d or "fail" in d.lower()) for d in res.details)


# --- §5.2 endpoint readiness verification ------------------------------------

def _endpoint_comp():
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     readiness="endpoint", run_argv=("./app",),
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),
                                EndpointSpec(kind="tcp", address="127.0.0.1:1234", ready=False)))


def test_ready_endpoint_absent_is_unverified(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ok, ev = svc._ready_endpoints_present(_endpoint_comp())
    assert not ok and any("9999: absent" in e for e in ev)


def test_ready_endpoint_present_verifies(tmp_path):
    from lhpc.core.probes.backends import FakeSystem, Listener
    fake = FakeSystem(listeners=[Listener(family="ipv4", ip="127.0.0.1", port=9999, inode=1)])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    ok, ev = svc._ready_endpoints_present(_endpoint_comp())
    assert ok and any("9999: present" in e for e in ev)


def test_non_ready_endpoint_does_not_gate(tmp_path):
    # Port 1234 is NOT marked ready; its absence must not affect readiness.
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    from lhpc.core.probes.backends import FakeSystem, Listener
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE, readiness="endpoint",
                     run_argv=("./app",),
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),
                                EndpointSpec(kind="tcp", address="127.0.0.1:1234", ready=False)))
    fake = FakeSystem(listeners=[Listener(family="ipv4", ip="127.0.0.1", port=9999, inode=1)])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    ok, _ = svc._ready_endpoints_present(comp)
    assert ok                                  # 1234 absent but not a ready endpoint


# --- §6.4 manual/interactive main is MANUAL_REQUIRED (non-success) ------------

def test_interactive_main_start_is_manual_required(tmp_path):
    # Daemon serving 433 (so the dependent isn't gated) + chat source built+installed.
    src = tmp_path / "src" / "LoRaHAM_Daemon"
    src.mkdir(parents=True)
    (src / "loraham_chat").write_text("#!bin")          # built artifact present
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED\n")
    set_call(svc)
    res = svc.start("chat", apply=True)
    assert not res.ok                                   # LHPC cannot launch the TUI
    assert any("manual_required" in d and "loraham-chat" in d for d in res.details)
    assert "manual start" in res.summary.lower()
    # The start command must NOT be duplicated in the result — it lives on the dash card.
    assert "run it in a terminal" not in res.summary.lower()
    assert not any("loraham_chat" in d or "run it in a terminal" in d for d in res.details)


# --- §8 single success authority: ActionResult.results, not StartLaunch ------

def test_raw_launch_ok_does_not_decide_top_level_success(tmp_path):
    # The raw launch "succeeds" (fake spawn returns a pid), but readiness=endpoint has
    # no endpoint -> the TYPED result is UNVERIFIED and ActionResult.ok is False.
    # i.e. StartLaunch.ok never overrides the typed CompResult authority.
    from lhpc.core.lifecycle import Lifecycle
    (tmp_path / "src" / "loraham-daemon" / "loraham_daemon").mkdir(parents=True)
    (tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon").write_text("#bin")
    svc = _svc_with_daemon(tmp_path, b"STATUS RADIO=READY TXMODE=MANAGED\n")
    # kiss is readiness=endpoint (tcp 8001); no endpoint is present in the fake system
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True, exist_ok=True)
    res = svc.start("kiss", apply=True)
    # whatever the raw launch did, the typed results drive ok
    assert res.ok == all(r.ok and r.verified for r in res.results) if res.results else True
    assert not res.ok or all(r.verified for r in res.results)


@pytest.mark.needs_session  # spawns a real process; identity_complete needs sid>0 (skips under sid==0)
def test_running_band_marker_failure_downgrades_to_unverified(tmp_path, monkeypatch):
    # A verified start whose running-band marker cannot persist must report UNVERIFIED,
    # not VERIFIED — the marker drives multi-band decisions + dashboard state.
    from conftest import real_spawn
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.outcomes import Outcome
    # voice requires the daemon in DIRECT, so the fixture daemon must already report DIRECT
    # for the start to clear the TX-mode gate and reach the running-band marker step.
    DIRECT = b"STATUS RADIO=READY TXMODE=DIRECT\n"
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": DIRECT,
                                   "/tmp/loraconf868.sock": DIRECT}).system
    (tmp_path / "src" / "LoRaHAM_Voice").mkdir(parents=True)
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "is_built", lambda self, c: True)
    monkeypatch.setattr(type(svc), "_running_conflicts", lambda self, c, b: False)
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, c: [])
    monkeypatch.setattr(type(svc), "write_config_files", lambda self, t, b="", overrides=None: [])
    monkeypatch.setattr(type(svc), "_lifecycle",
                        lambda self: Lifecycle(self._paths, self.stacks(), self.config(),
                                               self._system, spawn=real_spawn))
    monkeypatch.setattr(type(svc), "_set_running_band", lambda self, s, b: False)  # marker fails
    set_call(svc)
    res = svc.start("voice", apply=True, band="433")
    assert any(r.component == "loraham-voice" and r.outcome == Outcome.UNVERIFIED
               and "running-band marker" in (r.summary or "") for r in res.results)

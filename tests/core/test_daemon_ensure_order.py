"""T-I5c at L3 (A §1 I5; PROOF §1): `_ensure_daemon` launches the radios' daemons ONE AT A TIME and
waits for each to be READY or failed before the next; a radio that fails (launch or verify) does
not stop the others: each is reported, `ok_all` is False. At L3 a radio is a band. The fakes stand
in for the launch (`Lifecycle.start`) and the CONF socket (read and verify); the order is the one
the code calls them in. Since item 16 a radio's daemon is verified on its own socket key, its rid."""
import pytest

from lhpc.core import daemon_control, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


class _Life:
    def __init__(self, calls, fail):
        self.calls, self.fail = calls, fail

    def start(self, stack, comp, params, band="", **kw):
        self.calls.append(("launch", band))

        class R:
            ok = band not in self.fail
            detail = "exit 4"
        return R()


@pytest.fixture
def ensure(tmp_path, monkeypatch):
    # Stage L5: each band's daemon is its radio's; the hardware list holds one radio per band (r1 on 433, r2 on 868).
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    calls = []
    down = daemon_control.read_view(svc._system, "433")                 # unreachable: absent
    monkeypatch.setattr(daemon_control, "read_view", lambda system, key: down)
    monkeypatch.setattr(radio_start, "verify_identity", lambda svc, rid: "")      # ID=/BOUND= hold (tested apart)
    for name, value in (("hardware_configured", lambda self: True),
                        ("_daemon_arbitrated_bands", lambda self, radio: (["433", "868"], {})),
                        ("_source_present", lambda self, comp: True),
                        ("binary_behind", lambda self, comp: None),
                        ("is_built", lambda self, comp: True),
                        ("_apply_stack_daemon_params", lambda self, sid, b, rid="": ([], True, ""))):
        monkeypatch.setattr(ControllerService, name, value)
    monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: [])                 # no daemon process holds a radio

    def run(fail_launch=(), fail_verify=()):
        def verify(self, b):
            calls.append(("verify", b))
            return b not in fail_verify
        monkeypatch.setattr(ControllerService, "_verify_band_up", verify)
        stk = svc.stack(svc.stack_of(svc.DAEMON_ID))
        lines, ok, _gate = svc._ensure_daemon(_Life(calls, set(fail_launch)), stk, stk.main_component,
                                              None, "", "kiss")
        return calls, lines, ok
    return run


def test_each_daemon_is_verified_before_the_next_is_launched(ensure):
    calls, _lines, ok = ensure()
    assert calls == [("launch", "433"), ("verify", "r1"), ("launch", "868"), ("verify", "r2")]
    assert ok


def test_a_failed_launch_does_not_stop_the_next_radio(ensure):
    # A behaviour change (I11): today the first failed launch returned at once.
    calls, lines, ok = ensure(fail_launch={"433"})
    assert ("launch", "868") in calls and ("verify", "r2") in calls
    assert not ok and any("start daemon r1 --radio 433" in ln and "[fail]" in ln for ln in lines)


def test_a_failed_verify_does_not_stop_the_next_radio(ensure):
    calls, lines, ok = ensure(fail_verify={"r1"})
    assert calls == [("launch", "433"), ("verify", "r1"), ("launch", "868"), ("verify", "r2")]
    assert not ok and any("433 CONF socket never came up" in ln for ln in lines)


@pytest.mark.parametrize("reply, refused", [
    (b"STATUS RADIO=READY ID=r1 BOUND=-\n", ""),
    (b"STATUS RADIO=READY ID=r2 BOUND=-\n", "wrong daemon on this socket: expected r1, got r2"),
    (b"STATUS RADIO=READY BOUND=-\n", "wrong daemon on this socket: expected r1, got missing"),
    (b"STATUS RADIO=READY ID=r1 BOUND=port:1-1.3\n", "wrong stick at start: expected -, got port:1-1.3"),
])
def test_a_started_daemon_must_name_its_radio_and_binding(tmp_path, reply, refused):
    # W3-7 / A:170-176: after READY the radio's own socket must report ID=<rid> and its BOUND=; else the start fails.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "radios.toml").write_text(radios.render((radios.Radio("r1", "low", "loraham", "433"),)))
    svc = ControllerService(system=FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": reply}).system,
                            paths=Paths(runtime_root=tmp_path))
    assert radio_start.verify_identity(svc, "r1") == refused


def test_a_radio_whose_socket_names_another_daemon_fails_its_start(ensure, monkeypatch):
    # W3-7: after READY the start checks ID=/BOUND=; a mismatch fails that radio (not signalled), the next still starts.
    monkeypatch.setattr(radio_start, "verify_identity", lambda svc, rid: "wrong daemon on this socket" if rid == "r1" else "")
    calls, lines, ok = ensure()
    assert not ok and any("[fail] daemon r1: wrong daemon on this socket" in ln for ln in lines)
    assert ("launch", "868") in calls


def test_a_refused_start_plan_launches_nothing_for_that_radio(ensure, monkeypatch):
    # The start plan's refusal (e.g. the radio's daemon already runs elsewhere) blocks that radio's launch.
    real = radio_start.start_plan
    monkeypatch.setattr(radio_start, "start_plan", lambda svc, rid: (real(svc, rid)[0], "already runs elsewhere", ())
                        if rid == "r1" else real(svc, rid))
    calls, lines, ok = ensure()
    assert ("launch", "433") not in calls and ("launch", "868") in calls
    assert not ok and any("[BLOCKED] daemon on 433: already runs elsewhere" in ln for ln in lines)

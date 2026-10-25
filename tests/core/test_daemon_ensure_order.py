"""T-I5c at L3 (A §1 I5; PROOF §1): `_ensure_daemon` launches the radios' daemons ONE AT A TIME and
waits for each to be READY or failed before the next; a radio that fails (launch or verify) does
not stop the others: each is reported, `ok_all` is False. At L3 a radio is a band. The fakes stand
in for the launch (`Lifecycle.start`) and the CONF socket (read and verify); the order is the one
the code calls them in."""
import pytest

from lhpc.core import daemon_control
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
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    calls = []
    down = daemon_control.read_view(svc._system, "433")                 # unreachable: absent
    monkeypatch.setattr(daemon_control, "read_view", lambda system, b: down)
    for name, value in (("hardware_configured", lambda self: True),
                        ("_daemon_arbitrated_bands", lambda self, radio: (["433", "868"], {})),
                        ("_daemon_claimed_bands", lambda self: set()),
                        ("_source_present", lambda self, comp: True),
                        ("binary_behind", lambda self, comp: None),
                        ("is_built", lambda self, comp: True),
                        ("_apply_stack_daemon_params", lambda self, sid, b: ([], True, ""))):
        monkeypatch.setattr(ControllerService, name, value)

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
    assert calls == [("launch", "433"), ("verify", "433"), ("launch", "868"), ("verify", "868")]
    assert ok


def test_a_failed_launch_does_not_stop_the_next_radio(ensure):
    # A behaviour change (I11): today the first failed launch returned at once.
    calls, lines, ok = ensure(fail_launch={"433"})
    assert ("launch", "868") in calls and ("verify", "868") in calls
    assert not ok and any("start daemon --radio 433" in ln and "[fail]" in ln for ln in lines)


def test_a_failed_verify_does_not_stop_the_next_radio(ensure):
    calls, lines, ok = ensure(fail_verify={"433"})
    assert calls == [("launch", "433"), ("verify", "433"), ("launch", "868"), ("verify", "868")]
    assert not ok and any("433 CONF socket never came up" in ln for ln in lines)

"""`lhpc doctor` names the clock gate's verdict — the PKI refusal text points the operator to it."""
import pytest

from lhpc.core import service_system
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _clock_line(tmp_path, monkeypatch, state):
    monkeypatch.setattr(service_system, "read_kernel_time_state", lambda: state)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return [d for d in svc.doctor().details if d.strip().startswith("clock:")]


def test_doctor_says_verified_on_a_synchronised_clock(tmp_path, monkeypatch):
    assert _clock_line(tmp_path, monkeypatch, {"synced": True, "maxerror_us": 1000}) == [
        "  clock: verified (certificates may be issued)"]


@pytest.mark.parametrize("state, reason", [
    ({"synced": False, "maxerror_us": 1000}, "not synchronised"),
    ({"synced": True, "maxerror_us": 5_000_000}, "estimated error is 5.0 s"),
    (None, "kernel time state unavailable"),
])
def test_doctor_names_why_the_clock_is_not_verified(tmp_path, monkeypatch, state, reason):
    line = _clock_line(tmp_path, monkeypatch, state)
    assert len(line) == 1 and "NOT verified" in line[0] and reason in line[0]
    assert "--accept-unverified-clock" in line[0]


def test_the_refusal_s_pointer_to_doctor_is_true(tmp_path, monkeypatch):
    # The refusal text says "see `lhpc doctor`"; doctor must then name the same reason.
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": False, "maxerror_us": 1000})
    ok, why = service_system.clock_verified(FakeSystem().system.fs, tmp_path)
    assert "lhpc doctor" in service_system.clock_refusal(why, "x")
    assert why in _clock_line(tmp_path, monkeypatch, {"synced": False, "maxerror_us": 1000})[0]

"""The clock policy decides from its declared inputs alone: the kernel's sync state and `now`.

No controller, no System, no runtime root: `lhpc.core.clock.verdict` is given everything it needs,
and the kernel probe is made to raise so a hidden read would fail the test. The reason strings are
the operator's text (they reach `lhpc doctor` and every PKI refusal), so they are compared exactly.
"""
import subprocess
import sys

import pytest

from lhpc.core import clock, service_system

_SYNCED = {"synced": True, "maxerror_us": 1000}
_FLOOR = 1_735_689_600.0                              # 2025-01-01T00:00:00Z


@pytest.fixture(autouse=True)
def _probe_must_not_be_read(monkeypatch):
    def boom():
        raise AssertionError("the policy read the kernel itself")
    monkeypatch.setattr(service_system, "read_kernel_time_state", boom)


@pytest.mark.parametrize("kernel, now, expected", [
    (_SYNCED, _FLOOR, (True, "")),
    ({"synced": True, "maxerror_us": 1_000_000}, _FLOOR + 1, (True, "")),
    (None, _FLOOR + 1,
     (False, "kernel time state unavailable — cannot tell whether the clock is synchronised")),
    ({"synced": False, "maxerror_us": 1000}, _FLOOR + 1,
     (False, "the clock is not synchronised (no time source has set it yet)")),
    ({"synced": True, "maxerror_us": 1_000_001}, _FLOOR + 1,
     (False, "the clock's estimated error is 1.0 s, above the 1 s this needs")),
    (_SYNCED, _FLOOR - 1,
     (False, "the clock reads 2024-12-31 23:59:59Z, before the earliest date this software can "
             "plausibly run")),
    (_SYNCED, 0.0,
     (False, "the clock reads 1970-01-01 00:00:00Z, before the earliest date this software can "
             "plausibly run")),
])
def test_the_verdict_is_a_function_of_kernel_state_and_now(kernel, now, expected):
    assert clock.verdict(kernel, now) == expected


def test_the_floor_and_the_refusal_are_the_clock_module_s():
    assert clock.PKI_NOT_BEFORE == _FLOOR
    assert clock.clock_refusal("why", "renew the server certificate") == (
        "refusing to renew the server certificate: why. Nothing was changed. "
        "Fix the clock (see `lhpc doctor`), or accept the risk with --accept-unverified-clock.")


def test_pki_loads_without_the_dashboard_metrics_module():
    # pki needs the clock floor, not host metrics: with service_system unimportable, pki still loads.
    code = ("import sys; sys.modules['lhpc.core.service_system'] = None; "
            "import lhpc.core.pki as p; print(p.PROVISIONAL_NOT_BEFORE.isoformat())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "2025-01-01T00:00:00+00:00"


_NO_KERNEL = "kernel time state unavailable — cannot tell whether the clock is synchronised"
_UNSYNCED = "the clock is not synchronised (no time source has set it yet)"
_TOO_WIDE = "the clock's estimated error is 2.0 s, above the 1 s this needs"


def test_the_clock_is_read_only_after_the_kernel_checks(monkeypatch):
    # The order the gate had before the move: a refused kernel state never reads the clock, so a
    # realtime crossing the floor in between cannot turn a refusal into a pass. A clock that raises
    # proves no read happened; on the passing path the clock's value decides.
    def no_clock():
        raise AssertionError("the clock was read before the kernel checks")
    monkeypatch.setattr(clock.time, "time", no_clock)
    for kernel, why in ((None, _NO_KERNEL),
                        ({"synced": False, "maxerror_us": 1000}, _UNSYNCED),
                        ({"synced": True, "maxerror_us": 2_000_000}, _TOO_WIDE)):
        assert clock.verdict(kernel, None) == (False, why)
        monkeypatch.setattr(service_system, "read_kernel_time_state", lambda k=kernel: k)
        assert service_system.clock_verified(None, None) == (False, why)
    monkeypatch.setattr(clock.time, "time", lambda: _FLOOR - 1)
    assert clock.verdict(_SYNCED, None)[0] is False
    monkeypatch.setattr(clock.time, "time", lambda: _FLOOR)
    assert clock.verdict(_SYNCED, None) == (True, "")

"""M19: the console's periodic pass tries every start log against its cap (`svc.cap_start_logs`).

The pass is the only place outside a start that caps: a stack that runs for weeks is started once, so
without it its start log would grow until the next start. The service method never raises; the pass
still wraps it like every other maintenance unit, so a broken cap can never starve the CRL heal or
the AP tick."""
from __future__ import annotations

import logging

import pytest

from lhpc.adapters.web.app import network_watch_pass


_UNITS = ["apply-complete", "crl-heal", "clock-normalise", "disk", "cap", "cap-controller", "roll-trace"]


def _ran_every_unit_once(calls, *, tick):
    """Every maintenance unit ran exactly once (their order among themselves is not the contract),
    and on an AP box the start-log cap ran before the AP tick."""
    assert sorted(calls) == sorted(_UNITS + (["tick"] if tick else [])), calls
    if tick:
        assert calls.index("cap") < calls.index("tick"), calls


def _paths(root):
    from lhpc.core.paths import Paths
    (root / "state").mkdir(exist_ok=True)
    return Paths(runtime_root=root)


class _Svc:
    """Only what `network_watch_pass` calls; records the order of the units."""

    def __init__(self, paths, *, ap_box=False, cap_result=None, cap_raises=False, raises=()):
        self._paths = paths                       # where the maintenance pass records its outcomes
        self.calls: list[str] = []
        self.ap_box = ap_box
        self.cap_result = {} if cap_result is None else cap_result
        self.cap_raises = cap_raises
        self.raises = set(raises)                 # units that raise (A1: one never skips another)

    def _unit(self, name):
        self.calls.append(name)
        if name in self.raises:
            raise RuntimeError(f"{name} exploded")

    def disk_health(self):
        self._unit("disk")
        return []

    def cap_controller_logs(self):
        self._unit("cap-controller")
        return {}

    def rflog_roll_native_all(self):
        self._unit("roll-trace")

    def webserver_apply_complete_pending(self):
        self.calls.append("apply-complete")

    def crl_refresh_if_expired(self):
        self.calls.append("crl-heal")

    def pki_clock_normalise(self):
        self.calls.append("clock-normalise")

    def cap_start_logs(self):
        self.calls.append("cap")
        if self.cap_raises:
            raise RuntimeError("cap exploded")
        return self.cap_result

    def network_supported(self):
        return self.ap_box

    def webserver_apply_pending(self):
        return False

    def pki_normalise_pending(self):
        return False

    def _network_watch_tick(self):
        self.calls.append("tick")


def test_the_pass_caps_start_logs_on_every_box_before_the_ap_tick(tmp_path):
    non_ap = _Svc(_paths(tmp_path), ap_box=False)
    assert network_watch_pass(non_ap) == 300.0
    _ran_every_unit_once(non_ap.calls, tick=False)
    ap = _Svc(_paths(tmp_path), ap_box=True)
    assert network_watch_pass(ap) == 60.0
    _ran_every_unit_once(ap.calls, tick=True)


def test_a_failing_cap_never_breaks_the_pass(tmp_path):
    svc = _Svc(_paths(tmp_path), ap_box=True, cap_raises=True)
    assert network_watch_pass(svc) == 60.0
    _ran_every_unit_once(svc.calls, tick=True)


def test_a_malformed_cap_result_never_breaks_the_pass(tmp_path):
    svc = _Svc(_paths(tmp_path), ap_box=True)
    svc.cap_result = None                       # not a dict: `.items()` raises inside the unit
    assert network_watch_pass(svc) == 60.0
    assert svc.calls[-1] == "tick"


def test_only_a_log_that_could_not_be_capped_is_logged(caplog, tmp_path):
    svc = _Svc(_paths(tmp_path), cap_result={"start-a.log": "capped", "start-b.log": "error: OSError",
                           "start-c.log": "busy", "start-d.log": "below", "start-e.log": "absent"})
    with caplog.at_level(logging.WARNING, logger="lhpc.adapters.web.app"):
        network_watch_pass(svc)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    # S5: one line per failed maintenance task, naming each log that could not be capped.
    assert warnings == ["maintenance start-logs failed: start-b.log: error: OSError"]


def test_serving_the_console_never_caps(web, tmp_path):
    """Only the console's watch loop (started by `run_server`) runs the pass. Building the app and
    answering requests must not cap: with the watch loop not running, nothing happens and nothing
    breaks."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    capped: list[int] = []
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.cap_start_logs = lambda: capped.append(1) or {}
    client = web(service_factory=lambda: svc)
    assert client.get("/").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert capped == []


@pytest.mark.parametrize("bad", ["disk", "cap-controller", "roll-trace"])
def test_one_failing_log_unit_does_not_skip_the_others(bad, tmp_path):
    # A1: the disk level, the start-log cap, the controller-log cap and the trace roll each have their
    # own try; any one raising still runs every other unit and the AP tick.
    svc = _Svc(_paths(tmp_path), ap_box=True, raises={bad})
    assert network_watch_pass(svc) == 60.0
    _ran_every_unit_once(svc.calls, tick=True)


def test_only_a_controller_log_that_could_not_be_capped_is_logged(caplog, tmp_path):
    svc = _Svc(_paths(tmp_path))
    svc.cap_controller_logs = lambda: {"lhpc-web.log": "capped", "nginx-access.log": "error: OSError"}
    with caplog.at_level(logging.WARNING, logger="lhpc.adapters.web.app"):
        network_watch_pass(svc)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == ["maintenance controller-logs failed: nginx-access.log: error: OSError"]

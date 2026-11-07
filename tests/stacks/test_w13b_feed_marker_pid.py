"""A GPS feed's readiness marker with a pid outside Linux's range must not break the status page (found by item 13's
seeded fuzz).

`gps.marker_owner_pid` accepted any positive int, and the status probe and the start gate ask the kernel about it
(`os.kill(pid, 0)`): a pid of 2**31 or more raised OverflowError on every status GET while that marker existed. A pid
above the kernel's maximum (2**22, PID_MAX_LIMIT) names no process, so it is no usable owner: the feed reads
"no usable owner pid", like a marker without one.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, pid):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    d = tmp_path / "state" / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text(json.dumps({"state": "connected", "detail": "", "sentences": 1, "fixes": 0,
                                                  "updated": int(time.time()), "pid": pid}))
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


@pytest.mark.parametrize("pid", [2**31, 2**63, 10**30, 2**22 + 1], ids=["2^31", "2^63", "10^30", "control-pid_max+1"])
def test_r1_the_status_page_survives_a_marker_with_an_impossible_pid(web, tmp_path, pid):
    """RED before for 2^31 and above: GET raised OverflowError (500)."""
    svc = _svc(tmp_path, pid)
    r = web(service_factory=lambda: svc).get("/stacks/meshtastic/body")
    assert r.status_code == 200


@pytest.mark.parametrize("pid", [2**31, 2**63, 2**22 + 1], ids=["2^31", "2^63", "pid_max+1"])
def test_r2_the_start_gate_and_the_status_read_it_as_no_usable_owner(tmp_path, pid):
    from lhpc.core.status import StatusProber
    svc = _svc(tmp_path, pid)
    comp = next(c for c in svc.stack("meshtastic").components if c.id == "meshtastic-gps")
    assert svc.gps_feed_state(comp) == {}                       # the start gate: not this run's marker
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))
    assert prober._gps_feed_ready(comp) == (False, "readiness marker has no usable owner pid")


def test_r3_control_the_kernel_maximum_itself_is_a_pid():
    from lhpc.core import gps
    assert gps.marker_owner_pid({"pid": 2**22}) == 2**22 and gps.marker_owner_pid({"pid": os.getpid()}) == os.getpid()


def test_r4_control_pid_0_is_no_owner():
    """os.kill(0, 0) signals the caller's own process group and would read "alive"; 0 names no feed."""
    from lhpc.core import gps
    assert gps.marker_owner_pid({"pid": 0}) is None

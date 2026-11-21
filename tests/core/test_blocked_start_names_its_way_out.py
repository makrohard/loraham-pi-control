"""Q6b: a start blocked by a running holder shows the conflict sentence in full and its way out ("stop daemon@r4" or
"use another radio"), never the bare "resource conflict". Box H: r2 loraham 868 (SPI), r4 PineDio 868 (USB)."""
from __future__ import annotations

import subprocess
import sys

import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _rns_r2_vs_daemon_on_r4(tmp_path, record: bool):
    """rns@r2's conflicts while daemon@r4 (868) and daemon@r1 (433) run; `record`: both by their launch records."""
    radios_fixture.write(tmp_path, radios_fixture.BOX_H)
    procs = {rid: subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
             for rid in ("r1", "r4")}
    try:
        table = {procs["r1"].pid: ["loraham_daemon", "--radio", "433", "--id", "r1"],
                 procs["r4"].pid: ["loraham_daemon", "--radio", "868", "--id", "r4"]}
        svc = ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("daemon")
        for rid, band in (("r1", "433"), ("r4", "868")) if record else ():
            assert svc._lifecycle().record_launch(stk, stk.component("loraham-daemon"), procs[rid].pid, band=band,
                                                  extra={"rid": rid})
        inst = instances.copy_of(svc, "reticulum", "r2")[0]
        return svc._running_conflicts(svc.stack("reticulum").component("rns"), "868", inst)
    finally:
        for p in procs.values():
            p.kill()
            p.wait(timeout=5)


def test_a_radio_conflict_names_the_holders_instance_and_another_radio(tmp_path):
    # red-before (268d1378): the sentence alone, no way out; the start's line said only "resource conflict". Only the
    # holder on the conflicting band is named (daemon@r1 serves 433, its stop would not free 868).
    assert _rns_r2_vs_daemon_on_r4(tmp_path, record=True) == [
        "loraham-daemon (provider) vs rns (exclusive) on loraham.radio.868 (radios r2, r4) — both running; "
        "stop daemon@r4 or use another radio"]


def test_a_holder_without_a_launch_record_is_named_with_rid_to_fill_in(tmp_path):
    # never a guess (G1-C60-RE): a hand-started daemon's radio is not known by its record
    assert _rns_r2_vs_daemon_on_r4(tmp_path, record=False) == [
        "loraham-daemon (provider) vs rns (exclusive) on loraham.radio.868 (radios r2, r4) — both running; "
        "stop daemon@<rid> or use another radio"]

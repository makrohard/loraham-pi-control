"""Q6b: a start blocked by a running holder shows the conflict sentence in full and its way out ("stop daemon@r4" or
"use another radio"), never the bare "resource conflict". Box H: r1 loraham 433 (SPI), r2 loraham 868 (SPI), r4
PineDio 868 (USB). The holder is on rns@r2's own radio, or not told by a record or --id (Q6c: a holder told on another
radio of the band does not block, tests/core/test_per_radio_in_run_check.py)."""
from __future__ import annotations

import subprocess
import sys

import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _rns_r2_vs(tmp_path, daemons, record: bool):
    """rns@r2's conflicts while `daemons` ((argv, rid, band) each) run; `record`: each by its launch record."""
    radios_fixture.write(tmp_path, radios_fixture.BOX_H)
    procs = [subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
             for _ in daemons]
    try:
        table = {p.pid: argv for p, (argv, _r, _b) in zip(procs, daemons)}
        svc = ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("daemon")
        for p, (_a, rid, band) in zip(procs, daemons) if record else ():
            assert svc._lifecycle().record_launch(stk, stk.component("loraham-daemon"), p.pid, band=band,
                                                  extra={"rid": rid})
        inst = instances.copy_of(svc, "reticulum", "r2")[0]
        return svc._running_conflicts(svc.stack("reticulum").component("rns"), "868", inst)
    finally:
        for p in procs:
            p.kill()
            p.wait(timeout=5)


def _d(rid, band):
    return ["loraham_daemon", "--radio", band, "--id", rid], rid, band


def test_a_radio_conflict_names_the_holders_instance_and_another_radio(tmp_path):
    # red-before (268d1378): the sentence alone, no way out; the start's line said only "resource conflict". Only the
    # holder on the conflicting band is named (daemon@r1 serves 433, its stop would not free 868).
    assert _rns_r2_vs(tmp_path, [_d("r1", "433"), _d("r2", "868")], record=True) == [
        "loraham-daemon (provider) vs rns (exclusive) on loraham.radio.868 (radios r2, r4) — both running; "
        "stop daemon@r2 or use another radio"]


def test_a_holder_without_a_launch_record_is_named_with_rid_to_fill_in(tmp_path):
    # never a guess (G1-C60-RE): a hand-started daemon without --id and without a record holds the whole band
    assert _rns_r2_vs(tmp_path, [(["loraham_daemon", "--radio", "868"], "r4", "868")], record=False) == [
        "loraham-daemon (provider) vs rns (exclusive) on loraham.radio.868 (radios r2, r4) — both running; "
        "stop daemon@<rid> or use another radio"]

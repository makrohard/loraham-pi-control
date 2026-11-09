"""G1-K4B 3 and 6: a running instance is its verified launch record's (13.16), whatever the hardware list says now.
A radio that left the list still addresses its running instance for a stop (a start refuses on the why), and the
band a running stack holds is the one it was launched on, not an edited row's."""
import subprocess
import sys

import radios_fixture

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

R1, R3 = radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1")


def _running_kiss_on_r3(tmp_path):
    radios_fixture.write(tmp_path, (R1, R3))
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    kiss = svc.stack("kiss")
    assert svc._lifecycle().record_launch(kiss, kiss.component("loraham-kiss-tnc"), p.pid, band="433",
                                          extra={"rid": "r3"})
    return p


def test_a_running_instance_whose_radio_left_the_list_is_still_stopped_by_name(tmp_path):
    p = _running_kiss_on_r3(tmp_path)
    try:
        radios_fixture.write(tmp_path, (R1,))                       # r3 removed while kiss@r3 runs
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        _sid, inst, band, why = instances.parse_target(svc, "kiss@r3", "stop")
        assert (inst.rid, band, why) == ("r3", "433", "kiss@r3: r3 is not in the hardware list"), (inst, band, why)
        res = svc.stop("kiss", apply=True, inst=inst)
        assert res.ok, (res.summary, res.details)
        assert p.wait(timeout=10) is not None
        assert instances.parse_target(svc, "kiss@r3", "start")[1] is None       # nothing to start there
    finally:
        p.kill()
        p.wait(timeout=5)


def test_a_running_stacks_band_is_its_launch_band_not_an_edited_row(tmp_path):
    import dataclasses
    p = _running_kiss_on_r3(tmp_path)
    try:
        radios_fixture.write(tmp_path, (R1, dataclasses.replace(R3, band="868")))   # r3's row edited to 868
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        assert instances.launch_bands(svc, "kiss") == {"433"}
        assert svc._live_bands("kiss", "") == {"433"}
    finally:
        p.kill()
        p.wait(timeout=5)


def _spawn():
    return subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)


def test_re1_a_stop_after_the_row_moved_to_another_allowed_band_uses_the_launch_band(tmp_path):
    # kiss runs on 433 and 868: r3's row edited 433 -> 868 while kiss@r3 runs; the stop addresses it with its launch
    # band (433) and reaches it; a start refuses (it runs as launched).
    import dataclasses
    p = _running_kiss_on_r3(tmp_path)
    try:
        radios_fixture.write(tmp_path, (R1, dataclasses.replace(R3, band="868")))
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        _sid, inst, band, why = instances.parse_target(svc, "kiss@r3", "stop")
        assert (inst.rid, band) == ("r3", "433") and "runs as launched on 433 MHz" in why, (band, why)
        assert svc.stop("kiss", apply=True, inst=inst).ok
        assert p.wait(timeout=10) is not None
    finally:
        p.kill()
        p.wait(timeout=5)


def test_re3_two_verified_records_that_disagree_on_a_radios_band_tell_no_band(tmp_path):
    radios_fixture.write(tmp_path, (R1, R3))
    a, b = _spawn(), _spawn()
    try:
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        kiss = svc.stack("kiss")
        for p, band, comp in ((a, "433", "loraham-kiss-tnc"), (b, "868", "loraham-kiss-serial")):
            assert svc._lifecycle().record_launch(kiss, kiss.component(comp), p.pid, band=band, extra={"rid": "r3"})
        assert instances.recorded_bands(svc, "kiss") == {"r3": ""}
        assert instances.launch_bands(svc, "kiss") == set()          # the caller holds every band
    finally:
        for p in (a, b):
            p.kill()
            p.wait(timeout=5)


def test_re2_a_bare_kill_takes_an_unrecorded_process_of_a_removed_radio(tmp_path):
    from lhpc.core import kill
    from lhpc.core.commands import Instance
    radios_fixture.write(tmp_path, (R1, R3))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    tok = instances._token(plain, plain.stack("kiss").component("loraham-kiss-tnc"), Instance("r3"), "433")
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, (R1,))                          # r3 removed; its hand-started TNC runs on
        svc = ControllerService(system=FakeSystem(cmdlines_data={p.pid: ["/x/loraham-kiss-tnc", *tok]}).system,
                                paths=Paths(runtime_root=tmp_path))
        plan = kill.kill(svc, "kiss", "loraham-kiss-tnc")
        assert plan.ok and plan.data["pids"] == [p.pid], (plan.summary, plan.details)
    finally:
        p.kill()
        p.wait(timeout=5)


def test_re2_guard_a_stop_still_leaves_a_hand_started_spec_process_alone(tmp_path):
    # The bare kill's spec match is KILL only: `stop kiss@r1` never signals a hand-started TNC that carries no token
    # and no record (slice 2's safety fix); the stop does not claim it stopped.
    from lhpc.core.commands import Instance
    radios_fixture.write(tmp_path, (R1, R3))
    p = _spawn()
    try:
        svc = ControllerService(system=FakeSystem(cmdlines_data={p.pid: ["/x/loraham-kiss-tnc"]}).system,
                                paths=Paths(runtime_root=tmp_path))
        res = svc.stop("kiss", apply=True, inst=Instance("r1", band="433"))
        from lhpc.core.outcomes import Outcome
        assert p.poll() is None, res.summary                          # alive
        tnc = next(r for r in res.results if r.component == "loraham-kiss-tnc")
        assert not res.ok and tnc.outcome == Outcome.MANUAL_REQUIRED and f"kill {p.pid}" in tnc.summary, tnc.summary
    finally:
        p.kill()
        p.wait(timeout=5)

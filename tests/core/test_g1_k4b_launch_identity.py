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
        assert instances.running_bands(svc, "kiss") == {"433"}
        assert svc._live_bands("kiss", "") == {"433"}
    finally:
        p.kill()
        p.wait(timeout=5)

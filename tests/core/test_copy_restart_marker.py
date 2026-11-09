"""Must-fix 6 of the stack-copy cleanup (K5: every instance's own): the restart-required marker is the instance's own. A
running kiss@r3's saved restart-mode change marks kiss@r3 alone, never kiss@r1; kiss@r3's stop clears its own only."""
import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), copy, "433")
    table = {61: ["/x/loraham-kiss-tnc", opt, val]}                    # kiss@r3 runs; kiss@r1 does not
    return ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))


def test_a_running_copys_restart_mode_save_marks_that_copy_alone(tmp_path):
    svc = _svc(tmp_path)
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=copy).ok
    assert svc.restart_required("kiss@r3")["params"] == ["kiss_port"]
    assert svc.restart_required("kiss@r1") is None
    assert svc.restart_required_stacks() == ["kiss@r3"]



def test_an_instances_stop_clears_its_own_marker_and_never_the_other_instances(tmp_path):
    svc = _svc(tmp_path)
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=copy).ok
    d = tmp_path / "state" / "restart-required"                       # kiss@r1's own marker: a stand-in
    (d / "kiss@r1.json").write_text((d / "kiss@r3.json").read_text().replace('"kiss@r3"', '"kiss@r1"'))
    idle = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))   # nothing runs now
    assert idle.stop("kiss", apply=True, inst=instances.copy_of(idle, "kiss", "r3")[0]).ok
    assert idle.restart_required("kiss@r3") is None and idle.restart_required("kiss@r1") is not None


def test_a_stopped_copys_save_writes_no_marker_while_the_bare_copy_runs(tmp_path):
    svc = _svc(tmp_path)
    bare = instances.copy_of(svc, "kiss", band="433")[0]
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), bare, "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={62: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))                            # the bare copy runs, kiss@r3 not
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=instances.copy_of(svc, "kiss", "r3")[0]).ok
    assert svc.restart_required("kiss@r3") is None and svc.restart_required("kiss") is None

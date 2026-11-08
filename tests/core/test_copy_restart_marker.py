"""Must-fix 6 of the stack-copy cleanup: the restart-required marker is the copy's own. A running listed copy's
saved restart-mode change marks that copy (`<stack>@<rid>`), never slot 0; the copy's stop clears it."""
import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('kiss = ["r3"]\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), copy, "433")
    table = {61: ["/x/loraham-kiss-tnc", opt, val]}                    # kiss@r3 runs; slot 0 does not
    return ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))


def test_a_running_copys_restart_mode_save_marks_that_copy_alone(tmp_path):
    svc = _svc(tmp_path)
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=copy).ok
    assert svc.restart_required("kiss@r3")["params"] == ["kiss_port"]
    assert svc.restart_required("kiss") is None
    assert svc.restart_required_stacks() == ["kiss@r3"]



def test_the_copys_stop_clears_its_own_marker_and_never_slot0s(tmp_path):
    svc = _svc(tmp_path)
    copy = instances.copy_of(svc, "kiss", "r3")[0]
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=copy).ok
    from lhpc.core import restart_required as rr
    rr.clear_marker(svc._paths, "kiss")                              # (none of slot 0's: a stand-in below)
    (tmp_path / "state" / "restart-required" / "kiss.json").write_text(
        (tmp_path / "state" / "restart-required" / "kiss@r3.json").read_text().replace('"kiss@r3"', '"kiss"'))
    idle = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))   # nothing runs now
    assert idle.stop("kiss", apply=True, inst=instances.copy_of(idle, "kiss", "r3")[0]).ok
    assert idle.restart_required("kiss@r3") is None and idle.restart_required("kiss") is not None


def test_a_stopped_copys_save_writes_no_marker_while_slot0_runs(tmp_path):
    svc = _svc(tmp_path)
    slot0 = instances.copy_of(svc, "kiss", band="433")[0]
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), slot0, "433")
    svc = ControllerService(system=FakeSystem(cmdlines_data={62: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))                            # slot 0 runs, kiss@r3 not
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8050"}, inst=instances.copy_of(svc, "kiss", "r3")[0]).ok
    assert svc.restart_required("kiss@r3") is None and svc.restart_required("kiss") is None

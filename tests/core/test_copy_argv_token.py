"""Must-fix 3 of the stack-copy cleanup: a listed copy is told running by the argv token of its OWN settings (its
spec's instance_arg, kiss's `--data-socket`), never slot 0's. A copy whose own saved socket is in a running argv
runs; slot 0's saved socket never makes the copy look running."""
import radios_fixture
from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path, socket):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('kiss = ["r3"]\n')
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "kiss", {"data_socket": "/tmp/own-r3.sock"}, inst=Instance(1, "r3"))
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "kiss", {"data_socket": "/tmp/slot0.sock"}, band="433")
    table = {41: ["/x/loraham-kiss-tnc", "--data-socket", socket]}
    return ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))


def _copy_runs(svc):
    copy, why = instances.copy_of(svc, "kiss", "r3")
    assert why == ""
    return instances.instance_runs(svc, svc.stack("kiss").component("loraham-kiss-tnc"), copy, "433")


def test_a_copy_running_on_its_own_saved_socket_runs(tmp_path):
    assert _copy_runs(_svc(tmp_path, "/tmp/own-r3.sock")) is True


def test_slot0s_saved_socket_never_makes_the_copy_run(tmp_path):
    assert _copy_runs(_svc(tmp_path, "/tmp/slot0.sock")) is False

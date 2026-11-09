"""DEC-W384j: one Hardware view reads the hardware list a bounded number of times, with instances running. K4b's
per-instance helpers once re-read it per stack x instance x component (27,762 reads in one overlap test, a 10 s page),
because every instance's argv token was rendered for every component, running or not, and every component check
collected the processes again."""
import radios_fixture

from lhpc.core import instances, radio_start, radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))
RUNNING = (("kiss", "loraham-kiss-tnc", "r1"), ("meshcore", "meshcore-node", "r2"), ("chat", "loraham-chat", "r1"))
BOUND = 150         # measured: 87 here; 3119 on 97d39408 (the defect); the old order of a whole overlap test: 151


def test_one_hardware_view_reads_the_list_a_bounded_number_of_times(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, ROWS)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    table = {}
    for pid, (sid, cid, rid) in enumerate(RUNNING, 100):            # each instance hand-started, named by its token
        spec = plain.stack(sid).component(cid).process
        table[pid] = [f"/x/{spec.exec_name}", *spec.all_args, *instances._token(plain, plain.stack(sid).component(cid),
                                                                             Instance(rid), "")]
    svc = ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=paths)
    assert instances.running_keys(svc, "kiss") == ["kiss@r1"] and instances.running_keys(svc, "meshcore") == ["meshcore@r2"]
    reads, real = [], radios.load
    monkeypatch.setattr(radios, "load", lambda *a, **k: reads.append(1) or real(*a, **k))
    view = radio_start.hardware_view(svc)
    assert view and len(reads) <= BOUND, len(reads)

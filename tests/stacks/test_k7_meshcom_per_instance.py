"""K7 (DEC-W384s/v, W390): meshcom@rN is its own QEMU guest. run.sh gets --instance N - 1 (its .run/<n>/; r1 the
single-guest layout), its radio's host ports (--ports web:console, base + 133 x (n - 1)) and the firmware's compiled-in
bridge address 10.0.2.2:7000 reaches the host's 7000 natively (DEC-W384af: no --xr-redirect yet, so one instance at a
time)."""
import pytest
import radios_fixture

from lhpc.core import commands
from lhpc.core.commands import Instance
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios_fixture.R1, radios_fixture.R2)          # r1 and r2, both 433: two meshcom instances


def _argv(svc, inst):
    comp = svc.stack("meshcom").component("meshcom-qemu")
    src = str(svc._paths.resolve_source(comp.source.path))
    return commands.expand_argv(comp.run_argv, comp, svc.stack_config(comp.id, "433", inst), Config().operator,
                                str(svc._paths.runtime_root), src, "433", inst=inst)


def _flags(argv):
    return {f: argv[argv.index(f) + 1] for f in ("--instance", "--ports", "--xr-redirect") if f in argv}


@pytest.mark.parametrize("rid,want", [
    ("r1", {"--instance": "0", "--ports": "18083:12323"}),
    ("r2", {"--instance": "1", "--ports": "18216:12456"})])         # DEC-W384af: no --xr-redirect (the native route)
def test_k7_each_meshcom_guest_runs_on_its_radios_ports_and_its_own_bridge(tmp_path, rid, want):
    radios_fixture.write(tmp_path, TWO_433)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert _flags(_argv(svc, Instance(rid, band="433"))) == want


def test_k8_a_second_meshcom_instance_is_refused_by_name(tmp_path, monkeypatch):
    # DEC-W384af: until a per-connection bridge redirect lands, MeshCom runs one instance at a time: meshcom@r1 runs,
    # meshcom@r2's start is refused naming it (its guest would dial meshcom@r1's bridge)
    from lhpc.core import instances
    radios_fixture.write(tmp_path, TWO_433)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_operator_identity(callsign="XX0XXA").ok              # else the start stops at the callsign first
    monkeypatch.setattr(instances, "running_keys", lambda _s, sid: ["meshcom@r1"] if sid == "meshcom" else [])
    res = svc.start("meshcom", apply=False, inst=Instance("r2", band="433"))
    assert not res.ok and res.summary == ("'meshcom' runs one instance at a time (its guests share the firmware's "
                                          "bridge address 10.0.2.2:7000): meshcom@r1 runs"), res.summary

"""K7 (DEC-W384s/v, W390): meshcom@rN is its own QEMU guest. run.sh gets --instance N - 1 (its .run/<n>/; r1 the
single-guest layout), its radio's host ports (--ports web:console, base + 133 x (n - 1)) and the firmware's compiled-in
bridge address 10.0.2.2:7000 redirected to ITS bridge's default port (--xr-redirect); one shared XR password
(DEC-W384v)."""
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
    ("r1", {"--instance": "0", "--ports": "18083:12323", "--xr-redirect": "7000:7000"}),
    ("r2", {"--instance": "1", "--ports": "18216:12456", "--xr-redirect": "7000:7133"})])
def test_k7_each_meshcom_guest_runs_on_its_radios_ports_and_its_own_bridge(tmp_path, rid, want):
    radios_fixture.write(tmp_path, TWO_433)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert _flags(_argv(svc, Instance(rid, band="433"))) == want


def test_k7_a_second_meshcom_instance_is_no_longer_refused_as_once_only(tmp_path, monkeypatch):
    # MAY out (W330, DEC-W384h by property): meshcom@r1 runs; meshcom@r2's start is not refused by the one-instance
    # rule (only a fixed-ports stack, Reticulum's one node, keeps it)
    from lhpc.core import instances
    radios_fixture.write(tmp_path, TWO_433)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_operator_identity(callsign="XX0XXA").ok              # else the start stops at the callsign first
    monkeypatch.setattr(instances, "running_keys", lambda _s, sid: ["meshcom@r1"] if sid == "meshcom" else [])
    res = svc.start("meshcom", apply=False, inst=Instance("r2", band="433"))     # the plan: nothing is launched
    assert res.ok and "runs one instance at a time" not in res.summary, res.summary

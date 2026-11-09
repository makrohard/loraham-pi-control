"""DEC-W384ac: a saved port moves every use of its base. The saved value is the base (K6S-2, DEC-W348: rN listens on
base + 133 x (n - 1)); before, only the param itself followed it, while its endpoint, its tcp.port claim and a
sibling's target stayed on the manifest base (a saved kiss_port left the start unverified and kiss-serial dialled the
wrong port)."""
import radios_fixture

from lhpc.core import commands, instances
from lhpc.core.commands import Instance
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, rows=radios_fixture.RADIOS):
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    return svc


def _prep(svc, sid, inst):
    """The start's own preparation (what it renders its argv, readiness and claims from), no side effect."""
    prep = svc._prepare_from_fresh_evidence(sid, inst.band, False, None, "", apply=False, inst=inst)
    assert hasattr(prep, "inst"), getattr(prep, "summary", prep)
    return prep


def _argv(svc, comp, inst, band):
    src = str(svc._paths.resolve_source(comp.source.path)) if comp.source else str(svc._paths.runtime_root)
    return commands.expand_argv(comp.run_argv, comp, svc.stack_config(comp.id, band, inst), Config().operator,
                                str(svc._paths.runtime_root), src, band, inst=inst)


def test_k8_a_saved_kiss_port_moves_its_endpoint_claim_and_the_serial_target(tmp_path):
    svc = _svc(tmp_path)
    r2 = Instance("r2", band="868")
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000"}, inst=r2).ok
    prep = _prep(svc, "kiss", r2)
    tnc, serial = svc.stack("kiss").component("loraham-kiss-tnc"), svc.stack("kiss").component("loraham-kiss-serial")
    assert [e.address for e in instances.endpoints_for(tnc.endpoints, prep.inst) if e.kind == "tcp"] == ["127.0.0.1:9133"]
    assert {instances.claim_key(r, prep.inst) for r in tnc.resources if r.kind == "tcp-port"} == {"tcp.port.9133"}
    assert any("127.0.0.1:9133" in t for t in _argv(svc, serial, prep.inst, "868")), _argv(svc, serial, prep.inst, "868")
    assert instances.stop_scope(svc, "kiss", tnc, r2, "868")["endpoints"][0].address == "127.0.0.1:9133"


def test_k8_a_saved_meshcom_bridge_port_moves_its_endpoint_and_claim(tmp_path):
    # the guest's redirect to it follows with the per-connection bridge (DEC-W384af B)
    svc = _svc(tmp_path, (radios_fixture.R1, radios_fixture.R2))          # r1, r2 on 433
    r2 = Instance("r2", band="433")
    assert svc.save_config_bundle("meshcom", values={"port": "7100"}, inst=r2).ok
    prep = _prep(svc, "meshcom", r2)
    bridge = svc.stack("meshcom").component("meshcom-bridge")
    assert "127.0.0.1:7233" in [e.address for e in instances.endpoints_for(bridge.endpoints, prep.inst)]
    assert {instances.claim_key(r, prep.inst) for r in bridge.resources if r.kind == "tcp-port"} == {"tcp.port.7233"}


def test_k8_an_unsaved_port_keeps_the_radios_default(tmp_path):
    svc = _svc(tmp_path)
    prep = _prep(svc, "kiss", Instance("r2", band="868"))
    tnc = svc.stack("kiss").component("loraham-kiss-tnc")
    assert [e.address for e in instances.endpoints_for(tnc.endpoints, prep.inst) if e.kind == "tcp"] == ["127.0.0.1:8134"]

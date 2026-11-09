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


def test_g1_k8a_10_a_non_ascii_digit_in_a_saved_port_is_no_base(tmp_path):
    # a hand-edited "²" (str.isdigit() is True for it, int() raises): the radio's default, never a crash in status,
    # claims or the stop scope
    svc = _svc(tmp_path)
    r2 = Instance("r2", band="868")
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000"}, inst=r2).ok
    f = next((tmp_path / "config" / "stacks").glob("kiss@r2.toml"))
    f.write_text(f.read_text().replace('"9000"', '"²"'))
    assert "²" in f.read_text()
    tnc = svc.stack("kiss").component("loraham-kiss-tnc")
    assert instances.stop_scope(svc, "kiss", tnc, r2, "868")["endpoints"][0].address == "127.0.0.1:8134"


def test_dec_w384ac_a_saved_port_moves_its_firewall_row(tmp_path):
    # handler (G1-K8A, W290 5): proven, not inferred. kiss@r2 and meshcom@r2 exposed (bind 0.0.0.0) on saved ports:
    # their direct-access rows open the moved port (base + 133 for r2), never the manifest's
    svc = _svc(tmp_path, (radios_fixture.R1, radios_fixture.R2))          # r1, r2 on 433
    r2 = Instance("r2", band="433")
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000", "kiss_host": "0.0.0.0"}, inst=r2).ok
    assert svc.save_config_bundle("meshcom", values={"port": "7100", "bind": "0.0.0.0"}, inst=r2).ok
    rows = {e["id"]: e["port"] for e in svc.firewall_candidate()["endpoints"]}
    assert rows["loraham-kiss-tnc-r2.tcp-8001"] == 9133 and rows["meshcom-bridge-r2.tcp-7000"] == 7233, rows


def test_dec_w384ac_no_web_page_is_proxied_from_a_saveable_port(tmp_path):
    # a web row's upstream is its endpoint's instance port; no proxied page sits on a port an operator can save (the
    # saveable bases are kiss 8001, the MeshCom bridge 7000 and the MeshCore companion 5000), so a saved port never
    # leaves a web row on the old port
    from lhpc.core import model
    svc = _svc(tmp_path)
    saveable = {int(m.group(1)) for st in svc.stacks() for c in st.components for q in c.run_params
                if (m := commands._PORT_RE.fullmatch(getattr(q, "default_template", "") or str(q.default)))}
    assert saveable == {8001, 7000, 5000}
    pages = [p for st in svc.stacks() for p in model.web_pages(st, [commands.TEMPLATE])]
    assert pages and not {int(p.address.rsplit(":", 1)[1]) for p in pages} & saveable, [p.address for p in pages]


def test_dec_w384ac_a_saved_meshcore_companion_port_moves_every_use(tmp_path):
    # meshcore@r3 (868) with port 5100: the node's companion listener, the web UI's and the CLI's target follow it
    svc = _svc(tmp_path, (radios_fixture.R1, radios_fixture.R2, radios_fixture.R3))
    r3 = Instance("r3", band="868")
    assert svc.save_config_bundle("meshcore", values={"port": "5100"}, inst=r3).ok
    prep = _prep(svc, "meshcore", r3)
    st = svc.stack("meshcore")
    node, webui, cli = (st.component(c) for c in ("meshcore-node", "meshcore-webui", "meshcore-cli"))
    assert "127.0.0.1:5366" in [e.address for e in instances.endpoints_for(node.endpoints, prep.inst)]
    assert "5366" in _argv(svc, webui, prep.inst, "868") and "5366" in _argv(svc, cli, prep.inst, "868")


def _kiss_r2_on(tmp_path, monkeypatch, port):
    # kiss@r2 (868) runs with kiss_port 9000 saved; its TNC listens on `port`; r2's daemon is READY
    from lhpc.core.probes.backends import Listener
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    ready = b"STATUS RADIO=READY ID=r2 BOUND=- TXMODE=MANAGED\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r2.sock": ready},
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=port, inode=1)])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    r2 = Instance("r2", band="868")
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000"}, inst=r2).ok
    monkeypatch.setattr(instances, "instance_runs", lambda s, c, i, band: i.rid == "r2" and c.id == "loraham-kiss-tnc")
    return svc, r2, svc.stack("kiss").component("loraham-kiss-tnc")


def test_dec_w384ac_a_saved_kiss_port_judges_status_health_and_claims(tmp_path, monkeypatch):
    # handler DEC-W384ai: end to end for kiss@r2's saved 9000 (r2: 9133): its status is RUNNING on 9133 and DEGRADED on
    # the default 8134; the start's already-healthy check holds on 9133; its running claim is 9133's
    from lhpc.core.model import RunState
    svc, r2, tnc = _kiss_r2_on(tmp_path, monkeypatch, 9133)
    assert instances.instance_state(svc, tnc, r2, "868", RunState.RUNNING) is RunState.RUNNING
    assert instances.already_healthy(svc, "kiss", svc._run_order("kiss"), "868", r2, "868")
    claim = next(r for r in tnc.resources if r.kind == "tcp-port")
    assert instances.running_claim_keys(svc, "kiss", tnc, claim) == {"tcp.port.9133"}
    svc, r2, tnc = _kiss_r2_on(tmp_path / "default", monkeypatch, 8134)
    assert instances.instance_state(svc, tnc, r2, "868", RunState.RUNNING) is RunState.DEGRADED
    assert not instances.already_healthy(svc, "kiss", svc._run_order("kiss"), "868", r2, "868")

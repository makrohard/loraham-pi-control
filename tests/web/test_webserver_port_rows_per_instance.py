"""K8f S4-4b1 (D3 B3): the dashboard Webserver box has a port row per running instance, each at its own port, with
its own bind host and allow-list and its rid. kiss runs on r1 and on r2 (both 433)."""
import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path, monkeypatch, values):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r1, r2 = (instances.parse_target(svc, f"kiss@{r}", "start")[1] for r in ("r1", "r2"))
    assert svc.save_config_bundle("kiss", values=values, inst=r2).ok
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == "kiss":
            for cs in ss.components.values():
                cs.run_state = RunState.RUNNING
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self, *a, **k: snap)
    monkeypatch.setattr(instances, "running_insts", lambda s, sid: [r1, r2] if sid == "kiss" else None)
    return svc


@pytest.mark.parametrize("values, r2_exposure", [
    ({"kiss_host": "0.0.0.0"}, {"level": "warn", "label": "LAN"}),      # r2 binds every address (G1-K8F-S4-D3 B3)
    ({"kiss_bind": "0.0.0.0/0"}, {"level": "bad", "label": "public"}),  # r2 allows every source
])
def test_each_running_instance_has_its_own_port_row(tmp_path, monkeypatch, web, values, r2_exposure):
    # red on 617eed17: one row, r1's port 8001 and r1's bind host / allow-list (ok, local): r2's exposure unreported
    svc = _box(tmp_path, monkeypatch, values)
    rows = [(w["name"], w.get("rid", ""), w["port"], w["exposure"]) for w in svc.dashboard_webservers(fw_status={})
            if w["kind"] == "port"]
    assert rows == [("LoRaHAM KISS TNC (r1)", "r1", "8001", {"level": "ok", "label": "local"}),
                    ("LoRaHAM KISS TNC (r2)", "r2", "8134", r2_exposure)], rows
    page = web(service_factory=lambda: svc).get("/").get_data(as_text=True)
    assert "LoRaHAM KISS TNC (r2)</a>" in page and ":8134 · listener absent" in page


def test_each_instances_live_port_is_read_from_its_own_process(tmp_path, monkeypatch):
    # kiss@r1 runs as pid 101 with --kiss-port 8001, kiss@r2 as pid 102 with --kiss-port 8134: each row reads its own
    # process, so both ports are verified. red on 617eed17: one row over both pids, two ports: "unverified"
    svc = _box(tmp_path, monkeypatch, {})
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == "kiss":
            ss.components["loraham-kiss-tnc"].pids = [101, 102]
    table = {101: ["loraham_kiss_tnc", "--kiss-port", "8001"], 102: ["loraham_kiss_tnc", "--kiss-port", "8134"]}
    monkeypatch.setattr(svc._system.procfs, "cmdlines", lambda: table)
    monkeypatch.setattr(instances, "instance_processes", lambda s, t: [("kiss", "r1", "loraham-kiss-tnc", 101),
                                                                     ("kiss", "r2", "loraham-kiss-tnc", 102)])
    rows = [(w["port"], w["live_scope"]) for w in svc.dashboard_webservers(fw_status={}) if w["kind"] == "port"]
    assert rows == [("8001", "absent"), ("8134", "absent")], rows


def test_the_process_table_is_attributed_once_per_render(tmp_path, monkeypatch):
    # G1-K8F-S4-4B1 N2: the port rows of one render share one instance_processes() pass. red on 3b83a071: one per row
    svc = _box(tmp_path, monkeypatch, {})
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == "kiss":
            ss.components["loraham-kiss-tnc"].pids = [101, 102]
    calls = []
    monkeypatch.setattr(svc._system.procfs, "cmdlines", lambda: {101: ["k"], 102: ["k"]})
    monkeypatch.setattr(instances, "instance_processes", lambda s, t: calls.append(1) or [])
    rows = [w for w in svc.dashboard_webservers(fw_status={}) if w["kind"] == "port"]
    assert len(rows) == 2 and len(calls) == 1, (len(rows), calls)

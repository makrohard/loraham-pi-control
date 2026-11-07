"""Week item 14 (field test F3b): the dashboard names the degraded component and why, and a stopped web UI reads
"web UI stopped".

In the field test the MeshCore card read "degraded" and "listener absent" beside a running repeater: "degraded" was
only meshcore-gps (its gpsd had lost the device), "listener absent" was the stopped meshcore-webui. The component row
now carries its reason (status.component_note: the GPS feed's note, else the absent listeners), and a web-UI row whose
component is stopped says so; a running component whose listener is absent keeps "listener absent".

Driven through GET / with the snapshot stood in for (the states are the prober's: tests of status).
"""
from __future__ import annotations

import pytest
from htmlq import parse

from lhpc.core.model import ComponentStatus, EndpointObservation, RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import Snapshot, StackStatus

LOST = "source not delivering (gpsd: device activation failed)"


def _dash(web, tmp_path, monkeypatch, states, evidence=None, absent=(), proxied=False):
    """GET / with the meshcore stack in `states` (component id -> RunState), `evidence` per component, the
    endpoints of the components in `absent` observed absent, and the web UI's page proxied when `proxied`."""
    if proxied:
        from lhpc.core.config import save_stackweb_config
        save_stackweb_config(Paths(runtime_root=tmp_path), "meshcore", mode="lan", port=8451)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    st = svc.stack("meshcore")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=st)
    for comp in st.components:
        cs = ComponentStatus(component_id=comp.id, run_state=states.get(comp.id, RunState.STOPPED),
                             evidence=dict((evidence or {}).get(comp.id, {})))
        if comp.id in absent:
            cs.endpoints = [EndpointObservation(spec=ep, present=False) for ep in comp.endpoints]
        ss.components[comp.id] = cs
    snap.stacks.append(ss)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *a, **k: snap)
    return parse(web(service_factory=lambda: svc).get("/").get_data(as_text=True))


def _notes(doc):
    return {el["data-note"]: el.text.strip() for el in doc.find("span", class_="muted") if el.has_attr("data-note")}


UP, DEG, STOP = RunState.RUNNING, RunState.DEGRADED, RunState.STOPPED


def _stopped(doc):
    """The "web UI stopped" marks (a proxied row's pill or a direct row's link)."""
    return [el for tag in ("a", "span") for el in doc.find(tag, **{"data-state": "web-ui-stopped"})]


def test_r1_the_degraded_gps_feed_is_named_with_its_reason(web, tmp_path, monkeypatch):
    """RED before: the card listed meshcore-gps "degraded" and no reason."""
    doc = _dash(web, tmp_path, monkeypatch, {"meshcore-node": UP, "meshcore-gps": DEG, "meshcore-webui": UP},
                evidence={"meshcore-gps": {"gps.feed": LOST, "gps.fix": ""}})
    assert _notes(doc) == {"meshcore-gps": f"GPS feed: {LOST}"}


def test_r2_a_degraded_component_without_a_feed_note_names_its_absent_listener(web, tmp_path, monkeypatch):
    """RED before."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    addr = next(ep.address for ep in svc.stack("meshcore").component("meshcore-node").endpoints)
    doc = _dash(web, tmp_path, monkeypatch, {"meshcore-node": DEG, "meshcore-gps": UP, "meshcore-webui": UP},
                absent=("meshcore-node",))
    head, _sep, listeners = _notes(doc)["meshcore-node"].partition(": ")
    assert head == "listener absent" and addr in listeners.split(", ")


@pytest.mark.parametrize("proxied", [False, True], ids=["direct", "proxied"])
def test_r3_the_stopped_web_ui_reads_web_ui_stopped(web, tmp_path, monkeypatch, proxied):
    """RED before: "listener absent" (direct) or the proxy's link alone (proxied) while the component was stopped."""
    doc = _dash(web, tmp_path, monkeypatch, {"meshcore-node": UP, "meshcore-gps": UP, "meshcore-webui": STOP},
                proxied=proxied)
    assert [el.tag for el in _stopped(doc)] == (["span"] if proxied else ["a"])


def test_r4_control_a_running_web_ui_without_its_listener_keeps_listener_absent(web, tmp_path, monkeypatch):
    doc = _dash(web, tmp_path, monkeypatch, {"meshcore-node": UP, "meshcore-gps": UP, "meshcore-webui": UP})
    assert _stopped(doc) == []
    assert [a for a in doc.find("a", class_="wsurl") if a.text.strip() == "listener absent"]


def test_r5_control_everything_running_carries_no_note(web, tmp_path, monkeypatch):
    """Running components carry no reason, even with a listener absent or a feed note (those are the row's own)."""
    doc = _dash(web, tmp_path, monkeypatch, {"meshcore-node": UP, "meshcore-gps": UP, "meshcore-webui": UP},
                evidence={"meshcore-gps": {"gps.feed": "source live (9 sentences)", "gps.fix": "fix"},
                          "meshcore-node": {"gps.feed": LOST}}, absent=("meshcore-node",))
    assert _notes(doc) == {}


def test_r6_two_degraded_components_are_both_named(web, tmp_path, monkeypatch):
    """RED before."""
    doc = _dash(web, tmp_path, monkeypatch, {"meshcore-node": DEG, "meshcore-gps": DEG, "meshcore-webui": UP},
                evidence={"meshcore-gps": {"gps.feed": LOST}}, absent=("meshcore-node",))
    assert sorted(_notes(doc)) == ["meshcore-gps", "meshcore-node"]


@pytest.mark.parametrize("state", [DEG, RunState.FAILED])
def test_r7_lhpc_status_names_the_reason_too(tmp_path, monkeypatch, state):
    """RED before: the status line said only degraded/failed."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    st = svc.stack("meshcore")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=st)
    for comp in st.components:
        ss.components[comp.id] = ComponentStatus(component_id=comp.id, run_state=state if comp.id == "meshcore-gps"
                                                 else UP, evidence={"gps.feed": LOST} if comp.id == "meshcore-gps" else {})
    snap.stacks.append(ss)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *a, **k: snap)
    lines = [d.strip() for d in svc.status("meshcore").details]
    i = next(n for n, d in enumerate(lines) if d.split()[:1] == ["meshcore-gps"])
    assert lines[i + 1] == f"GPS feed: {LOST}"

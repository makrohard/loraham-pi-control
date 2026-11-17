"""RADIO-POWER item 4, N2 (the coordinator's decision): a MeshCom TX power step that ends unconfirmed keeps the start
(a warning, not a refusal), and the stack's row on the Apps page says so: the power is not confirmed, the node may still
use its own value, and the re-apply command. The line is the status line `lhpc status` shows (the rendered step
record); a stopped stack shows none."""
from __future__ import annotations

import re

import radios_fixture

from lhpc.core.model import ComponentStatus, RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import Snapshot, StackStatus

STEP = {"kind": "tcp_send", "label": "tx power", "outcome": "unverified", "attempts": 2, "elapsed_s": 0.3,
        "reported": "7"}


def _svc(tmp_path, monkeypatch, state):
    radios_fixture.write(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    st = svc.stack("meshcom")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=st)
    for comp in st.components:
        ss.components[comp.id] = ComponentStatus(component_id=comp.id, run_state=state)
    snap.stacks.append(ss)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *a, **k: snap)
    # the node's unconfirmed step, as its post-start record renders it for `lhpc status`
    monkeypatch.setattr(ControllerService, "_post_start_outcomes", lambda self, cid: (
        self._render_post_steps([STEP], "", "meshcom") if cid == "meshcom-qemu" else []))
    return svc


def _notes(web, svc):
    page = web(service_factory=lambda: svc).get("/stacks?open=meshcom").get_data(as_text=True)
    return re.findall(r'<p class="depnote depnote-warn" data-note="([^"]*)">⚠ ([^<]*)</p>', page)


def test_an_unconfirmed_tx_power_is_on_the_running_stacks_row(web, tmp_path, monkeypatch):
    notes = _notes(web, _svc(tmp_path, monkeypatch, RunState.RUNNING))
    want = ("meshcom-qemu: tx power UNVERIFIED — not confirmed (the node may use 7): the node answered but never "
            "reported the expected value within 0.3s (2 attempts); re-apply with: lhpc stack poststart meshcom@r1")
    assert ("meshcom-qemu", want) in notes, notes


def test_a_stopped_stack_shows_no_post_start_note(web, tmp_path, monkeypatch):
    assert _notes(web, _svc(tmp_path, monkeypatch, RunState.STOPPED)) == []

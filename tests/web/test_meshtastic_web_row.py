"""The Meshtastic web client's card on the Apps page (Track F C3): the component renders in the
meshtastic stack's body, closed and open, and is not labelled a build dependency (it has no
source and nothing builds against it; `lhpc status meshtastic` carries its selector and pairing)."""
from __future__ import annotations

import htmlq

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_the_web_client_card_renders_in_the_open_meshtastic_body(tmp_path, web):
    client = web(service_factory=lambda: _svc(tmp_path))
    closed = client.get("/stacks")
    assert closed.status_code == 200
    page = client.get("/stacks?open=meshtastic")
    assert page.status_code == 200
    doc = htmlq.parse(page.get_data(as_text=True))
    card = doc.within(doc.by_id("comp-meshtastic-web"))
    assert "The browser UI meshtasticd serves" in card.text
    pills = [p.text for p in card.find("span", class_="pill")]
    assert "build dependency" not in pills         # served by meshtasticd, never built into it
    assert "optional" not in pills                 # installed with the stack, not a choice

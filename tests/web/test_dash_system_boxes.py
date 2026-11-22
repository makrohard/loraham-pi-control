"""The System card body is three boxes in order: (1) the CPU..GNSS table, (2) autostart,
its texts before the Turn on / off button (the button last), (3) Reboot / Shut down, only when one of them is allowed
(no empty box); the host info line stays below the boxes. Rendered GET of Home."""
import pytest
from htmlq import parse

from lhpc.core.services import ControllerService


@pytest.mark.parametrize("power", [True, False])
def test_the_system_card_is_three_boxes(web, monkeypatch, power):
    monkeypatch.setattr(ControllerService, "power_supported", lambda self, kind: power)
    doc = parse(web().get("/").get_data(as_text=True))
    box = doc.within(doc.by_id("sysbox"))
    body = box.find("div", class_="body")[0]
    groups = [g for g in box.find("div", class_="sysgroup")]
    assert len(groups) == (3 if power else 2)
    assert all(body.index < g.index <= body.end for g in groups)
    first, auto = box.within(groups[0]), box.within(groups[1])
    assert first.find("table", class_="systab") and len(first.find("tr")) >= 5
    assert not [t for t in box.find("table", class_="systab") if not (groups[0].index < t.index <= groups[0].end)]
    sec = auto.find("section", class_="sysautostart")[0]
    kids = [e for e in auto.find("p") + auto.find("form") if sec.index < e.index <= sec.end]
    last = max(kids, key=lambda e: e.index)
    assert last.tag == "form" and auto.within(last).find("button")                 # the button last
    assert [p.text for p in auto.find("p") if p.text == "Applies at the next boot."]
    if power:
        assert box.within(groups[2]).find("div", class_="sys-power-actions")
    else:
        assert not box.find("div", class_="sys-power-actions")
    info = doc.by_id("sys-info")
    assert info.index > groups[-1].end

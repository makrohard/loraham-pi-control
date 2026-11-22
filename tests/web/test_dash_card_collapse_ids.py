"""Every Home radio card, a stray band card included, wraps its body in its own
details#radiobox-<rid or band> (open by default) inside div#radio-<rid or band>; ids stay unique with two stray cards."""
from htmlq import parse

from lhpc.core.services import ControllerService


def test_each_card_has_its_own_open_radiobox(web, monkeypatch):
    real = ControllerService.radio_overview

    def with_strays(self, strays=None):
        out = real(self, strays)
        if strays is not None:
            for band in ("915", "169"):
                strays.append({"band": band, "daemon": out[0]["daemon"], "shared_with": [], "rflogs": []})
        return out
    monkeypatch.setattr(ControllerService, "radio_overview", with_strays)
    body = web().get("/").get_data(as_text=True)
    doc = parse(body)
    cards = doc.find("div", class_="radio-col")
    keys = [c["id"].removeprefix("radio-") for c in cards]
    assert len(keys) == 4 and len(set(keys)) == 4 and {"915", "169"} <= set(keys), keys
    for c, key in zip(cards, keys):
        boxes = doc.within(c).find("details", class_="radiobox")
        assert [b["id"] for b in boxes] == [f"radiobox-{key}"] and boxes[0].has_attr("open"), key
        assert doc.within(boxes[0]).find("summary")[0].index == boxes[0].index + 1     # its summary first
    assert body.count('id="radiobox-') == 4

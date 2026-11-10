"""DEC-W384bu/bv/cl: Home is one card per enabled radio, labelled "<name> (<rid>, <band> MHz)", every value and control
its radio's own (radio_overview), then the §6 stray band daemons (stray_daemons). No link carries a band. With radios
listed but none enabled the banner says so (the former per-band card hint, DEC-W384cl); without a list it says none is
set up. Driven through the rendered page."""
import dataclasses
from urllib.parse import parse_qs, urlsplit

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "left", "uputronics", "433", ce="ce0"), radios.Radio("r2", "right", "uputronics", "433", ce="ce1"))
STATUS = "STATUS ID={} BOUND=- RADIO=READY TX=0 TXMODE={} CADWAIT=1500 CADRSSI=-90\n"


def _page(web, tmp_path, rows=TWO_433, replies=None):
    if rows is not None:
        radios_fixture.write(tmp_path, rows)
    fake = FakeSystem(unix_replies=replies or {})
    r = web(service_factory=lambda: ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))).get("/")
    return r, parse(r.get_data(as_text=True))


def test_each_radio_of_one_band_has_its_own_labelled_card_and_daemon_view(web, tmp_path):
    replies = {f"/tmp/loraconf-{rid}.sock": STATUS.format(rid, mode).encode() for rid, mode in (("r1", "MANAGED"), ("r2", "DIRECT"))}
    r, doc = _page(web, tmp_path, replies=replies)
    assert r.status_code == 200
    cards = doc.find("div", class_="radio-col")
    assert [doc.within(c).find("h2")[0].text.strip() for c in cards] == ["left (r1, 433 MHz)", "right (r2, 433 MHz)"]
    assert (doc.by_id("rd-txmode-r1").text, doc.by_id("rd-txmode-r2").text) == ("MANAGED", "DIRECT")   # each its own
    for rid in ("r1", "r2"):
        card = doc.within(doc.by_id(f"radio-{rid}"))
        cfg = [a["href"] for a in card.find("a") if a.text == "Radio config"]
        assert [parse_qs(urlsplit(h).query).get("radio") for h in cfg] == [[rid]]
        assert {card.within(f).field_default("target") for f in card.find("form", action="/action")} >= {f"daemon@{rid}"}
        log = [a["href"] for a in card.find("a") if a.text == "Daemon log"]
        assert [parse_qs(urlsplit(h).query) for h in log] == [{"job": [f"start-loraham-daemon-433-{rid}.log"]}]


def test_no_link_or_form_on_home_carries_a_band(web, tmp_path):
    replies = {f"/tmp/loraconf-{rid}.sock": STATUS.format(rid, "MANAGED").encode() for rid in ("r1", "r2")}
    _r, doc = _page(web, tmp_path, replies=replies)
    hrefs = [a["href"] for a in doc.find("a") if a["href"]]
    assert hrefs and not [h for h in hrefs if "band" in parse_qs(urlsplit(h).query)]
    assert not doc.find("input", name="band")                      # only a stray band daemon's Stop names a band


def test_a_conflict_and_the_high_power_note_name_the_radio(web, tmp_path, monkeypatch):
    real = ControllerService.radio_overview

    def overview(self, strays=None):
        out = real(self, strays)
        out[1]["conflict"] = ["kiss@r2", "chat@r2"]
        out[1]["daemon"] = dict(out[1]["daemon"], high_power={"warn": True, "mismatch": "saved on, running off"})
        return out
    monkeypatch.setattr(ControllerService, "radio_overview", overview)
    _r, doc = _page(web, tmp_path)
    card = doc.within(doc.by_id("radio-r2")).text
    assert "Radio conflict on r2 (433 MHz):" in card and "Run only one at a time on this radio." in card
    assert "enabled on r2 (433 MHz)." in doc.by_id("rd-hp-r2").text
    assert "High-power switch, r2 (433 MHz): saved on, running off" in doc.by_id("rd-hp-mismatch-r2").text
    assert "Radio conflict" not in doc.within(doc.by_id("radio-r1")).text


@pytest.mark.parametrize("case", [pytest.param("no-list", marks=pytest.mark.no_default_hardware), "all-disabled"])
def test_the_banner_tells_no_list_from_radios_none_enabled(web, tmp_path, case):
    # DEC-W384cl: the per-band card's hint is one truthful banner line; each case its text and its Hardware link
    rows = None if case == "no-list" else tuple(dataclasses.replace(x, enabled=False) for x in TWO_433)
    r, doc = _page(web, tmp_path, rows=rows)
    page = " ".join(doc.text.split())
    assert r.status_code == 200 and not doc.find("div", class_="radio-col")
    if case == "no-list":
        assert "No radio hardware is set up yet." in page and doc.find("a", href="/hardware/add")
        assert "none is enabled" not in page
    else:
        assert "Radios are set up, but none is enabled." in page and doc.find("a", href="/hardware")
        assert "No radio hardware is set up yet." not in page


@pytest.mark.parametrize("rows, narrow", [(TWO_433[:1], True), (TWO_433, False),
                                          (TWO_433 + (radios_fixture.R3,), False)], ids=["one", "two", "three"])
def test_g1_g4w_n1_one_card_is_one_column_two_or_more_are_the_grid(web, tmp_path, rows, narrow):
    r, doc = _page(web, tmp_path, rows=rows)
    assert r.status_code == 200 and len(doc.find("div", class_="radio-col")) == len(rows)
    assert doc.find("body")[0].has_class("dash-narrow") is narrow

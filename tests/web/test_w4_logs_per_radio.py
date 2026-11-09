"""W4 (WEB-LANE-PLAN-v5 §W4, DEC-W384bh): logs and RF logs per radio. Two radios on one band (r1, r2 on 433) are
told apart: `?radio=` picks the instance's process log and the RF log page's radio, whose stack row lists that radio's
own `<rid>/<file>` only; `?band=` is no filter. Driven through /logs and its bottom forms."""
from urllib.parse import parse_qs, urlsplit

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"),
        radios_fixture.R3)


@pytest.fixture
def box(web, tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    logs = tmp_path / "logs"
    for rid in ("r1", "r2"):
        (logs / rid).mkdir(parents=True)
        (logs / rid / "rf-kiss.log").write_text(f"2026-11-09T19:00:00.000Z RX rssi=-70 snr=9 len=1 hex=01 kiss-of-{rid}\n")
        (logs / f"start-loraham-kiss-tnc-433-{rid}.log").write_text(f"process-of-{rid}\n")
    return web(), tmp_path


def _radio_row(doc):
    navs = doc.find("nav", **{"aria-label": "RF log radios"})
    assert navs, "the page has no radio row"
    return doc.within(navs[0])


def _stack_jobs(doc):
    row = doc.within(doc.find("nav", **{"aria-label": "RF logs"})[0])
    return [parse_qs(urlsplit(a["href"]).query) for a in row.find("a")]


@pytest.mark.parametrize("rid, other", [("r1", "r2"), ("r2", "r1")])
def test_two_radios_on_one_band_have_their_own_rf_log_rows(box, rid, other):
    c, _tmp = box
    doc = parse(c.get(f"/logs/loraham-kiss-tnc?job=rf-kiss.log&radio={rid}&band=433").get_data(as_text=True))
    jobs = [q["job"][0] for q in _stack_jobs(doc)]
    assert f"{rid}/rf-kiss.log" in jobs and f"{other}/rf-kiss.log" not in jobs
    assert all(q.get("radio") == [rid] for q in _stack_jobs(doc))                   # every pill keeps the radio
    radio_row = _radio_row(doc)
    assert [a.text for a in radio_row.find("a", **{"aria-current": "page"})] == [f"{rid} · 433"]


def test_an_instances_rf_log_opens_on_its_own_radio(box):
    c, _tmp = box
    doc = parse(c.get("/logs/loraham-kiss-tnc?job=r2/rf-kiss.log").get_data(as_text=True))
    radio_row = _radio_row(doc)
    assert [a.text for a in radio_row.find("a", **{"aria-current": "page"})] == ["r2 · 433"]
    assert doc.within(doc.by_id("rflog-clear")).field_default("radio") == "r2"


@pytest.mark.parametrize("rid, other", [("r1", "r2"), ("r2", "r1")])
def test_the_process_log_is_the_radios_instances_and_band_is_no_filter(box, rid, other):
    c, _tmp = box
    page = c.get(f"/logs/loraham-kiss-tnc?radio={rid}&band=868").get_data(as_text=True)
    assert f"process-of-{rid}" in page and f"process-of-{other}" not in page
    api = c.get(f"/api/logs/loraham-kiss-tnc?radio={rid}").get_json()
    assert api["lines"] == [f"process-of-{rid}"]


def test_the_bottom_forms_return_to_the_same_radio(box, csrf):
    c, _tmp = box
    token = csrf(c, "/logs/loraham-kiss-tnc?job=r2/rf-kiss.log&radio=r2")
    r = c.post("/rflog/all", data={"_csrf": token, "value": "off", "job": "r2/rf-kiss.log", "radio": "r2"})
    assert r.status_code == 302
    assert parse_qs(urlsplit(r.headers["Location"]).query) == {"job": ["r2/rf-kiss.log"], "radio": ["r2"]}

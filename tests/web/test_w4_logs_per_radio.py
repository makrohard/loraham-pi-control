"""W4 (WEB-LANE-PLAN-v5 §W4, DEC-W384bh): logs and RF logs per radio. Two radios on one band (r1, r2 on 433) are
told apart: `?radio=` picks the instance's process log and the RF log page's radio, whose stack row lists that radio's
own `<rid>/<file>` only; a `band` is refused by name (G1-W5). Driven through /logs and its bottom forms."""
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
    (logs / "rf-kiss.log").write_text("2026-11-09T19:00:00.000Z RX rssi=-70 snr=9 len=1 hex=01 shared\n")   # Clear all's
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
    doc = parse(c.get(f"/logs/loraham-kiss-tnc?job=rf-kiss.log&radio={rid}").get_data(as_text=True))
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
def test_the_process_log_is_the_radios_instances(box, rid, other):
    c, _tmp = box
    page = c.get(f"/logs/loraham-kiss-tnc?radio={rid}").get_data(as_text=True)
    assert f"process-of-{rid}" in page and f"process-of-{other}" not in page
    api = c.get(f"/api/logs/loraham-kiss-tnc?radio={rid}").get_json()
    assert api["lines"] == [f"process-of-{rid}"]


def test_the_bottom_forms_return_to_the_same_radio(box, csrf):
    c, _tmp = box
    token = csrf(c, "/logs/loraham-kiss-tnc?job=r2/rf-kiss.log&radio=r2")
    r = c.post("/rflog/all", data={"_csrf": token, "value": "off", "job": "r2/rf-kiss.log", "radio": "r2"})
    assert r.status_code == 302
    assert parse_qs(urlsplit(r.headers["Location"]).query) == {"job": ["r2/rf-kiss.log"], "radio": ["r2"]}


def _all_files(tmp_path):
    return {p.relative_to(tmp_path).as_posix(): p.read_bytes() for d in ("logs", "config") for p in (tmp_path / d).rglob("*")
            if p.is_file()}


def test_g1_w5_b1_a_radio_pill_on_an_instances_file_opens_that_radios_own_file(box):
    c, _tmp = box
    doc = parse(c.get("/logs/loraham-kiss-tnc?job=r1/rf-kiss.log&radio=r1").get_data(as_text=True))
    pill = next(a["href"] for a in _radio_row(doc).find("a") if a.text == "r2 · 433")
    assert parse_qs(urlsplit(pill).query) == {"job": ["r2/rf-kiss.log"], "radio": ["r2"]}
    page = parse(c.get(pill).get_data(as_text=True))
    assert page.by_id("log-card")["data-job"] == "r2/rf-kiss.log"
    assert page.within(page.by_id("rflog-clear")).field_default("job") == "r2/rf-kiss.log"      # Clear empties r2's
    shared = parse(c.get("/logs/loraham-kiss-tnc?job=rf-kiss.log&radio=r1").get_data(as_text=True))
    assert {parse_qs(urlsplit(a["href"]).query)["job"][0] for a in _radio_row(shared).find("a")} == {"rf-kiss.log"}


@pytest.mark.parametrize("path, form", [("/rflog/all", {"value": "off"}), ("/rflog/clear-all", {}),
                                        ("/logs/loraham-kiss-tnc/clear", {}),
                                        ("/stacks/graywolf/rflog", {"value": "off", "from": "logs"})])
@pytest.mark.parametrize("radio, code", [("r99", 404), ("", 400)])
def test_g1_w5_b2_a_bad_radio_is_refused_before_anything_changes(box, csrf, path, form, radio, code):
    c, tmp_path = box
    token = csrf(c, "/logs/loraham-kiss-tnc?job=r1/rf-kiss.log&radio=r1")
    before = _all_files(tmp_path)
    r = c.post(path, data={"_csrf": token, "job": "r1/rf-kiss.log", "radio": radio, **form})
    assert r.status_code == code
    assert _all_files(tmp_path) == before                                           # nothing cleared or saved


@pytest.mark.parametrize("url", ["/logs/loraham-kiss-tnc?radio=r1&band=433", "/api/logs/loraham-kiss-tnc?band=433",
                                 "/logs/loraham-kiss-tnc?job=rf-kiss.log&band=433"])
def test_g1_w5_a_band_on_the_log_routes_is_refused_by_name(box, url):
    c, _tmp = box
    r = c.get(url)
    assert (r.status_code, "band is a radio's attribute: use radio=<rid>" in r.get_data(as_text=True)) == (400, True)


def test_g1_w5_n1_a_radios_process_log_badge_is_that_instances(box, monkeypatch):
    from lhpc.core import instances
    monkeypatch.setattr(instances, "recorded", lambda svc, sid: {"r2"} if sid == "kiss" else set())
    c, _tmp = box
    badge = {rid: parse(c.get(f"/logs/loraham-kiss-tnc?radio={rid}").get_data(as_text=True)).by_id("log-status")
             for rid in ("r1", "r2")}
    assert (badge["r1"].has_class("badge-stopped"), badge["r2"].has_class("badge-running")) == (True, True)
    assert [c.get(f"/api/logs/loraham-kiss-tnc?radio={rid}").get_json()["running"] for rid in ("r1", "r2")] == [False, True]


def test_g1_w5_the_stray_band_daemons_log_link_names_its_own_file(box, monkeypatch):
    from lhpc.core.services import ControllerService
    real = ControllerService.radio_overview
    monkeypatch.setattr(ControllerService, "radio_overview", lambda self: [
        *real(self), {**real(self)[0], "band": "868", "stray": True, "radios": [], "running": [], "rflogs": []}])
    c, _tmp = box
    doc = parse(c.get("/").get_data(as_text=True))
    card = doc.within(next(d for d in doc.find("div", class_="radio-stray")))
    links = [parse_qs(urlsplit(a["href"]).query) for a in card.find("a") if urlsplit(a["href"]).path == "/logs/loraham-daemon"]
    assert links == [{"job": ["start-loraham-daemon-868.log"]}]                      # no band param, no newest-log pick


def test_clear_all_also_empties_every_radios_own_rf_logs(box, csrf):
    # DEC-W384bo: "Clear all RF logs" said every log was cleared but left each radio's <rid>/<file>; now it empties them
    # (and removes their previous segment) through the same per-job clear; run logs are never touched
    c, tmp_path = box
    logs = tmp_path / "logs"
    (logs / "r2" / "rf-daemon.log").write_text("2026-11-09T19:00:00.000Z TX rssi=- snr=- len=1 hex=02\n")
    (logs / "r2" / "rf-kiss.log.1").write_text("older\n")
    token = csrf(c, "/logs/loraham-kiss-tnc?job=r1/rf-kiss.log&radio=r1")
    r = c.post("/rflog/clear-all", data={"_csrf": token, "job": "r1/rf-kiss.log", "radio": "r1"})
    assert r.status_code == 302
    assert {f: (logs / f).read_text() for f in ("rf-kiss.log", "r1/rf-kiss.log", "r2/rf-kiss.log", "r2/rf-daemon.log")} == \
        dict.fromkeys(("rf-kiss.log", "r1/rf-kiss.log", "r2/rf-kiss.log", "r2/rf-daemon.log"), "")
    assert not (logs / "r2" / "rf-kiss.log.1").exists()
    assert (logs / "start-loraham-kiss-tnc-433-r1.log").read_text() == "process-of-r1\n"           # a run log stays

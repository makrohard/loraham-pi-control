""""Radio config" and "Daemon log" are gone from a radio card (the daemon row's config / logs open the
same pages); the card's RF-log links (each link's target / job / radio, an unreadable entry's error pill) sit in the
daemon component row, after its config / logs, as logslink links, and nowhere else on the card. The component macro
gets that line from its callers: the instance's and an orphan entry's (rid ""), both with the card's radio. A stopped
daemon's card has no log links; a stray band card keeps its Stop and its links. Driven through the rendered Home page."""
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "left", "uputronics", "433", ce="ce0"), radios.Radio("r2", "right", "uputronics", "433", ce="ce1"))
READY = b"STATUS ID={} BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
RF = [{"label": "daemon r1 (433)", "target": "loraham-daemon", "job": "r1/rf-daemon.log"},
      {"label": "daemon r2 (433)", "target": "loraham-daemon", "job": "r2/rf-daemon.log"},
      {"error": "rf-log dir unreadable"},
      {"label": "reticulum", "target": "rns", "job": "rf-reticulum.log"}]


@pytest.fixture
def procs():
    out = []
    yield out
    for p in out:
        p.kill()
        p.wait(timeout=5)


def _home(web, tmp_path, monkeypatch, procs, up=("r1", "r2")):
    radios_fixture.write(tmp_path, TWO_433)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    monkeypatch.setattr(ControllerService, "_rflogs_for_band", lambda self, band, running: [dict(x) for x in RF])
    fake = FakeSystem(unix_replies={f"/tmp/loraconf-{rid}.sock": READY.replace(b"{}", rid.encode()) for rid in up})
    svc = lambda: ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))  # noqa: E731
    for rid in up:                             # a verified launch record of daemon@<rid> (a live process lhpc owns)
        procs.append(subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True))
        s = svc()
        stk = s.stack("daemon")
        assert s._lifecycle().record_launch(stk, stk.main_component, procs[-1].pid, band="433", extra={"rid": rid})
    page = web(service_factory=svc).get("/")
    assert page.status_code == 200
    return parse(page.get_data(as_text=True))


def _q(a):
    u = urlsplit(a["href"])
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


def _daemon_row(doc, card):
    logs = card.find("a", **{"aria-label": "loraham-daemon logs"})
    assert len(logs) == 1
    rows = [li for li in card.find("li", class_="crow") if li.index < logs[0].index <= li.end]
    assert len(rows) == 1
    return card.within(rows[0]), logs[0]


def _no_card_links(card):
    assert not [a for a in card.find("a") if a.text.strip() in ("Radio config", "Daemon log")]
    assert not card.find("p", class_="links")


def _row_rf(doc, card, rid):
    row, logs = _daemon_row(doc, card)
    lines = card.find("span", class_="rfline")
    assert len(lines) == 1 and row.find("span", class_="rfline") == lines          # only in the daemon row
    assert doc.index(logs) < doc.index(lines[0])                                    # after its config / logs
    rf = card.within(lines[0])
    assert [_q(a) for a in rf.find("a")] == [
        ("/logs/loraham-daemon", {"job": f"{rid}/rf-daemon.log", "radio": rid}),
        ("/logs/rns", {"job": "rf-reticulum.log", "radio": rid})]
    assert all(a.has_class("logslink") and not a.has_class("pill") for a in rf.find("a"))
    assert [p.text for p in rf.find("span", class_="pill")] == ["rf-log dir unreadable"]
    assert rf.text.startswith("RF log:")


@pytest.mark.parametrize("rid", ["r1", "r2"])
def test_the_rf_links_sit_in_the_instances_daemon_row(web, tmp_path, monkeypatch, procs, rid):
    doc = _home(web, tmp_path, monkeypatch, procs)
    card = doc.within(doc.by_id(f"radio-{rid}"))
    _no_card_links(card)
    _row_rf(doc, card, rid)
    other = [li for li in card.find("li", class_="crow") if card.within(li).find("span", class_="rfline")]
    assert len(other) == 1                                                          # no other component row has it


def test_an_orphan_daemon_entry_gets_the_line_with_the_cards_radio(web, tmp_path, monkeypatch, procs):
    real = ControllerService.radio_overview

    def orphaned(self, strays=None):           # the orphan caller (rid ""): the same entry without an instance
        out = real(self, strays)
        for e in out:
            e["running"] = [dict(s, rid="", id="daemon") if s.get("sid") == "daemon" else s for s in e["running"]]
        return out
    monkeypatch.setattr(ControllerService, "radio_overview", orphaned)
    doc = _home(web, tmp_path, monkeypatch, procs)
    card = doc.within(doc.by_id("radio-r2"))
    assert card.present("noinst-r2-daemon")                                         # rendered by the orphan branch
    _no_card_links(card)
    _row_rf(doc, card, "r2")


def test_a_stopped_daemons_card_has_no_log_links(web, tmp_path, monkeypatch, procs):
    doc = _home(web, tmp_path, monkeypatch, procs, up=())
    for rid in ("r1", "r2"):
        card = doc.within(doc.by_id(f"radio-{rid}"))
        _no_card_links(card)
        assert not card.find("span", class_="rfline")
        assert not [a for a in card.find("a") if urlsplit(a["href"] or "").path.startswith("/logs/")]


def test_a_stray_band_card_keeps_its_stop_and_links(web, tmp_path, monkeypatch, procs):
    real = ControllerService.radio_overview

    def with_stray(self, strays=None):
        out = real(self, strays)
        if strays is not None:
            strays.append({"band": "868", "daemon": out[0]["daemon"], "shared_with": [],
                           "rflogs": [{"label": "daemon 868", "target": "loraham-daemon", "job": "rf-daemon-868.log"}]})
        return out
    monkeypatch.setattr(ControllerService, "radio_overview", with_stray)
    doc = _home(web, tmp_path, monkeypatch, procs, up=())
    card = doc.within(next(d for d in doc.find("div", class_="radio-stray")))
    stops = [f for f in card.find("form", class_="act") if card.within(f).field_default("op") == "stop"]
    assert [(card.within(f).field_default("target"), card.within(f).field_default("band")) for f in stops] == [("daemon", "868")]
    assert [_q(a) for a in card.find("a") if urlsplit(a["href"]).path == "/logs/loraham-daemon"] == [
        ("/logs/loraham-daemon", {"job": "start-loraham-daemon-868.log"}), ("/logs/loraham-daemon", {"job": "rf-daemon-868.log"})]
    assert [a.text for a in card.find("a") if a.text == "Daemon log"] == ["Daemon log"]

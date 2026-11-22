"""Dash plan c1: a radio card has no daemon Start / Stop / Restart form and no "Daemon not running" hint of its own; while
THIS radio's daemon is installed and stopped, "daemon <band>" (target daemon@<rid>, the old form's op/CSRF/from fields) is
the first entry of "Start a stack on <rid>", exactly once, also when another radio of the band runs its daemon. Not
installed: the note and its link stay, no entry. A running daemon with a verified launch record shows under "Running on
<rid>" with its Stop / Restart (daemon@<rid>), and the card has no pair of its own; a running daemon that no record lists
there has no Stop on Home at all (it stops from Apps, the maintainer's Q15 "OK ii"). Driven through the rendered Home page."""
import subprocess
import sys

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "left", "uputronics", "433", ce="ce0"), radios.Radio("r2", "right", "uputronics", "433", ce="ce1"))
READY = b"STATUS ID={} BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


@pytest.fixture
def procs():
    out = []
    yield out
    for p in out:
        p.kill()
        p.wait(timeout=5)


def _home(web, tmp_path, monkeypatch, up=(), installed=True, recorded=(), procs=None):
    radios_fixture.write(tmp_path, TWO_433)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: installed)
    fake = FakeSystem(unix_replies={f"/tmp/loraconf-{rid}.sock": READY.replace(b"{}", rid.encode()) for rid in up})
    svc = lambda: ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))  # noqa: E731
    for rid in recorded:                       # a verified launch record of daemon@<rid> (a live process lhpc owns)
        procs.append(subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True))
        s = svc()
        stk = s.stack("daemon")
        assert s._lifecycle().record_launch(stk, stk.main_component, procs[-1].pid, band="433", extra={"rid": rid})
    page = web(service_factory=svc).get("/")
    assert page.status_code == 200
    return parse(page.get_data(as_text=True))


def _card(doc, rid):
    return doc.within(doc.by_id(f"radio-{rid}"))


def _start_options(card):
    forms = card.find("form", class_="startform")
    if not forms:
        return [], None
    form = card.within(forms[0])
    return [(o["value"], o.text.strip()) for o in form.find("option")], form


def _card_daemon_acts(card):
    return [f for f in card.find("form", class_="act") if (card.within(f).field_default("target") or "").startswith("daemon")]


def test_a_stopped_installed_daemon_leads_the_start_list(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch)
    for rid in ("r1", "r2"):
        card = _card(doc, rid)
        opts, form = _start_options(card)
        assert opts and opts[0] == (f"daemon@{rid}", "daemon 433")
        assert [v for v, _t in opts].count(f"daemon@{rid}") == 1 and not [v for v, _t in opts[1:] if v.startswith("daemon")]
        assert (form.field_default("op"), form.field_default("from"), form.field_default("target")) == ("start", "dash", f"daemon@{rid}")
        assert form.field_default("_csrf")
        assert not _card_daemon_acts(card) and "Daemon not running" not in card.text
        assert [h.text.strip() for h in card.find("h3")].count(f"Start a stack on {rid}") == 1


def _acts(scope, forms):
    return [(scope.within(f).field_default("op"), scope.within(f).field_default("target")) for f in forms]


def test_a_recorded_running_daemon_stops_from_running_on_only(web, tmp_path, monkeypatch, procs):
    doc = _home(web, tmp_path, monkeypatch, up=("r1", "r2"), recorded=("r1", "r2"), procs=procs)
    for rid in ("r1", "r2"):
        card = _card(doc, rid)
        opts, _form = _start_options(card)
        assert not [v for v, _t in opts if v.startswith("daemon")]
        assert not [b for b in card.find("button") if b.text.strip().startswith(("Stop daemon", "Restart daemon"))]
        rows = [p for p in card.find("p") if p.has_attr("data-stack") and p["data-stack"].startswith("daemon")]
        assert [p["data-stack"] for p in rows] == [f"daemon@{rid}"]          # its own instance, under "Running on <rid>"
        runs = [d for d in card.find("div", class_="runstack") if d.index < rows[0].index <= d.end]
        assert len(runs) == 1
        run = card.within(runs[0])
        assert _acts(run, run.find("form", class_="act")) == [("stop", f"daemon@{rid}"), ("restart", f"daemon@{rid}")]
        assert _acts(card, _card_daemon_acts(card)) == [("stop", f"daemon@{rid}"), ("restart", f"daemon@{rid}")]  # only there


def test_an_unrecorded_running_daemon_has_no_home_stop(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch, up=("r1", "r2"))
    for rid in ("r1", "r2"):
        card = _card(doc, rid)
        opts, _form = _start_options(card)
        assert not [v for v, _t in opts if v.startswith("daemon")]
        assert not [p for p in card.find("p") if p.has_attr("data-stack") and p["data-stack"].startswith("daemon")]
        assert not _card_daemon_acts(card)
        assert not [b for b in card.find("button") if b.text.strip().startswith(("Stop daemon", "Restart daemon"))]


def test_one_radio_running_the_other_stopped_each_card_its_own(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch, up=("r1",))
    r1, _ = _start_options(_card(doc, "r1"))
    r2, _ = _start_options(_card(doc, "r2"))
    assert not [v for v, _t in r1 if v.startswith("daemon")]
    assert r2 and r2[0] == ("daemon@r2", "daemon 433")               # the band's other daemon runs: r2 still offers its own


def test_not_installed_keeps_the_note_and_offers_no_daemon(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch, installed=False)
    card = _card(doc, "r1")
    opts, _form = _start_options(card)
    assert not [v for v, _t in opts if v.startswith("daemon")]
    assert "Daemon not installed. Install & build the" in " ".join(card.text.split())
    assert card.find("a", href="/stacks?open=daemon#stackrow-daemon")


@pytest.mark.parametrize("rid", ["r1", "r2"])
def test_the_entry_names_its_own_radio(web, tmp_path, monkeypatch, rid):
    doc = _home(web, tmp_path, monkeypatch)
    opts, _form = _start_options(_card(doc, rid))
    assert opts[0][0] == f"daemon@{rid}"

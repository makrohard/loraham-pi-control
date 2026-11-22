"""Dash plan c2: a radio card's header title is plain text (no link) and the body has no "<name> (<model>)" hardware
line any more; a "Hardware" link (class logslink, id rd-hw-<rid>) to /hardware/radio/<rid> sits below the RX-TX Monitor
while the daemon runs, and above "Running on <rid>" while it is stopped. It is the card's only link to its hardware page.
Driven through the rendered Home page."""
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "left", "uputronics", "433", ce="ce0"), radios.Radio("r2", "right", "uputronics", "433", ce="ce1"))
READY = b"STATUS ID=r1 BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


def _home(web, tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, TWO_433)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": READY})  # r1 up, r2 stopped
    page = web(service_factory=lambda: ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))).get("/")
    assert page.status_code == 200
    return parse(page.get_data(as_text=True))


def _running_on(doc, card, rid):
    hs = [h for h in card.find("h3") if h.text.startswith(f"Running on {rid}")]
    assert len(hs) == 1, [h.text for h in card.find("h3")]
    return hs[0]


def _hw_link(doc, card, rid):
    a = doc.by_id(f"rd-hw-{rid}")
    assert a is not None and card.present(f"rd-hw-{rid}")
    assert a["href"] == f"/hardware/radio/{rid}"
    assert a.has_class("logslink") and a.text == "Hardware"
    hw = [x for x in card.find("a") if x.has_attr("href") and x["href"].startswith(f"/hardware/radio/{rid}")]
    assert hw == [a], [x.text for x in hw]  # the old "<name> (<model>)" line is gone
    return a


def _plain_title(card):
    h2 = card.find("h2")[0]
    assert card.within(h2).find("a") == []
    assert "(uputronics)" not in card.text


def test_running_card_hardware_link_below_monitor(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch)
    card = doc.within(doc.by_id("radio-r1"))
    _plain_title(card)
    a = _hw_link(doc, card, "r1")
    feed = doc.by_id("rd-feed-r1")  # last element of the RX-TX Monitor
    assert feed is not None and doc.index(doc.by_id("monitor-r1")) < doc.index(feed) < doc.index(a)
    assert doc.index(a) < doc.index(_running_on(doc, card, "r1"))


def test_stopped_card_hardware_link_above_running_on(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch)
    card = doc.within(doc.by_id("radio-r2"))
    _plain_title(card)
    assert not card.present("monitor-r2")
    a = _hw_link(doc, card, "r2")
    assert doc.index(a) < doc.index(_running_on(doc, card, "r2"))

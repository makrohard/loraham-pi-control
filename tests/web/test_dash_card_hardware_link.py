"""A radio card's header title is plain text (no link) and the body has no "<name> (<model>)" hardware
line any more; a "Hardware" link (class logslink, id rd-hw-<rid>) to /hardware/radio/<rid> sits at the very bottom of
the radio card (F19, his word; it sat below the RX-TX Monitor / above "Running on"), daemon running or stopped. It is the
card's only link to its hardware page.
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


def _last_in_card(doc, rid, a):
    """The link's paragraph is the radio card body's last element (red before F19: it sat mid-card)."""
    box = doc.by_id(f"radiobox-{rid}")
    [body] = [d for d in doc.within(box).find("div", class_="body") if d.index > box.index][:1]
    [p] = [x for x in doc.within(body).find("p", class_="hwlink")
           if doc.within(x).find("a", id=f"rd-hw-{rid}")]
    assert p.end == body.end and doc.index(a) > doc.index(_running_on(doc, doc.within(box), rid))


def test_running_card_hardware_link_at_the_bottom(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch)
    card = doc.within(doc.by_id("radio-r1"))
    _plain_title(card)
    a = _hw_link(doc, card, "r1")
    assert doc.index(doc.by_id("rd-feed-r1")) < doc.index(a)
    _last_in_card(doc, "r1", a)


def test_stopped_card_hardware_link_at_the_bottom(web, tmp_path, monkeypatch):
    doc = _home(web, tmp_path, monkeypatch)
    card = doc.within(doc.by_id("radio-r2"))
    _plain_title(card)
    assert not card.present("monitor-r2")
    _last_in_card(doc, "r2", _hw_link(doc, card, "r2"))


def test_the_monitor_ends_with_its_radios_live_settings_link(web, tmp_path, monkeypatch):
    # F20 (his word; red before: no such link): "Daemon live settings", part of the RX-TX Monitor (inside its
    # <details>, after the feed), links that radio's Live radio settings level, which exists (a 200 page)
    doc = _home(web, tmp_path, monkeypatch)
    mon, feed, a = doc.by_id("monitor-r1"), doc.by_id("rd-feed-r1"), doc.by_id("rd-live-r1")
    assert a is not None and mon.index < feed.index < a.index <= mon.end
    assert a.has_class("logslink") and a.text == "Daemon live settings"
    assert a["href"] == "/stacks/daemon/menu/stack-settings-daemon--r1-live?radio=r1"
    assert not doc.by_id("rd-live-r2")                                              # r2 stopped: no Monitor, no link
    radios_fixture.write(tmp_path, TWO_433)
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": READY})
    c = web(service_factory=lambda: ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)))
    assert c.get(a["href"]).status_code == 200

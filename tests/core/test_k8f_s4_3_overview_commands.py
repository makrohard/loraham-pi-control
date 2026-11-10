"""K8f S4-3 (D2-CTX B4, D3 B2): the radio overview's launch commands are each radio's own. Two radios on one band are
two cards; an interactive app shown on both, and an interactive component of a stack both cards list, carry one
command per radio (its own config dir and daemon sockets), never one band value written for every card."""
import re

import radios_fixture

from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

READY = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"


def _svc(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))          # r1 and r2, both on 433
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": READY, "/tmp/loraconf-r2.sock": READY})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def _card(page, rid):
    """The HTML of radio `rid`'s dashboard card (from its id to the next card)."""
    start = page.index(f'id="radio-{rid}"')
    nxt = page.find('<div class="card', start + 1)
    return page[start:nxt if nxt != -1 else len(page)]


def _boxes(html):
    """The text of every command box in `html`."""
    return re.findall(r'<pre class="next" id="[^"]*">(.*?)</pre>', html, re.S)


def test_an_app_shown_on_two_radios_of_one_band_shows_each_card_its_own_command(tmp_path, web):
    # chat shown on r1 and on r2 (both 433, both daemons ready): r2's card runs chat@r2 (r2's config dir and sockets).
    # red on f6c9fe2c: both cards show r1's command (one entry["command"] for the band column)
    svc = _svc(tmp_path)
    for rid in ("r1", "r2"):
        assert svc.mark_shown("chat", Instance(rid, band="433"))
    page = web(service_factory=lambda: svc).get("/").get_data(as_text=True)
    r1, r2 = (" ".join(_boxes(_card(page, rid))) for rid in ("r1", "r2"))
    assert "/tmp/lora-r2.sock" in r2 and "/config/files/r2/" in r2, r2
    assert "/tmp/lora-r1.sock" not in r2, r2
    assert "/tmp/lora-r1.sock" in r1 and "/tmp/lora-r2.sock" not in r1, r1


def test_a_copy_button_copies_its_own_cards_command(tmp_path, web):
    # each command box id is its card's, so the copy button on r2's card copies r2's command (copy.js: getElementById).
    # red on f6c9fe2c: both cards' boxes have id "cmd-chat"; the first (r1's) is what r2's button copies
    svc = _svc(tmp_path)
    for rid in ("r1", "r2"):
        assert svc.mark_shown("chat", Instance(rid, band="433"))
    page = web(service_factory=lambda: svc).get("/").get_data(as_text=True)
    for rid in ("r1", "r2"):
        ids = re.findall(r'data-copy="([^"]*)"', _card(page, rid))
        assert ids, rid
        for i in ids:
            box = re.search(rf'<pre class="next" id="{re.escape(i)}">(.*?)</pre>', page, re.S)
            assert box and f"/tmp/lora-{rid}.sock" in box.group(1), (rid, i)


def _component_command(comp, rid):
    """The command a card of radio `rid` shows for `comp` (the per-radio map; the old scalar on the base)."""
    return comp.get("commands", {}).get(rid, comp.get("command"))


def test_an_interactive_component_carries_a_command_per_radio(tmp_path):
    # voice is listed on r1's and r2's cards; its terminal client (loraham-voice-cli) reads its radio's sockets, so each
    # card's component row is its radio's. red on f6c9fe2c: components[].command is one value, r1's, on both cards
    svc = _svc(tmp_path)
    for card in svc.radio_overview():
        voice = next(x for x in card["startable"] + card["interactive"] if x["id"] == "voice")
        cli = next(c for c in voice["components"] if c["id"] == "loraham-voice-cli")
        rid = card["rid"]
        other = "r1" if rid == "r2" else "r2"
        cmd = _component_command(cli, rid)
        assert f"/tmp/lora-{rid}.sock" in cmd and f"/tmp/lora-{other}.sock" not in cmd, (rid, cmd)

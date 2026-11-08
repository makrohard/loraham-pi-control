"""Web: the per-band +20 dBm permission control on the daemon's Hardware settings, and the
SX127x hazard banner that keys on the RUNNING daemon's STATUS (never on the saved switch)."""

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

DAEMON_PAGE = "/stacks?open=daemon&cfg=daemon"


def _status(family="SX127x", highpower="0"):
    return (f"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
            f"HIGHPOWER={highpower} CHIPFAMILY={family}\n").encode()


def _page(c, path=DAEMON_PAGE):
    return parse(c.get(path).get_data(as_text=True))


def _client(web, tmp_path, setup, status):
    p = Paths(runtime_root=tmp_path)
    from lhpc.core import radio_start
    assert radio_start.setup_radios(p, setup)[0]        # S2: the list `lhpc hardware <setup>` writes decides the board
    cfgmod.save_hardware_setup(p, setup)
    fs = FakeSystem(unix_replies={dc.conf_socket(r): status for r in ("r1", "r2")})
    return web(system=fs.system, paths=p), p


@pytest.mark.contract
def test_control_saves_through_the_form_and_refuses_junk(web, csrf, tmp_path):
    c, p = _client(web, tmp_path, "uputronics", _status("SX127x", "0"))
    doc = _page(c)
    assert doc.present("high-power-daemon") and doc.find("form", action="/hardware/high-power")
    assert "warranty void" in doc.text.lower() and "1 %" in doc.text
    tok = csrf(c, DAEMON_PAGE)
    r = c.post("/hardware/high-power", data={"_csrf": tok, "rid": "r1", "value": "on"})     # K2: per radio
    assert r.status_code in (302, 303)
    doc = _page(c)
    assert doc.present("hp-mismatch-r1") and "restart the daemon" in doc.text    # granted, running off
    assert not doc.present("hp-warn-r1")                                          # running says OFF
    # Junk never becomes ON.
    r = c.post("/hardware/high-power", data={"_csrf": tok, "rid": "r2", "value": "banana"})
    assert r.status_code in (302, 303)
    assert not _page(c).present("hp-mismatch-r2")
    # No CSRF token -> refused.
    assert c.post("/hardware/high-power", data={"rid": "r1", "value": "off"}).status_code == 400


@pytest.mark.contract
def test_banner_keys_on_the_running_sx127x_permission(web, tmp_path):
    c, _ = _client(web, tmp_path, "uputronics", _status("SX127x", "1"))
    doc = _page(c)
    assert doc.present("hp-warn-r1")                      # running ON, not granted: banner stays
    assert doc.present("hp-mismatch-r1") and "not granted" in doc.text
    assert _page(c, "/").present("rd-hp-r1")              # the dashboard card is r1's (433 on the list)
    assert doc.present("dp-hp-daemon")                    # the daemon-params panel too


@pytest.mark.contract
def test_no_sx127x_banner_on_an_sx1262(web, tmp_path):
    c, _ = _client(web, tmp_path, "waveshare-433", _status("SX1262", "1"))
    doc = _page(c)
    assert not doc.present("hp-warn-433") and not doc.present("dp-hp-daemon")
    assert not _page(c, "/").present("rd-hp-433")
    assert doc.present("high-power-daemon")               # the same control exists on every preset


def _radio_page(web, tmp_path, model, status):
    """Radio r1's page (the redesign's place of the radio card's banner, W257) with r1 of `model` on 433 whose daemon
    answers `status`."""
    import radios_fixture

    from lhpc.core import radios
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", model, "433", **({"ce": "ce0"} if model == "uputronics" else {})),))
    fs = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": status.replace(b"STATUS ", b"STATUS ID=r1 BOUND=- ", 1)})
    return parse(web(system=fs.system, paths=Paths(runtime_root=tmp_path)).get("/hardware/radio/r1").get_data(as_text=True))


def test_the_radio_page_warns_on_the_running_sx127x_permission(web, tmp_path):
    # Moved from Home's radio card (rd-hp-433, W250/W257): the radio's own page carries the banner.
    assert _radio_page(web, tmp_path, "uputronics", _status("SX127x", "1")).present("hw-hp-warn")


@pytest.mark.contract
def test_no_sx127x_warning_on_an_sx1262_radio_page(web, tmp_path):
    # Moved from Home's radio card (W250/W257).
    assert not _radio_page(web, tmp_path, "waveshare-sx1262", _status("SX1262", "1")).present("hw-hp-warn")

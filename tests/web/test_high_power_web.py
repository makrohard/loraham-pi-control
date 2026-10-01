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
    cfgmod.save_hardware_setup(p, setup)
    fs = FakeSystem(unix_replies={dc.conf_socket(b): status for b in ("433", "868")})
    return web(system=fs.system, paths=p), p


@pytest.mark.contract
def test_control_saves_through_the_form_and_refuses_junk(web, csrf, tmp_path):
    c, p = _client(web, tmp_path, "uputronics", _status("SX127x", "0"))
    doc = _page(c)
    assert doc.present("high-power-daemon") and doc.find("form", action="/hardware/high-power")
    assert "warranty void" in doc.text.lower() and "1 %" in doc.text
    tok = csrf(c, DAEMON_PAGE)
    r = c.post("/hardware/high-power", data={"_csrf": tok, "band": "433", "value": "on"})
    assert r.status_code in (302, 303)
    doc = _page(c)
    assert doc.present("hp-mismatch-433") and "restart the daemon" in doc.text   # saved on, running off
    assert not doc.present("hp-warn-433")                                         # running says OFF
    # Junk never becomes ON.
    r = c.post("/hardware/high-power", data={"_csrf": tok, "band": "868", "value": "banana"})
    assert r.status_code in (302, 303)
    assert not _page(c).present("hp-mismatch-868")
    # No CSRF token -> refused.
    assert c.post("/hardware/high-power", data={"band": "433", "value": "off"}).status_code == 400


@pytest.mark.contract
def test_banner_keys_on_the_running_sx127x_permission(web, tmp_path):
    c, _ = _client(web, tmp_path, "uputronics", _status("SX127x", "1"))
    doc = _page(c)
    assert doc.present("hp-warn-433")                     # running ON, saved off: banner stays
    assert doc.present("hp-mismatch-433") and "saved off" in doc.text
    assert _page(c, "/").present("rd-hp-433")
    assert doc.present("dp-hp-daemon")                    # the daemon-params panel too


@pytest.mark.contract
def test_no_sx127x_banner_on_an_sx1262(web, tmp_path):
    c, _ = _client(web, tmp_path, "waveshare-433", _status("SX1262", "1"))
    doc = _page(c)
    assert not doc.present("hp-warn-433") and not doc.present("dp-hp-daemon")
    assert not _page(c, "/").present("rd-hp-433")
    assert doc.present("high-power-daemon")               # the same control exists on every preset

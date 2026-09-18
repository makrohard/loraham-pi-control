"""Web: the per-band +20 dBm permission control on the daemon's Hardware settings, and the
SX127x hazard banner that keys on the RUNNING daemon's STATUS (never on the saved switch)."""

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

DAEMON_PAGE = "/stacks?open=daemon&cfg=daemon"


def _status(family="SX127x", highpower="0"):
    return (f"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
            f"HIGHPOWER={highpower} CHIPFAMILY={family}\n").encode()


def _client(web, tmp_path, setup, status):
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    fs = FakeSystem(unix_replies={dc.conf_socket(b): status for b in ("433", "868")})
    return web(system=fs.system, paths=p), p


@pytest.mark.contract
def test_control_saves_through_the_form_and_refuses_junk(web, csrf, tmp_path):
    c, p = _client(web, tmp_path, "uputronics", _status("SX127x", "0"))
    body = c.get(DAEMON_PAGE).get_data(as_text=True)
    assert 'id="high-power-daemon"' in body and 'action="/hardware/high-power"' in body
    assert "warranty void" in body.lower() and "1 %" in body
    tok = csrf(c, DAEMON_PAGE)
    r = c.post("/hardware/high-power", data={"_csrf": tok, "band": "433", "value": "on"})
    assert r.status_code in (302, 303)
    body = c.get(DAEMON_PAGE).get_data(as_text=True)
    assert 'id="hp-mismatch-433"' in body and "restart the daemon" in body   # saved on, running off
    assert 'id="hp-warn-433"' not in body                                     # running says OFF
    # Junk never becomes ON.
    r = c.post("/hardware/high-power", data={"_csrf": tok, "band": "868", "value": "banana"})
    assert r.status_code in (302, 303)
    body = c.get(DAEMON_PAGE).get_data(as_text=True)
    assert 'id="hp-mismatch-868"' not in body
    # No CSRF token -> refused.
    assert c.post("/hardware/high-power", data={"band": "433", "value": "off"}).status_code == 400


@pytest.mark.contract
def test_banner_keys_on_the_running_sx127x_permission(web, tmp_path):
    c, _ = _client(web, tmp_path, "uputronics", _status("SX127x", "1"))
    body = c.get(DAEMON_PAGE).get_data(as_text=True)
    assert 'id="hp-warn-433"' in body                     # running ON, saved off: banner stays
    assert 'id="hp-mismatch-433"' in body and "saved off" in body
    dash = c.get("/").get_data(as_text=True)
    assert 'id="rd-hp-433"' in dash
    assert 'id="dp-hp-daemon"' in body                    # the daemon-params panel too


@pytest.mark.contract
def test_no_sx127x_banner_on_an_sx1262(web, tmp_path):
    c, _ = _client(web, tmp_path, "waveshare-433", _status("SX1262", "1"))
    body = c.get(DAEMON_PAGE).get_data(as_text=True)
    assert 'id="hp-warn-433"' not in body and 'id="dp-hp-daemon"' not in body
    assert 'id="rd-hp-433"' not in c.get("/").get_data(as_text=True)
    assert 'id="high-power-daemon"' in body               # the same control exists on every preset

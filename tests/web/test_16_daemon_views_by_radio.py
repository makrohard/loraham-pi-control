"""Item 16 batch 2 (DEC-W53): the daemon's live views are addressed by radio, `?rid=rN`, with the band alone kept for
the single-radio case. Two radios of 433 each answer on their own CONF socket; a rid that is no enabled radio of the
band is a 404 naming why, never a 500, and nothing is read for it."""
import pytest
import radios_fixture

from lhpc.core import radios
from lhpc.core.probes.backends import FakeSystem

TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"),
           radios.Radio("r3", "c", "meshstick-1262", "868", usb_serial="MS654321"),
           radios.Radio("r4", "d", "meshstick-1262", "868", enabled=False, usb_serial="MS123456"))


def _status(rid):
    return f"STATUS ID={rid} BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n".encode()


@pytest.fixture
def client(web, tmp_path):
    radios_fixture.write(tmp_path, TWO_433)
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": _status("r1"), "/tmp/loraconf-r2.sock": _status("r2")})
    return web(system=fake.system), fake


@pytest.mark.parametrize("rid", ["r1", "r2"])
def test_each_radio_of_a_shared_band_answers_on_its_own_socket(client, rid):
    c, _fake = client
    r = c.get(f"/api/daemon/433?rid={rid}")
    assert r.status_code == 200
    j = r.get_json()
    assert (j["rid"], j["ready"], j["status"].get("ID")) == (rid, True, rid)
    s = c.get(f"/api/daemon/433/socket?rid={rid}").get_json()
    assert s["rid"] == rid and f"ID={rid}" in s["line"]


def test_the_band_alone_names_no_one_radio_on_a_shared_band(client):
    c, _fake = client
    assert c.get("/api/daemon/433").get_json()["reachable"] is False


@pytest.mark.parametrize("path", ["/api/daemon/433", "/api/daemon/433/feed", "/api/daemon/433/socket"])
@pytest.mark.parametrize("rid, why", [("r3", "r3 is on 868 MHz, not 433 MHz"), ("r4", "r4 is disabled"),
                                      ("r9", "r9 is not a radio of the hardware list"),
                                      ("../x", "../x is not a radio of the hardware list")])
def test_a_rid_that_is_no_enabled_radio_of_the_band_is_a_404_naming_why(client, path, rid, why):
    c, _fake = client
    r = c.get(f"{path}?rid={rid}")
    assert (r.status_code, r.get_json()) == (404, {"error": why})

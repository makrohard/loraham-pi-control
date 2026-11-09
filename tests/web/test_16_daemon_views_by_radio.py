"""The daemon's live views and live settings are addressed by radio (CLEANUP-DESIGN-v13 13.3, WEB-LANE-PLAN-v5 W1):
`/api/daemon/<rid>` (+ `/feed`, `/scan`, `/socket`) and `/radio/<rid>/set`. Two radios of 433 each answer on their own
CONF socket; a band is no radio (404), and a rid that is no enabled radio of the hardware list is a 404 naming why,
never a 500, and nothing is read for it."""
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
    r = c.get(f"/api/daemon/{rid}")
    assert r.status_code == 200
    j = r.get_json()
    assert (j["rid"], j["band"], j["ready"], j["status"].get("ID")) == (rid, "433", True, rid)
    s = c.get(f"/api/daemon/{rid}/socket").get_json()
    assert s["rid"] == rid and f"ID={rid}" in s["line"]
    assert c.get(f"/api/daemon/{rid}/feed").get_json()["rid"] == rid


@pytest.mark.parametrize("path", ["/api/daemon/433", "/api/daemon/433/feed", "/api/daemon/433/socket"])
def test_a_band_is_no_radio(client, path):
    c, _fake = client
    r = c.get(path)
    assert (r.status_code, r.get_json()) == (404, {"error": "433 is not a radio of the hardware list"})


@pytest.mark.parametrize("sub", ["", "/feed", "/socket"])
@pytest.mark.parametrize("rid, why", [("r4", "r4 is disabled"), ("r9", "r9 is not a radio of the hardware list")])
def test_a_rid_that_is_no_enabled_radio_is_a_404_naming_why(client, sub, rid, why):
    c, fake = client
    r = c.get(f"/api/daemon/{rid}{sub}")
    assert (r.status_code, r.get_json()) == (404, {"error": why})


def test_a_scan_of_a_disabled_radio_is_refused(client, csrf):
    c, _fake = client
    r = c.post("/api/daemon/r4/scan", data={"_csrf": csrf(c)})
    assert (r.status_code, r.get_json()) == (404, {"error": "r4 is disabled"})


def test_a_live_setting_plans_and_applies_on_that_radio_only(client, csrf):
    c, fake = client
    token = csrf(c)
    plan = c.post("/radio/r2/set", data={"_csrf": token, "key": "TXMODE", "value": "DIRECT"})
    assert plan.status_code == 200 and "r2 · 433 MHz" in plan.get_data(as_text=True)
    assert 'action="/radio/r2/set"' in plan.get_data(as_text=True)
    done = c.post("/radio/r2/set", data={"_csrf": token, "key": "TXMODE", "value": "DIRECT", "confirmed": "yes"})
    assert done.status_code == 302 and "radio=r2" in done.headers["Location"]      # W2: the one selector
    sent = [(path, payload) for path, payload in fake.sent if payload.startswith(b"SET ")]
    assert sent and {path for path, _ in sent} == {"/tmp/loraconf-r2.sock"}


def test_a_live_setting_of_a_band_or_a_disabled_radio_is_a_404(client, csrf):
    c, fake = client
    token = csrf(c)
    for path, why in (("/radio/433/set", "433 is not a radio of the hardware list"), ("/radio/r4/set", "r4 is disabled")):
        r = c.post(path, data={"_csrf": token, "key": "TXMODE", "value": "DIRECT", "confirmed": "yes"})
        assert (r.status_code, r.get_json()) == (404, {"error": why})
    assert not [p for p, payload in fake.sent if payload[:4] == b"SET "]


def test_the_daemon_panel_and_the_dashboard_address_each_radio(client):
    c, _fake = client
    body = c.get("/stacks?cfg=daemon&radio=r2").get_data(as_text=True)            # W2: ?radio=, no dp=&rid=
    assert 'id="liveout-r2"' in body and 'data-rid="r2"' in body and 'action="/radio/r2/set"' in body
    assert "liveout-433" not in body and 'data-band="433" data-kind' not in body
    dash = c.get("/").get_data(as_text=True)
    assert 'data-scan-rid="r1"' in dash and 'data-scan-rid="r2"' in dash and "data-scan-band" not in dash


def test_an_fsk_warning_lists_the_stacks_of_that_radio(web, tmp_path, csrf):
    """MODE=FSK breaks LoRa on THAT radio: the confirm page asks for the stacks running on it, not on its band."""
    radios_fixture.write(tmp_path, TWO_433)
    asked = []

    def record(svc):
        def running(band, rid=""):
            asked.append((band, rid))
            return ["kiss@r2"] if rid == "r2" else ["kiss@r1", "kiss@r2"]
        svc.running_lora_stacks = running
        return svc
    c = web(guard=record)
    body = c.post("/radio/r2/set", data={"_csrf": csrf(c), "key": "MODE", "value": "FSK"}).get_data(as_text=True)
    assert asked == [("433", "r2")]
    assert "affected: <strong>kiss@r2</strong>" in body and "kiss@r1" not in body

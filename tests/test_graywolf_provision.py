"""The graywolf provisioning script's convergence contract.

The script is shipped data, not an importable module, so it is loaded by path. The API is faked:
these tests are about WHAT it writes back, which is where the real hazards are — graywolf's
config endpoints are full replacements, so a partial PUT silently resets whatever LHPC does not
send, and a channel an operator edited must converge back to something that works.
"""

import importlib.util
import pathlib

_SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
           / "lhpc" / "data" / "scripts" / "graywolf-provision.py")


def _load():
    spec = importlib.util.spec_from_file_location("graywolf_provision", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeApi:
    """Records every call and answers GETs from a canned state."""

    def __init__(self, state):
        self.state = state
        self.calls = []

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return self.state.get(path)
        if method == "POST" and path == "/api/channels":
            return {"id": 7}
        if method == "POST" and path == "/api/kiss":
            return {"id": 3}
        return None

    def put_to(self, path):
        return [p for m, pth, p in self.calls if m == "PUT" and pth == path]


class Args:
    igate = True
    igate_server = "rotate.aprs2.net"
    igate_port = 14580
    igate_filter = "r/48.4/9.9/100"
    gate_rf_to_is = True
    gate_is_to_rf = False


def test_igate_put_preserves_fields_lhpc_does_not_own():
    """PUT /api/igate/config REPLACES the object. Sending only the LHPC-owned keys would reset
    simulation_mode, is_tx_via and the software identity on every restart — the opposite of what
    the stack docs promise."""
    mod = _load()
    api = FakeApi({"/api/igate/config": {
        "id": 1,
        "enabled": False,
        "server": "euro.aprs2.net",
        "port": 14580,
        "server_filter": "",
        "simulation_mode": True,          # operator-set, LHPC does not own it
        "is_tx_via": "WIDE2-1",           # ditto
        "software_name": "graywolf",
        "software_version": "0.14.12",
        "gate_rf_to_is": False,
        "gate_is_to_rf": False,
        "rf_channel": 0,
        "tx_channel": 0,
    }})

    mod.apply_igate(api, Args(), 7)

    (sent,) = api.put_to("/api/igate/config")
    # untouched, because LHPC does not own them
    assert sent["simulation_mode"] is True
    assert sent["is_tx_via"] == "WIDE2-1"
    assert sent["software_version"] == "0.14.12"
    # applied, because LHPC does
    assert sent["enabled"] is True
    assert sent["server"] == "rotate.aprs2.net"
    assert sent["server_filter"] == "r/48.4/9.9/100"
    assert sent["rf_channel"] == 7 and sent["tx_channel"] == 7
    assert "id" not in sent                      # response-only


def test_channel_in_packet_mode_is_repaired_to_aprs():
    """graywolf logs "beacon skipped: channel mode is packet", so a pure packet channel silently
    stops the station. It must converge back to aprs."""
    mod = _load()
    api = FakeApi({"/api/channels": [
        {"id": 7, "name": "LoRaHAM KISS", "mode": "packet",
         "modem_type": "kiss-only", "backing": {"summary": "kiss-tnc"}},
    ]})

    assert mod.ensure_channel(api, "LoRaHAM KISS") == 7
    (sent,) = api.put_to("/api/channels/7")
    assert sent["mode"] == "aprs"
    assert sent["modem_type"] == "kiss-only"
    assert "id" not in sent and "backing" not in sent      # response-only


def test_channel_repair_strips_response_only_fields():
    """graywolf's PUT decoder uses DisallowUnknownFields, and its channel RESPONSE carries three
    keys the request refuses: id, backing and ptt. `ptt` is omitempty, so it only appears once
    the channel has a PTT row — echo it back and the repair 400s on exactly the boxes that have
    one, while a fresh box looks fine."""
    mod = _load()
    api = FakeApi({"/api/channels": [
        {"id": 7, "name": "LoRaHAM KISS", "mode": "packet", "modem_type": "kiss-only",
         "backing": {"summary": "kiss-tnc"},
         "ptt": {"channel": 7, "kind": "gpio", "pin": 17},
         "bit_rate": 1200},
    ]})

    assert mod.ensure_channel(api, "LoRaHAM KISS") == 7
    (sent,) = api.put_to("/api/channels/7")
    for response_only in ("id", "backing", "ptt"):
        assert response_only not in sent, f"{response_only} must not be echoed back"
    assert sent["mode"] == "aprs"          # the repair still happened
    assert sent["bit_rate"] == 1200        # ... and unrelated fields survived


def test_channel_in_aprs_plus_packet_is_left_alone():
    """`aprs+packet` still carries APRS — it is a deliberate operator choice (connected-mode
    sessions), so forcing it back to plain aprs would undo their configuration for no gain."""
    mod = _load()
    api = FakeApi({"/api/channels": [
        {"id": 7, "name": "LoRaHAM KISS", "mode": "aprs+packet",
         "modem_type": "kiss-only"},
    ]})

    assert mod.ensure_channel(api, "LoRaHAM KISS") == 7
    assert api.put_to("/api/channels/7") == []


def test_channel_with_the_wrong_modem_type_is_repaired():
    """An audio-backed channel has no TNC behind it, whatever its mode says."""
    mod = _load()
    api = FakeApi({"/api/channels": [
        {"id": 7, "name": "LoRaHAM KISS", "mode": "aprs", "modem_type": "afsk"},
    ]})

    assert mod.ensure_channel(api, "LoRaHAM KISS") == 7
    (sent,) = api.put_to("/api/channels/7")
    assert sent["modem_type"] == "kiss-only"

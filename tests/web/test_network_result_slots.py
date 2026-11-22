"""A Stored-networks action result is addressed by its connection, never by its place in the list: after forgetting
network A the list is [B], and A's result must not show up in B's row (a position-numbered slot did that)."""
import re
from unittest.mock import patch

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def _net(uuid, name):
    return {"uuid": uuid, "name": name, "type": "802-11-wireless", "autoconnect": False, "priority": "0",
            "preferred": False}


def _view(stored):
    return {"supported": True, "authorized": True, "mode": "ap", "active": {}, "stored": list(stored),
            "preferred": {}, "hostname": "lhpc-e293", "outcome": {}, "scan": [], "pending": False}


def _row(body, name):
    """The table row of stored network `name` with the result row right under it, if any."""
    m = re.search(r"<tr>\s*<th>" + re.escape(name) + r"\b.*?</tr>(\s*<tr class=\"rowresult\">.*?</tr>)?", body, re.S)
    assert m, name
    return m.group(0)


def _slot(body, label):
    m = re.search(r'name="_slot" value="([^"]+)" aria-label="' + re.escape(label) + '"', body)
    assert m, label
    return m.group(1)


def _client(web, tmp_path, state):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return web(service_factory=lambda: svc)


def test_forgetting_a_network_never_puts_its_result_under_the_next_one(web, csrf, tmp_path):
    state = [_net("aaaa-1111", "NetA"), _net("bbbb-2222", "NetB")]

    def forget(self, uuid):
        gone = next(c for c in state if c["uuid"] == uuid)
        state.remove(gone)
        return ActionResult(True, f"Forgot {gone['name']}")
    c = _client(web, tmp_path, state)
    with patch.object(ControllerService, "network_view", lambda self: _view(state)), \
            patch.object(ControllerService, "network_supported", lambda self: True), \
            patch.object(ControllerService, "network_forget", forget):
        page = c.get("/stacks").get_data(as_text=True)
        r = c.post("/network/forget", data={"_csrf": csrf(c), "uuid": "aaaa-1111",
                                             "_slot": _slot(page, "Forget NetA")}, follow_redirects=True)
        body = r.get_data(as_text=True)
    stored = body[body.index("<h4>Stored networks</h4>"):]
    assert "Forgot NetA" in stored[stored.index("</table>"):stored.index("</section>")]   # right under the list
    assert "Forgot NetA" not in _row(body, "NetB")                 # never under the network that took its place


def test_a_kept_networks_result_stays_under_its_own_row(web, csrf, tmp_path):
    # prefer reorders nothing here, but the slot must still name the connection: NetB's result under NetB only
    state = [_net("aaaa-1111", "NetA"), _net("bbbb-2222", "NetB")]

    def prefer(self, uuid, on, **_kw):
        net = next(c for c in state if c["uuid"] == uuid)
        net["preferred"] = True
        return ActionResult(True, f"Preferring {net['name']}")
    c = _client(web, tmp_path, state)
    with patch.object(ControllerService, "network_view", lambda self: _view(state)), \
            patch.object(ControllerService, "network_supported", lambda self: True), \
            patch.object(ControllerService, "network_prefer", prefer):
        page = c.get("/stacks").get_data(as_text=True)
        r = c.post("/network/prefer", data={"_csrf": csrf(c), "uuid": "bbbb-2222", "on": "1",
                                             "_slot": _slot(page, "Prefer NetB")}, follow_redirects=True)
        body = r.get_data(as_text=True)
    assert "Preferring NetB" in _row(body, "NetB") and "Preferring NetB" not in _row(body, "NetA")


def test_a_scanned_networks_join_slot_names_the_network_not_its_place(web, tmp_path):
    # the scan list changes between scans; a result addressed to the Join of "Other" stays with "Other"
    def join_slots(scan):
        view = {**_view([]), "mode": "client", "active": {"uuid": "U1", "name": "HomeNet", "address": "10.0.0.2/24"},
                "scan": scan}
        with patch.object(ControllerService, "network_view", lambda self: dict(view)), \
                patch.object(ControllerService, "network_supported", lambda self: True):
            body = c.get("/stacks").get_data(as_text=True)
        return re.findall(r'name="ssid" value="([^"]+)">.*?name="_slot" value="([^"]+)">Join', body, re.S)
    c = _client(web, tmp_path, [])
    first = dict(join_slots([{"ssid": "Cafe", "signal": 70, "security": "WPA2"},
                             {"ssid": "Other", "signal": 60, "security": "WPA2"}]))
    later = dict(join_slots([{"ssid": "Other", "signal": 61, "security": "WPA2"}]))
    assert len(first) == 2 and first["Other"] == later["Other"] and first["Cafe"] != first["Other"], (first, later)

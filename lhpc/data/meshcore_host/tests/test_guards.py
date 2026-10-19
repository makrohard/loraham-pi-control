"""The guards' decisions, without upstream: what counts as a change of an LHPC value."""
from __future__ import annotations

import pytest

from meshcore_host import guards
from meshcore_host.config import ConfigError

KEY = "11" * 32


def _conf():
    return {"radio_type": "external",
            "radio": {"frequency": 869618000, "bandwidth": 62500, "spreading_factor": 8,
                      "coding_rate": 8, "tx_power": 14},
            "http": {"enabled": True, "host": "127.0.0.1", "port": 8000},
            "storage": {"storage_dir": "/rt/state/openhop"},
            "glass": {"enabled": False, "cert_store_dir": "/rt/state/openhop/glass"},
            "gps": {"enabled": True, "source": "file", "time_sync_enabled": False},
            "setup_completed": True,
            "repeater": {"node_name": "Relay 1", "mode": "forward",
                         "security": {"admin_password": "p" * 15, "jwt_secret": "j" * 64}},
            "mesh": {"default_region": "eu"},
            "identities": {"companions": [{"name": "Chat 1", "identity_key": KEY}],
                           "room_servers": []}}


@pytest.mark.parametrize("updates, mode, changed", [
    ({"mesh": {"default_region": "uk"}}, "patch", False),             # the user's
    ({"repeater": {"mode": "monitor"}}, "patch", False),               # one store, the user's too
    ({"glass": {"enabled": True, "base_url": "https://g"}}, "patch", False),
    ({"radio": {"frequency": 869618000}}, "patch", False),              # unchanged value passes
    ({"radio": {"frequency": 868100000}}, "patch", True),
    ({"radio": {"frequency": 869618000}}, "replace", True),             # replace drops the rest
    ({"repeater": {"security": {"jwt_secret": "x"}}}, "patch", True),
    ({"glass": {"cert_store_dir": "/tmp/elsewhere"}}, "patch", True),
    ({"gps": {"time_sync_enabled": True}}, "patch", True),
    ({"identities": {"companions": [{"name": "Chat 1", "identity_key": KEY, "settings": {"tcp_port": 1}}]}},
     "patch", True),
    ({"identities": {"companions": [{"name": "Chat 1", "identity_key": KEY}],
                     "room_servers": [{"name": "Room"}]}}, "patch", False),
    ({"kiss": {"port": "/dev/ttyUSB0"}}, "patch", True),
    ({"pymc_tcp": {"host": "192.0.2.1", "port": 5000}}, "patch", True),   # sweep C2.2: upstream writes modem_tcp
    ({"pymc_usb": {"port": "/dev/ttyACM0"}}, "patch", True),             # ... and modem_usb
])
def test_a_write_changing_an_lhpc_value_is_detected(updates, mode, changed):
    assert guards.changes_owned(_conf(), updates, mode, True, KEY) is changed


def test_with_use_gps_off_only_time_sync_is_lhpcs():
    assert not guards.changes_owned(_conf(), {"gps": {"source": "serial"}}, "patch", False, KEY)
    assert guards.changes_owned(_conf(), {"gps": {"time_sync_enabled": True}}, "patch", False, KEY)


@pytest.mark.parametrize("command, refused", [
    ("set radio 869.6 62.5 8 8", True), ("set freq 868.1", True), ("SET TX 20", True),
    ("tempradio 869.6 62.5 8 8 10", True), ("http stop", True), ("http start", True),
    ("set name Relay 2", False), ("set lat 50.1", False), ("set repeat off", False),
    ("password secret12345", False), ("region put eu", False), ("get radio", False),
    ("reboot", False),
])
def test_the_cli_refuses_only_its_listed_commands(command, refused):
    assert guards._refused_cli(command) is refused


@pytest.mark.parametrize("data, changed", [
    ({"frequency": "869618000", "bandwidth": "62500.0", "tx_power": 14, "rx_delay_base": 2}, False),
    ({"frequency": 869618000.0, "spreading_factor": "8", "coding_rate": 8}, False),
    ({"frequency": 868100000}, True), ({"tx_power": 20}, True), ({"radio_id": "r2"}, True),
    ({"node_name": "Relay 2", "latitude": 50.1}, False),
    ({"frequency": "not a number"}, True),           # fail closed
    ({"frequency": "x", "tx_power": 14}, True),       # one bad value never hides the others
    ({"tx_power": 14, "kiss_port": "/dev/ttyS0"}, True), ({"kiss_baud_rate": 9600}, True),
    ({"rx_delay_base": 2, "loop_detect": "strict", "path_hash_mode": 1}, False),
    ({"radio_id": ""}, False), ({"radio_id": None}, False), ({"radio_id": "  "}, False),
    ({"radio_id": 0}, True), ({"radio_id": ["r1"]}, True), ({"radio_id": {"id": "r1"}}, True),
])
def test_the_radio_route_compares_after_upstreams_coercion(data, changed):
    assert guards._radio_change(_conf(), data) is changed


@pytest.mark.parametrize("data, hit", [
    ({"type": "companion", "name": "Chat 1"}, True),
    ({"type": "companion", "lookup_identity_key": KEY}, True),
    ({"type": "companion", "public_key_prefix": "abcd"}, True),
    ({"identity_key": KEY}, True),
    ({"type": "companion", "name": "Mine"}, False),
    ({"type": "room_server", "name": "Chat 1"}, False),
    ({"lookup_identity_key": KEY}, True),                     # 5u R10-F2: no type, a key selector
    ({"public_key_prefix": "abcd"}, True),
    ({"type": "room_server", "public_key_prefix": "abcd"}, True),
    ({"name": "Chat 1"}, False),                              # no type = a room server: its own names
])
def test_identity_requests_naming_the_chat_node_are_caught(data, hit):
    assert guards.targets_chat_node(data, KEY, "Chat 1", "abcdef01") is hit


def test_a_key_in_upper_case_still_names_the_chat_node():
    """gate 2 r2a N4: a key with hex letters, so upper case differs from the chat node's key."""
    lettered = "ab" * 32
    assert guards.targets_chat_node({"type": None, "lookup_identity_key": lettered.upper()}, lettered,
                                    "Chat 1", "abcdef01") is True


@pytest.mark.parametrize("query", [{"lookup_identity_key": KEY}, {"public_key_prefix": "abcd"},
                                   {"type": "room_server", "lookup_identity_key": KEY}])
def test_delete_identity_without_a_companion_type_is_refused_by_key(installed, query):
    """5u R10-F2 through the wrapper: the bound delete_identity refuses a key selector whatever
    `type` says, before upstream's route runs."""
    from repeater.web import http_server
    api = http_server.APIEndpoints
    assert api.delete_identity(object.__new__(api), **query) == {"success": False, "error": guards.NOTICE}


def _bound_json_route(monkeypatch, route, body):
    """Call the bound JSON route with `body` as the request JSON; upstream's own route (the base
    class) is replaced by a marker, so a pass is visible and nothing upstream runs."""
    import cherrypy
    from repeater.web import http_server
    api = http_server.APIEndpoints
    monkeypatch.setattr(api.__mro__[1], route, lambda self: "upstream ran")
    monkeypatch.setattr(cherrypy.serving.request, "json", body, raising=False)
    return getattr(api, route)(object.__new__(api))


@pytest.mark.parametrize("route", ["create_identity", "update_identity"])
@pytest.mark.parametrize("body", [{"lookup_identity_key": KEY}, {"public_key_prefix": "abcd"},
                                  {"type": "room_server", "lookup_identity_key": KEY}])
def test_create_and_update_without_a_companion_type_are_refused_by_key(installed, monkeypatch, route, body):
    """5v F1: the no-type key and prefix selectors through EACH bound JSON identity route."""
    assert _bound_json_route(monkeypatch, route, body) == {"success": False, "error": guards.NOTICE}
    assert _bound_json_route(monkeypatch, route, {"type": "room_server", "name": "Room"}) == "upstream ran"


@pytest.mark.parametrize("alias, section", [("pymc_tcp", {"host": "192.0.2.1", "port": 5000}),
                                            ("pymc_usb", {"port": "/dev/ttyACM0"})])
def test_config_import_refuses_a_legacy_modem_alias(installed, monkeypatch, alias, section):
    """5v F2: the bound config_import (replace) refuses the legacy alias spellings, which upstream's
    import renames to LHPC's removed modem sections before applying; the same document without the
    alias imports."""
    doc = _conf()
    assert _bound_json_route(monkeypatch, "config_import", {"config": doc}) == "upstream ran"
    assert (_bound_json_route(monkeypatch, "config_import", {"config": {**doc, alias: section}})
            == {"success": False, "error": guards.NOTICE})


def test_the_self_check_names_the_first_missing_coupling_point(monkeypatch):
    def fake_import(name):
        raise ImportError(name)
    monkeypatch.setattr(guards.importlib, "import_module", fake_import)
    with pytest.raises(ConfigError, match=r"missing repeater\.config\.load_config\); use the pinned source"):
        guards.self_check()


def test_every_coupling_point_exists_at_the_pin():
    pytest.importorskip("repeater.main")
    guards.self_check()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """The guards bound at the pin; every upstream name they replace is restored afterwards."""
    pytest.importorskip("repeater.main")
    from repeater import main as rmain
    from repeater import service_utils
    from repeater.data_acquisition import glass_handler
    from repeater.handler_helpers import text as text_helper
    from repeater.web import http_server
    for mod, name in ((text_helper, "MeshCLI"), (rmain, "GlassHandler"),
                      (http_server, "APIEndpoints"), (http_server, "CompanionFrameWebSocket"),
                      (service_utils, "restart_service"), (glass_handler, "restart_service")):
        monkeypatch.setattr(mod, name, getattr(mod, name))
    base_ws = http_server.CompanionFrameWebSocket
    lock = tmp_path / "meshcore-cli.lock"
    guards.install(_conf, use_gps=True, chat_key=KEY, chat_name="Chat 1", chat_pubkey="abcdef01",
                   cli_lock=str(lock), restart_argv=["lhpc", "stack", "restart", "meshcore", "--yes"])
    return http_server.CompanionFrameWebSocket, base_ws, lock, service_utils, glass_handler


def test_every_upstream_restart_runs_lhpcs_restart_detached(installed, monkeypatch):
    _ws, _base, _lock, service_utils, glass_handler = installed
    assert service_utils.restart_service is glass_handler.restart_service
    calls = []
    monkeypatch.setattr(guards.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)))
    assert service_utils.restart_service() == (True, guards.RESTARTED)
    assert calls[0][0] == ["lhpc", "stack", "restart", "meshcore", "--yes"]
    assert calls[0][1]["start_new_session"] is True

    def refuse(argv, **kw):
        raise OSError("no such file")
    monkeypatch.setattr(guards.subprocess, "Popen", refuse)
    ok, text = service_utils.restart_service()
    assert not ok and text == "Restart failed: no such file"


def _socket(cls, name, events):
    ws = cls.__new__(cls)
    ws.environ = {"QUERY_STRING": f"companion_name={name.replace(' ', '%20')}"}
    ws.close = lambda code=1000, reason="": events.append(("close", code, reason))
    return ws


def _lock_free(lock) -> bool:
    import fcntl
    import os
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def test_the_browser_chat_takes_the_cli_lock_and_yields_to_meshcli(installed, monkeypatch):
    import fcntl
    import os
    ws_cls, base, lock, _su, _gh = installed
    events = []

    def base_opened(self):
        events.append("opened")
        self._tcp = object()                    # upstream connected to the companion
    monkeypatch.setattr(base, "opened", base_opened)
    monkeypatch.setattr(base, "closed", lambda self, code, reason=None: events.append("closed"))

    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)       # meshcli holds the lease
    fcntl.flock(held, fcntl.LOCK_EX)
    _socket(ws_cls, "Chat 1", events).opened()
    assert events == [("close", 1008, guards.SLOT_BUSY)]
    os.close(held)

    events.clear()
    ws = _socket(ws_cls, "Chat 1", events)
    ws.opened()
    assert events == ["opened"] and not _lock_free(lock)       # the browser holds it now
    ws.closed(1000)
    assert _lock_free(lock)                                   # released on close

    events.clear()
    other = _socket(ws_cls, "My companion", events)            # a user companion: no lock
    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    other.opened()
    assert events == ["opened"]
    os.close(held)


@pytest.mark.parametrize("mode, updates", [
    ("patch", {"lhpc_managed": {"chat_node": "cd" * 8}}),
    ("replace", {"lhpc_managed": {"note": "mine"}}),
    ("replace", {"lhpc_managed": None}),
])
def test_a_write_touching_lhpcs_marker_block_is_an_owned_change(mode, updates):
    """gate 1 5l F2: the lhpc_managed block is LHPC's; an import or Glass update that plants,
    changes or nulls it is refused like any other owned key (upstream's replace is per section)."""
    conf = _conf() | {"lhpc_managed": {"note": "x", "keys": ["radio"], "lhpc_one_store": []}}
    assert guards.changes_owned(conf, updates, mode, True, KEY)
    assert not guards.changes_owned(conf, {"mesh": {"default_region": "uk"}}, "patch", True, KEY)


def test_every_field_the_pinned_radio_route_reads_is_classified():
    """gate 1 5l F3: the pinned `update_radio_config` reads exactly the classified fields; a repin
    that adds one fails here until it is classified LHPC's or the user's.

    Rule-1 exception (tests/README.md): an inventory of a pinned UPSTREAM method's reads; no driven
    path can enumerate which request fields a method reads. It covers the direct reads (`"x" in
    data`, `data.get("x")`, `data["x"]`); a read through a variable (a list or set of names, as the
    route's own `radio_fields`) is caught by the source digest of the next test instead."""
    pytest.importorskip("repeater.main")
    import inspect
    import re
    from repeater.web.api_endpoints import APIEndpoints
    src = inspect.getsource(APIEndpoints.update_radio_config)  # rule-1 exception
    read = set(re.findall(r'"(\w+)" in data', src)) | set(re.findall(r'data\.get\("(\w+)"', src)) \
        | set(re.findall(r'data\["(\w+)"\]', src))
    classified = set(guards.RADIO_FIELDS) | set(guards.RADIO_ROUTE_REMOVED) \
        | set(guards.RADIO_ROUTE_USER) | {"radio_id"}
    assert read == classified


def test_the_classified_radio_route_is_the_pinned_one():
    """gate 1 5m F1: the classification above holds for exactly this version of the route; any
    change at a repin (a new field read through a variable included) fails here until the route
    is re-classified and RADIO_ROUTE_SOURCE updated. Rule-1 exception: see the test above."""
    pytest.importorskip("repeater.main")
    import hashlib
    import inspect
    import textwrap
    from repeater.web.api_endpoints import APIEndpoints
    src = textwrap.dedent(inspect.getsource(APIEndpoints.update_radio_config))  # rule-1 exception
    assert hashlib.sha256(src.encode()).hexdigest()[:16] == guards.RADIO_ROUTE_SOURCE


def test_a_failing_upstream_open_releases_the_slot_at_once(installed, monkeypatch):
    """gate 1 5n N1: an exception out of upstream's opened() releases the lease before it propagates."""
    ws_cls, base, lock, _su, _gh = installed

    def broken(self):
        raise RuntimeError("upstream open failed")
    monkeypatch.setattr(base, "opened", broken)
    ws = _socket(ws_cls, "Chat 1", [])
    with pytest.raises(RuntimeError, match="upstream open failed"):
        ws.opened()
    assert _lock_free(lock) and getattr(ws, "_lhpc_slot_fd", None) is None


def test_a_glass_replace_with_padding_is_checked_as_a_replace(installed, monkeypatch):
    """gate 2 r1 F1: upstream strips merge_mode before choosing replace; the guard must judge the
    same mode. A padded " replace " of LHPC's http section (it would drop host and port) is
    refused before upstream runs; the same body as a patch changes nothing owned and passes."""
    from repeater import main as rmain
    cls = rmain.GlassHandler
    monkeypatch.setattr(cls.__mro__[1], "_apply_config_update", lambda self, u, m="patch": (True, "upstream ran"))
    handler = object.__new__(cls)
    assert handler._apply_config_update({"http": {"enabled": True}}, " replace ") == (False, guards.NOTICE)
    assert handler._apply_config_update({"http": {"enabled": True}}, "patch") == (True, "upstream ran")


def test_a_padded_chat_node_name_is_refused_by_the_bound_delete(installed, monkeypatch):
    """gate 2 r1 F2: upstream's delete strips the name before it looks the companion up, so a
    padded chat node name (no key, no prefix) names the protected companion."""
    from repeater.web import http_server
    api = http_server.APIEndpoints
    monkeypatch.setattr(api.__mro__[1], "delete_identity", lambda self, **kw: "upstream ran")
    assert (api.delete_identity(object.__new__(api), name=" Chat 1 ", type="companion")
            == {"success": False, "error": guards.NOTICE})
    assert api.delete_identity(object.__new__(api), name=" Mine ", type="companion") == "upstream ran"

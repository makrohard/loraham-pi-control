"""Network exposure for no-auth service ports: the kiss `--bind` / meshcore `wifi.allow`
allow-list settings (their own 'Network exposure' settings block, shown on the settings page AND
the confirm:start panel) and the dashboard 'a line per open port' with its exposure colour."""

from __future__ import annotations

import pytest

from lhpc.adapters.web.app import create_app
from lhpc.core.model import emit_param
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.webserver import port_exposure


def _svc(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


# --- exposure classification: green local / yellow LAN / red public ------------------------------

@pytest.mark.parametrize("bind,expect", [
    ("127.0.0.1", ("ok", "local")),
    ("127.0.0.0/8", ("ok", "local")),
    ("192.168.0.0/24", ("warn", "LAN")),
    ("10.0.0.5", ("warn", "LAN")),
    ("0.0.0.0/0", ("bad", "public")),
    ("", ("ok", "local")),           # empty -> fail closed to local
    ("::1", ("ok", "local")),        # IPv6 -> fail closed to local (IPv4-only feature)
])
def test_port_exposure(bind, expect):
    assert port_exposure(bind) == expect


# --- the 'Network exposure' settings block (settings page + confirm:start both use this) ----------

@pytest.mark.parametrize("stack", ["kiss", "meshcore"])
def test_network_exposure_is_its_own_settings_block(tmp_path, stack):
    groups = _svc(tmp_path).config_param_groups(stack)
    ne = [g for g in groups if g["name"] == "Network exposure"]
    assert ne, f"{stack} should render its own 'Network exposure' block"
    labels = " ".join(r.get("label", "") for g in ne for r in g["rows"]).lower()
    assert "allow-list" in labels


# --- kiss passes the allow-list to the binary as `--bind` ----------------------------------------

def test_kiss_run_argv_carries_bind_param(tmp_path):
    comp = _svc(tmp_path).stack("kiss").component("loraham-kiss-tnc")
    assert "{param:kiss_bind}" in comp.run_argv
    p = next(x for x in comp.run_params if x.name == "kiss_bind")
    assert p.arg == "--bind" and p.validator == "bind" and p.group == "Network exposure"
    assert emit_param(p, "192.168.0.0/24") == ["--bind", "192.168.0.0/24"]


# --- meshcore drives the upstream `wifi.allow` config key ----------------------------------------

def test_meshcore_allow_targets_wifi_allow_key(tmp_path):
    comp = _svc(tmp_path).stack("meshcore").component("meshcore-pi")
    p = next(x for x in comp.config_file.params if x.name == "meshcore_allow")
    assert p.key == "wifi.allow" and p.section == "device.companion"
    assert p.validator == "bind" and p.group == "Network exposure"


# --- dashboard: a port line, exposure-coloured pill + a per-service logs link ---------------------

def _dash_body(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(ControllerService, "dashboard_webservers", lambda self, **k: rows)
    return create_app(lambda: _svc(tmp_path)).test_client().get("/").get_data(as_text=True)


_CONSOLE = {"kind": "console", "name": "LHCP", "port": "8770", "logs_component": None,
            "posture": {"auth": "open", "iface": "loopback", "sec_level": "ok", "scheme": "https",
                        "auth_level": "ok", "iface_level": "ok", "scheme_level": "ok",
                        "run": "lhpc-web", "run_level": "ok"}}


@pytest.mark.parametrize("level,label,color", [
    ("ok", "local", "pill-ok"), ("warn", "LAN", "pill-warn"), ("bad", "public", "pill-bad")])
def test_dashboard_port_line_colour_and_logs(tmp_path, monkeypatch, level, label, color):
    rows = [_CONSOLE,
            {"kind": "port", "name": "KISS TNC", "sid": "kiss", "port": "8001",
             "exposure": {"level": level, "label": label}, "logs_component": "loraham-kiss-tnc"}]
    body = _dash_body(tmp_path, monkeypatch, rows)
    assert color in body and ":8001" in body and label in body
    assert 'href="/logs/loraham-kiss-tnc"' in body          # per-service logs link (kiss/meshcore)
    assert 'href="/stacks?open=kiss' in body                # the name links to the stack


def test_dashboard_meshtastic_api_line_is_public_without_logs(tmp_path, monkeypatch):
    rows = [_CONSOLE,
            {"kind": "port", "name": "Meshtastic", "sid": "meshtastic", "port": "4403",
             "exposure": {"level": "bad", "label": "public"}, "logs_component": None}]
    body = _dash_body(tmp_path, monkeypatch, rows)
    assert "pill-bad" in body and ":4403" in body and "public" in body
    assert "/logs/" not in body                             # no logs link on the meshtastic API line


def test_dashboard_loopback_port_shows_127_and_exposed_shows_reached_host(tmp_path, monkeypatch):
    # Host is decided from the LIVE listener scope, NOT the saved allow-list `exposure`: a service
    # whose saved policy reads "public" but is actually bound loopback (MeshCom QEMU, no bind knob)
    # must stay 127.0.0.1; only a genuinely exposed live listener uses the reached host.
    rows = [_CONSOLE,
            {"kind": "port", "name": "KISS TNC", "sid": "kiss", "port": "8001",
             "live_scope": "loopback", "exposure": {"level": "bad", "label": "public"},
             "logs_component": "loraham-kiss-tnc"}]
    body = _dash_body(tmp_path, monkeypatch, rows)
    assert "127.0.0.1:8001" in body and "localhost:8001" not in body   # live loopback -> 127.0.0.1
    rows[1]["live_scope"] = "exposed"
    body = _dash_body(tmp_path, monkeypatch, rows)
    assert "localhost:8001" in body                         # live exposed -> the reached host (test client)


# --- webserver-box port pin: copyable address, viewer-correct host, no dead links ----------------
# Behaviour facts (rendered address VALUES, link TARGETS via htmlq) — not markup/CSS-class strings.

def _dash_body_h(tmp_path, monkeypatch, rows, headers):
    # Host defaults to the always-allowed "localhost"; only X-LHPC-Peer varies (local vs remote).
    monkeypatch.setattr(ControllerService, "dashboard_webservers", lambda self, **k: rows)
    return create_app(lambda: _svc(tmp_path)).test_client().get("/", headers=headers).get_data(as_text=True)


def _port_row(scope):
    return [_CONSOLE,
            {"kind": "port", "name": "Meshtastic", "sid": "meshtastic", "port": "4403",
             "scheme": "tcp", "live_scope": scope, "exposure": {"level": "bad", "label": "public"},
             "logs_component": None}]


def _hrefs(body):
    from htmlq import parse
    return [a["href"] for a in parse(body).find("a") if a["href"]]


def test_port_pin_is_never_an_inert_tcp_anchor(tmp_path, monkeypatch):
    # A tcp:// URL has no browser handler; the address is COPYABLE TEXT, never an <a href="tcp://…">.
    for scope, peer in (("loopback", "remote"), ("exposed", "remote"), ("loopback", "loopback")):
        body = _dash_body_h(tmp_path, monkeypatch, _port_row(scope), {"X-LHPC-Peer": peer})
        assert not any(h.startswith("tcp://") for h in _hrefs(body))   # no inert tcp:// link
        assert "4403" in body                                          # the address is still shown


def test_port_pin_remote_loopback_is_local_only_with_stack_link(tmp_path, monkeypatch):
    # Remote browser + loopback-only listener: truthful 'local only' address (not a dead 127.0.0.1
    # service link, not a fabricated request-host link); the stack name is the navigable link.
    body = _dash_body_h(tmp_path, monkeypatch, _port_row("loopback"), {"X-LHPC-Peer": "remote"})
    assert "127.0.0.1:4403" in body and "local only" in body
    assert any("/stacks?open=meshtastic" in h for h in _hrefs(body))   # stack name links to Settings
    assert "localhost:4403" not in body                                # no fabricated request-host


def test_port_pin_remote_exposed_shows_reached_host(tmp_path, monkeypatch):
    body = _dash_body_h(tmp_path, monkeypatch, _port_row("exposed"), {"X-LHPC-Peer": "remote"})
    assert "localhost:4403" in body                          # live-exposed -> the reached host


def test_port_pin_local_viewer_loopback_shows_127(tmp_path, monkeypatch):
    body = _dash_body_h(tmp_path, monkeypatch, _port_row("loopback"), {"X-LHPC-Peer": "loopback"})
    assert "127.0.0.1:4403" in body                          # local viewer: the loopback address works


# --- enabled proxy pin: link only where the proxy LIVE-listens (never a dead proxy URL) ----------

def _proxy_row(listen_scope):
    posture = {"auth": "open", "auth_level": "ok", "iface": listen_scope, "iface_level": "ok",
               "scheme": "https", "scheme_level": "ok", "run": "up", "run_level": "ok"}
    return [_CONSOLE,
            {"kind": "stack", "name": "MeshCom", "sid": "meshcom", "enabled": True, "port": 8444,
             "listen_scope": listen_scope, "posture": posture,
             "direct_port": "", "direct_scheme": "", "direct_scope": "absent"}]


def test_proxy_pin_remote_loopback_has_no_request_host_service_link(tmp_path, monkeypatch):
    # nginx listens on 127.0.0.1:8444 only; a REMOTE viewer must not get a reached-host proxy link.
    body = _dash_body_h(tmp_path, monkeypatch, _proxy_row("loopback"), {"X-LHPC-Peer": "remote"})
    assert not any(":8444" in h for h in _hrefs(body))       # no dead proxy service link
    assert any("/stacks?open=meshcom" in h for h in _hrefs(body))   # internal Settings link instead


def test_proxy_pin_absent_has_no_service_link_even_for_local_viewer(tmp_path, monkeypatch):
    # Proxy configured but NOT listening: no service link for anyone (not even 127.0.0.1) — Apply.
    for peer in ("remote", "loopback"):
        body = _dash_body_h(tmp_path, monkeypatch, _proxy_row("absent"), {"X-LHPC-Peer": peer})
        assert not any(":8444" in h for h in _hrefs(body))   # no proxy socket link at all
        assert "Apply" in body                                # tells the operator to Apply
        assert any("/stacks?open=meshcom" in h for h in _hrefs(body))


# --- absent listeners are shown as ABSENT, never as a working/loopback address -------------------

def _direct_row(direct_scope):
    return [_CONSOLE,
            {"kind": "stack", "name": "MeshCom", "sid": "meshcom", "enabled": False, "port": None,
             "posture": None, "listen_scope": None,
             "direct_port": "18083", "direct_scheme": "http", "direct_scope": direct_scope}]


def test_direct_web_absent_has_no_service_anchor_even_for_local_viewer(tmp_path, monkeypatch):
    # A degraded not-proxied web UI whose listener is DOWN: no clickable http://127.0.0.1:18083 link
    # for anyone (it's ABSENT, not local-only) — an internal 'listener absent' link instead.
    for peer in ("loopback", "remote"):
        body = _dash_body_h(tmp_path, monkeypatch, _direct_row("absent"), {"X-LHPC-Peer": peer})
        assert not any(h.startswith("http://127.0.0.1:18083") for h in _hrefs(body))
        assert "listener absent" in body
        assert any("/stacks?open=meshcom" in h for h in _hrefs(body))


def test_port_pin_absent_labelled_listener_absent_for_both_viewers(tmp_path, monkeypatch):
    # A no-auth port whose listener is DOWN reads 'listener absent' — never 'local only' and never a
    # working address — for a local AND a remote viewer.
    for peer in ("loopback", "remote"):
        body = _dash_body_h(tmp_path, monkeypatch, _port_row("absent"), {"X-LHPC-Peer": peer})
        assert "listener absent" in body
        assert "local only" not in body
        assert not any(":4403" in h for h in _hrefs(body))   # no service anchor to the dead port


def test_meshcore_config_generation_writes_wifi_allow(tmp_path):
    # The generated meshcore config must carry the operator's allow-list under the dotted
    # device.companion `wifi.allow` key (blank leaves the base default untouched).
    from lhpc.core.config import update_toml
    from lhpc.core.model import FileParam
    base = ('[device.companion]\ninterface = "wifi"\nwifi.allow = "127.0.0.1"\nwifi.port = 5000\n')
    p = FileParam(name="meshcore_allow", key="wifi.allow", section="device.companion",
                  default="127.0.0.1")
    out = update_toml(base, [p], {"meshcore_allow": "192.168.0.0/24"}, lambda s: s)
    assert 'wifi.allow = "192.168.0.0/24"' in out
    assert 'wifi.port = 5000' in out                     # other keys preserved
    unchanged = update_toml(base, [p], {"meshcore_allow": ""}, lambda s: s)
    assert 'wifi.allow = "127.0.0.1"' in unchanged        # blank -> keep the base default

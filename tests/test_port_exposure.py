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
    rows = [_CONSOLE,
            {"kind": "port", "name": "KISS TNC", "sid": "kiss", "port": "8001",
             "exposure": {"level": "ok", "label": "local"}, "logs_component": "loraham-kiss-tnc"}]
    assert "127.0.0.1:8001" in _dash_body(tmp_path, monkeypatch, rows)   # loopback -> 127.0.0.1
    rows[1]["exposure"] = {"level": "bad", "label": "public"}
    body = _dash_body(tmp_path, monkeypatch, rows)
    assert "localhost:8001" in body                         # exposed -> the reached host (test client)


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

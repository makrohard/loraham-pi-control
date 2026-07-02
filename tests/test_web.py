"""Tests for the Flask web console: rendering, escaping, 404/405, security
headers, loopback binding, and proof that page loads are read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from lhpc.adapters.web.app import _LOOPBACK_HOSTS, create_app, run_server
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


_MUTATING = {"start", "stop", "build", "update", "test",
             "uninstall", "daemon_set"}


def _real_app(tmp_path, manifest=None):
    """App backed by a fake-system ControllerService (daemon unreachable)."""
    def factory():
        return ControllerService(manifest_path=manifest, system=FakeSystem().system,
                                 paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=factory).test_client()


def _csrf(client, path="/stacks/daemon/config"):
    import re
    body = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    return m.group(1) if m else ""


class ReadOnlyGuard:
    """Delegates read-only calls; fails the test if a mutating method is used."""

    def __init__(self, service: ControllerService) -> None:
        self._service = service

    def __getattr__(self, name: str):
        if name in _MUTATING:
            raise AssertionError(f"web invoked mutating method '{name}'")
        return getattr(self._service, name)


def _client(tmp_path: Path, manifest: Path | None = None):
    def factory():
        svc = ControllerService(
            manifest_path=manifest,
            system=FakeSystem().system,
            paths=Paths(runtime_root=tmp_path),
        )
        return ReadOnlyGuard(svc)

    return create_app(service_factory=factory).test_client()


def test_dashboard_ok_and_headers(tmp_path):
    resp = _client(tmp_path).get("/")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert "connect-src 'self'" in csp   # allows the live-monitor fetch polling
    assert b"LoRaHAM Pi Control" in resp.data


def test_stack_detail_ok(tmp_path):
    resp = _client(tmp_path).get("/stacks/meshcom")
    assert resp.status_code == 200
    assert b"DIRECT" in resp.data  # the corrected 433 DIRECT requirement is shown


def test_unknown_stack_404(tmp_path):
    assert _client(tmp_path).get("/stacks/nope").status_code == 404


def test_non_get_405(tmp_path):
    assert _client(tmp_path).post("/").status_code == 405
    assert _client(tmp_path).post("/stacks/meshcom").status_code == 405


def test_healthz(tmp_path):
    resp = _client(tmp_path).get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_html_escaping(tmp_path):
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[[stack]]\n'
        'id = "x"\n'
        'name = "<script>alert(1)</script>"\n'
        'summary = "s"\n'
        '[[stack.component]]\n'
        'id = "c"\n'
        'name = "c"\n'
        'kind = "service"\n'
    )
    resp = _client(tmp_path, manifest=manifest).get("/stacks")
    assert resp.status_code == 200
    assert b"<script>alert(1)</script>" not in resp.data
    assert b"&lt;script&gt;" in resp.data


def test_page_load_is_read_only(tmp_path):
    # If any page handler called a mutating service method, ReadOnlyGuard would
    # raise and these requests would 500. 200 proves the load was read-only.
    client = _client(tmp_path)
    assert client.get("/").status_code == 200
    assert client.get("/stacks/daemon").status_code == 200


_NET_GIT = {"ls-remote", "fetch", "clone", "pull", "push", "remote"}
_NET_CMD = {"curl", "wget", "nc", "ssh", "ping", "host", "dig", "nslookup"}


def _is_network(argv: list[str]) -> bool:
    if not argv:
        return False
    exe = argv[0].rsplit("/", 1)[-1]
    if exe in _NET_CMD:
        return True
    if exe == "git" and any(a in _NET_GIT for a in argv[1:]):
        return True
    return False


def test_get_routes_make_no_network_calls(tmp_path):
    """P0.6 — every GET route must run no network/git-remote command. A recording
    runner captures every subprocess invocation during each GET; none may be a
    network command (git ls-remote/fetch/clone/…, curl, ssh, DNS)."""
    calls: list[list[str]] = []

    def factory():
        sys = FakeSystem().system
        inner = sys.runner

        class Rec:
            def run(self, argv, timeout=None, *a, **k):
                calls.append(list(argv))
                return inner.run(argv, timeout, *a, **k)

        sys.runner = Rec()
        return ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))

    client = create_app(service_factory=factory).test_client()
    for path in ("/", "/stacks", "/stacks/daemon", "/stacks/daemon/config", "/config",
                 "/healthz", "/logs/loraham-daemon", "/api/daemon/433",
                 "/api/dash-signature", "/api/logs/loraham-daemon"):
        client.get(path)
    offenders = [c for c in calls if _is_network(c)]
    assert not offenders, f"GET routes ran network commands: {offenders}"


def test_dashboard_is_a_control_hub(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert 'class="tiles"' in body              # overview tiles
    assert "badge badge-" in body               # status badges
    assert 'action="/action"' in body           # stack action buttons on the stacks page
    assert ">Run<" in body and ">Install<" in body
    assert 'class="stackrow"' in body            # collapsed per-stack list rows
    assert "Dependency component" in body        # deps table inside the expanded row


def test_radio_dashboard_has_two_band_columns(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    assert 'class="radiogrid"' in body
    assert 'data-radio-band="433"' in body and 'data-radio-band="868"' in body
    assert "433 MHz" in body and "868 MHz" in body
    # per-band: a start-stack control and a radio-config link
    assert 'name="op" value="start"' in body
    assert "Radio config" in body or "daemon offline" in body


def test_menu_has_dash_apps_config(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    assert 'class="topnav"' in body
    assert ">Dash<" in body and ">Apps<" in body and ">Config<" in body
    assert ">Monitor<" not in body          # Monitor page deleted


def test_stack_detail_has_panels_and_evidence(tmp_path):
    body = _client(tmp_path).get("/stacks/daemon").get_data(as_text=True)
    assert "Declared resources" in body
    assert "Endpoints" in body
    assert "<details>" in body              # expandable evidence, no JS needed


def test_no_inline_style_or_script_on_pages(tmp_path):
    # CSP is default-src 'self'; inline styles/scripts would be blocked. External
    # same-origin <script src> is CSP-compliant, but inline scripts/styles are not.
    import re
    for path in ("/", "/stacks", "/stacks/meshcom"):
        body = _client(tmp_path).get(path).get_data(as_text=True)
        assert "style=" not in body.lower()
        for tag in re.findall(r"<script[^>]*>", body.lower()):
            assert "src=" in tag                 # no inline <script> blocks


def _daemon_client(tmp_path, guard=False):
    reply = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})

    def factory():
        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
        return ReadOnlyGuard(svc) if guard else svc

    return create_app(service_factory=factory).test_client()


def test_daemon_config_page_has_live_settings(tmp_path):
    # Live daemon settings now live on the daemon's config page (Monitor page deleted).
    body = _daemon_client(tmp_path).get("/stacks/daemon/config").get_data(as_text=True)
    assert "Live radio settings" in body and 'name="_csrf"' in body
    assert "<select name=\"value\">" in body          # enum -> dropdown
    assert 'type="number"' in body and 'min="-130"' in body   # int -> ranged input


def test_old_monitor_page_is_gone(tmp_path):
    assert _daemon_client(tmp_path).get("/daemon/433").status_code == 404


def test_daemon_api_json(tmp_path):
    j = _daemon_client(tmp_path).get("/api/daemon/433").get_json()
    assert j["reachable"] and j["status"]["TXMODE"] == "MANAGED"


def test_radio_set_requires_csrf(tmp_path):
    c = _daemon_client(tmp_path)
    r = c.post("/radio/433/set", data={"key": "TXMODE", "value": "DIRECT"})
    assert r.status_code == 400


def test_radio_set_is_two_step(tmp_path):
    # P0.7: a live daemon setting needs plan + confirm, like every other mutation.
    c = _daemon_client(tmp_path)
    token = _csrf(c)
    # First POST (no confirmed) -> shows the plan, does NOT apply (200, not 302).
    r = c.post("/radio/433/set", data={"_csrf": token, "key": "TXMODE", "value": "DIRECT"})
    assert r.status_code == 200 and b"Confirm live daemon setting" in r.data
    # Confirmed POST -> applies (redirect to the daemon config page).
    r2 = c.post("/radio/433/set", data={"_csrf": token, "key": "TXMODE",
                                        "value": "DIRECT", "confirmed": "yes"})
    assert r2.status_code == 302


def test_config_path_cannot_escape_via_band_or_id(tmp_path):
    import pytest as _pytest
    from lhpc.core.config import _stack_config_path, save_stack_config
    from lhpc.core.validators import ValidationError
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    stacks = (tmp_path / "config" / "stacks").resolve()
    # A traversal band or id must be rejected, never resolve outside config/stacks/.
    for sid, band in [("daemon", "../../etc"), ("../../evil", "433"), ("a/b", ""),
                      ("daemon", "433/../../x")]:
        with _pytest.raises(ValidationError):
            _stack_config_path(paths, sid, band)
    # A legitimate write stays inside config/stacks/.
    p = save_stack_config(paths, "kiss", {"x": "1"}, "868")
    assert stacks in p.resolve().parents


def test_get_daemon_config_is_read_only(tmp_path):
    # daemon_set is in _MUTATING; a GET of the config page must never call it.
    assert _daemon_client(tmp_path, guard=True).get("/stacks/daemon/config").status_code == 200


def test_multi_band_config_stored_per_band(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/kiss/config?band=868")     # kiss stays multi-band
    c.post("/stacks/kiss/config", data={"_csrf": token, "band": "868",
                                        "c_tx_freq": "869.525"})
    assert "869.525" in c.get("/stacks/kiss/config?band=868").get_data(as_text=True)
    assert "433.900" in c.get("/stacks/kiss/config?band=433").get_data(as_text=True)  # 433 untouched


def test_config_index_lists_stacks(tmp_path):
    body = _client(tmp_path).get("/config").get_data(as_text=True)
    assert "Configuration" in body and ">Configure" in body
    assert "daemon" in body and "igate" in body


def test_stack_page_has_action_controls(tmp_path):
    body = _real_app(tmp_path).get("/stacks/daemon").get_data(as_text=True)
    assert "Stack actions" in body and 'action="/action"' in body


def test_actions_grouped_and_install_state_aware(tmp_path):
    # fresh runtime: sources missing -> not installed -> Install shown, Build hidden
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "grouplabel" in body and ">Install<" in body
    assert ">Build<" not in body


def test_install_confirm_offers_source_versions(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    cf = c.post("/action", data={"_csrf": token, "op": "install", "target": "daemon"}).get_data(as_text=True)
    assert 'name="source"' in cf and "pinned known-good" in cf and "latest dev" in cf


def test_apps_page_shows_interactive_command_with_copy(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert 'id="appcmd-chat"' in body and 'data-copy="appcmd-chat"' in body
    assert "loraham_chat" in body and "copy.js" in body   # copyable line + handler


def test_action_requires_csrf(tmp_path):
    c = _real_app(tmp_path)
    assert c.post("/action", data={"op": "start", "target": "daemon"}).status_code == 400


def test_action_unknown_op_rejected(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    assert c.post("/action", data={"_csrf": token, "op": "evil", "target": "daemon"}).status_code == 400


def test_start_confirm_shows_daemon_run_params(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)            # daemon installed
    binp.write_text("#!/bin/sh\n")             # and built
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'name="p_radio"' in body and 'name="p_debug"' in body   # radio + debug inputs


def test_start_confirm_includes_daemon_params_panel(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)            # daemon installed
    binp.write_text("#!/bin/sh\n")             # and built
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    body = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon"}).get_data(as_text=True)
    assert "Daemon radio parameters" in body                 # panel on the start-confirm page
    assert '<details class="advcfg dparams">' in body        # inline panel, collapsed by default
    assert "Reset to defaults" in body                       # (client-side) Reset stays...
    assert 'name="_save" value="daemon">Save</button>' in body   # inline Save persists these params
    assert ">Apply live</button>" not in body                # ...but no Apply-live on the confirm


def test_start_daemon_only_on_a_band(tmp_path):
    # The dash "Start daemon (868 only)" posts op=start target=daemon p_radio=868.
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)            # installed
    binp.write_text("#!/bin/sh\n")             # and built
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon",
                                "p_radio": "868"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Confirm: start" in body
    # radio band is no longer a grid dropdown on the daemon confirm — preserved as a hidden input.
    assert 'type="hidden" name="p_radio" value="868"' in body


def test_start_uninstalled_stack_redirects_to_app_page(tmp_path):
    # Fresh runtime: igate source absent -> starting it refuses and forwards to the
    # app page (which has the Install button) with a warning.
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "igate"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/stacks/igate")


def test_install_confirm_shows_missing_system_deps(tmp_path):
    # FakeSystem fs reports the ncurses header absent -> chat install warns with apt cmd.
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/chat")
    cf = c.post("/action", data={"_csrf": token, "op": "install", "target": "chat"}).get_data(as_text=True)
    assert "Missing system dependencies" in cf and "libncurses-dev" in cf


def test_install_runs_as_live_logged_job(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": token, "op": "install",
                                "target": "igate", "confirmed": "yes"})
    assert r.status_code == 302 and "/logs/" in r.headers["Location"]
    assert "job=" in r.headers["Location"]


def test_action_plan_then_confirm(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    # Stage 1: no ack -> confirm page (200, not applied)
    r1 = c.post("/action", data={"_csrf": token, "op": "stop", "target": "daemon"})
    assert r1.status_code == 200 and b"Confirm: stop" in r1.data
    # Stage 2: ack -> applies, redirect
    r2 = c.post("/action", data={"_csrf": token, "op": "stop", "target": "daemon", "confirmed": "yes"})
    assert r2.status_code == 302


def test_logs_view(tmp_path):
    assert _real_app(tmp_path).get("/logs/loraham-daemon").status_code == 200
    assert _real_app(tmp_path).get("/logs/bogus").status_code == 404


def test_log_api_returns_lines(tmp_path):
    j = _real_app(tmp_path).get("/api/logs/loraham-daemon").get_json()
    assert "lines" in j and isinstance(j["lines"], list)


def test_build_action_redirects_to_live_log(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/kiss")
    r = c.post("/action", data={"_csrf": token, "op": "build",
                                "target": "loraham-kiss-tnc", "confirmed": "yes"})
    assert r.status_code == 302 and "/logs/" in r.headers["Location"]
    assert "job=" in r.headers["Location"]


def test_log_api_rejects_path_traversal_job(tmp_path):
    # ?job is restricted to a bare filename.
    j = _real_app(tmp_path).get("/api/logs/loraham-daemon?job=../../etc/passwd").get_json()
    assert "etc/passwd" not in (j["path"] or "")


def test_run_server_rejects_non_loopback(capsys):
    assert run_server(host="0.0.0.0", port=8770) == 1
    assert "loopback-only" in capsys.readouterr().out


def test_loopback_set_is_exactly_localhost():
    assert _LOOPBACK_HOSTS == {"127.0.0.1", "::1"}


# --- transient green start-note (meshcore-nodegui connect hint) ---------------

def test_start_note_for_started_component():
    from lhpc.core.services import ControllerService, ActionResult
    from lhpc.core.outcomes import CompResult, Outcome
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    import tempfile, pathlib
    svc = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=pathlib.Path(tempfile.mkdtemp())))
    verified = ActionResult(True, "ok", results=(
        CompResult(component="meshcore-nodegui", action="start", outcome=Outcome.VERIFIED),))
    assert svc.start_notes(verified) == ["Connect MeshCore-Node-GUI to TCP 127.0.0.1 Port 5000"]
    # already-healthy also emits the note
    healthy = ActionResult(True, "ok", results=(
        CompResult(component="meshcore-nodegui", action="start", outcome=Outcome.ALREADY_HEALTHY),))
    assert svc.start_notes(healthy) == ["Connect MeshCore-Node-GUI to TCP 127.0.0.1 Port 5000"]
    # blocked / unverified / failed -> NO note
    for bad in (Outcome.BLOCKED, Outcome.UNVERIFIED, Outcome.FAILED):
        res = ActionResult(True, "ok", results=(
            CompResult(component="meshcore-nodegui", action="start", outcome=bad),))
        assert svc.start_notes(res) == []


def test_start_note_is_html_escaped(tmp_path):
    # The dashboard renders flash notes with Jinja autoescaping ({{ msg }}), so a note
    # containing markup is escaped — never injected as live HTML.
    from lhpc.adapters.web.app import create_app
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    app = create_app(service_factory=lambda: ControllerService(
        system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
    with app.test_request_context():
        from flask import render_template
        out = render_template("base.html", version="t")  # no flashes -> just proves render
    # the flash loop uses {{ msg }} (autoescaped), never |safe
    base = Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web" / "templates" / "base.html"
    src = base.read_text()
    assert "{{ msg }}" in src and "msg|safe" not in src and "msg | safe" not in src
    from markupsafe import escape
    assert "&lt;script&gt;" in str(escape("<script>x</script>"))


def test_wheel_includes_flash_js():
    # flash.js must ship in the wheel (package-data), else the transient note can't hide.
    import tomllib, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    globs = data["tool"]["setuptools"]["package-data"]["lhpc.adapters.web"]
    assert any(g == "static/*.js" for g in globs)
    assert (root / "lhpc" / "adapters" / "web" / "static" / "flash.js").exists()


def test_transient_flash_assets_present():
    # the auto-hide ("show then hide") wiring exists
    import pathlib
    base = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
    assert "flash.js" in (base / "templates" / "base.html").read_text()
    js = (base / "static" / "flash.js").read_text()
    assert ".flash.transient" in js and "remove()" in js
    assert "flash-hide" in (base / "static" / "style.css").read_text()


def test_clear_stale_interactive_survives_unlink_io_error(tmp_path, monkeypatch):
    # Stale-marker cleanup runs AFTER lifecycle work; a PermissionError (not just a
    # containment error) from safe_unlink must NOT escape as an unhandled exception.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.mark_interactive("chat", "433")             # a stale interactive marker exists
    def boom(self, path):
        raise PermissionError("EACCES")
    monkeypatch.setattr(Paths, "safe_unlink", boom)
    assert svc._safe_unlink(svc._interactive_marker("chat")) is False   # typed, not raised
    svc.clear_stale_interactive(keep="daemon")      # must NOT raise
    svc.dismiss_interactive("chat")                 # must NOT raise


def test_daemon_start_stop_confirm_shows_band(tmp_path):
    # The daemon START and STOP confirm dialog must include the band (e.g. "start daemon 433").
    # Make the daemon installed+built so start reaches the confirm page (not the redirect guard).
    bind = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    bind.mkdir(parents=True)
    (bind / "loraham_daemon").write_text("#!bin")
    client = _real_app(tmp_path)
    token = _csrf(client, "/stacks/daemon")
    for op in ("start", "stop"):
        r = client.post("/action", data={"_csrf": token, "op": op, "target": "daemon",
                                         "band": "433"})   # no 'confirmed' -> stage-1 plan page
        body = r.get_data(as_text=True)
        assert r.status_code == 200, f"{op}: {r.status_code}"
        assert f"Confirm: {op}" in body and "daemon 433" in body


def test_daemon_confirm_hides_band_tx_cad_params_but_preserves_them(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    body = c.post("/action",
                  data={"_csrf": token, "op": "start", "target": "daemon", "p_radio": "868"}).get_data(as_text=True)
    # radio / tx / cad-monitor / cad-rssi are removed from the visible grid...
    for name in ("tx_433", "cadmon_433", "cadrssi_433"):
        assert f'type="hidden" name="p_{name}"' in body        # ...kept as hidden inputs
    assert 'type="hidden" name="p_radio" value="868"' in body  # band selection preserved
    assert 'name="p_debug"' in body                            # debug stays in the grid
    assert 'name="dp_868_CADRSSI"' in body                     # CAD RSSI in the 868 panel


def test_confirm_start_daemon_params_inline_client_reset(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    c = _real_app(tmp_path)
    token = _csrf(c, "/stacks/daemon")
    body = c.post("/action",
                  data={"_csrf": token, "op": "start", "target": "daemon", "p_radio": "433"}).get_data(as_text=True)
    # Panel inputs are part of the confirm form (applied for this start), with defaults for reset.
    assert 'name="dp_433_CADIDLE"' in body and "data-dpdefault=" in body
    assert 'type="button" class="act dp-reset-inline"' in body     # client-side reset...
    assert "/daemon-params/reset" not in body                      # ...NOT the server config-reset


# --- A5: band-aware observed conflicts in the web UI ----------------------------------------

def _conflict_app(tmp_path, cmdlines, socks, mesh_band):
    def factory():
        svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines, unix_replies=socks).system,
                                paths=Paths(runtime_root=tmp_path))
        svc._set_running_band("meshtastic", mesh_band)
        return svc
    return create_app(service_factory=factory).test_client()


_RDY_A5 = b"STATUS RADIO=READY TXMODE=MANAGED\n"


def test_stacks_pages_suppress_false_daemon433_vs_meshtastic868(tmp_path):
    # daemon serving ONLY 433 + meshtastic on 868 must NOT show a conflict.
    c = _conflict_app(tmp_path, {100: ["loraham_daemon", "--radio", "433"], 200: ["meshtasticd"]},
                      {"/tmp/loraconf433.sock": _RDY_A5}, "868")
    body = c.get("/stacks").get_data(as_text=True)
    assert "loraham.radio.433" not in body and "loraham.radio.868" not in body   # no false conflict
    detail = c.get("/stacks/meshtastic").get_data(as_text=True)
    assert "OBSERVED" not in detail


def test_stacks_pages_show_true_daemon_both_vs_meshtastic868(tmp_path):
    # daemon serving BOTH + meshtastic on 868 IS a real conflict on 868 -> shown.
    c = _conflict_app(tmp_path, {100: ["loraham_daemon", "--radio", "both"], 200: ["meshtasticd"]},
                      {"/tmp/loraconf433.sock": _RDY_A5, "/tmp/loraconf868.sock": _RDY_A5}, "868")
    body = c.get("/stacks").get_data(as_text=True)
    assert "loraham.radio.868" in body and "loraham.radio.433" not in body
    detail = c.get("/stacks/meshtastic").get_data(as_text=True)
    assert "OBSERVED" in detail and "loraham.radio.868" in detail


# --- Start-confirm "Stack parameters" panel + CALL/node enforcement + Save -------------------

def _install_igate(tmp_path):
    # igate shares LoRaHAM_Daemon source; create its built binary so the start-confirm renders.
    from lhpc.core.services import ControllerService as _CS
    svc = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    srcdir = svc._lifecycle().source_dir(svc.stack("igate").main_component)
    srcdir.mkdir(parents=True, exist_ok=True)
    (srcdir / "loraham_igate").write_text("#!/bin/sh\n")
    return _real_app(tmp_path)


def test_confirm_shows_stack_params_panel(tmp_path):
    c = _install_igate(tmp_path)
    tok = _csrf(c, "/stacks/igate")
    body = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate"}).get_data(as_text=True)
    assert "Stack parameters" in body                        # the new panel
    assert 'name="_params" value="1"' in body                # confirm-form marker
    assert 'name="p_call"' in body                           # the identity run param
    assert 'class="act sp-reset"' in body                    # client Reset-to-defaults
    assert 'name="_save" value="stack">Save</button>' in body  # Save persists to config
    assert '<span class="req"' in body                       # identity marked required


def test_confirm_blocks_empty_call_and_highlights(tmp_path):
    c = _install_igate(tmp_path)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "confirmed": "yes", "_params": "1", "p_call": "", "band": ""})
    body = r.get_data(as_text=True)
    assert r.status_code == 200                              # re-rendered, not started
    assert 'class="advcfg stackparams" open' in body         # panel expanded
    assert "field-bad" in body                               # offending field highlighted
    assert "callsign is required" in body.lower() or "valid callsign" in body.lower()


def test_confirm_save_stack_persists_config(tmp_path):
    c = _install_igate(tmp_path)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "_save": "stack", "_params": "1",
                                "p_call": "DJ0CHE-10", "p_tx_freq": "434.500", "band": ""})
    assert r.status_code == 200                              # re-rendered confirm, not started
    # persisted to the user config
    from lhpc.core.services import ControllerService as _CS
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-10" and cfg["tx_freq"] == "434.500"


def test_confirm_save_does_not_start(tmp_path):
    # A ReadOnlyGuard app would raise if Save invoked a mutating lifecycle method — Save only writes
    # config. Use the guarded client to prove Save never starts.
    binp = tmp_path / "src" / "LoRaHAM_Daemon" / "loraham_igate"
    binp.parent.mkdir(parents=True); binp.write_text("#!/bin/sh\n")
    c = _client(tmp_path)                                    # ReadOnlyGuard
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "_save": "stack", "_params": "1", "p_call": "DJ0CHE", "band": ""})
    assert r.status_code == 200                              # no start attempted (no guard tripwire)


def test_confirm_save_then_start_persists_and_starts(tmp_path):
    # The modal "Save & start" path sets _save=all + _save_then_start=1: persist, then proceed to
    # apply (which here blocks later in the pipeline, but MUST get past enforcement + save first).
    c = _install_igate(tmp_path)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "confirmed": "yes", "_save": "all", "_save_then_start": "1",
                                "_params": "1", "p_call": "DJ0CHE-10", "band": ""})
    assert r.status_code in (302, 303)                       # proceeded to apply (not a re-render)
    from lhpc.core.services import ControllerService as _CS
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-10"                        # saved before starting


def test_confirm_daemon_inline_save_persists(tmp_path):
    c = _install_igate(tmp_path)                             # igate is a daemon client -> has panel
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "_save": "daemon", "_params": "1",
                                "dp_433_SF": "10", "band": "433"})
    assert r.status_code == 200                              # re-rendered confirm, not started
    from lhpc.core.services import ControllerService as _CS
    svc = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    rows = {r["name"]: r for r in svc.daemon_params_view("igate", "433")["rows"]}
    assert rows["SF"]["value"] == "10"


# --- Area 2: fail-closed Save & start -------------------------------------------------------

def _spy_starts(monkeypatch):
    """Record run_action(apply=True, op=start) calls and stub them (no real start)."""
    from lhpc.core.services import ControllerService, ActionResult
    starts = []
    orig = ControllerService.run_action
    def spy(self, op, target, apply=False, **k):
        if apply and op == "start":
            starts.append(target)
            return ActionResult(True, "started (stub)")
        return orig(self, op, target, apply=apply, **k)
    monkeypatch.setattr(ControllerService, "run_action", spy)
    return starts


def test_save_and_start_blocks_on_failed_stack_save(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "could not save (disk full)"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "band": ""})
    assert r.status_code == 200 and starts == []             # re-rendered, run_action(apply) NOT called
    assert "not started" in r.get_data(as_text=True).lower()


def test_save_and_start_blocks_on_failed_daemon_save(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_daemon_params",
                        lambda self, *a, **k: ActionResult(False, "CONF write failed"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_433_SF": "10", "band": ""})
    assert r.status_code == 200 and starts == []             # daemon save failed -> no start


def test_save_and_start_blocks_on_invalid_daemon_form(tmp_path, monkeypatch):
    c = _install_igate(tmp_path)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_bad": "x", "band": ""})   # malformed dp_
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and starts == []
    assert "daemon" in body.lower()


def test_failed_save_and_start_rerenders_submitted_values(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "nope"))
    _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-99", "dp_433_SF": "9", "band": ""})
    body = r.get_data(as_text=True)
    assert 'value="DJ0CHE-99"' in body                       # submitted stack value preserved
    assert 'value="9"' in body                               # submitted daemon-panel value preserved


def test_save_and_start_success_persists_and_starts_once(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService as _CS
    c = _install_igate(tmp_path)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-7", "band": ""})
    assert starts == ["igate"]                               # started exactly once, after saving
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-7"                         # persisted before starting


def test_start_without_saving_is_ephemeral(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService as _CS
    c = _install_igate(tmp_path)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_params": "1", "p_call": "DJ0CHE-8", "band": ""})   # no _save
    assert starts == ["igate"]                               # started
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] != "DJ0CHE-8"                         # ephemeral: NOT persisted


# --- Area 2: Save & start short-circuits after the first failed persistence ------------------

def test_failed_stack_save_short_circuits_daemon_save_and_start(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "stack save failed"))
    daemon_saves = []
    monkeypatch.setattr(ControllerService, "save_daemon_params",
                        lambda self, *a, **k: daemon_saves.append(1) or ActionResult(True, "ok"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_433_SF": "10", "band": ""})
    assert r.status_code == 200
    assert daemon_saves == []                    # daemon save NEVER reached after stack-save failure
    assert starts == []                          # run_action(apply=True) never called


def test_stack_ok_then_daemon_fail_is_partial_and_non_starting(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_daemon_params",   # stack save is REAL (succeeds)
                        lambda self, *a, **k: ActionResult(False, "CONF write failed"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-13", "dp_433_SF": "10", "band": ""})
    assert r.status_code == 200 and starts == []               # not started
    body = r.get_data(as_text=True)
    assert "not started" in body.lower()                       # truthful report
    assert 'value="DJ0CHE-13"' in body and 'value="10"' in body  # submitted stack + daemon visible
    # the earlier successful stack save is RETAINED
    cfg = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-13"


# --- Area 3: truthful daemon-save reporting -------------------------------------------------

def test_daemon_only_save_and_start_failure_no_false_stack_claim(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    sb_calls = []
    orig_sb = ControllerService.save_config_bundle
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: sb_calls.append(1) or orig_sb(self, *a, **k))
    monkeypatch.setattr(ControllerService, "save_daemon_params",
                        lambda self, *a, **k: ActionResult(False, "CONF write failed"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/igate")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "daemon", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_433_SF": "10", "band": ""})
    body = r.get_data(as_text=True).lower()
    assert starts == []                              # not started
    assert sb_calls == []                            # _save=daemon -> NO stack save attempted
    assert "not saved: daemon 433" in body           # truthful about the daemon failure
    assert "saved: stack config" not in body         # NEVER falsely claims stack config was saved


def test_daemon_433_ok_868_fail_partial_no_start(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True); binp.write_text("#!/bin/sh\n")    # daemon installed+built
    c = _real_app(tmp_path)
    saved_bands = []
    def _sd(self, target, band, values):
        ok = band != "868"                           # 433 succeeds, 868 fails
        if ok:
            saved_bands.append(band)
        return ActionResult(ok, "saved" if ok else "868 CONF write failed")
    monkeypatch.setattr(ControllerService, "save_daemon_params", _sd)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c, "/stacks/daemon")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_radio": "both", "dp_433_SF": "9", "dp_868_SF": "9", "band": ""})
    body = r.get_data(as_text=True).lower()
    assert starts == []                              # not started
    assert saved_bands == ["433"]                    # 433 persisted (retained); 868 failed
    assert "433 mhz: saved" in body and "868 mhz: save failed" in body   # both outcomes shown accurately


# --- component identity end to end through the web (stack-target collisions) -----------------

def _collide_app(tmp_path):
    from test_stack_params import _SCOPE2_MANIFEST
    m = tmp_path / "col.toml"; m.write_text(_SCOPE2_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "files").mkdir(parents=True, exist_ok=True)
    return m, _real_app(tmp_path, manifest=m)


def test_stack_confirm_panel_distinct_collision_fields(tmp_path):                  # (1)
    m, c = _collide_app(tmp_path)
    # save distinct scoped values for the colliding components
    svc = ControllerService(manifest_path=m, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("ostack2", values={"tgt.rp": "RP-T", "dep.rp": "RP-D",
                                                     "file_tgt.fp": "FP-T", "file_dep.fp": "FP-D"}).ok
    tok = _csrf(c, "/stacks/ostack2")
    body = c.post("/action", data={"_csrf": tok, "op": "start", "target": "ostack2"}).get_data(as_text=True)
    # distinct, component-qualified field names — never a shared bare field
    assert 'name="p_tgt__rp"' in body and 'name="p_dep__rp"' in body
    assert 'name="pf_tgt__fp"' in body and 'name="pf_dep__fp"' in body
    assert 'name="p_rp"' not in body and 'name="pf_fp"' not in body
    # each colliding component shows its OWN saved value
    assert 'value="RP-T"' in body and 'value="RP-D"' in body
    assert 'value="FP-T"' in body and 'value="FP-D"' in body


def test_stack_save_and_start_scoped_per_component(tmp_path, monkeypatch):         # (2)
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    from lhpc.core import config as cfgmod
    m, c = _collide_app(tmp_path)
    seen = {}
    def stub(self, stack, comp, cfg, band=""):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    tok = _csrf(c, "/stacks/ostack2")
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "ostack2", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_tgt__rp": "RP-T", "p_dep__rp": "RP-D", "p_uniq": "U-FLAT",
                                "pf_tgt__fp": "FP-T", "pf_dep__fp": "FP-D", "band": ""})
    assert r.status_code in (200, 302)
    cfg = cfgmod.load_stack_config(Paths(runtime_root=tmp_path), "ostack2")
    assert cfg["__r__tgt__rp"] == "RP-T" and cfg["__r__dep__rp"] == "RP-D"     # scoped run keys
    assert cfg["__f__tgt__fp"] == "FP-T" and cfg["__f__dep__fp"] == "FP-D"     # scoped file keys
    assert cfg["uniq"] == "U-FLAT" and "__r__tgt__uniq" not in cfg            # unique stays flat
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"         # launched per component
    files = tmp_path / "config" / "files"
    assert "FP=FP-T" in (files / "tgt.conf").read_text()                      # own generated config
    assert "FP=FP-D" in (files / "dep.conf").read_text()
    assert not (files / "sib.conf").exists()                                  # sibling never generated


# --- permanent Config page: component-aware (collision fixture) ------------------------------

def test_config_page_distinct_collision_fields_and_values(tmp_path):
    m, c = _collide_app(tmp_path)
    svc = ControllerService(manifest_path=m, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("ostack2", values={"tgt.rp": "RP-T", "dep.rp": "RP-D",
                                                     "file_tgt.fp": "FP-T", "file_dep.fp": "FP-D"}).ok
    body = c.get("/stacks/ostack2/config").get_data(as_text=True)
    assert 'name="c_tgt__rp"' in body and 'name="c_dep__rp"' in body        # distinct run fields
    assert 'name="f_tgt__fp"' in body and 'name="f_dep__fp"' in body        # distinct file fields
    assert 'name="c_rp"' not in body and 'name="f_fp"' not in body          # no shared bare field
    assert 'name="c_uniq"' in body                                          # unique stays bare
    assert 'value="RP-T"' in body and 'value="RP-D"' in body                # each component's own value
    assert 'value="FP-T"' in body and 'value="FP-D"' in body


def test_config_page_post_persists_scoped_and_reloads(tmp_path):
    from lhpc.core import config as cfgmod
    m, c = _collide_app(tmp_path)
    tok = _csrf(c, "/stacks/ostack2/config")
    r = c.post("/stacks/ostack2/config",
               data={"_csrf": tok, "band": "", "c_tgt__rp": "RP-T", "c_dep__rp": "RP-D",
                     "c_uniq": "U-FLAT", "f_tgt__fp": "FP-T", "f_dep__fp": "FP-D"})
    assert r.status_code in (200, 302)
    cfg = cfgmod.load_stack_config(Paths(runtime_root=tmp_path), "ostack2")
    assert cfg["__r__tgt__rp"] == "RP-T" and cfg["__r__dep__rp"] == "RP-D"    # scoped run keys
    assert cfg["__f__tgt__fp"] == "FP-T" and cfg["__f__dep__fp"] == "FP-D"    # scoped file keys
    assert cfg["uniq"] == "U-FLAT" and "__r__tgt__uniq" not in cfg            # unique stays flat
    body = c.get("/stacks/ostack2/config").get_data(as_text=True)             # reloads correctly
    assert 'value="RP-T"' in body and 'value="RP-D"' in body


def test_config_saved_values_launch_per_component(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    m, c = _collide_app(tmp_path)
    tok = _csrf(c, "/stacks/ostack2/config")
    c.post("/stacks/ostack2/config",
           data={"_csrf": tok, "band": "", "c_tgt__rp": "RP-T", "c_dep__rp": "RP-D",
                 "c_uniq": "U", "f_tgt__fp": "FP-T", "f_dep__fp": "FP-D"})
    seen = {}
    def stub(self, stack, comp, cfg, band=""):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    ControllerService(manifest_path=m, system=FakeSystem().system,
                      paths=Paths(runtime_root=tmp_path)).start("ostack2", apply=True)
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"        # own saved run value
    files = tmp_path / "config" / "files"
    assert "FP=FP-T" in (files / "tgt.conf").read_text()                     # own generated file config
    assert "FP=FP-D" in (files / "dep.conf").read_text()
    assert not (files / "sib.conf").exists()

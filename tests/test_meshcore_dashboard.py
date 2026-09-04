"""The openHop repeater dashboard as the MeshCore stack's SECOND proxied page (0.2.8):
`meshcore` stays the MeshCore Web UI with every saved `meshcore_*` proxy setting, the dashboard is
`meshcore-meshcore-node`, and LHPC's proxy refuses every dashboard route that would mutate the
repeater's configuration (the deny list is source-derived from the pinned upstream)."""
from __future__ import annotations

from lhpc.core import webserver as ws
from lhpc.core.config import save_stackweb_config
from lhpc.core.manifest import load_manifest
from lhpc.core.model import web_pages
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.webserver import StackWebProxy


def _svc(tmp_path, mode="chat+repeater"):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    if mode != "chat":
        r = svc.save_config("meshcore", {"file_mode": mode, "file_repeater_name": "Relay"})
        assert r.ok, (r.summary, r.details)
        svc._invalidate_config()
    return svc


def test_the_web_ui_keeps_the_stack_id_and_the_dashboard_is_the_second_page():
    # AUDITOR P1: adding meshcore-node:8000 must NOT make it the primary `meshcore` page.
    st = {s.id: s for s in load_manifest()}["meshcore"]
    pages = web_pages(st)
    assert [(p.page_id, p.component_id, p.address) for p in pages] == [
        ("meshcore", "meshcore-webui", "127.0.0.1:8788"),
        ("meshcore-meshcore-node", "meshcore-node", "127.0.0.1:8000")]
    assert pages[0].primary and not pages[1].primary
    # the node carries the stack's own name, so the page is named by what it IS
    assert pages[1].name == "openHop repeater dashboard"
    assert pages[1].label == "MeshCore (OpenHop) · openHop repeater dashboard"
    # the other stacks are untouched by the main-last rule
    for sid in ("graywolf", "meshtastic", "meshcom"):
        assert [p.page_id for p in web_pages({s.id: s for s in load_manifest()}[sid])] == [sid]


def test_saved_meshcore_proxy_settings_still_govern_the_web_ui(tmp_path):
    svc = _svc(tmp_path)
    save_stackweb_config(svc._paths, "meshcore", mode="lan", port=8451,
                         allowed_cidrs=["192.168.178.0/24"])
    svc._invalidate_config()
    v = svc.stack_web_view("meshcore")
    assert v["upstream_address"] == "127.0.0.1:8788" and v["cfg"].port == 8451
    d = svc.stack_web_view("meshcore-meshcore-node")
    assert d["upstream_address"] == "127.0.0.1:8000" and not d["cfg"].enabled
    # positions: the four first pages keep 8444-8447, the dashboard comes after them
    console = svc.config().webserver.port
    assert svc._page_positions()[-1] == "meshcore-meshcore-node"
    assert svc._default_stack_web_port("meshcore-meshcore-node", console) == console + 5
    assert svc.stack_web_eligible() == ["graywolf", "meshtastic", "meshcom", "meshcore",
                                        "meshcore-meshcore-node"]


def test_the_dashboard_login_is_lhpcs_minted_password_on_the_node(tmp_path):
    svc = _svc(tmp_path)
    creds = svc.ui_credentials("meshcore", "meshcore-node")
    assert creds["user"] == "admin" and creds["path"].endswith("openhop_repeater_admin.txt")
    assert "LHPC mints this password" in creds["note"]          # not graywolf's contract
    assert svc.ui_credentials("meshcore", "meshcore-webui") == {}
    assert [c["component_id"] for c in svc.ui_credentials_list("meshcore")] == ["meshcore-node"]
    assert "signs in on every start" in svc.ui_credentials("graywolf")["note"]


def test_the_dashboard_page_exists_in_every_mode_and_says_when_it_is_served(tmp_path, monkeypatch):
    """User decision (2026-09-04): both pages always — like every stack's page exists while the
    stack is stopped — so the proxy is configured BEFORE the mode is switched and a saved policy
    is never hidden. The panel says when the upstream is not served in the saved mode; the
    Password section says when the admin password is not minted yet."""
    svc = _svc(tmp_path, mode="chat")
    assert [p.page_id for p in svc.stack_web_pages("meshcore")] == ["meshcore", "meshcore-meshcore-node"]
    assert "meshcore-meshcore-node" in svc.stack_web_eligible()
    dash, webui = svc.web_page("meshcore-meshcore-node"), svc.web_page("meshcore")
    assert "Not served in the current mode (chat)" in svc.page_mode_note(dash)
    assert "chat+repeater and repeater" in svc.page_mode_note(dash)
    assert svc.page_mode_note(webui) == ""
    assert svc.page_mode_note(svc.web_page("graywolf")) == ""
    creds = svc.ui_credentials_list("meshcore")
    assert [c["component_id"] for c in creds] == ["meshcore-node"] and creds[0]["exists"] is False
    r = svc.stack_web_configure("meshcore-meshcore-node", port=8448)      # configurable in chat
    assert r.ok, (r.summary, r.details)
    assert svc.stack_web_view("meshcore-meshcore-node")["mode_note"]
    # the Dashboard row (rendered once the main component runs) carries the same note instead
    # of "listener absent — check its logs or restart it"
    from lhpc.core.model import RunState
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if ss.stack.id == "meshcore":
            ss.components["meshcore-node"].run_state = RunState.RUNNING
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self, *a, **k: snap)
    row = next(w for w in svc.dashboard_webservers() if w.get("pid") == "meshcore-meshcore-node")
    assert "Not served in the current mode (chat)" in row["mode_note"]
    assert "Mode switch" in row["mode_note"] and "chat+repeater and repeater" in row["mode_note"]
    # the other way round: in repeater-only the WEB UI's upstream is the one not served
    r = svc.save_config("meshcore", {"file_mode": "repeater", "file_repeater_name": "Relay"})
    assert r.ok
    svc._invalidate_config()
    assert svc.page_mode_note(dash) == ""
    assert "chat and chat+repeater" in svc.page_mode_note(webui)


def test_the_deny_regex_refuses_spelling_variants_and_sub_paths():
    import re
    rx = re.compile(ws.deny_location_regex("/api/set_mode"))
    for hit in ("/api/set_mode", "/api/set_mode/", "/api/set-mode", "/api/set.mode",
                "/api/set_mode/extra", "/api/set-mode/x/y"):
        assert rx.search(hit), hit
    for miss in ("/api/set_modes", "/api/stats", "/apix/set_mode", "/api/set_mode_x"):
        assert not rx.search(miss), miss
    rx2 = re.compile(ws.deny_location_regex("/api/config_export"))
    assert rx2.search("/api/config_export/true")                 # positional-arg bypass
    rx3 = re.compile(ws.deny_location_regex("/ws/companion_frame/"))
    assert rx3.search("/ws/companion_frame/anything")


def test_repeater_dashboard_proxy_denies_every_config_mutating_route(tmp_path):
    """Source-derived at openhop_repeater efc5616 (see the manifest comment): every route that
    mutates upstream configuration, the mesh CLI, identities, OTA and the companion-frame
    websocket. This guard fails if a manifest edit or a repin drops one."""
    svc = _svc(tmp_path)
    required = {
        "/api/setup_wizard", "/api/config_import", "/api/config_export",
        "/api/update_web_config", "/api/update_mqtt_config", "/api/update_duty_cycle_config",
        "/api/update_advert_rate_limit_config", "/api/set_duty_cycle", "/api/set_mode",
        "/api/restart_service", "/api/policy", "/api/policy_groups", "/api/policy_group_entries",
        "/api/transport_keys", "/api/transport_key", "/api/default_region",
        "/api/unscoped_flood_policy", "/api/update_radio_config", "/api/save_cad_settings",
        "/api/cad_calibration_start", "/api/cad_calibration_stop", "/api/cad_manual_check",
        "/api/cli", "/api/identities", "/api/identity", "/api/create_identity",
        "/api/update_identity", "/api/delete_identity", "/api/generate_vanity_key",
        "/api/identity_export", "/api/companion/set_advert_name",
        "/api/companion/set_advert_location", "/api/update/install", "/api/update/check",
        "/api/update/set_channel", "/auth/change_password", "/ws/companion_frame",
    }
    deny = set(svc.stack_web_deny_paths("meshcore-meshcore-node"))
    missing = required - deny
    assert not missing, f"repeater dashboard proxy no longer denies: {sorted(missing)}"
    assert "/api/auth/tokens" in deny                             # a second credential
    allowed = {"/api/stats", "/api/logs", "/api/send_advert", "/auth/login", "/ws/packets",
               "/api/needs_setup", "/api/recent_packets"}
    assert not (allowed & deny)
    # and the MeshCore Web UI's own list is untouched by the sibling page
    assert "/api/device/reset" in svc.stack_web_deny_paths("meshcore")
    assert "/api/set_mode" not in svc.stack_web_deny_paths("meshcore")
    up = svc.stack_web_upstream("meshcore-meshcore-node")

    class _SWC:
        stack_id = "meshcore-meshcore-node"; enabled = True; remote = False; allowed_cidrs = []
        scheme = "https"; port = 8448; access_mode = "auth-everywhere"

    out = ws.render_nginx_config(
        svc._paths, svc.config().webserver,
        [StackWebProxy(_SWC(), up[0], up[1], svc.stack_web_deny_paths("meshcore-meshcore-node"))])
    for p in required:
        assert f"location ~ {ws.deny_location_regex(p)} {{ return 403; }}" in out, p
    assert "location = " not in out.split("# meshcore-meshcore-node web UI")[1]   # no exact-only form
    assert "upstream lhpc_ui_meshcore_meshcore_node {" in out

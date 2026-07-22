"""Service + web layer for the per-stack web-UI proxies.

Includes the two truthfulness guards:
  * the raw-upstream-port warning fires from EVIDENCE (/proc/net/tcp), even in `local` mode;
  * an `http` console keeps the trusted-host policy although it must drop Secure cookies.
"""

from __future__ import annotations

import pytest

from lhpc.adapters.web.app import create_app
from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def _svc(tmp_path, listeners=()):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    fake = FakeSystem(listeners=[Listener(**l) for l in listeners])
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def _csrf(client, path="/stacks"):
    import re
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


# --- eligibility comes from the manifest, not a hardcoded list -------------------------------------

def test_eligible_stacks_are_derived_from_client_web_endpoints(tmp_path):
    svc = _svc(tmp_path)
    eligible = svc.stack_web_eligible()
    assert "meshcom" in eligible and "meshtastic" in eligible
    assert "daemon" not in eligible and "chat" not in eligible    # no client http endpoint


def test_upstream_is_read_from_the_manifest_endpoint(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_upstream("meshcom") == ("127.0.0.1:18083", "http")
    assert svc.stack_web_upstream("meshtastic") == ("127.0.0.1:9443", "https")
    assert svc.stack_web_upstream("daemon") is None


def test_view_is_empty_for_a_stack_without_a_web_ui(tmp_path):
    assert _svc(tmp_path).stack_web_view("daemon") == {}


# --- the raw-port bypass warning -------------------------------------------------------------------

def test_exposed_upstream_is_reported_as_bypassable(tmp_path):
    # meshtasticd binds all interfaces and has no bind knob: our proxy is NOT the only door.
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    view = svc.stack_web_view("meshtastic")
    assert view["upstream_scope"] == "exposed" and view["bypassable"] is True


def test_loopback_upstream_is_not_bypassable(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 2}])
    view = svc.stack_web_view("meshcom")
    assert view["upstream_scope"] == "loopback" and view["bypassable"] is False


def test_bypass_warning_fires_even_in_local_mode(tmp_path):
    # The raw port is exposed regardless of what we put in front of it, and the operator who chose
    # "local only" is exactly the person most likely to believe they are safe.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_stackweb_config(p, "meshtastic", mode="local", port=8445)
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    assert svc.stack_web_view("meshtastic")["bypassable"] is True


def test_configure_discloses_the_bypass_in_its_details(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    res = svc.stack_web_configure("meshtastic", mode="local", port=8445)
    assert res.ok
    assert any("bypassing this proxy" in d for d in res.details)
    assert any("LHPC cannot close it" in d for d in res.details)


# --- confirmation matrix ---------------------------------------------------------------------------

def test_local_needs_no_confirmation(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("meshcom", mode="local", port=8444).ok


def test_lan_needs_the_phrase(tmp_path):
    svc = _svc(tmp_path)
    r = svc.stack_web_configure("meshcom", mode="lan", port=8444, cidrs=["192.168.0.0/24"])
    combined = r.summary + " ".join(r.details)                     # aggregated refusal: reasons in details
    assert not r.ok and "confirmation required" in combined
    assert "--confirm-phrase enable-remote" in combined            # the exact flag is named
    assert svc.config().stackweb.get("meshcom") is None            # nothing written

    r = svc.stack_web_configure("meshcom", mode="lan", port=8444, cidrs=["192.168.0.0/24"],
                                confirm=True)
    assert r.ok and svc.config().stackweb["meshcom"].mode == "lan"


@pytest.mark.parametrize("kw", [
    dict(mode="public", cidrs=["0.0.0.0/0"]),
    dict(mode="lan", cidrs=["192.168.0.0/24"], access_mode="no-auth"),
    dict(mode="lan", cidrs=["192.168.0.0/24"], scheme="http", access_mode="no-auth"),
])
def test_elevated_cases_reject_the_weak_phrase(tmp_path, kw):
    svc = _svc(tmp_path)
    r = svc.stack_web_configure("meshcom", port=8444, confirm=True, **kw)
    combined = r.summary + " ".join(r.details)                     # aggregated refusal: reasons in details
    assert not r.ok and "elevated confirmation" in combined
    assert "--confirm-phrase enable-remote-danger" in combined     # the exact strong phrase is named
    assert svc.config().stackweb.get("meshcom") is None

    r = svc.stack_web_configure("meshcom", port=8444, confirm=True, confirm_public=True, **kw)
    assert r.ok


def test_remote_without_cidr_is_refused(tmp_path):
    r = _svc(tmp_path).stack_web_configure("meshcom", mode="lan", port=8444, confirm=True)
    assert not r.ok and any("allowed source CIDR" in d for d in r.details)


def test_http_with_certificate_auth_is_refused(tmp_path):
    r = _svc(tmp_path).stack_web_configure("meshcom", mode="local", port=8444,
                                           scheme="http", access_mode="auth-everywhere")
    assert not r.ok
    assert any("cannot do client-certificate authentication" in d for d in r.details)


def test_port_collision_with_the_console_is_refused(tmp_path):
    r = _svc(tmp_path).stack_web_configure("meshcom", mode="local", port=8443)
    assert not r.ok and any("console's port" in d for d in r.details)


def test_port_collision_between_stacks_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("meshcom", mode="local", port=8444).ok
    r = svc.stack_web_configure("meshtastic", mode="local", port=8444)
    assert not r.ok and any("another stack" in d for d in r.details)


def test_configure_refuses_a_stack_without_a_web_ui(tmp_path):
    assert not _svc(tmp_path).stack_web_configure("daemon", port=8444).ok


def test_default_ports_are_stable_per_stack_and_never_collide(tmp_path):
    # The default is deterministic per stack, NOT "first free above the console" — which handed
    # every not-yet-enabled stack 8444, so accepting two suggestions collided on 8444.
    svc = _svc(tmp_path)
    assert svc.stack_web_view("meshcom")["suggested_port"] == 8444
    assert svc.stack_web_view("meshtastic")["suggested_port"] == 8445   # distinct even when neither is enabled
    # stable after one is enabled
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    assert svc.stack_web_view("meshtastic")["suggested_port"] == 8445
    assert svc.stack_web_view("meshcom")["suggested_port"] == 8444


def test_default_port_prefills_the_form_so_saving_enables_the_proxy(tmp_path):
    # A blank port silently saves as 0 (disabled). The form pre-fills the default value, so it is
    # submitted and the proxy actually listens.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    import re
    body = create_app(lambda: svc).test_client().get("/stacks?open=meshcom").get_data(as_text=True)
    i = body.index('id="stack-webserver-meshcom"')
    panel = body[i:body.index("</details>", i)]
    m = re.search(r'<input name="port"[^>]*>', panel)
    assert m and 'value="8444"' in m.group(0), m.group(0) if m else "no port input"


# --- rendering is wired through ---------------------------------------------------------------------

def test_enabled_stack_reaches_the_rendered_nginx_config(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("meshcom", mode="local", port=8444).ok
    proxies = svc._stack_web_proxies()
    assert len(proxies) == 1
    assert proxies[0].upstream_address == "127.0.0.1:18083"
    assert proxies[0].upstream_scheme == "http"
    from lhpc.core import webserver as _ws
    conf = _ws.render_nginx_config(Paths(runtime_root=tmp_path), svc.config().webserver, proxies)
    assert "upstream lhpc_ui_meshcom" in conf and "listen 127.0.0.1:8444 ssl;" in conf


def test_disabled_stack_contributes_no_proxy(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("meshcom", mode="local", port=0).ok
    assert svc._stack_web_proxies() == []


# --- dashboard links never mislead -------------------------------------------------------------------

class _Obs:
    def __init__(self, spec):
        self.spec, self.present = spec, True


class _Spec:
    client = True
    def __init__(self, address, scheme, description=""):
        self.address, self.scheme, self.description = address, scheme, description


class _Status:
    def __init__(self, eps):
        self.endpoints = eps


def _ifaces(svc, sid):
    st = _Status([_Obs(_Spec("127.0.0.1:18083", "http", "MeshCom web UI"))])
    return svc._client_interfaces(st, sid)


def test_unproxied_web_ui_keeps_its_honest_loopback_literal(tmp_path):
    itf = _ifaces(_svc(tmp_path), "meshcom")[0]
    assert itf["link"] == "http://127.0.0.1:18083"          # it really IS loopback-only


def test_applied_remote_proxy_links_the_reachable_address(tmp_path):
    # An APPLIED lan/public proxy: nginx is live on 0.0.0.0:8444, so the interface is truthfully
    # remote (proxy_remote), in sync with the saved mode (not pending). The dashboard fills the host
    # from request.host; the CLI/no-request fallback link is loopback.
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8444, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="lan", port=8444, cidrs=["192.168.178.0/24"],
                            confirm=True)
    itf = _ifaces(svc, "meshcom")[0]
    assert itf["proxy_remote"] and itf["proxy_port"] == 8444 and itf["proxy_scheme"] == "https"
    assert "local only" not in itf["label"] and itf["pending"] is False
    assert itf["link"] == "https://127.0.0.1:8444/"          # loopback fallback (dash uses request.host)


def test_dashboard_link_uses_the_host_the_browser_reached_the_console_at(tmp_path):
    # Accessed remotely at a LAN IP or a hostname -> the mesh link points at THAT host on the proxy
    # port. Requires the proxy to be LIVE on all interfaces (applied), which the injected listener models.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8444, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="lan", port=8444, cidrs=["0.0.0.0/0"],
                            confirm=True, confirm_public=True)
    app = create_app(lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False   # allow an arbitrary Host through in tests
    c = app.test_client()
    body = c.get("/", headers={"Host": "pi.example.lan:8443"}).get_data(as_text=True)
    # the console host, meshcom's proxy port — regardless of local_ip
    if "MeshCom" in body and "iface-web" in body:
        assert "https://pi.example.lan:8444/" in body


def test_drift_local_mode_but_exposed_listener_is_truthfully_remote(tmp_path):
    # THE REPORTED BUG: config says `local`, but the running nginx still holds 0.0.0.0:8444 (mode was
    # changed to local without an Apply). The link must reflect REALITY — remotely reachable, flagged
    # `pending` — never a misleading "local only".
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8444, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    itf = _ifaces(svc, "meshcom")[0]
    assert itf["proxy_remote"] and itf["proxy_port"] == 8444
    assert "local only" not in itf["label"] and itf["pending"] is True


def test_drift_remote_mode_not_yet_applied_is_loopback_and_pending(tmp_path):
    # Saved `public` but Apply not run: the live listener is still 127.0.0.1 -> honestly loopback, and
    # flagged pending so the operator knows to Apply.
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8445, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="public", port=8445, cidrs=["0.0.0.0/0"],
                            confirm=True, confirm_public=True)
    itf = _ifaces(svc, "meshtastic")[0]
    assert not itf["proxy_remote"] and "local only" in itf["label"] and itf["pending"] is True


def test_enabled_proxy_with_no_listener_is_marked_not_active(tmp_path):
    # Enabled in config but nothing listening on the port (nginx down / never applied): honest
    # "not active", pending an Apply — not a dead remote link.
    svc = _svc(tmp_path)                                      # no listeners
    svc.stack_web_configure("meshcom", mode="lan", port=8444, cidrs=["0.0.0.0/0"],
                            confirm=True, confirm_public=True)
    itf = _ifaces(svc, "meshcom")[0]
    assert not itf["proxy_remote"] and "Apply" in itf["label"] and itf["pending"] is True


def test_url_host_helper_is_ipv6_safe():
    from lhpc.adapters.web.app import _url_host
    assert _url_host("192.168.1.5:8443") == "192.168.1.5"
    assert _url_host("pi.local:8443") == "pi.local"
    assert _url_host("[::1]:8443") == "[::1]"           # re-bracketed for a URL
    assert _url_host("::1") == "[::1]"


def test_applied_local_proxy_is_labelled_local_only(tmp_path):
    # Applied local proxy: nginx is live on 127.0.0.1:8444 -> loopback, honestly labelled, not pending.
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8444, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    itf = _ifaces(svc, "meshcom")[0]
    assert itf["link"] == "https://127.0.0.1:8444/"
    assert "local only" in itf["label"] and itf["pending"] is False   # honest for a remote reader


def test_view_reports_live_listen_scope_and_pending_drift(tmp_path):
    # stack_web_view carries the EFFECTIVE listen scope + a pending flag for the stacks-page header.
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8444, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="local", port=8444)   # desired local, live exposed -> drift
    v = svc.stack_web_view("meshcom")
    assert v["listen_scope"] == "exposed" and v["pending"] is True


def test_view_in_sync_public_is_not_pending(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8445, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="public", port=8445, cidrs=["0.0.0.0/0"],
                            confirm=True, confirm_public=True)
    v = svc.stack_web_view("meshtastic")
    assert v["listen_scope"] == "exposed" and v["pending"] is False


def test_view_disabled_proxy_is_absent_and_not_pending(tmp_path):
    v = _svc(tmp_path).stack_web_view("meshcom")             # never configured
    assert v["listen_scope"] == "absent" and v["pending"] is False


def test_stacks_panel_shows_running_state(tmp_path):
    # The stacks-page Webserver header states the running pill: with the stack's web-UI upstream (18083)
    # AND the nginx proxy port (8444) both listening, it reads "proxied" (green).
    app, svc = _app(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 1},
                               {"family": "ipv4", "ip": "0.0.0.0", "port": 8444, "inode": 2}])
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    body = app.test_client().get("/stacks?open=meshcom").get_data(as_text=True)   # webserver panel is deferred
    assert 'id="stack-webserver-meshcom"' in body
    assert ">proxied</span>" in body


def test_stack_running_pill_offline_localonly_proxied(tmp_path):
    UP = {"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 1}   # stack's web-UI upstream
    PROXY = {"family": "ipv4", "ip": "0.0.0.0", "port": 8444, "inode": 2}   # nginx proxy port
    # (i) stack not started -> upstream absent -> grey "offline"
    svc = _svc(tmp_path); svc.stack_web_configure("meshcom", mode="local", port=8444)
    p = svc.stack_web_view("meshcom")["posture"]
    assert p["run"] == "offline" and p["run_level"] == "off"
    # (ii) stack started, nginx not proxying -> upstream up, proxy port absent -> yellow "local-only"
    svc = _svc(tmp_path, [UP]); svc.stack_web_configure("meshcom", mode="local", port=8444)
    p = svc.stack_web_view("meshcom")["posture"]
    assert p["run"] == "local-only" and p["run_level"] == "warn"
    # (iii) stack started AND nginx proxying -> both listening -> green "proxied"
    svc = _svc(tmp_path, [UP, PROXY]); svc.stack_web_configure("meshcom", mode="local", port=8444)
    p = svc.stack_web_view("meshcom")["posture"]
    assert p["run"] == "proxied" and p["run_level"] == "ok"


def test_dashboard_webservers_always_has_lhcp_console_and_hides_stopped_stacks(tmp_path):
    # The dashboard Webserver box always leads with the LHCP console row (with its posture pills); a stack
    # row appears only when that stack is running — nothing is running here, so only the console row.
    rows = _svc(tmp_path).dashboard_webservers()
    assert rows and rows[0]["kind"] == "console" and rows[0]["name"] == "LHCP"
    assert rows[0]["posture"] and rows[0]["posture"]["run"] in ("nginx", "lhpc-web")
    assert all(r["kind"] == "console" for r in rows)          # no running web-UI stacks -> no stack rows


def test_dashboard_port_row_excludes_non_network_serial_pty(tmp_path):
    # KISS has TWO client endpoints: the TCP interface (127.0.0.1:8001, scheme "kiss") and the optional
    # socat PTY (address "state/loraham_kiss", scheme "serial" — a local device path, not a network port).
    # The network-exposure box must advertise ONLY the TCP interface: one line for KISS, not two.
    fake = FakeSystem(cmdlines_data={42: ["./loraham-kiss-tnc", "--config", "loraham_kiss_tnc.conf.example"]},
                      listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8001, inode=1)])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    kiss = [r for r in svc.dashboard_webservers() if r.get("sid") == "kiss" and r["kind"] == "port"]
    assert len(kiss) == 1                                      # one interface, not two
    assert kiss[0]["port"] == "8001"                           # the TCP port, NOT "state/loraham_kiss"


def test_dashboard_not_proxied_web_ui_shows_direct_address_and_name_link(tmp_path, monkeypatch):
    # A running but NOT-proxied web UI shows its DIRECT address (reached host + endpoint port) BEFORE
    # "not proxied", and each name links to the respective webserver config on the Apps page.
    from lhpc.core.services import ControllerService
    rows = [{"kind": "console", "name": "LHCP", "port": "8770", "logs_component": None,
             "posture": {"auth": "open", "iface": "loopback", "sec_level": "ok", "scheme": "https",
                         "auth_level": "ok", "iface_level": "ok", "scheme_level": "ok",
                         "run": "lhpc-web", "run_level": "ok"}},
            {"kind": "stack", "name": "MeshCom (QEMU)", "sid": "meshcom", "enabled": False,
             "posture": None, "port": None, "direct_port": "18083", "direct_scheme": "http",
             "logs_component": None}]
    monkeypatch.setattr(ControllerService, "dashboard_webservers", lambda self, **k: rows)
    app, _ = _app(tmp_path)
    body = app.test_client().get("/").get_data(as_text=True)
    assert ":18083" in body and "not proxied" in body               # direct address IS shown
    assert body.index(":18083") < body.index("not proxied")         # …BEFORE "not proxied"
    assert 'href="/stacks?open=meshcom#stack-webserver-meshcom"' in body    # stack name -> its ws config
    assert 'href="/stacks#webserver-row"' in body                          # console name -> console ws config
    assert 'pill-warn"><a class="wsurl"' in body                    # http direct addr -> yellow pill wrapping a link
    assert 'href="http://' in body                                  # direct address is a clickable http:// URL
    assert 'wsurl" href="https://' in body                          # console address is a clickable https:// URL pill


def test_stack_monitor_carries_the_same_exposure_warnings_as_the_console(tmp_path):
    from lhpc.core import webserver as _ws
    svc = _svc(tmp_path); svc.stack_web_configure("meshcom", mode="local", port=8444)
    v = svc.stack_web_view("meshcom")
    # Identical wording/values to the console Monitor — driven by the SINGLE shared source.
    assert v["warnings"] == _ws.exposure_warnings(
        remote=False, access_mode="local-open-remote-auth", allowed_cidrs=(),
        bind="127.0.0.1", port=8444, live_scope=v["listen_scope"])
    assert any("Remote exposure is disabled — listening on loopback only" in w["text"]
               for w in v["warnings"])


# --- web routes ---------------------------------------------------------------------------------------

def _app(tmp_path, listeners=()):
    svc = _svc(tmp_path, listeners)
    return create_app(lambda: svc), svc


def test_route_requires_csrf(tmp_path):
    app, _ = _app(tmp_path)
    assert app.test_client().post("/stacks/meshcom/webserver").status_code == 400


def test_route_404s_for_unknown_and_non_web_stacks(tmp_path):
    app, _ = _app(tmp_path)
    c = app.test_client()
    tok = _csrf(c)
    assert c.post("/stacks/nope/webserver", data={"_csrf": tok}).status_code == 404
    assert c.post("/stacks/daemon/webserver", data={"_csrf": tok}).status_code == 404


def test_route_saves_and_redirects_to_the_panel(tmp_path):
    app, svc = _app(tmp_path)
    c = app.test_client()
    r = c.post("/stacks/meshcom/webserver",
               data={"_csrf": _csrf(c), "mode": "local", "port": "8444"})
    assert r.status_code == 302 and r.headers["Location"].endswith("#stack-webserver-meshcom")
    # anchors the webserver panel, NOT ?cfg (which would wrongly open Settings)
    assert "cfg=" not in r.headers["Location"]
    assert svc.config().stackweb["meshcom"].port == 8444


def test_route_maps_the_typed_phrase_like_webserver_configure(tmp_path):
    app, svc = _app(tmp_path)
    c = app.test_client()
    base = {"_csrf": _csrf(c), "mode": "public", "port": "8444", "cidrs": "0.0.0.0/0"}
    c.post("/stacks/meshcom/webserver", data={**base, "confirm_phrase": "enable-remote"})
    assert svc.config().stackweb.get("meshcom") is None           # weak phrase: nothing written
    c.post("/stacks/meshcom/webserver", data={**base, "confirm_phrase": "enable-remote-danger"})
    assert svc.config().stackweb["meshcom"].mode == "public"


def test_panel_renders_with_the_bypass_warning(tmp_path):
    app, _ = _app(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    body = app.test_client().get("/stacks?open=meshtastic").get_data(as_text=True)   # panel is in the deferred body
    assert 'id="stack-webserver-meshtastic"' in body
    assert "listening on all interfaces" in body and "depnote-bad" in body
    assert "bypassing this proxy" in body


def test_panel_absent_for_a_stack_without_a_web_ui(tmp_path):
    app, _ = _app(tmp_path)
    body = app.test_client().get("/stacks").get_data(as_text=True)
    assert 'id="stack-webserver-daemon"' not in body


# --- console over http -------------------------------------------------------------------------------

def test_http_console_keeps_the_trusted_host_policy_without_secure_cookies(tmp_path):
    # Gating _trusted_host on SESSION_COOKIE_SECURE would have switched the allowlist OFF for an
    # http console, because a browser discards Secure cookies over plain http.
    app, _ = _app(tmp_path)
    c = app.test_client()
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["LHPC_PRODUCTIVE"] = True
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200

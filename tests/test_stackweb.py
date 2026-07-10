"""Per-stack web-UI reverse proxies (MeshCom, Meshtastic) behind the console's nginx.

Three properties carry the whole feature:
  * a deployment that proxies nothing renders BYTE-IDENTICALLY to the pre-feature config;
  * the LISTENER scheme and the UPSTREAM scheme are independent (a cleartext upstream must never
    disable outside TLS);
  * `http` cannot authenticate anyone, and the code says so instead of pretending.
"""

from __future__ import annotations

import pathlib

import pytest

from lhpc.core import webserver
from lhpc.core.config import StackWebConfig, WebserverConfig
from lhpc.core.paths import Paths

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GOLDEN_ROOT = pathlib.Path("/GOLDEN")        # stable absolute paths; never touched


def _paths():
    return Paths(runtime_root=GOLDEN_ROOT)


def _proxy(sid, upstream="127.0.0.1:18083", upstream_scheme="http", **kw):
    return webserver.StackWebProxy(StackWebConfig(stack_id=sid, **kw), upstream, upstream_scheme)


# --- the default must not move ------------------------------------------------------------------

@pytest.mark.parametrize("name,cfg", [
    ("local-open-remote-auth", WebserverConfig()),
    ("auth-everywhere", WebserverConfig(access_mode="auth-everywhere")),
    ("no-auth", WebserverConfig(access_mode="no-auth")),
    ("exposed", WebserverConfig(bind="0.0.0.0", remote_exposed=True, port=8443,
                                allowed_cidrs=("192.168.0.0/24",))),
])
def test_default_render_is_byte_identical_to_the_pre_feature_config(name, cfg):
    want = (FIXTURES / f"nginx-{name}.conf").read_text()
    assert webserver.render_nginx_config(_paths(), cfg) == want
    assert webserver.render_nginx_config(_paths(), cfg, stack_webs=()) == want


def test_no_proxy_tokens_leak_into_the_default_render():
    # The websocket map in particular must be conditional, or the byte-identity above is a lie.
    conf = webserver.render_nginx_config(_paths(), WebserverConfig())
    for token in ("lhpc_ui_", "$http_upgrade", "lhpc_conn_upgrade", "proxy_ssl_verify"):
        assert token not in conf, token


def test_a_disabled_stack_emits_nothing():
    conf = webserver.render_nginx_config(_paths(), WebserverConfig(),
                                         [_proxy("meshcom", port=0, mode="lan")])
    assert conf == (FIXTURES / "nginx-local-open-remote-auth.conf").read_text()


# --- the proxy block ------------------------------------------------------------------------------

def test_lan_proxy_reuses_the_console_client_ca():
    # The whole point: ONE client certificate authenticates the console and every stack UI.
    conf = webserver.render_nginx_config(
        _paths(), WebserverConfig(),
        [_proxy("meshcom", mode="lan", port=8444, allowed_cidrs=("192.168.178.0/24",))])
    assert "upstream lhpc_ui_meshcom { server 127.0.0.1:18083; }" in conf
    assert "listen 0.0.0.0:8444 ssl;" in conf
    assert "proxy_pass http://lhpc_ui_meshcom;" in conf
    assert "allow 192.168.178.0/24;" in conf and "deny all;" in conf
    assert conf.count("/GOLDEN/config/tls/client-ca/ca.crt") == 2      # console + stack
    assert conf.count("/GOLDEN/config/tls/client-ca/crl.pem") == 2


def test_websocket_map_appears_exactly_once_for_many_blocks():
    conf = webserver.render_nginx_config(_paths(), WebserverConfig(), [
        _proxy("meshcom", mode="lan", port=8444, allowed_cidrs=("10.0.0.0/8",)),
        _proxy("meshtastic", "127.0.0.1:9443", "https", mode="local", port=8445),
    ])
    assert conf.count("map $http_upgrade $lhpc_conn_upgrade") == 1
    assert conf.count("upstream lhpc_ui_") == 2


def test_local_mode_listens_on_loopback_only():
    conf = webserver.render_nginx_config(_paths(), WebserverConfig(),
                                         [_proxy("meshcom", mode="local", port=8444)])
    assert "listen 127.0.0.1:8444 ssl;" in conf
    assert "0.0.0.0" not in conf


def test_stack_listener_does_not_follow_the_console_bind():
    # Separately-confirmed policies: resetting the console to loopback must not relocate a mesh UI
    # the operator deliberately exposed, and a remote console must not expose a `local` mesh UI.
    loopback_console = WebserverConfig()                            # bind 127.0.0.1, not exposed
    conf = webserver.render_nginx_config(
        _paths(), loopback_console,
        [_proxy("meshcom", mode="lan", port=8444, allowed_cidrs=("10.0.0.0/8",))])
    assert "listen 0.0.0.0:8444 ssl;" in conf                       # stack keeps its own policy
    assert "listen 127.0.0.1:8443 ssl;" in conf                     # console keeps its own

    exposed_console = WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                                      allowed_cidrs=("10.0.0.0/8",))
    conf2 = webserver.render_nginx_config(_paths(), exposed_console,
                                          [_proxy("meshcom", mode="local", port=8444)])
    assert "listen 127.0.0.1:8444 ssl;" in conf2
    assert "listen 0.0.0.0:8443 ssl;" in conf2


def test_https_upstream_gets_proxy_ssl_verify_off():
    # meshtasticd's cert is self-signed and the hop is loopback; verification is impossible and moot.
    conf = webserver.render_nginx_config(
        _paths(), WebserverConfig(),
        [_proxy("meshtastic", "127.0.0.1:9443", "https", mode="local", port=8445)])
    assert "proxy_pass https://lhpc_ui_meshtastic;" in conf
    assert "proxy_ssl_verify off;" in conf


def test_cleartext_upstream_never_disables_the_public_tls_listener():
    # THE confusion this guards: MeshCom's upstream is plain http on loopback.
    conf = webserver.render_nginx_config(
        _paths(), WebserverConfig(),
        [_proxy("meshcom", "127.0.0.1:18083", "http", mode="lan", port=8444,
                scheme="https", allowed_cidrs=("10.0.0.0/8",))])
    assert "listen 0.0.0.0:8444 ssl;" in conf                       # listener_scheme = https
    assert "proxy_pass http://lhpc_ui_meshcom;" in conf             # upstream_scheme = http
    assert "proxy_ssl_verify" not in conf


def test_http_listener_has_no_tls_and_no_mtls():
    conf = webserver.render_nginx_config(
        _paths(), WebserverConfig(),
        [_proxy("meshcom", mode="local", port=8444, scheme="http", access_mode="no-auth")])
    assert "listen 127.0.0.1:8444;" in conf                         # no ` ssl`
    block = conf[conf.index("# meshcom web UI"):]
    assert "ssl_certificate" not in block and "ssl_client_certificate" not in block
    assert "ssl_verify_client" not in block


# --- nginx identifiers ----------------------------------------------------------------------------

def test_nginx_token_sanitizes_ids_unusable_as_nginx_variables():
    assert webserver.nginx_token("mesh-com") == "mesh_com"
    assert webserver.nginx_token("a.b@c") == "a_b_c"
    assert webserver.nginx_token("meshcom") == "meshcom"


def test_hyphenated_stack_id_never_reaches_a_variable_name():
    conf = webserver.render_nginx_config(_paths(), WebserverConfig(),
                                         [_proxy("mesh-com", mode="local", port=8444)])
    assert "upstream lhpc_ui_mesh_com {" in conf
    assert "$lhpc_need_auth_mesh_com" in conf
    assert "lhpc_ui_mesh-com" not in conf and "$lhpc_need_auth_mesh-com" not in conf
    # the raw id survives only inside a comment
    assert "# mesh-com web UI" in conf


def test_colliding_tokens_refuse_to_render_rather_than_merge_blocks():
    with pytest.raises(ValueError, match="both map to the nginx identifier"):
        webserver.render_nginx_config(_paths(), WebserverConfig(), [
            _proxy("mesh-com", mode="local", port=8444),
            _proxy("mesh_com", mode="local", port=8445),
        ])


# --- policy ---------------------------------------------------------------------------------------

def _plan(**kw):
    return webserver.plan_stack_exposure(StackWebConfig(stack_id="meshcom", **kw), 8443, ())


def test_disabled_stack_needs_no_confirmation():
    assert _plan(port=0)["danger"] == "none"


def test_local_needs_no_confirmation():
    assert _plan(port=8444, mode="local")["danger"] == "none"


def test_lan_with_auth_is_a_normal_confirmation():
    p = _plan(port=8444, mode="lan", allowed_cidrs=("192.168.0.0/24",))
    assert p["remote"] and p["danger"] == "normal" and not p["problems"]


@pytest.mark.parametrize("kw,flag", [
    (dict(mode="public", allowed_cidrs=("0.0.0.0/0",)), "public"),
    (dict(mode="lan", allowed_cidrs=("192.168.0.0/24",), access_mode="no-auth"), "no_auth"),
    (dict(mode="lan", allowed_cidrs=("192.168.0.0/24",), scheme="http",
          access_mode="no-auth"), "cleartext"),
])
def test_remote_public_noauth_or_cleartext_each_demand_the_strong_phrase(kw, flag):
    p = _plan(port=8444, **kw)
    assert p["danger"] == "elevated" and p[flag] is True


def test_remote_without_a_cidr_is_refused():
    assert "at least one allowed source CIDR" in " ".join(_plan(port=8444, mode="lan")["problems"])


def test_http_with_certificate_auth_is_refused_as_impossible():
    p = _plan(port=8444, mode="local", scheme="http", access_mode="auth-everywhere")
    assert any("cannot do client-certificate authentication" in x for x in p["problems"])


@pytest.mark.parametrize("port,needle", [
    (80, "out of range"),
    (70000, "out of range"),
    (8443, "already the console's port"),
])
def test_port_validation(port, needle):
    p = webserver.plan_stack_exposure(StackWebConfig("meshcom", port=port), 8443, ())
    assert any(needle in x for x in p["problems"])


def test_port_collision_between_two_stacks_is_refused():
    p = webserver.plan_stack_exposure(StackWebConfig("meshcom", port=8444), 8443, (8444,))
    assert any("used by another stack" in x for x in p["problems"])


# --- listener_scope: evidence from THIS host ------------------------------------------------------

def _sys_with(listeners):
    from lhpc.core.probes.backends import FakeSystem, Listener
    return FakeSystem(listeners=[Listener(**l) for l in listeners]).system


def test_listener_scope_reports_exposed_for_a_wildcard_bind():
    s = _sys_with([{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    assert webserver.listener_scope(s, 9443) == "exposed"


def test_listener_scope_reports_exposed_for_a_concrete_lan_ip():
    s = _sys_with([{"family": "ipv4", "ip": "192.168.178.95", "port": 9443, "inode": 1}])
    assert webserver.listener_scope(s, 9443) == "exposed"


def test_listener_scope_reports_loopback():
    s = _sys_with([{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 2}])
    assert webserver.listener_scope(s, 18083) == "loopback"


def test_listener_scope_reports_ipv6_loopback():
    s = _sys_with([{"family": "ipv6", "ip": "::1", "port": 18083, "inode": 2}])
    assert webserver.listener_scope(s, 18083) == "loopback"


def test_listener_scope_reports_absent_when_nothing_listens():
    assert webserver.listener_scope(_sys_with([]), 9443) == "absent"


def test_listener_scope_prefers_exposed_when_a_port_has_both_binds():
    # A process listening on 127.0.0.1 AND 0.0.0.0 is reachable remotely; say so.
    s = _sys_with([{"family": "ipv4", "ip": "127.0.0.1", "port": 9443, "inode": 1},
                   {"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 2}])
    assert webserver.listener_scope(s, 9443) == "exposed"


# --- display URLs ---------------------------------------------------------------------------------

def test_stack_ui_urls_local_is_loopback_only(monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.178.95")
    swc = StackWebConfig("meshcom", mode="local", port=8444)
    assert webserver.stack_ui_urls(swc) == ["https://127.0.0.1:8444/"]


def test_stack_ui_urls_lan_puts_the_reachable_address_first(monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.178.95")
    swc = StackWebConfig("meshcom", mode="lan", port=8444, allowed_cidrs=("10.0.0.0/8",))
    assert webserver.stack_ui_urls(swc) == ["https://192.168.178.95:8444/", "https://127.0.0.1:8444/"]


def test_stack_ui_urls_degrade_when_local_ip_is_unknown(monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "")
    swc = StackWebConfig("meshcom", mode="lan", port=8444)
    assert webserver.stack_ui_urls(swc) == ["https://127.0.0.1:8444/"]   # never "https://:8444/"


def test_stack_ui_urls_empty_when_not_proxied():
    assert webserver.stack_ui_urls(StackWebConfig("meshcom")) == []

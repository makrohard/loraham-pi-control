"""Per-stack web-UI reverse proxies (MeshCom, Meshtastic) behind the console's nginx.

Three properties carry the whole feature:
  * a deployment that proxies nothing renders BYTE-IDENTICALLY to the pre-feature config;
  * the LISTENER scheme and the UPSTREAM scheme are independent (a cleartext upstream must never
    disable outside TLS);
  * `http` cannot authenticate anyone, and the code says so instead of pretending."""


from __future__ import annotations
import pathlib
import pytest
from lhpc.core import webserver, config as cfgmod
from lhpc.core.config import StackWebConfig, WebserverConfig, ConfigError, load_config, save_stackweb_config
from lhpc.core.paths import Paths
from lhpc.adapters.web.app import create_app
from lhpc.core.probes.backends import FakeSystem, Listener, CommandResult as CR
from lhpc.core.services import ControllerService


# ===== merged from test_stackweb.py =====
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


GOLDEN_ROOT = pathlib.Path("/GOLDEN")        # stable absolute paths; never touched


def _paths():
    return Paths(runtime_root=GOLDEN_ROOT)


def _proxy(sid, upstream="127.0.0.1:18083", upstream_scheme="http", **kw):
    return webserver.StackWebProxy(StackWebConfig(stack_id=sid, **kw), upstream, upstream_scheme)


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


def _sys_with(listeners):
    from lhpc.core.probes.backends import FakeSystem, Listener
    return FakeSystem(listeners=[Listener(**l) for l in listeners]).system


@pytest.mark.parametrize("binds,port,expected", [
    pytest.param([{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}], 9443, "exposed",
                 id="wildcard-bind-exposed"),
    pytest.param([{"family": "ipv4", "ip": "192.168.178.95", "port": 9443, "inode": 1}], 9443, "exposed",
                 id="concrete-lan-ip-exposed"),
    pytest.param([{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 2}], 18083, "loopback",
                 id="ipv4-loopback"),
    pytest.param([{"family": "ipv6", "ip": "::1", "port": 18083, "inode": 2}], 18083, "loopback",
                 id="ipv6-loopback"),
    pytest.param([], 9443, "absent", id="absent-nothing-listens"),
])
def test_listener_scope_reports(binds, port, expected):
    assert webserver.listener_scope(_sys_with(binds), port) == expected


def test_listener_scope_prefers_exposed_when_a_port_has_both_binds():
    # A process listening on 127.0.0.1 AND 0.0.0.0 is reachable remotely; say so.
    s = _sys_with([{"family": "ipv4", "ip": "127.0.0.1", "port": 9443, "inode": 1},
                   {"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 2}])
    assert webserver.listener_scope(s, 9443) == "exposed"


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


# ===== merged from test_stackweb_config.py =====
def _paths_stackweb_config(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return Paths(runtime_root=tmp_path)


def _write_local(tmp_path, text):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(text)


@pytest.mark.parametrize("key,expect", [
    ("meshcom_access_mode", ("meshcom", "access_mode")),       # NOT ("meshcom", "access")
    ("meshcom_allowed_cidrs", ("meshcom", "allowed_cidrs")),   # NOT ("meshcom_allowed", "cidrs")
    ("meshcom_mode", ("meshcom", "mode")),
    ("meshcom_port", ("meshcom", "port")),
    ("meshcom_scheme", ("meshcom", "scheme")),
    ("my_stack_port", ("my_stack", "port")),                   # stack ids may contain underscores
    ("meshcom_bogus", None),
    ("_port", None),
    ("port", None),
])
def test_key_split_is_suffix_driven(key, expect):
    assert cfgmod._split_stackweb_key(key) == expect


def test_access_mode_survives_a_round_trip_through_the_parser(tmp_path):
    # The regression this file exists for: a first-underscore split loses this value entirely.
    _write_local(tmp_path, '[stackweb]\nmeshcom_access_mode = "auth-everywhere"\nmeshcom_port = 8444\n')
    sw = load_config(_paths_stackweb_config(tmp_path)).stackweb["meshcom"]
    assert sw.access_mode == "auth-everywhere"


def test_full_entry_parses(tmp_path):
    _write_local(tmp_path, '[stackweb]\n'
                           'meshcom_mode = "lan"\n'
                           'meshcom_port = 8444\n'
                           'meshcom_scheme = "https"\n'
                           'meshcom_access_mode = "local-open-remote-auth"\n'
                           'meshcom_allowed_cidrs = "192.168.178.0/24,10.0.0.0/8"\n')
    sw = load_config(_paths_stackweb_config(tmp_path)).stackweb["meshcom"]
    assert (sw.mode, sw.port, sw.scheme) == ("lan", 8444, "https")
    assert sw.allowed_cidrs == ("192.168.178.0/24", "10.0.0.0/8")
    assert sw.enabled and sw.remote


def test_absent_table_yields_no_entries(tmp_path):
    _write_local(tmp_path, "")
    assert load_config(_paths_stackweb_config(tmp_path)).stackweb == {}


def test_default_is_not_proxied(tmp_path):
    _write_local(tmp_path, '[stackweb]\nmeshcom_mode = "lan"\n')
    sw = load_config(_paths_stackweb_config(tmp_path)).stackweb["meshcom"]
    assert sw.port == 0 and not sw.enabled       # no port -> renders no nginx block at all


@pytest.mark.parametrize("line,diag", [
    ('meshcom_mode = "sideways"', "unknown stackweb.meshcom_mode"),
    ('meshcom_port = 80', "invalid stackweb.meshcom_port"),
    ('meshcom_port = true', "invalid stackweb.meshcom_port"),
    ('meshcom_scheme = "gopher"', "unknown stackweb.meshcom_scheme"),
    ('meshcom_access_mode = "root"', "unknown stackweb.meshcom_access_mode"),
    ('meshcom_allowed_cidrs = "not-a-cidr"', "dropped invalid stackweb.meshcom_allowed_cidrs"),
    ('meshcom_bogus = "x"', "dropped unknown stackweb key"),
])
def test_malformed_values_degrade_with_a_diagnostic(tmp_path, line, diag):
    _write_local(tmp_path, f"[stackweb]\n{line}\n")
    cfg = load_config(_paths_stackweb_config(tmp_path))
    assert any(diag in d for d in cfg.diagnostics), cfg.diagnostics


def test_a_bad_sibling_does_not_take_the_others_down(tmp_path):
    _write_local(tmp_path, '[stackweb]\nmeshcom_port = 8444\nmeshtastic_bogus = "x"\n')
    cfg = load_config(_paths_stackweb_config(tmp_path))
    assert cfg.stackweb["meshcom"].port == 8444


def test_non_table_stackweb_is_a_diagnostic(tmp_path):
    _write_local(tmp_path, 'stackweb = "nope"\n')
    cfg = load_config(_paths_stackweb_config(tmp_path))
    assert cfg.stackweb == {} and any("non-table [stackweb]" in d for d in cfg.diagnostics)


def test_parser_downgrades_a_hand_edited_http_plus_cert_auth(tmp_path):
    # Fail-soft: parsing never crashes. It must not silently keep a mode nginx would ignore either.
    _write_local(tmp_path, '[stackweb]\nmeshcom_port = 8444\nmeshcom_scheme = "http"\n'
                           'meshcom_access_mode = "auth-everywhere"\n')
    cfg = load_config(_paths_stackweb_config(tmp_path))
    assert cfg.stackweb["meshcom"].access_mode == "no-auth"
    assert any("cannot do client-certificate auth" in d for d in cfg.diagnostics)


def test_console_parser_downgrades_http_plus_cert_auth(tmp_path):
    _write_local(tmp_path, '[webserver]\nscheme = "http"\naccess_mode = "auth-everywhere"\n')
    cfg = load_config(_paths_stackweb_config(tmp_path))
    assert cfg.webserver.access_mode == "no-auth"
    assert any("cannot do client-certificate auth" in d for d in cfg.diagnostics)


def test_saving_http_with_cert_auth_is_refused(tmp_path):
    p = _paths_stackweb_config(tmp_path)
    with pytest.raises(ConfigError, match="cannot do client-certificate"):
        save_stackweb_config(p, "meshcom", port=8444, scheme="http", access_mode="auth-everywhere")


def test_saving_http_alone_is_refused_against_the_stored_cert_mode(tmp_path):
    # Neither half may sneak in alone: the check resolves patch-over-stored.
    p = _paths_stackweb_config(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444, scheme="https", access_mode="auth-everywhere")
    with pytest.raises(ConfigError, match="cannot do client-certificate"):
        save_stackweb_config(p, "meshcom", scheme="http")


def test_saving_http_with_no_auth_is_allowed(tmp_path):
    p = _paths_stackweb_config(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444, scheme="http", access_mode="no-auth")
    sw = load_config(p).stackweb["meshcom"]
    assert sw.scheme == "http" and sw.access_mode == "no-auth"


def test_console_saving_http_with_cert_auth_is_refused(tmp_path):
    p = _paths_stackweb_config(tmp_path)
    with pytest.raises(ConfigError, match="cannot do client-certificate"):
        cfgmod.save_webserver_config(p, scheme="http", access_mode="auth-everywhere")


def test_save_roundtrip_and_partial_update(tmp_path):
    p = _paths_stackweb_config(tmp_path)
    save_stackweb_config(p, "meshcom", mode="lan", port=8444,
                         allowed_cidrs=["192.168.178.0/24"])
    sw = load_config(p).stackweb["meshcom"]
    assert (sw.mode, sw.port, sw.allowed_cidrs) == ("lan", 8444, ("192.168.178.0/24",))
    save_stackweb_config(p, "meshcom", port=8500)          # None = leave unchanged
    sw = load_config(p).stackweb["meshcom"]
    assert sw.port == 8500 and sw.mode == "lan" and sw.allowed_cidrs == ("192.168.178.0/24",)


def test_two_stacks_do_not_clobber_each_other(tmp_path):
    p = _paths_stackweb_config(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444)
    save_stackweb_config(p, "meshtastic", port=8445)
    sw = load_config(p).stackweb
    assert sw["meshcom"].port == 8444 and sw["meshtastic"].port == 8445


@pytest.mark.parametrize("kw", [
    dict(port=80), dict(port=70000), dict(mode="sideways"),
    dict(scheme="gopher"), dict(access_mode="root"), dict(allowed_cidrs=["nope"]),
])
def test_save_validates_before_writing(tmp_path, kw):
    p = _paths_stackweb_config(tmp_path)
    with pytest.raises(Exception):
        save_stackweb_config(p, "meshcom", **kw)
    assert load_config(p).stackweb == {}                   # nothing written


def test_port_zero_disables_and_is_savable(tmp_path):
    p = _paths_stackweb_config(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444)
    save_stackweb_config(p, "meshcom", port=0)
    assert not load_config(p).stackweb["meshcom"].enabled


# ===== merged from test_stackweb_service.py =====
def _svc(tmp_path, listeners=()):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    fake = FakeSystem(listeners=[Listener(**l) for l in listeners])
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def _csrf(client, path="/stacks"):
    import re
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


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


@pytest.mark.parametrize("stack,binds,scope,bypassable", [
    # an exposed upstream (binds all interfaces, no bind knob) means our proxy is NOT the only door
    pytest.param("meshtastic", [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}],
                 "exposed", True, id="exposed-upstream-bypassable"),
    pytest.param("meshcom", [{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 2}],
                 "loopback", False, id="loopback-upstream-not-bypassable"),
])
def test_upstream_bypassability(tmp_path, stack, binds, scope, bypassable):
    svc = _svc(tmp_path, binds)
    view = svc.stack_web_view(stack)
    assert view["upstream_scope"] == scope and view["bypassable"] is bypassable


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
    assert any("managed firewall can close" in d for d in res.details)


def test_local_needs_no_confirmation_via_configure(tmp_path):
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
    # console_port + 1 + position among the web-UI stacks sorted BY ID, so "graywolf"
    # takes the first slot and the mesh* stacks follow it.
    assert svc.stack_web_view("graywolf")["suggested_port"] == 8444
    assert svc.stack_web_view("meshcom")["suggested_port"] == 8445
    assert svc.stack_web_view("meshtastic")["suggested_port"] == 8446   # distinct even when none is enabled
    # stable after one is enabled
    svc.stack_web_configure("meshcom", mode="local", port=8445)
    assert svc.stack_web_view("meshtastic")["suggested_port"] == 8446
    assert svc.stack_web_view("meshcom")["suggested_port"] == 8445
    assert svc.stack_web_view("graywolf")["suggested_port"] == 8444


def test_password_section_names_the_account_and_never_the_secret(tmp_path):
    """A stack whose app mints its own web-UI password gets a Password sub-section: the account
    name, where the file is, and a copyable command to read it ON THE BOX. The value itself must
    never be rendered — a password on a page ends up in screenshots and chat history."""
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)

    creds = svc.ui_credentials("graywolf")
    assert creds["user"] == "admin"
    assert creds["path"].endswith("state/graywolf/graywolf-admin.txt")
    assert creds["command"] == f"cat {creds['path']}"
    assert svc.ui_credentials("kiss") == {}          # only declared where it applies

    # A real secret in the file must not leak into the page.
    pw_file = tmp_path / "state" / "graywolf" / "graywolf-admin.txt"
    pw_file.parent.mkdir(parents=True, exist_ok=True)
    pw_file.write_text("s3cret-do-not-render\n")

    body = create_app(lambda: svc).test_client().get("/stacks?open=graywolf").get_data(as_text=True)
    assert 'id="stack-password-graywolf"' in body
    assert "<summary>Password</summary>" in body
    assert 'data-copy="uipw-graywolf"' in body
    assert creds["command"] in body
    assert "s3cret-do-not-render" not in body


def test_default_port_skips_a_port_another_stack_already_saved(tmp_path):
    # Positional defaults are only stable while the eligible SET is stable: adding a stack whose
    # id sorts earlier shifts everyone after it, and on an upgraded box that shift can land on a
    # port another stack has ALREADY saved. A suggestion Apply would refuse ("already used by
    # another stack's web UI") is a dead prefill, so a taken port is skipped.
    svc = _svc(tmp_path)
    assert svc.stack_web_view("graywolf")["suggested_port"] == 8444
    svc.stack_web_configure("meshcom", mode="local", port=8444)      # the pre-upgrade choice
    assert svc.stack_web_view("graywolf")["suggested_port"] == 8445   # not the taken 8444
    assert svc.stack_web_view("meshcom")["suggested_port"] == 8444    # its own saved port stands


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
    assert m and 'value="8445"' in m.group(0), m.group(0) if m else "no port input"


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
    fake = FakeSystem(cmdlines_data={42: ["./loraham-kiss-tnc", "--config",
                                          "loraham_kiss_tnc.conf.example", "--kiss-port", "8001",
                                          "--kiss-host", "127.0.0.1"]},
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


def test_http_console_keeps_the_trusted_host_policy_without_secure_cookies(tmp_path):
    # Gating _trusted_host on SESSION_COOKIE_SECURE would have switched the allowlist OFF for an
    # http console, because a browser discards Secure cookies over plain http.
    app, _ = _app(tmp_path)
    c = app.test_client()
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["LHPC_PRODUCTIVE"] = True
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200


# ===== merged from test_stackweb_verify.py =====
def _fake(tmp_path, listeners=(), nginx_ok=True):
    staged = str(Paths(runtime_root=tmp_path).under(*webserver.NGINX_CONF_STAGED))
    cmds = {("nginx", "-v"): CR(0, "", "nginx version: 1.0")}
    cmds[("nginx", "-t", "-c", staged)] = (CR(0, "", "ok") if nginx_ok
                                           else CR(1, "", "nginx: [emerg] bad"))
    return FakeSystem(commands=cmds, listeners=[Listener(**l) for l in listeners])


def _svc_stackweb_verify(tmp_path, listeners=(), nginx_ok=True):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=_fake(tmp_path, listeners, nginx_ok).system,
                             paths=Paths(runtime_root=tmp_path))


def _staged_text(tmp_path):
    return (tmp_path / "config" / "nginx" / "lhpc.conf.staged").read_text()


def _proxy_stackweb_verify(sid, upstream="127.0.0.1:18083", uscheme="http", **kw):
    return webserver.StackWebProxy(StackWebConfig(stack_id=sid, **kw), upstream, uscheme)


def test_verify_validates_the_stack_proxy_blocks_not_a_console_only_config(tmp_path):
    svc = _svc_stackweb_verify(tmp_path)
    assert svc.stack_web_configure("meshcom", mode="local", port=8444).ok
    res = svc.webserver_verify()
    # The staged config `verify` ran `nginx -t` against must contain the proxy block.
    text = _staged_text(tmp_path)
    assert "upstream lhpc_ui_meshcom" in text and "listen 127.0.0.1:8444" in text
    assert res.data["checks"]["nginx_config_valid"] == "ok"


def test_verify_surfaces_the_stack_proxies_as_evidence(tmp_path):
    svc = _svc_stackweb_verify(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    ev = svc.webserver_verify().data
    assert [p["stack_id"] for p in ev["stack_proxies"]] == ["meshcom"]
    assert ev["stack_proxies"][0]["upstream"] == "127.0.0.1:18083"
    assert ev["desired_snapshot"]["scheme"] == "https"


def test_verify_reports_a_stack_config_problem_as_a_config_failure(tmp_path):
    # A proxy whose policy is invalid makes the DESIRED config invalid; verify must say so.
    Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    # Hand-write a remote proxy with no CIDR (the writer would refuse; the parser keeps it).
    (tmp_path / "config" / "local.toml").write_text(
        '[stackweb]\nmeshcom_port = 8444\nmeshcom_mode = "lan"\n')
    svc = _svc_stackweb_verify(tmp_path)
    res = svc.webserver_verify()
    assert not res.ok
    assert any("meshcom: remote exposure requires at least one allowed source CIDR" in x
               for x in res.data["checks"]["config_problems"])


def test_verify_warns_about_a_bypassable_upstream_without_failing(tmp_path):
    # LHPC cannot close meshtasticd's port, so this is a standing WARNING, never a config failure.
    # (An https console with no PKI on disk fails server_cert here — that is a DIFFERENT, real
    # failure; what must not happen is the bypass itself entering the failed set.)
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    res = svc.webserver_verify()
    checks = res.data["checks"]
    assert checks["upstream_bypass"] == "warn"
    assert checks["upstream_bypass_stacks"] == ["meshtastic"]
    failed = [k for k, v in checks.items() if v == "failed"]
    assert "upstream_bypass" not in failed
    assert any("bypassing this proxy" in d for d in res.details)


def test_a_bypassable_upstream_alone_still_verifies_ok(tmp_path):
    # All-http desired config -> no PKI needed -> the ONLY finding is the (non-failing) warning.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    # A RUNNING deployment: console + proxy listeners bound loopback (exact-scope matching now
    # fails an absent listener — a dead front-end is not a successful local bind).
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1},
                          {"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 2},
                          {"family": "ipv4", "ip": "127.0.0.1", "port": 8445, "inode": 3}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445,
                            scheme="http", access_mode="no-auth")
    res = svc.webserver_verify()
    assert res.ok and res.data["checks"]["upstream_bypass"] == "warn"


def test_verify_of_a_loopback_upstream_raises_no_warning(tmp_path):
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    ev = svc.webserver_verify().data
    assert "upstream_bypass" not in ev["checks"]
    assert ev["stack_proxies"][0]["bypassable"] is False


def test_tls_required_follows_every_public_listener():
    https_console = WebserverConfig()
    http_console = WebserverConfig(scheme="http", access_mode="no-auth")
    assert webserver.tls_required(https_console, ()) is True
    assert webserver.tls_required(http_console, ()) is False
    # an http console with an https proxy still needs a server certificate
    assert webserver.tls_required(
        http_console, [_proxy_stackweb_verify("meshcom", mode="local", port=8444, scheme="https")]) is True
    # a disabled proxy contributes nothing
    assert webserver.tls_required(
        http_console, [_proxy_stackweb_verify("meshcom", port=0, scheme="https")]) is False
    assert webserver.tls_required(
        http_console, [_proxy_stackweb_verify("meshcom", mode="local", port=8444, scheme="http",
                              access_mode="no-auth")]) is False


def test_client_auth_required_is_not_decided_by_the_console_alone():
    # A no-auth console with a cert-auth stack proxy still makes nginx load the client CA + CRL.
    no_auth_console = WebserverConfig(access_mode="no-auth")
    assert webserver.client_auth_required(no_auth_console, ()) is False
    assert webserver.client_auth_required(
        no_auth_console,
        [_proxy_stackweb_verify("meshcom", mode="local", port=8444, access_mode="auth-everywhere")]) is True
    # and an http proxy can never verify a client cert, so it never demands one
    assert webserver.client_auth_required(
        no_auth_console,
        [_proxy_stackweb_verify("meshcom", mode="local", port=8444, scheme="http",
                access_mode="no-auth")]) is False


def test_all_http_config_verifies_without_any_pki(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    # console listener bound loopback (exact-scope matching fails an absent listener)
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1}])
    res = svc.webserver_verify()
    checks = res.data["checks"]
    assert checks["tls_required"] == "no"
    assert "server_cert" not in checks and "server_ca" not in checks
    assert "client_ca" not in checks and "crl" not in checks
    assert res.ok                                              # no certificate, and nothing failed


def test_https_config_still_demands_the_server_certificate(tmp_path):
    svc = _svc_stackweb_verify(tmp_path)                                       # default https console, no PKI on disk
    checks = svc.webserver_verify().data["checks"]
    assert checks["tls_required"] == "yes"
    assert checks["server_cert"] == "failed" and checks["server_ca"] == "failed"


def test_http_console_with_an_https_proxy_still_demands_the_certificate(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    svc = _svc_stackweb_verify(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444, scheme="https")
    checks = svc.webserver_verify().data["checks"]
    assert checks["tls_required"] == "yes" and checks["server_cert"] == "failed"


def test_no_auth_console_with_a_cert_auth_proxy_checks_the_client_ca(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, access_mode="no-auth")
    svc = _svc_stackweb_verify(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444, access_mode="auth-everywhere")
    checks = svc.webserver_verify().data["checks"]
    assert checks["client_ca"] == "failed" and checks["crl"] == "failed"


def _start_svc(tmp_path, monkeypatch, listeners=()):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    staged = str(Paths(runtime_root=tmp_path).under(*webserver.NGINX_CONF_STAGED))
    cmds = {("nginx", "-v"): CR(0, "", "1.0"),
            ("nginx", "-t", "-c", staged): CR(0, "", "ok"),
            ("systemctl", "--user", "enable", "--now", "lhpc-nginx.service"): CR(0, "", "")}
    fake = FakeSystem(commands=cmds, listeners=[Listener(**l) for l in listeners])
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def test_start_service_refuses_without_a_cert_when_tls_is_needed(tmp_path, monkeypatch):
    r = _start_svc(tmp_path, monkeypatch).webserver_start_service()
    assert not r.ok and "no HTTPS server certificate" in r.summary


def test_start_service_starts_an_all_http_config_without_pki(tmp_path, monkeypatch):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    r = _start_svc(tmp_path, monkeypatch).webserver_start_service()
    assert r.ok, r.summary
    assert "http://127.0.0.1:8443/" in r.summary
    assert "https://" not in r.summary


def test_start_service_url_is_never_the_bind_wildcard(tmp_path, monkeypatch):
    # `https://0.0.0.0:8443/` is a bind wildcard, not an address anyone can visit.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth",
                                 bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["192.168.0.0/24"])
    r = _start_svc(tmp_path, monkeypatch).webserver_start_service()
    assert r.ok and "0.0.0.0" not in r.summary


def test_console_urls_use_the_configured_scheme(monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.178.95")
    assert webserver.console_urls(WebserverConfig()) == ["https://127.0.0.1:8443/"]
    assert webserver.console_urls(
        WebserverConfig(scheme="http", access_mode="no-auth")) == ["http://127.0.0.1:8443/"]
    exposed = WebserverConfig(scheme="http", access_mode="no-auth", bind="0.0.0.0",
                              remote_exposed=True, allowed_cidrs=("10.0.0.0/8",))
    assert webserver.console_urls(exposed) == ["http://192.168.178.95:8443/",
                                               "http://127.0.0.1:8443/"]


def test_monitor_surfaces_the_proxies_and_the_bypass_warning(tmp_path):
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    data = svc.webserver_monitor().data
    assert [p["stack_id"] for p in data["stack_proxies"]] == ["meshtastic"]
    # monitor_view warnings are {"level","text"} dicts (the template renders w.level/w.text).
    warns = [w for w in data["warnings"] if isinstance(w, dict)]
    assert warns == data["warnings"]                          # NO plain strings leak in
    assert any("bypassing this proxy's authentication" in w["text"] for w in warns)
    assert all(w.get("level") for w in warns)


def test_monitor_lists_no_proxies_by_default(tmp_path):
    assert _svc_stackweb_verify(tmp_path).webserver_monitor().data["stack_proxies"] == []


def test_monitor_warnings_are_all_dicts_never_plain_strings(tmp_path):
    # P3: a plain string here renders as an empty flash (the template reads w.level/w.text).
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    for w in svc.webserver_monitor().data["warnings"]:
        assert isinstance(w, dict) and w.get("text") and w.get("level")


def test_monitor_live_refreshes_the_console_listener_scope(tmp_path):
    # The reported bug end-to-end: an exposed console with nginx live on 0.0.0.0:8443 must show the
    # green "active" notice and NEITHER false warning — without a re-verify (the panel reads /proc
    # live). Stale/empty evidence must not win.
    cfgmod.save_webserver_config(Paths(runtime_root=tmp_path), bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["192.168.0.0/24"])
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 9}])
    warns = svc.webserver_monitor().data["warnings"]
    texts = [w["text"] for w in warns]
    assert any(w["level"] == "ok" and "Remote listener active on 0.0.0.0:8443" in w["text"]
               for w in warns)
    assert not any("not active" in t or "unproven" in t or "loopback-only" in t
                   or "no listener is active" in t for t in texts)


def test_monitor_loopback_console_prompts_apply(tmp_path):
    cfgmod.save_webserver_config(Paths(runtime_root=tmp_path), bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["192.168.0.0/24"])
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 9}])
    assert any("loopback-only" in w["text"] and "Apply" in w["text"]
               for w in svc.webserver_monitor().data["warnings"])


def test_monitor_not_exposed_console_says_disabled(tmp_path):
    svc = _svc_stackweb_verify(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 9}])
    warns = svc.webserver_monitor().data["warnings"]
    assert any(w["level"] == "info" and "Remote exposure is disabled" in w["text"] for w in warns)
    assert not any("loopback-only" in w["text"] or "no listener is active" in w["text"]
                   for w in warns)


def _reset_svc(tmp_path, listeners=()):
    # A reachable, reloadable nginx master so reset can actually PROVE cessation.
    import os
    staged = str(Paths(runtime_root=tmp_path).under(*webserver.NGINX_CONF_STAGED))
    live = str(Paths(runtime_root=tmp_path).under(*webserver.NGINX_CONF))
    pid_path = Paths(runtime_root=tmp_path).under(*webserver.NGINX_PID)
    cmds = {("nginx", "-v"): CR(0, "", "1.0"),
            ("nginx", "-t", "-c", staged): CR(0, "", "ok"),
            ("nginx", "-s", "reload", "-c", live): CR(0, "", "")}
    fake = FakeSystem(commands=cmds, listeners=[Listener(**l) for l in listeners])
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "run").mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))       # a live master (os.kill(pid,0) succeeds)
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def test_reset_disables_enabled_stack_proxies(tmp_path):
    svc = _reset_svc(tmp_path)
    svc.stack_web_configure("meshcom", mode="lan", port=8444,
                            cidrs=["192.168.0.0/24"], confirm=True)
    assert svc.config().stackweb["meshcom"].enabled
    res = svc.webserver_reset_defaults()
    assert res.ok and "remote exposure ceased" in res.summary
    assert not svc.config().stackweb["meshcom"].enabled        # port -> 0
    assert any("disabled stack web-UI proxy: meshcom" in d for d in res.details)
    # the mode/CIDR are kept for an easy re-enable
    assert svc.config().stackweb["meshcom"].mode == "lan"
    assert svc.config().stackweb["meshcom"].allowed_cidrs == ("192.168.0.0/24",)


def test_reset_evidence_does_not_claim_cessation_while_a_remote_proxy_would_bind(tmp_path, monkeypatch):
    # If the disable write somehow does NOT take effect, reset must stay honest rather than assert
    # a remote listener is gone. Simulate that by making the disable loop a no-op.
    from lhpc.core import config as _config
    svc = _reset_svc(tmp_path)
    svc.stack_web_configure("meshcom", mode="public", port=8444,
                            cidrs=["0.0.0.0/0"], confirm=True, confirm_public=True)
    orig = _config.save_stackweb_config
    monkeypatch.setattr(_config, "save_stackweb_config",
                        lambda paths, sid, **kw: None if "port" in kw and kw["port"] == 0
                        else orig(paths, sid, **kw))
    res = svc.webserver_reset_defaults()
    assert not res.ok
    assert "STILL bound remotely" in res.summary and "stack web-UI proxy" in res.summary
    # cessation is unproven because a remote STACK proxy remains…
    assert res.data["effective"]["remote_cessation_proven"] is False
    # …and `remote_listener` now truthfully tracks the CONSOLE listener scope specifically (this fake
    # has no console listener on 8443 -> not exposed), separate from the proxy-remaining condition.
    assert res.data["effective"]["remote_listener"] is False
    assert res.data["effective"]["listener_scope"] in ("absent", "loopback")


def test_reset_proves_cessation_when_no_stack_proxy_was_enabled(tmp_path):
    svc = _reset_svc(tmp_path)
    res = svc.webserver_reset_defaults()
    assert res.ok and res.data["effective"]["remote_cessation_proven"] is True


def test_reset_refuses_cessation_while_the_console_listener_stays_exposed(tmp_path):
    # P2: a live console listener on 0.0.0.0:8443 that survives the reload -> cessation is NOT proven,
    # and `remote_listener`/`listener_scope` truthfully say exposed (not a stale, inconsistent block).
    svc = _reset_svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 7}])
    res = svc.webserver_reset_defaults()
    assert not res.ok and "console listener" in res.summary
    eff = res.data["effective"]
    assert eff["remote_cessation_proven"] is False
    assert eff["remote_listener"] is True and eff["listener_scope"] == "exposed"


def test_reset_persists_a_consistent_non_exposed_scope_when_cessation_is_proven(tmp_path, monkeypatch):
    # P2: verify() runs BEFORE the reload and records the pre-reset (exposed) scope; reset must
    # RE-READ after the reload and persist a consistent block. Simulate the 0.0.0.0 -> 127.0.0.1
    # transition: first listener_scope call (verify, pre-reload) sees exposed, the post-reload
    # re-read sees loopback.
    svc = _reset_svc(tmp_path)
    calls = {"n": 0}

    def _scope(system, port):
        calls["n"] += 1
        return "exposed" if calls["n"] == 1 else "loopback"    # pre-reload exposed, then ceased
    monkeypatch.setattr(webserver, "listener_scope", _scope)
    res = svc.webserver_reset_defaults()
    assert res.ok and res.data["effective"]["remote_cessation_proven"] is True
    # the persisted scope reflects the POST-reload state, not verify's stale pre-reload "exposed"
    eff = res.data["effective"]
    assert eff["listener_scope"] == "loopback" and eff["remote_listener"] is False
    on_disk = webserver.read_evidence(svc._paths)["effective"]
    assert on_disk["listener_scope"] == "loopback"


def test_reset_leaves_a_local_only_proxy_alone_is_still_provable(tmp_path):
    # A `local` proxy binds loopback, so it is NOT a remote listener — but reset still disables it,
    # because "reset to defaults" means defaults, and cessation is trivially proven.
    svc = _reset_svc(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    res = svc.webserver_reset_defaults()
    assert res.ok and res.data["effective"]["remote_cessation_proven"] is True
    assert not svc.config().stackweb["meshcom"].enabled


def test_reset_restores_https_from_a_valid_http_console(tmp_path):
    # An http console stores access_mode=no-auth. Reset restores a cert-based access mode, so it
    # MUST restore scheme=https in the SAME save — otherwise the writer rejects http+cert-auth and
    # the reset raises ConfigError before it can disable proxies or prove cessation.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    svc = _reset_svc(tmp_path)
    # http+lan is a cleartext remote listener -> elevated; needs the strong phrase.
    assert svc.stack_web_configure("meshcom", mode="lan", port=8444,
                                   scheme="http", access_mode="no-auth",
                                   cidrs=["192.168.0.0/24"],
                                   confirm=True, confirm_public=True).ok
    assert svc.config().webserver.scheme == "http"             # precondition
    assert svc.config().stackweb["meshcom"].enabled
    res = svc.webserver_reset_defaults()
    assert res.ok, res.summary                                 # no ConfigError, no failure
    cfg = svc.config().webserver
    assert cfg.scheme == "https" and cfg.access_mode == "local-open-remote-auth"
    assert not svc.config().stackweb["meshcom"].enabled        # proxy disabled
    assert res.data["effective"]["remote_cessation_proven"] is True


def test_webserver_panel_is_the_last_sub_section_and_styled_like_the_others(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc_stackweb_verify(tmp_path)
    body = create_app(lambda: svc).test_client().get("/stacks?open=meshcom").get_data(as_text=True)
    i = body.index('id="stackrow-meshcom"')
    row = body[i:body.index('id="stackrow-', i + 1)] if body.find(
        'id="stackrow-', i + 1) != -1 else body[i:]
    # same element/class as Install, Info, Settings — not a nested stackrow
    assert '<details class="advcfg" id="stack-webserver-meshcom">' in row
    assert "stackrow ws-comp" not in row
    # LAST: after Install and after Settings
    assert row.index('id="stack-install-meshcom"') < row.index('id="stack-webserver-meshcom"')
    assert row.index('id="stack-settings-meshcom"') < row.index('id="stack-webserver-meshcom"')


# ===== raw endpoint verdicts, proxy containment, and desired-vs-applied proxy policy =====
#
# Rendered through `dashboard_webservers` / `stack_web_view` / `security_pill`, not through the
# helpers underneath them: every one of the bugs below was a wiring fault that the helper-level
# tests were individually happy with.

def _sc(port, *, proto="tcp", family="dual", addr="*", **kw):
    return {"proto": proto, "family": family, "addr": addr, "port": port, **kw}


def _fw(*, mode="secure-default", eps=(), ing=(), extra=(), **kw):
    st = {"installed": True, "config_ok": True, "live_ok": True, "transitional": False,
          "candidate": {"mode": mode, "endpoints": list(eps), "proxy_ingress": list(ing),
                        "extra_allow": list(extra)}}
    st.update(kw)
    return st


def _meshtastic_svc(tmp_path, listeners, fw=None, monkeypatch=None):
    """meshtasticd RUNNING with the given live sockets, and an optional verified firewall."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    fake = FakeSystem(cmdlines_data={70: ["/opt/meshtasticd", "-c", "meshtasticd.yaml"]},
                      listeners=[Listener(**l) for l in listeners])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    if fw is not None:
        monkeypatch.setattr(type(svc), "firewall_status", lambda self: fw)
    return svc


_MT_4403 = {"family": "ipv4", "ip": "0.0.0.0", "port": 4403, "inode": 1}
_MT_9443 = {"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 2}


def _port_rows(svc):
    return {r["port"]: r for r in svc.dashboard_webservers() if r["kind"] == "port"}


def test_a_raw_https_endpoint_gets_its_own_security_verdict(tmp_path):
    # 9443 is Meshtastic's web GUI: scheme https, NO authentication, no bind knob. Excluding it
    # from the raw model by SCHEME meant the box asserted containment for 4403 while an equally
    # open listener sat next to it. The rule is the endpoint's no-auth firewall metadata, not a
    # hardcoded stack or port.
    rows = _port_rows(_meshtastic_svc(tmp_path, [_MT_4403, _MT_9443]))
    assert set(rows) >= {"4403", "9443"}                       # BOTH are represented
    assert rows["9443"]["scheme"] == "https"
    for p in ("4403", "9443"):
        assert rows[p]["exposure"] == {"level": "bad", "label": "public"}, p


def test_deny_default_greens_both_raw_meshtastic_endpoints(tmp_path, monkeypatch):
    fw = _fw(eps=[_sc(4403, selected=False, deny_default=True, allow_cidrs=[]),
                  _sc(9443, selected=False, deny_default=True, allow_cidrs=[])])
    svc = _meshtastic_svc(tmp_path, [_MT_4403, _MT_9443], fw, monkeypatch)
    rows = _port_rows(svc)
    for p in ("4403", "9443"):
        assert rows[p]["exposure"] == {"level": "ok", "label": "firewalled"}, p
    assert svc.security_pill(svc.dashboard_webservers())["level"] == "ok"


def test_an_open_raw_9443_reds_the_box_even_while_4403_stays_denied(tmp_path, monkeypatch):
    # The aggregate must never stay green because the API half is contained: the web GUI is a
    # separate listener and an unauthenticated one.
    fw = _fw(eps=[_sc(4403, selected=False, deny_default=True, allow_cidrs=[]),
                  _sc(9443, selected=True, allow_cidrs=[])])          # opened by the operator
    svc = _meshtastic_svc(tmp_path, [_MT_4403, _MT_9443], fw, monkeypatch)
    rows = _port_rows(svc)
    assert rows["4403"]["exposure"]["label"] == "firewalled"
    assert rows["9443"]["exposure"] == {"level": "bad", "label": "public"}
    assert svc.security_pill(svc.dashboard_webservers())["level"] == "bad"


def test_a_denied_upstream_is_not_reachable_around_the_proxy(tmp_path, monkeypatch):
    # "Bound off-loopback" alone raised "reachable directly, bypassing this proxy's
    # authentication" for endpoints the managed firewall drops by default — they bypass nothing.
    fw = _fw(eps=[_sc(9443, selected=False, deny_default=True, allow_cidrs=[])])
    svc = _meshtastic_svc(tmp_path, [_MT_9443], fw, monkeypatch)
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    assert svc.stack_web_view("meshtastic")["bypassable"] is False
    warnings = svc.webserver_monitor().data.get("warnings", [])
    assert not any("bypassing" in w["text"] for w in warnings)
    assert svc.webserver_verify().data["checks"].get("upstream_bypass_stacks", []) == []


@pytest.mark.parametrize("verdict,eps,bypassable", [
    ("restricted", [_sc(9443, selected=True, allow_cidrs=["192.168.0.0/24"])], True),
    ("open", [_sc(9443, selected=True, allow_cidrs=[])], True),
    ("unknown", [], True),
])
def test_anything_short_of_denied_still_bypasses_the_proxy(tmp_path, monkeypatch, verdict,
                                                           eps, bypassable):
    svc = _meshtastic_svc(tmp_path, [_MT_9443], _fw(eps=eps), monkeypatch)
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    assert svc.stack_web_view("meshtastic")["bypassable"] is bypassable, verdict


def test_a_loopback_or_absent_upstream_bypasses_nothing(tmp_path):
    loopback = _meshtastic_svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1",
                                           "port": 9443, "inode": 1}])
    loopback.stack_web_configure("meshtastic", mode="local", port=8445)
    assert loopback.stack_web_view("meshtastic")["bypassable"] is False


def _proxy_applied(tmp_path, monkeypatch=None, fw=None, **cfg):
    """A meshtastic proxy whose policy has really been APPLIED, with its listener live on 8445."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    import os
    from lhpc.core import runtime_fs
    paths = Paths(runtime_root=tmp_path)
    boot = ControllerService(system=FakeSystem().system, paths=paths)
    boot.webserver_init(dns_sans=["pi.local"])
    boot.stack_web_configure("meshtastic", **cfg)
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    staged = str(paths.under(*webserver.NGINX_CONF_STAGED))
    live = str(paths.under(*webserver.NGINX_CONF))
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", "nginx/1.24"),
                                ("nginx", "-t", "-c", staged): CR(0, "", "ok"),
                                ("nginx", "-s", "reload", "-c", live): CR(0, "", "")},
                      cmdlines_data={70: ["/opt/meshtasticd", "-c", "meshtasticd.yaml"]},
                      listeners=[Listener("ipv4", "127.0.0.1", 8443, 1),   # loopback console
                                 Listener("ipv4", "0.0.0.0", 8445, 3),     # the proxy itself
                                 Listener("ipv4", "127.0.0.1", 9443, 2)])  # upstream, loopback
    svc = ControllerService(system=fake.system, paths=paths)
    if fw is not None:
        monkeypatch.setattr(type(svc), "firewall_status", lambda self: fw)
    assert svc.webserver_apply().ok
    return svc


def test_a_saved_proxy_narrowing_cannot_improve_the_live_proxy(tmp_path):
    # Same law as the console: mode/scheme/auth/CIDRs only reach nginx at Apply, so the live
    # listener keeps being coloured by the policy that was actually activated.
    svc = _proxy_applied(tmp_path, mode="lan", port=8445, scheme="https", access_mode="no-auth",
                         cidrs=["192.168.0.0/24"], confirm=True, confirm_public=True)
    applied = webserver.applied_proxy(webserver.read_applied(svc._paths), "meshtastic")
    assert applied["access_mode"] == "no-auth" and applied["port"] == 8445
    assert svc.stack_web_view("meshtastic")["posture"]["sec_level"] == "bad"
    for field in ({"access_mode": "auth-everywhere"}, {"mode": "local"},
                  {"cidrs": ["192.168.0.5/32"]}):
        svc.stack_web_configure("meshtastic", **field)
        assert svc.stack_web_view("meshtastic")["posture"]["sec_level"] == "bad", field
    svc.webserver_verify()                                    # verify never advances it
    assert webserver.applied_proxy(webserver.read_applied(svc._paths),
                                   "meshtastic")["access_mode"] == "no-auth"


def test_a_verified_source_restriction_makes_a_live_no_auth_proxy_yellow(tmp_path, monkeypatch):
    fw = _fw(ing=[_sc(8445, allow_cidrs=["192.168.0.0/24"])])
    svc = _proxy_applied(tmp_path, monkeypatch, fw, mode="lan", port=8445, scheme="https",
                         access_mode="no-auth", cidrs=["192.168.0.0/24"], confirm=True,
                         confirm_public=True)
    p = svc.stack_web_view("meshtastic")["posture"]
    assert p["sec_level"] == "warn" and p["warn_reason"] == "restricted_noauth"


@pytest.mark.parametrize("name,fw", [
    ("compatibility CIDRs are not restrictions",
     _fw(mode="compatibility", ing=[_sc(8445, allow_cidrs=["192.168.0.0/24"])])),
    ("a transitional ruleset proves nothing",
     _fw(ing=[_sc(8445, allow_cidrs=["192.168.0.0/24"])], transitional=True)),
    ("an unverified ruleset proves nothing",
     _fw(ing=[_sc(8445, allow_cidrs=["192.168.0.0/24"])], live_ok=False)),
    ("an unrestricted allow is not a restriction", _fw(ing=[_sc(8445, allow_cidrs=[])])),
])
def test_unproven_containment_never_softens_a_live_no_auth_proxy(tmp_path, monkeypatch, name, fw):
    svc = _proxy_applied(tmp_path, monkeypatch, fw, mode="lan", port=8445, scheme="https",
                         access_mode="no-auth", cidrs=["192.168.0.0/24"], confirm=True,
                         confirm_public=True)
    assert svc.stack_web_view("meshtastic")["posture"]["sec_level"] == "bad", name


def test_remote_cleartext_http_proxy_stays_red_under_a_verified_restriction(tmp_path, monkeypatch):
    fw = _fw(ing=[_sc(8445, allow_cidrs=["192.168.0.0/24"])])
    svc = _proxy_applied(tmp_path, monkeypatch, fw, mode="lan", port=8445, scheme="http",
                         access_mode="no-auth", cidrs=["192.168.0.0/24"], confirm=True,
                         confirm_public=True)
    p = svc.stack_web_view("meshtastic")["posture"]
    assert p["scheme_level"] == "bad" and p["sec_level"] == "bad"


def test_the_intended_zero_state_is_constructible(tmp_path, monkeypatch):
    # The whole batch, in one rendered dashboard, exactly as the Zero 2W is meant to read:
    # secure-default firewall, an unauthenticated https console and Meshtastic proxy restricted to
    # the AP's /24, both raw Meshtastic listeners dropped, and KISS bound wide but restricted.
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core import runtime_fs
    LAN = ["10.42.0.0/24"]
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    boot = ControllerService(system=FakeSystem().system, paths=paths)
    boot.webserver_init(dns_sans=["pi.local"])
    cfgmod.save_webserver_config(paths, bind="0.0.0.0", port=8443, remote_exposed=True,
                                 access_mode="no-auth", allowed_cidrs=LAN, scheme="https")
    boot._invalidate_config()
    boot.stack_web_configure("meshtastic", mode="lan", port=8445, scheme="https",
                             access_mode="no-auth", cidrs=LAN, confirm=True, confirm_public=True)
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    staged, live = (str(paths.under(*webserver.NGINX_CONF_STAGED)),
                    str(paths.under(*webserver.NGINX_CONF)))
    fake = FakeSystem(
        commands={("nginx", "-v"): CR(0, "", "nginx/1.24"),
                  ("nginx", "-t", "-c", staged): CR(0, "", "ok"),
                  ("nginx", "-s", "reload", "-c", live): CR(0, "", "")},
        cmdlines_data={70: ["/opt/meshtasticd", "-c", "meshtasticd.yaml"],
                       42: ["./loraham-kiss-tnc", "--kiss-port", "8001", "--kiss-host", "0.0.0.0"]},
        listeners=[Listener("ipv4", "0.0.0.0", 8443, 1), Listener("ipv4", "0.0.0.0", 8445, 2),
                   Listener("ipv4", "0.0.0.0", 4403, 3), Listener("ipv4", "0.0.0.0", 9443, 4),
                   Listener("ipv4", "0.0.0.0", 8001, 5)])
    svc = ControllerService(system=fake.system, paths=paths)
    fw = _fw(eps=[_sc(4403, selected=False, deny_default=True, allow_cidrs=[]),
                  _sc(9443, selected=False, deny_default=True, allow_cidrs=[]),
                  _sc(8001, selected=True, allow_cidrs=LAN)],
             ing=[_sc(8443, allow_cidrs=LAN), _sc(8445, allow_cidrs=LAN)])
    monkeypatch.setattr(type(svc), "firewall_status", lambda self: fw)
    assert svc.webserver_apply().ok                        # activation recorded

    rows = svc.dashboard_webservers()
    by_port = {r["port"]: r for r in rows if r["kind"] == "port"}
    console = next(r for r in rows if r["kind"] == "console")
    proxy = next(r for r in rows if r["kind"] == "stack" and r["sid"] == "meshtastic")
    assert console["posture"]["sec_level"] == "warn"                    # https no-auth, LAN-bound
    assert console["posture"]["warn_reason"] == "restricted_noauth"
    assert proxy["posture"]["sec_level"] == "warn"
    assert by_port["4403"]["exposure"] == {"level": "ok", "label": "firewalled"}
    assert by_port["9443"]["exposure"] == {"level": "ok", "label": "firewalled"}
    assert by_port["8001"]["exposure"] == {"level": "warn", "label": "LAN"}   # never "local"
    assert by_port["8001"]["warn_reason"] == "restricted_noauth"
    pill = svc.security_pill(rows)
    assert pill["level"] == "warn" and pill["label"] == "lan-exposed"
    assert not any("bypassing" in w["text"]
                   for w in svc.webserver_monitor().data.get("warnings", []))


def test_a_saved_proxy_port_move_keeps_advertising_the_port_that_still_answers(tmp_path):
    # `stack_web_view` already finds the OLD applied port after a saved-but-unapplied port change,
    # but that truth stopped there: the panel read "in sync" and the dashboard advertised the NEW
    # port, which nothing is listening on. The operator was handed a dead address while a working
    # one was live one line away.
    import os
    from lhpc.core import runtime_fs
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    boot = ControllerService(system=FakeSystem().system, paths=paths)
    boot.webserver_init(dns_sans=["pi.local"])
    boot.stack_web_configure("meshtastic", mode="lan", port=8445, scheme="https",
                             access_mode="no-auth", cidrs=["192.168.0.0/24"],
                             confirm=True, confirm_public=True)
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    staged, live = (str(paths.under(*webserver.NGINX_CONF_STAGED)),
                    str(paths.under(*webserver.NGINX_CONF)))
    fake = FakeSystem(
        commands={("nginx", "-v"): CR(0, "", "nginx/1.24"),
                  ("nginx", "-t", "-c", staged): CR(0, "", "ok"),
                  ("nginx", "-s", "reload", "-c", live): CR(0, "", "")},
        cmdlines_data={70: ["/opt/meshtasticd", "-c", "meshtasticd.yaml"]},
        listeners=[Listener("ipv4", "127.0.0.1", 8443, 1),    # loopback console
                   Listener("ipv4", "0.0.0.0", 8445, 2),      # the APPLIED proxy port
                   Listener("ipv4", "127.0.0.1", 9443, 3)])   # upstream, loopback
    svc = ControllerService(system=fake.system, paths=paths)
    assert svc.webserver_apply().ok                            # 8445 is now the applied policy

    # Save the SAME policy on a new port — no Apply. nginx keeps the 8445 socket it holds.
    assert svc.stack_web_configure("meshtastic", port=8555, confirm=True,
                                   confirm_public=True).ok

    v = svc.stack_web_view("meshtastic")
    assert v["cfg"].port == 8555                               # desired is untouched, for Settings
    assert v["live_port"] == 8445 and v["listen_scope"] == "exposed"
    assert v["pending"] is True                                # an Apply really is outstanding

    row = next(r for r in svc.dashboard_webservers()
               if r["kind"] == "stack" and r["sid"] == "meshtastic")
    assert row["port"] == 8445

    app = create_app(lambda: svc)
    client = app.test_client()
    body = client.get("/", headers={"Host": "127.0.0.1"}).get_data(as_text=True)
    assert "127.0.0.1:8445" in body                             # the address that answers
    assert ":8555" not in body                                  # ...and not the one that does not

    # The Settings panel branches on the same live scope, so it must name the same port. It read
    # "Currently listening on port 8555 on all interfaces" and offered an Open link to 8555 —
    # describing, and linking to, a socket nothing is bound to.
    panel = client.get("/stacks?open=meshtastic",
                       headers={"Host": "127.0.0.1"}).get_data(as_text=True)
    assert "Currently listening on port 8445 on all interfaces" in panel
    assert "Currently listening on port 8555" not in panel
    assert "127.0.0.1:8445" in panel                             # the Open link and the pill
    assert ":8555" not in panel.replace('value="8555"', "")      # ...except the desired-port FIELD

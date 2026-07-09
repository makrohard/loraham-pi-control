"""M7: nginx config generation (three access modes, CIDR gate, header strip, fail-safe
listen) + exposure policy + `nginx -t` validation. All inputs are already-validated typed
WebserverConfig values, so these assert the generated POLICY, not input sanitization
(that is enforced by validators/config and covered in test_webserver_config.py)."""

from __future__ import annotations

from pathlib import Path

from lhpc.core import webserver
from lhpc.core.config import WebserverConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem


def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


def _render(tmp_path, **kw):
    cfg = WebserverConfig(**kw)
    return webserver.render_nginx_config(_paths(tmp_path), cfg)


# --- access modes ------------------------------------------------------------

def test_mode_local_open_remote_auth(tmp_path):
    conf = _render(tmp_path, access_mode="local-open-remote-auth")
    assert "ssl_verify_client optional;" in conf
    assert 'map "$lhpc_peer:$ssl_client_verify" $lhpc_need_auth' in conf
    assert '"~^loopback:" 0;' in conf and '"~^remote:SUCCESS$" 0;' in conf
    assert "ssl_client_certificate" in conf and "ssl_crl" in conf   # mTLS material present
    assert "if ($lhpc_need_auth) { return 403; }" in conf


def test_mode_auth_everywhere_is_mandatory(tmp_path):
    conf = _render(tmp_path, access_mode="auth-everywhere")
    assert "ssl_verify_client on;" in conf                          # handshake-mandatory
    assert 'map "$ssl_client_verify" $lhpc_need_auth' in conf


def test_mode_no_auth_has_no_mtls_material(tmp_path):
    conf = _render(tmp_path, access_mode="no-auth")
    assert "ssl_verify_client off;" in conf
    assert "ssl_client_certificate" not in conf and "ssl_crl" not in conf
    assert "$lhpc_need_auth {\n        default 0;" in conf          # never rejects on cert


# --- fail-safe listen + CIDR gate -------------------------------------------

def test_not_exposed_forces_loopback_listen_even_with_stale_bind(tmp_path):
    conf = _render(tmp_path, bind="0.0.0.0", remote_exposed=False, port=8443)
    assert "listen 127.0.0.1:8443 ssl;" in conf
    assert "listen 0.0.0.0" not in conf                             # no remote listener


def test_exposed_binds_wildcard_and_gates_cidrs(tmp_path):
    conf = _render(tmp_path, bind="0.0.0.0", remote_exposed=True, port=8443,
                   allowed_cidrs=("192.168.0.0/24",), access_mode="local-open-remote-auth")
    assert "listen 0.0.0.0:8443 ssl;" in conf
    assert "allow 127.0.0.1;" in conf and "allow 192.168.0.0/24;" in conf
    assert "deny all;" in conf


def test_headers_are_stripped_and_evidence_set(tmp_path):
    conf = _render(tmp_path)
    assert 'proxy_set_header X-Forwarded-For "";' in conf
    assert 'proxy_set_header Forwarded "";' in conf
    assert "proxy_set_header X-LHPC-Peer $lhpc_peer;" in conf
    assert "proxy_set_header X-LHPC-Client-Verify $ssl_client_verify;" in conf
    assert "geo $lhpc_peer {" in conf and "127.0.0.0/8 loopback;" in conf
    assert "server unix:" in conf and "lhpc-web.sock" in conf       # backend is the unix socket


# --- exposure policy ---------------------------------------------------------

def test_plan_exposure_defaults_local():
    p = webserver.plan_exposure(WebserverConfig())
    assert p["remote"] is False and p["problems"] == []


def test_plan_exposure_requires_cidr():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True))
    assert p["remote"] is True and any("at least one allowed source CIDR" in x for x in p["problems"])


def test_plan_exposure_public_route_is_elevated():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                                                allowed_cidrs=("0.0.0.0/0",)))
    assert p["public"] is True and p["danger"] == "elevated"


def test_plan_exposure_no_auth_remote_is_elevated():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                                                allowed_cidrs=("192.168.0.0/24",),
                                                access_mode="no-auth"))
    assert p["danger"] == "elevated" and p["no_auth"] is True


def test_plan_exposure_flags_contradictory_remote_bind_when_not_exposed():
    p = webserver.plan_exposure(WebserverConfig(bind="0.0.0.0", remote_exposed=False))
    assert p["remote"] is False and any("non-loopback" in x for x in p["problems"])


# --- validation --------------------------------------------------------------

def test_validate_config_ok(tmp_path):
    fake = FakeSystem(commands={
        ("nginx", "-t", "-c", "/x/lhpc.conf"): CommandResult(0, "", "nginx: configuration test is successful"),
    })
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert ok and "successful" in msg
    assert ["nginx", "-t", "-c", "/x/lhpc.conf"] in fake.calls


def test_validate_config_failure(tmp_path):
    fake = FakeSystem(commands={
        ("nginx", "-t", "-c", "/x/lhpc.conf"): CommandResult(1, "", "nginx: [emerg] bad thing"),
    })
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert not ok and "bad thing" in msg


def test_validate_config_surfaces_emerg_cause_not_generic_tail(tmp_path):
    # Real rootless failure: the '[emerg] mkdir…' CAUSE precedes a generic 'test failed' tail. The
    # message must carry the cause, not the useless last line (the bug behind the opaque error).
    stderr = ('nginx: [emerg] mkdir() "/r/state/run/nginx/body" failed (2: No such file or directory)\n'
              "nginx: configuration file /r/config/nginx/lhpc.conf.staged test failed")
    fake = FakeSystem(commands={("nginx", "-t", "-c", "/x/lhpc.conf"): CommandResult(1, "", stderr)})
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert not ok
    assert "[emerg]" in msg and "mkdir()" in msg          # the actual cause
    assert "test failed" not in msg                       # not the generic tail


def test_validate_config_nginx_absent(tmp_path):
    fake = FakeSystem()      # unknown command -> not_found default
    ok, msg = webserver.validate_config(fake.system, _paths(tmp_path), "/x/lhpc.conf")
    assert not ok and "not installed" in msg and "sudo apt install -y nginx" in msg


def test_stage_and_validate_creates_rootless_runtime_dirs(tmp_path):
    # The temp-path parent state/run/nginx (and logs/) must exist BEFORE nginx -t, else rootless
    # nginx's single-level mkdir of body/proxy/… fails. stage_and_validate ensures them.
    paths = _paths(tmp_path)
    staged = paths.under(*webserver.NGINX_CONF_STAGED)
    fake = FakeSystem(commands={
        ("nginx", "-t", "-c", str(staged)): CommandResult(0, "", "configuration test is successful"),
    })
    ok, msg, out = webserver.stage_and_validate(fake.system, paths, WebserverConfig())
    assert ok and out == staged
    assert (tmp_path / "state" / "run" / "nginx").is_dir()   # temp-path parent nginx needs
    assert (tmp_path / "logs").is_dir()                       # error/access log parent
    assert staged.exists()                                    # config was staged for the -t


def test_nginx_serves_static_updating_page_on_502(tmp_path):
    # On a 502/503/504 (e.g. the Waitress upstream gone mid self-update) nginx serves a branded
    # static page from disk (no upstream, no JS) instead of the raw "502 Bad Gateway".
    paths = _paths(tmp_path)
    conf = webserver.render_nginx_config(paths, WebserverConfig())
    assert "error_page 502 503 504 /_lhpc_updating.html;" in conf
    assert "location = /_lhpc_updating.html" in conf and "internal;" in conf and "alias " in conf
    # stage_and_validate must WRITE the actual static file at the served path.
    staged = paths.under(*webserver.NGINX_CONF_STAGED)
    fake = FakeSystem(commands={("nginx", "-t", "-c", str(staged)): CommandResult(0, "", "ok")})
    webserver.stage_and_validate(fake.system, paths, WebserverConfig())
    page = tmp_path / "config" / "nginx" / "_lhpc_updating.html"
    assert page.is_file()
    html = page.read_text()
    assert "Return to the console" in html and "<script" not in html

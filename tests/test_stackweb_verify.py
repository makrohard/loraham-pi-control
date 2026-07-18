"""`verify` / `start-service` must describe the config nginx will actually load.

Two truthfulness bugs this pins:
  * verify validated a CONSOLE-ONLY config and reported "verified", although `apply` promotes a
    config that also contains the stack proxy blocks;
  * verify and start-service demanded HTTPS PKI unconditionally, so a fully-`http` desired config
    (which needs no certificate at all) could never be verified or started.
"""

from __future__ import annotations

import pytest

from lhpc.core import config as cfgmod
from lhpc.core import webserver
from lhpc.core.config import StackWebConfig, WebserverConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def _fake(tmp_path, listeners=(), nginx_ok=True):
    staged = str(Paths(runtime_root=tmp_path).under(*webserver.NGINX_CONF_STAGED))
    cmds = {("nginx", "-v"): CR(0, "", "nginx version: 1.0")}
    cmds[("nginx", "-t", "-c", staged)] = (CR(0, "", "ok") if nginx_ok
                                           else CR(1, "", "nginx: [emerg] bad"))
    return FakeSystem(commands=cmds, listeners=[Listener(**l) for l in listeners])


def _svc(tmp_path, listeners=(), nginx_ok=True):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=_fake(tmp_path, listeners, nginx_ok).system,
                             paths=Paths(runtime_root=tmp_path))


def _staged_text(tmp_path):
    return (tmp_path / "config" / "nginx" / "lhpc.conf.staged").read_text()


def _proxy(sid, upstream="127.0.0.1:18083", uscheme="http", **kw):
    return webserver.StackWebProxy(StackWebConfig(stack_id=sid, **kw), upstream, uscheme)


# --- verify validates the config APPLY would promote -----------------------------------------------

def test_verify_validates_the_stack_proxy_blocks_not_a_console_only_config(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("meshcom", mode="local", port=8444).ok
    res = svc.webserver_verify()
    # The staged config `verify` ran `nginx -t` against must contain the proxy block.
    text = _staged_text(tmp_path)
    assert "upstream lhpc_ui_meshcom" in text and "listen 127.0.0.1:8444" in text
    assert res.data["checks"]["nginx_config_valid"] == "ok"


def test_verify_surfaces_the_stack_proxies_as_evidence(tmp_path):
    svc = _svc(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    ev = svc.webserver_verify().data
    assert [p["stack_id"] for p in ev["stack_proxies"]] == ["meshcom"]
    assert ev["stack_proxies"][0]["upstream"] == "127.0.0.1:18083"
    assert ev["desired_snapshot"]["scheme"] == "https"


def test_verify_reports_a_stack_config_problem_as_a_config_failure(tmp_path):
    # A proxy whose policy is invalid makes the DESIRED config invalid; verify must say so.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    # Hand-write a remote proxy with no CIDR (the writer would refuse; the parser keeps it).
    (tmp_path / "config" / "local.toml").write_text(
        '[stackweb]\nmeshcom_port = 8444\nmeshcom_mode = "lan"\n')
    svc = _svc(tmp_path)
    res = svc.webserver_verify()
    assert not res.ok
    assert any("meshcom: remote exposure requires at least one allowed source CIDR" in x
               for x in res.data["checks"]["config_problems"])


def test_verify_warns_about_a_bypassable_upstream_without_failing(tmp_path):
    # LHPC cannot close meshtasticd's port, so this is a standing WARNING, never a config failure.
    # (An https console with no PKI on disk fails server_cert here — that is a DIFFERENT, real
    # failure; what must not happen is the bypass itself entering the failed set.)
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
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
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445,
                            scheme="http", access_mode="no-auth")
    res = svc.webserver_verify()
    assert res.ok and res.data["checks"]["upstream_bypass"] == "warn"


def test_verify_of_a_loopback_upstream_raises_no_warning(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 18083, "inode": 1}])
    svc.stack_web_configure("meshcom", mode="local", port=8444)
    ev = svc.webserver_verify().data
    assert "upstream_bypass" not in ev["checks"]
    assert ev["stack_proxies"][0]["bypassable"] is False


# --- PKI is required only by what the config actually loads ------------------------------------------

def test_tls_required_follows_every_public_listener():
    https_console = WebserverConfig()
    http_console = WebserverConfig(scheme="http", access_mode="no-auth")
    assert webserver.tls_required(https_console, ()) is True
    assert webserver.tls_required(http_console, ()) is False
    # an http console with an https proxy still needs a server certificate
    assert webserver.tls_required(
        http_console, [_proxy("meshcom", mode="local", port=8444, scheme="https")]) is True
    # a disabled proxy contributes nothing
    assert webserver.tls_required(
        http_console, [_proxy("meshcom", port=0, scheme="https")]) is False
    assert webserver.tls_required(
        http_console, [_proxy("meshcom", mode="local", port=8444, scheme="http",
                              access_mode="no-auth")]) is False


def test_client_auth_required_is_not_decided_by_the_console_alone():
    # A no-auth console with a cert-auth stack proxy still makes nginx load the client CA + CRL.
    no_auth_console = WebserverConfig(access_mode="no-auth")
    assert webserver.client_auth_required(no_auth_console, ()) is False
    assert webserver.client_auth_required(
        no_auth_console,
        [_proxy("meshcom", mode="local", port=8444, access_mode="auth-everywhere")]) is True
    # and an http proxy can never verify a client cert, so it never demands one
    assert webserver.client_auth_required(
        no_auth_console,
        [_proxy("meshcom", mode="local", port=8444, scheme="http",
                access_mode="no-auth")]) is False


def test_all_http_config_verifies_without_any_pki(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    svc = _svc(tmp_path)
    res = svc.webserver_verify()
    checks = res.data["checks"]
    assert checks["tls_required"] == "no"
    assert "server_cert" not in checks and "server_ca" not in checks
    assert "client_ca" not in checks and "crl" not in checks
    assert res.ok                                              # no certificate, and nothing failed


def test_https_config_still_demands_the_server_certificate(tmp_path):
    svc = _svc(tmp_path)                                       # default https console, no PKI on disk
    checks = svc.webserver_verify().data["checks"]
    assert checks["tls_required"] == "yes"
    assert checks["server_cert"] == "failed" and checks["server_ca"] == "failed"


def test_http_console_with_an_https_proxy_still_demands_the_certificate(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, scheme="http", access_mode="no-auth")
    svc = _svc(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444, scheme="https")
    checks = svc.webserver_verify().data["checks"]
    assert checks["tls_required"] == "yes" and checks["server_cert"] == "failed"


def test_no_auth_console_with_a_cert_auth_proxy_checks_the_client_ca(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfgmod.save_webserver_config(p, access_mode="no-auth")
    svc = _svc(tmp_path)
    svc.stack_web_configure("meshcom", mode="local", port=8444, access_mode="auth-everywhere")
    checks = svc.webserver_verify().data["checks"]
    assert checks["client_ca"] == "failed" and checks["crl"] == "failed"


# --- start-service ------------------------------------------------------------------------------------

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


# --- console_urls follows the scheme -------------------------------------------------------------------

def test_console_urls_use_the_configured_scheme(monkeypatch):
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.178.95")
    assert webserver.console_urls(WebserverConfig()) == ["https://127.0.0.1:8443/"]
    assert webserver.console_urls(
        WebserverConfig(scheme="http", access_mode="no-auth")) == ["http://127.0.0.1:8443/"]
    exposed = WebserverConfig(scheme="http", access_mode="no-auth", bind="0.0.0.0",
                              remote_exposed=True, allowed_cidrs=("10.0.0.0/8",))
    assert webserver.console_urls(exposed) == ["http://192.168.178.95:8443/",
                                               "http://127.0.0.1:8443/"]


# --- monitor ------------------------------------------------------------------------------------------

def test_monitor_surfaces_the_proxies_and_the_bypass_warning(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    data = svc.webserver_monitor().data
    assert [p["stack_id"] for p in data["stack_proxies"]] == ["meshtastic"]
    # monitor_view warnings are {"level","text"} dicts (the template renders w.level/w.text).
    warns = [w for w in data["warnings"] if isinstance(w, dict)]
    assert warns == data["warnings"]                          # NO plain strings leak in
    assert any("bypassing this proxy's authentication" in w["text"] for w in warns)
    assert all(w.get("level") for w in warns)


def test_monitor_lists_no_proxies_by_default(tmp_path):
    assert _svc(tmp_path).webserver_monitor().data["stack_proxies"] == []


def test_monitor_warnings_are_all_dicts_never_plain_strings(tmp_path):
    # P3: a plain string here renders as an empty flash (the template reads w.level/w.text).
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 9443, "inode": 1}])
    svc.stack_web_configure("meshtastic", mode="local", port=8445)
    for w in svc.webserver_monitor().data["warnings"]:
        assert isinstance(w, dict) and w.get("text") and w.get("level")


def test_monitor_live_refreshes_the_console_listener_scope(tmp_path):
    # The reported bug end-to-end: an exposed console with nginx live on 0.0.0.0:8443 must show the
    # green "active" notice and NEITHER false warning — without a re-verify (the panel reads /proc
    # live). Stale/empty evidence must not win.
    cfgmod.save_webserver_config(Paths(runtime_root=tmp_path), bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["192.168.0.0/24"])
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 9}])
    warns = svc.webserver_monitor().data["warnings"]
    texts = [w["text"] for w in warns]
    assert any(w["level"] == "ok" and "Remote listener active on 0.0.0.0:8443" in w["text"]
               for w in warns)
    assert not any("not active" in t or "unproven" in t or "loopback-only" in t
                   or "no listener is active" in t for t in texts)


def test_monitor_loopback_console_prompts_apply(tmp_path):
    cfgmod.save_webserver_config(Paths(runtime_root=tmp_path), bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["192.168.0.0/24"])
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 9}])
    assert any("loopback-only" in w["text"] and "Apply" in w["text"]
               for w in svc.webserver_monitor().data["warnings"])


def test_monitor_not_exposed_console_says_disabled(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 9}])
    warns = svc.webserver_monitor().data["warnings"]
    assert any(w["level"] == "info" and "Remote exposure is disabled" in w["text"] for w in warns)
    assert not any("loopback-only" in w["text"] or "no listener is active" in w["text"]
                   for w in warns)


# --- reset-to-defaults must take stack proxies down too -----------------------------------------------

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


# --- GUI placement ------------------------------------------------------------------------------------

def test_webserver_panel_is_the_last_sub_section_and_styled_like_the_others(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
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

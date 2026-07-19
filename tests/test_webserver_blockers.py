"""Correction blockers 5-7: reset-to-default cessation proof, typed dangerous-op
confirmations, and productive-only trusted-host enforcement."""

from __future__ import annotations

import os

from lhpc.adapters.web.app import create_app
from lhpc.core import pki, runtime_fs, webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, fake=None):
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def _app(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(lambda: svc), svc


def _csrf(c):
    with c.session_transaction() as s:
        s["_csrf"] = "tok"
    return "tok"


def _conf(paths):
    return str(paths.under(*webserver.NGINX_CONF))            # live (reload target)


def _staged(paths):
    return str(paths.under(*webserver.NGINX_CONF_STAGED))     # nginx -t validates the staged file


# --- blocker 5: reset proves cessation --------------------------------------

def test_reset_unproven_without_master_preserves_pki(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    svc0.webserver_cert_issue("laptop", "pw")
    svc0.webserver_expose(["192.168.0.0/24"], confirm=True)      # desired: exposed
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged(svc0._paths)): CR(0, "", "ok")})
    svc = ControllerService(system=fake.system, paths=svc0._paths)   # no nginx master (no pidfile)
    r = svc.webserver_reset_defaults()
    assert not r.ok and "UNPROVEN" in r.summary                  # config valid but no master
    cfg = svc.config().webserver
    assert cfg.remote_exposed is False and cfg.bind == "127.0.0.1" and cfg.allowed_cidrs == ()
    # PKI + client inventory preserved by reset
    assert pki.pki_status(svc._paths)["server_ca"]["present"]
    assert any(c["label"] == "laptop" for c in pki.list_client_certs(svc._paths))


def test_reset_proven_with_running_master(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    conf = _conf(paths)
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged(paths)): CR(0, "", "ok"),
                                ("nginx", "-s", "reload", "-c", conf): CR(0, "", "")})
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_reset_defaults()
    assert r.ok and "ceased" in r.summary
    assert svc.webserver_monitor().data["effective"].get("remote_cessation_proven") is True


# --- blocker 6: typed confirmations -----------------------------------------

def test_init_recreate_requires_confirmation(tmp_path):
    svc = _svc(tmp_path)
    assert svc.webserver_init(dns_sans=["pi.local"]).ok          # fresh PKI: no confirm needed
    assert not svc.webserver_init().ok                           # exists -> destructive -> refuse
    assert svc.webserver_init(confirm=True).ok                   # explicit confirm recreates


def test_gui_expose_uses_typed_phrase(tmp_path):
    # The unified Apply (/webserver/configure) enforces the same typed-phrase ladder the dedicated
    # expose form used to: wrong phrase refuses, 'enable-remote' clears a private range, and a public
    # range (0.0.0.0/0) additionally demands the elevated 'enable-remote-danger'.
    app, svc = _app(tmp_path)
    c = app.test_client(); tok = _csrf(c)
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "192.168.0.0/24",
                                         "confirm_phrase": "nope"})
    assert svc.config().webserver.remote_exposed is False        # wrong phrase -> not exposed
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "192.168.0.0/24",
                                         "confirm_phrase": "enable-remote"})
    assert svc.config().webserver.remote_exposed is True
    # a public range needs the elevated phrase
    svc.webserver_disable_remote()
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "0.0.0.0/0",
                                         "confirm_phrase": "enable-remote"})
    assert svc.config().webserver.remote_exposed is False        # normal phrase insufficient
    c.post("/webserver/configure", data={"_csrf": tok, "bind": "0.0.0.0", "cidrs": "0.0.0.0/0",
                                         "confirm_phrase": "enable-remote-danger"})
    assert svc.config().webserver.remote_exposed is True


def test_gui_revoke_requires_typed_label(tmp_path):
    app, svc = _app(tmp_path)
    c = app.test_client(); tok = _csrf(c)
    svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    c.post("/webserver/cert", data={"_csrf": tok, "op": "revoke", "label": "laptop",
                                    "confirm_phrase": "wrong"})
    assert all(x["state"] == "active" for x in pki.list_client_certs(svc._paths)
               if x["label"] == "laptop")                        # not revoked
    c.post("/webserver/cert", data={"_csrf": tok, "op": "revoke", "label": "laptop",
                                    "confirm_phrase": "laptop"})
    assert any(x["state"] == "revoked" for x in pki.list_client_certs(svc._paths)
               if x["label"] == "laptop")


# --- blocker 7: trusted-host (productive only) ------------------------------

def test_trusted_host_enforced_in_all_modes(tmp_path):
    # Item 1: the trusted-host policy is enforced in EVERY serving mode — including the interactive
    # loopback console (Secure cookies off), not only productive/HTTPS.
    app, _ = _app(tmp_path)
    c = app.test_client()
    # interactive (Secure cookies off) -> an unknown/rebinding Host is STILL rejected; loopback allowed
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200
    # productive HTTPS -> identical policy
    app.config["SESSION_COOKIE_SECURE"] = True
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200


# --- trusted-host: remote exposure, IP literals, IPv6, diagnostics ------------

def _productive(tmp_path, **ws):
    """A productive-mode client whose [webserver] config is `ws`."""
    from lhpc.core import config as _config
    if ws:
        _config.save_webserver_config(Paths(runtime_root=tmp_path), **ws)
    app, svc = _app(tmp_path)
    svc._invalidate_config()
    app.config["SESSION_COOKIE_SECURE"] = True
    return app.test_client()


def test_exposed_console_accepts_its_lan_ip_as_host(tmp_path):
    # THE REPORTED BUG: bind=0.0.0.0 + remote_exposed, empty ip_sans -> every remote request 400'd,
    # because `bind` ("0.0.0.0") is the only IP in the allowlist and no browser ever sends it.
    c = _productive(tmp_path, bind="0.0.0.0", remote_exposed=True, allowed_cidrs=["0.0.0.0/0"])
    assert c.get("/stacks", headers={"Host": "192.168.178.66:8443"}).status_code == 200


def test_exposed_console_still_rejects_a_name_so_rebinding_stays_blocked(tmp_path):
    # The whole relaxation rests on this: DNS rebinding needs a NAME. Only IP literals are relaxed.
    c = _productive(tmp_path, bind="0.0.0.0", remote_exposed=True, allowed_cidrs=["0.0.0.0/0"])
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "pi.local"}).status_code == 400   # name, not in dns_sans


def test_loopback_only_console_rejects_a_lan_ip_host(tmp_path):
    c = _productive(tmp_path)                       # remote_exposed defaults to False
    assert c.get("/stacks", headers={"Host": "192.168.178.66"}).status_code == 400


def test_wildcard_bind_is_never_a_valid_host(tmp_path):
    c = _productive(tmp_path, bind="0.0.0.0")       # bind set, but NOT exposed
    assert c.get("/stacks", headers={"Host": "0.0.0.0"}).status_code == 400


def test_ipv6_loopback_host_is_accepted(tmp_path):
    # `[::1]:8443`.split(":")[0] == "[" -> the hardcoded ::1 entry was unreachable before.
    c = _productive(tmp_path)
    assert c.get("/stacks", headers={"Host": "[::1]:8443"}).status_code == 200


def test_ipv6_literal_is_accepted_only_while_exposed(tmp_path):
    exposed = tmp_path / "exposed"
    loopback = tmp_path / "loopback"
    exposed.mkdir()
    loopback.mkdir()
    c_exposed = _productive(exposed, bind="0.0.0.0", remote_exposed=True,
                            allowed_cidrs=["0.0.0.0/0"])
    assert c_exposed.get("/stacks", headers={"Host": "[2001:db8::1]:8443"}).status_code == 200
    c_loopback = _productive(loopback)
    assert c_loopback.get("/stacks", headers={"Host": "[2001:db8::1]:8443"}).status_code == 400


def test_ip_sans_match_by_parsed_value_not_string(tmp_path):
    # An ip_sans entry of the compressed form must match a request for the expanded form.
    c = _productive(tmp_path, ip_sans=["2001:db8::1"])
    assert c.get("/stacks", headers={"Host": "[2001:db8:0:0:0:0:0:1]:8443"}).status_code == 200


def test_rejection_is_plain_text_actionable_and_logged(tmp_path, caplog):
    import logging
    c = _productive(tmp_path)
    with caplog.at_level(logging.WARNING):
        r = c.get("/stacks", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert r.mimetype == "text/plain"                    # nothing to inject into
    body = r.get_data(as_text=True)
    assert '"evil.example"' in body                      # names the rejected host
    assert "dns_sans" in body and "ip_sans" in body      # and the fix
    assert "<" not in body
    # the FULL diagnostic (raw header, allowlist, exposure) lands in the log, not the response
    rec = [r for r in caplog.records if "trusted-host: rejected Host" in r.message]
    assert rec and "127.0.0.1" not in body               # allowlist is not enumerated to the client


def test_host_echo_can_never_reflect_markup():
    # Werkzeug already blanks a Host containing illegal characters, so `<` cannot reach the body via
    # a real request. `_host_echo` is the belt to that braces — assert it directly.
    from lhpc.adapters.web.app import _host_echo
    assert _host_echo("<script>alert(1)</script>") == "scriptalert1script"
    assert "<" not in _host_echo("<b>")
    assert _host_echo("!!!") == "(unprintable)"
    assert _host_echo("pi.local") == "pi.local"
    assert len(_host_echo("a" * 500)) <= 80


def test_illegal_host_header_is_safe_200_or_400(tmp_path):
    # An unparseable Host must be SAFE either way and must never 500. Werkzeug's behaviour varies across
    # 3.1.x: 3.1.7 fail-closes by RAISING SecurityError (a BadRequest, .code == 400) from the test client;
    # 3.1.8 blanks request.host to "" -> 200. Both are fine — nothing downstream trusts a Host claim, and a
    # raised 400 is the same fail-closed outcome as a returned 400. (A fresh install resolves the newest
    # Werkzeug flask allows, so this is 200 in practice; the pinned floor werkzeug>=3.1 also covers 3.1.7.)
    from werkzeug.exceptions import HTTPException
    c = _productive(tmp_path)
    try:
        r = c.get("/stacks", headers={"Host": "a<b.com"})
    except HTTPException as exc:                  # 3.1.7 raises SecurityError(code=400) instead of returning it
        assert exc.code == 400, f"illegal Host raised a non-400 HTTP error: {exc!r}"
        return
    except Exception as exc:                      # anything else (e.g. a 500-class crash) is a real failure
        raise AssertionError(f"illegal Host raised a non-HTTP error: {exc!r}") from exc
    assert r.status_code in (200, 400)            # 200 = blanked, 400 = fail-closed; never 500


# --- blocker 1 regression + secret hygiene ----------------------------------

def test_webserver_modules_and_page_present_from_installed_package(tmp_path):
    # The exact 59f00de defect: these modules/template were referenced but missing. Importing
    # them + rendering the page proves they ship.
    import importlib
    importlib.import_module("lhpc.core.webserver")
    importlib.import_module("lhpc.core.pki")
    app, _ = _app(tmp_path)
    assert app.test_client().get("/stacks").status_code == 200   # template renders


def test_no_key_or_passphrase_leak_in_status_or_evidence(tmp_path):
    import json
    svc = _svc(tmp_path)
    svc.webserver_init(dns_sans=["pi.local"])
    svc.webserver_cert_issue("laptop", "sup3r-secret-pass")
    blob = json.dumps(svc.webserver_monitor().data)
    assert "BEGIN" not in blob and "PRIVATE KEY" not in blob and "sup3r-secret-pass" not in blob
    svc.webserver_verify()
    ev = (tmp_path / "state" / "webserver.json").read_text()
    assert "BEGIN" not in ev and "PRIVATE KEY" not in ev and "sup3r-secret-pass" not in ev

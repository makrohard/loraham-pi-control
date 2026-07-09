"""Correction blockers 5-7: reset-to-default cessation proof, typed dangerous-op
confirmations, and productive-only trusted-host enforcement."""

from __future__ import annotations

import os
from pathlib import Path

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
    app, svc = _app(tmp_path)
    c = app.test_client(); tok = _csrf(c)
    c.post("/webserver/expose", data={"_csrf": tok, "cidrs": "192.168.0.0/24",
                                      "confirm_phrase": "nope"})
    assert svc.config().webserver.remote_exposed is False        # wrong phrase -> not exposed
    c.post("/webserver/expose", data={"_csrf": tok, "cidrs": "192.168.0.0/24",
                                      "confirm_phrase": "enable-remote"})
    assert svc.config().webserver.remote_exposed is True
    # a public range needs the elevated phrase
    svc.webserver_disable_remote()
    c.post("/webserver/expose", data={"_csrf": tok, "cidrs": "0.0.0.0/0",
                                      "confirm_phrase": "enable-remote"})
    assert svc.config().webserver.remote_exposed is False        # normal phrase insufficient
    c.post("/webserver/expose", data={"_csrf": tok, "cidrs": "0.0.0.0/0",
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

def test_trusted_host_enforced_only_in_productive_mode(tmp_path):
    app, _ = _app(tmp_path)
    c = app.test_client()
    # non-productive (Secure cookies off) -> host not enforced
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 200
    # productive HTTPS -> unknown Host rejected, loopback allowed
    app.config["SESSION_COOKIE_SECURE"] = True
    assert c.get("/stacks", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/stacks", headers={"Host": "127.0.0.1"}).status_code == 200


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

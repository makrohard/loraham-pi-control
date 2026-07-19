"""Correction round 2 (blockers 1-5): operator-context start-service, staged nginx config,
persisted init SANs, transactional revocation, CLI confirmations."""

from __future__ import annotations

import os

from lhpc.adapters.cli.main import main
from lhpc.adapters.web.app import create_app
from lhpc.core import pki, runtime_fs, webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, fake=None):
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def _staged(paths):
    return str(paths.under(*webserver.NGINX_CONF_STAGED))


def _live(tmp_path):
    return tmp_path / "config" / "nginx" / "lhpc.conf"


# --- correction 2: staged config, live preserved on failure -----------------

def test_apply_invalid_config_leaves_live_intact(tmp_path):
    svc0 = _svc(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "config", "nginx")
    runtime_fs.atomic_write(paths, paths.under(*webserver.NGINX_CONF), "SENTINEL-LIVE\n", 0o644)
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged(paths)): CR(1, "", "emerg: bad")})
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_apply()
    assert not r.ok and "remains active" in r.summary
    assert _live(tmp_path).read_text() == "SENTINEL-LIVE\n"          # untouched


def test_apply_valid_promotes_staged(tmp_path):
    svc0 = _svc(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    live = str(paths.under(*webserver.NGINX_CONF))
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged(paths)): CR(0, "", "ok"),
                                ("nginx", "-s", "reload", "-c", live): CR(0, "", "")})
    svc = ControllerService(system=fake.system, paths=paths)
    assert svc.webserver_apply().ok
    assert _live(tmp_path).exists() and "server unix:" in _live(tmp_path).read_text()


def test_verify_does_not_touch_live_config(tmp_path):
    svc0 = _svc(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    runtime_fs.mkdir(paths, "config", "nginx")
    runtime_fs.atomic_write(paths, paths.under(*webserver.NGINX_CONF), "SENTINEL\n", 0o644)
    fake = FakeSystem(commands={("nginx", "-v"): CR(0, "", ""),
                                ("nginx", "-t", "-c", _staged(paths)): CR(0, "", "ok")})
    ControllerService(system=fake.system, paths=paths).webserver_verify()
    assert _live(tmp_path).read_text() == "SENTINEL\n"


# --- correction 3: init persists SANs ---------------------------------------

def test_init_persists_sans_for_trusted_host_and_renew(tmp_path):
    svc = _svc(tmp_path)
    svc.webserver_init(dns_sans=["pi.local"], ip_sans=["192.168.0.10"])
    cfg = svc.config().webserver
    assert cfg.dns_sans == ("pi.local",) and cfg.ip_sans == ("192.168.0.10",)
    # tls-renew uses the saved SANs (no empty-SAN failure)
    assert ControllerService(system=FakeSystem().system, paths=svc._paths).webserver_tls_renew().ok
    # productive trusted-host accepts the SANs
    app = create_app(lambda: ControllerService(system=FakeSystem().system, paths=svc._paths))
    app.config["SESSION_COOKIE_SECURE"] = True
    c = app.test_client()
    assert c.get("/stacks", headers={"Host": "pi.local"}).status_code == 200
    assert c.get("/stacks", headers={"Host": "192.168.0.10"}).status_code == 200


# --- correction 4: transactional revocation ---------------------------------

def test_revoke_crl_failure_keeps_cert_active(tmp_path, monkeypatch):
    svc = _svc(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    monkeypatch.setattr(pki, "_build_and_write_crl",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    r = svc.webserver_cert_revoke("laptop")
    assert not r.ok and "ACTIVE" in r.summary                       # not falsely revoked
    assert all(c["state"] == "active" for c in pki.list_client_certs(svc._paths)
               if c["label"] == "laptop")


def test_revoke_index_save_failure_is_pending_not_active(tmp_path, monkeypatch):
    # Correction B: CRL written but inventory commit fails -> 'revocation-pending', not active.
    svc = _svc(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    monkeypatch.setattr(pki, "_save_index",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    r = svc.webserver_cert_revoke("laptop")
    assert not r.ok and "REVOCATION-PENDING" in r.summary
    states = {c["state"] for c in pki.list_client_certs(svc._paths) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}                          # never ordinary active/revoked
    assert (tmp_path / "config/tls/client-ca/crl.pem").exists()      # CRL was written


# --- correction (P2): CRL is the FAIL-SAFE truth source for revocation -------
#
# A certificate whose serial is present in the CRL must NEVER be shown as ordinary 'active',
# even if the inventory still says active and the 'revocation-pending.json' marker is missing,
# unreadable, or malformed. list_client_certs reads the CRL directly and derives revoked serials.

def _revoke_serial_in_crl_only(paths):
    """Write a CRL that revokes 'laptop' WITHOUT committing the inventory or a pending marker,
    returning the serial. Mimics: CRL write succeeded, inventory commit + marker both lost."""
    import copy
    idx = pki._load_index(paths)
    cand = copy.deepcopy(idx)
    cand["crl_number"] = int(cand.get("crl_number", 0)) + 1
    hit = next(e for e in cand["certs"] if e.get("label") == "laptop" and e.get("state") == "active")
    hit["state"] = "revoked"
    pki._build_and_write_crl(paths, cand)          # CRL now revokes the serial; index untouched
    return hit["serial"]


def test_revoke_pending_marker_and_index_both_fail_still_pending(tmp_path, monkeypatch):
    # Worst case: CRL written, but BOTH the inventory commit and the pending-marker write fail.
    # The CRL is authoritative -> the cert is still surfaced as revocation-pending, never active.
    svc = _svc(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    monkeypatch.setattr(pki, "_save_index",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(pki, "_add_pending",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("marker write failed")))
    r = svc.webserver_cert_revoke("laptop")
    assert not r.ok and "REVOCATION-PENDING" in r.summary          # truthful even when marker fails
    p = svc._paths
    assert not (tmp_path / "config/tls/client-ca/revocation-pending.json").exists()   # marker absent
    assert (tmp_path / "config/tls/client-ca/crl.pem").exists()                       # CRL written
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}                        # never ordinary active/revoked


def test_crl_is_truth_source_when_pending_marker_missing_or_malformed(tmp_path):
    svc = _svc(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    p = svc._paths
    serial = _revoke_serial_in_crl_only(p)         # CRL revokes serial; inventory still 'active'
    marker = tmp_path / "config/tls/client-ca/revocation-pending.json"
    assert not marker.exists()                     # missing marker
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}        # CRL alone drives the truthful state
    # malformed marker: still pending (CRL wins; malformed marker tolerated as no-evidence)
    marker.write_text("}{ not json")
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revocation-pending"}
    # the raw inventory on disk was never mutated -> the CRL is genuinely the overlay source
    assert any(c["state"] == "active" and c["serial"] == serial
               for c in pki._load_index(p)["certs"])


def test_active_cert_not_in_crl_stays_active(tmp_path):
    # A cert whose serial is NOT in the CRL must remain ordinary active (no false positives).
    svc = _svc(tmp_path); svc.webserver_init()
    svc.webserver_cert_issue("keep", "pw"); svc.webserver_cert_issue("gone", "pw")
    p = svc._paths
    assert svc.webserver_cert_revoke("gone").ok     # clean revoke -> committed
    by_label = {c["label"]: c["state"] for c in pki.list_client_certs(p)}
    assert by_label["keep"] == "active"             # not in CRL -> untouched
    assert by_label["gone"] == "revoked"            # committed revoked stays revoked


def test_committed_revoked_stays_revoked(tmp_path):
    svc = _svc(tmp_path); svc.webserver_init(); svc.webserver_cert_issue("laptop", "pw")
    p = svc._paths
    assert svc.webserver_cert_revoke("laptop").ok
    states = {c["state"] for c in pki.list_client_certs(p) if c["label"] == "laptop"}
    assert states == {"revoked"}                    # never downgraded to pending by the CRL overlay


# --- correction A: init fails closed on SAN persistence failure --------------

def test_init_fails_closed_on_san_persist_failure(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    monkeypatch.setattr(cfgmod, "save_webserver_config",
                        lambda *a, **k: (_ for _ in ()).throw(cfgmod.ConfigError("save failed")))
    r = svc.webserver_init(dns_sans=["pi.local"], ip_sans=["192.168.0.10"])
    assert not r.ok and "no PKI was created" in r.summary            # failed, no success message
    st = pki.pki_status(svc._paths)
    assert not st["server_ca"]["present"] and not st["client_ca"]["present"]
    assert not st["server_cert"]["present"]                          # nothing created/replaced


# --- correction 5: CLI confirmations ----------------------------------------

def test_cli_revoke_requires_confirm_label(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path)); (tmp_path / "config").mkdir(exist_ok=True)
    assert main(["webserver", "init"]) == 0
    assert main(["webserver", "cert", "issue", "laptop"]) == 0
    capsys.readouterr()
    assert main(["webserver", "cert", "revoke", "laptop"]) == 1     # no --confirm-label -> refused
    p = Paths(runtime_root=tmp_path)
    assert all(c["state"] == "active" for c in pki.list_client_certs(p) if c["label"] == "laptop")
    assert main(["webserver", "cert", "revoke", "laptop", "--confirm-label", "laptop"]) == 0
    assert any(c["state"] == "revoked" for c in pki.list_client_certs(p) if c["label"] == "laptop")


# --- correction 1: operator-context start-service ---------------------------

def test_start_service_refuses_from_managed_unit(monkeypatch, tmp_path):
    monkeypatch.setenv("INVOCATION_ID", "managed")
    r = _svc(tmp_path).webserver_start_service()
    assert not r.ok and "managed unit" in r.summary


def test_start_service_prereqs(monkeypatch, tmp_path):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    r = _svc(tmp_path).webserver_start_service()          # no nginx (FakeSystem) -> refused
    assert not r.ok and "nginx is not installed" in r.summary


def test_start_service_enables_and_starts(monkeypatch, tmp_path):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    svc0 = _svc(tmp_path); svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    fake = FakeSystem(commands={
        ("nginx", "-v"): CR(0, "", ""),
        ("nginx", "-t", "-c", _staged(paths)): CR(0, "", "ok"),
        ("systemctl", "--user", "enable", "--now", "lhpc-nginx.service"): CR(0, "", ""),
    })
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_start_service()
    assert r.ok and "https://" in r.summary
    assert ["systemctl", "--user", "enable", "--now", "lhpc-nginx.service"] in fake.calls
    assert _live(tmp_path).exists()                        # config promoted
    assert (tmp_path / "state" / "run" / "nginx").is_dir()  # rootless temp-path parent created
    assert (tmp_path / "logs").is_dir()                     # nginx error/access log parent

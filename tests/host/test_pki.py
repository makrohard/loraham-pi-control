"""M4-M6: two-CA PKI, server + client certificates, encrypted PKCS#12, CRL/revocation.

Pure-crypto unit tests (no nginx). These prove CA separation, SAN policy, bundle
contents/encryption, label-injection resistance, inventory/CRL state, and restrictive
permissions. They do NOT claim end-to-end "revoked cert rejected by nginx" enforcement —
that is an opt-in integration test with a real proxy (see the plan).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12

from lhpc.core import pki
from lhpc.core.paths import Paths


def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


def _mode(tmp_path: Path, *parts: str) -> int:
    return stat.S_IMODE(os.stat(tmp_path.joinpath("config", "tls", *parts)).st_mode)


def _init_both(paths):
    pki.init_server_ca(paths)
    pki.init_client_ca(paths)


# --- CA lifecycle + separation ----------------------------------------------

def test_two_cas_are_independent(tmp_path):
    paths = _paths(tmp_path)
    s = pki.init_server_ca(paths)
    c = pki.init_client_ca(paths)
    assert s["serial"] != c["serial"]
    assert "Server TLS CA" in s["subject"] and "Client Auth CA" in c["subject"]
    assert pki.cas_are_distinct(paths) is True


def test_ca_reinit_refused_without_force(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    with pytest.raises(pki.PKIError):
        pki.init_server_ca(paths)          # exists -> refuse (rotate is the destructive path)
    pki.rotate_server_ca(paths)            # explicit destructive replace is allowed


def test_ca_and_key_permissions(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    assert _mode(tmp_path) == 0o700                       # config/tls dir
    assert _mode(tmp_path, "server-ca", "ca.key") == 0o600
    assert _mode(tmp_path, "server-ca", "ca.crt") == 0o644


# --- server certificate + SAN policy ----------------------------------------

def test_server_cert_with_dns_and_ip_sans(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    summ = pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=["192.168.0.10"], days=90)
    assert summ["kind"] == "server"
    cert = x509.load_pem_x509_certificate(
        (tmp_path / "config/tls/server/server.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "pi.local" in san.get_values_for_type(x509.DNSName)
    ips = [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    assert "192.168.0.10" in ips
    assert _mode(tmp_path, "server", "server.key") == 0o600


def test_server_cert_requires_a_san(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    with pytest.raises(pki.PKIError):
        pki.issue_server_cert(paths, dns_sans=[], ip_sans=[], days=90)


def test_server_cert_rejects_wildcard_ip_san(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    with pytest.raises(pki.PKIError):
        pki.issue_server_cert(paths, dns_sans=[], ip_sans=["0.0.0.0"], days=90)


def test_server_cert_requires_ca(tmp_path):
    with pytest.raises(pki.PKIError):
        pki.issue_server_cert(_paths(tmp_path), dns_sans=["pi.local"], ip_sans=[], days=90)


# --- client certificate + PKCS#12 -------------------------------------------

def test_client_cert_p12_contents_and_encryption(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    summ = pki.issue_client_cert(paths, "laptop", days=90, passphrase="s3cret-pass")
    assert summ["label"] == "laptop" and summ["state"] == "active"
    p12_path = tmp_path / "config/tls/exports/laptop.p12"
    assert _mode(tmp_path, "exports", "laptop.p12") == 0o600
    blob = p12_path.read_bytes()
    assert summ["export_sha256"] == __import__("hashlib").sha256(blob).hexdigest()
    # Wrong passphrase must fail; right passphrase yields key + client leaf + CA chain.
    with pytest.raises(Exception):
        pkcs12.load_key_and_certificates(blob, b"wrong-pass")
    key, cert, cas = pkcs12.load_key_and_certificates(blob, b"s3cret-pass")
    assert key is not None and cert is not None
    assert cert.subject.rfc4514_string().endswith("CN=laptop")
    assert any("Client Auth CA" in c.subject.rfc4514_string() for c in cas)


def test_client_label_injection_rejected(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    for bad in ("../escape", "a/b", "he;rm", "spa ce", ""):
        with pytest.raises(Exception):
            pki.issue_client_cert(paths, bad, days=90, passphrase="p")


def test_client_requires_passphrase_and_ca(tmp_path):
    paths = _paths(tmp_path)
    pki.init_client_ca(paths)
    with pytest.raises(pki.PKIError):
        pki.issue_client_cert(paths, "laptop", days=90, passphrase="")
    fresh = _paths(tmp_path / "fresh")
    (tmp_path / "fresh").mkdir()
    with pytest.raises(pki.PKIError):
        pki.issue_client_cert(fresh, "laptop", days=90, passphrase="p")   # no client CA


def test_duplicate_active_label_refused_then_reissue(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_client_cert(paths, "tablet", days=90, passphrase="p1")
    with pytest.raises(pki.PKIError):
        pki.issue_client_cert(paths, "tablet", days=90, passphrase="p2")
    # reissue = revoke old + issue new; ends with exactly one active 'tablet'
    pki.reissue_client_cert(paths, "tablet", days=90, passphrase="p3")
    certs = pki.list_client_certs(paths)
    active = [c for c in certs if c["label"] == "tablet" and c["state"] == "active"]
    revoked = [c for c in certs if c["label"] == "tablet" and c["state"] == "revoked"]
    assert len(active) == 1 and len(revoked) == 1


# --- revocation + CRL --------------------------------------------------------

def test_revoke_records_state_and_populates_crl(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    s = pki.issue_client_cert(paths, "firefox-backup", days=90, passphrase="p")
    hit = pki.revoke_client_cert(paths, "firefox-backup")
    assert hit["state"] == "revoked" and "revoked_at" in hit
    # CRL exists, is signed by the client CA, and lists the revoked serial.
    crl = x509.load_pem_x509_crl((tmp_path / "config/tls/client-ca/crl.pem").read_bytes())
    serials = {format(r.serial_number, "x") for r in crl}
    assert s["serial"] in serials
    # revoking a non-active label fails
    with pytest.raises(pki.PKIError):
        pki.revoke_client_cert(paths, "firefox-backup")


def test_discard_export_removes_bundle_keeps_history(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_client_cert(paths, "admin-browser", days=90, passphrase="p")
    assert (tmp_path / "config/tls/exports/admin-browser.p12").exists()
    assert pki.discard_export(paths, "admin-browser") is True
    assert not (tmp_path / "config/tls/exports/admin-browser.p12").exists()
    entry = next(c for c in pki.list_client_certs(paths) if c["label"] == "admin-browser")
    assert entry.get("export") in (None, "") and entry.get("export_discarded") is True


def test_rotate_client_ca_resets_inventory(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_client_cert(paths, "laptop", days=90, passphrase="p")
    assert pki.list_client_certs(paths)
    pki.rotate_client_ca(paths)
    assert pki.list_client_certs(paths) == []      # old certs untrusted -> inventory reset


@pytest.mark.parametrize("damage", [b"", b"{not json", b'{"schema": 99, "certs": []}'])
def test_a_damaged_index_is_never_overwritten(tmp_path, damage):
    # Saving the empty index a damaged one reads as would forget every issued certificate: an
    # ACTIVE one could then never be revoked. Both writers refuse instead; the bytes stay. (The
    # CRL refresh re-signs the existing CRL instead: test_a_damaged_index_keeps_the_crl_alive.)
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_client_cert(paths, "phone", days=90, passphrase="p")
    index = tmp_path / "config/tls/client-ca/client-index.json"
    good = index.read_bytes()
    index.write_bytes(damage)
    with pytest.raises(pki.PKIError, match="client-index.json"):
        pki.issue_client_cert(paths, "tablet", days=90, passphrase="p")
    with pytest.raises(pki.PKIError, match="client-index.json"):
        pki.revoke_client_cert(paths, "phone")
    assert index.read_bytes() == damage
    index.write_bytes(good)                        # restored: the certificate is still revocable
    assert pki.revoke_client_cert(paths, "phone")["state"] == "revoked"


@pytest.mark.parametrize("damage", [
    b'{"schema": 1, "certs": [null]}',
    b'{"schema": 1, "certs": ["phone"]}',
    "label=5", "state=null", "serial=17", "serial=not-hex", "crl_number=x"])
def test_a_schema_1_index_with_a_bad_entry_is_refused_by_both_writers(tmp_path, damage):
    # schema == 1 and a list of certs is not enough: an entry the writers cannot read made them
    # raise AttributeError/TypeError (or act on a wrong-typed field) instead of the typed refusal.
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.build_crl(paths)
    pki.issue_client_cert(paths, "phone", days=90, passphrase="p")
    index = tmp_path / "config/tls/client-ca/client-index.json"
    crl = tmp_path / "config/tls/client-ca/crl.pem"
    if isinstance(damage, str):
        field, value = damage.split("=")
        import json
        value = {"null": None, "5": 5, "17": 17}.get(value, value)
        data = json.loads(index.read_text())
        if field == "crl_number":
            data["crl_number"] = value
        else:
            data["certs"][0][field] = value
        damage = json.dumps(data).encode()
    index.write_bytes(damage)
    crl_before = crl.read_bytes()
    with pytest.raises(pki.PKIError, match="client-index.json"):
        pki.issue_client_cert(paths, "tablet", days=90, passphrase="p")
    with pytest.raises(pki.PKIError, match="client-index.json"):
        pki.revoke_client_cert(paths, "phone")
    assert index.read_bytes() == damage                     # nothing is written
    assert crl.read_bytes() == crl_before
    assert not (tmp_path / "config/tls/exports/tablet.p12").exists()
    assert pki.list_client_certs(paths) == []               # a reader stays fail-safe


def _crl(tmp_path):
    return x509.load_pem_x509_crl((tmp_path / "config/tls/client-ca/crl.pem").read_bytes())


def test_a_damaged_index_keeps_the_crl_alive(tmp_path):
    # Maintainer policy (CR5-1): while the inventory is damaged the CRL refresh re-signs the
    # EXISTING revocation set — no valid client is locked out when the CRL would expire, nothing
    # revoked comes back — and leaves the inventory alone. Issue and revoke stay refused.
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_client_cert(paths, "phone", days=90, passphrase="p")
    pki.issue_client_cert(paths, "old-tablet", days=90, passphrase="p")
    pki.issue_client_cert(paths, "old-laptop", days=90, passphrase="p")
    pki.revoke_client_cert(paths, "old-tablet")
    pki.revoke_client_cert(paths, "old-laptop")
    old = _crl(tmp_path)
    old_serials = {r.serial_number for r in old}
    assert len(old_serials) == 2
    index = tmp_path / "config/tls/client-ca/client-index.json"
    index.write_bytes(b"{not json")
    pki.build_crl(paths)
    new = _crl(tmp_path)
    assert {r.serial_number for r in new} == old_serials        # the same set, nothing un-revoked
    ca = x509.load_pem_x509_certificate((tmp_path / "config/tls/client-ca/ca.crt").read_bytes())
    assert new.is_signature_valid(ca.public_key()) and new.issuer == old.issuer
    num = lambda c: c.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number
    assert num(new) == num(old) + 1
    assert index.read_bytes() == b"{not json"                   # the inventory is not touched
    with pytest.raises(pki.PKIError, match="client-index.json"):
        pki.revoke_client_cert(paths, "phone")


def test_a_damaged_index_without_a_crl_refuses_the_refresh(tmp_path):
    # No current CRL: there is no existing set to re-sign, and an empty one could un-revoke what only
    # the damaged inventory records.
    paths = _paths(tmp_path)
    _init_both(paths)
    (tmp_path / "config/tls/client-ca/crl.pem").unlink(missing_ok=True)
    (tmp_path / "config/tls/client-ca/client-index.json").write_bytes(b"{not json")
    with pytest.raises(pki.PKIError, match="client-index.json"):
        pki.build_crl(paths)
    assert not (tmp_path / "config/tls/client-ca/crl.pem").exists()


def test_status_says_a_damaged_index_needs_repair(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    assert pki.pki_status(paths)["index_problem"] is None
    (tmp_path / "config/tls/client-ca/client-index.json").write_bytes(b'{"schema": 1, "certs": [null]}')
    problem = pki.pki_status(paths)["index_problem"]
    assert "client-index.json" in problem and "restore it from a backup" in problem


def test_an_absent_index_still_starts_fresh(tmp_path):
    paths = _paths(tmp_path)
    _init_both(paths)
    (tmp_path / "config/tls/client-ca/client-index.json").unlink(missing_ok=True)
    pki.build_crl(paths)
    assert pki.list_client_certs(paths) == []
    assert (tmp_path / "config/tls/client-ca/client-index.json").exists()


# --- read-only status --------------------------------------------------------

def test_pki_status_reports_presence(tmp_path):
    paths = _paths(tmp_path)
    assert pki.pki_status(paths)["server_ca"]["present"] is False
    _init_both(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)
    pki.issue_client_cert(paths, "laptop", days=90, passphrase="p")
    st = pki.pki_status(paths)
    assert st["server_ca"]["present"] and st["client_ca"]["present"]
    assert st["server_cert"]["present"] and len(st["clients"]) == 1


@pytest.mark.parametrize("sub", ["server", "client-ca"])
def test_a_symlinked_tls_dir_reads_as_unreadable(tmp_path, sub):
    # The descriptor-anchored reader refuses a symlinked parent with PathContainmentError (a
    # ValueError, not an OSError); it must become the typed "unreadable" answer, never escape.
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)
    pki.issue_client_cert(paths, "laptop", days=90, passphrase="p")
    d = tmp_path / "config" / "tls" / sub
    d.rename(tmp_path / "elsewhere")
    d.symlink_to(tmp_path / "elsewhere")             # same files, reached through a symlink
    st = pki.pki_status(paths)
    if sub == "server":
        assert pki.server_cert_names(paths)[0] == "unreadable"
        assert pki.server_key_state(paths)[0] == "unreadable"
        assert pki.server_cert_chain_ok(paths)[0] is False
        assert st["server_cert"]["present"] is False
    else:
        assert st["client_ca"] == {"present": False, "error": "unreadable"}
        assert st["clients"] == []
        with pytest.raises(pki.PKIError):
            pki.build_crl(paths)


# --- the server chain check ---------------------------------------------------

@pytest.mark.parametrize("exc", [ValueError("issuer"), TypeError("key type"), "invalid-signature"])
def test_chain_check_maps_each_verify_error_to_not_issued(tmp_path, monkeypatch, exc):
    from cryptography.exceptions import InvalidSignature
    err = InvalidSignature() if exc == "invalid-signature" else exc

    class _Cert:
        def verify_directly_issued_by(self, issuer):
            raise err
    # `_read_cert` stubbed as the collaborator: each verify error class needs a certificate whose
    # `verify_directly_issued_by` raises exactly it, which no real pair produces on demand.
    monkeypatch.setattr(pki, "_read_cert", lambda paths, path: _Cert())
    assert pki.server_cert_chain_ok(_paths(tmp_path)) == (False, pki.CHAIN_NOT_ISSUED)


def test_chain_check_unreadable_never_raises(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)

    def boom(p, path):
        raise pki.PKIError("malformed certificate")
    # `_read_cert` stubbed as the collaborator: the mapping of its typed refusal is under test.
    monkeypatch.setattr(pki, "_read_cert", boom)
    assert pki.server_cert_chain_ok(paths) == (False, "unreadable: malformed certificate")


def test_doctor_says_a_damaged_index_needs_repair(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    paths = _paths(tmp_path)
    _init_both(paths)
    pki.build_crl(paths)
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    assert not any("client-certificate list" in d for d in svc.doctor().details)
    (tmp_path / "config/tls/client-ca/client-index.json").write_bytes(b"{not json")
    line = [d for d in svc.doctor().details if "client-certificate list" in d]
    assert line and "repair needed" in line[0] and "client-index.json" in line[0]

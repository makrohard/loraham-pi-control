"""Local PKI for the production webserver — two INDEPENDENT certificate authorities:

    Server TLS CA   -> signs the HTTPS server leaf
    Client-auth CA  -> signs browser/device client certificates (mTLS)

plus the encrypted PKCS#12 client bundles and the client-auth CRL. Everything is generated
with the `cryptography` library — never by building OpenSSL shell command strings for
security-sensitive material.

Storage: <runtime>/config/tls/ (dir 0700). CA/leaf keys 0600, certificates + CRL 0644,
`.p12` exports 0600. All writes go through `runtime_fs` (descriptor-anchored, no-follow,
atomic temp+rename). This module is pure generation/inventory; nginx activation and the real
"revoked cert rejected" ENFORCEMENT proof live in webserver.py/services.py. It NEVER logs
private keys, passphrases, or raw key material.

Design notes:
  * `[webserver]` desired config selects SANs/lifetimes; this module is the storage/crypto.
  * A client certificate label is a device identifier only (no accounts/roles); it is
    validated with `path_component` so it is safe as a filename and cannot inject paths.
  * `0.0.0.0` is refused as a SAN (it is a bind wildcard, never an identity).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import runtime_fs, validators
from .paths import PathContainmentError, Paths
from .service_system import PKI_NOT_BEFORE

_TLS = ("config", "tls")
_SERVER_CA, _CLIENT_CA, _SERVER, _EXPORTS = "server-ca", "client-ca", "server", "exports"
_INDEX = "client-index.json"
INDEX_SCHEMA = 1
_CA_DAYS_DEFAULT = 3650
_CRL_DAYS_DEFAULT = 30

# --------------------------------------------------------------------------- provisional validity
#
# Material minted while the clock is UNVERIFIED -- commissioning on a box with no RTC, no NTP and no
# GPS fix -- gets a FIXED window, never one derived from that clock. A clock we have just declared
# untrustworthy cannot anchor a validity period in either direction: a stale clock would mint
# material that expires early, a fast one material that is "not yet valid" to a correct-clock
# browser from the first minute. The lower bound is the clock gate's own floor; the upper bound is
# the last instant expressible as UTCTime, so the encoding never crosses into GeneralizedTime
# (Phase 0, 2026-09-17: verified with OpenSSL 3.5, Python TLS, NSS and GnuTLS).
PROVISIONAL_NOT_BEFORE = _dt.datetime.fromtimestamp(PKI_NOT_BEFORE, _dt.UTC)
PROVISIONAL_NOT_AFTER = _dt.datetime(2049, 12, 31, 23, 59, 59, tzinfo=_dt.UTC)
PROVISIONAL_VALIDITY = (PROVISIONAL_NOT_BEFORE, PROVISIONAL_NOT_AFTER)
# The marker meaning exactly "this PKI still needs normalising". It lives INSIDE config/tls so the
# image seal removes it with the material it describes, and it is written BEFORE the first PKI write
# so a power cut can never leave provisional material that LHPC has forgotten about.
_PROVISIONAL_MARKER = "unverified-clock"


class PKIError(Exception):
    """A PKI operation failed — surfaced as a typed diagnostic, never a crash."""


# --------------------------------------------------------------------------- paths / layout

def _p(paths: Paths, *parts: str) -> Path:
    return paths.under(*_TLS, *parts)


def _ca_paths(paths: Paths, which: str) -> tuple:
    return _p(paths, which, "ca.key"), _p(paths, which, "ca.crt")


def _index_path(paths: Paths) -> Path:
    return _p(paths, _CLIENT_CA, _INDEX)


def ensure_layout(paths: Paths) -> None:
    """Create config/tls/ (0700) and its subdirs with restrictive perms (idempotent)."""
    runtime_fs.chmod(paths, _p(paths), 0o700, create_dir=True)
    for sub in (_SERVER_CA, _CLIENT_CA, _SERVER, _EXPORTS):
        runtime_fs.chmod(paths, _p(paths, sub), 0o700, create_dir=True)


# --------------------------------------------------------------------------- provisional marker

def provisional_marker_path(paths: Paths) -> Path:
    return _p(paths, _PROVISIONAL_MARKER)


def mark_provisional(paths: Paths) -> None:
    """Record, durably and BEFORE any material is written, that what follows is provisional.
    Raises on failure: material that cannot be recorded as provisional must not be created."""
    ensure_layout(paths)
    runtime_fs.atomic_write(paths, provisional_marker_path(paths), "unverified-clock\n", mode=0o600)


def provisional_pending(paths: Paths) -> bool:
    """True while the PKI was minted under an unverified clock and has not been normalised."""
    return _exists(paths, provisional_marker_path(paths))


def clear_provisional(paths: Paths) -> None:
    runtime_fs.unlink(paths, provisional_marker_path(paths))


def _is_provisional_window(not_before, not_after) -> bool:
    return not_before == PROVISIONAL_NOT_BEFORE and not_after == PROVISIONAL_NOT_AFTER


def server_cert_is_provisional(paths: Paths):
    """True/False for the server leaf's window, None when there is no leaf. Decidable because
    the provisional window is a fixed value -- which is what lets normalisation be restartable
    without minting again."""
    cert = _read_cert(paths, _p(paths, _SERVER, "server.crt"))
    if cert is None:
        return None
    return _is_provisional_window(cert.not_valid_before_utc, cert.not_valid_after_utc)


def crl_is_provisional(paths: Paths):
    crl_p = _p(paths, _CLIENT_CA, "crl.pem")
    if not _exists(paths, crl_p):
        return None
    try:
        crl = x509.load_pem_x509_crl(runtime_fs.read_text_regular(paths, crl_p).encode("ascii"))
    except (OSError, ValueError, PathContainmentError) as exc:
        raise PKIError(f"unreadable CRL {crl_p}: {exc}") from exc
    lu = getattr(crl, "last_update_utc", None) or crl.last_update.replace(tzinfo=_dt.UTC)
    nu = getattr(crl, "next_update_utc", None) or crl.next_update.replace(tzinfo=_dt.UTC)
    return _is_provisional_window(lu, nu)


def _validity(backdate: _dt.timedelta, days: int, validity):
    """(notBefore, notAfter): the fixed provisional window when given, else now-based."""
    if validity is not None:
        return validity
    now = _now()
    return now - backdate, now + _dt.timedelta(days=days)


# --------------------------------------------------------------------------- read/write

def _write_key(paths: Paths, path: Path, key) -> None:
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    runtime_fs.atomic_write(paths, path, pem.decode("ascii"), mode=0o600)


def _write_cert(paths: Paths, path: Path, cert) -> None:
    runtime_fs.atomic_write(paths, path,
                            cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
                            mode=0o644)


def _read_cert(paths: Paths, path: Path):
    try:
        raw = runtime_fs.read_text_regular(paths, path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PKIError(f"unsafe/unreadable certificate {path}: {exc}") from exc
    try:
        return x509.load_pem_x509_certificate(raw.encode("ascii"))
    except ValueError as exc:
        raise PKIError(f"malformed certificate {path}: {exc}") from exc


def _read_key(paths: Paths, path: Path):
    try:
        raw = runtime_fs.read_text_regular(paths, path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PKIError(f"unsafe/unreadable key {path}: {exc}") from exc
    try:
        return serialization.load_pem_private_key(raw.encode("ascii"), password=None)
    except ValueError as exc:
        raise PKIError(f"malformed key {path}: {exc}") from exc


def _exists(paths: Paths, path: Path) -> bool:
    return runtime_fs.stat_leaf_nofollow(paths, path) is not None


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- summaries / index

def _summary(cert, kind: str, **extra) -> dict:
    s = {
        "kind": kind,
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "subject": cert.subject.rfc4514_string(),
    }
    s.update(extra)
    return s


def _empty_index() -> dict:
    return {"schema": INDEX_SCHEMA, "certs": [], "crl_number": 0}


def _load_index(paths: Paths) -> dict:
    """Fail-safe read of the client-cert inventory (mirrors the selfupdate cache pattern):
    absent/unsafe/malformed/wrong-schema -> a fresh empty index, never a crash."""
    try:
        raw = runtime_fs.read_text_regular(paths, _index_path(paths))
    except (FileNotFoundError, OSError):
        return _empty_index()
    try:
        data = json.loads(raw)
    except ValueError:
        return _empty_index()
    if (not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA
            or not isinstance(data.get("certs"), list)):
        return _empty_index()
    data.setdefault("crl_number", 0)
    return data


def _save_index(paths: Paths, idx: dict) -> None:
    runtime_fs.write_marker(paths, _index_path(paths), json.dumps(idx, indent=2, sort_keys=True),
                            mode=0o600)


# Durable recovery marker (correction B): a certificate whose CRL entry was written but whose
# inventory commit failed. Such a cert is CRL-revoked on disk but still 'active' in the (stale)
# inventory — status must show it as 'revocation-pending', never ordinary active.
_PENDING = "revocation-pending.json"


def _pending_path(paths: Paths) -> Path:
    return _p(paths, _CLIENT_CA, _PENDING)


def _load_pending(paths: Paths) -> list:
    try:
        raw = runtime_fs.read_text_regular(paths, _pending_path(paths))
    except (FileNotFoundError, OSError):
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _add_pending(paths: Paths, entry: dict) -> None:
    cur = _load_pending(paths)
    if not any(e.get("serial") == entry.get("serial") for e in cur):
        cur.append({"serial": entry["serial"], "label": entry.get("label", "")})
    runtime_fs.write_marker(paths, _pending_path(paths),
                            json.dumps(cur, indent=2, sort_keys=True), mode=0o600)


def _remove_pending(paths: Paths, serial: str) -> None:
    cur = [e for e in _load_pending(paths) if e.get("serial") != serial]
    if cur:
        runtime_fs.write_marker(paths, _pending_path(paths),
                                json.dumps(cur, indent=2, sort_keys=True), mode=0o600)
    else:
        try:
            runtime_fs.unlink(paths, _pending_path(paths))
        except (FileNotFoundError, OSError):
            pass


# --------------------------------------------------------------------------- key / CA / leaf builders

def _new_key():
    # EC P-256: modern, small, fast; supported by nginx for both server + client leaves.
    return ec.generate_private_key(ec.SECP256R1())


# Leaf certificates are backdated by a DAY, not the minute the CAs use. A minute is far too
# tight to absorb an ordinary clock difference at validation time: the browser or nginx checking
# the certificate may be seconds-to-hours off from the Pi that signed it, and a leaf that is "not
# yet valid" is indistinguishable from a broken one to the person locked out by it. A day costs
# nothing (the certificate is no more powerful for existing yesterday) and is ordinary CA practice.
#
# The CAs deliberately keep the one-minute backdate: they are signed once, their lifetime is
# measured in years, and widening it buys nothing the leaves do not already get.
#
# This is a TOLERANCE, not a verification -- it does not rescue a box whose clock is wrong by more
# than a day, which is what the clock gate in service_webserver is for. Existing certificates are
# NOT reissued to apply it.
_LEAF_BACKDATE = _dt.timedelta(days=1)
_CA_BACKDATE = _dt.timedelta(minutes=1)


def _sign_ca(key, common_name: str, days: int, *, validity=None):
    not_before, not_after = _validity(_CA_BACKDATE, days, validity)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    ku = x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                       crl_sign=True, encipher_only=False, decipher_only=False)
    return (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(ku, critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))


def _sign_leaf(ca_key, ca_cert, leaf_key, common_name: str, days: int, *, eku, san=None,
               validity=None):
    not_before, not_after = _validity(_LEAF_BACKDATE, days, validity)
    ku = x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                       data_encipherment=False, key_agreement=False, key_cert_sign=False,
                       crl_sign=False, encipher_only=False, decipher_only=False)
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
               .issuer_name(ca_cert.subject)
               .public_key(leaf_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(not_before)
               .not_valid_after(not_after)
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(ku, critical=True)
               .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                              critical=False))
    if san is not None:
        builder = builder.add_extension(san, critical=False)
    return builder.sign(ca_key, hashes.SHA256())


def _build_san(dns_sans, ip_sans):
    entries = []
    for d in dns_sans:
        entries.append(x509.DNSName(validators.host(d, field="dns SAN")))
    for i in ip_sans:
        try:
            ip = ipaddress.ip_address(str(i).strip())
        except ValueError as exc:
            raise PKIError(f"invalid IP SAN {i!r}") from exc
        if int(ip) == 0:
            raise PKIError("0.0.0.0/:: is a bind wildcard, never a certificate SAN")
        entries.append(x509.IPAddress(ip))
    if not entries:
        raise PKIError("a server certificate requires at least one DNS or IP SAN")
    return x509.SubjectAlternativeName(entries)


# --------------------------------------------------------------------------- CA lifecycle

def _init_ca(paths: Paths, which: str, cn: str, *, days: int, force: bool,
             validity=None) -> dict:
    ensure_layout(paths)
    key_p, crt_p = _ca_paths(paths, which)
    if _exists(paths, crt_p) and not force:
        raise PKIError(f"{which} already exists — rotate (destructive) to replace it")
    key = _new_key()
    cert = _sign_ca(key, cn, days, validity=validity)
    _write_key(paths, key_p, key)
    _write_cert(paths, crt_p, cert)
    return _summary(cert, which)


def init_server_ca(paths: Paths, *, days: int = _CA_DAYS_DEFAULT, force: bool = False,
                   validity=None) -> dict:
    return _init_ca(paths, _SERVER_CA, "LHPC Server TLS CA", days=days, force=force,
                    validity=validity)


def init_client_ca(paths: Paths, *, days: int = _CA_DAYS_DEFAULT, force: bool = False,
                   validity=None) -> dict:
    return _init_ca(paths, _CLIENT_CA, "LHPC Client Auth CA", days=days, force=force,
                    validity=validity)


def rotate_server_ca(paths: Paths, *, days: int = _CA_DAYS_DEFAULT) -> dict:
    """DESTRUCTIVE: replace the server TLS CA. Existing server certs must be reissued."""
    return _init_ca(paths, _SERVER_CA, "LHPC Server TLS CA", days=days, force=True)


def rotate_client_ca(paths: Paths, *, days: int = _CA_DAYS_DEFAULT) -> dict:
    """DESTRUCTIVE: replace the client-auth CA and RESET the client inventory + CRL — every
    previously issued client certificate becomes untrusted (it was signed by the old CA)."""
    summary = _init_ca(paths, _CLIENT_CA, "LHPC Client Auth CA", days=days, force=True)
    _save_index(paths, _empty_index())
    # A fresh (empty) CRL under the new CA.
    build_crl(paths)
    return summary


# --------------------------------------------------------------------------- server leaf

def issue_server_cert(paths: Paths, *, dns_sans=(), ip_sans=(), days: int,
                      validity=None, keep_key: bool = False) -> dict:
    """Issue the HTTPS server leaf. `keep_key=True` re-signs the EXISTING key (normalisation: the
    browser sees the same CA and the same key, only new dates); the default mints a fresh one."""
    ca_key = _read_key(paths, _ca_paths(paths, _SERVER_CA)[0])
    ca_cert = _read_cert(paths, _ca_paths(paths, _SERVER_CA)[1])
    if ca_key is None or ca_cert is None:
        raise PKIError("server TLS CA not initialized")
    san = _build_san(dns_sans, ip_sans)         # raises on empty / invalid / 0.0.0.0
    key_p = _p(paths, _SERVER, "server.key")
    if keep_key:
        key = _read_key(paths, key_p)
        if key is None:
            raise PKIError("no server key to keep — issue a new certificate instead")
    else:
        key = _new_key()
    cert = _sign_leaf(ca_key, ca_cert, key, "lhpc-web", days,
                      eku=ExtendedKeyUsageOID.SERVER_AUTH, san=san, validity=validity)
    if not keep_key:
        _write_key(paths, key_p, key)
    _write_cert(paths, _p(paths, _SERVER, "server.crt"), cert)
    return _summary(cert, "server")


# --------------------------------------------------------------------------- client leaf + PKCS#12

def issue_client_cert(paths: Paths, label: str, *, days: int, passphrase: str) -> dict:
    label = validators.path_component(label, field="cert label")   # filename-safe device id
    if not passphrase:
        raise PKIError("a one-time bundle passphrase is required")
    ca_key = _read_key(paths, _ca_paths(paths, _CLIENT_CA)[0])
    ca_cert = _read_cert(paths, _ca_paths(paths, _CLIENT_CA)[1])
    if ca_key is None or ca_cert is None:
        raise PKIError("client-auth CA not initialized")
    idx = _load_index(paths)
    if any(e.get("label") == label and e.get("state") == "active" for e in idx["certs"]):
        raise PKIError(f"an active certificate labelled {label!r} already exists "
                       f"(reissue or revoke it first)")
    key = _new_key()
    cert = _sign_leaf(ca_key, ca_cert, key, label, days, eku=ExtendedKeyUsageOID.CLIENT_AUTH)
    # The private key leaves ONLY inside the encrypted PKCS#12 bundle; it is not retained
    # separately on disk.
    p12 = pkcs12.serialize_key_and_certificates(
        name=label.encode("utf-8"), key=key, cert=cert, cas=[ca_cert],
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode("utf-8")))
    export_p = _p(paths, _EXPORTS, f"{label}.p12")
    runtime_fs.atomic_write_bytes(paths, export_p, p12, mode=0o600)
    summary = _summary(cert, "client", label=label, state="active",
                       export=str(export_p), export_sha256=_sha256_hex(p12))
    idx["certs"].append(summary)
    _save_index(paths, idx)
    return summary


def reissue_client_cert(paths: Paths, label: str, *, days: int, passphrase: str) -> dict:
    """Revoke any active cert with this label (recording revocation) then issue a fresh one."""
    label = validators.path_component(label, field="cert label")
    idx = _load_index(paths)
    if any(e.get("label") == label and e.get("state") == "active" for e in idx["certs"]):
        revoke_client_cert(paths, label)
    return issue_client_cert(paths, label, days=days, passphrase=passphrase)


def _crl_revoked_serials(paths: Paths):
    """Authoritative set of hex serials the CRL revokes. Returns an empty set if the CRL is
    ABSENT, or `None` if the CRL is PRESENT but unreadable/malformed (uncertain)."""
    crl_p = _p(paths, _CLIENT_CA, "crl.pem")
    if not _exists(paths, crl_p):
        return set()
    try:
        raw = runtime_fs.read_text_regular(paths, crl_p)
        crl = x509.load_pem_x509_crl(raw.encode("ascii"))
    except (OSError, ValueError, PathContainmentError):
        return None
    return {format(r.serial_number, "x") for r in crl}


def list_client_certs(paths: Paths) -> list:
    """The client-cert inventory with a FAIL-SAFE truth source (correction): a certificate whose
    serial the CRL revokes is NEVER shown as ordinary 'active' — it becomes 'revocation-pending'
    even if the inventory still says active and the pending marker is missing/unreadable. The
    CRL is authoritative; the pending marker is secondary evidence (also honoured, and used when
    the CRL is present-but-unreadable)."""
    certs = list(_load_index(paths)["certs"])
    crl_serials = _crl_revoked_serials(paths)               # set | None (None = CRL unreadable)
    pending = {e.get("serial") for e in _load_pending(paths)}
    for c in certs:
        if c.get("state") != "active":
            continue                                        # committed 'revoked' stays revoked
        serial = c.get("serial")
        crl_says_revoked = (crl_serials is not None and serial in crl_serials)
        if crl_says_revoked or serial in pending:
            # CRL revokes it (primary), or pending evidence exists (secondary, incl. when the
            # CRL is present-but-unreadable) -> never ordinary active.
            c["state"] = "revocation-pending"
    return certs


def read_export(paths: Paths, label: str) -> bytes | None:
    """Return the raw `.p12` export bytes for a label, or None if absent/unsafe. The caller
    (web route) is responsible for the loopback-only gate before serving these bytes."""
    label = validators.path_component(label, field="cert label")
    from . import runtime_fs
    try:
        return runtime_fs.read_bytes(paths, _p(paths, _EXPORTS, f"{label}.p12"))
    except (FileNotFoundError, OSError):
        return None


def discard_export(paths: Paths, label: str) -> bool:
    """Delete a `.p12` export after the operator has transferred it. Revocation history and
    the inventory entry are preserved; only the exported bundle is removed."""
    label = validators.path_component(label, field="cert label")
    removed = True
    try:
        runtime_fs.unlink(paths, _p(paths, _EXPORTS, f"{label}.p12"))
    except FileNotFoundError:
        removed = False
    idx = _load_index(paths)
    changed = False
    for e in idx["certs"]:
        if e.get("label") == label and e.get("export"):
            e["export"] = None
            e["export_discarded"] = True
            changed = True
    if changed:
        _save_index(paths, idx)
    return removed


# --------------------------------------------------------------------------- revocation / CRL

def revoke_client_cert(paths: Paths, label: str) -> dict:
    """TRANSACTIONAL revocation. Builds a CANDIDATE inventory (cert marked revoked + CRL number
    bumped), writes the CRL from it FIRST, and commits the inventory ONLY if the CRL was
    generated and written successfully. If the CRL cannot be built/written, the inventory is
    left unchanged — the certificate stays ACTIVE and is NEVER shown as an enforceable
    'revoked'. (Revocation is *recorded* here; it is *effective* only once the proxy reloads
    the new CRL and the revoked cert is proven rejected — that proof lives in the service.)"""
    import copy
    label = validators.path_component(label, field="cert label")
    idx = _load_index(paths)
    if not any(e.get("label") == label and e.get("state") == "active" for e in idx["certs"]):
        raise PKIError(f"no active certificate labelled {label!r}")
    candidate = copy.deepcopy(idx)
    candidate["crl_number"] = int(candidate.get("crl_number", 0)) + 1
    chit = next(e for e in candidate["certs"]
                if e.get("label") == label and e.get("state") == "active")
    chit["state"] = "revoked"
    chit["revoked_at"] = _now().isoformat()
    try:
        _build_and_write_crl(paths, candidate)          # CRL first; may raise -> nothing committed
    except Exception as exc:
        raise PKIError(f"revocation NOT applied — CRL generation/write failed ({exc}); "
                       f"certificate {label!r} remains ACTIVE") from exc
    # CRL now revokes this serial. Commit the inventory; if THAT fails the CRL and inventory
    # would diverge, so record a durable pending marker — status then shows 'revocation-pending'
    # (never ordinary active) until a retry commits the inventory. The marker is BEST-EFFORT
    # secondary evidence: the CRL is the authoritative truth source (list_client_certs reads the
    # CRL directly), so even if the marker write also fails the cert is still shown pending.
    try:
        _save_index(paths, candidate)
    except Exception as exc:
        try:
            _add_pending(paths, chit)
        except Exception:
            pass                                        # CRL already revokes it — marker is optional
        raise PKIError(f"CRL updated but inventory save FAILED — certificate {label!r} is "
                       f"REVOCATION-PENDING (the CRL revokes it; the inventory was not "
                       f"committed). Retry the revoke to reconcile ({exc})") from exc
    _remove_pending(paths, chit["serial"])              # clean commit -> not pending
    return chit


def _build_and_write_crl(paths: Paths, index: dict, *, days: int = _CRL_DAYS_DEFAULT,
                         validity=None) -> None:
    """Build the CRL from `index`'s revoked certs (using `index['crl_number']`) and atomically
    write crl.pem. Raises PKIError/OSError on missing CA or build/write failure. Does NOT
    persist `index` — the caller commits it only after this succeeds."""
    ca_key = _read_key(paths, _ca_paths(paths, _CLIENT_CA)[0])
    ca_cert = _read_cert(paths, _ca_paths(paths, _CLIENT_CA)[1])
    if ca_key is None or ca_cert is None:
        raise PKIError("client-auth CA not initialized")
    now = _now()
    last_update, next_update = _validity(_dt.timedelta(minutes=1), days, validity)
    builder = (x509.CertificateRevocationListBuilder()
               .issuer_name(ca_cert.subject)
               .last_update(last_update)
               .next_update(next_update)
               .add_extension(x509.CRLNumber(int(index["crl_number"])), critical=False))
    for e in index["certs"]:
        if e.get("state") == "revoked":
            revoked = (x509.RevokedCertificateBuilder()
                       .serial_number(int(e["serial"], 16))
                       .revocation_date(now - _dt.timedelta(minutes=1))
                       .build())
            builder = builder.add_revoked_certificate(revoked)
    crl = builder.sign(ca_key, hashes.SHA256())
    runtime_fs.atomic_write(paths, _p(paths, _CLIENT_CA, "crl.pem"),
                            crl.public_bytes(serialization.Encoding.PEM).decode("ascii"),
                            mode=0o644)


def build_crl(paths: Paths, *, days: int = _CRL_DAYS_DEFAULT, validity=None) -> Path:
    idx = _load_index(paths)
    idx["crl_number"] = int(idx.get("crl_number", 0)) + 1
    _build_and_write_crl(paths, idx, days=days, validity=validity)
    _save_index(paths, idx)
    return _p(paths, _CLIENT_CA, "crl.pem")


# --------------------------------------------------------------------------- read-only status (cached evidence input)

def _ca_evidence(paths: Paths, which: str) -> dict:
    try:
        cert = _read_cert(paths, _ca_paths(paths, which)[1])
    except PKIError:
        return {"present": False, "error": "unreadable"}
    return {"present": True, **_summary(cert, which)} if cert else {"present": False}


def pki_status(paths: Paths) -> dict:
    """READ-ONLY PKI evidence for cached status (never generates anything). Distinct-purpose
    from `verify` — this just reads what exists on disk."""
    server = None
    try:
        server = _read_cert(paths, _p(paths, _SERVER, "server.crt"))
    except PKIError:
        server = None
    return {
        "server_ca": _ca_evidence(paths, _SERVER_CA),
        "client_ca": _ca_evidence(paths, _CLIENT_CA),
        "server_cert": ({"present": True, **_summary(server, "server")}
                        if server else {"present": False}),
        "clients": list_client_certs(paths),
        "crl_present": _exists(paths, _p(paths, _CLIENT_CA, "crl.pem")),
        # Minted under an unverified clock and not yet normalised (fixed provisional window).
        "provisional": provisional_pending(paths),
    }


def cas_are_distinct(paths: Paths) -> bool:
    """True iff both CAs exist with DIFFERENT keys/subjects (the two-trust-domain invariant)."""
    sc = _read_cert(paths, _ca_paths(paths, _SERVER_CA)[1])
    cc = _read_cert(paths, _ca_paths(paths, _CLIENT_CA)[1])
    if sc is None or cc is None:
        return False
    return sc.fingerprint(hashes.SHA256()) != cc.fingerprint(hashes.SHA256())

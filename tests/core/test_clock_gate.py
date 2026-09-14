"""The clock gate: unverified or unsynchronised time may not mutate the PKI.

A certificate outlives the boot that made it. An RTC-less Pi that comes up in 1970, or one whose
GPS handed it a rolled-back date, mints material that is "not yet valid" for years and locks the
operator out of the console the PKI exists to protect.

What is NOT claimed here, and must not be: that this detects a WRONG clock. LHPC reads the
kernel's synchronisation evidence, not a trusted date, so a source that is synchronised and wrong
passes. These tests pin the property that is actually enforced.
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

from cryptography import x509

from lhpc.core import pki
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_system import clock_verified
from lhpc.core.services import ControllerService


def _svc(tmp_path, *, synced=True, maxerror_us=1000, monkeypatch=None):
    """A service whose kernel clock state is whatever the test needs. `read_kernel_time_state`
    is the single seam: it is the only thing that reads the real clock."""
    from lhpc.core import service_system
    if monkeypatch is not None:
        state = None if synced is None else {"synced": synced, "maxerror_us": maxerror_us}
        monkeypatch.setattr(service_system, "read_kernel_time_state", lambda: state)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _init_pki(tmp_path):
    p = Paths(runtime_root=tmp_path)
    pki.init_server_ca(p, force=True)
    pki.init_client_ca(p, force=True)
    pki.build_crl(p)
    return p


# --- the predicate itself -------------------------------------------------------------------

def test_unsynchronised_clock_is_not_verified(tmp_path, monkeypatch):
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": False, "maxerror_us": 1000})
    ok, reason = clock_verified(FakeSystem().system.fs, tmp_path)
    assert not ok and "not synchronised" in reason


def test_a_synced_clock_with_a_huge_error_is_not_verified(tmp_path, monkeypatch):
    # "Synced" is not enough on its own: the kernel clamps maxerror upward when nothing is
    # steering the clock, and that is precisely the box this gate exists for.
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": True, "maxerror_us": 16_000_000})
    ok, reason = clock_verified(FakeSystem().system.fs, tmp_path)
    assert not ok and "estimated error" in reason


def test_unreadable_kernel_state_is_not_verified(tmp_path, monkeypatch):
    # Fail closed: "we could not tell" must never read as "fine".
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state", lambda: None)
    ok, reason = clock_verified(FakeSystem().system.fs, tmp_path)
    assert not ok and "unavailable" in reason


def test_a_plausible_but_pre_floor_clock_is_not_verified(tmp_path, monkeypatch):
    # A FLOOR alone cannot catch a future date, and a synced flag alone cannot catch a past one.
    # This is the past half: 2020 is a perfectly plausible-looking date that is still below the
    # independent lower bound.
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": True, "maxerror_us": 1000})
    ok, reason = clock_verified(FakeSystem().system.fs, tmp_path,
                                now=_dt.datetime(2020, 1, 1, tzinfo=_dt.UTC).timestamp())
    assert not ok and "before the earliest date" in reason


def test_a_good_clock_is_verified(tmp_path, monkeypatch):
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": True, "maxerror_us": 1000})
    ok, reason = clock_verified(FakeSystem().system.fs, tmp_path)
    assert ok and reason == ""


# --- the gate on every mutating path --------------------------------------------------------

_MUTATORS = ("init", "tls_renew", "cert_issue", "cert_reissue", "cert_revoke")


def _call(svc, which, *, accept=False):
    if which == "init":
        return svc.webserver_init(confirm=True, accept_unverified=accept)
    if which == "tls_renew":
        return svc.webserver_tls_renew(accept)
    if which == "cert_issue":
        return svc.webserver_cert_issue("dev", "pw-for-the-bundle", accept)
    if which == "cert_reissue":
        return svc.webserver_cert_reissue("dev", "pw-for-the-bundle", accept)
    if which == "cert_revoke":
        return svc.webserver_cert_revoke("dev", accept)
    raise AssertionError(which)


@pytest.mark.parametrize("which", _MUTATORS)
def test_every_mutating_path_refuses_an_unverified_clock(tmp_path, monkeypatch, which):
    _init_pki(tmp_path)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    res = _call(svc, which)
    assert not res.ok
    assert "not synchronised" in res.summary            # names the CLOCK, not a generic failure
    assert "Nothing was changed" in res.summary
    assert "--accept-unverified-clock" in res.summary   # and the exact way to proceed anyway


@pytest.mark.parametrize("which", _MUTATORS)
def test_the_override_lets_every_path_through(tmp_path, monkeypatch, which):
    _init_pki(tmp_path)
    if which in ("cert_reissue", "cert_revoke"):
        pki.issue_client_cert(Paths(runtime_root=tmp_path), "dev", days=30, passphrase="x" * 12)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    res = _call(svc, which, accept=True)
    assert "--accept-unverified-clock" not in res.summary   # it got past the gate


def test_a_refused_reissue_leaves_the_original_certificate_active(tmp_path, monkeypatch):
    # THE reason the gate sits before the call and not inside the signing routine: reissue
    # REVOKES the old certificate first, so a refusal any later leaves the operator with neither
    # a working certificate nor a replacement.
    p = _init_pki(tmp_path)
    before = pki.issue_client_cert(p, "dev", days=30, passphrase="x" * 12)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)

    assert not svc.webserver_cert_reissue("dev", "y" * 12).ok

    after = [c for c in pki.list_client_certs(p) if c["label"] == "dev"]
    assert len(after) == 1
    assert after[0]["serial"] == before["serial"]          # same certificate, not a replacement
    assert not after[0].get("revoked"), "the refusal revoked it anyway"
    crl = x509.load_pem_x509_crl(
        (tmp_path / "config" / "tls" / "client-ca" / "crl.pem").read_bytes())
    assert len(list(crl)) == 0, "the refusal wrote a revocation into the CRL"


def test_the_override_is_one_shot_and_not_remembered(tmp_path, monkeypatch):
    # Accepting the risk once must not arm every later operation. Nothing persists it.
    _init_pki(tmp_path)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    assert svc.webserver_cert_issue("first", "x" * 12, True).ok
    second = svc.webserver_cert_issue("second", "x" * 12)     # no flag this time
    assert not second.ok and "--accept-unverified-clock" in second.summary


def test_a_destructive_confirm_does_not_imply_accepting_the_clock(tmp_path, monkeypatch):
    # `--confirm-recreate` says "yes, destroy my CAs". It does NOT say "yes, date them from a
    # clock I cannot verify". Conflating the two is how an operator gets certificates they never
    # agreed to.
    _init_pki(tmp_path)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    res = svc.webserver_init(confirm=True)
    assert not res.ok and "--accept-unverified-clock" in res.summary


def test_a_refused_init_does_not_replace_the_existing_cas(tmp_path, monkeypatch):
    p = _init_pki(tmp_path)
    fp_before = pki.pki_status(p)["server_ca"]
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    assert not svc.webserver_init(confirm=True).ok
    assert pki.pki_status(p)["server_ca"] == fp_before


# --- automatic CRL repair --------------------------------------------------------------------

def _stale_crl(tmp_path, *, last_update, next_update):
    """Overwrite the CRL with one carrying chosen dates, keeping the real client CA."""
    from cryptography.hazmat.primitives import hashes, serialization
    ca_dir = tmp_path / "config" / "tls" / "client-ca"
    ca = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    key = serialization.load_pem_private_key((ca_dir / "ca.key").read_bytes(), password=None)
    crl = (x509.CertificateRevocationListBuilder()
           .issuer_name(ca.subject)
           .last_update(last_update)
           .next_update(next_update)
           .sign(private_key=key, algorithm=hashes.SHA256()))
    (ca_dir / "crl.pem").write_bytes(crl.public_bytes(serialization.Encoding.PEM))


def test_an_expired_crl_is_repaired_when_the_clock_is_verified(tmp_path, monkeypatch):
    _init_pki(tmp_path)
    now = _dt.datetime.now(_dt.UTC)
    _stale_crl(tmp_path, last_update=now - _dt.timedelta(days=60),
               next_update=now - _dt.timedelta(days=30))
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.crl_refresh_if_expired() is True
    crl = x509.load_pem_x509_crl(
        (tmp_path / "config" / "tls" / "client-ca" / "crl.pem").read_bytes())
    nu = crl.next_update_utc if hasattr(crl, "next_update_utc") else crl.next_update
    assert nu > now


def test_a_future_dated_crl_is_repaired(tmp_path, monkeypatch):
    # The case a pure nextUpdate check can never see: a CRL minted while the clock was wrong is
    # rejected by nginx the moment the clock is CORRECTED, and its nextUpdate is even further
    # out — so it would never expire and never be rebuilt. Total lockout, nothing revoked.
    _init_pki(tmp_path)
    now = _dt.datetime.now(_dt.UTC)
    _stale_crl(tmp_path, last_update=now + _dt.timedelta(days=365),
               next_update=now + _dt.timedelta(days=395))
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.crl_refresh_if_expired() is True
    crl = x509.load_pem_x509_crl(
        (tmp_path / "config" / "tls" / "client-ca" / "crl.pem").read_bytes())
    lu = crl.last_update_utc if hasattr(crl, "last_update_utc") else crl.last_update
    assert lu <= now, "still dated in the future"


def test_an_unverified_clock_leaves_the_crl_untouched(tmp_path, monkeypatch):
    # No operator is present in the watchdog, so there is nobody to accept the risk. Rebuilding
    # from a wrong clock would replace one broken CRL with another; waiting costs nothing,
    # because the watchdog calls again on every pass.
    _init_pki(tmp_path)
    now = _dt.datetime.now(_dt.UTC)
    _stale_crl(tmp_path, last_update=now - _dt.timedelta(days=60),
               next_update=now - _dt.timedelta(days=30))
    crl_path = tmp_path / "config" / "tls" / "client-ca" / "crl.pem"
    before = crl_path.read_bytes()
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    assert svc.crl_refresh_if_expired() is False
    assert crl_path.read_bytes() == before


def test_a_pending_marker_does_not_shortcut_an_invalid_crl(tmp_path, monkeypatch):
    # The ordering bug. The pending-reload early return ran BEFORE the CRL was ever loaded, so a
    # marker left by a failed reload retried forever against a file that was itself invalid and
    # could never be made valid by reloading it.
    _init_pki(tmp_path)
    now = _dt.datetime.now(_dt.UTC)
    _stale_crl(tmp_path, last_update=now - _dt.timedelta(days=60),
               next_update=now - _dt.timedelta(days=30))
    pending = tmp_path / "state" / "crl-reload-pending"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("reload-pending\n")
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.crl_refresh_if_expired() is True
    crl = x509.load_pem_x509_crl(
        (tmp_path / "config" / "tls" / "client-ca" / "crl.pem").read_bytes())
    nu = crl.next_update_utc if hasattr(crl, "next_update_utc") else crl.next_update
    assert nu > now, "the marker short-circuited the rebuild again"


def test_repair_preserves_the_ca_and_the_revoked_serials(tmp_path, monkeypatch):
    # A repair that quietly forgot a revocation would un-revoke a certificate the operator had
    # deliberately killed — a far worse outcome than the expired CRL it was fixing.
    p = _init_pki(tmp_path)
    pki.issue_client_cert(p, "doomed", days=30, passphrase="x" * 12)
    pki.revoke_client_cert(p, "doomed")
    ca_before = pki.pki_status(p)["client_ca"]
    revoked_before = {r.serial_number for r in x509.load_pem_x509_crl(
        (tmp_path / "config" / "tls" / "client-ca" / "crl.pem").read_bytes())}
    assert revoked_before

    now = _dt.datetime.now(_dt.UTC)
    _stale_crl(tmp_path, last_update=now - _dt.timedelta(days=60),
               next_update=now - _dt.timedelta(days=30))
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.crl_refresh_if_expired() is True

    assert pki.pki_status(p)["client_ca"] == ca_before
    revoked_after = {r.serial_number for r in x509.load_pem_x509_crl(
        (tmp_path / "config" / "tls" / "client-ca" / "crl.pem").read_bytes())}
    assert revoked_after == revoked_before


# --- remote exposure: the paths the first implementation missed -------------------------------
# Both exposure entry points add the host IP SAN and REISSUE the server certificate, so they date
# PKI material exactly as `tls-renew` does. The first round of this work gated the five obvious
# operations and missed these two; the audit caught it. The gate has to run before the CONFIG
# write, not merely before the reissue — a saved exposure whose certificate was refused is a
# half-applied change, which is worse than a refused one.

def _server_cert_serial(tmp_path):
    from cryptography import x509
    p = tmp_path / "config" / "tls" / "server" / "server.crt"
    return x509.load_pem_x509_certificate(p.read_bytes()).serial_number if p.exists() else None


def test_expose_refuses_an_unverified_clock_and_changes_nothing(tmp_path, monkeypatch):
    _init_pki(tmp_path)
    pki.issue_server_cert(Paths(runtime_root=tmp_path), dns_sans=("box.lan",),
                          ip_sans=(), days=30)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    bind_before = svc.config().webserver.bind
    serial_before = _server_cert_serial(tmp_path)

    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)

    assert not res.ok and "--accept-unverified-clock" in res.summary
    svc._invalidate_config()
    assert svc.config().webserver.bind == bind_before, "exposure was saved despite the refusal"
    assert not svc.config().webserver.remote_exposed
    assert _server_cert_serial(tmp_path) == serial_before, "the server certificate was reissued"


def test_configure_apply_with_a_remote_bind_refuses_an_unverified_clock(tmp_path, monkeypatch):
    _init_pki(tmp_path)
    pki.issue_server_cert(Paths(runtime_root=tmp_path), dns_sans=("box.lan",),
                          ip_sans=(), days=30)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    sans_before = tuple(svc.config().webserver.ip_sans)
    serial_before = _server_cert_serial(tmp_path)

    res = svc.webserver_configure_apply(bind="0.0.0.0", confirm=True,
                                        allowed_cidrs=["192.168.0.0/24"])

    assert not res.ok and "--accept-unverified-clock" in res.summary
    svc._invalidate_config()
    cfg = svc.config().webserver
    assert cfg.bind != "0.0.0.0" and not cfg.remote_exposed
    assert tuple(cfg.ip_sans) == sans_before, "an IP SAN was added despite the refusal"
    assert _server_cert_serial(tmp_path) == serial_before


def test_a_loopback_configure_apply_is_not_gated(tmp_path, monkeypatch):
    # Only EXPOSURE reissues the certificate. A loopback settings change dates nothing, so the
    # gate must not block ordinary configuration on a box whose clock is not yet set — that would
    # be a lockout of its own.
    _init_pki(tmp_path)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    res = svc.webserver_configure_apply(port=8443)
    assert "--accept-unverified-clock" not in res.summary


def test_the_override_lets_exposure_through(tmp_path, monkeypatch):
    _init_pki(tmp_path)
    pki.issue_server_cert(Paths(runtime_root=tmp_path), dns_sans=("box.lan",),
                          ip_sans=(), days=30)
    svc = _svc(tmp_path, synced=False, monkeypatch=monkeypatch)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True, accept_unverified=True)
    assert "--accept-unverified-clock" not in res.summary
    svc._invalidate_config()
    assert svc.config().webserver.remote_exposed


# --- the leaf backdate ------------------------------------------------------------------------

def test_leaves_are_backdated_a_day_and_the_cas_are_not(tmp_path):
    # A minute is too tight to absorb an ordinary clock difference at VALIDATION time: the
    # browser checking the certificate may be off from the Pi that signed it, and "not yet valid"
    # is indistinguishable from broken to the person locked out. The CAs keep the minute — they
    # are signed once and live for years, so widening buys nothing the leaves do not get.
    p = _init_pki(tmp_path)
    before = _dt.datetime.now(_dt.UTC)
    leaf = pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=30)
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server" / "server.crt").read_bytes())
    nvb = cert.not_valid_before_utc if hasattr(cert, "not_valid_before_utc") else cert.not_valid_before
    if nvb.tzinfo is None:
        nvb = nvb.replace(tzinfo=_dt.UTC)
    assert _dt.timedelta(hours=23) < (before - nvb) < _dt.timedelta(hours=25), leaf["serial"]

    ca = x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server-ca" / "ca.crt").read_bytes())
    ca_nvb = ca.not_valid_before_utc if hasattr(ca, "not_valid_before_utc") else ca.not_valid_before
    if ca_nvb.tzinfo is None:
        ca_nvb = ca_nvb.replace(tzinfo=_dt.UTC)
    assert (before - ca_nvb) < _dt.timedelta(minutes=5), "the CA backdate was widened too"


# --- the dependency-panel copybox -------------------------------------------------------------
# The other half of the NMEA policy. The standalone bootstrap keeps its own shell pre-flight
# because it may run before lhpc exists; the running controller does not re-derive any of that,
# it already knows its effective GPS source. One policy, two boundaries.
#
# The first implementation of this feature defined the copybox helper and never wired it to a
# caller, while the report claimed the surface was closed. These tests exist so that cannot
# recur silently.

def test_the_copybox_is_refused_on_a_direct_nmea_box(tmp_path):
    from lhpc.core import deps
    offer, text = deps.time_source_offer("nmea", "op")
    assert offer is False
    assert "apt install" not in text, "an installable command was handed to an NMEA box"
    assert "UBX" in text and "source to gpsd" in text     # names the reason and the way out


def test_the_copybox_is_offered_for_gpsd_and_auto(tmp_path):
    from lhpc.core import deps
    for source in ("gpsd", "auto", ""):
        offer, text = deps.time_source_offer(source, "op")
        assert offer is True, source
        assert "apt install -y chrony gpsd" in text


def test_the_copybox_warns_before_it_installs_not_after(tmp_path):
    # Ordering is the whole point: gpsd with Debian's USBAUTO claims the receiver the moment it
    # is installed, and on a u-blox that is irreversible from software. A caution printed after
    # the apt line would be advice about something that already happened.
    from lhpc.core import deps
    text = deps.time_source_install_cmd("op")
    assert text.index("source = nmea") < text.index("apt install")


def test_the_dependency_panel_carries_the_time_source_entry(tmp_path):
    # The wiring the first round claimed and did not do.
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    groups = {g["title"]: g for g in svc.controller_system_deps()}
    assert "Time source" in groups
    dep = groups["Time source"]["deps"][0]
    assert dep["bootstrap"] is False      # it has its own scaffold; never fold it in twice
    assert not dep["required"]            # a box with another time daemon is not broken
    assert "apt install -y chrony gpsd" in dep["install"]


def test_file_timestamps_never_authorise_issuance_or_repair(tmp_path, monkeypatch):
    """Filesystem mtimes are NOT an input to the clock gate, and this pins that they never become
    one again.

    An earlier version used the newest mtime among the runtime and PKI paths as a lower bound, on
    the reasoning that the clock cannot legitimately read earlier than something this box has
    written. That is circular — those timestamps came from the same possibly-wrong clock. The
    audit reproduced the deadlock it creates: a CRL minted while the clock was a year fast has a
    file mtime a year in the future, so the floor rejects the CORRECTED time, and CRL repair
    (which requires a verified clock) then refuses to replace the very file locking the operator
    out. No operator override helps, because the watchdog is unattended.

    A floor is only useful if it is independent of the thing being checked. `_NOT_BEFORE` is.
    """
    from lhpc.core import service_system
    from lhpc.core.probes.backends import RealFileSystem
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": True, "maxerror_us": 1000})
    _init_pki(tmp_path)

    future = _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=365)
    for rel in ("config", "config/tls", "config/tls/client-ca", "config/tls/client-ca/crl.pem",
                "config/tls/server/server.crt", "state", "logs"):
        target = tmp_path / rel
        if target.exists():
            os.utime(target, (future.timestamp(), future.timestamp()))

    # A corrected, synchronised clock must be accepted even though everything on disk is
    # timestamped a year ahead of it.
    ok, reason = clock_verified(RealFileSystem(), tmp_path)
    assert ok, f"future file timestamps blocked the corrected clock: {reason}"


def test_a_future_dated_crl_is_repaired_despite_future_file_times(tmp_path, monkeypatch):
    """The end-to-end version of the same deadlock: the repair must actually run."""
    from lhpc.core.probes.backends import RealFileSystem
    _init_pki(tmp_path)
    now = _dt.datetime.now(_dt.UTC)
    future = now + _dt.timedelta(days=365)
    _stale_crl(tmp_path, last_update=future, next_update=future + _dt.timedelta(days=30))
    crl = tmp_path / "config" / "tls" / "client-ca" / "crl.pem"
    for path in (crl, crl.parent):
        os.utime(path, (future.timestamp(), future.timestamp()))

    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    real = RealFileSystem()
    monkeypatch.setattr(type(svc._system.fs), "mtime", lambda self, path: real.mtime(path))
    before = crl.read_bytes()
    assert svc.crl_refresh_if_expired() is True
    assert crl.read_bytes() != before, "the future-dated CRL was never replaced"


def test_an_unreadable_gps_config_refuses_the_copybox(tmp_path):
    """Uncertainty about receiver ownership fails safe here exactly as it does in the bootstrap
    pre-flight. The previous version returned "" for a failed read, which is the same value as an
    ABSENT [gps] section — legitimately `auto` — so it rendered an installable `apt install` for
    a box whose configuration it had just failed to read. The comment justifying that claimed the
    standalone script was an independent second guard; it is not, because the copybox runs apt
    directly rather than invoking the script."""
    from lhpc.core import deps
    offer, text = deps.time_source_offer(deps.TIME_SOURCE_SOURCE_UNKNOWN, "op")
    assert offer is False and "apt install" not in text
    assert "could not read" in text
    # and an ABSENT section is still the fresh-image case, still offered
    assert deps.time_source_offer("", "op")[0] is True


def test_a_failed_config_read_reaches_the_panel_as_unknown(tmp_path, monkeypatch):
    from lhpc.core import deps
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "config",
                        lambda self: (_ for _ in ()).throw(RuntimeError("unreadable")))
    assert svc._gps_source_for_offer() == deps.TIME_SOURCE_SOURCE_UNKNOWN


def test_a_half_finished_setup_does_not_read_as_satisfied(tmp_path, monkeypatch):
    """The drop-in is written early. A setup that failed after that — chrony refusing to start,
    the prefer edit failing — would leave the file behind, and the panel would report the
    dependency satisfied and hide the copybox offering the repair. Both ends of the setup must be
    visible before it counts."""
    from lhpc.core import deps
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    seen = {deps.CHRONY_DROPIN_PATH}          # drop-in only: the half-finished state
    monkeypatch.setattr(type(svc._system.fs), "exists", lambda self, p: p in seen, raising=False)
    assert svc._time_source_present() is False
    seen.add(deps.CLOCK_EPOCH_PATH)           # the boot floor survives failed re-runs: NOT a witness
    assert svc._time_source_present() is False
    seen.add(deps.TIME_SOURCE_STAMP_PATH)     # the per-run witness, written only after the verdict
    assert svc._time_source_present() is True

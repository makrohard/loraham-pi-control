"""A2: the server certificate's 825-day cap and the expiry display.

The cap: Apple requires "825 days or fewer (as expressed in the NotBefore and NotAfter fields)" and
counts, for its later 398-day rule, notBefore through notAfter inclusive with a day of 86,400 s.
Applied to the 825 days, a leaf's whole span may be at most 825 * 86,400 - 1 s. Before the cap LHPC
issued 825 days plus the one-day backdate: 826 days, over Apple's stated limit.

What is claimed, exactly: every server leaf OUTSIDE the fixed provisional window, on all five issue
paths, is capped. A leaf IN the provisional window (a PKI made without a verified clock and not yet
normalised) is NOT capped; since C13 one is issued only while the clock is unverified. The display marks it
as over the cap while it is provisional and names the manual way: `lhpc webserver tls-renew` under a verified
clock (which issues outside the window whatever the marker says), then `lhpc webserver apply`.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from cryptography import x509

from lhpc.core import pki
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

MAX_SPAN_S = 825 * 86_400 - 1          # 71,279,999 s
DAY = _dt.timedelta(days=1)


def _svc(tmp_path, monkeypatch, *, synced=True):
    """A service whose kernel clock state is what the test needs (the clock gate's seam, as in
    test_clock_gate)."""
    from lhpc.core import service_system
    monkeypatch.setattr(service_system, "read_kernel_time_state",
                        lambda: {"synced": synced, "maxerror_us": 1000})
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _init_pki(tmp_path):
    p = Paths(runtime_root=tmp_path)
    pki.init_server_ca(p, force=True)
    pki.init_client_ca(p, force=True)
    pki.build_crl(p)
    return p


def _leaf(tmp_path):
    return x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server" / "server.crt").read_bytes())


def _span_s(cert) -> int:
    return int((cert.not_valid_after_utc - cert.not_valid_before_utc).total_seconds())


def _at(monkeypatch, when):
    """Move the display's clock (pki._now) to `when`; issuing is done before this is called."""
    monkeypatch.setattr(pki, "_now", lambda: when)


# --- (a) the cap ---------------------------------------------------------------------------------

@pytest.mark.parametrize("days", [1, 823, 824, 825, 3650])
def test_server_leaf_span_is_at_most_825_days_by_apple_counting(tmp_path, days):
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=days)
    span = _span_s(_leaf(tmp_path))
    assert span <= MAX_SPAN_S
    if days >= 825:
        assert span == MAX_SPAN_S                    # 825 used to give 71,366,400 s (826 days)
    if days == 1:
        assert span == 2 * 86_400                    # the plain backdate + one day, unclamped


def test_client_span_is_unchanged(tmp_path):
    # Apple's pages cited in pki.py state no limit for client certificates: they keep 825 days
    # plus the backdate.
    p = _init_pki(tmp_path)
    s = pki.issue_client_cert(p, "laptop", days=825, passphrase="pw-for-the-test")
    nb = _dt.datetime.fromisoformat(s["not_before"])
    na = _dt.datetime.fromisoformat(s["not_after"])
    assert int((na - nb).total_seconds()) == 826 * 86_400


def _path_init(svc, tmp_path, monkeypatch):
    res = svc.webserver_init(dns_sans=["box.lan"], ip_sans=[], confirm=True)
    assert res.ok, res.summary


def _path_replacement(svc, tmp_path, monkeypatch):
    # C04's consented replacement: exposing to a LAN whose address is not yet a SAN.
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.0.2.50")
    return svc.webserver_expose(["192.0.2.0/24"], confirm=True, replace_certificate=True)


def _path_tls_renew(svc, tmp_path, monkeypatch):
    from lhpc.core import config as _config
    _config.save_webserver_config(svc._paths, dns_sans=["box.lan"])     # tls-renew reads the SANs
    svc._invalidate_config()
    res = svc.webserver_tls_renew()
    assert res.ok, res.summary


def _path_normalisation(svc, tmp_path, monkeypatch):
    assert svc.pki_clock_normalise() == "normalised"


def _path_wlan_join(svc, tmp_path, monkeypatch):
    from lhpc.core import config as _config
    from lhpc.core.services import ActionResult
    _config.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True,
                                  allowed_cidrs=["198.51.100.0/24"],
                                  access_mode="local-open-remote-auth")
    monkeypatch.setattr(ControllerService, "webserver_apply",
                        lambda self: ActionResult(True, "applied"))
    state, _cmd, _msg = svc._network_extend_console("203.0.113.0/24", ip="203.0.113.42")
    assert state == "applied"


_PATHS = {"init": _path_init, "replacement": _path_replacement, "tls-renew": _path_tls_renew,
          "normalisation": _path_normalisation, "wlan-join": _path_wlan_join}


@pytest.mark.parametrize("path", sorted(_PATHS))
def test_every_issue_path_caps_a_leaf_outside_the_provisional_window(tmp_path, monkeypatch, path):
    if path == "normalisation":
        # a box commissioned with no verified clock, then time arrives
        res = _svc(tmp_path, monkeypatch, synced=False).webserver_init(
            dns_sans=["box.lan"], ip_sans=[], confirm=True)
        assert res.ok, res.summary
    elif path != "init":
        _init_pki(tmp_path)
        pki.issue_server_cert(Paths(runtime_root=tmp_path), dns_sans=("box.lan",),
                              ip_sans=(), days=30)
    serial_before = _leaf(tmp_path).serial_number if path != "init" else None
    svc = _svc(tmp_path, monkeypatch, synced=True)
    _PATHS[path](svc, tmp_path, monkeypatch)
    leaf = _leaf(tmp_path)
    assert leaf.serial_number != serial_before, "the path did not issue a new leaf"
    assert _span_s(leaf) == MAX_SPAN_S, f"{path}: span {_span_s(leaf)} s"


def test_provisional_validity_is_not_clamped_and_shown_over_the_cap(tmp_path):
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825,
                          validity=pki.PROVISIONAL_VALIDITY)
    leaf = _leaf(tmp_path)
    assert (leaf.not_valid_before_utc, leaf.not_valid_after_utc) == pki.PROVISIONAL_VALIDITY
    sc = pki.pki_status(p)["server_cert"]
    assert sc["expiry"]["provisional"] and sc["expiry"]["over_cap"]
    assert ("provisional: over the Apple 825-day cap; to replace it: lhpc webserver tls-renew under a "
            "verified clock, then lhpc webserver apply") in sc["expiry_text"]


def test_a_replacement_while_the_pki_is_provisional_is_capped_under_a_verified_clock(
        tmp_path, monkeypatch):
    # C13: the window is decided by the clock, as `init` does: a box commissioned without a verified
    # clock, whose clock is verified now but not yet normalised, gets a replacement dated from the
    # clock and capped, not shown as provisional.
    res = _svc(tmp_path, monkeypatch, synced=False).webserver_init(
        dns_sans=["box.lan"], ip_sans=[], confirm=True)
    assert res.ok, res.summary
    serial_before = _leaf(tmp_path).serial_number
    svc = _svc(tmp_path, monkeypatch, synced=True)
    assert pki.provisional_pending(Paths(runtime_root=tmp_path))
    res = _path_replacement(svc, tmp_path, monkeypatch)
    leaf = _leaf(tmp_path)
    assert leaf.serial_number != serial_before, "the replacement did not issue a new leaf"
    assert (leaf.not_valid_before_utc, leaf.not_valid_after_utc) != pki.PROVISIONAL_VALIDITY
    assert _span_s(leaf) == MAX_SPAN_S
    said = "\n".join([res.summary, *res.details])        # the operator's text: no window named
    assert "provisional window" not in said
    assert "provisional" not in pki.pki_status(Paths(runtime_root=tmp_path))["server_cert"]["expiry_text"]


def test_tls_renew_under_a_verified_clock_leaves_the_window_while_the_marker_is_set(
        tmp_path, monkeypatch):
    # The command the provisional line names: tls-renew reads no marker, so under a verified clock
    # it issues a capped leaf outside the window even while the PKI is still marked provisional.
    from lhpc.core import config as _config
    res = _svc(tmp_path, monkeypatch, synced=False).webserver_init(
        dns_sans=["box.lan"], ip_sans=[], confirm=True)
    assert res.ok, res.summary
    svc = _svc(tmp_path, monkeypatch, synced=True)
    assert pki.provisional_pending(Paths(runtime_root=tmp_path))
    _config.save_webserver_config(svc._paths, dns_sans=["box.lan"])
    svc._invalidate_config()
    res = svc.webserver_tls_renew()
    assert res.ok, res.summary
    leaf = _leaf(tmp_path)
    assert (leaf.not_valid_before_utc, leaf.not_valid_after_utc) != pki.PROVISIONAL_VALIDITY
    assert _span_s(leaf) == MAX_SPAN_S
    assert "provisional" not in pki.pki_status(Paths(runtime_root=tmp_path))["server_cert"]["expiry_text"]


def test_an_existing_826_day_leaf_is_reported_over_the_cap(tmp_path):
    p = _init_pki(tmp_path)
    now = pki._now().replace(microsecond=0)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825,
                          validity=(now - DAY, now + 825 * DAY))      # the pre-cap dates
    text = pki.pki_status(p)["server_cert"]["expiry_text"]
    assert "over the Apple 825-day cap: run lhpc webserver tls-renew, then lhpc webserver apply" in text


# --- (c) the one helper ----------------------------------------------------------------------------

@pytest.mark.parametrize("days_left, server_state, client_state", [
    (61, "ok", "ok"), (59, "ok", "soon"), (29, "soon", "soon"), (-1, "expired", "expired")])
def test_the_helper_at_the_boundaries(days_left, server_state, client_state):
    na = _dt.datetime(2030, 1, 1, tzinfo=_dt.UTC)
    nb = na - 800 * DAY
    now = na - days_left * DAY - _dt.timedelta(minutes=1)
    s = pki.expiry_view(nb, na, warn_days=pki.SERVER_WARN_DAYS, now=now)
    c = pki.expiry_view(nb, na, warn_days=pki.CLIENT_WARN_DAYS, now=now)
    assert s["days_left"] == days_left and (s["state"], c["state"]) == (server_state, client_state)
    assert not s["over_cap"] and not s["provisional"]


def test_the_helper_at_exactly_the_warning_and_the_end():
    na = _dt.datetime(2030, 1, 1, tzinfo=_dt.UTC)
    nb = na - 800 * DAY
    assert pki.expiry_view(nb, na, warn_days=30, now=na - 30 * DAY)["state"] == "ok"
    assert pki.expiry_view(nb, na, warn_days=30, now=na - 30 * DAY + _dt.timedelta(seconds=1)
                           )["state"] == "soon"
    assert pki.expiry_view(nb, na, warn_days=30, now=na)["state"] == "expired"


@pytest.mark.parametrize("days_left, doctor_ok, words", [
    (61, True, "(in 61 days)"), (59, True, "(in 59 days)"),
    (29, True, "(in 29 days): renew it: lhpc webserver tls-renew, then lhpc webserver apply"),
    (-1, False, "EXPIRED on")])
def test_status_line_and_doctor_for_the_server_certificate(tmp_path, monkeypatch, days_left,
                                                           doctor_ok, words):
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825)
    na = _leaf(tmp_path).not_valid_after_utc
    svc = _svc(tmp_path, monkeypatch)
    baseline = svc.doctor()                          # the fake box's verdict with a fresh leaf
    _at(monkeypatch, na - days_left * DAY - _dt.timedelta(minutes=1))
    line = svc.webserver_monitor().data["pki"]["server_cert"]["expiry_text"]
    assert words in line
    d = svc.doctor()
    assert any(words in x and "server certificate" in x for x in d.details)
    if doctor_ok:
        assert d.ok is baseline.ok                   # information only
    else:
        assert d.ok is False
        if baseline.ok:                              # no other cause outranks it in the summary
            assert "server certificate has expired" in d.summary


def test_doctor_verdict_does_not_change_for_near_end_certificates(tmp_path, monkeypatch):
    # 29 days left on the server leaf and a client about to expire: information, the verdict unchanged.
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825)
    pki.issue_client_cert(p, "laptop", days=825, passphrase="pw-for-the-test")
    svc = _svc(tmp_path, monkeypatch)
    ok_before = svc.doctor().ok
    na = _leaf(tmp_path).not_valid_after_utc
    _at(monkeypatch, na - 29 * DAY - _dt.timedelta(minutes=1))
    assert svc.doctor().ok is ok_before


def test_a_client_near_its_end_is_marked_in_the_list_and_doctor_and_a_revoked_one_is_not(
        tmp_path, monkeypatch):
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825)
    act = pki.issue_client_cert(p, "laptop", days=825, passphrase="pw-for-the-test")
    pki.issue_client_cert(p, "old-phone", days=825, passphrase="pw-for-the-test")
    pki.revoke_client_cert(p, "old-phone")
    svc = _svc(tmp_path, monkeypatch)
    _at(monkeypatch, _dt.datetime.fromisoformat(act["not_after"]) - 59 * DAY
        - _dt.timedelta(minutes=1))
    certs = {c["label"]: c for c in svc.webserver_cert_list().data["certs"]}
    assert certs["laptop"]["expiry_mark"] == "expires in 59 days"
    assert certs["old-phone"]["expiry_mark"] == ""
    details = "\n".join(svc.doctor().details)
    assert "client certificate 'laptop': expires in 59 days" in details
    assert "lhpc webserver cert reissue laptop" in details
    assert "old-phone" not in details


def test_no_mark_with_more_than_60_days_left(tmp_path, monkeypatch):
    p = _init_pki(tmp_path)
    act = pki.issue_client_cert(p, "laptop", days=825, passphrase="pw-for-the-test")
    _at(monkeypatch, _dt.datetime.fromisoformat(act["not_after"]) - 61 * DAY
        - _dt.timedelta(minutes=1))
    assert pki.client_certs_with_expiry(p)[0]["expiry_mark"] == ""


# --- the console panel -----------------------------------------------------------------------------

def test_the_console_panel_shows_the_line_and_not_the_old_renewal_phrase(web, tmp_path, monkeypatch):
    p = _init_pki(tmp_path)
    pki.issue_server_cert(p, dns_sans=("box.lan",), ip_sans=(), days=825)
    act = pki.issue_client_cert(p, "laptop", days=825, passphrase="pw-for-the-test")
    _at(monkeypatch, _dt.datetime.fromisoformat(act["not_after"]) - 59 * DAY
        - _dt.timedelta(minutes=1))
    body = web().get("/stacks").get_data(as_text=True)
    line = pki.pki_status(p)["server_cert"]["expiry_text"]
    assert f"Server certificate: <code>{line}</code>" in body
    assert "expires in 59 days" in body                            # the client list's mark
    assert "renewed automatically" not in body                     # the old phrases are replaced


def test_the_console_panel_marks_a_provisional_certificate(web, tmp_path, monkeypatch):
    res = _svc(tmp_path, monkeypatch, synced=False).webserver_init(
        dns_sans=["box.lan"], ip_sans=[], confirm=True)
    assert res.ok, res.summary
    body = web().get("/stacks").get_data(as_text=True)
    assert ("provisional: over the Apple 825-day cap; to replace it: lhpc webserver tls-renew under a "
            "verified clock, then lhpc webserver apply") in body
    assert "renewed automatically" not in body

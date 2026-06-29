"""1.3 — daemon live-setting boundaries: strict FREQ validation (SX126x domain),
band validation at every boundary (no arbitrary socket paths), and a truthful
confirmed-vs-sent distinction (never claim "applied" for an unconfirmable radio param)."""

import pytest

from lhpc.core import daemon_control as dc
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


# --- FREQ validation ---------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "nan", "NaN", "inf", "-inf", "+inf",          # non-finite
    "1e9", "1E3", "433e0",                          # exponent tricks
    " 433", "433 ", "4 33", "\t433",               # whitespace edge/embedded
    "+433", "-433",                                # signs
    "0", "-1",                                      # non-positive
    "100", "149.9", "960.1", "2400",              # outside SX126x [150,960]
    "", "abc", "43x",                              # junk
])
def test_freq_rejects_bad(bad):
    assert dc.validate_set("FREQ", bad) is not None


@pytest.mark.parametrize("good", ["433.775", "868.0", "150", "960", "433", "915.0"])
def test_freq_accepts_valid_domain(good):
    assert dc.validate_set("FREQ", good) is None


# --- band validation at the boundary ----------------------------------------

@pytest.mark.parametrize("bad", ["", "999", "433f", "../../etc/x", "433\n", "both"])
def test_conf_socket_refuses_invalid_band(bad):
    with pytest.raises(dc.InvalidBand):
        dc.conf_socket(bad)
    assert dc.is_valid_band(bad) is False


def test_read_view_invalid_band_is_not_reachable():
    v = dc.read_view(FakeSystem().system, "../evil")
    assert not v.reachable and not v.ready and "invalid band" in v.error


def test_apply_set_invalid_band_is_typed_error():
    ok, confirmed, detail = dc.apply_set(FakeSystem().system, "999", "TXMODE", "DIRECT")
    assert not ok and not confirmed and "invalid band" in detail


def test_daemon_set_service_validates_band(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.daemon_set("../../etc", "TXMODE", "DIRECT", apply=True)
    assert not r.ok and "Invalid band" in r.summary


# --- confirmed vs sent-but-unconfirmable ------------------------------------

def test_confirmable_key_reports_applied(tmp_path):
    sys = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS TXMODE=DIRECT\n"}).system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    r = svc.daemon_set("433", "TXMODE", "DIRECT", apply=True)
    assert r.ok and r.data.get("confirmed") is True and "applied (confirmed)" in r.summary


def test_unconfirmable_radio_param_is_sent_not_applied(tmp_path):
    # FREQ is accepted by the chip but not echoed by any GET: it must be "SENT
    # (unconfirmed)", never "applied", and the confirmed flag must be False.
    sys = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS TXMODE=MANAGED\n"}).system
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    r = svc.daemon_set("433", "FREQ", "433.775", apply=True)
    assert r.ok and r.data.get("confirmed") is False
    assert "applied" not in r.summary.lower() and "SENT (unconfirmed)" in r.summary


def test_is_confirmable_matches_verify_table():
    assert dc.is_confirmable("TXMODE") and dc.is_confirmable("CADIDLE")
    assert not dc.is_confirmable("FREQ") and not dc.is_confirmable("SF")

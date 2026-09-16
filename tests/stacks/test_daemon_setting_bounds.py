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


# --- POWER: the range depends on the chip fitted -----------------------------
#
# Daemon 1.0.0 (`config_policy.cpp`, config_policy_power_valid_family) enforces 2..17 on SX127x
# and 0..20 on SX1262. Below 2 the SX127x driver would transmit on RFO instead of the antenna's
# PA_BOOST pin; 18/19 RadioLib rejects; 20 is declined for its duty-cycle contract. Validating
# against the union would let the operator send a value the daemon will refuse.

@pytest.mark.parametrize("value", ["0", "1", "18", "19", "20", "-1"])
def test_power_outside_the_sx127x_window_is_refused(value):
    assert dc.validate_set("POWER", value, "sx127x") is not None


@pytest.mark.parametrize("value", ["2", "10", "17"])
def test_power_inside_the_sx127x_window_is_accepted(value):
    assert dc.validate_set("POWER", value, "sx127x") is None


@pytest.mark.parametrize("value", ["0", "2", "17", "20"])
def test_sx1262_keeps_the_full_range(value):
    assert dc.validate_set("POWER", value, "sx1262") is None


@pytest.mark.parametrize("family", ["", "unknown-board", "SX127X"])
def test_unknown_family_narrows_nothing(family):
    # "" means "the family is not known here": validate the union and let the daemon, which
    # knows its own hardware, issue the refusal. A board must never inherit another's limits
    # from a typo, so the match is exact and case-sensitive.
    assert dc.validate_set("POWER", "0", family) is None
    assert dc.validate_set("POWER", "20", family) is None
    assert dc.validate_set("POWER", "21", family) is not None


def test_only_power_is_family_dependent():
    # Every other numeric key must answer the same range for every family — otherwise a caller
    # that omits the family silently changes behaviour for a key nobody meant to make chip-specific.
    for key in ("CADRSSI", "CADWAIT", "CADIDLE", "CADPOLL", "SF", "CR", "PREAMBLE"):
        assert dc.int_range(key, "sx127x") == dc.int_range(key, "sx1262") == dc.int_range(key)


def test_power_range_matches_the_daemons_own_policy():
    assert dc.int_range("POWER", "sx127x") == (2, 17)
    assert dc.int_range("POWER", "sx1262") == (0, 20)
    assert dc.int_range("POWER") == (0, 20)


# --- the family reaches the gate from the configured board -------------------

@pytest.mark.parametrize("setup,band,rejected", [
    ("loraham", "433", "20"),
    ("loraham", "868", "0"),
    ("uputronics", "433", "0"),
    ("uputronics", "868", "20"),
])
def test_service_refuses_out_of_range_power_for_an_sx127x_board(tmp_path, setup, band, rejected):
    from lhpc.core import config as cfgmod
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    svc = ControllerService(system=FakeSystem().system, paths=p)
    assert svc.chip_family_for_band(band) == "sx127x"
    r = svc.daemon_set(band, "POWER", rejected, apply=True)
    assert not r.ok and "POWER" in r.summary


@pytest.mark.parametrize("setup,band", [("waveshare-433", "433"), ("waveshare-868", "868")])
def test_service_keeps_the_full_range_for_a_waveshare_board(tmp_path, setup, band):
    # The regression that matters in the other direction: narrowing by accident would make a
    # Waveshare box refuse power levels its SX1262 accepts.
    from lhpc.core import config as cfgmod
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    svc = ControllerService(system=FakeSystem().system, paths=p)
    assert svc.chip_family_for_band(band) == "sx1262"
    assert dc.validate_set("POWER", "20", svc.chip_family_for_band(band)) is None
    assert dc.validate_set("POWER", "0", svc.chip_family_for_band(band)) is None


@pytest.mark.no_default_hardware
def test_unconfigured_box_has_no_family_and_narrows_nothing(tmp_path):
    # The test baseline pretends a LoRaHAM board is fitted (conftest `_default_hardware`), so this
    # one opts out to reach the real fresh-install state: no board picked, no family, no narrowing.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.chip_family_for_band("433") == ""
    assert dc.validate_set("POWER", "20", svc.chip_family_for_band("433")) is None


def test_every_hw_preset_has_a_family():
    # A preset the catalog can launch but the family map does not know would silently fall back
    # to the union range — the failure would be invisible, so it is asserted here instead.
    from lhpc.core.config import HW_PRESETS, hw_preset_family
    for preset in HW_PRESETS:
        assert hw_preset_family(preset) in ("sx127x", "sx1262"), preset

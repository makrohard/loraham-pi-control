"""T-R4's L3 part (A §2.3, PROOF §1): a claim may say `claims_if = "spi" | "usb"`; it is made only
when the radio's transport is the one named. Until L5 every radio is an SPI HAT (today's
hardware setups), so a `claims_if = "usb"` claim is dropped and a `claims_if = "spi"` claim kept.
The daemon's `spi.bus.0` claim is the one that carries it (A §2.3 "SPI gate")."""
import pytest

from lhpc.core.config import HW_SETUPS
from lhpc.core.manifest import ManifestError, _parse_component, _parse_resource, load_manifest


def _comp(*claims):
    return _parse_component({"id": "x", "resource": [
        {"key": key, "kind": "spi-bus", "mode": "cooperative", **extra} for key, extra in claims]})


def test_a_claim_for_another_transport_is_dropped():
    comp = _comp(("spi.bus.0", {"claims_if": "spi"}), ("usb.only", {"claims_if": "usb"}),
                 ("always", {}))
    assert [r.key for r in comp.resources] == ["spi.bus.0", "always"]
    assert comp.resources[0].claims_if == "spi"


@pytest.mark.parametrize("bad", ["SPI", "i2c", "", 1, True])
def test_an_unknown_transport_is_refused(bad):
    with pytest.raises(ManifestError, match="claims_if"):
        _parse_resource({"key": "k", "kind": "spi-bus", "claims_if": bad})


def test_the_daemons_spi_bus_claim_is_spi_only_and_kept():
    daemon = next(c for st in load_manifest() for c in st.components if c.id == "loraham-daemon")
    spi = [r for r in daemon.resources if r.key == "spi.bus.0"]
    assert len(spi) == 1 and spi[0].claims_if == "spi"


def test_every_setup_until_l5_is_spi():
    # The rule the L3 drop rests on: every hardware setup names SPI presets only (no USB stick).
    presets = {p for _label, bands in HW_SETUPS.values() for p in bands.values()}
    assert presets == {"loraham", "uputronics-ce0", "uputronics-ce1", "waveshare-sx1262"}

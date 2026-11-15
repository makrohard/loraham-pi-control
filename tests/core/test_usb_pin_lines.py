"""W23 (DESIGN-TABLES-0.14.0 T1.4b point 7): a USB stick's pins are the CH341 bridge's lines, not BCM lines.
irq is D0-D7 or 10 (INT#), gpio is D0-D7 or 11 (SLCT), every other pin (cs, rst, rxen, ...) D0-D7 only; an SPI
row keeps BCM lines 0-27. Driven through radio_models.load on a written table."""
from __future__ import annotations

import pytest

from lhpc.core import radio_models

_USB = '''[[model]]
id = "stick"
label = "A stick"
short = "stick"
transport = "usb-ch341"
chip = "sx1262"
bands = ["868"]
power_max_dbm = 14
high_power_switch = false
usb = {{ vid = "1a86", pid = "5512" }}

  [[model.variant]]
  key = "default"
  preset = "stick"
  pins = {{ {pins} }}
'''
_SPI = _USB.replace('transport = "usb-ch341"', 'transport = "spi"').replace('usb = {{ vid = "1a86", pid = "5512" }}\n', "")


def _load(tmp_path, text):
    p = tmp_path / "models.toml"
    p.write_text(text, encoding="utf-8")
    return radio_models.load(p)


@pytest.mark.parametrize("pins", ["cs = 0, irq = 10, gpio = 11", "cs = 0, irq = 6, rst = 2, gpio = 4, rxen = 1",
                                  "cs = 7, irq = 0, rst = 7"])
def test_a_usb_row_takes_d_lines_and_the_two_status_pins(tmp_path, pins):
    assert _load(tmp_path, _USB.format(pins=pins))["stick"].variant("default").pins


@pytest.mark.parametrize("pins", ["cs = 8", "rst = 10", "rst = 11", "cs = 10", "irq = 11", "irq = 8", "gpio = 10",
                                  "gpio = 12", "rxen = 8", "cs = 27"])
def test_a_usb_row_refuses_a_line_the_bridge_does_not_have(tmp_path, pins):
    with pytest.raises(radio_models.ModelError, match="D0-D7"):
        _load(tmp_path, _USB.format(pins=pins))


@pytest.mark.parametrize("pins", ["cs = 27, irq = 25", "cs = 8, irq = 10, gpio = 11"])
def test_an_spi_row_keeps_bcm_lines(tmp_path, pins):
    assert _load(tmp_path, _SPI.format(pins=pins))["stick"].variant("default").pins


def test_an_spi_row_still_refuses_a_line_past_27(tmp_path):
    with pytest.raises(radio_models.ModelError, match="BCM line 0-27"):
        _load(tmp_path, _SPI.format(pins="cs = 28"))

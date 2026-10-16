"""The SPI chip-select mode: the boot file and the booted device tree must agree, only the two
supported configurations are admitted, and meshtasticd's YAML follows the live mode."""

import pytest

from lhpc.core import spi_mode
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import (
    Component,
    ComponentKind,
    ResourceClaim,
    ResourceKind,
    ResourceMode,
    Stack,
)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

GPIO_PHANDLE = 0x5A


def _be(*vals):
    return b"".join(v.to_bytes(4, "big") for v in vals)


def _tree(tmp_path, cs=b"", alias=True, symbols=True, phandle=GPIO_PHANDLE, status=None):
    """A fake /sys/firmware/devicetree/base with spi0 and the `gpio` label."""
    dt = tmp_path / "dt"
    (dt / "soc" / "spi@7e204000").mkdir(parents=True)
    (dt / "soc" / "gpio@7e200000").mkdir(parents=True)
    (dt / "aliases").mkdir()
    (dt / "__symbols__").mkdir()
    if alias:
        (dt / "aliases" / "spi0").write_bytes(b"/soc/spi@7e204000\0")
    if cs is not None:
        (dt / "soc" / "spi@7e204000" / "cs-gpios").write_bytes(cs)
    if symbols:
        (dt / "__symbols__" / "gpio").write_bytes(b"/soc/gpio@7e200000\0")
    (dt / "soc" / "gpio@7e200000" / "phandle").write_bytes(_be(phandle))
    if status is not None:
        (dt / "soc" / "spi@7e204000" / "status").write_bytes(status)
    return dt


KERNEL_CS = _be(GPIO_PHANDLE, 8, 1, GPIO_PHANDLE, 7, 1)


@pytest.mark.parametrize("text, mode", [
    (None, "off"),
    ("arm_64bit=1\n", "off"),
    ("dtparam=spi=on\n", "kernel"),
    ("dtparam=spi=on\ndtoverlay=spi0-0cs\n", "soft"),
    ("dtparam=spi=on\n# dtoverlay=spi0-0cs\n", "kernel"),
    ("dtparam=spi=on\ndtoverlay=spi0-1cs\n", "unsupported"),
    ("dtparam=spi=on\ndtoverlay=spi0-2cs,cs0_pin=24\n", "unsupported"),
])
def test_the_boot_file_names_one_of_the_two_supported_modes(text, mode):
    assert spi_mode.boot_mode(text) == mode


@pytest.mark.parametrize("kw, mode", [
    ({"cs": b""}, "soft"),                                            # spi0-0cs: cs-gpios;
    ({"cs": KERNEL_CS}, "kernel"),                                    # <&gpio 8 1>, <&gpio 7 1>
    ({"cs": _be(GPIO_PHANDLE, 8, 1, GPIO_PHANDLE, 24, 1)}, "unsupported"),   # remapped CE1
    ({"cs": _be(GPIO_PHANDLE, 8, 1)}, "unsupported"),                 # spi0-1cs
    ({"cs": _be(GPIO_PHANDLE, 7, 1, GPIO_PHANDLE, 8, 1)}, "unsupported"),    # swapped
    ({"cs": _be(0x77, 8, 1, 0x77, 7, 1)}, "unsupported"),             # another GPIO controller
    ({"cs": _be(GPIO_PHANDLE, 8, 0, GPIO_PHANDLE, 7, 0)}, "unsupported"),    # other flags
    ({"cs": KERNEL_CS, "symbols": False}, "unsupported"),             # the phandle unprovable
    ({"cs": None}, "unsupported"),                                    # no cs-gpios at all
    ({"alias": False}, "off"),                                        # no SPI in the tree
    ({"cs": KERNEL_CS, "status": b"disabled\0"}, "off"),             # SPI disabled, alias kept
    ({"cs": KERNEL_CS, "status": b"okay\0"}, "kernel"),
])
def test_the_booted_tree_is_read_from_spi0s_full_cs_gpios_specifiers(tmp_path, kw, mode):
    assert spi_mode.live_mode(_tree(tmp_path, **kw)) == mode


@pytest.mark.parametrize("boot, live, why", [
    ("soft", "soft", ""),
    ("kernel", "kernel", ""),
    ("off", "off", ""),
    ("kernel", "soft", "reboot required"),          # edited to kernel, not rebooted
    ("soft", "kernel", "reboot required"),          # edited back to soft, not rebooted
    ("kernel", "unsupported", "unsupported SPI boot configuration"),   # booted another overlay
    ("unsupported", "kernel", "unsupported SPI boot configuration"),
])
def test_the_mode_is_valid_only_when_file_and_kernel_agree(boot, live, why):
    got = spi_mode.refusal(boot, live)
    assert (got == "") if not why else got.startswith(why)


BASE = """Lora:
# 868 MHz
  Module: RF95
  CS: 7
  IRQ: 16      # DIO0

# 433 MHz
#  CS: 8

Logging:
  LogLevel: info
"""


def test_soft_mode_leaves_the_rendered_yaml_as_it_is():
    assert spi_mode.meshtasticd_yaml(BASE, "soft") == BASE


@pytest.mark.parametrize("cs, dev", [("7", "spidev0.1"), ("8", "spidev0.0")])
def test_kernel_mode_names_the_ce_spidev_and_drops_the_gpio_cs(cs, dev):
    got = spi_mode.meshtasticd_yaml(BASE.replace("CS: 7", f"CS: {cs}"), "kernel")
    assert f"  spidev: {dev}\n" in got and "\n  CS:" not in got
    assert "#  CS: 8" in got and "  IRQ: 16" in got                 # the rest is untouched


def test_kernel_mode_replaces_a_spidev_line_already_there():
    got = spi_mode.meshtasticd_yaml(BASE.replace("  IRQ:", "  spidev: spidev0.0\n  IRQ:"),
                                    "kernel")
    assert got.count("spidev:") == 1 and "spidev: spidev0.1" in got


def test_kernel_mode_refuses_a_cs_off_ce0_ce1():
    with pytest.raises(ValueError, match="meshtastic's CS is GPIO 21"):
        spi_mode.meshtasticd_yaml(BASE.replace("CS: 7", "CS: 21"), "kernel")


def test_rollback_to_soft_renders_the_gpio_cs_again(monkeypatch):
    """The YAML is rendered from the base at every start: after a reboot back to soft chip
    selects the next start writes `CS:` again and no `spidev:`."""
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: "kernel")
    assert "spidev: spidev0.1" in spi_mode.render("yaml-update", BASE)
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: "soft")
    again = spi_mode.render("yaml-update", BASE)
    assert "  CS: 7\n" in again and "spidev" not in again
    assert spi_mode.render("toml-update", "x = 1\n") == "x = 1\n"


# --- the start gate -----------------------------------------------------------------------

def _start(tmp_path, monkeypatch, boot, live, spi=True):
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda paths=None: boot)
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: live)
    res = (ResourceClaim(key="spi.bus.0", kind=ResourceKind.SPI_BUS,
                         mode=ResourceMode.COOPERATIVE),) if spi else ()
    comp = Component(id="radio", name="radio", kind=ComponentKind.SERVICE, resources=res,
                     run_argv=("/bin/true",))
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system)
    return life.start(Stack(id="s", name="s", components=(comp,)), comp)


def test_a_bus_user_is_refused_while_the_boot_file_waits_for_a_reboot(tmp_path, monkeypatch):
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "soft")
    assert not got.ok and got.detail.startswith("reboot required")


def test_a_bus_user_is_refused_on_a_booted_unsupported_layout(tmp_path, monkeypatch):
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "unsupported")
    assert not got.ok and got.detail.startswith("unsupported SPI boot configuration")


def test_a_component_off_the_bus_is_never_gated(tmp_path, monkeypatch):
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "unsupported", spi=False)
    assert "SPI" not in (got.detail or "")

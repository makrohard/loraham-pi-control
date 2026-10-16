"""The box's SPI chip-select mode, and what it means for the components that drive the bus.

Exactly two boot configurations are supported:

* soft: `dtparam=spi=on` + `dtoverlay=spi0-0cs`. The kernel releases CE0/CE1 and every radio
  drives its own chip select as a GPIO on `/dev/spidev0.0`.
* kernel: `dtparam=spi=on` with no other spi0 overlay. The kernel owns CE0 = GPIO 8 and
  CE1 = GPIO 7, so a radio on GPIO 8 uses `/dev/spidev0.0` and one on GPIO 7 `/dev/spidev0.1`,
  and every transfer is one kernel message with its chip select held by the SPI core.

The mode is valid only when the boot file and the BOOTED device tree agree. A file edited but not
yet rebooted, or any other spi0 overlay (one chip select, remapped pins), refuses every component
that drives the bus. The daemon and the RNS node decide their own spidev from the live kernel; the
meshtasticd config is rewritten here, because meshtasticd reads its chip select from its YAML.
"""
from __future__ import annotations

import re
from pathlib import Path

SOFT, KERNEL, OFF, UNSUPPORTED = "soft", "kernel", "off", "unsupported"
DT = Path("/sys/firmware/devicetree/base")
CONFIG_TXT = (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))
CE_PINS = (8, 7)                 # CE0, CE1 as the Pi 5 and Zero 2 W device trees declare them
ACTIVE_LOW = 1                   # the specifier flags those trees give: <&gpio 8 1>, <&gpio 7 1>


def boot_mode(text: str | None) -> str:
    """What config.txt asks for: soft, kernel, off (no SPI, or no file) or unsupported."""
    if text is None:
        return OFF
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    if "dtparam=spi=on" not in lines:
        return OFF
    overlays = [ln for ln in lines if ln.startswith("dtoverlay=spi0-")]
    if not overlays:
        return KERNEL
    return SOFT if overlays == ["dtoverlay=spi0-0cs"] else UNSUPPORTED


def _u32(raw: bytes) -> list[int]:
    return [int.from_bytes(raw[i:i + 4], "big") for i in range(0, len(raw) - 3, 4)]


def live_mode(dt: Path = DT) -> str:
    """What the running kernel booted with, from spi0's `cs-gpios` (big-endian u32 triplets:
    the GPIO controller's phandle, the pin, the flags). Empty = soft; exactly
    `<&gpio 8 1>, <&gpio 7 1>` = kernel; no spi0 alias, or the node's `status` not "okay"
    (SPI disabled; the base tree keeps the alias) = off; anything else = unsupported."""
    try:
        node = (dt / "aliases" / "spi0").read_bytes().rstrip(b"\0").decode().lstrip("/")
    except (OSError, UnicodeDecodeError):
        return OFF
    try:
        if not (dt / node / "status").read_bytes().startswith(b"okay"):
            return OFF
    except OSError:
        pass                                  # no status property = enabled (DT default)
    try:
        raw = (dt / node / "cs-gpios").read_bytes()
    except OSError:
        return UNSUPPORTED
    if not raw:
        return SOFT
    try:
        gpio = (dt / "__symbols__" / "gpio").read_bytes().rstrip(b"\0").decode().lstrip("/")
        phandle = _u32((dt / gpio / "phandle").read_bytes())[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return UNSUPPORTED
    want = [v for pin in CE_PINS for v in (phandle, pin, ACTIVE_LOW)]
    return KERNEL if len(raw) == 4 * len(want) and _u32(raw) == want else UNSUPPORTED


def read_config_txt(paths=CONFIG_TXT) -> str | None:
    for p in paths:
        try:
            return Path(p).read_text(errors="replace")
        except OSError:
            continue
    return None


def refusal(boot: str, live: str) -> str:
    """Why no component may drive the SPI bus now, or "" when the mode is valid."""
    if UNSUPPORTED in (boot, live):
        return ("unsupported SPI boot configuration: use `dtparam=spi=on` alone (kernel chip "
                "selects) or with `dtoverlay=spi0-0cs` (soft), then reboot")
    if boot != live:
        return (f"reboot required: config.txt asks for {boot} SPI chip selects, but the running "
                f"kernel booted with {live}")
    return ""


def drives_spi(comp) -> bool:
    return any(getattr(r.kind, "value", r.kind) == "spi-bus" for r in comp.resources)


def meshtasticd_yaml(text: str, live: str) -> str:
    """meshtasticd's config for the live mode: with kernel chip selects the `Lora` section's
    `CS: 8|7` becomes `spidev: spidev0.0|spidev0.1` (meshtasticd then leaves CS to the kernel).
    Any other CS in that mode is refused (a transfer on spidev0.0 would also assert CE0)."""
    if live != KERNEL:
        return text
    out, section = [], ""
    for line in text.splitlines(keepends=True):
        if line[:1] not in (" ", "\t", "#", "\n", "\r") and line.strip():
            section = line.split(":", 1)[0].strip()
        m = re.match(r"^(\s+)(CS|spidev):\s*([^#\s]*)", line) if section == "Lora" else None
        if m and m.group(2) == "spidev":
            continue                                  # replaced below
        if m:
            cs = m.group(3)
            if cs not in ("8", "7"):
                raise ValueError(f"the kernel owns the SPI chip selects (GPIO 8 and 7) and "
                                 f"meshtastic's CS is GPIO {cs}: use soft chip selects "
                                 f"(dtoverlay=spi0-0cs) or CS 8/7")
            out.append(f"{m.group(1)}spidev: spidev0.{CE_PINS.index(int(cs))}\n")
            continue
        out.append(line)
    return "".join(out)


def render(fmt: str, text: str) -> str:
    """The generated-config hook: meshtasticd's YAML (the only `yaml-update` file) follows the
    live chip-select mode; every other format is returned as it is."""
    return meshtasticd_yaml(text, live_mode()) if fmt == "yaml-update" else text

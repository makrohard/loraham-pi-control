"""The Pi SPI bus for `chips`: spidev + gpiod, as the daemon drives it.

- Chip select: with kernel chip selects (`/dev/spidev0.1` exists, as the daemon decides) a radio on BCM 8
  uses `spidev0.0` and one on BCM 7 `spidev0.1` with the CS line the kernel's; any other CS line is refused.
  Otherwise the radio's CS line is driven as a GPIO on `spidev0.0`.
- Lines: only the radio's own are requested (RESET, its CS in soft mode, BUSY for an SX1262). A line another
  process holds (the other radio's daemon) refuses Detect with the reason; nothing else is requested.
- The bus: each transfer holds the daemon's `spi0.lock` (an exclusive flock from before CS low to after
  CS high), so it never interleaves with a daemon's transaction. 2 MHz, mode 0, the daemon's settings.
spidev and gpiod are imported only when a bus opens.
"""
from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path

from . import DetectRefused

SPEED_HZ = 2_000_000
CONSUMER = "lhpc-detect"


def kernel_cs(dev: Path = Path("/dev")) -> bool:
    return (dev / "spidev0.1").exists()


def device_for(cs: int, kernel: bool, dev: Path = Path("/dev")) -> str:
    """The spidev node a radio on CS line `cs` uses."""
    if not kernel:
        return str(dev / "spidev0.0")
    if cs not in (8, 7):
        raise DetectRefused(f"kernel chip selects own BCM 8 and 7 only; this radio's CS is BCM {cs}")
    return str(dev / f"spidev0.{0 if cs == 8 else 1}")


class SpiBus:
    """One radio's bus (`chips.Bus`). `pins` are the model variant's (cs, rst, gpio = BUSY on an SX1262)."""

    def __init__(self, pins: dict, chip: str, lock_path: Path, *, dev: Path = Path("/dev"),
                 gpiochip: str = "/dev/gpiochip0", spi_factory=None, gpio_request=None):
        self._kernel = kernel_cs(dev)
        node = device_for(pins["cs"], self._kernel, dev)
        self._rst, self._cs = pins.get("rst"), None if self._kernel else pins["cs"]
        self._busy = pins.get("gpio") if chip == "sx1262" else None
        outputs = [line for line in (self._rst, self._cs) if line is not None]
        inputs = [self._busy] if self._busy is not None else []
        try:
            self._lines = (gpio_request or _gpiod_request)(gpiochip, outputs, inputs)
        except OSError as exc:
            held = exc.errno == errno.EBUSY
            raise DetectRefused(f"GPIO line(s) {outputs + inputs} "
                                + ("held by another process" if held else f"unavailable: {exc}")) from exc
        self._spi = (spi_factory or _spidev_open)(node)
        self._lock = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)

    def reset(self, level: int) -> None:
        if self._rst is not None:
            self._lines.set(self._rst, level)

    def busy(self) -> bool:
        return self._busy is not None and bool(self._lines.get(self._busy))

    def transfer(self, out: bytes) -> bytes:
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            if self._cs is not None:
                self._lines.set(self._cs, 0)
            try:
                return bytes(self._spi.xfer2(list(out)))
            finally:
                if self._cs is not None:
                    self._lines.set(self._cs, 1)
        finally:
            fcntl.flock(self._lock, fcntl.LOCK_UN)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()

    def close(self) -> None:
        self._spi.close()
        self._lines.release()
        os.close(self._lock)


def _spidev_open(node: str):
    import spidev
    spi = spidev.SpiDev()
    bus, dev = node.rsplit("spidev", 1)[1].split(".")
    spi.open(int(bus), int(dev))
    spi.max_speed_hz, spi.mode = SPEED_HZ, 0
    return spi


class _GpiodLines:
    def __init__(self, request):
        self._request = request

    def set(self, line: int, level: int) -> None:
        import gpiod
        self._request.set_value(line, gpiod.line.Value.ACTIVE if level else gpiod.line.Value.INACTIVE)

    def get(self, line: int) -> int:
        import gpiod
        return int(self._request.get_value(line) == gpiod.line.Value.ACTIVE)

    def release(self) -> None:
        self._request.release()


def _gpiod_request(chip: str, outputs: list, inputs: list) -> _GpiodLines:
    import gpiod
    from gpiod.line import Direction, Value
    config = {tuple(outputs): gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)} if outputs else {}
    if inputs:
        config[tuple(inputs)] = gpiod.LineSettings(direction=Direction.INPUT)
    return _GpiodLines(gpiod.request_lines(chip, consumer=CONSUMER, config=config))

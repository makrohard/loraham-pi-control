"""The Pi SPI bus for `chips`: spidev + gpiod, as the daemon drives it.

- Chip select: with kernel chip selects (`/dev/spidev0.1` exists, as the daemon decides) a radio on BCM 8
  uses `spidev0.0` and one on BCM 7 `spidev0.1` with the CS line the kernel's; any other CS line is refused.
  Otherwise the radio's CS line is driven as a GPIO on `spidev0.0`, and the controller must not assert a
  chip select of its own, as the Reticulum interface proves it: SPI_NO_CS where the controller accepts it;
  where it does not (the Pi 5's RP1 answers EINVAL), the held CS line and a controller that declares no
  chip select (`cs-gpios` empty, `dtoverlay=spi0-0cs`); else Detect is refused.
- Lines: only the radio's own are requested (RESET, its CS in soft mode, BUSY for an SX1262); a radio with
  none (an Uputronics board on a kernel chip select) requests no lines. A line another process holds (the
  other radio's daemon) refuses Detect with the reason; nothing else is requested.
- The bus: each transfer holds the daemon's `spi0.lock` (an exclusive flock from before CS low to after
  CS high), so it never interleaves with a daemon's transaction. 2 MHz, mode 0, the daemon's settings.
spidev and gpiod are imported only when a bus opens; both come from the system packages (python3-spidev,
python3-libgpiod 2.x), so a console venv that does not see them, or a 1.x gpiod, is a refusal, never an error.
"""
from __future__ import annotations

import errno
import fcntl
import importlib
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
                 gpiochip: str = "/dev/gpiochip0", spi_factory=None, gpio_request=None, sys: Path = Path("/sys")):
        self._kernel = kernel_cs(dev)
        node = device_for(pins["cs"], self._kernel, dev)
        self._rst, self._cs = pins.get("rst"), None if self._kernel else pins["cs"]
        self._busy = pins.get("gpio") if chip == "sx1262" else None
        outputs = [line for line in (self._rst, self._cs) if line is not None]
        inputs = [self._busy] if self._busy is not None else []
        try:
            self._lines = (gpio_request or _gpiod_request)(gpiochip, outputs, inputs) if outputs or inputs else None
        except OSError as exc:
            held = exc.errno == errno.EBUSY
            raise DetectRefused(f"GPIO line(s) {outputs + inputs} "
                                + ("held by another process" if held else f"unavailable: {exc}")) from exc
        self._spi = None
        try:   # from here every failure gives back what this bus already holds: the spidev, then the lines
            self._spi = (spi_factory or _spidev_open)(node)
            if not self._kernel:
                _no_kernel_cs(self._spi, sys / "class/spidev" / Path(node).name)
            lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # as the daemon's start pre_step makes it
            self._lock = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except BaseException:
            self._give_back()
            raise

    def _give_back(self) -> None:
        """A failed open's cleanup: each step on its own, and an OSError of one never replaces the open's error."""
        for step in (self._spi.close if self._spi is not None else None,
                     self._lines.release if self._lines is not None else None):
            if step is not None:
                try:
                    step()
                except OSError:
                    pass

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
        if self._lines is not None:
            self._lines.release()
        os.close(self._lock)


def _no_kernel_cs(spi, spidev: Path) -> None:
    """The controller asserts no chip select beside the GPIO one (the Reticulum interface's rule)."""
    try:
        spi.no_cs = True
        if spi.no_cs:
            return
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTTY, errno.EOPNOTSUPP):
            raise DetectRefused(f"setting SPI_NO_CS failed: {exc}") from exc
    try:
        size = (Path(os.path.realpath(spidev / "device/..")) / "of_node/cs-gpios").stat().st_size
    except OSError as exc:
        raise DetectRefused("SPI_NO_CS is unsupported and the controller's chip-select configuration could not "
                            f"be read ({exc}); boot with dtoverlay=spi0-0cs") from exc
    if size:
        raise DetectRefused(f"the SPI controller still declares {size // 12} hardware chip-select(s); it can "
                            "assert one during this transfer. Boot with dtoverlay=spi0-0cs")


def _binding(name: str, package: str):
    """The system binding `name` (apt `package`), or a refusal that says the console's Python does not see it."""
    try:
        return importlib.import_module(name)
    except ImportError:
        raise DetectRefused(f"the console's Python does not see the system {name} binding ({package}): its venv "
                            "venv/lhpc must include the system site packages (the 0.14.0 CHANGELOG upgrade step)") from None


def _spidev_open(node: str):
    spidev = _binding("spidev", "python3-spidev")
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
    gpiod = _binding("gpiod", "python3-libgpiod")
    if not hasattr(gpiod, "request_lines"):   # libgpiod 1.x (e.g. Debian 12's 1.6): another API, no request_lines
        raise DetectRefused(f"the system gpiod binding is {getattr(gpiod, '__version__', 'older than 2')}; Detect "
                            "needs python3-libgpiod 2.x")
    from gpiod.line import Direction, Value
    config = {tuple(outputs): gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)} if outputs else {}
    if inputs:
        config[tuple(inputs)] = gpiod.LineSettings(direction=Direction.INPUT)
    return _GpiodLines(gpiod.request_lines(chip, consumer=CONSUMER, config=config))

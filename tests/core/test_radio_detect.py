"""radio_detect (stage L2, unused until Detect goes live): the chip search on fake buses with an injected
clock, the SPI backend on fake spidev/gpiod and the real spi0.lock, the CH341 selection contract and
framing on a fake libusb. One medium per test (PROOF T-X1-T-X4, T-X7, T-X8)."""
from __future__ import annotations

import errno
import fcntl
import os

import pytest

from lhpc.core.radio_detect import DetectRefused, ch341, chips, spi


class _Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


class _Sx127x(_Clock):
    def __init__(self, versions):
        super().__init__()
        self.versions, self.resets, self.reads = list(versions), [], 0

    def reset(self, level):
        self.resets.append((level, self.t))

    def busy(self):
        return False

    def transfer(self, out):
        assert out == bytes([0x42, 0x00])
        self.reads += 1
        return bytes([0, self.versions.pop(0) if self.versions else 0x00])


@pytest.mark.parametrize("versions, found, reads", [
    ([0x00, 0x00, 0x11], (True, 0x11), 3),       # an RFM9x answers on the third try
    ([0x13], (True, 0x13), 1),
    ([0x22] * 12, (False, 0x22), 10),           # an SX1272 is not one of these: 10 tries, then no
])
def test_sx127x_search_is_radiolibs(versions, found, reads):
    # T-X1: up to 10 x (RESET 1 ms low, 5 ms settle, read 0x42 in {0x12, 0x13, 0x11}), 10 ms between tries.
    bus = _Sx127x(versions)
    assert chips.find_sx127x(bus) == found and bus.reads == reads
    assert bus.resets[:2] == [(0, 0.0), (1, 0.001)]


class _Sx1262(_Clock):
    """BUSY is high until `busy_until`; standby answers `statuses` in turn (then a valid one)."""

    def __init__(self, busy_until=0.0, statuses=(), version=b"SX1261 V2D 2D02"):
        super().__init__()
        self.busy_until, self.statuses, self.version, self.log = busy_until, list(statuses), version, []

    def reset(self, level):
        self.log.append(("reset", level))

    def busy(self):
        return self.t < self.busy_until

    def transfer(self, out):
        self.log.append(out[0])
        if out[0] == 0x80:
            return bytes([0, self.statuses.pop(0) if self.statuses else 0x22])
        if out[0] == 0x1D:
            return bytes(4) + self.version.ljust(16, b"\0")
        return bytes(len(out))


def test_sx1262_waits_for_busy_and_a_valid_standby_then_reads_the_version():
    # T-X2: RESET; standby until a valid status (CMD timeout 0x06 and 0xFF refused); BUSY waited out.
    bus = _Sx1262(busy_until=0.3, statuses=[0x06, 0xFF])
    found, version = chips.find_sx1262(bus)
    assert found and version[:6] == b"SX1261"
    assert bus.log.count(0x80) == 3 and bus.t >= 0.3


def test_sx1262_gives_up_after_ten_tries_bounded_by_the_busy_window():
    # BUSY never drops: every wait ends at 1 s, and the search ends after 10 tries.
    bus = _Sx1262(busy_until=float("inf"))
    found, version = chips.find_sx1262(bus)
    assert (found, version) == (False, b"")
    assert bus.log.count(("reset", 0)) == 10 and bus.t < 10 * 3.1


def test_kernel_chip_selects_follow_the_daemons_rule(tmp_path):
    # T-X3: kernel CS iff /dev/spidev0.1; then BCM 8 -> spidev0.0, BCM 7 -> spidev0.1, others refused.
    assert not spi.kernel_cs(tmp_path) and spi.device_for(21, False, tmp_path).endswith("spidev0.0")
    (tmp_path / "spidev0.1").touch()
    assert spi.kernel_cs(tmp_path)
    assert spi.device_for(7, True, tmp_path).endswith("spidev0.1")
    with pytest.raises(DetectRefused, match="BCM 21"):
        spi.device_for(21, True, tmp_path)


class _Lines:
    def __init__(self, outputs, inputs, held=False):
        if held:
            raise OSError(errno.EBUSY, "Device or resource busy")
        self.requested, self.levels = (list(outputs), list(inputs)), {}

    def set(self, line, level):
        self.levels[line] = level

    def get(self, line):
        return 0

    def release(self):
        pass


@pytest.mark.parametrize("pins, chip, kernel, outputs, inputs", [
    ({"cs": 8, "irq": 25, "rst": 5, "gpio": 24, "led_pin": 13}, "sx127x", False, [5, 8], []),
    ({"cs": 8, "irq": 25, "rst": 5, "gpio": 24, "led_pin": 13}, "sx127x", True, [5], []),
    ({"cs": 21, "irq": 16, "rst": 18, "gpio": 20, "txen": 6}, "sx1262", False, [18, 21], [20]),
])
def test_only_the_radios_own_lines_are_requested(tmp_path, pins, chip, kernel, outputs, inputs):
    # T-X7: RESET, CS (soft mode only), BUSY (SX1262); never IRQ, LED, TXEN or another radio's lines.
    if kernel:
        (tmp_path / "spidev0.1").touch()
    seen = []
    bus = spi.SpiBus(pins, chip, tmp_path / "spi0.lock", dev=tmp_path,
                     gpio_request=lambda chip_, o, i: seen.append(_Lines(o, i)) or seen[-1],
                     spi_factory=lambda node: None)
    assert seen[0].requested == (outputs, inputs) and bus.busy() is False


def test_a_line_another_process_holds_refuses_and_opens_nothing(tmp_path):
    opened = []
    with pytest.raises(DetectRefused, match="held by another process"):
        spi.SpiBus({"cs": 7, "rst": 6}, "sx127x", tmp_path / "spi0.lock", dev=tmp_path,
                   gpio_request=lambda c, o, i: _Lines(o, i, held=True),
                   spi_factory=lambda node: opened.append(node))
    assert opened == []


def test_each_transfer_holds_spi0_lock_from_cs_low_to_cs_high(tmp_path):
    # T-X4: the daemon's lock, exclusive for the transaction and free between transactions.
    lock = tmp_path / "spi0.lock"
    probe = os.open(lock, os.O_RDWR | os.O_CREAT)

    def held():
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False

    class Spi:
        def xfer2(self, data):
            assert held() and lines.levels[7] == 0          # inside: locked, CS low
            return data

    lines = None

    def request(c, o, i):
        nonlocal lines
        lines = _Lines(o, i)
        return lines
    bus = spi.SpiBus({"cs": 7, "rst": 6}, "sx127x", lock, dev=tmp_path, gpio_request=request,
                     spi_factory=lambda node: Spi())
    assert bus.transfer(b"\x42\x00") == b"\x42\x00"
    assert not held() and lines.levels[7] == 1
    os.close(probe)


class _Usb:
    """Two (or more) fake sticks; `claimed` raises BUSY, `gone` raises NO_DEVICE, `changes` alters the
    re-read serial. Every write any handle makes is in `writes`."""

    def __init__(self, sticks, claimed=(), gone=(), changes=()):
        self.sticks, self.claimed, self.gone, self.changes = sticks, claimed, gone, changes
        self.writes, self.released = [], []

    def devices(self, vid, pid):
        return [ch341.Found(s, p, i) for i, (s, p) in enumerate(self.sticks)]

    def claim(self, found):
        if found.ref in self.gone:
            raise ch341.UsbError(ch341.ERROR_NO_DEVICE)
        if found.ref in self.claimed:
            raise ch341.UsbError(ch341.ERROR_BUSY)
        usb, serial = self, ("X" + (found.serial or "")) if found.ref in self.changes else found.serial

        class Handle:
            def serial(self):
                return serial

            def port(self):
                return found.port

            def write(self, data):
                usb.writes.append(bytes(data))
        return Handle()

    def release(self, handle):
        self.released.append(handle)


_A, _B = ("A1B2C3D4E5", (1, 3)), ("Q9R8S7T6", (1, 4))


@pytest.mark.parametrize("sticks, kw, usb_kw, refused", [
    ([_A, _B], {"serial": "A1B2C3D4"}, {}, None),                                    # by serial
    ([(None, (1, 3)), _B], {"port": (1, 3)}, {}, None),                              # serial-less, by port
    ([_B], {"serial": "A1B2C3D4"}, {}, "no stick with serial A1B2C3D4"),             # absent
    ([_A, _B], {"serial": "A1B2C3D4"}, {"claimed": (0,)}, "in use by another"),     # held
    ([_A, ("A1B2C3D4ZZ", (1, 4))], {"serial": "A1B2C3D4"}, {}, "more than one stick"),
    ([(None, (1, 3)), (None, (1, 4))], {"port": (1, 3)}, {}, "without a serial"),
    ([_A, _B], {"serial": "A1B2C3D4"}, {"gone": (0,)}, "removed during selection"),
    ([_A, _B], {"serial": "A1B2C3D4"}, {"changes": (0,)}, "changed during selection"),
])
def test_the_usb_selection_contract(sticks, kw, usb_kw, refused):
    # T-X8: only the configured stick, identified before any write; each refusal writes to no device.
    usb = _Usb(sticks, **usb_kw)
    if refused is None:
        assert ch341.select(usb, 0x1A86, 0x5512, **kw).serial() == sticks[0][0]
    else:
        with pytest.raises(DetectRefused, match=refused):
            ch341.select(usb, 0x1A86, 0x5512, **kw)
    assert usb.writes == []
    assert len(usb.released) == (1 if usb_kw.get("changes") else 0)        # a claim is always released


class _Stick:
    """A CH341 as the wire sees it: the last UIO stream sets the D-line levels and directions; an SPI packet
    is clocked only while SCK (D3) and MOSI (D5) are outputs, else it reads all-FF (the lines float); every
    SPI packet records the CS (D0) level it was sent with."""

    def __init__(self):
        self.out = self.dir = 0
        self.cs_at_spi, self.pending, self.reads = [], b"", []

    def write(self, data):
        if data[0] == 0xAB:
            self.out, self.dir = data[1] & 0x3F, data[2] & 0x3F
            return
        driven = self.dir & 0b101000 == 0b101000
        for i in range(0, len(data), 32):
            self.cs_at_spi.append(self.out & 1)
            body = data[i + 1:i + 32]
            self.pending += body if driven else b"\xff" * len(body)        # a driven bus echoes MOSI

    def read(self, n):
        self.reads.append(n)
        got, self.pending = self.pending[:n], self.pending[n:]
        return got


def test_ch341_drives_sck_and_mosi_as_outputs():
    # The daemon's adapter sets D3/D5 as outputs (libpinedio needs that pinmux for SPI); without them the
    # SPI lines float and every read is 0xFF (seen live on the Meshstick: 6 invalid bytes, not "SX1261").
    stick = _Stick()
    bus = ch341.Ch341Bus(None, stick, {"cs": 0, "rst": 2, "gpio": 4})
    assert bus.transfer(b"\x12\x34") == b"\x12\x34"


def test_ch341_selects_the_chip_with_cs_low_as_radiolib_does():
    # RadioLib drives CS low for a transfer and high after it (the daemon turns libpinedio's AUTO_CS off);
    # CS is high (deselected) from the first UIO write on.
    stick = _Stick()
    bus = ch341.Ch341Bus(None, stick, {"cs": 0, "rst": 2, "gpio": 4})
    assert stick.out & 1 == 1
    bus.transfer(bytes(40))
    assert stick.cs_at_spi == [0, 0] and stick.out & 1 == 1


def test_ch341_framing_is_libpinedios():
    # 0xA8 packets of up to 31 bit-reversed bytes, IN reads of up to 31, D0 low around the transfer.
    class Handle:
        def __init__(self):
            self.writes, self.reads = [], []

        def write(self, data):
            self.writes.append(bytes(data))

        def read(self, n):
            self.reads.append(n)
            return bytes([0x80] * n)                     # bit-reversed 0x01
    h = Handle()
    bus = ch341.Ch341Bus(None, h, {"cs": 0, "rst": 2, "gpio": 4})
    assert h.writes == [bytes([0xAB, 0x80 | 0b1, 0x40 | 0b101101, 0x20])]   # CS high; D0, D2, D3, D5 outputs
    out = bytes(range(40))
    assert bus.transfer(out) == bytes([0x01] * 40)
    uio_cs_low, packets, uio_cs_high = h.writes[1:]
    assert uio_cs_low[1] == 0x80 and uio_cs_high[1] == 0x80 | 0b001
    assert packets[0] == 0xA8 and packets[32] == 0xA8 and len(packets) == 32 + 1 + 9
    assert packets[1:3] == bytes([ch341.reverse_byte(0), ch341.reverse_byte(1)]) == b"\x00\x80"
    assert h.reads == [31, 9]

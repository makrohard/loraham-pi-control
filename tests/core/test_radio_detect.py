"""radio_detect (stage L2, unused until Detect goes live): the chip search on fake buses with an injected
clock, the SPI backend on fake spidev/gpiod and the real spi0.lock, the CH341 selection contract and
framing on a fake libusb. One medium per test (PROOF T-X1-T-X4, T-X7, T-X8)."""
from __future__ import annotations

import ctypes
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


class _Echo:
    def xfer2(self, data):
        return data

    def close(self):
        pass


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
                     spi_factory=lambda node: _Echo())
    assert seen[0].requested == (outputs, inputs) and bus.busy() is False



@pytest.mark.parametrize("variant", ["ce0", "ce1"])
def test_an_uputronics_board_on_kernel_chip_selects_requests_no_lines(tmp_path, variant):
    # The table's Uputronics rows have no RESET and no BUSY; on kernel CS there is no line to request.
    from lhpc.core import radio_models
    (tmp_path / "spidev0.1").touch()
    pins = radio_models.load()["uputronics"].variant(variant).pins
    requests = []
    bus = spi.SpiBus(pins, "sx127x", tmp_path / "spi0.lock", dev=tmp_path,
                     gpio_request=lambda c, o, i: requests.append((o, i)), spi_factory=lambda node: _Echo())
    assert bus.transfer(b"\x42\x00") == b"\x42\x00"
    bus.close()
    assert requests == []



class _NoCsRefused(_Echo):
    """An spidev on a controller that rejects SPI_NO_CS (the Pi 5's RP1: EINVAL)."""
    closed = False

    @property
    def no_cs(self):
        return False

    @no_cs.setter
    def no_cs(self, value):
        raise OSError(errno.EINVAL, "Invalid argument")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("spidev, cs_gpios, refused", [
    (_Echo, None, None),                                          # SPI_NO_CS accepted
    (_NoCsRefused, b"", None),                                    # rejected, the controller declares no CS
    (_NoCsRefused, bytes(24), "declares 2 hardware chip-select"),  # rejected, the controller has two
])
def test_a_software_chip_select_runs_only_where_the_controller_asserts_none(tmp_path, spidev, cs_gpios, refused):
    # Soft CS on spidev0.0: the controller must not also assert CE0 (another radio's select).
    node = tmp_path / "sys/class/spidev/spidev0.0"
    (node / "device").mkdir(parents=True)
    if cs_gpios is not None:
        (node / "of_node").mkdir()
        (node / "of_node/cs-gpios").write_bytes(cs_gpios)
    dev, lines = spidev(), []
    kw = dict(dev=tmp_path, sys=tmp_path / "sys", spi_factory=lambda n: dev,
              gpio_request=lambda c, o, i: lines.append(_Lines(o, i)) or lines[-1])
    if refused is None:
        spi.SpiBus({"cs": 21, "rst": 18}, "sx127x", tmp_path / "spi0.lock", **kw).close()
        assert spidev is _NoCsRefused or dev.no_cs is True
    else:
        with pytest.raises(DetectRefused, match=refused):
            spi.SpiBus({"cs": 21, "rst": 18}, "sx127x", tmp_path / "spi0.lock", **kw)
        assert dev.closed

def test_a_line_another_process_holds_refuses_and_opens_nothing(tmp_path):
    opened = []
    with pytest.raises(DetectRefused, match="held by another process"):
        spi.SpiBus({"cs": 7, "rst": 6}, "sx127x", tmp_path / "spi0.lock", dev=tmp_path,
                   gpio_request=lambda c, o, i: _Lines(o, i, held=True),
                   spi_factory=lambda node: opened.append(node))
    assert opened == []


@pytest.mark.parametrize("fails", [False, True])
def test_each_transfer_holds_spi0_lock_from_cs_low_to_cs_high(tmp_path, fails):
    # T-X4: the daemon's lock, exclusive for the transaction (both CS edges inside it, also when the
    # transfer fails) and free between transactions.
    lock = tmp_path / "spi0.lock"
    probe = os.open(lock, os.O_RDWR | os.O_CREAT)

    def held():
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False

    class Spi(_Echo):
        def xfer2(self, data):
            assert held() and lines.levels[7] == 0          # inside: locked, CS low
            if fails:
                raise OSError(errno.EIO, "transfer failed")
            return data

    class Lines(_Lines):
        def set(self, line, level):
            super().set(line, level)
            edges.append((line, level, held()))

    lines, edges = None, []

    def request(c, o, i):
        nonlocal lines
        lines = Lines(o, i)
        return lines
    bus = spi.SpiBus({"cs": 7, "rst": 6}, "sx127x", lock, dev=tmp_path, gpio_request=request,
                     spi_factory=lambda node: Spi())
    if fails:
        with pytest.raises(OSError):
            bus.transfer(b"\x42\x00")
    else:
        assert bus.transfer(b"\x42\x00") == b"\x42\x00"
    assert edges == [(7, 0, True), (7, 1, True)] and not held()
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


class _Libusb:
    """libusb-1.0 as `ch341.Libusb` calls it through ctypes: matching devices by (serial, port, how); `how`
    "ok", "unopenable" (open fails) or "serial-fails" (the descriptor read fails). Counts what is held."""

    def __init__(self, sticks):
        self.sticks = {i + 1: s for i, s in enumerate(sticks)}
        self.writes, self.held = [], {"ctx": 0, "refs": 0, "open": 0, "claimed": 0}

    def libusb_init(self, ctx):
        self.held["ctx"] += 1
        return 0

    def libusb_get_device_list(self, ctx, out):
        self._list = (ctypes.c_void_p * (len(self.sticks) + 1))(*self.sticks, None)
        ptr = ctypes.cast(self._list, ctypes.POINTER(ctypes.c_void_p))
        ctypes.memmove(ctypes.addressof(out._obj), ctypes.addressof(ptr), ctypes.sizeof(ptr))
        return len(self.sticks)

    def libusb_free_device_list(self, lst, unref):
        return None

    def libusb_get_device_descriptor(self, dev, desc):
        desc[8:12] = [0x86, 0x1A, 0x12, 0x55]
        desc[16] = 0 if self.sticks[dev.value][0] is None else 3
        return 0

    def libusb_open(self, dev, out):
        if self.sticks[dev.value][2] == "unopenable":
            return -3
        out._obj.value = dev.value
        self.held["open"] += 1
        return 0

    def libusb_close(self, handle):
        self.held["open"] -= 1

    def libusb_get_string_descriptor_ascii(self, handle, index, buf, n):
        serial, _port, how = self.sticks[handle.value if hasattr(handle, "value") else handle]
        if how == "serial-fails" or (how == "recheck-fails" and self.held["claimed"]):
            return -9
        buf.value = serial.encode()
        return len(serial)

    def libusb_ref_device(self, dev):
        self.held["refs"] += 1

    def libusb_unref_device(self, dev):
        self.held["refs"] -= 1

    def libusb_exit(self, ctx):
        self.held["ctx"] -= 1

    def libusb_get_bus_number(self, dev):
        return self.sticks[dev.value][1][0]

    def libusb_get_port_numbers(self, dev, ports, n):
        rest = self.sticks[dev.value][1][1:]
        ports[:len(rest)] = rest
        return len(rest)

    def libusb_detach_kernel_driver(self, handle, iface):
        return -5

    def libusb_attach_kernel_driver(self, handle, iface):
        return 0

    def libusb_claim_interface(self, handle, iface):
        self.held["claimed"] += 1
        return 0

    def libusb_release_interface(self, handle, iface):
        self.held["claimed"] -= 1

    def libusb_bulk_transfer(self, handle, ep, buf, n, done, timeout):
        self.writes.append(bytes(buf[:n]))
        return 0


@pytest.mark.parametrize("sticks, kw", [
    ([("A1B2C3D4E5", (1, 3), "ok"), ("Q9R8S7T6", (1, 4), "serial-fails")], {"serial": "A1B2C3D4"}),
    ([("A1B2C3D4E5", (1, 3), "ok"), ("Q9R8S7T6", (1, 4), "unopenable")], {"serial": "A1B2C3D4"}),
    ([("Q9R8S7T6", (1, 3), "serial-fails")], {"port": (1, 3)}),
])
def test_a_stick_of_unknown_identity_refuses_the_selection(sticks, kw):
    # A serial that cannot be read is not "no serial", and an unopenable stick still counts: no claim, no write.
    lib = _Libusb(sticks)
    with pytest.raises(DetectRefused, match="identity cannot be read"):
        ch341.select(ch341.Libusb(lib), 0x1A86, 0x5512, **kw)
    assert lib.writes == [] and lib.held["claimed"] == lib.held["open"] == 0


@pytest.mark.parametrize("how, refused", [("ok", None), ("recheck-fails", "cannot be re-checked")])
def test_the_usb_side_gives_back_everything_it_took(how, refused):
    # Context, device references, open handles and the claim: all released, on success (Ch341Bus.close)
    # and when the re-check raises (select refuses; the caller closes the Libusb).
    lib = _Libusb([("A1B2C3D4E5", (1, 3), how), ("Q9R8S7T6", (1, 4), "ok")])
    usb = ch341.Libusb(lib)
    if refused is None:
        ch341.Ch341Bus(usb, ch341.select(usb, 0x1A86, 0x5512, serial="A1B2C3D4"), {"cs": 0}).close()
    else:
        with pytest.raises(DetectRefused, match=refused):
            ch341.select(usb, 0x1A86, 0x5512, serial="A1B2C3D4")
        usb.close()
    assert lib.held == {"ctx": 0, "refs": 0, "open": 0, "claimed": 0}

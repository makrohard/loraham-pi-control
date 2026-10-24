"""The CH341 USB-to-SPI bridge for `chips` (the Meshstick), with libpinedio's byte sequences at b0694ec8.

Selection (A §2.11, F2) opens only the configured stick, identified before any write:
- by serial: the vid:pid devices' serials are read without a claim; exactly one whose first 8 characters
  equal the radio's is opened and claimed (interface 0), then its serial and port are read again and must
  still match;
- serial-less: the device on the stored port must exist with no serial, and be the only serial-less one.
Refused, writing to no device: none found, more than one match, the target claimed by another process
(its daemon), a re-check mismatch, the stick removed during selection. After a claim, the release and the
kernel driver's re-attach always run.

The bridge: D-lines are set with a UIO stream (`0xAB`, `0x80|state`, `0x40|mode`, `0x20`), read with `0xA0`
(6 bytes, D-line n = bit n of the first); SPI goes in 32-byte packets (`0xA8` + up to 31 bytes), every byte
bit-reversed both ways, read back in IN transfers of up to 31 bytes. As the daemon's adapter sets it up
(libpinedio's AUTO_CS off, D3/D5 outputs): SCK (D3) and MOSI (D5) are outputs, the SPI pinmux the UIO
direction word carries; chip select is D0, driven low for a transfer and high otherwise, as RadioLib's
Module does; RESET starts low until the chip search raises it.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import time
from dataclasses import dataclass

from . import DetectRefused

UIO_STREAM, UIO_OUT, UIO_DIR, UIO_END = 0xAB, 0x80, 0x40, 0x20
SCK, MOSI = 3, 5                             # D-lines the SPI stream drives
SPI_STREAM, GET_INPUT, PACKET = 0xA8, 0xA0, 0x20
WRITE_EP, READ_EP, TIMEOUT_MS = 0x02, 0x82, 1000
ERROR_NO_DEVICE, ERROR_NOT_FOUND, ERROR_BUSY = -4, -5, -6


def reverse_byte(x: int) -> int:
    x = ((x >> 1) & 0x55) | ((x << 1) & 0xAA)
    x = ((x >> 2) & 0x33) | ((x << 2) & 0xCC)
    return ((x >> 4) & 0x0F) | ((x << 4) & 0xF0)


@dataclass(frozen=True)
class Found:
    """A vid:pid device as listed, before any claim: its serial (None: the stick reports none) and port."""
    serial: str | None
    port: tuple
    ref: object = None


class UsbError(OSError):
    def __init__(self, code: int):
        super().__init__(code, f"libusb error {code}")
        self.code = code


def select(usb, vid: int, pid: int, *, serial: str = "", port: tuple = ()):
    """The claimed handle of the configured stick, or DetectRefused. `usb` lists, claims and releases."""
    listed = usb.devices(vid, pid)
    if serial:
        hits = [d for d in listed if d.serial and d.serial[:8] == serial[:8]]
        what = f"serial {serial[:8]}"
    else:
        hits = [d for d in listed if d.serial is None and d.port == tuple(port)]
        what = f"port {'.'.join(map(str, port))}"
        if hits and sum(d.serial is None for d in listed) > 1:
            raise DetectRefused("more than one stick without a serial is connected; unplug the others")
    if not hits:
        raise DetectRefused(f"no stick with {what} is connected")
    if len(hits) > 1:
        raise DetectRefused(f"more than one stick matches {what}")
    try:
        handle = usb.claim(hits[0])
    except UsbError as exc:
        if exc.code == ERROR_BUSY:
            raise DetectRefused("the stick is in use by another process (its daemon?)") from exc
        if exc.code == ERROR_NO_DEVICE:
            raise DetectRefused("the stick was removed during selection") from exc
        raise DetectRefused(f"the stick cannot be opened: {exc}") from exc
    if (handle.serial() or None, handle.port()) != (hits[0].serial, hits[0].port):
        usb.release(handle)
        raise DetectRefused("the stick changed during selection (serial or port re-check)")
    return handle


class Ch341Bus:
    """One stick's bus (`chips.Bus`) on a claimed handle; `pins` are the model variant's D-lines."""

    def __init__(self, usb, handle, pins: dict):
        self._usb, self._h = usb, handle
        self._cs, self._rst, self._busy = pins["cs"], pins.get("rst"), pins.get("gpio")
        self._mode = (1 << SCK) | (1 << MOSI) | (1 << self._cs) | (1 << self._rst if self._rst is not None else 0)
        self._state = 1 << self._cs                          # deselected
        self._uio()

    def _uio(self) -> None:
        self._h.write(bytes([UIO_STREAM, UIO_OUT | self._state, UIO_DIR | self._mode, UIO_END]))

    def _line(self, pin: int, level: int) -> None:
        self._state = self._state | (1 << pin) if level else self._state & ~(1 << pin)
        self._uio()

    def reset(self, level: int) -> None:
        if self._rst is not None:
            self._line(self._rst, level)

    def busy(self) -> bool:
        if self._busy is None:
            return False
        self._h.write(bytes([GET_INPUT]))
        return bool(self._h.read(6)[0] & (1 << self._busy))

    def transfer(self, out: bytes) -> bytes:
        self._line(self._cs, 0)
        try:
            packets = bytearray()
            for i in range(0, len(out), PACKET - 1):
                packets += bytes([SPI_STREAM]) + bytes(reverse_byte(b) for b in out[i:i + PACKET - 1])
            self._h.write(bytes(packets))
            rx = bytearray()
            while len(rx) < len(out):
                if not (chunk := self._h.read(min(PACKET - 1, len(out) - len(rx)))):
                    raise DetectRefused("the stick answered a transfer with no data")
                rx += chunk
            return bytes(reverse_byte(b) for b in rx)
        finally:
            self._line(self._cs, 1)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()

    def close(self) -> None:
        self._usb.release(self._h)


class Libusb:
    """`select`'s USB side on libusb-1.0 through ctypes (loaded when Detect runs)."""

    def __init__(self):
        lib = ctypes.CDLL(ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0")
        self._lib, self._ctx = lib, ctypes.c_void_p()
        self._check(lib.libusb_init(ctypes.byref(self._ctx)))

    @staticmethod
    def _check(code: int) -> int:
        if code < 0:
            raise UsbError(code)
        return code

    def _descriptor(self, dev):
        desc = (ctypes.c_uint8 * 18)()
        self._check(self._lib.libusb_get_device_descriptor(dev, desc))
        return desc

    def _port(self, dev) -> tuple:
        ports = (ctypes.c_uint8 * 8)()
        n = self._check(self._lib.libusb_get_port_numbers(dev, ports, 8))
        return (self._lib.libusb_get_bus_number(dev), *ports[:n])

    def _serial(self, handle, index: int) -> str | None:
        if not index:
            return None
        buf = ctypes.create_string_buffer(64)
        n = self._lib.libusb_get_string_descriptor_ascii(handle, index, buf, 64)
        return buf.value.decode("ascii", "replace") if n > 0 else None

    def devices(self, vid: int, pid: int) -> list:
        lst = ctypes.POINTER(ctypes.c_void_p)()
        count = self._check(self._lib.libusb_get_device_list(self._ctx, ctypes.byref(lst)))
        found = []
        for i in range(count):
            dev = ctypes.c_void_p(lst[i])
            desc = self._descriptor(dev)
            if (desc[8] | desc[9] << 8, desc[10] | desc[11] << 8) != (vid, pid):
                continue
            handle = ctypes.c_void_p()
            if self._lib.libusb_open(dev, ctypes.byref(handle)) == 0:          # reads only; no claim
                serial = self._serial(handle, desc[16])
                self._lib.libusb_close(handle)
                self._lib.libusb_ref_device(dev)
                found.append(Found(serial, self._port(dev), dev))
        self._lib.libusb_free_device_list(lst, 1)
        return found

    def claim(self, found: Found):
        handle = ctypes.c_void_p()
        self._check(self._lib.libusb_open(found.ref, ctypes.byref(handle)))
        detached = self._lib.libusb_detach_kernel_driver(handle, 0) == 0
        code = self._lib.libusb_claim_interface(handle, 0)
        if code < 0:
            if detached:
                self._lib.libusb_attach_kernel_driver(handle, 0)
            self._lib.libusb_close(handle)
            raise UsbError(code)
        return _Handle(self, handle, found.ref, self._descriptor(found.ref)[16], detached)

    def release(self, h: _Handle) -> None:
        self._lib.libusb_release_interface(h.handle, 0)
        if h.detached:
            self._lib.libusb_attach_kernel_driver(h.handle, 0)
        self._lib.libusb_close(h.handle)


@dataclass
class _Handle:
    usb: Libusb
    handle: ctypes.c_void_p
    dev: object
    serial_index: int
    detached: bool

    def serial(self) -> str | None:
        return self.usb._serial(self.handle, self.serial_index)

    def port(self) -> tuple:
        return self.usb._port(self.dev)

    def write(self, data: bytes) -> None:
        self._bulk(WRITE_EP, ctypes.create_string_buffer(data, len(data)), len(data))

    def read(self, n: int) -> bytes:
        buf = ctypes.create_string_buffer(n)
        got = self._bulk(READ_EP, buf, n)
        return buf.raw[:got]

    def _bulk(self, ep: int, buf, n: int) -> int:
        done = ctypes.c_int()
        Libusb._check(self.usb._lib.libusb_bulk_transfer(self.handle, ep, buf, n, ctypes.byref(done), TIMEOUT_MS))
        return done.value

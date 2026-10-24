# Derived from RadioLib (https://github.com/jgromes/RadioLib) at 187ef24791c3d844939b2be13a68bd890bd04e4c:
# src/modules/SX127x/SX127x.cpp:1650-1675 (findChip), SX1278.cpp:97-104 (reset), SX1278.h:71-73 (versions),
# src/modules/SX126x/SX126x.cpp:179-212 (reset + standby), :1468-1512 (SPIparseStatus, findChip),
# SX126x_commands.cpp:71-88 (standby), src/Module.cpp:123-137, :330-420 (register burst, SPI stream).
#
# MIT License
#
# Copyright (c) 2018 Jan Gromeš
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""RadioLib's chip search, on any bus: up to 10 tries of reset + version read.

The bus (`Bus`) is one radio's: `reset(level)` drives its RESET line (a board without one ignores it),
`busy()` reads its BUSY line (SX1262 only), `transfer(out)` is one chip-selected full-duplex transfer,
`sleep(s)`/`now()` the clock (injected in tests).
"""
from __future__ import annotations

from typing import Protocol

SX127X_VERSIONS = (0x12, 0x13, 0x11)         # SX1278, the "sometimes" value, RFM9x
SX126X_VERSION = b"SX1261"                   # RadioLib's chipType for SX1261/2: the 6-character prefix
TRIES = 10
_SPI_TIMEOUT_S = 1.0                         # BUSY waits and the standby retry window


class Bus(Protocol):
    def reset(self, level: int) -> None: ...
    def busy(self) -> bool: ...
    def transfer(self, out: bytes) -> bytes: ...
    def sleep(self, seconds: float) -> None: ...
    def now(self) -> float: ...


def find_sx127x(bus: Bus) -> tuple[bool, int | None]:
    """(found, the last version read): RESET 1 ms low, 5 ms settle, read register 0x42."""
    version = None
    for _ in range(TRIES):
        bus.reset(0)
        bus.sleep(0.001)
        bus.reset(1)
        bus.sleep(0.005)
        version = bus.transfer(bytes([0x42 & 0x7F, 0x00]))[1]
        if version in SX127X_VERSIONS:
            return True, version
        bus.sleep(0.010)
    return False, version


def find_sx1262(bus: Bus) -> tuple[bool, bytes]:
    """(found, the last version string read): RESET, standby until a valid status (<= 1 s), read 0x0320 x16.
    A standby that never answers does not end the try: RadioLib reads the version regardless."""
    version = b""
    for _ in range(TRIES):
        bus.reset(0)
        bus.sleep(0.001)
        bus.reset(1)
        start = bus.now()
        while not _standby(bus) and bus.now() - start < _SPI_TIMEOUT_S:
            bus.sleep(0.010)
        rx = _stream(bus, bytes([0x1D, 0x03, 0x20, 0x00]) + bytes(16))      # READ_REGISTER, addr, status
        version = rx[4:20] if rx else b""
        if version[:6] == SX126X_VERSION:
            return True, version
        bus.sleep(0.010)
    return False, version


def _standby(bus: Bus) -> bool:
    bus.transfer(bytes([0x00]))                     # NOP: wakes a sleeping chip, no BUSY wait
    rx = _stream(bus, bytes([0x80, 0x00]))          # SET_STANDBY, STDBY_RC
    return rx is not None and _status_ok(rx[1])


def _stream(bus: Bus, out: bytes) -> bytes | None:
    """One SX126x command: BUSY low before (<= 1 s, else None), the transfer, BUSY low after (<= 1 s)."""
    if not _busy_clear(bus):
        return None
    rx = bus.transfer(out)
    return rx if _busy_clear(bus) else None


def _busy_clear(bus: Bus) -> bool:
    start = bus.now()
    while bus.busy():
        if bus.now() - start >= _SPI_TIMEOUT_S:
            return False
        bus.sleep(0.001)
    return True


def _status_ok(status: int) -> bool:
    """SPIparseStatus: command timeout / invalid / failed, or a bus that reads 0x00/0xFF, is not a chip."""
    return (status & 0b1110) not in (0b0110, 0b1000, 0b1010) and status not in (0x00, 0xFF)

"""W23 (DESIGN-TABLES-0.14.0 T1.3): lhpc's chip probe reads a stick's BUSY from the bridge's whole input word, so
the PINE64's BUSY on SLCT (bit 11, measured on box H: idle 0x80e3d7, asleep 0x80ebd7) is seen, and a D-line BUSY
(the Meshstick's D4) reads as before. The handle is a fake that answers the 0xA0 input read with given bytes."""
from __future__ import annotations

import pytest

from lhpc.core.radio_detect import ch341


class _Handle:
    def __init__(self, reply: bytes):
        self.reply, self.writes = reply, []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, n: int) -> bytes:
        return self.reply[:n]


_IDLE = bytes([0xD7, 0xE3, 0xEF, 0x94, 0x13, 0xFF])     # box H, PINE64, chip in STBY_RC: word 0x80e3d7
_ASLEEP = bytes([0xD7, 0xEB, 0xEF, 0x94, 0x13, 0xFF])   # after SetSleep: word 0x80ebd7 (bit 11 set)


def _bus(reply, gpio):
    pins = {"cs": 0, "irq": 10} | ({"gpio": gpio} if gpio is not None else {})
    return ch341.Ch341Bus(None, _Handle(reply), pins)


@pytest.mark.parametrize("reply, busy", [(_IDLE, False), (_ASLEEP, True)], ids=["idle", "asleep"])
def test_the_pine64s_busy_is_the_slct_status_bit(reply, busy):
    assert _bus(reply, 11).busy() is busy


@pytest.mark.parametrize("d4, busy", [(0x00, False), (0x10, True)])
def test_a_d_line_busy_reads_as_before(d4, busy):
    reply = bytes([0x40 | d4, 0x00, 0x00, 0x00, 0x00, 0x00])
    assert _bus(reply, 4).busy() is busy


def test_no_busy_pin_is_never_busy():
    assert _bus(_ASLEEP, None).busy() is False

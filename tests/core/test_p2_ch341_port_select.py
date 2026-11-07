"""P2 (DETECT-SCOPE-0.14.0-v4, DEC-W45): a USB stick selected by its PORT is not blocked by another stick's
unreadable serial; only the target decides. W1 calls select() with the stick's current port from sysfs and,
for a row with a serial, that serial too (DEC-W35). A readable serial on that port must be the row's; an
unreadable one (a stick whose string descriptors stall, CHE1W433 on box H) proceeds by port and is re-checked
by port. Without a port nothing changes: a stick of unknown identity could be the one asked for, so it refuses."""
import pytest

from lhpc.core.radio_detect import DetectRefused, ch341
from test_radio_detect import _Libusb

A, B, C = "A1B2C3D4E5", "Q9R8S7T6", "CHE1W433"


class _Usb:
    """Fake sticks (serial, port, readable): a stick whose serial is unreadable raises on every re-read."""

    def __init__(self, sticks, moves=()):
        self.sticks, self.moves = sticks, moves
        self.writes, self.released, self.claims = [], [], []

    def devices(self, vid, pid):
        return [ch341.Found(s if ok else None, p, i, ok) for i, (s, p, ok) in enumerate(self.sticks)]

    def claim(self, found):
        self.claims.append(found.ref)
        usb, (s, p, ok) = self, self.sticks[found.ref]
        port = (9, 9) if found.ref in self.moves else p

        class Handle:
            def serial(self):
                if not ok:
                    raise ch341.UsbError(-9)              # LIBUSB_ERROR_PIPE: the descriptor read stalls
                return s

            def port(self):
                return port

            def write(self, data):
                usb.writes.append(bytes(data))
        return Handle()

    def release(self, handle):
        self.released.append(handle)


# (sticks, select kwargs, the claimed stick's index or None, the refusal)
CASES = [
    ([(A, (1, 3), True), (C, (1, 4), False)], {"port": (1, 3), "serial": "A1B2C3D4"}, 0, None),
    ([(A, (1, 3), True), (C, (1, 4), False)], {"port": (1, 3)}, None, "has serial A1B2C3D4; this radio is"),
    ([(None, (1, 3), True), (C, (1, 4), False)], {"port": (1, 3)}, 0, None),
    ([(C, (1, 4), False), (A, (1, 3), True)], {"port": (1, 4), "serial": "CHE1W433"}, 0, None),
    ([(C, (1, 4), False)], {"port": (1, 4)}, 0, None),
    ([(A, (1, 3), True), (B, (1, 4), True)], {"port": (1, 3), "serial": "Q9R8S7T6"}, None,
     "the stick on port 1.3 has serial A1B2C3D4, not Q9R8S7T6"),
    ([(None, (1, 3), True)], {"port": (1, 3), "serial": "A1B2C3D4"}, None,
     "the stick on port 1.3 has serial (none), not A1B2C3D4"),
    ([(A, (1, 3), True), (C, (1, 4), False)], {"port": (1, 5), "serial": "A1B2C3D4"}, None,
     "no stick with port 1.5 is connected"),
    ([(None, (1, 3), True), (None, (1, 4), True)], {"port": (1, 4)}, 1, None),
    # without a port: as before, a stick of unknown identity could be the one asked for
    ([(A, (1, 3), True), (C, (1, 4), False)], {"serial": "A1B2C3D4"}, None, "identity cannot be read"),
    ([(A, (1, 3), True), (B, (1, 4), True)], {"serial": "A1B2C3D4"}, 0, None),
    ([(A, (1, 3), True)], {}, None, "neither a serial nor a port"),
]


@pytest.mark.parametrize("sticks, kw, claimed, refused", CASES, ids=[str(i) for i in range(len(CASES))])
def test_the_port_names_the_stick(sticks, kw, claimed, refused):
    usb = _Usb(sticks)
    if refused is None:
        h = ch341.select(usb, 0x1A86, 0x5512, **kw)
        assert usb.claims == [claimed] and h.port() == sticks[claimed][1]
    else:
        with pytest.raises(DetectRefused, match=refused.replace("(", r"\(").replace(")", r"\)")):
            ch341.select(usb, 0x1A86, 0x5512, **kw)
        assert usb.claims == []                                   # refused before any claim
    assert usb.writes == []


def test_an_unreadable_target_that_moved_during_selection_is_refused():
    usb = _Usb([(C, (1, 4), False)], moves=(0,))
    with pytest.raises(DetectRefused, match="changed during selection"):
        ch341.select(usb, 0x1A86, 0x5512, port=(1, 4))
    assert len(usb.released) == 1 and usb.writes == []


def test_by_port_through_libusb_a_stalling_serial_proceeds_and_another_stalling_stick_is_no_bar():
    # The real Libusb wrapper on the libusb fake: the target's descriptor read fails (-9), and so does the other
    # stick's; the port still selects, claims and re-checks the target, and everything taken is given back.
    lib = _Libusb([("Q9R8S7T6", (1, 3), "serial-fails"), ("A1B2C3D4E5", (1, 4), "serial-fails")])
    usb = ch341.Libusb(lib)
    bus = ch341.Ch341Bus(usb, ch341.select(usb, 0x1A86, 0x5512, port=(1, 3)), {"cs": 0})
    assert lib.held["claimed"] == 1
    bus.close()
    assert lib.held["claimed"] == lib.held["open"] == 0

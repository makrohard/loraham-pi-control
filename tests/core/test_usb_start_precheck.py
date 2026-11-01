"""The start pre-check of a USB radio (B5-WIRING-TABLES-v1 Table P; INTERFACE-B5-W1 `start_precheck`, `bound_text`).

The verdict per kind of row, the library's outcomes mapped to a start verdict, node access, and the fail-closed
errors. New modules (lhpc.core.usb_flow, the USB library): each test imports them itself.
"""
from __future__ import annotations

import dataclasses

import pytest

from lhpc.core import radio_models

MODELS = radio_models.load()
STICK = "meshstick-1262"


def _ua():
    from lhpc.core import usb_access
    return usb_access


def _uf():
    from lhpc.core import usb_flow
    return usb_flow


def _r(rid="r1", model=STICK, band="868", **kw):
    from lhpc.core import radios
    return radios.Radio(id=rid, name=f"Radio {rid}", model=model, band=band, **kw)


def _field(value):
    ua = _ua()
    if value is None:
        return ua.Field(ua.Probe.ABSENT)
    if value is ua.Probe.UNKNOWN:
        return ua.Field(ua.Probe.UNKNOWN)
    return ua.Field(ua.Probe.PRESENT, value)


def _dev(port, *, vid="1a86", pid="5512", serial=None, devnum="5"):
    ua = _ua()
    return ua.UsbDevice(port, _field(vid), _field(pid), _field("1"), _field(port.split("-", 1)[1]),
                        _field(devnum), _field(None), _field(serial))


def _scan(*devices, listing=None):
    ua = _ua()
    return ua.Scan(listing or ua.Probe.PRESENT, tuple(devices))


def _check(radio, found=None, access=None, *, model=None, scan_fn=None, access_fn=None):
    uf, ua = _uf(), _ua()
    scan_fn = scan_fn or (lambda: found if found is not None else _scan())
    access_fn = access_fn or (lambda device: access or ua.Probe.PRESENT)
    return uf.start_precheck(radio, model or MODELS[radio.model], scan_fn, access_fn)


def _proceeds(v):
    return v.outcome == "proceed"


def _refuses(v):
    return v.outcome == "refuse" and bool(v.reason)


# ---- the kinds of row ----------------------------------------------------------------------------

def test_p7_an_spi_row_proceeds_without_a_usb_check():
    def never():
        raise AssertionError("an SPI row must not scan")
    v = _check(_r("r1", model="loraham", band="433"), scan_fn=never)
    assert _proceeds(v) and v.reason == ""


def test_p6_a_row_with_a_serial_proceeds_the_daemon_selects_by_serial():
    def never():
        raise AssertionError("a serial row has no port pre-check")
    assert _proceeds(_check(_r("r1", usb_serial="ABCDEFGH"), scan_fn=never))


# ---- a serial-less row: the library's outcomes ------------------------------------------------------

def test_p1_the_stick_on_its_port_without_a_serial_proceeds():
    assert _proceeds(_check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3"))))


def test_p3_no_stick_on_the_stored_port_refuses_naming_the_port():
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.2", vid="0bda")))
    assert _refuses(v) and "1-1.3" in v.reason


def test_p2_a_stick_with_a_serial_on_the_serialless_port_refuses_naming_the_serial_as_bound_writes_it():
    uf = _uf()
    serial = "AB CD%1"
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3", serial=serial)))
    assert _refuses(v) and "1-1.3" in v.reason and uf.bound_text(serial) in v.reason


def test_p4_a_second_serialless_stick_refuses_naming_every_port():
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.2"), _dev("1-1.3")))
    assert _refuses(v) and "1-1.2" in v.reason and "1-1.3" in v.reason
    assert set(v.ports) >= {"1-1.2", "1-1.3"}


def test_p5_an_unknown_listing_refuses():
    ua = _ua()
    assert _refuses(_check(_r("r1", usb_port="1-1.3"), _scan(listing=ua.Probe.UNKNOWN)))


@pytest.mark.parametrize("port", ["1-", "0-1", "1-1..2", "1-1.3 "])
def test_p5_a_stored_port_that_is_not_a_port_refuses(port):
    v = _check(_r("r1", usb_port=port), _scan(_dev("1-1.3")))
    assert _refuses(v)


def test_p5_an_unreadable_device_refuses_naming_its_port():
    ua = _ua()
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3"), _dev("1-1.4", vid=ua.Probe.UNKNOWN)))
    assert _refuses(v) and "1-1.4" in v.reason


# ---- node access after PROCEED (P11) -----------------------------------------------------------------

def test_p11_no_access_refuses_with_the_copybox():
    ua = _ua()
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3")), ua.Probe.ABSENT)
    assert _refuses(v) and v.copybox is True


def test_p11_unknown_access_proceeds_the_daemon_decides():
    ua = _ua()
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3")), ua.Probe.UNKNOWN)
    assert _proceeds(v) and v.copybox is False


def test_p11_access_is_asked_for_the_stick_on_the_port():
    ua = _ua()
    asked = []

    def access(device):
        asked.append(device.port)
        return ua.Probe.PRESENT
    _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.2", vid="0bda"), _dev("1-1.3")), access_fn=access)
    assert asked == ["1-1.3"]


# ---- fail closed (P8, P9) ------------------------------------------------------------------------

def test_p8_bad_model_usb_data_refuses_naming_the_model():
    model = dataclasses.replace(MODELS[STICK], usb={"vid": "zz", "pid": "5512"})
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3")), model=model)
    assert _refuses(v) and STICK in v.reason


def test_p9_a_raising_scan_refuses_and_never_raises():
    def boom():
        raise OSError(5, "I/O error")
    v = _check(_r("r1", usb_port="1-1.3"), scan_fn=boom)
    assert _refuses(v) and "I/O error" in v.reason


def test_p9_a_raising_access_check_refuses_and_never_raises():
    def boom(device):
        raise RuntimeError("stat failed")
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3")), access_fn=boom)
    assert _refuses(v) and "stat failed" in v.reason


# ---- bound_text (P2) ---------------------------------------------------------------------------

@pytest.mark.parametrize("serial,want", [
    ("ABCDEFGH", "ABCDEFGH"), ("A B", "A%20B"), ("50%", "50%25"), ("é", "%C3%A9"), ("a\x7f", "a%7F"),
    ("~!", "~!"), ("", ""),
])
def test_bound_text_writes_bytes_outside_0x21_0x7e_and_percent_as_upper_hex(serial, want):
    assert _uf().bound_text(serial) == want


def test_bound_text_caps_the_input_at_64_bytes():
    assert _uf().bound_text("A" * 70) == "A" * 64


# ---- rows the B5 mutation sweep showed open (B03, B06) ------------------------------------------------

def test_p8_a_model_of_an_unknown_transport_refuses_naming_the_radio():
    v = _check(_r("r7"), model=dataclasses.replace(MODELS[STICK], transport="i2c"))
    assert _refuses(v) and "r7" in v.reason


@pytest.mark.parametrize("answer", ["absent", None, True, 0, "PRESENT"])
def test_p9_an_access_answer_that_is_no_probe_refuses(answer):
    v = _check(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3")), access_fn=lambda device: answer)
    assert _refuses(v)

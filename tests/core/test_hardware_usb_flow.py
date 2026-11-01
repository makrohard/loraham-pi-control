"""The Hardware section's USB flow (B5-WIRING-TABLES-v1 Table U; INTERFACE-B5-W1 `usb_choices`, `bind_choice`).

The configured list is the truth and presence is a badge only: a scan offers choices, the operator binds one, and
the save rules of the hardware list and the in-use refusals decide. New modules: each test imports them itself.
"""
from __future__ import annotations

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


def _rad():
    from lhpc.core import radios
    return radios


def _r(rid="r1", model=STICK, band="868", **kw):
    return _rad().Radio(id=rid, name=f"Radio {rid}", model=model, band=band, **kw)


def _field(value):
    ua = _ua()
    if value is None:
        return ua.Field(ua.Probe.ABSENT)
    if value is ua.Probe.UNKNOWN:
        return ua.Field(ua.Probe.UNKNOWN)
    return ua.Field(ua.Probe.PRESENT, value)


def _dev(port, *, vid="1a86", pid="5512", serial=None):
    ua = _ua()
    return ua.UsbDevice(port, _field(vid), _field(pid), _field("1"), _field(port.split("-", 1)[1]),
                        _field("5"), _field(None), _field(serial))


def _scan(*devices, listing=None):
    ua = _ua()
    return ua.Scan(listing or ua.Probe.PRESENT, tuple(devices))


def _choices(found):
    return _uf().usb_choices(found, MODELS[STICK])


def _state(*radios):
    rad = _rad()
    return rad.State(rad.VALID, radios=tuple(radios))


# ---- usb_choices (U-1, U-2, U-3) -------------------------------------------------------------------

def test_u1_an_unknown_listing_offers_no_choice_and_says_why():
    c = _choices(_scan(listing=_ua().Probe.UNKNOWN))
    assert c.listing is _ua().Probe.UNKNOWN and c.items == () and c.reason.startswith("USB devices cannot be listed")


def test_u2_each_model_match_is_listed_with_its_port_and_serial_or_no_serial():
    c = _choices(_scan(_dev("1-1.2", serial="ABCDEFGH12"), _dev("1-1.3"), _dev("1-1.4", vid="0bda")))
    by_port = {i.port: i for i in c.items}
    assert set(by_port) == {"1-1.2", "1-1.3"}                      # the other vendor is no choice
    assert by_port["1-1.2"].serial == "ABCDEFGH12" and by_port["1-1.2"].selectable
    assert by_port["1-1.3"].serial == "" and "no serial" in by_port["1-1.3"].label and by_port["1-1.3"].selectable


def test_u2_an_unreadable_device_is_listed_but_cannot_be_chosen():
    ua = _ua()
    c = _choices(_scan(_dev("1-1.4", vid=ua.Probe.UNKNOWN)))
    (item,) = c.items
    assert not item.selectable and "unreadable (port 1-1.4)" in item.label


@pytest.mark.parametrize("serial", ["ABC1234", "ABCD EFGH", "ABCDEFGé", "ABCD{EFG}", "A" * 65])
def test_u3_a_serial_the_save_rule_refuses_cannot_be_chosen_and_says_why(serial):
    c = _choices(_scan(_dev("1-1.2", serial=serial)))
    (item,) = c.items
    assert not item.selectable and item.why


# ---- bind_choice (U-4, U-6, U-7, U-8) --------------------------------------------------------------

def _choice(port, serial="", selectable=True):
    return _uf().Choice(port=port, serial=serial, selectable=selectable, label=port, why="")


def _bind(state, rid, choice, users=(), procs=(), owned=frozenset()):
    return _uf().bind_choice(state, rid, choice, users, procs, owned)


def test_bind_a_serial_stick_sets_the_serial_and_clears_the_port():
    b = _bind(_state(_r("r1", usb_port="1-1.2")), "r1", _choice("1-1.2", "ABCDEFGH12"))
    assert b.outcome == "proceed"
    (row,) = b.radios
    assert row.usb_serial == "ABCDEFGH12" and row.usb_port == ""


def test_u4_bind_a_serialless_stick_records_its_port_and_clears_the_serial():
    b = _bind(_state(_r("r1", usb_serial="ABCDEFGH")), "r1", _choice("1-1.3"))
    assert b.outcome == "proceed"
    (row,) = b.radios
    assert row.usb_port == "1-1.3" and row.usb_serial == ""


def test_u4_a_second_serialless_row_is_refused_by_the_save_rule():
    st = _state(_r("r1", usb_port="1-1.2"), _r("r2", usb_serial="ABCDEFGH"))
    b = _bind(st, "r2", _choice("1-1.3"))
    assert b.outcome == "refuse" and b.reason


def test_u6_a_bind_while_a_user_runs_is_refused_naming_it():
    b = _bind(_state(_r("r1", usb_port="1-1.2")), "r1", _choice("1-1.3"), users=("meshtastic@r1",))
    assert b.outcome == "refuse" and "meshtastic@r1" in b.reason


def test_u6_a_bind_while_the_radios_daemon_runs_is_refused_naming_its_pid():
    from lhpc.core import radio_daemon
    proc = radio_daemon.DaemonProc(pid=77, kind="id", rid="r1", band="", reason="")
    b = _bind(_state(_r("r1", usb_port="1-1.2")), "r1", _choice("1-1.3"), procs=(proc,), owned={77})
    assert b.outcome == "refuse" and "77" in b.reason


def test_u7_the_save_does_not_need_the_stick_to_be_there_now():
    b = _bind(_state(_r("r1", usb_port="1-1.2")), "r1", _choice("1-1.9"))
    assert b.outcome == "proceed"


def test_an_unselectable_choice_or_an_unknown_radio_is_refused():
    st = _state(_r("r1", usb_port="1-1.2"))
    assert _bind(st, "r1", _choice("1-1.3", selectable=False)).outcome == "refuse"
    assert _bind(st, "r9", _choice("1-1.3")).outcome == "refuse"



# ---- U-5: node access of the chosen stick (INTERFACE-B5-W1-NOTE-1 §1) ---------------------------------

@pytest.mark.parametrize("access,copybox", [("ABSENT", True), ("PRESENT", False), ("UNKNOWN", False)])
def test_u5_no_access_still_binds_and_offers_the_copybox(access, copybox):
    P = _ua().Probe
    b = _uf().bind_choice(_state(_r("r1", usb_port="1-1.2")), "r1", _choice("1-1.3"), (), (), frozenset(),
                          access=getattr(P, access))
    assert b.outcome == "proceed" and b.copybox is copybox


@pytest.mark.parametrize("access", ["absent", None, True, 0])
def test_u5_an_access_value_that_is_no_probe_refuses(access):
    b = _uf().bind_choice(_state(_r("r1", usb_port="1-1.2")), "r1", _choice("1-1.3"), (), (), frozenset(),
                          access=access)
    assert b.outcome == "refuse" and b.reason

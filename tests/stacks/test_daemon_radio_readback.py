"""Daemon 1.5.0's `GET RADIO` on the LHPC side (daemon plan v17 §13 A6/A7): `read_radio` is the one strict reader,
asked only by a daemon whose STATUS carries `RADIOERR=` (Guard A), and a radio-param SET is confirmed by it only
from a complete reply that says `RADIO=READY`. The fake socket records every request."""
from __future__ import annotations

import pytest

from lhpc.core import daemon_control as dc
from lhpc.core.probes.backends import FakeSystem

_ST15 = b"STATUS RADIO=READY TXMODE=MANAGED RADIOERR=retry FREQ=433.175 SF=12 POWER=17\n"   # a 1.5.0 daemon
_ST142 = b"STATUS RADIO=READY TXMODE=MANAGED FREQ=433.175 SF=12 POWER=17\n"                 # 1.4.2: no RADIOERR
_FIELDS = {"RADIO": "READY", "MODE": "LORA", "FREQ": "433.175", "SF": "12", "BW": "125.0", "CR": "5",
           "PREAMBLE": "8", "SYNC": "0x12", "CRC": "1", "LDRO": "1", "POWER": "17"}


def _radio(**over) -> bytes:
    f = {**_FIELDS, **over}
    return ("RADIO " + " ".join(f"{k}={v}" for k, v in f.items()) + "\n").encode()


class _Daemon:
    """A CONF socket: a SET gets OK, a GET its reply from `replies` (an exception is raised), anything else ''."""

    def __init__(self, replies):
        self.replies, self.sent = replies, []

    def request(self, path, payload, timeout, maxb):
        self.sent.append(payload)
        if payload.startswith(b"SET "):
            return b"OK\n"
        r = self.replies.get(payload, b"")
        if isinstance(r, BaseException):
            raise r
        return r[:maxb]

    def send(self, *a): ...


def _system(status=_ST15, radio=None):
    sys = FakeSystem().system
    sys.unix = _Daemon({b"GET STATUS\n": status, b"GET RADIO\n": _radio() if radio is None else radio})
    return sys, sys.unix


# ---- read_radio: Guard A and the strict reply ---------------------------------------------------------------

def test_a_1_5_0_reply_is_read_whole():
    sys, d = _system()
    assert dc.read_radio(sys, "433", dc.parse_conf_reply(_ST15, "STATUS")) == _FIELDS
    assert d.sent == [b"GET RADIO\n"]


@pytest.mark.parametrize("status", [{}, dc.parse_conf_reply(_ST142, "STATUS")], ids=["unreachable", "daemon-1.4.2"])
def test_guard_a_never_asks_a_daemon_without_radioerr(status):
    sys, d = _system(status=_ST142)
    assert dc.read_radio(sys, "433", status) == {}
    assert d.sent == []                                     # GET RADIO would block a 1.4.2 daemon under its radio lock


_CUT = _radio().rstrip(b"\n")
_BAD = {
    "no final newline": _CUT,
    "cut inside the last value": _CUT[:-1],                # POWER=1 of POWER=17, EOF before the newline
    "a field missing": _radio().replace(b" POWER=17", b""),
    "err unknown": b"ERR UNKNOWN\n",
    "empty": b"",
    "oversized": _radio() + b"x" * 5000,
    "timeout": TimeoutError("timed out"),
    "transport error": ConnectionRefusedError("no daemon"),
}


@pytest.mark.parametrize("reply", list(_BAD.values()), ids=list(_BAD))
def test_anything_but_a_complete_eleven_field_line_is_unavailable(reply):
    sys, _d = _system(radio=reply)
    assert dc.read_radio(sys, "433", {"RADIOERR": "retry"}) == {}


# ---- a radio-param SET's read-back ------------------------------------------------------------------------------

def test_a_1_5_0_daemon_confirms_a_radio_set_by_get_radio():
    sys, d = _system()
    ok, confirmed, detail = dc.apply_set(sys, "433", "FREQ", "433.175")
    assert (ok, confirmed) == (True, True) and "confirmed" in detail, detail
    assert d.sent == [b"SET FREQ=433.175\n", b"GET STATUS\n", b"GET RADIO\n"]


def test_a_daemon_before_1_5_0_stays_sent_but_unconfirmed_and_is_never_asked_get_radio():
    sys, d = _system(status=_ST142)
    ok, confirmed, detail = dc.apply_set(sys, "433", "FREQ", "433.175")
    assert (ok, confirmed) == (True, False) and "UNCONFIRMED" in detail, detail
    assert b"GET RADIO\n" not in d.sent


@pytest.mark.parametrize("key,sent,reported", [
    ("FREQ", "433.175", "433.9"),       # L1: int(float()) made both 433
    ("BW", "7.8", "7"),                 # L1: and both 7
])
def test_a_different_decimal_is_not_applied(key, sent, reported):
    sys, _d = _system(radio=_radio(**{key: reported}))
    ok, confirmed, detail = dc.apply_set(sys, "433", key, sent)
    assert (ok, confirmed) == (False, False) and "NOT applied" in detail, detail


@pytest.mark.parametrize("key,sent,reported", [
    ("FREQ", "433.175", "433.175"), ("FREQ", "433.900", "433.9"), ("SF", "12", "12"), ("BW", "125.0", "125.0"),
    ("BW", "62.5", "62.50"), ("CR", "5", "5"), ("PREAMBLE", "8", "8"),
    ("SYNC", "18", "0x12"),             # L2: a number, not text
    ("SYNC", "0x12", "0x12"), ("CRC", "1", "1"), ("LDRO", "auto", "AUTO"), ("LDRO", "1", "1"), ("POWER", "17", "17"),
])
def test_each_radio_key_confirms_in_its_canonical_form(key, sent, reported):
    sys, _d = _system(radio=_radio(**{key: reported}))
    ok, confirmed, detail = dc.apply_set(sys, "433", key, sent)
    assert (ok, confirmed) == (True, True), detail


def test_a_read_back_cut_inside_its_last_field_never_confirms():
    # SET POWER=1 -> OK; the reply says READY and would parse to POWER=1 (cut from POWER=17) but has no newline
    sys, d = _system(radio=_CUT[:-1])
    ok, confirmed, detail = dc.apply_set(sys, "433", "POWER", "1")
    assert (ok, confirmed) == (True, False) and "unavailable" in detail, detail
    assert d.sent.count(b"GET RADIO\n") == 1                # read once, through read_radio


@pytest.mark.parametrize("health", ["UNPLUGGED", "FAILED"])
def test_matching_values_without_radio_ready_are_not_confirmed(health):
    # the daemon keeps the values while UNPLUGGED/FAILED: only READY in the same reply confirms
    sys, _d = _system(status=_ST15, radio=_radio(RADIO=health))
    ok, confirmed, detail = dc.apply_set(sys, "433", "FREQ", "433.175")
    assert (ok, confirmed) == (True, False) and f"not confirmed (RADIO={health})" in detail, detail


def test_norm_compares_numbers_exactly_and_never_expands_a_hostile_exponent():
    assert dc._norm("433.175") != dc._norm("433.9") and dc._norm("433.900") == dc._norm("433.9") == "433.9"
    assert dc._norm("0x12") == dc._norm("18") == "18" and dc._norm("-0") == "0"
    assert dc._norm("1E999999999") == "1E999999999" and dc._norm("nan") == "NAN"

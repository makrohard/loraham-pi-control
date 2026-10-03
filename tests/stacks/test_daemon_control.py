"""Tests for daemon CONF monitoring and the whitelisted live-settings layer."""

from __future__ import annotations

import pytest

from lhpc.core import daemon_control as dc
from lhpc.core.probes.backends import FakeSystem

_STATUS = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
_STATS = b"STATS UPTIME=5 RADIO=READY RX=2 TXOK=1 TXERR=0\n"
_CHANNEL = b"CHANNEL RADIO=READY BUSY=0 CADSTATE=FREE RSSI=-95 PACKETRSSI=-95 LIVERSSI=-103 MODE=LORA\n"
_CHANNEL_NOSCAN = (b"CHANNEL RADIO=READY BUSY=0 CAD=0 CADSCAN=0 CADSTATE=NOTSCANNED "
                   b"RSSI=-95 PACKETRSSI=-95 LIVERSSI=-103 MODE=LORA TXMODE=MANAGED\n")


def _system_for(band):
    sock = dc.conf_socket(band)
    # FakeSystem returns the same reply per path; STATUS prefix is what read_view
    # checks first, so seed STATUS (the other queries reuse the same map here).
    return FakeSystem(unix_replies={sock: _STATUS})


def test_validate_set_enum_and_int():
    assert dc.validate_set("MODE", "LORA") is None              # enum
    assert dc.validate_set("MODE", "bogus") is not None
    assert dc.validate_set("SF", "12") is None                  # int range
    assert dc.validate_set("SF", "99") is not None              # out of range
    assert dc.validate_set("POWER", "abc") is not None          # not an int
    assert dc.validate_set("FREQ", "433.900") is None           # frequency
    assert dc.validate_set("SYNC", "0x12") is None              # hex byte
    assert dc.validate_set("TXMODE", "DIRECT") is None          # TX-mode monitoring SET
    assert dc.validate_set("CADWAIT", "1500") is None           # CAD monitoring SET
    assert dc.validate_set("BOGUS", "x") is not None            # no passthrough


def test_validate_set_rejects_unknown_and_arbitrary():
    assert dc.validate_set("RXFREQ", "433") is not None
    assert dc.validate_set("ANYTHING", "x") is not None         # no passthrough


def test_read_view_parses_status():
    view = dc.read_view(_system_for("433").system, "433")
    assert view.reachable and view.status["RADIO"] == "READY"
    assert view.status["TXMODE"] == "MANAGED"


def test_read_view_unreachable_on_error():
    sock = dc.conf_socket("868")
    sys = FakeSystem(unix_errors={sock: "no socket"}).system
    view = dc.read_view(sys, "868")
    assert not view.reachable and "unreachable" in view.error


def test_apply_set_validates_before_sending():
    sys = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS SF=12\n"}).system
    ok, _c, _ = dc.apply_set(sys, "433", "SF", "12")
    assert ok
    ok, _c, detail = dc.apply_set(sys, "433", "SF", "99")
    assert not ok and "[7, 12]" in detail


def test_integer_values_are_ascii_decimal_and_sent_canonical():
    # int() also takes "1_0", " 10", "10\n" and non-ASCII digits; none of those is a decimal the
    # daemon parses. A sign or a leading zero is canonicalised, and the socket gets that form.
    for bad in ("1_0", " 10", "10\n", "\u0661\u0660"):
        assert dc.validate_set("SF", bad) is not None, bad
    assert dc.validate_set("SF", "+10") is None
    assert dc.validate_set("SF", "1" * 5000) == "SF must be an integer in [7, 12]"   # past int()'s digit limit
    fs = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS SF=10\n"})
    sent = lambda: [p for _, p in fs.sent]                         # noqa: E731
    ok, _c, _ = dc.apply_set(fs.system, "433", "SF", "010")
    assert ok and sent() == [b"SET SF=10\n"]
    ok, _c, _ = dc.apply_set(fs.system, "433", "SF", "1_0")
    assert not ok and sent() == [b"SET SF=10\n"]                # refused before the socket


def test_apply_set_confirms_via_readback():
    # The daemon acks a SET with OK; confirmation is the read-back — apply_set GETs the field
    # back and confirms the hardware took the value before reporting success.
    fake = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS TXMODE=DIRECT CADWAIT=1500\n"})
    ok, confirmed, detail = dc.apply_set(fake.system, "433", "TXMODE", "DIRECT")
    assert ok and confirmed and "confirmed" in detail
    assert any(p == b"SET TXMODE=DIRECT\n" for _, p in fake.sent)   # the SET was sent


def test_apply_set_readback_mismatch_is_failure():
    # Daemon reports a different value than we set -> NOT applied (no green banner).
    fake = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS TXMODE=MANAGED\n"})
    ok, confirmed, detail = dc.apply_set(fake.system, "433", "TXMODE", "DIRECT")
    assert not ok and not confirmed and "NOT applied" in detail and "MANAGED" in detail


def test_apply_set_radio_param_cannot_be_confirmed():
    # FREQ/SF/etc are applied to the chip but not reported by any GET -> sent, UNCONFIRMED
    # (ok=True but confirmed=False; the caller must never present this as "applied").
    fake = FakeSystem(unix_replies={dc.conf_socket("433"): b"STATUS TXMODE=MANAGED\n"})
    ok, confirmed, detail = dc.apply_set(fake.system, "433", "SF", "12")
    assert ok and not confirmed and "UNCONFIRMED" in detail


def test_apply_set_unreachable_socket():
    err = FakeSystem(unix_errors={dc.conf_socket("868"): "no socket"}).system
    ok, confirmed, detail = dc.apply_set(err, "868", "TXMODE", "DIRECT")
    assert not ok and not confirmed and "not confirmed" in detail and "no socket" in detail


class _AckStub:
    """A unix client with the base interface (`request` + `send`): a SET gets `ack` (raising it
    when it is an exception), every GET the `status` line. `send` is a no-op, so a client that
    fires the SET and never reads the ack sees no refusal."""

    def __init__(self, ack, status=b"STATUS RADIO=READY TXMODE=MANAGED\n"):
        self.ack, self.status = ack, status

    def request(self, path, payload, timeout, max_bytes):
        if payload.startswith(b"SET "):
            if isinstance(self.ack, BaseException):
                raise self.ack
            return self.ack
        return self.status

    def send(self, path, payload, timeout):
        pass


def _stub_system(ack, status=b"STATUS RADIO=READY TXMODE=MANAGED\n"):
    sys = FakeSystem().system
    sys.unix = _AckStub(ack, status)
    return sys


@pytest.mark.safety("daemon-set-truthful")
def test_a_daemon_err_reply_fails_apply_set():
    # The daemon answers a SET it refuses with ERR <WHY>. A radio param has no read-back, so the
    # ERR is the only evidence: dropping it reported "SENT" for a value the daemon rejected.
    ok, confirmed, detail = dc.apply_set(_stub_system(b"ERR INVALID\n"), "433", "SF", "12")
    assert not ok and not confirmed and "ERR INVALID" in detail


@pytest.mark.safety("daemon-set-truthful")
def test_a_daemon_err_ack_makes_apply_set_fail():
    sock = dc.conf_socket("433")
    fake = FakeSystem(unix_replies={sock: b"STATUS TXMODE=DIRECT\n"},
                      set_replies={sock: b"ERR INVALID\n"})
    ok, confirmed, detail = dc.apply_set(fake.system, "433", "SF", "12")
    assert ok is False and confirmed is False and "ERR INVALID" in detail
    ok, _c, detail = dc.apply_set(fake.system, "433", "TXMODE", "DIRECT")
    assert not ok and "ERR INVALID" in detail                     # no read-back after an ERR


@pytest.mark.safety("daemon-set-truthful")
def test_a_set_without_an_ok_is_never_reported_sent():
    # A timeout, an empty reply or a line that is neither OK nor ERR is no ack: never "sent".
    for ack in (TimeoutError("timed out"), b"", b"TX=1\n"):
        ok, confirmed, detail = dc.apply_set(_stub_system(ack), "433", "SF", "12")
        assert not ok and not confirmed and "unconfirmed" in detail and "SENT" not in detail, ack


@pytest.mark.safety("daemon-set-truthful")
def test_a_malformed_reply_is_classified_on_the_raw_line():
    # The token is classified before any sanitising: a NUL inside "OK" is not OK, and an "ERR"
    # prefix without a space is not a refusal either -- both are invalid replies, never "sent".
    status = b"STATUS RADIO=READY TXMODE=DIRECT\n"
    for ack in (b"O\x00K\n", b"OK \n", b"ERROR INVALID\n", b"ERRINVALID\n"):
        for key, value in (("SF", "12"), ("TXMODE", "DIRECT")):
            ok, confirmed, detail = dc.apply_set(_stub_system(ack, status), "433", key, value)
            assert not ok and not confirmed, (ack, key)
            assert "invalid reply" in detail and "refused" not in detail, (ack, key)


@pytest.mark.safety("daemon-set-truthful")
def test_a_well_formed_reply_keeps_its_meaning():
    # "ERR <WHY>" is a refusal with its (sanitised) reason; a CRLF line ending is still OK.
    ok, confirmed, detail = dc.apply_set(_stub_system(b"ERR INVALID\r\n"), "433", "SF", "12")
    assert not ok and not confirmed and "refused by the daemon (ERR INVALID)" in detail
    ok, _c, detail = dc.apply_set(_stub_system(b"ERR BAD\x07 VALUE\n"), "433", "SF", "12")
    assert not ok and "(ERR BAD VALUE)" in detail
    ok, confirmed, detail = dc.apply_set(_stub_system(b"OK\r\n"), "433", "SF", "12")
    assert ok and not confirmed and "SENT but UNCONFIRMED" in detail
    status = b"STATUS RADIO=READY TXMODE=DIRECT\n"
    ok, confirmed, _d = dc.apply_set(_stub_system(b"OK\r\n", status), "433", "TXMODE", "DIRECT")
    assert ok and confirmed


@pytest.mark.safety("daemon-set-truthful")
def test_confirmable_key_without_valid_ack_is_not_applied():
    # STATUS already shows the requested value (it was there before): without a valid OK the
    # read-back proves nothing, so it must not run and the SET is not applied.
    status = b"STATUS RADIO=READY TXMODE=DIRECT\n"
    for ack in (TimeoutError("timed out"), b"", b"TX=1\n"):
        ok, confirmed, detail = dc.apply_set(_stub_system(ack, status), "433", "TXMODE", "DIRECT")
        assert not ok and not confirmed and "unconfirmed" in detail, ack


class _Recorder:
    """Fake unix client that records every command and answers per-command."""

    def __init__(self, replies=None):
        self.sent: list[bytes] = []
        self.replies = replies or {
            b"GET STATUS\n": _STATUS,
            b"GET STATS\n": _STATS,
            b"GET CHANNEL\n": _CHANNEL,
            b"GET CHANNEL NOSCAN\n": _CHANNEL_NOSCAN,
        }

    def request(self, path, payload, timeout, maxb):
        self.sent.append(payload)
        return self.replies.get(payload, b"OK\n" if payload.startswith(b"SET ") else b"")

    def send(self, *a): ...


def _recording_system(replies=None):
    sys = FakeSystem().system
    rec = _Recorder(replies)
    sys.unix = rec
    return sys, rec


def test_full_status_stats(monkeypatch):
    # read_view is STATUS + STATS only now; channel comes from an explicit call.
    sys, _rec = _recording_system()
    view = dc.read_view(sys, "433")
    assert view.stats["TXOK"] == "1"


def test_read_view_never_issues_a_channel_command():
    """The generic status read must not touch the radio.

    `GET CHANNEL` runs a CAD scan and destroys a frame in flight. read_view() is the generic
    status read — 15 of its callers want only .ready/.reachable/.status — so a scan here made
    every admission check, blocker test and SET read-back cost reception. It must issue STATUS
    and STATS and nothing else."""
    sys, rec = _recording_system()
    view = dc.read_view(sys, "433")
    assert view.reachable
    assert rec.sent == [b"GET STATUS\n", b"GET STATS\n"]
    assert not any(b"CHANNEL" in c for c in rec.sent)
    assert view.channel == {}


def test_passive_channel_read_uses_noscan_and_scan_read_does_not():
    """The two channel helpers must be exactly what their names say."""
    sys, rec = _recording_system()
    ch = dc.read_channel_passive(sys, "433")
    assert rec.sent == [b"GET CHANNEL NOSCAN\n"]
    assert ch["LIVERSSI"] == "-103" and ch["CADSTATE"] == "NOTSCANNED"

    sys2, rec2 = _recording_system()
    ch2 = dc.read_channel_scan(sys2, "433")
    assert rec2.sent == [b"GET CHANNEL\n"]
    assert ch2["CADSTATE"] == "FREE"


def test_mode_readback_never_scans():
    """Confirming a MODE set is automatic, not an operator asking to measure the channel.

    `_VERIFY["MODE"]` mapped to the scanning `GET CHANNEL`, entirely outside read_view() — so
    every `SET MODE=...` ran a CAD scan. It is the instance that hid longest, because the table
    is nowhere near the channel-reading code."""
    assert dc._VERIFY["MODE"][0] == b"GET CHANNEL NOSCAN\n"
    sys, rec = _recording_system()
    dc.apply_set(sys, "433", "MODE", "LORA")
    assert b"GET CHANNEL NOSCAN\n" in rec.sent
    assert b"GET CHANNEL\n" not in rec.sent


def test_no_verify_entry_uses_the_scanning_channel_read():
    """Nothing in the SET read-back table may reach the destructive form.

    Guards the whole table, not just MODE: a future confirmable key that needs a CHANNEL field
    must use NOSCAN, or it silently reintroduces this defect on every SET of that key."""
    for key, (command, _field) in dc._VERIFY.items():
        assert command != b"GET CHANNEL\n", f"{key} read-back would run a CAD scan"


def test_old_daemon_noscan_degrades_without_falling_back():
    """A daemon older than 1.1.0 answers ERR UNKNOWN; we degrade, we do not retry.

    Falling back to `GET CHANNEL` would quietly restore the very defect this removes, so the
    display shows "?" instead and self-heals when the pinned daemon is updated."""
    sys, rec = _recording_system(replies={b"GET CHANNEL NOSCAN\n": b"ERR UNKNOWN\n"})
    assert dc.read_channel_passive(sys, "433") == {}
    assert rec.sent == [b"GET CHANNEL NOSCAN\n"]          # no retry with the scanning form


def test_the_frequency_pattern_accepts_ascii_digits_only():
    # \d matched Unicode digits; ASCII-only [0-9] intended.
    from lhpc.core import daemon_control as dc
    assert dc._FREQ_RE.fullmatch("433.775")
    assert not dc._FREQ_RE.fullmatch("٤٣٣")           # Arabic-Indic digits rejected

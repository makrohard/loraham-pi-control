"""Tests for daemon CONF monitoring and the whitelisted live-settings layer."""

from __future__ import annotations

from lhpc.core import daemon_control as dc
from lhpc.core.probes.backends import FakeSystem

_STATUS = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
_STATS = b"STATS UPTIME=5 RADIO=READY RX=2 TXOK=1 TXERR=0\n"
_CHANNEL = b"CHANNEL RADIO=READY BUSY=0 CADSTATE=FREE RSSI=-95 PACKETRSSI=-95 LIVERSSI=-103 MODE=LORA\n"


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


def test_apply_set_confirms_via_readback():
    # The daemon never acks a SET — apply_set sends it, then GETs the field back and
    # confirms the hardware took the value before reporting success.
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
    assert not ok and not confirmed and "unreachable" in detail


def test_full_status_stats_channel(monkeypatch):
    # Drive distinct replies per command via a stateful fake unix client.
    class Multi:
        def request(self, path, payload, timeout, maxb):
            return {b"GET STATUS\n": _STATUS, b"GET STATS\n": _STATS,
                    b"GET CHANNEL\n": _CHANNEL}.get(payload, b"")
        def send(self, *a): ...
    sys = FakeSystem().system
    sys.unix = Multi()
    view = dc.read_view(sys, "433")
    assert view.stats["TXOK"] == "1" and view.channel["LIVERSSI"] == "-103"


def test_audit_freq_regex_is_ascii_only():
    # AUDIT IN5: \d matched Unicode digits; ASCII-only [0-9] intended.
    from lhpc.core import daemon_control as dc
    assert dc._FREQ_RE.fullmatch("433.775")
    assert not dc._FREQ_RE.fullmatch("٤٣٣")           # Arabic-Indic digits rejected

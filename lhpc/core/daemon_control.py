"""Daemon CONF-socket monitoring and live settings.

Read side (safe, no RF): `GET STATUS`, `GET STATS`, `GET CHANNEL` parsed into
field maps — used by the web monitor (RSSI bars, counters) and the CLI.

Write side (mutating): a STRICT whitelist of `SET <key>=<value>` commands applied
to the CONF socket. Only non-RF tuning is allowed (TX mode, CAD/LBT parameters,
result/queue flags). Enabling a TX-capable mode does not itself transmit — TX
still only happens when a client sends data — but SET is treated as a mutating
action: the web layer requires POST + CSRF + confirmation before calling it.

Verified against daemon 111a (config_status.h / daemon_stats.cpp).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .probes import System

_READ_TIMEOUT = 1.0
_MAX = 4096
_MAX_LINE = 1024        # max bytes in the parsed first status line
_MAX_TOKENS = 64        # max KEY=VALUE tokens parsed from a status line
_MAX_TOKEN_LEN = 128    # max bytes in a single KEY=VALUE token
_KEY_RE = re.compile(r"[A-Za-z0-9_]+")        # daemon CONF key grammar

# Live SET keys the daemon (hardening build) accepts on the CONF socket, verified
# against config_dispatch.h (TX/CAD monitoring) + config_validate.cpp/config_apply.cpp
# (radio params). The daemon applies a SET SILENTLY (no socket reply); only GET
# returns data. NO raw passthrough.
_ALLOWED_SET: dict[str, set[str]] = {
    # TX / CAD monitoring (config_dispatch)
    "TXMODE": {"MANAGED", "DIRECT"},
    "TXRESULT": {"0", "1"},
    "TXQUEUE": {"0", "1"},
    "CADMONITOR": {"0", "1"},
    "CADTXAFTERTIMEOUT": {"0", "1"},
    # Radio parameters (config_apply)
    "MODE": {"LORA", "FSK"},
    "CRC": {"0", "1"},
    "LDRO": {"0", "1", "AUTO"},
    "GETRSSI": {"0", "1"},
    "BW": {"7.8", "10.4", "15.6", "20.8", "31.25", "41.7", "62.5",
           "125.0", "250.0", "500.0"},
}
# Numeric SET keys with an inclusive integer range.
_ALLOWED_SET_INT: dict[str, tuple[int, int]] = {
    "CADRSSI": (-130, 0),
    "CADWAIT": (50, 5000),
    "CADIDLE": (0, 2000),
    "CADPOLL": (10, 500),
    "POWER": (0, 20),
    "SF": (7, 12),
    "CR": (5, 8),
    "PREAMBLE": (6, 65535),
}


@dataclass
class DaemonView:
    band: str
    reachable: bool
    status: dict[str, str] = field(default_factory=dict)
    stats: dict[str, str] = field(default_factory=dict)
    channel: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def radio_state(self) -> str:
        """The daemon-reported RADIO state (READY/FAILED/UNINITIALIZED), upper-cased;
        "" when unreachable or the daemon did not report RADIO."""
        return self.status.get("RADIO", "").upper() if self.reachable else ""

    @property
    def ready(self) -> bool:
        """True ONLY when the band is reachable AND the daemon reports RADIO=READY.
        A reachable daemon reporting RADIO=FAILED or RADIO=UNINITIALIZED is NOT ready:
        it never serves a usable radio band and never permits a dependent launch.
        (A valid CONF response alone is `reachable`, not `ready`.)"""
        return self.reachable and self.radio_state == "READY"


# The daemon serves exactly these bands; only they have a CONF socket. Validated at
# every public boundary so a caller can never make us build an arbitrary socket path.
ALLOWED_BANDS = ("433", "868")


def is_valid_band(band) -> bool:
    return isinstance(band, str) and band in ALLOWED_BANDS


class InvalidBand(ValueError):
    """Raised when a band is not one the daemon serves (never build a path from it)."""


def _prefer_run_socket(run: str, tmp: str) -> str:
    """The daemon serves its sockets under /run/loraham (systemd) or /tmp (direct/user start via
    LORAHAM_SOCKET_DIR). Prefer /run/loraham when the socket EXISTS there, else the /tmp fallback —
    mirrors the daemon's clients (lorachat/igate) so lhpc connects to whichever the running daemon
    actually created. A local stat only; no network/subprocess."""
    import os
    import stat as _stat
    try:
        return run if _stat.S_ISSOCK(os.stat(run).st_mode) else tmp
    except OSError:
        return tmp


def conf_socket(band: str) -> str:
    """The daemon CONF socket path for a VALIDATED band. Refuses any band that is not 433/868 so a
    direct caller can never construct `loraconf<arbitrary>.sock`. Resolves /run/loraham → /tmp."""
    if not is_valid_band(band):
        raise InvalidBand(f"invalid band {band!r} (allowed: {', '.join(ALLOWED_BANDS)})")
    return _prefer_run_socket(f"/run/loraham/loraconf{band}.sock", f"/tmp/loraconf{band}.sock")


# The daemon's frequency validation is `parse_float_exact() && f > 0` and it delegates
# the MHz range to `radio.setFrequency()`, i.e. the SX126x hardware domain that RadioLib
# enforces (150–960 MHz). We mirror BOTH: a strict finite positive decimal (no exponent,
# NaN, inf, or embedded whitespace) inside that hardware domain.
_FREQ_MIN_MHZ = 150.0
_FREQ_MAX_MHZ = 960.0
_FREQ_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")  # ASCII decimal MHz only — \d would match Unicode digits


def _query(system: System, band: str, command: bytes, prefix: str) -> dict[str, str]:
    """Read one bounded status line from the daemon CONF socket and parse `KEY=VALUE`
    tokens. Fail-closed (return {}) on an oversized, over-long, over-tokenized, or
    malformed response — a hostile/garbled daemon socket can never make us parse an
    unbounded reply or hang."""
    raw = system.unix.request(conf_socket(band), command, _READ_TIMEOUT, _MAX)
    if len(raw) >= _MAX:                    # hit the read cap -> oversized, untrusted
        return {}
    first = raw.split(b"\n", 1)[0]
    if len(first) > _MAX_LINE:              # first line implausibly long -> reject
        return {}
    line = re.sub(r"\x1b\[[0-9;]*m", "", first.decode("ascii", "replace")).strip()
    parts = line.split()
    # STRICT: the FIRST token must EQUAL the expected prefix exactly (a "STATUSX"
    # prefix or any other framing is rejected — never a startswith() match).
    if not parts or parts[0] != prefix:
        return {}
    toks = parts[1:]
    if len(toks) > _MAX_TOKENS:             # too many tokens -> reject
        return {}
    out: dict[str, str] = {}
    for tok in toks:
        if len(tok) > _MAX_TOKEN_LEN:       # a single token implausibly long -> reject all
            return {}
        k, sep, v = tok.partition("=")
        # STRICT: every remaining token must be a well-formed, non-empty KEY=VALUE —
        # a bare/malformed token, an empty key/value, an illegal key, or a control
        # character in the value makes the WHOLE response invalid (fail closed). A
        # duplicate key is rejected (a well-formed daemon never repeats a key).
        if (not sep or not _KEY_RE.fullmatch(k) or not v
                or any(ord(c) < 32 for c in v) or k in out):
            return {}
        out[k] = v
    return out


def read_socket_line(system: System, band: str, command: bytes = b"GET STATUS\n") -> str:
    """One RAW, bounded, sanitised line from the daemon CONF socket — for the live 'View Socket'
    monitor. READ-ONLY (a `GET ` command only) and FAIL-CLOSED: returns '' on an invalid band, a
    transport error, an empty/oversized reply, or an over-long first line, so a hostile or garbled
    socket can never make us return unbounded data, hang, or emit control characters. The band is
    validated (never an arbitrary socket path) and the line is stripped to printable ASCII. Same
    bounds as `_query`."""
    if not is_valid_band(band):
        return ""
    if not command.startswith(b"GET "):          # defence in depth: this monitor never writes
        return ""
    try:
        raw = system.unix.request(conf_socket(band), command, _READ_TIMEOUT, _MAX)
    except OSError:
        return ""
    if not raw or len(raw) >= _MAX:               # empty, or hit the read cap -> untrusted
        return ""
    first = raw.split(b"\n", 1)[0]
    if len(first) > _MAX_LINE:                    # first line implausibly long -> reject
        return ""
    line = re.sub(r"\x1b\[[0-9;]*m", "", first.decode("ascii", "replace"))
    return "".join(c for c in line if 32 <= ord(c) < 127).strip()   # printable ASCII only


def read_view(system: System, band: str) -> DaemonView:
    """Read STATUS + STATS + CHANNEL for a band (read-only, bounded)."""
    view = DaemonView(band=band, reachable=False)
    if not is_valid_band(band):
        view.error = f"invalid band {band!r}"
        return view
    try:
        view.status = _query(system, band, b"GET STATUS\n", "STATUS")
        if not view.status:
            view.error = "no STATUS response"
            return view
        view.reachable = True
        view.stats = _query(system, band, b"GET STATS\n", "STATS")
        view.channel = _query(system, band, b"GET CHANNEL\n", "CHANNEL")
    except OSError as exc:
        view.error = f"CONF socket unreachable: {exc}"
    return view


def validate_set(key: str, value: str) -> str | None:
    """Return an error string if (key, value) is not an allowed SET, else None."""
    key = key.upper()
    value = value.upper()
    if key in _ALLOWED_SET:
        if value not in _ALLOWED_SET[key]:
            return f"{key} must be one of {sorted(_ALLOWED_SET[key])}"
        return None
    if key in _ALLOWED_SET_INT:
        lo, hi = _ALLOWED_SET_INT[key]
        try:
            n = int(value)
        except ValueError:
            return f"{key} must be an integer in [{lo}, {hi}]"
        if not (lo <= n <= hi):
            return f"{key} must be in [{lo}, {hi}]"
        return None
    if key == "FREQ":
        # Mirror the daemon's config_value_parse_float_exact + f>0 + setFrequency domain,
        # but STRICTLY: only a plain finite positive decimal (no exponent, NaN, inf, sign,
        # or surrounding/embedded whitespace) inside the SX126x MHz domain is accepted.
        if not _FREQ_RE.fullmatch(value):
            return "FREQ must be a plain decimal MHz value (no exponent, NaN, inf, sign, or whitespace)"
        f = float(value)                         # regex guarantees a finite decimal
        if not math.isfinite(f) or f <= 0.0:
            return "FREQ must be a finite positive MHz value"
        if not (_FREQ_MIN_MHZ <= f <= _FREQ_MAX_MHZ):
            return (f"FREQ {f:g} MHz is outside the SX126x radio domain "
                    f"[{_FREQ_MIN_MHZ:g}, {_FREQ_MAX_MHZ:g}] MHz")
        return None
    if key == "SYNC":
        try:
            n = int(value, 16) if value.startswith("0X") else int(value)
        except ValueError:
            return "SYNC must be a hex (0x12) or decimal byte"
        if not (0 <= n <= 0xFF):
            return "SYNC must be in [0, 0xFF]"
        return None
    return f"setting '{key}' is not permitted"


# Which GET command + field reports each SET key back, for read-back confirmation
# (config_status.h: STATUS reports the TX/CAD flags, CHANNEL reports MODE). The radio
# params (FREQ/SF/BW/CR/CRC/PREAMBLE/SYNC/LDRO/POWER) are applied to the chip but NOT
# echoed by any GET, so they cannot be confirmed over the socket.
_VERIFY: dict[str, tuple[bytes, str]] = {
    "TXMODE": (b"GET STATUS\n", "TXMODE"),
    "TXRESULT": (b"GET STATUS\n", "TXRESULT"),
    "TXQUEUE": (b"GET STATUS\n", "TXQUEUE"),
    "CADMONITOR": (b"GET STATUS\n", "CADMONITOR"),
    "CADTXAFTERTIMEOUT": (b"GET STATUS\n", "CADTXAFTERTIMEOUT"),
    "CADWAIT": (b"GET STATUS\n", "CADWAIT"),
    "CADIDLE": (b"GET STATUS\n", "CADIDLE"),
    "CADPOLL": (b"GET STATUS\n", "CADPOLL"),
    "CADRSSI": (b"GET STATUS\n", "CADRSSI"),
    "GETRSSI": (b"GET STATUS\n", "GETRSSI"),
    "MODE": (b"GET CHANNEL\n", "MODE"),
}


def _norm(val: str) -> str:
    """Canonicalise for comparison: integers numerically, else upper-cased."""
    v = val.strip().upper()
    try:
        f = float(v)
    except ValueError:
        return v
    # A hostile/garbled daemon reply of `inf`/`-inf`/`1e400`/a long digit run makes
    # float() non-finite; int(inf) raises OverflowError. Never let a socket value crash
    # a mutating action — a non-finite/overflowing value is left as its upper-cased text.
    if not math.isfinite(f):
        return v
    try:
        return str(int(f))
    except (ValueError, OverflowError):
        return v


def canonical_value(key: str, value: str) -> str:
    """Canonical STORED form of a VALIDATED (key, value): enum values upper-cased (so `fsk` →
    `FSK`, `managed` → `MANAGED`), integer-range values normalised to a bare decimal (so `028` →
    `28`, `+5` → `5`). FREQ / SYNC keep their validated textual form. Assumes
    `validate_set(key, value)` is None. Lets equivalent values compare equal (no redundant
    override) and display correctly."""
    k, v = key.upper(), str(value).strip()
    if k in _ALLOWED_SET:
        return v.upper()
    if k in _ALLOWED_SET_INT:
        return str(int(v))
    return v


def is_confirmable(key: str) -> bool:
    """True if the daemon reports this key back over a GET, so a SET can be CONFIRMED.
    Radio params (FREQ/SF/BW/…) are applied to the chip but never echoed, so a SET of
    them can only be reported as SENT-but-unconfirmed, never 'applied'."""
    return key.upper() in _VERIFY


def apply_set(system: System, band: str, key: str, value: str) -> tuple[bool, bool, str]:
    """Apply one validated SET to the CONF socket and CONFIRM via read-back.

    The daemon applies a SET SILENTLY (no socket ack; only GET replies). So: send the
    SET, then GET the field that reports it back and check the hardware actually took
    the value before reporting success. Returns (ok, confirmed, detail):
      * read-back matches                     -> (True,  True,  "… confirmed")
      * key the daemon never reports back     -> (True,  False, "… SENT but UNCONFIRMED …")
      * read-back mismatch / not reported     -> (False, False, "NOT applied — daemon reports …")
      * invalid band / rejected / unreachable -> (False, False, …)
    A caller must NEVER present a (True, False, …) result as 'applied' — only 'sent'."""
    err = validate_set(key, value)
    if err:
        return False, False, err
    if not is_valid_band(band):
        return False, False, f"invalid band {band!r}"
    key, value = key.upper(), value.upper()
    sock = conf_socket(band)                     # band already validated above
    try:
        system.unix.send(sock, f"SET {key}={value}\n".encode(), _READ_TIMEOUT)
    except OSError as exc:
        return False, False, f"CONF socket unreachable: {exc}"
    if key not in _VERIFY:
        return True, False, (f"{key}={value} SENT but UNCONFIRMED — a radio param the daemon "
                             "does not report back, so it cannot be verified over the socket "
                             "(check the daemon log)")
    cmd, field = _VERIFY[key]
    prefix = cmd.split()[1].decode("ascii")     # b"GET STATUS\n" -> "STATUS"
    try:
        parsed = _query(system, band, cmd, prefix)   # bounded, fail-closed parser
    except OSError as exc:
        return False, False, f"sent, but read-back failed: {exc}"
    got = parsed.get(field)
    if got is None:
        return False, False, (f"sent, but the daemon did not report {field} back "
                              "(or the read-back was oversized/malformed)")
    if _norm(got) == _norm(value):
        return True, True, f"{key}={value} confirmed by the daemon ({field}={got})"
    return False, False, f"NOT applied — daemon reports {field}={got} (expected {value})"


def allowed_settings() -> dict:
    """Describe the settable keys (for the web control panel / CLI help)."""
    out = {k: {"type": "enum", "choices": sorted(v)} for k, v in _ALLOWED_SET.items()}
    out.update({k: {"type": "int", "min": lo, "max": hi}
                for k, (lo, hi) in _ALLOWED_SET_INT.items()})
    out["FREQ"] = {"type": "str", "hint": "MHz, e.g. 433.900"}
    out["SYNC"] = {"type": "str", "hint": "hex, e.g. 0x12"}
    return out

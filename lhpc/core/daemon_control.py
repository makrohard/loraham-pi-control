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

import re
from dataclasses import dataclass, field

from .probes import System

_READ_TIMEOUT = 1.0
_MAX = 4096

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
# Free-form keys with a custom validator (frequency MHz, sync word hex/dec).
_ALLOWED_SET_FREE = ("FREQ", "SYNC")


@dataclass
class DaemonView:
    band: str
    reachable: bool
    status: dict[str, str] = field(default_factory=dict)
    stats: dict[str, str] = field(default_factory=dict)
    channel: dict[str, str] = field(default_factory=dict)
    error: str = ""


def conf_socket(band: str) -> str:
    return f"/tmp/loraconf{band}.sock"


def _query(system: System, band: str, command: bytes, prefix: str) -> dict[str, str]:
    raw = system.unix.request(conf_socket(band), command, _READ_TIMEOUT, _MAX)
    line = raw.split(b"\n", 1)[0].decode("ascii", "replace").strip()
    if not line.startswith(prefix):
        return {}
    out: dict[str, str] = {}
    for tok in line.split()[1:]:
        k, sep, v = tok.partition("=")
        if sep:
            out[k] = v
    return out


def read_view(system: System, band: str) -> DaemonView:
    """Read STATUS + STATS + CHANNEL for a band (read-only, bounded)."""
    view = DaemonView(band=band, reachable=False)
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
        try:
            if float(value) <= 0:
                raise ValueError
        except ValueError:
            return "FREQ must be a positive frequency in MHz"
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


def _status_field(text: str, field: str) -> str | None:
    for tok in text.split():
        k, sep, v = tok.partition("=")
        if sep and k == field:
            return v
    return None


def _norm(val: str) -> str:
    """Canonicalise for comparison: integers numerically, else upper-cased."""
    v = val.strip().upper()
    try:
        return str(int(float(v)))
    except ValueError:
        return v


def apply_set(system: System, band: str, key: str, value: str) -> tuple[bool, str]:
    """Apply one validated SET to the CONF socket and CONFIRM via read-back.

    The daemon applies a SET SILENTLY (no socket ack; only GET replies). So: send the
    SET, then GET the field that reports it back and check the hardware actually took
    the value before reporting success. Returns (ok, detail):
      * confirmed (read-back matches)         -> (True,  "… confirmed")
      * read-back mismatch                    -> (False, "NOT applied — daemon reports …")
      * key the daemon never reports back     -> (True,  "… sent (cannot confirm over socket)")
      * socket unreachable                    -> (False, "CONF socket unreachable …")
    """
    err = validate_set(key, value)
    if err:
        return False, err
    key, value = key.upper(), value.upper()
    sock = conf_socket(band)
    try:
        system.unix.send(sock, f"SET {key}={value}\n".encode(), _READ_TIMEOUT)
    except OSError as exc:
        return False, f"CONF socket unreachable: {exc}"
    if key not in _VERIFY:
        return True, f"{key}={value} sent (radio param — the daemon does not report it " \
                     f"back, so it cannot be confirmed over the socket; check the daemon log)"
    cmd, field = _VERIFY[key]
    try:
        raw = system.unix.request(sock, cmd, _READ_TIMEOUT, _MAX)
        text = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode("ascii", "replace"))
    except OSError as exc:
        return False, f"sent, but read-back failed: {exc}"
    got = _status_field(text, field)
    if got is None:
        return False, f"sent, but the daemon did not report {field} back"
    if _norm(got) == _norm(value):
        return True, f"{key}={value} confirmed by the daemon ({field}={got})"
    return False, f"NOT applied — daemon reports {field}={got} (expected {value})"


def allowed_settings() -> dict:
    """Describe the settable keys (for the web control panel / CLI help)."""
    out = {k: {"type": "enum", "choices": sorted(v)} for k, v in _ALLOWED_SET.items()}
    out.update({k: {"type": "int", "min": lo, "max": hi}
                for k, (lo, hi) in _ALLOWED_SET_INT.items()})
    out["FREQ"] = {"type": "str", "hint": "MHz, e.g. 433.900"}
    out["SYNC"] = {"type": "str", "hint": "hex, e.g. 0x12"}
    return out

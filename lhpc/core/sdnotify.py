"""Minimal `sd_notify` client (stdlib only, no python-systemd dependency).

Used to push a `STATUS=` line into `systemctl --user status lhpc-web`, which otherwise shows nothing:
the unit redirects its output to `{root}/logs/lhpc-web.log` rather than the journal, and the console
serves a unix socket (nginx owns the only TCP port), so it has no port of its own to report.

BEST EFFORT BY CONTRACT. The unit stays `Type=simple` with `NotifyAccess=main`, so there is no
readiness handshake: a missing socket, a closed socket, or any error here is swallowed and MUST NOT
delay or fail startup. Nothing in the console's operation depends on the notification arriving.
"""

from __future__ import annotations

import os
import socket

# systemd caps a notification datagram at 4 KiB; stay under it and truncate rather than let the
# kernel refuse an oversized send.
MAX_PAYLOAD = 4000


def sanitize(state: str) -> str | None:
    """Make `state` safe for the sd_notify wire format, or None if it cannot be.

    The protocol is newline-separated `KEY=VALUE`, so a payload is NOT free text:
      * an embedded NUL truncates the datagram at the C boundary -> REJECT (never ship half a
        status, which would read as a complete one);
      * a newline/carriage return would inject a SECOND protocol field (a smuggled `READY=1` is the
        one you least want) -> strip it, along with the other C0 controls;
      * over-long payloads are truncated, not sent whole.
    """
    if "\x00" in state:
        return None                                   # unrepresentable: refuse, do not mangle
    cleaned = "".join(ch for ch in state if ch >= " " or ch == "\t")
    return cleaned[:MAX_PAYLOAD]


def notify(state: str) -> bool:
    """Send one sd_notify datagram. True only when it was actually handed to the socket.

    No $NOTIFY_SOCKET (running outside systemd, or NotifyAccess=none) -> False, silently.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    payload = sanitize(state)
    if payload is None:
        return False
    if addr[0] == "@":                                # abstract namespace: leading NUL, not '@'
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.connect(addr)
            sock.sendall(payload.encode("utf-8"))     # EXPLICIT utf-8: the unit has no locale
        return True
    except Exception:                                 # noqa: BLE001 — never let this reach startup
        return False


def notify_status(text: str) -> bool:
    """Convenience: publish `text` as the unit's `Status:` line."""
    return notify(f"STATUS={text}")

"""One LHPC record line per console power action (Reboot / Shut down), written before the trigger spawns.

An orderly poweroff asked from the console used to leave no LHPC line at all: two "abrupt ends" of the 11-03
field test were such poweroffs, and no log named them. The record is one line in logs/power-actions.log,
flushed to disk before the trigger runs, and the same line goes to the console's log (the journal):

    2026-11-07T10:30:00Z power poweroff requested by client phone-dl1abc

The client is the CN of the verified client certificate nginx passed (`X-LHPC-Client-DN`), or "-" when the
console was reached without one. A record that cannot be written never blocks the action; the caller says so.
"""
from __future__ import annotations

import logging
import os
import re
import time

from . import runtime_fs
from .paths import PathContainmentError, Paths

RECORD = ("logs", "power-actions.log")
_CN = re.compile(r"(?:^|[,/])\s*CN=([^,/]+)")
_UNSAFE = re.compile(r"[^A-Za-z0-9._@ -]")       # one log line: no newline, no control or shell character
NAME_MAX = 64


def client_name(dn) -> str:
    """The CN of a client certificate's subject DN (nginx's `$ssl_client_s_dn`, RFC 2253 `CN=a,O=b` or the old
    `/O=b/CN=a`), each unsafe character as "?", at most NAME_MAX; "-" when there is none."""
    m = _CN.search(dn) if isinstance(dn, str) else None
    name = _UNSAFE.sub("?", m.group(1).strip())[:NAME_MAX] if m else ""
    return name or "-"


def line(kind: str, client_dn, now: float | None = None) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))
    return f"{stamp} power {kind} requested by client {client_name(client_dn)}"


def write(paths: Paths, kind: str, client_dn, now: float | None = None) -> tuple[str, str]:
    """Append the record and flush it to disk, then log it. Returns (line, "") or (line, why it was not written)."""
    text = line(kind, client_dn, now)
    logging.getLogger("lhpc.power").warning(text)
    try:
        with runtime_fs.open_log_append(paths, paths.under(*RECORD)) as fh:
            fh.write((text + "\n").encode("ascii", "replace"))
            fh.flush()
            os.fsync(fh.fileno())
    except (OSError, PathContainmentError, ValueError) as exc:
        return text, f"{type(exc).__name__}: {exc}"
    return text, ""

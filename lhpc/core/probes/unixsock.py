"""Read-only Unix-socket probes.

Two probes:
  * existence/type — does the path exist and is it actually a socket?
  * daemon readiness — a bounded `GET STATUS` exchange against a LoRaHAM daemon
    111a CONF socket.

The readiness probe is strictly read-only: it sends only `GET STATUS\n` (never a
`SET`), enforces a short timeout and a hard response-size limit, and converts any
malformed/absent/oversize response into degraded/unknown evidence — never an
exception and never a false "healthy".

Protocol (verified against daemon 111a source, config_status.h):
  request : ``GET STATUS\n``  (case-insensitive, newline-terminated)
  reply   : one ``\n``-terminated line, e.g.
            ``STATUS RADIO=READY TX=0 ... TXMODE=DIRECT CADWAIT=1500 ...``
  RADIO   : ``READY`` | ``FAILED`` | ``UNINITIALIZED``
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backends import System

_STATUS_REQUEST = b"GET STATUS\n"
_READ_TIMEOUT_S = 1.0
_MAX_BYTES = 4096


@dataclass
class SocketProbe:
    path: str
    exists: bool
    is_socket: bool
    evidence: dict[str, str] = field(default_factory=dict)


def probe_socket(system: System, path: str) -> SocketProbe:
    exists = system.fs.exists(path)
    is_sock = system.fs.is_socket(path) if exists else False
    ev = {"path": path, "exists": "yes" if exists else "no"}
    if exists and not is_sock:
        ev["type"] = "not-a-socket"
    return SocketProbe(path=path, exists=exists, is_socket=is_sock, evidence=ev)


@dataclass
class DaemonStatus:
    reachable: bool             # the CONF socket answered with a parseable STATUS line
    ready: bool                 # RADIO=READY
    radio: str = ""             # READY | FAILED | UNINITIALIZED | ""
    tx_mode: str = ""           # MANAGED | DIRECT | ""
    fields: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)


def probe_daemon_status(system: System, path: str) -> DaemonStatus:
    ev = {"path": path, "request": "GET STATUS"}
    try:
        raw = system.unix.request(path, _STATUS_REQUEST, _READ_TIMEOUT_S, _MAX_BYTES)
    except OSError as exc:
        ev["error"] = f"connect/read failed: {exc}"
        return DaemonStatus(reachable=False, ready=False, evidence=ev)

    if not raw:
        ev["error"] = "empty response"
        return DaemonStatus(reachable=False, ready=False, evidence=ev)

    line = raw.split(b"\n", 1)[0].decode("ascii", "replace").strip()
    if not line.startswith("STATUS"):
        ev["error"] = "malformed response (no STATUS prefix)"
        ev["sample"] = line[:80]
        return DaemonStatus(reachable=False, ready=False, evidence=ev)

    fields = _parse_status_fields(line)
    radio = fields.get("RADIO", "")
    tx_mode = fields.get("TXMODE", "")
    ev["radio"] = radio or "?"
    if tx_mode:
        ev["tx_mode"] = tx_mode
    return DaemonStatus(
        reachable=True,
        ready=(radio == "READY"),
        radio=radio,
        tx_mode=tx_mode,
        fields=fields,
        evidence=ev,
    )


def _parse_status_fields(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in line.split()[1:]:  # drop the leading "STATUS"
        key, sep, value = token.partition("=")
        if sep:
            out[key] = value
    return out

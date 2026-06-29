"""ONE host/family-aware endpoint parser + TCP readiness matcher, shared by manifest
validation, start readiness, and stop endpoint-cessation checks.

A `ready = true` TCP endpoint is matched by PORT **and** family/host — a listener on the
same port but the wrong address family (or a non-loopback address) does NOT satisfy a
loopback ready endpoint. Manifest validation already restricts ready endpoints to
loopback hosts; this keeps the runtime probe consistent with that contract.

Documented host semantics (ready endpoints are loopback-only):
  * ``127.0.0.1``  -> IPv4 loopback; satisfied by an IPv4 listener on 127.0.0.1 or
                      0.0.0.0 (INADDR_ANY, which includes loopback).
  * ``::1``        -> IPv6 loopback; satisfied by an IPv6 listener on ::1 or :: (the
                      IPv6 wildcard).
  * ``localhost``  -> either family; satisfied by an IPv4 OR IPv6 loopback listener.
IPv4 and IPv6 evidence stays distinct: an IPv6-only listener never satisfies an IPv4
endpoint and vice-versa.
"""

from __future__ import annotations

from .backends import System

_V4_LOOPBACK = {"127.0.0.1", "0.0.0.0"}
_V6_LOOPBACK = {"::1", "::"}


def parse_endpoint(address: str) -> tuple[str, int, str | None]:
    """Parse ``host:port`` (or ``[ipv6]:port``) into (host, port, family). family is
    ``"ipv4"`` / ``"ipv6"`` / ``None`` (``localhost`` -> either). Raises ValueError on a
    malformed address."""
    address = address.strip()
    if address.startswith("["):                       # [::1]:4403
        host, _, rest = address[1:].partition("]")
        if not rest.startswith(":"):
            raise ValueError(f"malformed bracketed endpoint {address!r}")
        port_s = rest[1:]
        family = "ipv6"
    else:
        host, sep, port_s = address.rpartition(":")
        if not sep:
            raise ValueError(f"endpoint {address!r} has no port")
        if host == "localhost":
            family = None
        elif ":" in host:                             # bare IPv6 literal
            family = "ipv6"
        else:
            family = "ipv4"
    try:
        port = int(port_s)
    except ValueError as exc:
        raise ValueError(f"endpoint {address!r} has a non-numeric port") from exc
    if not (0 < port < 65536):
        raise ValueError(f"endpoint {address!r} port out of range")
    return host, port, family


# Owner-PID resolution budget: matching a listener is cheap, but resolving its owning
# PID scans /proc/*/fd, so it is time-bounded (mirrors probes.net._OWNER_BUDGET_S).
_OWNER_BUDGET_S = 0.25


def _matched_listeners(listeners, port: int, family: str | None) -> list:
    """The loopback listeners of the DECLARED family on `port` (localhost -> either).
    A wrong-family or non-loopback listener on the same port is never matched."""
    out = []
    for l in listeners:
        if l.port != port:
            continue
        if family in (None, "ipv4") and l.family == "ipv4" and l.ip in _V4_LOOPBACK:
            out.append(l)
        elif family in (None, "ipv6") and l.family == "ipv6" and l.ip in _V6_LOOPBACK:
            out.append(l)
    return out


def tcp_endpoint_match(system: System, address: str,
                       resolve_owner: bool = True) -> tuple[bool, str, int | None, bool]:
    """Family/host-aware TCP endpoint match — the ONE matcher shared by status, start
    readiness, and stop cessation. Returns (present, evidence, owner_pid, owner_incomplete).
    The owner PID, when resolved, is that of the MATCHED loopback listener — never a
    wrong-family listener that merely shares the port."""
    try:
        host, port, family = parse_endpoint(address)
    except ValueError as exc:
        return False, f"{address}: invalid ({exc})", None, False
    matched = _matched_listeners(system.procfs.tcp_listeners(), port, family)
    fam = family or "localhost"
    if not matched:
        return False, f"{address}: absent (family={fam})", None, False
    owner_pid, incomplete = (None, False)
    if resolve_owner:
        owner_pid, incomplete = system.procfs.owner_pid(matched[0].inode, _OWNER_BUDGET_S)
    ev = f"{address}: present (family={fam}"
    ev += f", owner_pid={owner_pid})" if owner_pid is not None else ")"
    return True, ev, owner_pid, incomplete


def tcp_endpoint_present(system: System, address: str) -> tuple[bool, str]:
    """True iff a loopback listener of the DECLARED family is present on the endpoint's
    port. Returns (present, evidence). A wrong-family/host listener on the same port does
    NOT count. Thin wrapper over `tcp_endpoint_match` (no owner-PID resolution)."""
    present, evidence, _pid, _inc = tcp_endpoint_match(system, address, resolve_owner=False)
    return present, evidence

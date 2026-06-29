"""Read-only TCP listening-endpoint probe.

Inspects locally listening sockets (IPv4 + IPv6) from /proc; never opens a
remote connection. Owner (PID) resolution is attempted only within a strict time
budget and is reported as incomplete rather than blocking or guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import System

# Total wall-clock budget for resolving the owning PID of a listener.
_OWNER_BUDGET_S = 0.15


@dataclass
class TcpProbe:
    port: int
    listening: bool
    families: list[str]          # e.g. ["ipv4", "ipv6"]
    owner_pid: int | None = None
    owner_incomplete: bool = False
    evidence: dict[str, str] | None = None


def probe_tcp_port(system: System, port: int, resolve_owner: bool = True) -> TcpProbe:
    matches = [lst for lst in system.procfs.tcp_listeners() if lst.port == port]
    families = sorted({lst.family for lst in matches})
    ev = {"port": str(port)}
    if not matches:
        ev["listening"] = "no"
        return TcpProbe(port=port, listening=False, families=[], evidence=ev)

    ev["listening"] = "yes"
    ev["families"] = ",".join(families)
    owner_pid: int | None = None
    incomplete = False
    if resolve_owner:
        owner_pid, incomplete = system.procfs.owner_pid(matches[0].inode, _OWNER_BUDGET_S)
        if owner_pid is not None:
            ev["owner_pid"] = str(owner_pid)
        elif incomplete:
            ev["owner"] = "incomplete (time budget)"
    return TcpProbe(
        port=port,
        listening=True,
        families=families,
        owner_pid=owner_pid,
        owner_incomplete=incomplete,
        evidence=ev,
    )

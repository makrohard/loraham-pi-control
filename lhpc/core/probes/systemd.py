"""Read-only systemd state probe (system and user scope).

Uses `systemctl show` with a fixed set of properties — robust across versions
and unambiguous about "not found" vs "inactive" vs "failed". Never starts,
stops, enables or reloads anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..model import SystemdScope
from .backends import System

_TIMEOUT_S = 3.0
_PROPS = "ActiveState,SubState,LoadState,UnitFileState"


class UnitState(str, Enum):
    ACTIVE = "active"
    ACTIVATING = "activating"
    INACTIVE = "inactive"
    FAILED = "failed"
    NOT_FOUND = "not-found"
    UNAVAILABLE = "unavailable"   # no bus / permission denied
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class UnitProbe:
    unit: str
    scope: SystemdScope
    state: UnitState
    enabled: str = ""            # UnitFileState (enabled/disabled/static/…)
    evidence: dict[str, str] = field(default_factory=dict)


def probe_unit(system: System, unit: str, scope: SystemdScope) -> UnitProbe:
    argv = ["systemctl"]
    if scope is SystemdScope.USER:
        argv.append("--user")
    argv += ["show", unit, "--property", _PROPS]
    result = system.runner.run(argv, timeout=_TIMEOUT_S)

    ev: dict[str, str] = {"argv": " ".join(argv)}
    if result.not_found:
        ev["error"] = "systemctl not found"
        return UnitProbe(unit, scope, UnitState.UNAVAILABLE, evidence=ev)
    if result.timed_out:
        return UnitProbe(unit, scope, UnitState.TIMEOUT, evidence=ev)

    stderr = result.stderr.lower()
    if "failed to connect to bus" in stderr or "permission denied" in stderr:
        ev["error"] = result.stderr.strip()
        return UnitProbe(unit, scope, UnitState.UNAVAILABLE, evidence=ev)

    props = _parse_show(result.stdout)
    ev.update(props)
    load = props.get("LoadState", "")
    active = props.get("ActiveState", "")
    enabled = props.get("UnitFileState", "")

    if load == "not-found":
        return UnitProbe(unit, scope, UnitState.NOT_FOUND, enabled=enabled, evidence=ev)
    state = {
        "active": UnitState.ACTIVE,
        "activating": UnitState.ACTIVATING,
        "reloading": UnitState.ACTIVE,
        "deactivating": UnitState.ACTIVE,
        "inactive": UnitState.INACTIVE,
        "failed": UnitState.FAILED,
    }.get(active, UnitState.UNKNOWN)
    return UnitProbe(unit, scope, state, enabled=enabled, evidence=ev)


def _parse_show(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out

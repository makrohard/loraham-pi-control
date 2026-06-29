"""Tests for the read-only systemd probe (system + user scope, error states)."""

from __future__ import annotations

from lhpc.core.model import SystemdScope
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.probes.systemd import UnitState, probe_unit

_PROPS = "ActiveState,SubState,LoadState,UnitFileState"


def _argv(unit: str, user: bool = False) -> tuple[str, ...]:
    base = ["systemctl"]
    if user:
        base.append("--user")
    base += ["show", unit, "--property", _PROPS]
    return tuple(base)


def _show(active: str, load: str = "loaded", enabled: str = "enabled") -> str:
    return f"ActiveState={active}\nSubState=x\nLoadState={load}\nUnitFileState={enabled}\n"


def test_active_system_unit():
    fake = FakeSystem(commands={_argv("d@433.service"): CommandResult(0, _show("active"), "")})
    p = probe_unit(fake.system, "d@433.service", SystemdScope.SYSTEM)
    assert p.state is UnitState.ACTIVE
    assert p.enabled == "enabled"


def test_failed_unit():
    fake = FakeSystem(commands={_argv("d.service"): CommandResult(0, _show("failed"), "")})
    assert probe_unit(fake.system, "d.service", SystemdScope.SYSTEM).state is UnitState.FAILED


def test_inactive_unit():
    fake = FakeSystem(commands={_argv("d.service"): CommandResult(0, _show("inactive"), "")})
    assert probe_unit(fake.system, "d.service", SystemdScope.SYSTEM).state is UnitState.INACTIVE


def test_not_found_unit():
    fake = FakeSystem(commands={_argv("x.service"): CommandResult(0, _show("inactive", load="not-found"), "")})
    assert probe_unit(fake.system, "x.service", SystemdScope.SYSTEM).state is UnitState.NOT_FOUND


def test_user_scope_no_bus_is_unavailable():
    argv = _argv("hub.service", user=True)
    fake = FakeSystem(commands={argv: CommandResult(1, "", "Failed to connect to bus: no medium")})
    p = probe_unit(fake.system, "hub.service", SystemdScope.USER)
    assert p.state is UnitState.UNAVAILABLE


def test_timeout_is_timeout():
    fake = FakeSystem(commands={_argv("d.service"): CommandResult(124, "", "", timed_out=True)})
    assert probe_unit(fake.system, "d.service", SystemdScope.SYSTEM).state is UnitState.TIMEOUT


def test_systemctl_missing_is_unavailable():
    fake = FakeSystem(commands={_argv("d.service"): CommandResult(127, "", "", not_found=True)})
    assert probe_unit(fake.system, "d.service", SystemdScope.SYSTEM).state is UnitState.UNAVAILABLE

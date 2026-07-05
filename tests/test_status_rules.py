"""Tests for the component status state rules (running/degraded/stopped/failed/
unknown/not-installed), driven entirely by fakes."""

from __future__ import annotations

from pathlib import Path

from lhpc.core.model import (
    Component,
    ComponentKind,
    EndpointSpec,
    ProcessSpec,
    RunState,
    SourceSpec,
    UnitRef,
)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem, Listener
from lhpc.core.status import StatusProber

_PROPS = "ActiveState,SubState,LoadState,UnitFileState"


def _unit_argv(unit: str) -> tuple[str, ...]:
    return ("systemctl", "show", unit, "--property", _PROPS)


def _show(active: str, load: str = "loaded") -> str:
    return f"ActiveState={active}\nSubState=x\nLoadState={load}\nUnitFileState=enabled\n"


def _prober(fake: FakeSystem, tmp_path: Path) -> StatusProber:
    return StatusProber(fake.system, Paths(runtime_root=tmp_path))


def _svc(**kw) -> Component:
    kw.setdefault("name", kw["id"])
    kw.setdefault("kind", ComponentKind.SERVICE)
    return Component(**kw)


def test_running_with_active_unit_and_listening_endpoint(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:7000"),))
    fake = FakeSystem(
        commands={_unit_argv("x.service"): CommandResult(0, _show("active"), "")},
        listeners=[Listener("ipv4", "127.0.0.1", 7000, 1)],
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.RUNNING


def test_degraded_when_active_but_endpoint_absent(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:7000"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("active"), "")})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.DEGRADED


def test_stopped_when_inactive_and_no_process(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("inactive"), "")})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.STOPPED


def test_failed_unit(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("failed"), "")})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.FAILED


def test_unknown_when_probe_unavailable(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(127, "", "", not_found=True)})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.UNKNOWN


def test_running_by_process_only_no_systemd(tmp_path):
    # Proves a verdict needs real evidence (matched process), not a PID file.
    comp = _svc(id="x", process=ProcessSpec(exec_name="loraham_daemon", any_args=("433",)))
    fake = FakeSystem(cmdlines_data={42: ["loraham_daemon", "--radio", "433"]})
    st = _prober(fake, tmp_path).assess_component(comp)
    assert st.run_state is RunState.RUNNING and st.pids == [42]


def test_socket_present_but_no_process_is_not_running(tmp_path):
    # A provider socket existing is NOT sufficient to call a service running.
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                process=ProcessSpec(exec_name="loraham_daemon", any_args=("433",)),
                endpoints=(EndpointSpec(kind="unix", address="/tmp/loraconf433.sock", role="provider"),))
    fake = FakeSystem(
        commands={_unit_argv("x.service"): CommandResult(0, _show("inactive"), "")},
        sockets={"/tmp/loraconf433.sock"},
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.STOPPED


def test_not_installed_when_source_missing(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                source=SourceSpec(path="src/x", pin_commit="a" * 40))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("inactive"), "")})
    # runtime root (tmp_path) exists, but the component source path does not.
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.NOT_INSTALLED


def test_daemon_ready_endpoint_makes_running(tmp_path):
    comp = _svc(id="d", units=(UnitRef("d.service"),),
                endpoints=(EndpointSpec(kind="unix", address="/tmp/loraconf433.sock",
                                        role="provider", readiness="daemon-status"),))
    fake = FakeSystem(
        commands={_unit_argv("d.service"): CommandResult(0, _show("active"), "")},
        sockets={"/tmp/loraconf433.sock"},
        unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=READY TXMODE=DIRECT\n"},
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.RUNNING


def test_daemon_not_ready_endpoint_makes_degraded(tmp_path):
    comp = _svc(id="d", units=(UnitRef("d.service"),),
                endpoints=(EndpointSpec(kind="unix", address="/tmp/loraconf433.sock",
                                        role="provider", readiness="daemon-status"),))
    fake = FakeSystem(
        commands={_unit_argv("d.service"): CommandResult(0, _show("active"), "")},
        sockets={"/tmp/loraconf433.sock"},
        unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=UNINITIALIZED\n"},
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.DEGRADED


def test_path_endpoint_symlink_to_outside_device_reads_present(tmp_path):
    """A `path` endpoint that is a SYMLINK to a node OUTSIDE the runtime root (e.g. a socat
    PTY link `state/loraham_kiss -> /dev/pts/N`) must resolve to the IN-ROOT leaf and read
    PRESENT — not be mistaken for a containment escape and reported absent. Strict `under()`
    realpath-follows the leaf, sees it escape, and refuses (the bug that stuck the KISS
    serial bridge in DEGRADED); the lenient (path) resolution contains it lexically."""
    import os

    from lhpc.core.probes import RealSystem
    (tmp_path / "state").mkdir()
    outside = tmp_path.parent / (tmp_path.name + "_dev_target")
    outside.write_text("x")                               # stands in for /dev/pts/N
    (tmp_path / "state" / "loraham_kiss").symlink_to(outside)
    prober = StatusProber(RealSystem(), Paths(runtime_root=tmp_path))
    lenient = prober._resolve_addr("state/loraham_kiss", lenient=True)
    strict = prober._resolve_addr("state/loraham_kiss", lenient=False)
    # lenient -> the in-root leaf, which os.path.exists follows to the (existing) target
    assert lenient.endswith("state/loraham_kiss") and os.path.exists(lenient)
    # strict -> a guaranteed-absent sentinel (containment refusal), i.e. the old buggy path
    assert strict.endswith(".unresolved-endpoint") and not os.path.exists(strict)


def test_path_endpoint_lenient_still_rejects_dotdot_escape(tmp_path):
    """Lexical leniency for path endpoints must NOT allow a `..` escape — only a symlink
    leaf to an external node is tolerated, never a path that lexically leaves the root."""
    from lhpc.core.probes import RealSystem
    prober = StatusProber(RealSystem(), Paths(runtime_root=tmp_path))
    resolved = prober._resolve_addr("../evil", lenient=True)
    assert resolved.endswith(".unresolved-endpoint")     # refused -> absent sentinel

"""Status composition — maps probe evidence onto component status.

Stateless and bounded. On every call it reconstructs real state from systemd,
process identity, endpoint probes and source state. It never trusts a stale PID
file: a "running" verdict always carries process and/or systemd evidence. Every
probe error becomes evidence (UNKNOWN), never an exception.

The status state rules are implemented in `_run_state_for_service`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import (
    Component,
    ComponentKind,
    ComponentStatus,
    DependencyObservation,
    EndpointObservation,
    ProfileState,
    RunState,
    SourceState,
    Stack,
    TxState,
)
from .paths import Paths
from .probes import System
from .probes.process import probe_process
from .probes.net import probe_tcp_port
from .probes.source import SourceProbe, probe_source

_NA_SOURCE = SourceProbe(state=SourceState.NOT_APPLICABLE)
from .probes.systemd import UnitState, probe_unit
from .probes.unixsock import probe_daemon_status, probe_socket
from . import resources as resources_mod


@dataclass
class StackStatus:
    stack: Stack
    components: dict[str, ComponentStatus] = field(default_factory=dict)


@dataclass
class Snapshot:
    stacks: list[StackStatus] = field(default_factory=list)
    conflicts: list = field(default_factory=list)   # list[ResourceConflict]
    runtime_root_exists: bool = False

    def stack(self, stack_id: str) -> StackStatus | None:
        for s in self.stacks:
            if s.stack.id == stack_id:
                return s
        return None


class StatusProber:
    """Bounded, read-only status assessment for components and snapshots."""

    def __init__(self, system: System, paths: Paths, profiles: dict | None = None) -> None:
        self._system = system
        self._paths = paths
        self._profiles = profiles or {}

    # -- whole-snapshot ----------------------------------------------------

    def assess_stacks(self, stacks: tuple[Stack, ...]) -> Snapshot:
        snap = Snapshot(runtime_root_exists=self._paths.runtime_root_exists)
        index: dict[str, ComponentStatus] = {}
        all_components: list[Component] = []
        for stack in stacks:
            ss = StackStatus(stack=stack)
            for comp in stack.components:
                status = self.assess_component(comp)
                ss.components[comp.id] = status
                index[comp.id] = status
                all_components.append(comp)
            snap.stacks.append(ss)

        # Attach runtime dependency observations now that all are computed.
        for stack in stacks:
            for comp in stack.components:
                st = index[comp.id]
                for dep_id in comp.depends_on:
                    dep_status = index.get(dep_id)
                    dep_comp = _find(stacks, dep_id)
                    st.dependencies.append(
                        DependencyObservation(
                            component_id=dep_id,
                            run_state=dep_status.run_state if dep_status else RunState.UNKNOWN,
                            band=dep_comp.band if dep_comp else "",
                        )
                    )

        running = {cid for cid, s in index.items()
                   if s.run_state in (RunState.RUNNING, RunState.DEGRADED)}
        snap.conflicts = resources_mod.interpret_conflicts(all_components, running)
        return snap

    # -- single component --------------------------------------------------

    def assess_component(self, comp: Component) -> ComponentStatus:
        status = ComponentStatus(component_id=comp.id)
        src = self._assess_source(comp)
        status.source_state = src.state
        status.source_version = src.version
        status.profile_state = self._profile_state(comp, src)

        # Libraries/firmware have no run state. Oneshots normally don't either —
        # EXCEPT interactive apps (chat/voice/meshcli/GUI), which are long-running
        # processes the operator starts by hand: assess them by process so the dash
        # shows running/stopped (not "not applicable").
        if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE) or (
                comp.kind == ComponentKind.ONESHOT and not comp.interactive):
            status.run_state = RunState.NOT_APPLICABLE
            status.tx_state = TxState.DISABLED
            return status

        # SERVICE (and interactive oneshots)
        unit_states = [probe_unit(self._system, u.name, u.scope) for u in comp.units]
        for up in unit_states:
            status.evidence[f"unit:{up.unit}"] = up.state.value

        proc_matched = False
        if comp.process is not None:
            pm = probe_process(self._system, comp.process)
            proc_matched = pm.matched
            status.pids = pm.pids
            status.evidence.update({f"process.{k}": v for k, v in pm.evidence.items()})

        endpoints, all_ready, any_present, has_expected = self._assess_endpoints(comp)
        status.endpoints = endpoints

        status.run_state = _run_state_for_service(
            unit_states=unit_states,
            proc_matched=proc_matched,
            has_expected_endpoints=has_expected,
            all_endpoints_ready=all_ready,
            source_state=status.source_state,
            has_source=comp.source is not None,
            runtime_root_exists=self._paths.runtime_root_exists,
        )

        if not comp.tx_capable:
            status.tx_state = TxState.DISABLED
        elif status.run_state in (RunState.RUNNING, RunState.DEGRADED):
            status.tx_state = TxState.UNKNOWN
        else:
            status.tx_state = TxState.DISABLED
        return status

    # -- helpers -----------------------------------------------------------

    def _assess_source(self, comp: Component):
        if comp.source is None:
            return _NA_SOURCE
        abs_path = str(self._paths.resolve_source(comp.source.path))
        return probe_source(self._system, comp.source, abs_path)

    def _profile_state(self, comp: Component, src) -> ProfileState:
        # confirmed-working only when a profile exists AND the source is clean and
        # at exactly the profile's commit. A dirty/diverged tree is never confirmed.
        profile = self._profiles.get(comp.id)
        if (profile and src.state is SourceState.MATCH
                and src.head and src.head == profile.commit):
            return ProfileState.CONFIRMED_WORKING
        return _profile_from_source(src.state)

    def _assess_endpoints(
        self, comp: Component
    ) -> tuple[list[EndpointObservation], bool, bool, bool]:
        observations: list[EndpointObservation] = []
        expected_present: list[bool] = []
        for spec in comp.endpoints:
            obs = EndpointObservation(spec=spec)
            if spec.kind == "tcp":
                host, _, port = spec.address.partition(":")
                tcp = probe_tcp_port(self._system, int(port))
                obs.present = tcp.listening
                obs.owner_pid = tcp.owner_pid
                obs.owner_incomplete = tcp.owner_incomplete
                obs.detail = "listening" if tcp.listening else "not listening"
            elif spec.kind == "unix":
                sock = probe_socket(self._system, spec.address)
                obs.present = sock.is_socket
                obs.detail = "socket present" if sock.is_socket else "absent"
                if sock.is_socket and spec.readiness == "daemon-status":
                    ds = probe_daemon_status(self._system, spec.address)
                    if ds.reachable:
                        obs.detail = f"RADIO={ds.radio or '?'}"
                        if ds.tx_mode:
                            obs.detail += f" TXMODE={ds.tx_mode}"
                        # Present-but-not-ready keeps it out of the "ready" set.
                        obs.present = ds.ready
                    else:
                        obs.present = False
                        obs.detail = ds.evidence.get("error", "status unreadable")
            elif spec.kind == "path":
                present = self._system.fs.exists(spec.address)
                obs.present = present
                obs.detail = "present" if present else "absent"
            observations.append(obs)
            if spec.role in ("listener", "provider"):
                expected_present.append(obs.present)

        has_expected = bool(expected_present)
        all_ready = all(expected_present) if has_expected else False
        any_present = any(expected_present) if has_expected else False
        return observations, all_ready, any_present, has_expected


def _run_state_for_service(
    *,
    unit_states,
    proc_matched: bool,
    has_expected_endpoints: bool,
    all_endpoints_ready: bool,
    source_state: SourceState,
    has_source: bool,
    runtime_root_exists: bool,
) -> RunState:
    states = [u.state for u in unit_states]
    systemd_active = any(s is UnitState.ACTIVE for s in states)
    systemd_failed = any(s is UnitState.FAILED for s in states)
    systemd_unavailable = bool(states) and all(
        s in (UnitState.UNAVAILABLE, UnitState.TIMEOUT) for s in states
    )

    if systemd_failed and not (proc_matched or systemd_active):
        return RunState.FAILED

    running_evidence = systemd_active or proc_matched
    if running_evidence:
        if has_expected_endpoints:
            return RunState.RUNNING if all_endpoints_ready else RunState.DEGRADED
        return RunState.RUNNING

    # Not running.
    if has_source and (source_state in (SourceState.MISSING, SourceState.NOT_A_REPO)
                       or not runtime_root_exists):
        return RunState.NOT_INSTALLED
    if systemd_unavailable and not proc_matched and not has_expected_endpoints:
        return RunState.UNKNOWN
    return RunState.STOPPED


# Severity ranking used to roll a stack's components up to a single badge.
_SEVERITY = {
    RunState.FAILED: 6,
    RunState.DEGRADED: 5,
    RunState.UNKNOWN: 4,
    RunState.RUNNING: 3,
    RunState.NOT_INSTALLED: 2,
    RunState.STOPPED: 1,
    RunState.NOT_APPLICABLE: 0,
}


def summarize(snapshot: Snapshot) -> dict:
    """Counts for the dashboard overview tiles (presentation-neutral)."""
    states: dict[str, int] = {}
    components = 0
    for ss in snapshot.stacks:
        for st in ss.components.values():
            components += 1
            states[st.run_state.value] = states.get(st.run_state.value, 0) + 1
    return {"stacks": len(snapshot.stacks), "components": components, "states": states}


def stack_dependencies(stacks) -> dict[str, list[str]]:
    """Map each stack id to the other stack ids it depends on (via component
    `depends_on` edges that cross stack boundaries)."""
    owner = {c.id: s.id for s in stacks for c in s.components}
    deps: dict[str, list[str]] = {}
    for s in stacks:
        found: set[str] = set()
        for c in s.components:
            for dep in c.depends_on:
                ds = owner.get(dep)
                if ds and ds != s.id:
                    found.add(ds)
        deps[s.id] = sorted(found)
    return deps


def rollup_states(snapshot: Snapshot) -> dict[str, str]:
    """Worst (highest-severity) run state per stack, as a value string."""
    out: dict[str, str] = {}
    for ss in snapshot.stacks:
        worst = RunState.NOT_APPLICABLE
        for st in ss.components.values():
            if _SEVERITY[st.run_state] > _SEVERITY[worst]:
                worst = st.run_state
        out[ss.stack.id] = worst.value
    return out


def _profile_from_source(source_state: SourceState) -> ProfileState:
    return {
        SourceState.MATCH: ProfileState.INSTALLED_UNVALIDATED,
        SourceState.DIFFERS: ProfileState.CANDIDATE_AVAILABLE,
        SourceState.DIRTY: ProfileState.LOCALLY_MODIFIED,
    }.get(source_state, ProfileState.UNKNOWN)


def _find(stacks: tuple[Stack, ...], component_id: str) -> Component | None:
    for stack in stacks:
        c = stack.component(component_id)
        if c:
            return c
    return None

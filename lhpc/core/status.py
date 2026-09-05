"""Status composition — maps probe evidence onto component status.

Stateless and bounded. On every call it reconstructs real state from systemd,
process identity, endpoint probes and source state. It never trusts a stale PID
file: a "running" verdict always carries process and/or systemd evidence. Every
probe error becomes evidence (UNKNOWN), never an exception.

The status state rules are implemented in `_run_state_for_service`.
"""

from __future__ import annotations

import os
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
from .probes.endpoints import tcp_endpoint_match
from .probes.process import probe_process
from .probes.source import SourceProbe, probe_source

_NA_SOURCE = SourceProbe(state=SourceState.NOT_APPLICABLE)
from . import meshcore_mode as _meshcore_mode
from . import resources as resources_mod
from .probes.systemd import UnitState, probe_unit
from .probes.unixsock import probe_daemon_status, probe_socket


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


# A GPS feed refreshes its marker every ~10 s; anything older is not this run.
_GPS_MARKER_MAX_AGE_S = 60.0


class StatusProber:
    """Bounded, read-only status assessment for components and snapshots."""

    def __init__(self, system: System, paths: Paths, profiles: dict | None = None,
                 binary_cover: dict | None = None,
                 meshcore_mode: str = _meshcore_mode.DEFAULT_MODE) -> None:
        self._system = system
        self._paths = paths
        self._profiles = profiles or {}
        # The MeshCore stack's effective mode (services.meshcore_mode()): decides which of the
        # node's DECLARED endpoints are expected, so ongoing status and start readiness agree.
        self._meshcore_mode = _meshcore_mode.normalize(meshcore_mode)
        # {component_id: BinaryReceipt} for components currently provided by a verified
        # artifact. Passed in by the service (which owns the receipt read) so status stays a
        # pure, bounded assessor.
        self._binary_cover = binary_cover or {}
        # ONE /proc/net/tcp read per whole-snapshot assessment (set by `assess_stacks`); None
        # outside it, so a standalone `assess_component` still reads fresh.
        self._listeners = None
        # ONE git probe per identical (checkout, pin) for the life of this prober — several
        # components share a checkout (chat and igate both build from src/LoRaHAM_Daemon), and the
        # probe's answer depends on nothing but the path and the pin. A prober lives for one
        # snapshot, so this needs no invalidation; a different pin is a different key.
        self._source_probes: dict = {}

    def _resolve_addr(self, address: str, *, lenient: bool = False) -> str:
        """A RELATIVE unix/path endpoint address is runtime-root-relative (contained by
        construction); absolute addresses (the external daemon's own /tmp sockets) pass
        through untouched — LHPC only connects to those as a client.

        `lenient=True` (for `path` endpoints) contains the leaf LEXICALLY without
        realpath-following it: a path endpoint may LEGITIMATELY be a SYMLINK to a device
        node OUTSIDE the root — e.g. a socat PTY link `state/loraham_kiss -> /dev/pts/N`.
        Strict `under()` realpath-resolves that to `/dev/pts/N`, sees it escape the root,
        and refuses — so the running PTY bridge read as ABSENT and the component was stuck
        DEGRADED. Lexical containment keeps the resolved path the in-root leaf (never
        CWD-relative), mirroring how observe-only source dirs allow a symlink leaf."""
        import os
        from pathlib import Path as _P
        if address and not _P(address).is_absolute():
            try:
                rel = os.path.join(*_P(address).parts)
                return str(self._paths._lexical_under(rel) if lenient
                           else self._paths.under(*_P(address).parts))
            except Exception:
                # AUDIT ER3: containment refusal must read as ABSENT. Returning the
                # ORIGINAL relative address let the probe resolve it against the
                # controller's CWD — a same-named file/socket there falsely reported the
                # component present/ready. Return a guaranteed-absent absolute sentinel
                # under the runtime root instead (never CWD-relative).
                return str(self._paths.runtime_root / "state" / ".unresolved-endpoint")
        return address

    # -- whole-snapshot ----------------------------------------------------

    def assess_stacks(self, stacks: tuple[Stack, ...]) -> Snapshot:
        snap = Snapshot(runtime_root_exists=self._paths.runtime_root_exists)
        index: dict[str, ComponentStatus] = {}
        all_components: list[Component] = []
        try:
            self._listeners = self._system.procfs.tcp_listeners()   # once for every TCP endpoint
        except Exception:
            self._listeners = None
        try:
            for stack in stacks:
                ss = StackStatus(stack=stack)
                for comp in stack.components:
                    status = self.assess_component(comp)
                    ss.components[comp.id] = status
                    index[comp.id] = status
                    all_components.append(comp)
                snap.stacks.append(ss)
        finally:
            self._listeners = None

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
        status.source_head = src.head
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

        endpoints, all_ready, _any_present, has_expected = self._assess_endpoints(comp)
        status.endpoints = endpoints

        # A GPS feed's health is its UPSTREAM SOURCE, not a path. Its endpoint exists from the
        # moment it is created, so without this a feed whose gpsd died would keep reading
        # RUNNING while delivering nothing. Feeding the verdict through the SAME
        # ready/not-ready channel the endpoint machinery uses gives DEGRADED on loss and a
        # clean return to RUNNING on recovery, with no restart.
        if comp.readiness == "gps-feed":
            feed_ready, feed_note = self._gps_feed_ready(comp)
            has_expected = True
            all_ready = feed_ready
            status.evidence["gps.feed"] = feed_note

        status.run_state = _run_state_for_service(
            unit_states=unit_states,
            proc_matched=proc_matched,
            has_expected_endpoints=has_expected,
            all_endpoints_ready=all_ready,
            source_state=status.source_state,
            has_source=comp.source is not None,
            runtime_root_exists=self._paths.runtime_root_exists,
        )

        # NOTE: this is the FIRST of two tx_state sites. The prober cannot resolve operator
        # identity, so it never emits ENABLED; `ControllerService._overlay_licensed_tx_enabled`
        # upgrades UNKNOWN -> ENABLED for a running LICENSED stack. DISABLED set here is
        # authoritative and the overlay never touches it. Do not "simplify" UNKNOWN away.
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
        rec = self._binary_cover.get(comp.id)
        if rec is not None:
            # BINARY channel: there is no checkout to compare against the pin (and for a
            # covered component none is expected). Report the artifact's provenance instead —
            # without this branch a binary stack reads NOT_A_REPO/MISSING and therefore
            # NOT_INSTALLED (see `_run_state_for_service`).
            commit = rec.components.get(comp.id, "")
            return SourceProbe(
                SourceState.BINARY, head=commit,
                version=f"binary@{rec.artifact_sha256[:9]}",
                evidence={"artifact": rec.filename, "sha256": rec.artifact_sha256,
                          "built_from": commit, "channel": "binary"})
        abs_path = str(self._paths.resolve_source(comp.source.path))
        key = (abs_path, comp.source.pin_commit or "")
        probe = self._source_probes.get(key)
        if probe is None:
            probe = self._source_probes[key] = probe_source(self._system, comp.source, abs_path)
        return probe

    def _profile_state(self, comp: Component, src) -> ProfileState:
        # confirmed-working only when the CLEAN source's HEAD appears in an operator-confirmed
        # known-working composition of its stack (`self._profiles` = {comp_id: {commits}},
        # built by `build_snapshot` from the known-working store). MATCH (at the pin) and
        # DIFFERS (clean at another exact commit, e.g. a confirmed stable update) both
        # qualify; a DIRTY tree is never confirmed.
        commits = self._profiles.get(comp.id) or ()
        if (src.head and src.head in commits
                and src.state in (SourceState.MATCH, SourceState.DIFFERS)):
            return ProfileState.CONFIRMED_WORKING
        return _profile_from_source(src.state)

    def _gps_feed_ready(self, comp) -> tuple[bool, str]:
        """(healthy, note) for a GPS feed, from the marker it writes about its UPSTREAM.

        `ready` (sentences flowing) and `connected` (source reachable, no fix yet — a cold
        start can take minutes) both count as healthy. Anything else means the position is not
        arriving, which must show as DEGRADED rather than a confident RUNNING.

        Read-only and failure-tolerant: this runs on every status GET.
        """
        import json
        import time
        from pathlib import Path as _Path

        from . import runtime_fs
        from .gps import bridge_state_dir, consumer_for_component
        # DERIVED from the ONE feed mapping — see gps.FEED_COMPONENTS.
        consumer = consumer_for_component(comp.id)
        if not consumer:
            return True, "not a GPS feed"
        marker = _Path(bridge_state_dir(self._paths.runtime_root, consumer)) / "readiness.json"
        try:
            got = json.loads(runtime_fs.read_text_regular(self._paths, marker, max_bytes=4096))
        except (OSError, ValueError, runtime_fs.PathContainmentError):
            return False, "no readiness marker"
        if not isinstance(got, dict):
            return False, "unreadable readiness marker"
        # Same rule as the start gate: a marker from a previous run, or one nobody has
        # refreshed, does not describe the feed that is supposed to be running now.
        try:
            updated = float(got.get("updated", 0) or 0)
        except (TypeError, ValueError):
            updated = 0.0
        if updated <= 0 or (time.time() - updated) > _GPS_MARKER_MAX_AGE_S:
            return False, "readiness marker is stale"
        # A feed killed moments after its last refresh leaves a marker that is still RECENT, so
        # the owning process is what says it is still there. Required, and validated the same way
        # as in the start gate: `bool` is an `int`, and True would read as the always-alive pid 1.
        pid = got.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False, "readiness marker has no usable owner pid"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, "readiness marker is stale (feed is gone)"
        except PermissionError:
            pass                                       # alive, owned by another user
        except OSError:
            return False, "readiness marker is stale (feed is gone)"
        state = str(got.get("state", ""))
        if state == "ready":
            return True, f"source live ({got.get('sentences', 0)} sentences)"
        if state == "connected":
            return True, "source reachable, waiting for a fix"
        return False, f"source not delivering ({got.get('detail') or state or 'unknown'})"

    def _assess_endpoints(
        self, comp: Component
    ) -> tuple[list[EndpointObservation], bool, bool, bool]:
        observations: list[EndpointObservation] = []
        expected_present: list[bool] = []
        for spec in _meshcore_mode.expected_endpoints(comp, self._meshcore_mode):
            obs = EndpointObservation(spec=spec)
            if spec.kind == "tcp":
                # Use the ONE host/family-aware matcher (not a port-only check): a listener
                # on the wrong address family/host must NOT satisfy this endpoint, and the
                # retained owner PID is that of the MATCHED listener. Keeps status in exact
                # agreement with start readiness and stop cessation.
                present, detail, owner_pid, owner_incomplete = tcp_endpoint_match(
                    self._system, spec.address, listeners=self._listeners)
                obs.present = present
                obs.owner_pid = owner_pid
                obs.owner_incomplete = owner_incomplete
                obs.detail = detail
            elif spec.kind == "unix":
                sock = probe_socket(self._system, self._resolve_addr(spec.address))
                obs.present = sock.is_socket
                obs.detail = "socket present" if sock.is_socket else "absent"
                if sock.is_socket and spec.readiness == "daemon-status":
                    ds = probe_daemon_status(self._system,
                                             self._resolve_addr(spec.address))
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
                # A path endpoint (e.g. a socat PTY link) may be a symlink to a device
                # node outside the root — contain it lexically so the legitimate link is
                # not mistaken for a containment escape and reported absent.
                present = self._system.fs.exists(
                    self._resolve_addr(spec.address, lenient=True))
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
    """Worst (highest-severity) run state per stack, as a value string.

    An OPTIONAL component that was never installed does not count. `NOT_INSTALLED` outranks
    `STOPPED`, so one never-cloned optional helper (MeshCore's Tk Node Manager) rolled an
    otherwise fine stack's badge to "not-installed" — telling the operator their installed,
    merely stopped stack was missing. The COMPONENT still reports `not-installed`
    truthfully; only this summary looks past it.

    Deliberately narrow: an optional component that IS installed keeps its full weight, so a
    `FAILED` or `DEGRADED` optional is never hidden, and a missing MANDATORY component still
    rolls the stack up to not-installed.
    """
    out: dict[str, str] = {}
    for ss in snapshot.stacks:
        optional_ids = {c.id for c in ss.stack.components if c.optional}
        worst = RunState.NOT_APPLICABLE
        for cid, st in ss.components.items():
            if st.run_state is RunState.NOT_INSTALLED and cid in optional_ids:
                continue
            if _SEVERITY[st.run_state] > _SEVERITY[worst]:
                worst = st.run_state
        out[ss.stack.id] = worst.value
    return out


def _profile_from_source(source_state: SourceState) -> ProfileState:
    return {
        SourceState.MATCH: ProfileState.INSTALLED_UNVALIDATED,
        SourceState.DIFFERS: ProfileState.CANDIDATE_AVAILABLE,
        SourceState.DIRTY: ProfileState.LOCALLY_MODIFIED,
        # A verified artifact is installed material, not a validated known-working profile.
        SourceState.BINARY: ProfileState.INSTALLED_UNVALIDATED,
    }.get(source_state, ProfileState.UNKNOWN)


def _find(stacks: tuple[Stack, ...], component_id: str) -> Component | None:
    for stack in stacks:
        c = stack.component(component_id)
        if c:
            return c
    return None

"""Shared application/service layer — the single entry point for all behaviour.

The CLI adapter and the web adapter both call ONLY this module, guaranteeing
identical validation, status interpretation and results. Read methods are bounded
and read-only; mutating methods print a plan and apply only when confirmed.

`build_snapshot()` is the single probing path; both `status()` (CLI text) and the
web adapter call it, so a page load and a CLI run see the same fresh evidence.
"""

from __future__ import annotations

import os
import threading
import time
import contextlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import daemon_control
from . import manifest as manifest_mod
from . import resources as resources_mod
from . import validators
from .config import (
    Config,
    ConfigError,
    apply_config_transaction,
    merge_stack_values,
    _patch_local_table,
    render_local_tables,
    render_stack_config,
    update_stack_config,
    _load_runtime_toml,
    _stack_config_path,
    load_config,
    load_stack_config,
    render_keyval,
    save_component_remote,
    save_stack_config,
    update_toml,
    update_yaml,
)
from .install import Installer, Plan
from .lifecycle import Lifecycle
from . import profiles as profiles_mod
from .model import (
    ComponentKind,
    ResourceMode,
    RunState,
    SourceState,
    Stack,
    TxState,
    emit_param,
)
from . import procident
from . import runtime_fs
from .outcomes import CompResult, Outcome, applied_ok
from .paths import Paths, PathContainmentError, resolve_paths
from .probes import RealSystem, System
from .probes import hardware
from .status import Snapshot, StatusProber, rollup_states, summarize

_SPI_DEV = "/dev/spidev0.0"
_GPIO_DEV = "/dev/gpiochip0"


class SourceTxnBlocked(Exception):
    """Raised by the source-operation guard when an unresolved source-transaction journal
    is present — every source-mutating op fails closed until an operator resolves it."""


@dataclass(frozen=True)
class ConfigWrite:
    """Structured result of generating one component's config file."""

    component: str
    path: str
    status: str            # "written" | "linked-readonly" | "no-base" | "failed"
    detail: str = ""


@dataclass
class ActionResult:
    """Uniform result object rendered identically by every adapter."""

    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    # Typed per-component lifecycle results (start/stop/restart). Adapters may render
    # from these; `ok` for an applied lifecycle action is derived from them.
    results: tuple = field(default_factory=tuple)


class ControllerService:
    """Facade over the core. Construct once per process; cheap and stateless.

    `system` and `paths` are injectable so tests drive it with fakes.
    """

    def __init__(
        self,
        manifest_path: Path | None = None,
        system: System | None = None,
        paths: Paths | None = None,
    ) -> None:
        self._manifest_path = manifest_path
        self._system = system or RealSystem()
        self._paths = paths or resolve_paths()
        self._stacks: tuple[Stack, ...] | None = None
        self._config: Config | None = None
        # The config cache is shared by the (threaded) web app; guard it so a save on one
        # thread is visible to the next read on any thread (no stale callsign/remote).
        self._config_lock = threading.RLock()
        # THREAD-LOCAL re-entrancy bookkeeping: this service is shared by the (possibly
        # threaded) web app, so lock ownership is scoped to the CURRENT thread. Only
        # nested calls in the SAME thread skip re-acquisition; an independent thread
        # contends through `reslock`. Recursion COUNTS (not a flat set) so a nested
        # lifecycle call cannot prematurely release an outer guard's lock.
        self._lock_state = threading.local()
        # Per-thread re-entrancy for the SHARED configuration-stability guard held across an applied
        # start/restart (see `_config_stable`).
        self._cfg_stable_state = threading.local()

    @contextmanager
    def _config_stable(self):
        """Hold saved configuration STABLE for the duration of an applied lifecycle transition — a
        SHARED read lock on the runtime config lock file. Config MUTATIONS take the EXCLUSIVE
        `config_lock` (LOCK_EX), so a concurrent save (this process or another) WAITS until the
        protected transition completes, and a start WAITS for an in-progress save. Independent starts
        share the lock and never serialise. RE-ENTRANT per thread, so a public start/restart nests
        with the internal `_start_impl`/`_restart_impl` (and stop/start within a restart) without
        self-deadlock. LOCK ORDER: this guard is acquired BEFORE any lifecycle/resource lock; a
        lock/read failure raises here so the caller fails typed BEFORE any lifecycle side effect."""
        import fcntl
        from . import runtime_fs
        st = self._cfg_stable_state
        depth = getattr(st, "depth", 0)
        if depth == 0:
            fh = runtime_fs.open_lock(self._paths, self._paths.under("config", ".lock"))
            try:
                fcntl.flock(fh, fcntl.LOCK_SH)
            except OSError:
                fh.close()
                raise
            st.fh = fh
        st.depth = depth + 1
        try:
            yield
        finally:
            st.depth -= 1
            if st.depth == 0:
                try:
                    fcntl.flock(st.fh, fcntl.LOCK_UN)
                finally:
                    st.fh.close()
                    st.fh = None

    # ---- config / installer ---------------------------------------------

    def config(self) -> Config:
        with self._config_lock:
            if self._config is None:
                self._config = load_config(self._paths)
            return self._config

    def _invalidate_config(self) -> None:
        """Drop the cached Config so the NEXT read (any thread) reloads from disk. Called
        after every successful config mutation so a saved callsign/locator/remote/param is
        immediately visible to subsequent web AND CLI service actions (no stale cache)."""
        with self._config_lock:
            self._config = None

    def _installer(self) -> Installer:
        return Installer(self._paths, self.stacks(), self.config(), self._system)

    @contextmanager
    def _source_operation_guard(self, source_paths, op: str = "source-op"):
        """ONE atomic source-operation boundary (P0.1) — no preflight/acquire gap:
          1. acquire the source-transaction INDEX lock;
          2. recover + validate journals;
          3. block (raise `SourceTxnBlocked`) if ANY unresolved journal remains;
          4. acquire ALL affected source-path locks (stable sorted) WHILE STILL HOLDING
             the index lock — a handoff, so no journal can appear between the check and
             the lock and the source is already locked before the index is released;
          5. release the index lock and yield with the source locks held for the op.
        Raises `reslock.ResourceBusy` if the index or a source lock is contended."""
        from . import reslock
        inst = self._installer()
        keys = sorted({reslock.source_lock_key(sp) for sp in source_paths})
        with contextlib.ExitStack() as src_stack:
            # Index held across recovery + the source-lock handoff, then released.
            with reslock.operation_lock(self._paths, inst._index_key(), op, ""):
                inst._recover_scan()
                if inst._pending_journals():
                    raise SourceTxnBlocked(
                        "an unresolved source-transaction journal is present — "
                        "resolve it before any source operation")
                for k in keys:
                    src_stack.enter_context(reslock.operation_lock(self._paths, k, op, ""))
            # Index released; source lock(s) remain held by src_stack for the operation.
            yield

    # ---- manifest --------------------------------------------------------

    def stacks(self) -> tuple[Stack, ...]:
        if self._stacks is None:
            self._stacks = manifest_mod.load_manifest(self._manifest_path)
        return self._stacks

    def stack(self, stack_id: str) -> Stack | None:
        for s in self.stacks():
            if s.id == stack_id:
                return s
        return None

    @property
    def runtime_root(self):
        """Absolute runtime installation root (display/resolution use)."""
        return self._paths.runtime_root

    # ---- the single probing path (used by CLI and web) -------------------

    def build_snapshot(self) -> Snapshot:
        """Fresh, bounded, read-only assessment of every stack. No caching."""
        profiles = profiles_mod.load_profiles(self._paths)
        return StatusProber(self._system, self._paths, profiles).assess_stacks(self.stacks())

    # ---- read-only operations --------------------------------------------

    def list_stacks(self) -> ActionResult:
        stacks = self.stacks()
        details = [
            f"{s.id:10s} {len(s.components):2d} components  {s.summary}" for s in stacks
        ]
        return ActionResult(
            ok=True,
            summary=f"{len(stacks)} stacks defined in the manifest.",
            details=details,
            next_commands=["lhpc status", "lhpc explain <stack>"],
        )

    def status(self, stack_id: str | None = None) -> ActionResult:
        if stack_id and self.stack(stack_id) is None:
            return self._unknown_stack(stack_id)
        snap = self.build_snapshot()
        rollup = rollup_states(snap)
        details: list[str] = []
        if not stack_id:
            counts = summarize(snap)["states"]
            tally = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            details.append(f"{len(snap.stacks)} stacks, "
                           f"{summarize(snap)['components']} components: {tally}")
            details.append("")
        for ss in snap.stacks:
            if stack_id and ss.stack.id != stack_id:
                continue
            details.append(f"[{ss.stack.id}] {ss.stack.name}  ({rollup[ss.stack.id]})")
            for comp in ss.stack.components:
                st = ss.components[comp.id]
                details.extend(_render_component(comp, st))
        observed = self._observed_conflicts(snap)
        if observed:
            details.append("")
            details.append("Observed resource conflicts:")
            for c in observed:
                details.append(f"  ! {c.message}")
        if not snap.runtime_root_exists:
            details.append("")
            details.append(
                "Note: runtime root not installed; managed sources report "
                "'not-installed' (expected before install)."
            )
        # Probing succeeded; status is informational — exit success even when stopped.
        return ActionResult(
            ok=True,
            summary="Status collected (read-only; no network, no changes).",
            details=details,
            next_commands=["lhpc explain <stack>", "lhpc doctor", "lhpc status --versions"],
        )

    def status_versions(self) -> ActionResult:
        snap = self.build_snapshot()
        details: list[str] = []
        for ss in snap.stacks:
            for comp in ss.stack.components:
                if comp.source is None:
                    continue
                st = ss.components[comp.id]
                pin = (comp.source.pin_commit[:12] or "-") if comp.source else "-"
                tag = comp.source.pin_tag or "-"
                details.append(
                    f"  {comp.id:24s} {st.source_state.value:12s} "
                    f"pin={pin} tag={tag}"
                )
        return ActionResult(
            ok=True,
            summary="Source/pin status (local git only; no fetch). "
            "A pin match is NOT a confirmed-working judgement.",
            details=details,
            next_commands=["lhpc status", "lhpc doctor"],
        )

    def explain(self, stack_id: str) -> ActionResult:
        s = self.stack(stack_id)
        if s is None:
            return self._unknown_stack(stack_id)
        details = [s.summary, "", "Components (manual start order):"]
        ordered = sorted(
            s.components, key=lambda c: (c.start_order is None, c.start_order or 0)
        )
        for c in ordered:
            order = "-" if c.start_order is None else str(c.start_order)
            tx = "TX-capable" if c.tx_capable else "RX-only"
            band = f" {c.band}MHz" if c.band else ""
            details.append(f"  {order}. {c.id}{band} — {c.purpose} [{c.kind.value}, {tx}]")
            if c.depends_on:
                details.append(f"        depends on: {', '.join(c.depends_on)}")
            for r in c.resources:
                extra = f" = {r.requirement}" if r.requirement else ""
                details.append(f"        claims {r.key} ({r.mode.value}{extra})")
            if c.note:
                details.append(f"        note: {c.note}")
        return ActionResult(
            ok=True,
            summary=f"Stack '{s.id}': {s.name}",
            details=details,
            next_commands=[f"lhpc status {s.id}"],
        )

    def doctor(self) -> ActionResult:
        sys = self._system
        details: list[str] = []

        root = self._paths.runtime_root
        details.append(
            f"runtime root: {'present' if self._paths.runtime_root_exists else 'absent (run lhpc bootstrap)'} ({root})"
        )
        op = self.config().operator
        details.append(
            f"  operator: {op.callsign + ' (' + op.locator + ')' if op.configured else 'not configured (set in runtime config/local.toml)'}"
        )
        details.append(f"  systemctl: {hardware.check_systemctl(sys, user=False).detail}")
        details.append(f"  systemctl --user: {hardware.check_systemctl(sys, user=True).detail}")
        for dev in (_SPI_DEV, _GPIO_DEV):
            chk = hardware.check_char_device(sys, dev)
            details.append(f"  {dev}: {chk.detail}")

        # Configured source paths present?
        present = missing = 0
        for s in self.stacks():
            for c in s.components:
                if c.source is None:
                    continue
                p = str(self._paths.resolve_source(c.source.path))
                if sys.fs.exists(p):
                    present += 1
                else:
                    missing += 1
        details.append(f"  configured sources: {present} present, {missing} missing")

        # Run-state tally from a fresh snapshot.
        snap = self.build_snapshot()
        tally: dict[str, int] = {}
        for ss in snap.stacks:
            for st in ss.components.values():
                tally[st.run_state.value] = tally.get(st.run_state.value, 0) + 1
        details.append("  components: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        observed = self._observed_conflicts(snap)
        details.append(f"  observed resource conflicts: {len(observed)}")

        return ActionResult(
            ok=True,
            summary="doctor: bounded local checks only (no init, no network, no RF).",
            details=details,
            next_commands=["lhpc status", "lhpc status --versions"],
        )

    # ---- install / bootstrap ---------------------------------------------

    def bootstrap(self, apply: bool = False) -> ActionResult:
        inst = self._installer()
        plan = inst.plan_bootstrap()
        if not apply:
            return self._plan_result(plan, applied=False, next_apply="lhpc bootstrap --yes")
        plan = inst.apply_bootstrap(plan)
        return self._plan_result(plan, applied=True, next_apply=None)

    def install(self, stack_id: str | None = None, apply: bool = False,
                source: str = "pinned") -> ActionResult:
        if stack_id and self.stack(stack_id) is None:
            return self._unknown_stack(stack_id)
        if not self._paths.runtime_root_exists:
            return ActionResult(
                ok=False,
                summary="Runtime root is not bootstrapped yet.",
                details=[f"Run 'lhpc bootstrap' to create {self._paths.runtime_root}."],
                next_commands=["lhpc bootstrap"],
            )
        inst = self._installer()
        plan = inst.plan_install(stack_id)
        if not apply:
            cmd = f"lhpc install {stack_id} --yes" if stack_id else "lhpc install --yes"
            return self._plan_result(plan, applied=False, next_apply=cmd)
        for stack in self.stacks():
            if stack_id and stack.id != stack_id:
                continue
            for comp in stack.components:
                if comp.source is None:
                    continue
                dest = self._paths.resolve_source(comp.source.path)
                if dest.exists():
                    continue
                result = inst.adopt_source(comp, source=source)
                for a in plan.actions:
                    if a.target == str(dest):
                        a.status, a.detail = result.status, result.detail
                        a.provenance = result.provenance
        return self._plan_result(plan, applied=True, next_apply=None)

    def _plan_result(self, plan: Plan, *, applied: bool, next_apply: str | None) -> ActionResult:
        details = [
            f"  [{a.status}] {a.description}" + (f" — {a.detail}" if a.detail else "")
            for a in plan.actions
        ]
        failed = [a for a in plan.actions if a.status == "failed"]
        # Expose the per-source provenance state in the result data (activated sources only).
        provenance = {a.target: a.provenance for a in plan.actions if a.provenance}
        if applied:
            done = sum(1 for a in plan.actions if a.status == "done")
            summary = (f"{plan.title}: applied {done} action(s)."
                       if not failed else
                       f"{plan.title}: completed with {len(failed)} failure(s).")
            return ActionResult(ok=not failed, summary=summary, details=details,
                                next_commands=["lhpc status", "lhpc doctor"],
                                data={"provenance": provenance} if provenance else {})
        n = len(plan.changes)
        summary = f"{plan.title}: {n} change(s) planned (dry run)."
        return ActionResult(ok=True, summary=summary, details=details,
                            next_commands=[next_apply] if next_apply and n else [],
                            data={"changes": n})

    # ---- lifecycle operations: build/start/stop/logs/test ----------------

    def _lifecycle(self) -> Lifecycle:
        return Lifecycle(self._paths, self.stacks(), self.config(), self._system)

    def _resolve(self, target: str):
        """Resolve a target to an ordered list of (stack, component). A stack id
        expands to its runnable components in start order; a component id is one."""
        s = self.stack(target)
        if s is not None:
            runnable = [c for c in s.components if c.run_argv]
            runnable.sort(key=lambda c: (c.start_order is None, c.start_order or 0))
            return [(s, c) for c in runnable], None
        for st in self.stacks():
            c = st.component(target)
            if c is not None:
                return [(st, c)], None
        return [], f"Unknown stack or component '{target}'."

    def _band_limited_running(self, snap):
        """(limited_fn, running_components, running_ids) with each running component's
        `loraham.radio.<band>` claims restricted to the band(s) it ACTUALLY uses — the daemon to
        the bands it currently serves, a band-switchable app to its effective band, a fixed-band
        app to its band. So a daemon on 433 and meshtastic on 868 are NOT seen as one radio."""
        import dataclasses
        # Daemon radio ownership is PROCESS topology (a dead CONF socket does not free the radio),
        # not socket reachability.
        served = self._daemon_claimed_bands()

        def limited(c, eff_bands):
            if c.id != self.DAEMON_ID and not c.bands:
                return c                                  # single-fixed-band: unchanged
            # Keep only radio claims for the band(s) this component actually uses. When the band is
            # unknown (empty eff_bands), STRIP every radio claim — a band-switchable app must never
            # claim BOTH radios just because its running-band marker is missing.
            keep = [r for r in c.resources
                    if not (r.key.startswith("loraham.radio.")
                            and r.key.rsplit(".", 1)[-1] not in eff_bands)]
            return dataclasses.replace(c, resources=tuple(keep))

        running, running_ids = [], set()
        for ss in snap.stacks:
            for c in ss.stack.components:
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                if c.id == self.DAEMON_ID:
                    eff = served
                elif c.bands:
                    # Actual running band, else the component's DECLARED band — never "both".
                    eb = self._effective_band(ss.stack.id, c.band)
                    eff = {eb} if eb in ("433", "868") else set()
                else:
                    eff = {c.band} if c.band else set()
                running.append(limited(c, eff))
                running_ids.add(c.id)
        return limited, running, running_ids

    def _observed_conflicts(self, snap=None):
        """Band-aware observed resource conflicts (both claimants running, radio claims limited to
        the band each actually uses). Replaces the raw, band-blind `snap.conflicts` for display."""
        snap = snap if snap is not None else self.build_snapshot()
        _, running, running_ids = self._band_limited_running(snap)
        return [c for c in resources_mod.interpret_conflicts(running, running_ids) if c.observed]

    def observed_conflicts(self, snap=None):
        """PUBLIC read-only band-aware observed resource conflicts — the single source of truth for
        every UI (CLI status, doctor, /stacks, /stacks/<id>). Never renders a false 433/868 conflict
        (a daemon serving only 433 does not conflict with a direct-radio owner on 868)."""
        return self._observed_conflicts(snap)

    def _running_conflicts(self, comp, band: str = "") -> list[str]:
        """Observed conflicts that would block starting `comp` right now. Radio
        claims are matched by the band each side actually uses, so a multi-band app
        on 868 does not conflict with a daemon serving only 433 (and vice-versa)."""
        snap = self.build_snapshot()
        limited, running, running_ids = self._band_limited_running(snap)
        target = limited(comp, {band} if band else set())
        conflicts = resources_mod.interpret_conflicts(running + [target], running_ids | {comp.id})
        return [c.message for c in conflicts if comp.id in c.holders and c.observed]

    DAEMON_ID = "loraham-daemon"
    # After auto-starting the daemon, wait up to this long for its CONF socket to
    # answer before reporting success (the daemon inits the radio asynchronously).
    DAEMON_VERIFY_TIMEOUT_S = 4.0
    DAEMON_VERIFY_POLL_S = 0.5
    # For readiness="endpoint": wait up to this long for every ready=true endpoint.
    ENDPOINT_VERIFY_TIMEOUT_S = 6.0
    ENDPOINT_VERIFY_POLL_S = 0.3

    def _ready_endpoints_present(self, comp) -> tuple[bool, list[str]]:
        """Probe a component's `ready = true` endpoints (bounded). Returns
        (all_present, evidence-lines). Only endpoints explicitly marked ready
        participate — reference/client/data endpoints never gate."""
        from .probes.endpoints import tcp_endpoint_present
        from .probes.unixsock import probe_socket
        ready = [e for e in comp.endpoints if e.ready]
        if not ready:
            return True, []
        def snapshot() -> tuple[bool, list[str]]:
            ev, ok_all = [], True
            for e in ready:
                if e.kind == "tcp":
                    # Host/family-aware: a wrong-family/host listener on the same port
                    # does NOT satisfy readiness.
                    present, line = tcp_endpoint_present(self._system, e.address)
                    ev.append(line)
                elif getattr(e, "external", False):
                    # External endpoints are observe-only and NEVER gate readiness.
                    continue
                elif not self._paths.contains(Path(e.address)):
                    # A ready Unix/path endpoint must be runtime-contained unless
                    # explicitly external — an outside-root endpoint can never gate.
                    present = False
                    ev.append(f"{e.address}: rejected (ready endpoint not runtime-contained)")
                else:
                    if e.kind == "unix":
                        present = probe_socket(self._system, e.address).is_socket
                    else:
                        present = self._system.fs.exists(e.address)
                    ev.append(f"{e.address}: {'present' if present else 'absent'}")
                ok_all = ok_all and present
            return ok_all, ev
        waited = 0.0
        while True:
            ok_all, ev = snapshot()
            if ok_all or self.ENDPOINT_VERIFY_TIMEOUT_S <= 0 or waited >= self.ENDPOINT_VERIFY_TIMEOUT_S:
                return ok_all, ev
            time.sleep(self.ENDPOINT_VERIFY_POLL_S)
            waited += self.ENDPOINT_VERIFY_POLL_S

    def _component_index(self):
        return {c.id: (s, c) for s in self.stacks() for c in s.components}

    # -- target resolution: a target is either a STACK id or a direct COMPONENT id --------------
    # For a direct component target the OWNER STACK provides persisted config / per-band selection /
    # config-file storage, while only the TARGETED component contributes editable fields + identity.

    def _owner_stack(self, target: str):
        """The stack that owns `target` for config/per-band/config-file storage — the stack itself
        for a stack target, or the owning stack for a direct component target; None if unknown."""
        s = self.stack(target)
        if s is not None:
            return s
        hit = self._component_index().get(target)
        return hit[0] if hit else None

    def _owner_stack_id(self, target: str) -> str:
        s = self._owner_stack(target)
        return s.id if s is not None else target

    def _target_components(self, target: str) -> list:
        """The components whose run/file params + identity a target exposes: ALL of a stack's
        components, or JUST the one component for a direct component target."""
        s = self.stack(target)
        if s is not None:
            return list(s.components)
        hit = self._component_index().get(target)
        return [hit[1]] if hit else []

    def _is_daemon_target(self, target: str) -> bool:
        """A target is daemon-scoped (identity/param-panel exempt) when its owner stack's main IS
        the daemon (a daemon stack target, or a direct daemon-component target)."""
        owner = self._owner_stack(target)
        return owner is not None and owner.main == self.DAEMON_ID

    def _run_order(self, target: str):
        """Ordered (stack, component) list to bring `target` up: the target's
        non-optional components plus their transitive dependencies, deps first."""
        idx = self._component_index()
        s = self.stack(target)
        if s is not None:
            # Optional components are soft: included only when the operator has
            # opted into auto-starting them (even via another component's depends_on).
            cfg = load_stack_config(self._paths, target)
            allowed_optional = {c.id for c in s.components
                                if c.optional and cfg.get(f"autostart_{c.id}") == "on"}
            seeds = [c.id for c in s.components if not c.optional]
            if s.main and s.main not in seeds:
                seeds.append(s.main)
            seeds += list(allowed_optional)
        elif target in idx:
            seeds = [target]
            allowed_optional = {target}   # an explicit component run is always allowed
        else:
            return None
        order, seen = [], set()

        def visit(cid: str):
            if cid in seen or cid not in idx:
                return
            comp = idx[cid][1]
            if comp.optional and cid not in allowed_optional:
                return                    # soft dependency the operator hasn't opted into
            seen.add(cid)
            for dep in comp.depends_on:
                visit(dep)
            order.append(cid)

        for sid in seeds:
            visit(sid)
        return [idx[cid] for cid in order]

    def _all_components_healthy(self, order, st_index, radio: str) -> bool:
        """True when EVERY requested component is already healthy — the daemon serving every needed
        band (READY), and each non-library service component RUNNING. Basis for a no-side-effect
        Start (no launch, no daemon CONF SET, no param apply). A missing band or a stopped client
        makes it False so the normal apply-once-then-start path runs."""
        need = ["433", "868"] if radio == "both" else ([radio] if radio in ("433", "868") else [])
        for _stack, comp in order:
            if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE):
                continue
            if comp.id == self.DAEMON_ID:
                if not need or not all(self.daemon_view(b).ready for b in need):
                    return False
                continue
            if st_index[comp.id].run_state != RunState.RUNNING:
                return False
        return True

    def _daemon_needs(self, order, params, band: str = ""):
        """The daemon's required radio band + TX mode for this run order. `band`
        overrides the band for a band-switchable app stack. Returns (radio, tx); tx is
        None when no single value applies."""
        if not any(c.id == self.DAEMON_ID for _, c in order):
            return None, None
        if params and params.get("radio"):
            return params["radio"], None          # explicit (daemon stack) override
        if band in ("433", "868"):
            bands = {band}
        else:
            bands = {c.band for _, c in order if self.DAEMON_ID in c.depends_on and c.band}
        txs = {c.requires_daemon_tx for _, c in order
               if self.DAEMON_ID in c.depends_on and c.requires_daemon_tx}
        radio = "both" if len(bands) != 1 else next(iter(bands))
        tx = next(iter(txs)) if len(txs) == 1 else None
        return radio, tx

    def _effective_band(self, stack_id: str, fallback: str = "") -> str:
        """The band a stack is actually running on (start marker, or for an
        interactive app the band it was launched on)."""
        return (self.running_band(stack_id, "") or self.interactive_band(stack_id)
                or fallback)

    def _operation_bands(self, target: str, band: str = "", radio: str = "",
                         op: str = "") -> set:
        """THE authoritative radio band(s) a lifecycle op on `target` touches — one source of truth
        for radio locking + conflict detection.
          * START  — the REQUESTED bands (client: its chosen/declared band; daemon: `radio`, else
                     the saved daemon `radio`). Never inferred from the daemon's empty Component.band.
          * STOP   — the ACTUAL running bands: a client uses its running/interactive MARKER (falling
                     back to the declared band only when there is NO runtime evidence); the daemon
                     uses PROCESS TOPOLOGY — a per-band stop also locks the other band when the SAME
                     process serves it (a manual `--radio both`), and a whole-daemon stop locks
                     every band an owned/observed daemon PROCESS serves, even if that band's CONF
                     socket is unreachable / UNINITIALIZED / FAILED.
          * RESTART— the UNION of the actual STOP bands and the requested START bands."""
        order = self._run_order(target)
        if not order:
            return set()
        sid = self.stack_of(target) or target
        stk = self.stack(sid)
        is_daemon = stk is not None and stk.main == self.DAEMON_ID
        if op == "restart":
            return (self._operation_bands(target, band, "", "stop")
                    | self._operation_bands(target, band, radio, "start"))
        if is_daemon:
            if op == "stop":
                if band in ("433", "868"):
                    bands = {band}
                    other = "868" if band == "433" else "433"
                    # --radio both collateral: the SAME process also serves the other band -> lock
                    # it too, regardless of that band's CONF socket state (topology, not reachability).
                    if set(self._daemon_pids_for_band(band)) & set(self._daemon_pids_for_band(other)):
                        bands.add(other)
                    return bands
                # Whole-daemon stop: every band an owned/observed daemon PROCESS claims.
                return self._daemon_claimed_bands()
            r = radio or str(self.stack_config(sid).get("radio") or "both")
            return {"433", "868"} if r == "both" else ({r} if r in ("433", "868") else set())
        # Client.
        if op == "stop":
            eb = self._effective_band(sid, "")        # ACTUAL running band (marker/interactive)
            if eb in ("433", "868"):
                return {eb}
        cfg_band = self._config_band(target, band)    # declared/default (start, or stop w/o evidence)
        return {cfg_band} if cfg_band else {c.band for _, c in order if c.band}

    def _operation_resource_keys(self, target: str, band: str = "", radio: str = "",
                                 op: str = "") -> list[str]:
        """Canonical resource keys an EXCLUSIVE/PROVIDER operation on `target` touches —
        the basis for cross-stack operation locks so a start/stop/restart of one stack
        serializes against another stack claiming the SAME radio/port/socket. Radio claims
        are scoped by `_operation_bands` (band-aware, daemon-radio-aware). Mirrors `run_blockers`
        so the lock set equals the conflict set. CONSUMER/COOPERATIVE claims take no lock."""
        order = self._run_order(target)
        if not order:
            return []
        keys = set()
        for _, c in order:
            for r in c.resources:
                if (r.mode in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER)
                        and not r.key.startswith("loraham.radio.")):
                    keys.add(r.key)
        for b in self._operation_bands(target, band, radio, op):
            keys.add(f"loraham.radio.{b}")
        return sorted(keys)

    def _operation_source_paths(self, target: str) -> list[str]:
        """Distinct managed source paths a start touches (generated config, command
        expansion, launch, post-start prep all read from them) — locked for the start so
        a concurrent update/uninstall cannot swap the tree mid-start. Sorted for a stable
        acquisition order; shared checkouts collapse to one key."""
        order = self._run_order(target)
        return sorted({c.source.path for _, c in order if c.source})

    def _lifecycle_lock_keys(self, op: str, target: str, band: str = "",
                             stop_owners: bool = False, cascade: bool = False,
                             radio: str = "") -> list[str]:
        """The COMPLETE lock bundle a lifecycle op must hold: the target's
        `lifecycle.<stack>` + `claim.<resource>` keys (+ source-path keys for start/
        restart), AND — for `stop_owners`/`cascade` — the owners'/dependents' keys too, so
        a cross-target mutation never bypasses another target's coordination. Radio claims are
        band-aware (`radio` carries the daemon's requested mode for a daemon start/restart).
        Returned de-duplicated; the caller acquires them in ONE stable sorted order."""
        from . import reslock
        keys: set[str] = set()

        def add(t: str, with_source: bool, scoped_band: str, scoped_radio: str, scoped_op: str) -> None:
            sid = self.stack_of(t) or t
            keys.add(f"lifecycle.{sid}")
            for rk in self._operation_resource_keys(t, scoped_band, scoped_radio, scoped_op):
                keys.add(f"claim.{rk}")
            if with_source:
                for sp in self._operation_source_paths(t):
                    keys.add(reslock.source_lock_key(sp))

        add(target, op in ("start", "restart"), band, radio, op)
        if stop_owners and op in ("start", "restart"):
            for b in self.run_blockers(target, band, radio):
                holder = b.get("holder_stack") or b.get("holder")
                if holder:
                    add(holder, False, "", "", "")   # holder is a running peer; its own bands apply
        if cascade and op == "stop":
            for dep in self._dependents_of(target):
                add(dep, False, "", "", "stop")
        return sorted(keys, key=reslock.canonical_key)

    def _dependents_of(self, target: str) -> list[str]:
        """Stack ids of RUNNING stacks that depend on `target` (for cascade stop)."""
        order_ids = {c.id for _, c in (self._run_order(target) or [])}
        out = set()
        for s in self.stacks():
            for c in s.components:
                if any(d in order_ids for d in (c.depends_on or ())):
                    out.add(s.id)
        return sorted(out)

    def _held_counts(self) -> dict:
        """Per-THREAD map of lock key -> recursion depth currently held by THIS thread."""
        st = self._lock_state
        counts = getattr(st, "counts", None)
        if counts is None:
            counts = st.counts = {}
        return counts

    # How long to WAIT for a resource claim held by our OWN controller process before failing.
    _SELF_LOCK_WAIT_S = 5.0

    def _acquire_key(self, stack, k: str, op: str, target: str) -> None:
        """Enter one reslock key into `stack`. A claim held by ANOTHER process is a real external
        conflict → fail fast (`ResourceBusy`). A claim held by our OWN controller process is a
        concurrent/overlapping controller op (this service is shared across waitress threads, and
        two lifecycle ops can touch a shared claim like `loraham.daemon-socket.433`) that releases
        shortly → wait BOUNDED, so the operator is never told their own stack is 'busy' on itself,
        while a genuinely hung holder still can't wedge us forever."""
        from . import reslock
        deadline = time.monotonic() + self._SELF_LOCK_WAIT_S
        while True:
            try:
                stack.enter_context(reslock.operation_lock(self._paths, k, op, target))
                return
            except reslock.ResourceBusy as busy:
                same_process = str(busy.holder.get("pid")) == str(os.getpid())
                if not same_process or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    @contextmanager
    def _lifecycle_guard(self, op: str, target: str, band: str = "",
                         stop_owners: bool = False, cascade: bool = False, radio: str = ""):
        """Acquire the lifecycle lock bundle. RE-ENTRANT per THREAD: a key already held by
        an outer guard in THIS thread is not re-flocked (so restart→stop+start and
        stop_owners→stop nest without self-contending), but an INDEPENDENT thread sharing
        this service contends through `reslock` and gets `ResourceBusy`. Recursion counts
        ensure a nested guard never releases an outer guard's flock."""
        from . import reslock
        keys = self._lifecycle_lock_keys(op, target, band, stop_owners, cascade, radio)
        counts = self._held_counts()
        bumped: list[str] = []
        # For a start/restart that acquires source locks FRESH (not nested inside an outer
        # guard that already holds them), do the index→recover→block→source handoff: hold
        # the INDEX lock across the journal check AND the source-lock acquisition, then
        # release it — so a start cannot pass a journal check then race a retained journal.
        fresh_source = any(k.startswith("source.") and counts.get(k, 0) == 0 for k in keys)
        do_handoff = op in ("start", "restart") and fresh_source
        try:
            with contextlib.ExitStack() as stack:
                idx_stack = contextlib.ExitStack()
                try:
                    if do_handoff:
                        inst = self._installer()
                        idx_stack.enter_context(
                            reslock.operation_lock(self._paths, inst._index_key(), op, target))
                        inst._recover_scan()
                        if inst._pending_journals():
                            raise SourceTxnBlocked(
                                "an unresolved source-transaction journal is present — "
                                "resolve it before starting")
                    for k in keys:
                        if counts.get(k, 0) == 0:   # not held by an outer guard in THIS thread
                            self._acquire_key(stack, k, op, target)
                        counts[k] = counts.get(k, 0) + 1
                        bumped.append(k)
                finally:
                    idx_stack.close()               # release index AFTER source held (or on error)
                yield
        finally:
            for k in bumped:
                counts[k] -= 1
                if counts[k] <= 0:
                    counts.pop(k, None)

    @contextmanager
    def _keys_guard(self, op: str, target: str, keys: list):
        """Acquire an explicit set of reslock keys, RE-ENTRANT per thread (sharing the same
        `_held_counts` as `_lifecycle_guard`, so a key already held by an enclosing start is not
        re-flocked). Raises `reslock.ResourceBusy` if an independent operation holds one."""
        from . import reslock
        counts = self._held_counts()
        bumped: list = []
        try:
            with contextlib.ExitStack() as stack:
                for k in sorted(set(keys), key=reslock.canonical_key):
                    if counts.get(k, 0) == 0:
                        self._acquire_key(stack, k, op, target)
                    counts[k] = counts.get(k, 0) + 1
                    bumped.append(k)
                yield
        finally:
            for k in bumped:
                counts[k] -= 1
                if counts[k] <= 0:
                    counts.pop(k, None)

    def run_blockers(self, target: str, band: str = "", radio: str = "") -> list[dict]:
        """REAL resource conflicts only: exclusive/provider resources this run would
        use that a RUNNING component of another stack is *actually* using. Radio
        bands are matched by the band each side really uses (a multi-band stack on
        868 does not block another stack on 433; the daemon only conflicts on the
        bands it currently serves). A daemon start's bands come from its REQUESTED radio
        mode (`radio`), not its empty `Component.band`."""
        order = self._run_order(target)
        if not order:
            return []
        cfg_band = self._config_band(target, band)
        order_ids = {c.id for _, c in order}
        target_stack = self.stack_of(target)
        target_is_daemon = bool(target_stack and self.stack(target_stack)
                                and self.stack(target_stack).main == self.DAEMON_ID)
        # Bands this run actually uses (band-aware + daemon-radio-aware).
        needed_bands = self._operation_bands(target, band, radio, "start")
        # Non-radio exclusive/provider claims (ports, sockets, …) + only the radio
        # band(s) the run really needs.
        claims: dict[str, str] = {}
        for _, c in order:
            for r in c.resources:
                if (r.mode in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER)
                        and not r.key.startswith("loraham.radio.")):
                    claims[r.key] = c.id
        for b in needed_bands:
            claims.setdefault(f"loraham.radio.{b}", target)

        snap = self.build_snapshot()
        # Daemon radio ownership is PROCESS topology (a dead CONF socket does not free the radio),
        # not socket reachability.
        served = self._daemon_claimed_bands()
        blockers, seen = [], set()

        def add(stack_id, holder, resource):
            key = (stack_id, resource)
            if key not in seen:
                seen.add(key)
                blockers.append({"resource": resource, "holder": holder,
                                 "holder_stack": stack_id})

        for ss in snap.stacks:
            sid = ss.stack.id
            multi = bool(self.stack_bands(sid))
            for c in ss.stack.components:
                if c.id in order_ids:
                    continue
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                # Which radio band(s) is THIS running component actually using?
                if c.id == self.DAEMON_ID:
                    active = served
                elif multi:
                    eb = self._effective_band(sid, c.band)   # actual band, else declared (never both)
                    active = {eb} if eb in ("433", "868") else set()
                else:
                    active = {c.band} if c.band else set()
                for r in c.resources:
                    if r.mode not in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER):
                        continue
                    if r.key.startswith("loraham.radio."):
                        rb = r.key.rsplit(".", 1)[-1]
                        if rb in active and r.key in claims:
                            add(sid, c.id, r.key)
                    elif r.key in claims:
                        add(sid, c.id, r.key)
                # same-frequency rule: another APP stack competing for a band we need. Exclude the
                # daemon STACK (it provides the radio). Also skip it entirely when the TARGET is the
                # daemon: its own dependent clients are consumers of the radio it provides, not
                # competitors — a real competitor (a direct-radio EXCLUSIVE owner like meshtastic)
                # is already caught by the exclusive-claim check above.
                comp_band = self._effective_band(sid, c.band) if multi else c.band
                if (not target_is_daemon and sid != target_stack
                        and sid != self.stack_of(self.DAEMON_ID)
                        and comp_band and comp_band in needed_bands):
                    add(sid, c.id, f"radio {comp_band} MHz")
        return blockers

    def start(self, target: str, apply: bool = False, params: dict | None = None,
              stop_owners: bool = False, band: str = "",
              daemon_overrides: dict | None = None,
              file_overrides: dict | None = None) -> ActionResult:
        """Public, LOCKED entry — acquires the full lifecycle lock bundle (incl. owners
        when stop_owners) so a DIRECT call gets the same coordination as CLI/web.
        `daemon_overrides`/`file_overrides` are ephemeral per-start values (this launch only, never
        persisted); None = apply the saved config, as the CLI does."""
        from . import reslock
        if not apply:
            return self._start_impl(target, apply=False, params=params,
                                    stop_owners=stop_owners, band=band,
                                    daemon_overrides=daemon_overrides,
                                    file_overrides=file_overrides)
        # Validate + canonicalize ordinary run params + file overrides BEFORE any lock — including
        # the config-stability guard. This is config-INDEPENDENT (it validates against the manifest,
        # not stored config), so an unqualified duplicate name, a non-mapping payload, or an unknown/
        # invalid value fails TYPED here — before config-stability/lifecycle locks, daemon work, owner
        # stops, config writes, spawn, or post-start. The canonical values feed lock planning and
        # `_start_impl` (which re-validates as a defensive boundary for internal/direct callers).
        params, pv_err = self._normalize_run_params(target, params)
        if pv_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {pv_err}",
                                next_commands=[f"lhpc status {target}"])
        file_overrides, fo_err = self._normalize_file_overrides(target, file_overrides)
        if fo_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {fo_err}",
                                next_commands=[f"lhpc status {target}"])
        # Hold saved configuration STABLE from lock planning through the whole applied start (LOCK
        # ORDER: config guard BEFORE the lifecycle/resource lock; re-entrant with _start_impl).
        try:
            with self._config_stable():
                # The daemon's REQUESTED radio mode determines which bands the lock bundle covers.
                _order = self._run_order(target)
                _radio = ""
                if _order:
                    _r, _ = self._daemon_needs(_order, params, self._config_band(target, band))
                    _radio = _r or ""
                try:
                    with self._lifecycle_guard("start", target, band,
                                               stop_owners=stop_owners, radio=_radio):
                        return self._start_impl(target, apply=True, params=params,
                                                stop_owners=stop_owners, band=band,
                                                daemon_overrides=daemon_overrides,
                                                file_overrides=file_overrides)
                except SourceTxnBlocked as blocked:
                    return ActionResult(False, f"Cannot start '{target}': {blocked}",
                                        next_commands=[f"lhpc status {target}"])
                except reslock.ResourceBusy as busy:
                    return ActionResult(False, f"Cannot start '{target}': {busy}",
                                        next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot start '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _start_impl(self, target: str, apply: bool = False, params: dict | None = None,
                    stop_owners: bool = False, band: str = "",
                    daemon_overrides: dict | None = None,
                    file_overrides: dict | None = None) -> ActionResult:
        """Applied starts run under the configuration-stability guard so saved config is a stable
        snapshot from the first read through generation/launch/post-start — a direct/internal
        apply=True call cannot bypass it. Dry-run holds no long-lived guard. A guard/read failure is
        a TYPED failure returned BEFORE any lifecycle side effect."""
        if not apply:
            return self._start_impl_inner(target, apply=False, params=params,
                                          stop_owners=stop_owners, band=band,
                                          daemon_overrides=daemon_overrides,
                                          file_overrides=file_overrides)
        try:
            with self._config_stable():                          # re-entrant (no-op if start() holds it)
                return self._start_impl_inner(target, apply=True, params=params,
                                              stop_owners=stop_owners, band=band,
                                              daemon_overrides=daemon_overrides,
                                              file_overrides=file_overrides)
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot start '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _start_impl_inner(self, target: str, apply: bool = False, params: dict | None = None,
                          stop_owners: bool = False, band: str = "",
                          daemon_overrides: dict | None = None,
                          file_overrides: dict | None = None) -> ActionResult:
        order = self._run_order(target)
        if order is None:
            return ActionResult(False, f"Unknown stack or component '{target}'.",
                                next_commands=["lhpc list"])
        if not self._paths.runtime_root_exists:
            return ActionResult(False, "Runtime root not bootstrapped.",
                                next_commands=["lhpc bootstrap"])
        # THE authoritative validation of ordinary ephemeral run params — BEFORE daemon-band
        # calculation, lifecycle-lock selection, conflict/owner handling, any daemon launch, CONF
        # change, config generation, client launch or post-start. Scoped to the target (a stack's
        # exposed params, or a direct component's own). An invalid/unknown override is a typed
        # failure; a dry-run plan surfaces it too, but apply fails before ANY lifecycle side effect.
        params, pv_err = self._normalize_run_params(target, params)
        if pv_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {pv_err}",
                                next_commands=[f"lhpc status {target}"])
        # Band-switchable stack: resolve the chosen band (default = first allowed).
        cfg_band = self._config_band(target, band)
        life = self._lifecycle()
        radio, tx = self._daemon_needs(order, params, cfg_band)
        # The stack whose daemon params to apply once the daemon is up (a direct component target
        # resolves to its owning stack).
        start_sid = self._owner_stack_id(target)
        # THE authoritative boundary for ephemeral Start-confirm overrides: validate + canonicalise
        # per band BEFORE any daemon launch, CONF mutation or client launch. An invalid override is
        # a typed failure (never silently discarded in favour of a saved/default value).
        launch_bands = ["433", "868"] if radio == "both" else ([radio] if radio in ("433", "868") else [])
        daemon_overrides, ov_err = self._normalize_ephemeral_overrides(
            start_sid, launch_bands, daemon_overrides)
        if ov_err:
            return ActionResult(False, f"Cannot start '{target}': invalid daemon parameter — {ov_err}",
                                next_commands=[f"lhpc status {target}"])
        # Ephemeral file-config overrides (Start-confirm 'Stack parameters'): validated here, then
        # applied for THIS launch only when the config file is (re)generated. Invalid = typed fail.
        file_over, fo_err = self._normalize_file_overrides(target, file_overrides)
        if fo_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {fo_err}",
                                next_commands=[f"lhpc status {target}"])
        # CALL/node enforcement (authoritative backstop; the web also guards for UX): a licensed
        # stack refuses an empty/N0CALL callsign, an unlicensed stack refuses an empty node name.
        # Only the actual APPLY is blocked — the dry-run PLAN still renders so the confirm page can
        # show the 'Stack parameters' panel where the operator supplies the call/node.
        if apply:
            id_ok, id_field, id_msg = self.enforce_identity(target, band, params, file_over)
            if not id_ok:
                return ActionResult(False, f"Cannot start '{target}': {id_msg}",
                                    data={"enforce_field": id_field},
                                    next_commands=[f"lhpc config {target}"])
        if not apply:
            details = []
            commands = []   # copyable commands the operator must run themselves
            for _, comp in order:
                if comp.id == self.DAEMON_ID:
                    details.append(f"  [daemon] start/ensure --radio {radio or 'both'}"
                                   + (f", TXMODE={tx}" if tx else ""))
                elif comp.interactive:
                    cmd = self.manual_start_command(comp)
                    details.append(f"  [manual] {comp.id} is interactive — the daemon is "
                                   "ensured, then run it yourself in a terminal:")
                    details.append(f"    {cmd}")
                    commands.append(cmd)
                elif comp.units and not comp.run_argv:
                    cmd = f"sudo systemctl start {comp.units[0].name}"
                    details.append(f"  [manual] {comp.id} is a system service — start it with:")
                    details.append(f"    {cmd}")
                    commands.append(cmd)
                else:
                    details.append(f"  [start] {comp.id} (band {cfg_band or comp.band or '-'})")
            blockers = self.run_blockers(target, band, radio)
            for bl in blockers:
                details.append(f"  [conflict] {bl['resource']} is held by running stack "
                               f"'{bl['holder_stack']}' ({bl['holder']})")
            return ActionResult(True, f"Run plan for '{target}': {len(order)} component(s) in order.",
                                details=details,
                                next_commands=[f"lhpc stack start {target} --yes"],
                                data={"changes": len(order), "blockers": blockers,
                                      "commands": commands})

        # No-side-effect Start FIRST — BEFORE any owner handling: if EVERY requested component is
        # already healthy, return ALREADY_HEALTHY immediately. Never run blockers for mutation,
        # never stop owners (even with stop_owners=True), never launch/write config/apply params/
        # CONF SET/touch markers for an already-healthy target.
        if apply:
            _hsnap = self.build_snapshot()
            _hidx = {c.id: ss.components[c.id] for ss in _hsnap.stacks for c in ss.stack.components}
            if self._all_components_healthy(order, _hidx, radio):
                _hres = [CompResult(component=comp.id, stack=stack.id, action="start",
                             outcome=Outcome.ALREADY_HEALTHY, summary="already running")
                         for stack, comp in order
                         if comp.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE)]
                return ActionResult(True, f"'{target}' already healthy — nothing to start.",
                    details=[f"  [already_healthy] {r.component}: already running" for r in _hres],
                    results=tuple(_hres), next_commands=[f"lhpc status {target}"])

        # A component about to start must never SILENTLY inherit an AMBIGUOUS flat legacy value (a
        # run/file param name declared by >= 2 owner-stack components, with a flat value present and
        # no component-scoped value). Fail TYPED here — BEFORE any owner stop, daemon launch, daemon
        # mutation, config-file write, process spawn or post-start scheduling.
        _amb = self._config_ambiguity(target, order, band)
        if _amb is not None:
            return ActionResult(False, f"Cannot start '{target}': {_amb}",
                                next_commands=[f"lhpc config {target}"])

        # Ownership check: if a needed resource is held by another running stack,
        # either stop that stack first (stop_owners) or refuse and report it.
        blockers = self.run_blockers(target, band, radio)
        if blockers:
            owners = sorted({bl["holder_stack"] for bl in blockers})
            if not stop_owners:
                details = [f"  {bl['resource']} held by running stack '{bl['holder_stack']}'"
                           for bl in blockers]
                return ActionResult(
                    False,
                    f"Cannot run '{target}': {', '.join(owners)} must be stopped first.",
                    details=details,
                    next_commands=[f"lhpc stack stop {o}" for o in owners])
            prelude = []
            unstopped = []
            for o in owners:
                ores = self.stop(o, apply=True)
                if ores.ok:
                    prelude.append(f"  [stopped] conflicting stack '{o}'")
                else:
                    unstopped.append(o)
                    prelude.append(f"  [blocked] conflicting stack '{o}' did not stop "
                                   f"(verified): {ores.summary}")
            if unstopped:
                # Do not launch the target while a conflicting owner is still up.
                return ActionResult(
                    False,
                    f"Cannot run '{target}': conflicting stack(s) {', '.join(unstopped)} "
                    "could not be verified stopped.",
                    details=prelude,
                    next_commands=[f"lhpc status {o}" for o in unstopped])
            time.sleep(1.0)  # let sockets/locks release
        else:
            prelude = []

        snap = self.build_snapshot()
        st_index = {c.id: ss.components[c.id]
                    for ss in snap.stacks for c in ss.stack.components}
        out = list(prelude)
        results: list[CompResult] = []   # TYPED per-component outcomes (source of truth)
        daemon_ok = True                # gate dependents on verified daemon readiness

        def record(comp, stack, outcome, summary):
            results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                                      outcome=outcome, summary=summary))
            out.append(f"  [{outcome.value}] {comp.id}: {summary}")

        # Config generation + launch config are COMPONENT-scoped so a direct component start never
        # writes a sibling's config nor leaks the target's ephemeral overrides / run params into a
        # dependency: the explicit target's overrides apply ONLY to the target (or every component
        # of a stack target); each dependency uses its OWN saved/default values.
        _target_is_stack = self.stack(target) is not None
        def _comp_overrides(comp_id):
            if not (_target_is_stack or comp_id == target):
                return None
            return self._overrides_for_comp(target, "file", file_over, comp_id)

        for stack, comp in order:
            state = st_index[comp.id].run_state
            running = state == RunState.RUNNING        # DEGRADED is NOT healthy
            # A DEGRADED component (process up but a ready endpoint missing) must not
            # be treated as healthy and must not trigger a duplicate launch.
            if state == RunState.DEGRADED and comp.id != self.DAEMON_ID:
                record(comp, stack, Outcome.BLOCKED, "running but DEGRADED (a ready "
                       "endpoint is missing) — stop it (verified) and re-run")
                continue
            if comp.id == self.DAEMON_ID:
                dlines, dok = self._ensure_daemon(life, stack, comp, running, radio, params,
                                                  start_sid, daemon_overrides)
                out.extend(dlines)
                results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                    outcome=(Outcome.VERIFIED if dok else Outcome.FAILED),
                    summary="daemon ready" if dok else "daemon readiness/TX gating failed"))
                daemon_ok = dok
                continue
            # A dependent must NOT start when the daemon it needs failed readiness.
            if not daemon_ok and self.DAEMON_ID in comp.depends_on:
                record(comp, stack, Outcome.BLOCKED, "daemon not ready — not started")
                continue
            if running:
                record(comp, stack, Outcome.ALREADY_HEALTHY, "already running")
                continue
            if comp.source and not life.source_dir(comp).exists():
                record(comp, stack, Outcome.BLOCKED, f"not installed (lhpc install {stack.id})")
                continue
            if comp.interactive:
                # Never auto-start an interactive TUI — the operator runs it in a terminal.
                # But its required runtime config must be generated FIRST: if generation
                # fails (failed/no-base/unsafe source) or the source is a read-only linked
                # tree, DO NOT write the interactive marker and DO NOT present a manual
                # command as ready-to-run — return a typed block/manual-required instead.
                if comp.config_file:
                    cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id))
                    mine = [w for w in cw if w.component == comp.id]
                    bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                    if bad:
                        record(comp, stack, Outcome.BLOCKED,
                               f"interactive start blocked — required config could not be "
                               f"generated ({bad.path}: {bad.detail})")
                        continue
                    linked = next((w for w in mine if w.status == "linked-readonly"), None)
                    if linked:
                        record(comp, stack, Outcome.MANUAL_REQUIRED,
                               f"linked source is read-only — generate {comp.id}'s config in "
                               f"your own checkout before starting it ({linked.path})")
                        continue
                marked = self.mark_interactive(stack.id, cfg_band)
                blocker = self.install_blocker(comp)
                marker_note = ("" if marked else
                               " (note: interactive marker could not be persisted — the "
                               "dashboard may not show its command block)")
                if blocker:
                    record(comp, stack, Outcome.BLOCKED, f"interactive but {blocker}")
                else:
                    # The start COMMAND is shown on the app's dashboard card (and the
                    # interactive marker drives that) — don't duplicate it here.
                    record(comp, stack, Outcome.MANUAL_REQUIRED,
                           f"interactive — start it from its card on the dashboard{marker_note}")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised (systemd, root) — lhpc observes, never starts.
                record(comp, stack, Outcome.MANUAL_REQUIRED,
                       f"system service — start with: sudo systemctl start {comp.units[0].name}")
                continue
            miss = life.missing_requirements(comp)
            if miss:
                record(comp, stack, Outcome.BLOCKED, "missing "
                       + "; ".join(f"{r.cmd} ({r.install})" for r in miss))
                continue
            if self._running_conflicts(comp, cfg_band):
                record(comp, stack, Outcome.BLOCKED, "resource conflict")
                continue
            if not self.is_built(comp):
                record(comp, stack, Outcome.BLOCKED,
                       f"not built — build it first (lhpc build {stack.id})")
                continue
            # Regenerate any config file this component reads (per the chosen band). A
            # generation FAILURE for this component blocks the launch — never start with
            # stale or absent configuration.
            if comp.config_file:
                cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id))
                mine = [w for w in cw if w.component == comp.id]
                bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                if bad:
                    record(comp, stack, Outcome.BLOCKED,
                           f"config generation failed ({bad.path}: {bad.detail})")
                    continue
                # A linked external source is read-only to lhpc: it cannot generate the
                # required config, so the operator must provide it in their own checkout.
                # This is MANUAL_REQUIRED, never a silent start with absent config.
                linked = next((w for w in mine if w.status == "linked-readonly"), None)
                if linked:
                    record(comp, stack, Outcome.MANUAL_REQUIRED,
                           f"linked source is read-only — generate {comp.id}'s config in "
                           f"your own checkout ({linked.path})")
                    continue
            # COMPONENT-scoped launch config (this component's OWN run params from the owner-stack
            # store) so a stored sibling run parameter can never leak into another component's argv
            # through a name collision. Ephemeral confirm-page params (this start only) override it
            # for BOTH the launch and post-start — but only for the explicit target (or every
            # component of a stack target), never leaking the target's values into a dependency.
            comp_cfg = dict(self.stack_config(comp.id, cfg_band))
            if _target_is_stack or comp.id == target:
                comp_cfg.update(self._overrides_for_comp(target, "run", params, comp.id))
            res = life.start(stack, comp, comp_cfg, band=cfg_band)
            if not res.ok:
                # A launch that couldn't be owned AND couldn't be proven ceased is a
                # typed UNVERIFIED (residual process), not a clean FAILED.
                record(comp, stack,
                       Outcome.UNVERIFIED if res.unverified else Outcome.FAILED,
                       f"start failed: {res.detail} (log {res.log_path})")
                continue
            # readiness="endpoint": VERIFIED only once every ready=true endpoint is up;
            # otherwise SIGTERM the just-launched owned session (verified cleanup) and
            # report UNVERIFIED — no post-start work runs.
            if comp.readiness == "endpoint":
                ready_ok, ev = self._ready_endpoints_present(comp)
                if not ready_ok:
                    cleanup = life.stop(comp, band=cfg_band)
                    record(comp, stack, Outcome.UNVERIFIED,
                           f"ready endpoint(s) never came up ({'; '.join(ev)}); cleanup: "
                           + ("stopped" if cleanup.outcome == Outcome.STOPPED
                              else "cessation NOT verified — ownership retained"))
                    continue
                summary = f"started; ready endpoint(s) up ({'; '.join(ev)})"
            else:
                summary = f"started (log {res.log_path})"
            # Required post-start must complete before VERIFIED; optional is scheduled.
            # `required_ok` is True (required passed), False (required failed), or None
            # (no required post-start) — explicit, no enum-attribute confusion.
            required_ok, post_summary = self._run_post_start(life, stack, comp, comp_cfg, cfg_band)
            if required_ok is False:
                cleanup = life.stop(comp, band=cfg_band)
                record(comp, stack, Outcome.UNVERIFIED,
                       f"required post-start failed: {post_summary}; cleanup: "
                       + ("stopped" if cleanup.outcome == Outcome.STOPPED
                          else "cessation NOT verified — ownership retained"))
                continue
            if post_summary:
                summary += f"; {post_summary}"
            # Persist the running-band marker BEFORE declaring VERIFIED: it drives
            # multi-band decisions + dashboard state, so a write failure must surface in
            # the typed result (UNVERIFIED), not hide behind a clean VERIFIED.
            band_ok = True
            if cfg_band and self.stack_bands(stack.id):
                band_ok = self._set_running_band(stack.id, cfg_band)
            if band_ok:
                record(comp, stack, Outcome.VERIFIED, summary)
            else:
                record(comp, stack, Outcome.UNVERIFIED,
                       summary + "; running-band marker could not be persisted — "
                       "operational state may be inconsistent")
        self.clear_stale_interactive(keep=self.stack_of(target) or target)
        # ok derives ENTIRELY from typed outcomes. A MANUAL_REQUIRED for an OPTIONAL
        # component does not block; every other non-success outcome does.
        optional_ids = {c.id for _, c in order if c.optional}
        def blocks(r):
            if r.outcome == Outcome.MANUAL_REQUIRED and r.component in optional_ids:
                return False
            return not r.ok
        blocking = [r for r in results if blocks(r)]
        required_manual = [r.component for r in blocking if r.outcome == Outcome.MANUAL_REQUIRED]
        failed = [r.component for r in blocking if r.outcome != Outcome.MANUAL_REQUIRED]
        ok = not blocking
        if failed:
            summary = f"Run FAILED for '{target}': {', '.join(failed)} did not start/verify."
        elif required_manual:
            summary = (f"Run for '{target}': manual start required for "
                       f"{', '.join(required_manual)} — see the dashboard.")
        else:
            summary = f"Run applied for '{target}'."
        return ActionResult(ok, summary, details=out, results=tuple(results),
                            next_commands=[f"lhpc status {target}", f"lhpc logs {target}",
                                           f"lhpc stack stop {target}"])

    def _run_post_start(self, life, stack, comp, comp_cfg, band) -> tuple[bool | None, str]:
        """Run post-start steps. Returns (required_ok, summary):
          * (None, "")               — no post-start;
          * (None, "…scheduled")     — OPTIONAL steps scheduled detached (never gates);
          * (True,  "…completed")    — REQUIRED steps ran synchronously and PASSED;
          * (False, "…failed (rc N)")— REQUIRED steps FAILED → caller blocks VERIFIED
                                        and invokes verified cleanup.
        `required_ok is False` is the only blocking case (an explicit bool, not an
        enum attribute)."""
        if not comp.post_steps:
            return None, ""
        if life.has_required_post_start(comp):
            jr = life.run_required_post_start(stack, comp, comp_cfg, band=band)
            if jr.ok:
                return True, "required post-start completed"
            return False, f"required post-start failed (rc {jr.returncode})"
        # OPTIONAL: scheduling never gates the start, but its typed result makes any
        # scheduling failure VISIBLE in the details (it is no longer swallowed by
        # `spawn_post_start`, so no blanket catch is needed here).
        sched = life.spawn_post_start(stack, comp, comp_cfg, band=band)
        if sched.ok:
            return None, "optional post-start scheduled"
        if getattr(sched, "unverified", False):
            # Lifecycle-INTEGRITY failure: a spawned runner we can neither own nor prove stopped.
            # This GATES the main VERIFIED result (unlike an ordinary optional transport failure).
            return False, f"post-start runner integrity failure: {sched.detail}"
        return None, f"optional post-start could NOT be scheduled: {sched.detail}"

    def _daemon_radio_modes(self) -> list:
        """`--radio` mode of every OBSERVED daemon process (by command line). A missing/unknown
        mode is returned as None so callers can treat it conservatively."""
        import posixpath
        modes = []
        for _pid, argv in self._system.procfs.cmdlines().items():
            if not argv or posixpath.basename(argv[0]) != "loraham_daemon":
                continue
            radio = None
            for i, tok in enumerate(argv):
                if tok == "--radio" and i + 1 < len(argv):
                    radio = argv[i + 1]
                elif tok.startswith("--radio="):
                    radio = tok.split("=", 1)[1]
            modes.append(radio)
        return modes

    def _daemon_claimed_bands(self) -> set:
        """Radio bands CLAIMED by observed daemon PROCESSES (command-line topology — the authoritative
        ownership signal). `--radio 433` → {433}, `--radio 868` → {868}, `--radio both` (or a
        missing/unknown mode) → conservatively {433, 868}. A CONF socket that is unreachable /
        UNINITIALIZED / FAILED does NOT free the radio — the live process still owns it."""
        bands = set()
        for radio in self._daemon_radio_modes():
            if radio == "433":
                bands.add("433")
            elif radio == "868":
                bands.add("868")
            else:
                bands |= {"433", "868"}       # both / missing / unknown -> conservative
        return bands

    def _daemon_pids_for_band(self, band: str) -> list[int]:
        """PIDs of daemon instances that serve `band` — those launched with
        --radio <band> or --radio both. Lets a per-band Stop signal only that
        instance, leaving the other band's daemon running."""
        import posixpath
        out = []
        for pid, argv in self._system.procfs.cmdlines().items():
            if not argv or posixpath.basename(argv[0]) != "loraham_daemon":
                continue
            radio = None
            for i, tok in enumerate(argv):
                if tok == "--radio" and i + 1 < len(argv):
                    radio = argv[i + 1]
                elif tok.startswith("--radio="):
                    radio = tok.split("=", 1)[1]
            if radio in (band, "both") or radio not in ("433", "868"):
                out.append(pid)              # unknown mode -> conservatively serves the band
        return out

    def _ensure_daemon(self, life, stack, comp, running, radio, params, start_sid,
                       daemon_overrides=None):
        """Ensure the daemon is up FOR THE NEEDED BAND before the app starts.

        "Running" means the band's CONF socket is reachable, not merely that a
        daemon process exists. The daemon is MULTI-INSTANCE (independent process +
        lock per band), so a daemon serving only the OTHER band does not block us —
        we just start a separate instance for the band we need:
          * serving the band      -> just SET the needed TX mode (no restart);
          * not serving the band   -> start a daemon instance with --radio <band>
            (works alongside an instance already serving the other band).
        """
        # Bands this start must make ready. --radio both must ensure BOTH 433 and 868.
        needed = ["433", "868"] if (radio or "both") == "both" else [radio]
        views = {b: daemon_control.read_view(self._system, b) for b in needed}
        # Classify each band from CONF readiness AND process topology. A CONF socket that is
        # unreachable does NOT mean the radio is free: an observed daemon PROCESS may still hold it.
        #   READY (retain) | reachable-not-READY (fail) | CONF-down-but-process-claims-it (fail, do
        #   NOT relaunch/SET) | truly absent (safe to launch).
        claimed = self._daemon_claimed_bands()
        not_ready = [b for b in needed if views[b].reachable and not views[b].ready]
        claimed_down = [b for b in needed if not views[b].reachable and b in claimed]
        absent = [b for b in needed if not views[b].reachable and b not in claimed]
        lines, ok_all = [], True
        for b in not_ready:
            ok_all = False
            lines.append(f"  [fail] daemon on {b}: reachable but RADIO="
                         f"{views[b].radio_state or 'unknown'} (not READY) — dependent launch blocked")
        for b in claimed_down:
            ok_all = False
            lines.append(f"  [fail] daemon on {b}: CONF socket unreachable but a daemon process "
                         f"still holds the radio — not relaunched (resolve the stuck instance first)")
        started: set = set()
        if absent:
            if comp.source and not life.source_dir(comp).exists():
                lines.append("  [skip] daemon: not installed (lhpc install daemon)")
                return lines, False
            if not self.is_built(comp):
                lines.append("  [BLOCKED] daemon: not built — build it first (lhpc build daemon)")
                return lines, False
            # Start ONE per-band instance for EACH missing band — lhpc NEVER launches `--radio
            # both` (a legacy mode the operator may still start manually); it runs an independent
            # `--radio <band>` per band. The TX mode is applied LIVE once the socket is up.
            for b in sorted(absent):
                dparams = dict(self.stack_config("daemon"))
                dparams["radio"] = b
                if params and params.get("debug"):
                    dparams["debug"] = "1"
                res = life.start(stack, comp, dparams, band=b)
                base = f"start daemon --radio {b}"
                if not res.ok:
                    lines.append(f"  [fail] {base}: {res.detail}")
                    return lines, False
                lines.append(f"  [ok] {base}")
                started.add(b)
        # Verify + apply, once per band that should now be READY (retained or freshly started;
        # not-ready bands were already failed above and are skipped).
        for b in needed:
            if b in not_ready or b in claimed_down:
                continue                      # failed bands: no retain, no CONF SET
            if b in started:
                if not self._verify_band_up(b):
                    ok_all = False
                    lines.append(f"  [fail] {b} CONF socket never came up — the daemon failed "
                                 f"to init on {b} (radio/SPI busy or a stale lock); see its log.")
                    continue
            else:
                lines.append(f"  [ok] daemon already serving {b}")
                # A band already serving ANOTHER running stack must NOT be reconfigured — a daemon
                # (re)start in a new mode (e.g. FSK) must never disrupt a client already using this
                # band (its config is whatever that client needs). Freshly-started bands still get
                # this start's params applied.
                daemon_sid = self.stack_of(self.DAEMON_ID) or "daemon"
                others = [d for d in self.stop_dependents(daemon_sid, bands={b}) if d != start_sid]
                if others:
                    lines.append(f"  [keep] {b} in use by {', '.join(others)} — daemon "
                                 f"config left unchanged")
                    continue
            plines, tx_ok = self._apply_stack_daemon_params(start_sid, b,
                                                            (daemon_overrides or {}).get(b))
            lines.extend(plines)
            ok_all = ok_all and tx_ok
        return lines, ok_all

    def _apply_tx_mode(self, band: str, tx: str) -> tuple[bool, str]:
        """Apply a REQUIRED daemon TX mode and verify it by READBACK. Returns
        (ok, detail). A failed SET, or a readback that is absent/mismatched/
        malformed/timed-out, is a failure that gates every dependent — never a
        warning-success. Skips the SET only when the mode already matches."""
        want = tx.upper()
        view = daemon_control.read_view(self._system, band)
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for TX-mode set (RADIO={state})"
        if view.status.get("TXMODE", "").upper() == want:
            return True, f"TXMODE already {want}"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, "TXMODE", tx)
        if not ok:
            return False, f"SET TXMODE={want} rejected: {detail}"
        waited = 0.0
        while True:                              # bounded read-only readback
            v = daemon_control.read_view(self._system, band)
            got = v.status.get("TXMODE", "").upper() if v.reachable else ""
            if got == want:
                return True, f"SET TXMODE={want} confirmed by readback"
            if self.DAEMON_VERIFY_TIMEOUT_S <= 0 or waited >= self.DAEMON_VERIFY_TIMEOUT_S:
                return False, f"TXMODE readback {got or 'absent'} != {want} (not applied)"
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S

    @staticmethod
    def _cadidle_eq(got, want) -> bool:
        """Numeric equality of two CADIDLE values (daemon reports `CADIDLE=<ms>`); a
        missing/non-numeric reading never matches."""
        try:
            return got is not None and int(got) == int(want)
        except (TypeError, ValueError):
            return False

    def _apply_conf_param(self, band: str, key: str, want) -> tuple[bool, str]:
        """Apply a numeric daemon LBT param (CADIDLE/CADWAIT, ms) and verify by READBACK.
        Returns (ok, detail). NON-GATING tuning: a failure is only reported (never blocks the
        dependent) because the daemon still does LBT at its current value. Skips the SET when
        the value already matches."""
        want = str(want).strip()
        view = daemon_control.read_view(self._system, band)
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for {key} set (RADIO={state})"
        if self._cadidle_eq(view.status.get(key), want):
            return True, f"{key} already {want}ms"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, key, want)
        if not ok:
            return False, f"SET {key}={want} rejected: {detail}"
        waited = 0.0
        while True:                              # bounded read-only readback
            v = daemon_control.read_view(self._system, band)
            got = v.status.get(key) if v.reachable else None
            if self._cadidle_eq(got, want):
                return True, f"SET {key}={want}ms confirmed by readback"
            if self.DAEMON_VERIFY_TIMEOUT_S <= 0 or waited >= self.DAEMON_VERIFY_TIMEOUT_S:
                return False, f"{key} readback {got or 'absent'} != {want} (not applied)"
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S

    # -- per-stack daemon radio parameters (see core/daemon_params.py) ------

    def _apply_daemon_param(self, band: str, key: str, value: str) -> tuple[bool, str]:
        """Apply one daemon param at `band`. TXMODE and CAD timing are confirmed by readback;
        radio params (FREQ/SF/BW/…) are SET once — the daemon never echoes them, so they are
        reported SENT-but-unconfirmed (non-gating)."""
        if key == "TXMODE":
            return self._apply_tx_mode(band, value)
        if key in ("CADWAIT", "CADIDLE"):
            return self._apply_conf_param(band, key, value)
        view = daemon_control.read_view(self._system, band)
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for {key} (RADIO={state})"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, key, value)
        return ok, detail

    def _effective_daemon_band(self, target: str, band: str = "") -> str:
        """The single band the daemon-params panel/apply uses: the requested band if valid, else
        the stack's fixed band (from its band-component), else 433."""
        if band in daemon_control.ALLOWED_BANDS:
            return band
        s = self._owner_stack(target)
        fixed = sorted({c.band for c in (s.components if s else ()) if c.band}
                       & set(daemon_control.ALLOWED_BANDS))
        return fixed[0] if fixed else "433"

    def _daemon_param_overrides(self, stack_id: str, band: str) -> dict:
        """Persisted operator overrides for a stack's daemon params, read from the stack's
        runtime-local config as flat `dp_<band>_<PARAM>` keys (dot-free, so TOML never nests
        them). Only validated keys are returned; {} when none."""
        from . import daemon_params, config as cfgmod
        stored = cfgmod.load_stack_config(self._paths, self._owner_stack_id(stack_id))
        out: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            v = stored.get(f"dp_{band}_{name}")
            if v not in (None, "") and daemon_control.validate_set(name, str(v)) is None:
                out[name] = str(v)
        return out

    def _has_daemon_params(self, target: str) -> bool:
        """True when `target` gets a daemon-param panel: a daemon-client stack/component, the daemon
        component, or the daemon stack. Owner-stack scoped, so a direct daemon-backed COMPONENT
        target (e.g. meshcom-qemu, meshcore-pi) resolves through its owning stack."""
        from . import daemon_params
        return daemon_params.is_client(self._owner_stack_id(target)) or self._is_daemon_target(target)

    def _daemon_param_applies(self, stack_id: str, band: str, overrides: dict | None = None) -> dict:
        """The daemon params lhpc APPLIES for this stack+band — the effective value (source
        default merged with the persisted operator override) of EVERY param that has one. Applied
        ONCE after the daemon is up and before the stack's own components start; the app then
        overwrites the radio params it owns. `overrides` are EPHEMERAL per-start values (e.g. from
        the start-confirm panel) that take precedence for this apply only and are NOT persisted —
        so a confirm-page "Reset to defaults" changes what is applied now, never the saved config.
        Ordered radio-first. Empty for non-daemon stacks."""
        from . import daemon_params
        if not self._has_daemon_params(stack_id):
            return {}
        if band not in daemon_control.ALLOWED_BANDS:
            return {}
        ov = self._daemon_param_overrides(stack_id, band)          # persisted config overrides
        # Ephemeral this-start values (already validated + canonicalised at the start boundary,
        # `_normalize_ephemeral_overrides`) take precedence for this apply only; never persisted.
        for key, val in (overrides or {}).items():
            if key in daemon_params.ALL_PARAMS and str(val) != "":
                ov[key] = str(val)
        out: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            eff = ov.get(name) or daemon_params.default_value(stack_id, band, name)
            if eff:
                out[name] = eff
        return out

    def _normalize_ephemeral_overrides(self, target: str, launch_bands: list, raw):
        """THE service-side validation/normalization boundary for ephemeral Start-confirm daemon
        overrides. `raw` is a per-band map ``{band: {PARAM: value}}`` (or None). Returns
        ``({band: {PARAM: canonical}}, None)`` on success, or ``({}, error)`` — REJECTING an unknown
        param key, an unknown band, a band not part of THIS launch, and any malformed / out-of-range
        / invalid-enum value (identifying the band + param). Accepted values are canonicalised
        exactly like a persisted save (`fsk`→`FSK`, `028`→`28`); `MODE=FSK` is accepted (browser-
        warning-only). A BLANK value = no override for that key (absent key = same); Reset submits
        explicit default values, so a blank can never resurrect a saved override."""
        from . import daemon_params
        if not raw:
            return {}, None
        if not isinstance(raw, dict):
            return {}, "malformed daemon override payload"
        if not self._has_daemon_params(target):
            return {}, f"{target} has no configurable daemon parameters"
        allowed = set(launch_bands)
        out: dict = {}
        for band, params in raw.items():
            if band not in daemon_control.ALLOWED_BANDS:
                return {}, f"unknown radio band {band!r}"
            if band not in allowed:
                return {}, f"band {band} MHz is not part of this start"
            if not isinstance(params, dict):
                return {}, f"malformed override for band {band}"
            canon: dict = {}
            for name, val in params.items():
                if name not in daemon_params.ALL_PARAMS:
                    return {}, f"unknown daemon parameter {name!r} ({band} MHz)"
                v = str(val).strip()
                if v == "":
                    continue                                       # blank -> no override
                err = daemon_control.validate_set(name, v)
                if err:
                    return {}, f"{band} MHz {name}: {err}"
                canon[name] = daemon_control.canonical_value(name, v)
            if canon:
                out[band] = canon
        return out, None

    def _apply_stack_daemon_params(self, stack_id: str, band: str,
                                   overrides: dict | None = None) -> tuple[list, bool]:
        """Apply the stack's daemon params to a READY band, once (ephemeral `overrides` take
        precedence for this start only). Returns (lines, tx_ok): TXMODE is gating (the app needs
        its mode); radio params (sent-unconfirmed) and CAD tuning are non-gating."""
        lines, tx_ok = [], True
        for key, val in self._daemon_param_applies(stack_id, band, overrides).items():
            ok, detail = self._apply_daemon_param(band, key, val)
            gating = key == "TXMODE"                     # the app needs its mode; radio/CAD not
            if gating:
                tx_ok = ok
            tag = "ok" if ok else ("fail" if gating else "warn")
            # Radio params aren't echoed by the daemon; keep the start log concise (no verbose
            # "SENT but UNCONFIRMED …" explanation for each one).
            if ok and not daemon_control.is_confirmable(key):
                detail = f"{key}={val} sent"
            lines.append(f"  [{tag}] {band}: {detail}")
        return lines, tx_ok

    def daemon_params_view(self, target: str, band: str = "") -> dict:
        """Web view for the daemon-params panel: the grouped, editable radio-parameter rows for
        ONE band (the page's selected band, or the stack's fixed band). {} for direct-SPI /
        unknown stacks. Values are the effective config-file values (default + operator save)."""
        from . import daemon_params
        sid = self._owner_stack_id(target)                   # owner-stack daemon profile + storage
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or daemon_params.is_client(sid)):
            return {}
        b = self._effective_daemon_band(target, band)
        # "Apply live" only makes sense against a live daemon: the daemon page always, or an
        # app stack that is currently running (its daemon dependency is up).
        can_apply = is_daemon or self.stack_running(sid)
        return {"stack": target, "band": b, "is_daemon": is_daemon, "can_apply": can_apply,
                "rows": daemon_params.stack_view(sid, b, self._daemon_param_overrides(target, b))}

    def daemon_start_panels(self, target: str, params: dict | None = None, band: str = "",
                            display_overrides: dict | None = None) -> list:
        """Start-confirm panel view(s): ONE per band THIS launch will touch — two for a daemon
        `--radio both`, one for a single-band daemon or client start. Each panel carries its own
        band, source defaults + saved overrides, and (via the template) band-scoped input names
        `dp_<band>_<PARAM>`, so a 433 value never reaches 868. The radio mode comes from `params`
        (the daemon's `p_radio`), not just the URL band. `display_overrides` ({band: {PARAM: value}})
        are SUBMITTED-but-unsaved panel values shown on a re-render (so a failed Save & start keeps
        the operator's edits). [] for direct-SPI / unknown stacks."""
        from . import daemon_params
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or daemon_params.is_client(self._owner_stack_id(target))):
            return []
        radio, _tx = self._daemon_needs(self._run_order(target), params, self._config_band(target, band))
        if radio == "both":
            bands = ["433", "868"]
        elif radio in ("433", "868"):
            bands = [radio]
        else:
            bands = [self._effective_daemon_band(target, band)]

        sid = self._owner_stack_id(target)                   # owner-stack daemon profile + overrides
        def _rows(b):
            over = dict(self._daemon_param_overrides(target, b))
            sub = (display_overrides or {}).get(b) if display_overrides else None
            if sub:                                          # show non-blank submitted values
                over.update({k: v for k, v in sub.items() if str(v).strip() != ""})
            return daemon_params.stack_view(sid, b, over)
        return [{"stack": target, "band": b, "is_daemon": is_daemon, "rows": _rows(b)}
                for b in bands]

    def save_daemon_params(self, target: str, band: str, values: dict) -> ActionResult:
        """Persist operator overrides for a stack's daemon params (band-scoped). Semantics:
          * a param NOT present in `values` is left UNCHANGED (direct callers patch a subset);
          * an explicitly BLANK value clears ONLY that param's override;
          * a supplied value is validated, then CANONICALISED (enum upper-cased, integer
            normalised) before compare/store — an equivalent-to-default value clears rather than
            persisting a redundant override, and e.g. `fsk` is stored/displayed as `FSK`.
        Persisted via the LOCKED merge, so normal params, other-band dp_*, remotes and autostart
        all survive. Never applies live."""
        from . import daemon_params, config as cfgmod
        sid = self._owner_stack_id(target)                     # persist into the OWNER stack config
        band = self._effective_daemon_band(target, band)
        if not self._has_daemon_params(target):
            return ActionResult(False, f"{target} has no configurable daemon parameters")
        updates: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            if name not in values:
                continue                                           # omitted -> leave unchanged
            key = f"dp_{band}_{name}"
            raw = str(values[name]).strip()
            if raw == "":
                updates[key] = ""                                  # explicit blank -> clear this key
                continue
            err = daemon_control.validate_set(name, raw)
            if err:
                return ActionResult(False, f"{name}: {err}")
            canon = daemon_control.canonical_value(name, raw)
            updates[key] = "" if canon == daemon_params.default_value(sid, band, name) else canon
        try:
            # Merge ONLY the dp_<band>_<PARAM> keys into the OWNER stack config (clear_empty=True);
            # sibling run/file params, other-band dp_*, remotes and autostart all survive untouched.
            cfgmod.update_stack_config(self._paths, sid, updates)
        except (OSError, cfgmod.ConfigError, PathContainmentError) as exc:
            return ActionResult(False, f"could not save daemon params: {exc}")
        self._invalidate_config()
        return ActionResult(True, f"saved daemon params for {target} ({band})")

    def apply_daemon_params(self, target: str, band: str = "") -> ActionResult:
        """Apply this stack's effective daemon params to the RUNNING daemon now (the Apply button).

        Serializes against start/stop/restart and another Apply on the same band via the band
        lifecycle lock (re-entrant per thread). TRUTHFUL: `ok=True` only when every attempted set
        is applied; `ok=False` for total failure; a `PARTIAL` `ok=False` when some fail. The
        structured `data` reports band + attempted/applied/failed/confirmed/sent-unconfirmed keys —
        radio params the daemon does not echo are reported SENT (never claimed as read-back
        confirmed). Valid settings were persisted first (by the caller); a live-apply failure never
        rolls that back — `data['persisted']` says so."""
        from . import daemon_params, reslock
        sid = self._owner_stack_id(target)                       # owner-stack profile + lock scope
        if not self._has_daemon_params(target):
            return ActionResult(False, f"{target} has no configurable daemon parameters")
        # Apply live only on the daemon itself or a running app stack (defence in depth: the
        # UI disables the button, the service enforces it).
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or self.stack_running(sid)):
            return ActionResult(False, f"Apply live is only available while {target} is running")
        b = self._effective_daemon_band(target, band)
        keys = [f"lifecycle.{sid}", f"claim.loraham.radio.{b}"]   # serialize vs start/stop on band
        try:
            with self._keys_guard("apply", target, keys):        # re-entrant per thread
                if not daemon_control.read_view(self._system, b).reachable:
                    return ActionResult(False, f"daemon not serving {b} MHz — start it first",
                                        data={"band": b, "persisted": True})
                applies = self._daemon_param_applies(sid, b)
                applied, failed, confirmed, unconfirmed, details = [], [], [], [], []
                for key, val in applies.items():
                    ok, detail = self._apply_daemon_param(b, key, val)
                    if ok:
                        applied.append(key)
                        (confirmed if daemon_control.is_confirmable(key) else unconfirmed).append(key)
                    else:
                        failed.append(key)
                    details.append(f"{key}={val}: {detail}")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"radio {b} MHz is busy ({busy}); try again",
                                data={"band": b, "busy": True, "persisted": True})
        data = {"band": b, "attempted": list(applies), "applied": applied, "failed": failed,
                "confirmed": confirmed, "sent_unconfirmed": unconfirmed, "persisted": True}
        n = len(applies)
        if n == 0:
            return ActionResult(True, f"no daemon parameters to apply on {b} MHz", data=data)
        if not failed:
            return ActionResult(True, f"applied {n}/{n} on {b} MHz ({len(confirmed)} confirmed, "
                                f"{len(unconfirmed)} sent-unconfirmed)", details=details, data=data)
        if not applied:
            return ActionResult(False, f"FAILED to apply any of {n} daemon params on {b} MHz "
                                "(saved profile unchanged)", details=details, data=data)
        return ActionResult(False, f"PARTIAL: applied {len(applied)}/{n}, {len(failed)} FAILED on "
                            f"{b} MHz (saved profile unchanged)", details=details, data=data)

    def reset_daemon_params(self, target: str, band: str) -> ActionResult:
        """Clear all daemon-param overrides for a stack+band (back to source defaults)."""
        from . import daemon_params
        return self.save_daemon_params(target, band, {k: "" for k in daemon_params.ALL_PARAMS})

    def _verify_band_up(self, band: str) -> bool:
        """Poll a band's CONF socket until the daemon reports RADIO=READY, up to the
        verify timeout. A reachable daemon that is still UNINITIALIZED or FAILED is NOT
        up: the radio inits asynchronously, so we wait for readiness (not mere socket
        reachability). With the timeout disabled (tests) it is a single bounded check."""
        if self.DAEMON_VERIFY_TIMEOUT_S <= 0:
            return daemon_control.read_view(self._system, band).ready
        waited = 0.0
        while waited < self.DAEMON_VERIFY_TIMEOUT_S:
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S
            if daemon_control.read_view(self._system, band).ready:
                return True
        return False

    def _running_bands_of(self, ss, run_comps) -> set:
        """Radio band(s) the RUNNING components of a snapshot stack actually use: the
        effective band for a band-switchable stack, else the components' declared bands."""
        if self.stack_bands(ss.stack.id):                  # band-switchable -> running band
            eb = self._effective_band(ss.stack.id)
            return {eb} if eb else set()
        return {c.band for c in run_comps if c.band}

    def _uncertain_daemon_dependents(self, target: str) -> list[str]:
        """Running daemon-dependent stacks whose ACTIVE radio band cannot be trusted for a PER-BAND
        daemon stop — band-switchable stacks with NO valid running/interactive marker. A per-band
        stop must never guess such a peer's band (it could stop or spare the wrong one)."""
        tstack = self.stack(target)
        if tstack is None:
            return []
        member_ids = {c.id for c in tstack.components}
        up = (RunState.RUNNING, RunState.DEGRADED)
        out = []
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == tstack.id:
                continue
            run_comps = [c for c in ss.stack.components if ss.components[c.id].run_state in up]
            if not run_comps:
                continue
            if not any(d in member_ids for c in ss.stack.components for d in c.depends_on):
                continue
            if self.stack_bands(ss.stack.id) and not self._effective_band(ss.stack.id):
                out.append(ss.stack.id)
        return out

    def stop_dependents(self, target: str, bands=None) -> list[str]:
        """Running stacks that would be orphaned if `target` stops (they depend on one of
        its components) — e.g. stopping the daemon orphans kiss/igate/…

        When `bands` is given (the radio band(s) actually being stopped), a dependent is
        included ONLY if it is running on one of those bands: stopping the daemon's 433
        instance does NOT orphan an 868 dependent."""
        tstack = self.stack(target)
        if tstack is None:
            return []
        member_ids = {c.id for c in tstack.components}
        up = (RunState.RUNNING, RunState.DEGRADED)
        want = set(bands) if bands else None
        out = []
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == tstack.id:
                continue
            run_comps = [c for c in ss.stack.components
                         if ss.components[c.id].run_state in up]
            if not run_comps:
                continue
            if not any(d in member_ids for c in ss.stack.components for d in c.depends_on):
                continue
            if want is not None:
                dep_bands = self._running_bands_of(ss, run_comps)
                if dep_bands and not (dep_bands & want):
                    continue                               # different band -> not orphaned
            out.append(ss.stack.id)
        return out

    def _daemon_bands_to_release(self, stk, sid, active_bands) -> tuple[str, list]:
        """(daemon_stack_id, bands) a stopping CLIENT stack no longer needs — the band(s) it was
        ACTUALLY running on (`active_bands`, resolved from its running marker BEFORE deletion) that
        NO other running daemon-dependent stack still uses and where the daemon is still up. Empty
        for the daemon stack itself or a non-daemon-dependent stack. A band-switchable client (KISS/
        Voice) thus releases only the band it ran on, never both."""
        daemon_sid = next((s.id for s in self.stacks() if s.main == self.DAEMON_ID), None)
        if stk is None or not daemon_sid or sid == daemon_sid:
            return daemon_sid or "", []
        if not any(self.DAEMON_ID in (c.depends_on or ()) for c in stk.components):
            return daemon_sid, []
        release = []
        for b in sorted(bb for bb in active_bands if bb in ("433", "868")):
            others = [d for d in self.stop_dependents(daemon_sid, bands={b}) if d != sid]
            if not others and self.daemon_view(b).reachable:
                release.append(b)
        return daemon_sid, release

    def stop(self, target: str, apply: bool = False, cascade: bool = False,
             band: str = "", release_daemon: bool = True) -> ActionResult:
        """Public, LOCKED entry — acquires the lifecycle bundle (incl. dependents on
        cascade) so a DIRECT call gets the same coordination as CLI/web. `release_daemon=False`
        is the INTERNAL cascade path: a client stopped as part of a daemon cascade must not itself
        release the daemon (the outer daemon stop is the sole owner of daemon teardown)."""
        from . import reslock
        # A daemon stop is FORCED-cascade — resolve that BEFORE acquiring the lock bundle so the
        # dependents' lifecycle/resource locks are part of the outer guard (no dependent races the
        # cascade), and so a blocking dependent can gate the daemon stop.
        _sid0 = self.stack_of(target)
        _stk0 = self.stack(_sid0) if _sid0 else None
        if _stk0 is not None and _stk0.main == self.DAEMON_ID:
            cascade = True
        if not apply:
            return self._stop_impl(target, apply=False, cascade=cascade, band=band,
                                   release_daemon=release_daemon)
        try:
            with self._lifecycle_guard("stop", target, band, cascade=cascade):
                return self._stop_impl(target, apply=True, cascade=cascade, band=band,
                                       release_daemon=release_daemon)
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot stop '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])

    def _stop_impl(self, target: str, apply: bool = False, cascade: bool = False,
                   band: str = "", release_daemon: bool = True) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        # Stop in reverse start order.
        items = list(reversed(items))
        _sid = self.stack_of(target)
        _stk = self.stack(_sid) if _sid else None
        target_is_daemon = bool(_stk and _stk.main == self.DAEMON_ID)
        # The daemon is shared infrastructure: stopping it ALWAYS orphans its dependents, so the
        # cascade is forced (a client is never left pointing at a dead daemon).
        if target_is_daemon:
            cascade = True
        # Bands ACTUALLY being stopped — from the authoritative topology resolver (per-band daemon
        # stop includes its --radio both collateral; separate per-band instances are unaffected).
        _daemon_band_stop = bool(target_is_daemon and band in ("433", "868"))
        stopped_bands = self._operation_bands(target, band, "", "stop") if _daemon_band_stop else None
        other_bands = sorted((stopped_bands or set()) - {band}) if _daemon_band_stop else []
        # Fail closed: a PER-BAND daemon stop must not guess the band of a running band-switchable
        # dependent that has no trustworthy marker — block, name it, and stop NOTHING.
        if _daemon_band_stop:
            uncertain = self._uncertain_daemon_dependents(target)
            if uncertain:
                results = [CompResult(component=d, stack=d, action="stop", outcome=Outcome.BLOCKED,
                    summary="active radio band unknown (no running-band marker) — cannot safely "
                            "scope a per-band daemon stop; stop it explicitly first")
                    for d in uncertain]
                det = [f"  [blocked] dependent '{d}': active radio band unknown — daemon and all "
                       "dependents left untouched" for d in uncertain]
                return ActionResult(False, f"Per-band daemon stop for '{target}' ({band}) blocked — "
                                    f"dependent(s) with unknown active band: {', '.join(uncertain)}",
                                    details=det, results=tuple(results),
                                    next_commands=[f"lhpc status {target}"])
        # Orphaned dependents are scoped to the band(s) actually being stopped.
        dependents = self.stop_dependents(target, bands=stopped_bands)
        # systemd services lhpc doesn't own (e.g. meshtasticd) — stop them as root.
        sysd = [f"sudo systemctl stop {c.units[0].name}"
                for _, c in items if c.units and not c.run_argv]
        if not apply:
            details = [f"  [stop] {comp.id}" for _, comp in items]
            for cmd in sysd:
                details.append(f"    {cmd}")
            return ActionResult(True, f"Stop plan for '{target}': {len(items)} component(s).",
                                details=details,
                                next_commands=[f"lhpc stack stop {target} --yes"],
                                data={"changes": len(items), "dependents": dependents,
                                      "commands": sysd, "other_bands": other_bands})
        details = []
        results: list[CompResult] = []          # every result (dependents + own + daemon release)
        own_results: list[CompResult] = []       # ONLY the target's own components

        # Forced daemon cascade: stop dependents FIRST. An interactive/manual dependent (preflighted
        # before ANY automatic stop), or a dependent whose automatic stop fails / does not verify,
        # BLOCKS the daemon stop — the daemon is never stopped while a dependent is still running.
        dep_block = False
        if cascade:
            interactive_deps = [dep for dep in dependents
                                if (self.stack(dep) and self.stack(dep).main_component
                                    and self.stack(dep).main_component.interactive)]
            if interactive_deps:
                for dep in interactive_deps:
                    results.append(CompResult(component=dep, stack=dep, action="stop",
                        outcome=Outcome.MANUAL_REQUIRED,
                        summary="interactive dependent — stop it yourself before this stack"))
                    details.append(f"  [manual_required] dependent '{dep}' is interactive — "
                                   "stop it yourself first (daemon left running)")
                dep_block = True                 # preflight block: stop NO automatic dependent
            else:
                for dep in dependents:
                    # release_daemon=False: the OUTER daemon stop owns teardown — a dependent must
                    # not recursively stop the daemon (it just clears its own marker on cessation).
                    dep_res = self.stop(dep, apply=True, release_daemon=False)
                    results.append(CompResult(component=dep, stack=dep, action="stop",
                        outcome=Outcome.STOPPED if dep_res.ok else Outcome.UNVERIFIED,
                        summary=dep_res.summary))
                    details.append(f"  [{'stopped' if dep_res.ok else 'unverified'} dependent] {dep}")
                    if not dep_res.ok:
                        dep_block = True

        for _, comp in items:
            # Never stop the daemon while a dependent is still running / not verified stopped.
            if target_is_daemon and comp.id == self.DAEMON_ID and dep_block:
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.BLOCKED,
                    summary="not attempted — a dependent is still running or not verified stopped")
                results.append(cr); own_results.append(cr)
                details.append(f"  [blocked] {comp.id}: a dependent is still running — daemon left up")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised: LHPC cannot verify the stop -> MANUAL_REQUIRED.
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.MANUAL_REQUIRED,
                    summary=f"system service — stop as root: sudo systemctl stop {comp.units[0].name}")
                results.append(cr); own_results.append(cr)
                details.append(f"  [manual_required] {comp.id}: stop it as root: "
                               f"sudo systemctl stop {comp.units[0].name}")
                continue
            # The daemon is multi-instance (one process per band). A per-band stop
            # signals ONLY the owned instance(s) serving that band. Record-driven +
            # identity-verified inside Lifecycle.stop, which returns a typed result.
            if comp.id == self.DAEMON_ID and band in ("433", "868"):
                cr = life.stop(comp, band=band)
            else:
                cr = life.stop(comp)
            results.append(cr); own_results.append(cr)
            details.append(f"  [{cr.outcome.value}] {comp.id}: {cr.summary}"
                           + (f" (pid {cr.pid})" if cr.pid else ""))

        # The target's OWN cessation (independent of dependents / daemon-release outcome).
        own_ok = applied_ok(own_results) if own_results else True
        sid = self.stack_of(target)
        # Resolve the client's ACTUAL active band BEFORE clearing its running marker (topology
        # resolver: running/interactive marker, else declared band).
        active_bands = set()
        if own_ok and _stk is not None and not target_is_daemon:
            active_bands = self._operation_bands(target, "", "", "stop")
        # Clear band/interactive markers after the target's OWN verified cessation — even if the
        # later daemon-release fails, a stopped client must never look running.
        if sid and own_ok:
            self._safe_unlink(self._band_marker(sid))
            self.dismiss_interactive(sid)
        # A CLIENT stop also releases the daemon band it used — only where no other running
        # dependent needs it. Its typed result feeds the aggregate success (a failed release makes
        # the whole client stop non-success). The just-stopped client is excluded from that check,
        # so there is no recursive re-stop of it.
        if own_ok and not target_is_daemon and release_daemon:
            daemon_sid, release = self._daemon_bands_to_release(_stk, sid, active_bands)
            for b in release:
                dres = self.stop(daemon_sid, apply=True, band=b)
                results.append(CompResult(component=self.DAEMON_ID, stack=daemon_sid, action="stop",
                    outcome=Outcome.STOPPED if dres.ok else Outcome.UNVERIFIED,
                    summary=(f"released {daemon_sid} {b} (no other stack needs it)" if dres.ok
                             else f"{daemon_sid} {b} release NOT verified — {dres.summary}")))
                details.append(f"  [{'stopped' if dres.ok else 'unverified'} daemon] "
                               f"{daemon_sid} {b}: no other stack needs it")
        # ok only when every result (dependents + own + daemon release) is a verified stop.
        ok = applied_ok(results) if results else True
        summary = (f"Stop applied for '{target}'." if ok else
                   f"Stop for '{target}' is NOT fully verified — see details.")
        return ActionResult(ok, summary, details=details, results=tuple(results),
                            next_commands=[f"lhpc status {target}"])

    def restart(self, target: str, apply: bool = False, params: dict | None = None,
                stop_owners: bool = False, band: str = "",
                file_overrides: dict | None = None) -> ActionResult:
        """Public, LOCKED entry — holds ONE bundle across the internal stop+start so a
        DIRECT call gets the same coordination as CLI/web."""
        from . import reslock
        if not apply:
            return self._restart_impl(target, apply=False, params=params,
                                      stop_owners=stop_owners, band=band,
                                      file_overrides=file_overrides)
        # A non-daemon restart with NO explicit band restarts on the band it is ACTUALLY running on
        # (not the configured default) — resolve it BEFORE the guard so locking and the restart use
        # the same band (KISS/Voice on 868, restart no-band → lock 868 only, not 433).
        _rband = band
        _sid = self.stack_of(target)
        _stk = self.stack(_sid) if _sid else None
        if not band and _stk is not None and _stk.main != self.DAEMON_ID and self.stack_bands(_sid):
            _rband = self._effective_band(_sid, "") or band
        # Reject invalid / unqualified-duplicate params + file overrides BEFORE any lock (config-
        # independent validation) — so an unqualified duplicate name never acquires the config-
        # stability/lifecycle lock or stops the target. The preflight below re-validates (idempotent)
        # and adds identity enforcement, which needs the stable config read.
        params, _pv = self._normalize_run_params(target, params)
        if _pv:
            return ActionResult(False, f"Cannot restart '{target}': invalid parameter — {_pv}",
                                next_commands=[f"lhpc status {target}"])
        file_overrides, _fo = self._normalize_file_overrides(target, file_overrides)
        if _fo:
            return ActionResult(False, f"Cannot restart '{target}': invalid parameter — {_fo}",
                                next_commands=[f"lhpc status {target}"])
        # Hold saved configuration STABLE from PREFLIGHT through the whole stop→start transition, so
        # a valid target is never stopped and then rejected by a concurrently-mutated config (LOCK
        # ORDER: config guard BEFORE the lifecycle/resource lock; re-entrant with _restart_impl).
        try:
            with self._config_stable():
                # PREFLIGHT all start inputs BEFORE lock planning, the guard, owner handling or any
                # stop. A failed preflight is a typed failure that never acquires a lock or touches
                # lifecycle state. Canonical values feed lock/radio planning + _restart_impl.
                params, file_over, _pf_err = self._preflight_start_inputs(
                    target, _rband, params, file_overrides, "restart")
                if _pf_err is not None:
                    return _pf_err
                _order = self._run_order(target)
                _radio = ""
                if _order:
                    _r, _ = self._daemon_needs(_order, params, self._config_band(target, _rband))
                    _radio = _r or ""
                try:
                    with self._lifecycle_guard("restart", target, _rband,
                                               stop_owners=stop_owners, radio=_radio):
                        return self._restart_impl(target, apply=True, params=params,
                                                  stop_owners=stop_owners, band=_rband,
                                                  file_overrides=file_over)
                except SourceTxnBlocked as blocked:
                    return ActionResult(False, f"Cannot restart '{target}': {blocked}",
                                        next_commands=[f"lhpc status {target}"])
                except reslock.ResourceBusy as busy:
                    return ActionResult(False, f"Cannot restart '{target}': {busy}",
                                        next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot restart '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _restart_impl(self, target: str, apply: bool = False, params: dict | None = None,
                      stop_owners: bool = False, band: str = "",
                      file_overrides: dict | None = None) -> ActionResult:
        """Applied restarts run the WHOLE preflight→stop→start transition under the configuration-
        stability guard, so a concurrent save can never change the inputs mid-transition (and a valid
        target is never stopped only to be rejected by the later start). A direct/internal apply=True
        call cannot bypass it; dry-run holds no long-lived guard."""
        if not apply:
            return self._restart_impl_inner(target, apply=False, params=params,
                                             stop_owners=stop_owners, band=band,
                                             file_overrides=file_overrides)
        try:
            with self._config_stable():                          # re-entrant (no-op if restart() holds it)
                return self._restart_impl_inner(target, apply=True, params=params,
                                                stop_owners=stop_owners, band=band,
                                                file_overrides=file_overrides)
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot restart '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _restart_impl_inner(self, target: str, apply: bool = False, params: dict | None = None,
                            stop_owners: bool = False, band: str = "",
                            file_overrides: dict | None = None) -> ActionResult:
        """Stop then start a target — used to apply a config change to a running stack.
        With no band given, keep the band the stack is currently running on (so a
        restart doesn't move a band-switchable stack back to its default band)."""
        if not band:
            band = self._effective_band(self.stack_of(target) or target)
        if not apply:
            res = self.start(target, apply=False, params=params, band=band,
                             file_overrides=file_overrides)
            return ActionResult(res.ok, f"Restart plan for '{target}': stop then run.",
                                details=res.details, data=res.data,
                                next_commands=[f"lhpc stack restart {target} --yes"])
        # Defensive: an internal/direct apply=True call must validate BEFORE its stop() — never stop
        # a running target and only then discover the start inputs are invalid.
        params, file_overrides, _pf_err = self._preflight_start_inputs(
            target, band, params, file_overrides, "restart")
        if _pf_err is not None:
            return _pf_err
        stopped = self.stop(target, apply=True, band=band)
        if not stopped.ok:
            # Strict transition: never start after an unverified/failed stop. Preserve
            # the failed-stop typed results as the restart evidence.
            return ActionResult(False,
                                f"Restart aborted for '{target}': stop was not verified.",
                                details=list(stopped.details) + ["  [aborted] not starting "
                                "after an unverified stop — resolve the stop first"],
                                results=tuple(stopped.results),
                                next_commands=[f"lhpc status {target}"])
        time.sleep(1.0)  # let sockets/locks release before re-starting
        res = self.start(target, apply=True, params=params, stop_owners=stop_owners, band=band,
                         file_overrides=file_overrides)
        # Restart's typed results are the stop results followed by the start results.
        return ActionResult(res.ok, f"Restarted '{target}'. {res.summary}",
                            details=res.details,
                            results=tuple(stopped.results) + tuple(res.results),
                            next_commands=res.next_commands)

    def build(self, target: str, apply: bool = False) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        buildable = [(s, c) for s, c in items if c.build_steps]
        if not apply:
            details = [f"  [build] {c.id}: "
                       + " ; ".join(" ".join(str(t) for t in st.get("argv", []))
                                    for st in c.build_steps) for _, c in buildable]
            return ActionResult(True, f"Build plan for '{target}': {len(buildable)} component(s).",
                                details=details,
                                next_commands=[f"lhpc build {target} --yes"] if buildable else [],
                                data={"changes": len(buildable)})
        # P0.1: ONE atomic guard — index lock, recover, block on any unresolved journal,
        # then the source-path lock(s) (handoff) held for the whole build. No
        # preflight/acquire race: a journal that appears after a failed transaction is
        # caught under the index lock before the source locks are taken.
        from . import reslock
        src_paths = sorted({c.source.path for _, c in buildable if c.source})
        try:
            with self._source_operation_guard(src_paths, op="build"):
                details = []
                ok = True
                for _, comp in buildable:
                    res = life.build(comp)
                    ok = ok and res.ok
                    details.append(f"  [{res.state.value}] build {comp.id} "
                                   f"(rc {res.returncode}, log {res.log_path})")
                    if not res.ok:
                        details.extend(f"      {ln}" for ln in res.tail[-6:])
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Build blocked for '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Build blocked for '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])
        self.prune_logs()
        return ActionResult(ok, f"Build {'succeeded' if ok else 'FAILED'} for '{target}'.",
                            details=details, next_commands=["lhpc status " + target])

    def log_tail(self, target: str, lines: int = 300, job: str | None = None):
        """Raw (path, lines) for `target`'s log — for the live web log view.

        With `job` (a logs/<name>.log filename) it tails that specific job log
        (e.g. a build/test run); otherwise it tails the component's process log.
        """
        from .jobs import tail_log
        if job:
            # A web-supplied job selector may name ONLY an approved logs/<name>.log
            # file: a single path component, .log suffix, contained under logs/, and
            # never a symlink leaf. Anything else returns empty (no traversal/leak).
            try:
                name = validators.path_component(job, field="job log")
            except validators.ValidationError:
                return "", []
            if not name.endswith(".log"):
                return "", []
            try:
                p = self._paths.under("logs", name)
            except PathContainmentError:
                return "", []
            # Don't surface a symlinked/non-regular log path to the UI (the READ itself is
            # already O_NOFOLLOW-safe via runtime_fs.tail; this just refuses the display).
            if p.is_symlink() or not p.is_file():
                return "", []
            from . import runtime_fs
            return str(p), runtime_fs.tail(self._paths, p, lines)
        s = self.stack(target)
        if s is not None and s.main_component:
            comp = s.main_component
        else:
            idx = self._component_index()
            if target not in idx:
                return "", []
            comp = idx[target][1]
        return self._lifecycle().logs(comp, lines)

    def spawn_web_job(self, op: str, target: str, source: str = "pinned"):
        """Spawn detached build/test/install job(s) for `target`; return
        (job_log_name, error). The web redirects to a live view of the log."""
        life = self._lifecycle()
        # Install runs as one logged subprocess so the operator sees clone output
        # live and when it finishes (the dash redirects to this log).
        if op == "install":
            import sys
            # Reject an invalid source selector — never silently fall back to 'dev'.
            if source not in self.SOURCE_CHOICES:
                return None, (f"invalid source '{source}' (choose "
                              f"{', '.join(self.SOURCE_CHOICES)})")
            argv = [sys.executable, "-m", "lhpc", "install", target, "--yes",
                    "--source", source]
            ln, pid = life.spawn_job(f"install-{target}", argv, str(self._paths.runtime_root))
            if not ln or not pid:
                return None, f"could not start install for '{target}'"
            err = self._track_or_terminate(life, ln, pid, target, "install")
            if err:
                return None, err            # spawned-but-untracked: reported, not silent
            return ln, None
        items, err = self._resolve(target)
        if err:
            return None, err
        # Build a shell-free launcher per component (resolves pkg-config + env, runs
        # the structured steps). A stack may build several components.
        from . import commands, reslock, runtime_fs
        inst_index_key = self._installer()._index_key()
        runtime = str(self._paths.runtime_root)
        runtime_fs.ensure_dir(self._paths, self._paths.under("state", "locks"))
        jobs = []
        for _, c in items:
            if op == "build" and c.build_steps:
                jobs.append((c, list(c.build_steps), f"build-{c.id}"))
            elif op == "test" and c.test_argv:
                jobs.append((c, [{"argv": list(c.test_argv)}], f"test-{c.id}"))
        if not jobs:
            return None, f"nothing to {op} for '{target}'"
        s = self.stack(target)
        main_id = s.main if s else None
        first = main_log = None
        errors: list[str] = []
        post_dir = self._paths.under("state", "jobs")   # containment-checked
        runtime_fs.ensure_dir(self._paths, post_dir)
        import sys, os, time as _time
        for c, steps, name in jobs:
            src = str(life.source_dir(c))
            # The launcher holds the canonical SOURCE-PATH lock for its whole lifetime,
            # so an update/uninstall of the same checkout cannot race a running job.
            lock_paths = ([str(reslock.lock_file_path(self._paths,
                          reslock.source_lock_key(c.source.path)))] if c.source else [])
            # P0.3: index-to-source handoff — the launcher holds the source-transaction
            # INDEX lock and verifies NO unresolved journal before acquiring the source
            # lock(s), so a detached job cannot race past a retained journal.
            index_lock = str(reslock.lock_file_path(self._paths, inst_index_key))
            txn_dir = str(self._paths.under("state", "source-txn"))
            # Fail-closed: a missing/empty @file secret, bad env, or unresolved token
            # blocks the build cleanly (no silent empty value, no shell).
            try:
                script = commands.render_build_launcher(steps, runtime, src, lock_paths,
                                                        index_lock=index_lock, txn_dir=txn_dir)
            except commands.CommandError as exc:
                return None, f"cannot {op} '{c.id}': {exc}"
            # Unique runtime-owned launcher name so concurrent jobs never overwrite
            # each other's spec; written atomically THROUGH the safe runtime FS.
            uid = f"{name}-{os.getpid()}-{_time.monotonic_ns()}"
            launcher = runtime_fs.write_launcher(self._paths, post_dir / f"{uid}.py", script)
            ln, pid = life.spawn_job(name, [sys.executable, str(launcher)], src)
            if not ln or not pid:
                # Never silently continue when a component job cannot spawn.
                errors.append(f"could not start {op} for '{c.id}'")
                continue
            terr = self._track_or_terminate(life, ln, pid, c.id, op)
            if terr:
                errors.append(terr)
                continue
            if first is None:
                first = ln
            if c.id == main_id:
                main_log = ln
        self.prune_logs()
        if errors and (main_log or first) is None:
            return None, "; ".join(errors)             # nothing usable started
        if errors:
            return (main_log or first), "; ".join(errors)   # partial: surface the failures
        return (main_log or first), None

    # Bounded log retention (no background supervisor — runs at operation boundaries).
    LOG_RETENTION = 200          # keep at most this many *.log files
    LOG_RETENTION_BYTES = 64 * 1024 * 1024   # …and at most this many bytes total

    def prune_logs(self) -> int:
        """Delete the oldest runtime logs beyond a bounded count/byte budget, NEVER
        touching a log that belongs to an active job (so live evidence is preserved)
        and never following a symlink. Returns the number removed. Called at operation
        boundaries; there is no background cleaner."""
        from .paths import PathContainmentError
        d = self._paths.under("logs")
        protected = {j.get("log") for j in self.active_jobs() if j.get("log")}
        protected = {f"{n}.log" for n in protected} | {n for n in protected}
        # Descriptor-safe enumeration (no `is_dir()`/`glob`/`is_symlink`): a symlinked/
        # escaping logs dir fails closed; a symlinked log leaf is skipped (never followed,
        # never pruned so an outside target can't be deleted).
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except PathContainmentError:
            return 0
        logs = []
        for name, is_link in entries:
            if is_link or not name.endswith(".log"):
                continue
            f = d / name
            try:
                mtime = os.stat(f, follow_symlinks=False).st_mtime
            except OSError:
                continue
            logs.append((mtime, name, f))
        logs.sort(reverse=True)                                    # newest first
        removed, kept, total = 0, 0, 0
        for _mtime, name, f in logs:
            if name in protected:
                continue
            try:
                size = os.stat(f, follow_symlinks=False).st_size
            except OSError:
                continue
            kept += 1
            total += size
            if kept > self.LOG_RETENTION or total > self.LOG_RETENTION_BYTES:
                runtime_fs.unlink(self._paths, f)      # descriptor-safe, refuses a symlink
                removed += 1
        return removed

    def _jobs_dir(self):
        return self._paths.runtime_root / "state" / "jobs"

    def _write_job_marker(self, log_name: str, pid: int, target: str, op: str,
                          ident: dict | None = None) -> bool:
        """Record a build/test job with a COMPLETE, PID-reuse-resistant identity. Returns
        True only when a complete identity was captured AND the marker was durably
        persisted; False means the just-spawned process is UNTRACKED and the caller must
        terminate it (no silent orphan). `ident`, when given, is the identity captured
        immediately after spawn — used as-is (never re-read a possibly-reused pid)."""
        try:
            slug = validators.path_component(log_name, field="job log")
            path = self._paths.under("state", "jobs", slug + ".job")
        except (validators.ValidationError, PathContainmentError):
            return False
        if ident is None:
            ident = procident.proc_identity(pid) or {}
        # Refuse an incomplete identity via the ONE shared predicate — a marker is never
        # written with sentinel (-1)/blank fields; the caller then terminates the spawn.
        if not (isinstance(pid, int) and pid > 0 and procident.identity_complete(ident)):
            return False
        body = (f'launch_id = "{slug}"\npid = {pid}\n'
                f'starttime = {int(ident["starttime"])}\n'
                f'pgid = {int(ident["pgid"])}\nsid = {int(ident["sid"])}\n'
                f'exec = "{ident["exec"]}"\nargv_fp = "{ident["argv_fp"]}"\n'
                f'argv_len = {int(ident["argv_len"])}\n'
                f'target = "{target}"\nop = "{op}"\nlog = "{slug}"\n')
        try:
            runtime_fs.write_marker(self._paths, path, body)
            return True
        except (OSError, PathContainmentError, validators.ValidationError):
            return False

    def _track_or_terminate(self, life, log_name: str, pid: int, cid: str, op: str) -> str:
        """Persist a job marker; if it cannot be persisted, terminate the (identity-
        verified) spawned session so it never leaks as an untracked orphan. Returns ""
        on success, else a visible error describing the outcome."""
        # Capture the identity IMMEDIATELY after spawn and use exactly that for both the
        # marker and any cleanup — never re-read a possibly-reused pid as the original job.
        ident = procident.proc_identity(pid)
        if self._write_job_marker(log_name, pid, cid, op, ident=ident):
            return ""
        killed = life._terminate_unobserved(pid, ident)
        if killed:
            return (f"{op} '{cid}' spawned but its job marker could not be persisted; "
                    "the process was terminated (not left orphaned).")
        return (f"{op} '{cid}' spawned but its job marker could not be persisted AND the "
                "process could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")

    def active_jobs(self) -> list[dict]:
        """Build/test jobs whose ORIGINAL process is still alive (identity-verified).
        A reused PID, a malformed marker, or a symlinked marker is never treated as a
        live job; proven-finished markers are cleaned through the safe API."""
        import tomllib
        from .paths import PathContainmentError
        d = self._jobs_dir()
        # Descriptor-safe enumeration (no `is_dir()`/`glob`): a symlinked/escaping jobs dir
        # fails closed (no trusted jobs); a symlinked marker LEAF is diagnostic evidence,
        # never treated as a live job.
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except PathContainmentError:
            return []
        out = []
        for name, is_link in sorted(entries):
            if is_link or not name.endswith(".job"):
                continue
            f = d / name
            try:
                # No-follow read at the OPEN (no check-then-open): a swapped/symlinked marker
                # raises OSError here and is skipped, never followed.
                raw = tomllib.loads(runtime_fs.read_text(self._paths, f))
                pid = int(raw["pid"])
            except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
                continue                        # malformed -> retain for diagnosis
            if procident.identity_matches(raw, pid):
                out.append({"log": raw.get("log"), "target": raw.get("target"),
                            "op": raw.get("op"), "stack": self.stack_of(raw.get("target", ""))})
            else:
                # Finished/reused -> clear the marker. If it RACED into a symlink between
                # the no-follow read and now, the safe unlink refuses it: retain it as
                # evidence rather than letting this public read path throw.
                try:
                    runtime_fs.unlink(self._paths, f)
                except PathContainmentError:
                    pass
        return out

    def logs(self, target: str, lines: int = 200) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        details = []
        for _, comp in items:
            path, tail = life.logs(comp, lines)
            if not path:
                details.append(f"[{comp.id}] no log found")
                continue
            details.append(f"[{comp.id}] {path} (last {len(tail)} lines):")
            details.extend(f"  {ln}" for ln in tail)
        return ActionResult(True, f"Logs for '{target}' (bounded tail).", details=details,
                            next_commands=[f"lhpc status {target}"])

    def test(self, target: str, tx: bool = False,
             apply: bool = False) -> ActionResult:
        """Run host tests (RX-safe) or a bounded one-frame TX test (`tx=True`)."""
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        if not tx:
            # RX-safe host tests.
            if not apply:
                details = [f"  [host-test] {c.id}: "
                           + (" ".join(str(t) for t in c.test_argv) or "(no host test)")
                           for _, c in items]
                return ActionResult(True, f"Host-test plan for '{target}' (TX-safe).",
                                    details=details,
                                    next_commands=[f"lhpc test {target} --yes"],
                                    data={"changes": sum(1 for _, c in items if c.test_argv)})
            # P0.1: ONE atomic guard (index→recover→block→source-lock handoff) held for
            # the whole host-test run — a host test depends on the source's build
            # artifacts, so a concurrent update can't swap the tree mid-test, and a
            # retained journal blocks it with no preflight race.
            from . import reslock
            details = []
            ok = True
            src_paths = sorted({c.source.path for _, c in items if c.source and c.test_argv})
            try:
                with self._source_operation_guard(src_paths, op="host-test"):
                    for _, comp in items:
                        res = life.host_test(comp)
                        if res is None:
                            details.append(f"  [skip] {comp.id}: no host test")
                            continue
                        ok = ok and res.ok
                        details.append(f"  [{res.state.value}] {comp.id} "
                                       f"(rc {res.returncode}, log {res.log_path})")
            except SourceTxnBlocked as blocked:
                return ActionResult(False, f"Host test blocked for '{target}': {blocked}",
                                    next_commands=[f"lhpc status {target}"])
            except reslock.ResourceBusy as busy:
                return ActionResult(False, f"Host test blocked for '{target}': {busy}",
                                    next_commands=[f"lhpc status {target}"])
            return ActionResult(ok, f"Host test {'passed' if ok else 'FAILED'} for '{target}'.",
                                details=details, next_commands=[f"lhpc status {target}"])

        # TX-capable test — explicit, gated, bounded. TX is a DAEMON operation; the
        # band(s) come from the target's components (or, for the daemon itself, the
        # bands it is serving), and we only test bands the daemon actually serves.
        wanted = []
        for _, c in items:
            for b in ([c.band] if c.band else []) + list(c.bands):
                if b and b not in wanted:
                    wanted.append(b)
        if not wanted:
            wanted = list(self.RADIO_BANDS)
        # A TX test drives real RF, so it requires the radio to be READY (not merely a
        # reachable CONF socket): a FAILED/UNINITIALIZED radio must never be TX-tested.
        bands = [b for b in wanted if self.daemon_view(b).ready]
        if not bands:
            return ActionResult(
                False, f"Cannot TX-test '{target}': the daemon isn't serving a READY radio on "
                f"{' or '.join(wanted)} MHz — start the daemon and wait for RADIO=READY first.",
                next_commands=[f"lhpc status {target}"])
        op = self.config().operator
        payload = f"LHPC TX TEST{(' DE ' + op.callsign) if op.configured else ''}"
        if not apply:
            details = ["TX-CAPABLE TEST — this transmits real RF.",
                       "Ensure the antennas are on the connected DUMMY LOADS.", ""]
            for band in bands:
                plan = life.plan_tx_test(target, band, payload)
                details += [f"  band      : {plan.band} MHz",
                            f"  parameters: {plan.parameters}",
                            f"  payload   : {plan.payload!r}",
                            f"  expected  : {plan.expected}", ""]
            return ActionResult(True, f"TX test plan for '{target}' (ONE frame per band).",
                                details=details,
                                next_commands=[f"lhpc test {target} --tx --yes"],
                                data={"changes": len(bands)})
        details = []
        ok = True
        for band in bands:
            res = life.run_daemon_tx_test(band, payload)
            ok = ok and res.ok
            details.append(f"  [{'ok' if res.ok else 'fail'}] band {res.band}: {res.detail} "
                           f"(TXOK {res.txok_before}->{res.txok_after})")
        return ActionResult(ok, f"TX test {'PASSED' if ok else 'did not confirm'} for '{target}'.",
                            details=details, next_commands=[f"lhpc status {target}", f"lhpc logs {target}"])

    # ---- unified action dispatch (used by the web control interface) -----

    # Web-exposed actions -> the same gated service methods the CLI calls.
    WEB_ACTIONS = ("install", "update", "uninstall", "start", "stop", "restart",
                   "build", "test", "test-tx")

    def run_params_for(self, target: str):
        """Run parameters offered for `target` (from its main/own components)."""
        s = self.stack(target)
        comps = s.components if s else (
            [self._component_index()[target][1]] if target in self._component_index() else [])
        params = []
        for c in comps:
            params.extend(c.run_params)
        return params

    def _safe_unlink(self, path) -> bool:
        """Best-effort delete of a runtime-owned marker/leaf (contained, no symlink-
        follow). NON-THROWING: `Paths.safe_unlink` can raise ordinary FS errors
        (PermissionError, etc.) as well as a containment error — stale-marker cleanup runs
        AFTER lifecycle work, so it must never convert a completed start into an unhandled
        exception. Returns False if the leaf could not be removed."""
        try:
            self._paths.safe_unlink(path)
            return True
        except (OSError, PathContainmentError):
            return False

    def _safe_marker_write(self, path, text: str) -> bool:
        """Write a small runtime-owned marker atomically THROUGH the safe runtime FS
        (containment + no-follow leaf + fsync). Returns False if it could NOT be persisted
        (a symlink-leaf/escaping path or an I/O error) so a caller whose operational truth
        depends on the marker can reflect the failure in its typed result."""
        from . import runtime_fs
        from .paths import PathContainmentError as _PCE
        try:
            runtime_fs.write_marker(self._paths, path, text)
            return True
        except (OSError, _PCE):
            return False

    def _interactive_marker(self, stack_id: str):
        sid = validators.path_component(stack_id, field="stack id")
        return self._paths.under("state", "interactive", f"{sid}.show")

    def mark_interactive(self, stack_id: str, band: str = "") -> bool:
        """Remember that the operator asked to run an interactive app (so the dash
        shows its terminal-command block); stores the chosen band. Returns False if the
        marker could not be persisted."""
        return self._safe_marker_write(self._interactive_marker(stack_id), band)

    def interactive_band(self, stack_id: str) -> str | None:
        """The band an interactive app was started on, or None if not active."""
        from . import runtime_fs
        try:
            return runtime_fs.read_text(self._paths, self._interactive_marker(stack_id)).strip()
        except (OSError, ValueError):       # missing/unreadable/symlinked -> not active
            return None

    def dismiss_interactive(self, stack_id: str) -> None:
        self._safe_unlink(self._interactive_marker(stack_id))

    def clear_stale_interactive(self, keep: str = "") -> list[str]:
        """Drop interactive markers for apps that aren't actually running — they
        were launched/marked but never run (or since stopped). Called when another
        stack starts so the dash doesn't keep showing a prior interactive app's
        command block. `keep` is the stack just started (its marker is preserved)."""
        idir = self._paths.runtime_root / "state" / "interactive"
        if not idir.exists():
            return []
        up = (RunState.RUNNING, RunState.DEGRADED)
        live = {cid: st.run_state for ss in self.build_snapshot().stacks
                for cid, st in ss.components.items()}
        cleared = []
        for f in idir.glob("*.show"):
            sid = f.stem
            if sid == keep:
                continue
            s = self.stack(sid)
            main = s.main_component if s else None
            if not (main and live.get(main.id) in up):
                self.dismiss_interactive(sid)
                cleared.append(sid)
        return cleared

    def _band_marker(self, stack_id: str):
        sid = validators.path_component(stack_id, field="stack id")
        return self._paths.under("state", "running", f"{sid}.band")

    def _set_running_band(self, stack_id: str, band: str) -> bool:
        """Persist the running band (drives multi-band decisions + dashboard). Returns
        False if it could not be written."""
        return self._safe_marker_write(self._band_marker(stack_id), band)

    def running_band(self, stack_id: str, default: str = "") -> str:
        from . import runtime_fs
        try:
            return runtime_fs.read_text(self._paths, self._band_marker(stack_id)).strip() or default
        except (OSError, ValueError):
            return default

    def running_lora_stacks(self, band: str) -> list:
        """Daemon-client (LoRa) stacks currently running on `band` — the ones a live switch to
        MODE=FSK would break. Used to warn on the daemon's live-setting confirm."""
        from . import daemon_params
        out = []
        for sid in daemon_params.CLIENT_STACKS:
            if self.stack_running(sid) and \
                    self._effective_daemon_band(sid, self.running_band(sid)) == band:
                out.append(sid)
        return out

    def stack_bands(self, target: str) -> tuple:
        """Bands the operator may choose for `target` (empty = single fixed band). Owner-stack
        scoped, so a direct component target uses its stack's band choices."""
        s = self._owner_stack(target)
        for c in (s.components if s else ()):
            if c.bands:
                return c.bands
        return ()

    def _config_band(self, target: str, band: str) -> str:
        """The band key used for per-band config storage ("" for single-band stacks).
        With no explicit band, defaults to the stack's declared primary band (the
        band-component's `band`, e.g. 868 for meshtastic) rather than the first
        allowed band."""
        allowed = self.stack_bands(target)
        if not allowed:
            return ""
        if band in allowed:
            return band
        s = self._owner_stack(target)
        for c in (s.components if s else ()):
            if c.bands and c.band in allowed:
                return c.band
        return allowed[0]

    # -- component-scoped persisted keys (collision-free, inside the owner stack config) ---------
    # A run/file value can be stored either FLAT (legacy `<name>` / `file_<name>`) or COMPONENT-
    # SCOPED (`__r__<comp>__<name>` / `__f__<comp>__<name>`). A direct component save writes SCOPED
    # keys; a stack save keeps FLAT keys. On read, a scoped value wins; else a flat legacy value is
    # honoured ONLY when its param name is UNIQUE for that kind across the owner stack (otherwise it
    # is ambiguous and never silently applied — the start fails typed, see `_config_ambiguity`).

    @staticmethod
    def _scoped_key(kind: str, comp_id: str, name: str) -> str:
        return f"__{kind}__{comp_id}__{name}"

    def _owner_param_counts(self, owner) -> tuple[dict, dict]:
        """(run_counts, file_counts): how many owner-stack components declare each run/file param
        name — a name with count >= 2 is AMBIGUOUS for a flat legacy value."""
        run_c: dict = {}
        file_c: dict = {}
        for c in (owner.components if owner is not None else ()):
            for p in c.run_params:
                run_c[p.name] = run_c.get(p.name, 0) + 1
            for p in (c.config_file.params if c.config_file else ()):
                file_c[p.name] = file_c.get(p.name, 0) + 1
        return run_c, file_c

    def _resolve_stored(self, stored: dict, kind: str, comp_id: str, name: str,
                        count: int) -> tuple:
        """(value_or_None, ambiguous). A component-SCOPED key wins; else a FLAT legacy key ONLY when
        the name is UNIQUE (count <= 1). `ambiguous` is True when a flat legacy value exists but the
        name is declared by >= 2 components and no scoped value overrides it — a value that must NOT
        be silently applied."""
        sk = self._scoped_key(kind, comp_id, name)
        if sk in stored:
            return stored[sk], False
        flat = name if kind == "r" else f"file_{name}"
        if flat in stored:
            if count <= 1:
                return stored[flat], False               # unique legacy -> backward compatible
            return None, True                            # ambiguous flat -> never silently applied
        return None, False

    # -- component identity through the parameter pipeline (form/API/overrides/save) -------------
    # Every editable value is (component_id, kind, name). A name UNIQUE within the target's own
    # components keeps its bare representation (`name`, `p_<name>`/`pf_<name>`) — backward compatible;
    # a DUPLICATED name is component-qualified: API/CLI key `component_id.name`, web field
    # `p_<component_id>__<name>` / `pf_<component_id>__<name>`. This keeps colliding components'
    # values distinct end to end instead of flattening them into one `{name: value}` map.

    def _dup_names(self, target: str) -> tuple[set, set]:
        """(dup_run, dup_file): run/file parameter names declared by MORE THAN ONE component of the
        TARGET's own scope (a direct component target has one component, so never any duplicates)."""
        rc: dict = {}
        fc: dict = {}
        for c in self._target_components(target):
            for p in c.run_params:
                rc[p.name] = rc.get(p.name, 0) + 1
            for p in (c.config_file.params if c.config_file else ()):
                fc[p.name] = fc.get(p.name, 0) + 1
        return {n for n, k in rc.items() if k > 1}, {n for n, k in fc.items() if k > 1}

    def _param_key(self, target: str, kind: str, comp_id: str, name: str) -> str:
        """The API/CLI key for one (component, kind, name): bare `name` when unique, else the
        component-qualified `component_id.name`."""
        dup_run, dup_file = self._dup_names(target)
        dup = dup_run if kind == "run" else dup_file
        return f"{comp_id}.{name}" if name in dup else name

    def _param_field(self, target: str, kind: str, comp_id: str, name: str) -> str:
        """The Start-confirm form field for one (component, kind, name): `p_<name>`/`pf_<name>` when
        unique, else `p_<component_id>__<name>` / `pf_<component_id>__<name>`."""
        prefix = "p_" if kind == "run" else "pf_"
        dup_run, dup_file = self._dup_names(target)
        dup = dup_run if kind == "run" else dup_file
        return f"{prefix}{comp_id}__{name}" if name in dup else f"{prefix}{name}"

    def _config_field(self, target: str, kind: str, comp_id: str, name: str) -> str:
        """The permanent Config-page form field for one (component, kind, name): `c_<name>`/`f_<name>`
        when unique, else `c_<component_id>__<name>` / `f_<component_id>__<name>` (same qualification
        rule + canonical API key as Start-confirm, just the Config page's `c_`/`f_` prefixes)."""
        prefix = "c_" if kind == "run" else "f_"
        dup_run, dup_file = self._dup_names(target)
        dup = dup_run if kind == "run" else dup_file
        return f"{prefix}{comp_id}__{name}" if name in dup else f"{prefix}{name}"

    def config_param_fields(self, target: str, band: str = "") -> list[dict]:
        """Config-page editable run + file parameters as {component, name, kind ('run'|'file'), field
        (`c_`/`f_` scheme), key (canonical API key), flag, default} — the single source of truth for
        the Config POST parser to fold each submitted field into its canonical API key. [] for a
        daemon target (its radio params are ephemeral start options, not persisted config)."""
        if self._is_daemon_target(target):
            return []
        out: list[dict] = []
        for c in self._target_components(target):
            for kind, p in ([("run", p) for p in c.run_params]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                out.append({
                    "component": c.id, "name": p.name, "kind": kind, "flag": p.kind == "flag",
                    "field": self._config_field(target, kind, c.id, p.name),
                    "key": self._param_key(target, kind, c.id, p.name),
                    "default": p.default})
        return out

    def _param_ref(self, target: str, kind: str, key: str):
        """Resolve an API/CLI override key to (component, param, err). A `component_id.name` key is
        component-qualified; a bare key is the NAME and must be UNIQUE within the target's scope — a
        duplicated bare name is a TYPED error (the caller must qualify it). `err` is None on success."""
        comps = self._target_components(target)

        def _pget(c, nm):
            ps = (c.run_params if kind == "run"
                  else (c.config_file.params if c.config_file else ()))
            return next((p for p in ps if p.name == nm), None)

        if "." in key:
            cid, nm = key.split(".", 1)
            c = next((c for c in comps if c.id == cid), None)
            p = _pget(c, nm) if c is not None else None
            if p is None:
                return None, None, f"unknown {kind} parameter {key!r}"
            return c, p, None
        owners = [(c, _pget(c, key)) for c in comps if _pget(c, key) is not None]
        if not owners:
            return None, None, f"unknown {kind} parameter {key!r}"
        if len(owners) == 1:
            return owners[0][0], owners[0][1], None
        return None, None, (f"{kind} parameter {key!r} is declared by multiple components — qualify "
                            f"it as '<component>.{key}'")

    def _overrides_for_comp(self, target: str, kind: str, overrides, comp_id: str) -> dict:
        """The subset of ephemeral overrides (API-key form) that target `comp_id`, as {name: value}
        — so a qualified duplicate value overlays ONLY its named component and never a sibling."""
        out: dict = {}
        for k, v in (overrides or {}).items():
            if v is None:
                continue
            c, p, err = self._param_ref(target, kind, k)
            if err is None and c.id == comp_id:
                out[p.name] = v
        return out

    def _resolved_param_value(self, target: str, kind: str, comp_id: str, name: str,
                              band: str = "") -> str:
        """The component's OWN persisted value (component-scoped, else a UNIQUE flat legacy) or its
        operator-substituted default — never masked by a same-named sibling."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        rc, fc = self._owner_param_counts(owner)
        k = "r" if kind == "run" else "f"
        count = (rc if kind == "run" else fc).get(name, 0)
        val, _amb = self._resolve_stored(stored, k, comp_id, name, count)
        if val is not None:
            return str(val)
        _c, p, _e = self._param_ref(target, kind, f"{comp_id}.{name}")
        bd = dict(p.band_defaults).get(cfg_band or band, p.default) if p is not None else ""
        return self._op_subst(bd)

    def _config_ambiguity(self, target: str, order, band: str = ""):
        """A message naming the first AMBIGUOUS legacy value a started component would rely on — a
        run/file param name declared by >= 2 owner-stack components, stored as a flat legacy value,
        with no component-scoped value for that component. None when every started component resolves
        unambiguously. Used to fail a start TYPED (never silently apply a value to the wrong
        component). `order` is the resolved [(stack, comp), …] launch order."""
        owner = self._owner_stack(target)
        if owner is None:
            return None
        stored = load_stack_config(self._paths, self._owner_stack_id(target),
                                   self._config_band(target, band))
        run_counts, file_counts = self._owner_param_counts(owner)
        owner_comp_ids = {c.id for c in owner.components}
        for _stack, comp in order:
            if comp.id not in owner_comp_ids:
                continue                                     # a dependency from another stack
            for p in comp.run_params:
                _v, amb = self._resolve_stored(stored, "r", comp.id, p.name,
                                               run_counts.get(p.name, 0))
                if amb:
                    return (f"run parameter '{p.name}' is ambiguous — declared by more than one "
                            f"component and stored only as a flat legacy value; set a "
                            f"component-scoped value for '{comp.id}'")
            for p in (comp.config_file.params if comp.config_file else ()):
                _v, amb = self._resolve_stored(stored, "f", comp.id, p.name,
                                               file_counts.get(p.name, 0))
                if amb:
                    return (f"file parameter '{p.name}' is ambiguous — declared by more than one "
                            f"component and stored only as a flat legacy value; set a "
                            f"component-scoped value for '{comp.id}'")
        return None

    def stack_config(self, target: str, band: str = "") -> dict:
        """Effective run config for `target` (and band): the component-scoped saved value, else a
        UNIQUE flat legacy value, else the per-band/manifest default (operator `{callsign}`/
        `{locator}` tokens substituted in DEFAULTS only — a saved value is used verbatim). An
        ambiguous flat legacy value is NOT applied here (the start blocks; see `_config_ambiguity`)."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        run_counts, _ = self._owner_param_counts(owner)
        op = self.config().operator

        def _op_subst(v: str) -> str:
            return (str(v).replace("{callsign}", op.callsign or "")
                          .replace("{locator}", op.locator or ""))

        out = {}
        for c in self._target_components(target):
            for p in c.run_params:
                val, _amb = self._resolve_stored(stored, "r", c.id, p.name,
                                                 run_counts.get(p.name, 0))
                if val is not None:
                    out[p.name] = str(val)                    # saved value (scoped/unique), verbatim
                else:
                    bd = dict(p.band_defaults).get(cfg_band or band, p.default)
                    out[p.name] = _op_subst(bd)               # default with operator tokens
        return out

    def missing_system_deps(self, target: str) -> list[dict]:
        """Unsatisfied system dependencies (e.g. -dev packages) for a stack's
        components, with the command to install each. Empty = all satisfied."""
        return [d for d in self.system_deps(target) if not d["satisfied"]]

    def system_deps(self, target: str) -> list[dict]:
        """ALL declared system requirements for a stack (dev packages, headers,
        device nodes) with their satisfied state + install command — for the app
        tab ('Installed' vs a copyable install command) and the install gate."""
        life = self._lifecycle()
        s = self.stack(target)
        out, seen = [], set()
        for c in (s.components if s else ()):
            missing = life.missing_requirements(c)
            for req in c.requires:
                key = req.install or req.cmd or req.check_file
                if key and key not in seen:
                    seen.add(key)
                    out.append({"what": req.note or req.cmd or req.check_file,
                                "install": req.install,
                                "satisfied": req not in missing})
        return out

    def is_installed(self, target: str) -> bool:
        """Whether a stack's main source is present (nothing to install if it
        declares no source)."""
        s = self.stack(target)
        main = s.main_component if s else None
        if not main or not main.source:
            return True
        return self._paths.resolve_source(main.source.path).is_dir()

    def unbuilt_components(self, target: str) -> list[str]:
        """Component ids in `target` whose source is installed but whose compiled
        binary is missing (need a Build before they can run). Empty = all ready."""
        life = self._lifecycle()
        s = self.stack(target)
        return [c.id for c in (s.components if s else ())
                if c.source and life.source_dir(c).exists() and not self.is_built(c)]

    def update_status(self, comp) -> str:
        """Is the installed source up to date with its GitHub remote branch?
        Returns "up-to-date", "update-available", or "unknown" (no remote/git/net).
        Uses a bounded `git ls-remote` — no fetch, no mutation."""
        if comp is None or comp.source is None or not comp.source.remote:
            return "unknown"
        src = self._paths.resolve_source(comp.source.path)
        if not src.is_dir():
            return "unknown"
        remote = self.config().remotes.get(comp.id) or comp.source.remote
        # Revalidate the (possibly hand-edited) remote IMMEDIATELY before git — an invalid
        # runtime override must never reach `git ls-remote`; treat it as unknown, not a check.
        from . import validators
        try:
            remote = validators.remote_url(remote or "", field="remote")
        except validators.ValidationError:
            return "unknown"
        if not remote:
            return "unknown"
        ref = comp.source.branch or "HEAD"
        run = self._system.runner.run
        rem = run(["git", "ls-remote", remote, ref], timeout=12.0)
        if rem.returncode != 0 or not rem.stdout.strip():
            return "unknown"
        remote_sha = rem.stdout.split()[0]
        loc = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
        if loc.returncode != 0 or not loc.stdout.strip():
            return "unknown"
        return "up-to-date" if loc.stdout.strip() == remote_sha else "update-available"

    def stack_running(self, target: str) -> bool:
        """True if the stack's main component is currently running/degraded."""
        s = self.stack(target)
        main = s.main_component if s else None
        if not main:
            return False
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == s.id:
                st = ss.components.get(main.id)
                return bool(st and st.run_state in (RunState.RUNNING, RunState.DEGRADED))
        return False

    RADIO_BANDS = ("433", "868")

    def dash_signature(self) -> str:
        """A compact signature of the dashboard's STRUCTURAL state — which
        components are running, which bands the daemon serves, and which
        interactive apps are marked. The web polls this cheaply and only does a
        full reload when it changes, so live-monitor fields (RSSI/feed/…) update in
        place without the whole page reflowing. Excludes fast-changing telemetry."""
        snap = self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        running = sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                         if st.run_state in up)
        # D: = bands with a USABLE radio (RADIO=READY), NOT merely reachable — a FAILED or
        # UNINITIALIZED daemon must never appear "served".
        usable = [b for b in self.RADIO_BANDS if self.daemon_view(b).ready]
        from . import runtime_fs
        from .paths import PathContainmentError
        idir = self._paths.runtime_root / "state" / "interactive"
        marks = []
        # Descriptor-safe enumeration (no Path.exists()/glob): a symlinked/escaping marker
        # dir or a symlinked marker leaf is skipped, never followed.
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, idir)
        except PathContainmentError:
            entries = []
        for name, is_link in sorted(entries):
            if is_link or not name.endswith(".show"):
                continue
            stem = name[:-len(".show")]
            try:
                marks.append(f"{stem}={runtime_fs.read_text(self._paths, idir / name).strip()}")
            except (OSError, ValueError):
                marks.append(stem)
        return "R:" + ",".join(running) + ";D:" + ",".join(usable) + ";I:" + ",".join(marks)

    def _build_artifact(self, comp):
        """Relative path of the built binary (explicit `bin`, else the process
        exec_name), or None when the component compiles nothing."""
        if not comp.build_cmd:
            return None
        rel = comp.bin or (comp.process.exec_name if comp.process else "")
        return rel or None

    def is_built(self, comp) -> bool:
        """True if the component needs no build, or its built artifact is present.
        The artifact path may carry run-param placeholders (e.g. {env} for the
        firmware build dir), substituted from the stack's saved config."""
        rel = self._build_artifact(comp)
        if rel is None:
            return True
        if "{" in rel:
            cfg = self.stack_config(self.stack_of(comp.id) or "")
            for p in comp.run_params:
                rel = rel.replace("{" + p.name + "}", cfg.get(p.name, p.default))
        return (self._lifecycle().source_dir(comp) / rel).exists()

    def install_blocker(self, comp) -> str:
        """Why a component can't be launched yet ("" = ready): its source isn't
        installed, or it compiles to a binary that isn't built. Avoids handing the
        operator (or the spawner) a command that points at a missing binary."""
        life = self._lifecycle()
        sid = self.stack_of(comp.id) or comp.id
        if comp.source and not life.source_dir(comp).exists():
            return f"not installed — run: lhpc install {sid}"
        if not self.is_built(comp):
            return f"not built — run: lhpc build {sid}"
        return ""

    def manual_start_command(self, comp) -> str:
        """The command the operator runs in a terminal to start an interactive
        component (the controller never starts these itself). Rendered from the
        SAME structured command spec, shell-quoted — values are individual argv
        tokens, never interpolated into shell syntax."""
        import shlex
        from . import commands
        if not comp.run_argv:
            return "(no run command)"
        op = self.config().operator
        runtime = str(self._paths.runtime_root)
        src = str(self._paths.resolve_source(comp.source.path)) if comp.source else runtime
        cmd = commands.display_command(comp, op, runtime, src)
        if not cmd:
            return "(no run command)"
        cwd = (commands._paths_subst(comp.run_cwd, runtime, src, "")
               if comp.run_cwd else src)
        return f"cd {shlex.quote(cwd)} && {cmd}"

    @staticmethod
    def _client_interfaces(status) -> list[dict]:
        """User-facing interfaces a client connects to (KISS TCP, web UIs, serial
        PTYs) that are currently present — NOT internal transport sockets."""
        if status is None:
            return []
        out = []
        for obs in status.endpoints:
            sp = obs.spec
            if not getattr(sp, "client", False) or not obs.present:
                continue
            link = f"{sp.scheme}://{sp.address}" if sp.scheme in ("http", "https") else ""
            out.append({"label": sp.description or sp.address, "address": sp.address,
                        "scheme": sp.scheme or "tcp", "link": link})
        return out

    def _component_booting(self, comp_id: str) -> bool:
        """True while a detached post-start runner (e.g. MeshCom's `--setcall` retry) is STILL
        applying settings to a just-launched component. Surfaced as a transient 'booting' state
        (yellow, no client link yet) until the callsign lands and the runner finishes — then the
        component reads 'running' (green) with its web-UI link."""
        life = self._lifecycle()
        for rec in life.owned_records(comp_id, role="post"):
            if not life._original_ceased(rec):        # the runner process is still alive
                return True
        return False

    def radio_overview(self) -> list[dict]:
        """Per-band view for the radio dashboard: daemon/radio config (if running),
        which stack (+ its components) is up on that band, and which stacks can be
        started on it. Live RSSI/CAD/feed are polled separately via /api/daemon."""
        snap = self.build_snapshot()
        live = {cid: st for ss in snap.stacks for cid, st in ss.components.items()}
        up = (RunState.RUNNING, RunState.DEGRADED)
        dstat = live.get(self.DAEMON_ID)
        daemon_proc = dstat.run_state.value if dstat else "unknown"
        daemon_up = bool(dstat and dstat.run_state in up)
        daemon_installed = bool(dstat and dstat.source_state.value
                                in ("match", "dirty", "differs", "unknown", "not-a-repo"))
        dvs = {b: self.daemon_view(b) for b in self.RADIO_BANDS}
        # OCCUPIED = reachable (may physically hold SPI, used for conflict reasoning);
        # USABLE = RADIO=READY (a working radio service). User-facing "served" summaries are
        # USABLE — a FAILED/UNINITIALIZED band is never presented as served.
        occupied_bands = [b for b, v in dvs.items() if v.reachable]
        usable_bands = [b for b, v in dvs.items() if v.ready]
        out = []
        for band in self.RADIO_BANDS:
            dv = dvs[band]
            other_served = [b for b in usable_bands if b != band]
            running, startable, interactive = [], [], []
            for s in self.stacks():
                if s.id == self.DAEMON_ID:
                    continue
                # Bands this stack can run on (multi-band stacks list several).
                sbands = set()
                for c in s.components:
                    sbands |= set(c.bands) if c.bands else ({c.band} if c.band else set())
                if band not in sbands:
                    continue
                multi = bool(self.stack_bands(s.id))
                running_up = any(c.id in live and live[c.id].run_state in up
                                 for c in s.components if c.band or c.bands)
                comps = []
                for c in s.components:
                    # A running component whose post-start runner is still applying settings reads
                    # 'booting' (no client link yet) until the callsign lands (e.g. MeshCom).
                    booting = (c.id in live and live[c.id].run_state in up
                               and self._component_booting(c.id))
                    comps.append({"id": c.id, "name": c.name, "optional": c.optional,
                          "runnable": c.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE),
                          "interactive": c.interactive,
                          # Interactive components (GUI/CLI/REPL) are run by the
                          # operator, never by lhpc — show the command to copy.
                          "command": self.manual_start_command(c) if c.interactive else "",
                          "blocker": self.install_blocker(c) if c.interactive else "",
                          # Has tunables -> a config link; lhpc captures a start log
                          # (non-interactive run) or it declares its own -> a log link.
                          "configurable": bool(c.run_params or c.config_file),
                          "writes_log": bool(c.log_paths) or bool(c.run_argv and not c.interactive),
                          "state": ("booting" if booting
                                    else (live[c.id].run_state.value if c.id in live else "unknown")),
                          # No web-UI/client link while booting — it isn't serving yet.
                          "interfaces": ([] if booting else self._client_interfaces(live.get(c.id)))})
                entry = {"id": s.id, "name": s.name, "main": s.main, "components": comps,
                         "multi_band": multi}
                main_comp = s.component(s.main)

                # Interactive stacks (chat/voice): in the dropdown until the operator
                # "runs" them; after that a dismissable command block is shown in the
                # band column they were started on.
                if main_comp is not None and main_comp.interactive:
                    mark_band = self.interactive_band(s.id)        # None if not active
                    active = mark_band is not None or running_up
                    if not active:
                        startable.append(entry)                    # in the dropdown
                        continue
                    col = mark_band or (self.running_band(s.id, sorted(sbands)[0])
                                        if running_up else sorted(sbands)[0])
                    if col != band:
                        continue                                   # block lives in its own column
                    entry["running"] = running_up
                    entry["command"] = self.manual_start_command(main_comp)
                    entry["blocker"] = self.install_blocker(main_comp)
                    interactive.append(entry)
                    continue

                # A running multi-band stack belongs to the column of the band it
                # was actually started on (tracked at start time).
                if multi and running_up:
                    if self.running_band(s.id, sorted(sbands)[0]) != band:
                        continue
                    is_up = True
                elif multi:
                    is_up = False     # not running -> startable on every allowed band
                else:
                    is_up = any(c.id in live and live[c.id].run_state in up
                                for c in s.components if c.band == band)
                (running if is_up else startable).append(entry)
            out.append({
                "band": band,
                "daemon": {
                    "reachable": dv.reachable,     # CONF socket answered (daemon live)
                    # OCCUPIED: reachable — the daemon may physically hold the radio/SPI even
                    # if RADIO != READY (used for resource-conflict reasoning).
                    "occupied": dv.reachable,
                    "ready": dv.ready,             # ...AND RADIO=READY (serves a usable band)
                    # USABLE: only a READY radio is a usable service for dependents/TX.
                    "usable": dv.ready,
                    "radio_state": dv.radio_state or None,   # READY/FAILED/UNINITIALIZED
                    # A truthful one-word state for the UI: offline / occupied / usable.
                    "state_label": ("usable" if dv.ready else
                                    "occupied" if dv.reachable else "offline"),
                    "process": daemon_proc,        # process run-state (band-independent)
                    "process_up": daemon_up,
                    "installed": daemon_installed,
                    "other_served": other_served,  # other USABLE band(s) (RADIO=READY)
                    "served": usable_bands,        # usable bands (READY) — never FAILED/UNINIT
                    "occupied_bands": occupied_bands,   # reachable (may hold SPI) — conflicts
                    "usable_bands": usable_bands,       # RADIO=READY — dependent-start/TX
                    "radio": dv.status.get("RADIO") if dv.reachable else None,
                    "txmode": dv.status.get("TXMODE") if dv.reachable else None,
                    "cadrssi": dv.status.get("CADRSSI") if dv.reachable else None,
                    "cadwait": dv.status.get("CADWAIT") if dv.reachable else None,
                    "liverssi": dv.channel.get("LIVERSSI") if dv.reachable else None,
                },
                "running": running,
                "startable": startable,
                "interactive": interactive,
                # Two+ stacks sharing one radio (e.g. a manually-started chat plus a
                # running iGate) fight over the daemon's tuning — flag it red.
                "conflict": ([s["name"] for s in running]
                             + [s["name"] for s in interactive if s.get("running")])
                            if len(running) + sum(1 for s in interactive if s.get("running")) > 1
                            else [],
            })
        return out

    def log_running(self, target: str, job: str | None = None) -> bool:
        """Whether the process behind a log is still alive: for a build/test `job`
        it checks the job marker's pid; for a process log it checks the target's
        main component run-state."""
        if job:
            import tomllib
            f = self._jobs_dir() / (job + ".job")
            try:
                # No-follow read (no check-then-open): a symlinked marker -> OSError -> False.
                raw = tomllib.loads(runtime_fs.read_text(self._paths, f))
                # FULL identity match (PID-reuse-safe), never bare PID liveness: a recycled
                # pid running an unrelated process is not this job.
                return procident.identity_matches(raw, int(raw["pid"]))
            except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
                return False
        s = self.stack(target)
        cid = s.main_component.id if (s and s.main_component) else target
        for ss in self.build_snapshot().stacks:
            st = ss.components.get(cid)
            if st is not None:
                return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
        return False

    def config_view(self, target: str, band: str = "") -> dict:
        """Structured config for the Config page: operator identity (always shown
        on top) plus per-component run parameters and their effective values. For
        band-switchable stacks `band` selects which band's config is shown."""
        s = self.stack(target)
        members = s.components if s else ()
        comps = [c for c in members if c.run_params]
        # The daemon's run params (radio/debug/tx-mode/CAD/RSSI) are START options,
        # always chosen on confirm:start — not persistent config. Keep the daemon
        # Config page to its live tuning + sources only.
        if s is not None and s.main == self.DAEMON_ID:
            comps = []
        cfg = self.config()
        stored = load_stack_config(self._paths, target)
        live = {cid: st.run_state for ss in self.build_snapshot().stacks
                for cid, st in ss.components.items()}
        up = (RunState.RUNNING, RunState.DEGRADED)
        # Libraries/firmware are build/flash artifacts — never "started", so they
        # get no autostart toggle or Run button.
        optional = [{"id": c.id, "name": c.name, "purpose": c.purpose,
                     "autostart": stored.get(f"autostart_{c.id}") == "on",
                     "running": live.get(c.id) in up}
                    for c in members if c.optional
                    and c.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE)]
        main = s.main_component if s else None
        # Operator identity is only relevant to stacks that actually substitute
        # {callsign}/{locator} into a run/pre command (e.g. iGate) — not the daemon.
        # Stacks that edit their callsign in their own config (operator_box=false) don't show the
        # shared Operator box — the callsign lives in their config/run params instead.
        uses_operator = (s is not None and s.operator_box) and any(
            tok in (c.run_cmd or "") or tok in (c.pre_cmd or "")
            or any(tok in (p.default or "") for p in c.run_params)
            or any(tok in (p.default or "") for p in (c.config_file.params if c.config_file else ()))
            for c in members for tok in ("{callsign}", "{locator}"))
        sources = [{"id": c.id, "name": c.name,
                    "remote": cfg.remotes.get(c.id) or c.source.remote,
                    "default": c.source.remote,
                    "overridden": c.id in cfg.remotes}
                   for c in members if c.source and c.source.remote]
        bands = self.stack_bands(target)
        cfg_band = self._config_band(target, band)
        view = {
            "operator": ({"callsign": cfg.operator.callsign, "locator": cfg.operator.locator}
                         if uses_operator else None),
            # Each component carries its OWN field-name map (`fields`) and its OWN
            # component-scoped value map (`values`) so duplicate run/file names across components
            # never share a form field nor flatten into one value.
            "components": [{"id": c.id, "name": c.name, "params": list(c.run_params),
                            "fields": {p.name: self._config_field(target, "run", c.id, p.name)
                                       for p in c.run_params},
                            "values": {p.name: self._resolved_param_value(target, "run", c.id,
                                                                          p.name, cfg_band)
                                       for p in c.run_params}}
                           for c in comps],
            "optional": optional,
            "sources": sources,
            # File params GROUPED by component (like run params) — each with its own fields/values.
            "file_components": [{"id": c.id, "name": c.name,
                                 "params": [p for p in c.config_file.params if not p.hidden],
                                 "fields": {p.name: self._config_field(target, "file", c.id, p.name)
                                            for p in c.config_file.params},
                                 "values": {p.name: self._resolved_param_value(target, "file", c.id,
                                                                               p.name, cfg_band)
                                            for p in c.config_file.params}}
                                for c in members if c.config_file
                                and any(not p.hidden for p in c.config_file.params)],
            "file_params": [p for c in members if c.config_file
                            for p in c.config_file.params if not p.hidden],
            "file_values": self.file_config_values(target, cfg_band),
            "bands": list(bands),       # allowed bands ([] = single-band stack)
            "band": cfg_band,           # the band currently being edited
            "values": self.stack_config(target, cfg_band),
            "running": bool(main and live.get(main.id) in up),
            "radios": [],
        }
        # The daemon's config page carries its LIVE (runtime) settings for ONE band,
        # chosen by a 433/868 switch at the top. The repo/RadioLib remotes + save/
        # restore below apply to both bands.
        if s is not None and s.main == self.DAEMON_ID:
            live_band = band if band in self.RADIO_BANDS else self.RADIO_BANDS[0]
            view["bands"] = list(self.RADIO_BANDS)
            view["band"] = live_band
            view["live_band"] = True       # band switch selects the LIVE band, not config
            dv = self.daemon_view(live_band)
            # Populate each control with the daemon's REAL current value: STATUS + CHANNEL are
            # what it actually reports; the configured daemon-param value is the fallback for
            # radio params the daemon does not echo (FREQ/SF/BW/…).
            actual = {**dv.channel, **dv.status}
            cfg = self._daemon_param_applies("daemon", live_band) if dv.reachable else {}
            view["radios"] = [{"band": live_band, "reachable": dv.reachable,
                               "error": dv.error, "status": actual, "config": cfg}]
            # Order the live per-parameter controls the same as the daemon-params panel
            # below (shared keys in that order; any daemon-only extras after).
            from . import daemon_params
            allowed = daemon_control.allowed_settings()
            order = ([k for k in daemon_params.ALL_PARAMS if k in allowed]
                     + [k for k in allowed if k not in daemon_params.ALL_PARAMS])
            view["live_settings"] = {k: allowed[k] for k in order}
        return view

    def save_config(self, target: str, values: dict,
                    callsign: str | None = None, locator: str | None = None,
                    band: str = "") -> ActionResult:
        """Save operator identity (if supplied) and the stack's run parameters as ONE
        all-or-recoverable transaction (via `save_config_bundle`): if the stack config fails to
        validate/persist, operator identity is NOT partially written."""
        return self.save_config_bundle(target, values=values, callsign=callsign,
                                       locator=locator, band=band)

    def save_config_bundle(self, target: str, *, values: dict | None = None,
                           callsign: str | None = None, locator: str | None = None,
                           band: str = "", remotes: dict | None = None) -> ActionResult:
        """Validate the WHOLE Config-page submission, then persist it as ONE
        all-or-recoverable transaction (local.toml + the per-stack config file).
        Nothing is written unless every value validates; unknown fields are
        rejected; a malformed local.toml is preserved. (P0: replaces the previous
        per-remote sequential writes.)"""
        owner = self._owner_stack(target)
        if owner is None:
            return self._unknown_stack(target)
        # A direct COMPONENT target persists into its OWNER stack config, but may edit ONLY its own
        # run/file fields — never sibling components' fields, remotes, or autostart (those are
        # whole-stack concerns, allowed only for a stack target).
        is_stack_target = self.stack(target) is not None
        sid = owner.id
        errors: list[str] = []
        optional_ids = ({c.id for c in owner.components if c.optional} if is_stack_target else set())
        # Each submitted value carries component identity: a run value key is the API key
        # (`name`/`component.name`); a file value key is `file_<apikey>`. `_param_ref` resolves it to
        # (component, param) and REJECTS an unqualified duplicate — so colliding names never flatten.
        clean_params: list = []      # (kind 'r'|'f', component, param, value)
        clean_auto: dict = {}        # autostart_<id> -> "on"/""  (stack target only, flat)
        for key, value in (values or {}).items():
            if key.startswith("autostart_"):
                if key[len("autostart_"):] in optional_ids:
                    clean_auto[key] = "on" if str(value) in ("on", "1", "true", "yes") else ""
                else:
                    errors.append(f"unknown config field: {key!r}")
                continue
            if key.startswith("file_"):
                c, p, err = self._param_ref(target, "file", key[len("file_"):])
                if err:
                    errors.append(f"unknown config field: {key!r}" if err.startswith("unknown")
                                  else err)
                    continue
                try:
                    clean_params.append(("f", c, p, validators.validate_param(p, value)))
                except validators.ValidationError as exc:
                    errors.append(str(exc))
                continue
            c, p, err = self._param_ref(target, "run", key)
            if err:
                errors.append(f"unknown config field: {key!r}" if err.startswith("unknown") else err)
                continue
            v = str(value)
            if p.kind == "int" and v.strip() == "":
                clean_params.append(("r", c, p, v))
                continue
            try:
                clean_params.append(("r", c, p, validators.validate_param(p, v)))
            except validators.ValidationError as exc:
                errors.append(str(exc))
        op_change = callsign is not None or locator is not None
        cs = loc = None
        if op_change:
            try:
                cs = validators.callsign(callsign or "", field="callsign").upper()
                loc = validators.locator(locator or "", field="locator")
            except validators.ValidationError as exc:
                errors.append(str(exc))
        # A remote submission is a PATCH for THIS stack's own source components only (enforced in
        # the service, not the web form): validated non-blank -> set, blank -> clear that
        # component's override. A component id not declared by `target` (unknown, another stack's,
        # or one without a source remote) is REJECTED. Other stacks' overrides are untouched.
        stack_remote_cids = ({c.id for c in owner.components if c.source and c.source.remote}
                             if is_stack_target else set())
        remote_patch: dict = {}
        if remotes is not None:
            for cid, url in remotes.items():
                try:
                    vid = validators.path_component(cid, field="component id")
                except validators.ValidationError as exc:
                    errors.append(str(exc))
                    continue
                if vid not in stack_remote_cids:
                    errors.append(f"remote override not allowed for {vid!r} — not a source "
                                  f"component of '{target}'")
                    continue
                try:
                    remote_patch[vid] = validators.remote_url(url or "", field="remote")   # "" clears
                except validators.ValidationError as exc:
                    errors.append(str(exc))
        if errors:                                  # reject the whole bundle — zero mutation
            return ActionResult(False, f"Config not saved for '{target}'.", details=errors)

        targets: list = []
        local_path = self._paths.runtime_root / "config" / "local.toml"
        if op_change or remotes is not None:
            def _render_local(p, opc=op_change, _cs=cs, _loc=loc, patch=remote_patch,
                              do_remotes=(remotes is not None)):
                # Read the LATEST local.toml INSIDE the transaction lock and MERGE — preserving
                # every unrelated table and every other component's remote override. A malformed
                # local.toml raises here -> the transaction rolls back and preserves it.
                existing = _load_runtime_toml(self._paths, local_path)   # no-follow; ConfigError on corrupt
                data = dict(existing)                     # keep root scalars + every other table
                if opc:
                    # Patch ONLY callsign/locator — preserve any other [operator] scalar keys.
                    _patch_local_table(data, "operator", {"callsign": _cs, "locator": _loc})
                if do_remotes:
                    # Patch owned component keys only (None clears); other remotes preserved. A
                    # non-table `operator`/`remotes` value is rejected here -> transaction rollback.
                    _patch_local_table(data, "remotes",
                                       {vid: (vurl or None) for vid, vurl in patch.items()})
                return render_local_tables(data)          # type-safe, preserves root scalars
            targets.append(("local", local_path, _render_local, 0o600))
        cfg_band = self._config_band(target, band)
        # Apply-mode hints (restart/build) from CHANGED run params, compared per-component against the
        # PRE-SAVE effective value (never masked by a same-named sibling), BEFORE the write.
        modes = {p.apply_mode for kind, c, p, v in clean_params
                 if kind == "r"
                 and self._resolved_param_value(target, "run", c.id, p.name, band) != str(v)}
        # Persisted keys: a DIRECT component target, or a DUPLICATED stack-target name, is written
        # COMPONENT-SCOPED (`__r__/__f__<comp>__<name>`) so colliding components never share a key; a
        # UNIQUE name on a stack target keeps its FLAT legacy key (backward compatible). Autostart
        # (stack-only) stays flat.
        dup_run, dup_file = self._dup_names(target)
        clean: dict = dict(clean_auto)
        for kind, c, p, v in clean_params:
            dup = (dup_run if kind == "r" else dup_file)
            if is_stack_target and p.name not in dup:
                clean[p.name if kind == "r" else f"file_{p.name}"] = v      # unique -> flat legacy
            else:
                clean[self._scoped_key(kind, c.id, p.name)] = v             # scoped
        # The stack file is written as a MERGE rendered INSIDE the transaction lock: read the
        # latest config, overlay the submitted keys, keep daemon-profile dp_* + unrelated
        # manual scalars. A raise here (unsupported manual value) rolls the whole transaction back.
        targets.append(("stack", _stack_config_path(self._paths, sid, cfg_band),
                        lambda p, tgt=sid, b=cfg_band, cl=clean: render_stack_config(
                            tgt, merge_stack_values(p, tgt, b, cl, clear_empty=False)), 0o644))
        try:
            apply_config_transaction(self._paths, targets)
        except ConfigError as exc:
            return ActionResult(False, f"Config not saved for '{target}'.", details=[str(exc)])
        self._invalidate_config()               # saved operator/remotes visible immediately
        return ActionResult(True, f"Config saved for '{target}'.",
                            details=self._apply_hints(target, modes),
                            next_commands=[f"lhpc stack start {target}"])

    def save_stack_config(self, target: str, values: dict, band: str = "") -> ActionResult:
        """Validate and persist a stack/band's run + file configuration via the CANONICAL bundle
        path (`save_config_bundle`). `values` keys are the same canonical API keys the Config/Start
        pages use: `name`/`file_<name>` when unique, `component.name`/`file_<component.name>` when the
        name is duplicated across the stack. Unknown fields and unqualified duplicate names are typed
        failures with NO mutation; unique names keep their flat-key/field compatibility; daemon-profile
        `dp_*`, autostart, remotes and the transactional semantics are preserved."""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        return self.save_config_bundle(target, values=values, band=band)

    def _apply_hints(self, target: str, modes: set) -> list[str]:
        """Human guidance on how a saved config change takes effect."""
        running = self.stack_running(target)
        hints = []
        if "build" in modes:
            hints.append("Compile-time change — Rebuild (Build) the stack to apply.")
        if "restart" in modes:
            hints.append("Restart the stack to apply." if running
                         else "Start-time change — applies on the next Run.")
        if "live" in modes:
            hints.append("Runtime change — applied live.")
        return hints

    def save_component_remote(self, component_id: str, url: str) -> ActionResult:
        """Override (or clear, if url is blank) a component's GitHub remote. The URL
        is validated to a safe remote policy before any file change."""
        try:
            save_component_remote(self._paths, component_id, url)
        except validators.ValidationError as exc:
            return ActionResult(False, "Remote override rejected.", details=[str(exc)])
        except ConfigError as exc:
            return ActionResult(False, "Remote override not saved.",
                                details=[f"local.toml is malformed and was preserved: {exc}"])
        self._invalidate_config()               # new remote visible to the next read
        return ActionResult(True, "Remote override saved." if url.strip()
                            else "Remote override cleared.")

    # ---- file-based component config (writes the app's own config file) -----

    def _file_config_components(self, target: str):
        # Owner-stack scoped for a stack target; JUST the targeted component for a direct component.
        return [c for c in self._target_components(target) if c.config_file]

    def file_params_for(self, target: str):
        """Every file-config FileParam of a stack (for the web to collect `pf_<name>` inputs)."""
        return [p for c in self._file_config_components(target) for p in c.config_file.params]

    def file_config_values(self, target: str, band: str = "") -> dict:
        """Stored file-config values (component-scoped `__f__<comp>__<name>`, else a UNIQUE flat
        legacy `file_<name>`), falling back to the per-band default, then the FileParam default. An
        ambiguous flat legacy value is NOT applied here (the start blocks; see `_config_ambiguity`)."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        _, file_counts = self._owner_param_counts(owner)
        out = {}
        for c in self._file_config_components(target):
            for p in c.config_file.params:
                bd = dict(p.band_defaults).get(cfg_band or band, p.default)
                val, _amb = self._resolve_stored(stored, "f", c.id, p.name,
                                                 file_counts.get(p.name, 0))
                out[p.name] = str(val) if val is not None else bd
        return out

    # -- Start-confirm "Stack parameters" panel + CALL/node enforcement ----------

    # A run/file param whose validator marks it the stack's operator identity: a "callsign"
    # validator => LICENSED (refuse empty / N0CALL); a "node" validator => UNLICENSED (refuse only
    # empty, the default name is accepted).
    _IDENTITY_ENFORCE = {"callsign": "licensed", "node": "unlicensed"}

    def _op_subst(self, text: str) -> str:
        op = self.config().operator
        return (str(text).replace("{callsign}", op.callsign or "")
                         .replace("{locator}", op.locator or ""))

    def _identity_field(self, target: str) -> dict | None:
        """The operator-identity field CALL/node enforcement guards, or None. Scoped to the target:
        a STACK target inspects every component; a direct COMPONENT target inspects ONLY that
        component. The daemon is exempt. The FIRST run/file param with a callsign/node validator
        wins; a callsign (licensed) is preferred over a node (unlicensed) if both are declared."""
        if self._is_daemon_target(target):
            return None
        found = None
        for c in self._target_components(target):
            candidates = [("run", p) for p in c.run_params]
            candidates += [("file", p) for p in (c.config_file.params if c.config_file else ())]
            for kind, p in candidates:
                enforce = self._IDENTITY_ENFORCE.get(getattr(p, "validator", ""))
                if not enforce:
                    continue
                rec = {"comp": c.id, "name": p.name, "kind": kind, "enforce": enforce,
                       "field": self._param_field(target, kind, c.id, p.name)}
                if enforce == "licensed":
                    return rec                       # licensed wins immediately
                found = found or rec                 # remember an unlicensed node field
        return found

    def _identity_value(self, target: str, band: str, params, file_over) -> str:
        """The value of the SELECTED identity field, read from ITS OWN component (never masked by a
        same-named sibling): the ephemeral override for that component's key, else its own
        component-scoped/unique-flat stored value, else its operator-substituted default."""
        idf = self._identity_field(target)
        if not idf:
            return ""
        kind = "run" if idf["kind"] == "run" else "file"
        comp_id, name = idf["comp"], idf["name"]
        key = self._param_key(target, kind, comp_id, name)   # bare or component-qualified
        val = ((params if kind == "run" else file_over) or {}).get(key)
        if val is None:
            val = self._resolved_param_value(target, kind, comp_id, name, band)
        return str(self._op_subst(val or "")).strip()

    def enforce_identity(self, target: str, band: str = "", params: dict | None = None,
                         file_over: dict | None = None) -> tuple[bool, str, str]:
        """Whether `target` may start given its CALL/node rule. Returns (ok, field, message).
        Licensed stacks refuse an empty or `N0CALL` callsign; unlicensed stacks refuse only an
        empty node name (the default name is accepted). `field` is the confirm input to highlight."""
        idf = self._identity_field(target)
        if not idf:
            return (True, "", "")
        val = self._identity_value(target, band, params, file_over)
        if idf["enforce"] == "licensed":
            # Refuse empty and the reserved placeholder N0CALL, including any N0CALL-<SSID>.
            if not val or val.split("-", 1)[0].upper() == "N0CALL":
                return (False, idf["field"], f"A valid callsign is required to start '{target}' "
                        f"(licensed) — set '{idf['name']}' to your callsign (not empty or N0CALL).")
        elif not val:
            return (False, idf["field"], f"A node name is required to start '{target}' — "
                    f"set '{idf['name']}'.")
        return (True, idf["field"], "")

    def _stack_param_components(self, target: str):
        """Components whose run/file params make up the editable 'Stack parameters' set (never the
        daemon — its radio params are the separate daemon panel). Target-scoped: a stack target
        exposes all components, a direct component target only itself."""
        if self._is_daemon_target(target):
            return []
        return self._target_components(target)

    def stack_start_params(self, target: str, band: str = "", params: dict | None = None,
                           file_over: dict | None = None) -> list[dict]:
        """Rows for the Start-confirm 'Stack parameters' panel: every editable run + file param of
        the stack (never repo source / operator box / autostart), prefilled with the value that WILL
        be used for this start (ephemeral override, else saved config, else operator-substituted
        default). `field` is the confirm input name (`p_`/`pf_`); `default` is the manifest default
        (for the client Reset-to-defaults button)."""
        idf = self._identity_field(target)
        cfg_band = self._config_band(target, band)
        rows: list[dict] = []
        for c in self._stack_param_components(target):
            for kind, p in ([("run", p) for p in c.run_params]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                # Each (component, kind, name) carries its OWN field + API key (bare when unique,
                # component-qualified when the name collides) and its OWN saved value — colliding
                # components never share a field name or flatten into one value.
                default = self._op_subst(dict(p.band_defaults).get(cfg_band or band, p.default))
                saved = self._resolved_param_value(target, kind, c.id, p.name, band)
                key = self._param_key(target, kind, c.id, p.name)
                field = self._param_field(target, kind, c.id, p.name)
                cur = ((params if kind == "run" else file_over) or {}).get(key, saved)
                is_id = bool(idf and idf["comp"] == c.id and idf["name"] == p.name
                             and idf["kind"] == kind)
                rows.append(self._param_row(p, field, kind, cur, saved, default, is_id,
                                            c.name, key, c.id))
        return rows

    def start_param_fields(self, target: str, band: str = "") -> list[dict]:
        """Every editable run + file parameter as {component, name, kind ('run'|'file'), field, key,
        flag, saved} — covering BOTH the savable panel params and plain start-option params (e.g. the
        daemon's radio/debug). The single source of truth for the web to read each submitted form
        field into its correctly-scoped API key (bare when unique, `component.name` when duplicated)."""
        out: list[dict] = []
        for c in self._target_components(target):
            for kind, p in ([("run", p) for p in c.run_params]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())]):
                out.append({
                    "component": c.id, "name": p.name, "kind": kind, "flag": p.kind == "flag",
                    "field": self._param_field(target, kind, c.id, p.name),
                    "key": self._param_key(target, kind, c.id, p.name),
                    "saved": self._resolved_param_value(target, kind, c.id, p.name, band)})
        return out

    def stack_start_param_groups(self, target: str, band: str = "", params: dict | None = None,
                                 file_over: dict | None = None) -> list[dict]:
        """The Start-confirm panel rows GROUPED for display: a first 'Required' group with the
        identity (CALL/node) field(s) on top, then one group per component (header = component name)
        with that component's remaining params. [] when the stack has no editable params."""
        rows = self.stack_start_params(target, band, params, file_over)
        groups: list[dict] = []
        required = [r for r in rows if r["is_identity"]]
        if required:
            groups.append({"header": "Required", "rows": required})
        by_comp: dict[str, list] = {}
        order: list[str] = []
        for r in rows:
            if r["is_identity"]:
                continue
            if r["comp_name"] not in by_comp:
                by_comp[r["comp_name"]] = []
                order.append(r["comp_name"])
            by_comp[r["comp_name"]].append(r)
        for name in order:
            groups.append({"header": name, "rows": by_comp[name]})
        return groups

    @staticmethod
    def _param_row(p, field: str, kind: str, value, saved: str, default: str, is_identity: bool,
                   comp_name: str = "", key: str = "", component: str = "") -> dict:
        return {"field": field, "name": p.name, "key": key, "component": component,
                "kind": p.kind, "comp_name": comp_name,
                "choices": list(p.choices), "label": p.label or p.name,
                "value": "" if value is None else str(value),
                "config_value": "" if saved is None else str(saved), "default": default,
                "advanced": bool(getattr(p, "advanced", False)),
                "validator": getattr(p, "validator", ""),
                "is_identity": bool(is_identity),
                "min": getattr(p, "min", None), "max": getattr(p, "max", None)}

    def _normalize_file_overrides(self, target: str, raw: dict | None) -> tuple[dict, str]:
        """Validate ephemeral file-config overrides ({name: value}) against the TARGET's FileParams
        (a stack's whole set, or a direct component's own). Returns (clean, err); a non-mapping
        payload, an UNKNOWN name, or an invalid value is a TYPED start failure — never silently
        discarded. A blank string is passed through (defaults apply downstream only when ABSENT)."""
        if raw is None:
            return {}, ""
        if not isinstance(raw, dict):
            return {}, "file overrides must be a mapping"
        clean: dict[str, str] = {}
        for key, val in raw.items():
            _c, p, err = self._param_ref(target, "file", key)      # rejects unqualified duplicates
            if err:
                return {}, f"unknown file parameter {key!r}" if err.startswith("unknown") else err
            try:
                clean[key] = validators.validate_param(p, str(val))
            except validators.ValidationError as exc:
                return {}, f"{p.name}: {exc}"
        return clean, ""

    def _normalize_run_params(self, target: str, raw) -> tuple[dict | None, str]:
        """THE authoritative validation/canonicalisation boundary for ordinary ephemeral run params
        — parallel to `_normalize_ephemeral_overrides` (daemon) and `_normalize_file_overrides`
        (file). Validates each supplied value against the TARGET's own run params (a stack's whole
        exposed set, or a direct component's own) and returns (clean, err). `None` passes through
        (means: use saved config). A non-mapping payload, an UNKNOWN parameter name, or an invalid
        value is a TYPED failure — a requested override is NEVER silently ignored."""
        if raw is None:
            return None, ""
        if not isinstance(raw, dict):
            return {}, "run parameters must be a mapping"
        clean: dict[str, str] = {}
        for key, val in raw.items():
            _c, p, err = self._param_ref(target, "run", key)       # rejects unqualified duplicates
            if err:
                return {}, f"unknown parameter {key!r}" if err.startswith("unknown") else err
            try:
                clean[key] = validators.validate_param(p, val)
            except validators.ValidationError as exc:
                return {}, f"{p.name}: {exc}"
        return clean, ""

    def _preflight_start_inputs(self, target: str, band: str, params, file_overrides, op: str):
        """Validate ALL supplied start inputs — ordinary `params`, file overrides, and CALL/node
        identity (using the canonicalized ephemeral values + owner-stack persisted values) — BEFORE
        any lifecycle side effect. Returns (params_canon, file_canon, err) where `err` is a TYPED
        failed ActionResult (or None on success). Used by `restart()` before lock planning/stop and
        by `_restart_impl()` before its `stop()`; a non-mapping/unknown/invalid input NEVER raises,
        acquires a lock, or stops/alters any process/marker/config/daemon/owner."""
        params, pv_err = self._normalize_run_params(target, params)
        if pv_err:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': invalid parameter — {pv_err}",
                next_commands=[f"lhpc status {target}"])
        file_over, fo_err = self._normalize_file_overrides(target, file_overrides)
        if fo_err:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': invalid parameter — {fo_err}",
                next_commands=[f"lhpc status {target}"])
        id_ok, id_field, id_msg = self.enforce_identity(target, band, params, file_over)
        if not id_ok:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': {id_msg}", data={"enforce_field": id_field},
                next_commands=[f"lhpc config {target}"])
        return params, file_over, None

    def write_config_files(self, target: str, band: str = "",
                           overrides: dict | None = None) -> list["ConfigWrite"]:
        """(Re)generate every file-config component's config file from the stored
        (per-band) values. Returns a STRUCTURED result per component (written /
        linked-readonly / no-base / failed) so an auto-start can block on a generation
        failure rather than silently launching with stale or absent configuration.

        `overrides` are EPHEMERAL per-start file values ({param_name: value}, this launch only,
        never persisted) taken from the Start-confirm 'Stack parameters' panel — validated by the
        caller via `_normalize_file_overrides` before they reach here."""
        from pathlib import Path
        op = self.config().operator
        runtime = str(self._paths.runtime_root)
        cfg_band = self._config_band(target, band)
        # Validate operator identity; fall back to safe placeholders if invalid so a
        # corrupted local.toml can never inject into a generated config file.
        try:
            call = validators.callsign(op.callsign or "N0CALL") or "N0CALL"
        except validators.ValidationError:
            call = "N0CALL"
        try:
            loc = validators.locator(op.locator or "")
        except validators.ValidationError:
            loc = ""

        def subst(text: str) -> str:
            return (text.replace("{callsign}", call)
                        .replace("{locator}", loc)
                        .replace("{runtime}", runtime)
                        .replace("{band}", cfg_band))    # for per-band config keys

        stored = self.file_config_values(target, band)
        over = overrides or {}
        written: list[ConfigWrite] = []
        for c in self._file_config_components(target):
            fc = c.config_file
            # Validate every stored value against its FileParam; an invalid value
            # (e.g. hand-edited TOML) reverts to the manifest default — never written
            # raw into the app's config file. An ephemeral override (already validated by
            # `_normalize_file_overrides`) takes precedence for THIS launch only.
            values = {}
            for p in fc.params:
                raw = over.get(p.name, stored.get(p.name, p.default))
                try:
                    values[p.name] = validators.validate_param(p, raw)
                except validators.ValidationError:
                    values[p.name] = p.default
            # THREE explicit destination policies (P1 generated-config containment):
            dest = self._resolve_config_dest(c, fc.path)
            if dest.status != "ok":
                written.append(ConfigWrite(c.id, dest.detail_path, dest.status, dest.detail))
                continue
            out_path = dest.path
            if fc.fmt in ("toml-update", "yaml-update"):
                base = self._resolve_config_dest(c, fc.base, for_base=True)
                if base.status != "ok":
                    written.append(ConfigWrite(c.id, base.detail_path,
                                   "failed" if base.status != "linked-readonly" else base.status,
                                   base.detail))
                    continue
                try:
                    base_text = self._read_contained(c, base)
                except (OSError, PathContainmentError) as exc:
                    written.append(ConfigWrite(c.id, str(base.path), "no-base", str(exc)))
                    continue
                updater = update_toml if fc.fmt == "toml-update" else update_yaml
                text = updater(base_text, fc.params, values, subst)
            elif fc.fmt == "env":
                # KEY=value (no spaces, no header) for split-on-'=' parsers.
                text = render_keyval(fc.params, values, subst, sep="=", comment=False)
            else:
                text = render_keyval(fc.params, values, subst)
            try:
                if dest.policy == "runtime":
                    runtime_fs.atomic_write(self._paths, out_path, text, 0o644)
                else:
                    # Pass the RELATIVE path so containment is proven component-by-
                    # component before any directory is created (P1.1).
                    self._write_source_config(c, dest.detail_path, text)
                written.append(ConfigWrite(c.id, str(out_path), "written"))
            except (OSError, PathContainmentError) as exc:
                # NEVER silently continue — a config we could not write must be visible
                # so an auto-start can block rather than launch with stale/absent config.
                written.append(ConfigWrite(c.id, str(out_path), "failed", str(exc)))
        return written

    def _resolve_config_dest(self, c, raw: str, for_base: bool = False):
        """Resolve a FileConfig path/base into one of three policies:
          * runtime  — `{runtime}/...` only, resolved through `Paths.under` (containment);
          * source   — a RELATIVE path under the managed source root (rejects linked);
          * (reject) — an arbitrary absolute path, unknown placeholder, or traversal.
        Returns a small result with `.status` ("ok"/"failed"/"linked-readonly"),
        `.policy`, `.path`, `.detail`, `.detail_path`."""
        from types import SimpleNamespace
        runtime = str(self._paths.runtime_root)
        if raw == "{runtime}" or raw.startswith("{runtime}/"):
            rel = raw[len("{runtime}"):].lstrip("/")
            parts = [p for p in rel.split("/") if p]
            try:
                p = self._paths.under(*parts) if parts else self._paths.runtime_root
            except PathContainmentError as exc:
                return SimpleNamespace(status="failed", policy="runtime", path=None,
                                       detail=f"runtime path escapes root: {exc}", detail_path=raw)
            return SimpleNamespace(status="ok", policy="runtime", path=p, detail="", detail_path=raw)
        if raw.startswith("/") or raw.startswith("{") or ".." in raw.split("/"):
            return SimpleNamespace(status="failed", policy="reject", path=None, detail_path=raw,
                                   detail="config path must be {runtime}/... or a relative source path")
        # relative -> managed source destination
        if not c.source:
            return SimpleNamespace(status="failed", policy="reject", path=None, detail_path=raw,
                                   detail="a relative config path requires a managed source")
        if not for_base and self._lifecycle().is_linked_source(c):
            return SimpleNamespace(status="linked-readonly", policy="source", path=None,
                                   detail="linked source is read-only — generate config in your checkout",
                                   detail_path=raw)
        src_dir = self._paths.resolve_source(c.source.path)
        return SimpleNamespace(status="ok", policy="source", path=src_dir / raw,
                               detail="", detail_path=raw)

    def _read_contained(self, c, dest) -> str:
        """Read a config base safely: a runtime base via runtime_fs (no-follow); a managed
        source base via the SAME descriptor-anchored, O_NOFOLLOW traversal as the writer
        (no check-then-open — a base file or parent swapped to a symlink after a check
        cannot be followed)."""
        if dest.policy == "runtime":
            from . import runtime_fs
            return runtime_fs.read_text(self._paths, dest.path)
        return self._read_source_base(c, dest.detail_path)

    @contextmanager
    def _open_source_parent(self, c, rel_path: str, *, create: bool):
        """Descriptor-anchored descent under the managed source root, immune to a symlink-
        swap race: each path component is opened RELATIVE TO ITS PARENT fd with
        `O_DIRECTORY|O_NOFOLLOW` (a component that is — or was just swapped to — a symlink
        or non-directory is refused at the syscall). With `create=True` intermediate dirs
        are created one component at a time. Yields (parent_fd, leaf_name)."""
        root = self._paths.resolve_source(c.source.path)
        parts = [p for p in Path(rel_path).parts if p not in ("", ".")]
        if not parts or any(p in ("..", "/") for p in parts):
            raise PathContainmentError(f"unsafe source config path: {rel_path!r}")
        fds = [os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
        try:
            for comp in parts[:-1]:
                if create:
                    try:
                        os.mkdir(comp, 0o755, dir_fd=fds[-1])
                    except FileExistsError:
                        pass
                try:
                    fds.append(os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=fds[-1]))
                except OSError as exc:
                    raise PathContainmentError(
                        f"source config path component {comp!r} is a symlink or not a "
                        f"directory: {exc}") from exc
            yield fds[-1], parts[-1]
        finally:
            for fd in reversed(fds):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _read_source_base(self, c, rel_path: str) -> str:
        """Read a managed-source base file with O_NOFOLLOW at the leaf, anchored to its
        parent directory fd — no check-then-open."""
        with self._open_source_parent(c, rel_path, create=False) as (parent_fd, leaf):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            with os.fdopen(fd, "rb") as fh:
                return fh.read().decode("utf-8")

    def _write_source_config(self, c, rel_path: str, text: str) -> None:
        """Atomically write a generated config into the managed SOURCE tree using a
        DESCRIPTOR-ANCHORED walk that is immune to a symlink-swap race (P1.1): each path
        component is created and opened RELATIVE TO ITS PARENT directory fd with
        `O_DIRECTORY|O_NOFOLLOW`, so replacing an already-checked directory with a symlink
        before the next step is refused AT THE syscall (a `source/conf -> outside` link can
        never get `outside/newdir` created). The leaf is written `O_NOFOLLOW` (no symlink
        clobber) and renamed in place. Linked external sources are rejected before here."""
        import stat as _stat
        with self._open_source_parent(c, rel_path, create=True) as (parent_fd, leaf):
            try:                                              # refuse an existing symlink leaf
                st = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if _stat.S_ISLNK(st.st_mode):
                    raise OSError(f"refusing a symlink-leaf source config: {leaf}")
            except FileNotFoundError:
                pass
            tmp = f".{leaf}.tmp-{os.getpid()}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644,
                         dir_fd=parent_fd)
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.rename(tmp, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except BaseException:
                try:
                    os.unlink(tmp, dir_fd=parent_fd)
                except OSError:
                    pass
                raise

    def reset_config(self, target: str, band: str = "") -> ActionResult:
        """Reset a stack/band's NORMAL Config-page settings (run params, file config, autostart)
        to defaults. Owns ONLY those keys — daemon-profile `dp_*` overrides, another band's
        overrides, and unrelated manual scalars are PRESERVED (use the daemon panel's own Reset
        for `dp_*`)."""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        from . import validators
        from .paths import PathContainmentError
        cfg_band = self._config_band(target, band)
        label = f"'{target}'" + (f" ({cfg_band})" if cfg_band else "")
        run_names = {p.name for p in self.run_params_for(target)}
        try:
            stored = load_stack_config(self._paths, target, cfg_band)
            normal = [k for k in stored
                      if k in run_names or k.startswith("file_") or k.startswith("autostart_")
                      or k.startswith("__r__") or k.startswith("__f__")]
            if normal:
                # Clear ONLY the normal-owned keys under the config lock; dp_* + unrelated stay.
                update_stack_config(self._paths, target, {k: "" for k in normal}, cfg_band)
                self._invalidate_config()
        except (ConfigError, PathContainmentError, validators.ValidationError, OSError) as exc:
            return ActionResult(False, f"Config reset blocked for {label}: unsafe/malformed "
                                f"config (refused, not modified): {exc}")
        return ActionResult(True,
                            f"Config reset to defaults for {label}." if normal
                            else f"{label} already at defaults.",
                            next_commands=[f"lhpc stack start {target}"])

    SOURCE_CHOICES = ("pinned", "dev", "stable")   # pinned = production-safe default

    def start_notes(self, result: "ActionResult") -> list[str]:
        """Per-component `start_note` strings for components that actually started
        (verified / already-healthy) in this result — e.g. how to connect a just-
        launched GUI to its node. Shown as a transient green dashboard note."""
        started = {r.component for r in result.results
                   if getattr(r, "outcome", None) is not None
                   and r.outcome.value in ("verified", "started", "already_healthy")}
        out = []
        for s in self.stacks():
            for c in s.components:
                if c.id in started and c.start_note:
                    out.append(c.start_note)
        return out

    def run_action(self, op: str, target: str, apply: bool = False,
                   params: dict | None = None, source: str = "pinned",
                   stop_owners: bool = False, cascade: bool = False,
                   band: str = "", daemon_overrides: dict | None = None,
                   file_overrides: dict | None = None) -> ActionResult:
        """Dispatch a named action to its service method (plan when apply=False)."""
        # An invalid source selector is a typed failure — NEVER silently rewritten to 'dev'.
        if op in ("install", "update") and source not in self.SOURCE_CHOICES:
            return ActionResult(False, f"Invalid source '{source}' (choose "
                                f"{', '.join(self.SOURCE_CHOICES)}).",
                                next_commands=[f"lhpc {op} {target} --source pinned"])
        ops = {
            "install": lambda: self.install(target, apply=apply, source=source),
            "update": lambda: self.update(target, apply=apply, source=source),
            "uninstall": lambda: self.uninstall(target, apply=apply),
            "start": lambda: self.start(target, apply=apply, params=params,
                                        stop_owners=stop_owners, band=band,
                                        daemon_overrides=daemon_overrides,
                                        file_overrides=file_overrides),
            "stop": lambda: self.stop(target, apply=apply, cascade=cascade, band=band),
            "restart": lambda: self.restart(target, apply=apply, params=params,
                                            stop_owners=stop_owners, band=band,
                                            file_overrides=file_overrides),
            "build": lambda: self.build(target, apply=apply),
            "test": lambda: self.test(target, apply=apply),
            "test-tx": lambda: self.test(target, tx=True, apply=apply),
        }
        fn = ops.get(op)
        if fn is None:
            return ActionResult(False, f"Unknown action '{op}'.",
                                next_commands=["lhpc help"])
        # Lifecycle coordination now lives in the PUBLIC start/stop/restart methods (the
        # authoritative locked entry points), so a DIRECT service call is guarded
        # identically to a CLI/web call. install/build/update/uninstall lock internally on
        # their source paths. run_action simply dispatches.
        return fn()

    def stack_of(self, target: str) -> str | None:
        """The stack id that owns `target` (a stack id or a component id)."""
        for s in self.stacks():
            if s.id == target or s.component(target) is not None:
                return s.id
        return None

    # ---- daemon monitoring + live settings -------------------------------

    def daemon_view(self, band: str) -> daemon_control.DaemonView:
        """Read-only STATUS/STATS/CHANNEL for a band (RSSI bars, counters)."""
        return daemon_control.read_view(self._system, band)

    def daemon_feed(self, band: str, lines: int = 40) -> list[str]:
        """Bounded tail of the daemon log filtered to RX/TX activity."""
        from .jobs import tail_log
        from pathlib import Path
        out: list[str] = []
        for raw in tail_log(Path("/tmp/lora_daemon.log"), 400):
            if any(tok in raw for tok in (f"[TX{band}]", f"[RX{band}]", "TX_RESULT",
                                          "RX_PACKET", "[TX]", "[RX]")):
                out.append(raw)
        return out[-lines:]

    def daemon_set(self, band: str, key: str, value: str, apply: bool = False) -> ActionResult:
        # Validate the band at the service boundary too (not only in web routes): a
        # direct CLI/service caller must never reach a constructed arbitrary socket path.
        if not daemon_control.is_valid_band(band):
            return ActionResult(False, f"Invalid band '{band}' (allowed: "
                                f"{', '.join(daemon_control.ALLOWED_BANDS)}).")
        err = daemon_control.validate_set(key, value)
        if err:
            return ActionResult(False, f"Invalid setting: {err}",
                                next_commands=[f"lhpc daemon {band}"])
        confirmable = daemon_control.is_confirmable(key)
        if not apply:
            note = ("This changes live daemon behaviour (non-RF tuning)."
                    if confirmable else
                    "This is a radio param the daemon does NOT report back — it can be "
                    "SENT but NOT confirmed over the socket.")
            return ActionResult(
                True,
                f"Will apply SET {key.upper()}={value.upper()} to the {band} daemon (live).",
                details=[note, "It does not transmit by itself."],
                next_commands=[f"lhpc daemon {band} --set {key}={value} --yes"],
                data={"changes": 1, "confirmable": confirmable})
        ok, confirmed, detail = daemon_control.apply_set(self._system, band, key, value)
        # Truthful outcome: only a read-back-confirmed SET is "applied"; an unconfirmable
        # radio param that was accepted is "SENT (unconfirmed)", never "applied".
        if not ok:
            verb = "FAILED"
        elif confirmed:
            verb = "applied (confirmed)"
        else:
            verb = "SENT (unconfirmed)"
        return ActionResult(ok, f"SET {key.upper()}={value.upper()}: {verb}.",
                            details=[detail], next_commands=[f"lhpc daemon {band}"],
                            data={"confirmed": confirmed})


    def _with_source(self, target: str):
        """All (stack, component) under `target` that declare a source."""
        out = []
        for s in self.stacks():
            if target and s.id != target and s.component(target) is None:
                continue
            for c in s.components:
                if c.source is None:
                    continue
                if target and s.id != target and c.id != target:
                    continue
                out.append((s, c))
        return out

    def update(self, target: str = "", apply: bool = False,
               source: str = "pinned") -> ActionResult:
        """Refresh the managed source(s) from GitHub (version per `source`:
        dev/stable/pinned), falling back to the local checkout on failure. Skips
        optional libs/firmware unless one is targeted directly.
        """
        all_items = self._with_source(target)
        if not all_items:
            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
        # A NAMED component updates exactly itself; a stack (or the empty "all"
        # target) skips its optional libs/firmware unless one is named directly.
        is_component = target != "" and self.stack(target) is None
        items = all_items if is_component else [(s, c) for s, c in all_items if not c.optional]
        if not apply:
            # The dry-run is the explicit freshness check (`lhpc update --check`):
            # it is the ONLY place that contacts the remote (git ls-remote). GET web
            # routes never do this — they show "unknown" until this is run.
            details = []
            for _, c in items:
                fresh = self.update_status(c)
                details.append(f"  {c.id}: {fresh} — fetch newest from "
                               f"{c.source.remote or 'local checkout'}")
            return ActionResult(
                True, f"Update plan for '{target or 'all'}': refresh {len(items)} source(s) "
                "from GitHub (local fallback).",
                details=details,
                next_commands=[f"lhpc update {target} --yes"] if items else [],
                data={"changes": len(items)})
        inst = self._installer()
        out, ok = [], True
        for _, c in items:
            r = inst.adopt_source(c, force=True, source=source)
            out.append(f"  [{r.status}] {c.id}: {r.detail}")
            if r.status == "failed":
                ok = False
        return ActionResult(ok, f"Update {'applied' if ok else 'INCOMPLETE'} for "
                            f"'{target or 'all'}'.", details=out,
                            next_commands=["lhpc status --versions"])

    def uninstall(self, target: str, apply: bool = False) -> ActionResult:
        """Remove managed runtime sources for `target`. Refuses if a target
        component is running; never removes a source still referenced by another
        component (shared checkout); never touches config, secrets or profiles."""
        items = self._with_source(target)
        if not items:
            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
        target_ids = {c.id for _, c in items}

        # 1) Refuse while any target component is running.
        snap = self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        running = sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                         if cid in target_ids and st.run_state in up)
        if running:
            return ActionResult(
                False, f"Refusing to uninstall '{target or 'all'}': component(s) running.",
                details=[f"  running: {', '.join(running)} — stop them first"],
                next_commands=[f"lhpc stack stop {target} --yes"])

        # 2) Every component (manifest-wide) that references each source path.
        consumers: dict[str, set[str]] = {}
        for s in self.stacks():
            for c in s.components:
                if c.source:
                    consumers.setdefault(c.source.path, set()).add(c.id)

        # 3) Decide remove vs keep-shared, deduped by source path.
        to_remove: dict[str, Component] = {}     # path -> a representative component
        kept: list[tuple[str, list[str]]] = []   # (path, remaining consumers)
        for _, c in items:
            path = c.source.path
            if not self._paths.resolve_source(path).exists():
                continue
            remaining = sorted(consumers.get(path, set()) - target_ids)
            if remaining:
                if path not in {p for p, _ in kept}:
                    kept.append((path, remaining))
            else:
                to_remove.setdefault(path, c)

        details = [f"  [remove] src/{p.split('/')[-1]} ({c.id})" for p, c in to_remove.items()]
        details += [f"  [keep — shared] src/{p.split('/')[-1]} still used by {', '.join(r)}"
                    for p, r in kept]
        details.append("  (config, secrets and profiles are preserved)")
        if not apply:
            return ActionResult(
                True, f"Uninstall plan for '{target or 'all'}': remove {len(to_remove)}, "
                f"keep {len(kept)} shared.", details=details,
                next_commands=[f"lhpc uninstall {target} --yes"] if to_remove else [],
                data={"changes": len(to_remove)})

        import shutil
        from . import reslock
        out, ok = [], True
        # P0.1: ONE atomic guard for the whole uninstall — index→recover→block→source-lock
        # handoff, holding ALL affected source-path locks (sorted) for the removals. A
        # linked source is only UNLINKED — its external target is never removed.
        src_paths = sorted(to_remove.keys())
        try:
            with self._source_operation_guard(src_paths, op="uninstall"):
                for path, c in to_remove.items():
                    dest = self._paths.resolve_source(path)
                    try:
                        dest.unlink() if dest.is_symlink() else shutil.rmtree(dest)
                        out.append(f"  [removed] src/{path.split('/')[-1]}")
                    except OSError as exc:
                        out.append(f"  [fail] {path}: {exc}")
                        ok = False
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Uninstall blocked for '{target or 'all'}': {blocked}",
                                next_commands=["lhpc status"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Uninstall blocked for '{target or 'all'}': {busy}",
                                next_commands=["lhpc status"])
        out += [f"  [kept — shared] src/{p.split('/')[-1]} (used by {', '.join(r)})"
                for p, r in kept]
        return ActionResult(ok, f"Uninstall {'applied' if ok else 'incomplete'} for "
                            f"'{target or 'all'}' (config preserved).",
                            details=out, next_commands=["lhpc status"])

    # ---- helpers ---------------------------------------------------------

    def _unknown_stack(self, stack_id: str) -> ActionResult:
        known = ", ".join(s.id for s in self.stacks())
        return ActionResult(
            ok=False,
            summary=f"Unknown stack '{stack_id}'.",
            details=[f"Known stacks: {known}"],
            next_commands=["lhpc list"],
        )


def _render_component(comp, status) -> list[str]:
    band = f"band {comp.band}" if comp.band else "band -"
    line = (
        f"  {comp.id:24s} {status.run_state.value:14s} "
        f"[{comp.kind.value}] {band}  tx {status.tx_state.value}  "
        f"src {status.source_state.value}"
    )
    out = [line]
    for dep in status.dependencies:
        band_txt = f" on {dep.band} MHz" if dep.band else ""
        out.append(f"        depends on {dep.component_id}: {dep.run_state.value}{band_txt}")
    for obs in status.endpoints:
        out.append(f"        endpoint {obs.spec.address} {obs.spec.kind}: {obs.detail}")
    return out

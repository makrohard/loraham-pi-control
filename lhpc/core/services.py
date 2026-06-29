"""Shared application/service layer — the single entry point for all behaviour.

The CLI adapter and the web adapter both call ONLY this module, guaranteeing
identical validation, status interpretation and results. Read methods are bounded
and read-only; mutating methods print a plan and apply only when confirmed.

`build_snapshot()` is the single probing path; both `status()` (CLI text) and the
web adapter call it, so a page load and a CLI run see the same fresh evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import daemon_control
from . import manifest as manifest_mod
from . import resources as resources_mod
from .config import (
    Config,
    load_config,
    load_stack_config,
    render_keyval,
    save_component_remote,
    save_operator_config,
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
from .paths import Paths, resolve_paths
from .probes import RealSystem, System
from .probes import hardware
from .status import Snapshot, StatusProber, rollup_states, summarize

_SPI_DEV = "/dev/spidev0.0"
_GPIO_DEV = "/dev/gpiochip0"


@dataclass
class ActionResult:
    """Uniform result object rendered identically by every adapter."""

    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


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

    # ---- config / installer ---------------------------------------------

    def config(self) -> Config:
        if self._config is None:
            self._config = load_config(self._paths)
        return self._config

    def _installer(self) -> Installer:
        return Installer(self._paths, self.stacks(), self.config(), self._system)

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
        observed = [c for c in snap.conflicts if c.observed]
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
        observed = [c for c in snap.conflicts if c.observed]
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
                source: str = "dev") -> ActionResult:
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
        return self._plan_result(plan, applied=True, next_apply=None)

    def _plan_result(self, plan: Plan, *, applied: bool, next_apply: str | None) -> ActionResult:
        details = [
            f"  [{a.status}] {a.description}" + (f" — {a.detail}" if a.detail else "")
            for a in plan.actions
        ]
        failed = [a for a in plan.actions if a.status == "failed"]
        if applied:
            done = sum(1 for a in plan.actions if a.status == "done")
            summary = (f"{plan.title}: applied {done} action(s)."
                       if not failed else
                       f"{plan.title}: completed with {len(failed)} failure(s).")
            return ActionResult(ok=not failed, summary=summary, details=details,
                                next_commands=["lhpc status", "lhpc doctor"])
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
            runnable = [c for c in s.components if c.run_cmd]
            runnable.sort(key=lambda c: (c.start_order is None, c.start_order or 0))
            return [(s, c) for c in runnable], None
        for st in self.stacks():
            c = st.component(target)
            if c is not None:
                return [(st, c)], None
        return [], f"Unknown stack or component '{target}'."

    def _running_conflicts(self, comp, band: str = "") -> list[str]:
        """Observed conflicts that would block starting `comp` right now. Radio
        claims are matched by the band each side actually uses, so a multi-band app
        on 868 does not conflict with a daemon serving only 433 (and vice-versa)."""
        import dataclasses
        snap = self.build_snapshot()
        served = {b for b in self.RADIO_BANDS if self.daemon_view(b).reachable}

        def limited(c, eff_bands):
            # Drop a multi-band/daemon component's radio.<band> claims for bands it
            # is not actually using; single-fixed-band components are unchanged.
            if c.id != self.DAEMON_ID and not c.bands:
                return c
            keep = []
            for r in c.resources:
                if r.key.startswith("loraham.radio.") and eff_bands:
                    if r.key.rsplit(".", 1)[-1] not in eff_bands:
                        continue
                keep.append(r)
            return dataclasses.replace(c, resources=tuple(keep))

        running, running_ids = [], set()
        for ss in snap.stacks:
            for c in ss.stack.components:
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                if c.id == self.DAEMON_ID:
                    eff = served
                elif c.bands:
                    eb = self._effective_band(ss.stack.id)
                    eff = {eb} if eb else set()
                else:
                    eff = {c.band} if c.band else set()
                running.append(limited(c, eff))
                running_ids.add(c.id)
        target = limited(comp, {band} if band else set())
        conflicts = resources_mod.interpret_conflicts(running + [target], running_ids | {comp.id})
        return [c.message for c in conflicts if comp.id in c.holders and c.observed]

    DAEMON_ID = "loraham-daemon"
    # After auto-starting the daemon, wait up to this long for its CONF socket to
    # answer before reporting success (the daemon inits the radio asynchronously).
    DAEMON_VERIFY_TIMEOUT_S = 4.0
    DAEMON_VERIFY_POLL_S = 0.5

    def _component_index(self):
        return {c.id: (s, c) for s in self.stacks() for c in s.components}

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

    def _daemon_needs(self, order, params, band: str = ""):
        """The daemon's required radio band + TX mode for this run order. `band`
        overrides the band for a band-switchable app stack."""
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

    def run_blockers(self, target: str, band: str = "") -> list[dict]:
        """REAL resource conflicts only: exclusive/provider resources this run would
        use that a RUNNING component of another stack is *actually* using. Radio
        bands are matched by the band each side really uses (a multi-band stack on
        868 does not block another stack on 433; the daemon only conflicts on the
        bands it currently serves)."""
        order = self._run_order(target)
        if not order:
            return []
        cfg_band = self._config_band(target, band)
        order_ids = {c.id for _, c in order}
        target_stack = self.stack_of(target)
        # Bands this run actually uses (the chosen/app band — never both for a
        # band-switchable stack).
        needed_bands = {cfg_band} if cfg_band else {c.band for _, c in order if c.band}
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
        served = {b for b in self.RADIO_BANDS if self.daemon_view(b).reachable}
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
                    eb = self._effective_band(sid)
                    active = {eb} if eb else set()
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
                # same-frequency rule: another stack on a band we need. Exclude the
                # daemon STACK (it provides the radio, not a competing app) — by its
                # stack id, since DAEMON_ID is a component id.
                comp_band = self._effective_band(sid, c.band) if multi else c.band
                if (sid != target_stack and sid != self.stack_of(self.DAEMON_ID)
                        and comp_band and comp_band in needed_bands):
                    add(sid, c.id, f"radio {comp_band} MHz")
        return blockers

    def start(self, target: str, apply: bool = False, params: dict | None = None,
              stop_owners: bool = False, band: str = "") -> ActionResult:
        order = self._run_order(target)
        if order is None:
            return ActionResult(False, f"Unknown stack or component '{target}'.",
                                next_commands=["lhpc list"])
        if not self._paths.runtime_root_exists:
            return ActionResult(False, "Runtime root not bootstrapped.",
                                next_commands=["lhpc bootstrap"])
        # Band-switchable stack: resolve the chosen band (default = first allowed).
        cfg_band = self._config_band(target, band)
        life = self._lifecycle()
        radio, tx = self._daemon_needs(order, params, cfg_band)
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
                elif comp.units and not comp.run_cmd:
                    cmd = f"sudo systemctl start {comp.units[0].name}"
                    details.append(f"  [manual] {comp.id} is a system service — start it with:")
                    details.append(f"    {cmd}")
                    commands.append(cmd)
                else:
                    details.append(f"  [start] {comp.id} (band {cfg_band or comp.band or '-'})")
            blockers = self.run_blockers(target, band)
            for bl in blockers:
                details.append(f"  [conflict] {bl['resource']} is held by running stack "
                               f"'{bl['holder_stack']}' ({bl['holder']})")
            return ActionResult(True, f"Run plan for '{target}': {len(order)} component(s) in order.",
                                details=details,
                                next_commands=[f"lhpc stack start {target} --yes"],
                                data={"changes": len(order), "blockers": blockers,
                                      "commands": commands})

        # Ownership check: if a needed resource is held by another running stack,
        # either stop that stack first (stop_owners) or refuse and report it.
        blockers = self.run_blockers(target, band)
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
            for o in owners:
                self.stop(o, apply=True)
                prelude.append(f"  [stopped] conflicting stack '{o}'")
            time.sleep(1.0)  # let sockets/locks release
        else:
            prelude = []

        snap = self.build_snapshot()
        st_index = {c.id: ss.components[c.id]
                    for ss in snap.stacks for c in ss.stack.components}
        out = list(prelude)
        for stack, comp in order:
            running = st_index[comp.id].run_state in (RunState.RUNNING, RunState.DEGRADED)
            if comp.id == self.DAEMON_ID:
                out.extend(self._ensure_daemon(life, stack, comp, running, radio, tx, params))
                continue
            if running:
                out.append(f"  [ok] {comp.id}: already running")
                continue
            if comp.source and not life.source_dir(comp).exists():
                out.append(f"  [skip] {comp.id}: not installed (lhpc install {stack.id})")
                continue
            if comp.interactive:
                # Never auto-start an interactive TUI — the operator runs it in a
                # terminal. ALWAYS mark it so the dash shows it in the interactive
                # section; the dash shows either the launch command (when ready) or
                # the install/build blocker (so it never vanishes after a start).
                if comp.config_file:
                    self.write_config_files(stack.id, cfg_band)
                self.mark_interactive(stack.id, cfg_band)
                blocker = self.install_blocker(comp)
                if blocker:
                    out.append(f"  [manual] {comp.id} is interactive but {blocker}")
                else:
                    out.append(f"  [manual] {comp.id} is interactive — run it in a terminal:")
                    out.append(f"    {self.manual_start_command(comp)}")
                continue
            if comp.units and not comp.run_cmd:
                # Externally supervised (systemd, root) — lhpc observes, never starts.
                out.append(f"  [manual] {comp.id} is a system service — start it with: "
                           f"sudo systemctl start {comp.units[0].name}")
                continue
            miss = life.missing_requirements(comp)
            if miss:
                out.append(f"  [BLOCKED] {comp.id}: missing "
                           + "; ".join(f"{r.cmd} ({r.install})" for r in miss))
                continue
            if self._running_conflicts(comp, cfg_band):
                out.append(f"  [BLOCKED] {comp.id}: resource conflict")
                continue
            if not self.is_built(comp):
                # Don't spawn a doomed `exec <missing binary>` — it fails silently
                # and the stack never appears. Point at the build instead.
                out.append(f"  [BLOCKED] {comp.id}: not built — build it first "
                           f"(lhpc build {stack.id})")
                continue
            # Regenerate any config file this component reads (per the chosen band),
            # then apply the stack's saved configuration to the app component.
            if comp.config_file:
                self.write_config_files(stack.id, cfg_band)
            comp_cfg = self.stack_config(stack.id, cfg_band)
            res = life.start(stack, comp, comp_cfg)
            out.append(f"  [{'ok' if res.ok else 'fail'}] start {comp.id} (log {res.log_path})")
            # Optional detached post-start hook (e.g. set the Meshtastic region once up).
            if res.ok and comp.post_start:
                life.spawn_post_start(stack, comp, comp_cfg)
                out.append(f"  [post] {comp.id}: scheduled post-start setup")
            # Remember which band a band-switchable stack was started on (for the dash).
            if cfg_band and self.stack_bands(stack.id):
                self._set_running_band(stack.id, cfg_band)
        # Starting a stack clears any prior interactive apps that were marked but
        # aren't actually running, so the dash doesn't keep showing their command.
        self.clear_stale_interactive(keep=self.stack_of(target) or target)
        return ActionResult(True, f"Run applied for '{target}'.", details=out,
                            next_commands=[f"lhpc status {target}", f"lhpc logs {target}",
                                           f"lhpc stack stop {target}"])

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
            if radio in (band, "both"):
                out.append(pid)
        return out

    def _ensure_daemon(self, life, stack, comp, running, radio, tx, params):
        """Ensure the daemon is up FOR THE NEEDED BAND before the app starts.

        "Running" means the band's CONF socket is reachable, not merely that a
        daemon process exists. The daemon is MULTI-INSTANCE (independent process +
        lock per band), so a daemon serving only the OTHER band does not block us —
        we just start a separate instance for the band we need:
          * serving the band      -> just SET the needed TX mode (no restart);
          * not serving the band   -> start a daemon instance with --radio <band>
            (works alongside an instance already serving the other band).
        """
        band = radio if radio in ("433", "868") else None
        view = daemon_control.read_view(self._system, band) if band else None
        serving = view.reachable if band else running
        if serving:
            # TX mode can be changed live on a running daemon (SET TXMODE, applied
            # silently). Only set it when it differs, so we don't disturb the band.
            cur = (view.status.get("TXMODE", "") if view else "").upper()
            if tx and band and cur != tx.upper():
                ok, detail = daemon_control.apply_set(self._system, band, "TXMODE", tx)
                return [f"  [{'ok' if ok else 'warn'}] daemon serving {band}; "
                        f"SET TXMODE={tx}: {detail if not ok else 'applied'}"]
            return [f"  [ok] daemon already serving {band}" + (f" (TXMODE={cur})" if cur else "")]
        if comp.source and not life.source_dir(comp).exists():
            return ["  [skip] daemon: not installed (lhpc install daemon)"]
        if not self.is_built(comp):
            return ["  [BLOCKED] daemon: not built — build it first (lhpc build daemon)"]
        # Start with the saved daemon config (its normal/default TX mode). The needed
        # TX mode is applied LIVE once the socket is up (SET TXMODE) — the documented
        # MeshCom path; a --tx-mode-<band> direct START flag could fail to init.
        dparams = dict(self.stack_config("daemon"))
        dparams["radio"] = radio or "both"
        if params and params.get("debug"):
            dparams["debug"] = "1"
        res = life.start(stack, comp, dparams)
        base = f"start daemon --radio {dparams['radio']}"
        if not res.ok:
            return [f"  [fail] {base}"]
        # The daemon inits the radio + opens its CONF socket asynchronously. VERIFY it
        # actually came up so we don't report a false [ok] (and so dependent
        # components — and the live TX-mode SET — act on a real socket).
        if band and self.DAEMON_VERIFY_TIMEOUT_S > 0:
            waited = 0.0
            while waited < self.DAEMON_VERIFY_TIMEOUT_S:
                time.sleep(self.DAEMON_VERIFY_POLL_S)
                waited += self.DAEMON_VERIFY_POLL_S
                if daemon_control.read_view(self._system, band).reachable:
                    out = [f"  [ok] {base}"]
                    if tx:
                        ok, _ = daemon_control.apply_set(self._system, band, "TXMODE", tx)
                        out.append(f"  [{'ok' if ok else 'warn'}] SET TXMODE={tx} on {band}")
                    return out
            return [f"  [warn] {base} — but the {band} CONF socket isn't answering; it "
                    f"likely failed to init (radio/SPI busy or a stale lock). "
                    f"Check the daemon log."]
        return [f"  [ok] {base}"]

    def stop_dependents(self, target: str) -> list[str]:
        """Running stacks that would be orphaned if `target` stops (they depend on
        one of its components) — e.g. stopping the daemon orphans kiss/igate/…"""
        tstack = self.stack(target)
        if tstack is None:
            return []
        member_ids = {c.id for c in tstack.components}
        up = (RunState.RUNNING, RunState.DEGRADED)
        out = []
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == tstack.id:
                continue
            running = any(ss.components[c.id].run_state in up for c in ss.stack.components)
            depends = any(d in member_ids for c in ss.stack.components for d in c.depends_on)
            if running and depends:
                out.append(ss.stack.id)
        return out

    def stop(self, target: str, apply: bool = False, cascade: bool = False,
             band: str = "") -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        # Stop in reverse start order.
        items = list(reversed(items))
        dependents = self.stop_dependents(target)
        # Per-band daemon stop signals only the requested band's instance(s). The
        # other band is collateral ONLY when it is served by the SAME process — i.e.
        # a --radio both instance (so every PID serving the other band is also in
        # this band's stop set). Separate per-band instances are unaffected.
        other_bands = []
        _sid = self.stack_of(target)
        _stk = self.stack(_sid) if _sid else None
        if _stk and _stk.main == self.DAEMON_ID and band in ("433", "868"):
            other = "868" if band == "433" else "433"
            stop_pids = set(self._daemon_pids_for_band(band))
            other_pids = set(self._daemon_pids_for_band(other))
            if other_pids and other_pids <= stop_pids and self.daemon_view(other).reachable:
                other_bands = [other]
        # systemd services lhpc doesn't own (e.g. meshtasticd) — stop them as root.
        sysd = [f"sudo systemctl stop {c.units[0].name}"
                for _, c in items if c.units and not c.run_cmd]
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
        # Cascade: stop dependent stacks first so they aren't orphaned — but NEVER
        # auto-terminate an interactive app the operator started by hand; just note it.
        if cascade:
            for dep in dependents:
                dstk = self.stack(dep)
                dmain = dstk.main_component if dstk else None
                if dmain is not None and dmain.interactive:
                    details.append(f"  [kept] interactive app '{dep}' left running — stop it yourself")
                    continue
                self.stop(dep, apply=True)
                details.append(f"  [stopped dependent] {dep}")
        for _, comp in items:
            if comp.units and not comp.run_cmd:
                details.append(f"  [manual] {comp.id}: stop it as root: "
                               f"sudo systemctl stop {comp.units[0].name}")
                continue
            # The daemon is multi-instance (one process per band). A per-band stop
            # must signal ONLY the instance serving that band, not every daemon.
            if comp.id == self.DAEMON_ID and band in ("433", "868"):
                pids = self._daemon_pids_for_band(band)
                killed, note = life.stop_pids(pids)
                note = f"{note} (band {band})"
            else:
                killed, note = life.stop(comp)
            details.append(f"  {comp.id}: {note}" + (f" (pids {killed})" if killed else ""))
        sid = self.stack_of(target)
        if sid:
            self._band_marker(sid).unlink(missing_ok=True)
            self.dismiss_interactive(sid)
        return ActionResult(True, f"Stop applied for '{target}'.", details=details,
                            next_commands=[f"lhpc status {target}"])

    def restart(self, target: str, apply: bool = False, params: dict | None = None,
                stop_owners: bool = False, band: str = "") -> ActionResult:
        """Stop then start a target — used to apply a config change to a running stack.
        With no band given, keep the band the stack is currently running on (so a
        restart doesn't move a band-switchable stack back to its default band)."""
        if not band:
            band = self._effective_band(self.stack_of(target) or target)
        if not apply:
            res = self.start(target, apply=False, params=params, band=band)
            return ActionResult(res.ok, f"Restart plan for '{target}': stop then run.",
                                details=res.details, data=res.data,
                                next_commands=[f"lhpc stack restart {target} --yes"])
        self.stop(target, apply=True, band=band)
        time.sleep(1.0)  # let sockets/locks release before re-starting
        res = self.start(target, apply=True, params=params, stop_owners=stop_owners, band=band)
        return ActionResult(res.ok, f"Restarted '{target}'. {res.summary}",
                            details=res.details, next_commands=res.next_commands)

    def build(self, target: str, apply: bool = False) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        buildable = [(s, c) for s, c in items if c.build_cmd]
        if not apply:
            details = [f"  [build] {c.id}: {c.build_cmd}" for _, c in buildable]
            return ActionResult(True, f"Build plan for '{target}': {len(buildable)} component(s).",
                                details=details,
                                next_commands=[f"lhpc build {target} --yes"] if buildable else [],
                                data={"changes": len(buildable)})
        details = []
        ok = True
        for _, comp in buildable:
            res = life.build(comp)
            ok = ok and res.ok
            details.append(f"  [{res.state.value}] build {comp.id} (rc {res.returncode}, log {res.log_path})")
            if not res.ok:
                details.extend(f"      {ln}" for ln in res.tail[-6:])
        return ActionResult(ok, f"Build {'succeeded' if ok else 'FAILED'} for '{target}'.",
                            details=details, next_commands=["lhpc status " + target])

    def log_tail(self, target: str, lines: int = 300, job: str | None = None):
        """Raw (path, lines) for `target`'s log — for the live web log view.

        With `job` (a logs/<name>.log filename) it tails that specific job log
        (e.g. a build/test run); otherwise it tails the component's process log.
        """
        from pathlib import Path
        from .jobs import tail_log
        if job:
            p = self._paths.runtime_root / "logs" / job
            return str(p), tail_log(p, lines)
        s = self.stack(target)
        if s is not None and s.main_component:
            comp = s.main_component
        else:
            idx = self._component_index()
            if target not in idx:
                return "", []
            comp = idx[target][1]
        return self._lifecycle().logs(comp, lines)

    def spawn_web_job(self, op: str, target: str, source: str = "dev"):
        """Spawn detached build/test/install job(s) for `target`; return
        (job_log_name, error). The web redirects to a live view of the log."""
        life = self._lifecycle()
        # Install runs as one logged subprocess so the operator sees clone output
        # live and when it finishes (the dash redirects to this log).
        if op == "install":
            import sys
            cmd = (f"{sys.executable} -m lhpc install {target} --yes "
                   f"--source {source if source in self.SOURCE_CHOICES else 'dev'}")
            ln, pid = life.spawn_job(f"install-{target}", cmd, str(self._paths.runtime_root))
            if not ln or not pid:
                return None, f"could not start install for '{target}'"
            self._write_job_marker(ln, pid, target, "install")
            return ln, None
        items, err = self._resolve(target)
        if err:
            return None, err
        jobs = []
        for _, c in items:
            if op == "build" and c.build_cmd:
                jobs.append((c, c.build_cmd, f"build-{c.id}"))
            elif op == "test" and c.test_cmd:
                jobs.append((c, c.test_cmd, f"test-{c.id}"))
        if not jobs:
            return None, f"nothing to {op} for '{target}'"
        # A stack may build several components (e.g. meshcom = bridge + qemu firmware).
        # Redirect to the MAIN component's log when it's among them, so the operator
        # sees the outcome of the thing they're trying to run — not whichever job
        # happened to spawn first.
        s = self.stack(target)
        main_id = s.main if s else None
        first = main_log = None
        for c, cmd, name in jobs:
            ln, pid = life.spawn_job(name, cmd, str(life.source_dir(c)))
            if ln and pid:
                self._write_job_marker(ln, pid, c.id, op)
            if first is None:
                first = ln
            if c.id == main_id:
                main_log = ln
        return (main_log or first), None

    def _jobs_dir(self):
        return self._paths.runtime_root / "state" / "jobs"

    def _write_job_marker(self, log_name: str, pid: int, target: str, op: str) -> None:
        d = self._jobs_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / (log_name + ".job")).write_text(
            f'pid = {pid}\ntarget = "{target}"\nop = "{op}"\nlog = "{log_name}"\n',
            encoding="utf-8")

    def active_jobs(self) -> list[dict]:
        """Build/test jobs whose process is still alive. Stale markers are pruned."""
        import tomllib
        d = self._jobs_dir()
        if not d.is_dir():
            return []
        out = []
        for f in sorted(d.glob("*.job")):
            try:
                with f.open("rb") as fh:
                    raw = tomllib.load(fh)
                pid = int(raw["pid"])
            except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
                f.unlink(missing_ok=True)
                continue
            if _pid_running(pid):
                out.append({"log": raw.get("log"), "target": raw.get("target"),
                            "op": raw.get("op"), "stack": self.stack_of(raw.get("target", ""))})
            else:
                f.unlink(missing_ok=True)   # finished -> clear the marker
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

    def test(self, target: str, tx: bool = False, live: bool = False,
             apply: bool = False) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        if not tx:
            # RX-safe host tests.
            if not apply:
                details = [f"  [host-test] {c.id}: {c.test_cmd or '(no host test)'}" for _, c in items]
                return ActionResult(True, f"Host-test plan for '{target}' (TX-safe).",
                                    details=details,
                                    next_commands=[f"lhpc test {target} --yes"],
                                    data={"changes": sum(1 for _, c in items if c.test_cmd)})
            details = []
            ok = True
            for _, comp in items:
                res = life.host_test(comp)
                if res is None:
                    details.append(f"  [skip] {comp.id}: no host test")
                    continue
                ok = ok and res.ok
                details.append(f"  [{res.state.value}] {comp.id} (rc {res.returncode}, log {res.log_path})")
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
        bands = [b for b in wanted if self.daemon_view(b).reachable]
        if not bands:
            return ActionResult(
                False, f"Cannot TX-test '{target}': the daemon isn't serving "
                f"{' or '.join(wanted)} MHz — start the daemon on that band first.",
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

    def _owner(self, comp):
        for s in self.stacks():
            if s.component(comp.id):
                return s
        return self.stacks()[0]

    # ---- unified action dispatch (used by the web control interface) -----

    # Web-exposed actions -> the same gated service methods the CLI calls.
    WEB_ACTIONS = ("install", "update", "uninstall", "start", "stop", "restart",
                   "build", "repair", "rollback", "test", "test-tx")

    def run_params_for(self, target: str):
        """Run parameters offered for `target` (from its main/own components)."""
        s = self.stack(target)
        comps = s.components if s else (
            [self._component_index()[target][1]] if target in self._component_index() else [])
        params = []
        for c in comps:
            params.extend(c.run_params)
        return params

    def _interactive_marker(self, stack_id: str):
        return self._paths.runtime_root / "state" / "interactive" / f"{stack_id}.show"

    def mark_interactive(self, stack_id: str, band: str = "") -> None:
        """Remember that the operator asked to run an interactive app (so the dash
        shows its terminal-command block); stores the chosen band."""
        m = self._interactive_marker(stack_id)
        try:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(band, encoding="ascii")
        except OSError:
            pass

    def interactive_band(self, stack_id: str) -> str | None:
        """The band an interactive app was started on, or None if not active."""
        try:
            return self._interactive_marker(stack_id).read_text(encoding="ascii").strip()
        except OSError:
            return None

    def dismiss_interactive(self, stack_id: str) -> None:
        self._interactive_marker(stack_id).unlink(missing_ok=True)

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
        return self._paths.runtime_root / "state" / "running" / f"{stack_id}.band"

    def _set_running_band(self, stack_id: str, band: str) -> None:
        m = self._band_marker(stack_id)
        try:
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(band, encoding="ascii")
        except OSError:
            pass

    def running_band(self, stack_id: str, default: str = "") -> str:
        try:
            return self._band_marker(stack_id).read_text(encoding="ascii").strip() or default
        except OSError:
            return default

    def stack_bands(self, target: str) -> tuple:
        """Bands the operator may choose for `target` (empty = single fixed band)."""
        s = self.stack(target)
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
        s = self.stack(target)
        for c in (s.components if s else ()):
            if c.bands and c.band in allowed:
                return c.band
        return allowed[0]

    def stack_config(self, target: str, band: str = "") -> dict:
        """Effective config for `target` (and band, for band-switchable stacks):
        the stored value, else the per-band default, else the manifest default."""
        cfg_band = self._config_band(target, band)
        stored = load_stack_config(self._paths, target, cfg_band)
        out = {}
        for p in self.run_params_for(target):
            bd = dict(p.band_defaults).get(cfg_band or band, p.default)
            out[p.name] = stored.get(p.name, bd)
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
        served = [b for b in self.RADIO_BANDS if self.daemon_view(b).reachable]
        idir = self._paths.runtime_root / "state" / "interactive"
        marks = []
        if idir.exists():
            for f in sorted(idir.glob("*.show")):
                try:
                    marks.append(f"{f.stem}={f.read_text().strip()}")
                except OSError:
                    marks.append(f.stem)
        return "R:" + ",".join(running) + ";D:" + ",".join(served) + ";I:" + ",".join(marks)

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
        """The shell command the operator runs in a terminal to start an
        interactive component (the controller never starts these itself), with
        its run parameters and operator identity substituted in."""
        run = comp.run_cmd or "(no run command)"
        stack_id = self.stack_of(comp.id) or ""
        vals = self.stack_config(stack_id) if stack_id else {}
        op = self.config().operator
        src = self._paths.resolve_source(comp.source.path) if comp.source else ""
        for p in comp.run_params:
            run = run.replace("{" + p.name + "}", emit_param(p, vals.get(p.name, p.default)))
        run = (run.replace("{callsign}", op.callsign or "N0CALL")
                  .replace("{locator}", op.locator or "")
                  .replace("{runtime}", str(self._paths.runtime_root))
                  .replace("{source}", str(src)))
        run = " ".join(run.split())
        # A run that cd's itself (e.g. into the runtime config dir) needs no prefix.
        if comp.source is not None and not run.startswith("cd "):
            return f"cd {src} && {run}"
        return run

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
        served = [b for b, v in dvs.items() if v.reachable]
        out = []
        for band in self.RADIO_BANDS:
            dv = dvs[band]
            other_served = [b for b in served if b != band]
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
                comps = [{"id": c.id, "name": c.name, "optional": c.optional,
                          "runnable": c.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE),
                          "interactive": c.interactive,
                          # Interactive components (GUI/CLI/REPL) are run by the
                          # operator, never by lhpc — show the command to copy.
                          "command": self.manual_start_command(c) if c.interactive else "",
                          "blocker": self.install_blocker(c) if c.interactive else "",
                          # Has tunables -> a config link; lhpc captures a start log
                          # (non-interactive run) or it declares its own -> a log link.
                          "configurable": bool(c.run_params or c.config_file),
                          "writes_log": bool(c.log_paths) or bool(c.run_cmd and not c.interactive),
                          "state": (live[c.id].run_state.value if c.id in live else "unknown"),
                          "interfaces": self._client_interfaces(live.get(c.id))}
                         for c in s.components]
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
                    "reachable": dv.reachable,
                    "process": daemon_proc,        # process run-state (band-independent)
                    "process_up": daemon_up,
                    "installed": daemon_installed,
                    "other_served": other_served,  # other band(s) the daemon serves
                    "served": served,
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
                with f.open("rb") as fh:
                    return _pid_running(int(tomllib.load(fh)["pid"]))
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
        uses_operator = any(
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
            "components": [{"id": c.id, "name": c.name, "params": c.run_params} for c in comps],
            "optional": optional,
            "sources": sources,
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
            view["radios"] = [{"band": live_band, "reachable": dv.reachable,
                               "error": dv.error, "status": dv.status}]
            view["live_settings"] = daemon_control.allowed_settings()
        return view

    def save_config(self, target: str, values: dict,
                    callsign: str | None = None, locator: str | None = None,
                    band: str = "") -> ActionResult:
        """Save operator identity (if supplied) and the stack's run parameters."""
        if callsign is not None or locator is not None:
            save_operator_config(self._paths, (callsign or "").strip().upper(),
                                 (locator or "").strip())
        return self.save_stack_config(target, values, band=band)

    def save_stack_config(self, target: str, values: dict, band: str = "") -> ActionResult:
        """Validate and persist a stack/band's configuration (used on the next Run)."""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        clean, errors = {}, []
        for p in self.run_params_for(target):
            if p.name not in values:
                continue
            v = str(values[p.name])
            if p.kind == "enum" and p.choices and v not in p.choices:
                errors.append(f"{p.name}: must be one of {', '.join(p.choices)}")
                continue
            if p.kind == "int" and v.strip() != "":   # empty = unset (optional option)
                try:
                    n = int(v)
                except ValueError:
                    errors.append(f"{p.name}: not an integer")
                    continue
                if (p.min is not None and n < p.min) or (p.max is not None and n > p.max):
                    errors.append(f"{p.name}: out of range [{p.min}, {p.max}]")
                    continue
            clean[p.name] = v
        # Pass-through autostart toggles + file-config values (no run-param validation).
        for key, value in values.items():
            if key.startswith("autostart_"):
                clean[key] = "on" if str(value) in ("on", "1", "true", "yes") else ""
            elif key.startswith("file_"):
                clean[key] = str(value)
        if errors:
            return ActionResult(False, f"Config not saved for '{target}'.", details=errors,
                                next_commands=[])
        # What changed vs the effective config, and what workflow that needs.
        before = self.stack_config(target, band)
        params_by_name = {p.name: p for p in self.run_params_for(target)}
        changed_modes = {params_by_name[k].apply_mode
                         for k, v in clean.items()
                         if k in params_by_name and str(before.get(k, "")) != str(v)}
        save_stack_config(self._paths, target, clean, self._config_band(target, band))
        hints = self._apply_hints(target, changed_modes)
        return ActionResult(True, f"Config saved for '{target}'.", details=hints,
                            next_commands=[f"lhpc stack start {target}"])

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

    def save_component_remote(self, component_id: str, url: str) -> None:
        """Override (or clear, if url is blank) a component's GitHub remote."""
        save_component_remote(self._paths, component_id, url)

    # ---- file-based component config (writes the app's own config file) -----

    def _file_config_components(self, target: str):
        s = self.stack(target)
        return [c for c in (s.components if s else ()) if c.config_file]

    def file_config_values(self, target: str, band: str = "") -> dict:
        """Stored file-config values (key `file_<name>` in the per-band stack
        config), falling back to the per-band default, then the FileParam default."""
        cfg_band = self._config_band(target, band)
        stored = load_stack_config(self._paths, target, cfg_band)
        out = {}
        for c in self._file_config_components(target):
            for p in c.config_file.params:
                bd = dict(p.band_defaults).get(cfg_band or band, p.default)
                out[p.name] = stored.get(f"file_{p.name}", bd)
        return out

    def write_config_files(self, target: str, band: str = "") -> list[str]:
        """(Re)generate every file-config component's config file from the stored
        (per-band) values. Returns the paths written."""
        from pathlib import Path
        op = self.config().operator
        runtime = str(self._paths.runtime_root)
        cfg_band = self._config_band(target, band)

        def subst(text: str) -> str:
            return (text.replace("{callsign}", op.callsign or "N0CALL")
                        .replace("{locator}", op.locator or "")
                        .replace("{runtime}", runtime)
                        .replace("{band}", cfg_band))    # for per-band config keys

        values = self.file_config_values(target, band)
        written = []
        for c in self._file_config_components(target):
            fc = c.config_file
            src_dir = (self._paths.resolve_source(c.source.path) if c.source
                       else self._paths.runtime_root)
            out = fc.path.replace("{runtime}", runtime)
            out_path = Path(out) if out.startswith("/") else src_dir / out
            if fc.fmt in ("toml-update", "yaml-update"):
                base = Path(fc.base) if fc.base.startswith("/") else src_dir / fc.base
                try:
                    base_text = base.read_text(encoding="utf-8")
                except OSError:
                    continue   # base config not present — skip
                updater = update_toml if fc.fmt == "toml-update" else update_yaml
                text = updater(base_text, fc.params, values, subst)
            elif fc.fmt == "env":
                # KEY=value (no spaces, no header) for split-on-'=' parsers.
                text = render_keyval(fc.params, values, subst, sep="=", comment=False)
            else:
                text = render_keyval(fc.params, values, subst)
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(text, encoding="utf-8")
                written.append(str(out_path))
            except OSError:
                pass
        return written

    def reset_config(self, target: str, band: str = "") -> ActionResult:
        """Delete a stack/band's saved config so it reverts to the running defaults."""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        cfg_band = self._config_band(target, band)
        name = f"{target}@{cfg_band}.toml" if cfg_band else f"{target}.toml"
        path = self._paths.runtime_root / "config" / "stacks" / name
        existed = path.exists()
        if existed:
            path.unlink()
        label = f"'{target}'" + (f" ({cfg_band})" if cfg_band else "")
        return ActionResult(True,
                            f"Config reset to defaults for {label}." if existed
                            else f"{label} already at defaults.",
                            next_commands=[f"lhpc stack start {target}"])

    SOURCE_CHOICES = ("dev", "stable", "pinned")

    def run_action(self, op: str, target: str, apply: bool = False,
                   params: dict | None = None, source: str = "dev",
                   stop_owners: bool = False, cascade: bool = False,
                   band: str = "") -> ActionResult:
        """Dispatch a named action to its service method (plan when apply=False)."""
        if source not in self.SOURCE_CHOICES:
            source = "dev"
        ops = {
            "install": lambda: self.install(target, apply=apply, source=source),
            "update": lambda: self.update(target, apply=apply, source=source),
            "uninstall": lambda: self.uninstall(target, apply=apply),
            "start": lambda: self.start(target, apply=apply, params=params,
                                        stop_owners=stop_owners, band=band),
            "stop": lambda: self.stop(target, apply=apply, cascade=cascade, band=band),
            "restart": lambda: self.restart(target, apply=apply, params=params,
                                            stop_owners=stop_owners, band=band),
            "build": lambda: self.build(target, apply=apply),
            "repair": lambda: self.repair(target, apply=apply),
            "rollback": lambda: self.rollback(target, apply=apply),
            "test": lambda: self.test(target, apply=apply),
            "test-tx": lambda: self.test(target, tx=True, apply=apply),
        }
        fn = ops.get(op)
        if fn is None:
            return ActionResult(False, f"Unknown action '{op}'.",
                                next_commands=["lhpc help"])
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
        err = daemon_control.validate_set(key, value)
        if err:
            return ActionResult(False, f"Invalid setting: {err}",
                                next_commands=[f"lhpc daemon {band}"])
        if not apply:
            return ActionResult(
                True,
                f"Will apply SET {key.upper()}={value.upper()} to the {band} daemon (live).",
                details=["This changes live daemon behaviour (non-RF tuning).",
                         "It does not transmit by itself."],
                next_commands=[f"lhpc daemon {band} --set {key}={value} --yes"],
                data={"changes": 1})
        ok, detail = daemon_control.apply_set(self._system, band, key, value)
        return ActionResult(ok, f"SET {key.upper()}={value.upper()}: {'applied' if ok else 'FAILED'}.",
                            details=[detail], next_commands=[f"lhpc daemon {band}"])


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
               source: str = "dev") -> ActionResult:
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
            details = [f"  {c.id}: fetch newest from {c.source.remote or 'local checkout'}"
                       for _, c in items]
            return ActionResult(
                True, f"Update plan for '{target or 'all'}': refresh {len(items)} source(s) "
                "from GitHub (local fallback).",
                details=details,
                next_commands=[f"lhpc update {target} --yes"] if items else [],
                data={"changes": len(items)})
        inst = self._installer()
        out = []
        for _, c in items:
            r = inst.adopt_source(c, force=True, source=source)
            out.append(f"  [{r.status}] {c.id}: {r.detail}")
        return ActionResult(True, f"Update applied for '{target or 'all'}'.",
                            details=out, next_commands=["lhpc status --versions"])

    def repair(self, target: str, apply: bool = False) -> ActionResult:
        """Re-verify sources; re-adopt missing ones. Never resets a dirty tree."""
        items = self._with_source(target)
        if not items:
            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
        inst = self._installer()
        details, changes = [], 0
        for _, c in items:
            dest = self._paths.resolve_source(c.source.path)
            if not dest.exists():
                details.append(f"  [re-adopt] {c.id}: missing")
                changes += 1
            else:
                from .probes.source import probe_source
                state = probe_source(self._system, c.source, str(dest)).state.value
                verb = "ok" if state == "match" else f"report ({state})"
                details.append(f"  [{verb}] {c.id}")
        if not apply:
            return ActionResult(True, f"Repair plan for '{target or 'all'}': {changes} re-adopt(s).",
                                details=details,
                                next_commands=[f"lhpc repair {target} --yes"] if changes else [],
                                data={"changes": changes})
        out = []
        for _, c in items:
            if not self._paths.resolve_source(c.source.path).exists():
                r = inst.adopt_source(c)
                out.append(f"  [{r.status}] re-adopt {c.id}: {r.detail}")
        return ActionResult(True, f"Repair applied for '{target or 'all'}'.",
                            details=out or ["nothing to re-adopt"],
                            next_commands=["lhpc status " + (target or "")])

    def rollback(self, target: str, apply: bool = False) -> ActionResult:
        """Restore a managed copy to its confirmed-working profile commit via a
        non-destructive `git checkout` (never reset --hard). Linked/dirty trees
        are reported, not forced."""
        items = self._with_source(target)
        profiles = profiles_mod.load_profiles(self._paths)
        actions = []
        for _, c in items:
            prof = profiles.get(c.id)
            if not prof or not prof.commit:
                continue
            dest = self._paths.resolve_source(c.source.path)
            if dest.is_symlink():
                actions.append((c, dest, "report", "linked to working tree — roll back there manually"))
            elif not dest.exists():
                actions.append((c, dest, "report", "not installed — run lhpc install"))
            else:
                actions.append((c, dest, "checkout", prof.commit))
        if not actions:
            return ActionResult(False, f"No confirmed-working profile to roll back to for '{target}'.",
                                next_commands=["lhpc status --versions"])
        details = [f"  [{a[2]}] {a[0].id}: {a[3][:40]}" for a in actions]
        changes = sum(1 for a in actions if a[2] == "checkout")
        if not apply:
            return ActionResult(True, f"Rollback plan for '{target}': {changes} checkout(s).",
                                details=details,
                                next_commands=[f"lhpc rollback {target} --yes"] if changes else [],
                                data={"changes": changes})
        out = []
        for c, dest, kind, arg in actions:
            if kind != "checkout":
                out.append(f"  [skip] {c.id}: {arg}")
                continue
            res = self._system.runner.run(["git", "-C", str(dest), "checkout", arg], 10.0)
            ok = res.returncode == 0
            out.append(f"  [{'ok' if ok else 'fail'}] {c.id} -> {arg[:12]}"
                       + ("" if ok else f" ({(res.stderr or '').strip()[:60]})"))
        return ActionResult(True, f"Rollback applied for '{target}'.", details=out,
                            next_commands=["lhpc status --versions"])

    def uninstall(self, target: str, apply: bool = False) -> ActionResult:
        """Remove managed runtime sources for `target`. Preserves config, secrets
        and profiles by default."""
        items = self._with_source(target)
        if not items:
            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
        present = [(s, c) for s, c in items
                   if self._paths.resolve_source(c.source.path).exists()]
        details = [f"  [remove] src/{c.source.path.split('/')[-1]} ({c.id})" for _, c in present]
        details.append("  (config, secrets and profiles are preserved)")
        if not apply:
            return ActionResult(True, f"Uninstall plan for '{target or 'all'}': {len(present)} source(s).",
                                details=details,
                                next_commands=[f"lhpc uninstall {target} --yes"] if present else [],
                                data={"changes": len(present)})
        import shutil
        out = []
        for _, c in present:
            dest = self._paths.resolve_source(c.source.path)
            try:
                if dest.is_symlink():
                    dest.unlink()
                else:
                    shutil.rmtree(dest)
                out.append(f"  [removed] {c.id}")
            except OSError as exc:
                out.append(f"  [fail] {c.id}: {exc}")
        return ActionResult(True, f"Uninstall applied for '{target or 'all'}' (config preserved).",
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


def _pid_running(pid: int) -> bool:
    """True if pid exists and is not a zombie/dead (Linux /proc-based)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return False
    rparen = data.rfind(")")        # state char follows "(comm) "
    state = data[rparen + 2: rparen + 3] if rparen != -1 else ""
    return state not in ("Z", "X", "x")


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

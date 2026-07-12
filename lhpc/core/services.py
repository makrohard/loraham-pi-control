"""Shared application/service layer — the single entry point for all behaviour.

The CLI adapter and the web adapter both call ONLY this module, guaranteeing
identical validation, status interpretation and results. Read methods are bounded
and read-only; mutating methods print a plan and apply only when confirmed.

`build_snapshot()` is the single probing path; both `status()` (CLI text) and the
web adapter call it, so a page load and a CLI run see the same fresh evidence.
"""

from __future__ import annotations

import os
import shlex
import threading
import time
import contextlib
from contextlib import contextmanager
from pathlib import Path

from . import manifest as manifest_mod
from .config import (
    Config,
    ConfigError,
    conditional_clear_stack_config,
    load_config,
    load_stack_config,
)
from .install import Installer, Plan
from .lifecycle import Lifecycle
from .model import (
    ComponentKind,
    ResourceMode,
    RunState,
    Stack,
)
from .paths import Paths, PathContainmentError, resolve_paths
from .probes import RealSystem, System
from .probes import hardware
from .status import Snapshot, StatusProber, rollup_states, summarize

_SPI_DEV = "/dev/spidev0.0"
_GPIO_DEV = "/dev/gpiochip0"
_UNSET = object()                # sentinel: "not yet resolved" (distinct from None)


from .service_base import (ActionResult, ConfigWrite, SourceTxnBlocked, _StopRun,
                           _canon_git_url, _proc_ceased, _proc_start_time)

# Public import surface (the adapters + tests import these names FROM lhpc.core.services). Listing
# them in __all__ also marks the re-exports above as intentionally exported, so a name whose only
# in-module users have moved to a service_* mixin is not reported as an unused import.
__all__ = ["ControllerService", "ActionResult", "ConfigWrite", "SourceTxnBlocked",
           "_StopRun", "_canon_git_url", "_proc_start_time", "_proc_ceased"]

# A SHARED config-stability acquire (start/restart) waits at most this long before a typed refusal: long
# enough to sail past an ordinary EXCLUSIVE config SAVE (milliseconds), short enough that it does NOT hang
# for the whole install-all run when the bulk boundary holds config EXCLUSIVE — it refuses instead.
_CONFIG_STABLE_SHARED_TIMEOUT_S = 3.0


from .service_webserver import WebserverOpsMixin


from .service_selfupdate import SelfUpdateOpsMixin


from .service_bulk import BulkOpsMixin


from .service_maintenance import MaintenanceOpsMixin


from .service_params import ParamsConfigMixin


from .service_lifecycle_ops import LifecycleOpsMixin


from .service_hmac import HmacOpsMixin


class ControllerService(WebserverOpsMixin, BulkOpsMixin, SelfUpdateOpsMixin, MaintenanceOpsMixin, ParamsConfigMixin, LifecycleOpsMixin, HmacOpsMixin):
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
        self._controller = _UNSET       # controller spec (None = none declared); lazy
        self._config: Config | None = None
        # The config cache is shared by the (threaded) web app; guard it so a save on one
        # thread is visible to the next read on any thread (no stale callsign/remote).
        self._config_lock = threading.RLock()
        self._config_mtime = None               # local.toml mtime the cache was built from
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
    def _config_stable(self, exclusive: bool = False):
        """Hold saved configuration STABLE on the runtime config lock file. Two modes:
          * SHARED (default) — a read lock for the duration of an applied lifecycle transition. Config
            MUTATIONS take the EXCLUSIVE `config_lock`, so a concurrent save WAITS for the transition and a
            start WAITS for an in-progress save; independent starts share and never serialise.
          * EXCLUSIVE — the install-all bulk boundary holds `LOCK_EX` for the WHOLE run, so an atomic config
            write inside the boundary reuses this held lock (see `save_config_bundle`) instead of contending
            on a second descriptor. Acquired BOUNDED (a bulk run must not hang on a stuck holder) → typed
            `SourceTxnBlocked` on timeout.
        RE-ENTRANT per thread. The OUTERMOST entry FIXES the mode and holds it UNCHANGED — nested entries are
        depth-only and NEVER convert it (SH↔EX conversion is not atomic on Linux). Nested exclusive-under-
        exclusive and shared-under-exclusive are allowed; a nested EXCLUSIVE beneath a SHARED guard is
        REJECTED (a config mutation must never run under a shared stability guard). LOCK ORDER: acquired
        BEFORE any lifecycle/resource lock; a failure raises here so the caller fails typed with no side
        effect. Thread-local mode/fd state is cleared on the outermost exit, including exceptional exits."""
        import fcntl
        from . import runtime_fs
        st = self._cfg_stable_state
        depth = getattr(st, "depth", 0)
        if depth == 0:
            fh = runtime_fs.open_lock(self._paths, self._paths.under("config", ".lock"))
            try:
                # BOTH modes acquire BOUNDED (LOCK_NB + poll) → typed busy, so a caller NEVER hangs
                # indefinitely: a SHARED reader (start/restart) that meets a long-running EXCLUSIVE holder
                # (the install-all boundary) is REFUSED after a short wait rather than blocking for the
                # whole run; the EXCLUSIVE acquirer (the boundary) waits the full config timeout for
                # in-flight SHARED transitions to finish. Uncontended acquisition succeeds immediately.
                from .config import CONFIG_LOCK_TIMEOUT_S, _CONFIG_LOCK_POLL_S
                lock_op = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
                timeout = CONFIG_LOCK_TIMEOUT_S if exclusive else _CONFIG_STABLE_SHARED_TIMEOUT_S
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fh, lock_op)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise SourceTxnBlocked(
                                "config-stability lock is busy — a long-running config operation "
                                "holds it; try again shortly")
                        time.sleep(_CONFIG_LOCK_POLL_S)
            except BaseException:
                fh.close()
                raise
            st.fh = fh
            st.exclusive = exclusive
        elif exclusive and not getattr(st, "exclusive", False):
            # Never convert a held SHARED guard to EXCLUSIVE; a config mutation must not run beneath it.
            raise RuntimeError("config-stability: EXCLUSIVE requested beneath a SHARED guard")
        st.depth = depth + 1
        try:
            yield
        finally:
            st.depth -= 1
            if st.depth == 0:
                try:
                    try:
                        fcntl.flock(st.fh, fcntl.LOCK_UN)
                    finally:
                        st.fh.close()
                finally:
                    st.fh = None
                    st.exclusive = False

    def _holds_config_exclusive(self) -> bool:
        """True iff THIS thread currently holds the config-stability guard in EXCLUSIVE mode — the only
        state in which a config write may reuse the held lock (see `save_config_bundle`)."""
        st = self._cfg_stable_state
        return getattr(st, "depth", 0) > 0 and getattr(st, "exclusive", False)

    # ---- config / installer ---------------------------------------------

    def config(self) -> Config:
        with self._config_lock:
            # AUDIT CC4: reload when local.toml's mtime changed since the cache was built.
            # A long-lived web process otherwise served a stale callsign/remotes forever
            # after an out-of-band hand-edit (a scenario the loader explicitly supports),
            # and an in-lock plan could verify identity against the wrong effective remote.
            mtime = self._local_config_mtime()
            if self._config is None or mtime != self._config_mtime:
                self._config = load_config(self._paths)
                self._config_mtime = mtime
            return self._config

    def _local_config_mtime(self):
        try:
            return os.stat(self._paths.runtime_root / "config" / "local.toml").st_mtime
        except OSError:
            return None

    def web_session_secret(self) -> bytes:
        """The persistent web-console session secret (generated once, 0600, survives restart;
        not cleared by 'Reset to default'). Thin delegation to config — the web adapter calls
        this instead of reaching into runtime paths."""
        from . import config as _config
        return _config.web_session_secret(self._paths)

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
        Raises `reslock.ResourceBusy` if the index or a source lock is contended.

                RE-ENTRANT per THREAD (shared `_held_counts` with the lifecycle guard): a source key
        already held by an OUTER boundary in this thread — e.g. the bulk-operation lease —
        is not re-flocked, the index/recovery step is skipped for fully-covered nests (the
        outer boundary performed it and holds the locks, so no foreign journal can appear
        for a covered path), and a nested exit never releases the outer flocks. Independent
        threads/processes contend through `reslock` unchanged."""
        from . import reslock
        inst = self._installer()
        keys = sorted({reslock.source_lock_key(sp) for sp in source_paths})
        counts = self._held_counts()
        missing = [k for k in keys if counts.get(k, 0) == 0]
        bumped: list = []
        try:
            with contextlib.ExitStack() as src_stack:
                if missing:
                    # Index held across recovery + the source-lock handoff, then released.
                    with reslock.operation_lock(self._paths, inst._index_key(), op, ""):
                        inst._recover_scan()
                        if inst._pending_journals():
                            raise SourceTxnBlocked(
                                "an unresolved source-transaction journal is present — "
                                "resolve it before any source operation")
                        for k in missing:
                            self._acquire_key(src_stack, k, op, "")
                # Index released; source lock(s) remain held by src_stack for the operation.
                for k in keys:
                    counts[k] = counts.get(k, 0) + 1
                    bumped.append(k)
                yield
        finally:
            for k in bumped:
                counts[k] -= 1
                if counts[k] <= 0:
                    counts.pop(k, None)

    # ---- bulk run: status, gates, log, ack, spawn, driver (M2.1) -----------

    BULK_OP = "install-all"

    # Keep in sync with COMPLOG_MAX in static/bulk.js (the live window's scrollback cap, 1.5 MB):
    # a historical seed must not be larger than what the live view would keep.
    _COMPLOG_SEED_MAX_BYTES = 1_500_000
    _COMPLOG_SEED_MAX_READS = 512            # hard iteration bound (normal runs drain in <10 reads)

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

    def controller(self):
        """LHPC's own checkout as a dedicated controller identity (or None). Parsed via
        the SEPARATE `load_controller` accessor — never through stack machinery."""
        if self._controller is _UNSET:
            self._controller = manifest_mod.load_controller(self._manifest_path)
        return self._controller

    def _controller_deps_sync_cmd(self) -> str:
        """The EXACT editable-install command for the self-hosted controller after a
        `deps_changed` update: the DEPLOYMENT interpreter (`<root>/venv/lhpc/bin/python`)
        against the controller CHECKOUT (`<root>/<source_path>`), shell-quoted so a path
        with spaces/metacharacters is safe to paste. Empty when no controller is declared —
        the caller then falls back to the dev `pip install -e .`."""
        spec = self.controller()
        if spec is None:
            return ""
        root = self._paths.runtime_root
        python_bin = root / "venv" / "lhpc" / "bin" / "python"
        checkout = root.joinpath(*Path(spec.source_path).parts)
        return (f"{shlex.quote(str(python_bin))} -m pip install -e "
                f"{shlex.quote(str(checkout))}")

    def _controller_refusal(self, target) -> "ActionResult | None":
        """CENTRAL guard: a generic verb (install/update/uninstall/clean/build/test/
        start/stop) targeting the controller id returns a typed refusal BEFORE any target
        resolution or mutation. The CLI/web adapters only RENDER this — they hold no guard
        logic of their own. `lhpc update <controller-id>` is NOT an alias for self-update."""
        c = self.controller()
        if c is not None and target == c.id:
            return ActionResult(
                False, "LHPC's own checkout is controller-managed. Use: lhpc self-update",
                next_commands=["lhpc self-update"])
        return None

    def controller_identity_live(self) -> dict:
        """LIVE controller-identity proof (git subprocesses) — used ONLY at startup
        refresh, explicit "check now", and immediately before self-update apply. Returns a
        TRI-STATE verdict `{checked_at, status, ok, reason}` where `status` is:
          * `not_applicable` — the deployment is NOT self-hosted (lhpc does not run from the
            in-root `src/loraham-pi-control` checkout: a bootstrap-only root, a plain/dev
            install, etc.). NEUTRAL, not a failure — self-update proceeds via the normal
            `repo_root()` mechanism and apply is NOT blocked.
          * `unsafe` — the deployment IS self-hosted but the in-root checkout/layout is
            tampered/misconfigured (symlink leaf, group/other-writable, wrong branch/origin,
            repo/package mismatch). Apply IS blocked.
          * `ok` — self-hosted and every strict check passed.
        `ok` is the boolean `status == "ok"` for callers that only care about the green path.

        The strict (self-hosted) path is STRICTER than managed-source resolution
        (`resolve_source` permits a symlink to an external checkout; here that would let the
        deployment silently run from an outside tree). It is a detection boundary, NOT a
        same-account race-proof guarantee."""
        import stat as _stat

        import lhpc as _lhpc

        from . import selfupdate as _su
        now = int(time.time())

        def verdict(status: str, reason: str) -> dict:
            return {"checked_at": now, "status": status, "ok": status == "ok",
                    "reason": reason[:200]}

        spec = self.controller()
        if spec is None:
            return verdict("not_applicable", "no controller declared")
        if spec.source_path != manifest_mod.CONTROLLER_SOURCE_PATH:
            return verdict("unsafe", "controller source_path is not the fixed value")
        try:
            checkout = self._paths.under(*Path(spec.source_path).parts)   # contained, no-follow
        except PathContainmentError as exc:
            return verdict("unsafe", f"source path escapes runtime root ({exc})")

        # SELF-HOSTED? lhpc must actually run FROM the in-root checkout. If the checkout is
        # absent, or lhpc runs from a DIFFERENT tree (dev checkout / plain install / tangled
        # root), the controller-identity boundary does not apply -> NEUTRAL. This is the
        # common case for a bootstrap-only or non-migrated deployment and must NOT read as a
        # security failure or block self-update.
        repo = _su.repo_root()
        real_checkout = os.path.realpath(checkout)
        if repo is None or not os.path.exists(checkout):
            return verdict("not_applicable",
                           "not self-hosted: no controller checkout under the runtime root")
        if os.path.realpath(repo) != real_checkout:
            return verdict("not_applicable",
                           "not self-hosted: lhpc runs from a different checkout")

        # The deployment IS self-hosted -> strict tamper checks. A failure now is UNSAFE.
        root = self._paths.runtime_root
        for label, pth in (("runtime root", root), ("src", root / "src"),
                           ("checkout", checkout)):
            try:
                st = os.lstat(pth)
            except OSError:
                return verdict("unsafe", f"{label} is missing")
            if _stat.S_ISLNK(st.st_mode):
                return verdict("unsafe", f"{label} is a symlink (fixed layout required)")
            if not _stat.S_ISDIR(st.st_mode):
                return verdict("unsafe", f"{label} is not a directory")
            if st.st_uid != os.getuid():
                return verdict("unsafe", f"{label} not owned by the service user")
            if st.st_mode & 0o022:
                return verdict("unsafe", f"{label} is group/other-writable")
        real_root = os.path.realpath(root)
        if not (real_checkout == real_root or real_checkout.startswith(real_root + os.sep)):
            return verdict("unsafe", "checkout realpath escapes the runtime root")
        if os.path.realpath(str(Path(_lhpc.__file__).resolve().parents[1])) != real_checkout:
            return verdict("unsafe", "imported package repo != controller checkout")
        g = _su._git(self._system, Path(real_checkout), ["rev-parse", "--is-inside-work-tree"], 10.0)
        if g.returncode != 0 or g.stdout.strip() != "true":
            return verdict("unsafe", "not a git checkout")
        b = _su._git(self._system, Path(real_checkout), ["rev-parse", "--abbrev-ref", "HEAD"], 10.0)
        head_branch = b.stdout.strip() if b.returncode == 0 else ""
        if head_branch == "HEAD":
            return verdict("unsafe", "checkout is in detached HEAD")
        if head_branch != spec.branch:
            return verdict("unsafe", f"checkout branch {head_branch!r} != {spec.branch!r}")
        o = _su._git(self._system, Path(real_checkout), ["config", "--get", "remote.origin.url"], 10.0)
        origin = o.stdout.strip() if o.returncode == 0 else ""
        canon_spec = _canon_git_url(spec.remote)
        # Reject an EMPTY canonical on either side: a degenerate manifest remote (".git",
        # "/", "https://") canonicalizes to "" and would otherwise match a checkout with NO
        # origin (also "") — a false-accept. A valid, non-empty canonical must match.
        if not canon_spec or _canon_git_url(origin) != canon_spec:
            return verdict("unsafe", "origin is not the approved canonical remote")
        return verdict("ok", "identity ok")

    @property
    def runtime_root(self):
        """Absolute runtime installation root (display/resolution use)."""
        return self._paths.runtime_root

    # ---- the single probing path (used by CLI and web) -------------------

    def build_snapshot(self) -> Snapshot:
        """Fresh, bounded, read-only assessment of every stack. No caching. The
        confirmed-working map comes from the OPERATOR-CONFIRMED known-working compositions
        (file reads only): a component is confirmed-working when its clean source HEAD appears
        in a stored composition of its stack."""
        from . import known_working
        confirmed: dict = {}
        for s in self.stacks():
            comps = known_working.load(self._paths, s.id)
            for comp in comps:
                for cid, entry in comp["entries"].items():
                    if entry.get("commit"):
                        confirmed.setdefault(cid, set()).add(entry["commit"])
        return StatusProber(self._system, self._paths, confirmed).assess_stacks(self.stacks())

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
        flagged = [sid for sid in self.restart_required_stacks()
                   if not stack_id or sid == stack_id]
        if flagged:
            details.append("")
            for sid in flagged:
                marker = self.restart_required(sid) or {}
                if marker.get("unsafe"):
                    details.append(f"  ! RESTART REQUIRED (safe-side): '{sid}' — "
                                   f"{marker.get('reason', 'marker unreadable')}")
                else:
                    details.append(f"  ! RESTART REQUIRED: '{sid}' — saved settings differ "
                                   f"from the running stack (lhpc stack stop {sid} && "
                                   f"lhpc stack start {sid})")
        if not snap.runtime_root_exists:
            details.append("")
            details.append(
                "Note: runtime root not installed; managed sources report "
                "'not-installed' (expected before install)."
            )
        # Controller row — a DISTINCT non-stack entity (LHPC's own checkout). Cached-only
        # (no git/network/live check here); managed only via `lhpc self-update`.
        if not stack_id:
            cs = self.controller_status()
            if cs is not None:
                details.append("")
                idv = cs.get("identity")
                st_id = (idv or {}).get("status")
                if idv is None:
                    ident = "identity unchecked"
                elif st_id == "ok":
                    ident = "identity ok"
                elif st_id == "unsafe":
                    ident = f"identity UNSAFE ({idv.get('reason', '')})"
                else:
                    ident = "not self-hosted"
                upd = "update available" if cs["update_available"] else "up to date"
                head = f"@{cs['head_short']}" if cs["head_short"] else ""
                details.append(f"[controller] {cs['display_name']}  ({upd})")
                details.append(f"  v{cs['version']} {head}  {ident}  — manage with: "
                               f"{cs['self_update_cmd']}")
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
        # Copyable install/grant commands for every UNSATISFIED dep (mandatory OR optional), collected
        # here and printed as one block at the VERY END. Shell commands only — `lhpc install`/`build`
        # action entries are NOT shell commands and are excluded.
        install_cmds: list[str] = []

        def _add_cmd(cmd: str) -> None:
            if cmd and cmd not in install_cmds and not cmd.startswith(("lhpc install", "lhpc build")):
                install_cmds.append(cmd)

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
        # Controller's OWN system/runtime deps (same source as the /stacks System-dependencies panel).
        # A missing REQUIRED dep makes doctor non-OK (machine-actionable); optional ones never do.
        required_missing = False
        for grp in self.controller_system_deps():
            for d in grp["deps"]:
                if d["satisfied"]:
                    state = "present"
                elif d["required"]:
                    required_missing = True
                    state = f"MISSING — {d['install']}" if d["install"] else "MISSING"
                else:
                    hint = f": {d['install']}" if d["install"] else ""
                    state = f"not installed (optional — {d['purpose']}{hint})"
                if not d["satisfied"]:
                    _add_cmd(d["install"])
                details.append(f"  {d['what']}: {state}")
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

        # Itemized UNMET dependencies per stack (grouped): system prerequisites carry the
        # exact operator command — LHPC never installs system packages itself.
        from . import deps as deps_mod
        any_missing = False
        for s in self.stacks():
            groups = self.deps_report(s.id)
            unmet = [d for d in groups["system"] + groups["build"] if not d.satisfied]
            if not unmet:
                continue
            any_missing = True
            details.append(f"  {s.id}: unmet dependencies")
            for d in unmet:
                line = f"    [{d.kind}] {d.label} — {d.detail}"
                if d.install_cmd:
                    line += f" | run yourself: {d.install_cmd}"
                    _add_cmd(d.install_cmd)
                details.append(line)
        if not any_missing:
            details.append("  dependencies: all declared system/build prerequisites satisfied")
        details.append(f"  ({deps_mod.NOT_EXECUTED_NOTE})")

        # Run-state tally from a fresh snapshot.
        snap = self.build_snapshot()
        tally: dict[str, int] = {}
        for ss in snap.stacks:
            for st in ss.components.values():
                tally[st.run_state.value] = tally.get(st.run_state.value, 0) + 1
        details.append("  components: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        observed = self._observed_conflicts(snap)
        details.append(f"  observed resource conflicts: {len(observed)}")

        # Consolidated, copyable install/grant commands for everything unsatisfied — at the very end.
        if install_cmds:
            details.append("Install the missing dependencies:")
            details.extend(f"  {cmd}" for cmd in install_cmds)

        return ActionResult(
            ok=not required_missing,
            summary=("doctor: required dependencies missing; bounded local checks only "
                     "(no init, no network, no RF)." if required_missing
                     else "doctor: bounded local checks only (no init, no network, no RF)."),
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
                source: str = "pinned", bulk_ctx=None) -> ActionResult:
        if (_r := self._controller_refusal(stack_id)) is not None:
            return _r
        if stack_id and self.stack(stack_id) is None:
            return self._unknown_stack(stack_id)
        if not self._paths.runtime_root_exists:
            return ActionResult(
                ok=False,
                summary="Runtime root is not bootstrapped yet.",
                details=[f"Run 'lhpc bootstrap' to create {self._paths.runtime_root}."],
                next_commands=["lhpc bootstrap"],
            )
        # SHARED-SOURCE REMOTE COHERENCE gates BOTH planning and mutation: one checkout is
        # one clone with ONE effective remote — a legacy divergent per-component override
        # blocks install with ZERO candidate/source/registry/config mutation.
        planned_paths = sorted({c.source.path for st in self.stacks()
                                if not stack_id or st.id == stack_id
                                for c in st.components if c.source})
        conflicts = sorted({c for c in (self._shared_remote_conflict(p)
                                        for p in planned_paths) if c})
        if conflicts:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                "shared-source remote configuration is inconsistent.",
                                details=[f"  {c}" for c in conflicts])
        inst = self._installer()
        plan = inst.plan_install(stack_id)
        if not apply:
            cmd = f"lhpc install {stack_id} --yes" if stack_id else "lhpc install --yes"
            return self._plan_result(plan, applied=False, next_apply=cmd)
        from . import source_fs
        # ONE adoption per coherent source GROUP: each shared path is installed exactly once
        # (deterministic first declarer), never opportunistically re-attempted through
        # whichever consumer is encountered next.
        # ONE immutable plan for the whole install: known-working frozen per stack, one
        # adoption per shared source group, incompatible resolutions blocked up front.
        install_items = [(st, c) for st in self.stacks()
                         if not stack_id or st.id == stack_id
                         for c in st.components if c.source]
        ctx_err = self._bulk_ctx_error(bulk_ctx,
                                       {c.source.path for _, c in install_items})
        if ctx_err:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                f"{ctx_err}")
        groups, plan_conflicts = self._plan_source_groups(install_items, source)
        if plan_conflicts:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                "incompatible source resolutions for a shared checkout.",
                                details=[f"  {c}" for c in plan_conflicts])
        mutated_paths, extra_out = [], []
        for path, comp, resolved in groups:
            dest = self._paths.resolve_source(path)
            # DESCRIPTOR-PROVEN skip: only a healthy managed DIRECTORY is "already
            # installed". Anything else (absent, symlink, regular/special file) flows
            # into `adopt_source`, whose locked leaf checks install or refuse typed —
            # a dangling/unknown leaf is never silently treated as installed.
            try:
                if source_fs.leaf_kind(self._paths, dest) == "dir":
                    # HEALTHY SKIP: the leaf already serves this install. RE-JOIN the
                    # targeted consumers in the ownership record's live membership —
                    # otherwise a later sibling departure could remove a leaf this
                    # just-installed stack relies on.
                    from . import source_registry as _sreg
                    state, rec, _w = _sreg.record_state(self._paths, path)
                    targeted = {c2.id for _, c2 in install_items
                                if c2.source and c2.source.path == path}
                    if state == "valid" and not targeted <= set(rec.components):
                        if _sreg.update_components(self._paths, path,
                                                   set(rec.components) | targeted):
                            extra_out.append(f"  [re-joined] {path}: shared checkout now "
                                             "serves this stack again")
                        else:
                            extra_out.append(f"  [warn] {path}: shared-consumer record "
                                             "could not be updated — re-run install")
                    continue
            except PathContainmentError:
                pass                       # unsafe parent -> adopt_source refuses typed
            st_of = next((st2 for st2 in self.stacks()
                          if any(c2.id == comp.id for c2 in st2.components)), None)
            result = self._adopt_dev_fallback(inst, st_of, comp, source, resolved,
                                              force=False,
                                              locked=bulk_ctx is not None)
            if result.status == "done":
                mutated_paths.append(path)
            for a in plan.actions:
                if a.target == str(dest):
                    a.status, a.detail = result.status, result.detail
                    a.provenance = result.provenance
        # Password-auth by DEFAULT: after a successful source adoption and BEFORE the caller builds the
        # firmware, ensure the meshcom HMAC secret + param exist (idempotent — keeps an existing secret),
        # so the firmware bakes the shared secret in the same install. Covers per-stack + CLI install;
        # install-all adopts+builds directly (its own hook, before its build). Skip on a failed adopt —
        # nothing gets built, so don't flip visible HMAC state on a broken install.
        hmac_err = ""
        if not any(a.status == "failed" for a in plan.actions):
            for sid in {st.id for st, _ in install_items}:
                if self.hmac_applies(sid):
                    hr = self.hmac_set_secret(sid, "enable")
                    if not hr.ok:
                        # FAIL CLOSED: a failed enable must NOT report install success — the firmware would
                        # otherwise be built (by the caller) with an empty password while the operator
                        # believes auth is on.
                        hmac_err = f"{sid}: {self._hmac_redact(hr.summary)}"
                        break
        retire_ok = self._retire_candidates_for_paths(mutated_paths, extra_out)
        res = self._plan_result(plan, applied=True, next_apply=None)
        if hmac_err:
            return ActionResult(False, res.summary + " — but the HMAC password could NOT be enabled "
                                f"({hmac_err}); fix and re-run before starting the meshcom link.",
                                details=list(res.details) + extra_out,
                                next_commands=res.next_commands)
        if not retire_ok:
            return ActionResult(False, res.summary + " (candidate cleanup INCOMPLETE)",
                                details=list(res.details) + extra_out,
                                next_commands=res.next_commands)
        return res

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

    DAEMON_ID = "loraham-daemon"
    # After auto-starting the daemon, wait up to this long for its CONF socket to
    # answer before reporting success (the daemon inits the radio asynchronously).
    DAEMON_VERIFY_TIMEOUT_S = 4.0
    DAEMON_VERIFY_POLL_S = 0.5
    # For readiness="endpoint": wait up to this long for every ready=true endpoint.
    ENDPOINT_VERIFY_TIMEOUT_S = 6.0
    ENDPOINT_VERIFY_POLL_S = 0.3

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

    # Bounded log retention (no background supervisor — runs at operation boundaries).
    LOG_RETENTION = 200          # keep at most this many *.log files
    LOG_RETENTION_BYTES = 64 * 1024 * 1024   # …and at most this many bytes total

    # A `.job` marker is a tiny TOML (pid + identity fields); anything larger is untrusted
    # diagnostic evidence, never read in full or treated as a live job.
    _JOB_MARKER_MAX = 64 * 1024

    # ---- unified action dispatch (used by the web control interface) -----

    # Web-exposed actions -> the same gated service methods the CLI calls.
    WEB_ACTIONS = ("install", "update", "uninstall", "start", "stop", "restart",
                   "build", "test", "test-tx", "clean")

    def _run_migration(self, candidates: list, from_head: str) -> tuple:
        """Migrate one PROVEN transition's candidates race-safely. `from_head` is the TRANSITION
        record's pre-update commit; a key is deleted ONLY when its current stored value — canonicalised
        with the OLD (pre-update) parameter definition — equals that param's OLD default, both parsed
        from the manifest at `from_head` (`_prove_candidate`). The candidate's own `from_head`/`expected`
        never select the manifest or authorise deletion. Returns (migrated_count, remaining_candidates);
        an unprovable candidate is kept pending (never raw-value-deleted); a file whose write FAILS
        keeps all its candidates for retry."""
        from collections import defaultdict
        from .paths import PathContainmentError
        by_file: dict = defaultdict(dict)                                # (stack, band) -> {key: old_default}
        meta: dict = {}                                                  # (stack, band, key) -> (cand, old_param)
        remaining: list = []
        for cand in candidates:
            proven = self._prove_candidate(cand, from_head)
            if proven is None:
                remaining.append(cand)                                   # unprovable -> keep pending, never delete
                continue
            old_param, old_default = proven
            by_file[(cand["stack"], cand["band"])][cand["key"]] = old_default
            meta[(cand["stack"], cand["band"], cand["key"])] = (cand, old_param)
        migrated = 0
        for (stack_id, cfg_band), expected in by_file.items():
            def _matches(key, raw, exp, _s=stack_id, _b=cfg_band):
                _cand, old_param = meta[(_s, _b, key)]
                return self._canon_value(old_param, raw) == exp          # BOTH sides: OLD param semantics
            try:
                migrated += conditional_clear_stack_config(self._paths, stack_id, cfg_band,
                                                           expected, _matches)
            except (OSError, ConfigError, PathContainmentError, ValueError):
                remaining.extend(meta[(stack_id, cfg_band, k)][0] for k in expected)   # keep pending
        if migrated:
            self._invalidate_config()
        return migrated, remaining

    RADIO_BANDS = ("433", "868")

    # -- Start-confirm "Stack parameters" panel + CALL/node enforcement ----------

    # A run/file param whose validator marks it the stack's operator identity: a "callsign"
    # validator => LICENSED (refuse empty / N0CALL); a "node" validator => UNLICENSED (refuse only
    # empty, the default name is accepted).
    _IDENTITY_ENFORCE = {"callsign": "licensed", "node": "unlicensed"}

    SOURCE_CHOICES = ("pinned", "dev", "stable")   # pinned = production-safe default

    # ============================================================================================
    # One-click self-update — ESCAPE-PROOF trigger. The running console cannot mutate its own code
    # (it holds the controller-runtime lock SHARED), and it has NO user-systemd bus (its unit
    # InaccessiblePaths=%t/bus %t/systemd/private). So the web writes an in-root request marker
    # under EXCLUSIVE admission; a static lhpc-selfupdate.path unit starts the sandboxed helper;
    # web stop/restart is declarative (Conflicts/After/OnSuccess/OnFailure). NOTHING here calls
    # systemctl except the OPERATOR-shell repair/recover ops. See lhpc/core/updater_units.py.
    # ============================================================================================
    _PIP_SYNC_TIMEOUT_S = 600.0
    _LOCK_WAIT_S = 30.0

    # Feed scan window. The RX/TX lines are a SMALL fraction of the daemon's stdout, so we must
    # scan far more than we display and filter FIRST — tailing 400 lines and filtering afterwards
    # made "recent" mean "within the last 400 log lines", not "recent in time", and a chatty igate
    # (beacons + digipeat + RX) evicted a seconds-old TX while a quiet chat kept it for minutes.
    # Bounded + no-follow; ~200 KB typical per 3 s poll, which stays cheap on a Pi.
    _FEED_SCAN_LINES = 2000
    _FEED_SCAN_BYTES = 512 * 1024

    _VERSION_TAG_RE = None

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

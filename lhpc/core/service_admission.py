"""ControllerService mixin: task admission and the resource locks (keys, guards, the lock order).

Adapters import lhpc.core.services only. Split from services.py unchanged."""
from __future__ import annotations

import contextlib
import os
import time
from contextlib import contextmanager

from .daemon_control import ALLOWED_BANDS
from .model import ResourceMode
from .service_base import AdmissionRefused, SourceTxnBlocked


class AdmissionOpsMixin:

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
        already held by an OUTER boundary in this thread — e.g. the auto-install-operation lease —
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
                # Lock order #1: task admission, held across the whole source mutation (build/test/
                # install/update/uninstall/clean). Reentrant, so a source op nested inside an admitted
                # auto-install reuses the held lock.
                # The target names the sources, so a contender reads WHAT is busy (it said '').
                target = ", ".join(sorted(source_paths))
                self._admit(src_stack, op, target)
                if missing:
                    # Index held across recovery + the source-lock handoff, then released.
                    with reslock.operation_lock(self._paths, inst._index_key(), op, target):
                        inst._recover_scan()
                        if inst._pending_journals():
                            raise SourceTxnBlocked(
                                "an unresolved source-transaction journal is present — "
                                "resolve it before any source operation")
                        for k in missing:
                            self._acquire_key(src_stack, k, op, target)
                # OUTERMOST non-auto-install source op: with the source locks now held, recheck the
                # auto-install gate and REFUSE if a run is running/interrupted/UNSAFE. This closes the
                # window where an `unsafe` auto-install has released its locks but a process may still
                # hold the checkout. Nested ops UNDER the auto-install boundary (`missing` empty, fully
                # covered) skip this, so the run never self-blocks; the run's own op is `auto-install`.
                if missing and op != "auto-install":
                    ai_block = self._auto_install_gate()
                    if ai_block:
                        raise SourceTxnBlocked(f"an auto-install run needs recovery first — {ai_block}")
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

    def _web_source_precheck(self, source_paths) -> str:
        """Advisory probe for the web build/test/install admission: `""` if a detached source op could
        start right now, else the typed 'blocked' reason. Reuses the ONE tested boundary
        (index → recover/verify-no-journal → source locks) then releases — it NEVER spawns or mutates
        source. The detached child re-acquires authoritatively for its lifetime; the tiny release→respawn
        gap is closed by the admission handshake, not relied on for correctness."""
        if not source_paths:
            return ""
        from . import reslock
        try:
            with self._source_operation_guard(sorted(source_paths), op="web-precheck"):
                pass
            return ""
        except SourceTxnBlocked as exc:
            return str(exc)
        except reslock.ResourceBusy as exc:
            return f"a source operation is already in progress ({getattr(exc, 'key', '')})"

    def _operation_bands(self, target: str, band: str = "", radio: str = "",
                         op: str = "") -> set:
        """THE authoritative radio band(s) a lifecycle op on `target` touches — one source of truth
        for radio locking + conflict detection.
          * START  — the REQUESTED bands (client: its chosen/declared band; daemon: `radio`, else
                     the saved daemon `radio`). Never inferred from the daemon's empty Component.band.
          * STOP   — the ACTUAL running bands: a client uses its running/interactive MARKER (falling
                     back to the declared band only when there is NO runtime evidence); the daemon
                     uses PROCESS TOPOLOGY — a per-band stop also locks the other band when the SAME
                     process serves it (a dual-band value), and a whole-daemon stop locks
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
                if band in ALLOWED_BANDS:
                    bands = {band}
                    other = "868" if band == "433" else "433"
                    # dual-band collateral: the SAME process also serves the other band -> lock
                    # it too, regardless of that band's CONF socket state (topology, not reachability).
                    if set(self._daemon_pids_for_band(band)) & set(self._daemon_pids_for_band(other)):
                        bands.add(other)
                    return bands
                # Whole-daemon stop: every band an owned/observed daemon PROCESS claims.
                return self._daemon_claimed_bands()
            # START: clamp to the active radio mode (M-1) — never lock/serve an excluded band, never
            # 'both'. A serve-all start is ALSO arbitrated away from bands a running radio-direct stack
            # owns, so this single source of truth for the conflict set + lock set matches what the
            # daemon will actually serve (it starts the free band(s), not the owned one).
            # An EMPTY requested radio means "serve every active band after direct-owner
            # arbitration" (one process per band) — exactly what _ensure_daemon launches. NEVER
            # fall back to the saved/manifest default of the daemon's `radio` run param: its
            # manifest default is a single band, which UNDER-LOCKED a dual-band serve-all start
            # (locked 433 while both bands were launched). Enforced by
            # test_daemon_all_active_start_locks_every_band (tests/test_boot_restore.py).
            kept, _ = self._daemon_arbitrated_bands(radio or "")
            return set(kept)
        # Client.
        if op == "stop":
            eb = self._effective_band(sid, "")        # ACTUAL running band (marker/interactive)
            if eb in ALLOWED_BANDS:
                return {eb}
        # A LIVE multi-band owner with NO band evidence must claim EVERY band it could be
        # on. Falling back to the declared primary here meant a node actually running on
        # 433 with a lost/unreadable marker was arbitrated as an 868 owner — and a second
        # exclusive 433 owner could then be admitted onto the same radio. Unknown is not
        # the default band; unknown is all of them.
        allowed = self.stack_bands(sid) if sid else ()
        if len(allowed) > 1 and not self._effective_band(sid, "") \
                and self._band_owner_is_up(sid):
            return set(allowed)
        cfg_band = self._config_band(target, band)    # declared/default (start, or stop w/o evidence)
        return {cfg_band} if cfg_band else {c.band for _, c in order if c.band}

    def _operation_resource_keys(self, target: str, band: str = "", radio: str = "",
                                 op: str = "") -> list[str]:
        """Canonical resource keys an EXCLUSIVE/PROVIDER operation on `target` touches —
        the basis for cross-stack operation locks so a start/stop/restart of one stack
        serializes against another stack claiming the SAME radio/port/socket. Radio claims
        are scoped by `_operation_bands` (band-aware, daemon-radio-aware). Mirrors `run_blockers`
        so the lock set equals the conflict set, except that ADVISORY claims still lock: they
        never block a start, but two operations on the same slot still run one after the other.
        CONSUMER/COOPERATIVE claims take no lock."""
        order = self._run_order(target)
        if not order:
            return []
        keys = set()
        # PER-BAND daemon claims are scoped by `_operation_bands`, never taken wholesale. The
        # daemon is a PROVIDER of BOTH `loraham.daemon-socket.433` and `.868`, and it is in every
        # daemon-backed stack's run order — so adding its provider claims verbatim made an 868-only
        # stack (meshcore) lock the 433 socket, and a 433-only stack (meshcom) lock the 868 one.
        # They then serialized against each other across bands that neither touches. The radio key
        # already worked this way; the socket key has to follow the same rule.
        _band_scoped = ("loraham.radio.", "loraham.daemon-socket.")
        for _, c in order:
            for r in c.resources:
                if (r.mode in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER)
                        and not r.key.startswith(_band_scoped)):
                    keys.add(r.key)
        # Only a DAEMON-BACKED operation claims a daemon socket. A radio-direct stack
        # (meshtastic, reticulum) drives the hardware itself and never touches one — adding the
        # key for it would invent a conflict with every daemon client on the same band.
        serves_daemon = any(c.id == self.DAEMON_ID for _, c in order)
        for b in self._operation_bands(target, band, radio, op):
            keys.add(f"loraham.radio.{b}")
            if serves_daemon:
                keys.add(f"loraham.daemon-socket.{b}")
        # The GPS receiver is a DYNAMIC exclusive claim: which device (if any) a stack opens
        # comes from the resolved global plan, not from a static manifest resource. Without
        # this, two stacks configured for direct NMEA would both open the same receiver — two
        # readers on one device, which loses fixes intermittently instead of failing cleanly.
        # Keyed on the real character device (st_rdev), so /dev/ttyACM0 and its by-id alias
        # cannot be claimed as if they were two different receivers.
        gps_key = self._gps_device_claim(target)
        if gps_key:
            keys.add(gps_key)
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
        if cascade and op in ("stop", "restart"):      # a restart's stop leg cascades too
            # A PER-BAND daemon stop cascades only to the band(s) its stop leg takes down (dual-band
            # collateral included) — the same scope `_stop_impl` gives `stop_dependents`.
            sid = self.stack_of(target) or target
            stk = self.stack(sid)
            per_band = (stk is not None and stk.main == self.DAEMON_ID and band in ("433", "868"))
            stopped = self._operation_bands(target, band, "", "stop") if per_band else None
            for dep in self._dependents_of(target, stopped):
                add(dep, False, band if per_band else "", "", "stop")
        return sorted(keys, key=reslock.canonical_key)

    def _held_counts(self) -> dict:
        """Per-THREAD map of lock key -> recursion depth currently held by THIS thread."""
        st = self._lock_state
        counts = getattr(st, "counts", None)
        if counts is None:
            counts = st.counts = {}
        return counts

    # LOCK ORDER (fixed, to avoid deadlock): (1) task admission -> (2) controller/config/lifecycle/
    # source resource locks -> (3) the self-update lock. The admission key is always acquired FIRST.
    ADMISSION_KEY = "controller-task-admission"

    def _admit(self, stack, op: str, target: str = "") -> None:
        """Acquire the ONE interprocess task-admission flock (`controller-task-admission`) into `stack`,
        RE-ENTRANT per thread (shared `_held_counts`), and — on the FRESH (outermost) acquire — run the
        STRICT blocker check UNDER the held lock, raising `AdmissionRefused` if a controller self-update
        or uninstall is pending/in progress. A NESTED acquire (e.g. start nested inside an admitted
        restart, or build inside an admitted auto-install) reuses the held lock and never re-checks, so
        it can never self-deadlock or self-refuse. A second `ControllerService` on the same runtime root
        (any process) contends on the same flock. Skipped entirely when the runtime root is absent (no
        lockfile created, no check) — preserving zero-filesystem-mutation behavior."""
        if not self._paths.runtime_root_exists:
            return
        counts = self._held_counts()
        if counts.get(self.ADMISSION_KEY, 0) > 0:          # reentrant: already admitted in THIS thread
            counts[self.ADMISSION_KEY] += 1
            stack.callback(self._admit_release)
            return
        self._acquire_key(stack, self.ADMISSION_KEY, op, target)   # interprocess flock, released on close
        counts[self.ADMISSION_KEY] = 1
        stack.callback(self._admit_release)
        blocked = self._task_admission_blocked()           # STRICT, under the lock
        if blocked is not None:
            raise AdmissionRefused(blocked[0], blocked[1])

    def _admit_release(self) -> None:
        counts = self._held_counts()
        n = counts.get(self.ADMISSION_KEY, 0)
        if n <= 1:
            counts.pop(self.ADMISSION_KEY, None)
        else:
            counts[self.ADMISSION_KEY] = n - 1

    def _admit_raw(self, stack, op: str, target: str = "") -> None:
        """Acquire the task-admission flock into `stack` WITHOUT the strict self-update/uninstall
        self-check — for the operations that OWN the update/uninstall and would otherwise refuse
        themselves (the managed self-update helper, controller-uninstall prep). It STILL bumps
        `_held_counts`, so a nested admitted call (e.g. the helper's inner `self_update_apply`) reuses
        the SAME lock reentrantly instead of self-contending. Raises `reslock.ResourceBusy` if ANOTHER
        holder has it. Skipped when the runtime root is absent (no lockfile, no mutation)."""
        if not self._paths.runtime_root_exists:
            return
        counts = self._held_counts()
        if counts.get(self.ADMISSION_KEY, 0) > 0:          # already held in THIS thread -> reentrant
            counts[self.ADMISSION_KEY] += 1
            stack.callback(self._admit_release)
            return
        self._acquire_key(stack, self.ADMISSION_KEY, op, target)   # interprocess flock
        counts[self.ADMISSION_KEY] = 1
        stack.callback(self._admit_release)

    @contextmanager
    def _admission_guard(self, op: str, target: str = ""):
        """Standalone held task-admission for ops that do NOT go through the lifecycle/source guards
        (detached web-job spawn, auto-install, HMAC, self-update, controller-uninstall prep). Holds the
        admission flock across the whole `with` body; raises `AdmissionRefused` on the fresh acquire if
        blocked. Reentrant, so nested admitted calls are safe."""
        with contextlib.ExitStack() as stack:
            self._admit(stack, op, target)
            yield

    # How long to WAIT for a resource claim held by our OWN controller process before failing.
    _SELF_LOCK_WAIT_S = 5.0
    GPS_MONITOR_OP = "gps-monitor"     # the owner-record operation of the Monitor's sample

    def _acquire_key(self, stack, k: str, op: str, target: str) -> None:
        """Enter one reslock key into `stack`. A claim held by ANOTHER process is a real external
        conflict → fail fast (`ResourceBusy`). A claim held by our OWN controller process is a
        concurrent/overlapping controller op (this service is shared across waitress threads, and
        two lifecycle ops can touch a shared claim like `loraham.daemon-socket.433`) that releases
        shortly → wait BOUNDED, so the operator is never told their own stack is 'busy' on itself,
        while a genuinely hung holder still can't wedge us forever."""
        from . import reslock
        deadline = time.monotonic() + self._SELF_LOCK_WAIT_S
        # `operation_lock` serializes taking the flock with publishing its `.owner` record — per
        # key, within this process — so a holder in ANOTHER THREAD HERE can never be observed
        # mid-publication. That is what removes the old timing-based grace: waiting a fixed
        # fraction of a second for an owner record to appear was a race against SD-card write
        # latency, and it lost under load, which is exactly when two controller threads overlap.
        #
        # An unidentifiable holder is therefore, by construction, NOT one of ours: it is another
        # process mid-publication or a corrupt record. Both are external conflicts and fail fast.
        while True:
            try:
                stack.enter_context(reslock.operation_lock(self._paths, k, op, target))
                # ONE CHOKE POINT for the power-pending gate: EVERY fresh interprocess
                # admission acquisition passes through here (_admit, _admit_raw, the
                # self-update trigger's and uninstall-prep's direct acquires, and any
                # future one), and the check runs AFTER the flock is held so it can never
                # race the marker write (power_action writes it under this same lock).
                # Raising unwinds the ExitStack, releasing the just-taken flock. Reentrant
                # nested acquires never reach here (counts short-circuit in the callers).
                if k == self.ADMISSION_KEY:
                    _pb = self._power_pending_blocked()
                    if _pb is not None:
                        raise AdmissionRefused(_pb[0], _pb[1])
                return                      # the lock became available -> normal acquisition
            except reslock.ResourceBusy as busy:
                holder = busy.holder if isinstance(busy.holder, dict) else {}
                pid = holder.get("pid")
                # THE ONE NARROW EXCEPTION to fail-fast-on-external: a start/restart that meets
                # the GPS Monitor's bounded one-shot sample on a `claim.gps.*` key waits for it
                # within this same deadline instead of failing a perfectly valid start. The
                # sample holds the claim for a few seconds at most (docs/gps.md, "Monitor").
                # `operation_lock()` takes the flock before it publishes the owner record and
                # removes the record before it unlocks, and its publication mutex covers only
                # threads of ONE process — so a CLI start racing the web process can observe a
                # held GPS flock with NO owner record for a moment. On a GPS claim during a
                # start that momentarily unidentifiable holder is re-read within the deadline
                # until it becomes identifiable (then: the monitor -> keep waiting; anything
                # else -> fail fast as before) or the lock is free. Every other key keeps the
                # existing rule unchanged; nothing waits past the deadline.
                gps_start = op in ("start", "restart") and k.startswith("claim.gps.")
                if pid is None or not str(pid).strip():
                    if gps_start and time.monotonic() < deadline:
                        time.sleep(0.1)
                        continue
                    raise                   # unidentifiable => external -> fail fast
                if str(pid) != str(os.getpid()):
                    if (gps_start and holder.get("operation") == self.GPS_MONITOR_OP
                            and time.monotonic() < deadline):
                        time.sleep(0.1)
                        continue
                    raise                   # a genuinely EXTERNAL holder -> fail fast, as before
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)             # our own overlapping op -> bounded serialization

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
                # Lock order #1: task admission, held across the whole lifecycle op. start/restart are
                # task-STARTS (gated); stop is NOT (it must run to quiesce during uninstall — item 2).
                # restart's admission is reused reentrant by its nested stop/start.
                if op in ("start", "restart"):
                    self._admit(stack, op, target)
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

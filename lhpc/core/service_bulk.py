"""install-all / bulk-run driver: gates, claim, log streaming, markers, reconciliation.

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import os
import sys
import time
import uuid
from contextlib import contextmanager

from . import runtime_fs
from . import validators
from .model import RunState
from .paths import PathContainmentError
from .service_base import ActionResult, SourceTxnBlocked


class BulkOpsMixin:

    def bulk_status(self) -> dict | None:
        """Tri-state run state for GETs (file + /proc only, never mutates): None (absent),
        {"unsafe": True, reason}, or the marker dict — with a preparing/running marker
        whose identity-tracked job is provably GONE presented as `interrupted`."""
        from . import bulk as bulk_mod
        state, d = bulk_mod.read_marker(self._paths)
        if state == "absent":
            return None
        if state == "unsafe":
            return {"unsafe": True, "reason": d["reason"]}
        if d["state"] in ("preparing", "running"):
            job = bulk_mod.log_name_for(d["run_id"]) + ".log"
            if not self.log_running("all", job=job):
                d = dict(d, state="interrupted", derived_interrupted=True)
        return d

    def bulk_running(self) -> bool:
        st = self.bulk_status()
        return bool(st and not st.get("unsafe")
                    and st.get("state") in ("preparing", "running"))

    def _bulk_bootstrap_refusal(self) -> ActionResult:
        return ActionResult(
            ok=False,
            summary="Runtime root is not bootstrapped yet.",
            details=[f"Run 'lhpc bootstrap' to create {self._paths.runtime_root}."],
            next_commands=["lhpc bootstrap"],
        )

    def _bulk_gate(self) -> str:
        """Typed reason a NEW bulk run must not start; "" when clear. A DEAD lease, a
        dead/foreign bulk-start reservation, and an interrupted/unsafe marker are all
        MUTATION-BLOCKING until explicitly acknowledged."""
        from . import bulk as bulk_mod, procident
        rstate, res = bulk_mod.read_reservation(self._paths)
        if rstate == "unsafe":
            return ("the bulk-start reservation is unreadable or malformed — acknowledge "
                    "(recover) it before starting a new run")
        if rstate == "valid":
            if res.get("phase") == "spawning":
                # `spawning` is an IN-LOCK transition only: a persisted record is always
                # recovery evidence, never a live web-server-owned run.
                if res.get("child") == "none":
                    return ("a previous bulk start did not complete (no child process "
                            "remains) — acknowledge (recover) it before starting a "
                            "new run")
                return ("a previous bulk start may have spawned a child that was never "
                        "confirmed (ORPHAN RISK"
                        f"{', pid ' + str(res.get('pid')) if res.get('pid', 0) > 1 else ''}"
                        ") — inspect/terminate any such process, then acknowledge "
                        "(recover) with the confirmation")
            if res.get("phase") == "orphan-risk":
                return ("a previous bulk start left a child whose termination could not "
                        f"be proven (ORPHAN RISK{', pid ' + str(res.get('pid')) if res.get('pid', 0) > 1 else ''}"
                        f"): {res.get('reason', '')} — inspect/terminate the process, "
                        "then acknowledge (recover) with the confirmation")
            if procident.identity_matches(res.get("ident", {}), res.get("pid", -1)):
                return "a bulk run is already reserved/in progress"
            return ("a previous bulk start died holding its reservation — acknowledge "
                    "(recover) it before starting a new run")
        lstate, lease = bulk_mod.read_lease(self._paths)
        if lstate == "unsafe":
            return ("the bulk-operation lease is unreadable or malformed — acknowledge "
                    "(recover) it before starting a new run")
        if lstate == "valid":
            if procident.identity_matches(lease.get("ident", {}), lease.get("pid", -1)):
                return "a bulk run is already in progress (lease held)"
            return ("a previous bulk run died while holding its operation lease — "
                    "acknowledge (recover) it before starting a new run")
        st = self.bulk_status()
        if st is None:
            return ""
        if st.get("unsafe"):
            return ("the bulk run state is unreadable or malformed — acknowledge "
                    "(recover) it before starting a new run")
        if st["state"] in ("preparing", "running"):
            return "a bulk run is already in progress"
        if st["state"] == "interrupted":
            return ("the previous bulk run was interrupted — acknowledge (recover) it "
                    "before starting a new run")
        return ""

    def _bulk_claim(self, run_id: str) -> str:
        """Claim (or, for a manual CLI run, create) the bulk-start reservation for this
        driver process under the dedicated bulk-start lock. Returns "" when the slot is
        bound to us, else a typed refusal. Handles every reservation state fail-closed."""
        from . import bulk as bulk_mod, procident, reslock
        ident = procident.proc_identity(os.getpid()) or {}
        if not procident.identity_complete(ident):
            return "bulk run refused: process identity incomplete"
        try:
            with reslock.operation_lock(self._paths, "bulk-start", "install-all", ""):
                rstate, res = bulk_mod.read_reservation(self._paths)
                if rstate == "unsafe":
                    return ("the bulk-start reservation is unreadable or malformed — "
                            "acknowledge (recover) it before starting a new run")
                if rstate == "valid":
                    if res.get("run_id") != run_id:
                        if procident.identity_matches(res.get("ident", {}),
                                                      res.get("pid", -1)):
                            return "a bulk run is already reserved/in progress"
                        return ("a previous bulk start died holding its reservation — "
                                "acknowledge (recover) it before starting a new run")
                    # OUR run_id: the slot must be in phase `spawned` and bound to
                    # EXACTLY THIS process — a foreign or stale reservation is never
                    # overwritten by a claim.
                    if res.get("phase") != "spawned":
                        return ("the bulk-start reservation is not in the spawned phase "
                                "— refusing to claim (stale or foreign slot)")
                    if not (res.get("pid") == os.getpid()
                            and procident.identity_matches(res.get("ident", {}),
                                                           os.getpid())):
                        return ("the bulk-start reservation is bound to a different "
                                "process — refusing to claim a foreign slot")
                    if not bulk_mod.bind_reservation(self._paths, run_id,
                                                     os.getpid(), ident, "claimed"):
                        return ("the bulk-start reservation could not be claimed — "
                                "refusing to run unbound")
                    return ""
                # absent -> manual CLI start: gate, then create our own reservation
                gate = self._bulk_gate()
                if gate:
                    return f"Refusing to start the bulk run: {gate}"
                ok, why = bulk_mod.write_reservation(self._paths, run_id,
                                                     os.getpid(), ident,
                                                     phase="claimed")
                return "" if ok else f"bulk run refused: {why}"
        except reslock.ResourceBusy:
            return "a bulk start is already in progress (start lock contended)"

    def bulk_recovery_reason(self) -> str:
        """SAFE-SIDE recovery signal for GET rendering: the typed reason acknowledgement
        is required — derived from DEAD/UNSAFE reservation or lease evidence and from
        unsafe/interrupted run markers, EVEN when the run marker is absent or terminal.
        "" when nothing blocks. File + /proc reads only; never mutates."""
        gate = self._bulk_gate()
        if gate and "acknowledge" in gate:
            return gate
        return ""

    def bulk_log_chunk(self, run_id: str, offset: int) -> dict:
        """Byte-capped, cursor-based read of the primary run log for the run view. The
        filename is derived EXCLUSIVELY from the validated run_id (marker log fields are
        never opened); offsets are bounded non-negative ints. File-only, no-follow.

        FULLY FAIL-CLOSED (never raises through a GET route): path CONSTRUCTION (`under`
        can raise PathContainmentError when `logs/` is an escaping symlink), the no-follow
        parent walk, the O_NOFOLLOW open, and fstat/lseek/read are ALL guarded, and the
        whole body is wrapped as a backstop. An escaping/symlinked/non-regular/unreadable
        log yields bounded safe `error` data — the external target is never followed or
        read. Both /install-all and /api/install-all stay GET-safe (HTTP 200)."""
        import stat as stat_mod
        from . import bulk as bulk_mod
        try:
            try:
                name = bulk_mod.log_name_for(run_id) + ".log"
            except ValueError:
                return {"error": "invalid run id", "offset": 0, "data": ""}
            if not isinstance(offset, int) or offset < 0 or offset > (1 << 40):
                return {"error": "invalid offset", "offset": 0, "data": ""}
            fd = -1
            try:
                # Path CONSTRUCTION is inside the guard: `under` raises
                # PathContainmentError for an escaping/symlinked `logs/` parent.
                path = self._paths.under("logs", name)
                with runtime_fs._walk_parent(self._paths, path, create=False) as (pfd, leaf):
                    fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
            except FileNotFoundError:
                return {"offset": 0, "data": "", "size": 0}
            except (OSError, PathContainmentError, ValueError) as exc:
                return {"error": f"log unreadable ({exc})", "offset": 0, "data": ""}
            try:
                stt = os.fstat(fd)
                if not stat_mod.S_ISREG(stt.st_mode):
                    return {"error": "log is not a regular file",
                            "offset": 0, "data": ""}
                size = stt.st_size
                if offset > size:
                    offset = 0                   # truncated/new run: client restarts
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, 64 * 1024)    # byte cap per poll
                return {"offset": offset + len(data),
                        "data": data.decode("utf-8", "replace"), "size": size}
            except OSError as exc:
                return {"error": f"log unreadable ({exc})", "offset": 0, "data": ""}
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        except Exception:                        # noqa: BLE001 — a GET must never 500
            return {"error": "run log temporarily unavailable", "offset": 0, "data": ""}

    def _bulk_component_log_list(self, st) -> list:
        """The run's ordered (title, filename) component build/test logs read DIRECTLY
        from the marker's DURABLE, run-owned `component_logs` — recorded in exact creation
        order as each log was about to be written under a RUN-SPECIFIC name. Membership and
        order come ONLY from this list; there is NO mtime/timestamp/glob/manifest inference,
        so a prior run's generic log can never appear, the list is append-only (a new log
        only ever extends the end), and identical timestamps are irrelevant. Fail-closed:
        `bulk.component_logs` validates each entry's run-id-bound filename and SKIPS (never
        raises on) any malformed/foreign one — the browser never influences this list."""
        from . import bulk as bulk_mod
        return bulk_mod.component_logs(st)

    @staticmethod
    def _bulk_log_frame(title: str, path: str) -> str:
        """The optical separator between streamed logs: an ASCII frame naming the
        component/log and its path."""
        width = 74
        def row(text: str) -> str:
            return "| " + text[:width - 4].ljust(width - 4) + " |"
        bar = "+" + "=" * (width - 2) + "+"
        return f"\n{bar}\n{row(title)}\n{row(path)}\n{bar}\n"

    def _read_named_log_chunk(self, fname: str, offset: int, cap: int) -> tuple:
        """Descriptor-safe, O_NOFOLLOW, byte-capped read of logs/<fname> from offset:
        returns (raw_byte_count, text, size); (-1, "", 0) when unreadable. FAIL-CLOSED:
        path CONSTRUCTION (`under` can raise PathContainmentError), the no-follow parent
        walk, and open/stat/read are ALL inside the guard; `fname` must additionally be a
        single safe leaf. A symlinked/escaping/malformed logs parent or leaf yields the
        unreadable sentinel — it is never followed and never raised to the caller."""
        import stat as stat_mod
        fd = -1
        try:
            # Defense-in-depth: even though marker entries are already run-id-validated,
            # never build a path from a name with separators/`..`/NULs.
            validators.path_component(fname, field="component log")
            path = self._paths.under("logs", fname)
            with runtime_fs._walk_parent(self._paths, path, create=False) as (pfd, leaf):
                fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            # ABSENT: the log leaf does not exist YET. A bulk component's step logs are
            # registered in the marker before they are created (created one at a time as
            # the build runs), so an absent leaf is a FUTURE log — distinct from an unsafe
            # one, and the stream must WAIT at it, never frame or advance past it.
            return (-2, "", 0)
        except (OSError, PathContainmentError, ValueError, validators.ValidationError):
            return (-1, "", 0)                    # UNSAFE: present but symlink/non-regular/escaping
        try:
            stt = os.fstat(fd)
            if not stat_mod.S_ISREG(stt.st_mode):
                return (-1, "", 0)
            size = stt.st_size
            if offset > size:
                return (0, "", size)
            os.lseek(fd, offset, os.SEEK_SET)
            data = os.read(fd, cap)
            return (len(data), data.decode("utf-8", "replace"), size)
        except OSError:
            return (-1, "", 0)
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def bulk_component_log_chunk(self, run_id: str, index: int, offset: int) -> dict:
        """LIVE sequential stream over the run's DURABLE, run-owned component build/test
        logs (from the marker, run-id-bound): cursor = (index, byte offset) into that
        ordered list; each log begins with its ASCII-framed title, and a DRAINED log
        advances to the next. Stateless, GET-safe, byte-capped, NO mutation, NO network.

        FAIL-CLOSED (never raises through a GET route): the whole body is wrapped; an
        UNREADABLE log leaf (symlinked/malformed/escaping) is framed once with a bounded
        '[log unavailable — unsafe or unreadable]' notice and skipped if a successor
        exists, else surfaced as an explicit safe `error` — the browser is never given a
        500 and no unsafe evidence is followed or trusted."""
        try:
            st = self.bulk_status()
            if (not st or st.get("unsafe") or st.get("run_id") != run_id
                    or not isinstance(index, int) or index < 0 or index > 4096
                    or not isinstance(offset, int) or offset < 0 or offset > (1 << 40)):
                return {"index": 0, "offset": 0, "data": ""}
            logs = self._bulk_component_log_list(st)
            parts = []
            error = ""
            budget = 512 * 1024                  # keep up with verbose builds (PIO)
            hops = 0
            while index < len(logs) and budget > 0 and hops < 8:
                hops += 1
                title, fname = logs[index]
                nbytes, text, size = self._read_named_log_chunk(fname, offset, budget)
                if nbytes == -2:
                    # ABSENT: this log's step has not run yet — the live frontier. WAIT
                    # here (no frame, no advance); it is framed with its first bytes once
                    # created. This is what stops (a) re-framing the last registered step
                    # every poll and (b) skipping earlier steps before their content exists.
                    break
                if nbytes == -1:
                    # UNSAFE leaf (present but symlink/non-regular/escaping): frame a
                    # bounded notice ONCE, then advance past it if a successor exists
                    # (never stall, never follow it); no successor -> explicit safe error.
                    if offset == 0:
                        parts.append(self._bulk_log_frame(
                            title, f"logs/{fname} — [log unavailable — unsafe or "
                                   f"unreadable]"))
                    if index < len(logs) - 1:
                        index += 1
                        offset = 0
                        continue
                    error = "a component log is unavailable (unsafe or unreadable)"
                    break
                if nbytes:
                    # The frame is emitted EXACTLY ONCE per file — with its first bytes
                    # (never for a still-empty live tail, which would re-frame each poll).
                    if offset == 0:
                        parts.append(self._bulk_log_frame(title, f"logs/{fname}"))
                    parts.append(text)
                    offset += nbytes
                    budget -= nbytes
                    continue                     # maybe more of THIS file next loop
                # DRAINED (nbytes == 0, at EOF). Advance to the next log ONLY once the
                # successor actually EXISTS — because logs are created sequentially, a
                # created successor proves THIS step finished. If the successor is still
                # absent, THIS file is the live frontier: wait for more of it rather than
                # advancing past a step that may still be producing output.
                if offset >= size and index < len(logs) - 1:
                    succ_present = self._read_named_log_chunk(
                        logs[index + 1][1], 0, 1)[0] != -2
                    if succ_present:
                        if offset == 0:
                            # A COMPLETE empty file: frame it once while passing over it.
                            parts.append(self._bulk_log_frame(title, f"logs/{fname}"))
                        index += 1               # drained and a successor exists
                        offset = 0
                        continue
                break                            # live tail / frontier: wait for more bytes
            out = {"index": index, "offset": offset, "data": "".join(parts)}
            if error:
                out["error"] = error
            return out
        except Exception:                        # noqa: BLE001 — a GET must never 500
            return {"index": 0, "offset": 0, "data": "",
                    "error": "component-log stream temporarily unavailable"}

    def bulk_component_log_seed(self, run_id: str) -> str:
        """SERVER-SIDE seed of the historical component-log window (the '#bulk-complog' second
        window): a bounded DRAIN of the live `bulk_component_log_chunk` cursor API for a FINISHED
        run, so it inherits that method's run-id validation, safe no-follow reads, ASCII framing and
        unsafe-leaf handling (it never opens component logs / paths itself). Terminates when the
        cursor stops advancing (the chunk API exposes no explicit done flag — for a terminal run this
        coincides with empty data) or on a returned `error`. Hard-bounded by BOTH a byte cap and a
        read-count cap; front-trims with a visible notice on overflow. Fail-closed: returns "" (or the
        framed diagnostic the chunk API already produced) — never raises through a GET."""
        parts: list[str] = []
        total = 0
        index, offset = 0, 0
        truncated_reads = False
        try:
            for _ in range(self._COMPLOG_SEED_MAX_READS):
                chunk = self.bulk_component_log_chunk(run_id, index, offset)
                data = chunk.get("data", "")
                if data:
                    parts.append(data)
                    total += len(data)
                ni, no = chunk.get("index", index), chunk.get("offset", offset)
                if chunk.get("error"):
                    break                                   # diagnostic already in `data`; stop
                if ni == index and no == offset:            # cursor did not advance -> drained
                    break
                index, offset = ni, no
                if total >= self._COMPLOG_SEED_MAX_BYTES:
                    break
            else:
                truncated_reads = True                      # exhausted the read cap without draining
        except Exception:                                   # noqa: BLE001 — a GET must never 500
            return "".join(parts)
        seed = "".join(parts)
        if len(seed) > self._COMPLOG_SEED_MAX_BYTES:        # front-trim, keep the tail (matches bulk.js)
            keep = self._COMPLOG_SEED_MAX_BYTES - 200_000
            cut = len(seed) - keep
            nl = seed.find("\n", cut)
            seed = "[… older output trimmed …]\n" + seed[(nl + 1) if nl >= 0 else cut:]
        if truncated_reads:
            seed += "\n[… stream truncated (read cap) …]\n"
        return seed

    def bulk_ack(self, confirm_orphan: bool = False) -> ActionResult:
        """EXPLICIT recovery/acknowledgement of dead/unsafe bulk state, SERIALIZED with
        launches: the dedicated bulk-start lock is held from the liveness re-validation
        of reservation/lease/marker/job through the archival of every bulk runtime leaf
        (LOCK ORDER: bulk-start -> source-txn index; no code path acquires them in the
        reverse order). A start racing this either completed first — then the LIVE
        reservation/lease makes this refuse — or waits on the lock and starts fresh
        afterwards. A live run's evidence is NEVER archived."""
        from . import bulk as bulk_mod, procident, reslock
        inst = self._installer()
        try:
            with reslock.operation_lock(self._paths, "bulk-start", "bulk-ack", ""):
                lstate, lease = bulk_mod.read_lease(self._paths)
                if lstate == "valid" and procident.identity_matches(
                        lease.get("ident", {}), lease.get("pid", -1)):
                    return ActionResult(False, "Cannot acknowledge: the bulk run is "
                                        "still alive.")
                rstate, res = bulk_mod.read_reservation(self._paths)
                needs_confirm = rstate == "valid" and (
                    res.get("phase") == "orphan-risk"
                    or (res.get("phase") == "spawning"
                        and res.get("child") != "none"))
                if needs_confirm and not confirm_orphan:
                    return ActionResult(
                        False, "Cannot acknowledge automatically: a spawned child's "
                        "termination was never proven (ORPHAN RISK"
                        + (f", pid {res.get('pid')}" if res.get("pid", 0) > 1 else "")
                        + "). Inspect/terminate the process manually, then acknowledge "
                        "WITH the explicit confirmation.")
                if rstate == "valid" \
                        and res.get("phase") not in ("orphan-risk", "spawning") \
                        and procident.identity_matches(
                        res.get("ident", {}), res.get("pid", -1)):
                    return ActionResult(False, "Cannot acknowledge: the bulk start is "
                                        "still alive (reservation held by a live "
                                        "process).")
                st = self.bulk_status()
                if st and not st.get("unsafe") and st["state"] in ("preparing",
                                                                   "running"):
                    return ActionResult(False, "Cannot acknowledge: the bulk run is in "
                                        "progress.")
                with reslock.operation_lock(self._paths, inst._index_key(),
                                            "bulk-ack", ""):
                    inst._recover_scan()
                    if inst._pending_journals():
                        return ActionResult(False, "Cannot acknowledge: an unresolved "
                                            "source transaction journal exists — "
                                            "resolve it first (see lhpc status).")
                    ok1, d1 = bulk_mod.archive(self._paths, bulk_mod.MARKER, "run")
                    ok2, d2 = bulk_mod.archive(self._paths, bulk_mod.LEASE, "lease")
                    ok3, d3 = bulk_mod.archive(self._paths, bulk_mod.RESERVATION,
                                               "start")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot acknowledge: {busy}")
        ok = ok1 and ok2 and ok3
        return ActionResult(ok, "Bulk run state acknowledged and archived." if ok else
                            "Acknowledgement INCOMPLETE.",
                            details=[f"  marker: {d1}", f"  lease: {d2}",
                                     f"  reservation: {d3}"])

    def spawn_bulk_job(self, source: str, tests: bool, tx: bool) -> tuple:
        """Spawn the detached bulk driver (`python -u -m lhpc install-all …`) with an
        identity-tracked job marker. Returns (log_name, error)."""
        from . import bulk as bulk_mod
        if source not in self.SOURCE_CHOICES:
            return None, f"unknown source choice {source!r}"
        if tx and not tests:
            return None, "the TX test requires host tests to be enabled"
        if tx and not getattr(self.config().operator, "callsign", ""):
            return None, ("TX requested but no operator callsign is configured — set it "
                          "in Settings before a transmitting run")
        if not self._paths.runtime_root_exists:
            return None, ("Runtime root is not bootstrapped yet. "
                          "Run 'lhpc bootstrap' first.")
        from . import procident, reslock
        # ONE cross-process bulk-start critical section: gate -> reservation (no-clobber,
        # run_id-bound) -> spawn -> job claim, all under the dedicated bulk-start lock. A
        # second concurrent POST/CLI start is refused typed BEFORE it can spawn a child.
        try:
            with reslock.operation_lock(self._paths, "bulk-start", "install-all", ""):
                gate = self._bulk_gate()
                if gate:
                    return None, gate
                run_id = uuid.uuid4().hex
                ident = procident.proc_identity(os.getpid()) or {}
                if not procident.identity_complete(ident):
                    return None, "bulk start refused: process identity incomplete"
                ok, why = bulk_mod.write_reservation(self._paths, run_id,
                                                     os.getpid(), ident,
                                                     phase="spawning")
                if not ok:
                    return None, f"bulk start refused: {why}"
                argv = [sys.executable, "-u", "-m", "lhpc", "install-all", "--yes",
                        "--source", source, "--run-id", run_id]
                if not tests:
                    argv.append("--no-tests")
                if tx:
                    argv.append("--tx")
                # EXCEPTION-SAFE SETTLEMENT: from here, EVERY outcome — including
                # ordinary exceptions from spawn, identity capture, rebinding, tracking,
                # orphan-risk persistence, or clearing — settles the slot into exactly
                # one durable state before the lock releases: bound to the child,
                # safely removed, or a recovery-required record. A residual `spawning`
                # record is NEVER a live web-server-owned run.

                def settle_gone(msg: str) -> str:
                    """No child was created, or its cessation is identity-PROVEN."""
                    if bulk_mod.clear_reservation(self._paths):
                        return msg
                    if bulk_mod.mark_reservation_child(self._paths, run_id,
                                                       os.getpid(), ident, "none"):
                        return (msg + " — the reservation could not be removed; "
                                "acknowledge (recover) it before the next run")
                    return (msg + " — the reservation could not be removed or marked; "
                            "acknowledge (recover) with the confirmation")

                def settle_unproven(pid0, cident, msg: str) -> str:
                    """A child may exist and cessation is UNPROVEN: durable orphan-risk
                    evidence (child identity where available); if even that cannot be
                    persisted, the residual `spawning`+uncertain record itself is the
                    mutation-blocking evidence."""
                    if not bulk_mod.write_orphan_risk(
                            self._paths, run_id, pid0 or 0,
                            msg, cident):
                        return (msg + " — ORPHAN RISK; the orphan-risk record could "
                                "not be persisted either; the residual reservation "
                                "blocks new runs; acknowledge (recover) with the "
                                "confirmation")
                    return (msg + " — ORPHAN RISK; new bulk runs stay blocked; "
                            "inspect/terminate the process, then acknowledge "
                            "(recover) with the confirmation")

                pid = None
                child_ident = None
                try:
                    if not bulk_mod.mark_reservation_child(self._paths, run_id,
                                                           os.getpid(), ident,
                                                           "uncertain"):
                        # cannot durably record spawn INTENT -> do not spawn at all
                        return None, settle_gone(
                            "bulk start refused: spawn intent could not be recorded")
                    life = self._lifecycle()
                    ln, pid = life.spawn_job(bulk_mod.log_name_for(run_id), argv,
                                             str(self._paths.runtime_root))
                    if ln is None:
                        pid = None
                        return None, settle_gone("could not spawn the bulk run "
                                                 "(see logs)")
                    child_ident = procident.proc_identity(pid)
                    bound = (bool(child_ident)
                             and procident.identity_complete(child_ident)
                             and bulk_mod.bind_reservation(self._paths, run_id, pid,
                                                           child_ident, "spawned"))
                    if bound:
                        err = self._track_or_terminate(life, ln, pid, "all",
                                                       self.BULK_OP)
                        if not err:
                            return ln, None
                        if "ORPHAN RISK" in err:
                            return None, settle_unproven(
                                pid, child_ident,
                                "job tracking failed and cessation is unproven")
                        return None, settle_gone(err)
                    # identity capture or bind failed: SIGTERM-ONLY containment via the
                    # identity-verified primitive (never a signal to an unproven pid,
                    # never SIGKILL); cessation is either PROVEN or truthfully not.
                    if life._terminate_unobserved(pid, child_ident):
                        return None, settle_gone(
                            "spawned bulk run could not be identity-bound — SIGTERM "
                            "sent and child exit PROVEN")
                    return None, settle_unproven(
                        pid, child_ident,
                        "child identity could not be captured/bound after spawn and "
                        f"cessation is unproven (pid {pid})")
                except Exception as exc:            # noqa: BLE001 — settlement boundary
                    if pid is None:
                        return None, settle_gone(
                            f"bulk start failed before any child existed ({exc})")
                    proven = False
                    try:
                        proven = life._terminate_unobserved(pid, child_ident)
                    except Exception:               # noqa: BLE001
                        proven = False
                    if proven:
                        return None, settle_gone(
                            f"bulk start failed ({exc}) — SIGTERM sent and child "
                            "exit PROVEN")
                    return None, settle_unproven(
                        pid, child_ident,
                        f"bulk start failed ({exc}) and child cessation is unproven "
                        f"(pid {pid})")
        except reslock.ResourceBusy:
            return None, "a bulk start is already in progress"

    def install_all_dep_preflight(self) -> dict:
        """Per-stack install-time dependency gate across the bulk scope, for the /install-all page.
        Returns {"block": [{stack, name, deps}], "warn": [{stack, name, deps}]} — `block` = stacks
        that WILL BE SKIPPED (a mandatory dep of a non-optional component is missing), `warn` =
        stacks with only optional deps missing. GET-safe (install_dep_gate runs no subprocess)."""
        block, warn = [], []
        for st, _comps in self._bulk_scope():
            gate = self.install_dep_gate(st.id)
            if gate["block"]:
                block.append({"stack": st.id, "name": st.name, "deps": gate["block"]})
            if gate["warn"]:
                warn.append({"stack": st.id, "name": st.name, "deps": gate["warn"]})
        return {"block": block, "warn": warn}

    def install_all(self, source: str = "pinned", tests: bool = True, tx: bool = False,
                    run_id: str = "", apply: bool = False, emit=print) -> ActionResult:
        """THE bulk driver ("Install and Build all Stacks"): one outer bulk boundary
        (config-stable + all source locks + durable lease), one immutable global plan,
        per-source-group reconciliation, dependency-aware continuation, durable run
        marker at every transition (a write failure STOPS the run), disclosed TX phase.
        stdout (`emit`) is the narrative log."""
        from . import bulk as bulk_mod
        if source not in self.SOURCE_CHOICES:
            return ActionResult(False, f"Unknown source choice {source!r}.")
        if tx and not tests:
            return ActionResult(False, "Refusing: the TX test requires host tests to be "
                                "enabled (--tx without --no-tests).")
        if run_id and not bulk_mod.RUN_ID_RE.match(run_id):
            return ActionResult(False, "Refusing: invalid --run-id (32 lowercase hex).")
        scope = self._bulk_scope()
        if not scope:
            return ActionResult(False, "No stacks with managed sources in the manifest.")
        if not apply:
            details = [f"  [{self.bulk_mode()}] {st.id}: "
                       f"{', '.join(c.id for c in comps)}" for st, comps in scope]
            details.append(f"  host tests: {'on' if tests else 'off'}; "
                           f"TX test: {'ON (real RF!)' if tx else 'off'}; "
                           f"source: {source}")
            # PRE-FLIGHT dep gate: mandatory-missing stacks will be SKIPPED at run time; optional
            # missing deps only warn. Surfaced here so the operator can abort (answer N) and install
            # the copyable commands first, or continue to skip the blocked stacks.
            blocked_any = False
            for st, _comps in scope:
                gate = self.install_dep_gate(st.id)
                if gate["block"]:
                    blocked_any = True
                    cmds = "; ".join(sorted({d.get("install", "") for d in gate["block"]
                                             if d.get("install")}))
                    details.append(f"  [blocked] {st.id}: missing mandatory deps — "
                                   f"run: {cmds or 'see doctor'}")
                if gate["warn"]:
                    cmds = "; ".join(sorted({d.get("install", "") for d in gate["warn"]
                                             if d.get("install")}))
                    details.append(f"  [warn] {st.id}: optional deps missing"
                                   + (f" — run: {cmds}" if cmds else ""))
            if blocked_any:
                details.append("  NOTE: the [blocked] stacks above will be SKIPPED — abort (answer "
                               "N) and install their commands first, or continue to skip them.")
            if not self._paths.runtime_root_exists:
                details.append("  NOTE: runtime root is not bootstrapped yet — apply "
                               "requires 'lhpc bootstrap' first")
            return ActionResult(True, f"Bulk install/update plan: {len(scope)} stack(s) "
                                "in dependency order. This can take several minutes.",
                                details=details, data={"changes": len(scope)},
                                next_commands=["lhpc install-all --yes"])
        if not self._paths.runtime_root_exists:
            # BEFORE any reservation/lease/marker/source/log/job mutation.
            return self._bulk_bootstrap_refusal()
        run_id = run_id or uuid.uuid4().hex
        claim_err = self._bulk_claim(run_id)
        if claim_err:
            return ActionResult(False, claim_err if claim_err.startswith("Refusing")
                                else f"Refusing to start the bulk run: {claim_err}")
        self._lock_state.bulk_cleanup_failed = ""
        res = None
        try:
            if tx and not getattr(self.config().operator, "callsign", ""):
                # EARLY, NON-MUTATING: no boundary, no running marker, no source action —
                # only the short-lived launch reservation, released by the finally below.
                res = ActionResult(False, "Refusing the TX-enabled bulk run: no operator "
                                   "callsign is configured — set it in Settings first.")
            else:
                res = self._install_all_claimed(scope, source, tests, tx, run_id, emit)
        finally:
            # ONE converging cleanup path for EVERY claimed exit — pre-boundary refusals,
            # plan conflicts, post-lock refusals, marker-write aborts, lock contention,
            # and exceptions alike. A failed reservation/lease clear is never silent.
            failed = getattr(self._lock_state, "bulk_cleanup_failed", "")
            if not failed:
                if not bulk_mod.clear_reservation(self._paths):
                    failed = "bulk-start reservation"
                    self._lock_state.bulk_cleanup_failed = failed
        failed = getattr(self._lock_state, "bulk_cleanup_failed", "")
        if failed:
            detail = (f"bulk cleanup INCOMPLETE ({failed} could not be cleared) — "
                      "the next run is blocked until you acknowledge (recover)")
            # best-effort SAFE-SIDE marker downgrade; status stays safe-side via the
            # lease/reservation evidence even if this final rewrite also fails.
            mstate, m = bulk_mod.read_marker(self._paths)
            if mstate == "valid" and m.get("state") in ("completed",
                                                        "completed-with-failures"):
                m["state"] = "completed-with-failures"
                m["error"] = (m.get("error", "") + " " + detail).strip()
                bulk_mod.write_marker(self._paths, m)
            base = res.summary if res is not None else "Bulk run did not complete."
            return ActionResult(False, f"{base} {detail}",
                                details=list(res.details) if res is not None else [],
                                next_commands=["lhpc status"])
        return res

    def _install_all_claimed(self, scope, source, tests, tx, run_id, emit) -> ActionResult:
        from . import bulk as bulk_mod, reslock
        # cheap pre-lock preflight (typed early refusal; authoritative recheck post-lock)
        pre_running = self._bulk_running_components(scope)
        if pre_running:
            return self._bulk_running_refusal(pre_running)
        stacks_ids = [st.id for st, _ in scope]
        all_paths = sorted({c.source.path for _, comps in scope for c in comps})

        class _Abort(Exception):
            pass

        marker = None

        def bw() -> None:
            if not bulk_mod.write_marker(self._paths, marker):
                emit("FATAL: run marker could not be persisted — stopping (no work "
                     "without durable progress evidence)")
                raise _Abort()

        def register_log(title: str, log: str) -> None:
            # DURABLE, append-only registration of a component build/test log the run is
            # ABOUT to create — persisted (bw) before the file exists under its
            # run-specific name, so the live stream only ever shows this run's own logs.
            if bulk_mod.is_component_log_for(run_id, log):
                marker["component_logs"].append({"title": title, "log": log})
                bw()
        try:
            with self._bulk_boundary(run_id, stacks_ids, all_paths) as ctx:
                # AUTHORITATIVE post-lock stopped recheck: zero mutation on refusal
                # (no run marker either — nothing was started).
                running = self._bulk_running_components(scope)
                if running:
                    return self._bulk_running_refusal(running)
                # own job marker (manual CLI runs; web spawns already tracked this pid)
                job = bulk_mod.log_name_for(run_id) + ".log"
                if not self.log_running("all", job=job):
                    if not self._write_job_marker(job, os.getpid(), "all", self.BULK_OP):
                        return ActionResult(False, "Refusing: the bulk run could not be "
                                            "identity-tracked (job marker not persisted).")
                # ONE immutable global plan (frozen selectors/remotes) + reconciliation —
                # conflicts refuse BEFORE any marker/candidate/source mutation.
                items = [(st, c) for st, comps in scope for c in comps]
                groups, conflicts = self._plan_source_groups(items, source, freeze=True)
                if conflicts:
                    return ActionResult(False, "Refusing the bulk run: incompatible "
                                        "source resolutions for a shared checkout.",
                                        details=[f"  {c}" for c in conflicts])
                plan = {}                        # path -> (action, reason, comp, resolved)
                for path, comp, resolved in groups:
                    action, reason = self._reconcile_group(path, comp)
                    plan[path] = (action, reason, comp, resolved)
                # STRICT TX ADMISSION GATE (tx=True): validated after the boundary +
                # immutable plan, BEFORE any candidate/install/update/build/test. The
                # run itself proceeds; an inadmissible TX is refused HERE — durable,
                # actionable, and terminal-truthful (completed-with-failures).
                tx_refused = ""
                if tx:
                    dstack = next(((st, comps) for st, comps in scope
                                   if st.id == "daemon"), None)
                    if not getattr(self.config().operator, "callsign", ""):
                        tx_refused = ("no operator callsign is configured — set it in "
                                      "Settings")
                    elif dstack is None:
                        tx_refused = "the daemon stack is not part of this run"
                    else:
                        blocked = [f"{c.source.path}: {plan[c.source.path][1]}"
                                   for c in dstack[1]
                                   if plan[c.source.path][0] == "blocked"]
                        if blocked:
                            tx_refused = ("the daemon source group is blocked — "
                                          + "; ".join(blocked))
                        elif not any(c.build_steps for c in dstack[1]):
                            tx_refused = "the daemon has no host build planned"
                        elif not any(c.test_argv for c in dstack[1]):
                            tx_refused = "the daemon has no host test planned"
                mode = self.bulk_mode()
                mode = {"mixed": "mixed"}.get(mode, mode)
                rows = [{"id": st.id, "name": st.name,
                         "op": "+".join(sorted({plan[c.source.path][0] for c in comps}))}
                        for st, comps in scope]
                marker = bulk_mod.new_marker(run_id, mode, source, tests, tx, rows)
                if tx_refused:
                    marker["tx_phase"] = {"status": "fail",
                                          "detail": f"TX refused before source work: "
                                                    f"{tx_refused}"}
                    drow0 = next((r0 for r0 in marker["stacks"]
                                  if r0["id"] == "daemon"), None)
                    if drow0 is not None:
                        drow0["tx"] = {"ran": False, "ok": False,
                                       "detail": f"refused: {tx_refused}"}
                    emit(f"==== TX REFUSED before source work: {tx_refused} ====")
                bw()                             # 'preparing' BEFORE the first mutation
                marker["state"] = "running"
                bw()
                row = {r["id"]: r for r in marker["stacks"]}
                _, edges = self._bulk_scope_edges()
                processed: dict = {}             # path -> (ok, detail)
                failed_stacks: set = set()
                mutated: list = []
                inst = self._installer()
                for st, comps in scope:
                    r = row[st.id]
                    bad_deps = sorted(edges.get(st.id, set()) & failed_stacks)
                    if bad_deps:
                        r["status"] = "blocked"
                        r["detail"] = f"dependency failed: {', '.join(bad_deps)}"
                        failed_stacks.add(st.id)
                        emit(f"==== {st.id}: BLOCKED ({r['detail']}) ====")
                        bw()
                        continue
                    # MANDATORY system-dep gate — BEFORE any source clone/adopt. A stack missing a
                    # mandatory dep of a non-optional component is skipped without touching its
                    # sources; optional missing deps only warn and fall through into the build.
                    gate = self.install_dep_gate(st.id)
                    for d in gate["warn"]:
                        emit(f"  [warn] {st.id}: optional dep not installed: {d['what']}"
                             + (f" -> {d['install']}" if d.get("install") else ""))
                    if gate["block"]:
                        cmds = "; ".join(sorted({d.get("install", "") for d in gate["block"]
                                                 if d.get("install")}))
                        r["status"] = "blocked"
                        r["detail"] = f"missing mandatory system deps — run: {cmds or 'see doctor'}"
                        failed_stacks.add(st.id)
                        emit(f"==== {st.id}: BLOCKED ({r['detail']}) ====")
                        bw()
                        continue
                    emit(f"==== {st.id}: sources ====")
                    r["status"] = "downloading"
                    bw()
                    ok = True
                    for c in comps:
                        path = c.source.path
                        if path not in processed:
                            action, reason, comp, resolved = plan[path]
                            if action == "blocked":
                                processed[path] = (False, f"blocked: {reason}")
                            else:
                                a = self._adopt_dev_fallback(
                                    inst, st, comp, source, resolved,
                                    force=(action == "update"), locked=True)
                                emit(f"  [{a.status}] {path}: {a.detail}")
                                # every non-failed adopt outcome is OK: done (mutated),
                                # exists (already healthy), skipped (benign no-op, e.g.
                                # a linked dev tree left as-is) — only "failed" fails.
                                processed[path] = (a.status != "failed",
                                                   f"{action}: {a.detail}")
                                if a.status == "done" and action == "update":
                                    mutated.append(path)
                        p_ok, p_detail = processed[path]
                        if not p_ok:
                            ok = False
                            r["detail"] = p_detail
                    if not ok:
                        r["status"] = ("blocked"
                                       if r["detail"].startswith("blocked:") else "fail")
                        failed_stacks.add(st.id)
                        bw()
                        continue
                    # (mandatory system-dep gate already ran BEFORE source adoption, above)
                    # LINKED external trees: adoption may be a truthful no-op, but a
                    # linked stack with DECLARED build/test work that bulk intentionally
                    # refuses to execute is NOT a success — the row is blocked and the
                    # run cannot end fully `completed`.
                    linked_with_work = [c.id for c in comps
                                        if (c.source.strategy or "") == "link"
                                        and (c.build_steps or c.test_argv)]
                    if linked_with_work:
                        r["status"] = "blocked"
                        r["detail"] = ("sources linked ✓ — linked external tree: "
                                       "build/test must be performed in that checkout "
                                       f"({', '.join(linked_with_work)}); deliberate "
                                       "skip, LHPC never writes into your dev trees")
                        r["tests"] = {"ran": False, "ok": None,
                                      "detail": "skipped (linked source)"}
                        failed_stacks.add(st.id)
                        emit(f"  [blocked] {st.id}: {r['detail']}")
                        bw()
                        continue
                    linked = [c.id for c in comps
                              if (c.source.strategy or "") == "link"]
                    buildable = [c for c in comps if c.build_steps
                                 and (c.source.strategy or "") != "link"]
                    if buildable:
                        emit(f"==== {st.id}: build ====")
                        r["status"] = "building"
                        bw()
                        b = self.build(st.id, apply=True, bulk_ctx=ctx,
                                       on_component_log=register_log)
                        for line in b.details:
                            emit(line)
                        if not b.ok:
                            r["status"], r["detail"] = "fail", b.summary
                            failed_stacks.add(st.id)
                            bw()
                            continue
                    elif linked:
                        r["detail"] = ("linked external tree — LHPC never builds/tests "
                                       "into it (build it in that checkout)")
                    testable = [c for c in comps if c.test_argv
                                and (c.source.strategy or "") != "link"]
                    # Integration tests that need the stack RUNNING can't run in a build sweep
                    # (nothing is started) — they are DEFERRED, never failed, here.
                    auto = [c for c in testable if not c.test_requires_running]
                    deferred = len(testable) - len(auto)
                    if tests and testable:
                        emit(f"==== {st.id}: host tests ====")
                        r["status"] = "testing"
                        bw()
                        t = self.test(st.id, tx=False, apply=True, bulk_ctx=ctx,
                                      on_component_log=register_log)   # runs `auto`, defers the rest
                        for line in t.details:
                            emit(line)
                        if auto:
                            detail = "passed" if t.ok else "FAILED"
                            if deferred:
                                detail += (f"; {deferred} deferred (run `lhpc test {st.id}` "
                                           "with it started)")
                            r["tests"] = {"ran": True, "ok": bool(t.ok), "detail": detail}
                            if not t.ok:
                                r["status"], r["detail"] = "fail", t.summary
                                failed_stacks.add(st.id)
                                bw()
                                continue
                        else:   # only integration tests -> deferred, NOT "no host tests"
                            r["tests"] = {"ran": False, "ok": None,
                                          "detail": (f"deferred — {deferred} test(s) need the "
                                                     f"running stack (run `lhpc test {st.id}` "
                                                     "after starting it)")}
                    else:
                        r["tests"] = {"ran": False, "ok": None,
                                      "detail": ("skipped (tests disabled)" if not tests
                                                 else "skipped (no host tests)")}
                    r["status"] = "success"
                    bw()
                # candidate retirement for updated groups BEFORE the boundary releases
                extra: list = []
                if not self._retire_candidates_for_paths(mutated, extra):
                    for line in extra:
                        emit(line)
                    marker["error"] = "candidate-marker cleanup incomplete"
                # DISCLOSED TX phase (the only start this run performs) — ELIGIBLE
                # only when not already refused at admission, the daemon row is
                # `success`, its required host test PASSED, and required cleanup is
                # complete. Otherwise: no daemon start, no transmission — a truthful
                # refusal with an actionable detail.
                if tx and marker["tx_phase"]["status"] == "pending":
                    drow = next((r0 for r0 in marker["stacks"]
                                 if r0["id"] == "daemon"), None)
                    reason = ""
                    if drow is None or drow["status"] != "success":
                        reason = ("the daemon stack did not complete successfully "
                                  f"({(drow or {}).get('status', 'missing')}: "
                                  f"{(drow or {}).get('detail', '')})".strip())
                    elif not (drow["tests"].get("ran") and drow["tests"].get("ok")):
                        reason = ("the daemon host test did not pass "
                                  f"({drow['tests'].get('detail', 'not run')})")
                    elif marker["error"]:
                        reason = f"required cleanup incomplete ({marker['error']})"
                    if reason:
                        marker["tx_phase"] = {"status": "fail",
                                              "detail": "TX refused (no daemon start, "
                                                        f"no transmission): {reason}"}
                        if drow is not None:
                            drow["tx"] = {"ran": False, "ok": False,
                                          "detail": f"refused: {reason}"}
                            # the row is NEVER `success` while requested TX was refused;
                            # host build/test evidence stays intact in the tests field.
                            if drow["status"] == "success":
                                drow["status"] = "fail"
                                drow["detail"] = f"requested TX was refused: {reason}"
                        emit(f"==== TX REFUSED: {reason} ====")
                        bw()
                    else:
                        self._bulk_tx_phase(marker, ctx, emit, bw)
                elif tx and marker["tx_phase"]["status"] == "fail":
                    # TX was refused at ADMISSION (before source work): if the daemon
                    # nevertheless completed its host work successfully, the row must
                    # still not read `success` — flip it with the actionable detail,
                    # preserving the separate host-test evidence.
                    drow = next((r0 for r0 in marker["stacks"]
                                 if r0["id"] == "daemon"), None)
                    if drow is not None and drow["status"] == "success":
                        drow["status"] = "fail"
                        drow["detail"] = ("requested TX was refused: "
                                          + marker["tx_phase"].get("detail", ""))
                        bw()
                # TRUTHFUL terminal state: `completed` ONLY when every row is success,
                # TX is skipped/successful, and required cleanup is complete. Blocked
                # rows are NOT success — the run did not do everything it was asked to.
                any_bad = (any(r2["status"] != "success" for r2 in marker["stacks"])
                           or marker["tx_phase"]["status"] == "fail"
                           or bool(marker["error"]))
                marker["state"] = ("completed-with-failures" if any_bad
                                   else "completed")
                marker["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime())
                bw()
        except _Abort:
            return ActionResult(False, "Bulk run ABORTED: durable progress evidence "
                                "could not be persisted.")
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Bulk run refused: {blocked}")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Bulk run refused: {busy}")
        cleanup_failed = getattr(self._lock_state, "bulk_cleanup_failed", "")
        if cleanup_failed:
            # A retained lease/reservation blocks the NEXT run until acknowledged: the
            # result must be a durable INCOMPLETE, never a silent success.
            marker["state"] = "completed-with-failures"
            marker["error"] = (marker.get("error", "") +
                               f" boundary cleanup failed ({cleanup_failed}) — "
                               "acknowledge before the next run").strip()
            bulk_mod.write_marker(self._paths, marker)
        ok = marker["state"] == "completed"
        done = sum(1 for r2 in marker["stacks"] if r2["status"] == "success")
        blocked_n = sum(1 for r2 in marker["stacks"] if r2["status"] == "blocked")
        failed_n = sum(1 for r2 in marker["stacks"] if r2["status"] == "fail")
        summary = (f"Bulk run {marker['state']}: {done}/{len(marker['stacks'])} stack(s) "
                   f"successful, {blocked_n} blocked, {failed_n} failed."
                   + ("" if ok else " Successful stacks REMAIN installed and built."))
        if marker.get("error"):
            summary += f" ({marker['error']})"
        emit(f"==== {summary} ====")
        return ActionResult(ok, summary,
                            details=[f"  [{r2['status']}] {r2['id']}: {r2['detail']}"
                                     for r2 in marker["stacks"]],
                            next_commands=["lhpc status --versions"])

    def _bulk_running_components(self, scope) -> list:
        snap = self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        ids = {c.id for st, _ in scope for c in st.components}
        return sorted(cid for ss in snap.stacks for cid, cst in ss.components.items()
                      if cid in ids and cst.run_state in up)

    def _bulk_running_refusal(self, running) -> ActionResult:
        owners = sorted({self._owner_stack_id(cid) for cid in running})
        return ActionResult(
            False, "Refusing to start the bulk run: component(s) are running — this run "
            "never stops anything itself.",
            details=[f"  running: {', '.join(running)}"],
            next_commands=[f"lhpc stack stop {o} --yes" for o in owners])

    def _bulk_scope_edges(self) -> tuple:
        """(ordered stack ids, {stack -> set(dependency stacks)}) from the manifest graph."""
        stacks = [st for st in self.stacks() if any(c.source for c in st.components)]
        by_comp = {c.id: st.id for st in self.stacks() for c in st.components}
        edges = {st.id: set() for st in stacks}
        for st in stacks:
            for c in st.components:
                for dep in tuple(c.depends_on or ()) + tuple(c.build_requires or ()):
                    owner = by_comp.get(dep)
                    if owner and owner != st.id and owner in edges:
                        edges[st.id].add(owner)
        return [st.id for st in stacks], edges

    def _bulk_tx_phase(self, marker, ctx, emit, bw) -> None:
        """Disclosed temporary daemon start -> ONE bounded TX test -> guaranteed stop
        attempt. EVERY failure path — missing callsign, start failure, TX-test failure,
        or a failed final stop — marks the DAEMON ROW fail with a precise actionable
        detail AND the tx outcome, and persists the state while marker persistence is
        available. No task list may show the daemon successful with a failed TX phase."""
        daemon_row = next((r for r in marker["stacks"] if r["id"] == "daemon"), None)

        def fail_tx(detail: str, ran: bool) -> None:
            marker["tx_phase"] = {"status": "fail", "detail": detail}
            if daemon_row is not None:
                daemon_row["tx"] = {"ran": ran, "ok": False, "detail": detail}
                daemon_row["status"] = "fail"
                daemon_row["detail"] = f"TX phase failed: {detail}"
            bw()                                 # persisted before return when available

        op = self.config().operator
        if not getattr(op, "callsign", ""):
            fail_tx("operator callsign not configured — set it in Settings; refusing to "
                    "transmit unidentified", ran=False)
            return
        emit("==== TX phase: starting the daemon TEMPORARILY (disclosed; real RF) ====")
        marker["tx_phase"] = {"status": "running", "detail": ""}
        bw()
        started = False
        stop_failed = ""
        try:
            rs = self.start("daemon", apply=True, bulk_ctx=ctx)
            emit(rs.summary)
            if not rs.ok:
                fail_tx(f"temporary daemon start failed: {rs.summary}", ran=False)
                return
            started = True
            rt = self.test("daemon", tx=True, apply=True, bulk_ctx=ctx)
            for line in rt.details:
                emit(line)
            if not rt.ok:
                fail_tx(f"TX test failed: {rt.summary}", ran=True)
                return
            marker["tx_phase"] = {"status": "success", "detail": rt.summary}
            if daemon_row is not None:
                daemon_row["tx"] = {"ran": True, "ok": True, "detail": "passed"}
        finally:
            if started:
                rstop = self.stop("daemon", apply=True, bulk_ctx=ctx)
                emit(rstop.summary)
                if not rstop.ok:
                    prior = marker["tx_phase"].get("detail", "")
                    fail_tx((prior + " — " if prior and
                             marker["tx_phase"]["status"] == "fail" else "") +
                            "final daemon stop FAILED — the daemon may still be "
                            "RUNNING; stop it: lhpc stack stop daemon --yes", ran=True)
                    stop_failed = "stop"
            if not stop_failed:
                bw()

    # ---- bulk reconciliation + global plan (M2.0b) -------------------------

    def _reconcile_group(self, path: str, comp) -> tuple:
        """Per-SOURCE-GROUP action decision (never `is_installed(stack)` guessing):
        absent leaf -> install; registered + identity-valid -> update; anything partial,
        unowned, unsafe, dirty, or otherwise unprovable -> ("blocked", typed reason).
        Driver-side (may run git identity checks under the held boundary)."""
        from . import source_fs, source_registry
        try:
            dest = self._paths.resolve_source(path)
            kind = source_fs.leaf_kind(self._paths, dest)
        except PathContainmentError as exc:
            return "blocked", f"unsafe source path ({exc})"
        rec_state, rec, rec_why = source_registry.record_state(self._paths, path)
        if rec_state == "unsafe":
            return "blocked", f"unsafe ownership record — {rec_why}"
        if kind == "absent":
            if rec_state == "valid":
                return "blocked", ("ownership record exists but the source is absent — "
                                   "run uninstall to clear the orphaned record")
            return "install", ""
        if kind in ("file", "special"):
            return "blocked", f"unexpected {kind} leaf at the managed source path"
        if rec_state != "valid":
            return "blocked", ("present but UNOWNED (no ownership record) — LHPC never "
                               "overwrites an unmanaged tree; move it away or Clean")
        vrec, why = source_registry.verify_identity(
            self._paths, self._system, self.config(), comp, dest,
            components=tuple(sorted(self._source_consumers().get(path, {comp.id}))))
        if vrec is None:
            return "blocked", f"identity not provable — {why}"
        if kind == "dir":
            inst = self._installer()
            dirty = inst.dirty_report(dest, path)
            if dirty:
                return "blocked", ("local changes present — commit/stash or Clean before "
                                   "a bulk update touches this checkout")
        return "update", ""

    def bulk_mode(self) -> str:
        """FILE-ONLY page-mode aggregate for GET routes: 'install' (nothing present),
        'update' (all present), or 'mixed'. Uses leaf existence only — the authoritative
        per-group reconciliation runs in the driver under the held boundary."""
        from . import source_fs
        actions = set()
        for st in self.stacks():
            for c in st.components:
                if c.source is None or c.optional:
                    continue
                try:
                    kind = source_fs.leaf_kind(self._paths,
                                               self._paths.resolve_source(c.source.path))
                except PathContainmentError:
                    kind = "special"
                actions.add("install" if kind == "absent" else "update")
        if actions == {"install"} or not actions:
            return "install"
        if actions == {"update"}:
            return "update"
        return "mixed"

    def bulk_welcome(self) -> dict | None:
        """First-start banner decision, FILE-ONLY and tri-state: {"fresh": True} only when
        NO managed installed state exists AND everything is safely readable; an unsafe
        registry record, unresolved source transaction, or unowned present source returns
        {"fresh": False, "recovery": reason} — recovery guidance, never a misleading
        fresh-install welcome. None -> installed state exists (no banner)."""
        from . import source_fs, source_registry
        txn_dir = self._paths.under("state", "source-txn")
        try:
            names = [n for n, _ in runtime_fs.scandir_nofollow(self._paths, txn_dir)]
            if any(n.endswith(".json") for n in names):
                return {"fresh": False, "recovery":
                        "an unresolved source transaction exists — see lhpc status"}
        except FileNotFoundError:
            pass
        except (OSError, PathContainmentError):
            return {"fresh": False, "recovery": "runtime state is not safely readable"}
        for st in self.stacks():
            for c in st.components:
                if c.source is None:
                    continue
                try:
                    kind = source_fs.leaf_kind(self._paths,
                                               self._paths.resolve_source(c.source.path))
                except PathContainmentError:
                    return {"fresh": False, "recovery":
                            f"unsafe source path for {c.id} — inspect the runtime root"}
                state, rec, why = source_registry.record_state(self._paths, c.source.path)
                if state == "unsafe":
                    return {"fresh": False, "recovery":
                            f"unsafe ownership record for {c.source.path} — {why}"}
                if kind != "absent":
                    if state == "valid":
                        return None                       # managed install exists
                    return {"fresh": False, "recovery":
                            f"unmanaged tree at {c.source.path} — move it away or Clean"}
                if kind == "absent" and state == "valid":
                    return {"fresh": False, "recovery":
                            f"orphaned ownership record for {c.source.path} — run "
                            "uninstall to clear it"}
        return {"fresh": True}

    def _bulk_scope(self) -> list:
        """(stack, [components-with-sources]) for every stack in DEPENDENCY order
        (manifest graph: depends_on + build_requires stack edges; stable manifest order
        among independents). OPTIONAL components are INCLUDED — the bulk run installs and
        builds every declared source under <root>/src (they are only excluded from
        auto-START, which stays autostart-gated). This also keeps the boundary's lock set
        aligned with what build()/test() cover (a stack build covers ALL its comps)."""
        stacks = [st for st in self.stacks()
                  if any(c.source for c in st.components)]
        by_comp = {c.id: st.id for st in self.stacks() for c in st.components}
        edges = {st.id: set() for st in stacks}
        for st in stacks:
            for c in st.components:
                for dep in tuple(c.depends_on or ()) + tuple(c.build_requires or ()):
                    owner = by_comp.get(dep)
                    if owner and owner != st.id and owner in edges:
                        edges[st.id].add(owner)
        ordered, seen = [], set()
        def visit(sid, chain=()):
            if sid in seen or sid in chain:
                return
            for dep in sorted(edges.get(sid, ())):
                visit(dep, chain + (sid,))
            seen.add(sid)
            ordered.append(sid)
        for st in stacks:
            visit(st.id)
        by_id = {st.id: st for st in stacks}
        out = []
        for sid in ordered:
            st = by_id[sid]
            comps = [c for c in st.components if c.source]
            if comps:
                out.append((st, comps))
        return out

    # ---- bulk-operation boundary (M2.0) ----------------------------------

    def _current_bulk_ctx(self):
        return getattr(self._lock_state, "bulk_ctx", None)

    def _bulk_ctx_error(self, bulk_ctx, source_paths) -> str:
        """Fail-closed validation of an EXPLICIT outer bulk-operation context: it must BE
        this thread's active boundary and COVER the operation's source paths. Returns ""
        when valid (or when no context is supplied — the op runs standalone)."""
        if bulk_ctx is None:
            return ""
        if bulk_ctx is not self._current_bulk_ctx():
            return ("bulk operation context is not the active boundary of this thread — "
                    "refusing (locks not provably held)")
        if not bulk_ctx.covers(source_paths):
            missing = sorted(set(source_paths) - set(bulk_ctx.source_paths))
            return ("bulk operation context does not cover source path(s) "
                    f"{', '.join(missing)} — refusing (locks not provably held)")
        return ""

    @contextmanager
    def _bulk_boundary(self, run_id: str, stacks, source_paths):
        """The ONE outer boundary of a bulk run, held for its whole lifetime:
        config-stable (shared; a concurrent remote/config save waits) → source-txn
        index/recovery → ALL affected source-path locks (same coordination locks
        Start/Restart contend on) → durable LEASE bound to this process's full identity →
        the explicit `BulkOperationContext` active for this thread. Composed ops nest via
        the re-entrant guards and validate the context; the lease is cleared and the
        context deactivated before the locks release. Lease-write failure aborts typed —
        the boundary never operates without durable evidence."""
        from . import bulk as bulk_mod, procident
        with self._config_stable():
            with self._source_operation_guard(sorted(source_paths), op="install-all"):
                ident = procident.proc_identity(os.getpid()) or {}
                if not procident.identity_complete(ident):
                    raise SourceTxnBlocked(
                        "bulk lease refused: own process identity incomplete")
                if not bulk_mod.write_lease(self._paths, run_id, os.getpid(), ident,
                                            stacks, source_paths):
                    raise SourceTxnBlocked("bulk lease could not be persisted — refusing "
                                           "to operate without durable evidence")
                ctx = bulk_mod.BulkOperationContext(run_id, source_paths)
                self._lock_state.bulk_ctx = ctx
                try:
                    self._lock_state.bulk_cleanup_failed = ""
                    yield ctx
                finally:
                    self._lock_state.bulk_ctx = None
                    fails = []
                    if not bulk_mod.clear_lease(self._paths):
                        fails.append("lease")
                    if not bulk_mod.clear_reservation(self._paths):
                        fails.append("bulk-start reservation")
                    if fails:
                        # retained evidence blocks the next run until acknowledged; the
                        # driver reads this flag and reports a truthful INCOMPLETE result.
                        self._lock_state.bulk_cleanup_failed = " + ".join(fails)

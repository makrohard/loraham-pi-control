"""ControllerService mixin: the web and start jobs, their logs and job markers.

Adapters import lhpc.core.services only. Split from service_lifecycle_ops.py unchanged."""
from __future__ import annotations

from . import jobs, procident, runtime_fs, validators
from . import rflog as _rflog
from .model import RunState
from .paths import PathContainmentError
from .service_base import ActionResult, AdmissionRefused, admission_refusal
from .snapshot_memo import invalidates_snapshot


class JobsOpsMixin:

    def log_tail(self, target: str, lines: int = 300, job: str | None = None,
                 band: str = ""):
        """Raw (path, lines) for `target`'s log — for the live web log view.

        With `job` (a logs/<name>.log filename) it tails that specific job log
        (e.g. a build/test run); otherwise it tails the component's process log —
        `band` selects the instance of a band-scoped component (empty = newest).
        """
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
            rf = _rflog.by_job(name)
            if rf is not None:
                if rf[0].native:
                    self._rflog_roll_native(p)     # meshtasticd never rolls its own trace
                return str(p), self._rflog_tail(p, lines)
            return str(p), runtime_fs.tail(self._paths, p, lines)
        s = self.stack(target)
        if s is not None and s.main_component:
            comp = s.main_component
        else:
            idx = self._component_index()
            if target not in idx:
                return "", []
            comp = idx[target][1]
        return self._lifecycle().logs(comp, lines, band=band)

    _WEB_ADMIT_TIMEOUT_S = 3.0

    def _web_admit_timeout(self) -> float:
        import os
        try:
            t = float(os.environ.get("LHPC_WEB_ADMIT_TIMEOUT_S", str(self._WEB_ADMIT_TIMEOUT_S)))
            return t if t > 0 else self._WEB_ADMIT_TIMEOUT_S
        except (TypeError, ValueError):
            return self._WEB_ADMIT_TIMEOUT_S

    def _web_admit_handshake(self, log: str, attempt_id: str):
        """Bounded-poll the primary attempt marker for its admission outcome. Returns
        ("admitted", "") | ("blocked", detail) | ("pending", ""). The parent reserved THIS exact attempt
        before spawning, so an absent/mismatched-attempt marker means it was superseded → blocked immediately.
        Admission is decided by the persisted `admitted` flag (NOT a free-text detail prefix)."""
        import time as _t

        from . import jobresult
        deadline = _t.monotonic() + self._web_admit_timeout()
        while True:
            rec = jobresult.read_one(self._paths, log)
            if rec is None or rec.get("attempt_id") != attempt_id:
                return "blocked", "job attempt was superseded"
            st = rec.get("state")
            if st == "running":
                return "admitted", ""
            if st == "unsafe":                     # orphan/untracked — blocking, needs Recover
                return "blocked", (rec.get("detail") or "the job could not be tracked")
            if st in ("done", "failed"):
                # A child that reached authoritative admission (admitted=True) RAN — surface it (live log +
                # red banner). A terminal that never admitted (lock/journal block) is truly "blocked".
                if rec.get("admitted"):
                    return "admitted", ""
                return "blocked", (rec.get("detail") or "the job could not start")
            if _t.monotonic() >= deadline:         # still `starting`
                return "pending", ""
            _t.sleep(0.05)

    def _web_unsafe_source_block(self, src_keys) -> str:
        """Fail-closed by source IDENTITY: refuse a new web job whose source_keys intersect any attempt that
        PROJECTS to unsafe — persisted `unsafe` OR the derived case (startup_unverified + no live matching
        attempt: the gate-fail / failed-orphan-write fallback). Uses the SAME read-only projection as the
        banner (`_project_job`). "" when clear; Recover is the only unblock."""
        from . import jobresult
        want = set(src_keys or [])
        if not want:
            return ""
        for log, rec in jobresult.read_results(self._paths):
            keys = set(rec.get("source_keys") or [])
            # manifest plausibility: the recorded target must still resolve to a known component/stack.
            if not (self.stack_of(rec.get("target", "")) or self.stack(rec.get("target", ""))):
                continue
            if not (want & keys):
                continue
            state, _hint = self._project_job(rec, log)
            if state == "unsafe":
                return (f"blocked — a previous {rec.get('op', 'job')} on this source ended unsafe; "
                        "Recover it first")
        return ""

    @invalidates_snapshot
    def spawn_web_job(self, op: str, target: str, source: str = "pinned"):
        """Spawn detached build/test/install job(s) for `target`. Returns `(job_log_name, admission, reason)`
        with `admission ∈ {"admitted","blocked","pending"}` — the web adapter flashes per that and NEVER
        infers "started" from a returned log. PRIMARY-FIRST: the main job's admission is handshaked before
        any secondary component job is spawned; a blocked primary spawns none. Each job carries an attempt
        reservation (green/red banner) gated on parent identity tracking."""
        import os
        import sys
        import time as _time
        import uuid

        from . import build_plan, commands, jobresult, procident, reslock, runtime_fs
        life = self._lifecycle()
        import contextlib as _contextlib
        _adm_stack = _contextlib.ExitStack()
        try:
            self._admit(_adm_stack, "web-job", target)
        except AdmissionRefused as _adm:
            _adm_stack.close()
            return None, "blocked", _adm.reason
        except reslock.ResourceBusy:
            _adm_stack.close()
            return None, "blocked", "a task is starting right now (admission contended) — retry"
        try:
            _sid = self.stack_of(target) or target
            if op == "install":
                # The web may request any CHANNEL this stack allows (binary included); the
                # source-only selectors are validated against the source tuple.
                if (_cerr := self.channel_error(_sid, source)):
                    return None, "blocked", _cerr
            elif op in ("build", "test") and (_blk := self.binary_block_reason(
                    _sid, "build" if op == "build" else "host tests")):
                return None, "blocked", _blk
            items, err = self._resolve(target)
            if err:
                return None, "blocked", err
            comps = [c for _, c in items]
            # BUILD: the one selection and order (`build_plan`); the parallel per-component jobs
            # then build one round — a provider and its consumer never in the same round (the
            # consumer would race the provider's artifact). While a provider is not built, this
            # round builds it and the operator builds again once it finishes.
            build_dep_note = ""
            by_all = {c.id: c for st in self.stacks() for c in st.components}
            if op == "build":
                ordered = build_plan.order(build_plan.select(comps, self.stack(target)))
                comps, again = build_plan.web_round(ordered, self.is_built)
                if again:
                    names = ", ".join(c.name for c in comps)
                    build_dep_note = (f"Building the build dependency first ({names}) — run Build again "
                                      "to build the rest once it finishes.")
            src_paths = {c.source.path for c in comps if c.source}
            src_keys = sorted({reslock.source_lock_key(c.source.path) for c in comps if c.source})

            # ---- Part G: refuse up-front (auto-install lease / undismissed-unsafe same-source / source-txn busy) ----
            gate = self._auto_install_gate()
            if gate:
                return None, "blocked", f"blocked — {gate}"
            ublk = self._web_unsafe_source_block(src_keys)
            if ublk:
                return None, "blocked", ublk
            pre = self._web_source_precheck(src_paths)
            if pre:
                return None, "blocked", f"blocked — {pre}"

            runtime = str(self._paths.runtime_root)
            runtime_fs.ensure_dir(self._paths, self._paths.under("state", "locks"))
            post_dir = self._paths.under("state", "jobs")
            runtime_fs.ensure_dir(self._paths, post_dir)
            index_lock = str(reslock.lock_file_path(self._paths, self._installer()._index_key()))

            def _settle_track(log, aid, pid, tracked):
                """Turn a `_track_or_terminate` result into a terminal reservation and return its
                `TrackOutcome`: TERMINATION_UNVERIFIED ⇒ blocking `unsafe`; TERMINATED ⇒ ordinary
                `failed`; TRACKED ⇒ nothing to settle."""
                outcome, terr = tracked
                if outcome is jobs.TrackOutcome.TERMINATION_UNVERIFIED:
                    jobresult.terminalize(self._paths, log, aid, "unsafe",
                                          detail="the job could not be identity-tracked and its stop is "
                                                 "UNPROVEN — inspect processes (ps) then Recover",
                                          driver_ident=procident.proc_identity(pid))
                elif outcome is jobs.TrackOutcome.TERMINATED:
                    jobresult.terminalize(self._paths, log, aid, "failed", detail=terr[:200])
                return outcome

            def _spawn_install():
                name = f"install-{target}"
                log, aid = name + ".log", uuid.uuid4().hex
                if not jobresult.reserve(self._paths, log, aid, "install", target,
                                         self.stack_of(target) or "", src_keys):
                    return None, aid, "could not record the install job (a live attempt exists)"
                with jobresult.settle_on_raise(self._paths, log, aid):
                    argv = [sys.executable, "-m", "lhpc", "install", target, "--yes", "--source", source,
                            "--web-result", log, "--attempt-id", aid]
                    ln, pid = life.spawn_job(name, argv, runtime)
                    if not ln or not pid:
                        jobresult.terminalize(self._paths, log, aid, "failed", detail="could not start")
                        return None, aid, f"could not start install for '{target}'"
                return log, aid, (ln, pid, target)

            def _spawn_build(c, steps, name):
                log, aid = name + ".log", uuid.uuid4().hex
                src = str(life.source_dir(c))
                ckeys = sorted({reslock.source_lock_key(c.source.path)}) if c.source else []
                lock_paths = ([str(reslock.lock_file_path(self._paths, ckeys[0]))] if ckeys else [])
                # A BUILD also holds every other source it consumes (transitively) for its whole
                # run — SHARED, so the parallel jobs of one request can consume the same provider,
                # while any operation that changes that source (it locks exclusive) is refused.
                shared_paths = ([str(reslock.lock_file_path(self._paths, reslock.source_lock_key(sp)))
                                 for sp in build_plan.lock_sources([c], by_all)
                                 if not (c.source and sp == c.source.path)]
                                if op == "build" else [])
                if not jobresult.reserve(self._paths, log, aid, op, c.id,
                                         self.stack_of(c.id) or "", ckeys):
                    return None, aid, f"could not record the {op} job for '{c.id}' (a live attempt exists)"
                with jobresult.settle_on_raise(self._paths, log, aid):
                    try:
                        # The detached job must complete the SAME contract as lifecycle.build():
                        # invalidate the marker before step one, write the receipt only after
                        # every step passes. The receipt's source revisions are read by the
                        # launcher once it holds every lock (`consumed`), never here at spawn.
                        from .lifecycle import BUILD_MARKER_TEXT
                        # BUILD only: a detached TEST run goes through this same spawn and
                        # must never invalidate or rewrite the build completion marker.
                        _mark = bool(c.build_marker) and op == "build"
                        marker_path = str(life.source_dir(c) / c.build_marker) if _mark else ""
                        marker_text = BUILD_MARKER_TEXT if _mark else ""
                        consumed = (build_plan.consumed_sources(c, by_all, life.source_dir,
                                                                self.binary_capable(c.id))
                                    if _mark else [])
                        inputs = self._build_inputs_to_record(c) if _mark else None
                        # The same limits as lifecycle.build()/host_test() on the CLI path: a Build passes
                        # the raw manifest value (0 = none) to progress.build_limits in the launcher.
                        step_timeout = (c.build_timeout if op == "build"
                                        else (c.test_timeout or life.TEST_TIMEOUT_S))
                        script = commands.render_build_launcher(
                            steps, runtime, src, lock_paths, index_lock=index_lock,
                            shared_lock_paths=shared_paths, consumed=consumed,
                            result_name=log, attempt_id=aid, op=op, target=c.id, stack=self.stack_of(c.id) or "",
                            marker_path=marker_path, marker_text=marker_text,
                            inputs_path=str(inputs[0]) if inputs else "",
                            inputs_text=inputs[1] if inputs else "",
                            step_timeout=step_timeout,
                            **({"rev_path": str(_own[0]), "rev_consumed": _own[1]}
                               if _mark and (_own := self._own_rev_source(c)) else {}))
                    except commands.CommandError as exc:
                        jobresult.terminalize(self._paths, log, aid, "failed", detail=str(exc)[:200])
                        return None, aid, f"cannot {op} '{c.id}': {exc}"
                    uid = f"{name}-{os.getpid()}-{_time.monotonic_ns()}"
                    try:
                        launcher = runtime_fs.write_launcher(self._paths, post_dir / f"{uid}.py", script)
                    except (OSError, PathContainmentError) as exc:   # after reserve: settle it
                        jobresult.terminalize(self._paths, log, aid, "failed", detail=str(exc)[:200])
                        return None, aid, f"cannot {op} '{c.id}': {exc}"
                    ln, pid = life.spawn_job(name, [sys.executable, str(launcher)], src)
                    if not ln or not pid:
                        jobresult.terminalize(self._paths, log, aid, "failed", detail="could not start")
                        return None, aid, f"could not start {op} for '{c.id}'"
                return log, aid, (ln, pid, c.id)

            def _launch(spawn_fn, adm_stack):
                """ONE ordering for every detached job: reserve + spawn (under task admission) →
                capture the child's complete identity → RELEASE the parent's admission → publish the
                `.job` tracking marker. The child's `verify_tracked` gate passes only once the marker
                exists, so by the time it takes task admission itself the parent no longer holds the
                flock (the parent held admission across the handshake, so the child saw
                it as an external holder). Returns (log, aid, outcome) — outcome a `TrackOutcome`,
                or the spawn error when log is None."""
                log, aid, spawned = spawn_fn()
                if log is None:
                    adm_stack.close()
                    return None, aid, spawned
                ln, pid, cid = spawned
                ident = procident.proc_identity(pid)              # capture FIRST
                adm_stack.close()                                  # then release admission
                return log, aid, _settle_track(                    # then publish (or terminate)
                    log, aid, pid, self._track_or_terminate(life, ln, pid, cid, op,
                                                            attempt_id=aid, ident=ident))

            # ---- ordered job list (primary first) ----
            if op == "install":
                spawn_fns = [_spawn_install]
            else:
                bj = []
                for c in comps:
                    if op == "build" and c.build_steps:
                        bj.append((c, list(c.build_steps), f"build-{c.id}"))
                    elif op == "test" and c.test_argv:
                        bj.append((c, [{"argv": list(c.test_argv)}], f"test-{c.id}"))
                if not bj:
                    return None, "blocked", f"nothing to {op} for '{target}'"
                s = self.stack(target)
                main_id = s.main if s else None
                pi = next((i for i, (c, _, _) in enumerate(bj) if c.id == main_id), 0)
                ordered = [bj[pi]] + [j for i, j in enumerate(bj) if i != pi]
                spawn_fns = [(lambda c=c, st=st, nm=nm: _spawn_build(c, st, nm)) for (c, st, nm) in ordered]

            # ---- primary: spawn + handshake; a blocked primary spawns NO secondaries ----
            plog, paid, pout = _launch(spawn_fns[0], _adm_stack)
            if plog is None:                        # reserve/spawn/render failed
                self.prune_logs()
                return None, "blocked", f"blocked — {pout}"
            if pout is jobs.TrackOutcome.TERMINATION_UNVERIFIED:   # cessation UNPROVEN → blocking unsafe
                self.prune_logs()
                return None, "blocked", "blocked — the job could not be tracked; Recover it first"
            if pout is jobs.TrackOutcome.TERMINATED:   # PROVEN: driver terminated before it ran → ordinary failed
                self.prune_logs()
                return None, "blocked", "blocked — the job process was terminated before it ran"
            admission, reason = self._web_admit_handshake(plog, paid)
            if admission == "blocked":
                self.prune_logs()
                return None, "blocked", (f"blocked — {reason}" if reason and not reason.startswith("blocked")
                                         else (reason or "blocked"))
            for fn in spawn_fns[1:]:            # admitted/pending: start the secondary component jobs
                _sec = _contextlib.ExitStack()  # each under its own fresh admission (same ordering)
                try:
                    self._admit(_sec, "web-job", target)
                except (AdmissionRefused, reslock.ResourceBusy):
                    _sec.close()
                    break
                with _sec:                      # released even if the spawn raises
                    _launch(fn, _sec)
            self.prune_logs()
            return plog, admission, build_dep_note

        finally:
            _adm_stack.close()   # released long ago on the normal path; belt for the early returns

    @invalidates_snapshot
    def spawn_start_job(self, op: str, target: str, band: str = "", stop_owners: bool = False,
                        cascade: bool = False):
        """Spawn a DETACHED web start/restart (the hidden `lhpc _stack-start` verb) and return
        `(job_log_name, admission, reason)` like `spawn_web_job` — except that an admission refusal
        is typed `admission_blocked` (not `blocked`): the web flashes per the admission and returns
        immediately; the task banner follows the job (`web-start-<target>.log`
        / `web-restart-<target>.log`, never the `start-<x>.log` process-log namespace `spawn_job`
        truncates). One attempt per log: a second Start while one runs is a typed refusal. The child
        proves it was tracked (`webjob_gate`), then runs the ordinary locked `start()`/`restart()`
        with a hook that marks the attempt RUNNING under every lock and before any mutation — a
        superseded attempt cancels with zero side effects."""
        import contextlib as _contextlib
        import sys
        import uuid

        from . import jobresult, procident, reslock
        if op not in ("start", "restart"):
            return None, "blocked", f"{op} is not a detached operation"
        if not (self.stack(target) or self.stack_of(target)):
            return None, "blocked", f"unknown stack or component '{target}'"
        life = self._lifecycle()
        _adm = _contextlib.ExitStack()
        try:
            self._admit(_adm, "web-job", target)
        except AdmissionRefused as _a:
            _adm.close()
            # Typed, like every other path's admission refusal, and with its remedy in the text
            # the console flashes.
            res = admission_refusal(_a)
            return None, "admission_blocked", " ".join(
                [res.summary, *(f"Next: {c}" for c in res.next_commands),
                 *(d.strip() for d in res.details)])
        except reslock.ResourceBusy:
            _adm.close()
            return None, "blocked", "a task is starting right now (admission contended) — retry"
        try:
            name = f"web-{op}-{target}"
            log, aid = name + ".log", uuid.uuid4().hex
            if not jobresult.reserve(self._paths, log, aid, op, target,
                                     self.stack_of(target) or target, []):
                # reserve refuses over a LIVE attempt (running) — or over one that never settled
                # (a child that died before admitting reads `unsafe`): say both, name the remedy.
                return None, "blocked", (f"a {op} of '{target}' is already in progress — or its "
                                         "previous attempt is still open in the task banner "
                                         "(Recover or dismiss it first)")
            with jobresult.settle_on_raise(self._paths, log, aid):
                argv = [sys.executable, "-m", "lhpc", "_stack-start", target,
                        "--web-result", log, "--attempt-id", aid]
                if band:
                    argv += ["--band", band]
                if stop_owners:
                    argv.append("--stop-owners")
                if cascade and op == "restart":          # the confirmed "Stop dependents & restart"
                    argv.append("--cascade")
                if op == "restart":
                    argv.append("--restart")
                ln, pid = life.spawn_job(name, argv, str(self._paths.runtime_root))
                if not ln or not pid:
                    jobresult.terminalize(self._paths, log, aid, "failed", detail="could not start")
                    return None, "blocked", f"could not start the {op} of '{target}'"
            ident = procident.proc_identity(pid)                   # capture FIRST
            _adm.close()                                           # release admission
            tracked, terr = self._track_or_terminate(life, ln, pid, target, op, attempt_id=aid,
                                                     ident=ident)  # then publish
            if tracked is not jobs.TrackOutcome.TRACKED:
                if tracked is jobs.TrackOutcome.TERMINATION_UNVERIFIED:
                    jobresult.terminalize(self._paths, log, aid, "unsafe",
                                          detail="the job could not be identity-tracked and its "
                                                 "stop is UNPROVEN — inspect processes (ps) then "
                                                 "Recover", driver_ident=ident)
                    return None, "blocked", "blocked — the job could not be tracked; Recover it first"
                jobresult.terminalize(self._paths, log, aid, "failed", detail=terr[:200])
                return None, "blocked", "blocked — the job process was terminated before it ran"
            admission, reason = self._web_admit_handshake(log, aid)
            if admission == "blocked":
                return None, "blocked", reason or "blocked"
            return log, admission, ""
        finally:
            _adm.close()

    @invalidates_snapshot
    def cap_start_logs(self) -> dict[str, str]:
        """Try the cap on every start log (M19): the console's periodic pass calls this. EVERY regular,
        non-symlink `start-*.log` in logs/ is tried, running or not: a log with no writer is simply
        rotated, and no ownership lookup is needed. A "busy" result (another capper holds the lock)
        leaves that log unchanged; the next pass tries again. The cap's own `.prev.log` halves and every other
        log (RF logs, job logs) are left alone. Returns {name: result}; never raises."""
        out: dict[str, str] = {}
        try:
            d = self._paths.under("logs")
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except Exception as exc:
            return {"logs/": f"error: {type(exc).__name__}"}
        for name, is_link in entries:
            if (is_link or not name.startswith("start-") or not name.endswith(".log")
                    or name.endswith(".prev.log")):
                continue
            try:
                out[name] = runtime_fs.cap_start_log(self._paths, d / name)
            except Exception as exc:
                out[name] = f"error: {type(exc).__name__}"
        return out

    @invalidates_snapshot
    def cap_controller_logs(self) -> dict[str, str]:
        """Cap the controller's own long-lived logs (`updater_units.CONTROLLER_LOGS`) exactly as a
        start log: their writers (systemd `append:`, nginx) hold them O_APPEND. A missing one is
        skipped. Returns {name: result}; never raises."""
        from .updater_units import CONTROLLER_LOGS
        out: dict[str, str] = {}
        for name in CONTROLLER_LOGS:
            try:
                out[name] = runtime_fs.cap_start_log(self._paths, self._paths.under("logs", name))
            except Exception as exc:
                out[name] = f"error: {type(exc).__name__}"
        return out

    def prune_logs(self) -> int:
        """Delete the oldest runtime logs beyond a bounded count/byte budget, NEVER
        touching a log that belongs to an active job (so live evidence is preserved)
        and never following a symlink. Returns the number removed. Called at operation
        boundaries; there is no background cleaner."""
        from . import auto_install as ai_mod
        from . import jobresult
        from .paths import PathContainmentError
        from .updater_units import CONTROLLER_LOGS
        protected = {j.get("log") for j in self.active_jobs() if j.get("log")}
        protected = {f"{n}.log" for n in protected} | set(protected)
        # Housekeeping: drop `done` job-result markers older than the banner expiry (failed/unsafe stay),
        # and PROTECT the log of every undismissed failed/unsafe/incomplete-derived result so its banner's
        # View → /logs link is never dead. Naturally bounded — result markers reuse the finite job-log
        # names. All best-effort/read-only; a corrupt jobresults dir never breaks pruning.
        try:
            import time as _t
            jobresult.prune_done(self._paths, int(_t.time()), 60)
            live = {j.get("log") for j in self.active_jobs(cleanup=False) if j.get("log")}
            for log, rec in jobresult.read_results(self._paths):
                # `done` ages out; failed/unsafe (and starting/running that are no longer live → derived
                # incomplete) keep their log while the banner is up.
                if rec.get("state") != "done" or log in live:
                    protected.add(log)
        except Exception:
            pass
        # Protect the LIVE auto-install run's own component build/test logs (requirement #7): its
        # durable descriptors are this run's evidence — a mid-run prune must never remove a
        # component log the stream still owns, even if the retention budget is exceeded.
        # Retired runs' logs carry no such protection and age out normally.
        try:
            st = self.auto_install_status()
        except Exception:
            st = None
        auto_install_prefix = ""
        if st and not st.get("unsafe") and st.get("state") in ("preparing", "running"):
            try:
                # FULL-run-id component-log prefix (`auto-install-<run32>-`) — matches the
                # exact names the run writes; the 8-hex run-log prefix would not.
                auto_install_prefix = ai_mod.component_log_prefix(st["run_id"])
            except (ValueError, KeyError, TypeError):
                auto_install_prefix = ""
        # Protect the LIVE HMAC apply run's detailed build log(s), bound to the FULL run id — while the run
        # is RUNNING or UNSAFE/unresolved (they may be needed to diagnose an unverified cancellation).
        hmac_prefix = ""
        try:
            hst = self.hmac_apply_status()
        except Exception:
            hst = None
        import re as _re2
        if (hst and not hst.get("unsafe") and hst.get("phase") in ("running", "unsafe")
                and _re2.fullmatch(r"[0-9a-f]{32}", hst.get("run_id", ""))):
            # Protect EVERY per-step leaf of the live run (build- AND secret/bridge/node frames), not just
            # `-build-`: `build()` prunes at the end of the firmware step and the earlier secret frame (older
            # mtime) would otherwise be an eviction candidate, blanking it from the second window mid-run.
            hmac_prefix = f"hmac-apply-{hst['run_id']}-"
        # FAIL-CLOSED logs-root resolution + enumeration (no `is_dir()`/`glob`/`is_symlink`):
        # `under` resolves the path (an ESCAPING `logs/` symlink raises PathContainmentError
        # DURING construction — this was outside the guard and could 500 via build()/
        # spawn_web_job()), the no-follow scandir refuses a symlinked/swapped logs dir, and
        # a missing/unreadable directory is an ordinary OSError. Any of these returns 0
        # safely — nothing outside the runtime root is ever read, followed, or deleted.
        try:
            d = self._paths.under("logs")
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except (OSError, PathContainmentError, ValueError):
            return 0
        import stat as _stat
        logs = []
        for name, is_link in entries:
            if is_link or not name.endswith(".log"):
                continue
            # A registered RF log is persistent by contract (its writer keeps the descriptor
            # open and the file is meant to outlive every job log): it is neither a deletion
            # candidate nor part of the count/byte budget the job logs share.
            if _rflog.by_job(name) is not None:
                continue
            # The controller's own logs are capped in place by the console's pass (A1) and held open by
            # their writers: deleting one would leave the writer filling an unlinked file. Like RF logs,
            # they are neither deletion candidates nor part of the budget.
            if name in CONTROLLER_LOGS:
                continue
            f = d / name
            # REGULAR FILES ONLY, via a DESCRIPTOR-SAFE stat (a path-based lstat could
            # follow a `logs/` parent swapped after enumeration). A directory named
            # `bad.log`, a FIFO/socket/device node, an unreadable leaf, or any
            # uncertainty (`None`) is RETAINED and skipped — never a deletion candidate
            # (an `os.unlink` on a directory would raise IsADirectoryError and escape).
            stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
            if stt is None or not _stat.S_ISREG(stt.st_mode):
                continue
            logs.append((stt.st_mtime, name, f, stt.st_size))
        logs.sort(reverse=True)                                    # newest first
        removed, kept, total = 0, 0, 0
        for _mtime, name, f, size in logs:
            if (name in protected or (auto_install_prefix and name.startswith(auto_install_prefix))
                    or (hmac_prefix and name.startswith(hmac_prefix))):
                continue
            kept += 1
            total += size
            if kept > self.LOG_RETENTION or total > self.LOG_RETENTION_BYTES:
                try:
                    runtime_fs.unlink(self._paths, f)  # descriptor-safe, refuses a symlink
                    removed += 1
                except (OSError, PathContainmentError):
                    pass                               # refused/failed delete -> retain,
                    # never raise, never increment the count (a leaf swapped to a dir or
                    # symlink between stat and unlink lands here safely)
        removed += jobs.prune_ephemeral_launchers(self._paths, self.LOG_RETENTION)
        live = self._live_post_results()
        if live is not None:                 # unknown inventory -> keep every sidecar
            removed += jobs.prune_post_results(self._paths, self.LOG_RETENTION, live)
        return removed

    def _live_post_results(self) -> frozenset | None:
        """Result sidecars a LIVE launch still reads: the required run's leaf of every recorded
        main launch and the result file of every recorded post runner. None when the ownership
        inventory cannot be read — the caller then prunes no sidecar at all (fail-closed)."""
        from pathlib import Path as _P
        try:
            life = self._lifecycle()
            valid, issues, state = life.owned_inventory()
        except Exception:
            return None
        if state == "unsafe" or issues:          # a skipped record could be a live one
            return None
        names = set()
        for rec in valid:
            if rec.get("role", "") == "" and rec.get("launch_id"):
                names.add(life.required_result_leaf({"main_launch_id": rec["launch_id"]}))
            elif rec.get("role") == "post" and rec.get("result_path"):
                names.add(_P(str(rec["result_path"])).name)
        return frozenset(names)

    def _track_or_terminate(self, life, log_name: str, pid: int, cid: str, op: str,
                            attempt_id: str = "",
                            ident: dict | None = None) -> tuple[jobs.TrackOutcome, str]:
        """Persist a job marker; if it cannot be persisted, terminate the (identity-
        verified) spawned session so it never leaks as an untracked orphan. Returns
        `(jobs.TrackOutcome, message)`: TRACKED with "", else TERMINATED or
        TERMINATION_UNVERIFIED with a visible error for the operator — callers decide on the
        outcome, never on the message. `ident` is the identity the caller captured
        IMMEDIATELY after the spawn (the web spawners capture it while they still hold task
        admission, release admission, then publish through here — so the child's own admission
        never sees the parent as an external holder)."""
        # Capture the identity IMMEDIATELY after spawn and use exactly that for both the
        # marker and any cleanup — never re-read a possibly-reused pid as the original job.
        if ident is None:
            ident = procident.proc_identity(pid)
        if jobs.write_job_marker(self._paths, log_name, pid, cid, op, ident=ident, attempt_id=attempt_id):
            return jobs.TrackOutcome.TRACKED, ""
        killed = life._terminate_unobserved(pid, ident)
        if killed:
            return jobs.TrackOutcome.TERMINATED, (
                f"{op} '{cid}' spawned but its job marker could not be persisted; "
                "the process was terminated (not left orphaned).")
        return jobs.TrackOutcome.TERMINATION_UNVERIFIED, (
            f"{op} '{cid}' spawned but its job marker could not be persisted AND the "
            "process could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")

    def active_jobs(self, cleanup: bool = True, *, include_unsafe: bool = False) -> list[dict]:
        """Build/test jobs whose ORIGINAL process is still alive (identity-verified).
        A reused PID, a malformed marker, or a symlinked marker is never treated as a
        live job; proven-finished markers are cleaned through the safe API.

        `cleanup=True` (op-boundary callers) clears the marker of a finished/reused job.
        `cleanup=False` is STRICTLY READ-ONLY (no unlink) — the GET-path running-task
        projection uses it so `/api/tasks` never mutates state.

        `include_unsafe=True` (the SELF-UPDATE/uninstall admission gate) ADDITIONALLY reports every
        marker that cannot be PROVEN safe — an unreadable jobs directory, a symlinked/non-regular/
        oversized/uninspectable/malformed marker — as `{"unsafe": True, "name", "reason"}`. The gate
        must block on any such entry: an update/uninstall proceeding past an unprovable job marker
        could tear down a still-running build. (`cleanup=False` is implied when include_unsafe is set —
        the gate never mutates.)

        UNTRUSTED-STATE SAFE (P2): each marker is inspected non-blocking, no-follow,
        regular-only, and BYTE-BOUNDED. A FIFO/device (would block), a directory, a
        symlink/swapped leaf, an oversized or malformed marker is treated as diagnostic
        evidence — never blocks, never trusted as active, never followed, and never
        auto-deleted merely for being malformed. No exception escapes into the callers
        (`prune_logs`/`build`/`test`/`spawn_web_job`)."""
        import stat as _stat
        import tomllib
        d = jobs.jobs_dir(self._paths)
        # Descriptor-safe enumeration (no `is_dir()`/`glob`): a symlinked/escaping jobs dir
        # fails closed (no trusted jobs); a symlinked marker LEAF is diagnostic evidence,
        # never treated as a live job.
        if include_unsafe:
            cleanup = False                     # the admission gate is strictly read-only
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except (OSError, PathContainmentError) as exc:
            # An unreadable/escaping jobs directory cannot prove NO job is running -> a blocker.
            return [{"unsafe": True, "reason": f"jobs directory unreadable: {exc}"}] if include_unsafe else []
        out = []
        for name, is_link in sorted(entries):
            if not name.endswith(".job"):
                continue
            if is_link:
                if include_unsafe:
                    out.append({"unsafe": True, "name": name, "reason": "job marker is a symlink"})
                continue
            f = d / name
            # DESCRIPTOR-SAFE gate: regular file, and small enough to be a real marker.
            # An oversized marker is untrusted (a bounded read could truncate it to a
            # still-parseable prefix and be wrongly trusted) — skip it, do not read it.
            stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
            if stt is None or not _stat.S_ISREG(stt.st_mode) \
                    or stt.st_size > self._JOB_MARKER_MAX:
                if include_unsafe:
                    why = ("uninspectable" if stt is None
                           else "non-regular" if not _stat.S_ISREG(stt.st_mode) else "oversized")
                    out.append({"unsafe": True, "name": name, "reason": f"job marker {why}"})
                continue                        # non-regular/oversized -> untrusted, retain
            try:
                # BOUNDED, non-blocking, no-follow, regular-only read (never blocks on a
                # FIFO, never follows a swapped/symlinked marker, never reads unbounded).
                raw = tomllib.loads(runtime_fs.read_text_regular(
                    self._paths, f, max_bytes=self._JOB_MARKER_MAX))
                pid = int(raw["pid"])
            except (OSError, PathContainmentError, KeyError, ValueError,
                    tomllib.TOMLDecodeError) as exc:
                if include_unsafe:
                    out.append({"unsafe": True, "name": name,
                                "reason": f"job marker malformed/unreadable: {exc}"})
                continue                        # malformed/unsafe -> retain for diagnosis
            if procident.identity_matches(raw, pid):
                out.append({"log": raw.get("log"), "target": raw.get("target"),
                            "op": raw.get("op"), "stack": self.stack_of(raw.get("target", "")),
                            "attempt_id": raw.get("attempt_id", "")})
            elif cleanup:
                # Finished/reused -> clear the marker. If it RACED into a symlink OR a
                # DIRECTORY (IsADirectoryError) between the no-follow read and now, the
                # safe unlink refuses/fails: retain it as evidence rather than letting this
                # public read path throw into prune_logs()/build()/test()/spawn_web_job().
                try:
                    runtime_fs.unlink(self._paths, f)
                except (OSError, PathContainmentError):
                    pass
        return out

    def logs(self, target: str, lines: int = 200, band: str = "") -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        details = []
        for _, comp in items:
            path, tail = life.logs(comp, lines, band=band)
            if not path:
                details.append(f"[{comp.id}] no log found")
                continue
            details.append(f"[{comp.id}] {path} (last {len(tail)} lines):")
            details.extend(f"  {ln}" for ln in tail)
        return ActionResult(True, f"Logs for '{target}' (bounded tail).", details=details,
                            next_commands=[f"lhpc status {target}"])

    def _read_job_marker(self, job: str) -> dict | None:
        """THE centralized bounded, no-follow, regular-file-ONLY reader of a job identity marker
        (`state/jobs/<job>.job`) — reused by `log_running`, active-job inspection, and HMAC abort. Returns
        the parsed dict, or None for a missing/symlinked/oversized/malformed marker. NEVER deletes or
        mutates evidence (a caller that needs to signal validates identity/op/target on the returned dict
        immediately before acting)."""
        import stat as _stat
        import tomllib
        try:
            slug = validators.path_component(job, field="job log")
            f = self._paths.under("state", "jobs", slug + ".job")
        except (validators.ValidationError, PathContainmentError):
            return None
        stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
        if stt is None or not _stat.S_ISREG(stt.st_mode) or stt.st_size > self._JOB_MARKER_MAX:
            return None
        try:
            return tomllib.loads(runtime_fs.read_text_regular(
                self._paths, f, max_bytes=self._JOB_MARKER_MAX))
        except (OSError, PathContainmentError, ValueError, tomllib.TOMLDecodeError):
            return None

    def log_running(self, target: str, job: str | None = None) -> bool:
        """Whether the process behind a log is still alive: for a build/test `job`
        it checks the job marker's pid; for a process log it checks the target's
        main component run-state."""
        if job:
            raw = self._read_job_marker(job)
            if not raw or "pid" not in raw:
                return False
            try:
                # FULL identity match (PID-reuse-safe), never bare PID liveness: a recycled
                # pid running an unrelated process is not this job.
                return procident.identity_matches(raw, int(raw["pid"]))
            except (KeyError, ValueError, TypeError):
                return False
        s = self.stack(target)
        cid = s.main_component.id if (s and s.main_component) else target
        for ss in self.build_snapshot(sources=False).stacks:     # a run state: no git per poll
            st = ss.components.get(cid)
            if st is not None:
                return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
        return False

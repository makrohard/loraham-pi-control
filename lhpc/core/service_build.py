"""ControllerService mixin: build and test, the build artifacts and inputs, and the install preconditions.

Adapters import lhpc.core.services only. Split from service_lifecycle_ops.py unchanged."""
from __future__ import annotations

import contextlib
from pathlib import Path

from . import web_client
from .lifecycle import GUI_MISSING_HINT
from .paths import PathContainmentError
from .service_base import (
    TXN_BLOCKED_REMEDY,
    ActionResult,
    AdmissionRefused,
    SourceTxnBlocked,
    admission_refusal,
    busy_remedy,
)
from .service_lifecycle_ops import _HMAC_LOG_BASE_RE
from .snapshot_memo import invalidates_snapshot


class BuildOpsMixin:

    def _global_operator_callsign(self) -> str:
        """The stored global operator callsign, or "" — used to identify a transmission from a
        target that has no identity field of its own (the daemon, kiss). Returned verbatim: it is
        the operator's own call, not something a stack inherits, so the base-callsign rule that
        governs INHERITANCE does not apply to identifying their own transmission."""
        return str(self.config().operator.callsign or "").strip()

    def _tx_band_conflict(self, target: str, bands: list) -> ActionResult | None:
        """The refusal of an app stack's TX test on a band another running stack uses, or None."""
        for b in bands:
            if (others := self._band_used_by_others(target, b)):
                return ActionResult(False, f"Cannot TX-test '{target}' on {b} MHz: that band "
                                           f"serves {', '.join(others)} — stop it first",
                                    next_commands=[f"lhpc status {target}"])
        return None

    @invalidates_snapshot
    def build(self, target: str, apply: bool = False, auto_install_ctx=None,
              on_component_log=None, log_base_override: str = "",
              redactor=None, should_cancel=None) -> ActionResult:
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        # A binary-installed stack has no source tree to build FROM (and its artifacts would be
        # overwritten by a half-built one). Refuse with the switch-back command, never attempt it.
        _sid = self.stack_of(target) or target
        if target != web_client.COMPONENT and (_blk := self.binary_block_reason(_sid, "build")):
            return ActionResult(False, f"Refusing to build '{target}': {_blk}",
                                next_commands=[f"lhpc install {_sid} --source pinned --yes"],
                                data={"binary_channel": True})
        # A caller-supplied run-specific log-base prefix (HMAC apply) is validated BEFORE any path is
        # constructed — a strict controller pattern bound to the FULL 32-hex run id (never marker-time only).
        if log_base_override and not _HMAC_LOG_BASE_RE.match(log_base_override):
            return ActionResult(False, f"Refusing to build '{target}': invalid log-base prefix",
                                details=["  nothing to run here — this is an lhpc defect (an "
                                         "internal caller passed a bad log name), not a problem "
                                         "on the box; report it with this message"])
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        # _resolve returns RUNNABLE components; the build must ALSO cover buildable
        # non-runnable sources (libraries like RadioLib — their artifacts are consumed
        # via build_requires, so skipping them silently pushed builds onto external
        # fallbacks outside the runtime root). The one selection rule: `build_plan.select`.
        from . import build_plan, jobresult
        by_all = {c.id: c for st in self.stacks() for c in st.components}
        owner_of = {c.id: st for st in self.stacks() for c in st.components}
        buildable = [(owner_of[c.id], c) for c in
                     build_plan.select([c for _, c in items], self.stack(target))]
        life = self._lifecycle()
        # MISSING-SOURCE PREFLIGHT — BEFORE the GUI one, and for the same reason: decide
        # explicitly instead of letting the build discover it. `Lifecycle.build` joins the
        # source path lexically and hands it to Popen as `cwd`, so an absent directory
        # surfaced as a bare rc-127 "Build FAILED", indistinguishable from a missing tool.
        # ORDER MATTERS: after the GUI preflight, a direct `lhpc build loraham-voice` on a
        # headless box returned the GUI no-work SUCCESS instead of saying it is not installed.
        # APPLY ONLY: a dry run executes nothing, so there is no cwd to be wrong and no
        # reason to refuse a plan. Gating it too would stop the console planning a build for
        # a stack the operator is about to install.
        _is_component_target = target != "" and self.stack(target) is None
        _absent = [(s, c) for s, c in buildable
                   if c.source and not self._source_present(c)] if apply else []
        if apply:
            # A `build_requires` dependency is consumed by the build (pip-installed from its
            # checkout), so its absence would surface as a bare pip "Directory does not exist"
            # mid-build. Refuse up front with the remedy — the LIBRARY component itself has no
            # build steps, so the missing-source preflight above never sees it.
            # Only a dependency with NO build steps of its own (a pure checkout, e.g. the pinned
            # openhop repeater source) is decided here: one with build steps is a `buildable`
            # entry the missing-source preflight below already judges (optional -> skipped).
            _deps_absent = sorted({(s.id, d.id) for s, c in buildable
                                   for d in (by_all.get(x) for x in c.build_requires)
                                   if d is not None and d.source and not d.build_steps
                                   and not self._source_present(d)})
            if _deps_absent:
                return ActionResult(
                    False,
                    f"Refusing to build '{target}': build dependency "
                    f"{', '.join(sorted({d for _, d in _deps_absent}))} is not installed.",
                    details=[f"  [not-installed] {d}: no source at "
                             f"{life.source_dir(by_all[d])} — install the stack to adopt it"
                             for _, d in _deps_absent],
                    next_commands=[f"lhpc install {sid}" for sid in sorted({s for s, _ in _deps_absent})])
        if _absent:
            # A NAMED component is always refused — the operator asked for that one by name,
            # and reporting "nothing to do" would hide that it is not installed. Within a
            # stack (or a bulk build) an OPTIONAL absent component is simply skipped.
            _refused = [(s, c) for s, c in _absent if _is_component_target or not c.optional]
            if _refused:
                _sids = sorted({s.id for s, _ in _refused})
                return ActionResult(
                    False,
                    f"Refusing to build '{target}': "
                    f"{', '.join(sorted(c.id for _, c in _refused))} is not installed.",
                    details=[f"  [not-installed] {c.id}: no source at "
                             f"{life.source_dir(c)}" for _, c in _refused],
                    next_commands=[f"lhpc install {sid}" for sid in _sids])
            _absent_ids = sorted(c.id for _, c in _absent)
            buildable = [(s, c) for s, c in buildable if c.id not in set(_absent_ids)]
            if not buildable:
                return ActionResult(
                    True,
                    f"Nothing to build for '{target}': all requested component(s) skipped "
                    f"({', '.join(_absent_ids)}) — not installed.",
                    details=[f"  [skip] {cid}: not installed" for cid in _absent_ids],
                    next_commands=[f"lhpc status {target}"],
                    data={"skipped": _absent_ids, "built": 0, "changes": 0})
        # GUI PREFLIGHT — the SAME predicate auto-install planning uses, applied HERE: before the
        # index/source locks, before any build marker and before a single build step runs. A
        # component whose GUI toolkit is absent must be refused (mandatory) or dropped (optional)
        # by an explicit decision, never discovered by letting pkg-config or an import fail
        # halfway through a build.
        gui_skip: set = set()
        for s in {s.id: s for s, _ in buildable}.values():
            gui_skip |= set(self.gui_unavailable_components(s))
        gui_dropped: list = []
        if gui_skip:
            refused = [(s, c) for s, c in buildable
                       if c.id in gui_skip and not (c.optional or c.gui_optional)]
            if refused:
                return ActionResult(
                    False,
                    f"Refusing to build '{target}': {', '.join(c.id for _, c in refused)} "
                    f"needs a GUI toolkit that is not installed.",
                    details=[f"  {GUI_MISSING_HINT}"],
                    next_commands=["lhpc deps --script > bootstrap-deps.sh"])
            gui_dropped[:] = sorted(c.id for _, c in buildable if c.id in gui_skip)
            buildable = [(s, c) for s, c in buildable if c.id not in gui_skip]
            if not buildable:
                # EVERY requested component was skipped (e.g. a direct `lhpc build loraham-voice`
                # on a headless box). This is NOT a build: return a typed skipped/no-work result and
                # execute no build step, no marker mutation and no source lock. Reporting "succeeded"
                # here would claim an artifact that was never produced.
                return ActionResult(
                    True,
                    f"Nothing to build for '{target}': all requested component(s) skipped "
                    f"({', '.join(gui_dropped)}) — GUI dependencies not installed.",
                    details=[f"  [skip] {cid}: {GUI_MISSING_HINT}" for cid in gui_dropped],
                    next_commands=[f"lhpc status {target}"],
                    data={"skipped": list(gui_dropped), "built": 0, "changes": 0})
        # BUILD-DEPENDENCY order: a component's build_requires providers build FIRST
        # (fresh root: RadioLib's libRadioLib.a must exist before the daemon's build.sh
        # consumes it). Stable within equal rank (manifest order preserved).
        buildable = [(owner_of[c.id], c) for c in build_plan.order([c for _, c in buildable])]
        if not apply:
            details = [f"  [build] {c.id}: "
                       + " ; ".join(" ".join(str(t) for t in st.get("argv", []))
                                    for st in c.build_steps) for _, c in buildable]
            details += [f"  [skip] {cid}: {GUI_MISSING_HINT}" for cid in gui_dropped]
            return ActionResult(True, f"Build plan for '{target}': {len(buildable)} component(s).",
                                details=details,
                                next_commands=[f"lhpc build {target} --yes"] if buildable else [],
                                data={"changes": len(buildable)})
        # ONE atomic guard — index lock, recover, block on any unresolved journal,
        # then the source-path lock(s) (handoff) held for the whole build. No
        # preflight/acquire race: a journal that appears after a failed transaction is
        # caught under the index lock before the source locks are taken.
        from . import reslock
        # Every source the build consumes, transitively, stays locked for the whole build.
        src_paths = build_plan.lock_sources([c for _, c in buildable], by_all)
        ctx_err = self._auto_install_ctx_error(auto_install_ctx, src_paths)
        if ctx_err:
            return ActionResult(False, f"Refusing to build '{target}': {ctx_err}",
                                details=["  nothing to run here — this is an lhpc defect (the "
                                         "auto-install run's lock context does not match), not a "
                                         "problem on the box; report it with this message"])
        try:
            with self._source_operation_guard(src_paths, op="build"):
                # A MeshCore build recreates src/openhop-core/.venv — the interpreter an orphaned
                # plugin manager (a manager or node that died without its graceful shutdown) may
                # still be running from. LHPC never spawned those processes and cannot see them;
                # the same-boot marker is the evidence (meshcore_plugins.py). Under the lock, before
                # any mutation, the same gate update/uninstall/clean apply.
                if (_mp := self._meshcore_plugins_refusal(f"build '{target}'",
                                                          {c.id for _, c in buildable})):
                    return _mp
                from . import auto_install as ai_mod
                details = []
                ok = True
                run_id = getattr(auto_install_ctx, "run_id", "") if auto_install_ctx else ""
                build_meta: dict = {}
                for _, comp in buildable:
                    if comp.id == web_client.COMPONENT and (_w := web_client.kept_index_install(self)):
                        ok = ok and _w.ok                 # a kept stable/dev client (D3)
                        details += [f"  [{'done' if _w.ok else 'failed'}] {_w.summary}", *_w.details]
                        continue
                    # auto-install / HMAC: run-specific log base + DURABLE ordered registration BEFORE the
                    # build runs, so the live stream shows only this run's own logs (the file does not
                    # yet exist under a run-specific name -> no prior content).
                    log_base = None
                    if run_id and on_component_log is not None:
                        log_base = ai_mod.component_log_base(run_id, f"build-{comp.id}")
                    elif log_base_override:
                        log_base = f"{log_base_override}-build-{comp.id}"
                    if log_base is not None and on_component_log is not None:
                        n = len(comp.build_steps)
                        for i in range(n):
                            fn = f"{log_base}-{i}.log" if n > 1 else f"{log_base}.log"
                            title = (f"{comp.name} — Build log (step {i + 1}/{n})"
                                     if n > 1 else f"{comp.name} — Build log")
                            on_component_log(f"{title} · logs/{fn}", fn)   # per-step path shown in the web view
                    # read under the locks, never from the request memo
                    receipt = build_plan.consumed_lines(
                        build_plan.consumed_sources(comp, by_all, life.source_dir,
                                                    self.binary_capable(comp.id)), self._git_out)
                    rev = self._own_rev(comp)
                    with (web_client.lock(self._paths) if comp.id == web_client.COMPONENT
                          else contextlib.nullcontext()):
                        res = life.build(comp, log_base=log_base,
                                         redactor=redactor, should_cancel=should_cancel,
                                         on_log_open=self._log_announcer(comp.id, details),
                                         marker_extra=receipt,
                                         inputs=self._build_inputs_to_record(comp), rev=rev)
                        if res.ok and comp.id == web_client.COMPONENT:
                            web_client.record_binary(self)     # under the same lock as the swap
                    ok = ok and res.ok
                    if res.ok:                  # a console build's failure of it is now stale
                        jobresult.supersede(self._paths, f"build-{comp.id}.log")
                    details.append(f"  [{res.state.value}] build {comp.id} "
                                   f"(rc {res.returncode}, log {res.log_path})")
                    details += [f"  [unverified] {ln.split()[1]}: its revision could not be read "
                                "— the build reads as not built; rebuild it (`lhpc build "
                                f"{owner_of[comp.id].id} --yes`) once its revision can be read"
                                for ln in (receipt + (rev[1] if rev else "")).splitlines()
                                if ln.endswith(" unknown")]
                    if not res.ok:
                        details.extend(f"      {ln}" for ln in res.tail[-6:])
                    # Surface a cooperative-cancellation / UNVERIFIED-stop outcome so the caller (the HMAC
                    # driver) can persist the correct terminal state; stop the sweep on it.
                    if getattr(res, "cancelled", False) or getattr(res, "unsafe", False):
                        build_meta = {"cancelled": res.cancelled, "unsafe": res.unsafe,
                                      "unsafe_scope": res.unsafe_scope,
                                      "session_ident": res.session_ident}
                        ok = False
                        break
        except AdmissionRefused as _adm:
            return admission_refusal(_adm)
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Build blocked for '{target}': {blocked}",
                                details=[TXN_BLOCKED_REMEDY],
                                next_commands=[f"lhpc build {target} --yes"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Build blocked for '{target}': {busy}",
                                details=[busy_remedy(busy)],
                                next_commands=[f"lhpc build {target} --yes"])
        self.prune_logs()
        # Skips are part of the RESULT, not a discarded preflight detail: a run that built the
        # headless components while dropping a GUI one must say so, never a bare "succeeded".
        details = details + [f"  [skip] {cid}: {GUI_MISSING_HINT}" for cid in gui_dropped]
        if ok:
            summary = (f"Build succeeded with GUI skips for '{target}' "
                       f"({', '.join(gui_dropped)} skipped)." if gui_dropped
                       else f"Build succeeded for '{target}'.")
        else:
            summary = f"Build FAILED for '{target}'."
        if gui_dropped:
            build_meta = {**build_meta, "skipped": list(gui_dropped)}
        # A failed build names its log and the retry, not only the status.
        return ActionResult(ok, summary, details=details,
                            next_commands=(["lhpc status " + target] if ok else
                                           [f"lhpc logs {target}", f"lhpc build {target} --yes"]),
                            data=build_meta)

    def _git_out(self, argv) -> tuple[int, str]:
        """`build_plan.consumed_lines`'s runner on the CLI path and in `is_built`: the injected
        system's runner, bounded at 5 s."""
        r = self._system.runner.run(argv, 5.0)
        return getattr(r, "returncode", 1), (r.stdout or "")

    @invalidates_snapshot
    def test(self, target: str, tx: bool = False,
             apply: bool = False, auto_install_ctx=None, on_component_log=None,
             should_cancel=None) -> ActionResult:
        """Run host tests (RX-safe) or a bounded one-frame TX test (`tx=True`). `should_cancel`
        (auto-install Abort) is polled while each host test runs."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        ctx_err = self._auto_install_ctx_error(auto_install_ctx,
                                       {c.source.path for _, c in items if c.source})
        if ctx_err:
            return ActionResult(False, f"Refusing to test '{target}': {ctx_err}")
        life = self._lifecycle()
        if not tx and (_blk := self.binary_block_reason(self.stack_of(target) or target,
                                                        "host tests")):
            # The repo's own test suite lives in the source checkout; a binary install has none.
            # (A bounded TX test exercises the RUNNING stack and stays available.)
            return ActionResult(False, f"Refusing to host-test '{target}': {_blk}",
                                data={"binary_channel": True, "skipped": "binary-install"})
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
            # MISSING-SOURCE PREFLIGHT (as in build): a host test runs inside the checkout, so
            # an absent source is refused explicitly — never discovered as a bare rc 127. A
            # NAMED component is always refused; within a stack an OPTIONAL one is skipped.
            _named = target != "" and self.stack(target) is None
            _absent = {c.id for _, c in items
                       if c.source and c.test_argv and not self._source_present(c)}
            _refused = [c for _, c in items if c.id in _absent and (_named or not c.optional)]
            if _refused:
                return ActionResult(
                    False,
                    f"Refusing to test '{target}': "
                    f"{', '.join(sorted(c.id for c in _refused))} is not installed.",
                    details=[f"  [not-installed] {c.id}: no source at {life.source_dir(c)}"
                             for c in _refused],
                    next_commands=[f"lhpc install {sid}" for sid in
                                   sorted({self.stack_of(c.id) or c.id for c in _refused})])
            items = [(s, c) for s, c in items if c.id not in _absent]
            # ONE atomic guard (index→recover→block→source-lock handoff) held for
            # the whole host-test run — a host test depends on the source's build
            # artifacts, so a concurrent update can't swap the tree mid-test, and a
            # retained journal blocks it with no preflight race.
            from . import reslock
            details = []
            ok = True
            test_meta: dict = {}
            src_paths = sorted({c.source.path for _, c in items if c.source and c.test_argv})
            from . import auto_install as ai_mod
            run_id = getattr(auto_install_ctx, "run_id", "") if auto_install_ctx else ""
            try:
                with self._source_operation_guard(src_paths, op="host-test"):
                    for _, comp in items:
                        # An integration test needs the stack already running. An auto-install/auto-install
                        # sweep builds without starting, so DEFER it there (never a false failure);
                        # an explicit `lhpc test` (no auto_install_ctx) runs it against the running stack.
                        if comp.test_argv and comp.test_requires_running and auto_install_ctx is not None:
                            details.append(f"  [deferred] {comp.id}: needs the running stack "
                                           f"(run `lhpc test {target}` after starting it)")
                            continue
                        # auto-install: run-specific test-log base + DURABLE ordered registration
                        # BEFORE the test runs (same guarantees as the build path).
                        log_base = None
                        if run_id and on_component_log is not None and comp.test_argv:
                            log_base = ai_mod.component_log_base(run_id, f"test-{comp.id}")
                            on_component_log(f"{comp.name} — Test log · logs/{log_base}.log", f"{log_base}.log")
                        res = life.host_test(comp, log_base=log_base, should_cancel=should_cancel,
                                             on_log_open=self._log_announcer(comp.id, details))
                        if res is None:
                            details.append(f"  [skip] {comp.id}: no host test")
                            continue
                        ok = ok and res.ok
                        details.append(f"  [{res.state.value}] {comp.id} "
                                       f"(rc {res.returncode}, log {res.log_path})")
                        # Surface cooperative-cancellation / UNVERIFIED-stop (mirror build()) so the
                        # auto-install driver persists the correct terminal state; stop the sweep on it.
                        if getattr(res, "cancelled", False) or getattr(res, "unsafe", False):
                            test_meta = {"cancelled": res.cancelled, "unsafe": res.unsafe,
                                         "unsafe_scope": res.unsafe_scope,
                                         "session_ident": res.session_ident}
                            ok = False
                            break
            except AdmissionRefused as _adm:
                return admission_refusal(_adm)
            except SourceTxnBlocked as blocked:
                return ActionResult(False, f"Host test blocked for '{target}': {blocked}",
                                    next_commands=[f"lhpc status {target}"])
            except reslock.ResourceBusy as busy:
                return ActionResult(False, f"Host test blocked for '{target}': {busy}",
                                    next_commands=[f"lhpc status {target}"])
            return ActionResult(ok, f"Host test {'passed' if ok else 'FAILED'} for '{target}'.",
                                details=details, next_commands=[f"lhpc status {target}"],
                                data=test_meta)

        # TX-capable test — explicit, gated, bounded. TX is a DAEMON operation; the
        # band(s) come from the target's components (or, for the daemon itself, the
        # bands it is serving), and we only test bands the daemon actually serves.
        wanted = []
        for _, c in items:
            for b in ([c.band] if c.band else []) + list(c.bands):
                if b and b not in wanted:
                    wanted.append(b)
        # served bands only (radio mode); only a target with no band of its own (the daemon)
        # falls back to every band it serves — a client is never moved to another band.
        wanted = [b for b in wanted if self.band_active(b)] if wanted else list(self.active_bands())
        # A TX test drives real RF, so it requires the radio to be READY (not merely a
        # reachable CONF socket): a FAILED/UNINITIALIZED radio must never be TX-tested.
        bands = [b for b in wanted if self.daemon_view(b).ready]
        # An app stack never transmits on a band another running stack uses. Checked here for the
        # plan, and again under the bands' claims right before the frames go out (see below).
        app_target = not self._is_daemon_target(target)
        if app_target and (refused := self._tx_band_conflict(target, bands)):
            return refused
        if not bands:
            return ActionResult(
                False, f"Cannot TX-test '{target}': no daemon-served radio is READY on "
                f"{' or '.join(wanted)} MHz. A daemon stack (chat/kiss/voice/meshcom/meshcore) needs the "
                f"daemon started + RADIO=READY first; a stack that drives its OWN radio (e.g. meshtastic) "
                f"is not daemon-TX-testable — verify its TX from its own app/logs instead.",
                next_commands=[f"lhpc status {target}"])
        # A STACK TX test identifies as the stack's EFFECTIVE identity (local override,
        # else the inherited global for licensed stacks; the local node name for
        # unlicensed) — never the raw global when the stack overrides it, and never a
        # hidden placeholder. The standalone auto-install daemon TX phase keeps requiring
        # the global operator identity: that transmission is the operator's own.
        # A TX operation MUST identify the station. There is no unidentified payload to fall back
        # to: an empty identity refuses the whole test, before the plan is rendered and before any
        # frame is transmitted (the suffix was conditional, so a box with no global
        # callsign put a bare `LHPC TX TEST` frame on the air).
        # Per-BAND: a band-scoped local override identifies the transmission on ITS band.
        # The identity must be VALID, not merely present. A stack's own identity was validated
        # when it was stored; the global fallback (daemon, kiss) was not — the stored global may be a
        # placeholder or malformed value that this code refuses to inherit, and transmitting
        # `DE N0CALL` is no better than transmitting unidentified. Judged by the
        # same base-callsign rule that decides whether a global is usable as an identity at all.
        from . import validators
        _idents = {}
        for b in bands:
            if self._identity_fields(target):
                # A stack with its OWN identity is judged by THE identity gate, per band. An
                # explicit local value that the current rules reject must REFUSE, never fall back
                # to the global: `effective_identity` returns "" both when no local value exists
                # and when one exists but is invalid, and treating those alike transmitted under a
                # different callsign than the operator configured.
                ok, _fields, msg = self.enforce_identity(target, b)
                if not ok:
                    return ActionResult(
                        False, f"Cannot TX-test '{target}': {msg}",
                        details=["  [blocked] a TX test identifies the station on air"],
                        next_commands=self._identity_config_hints(target, b))
                _idents[b] = self.effective_identity(target, b)
                continue
            # Only a target with NO identity field of its own (the daemon, kiss) identifies with
            # the operator's global — validated, because the stored global may be a placeholder.
            try:
                _idents[b] = validators.callsign_base(self._global_operator_callsign(),
                                                      field="operator callsign", allow_empty=False)
            except validators.ValidationError as exc:
                return ActionResult(
                    False, f"Cannot TX-test '{target}': {exc}",
                    details=["  [blocked] a TX test identifies the station on air"],
                    next_commands=["lhpc config operator --callsign YOURCALL"])
        if not all(_idents.values()):
            return ActionResult(
                False, f"Cannot TX-test '{target}': no station identity to transmit under.",
                details=["  [blocked] a TX test identifies the station; set an identity first"],
                next_commands=(self._identity_config_hints(target)
                               or ["lhpc config operator --callsign YOURCALL"]))

        def payload_for(b):
            return f"LHPC TX TEST DE {_idents[b]}"

        if not apply:
            details = ["TX-CAPABLE TEST — this transmits real RF.",
                       "Ensure the antennas are on the connected DUMMY LOADS.", ""]
            for band in bands:
                plan = life.plan_tx_test(target, band, payload_for(band))
                details += [f"  band      : {plan.band} MHz",
                            f"  parameters: {plan.parameters}",
                            f"  payload   : {plan.payload!r}",
                            f"  expected  : {plan.expected}", ""]
            return ActionResult(True, f"TX test plan for '{target}' (ONE frame per band).",
                                details=details,
                                next_commands=[f"lhpc test {target} --tx --yes"],
                                data={"changes": len(bands)})
        # An applied TX test transmits REAL RF — hold task admission (lock order #1) across the whole
        # transmit loop so it can never run concurrently with a controller self-update/uninstall. A
        # refusal returns BEFORE any frame is transmitted.
        from . import reslock as _reslock
        # An app stack's TX test also holds each band's `claim.loraham.radio.<band>` (lock order #2,
        # after admission) and re-checks under it: a start taking one of those bands after the check
        # above waits for the frames or is refused, never transmitted over.
        claims = [f"claim.loraham.radio.{b}" for b in bands] if app_target else []
        try:
            with self._admission_guard("tx-test", target), self._keys_guard("tx-test", target, claims):
                if app_target and (refused := self._tx_band_conflict(target, bands)):
                    return refused
                details = []
                ok = True
                attempted_bands: list = []
                for band in bands:
                    # Operator Abort between frames: stop BEFORE the next transmit. A bare `break` would
                    # be UNtruthful (ok stays True → "PASSED"); return a non-success cancelled result.
                    if should_cancel and should_cancel():
                        return ActionResult(
                            False, f"TX test cancelled by operator (Abort) after {len(attempted_bands)} "
                            "band attempt(s).", details=details,
                            data={"cancelled": True, "attempted_bands": attempted_bands})
                    attempted_bands.append(band)           # a frame may transmit even if unconfirmed
                    res = life.run_daemon_tx_test(band, payload_for(band))
                    ok = ok and res.ok
                    details.append(f"  [{'ok' if res.ok else 'fail'}] band {res.band}: {res.detail} "
                                   f"(TXOK {res.txok_before}->{res.txok_after})")
                return ActionResult(ok, f"TX test {'PASSED' if ok else 'did not confirm'} for '{target}'.",
                                    details=details,
                                    next_commands=[f"lhpc status {target}", f"lhpc logs {target}"])
        except AdmissionRefused as _adm:
            return admission_refusal(_adm)
        except _reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot TX-test '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])

    def run_params_for(self, target: str):
        """Run parameters offered for `target` (from its main/own components)."""
        s = self.stack(target)
        comps = s.components if s else (
            [self._component_index()[target][1]] if target in self._component_index() else [])
        params = []
        for c in comps:
            params.extend(c.run_params)
        return params

    def fetched_artifacts_present(self, target: str) -> bool:
        """Is there anything ON DISK that Uninstall/Clean would remove for a fetched-binary
        (source-less) stack — the `build_root` workspace or the artifact itself?

        This, not `is_built`, is what the console's removal buttons must gate on: an
        interrupted fetch (binary landed, completion marker never written) or a bumped pin
        (marker mismatches) reads NOT built, yet the stale artifact is exactly what the
        operator needs to remove before retrying. Gating removal on fully-built hid the
        buttons in precisely the states that need them.
        """
        s = self.stack(target)
        for c in (s.components if s else ()):
            if c.source:
                continue
            for rel in (c.build_root, c.bin):
                if not rel:
                    continue
                try:
                    if self._paths.under(*rel.split("/")).exists():
                        return True
                except (OSError, PathContainmentError):
                    continue
        return False

    def unbuilt_components(self, target: str) -> list[str]:
        """Component ids in `target` that need a Build before they can run. Empty = all ready.

        Two shapes qualify. A SOURCED component counts once its checkout exists and its artifact
        does not. A SOURCE-LESS component with build steps counts whenever its artifact is
        missing — that is a package-managed stack whose binary is fetched rather than compiled
        (graywolf), and it has no checkout to wait for. Without the second case the console
        showed no "build needed" state at all for such a stack, while the start gate still
        refused it as not built.
        """
        s = self.stack(target)
        # A gui_optional component whose GUI toolkit is absent can NEVER be built here —
        # the build preflight drops it by design (voice's GTK app on a Lite box, whose
        # shared checkout exists for the terminal variant). Counting it would wedge the
        # web start gate in a permanent needs-build loop no build can clear.
        gui_dropped = set(self.gui_unavailable_components(s)) if s else set()
        out = []
        for c in (s.components if s else ()):
            if c.id in gui_dropped and (c.optional or c.gui_optional):
                continue
            if c.source:
                if self._source_present(c) and not self.is_built(c):
                    out.append(c.id)
            elif c.build_steps and not self.is_built(c):
                out.append(c.id)
        return out

    def unbuilt_build_deps(self, target: str) -> list[str]:
        """The subset of `unbuilt_components` that are BUILD DEPENDENCIES — a `build_requires` provider
        of another component in the stack (e.g. RadioLib, consumed by the daemon's build.sh). These MUST
        be built BEFORE their consumer; the daemon build otherwise fails 'RADIOLIB_DIR not usable'."""
        s = self.stack(target)
        if s is None:
            return []
        providers = {d for c in s.components for d in (c.build_requires or ())}
        unbuilt = set(self.unbuilt_components(target))
        return [c.id for c in s.components if c.id in providers and c.id in unbuilt]

    def _build_artifact(self, comp):
        """Relative path of the built artifact (the explicit `bin`), or None when the component
        compiles nothing OR declares no build output to check. A component that compiles via
        `build_steps` (e.g. the RadioLib library, or a venv-based Python node) declares its real
        output via `bin` (the .a, or `.venv/bin/python`) so `is_built` is honest. `exec_name` is a
        process-match NAME, never a build-output PATH, so it is NOT used here — using it made venv
        components (exec_name="python") check a non-existent `<src>/python` and read "not built"
        forever."""
        if not comp.build_steps:
            return None
        return comp.bin or None

    def _consumed_source_lines(self, comp) -> str:
        """What this build consumed, as marker lines: one `consumed <id> <sha>` per source it
        installs FROM (the component's own checkout plus each `build_requires` dependency's),
        then one `input <name> <value>` per declared non-source build input.

        Recorded into the build marker and recomputed by `is_built`, so the marker is a
        receipt for the exact inputs consumed — a static marker stayed "built" after
        `rns-lora-interface` (or Reticulum itself) was updated, leaving the OLD driver
        installed in the venv while lhpc reported the component built.

        The non-source `build_inputs` are recorded BESIDE the marker, not inside it — see
        `build_inputs_path`. Putting them in the marker made the two sides of a release
        impossible to order: an artifact carrying them reads NOT built to a controller that does
        not know about them, and an artifact without them reads NOT built to one that does, so
        neither publishing first nor releasing first was safe.
        """
        if not comp.build_marker:
            return ""
        return self._request_memo(("consumed-source", comp.id),
                                  lambda: self._consumed_source_lines_uncached(comp))

    def _own_rev(self, comp):
        """`(path, text)` of the side file a SOURCE build of a binary-capable component writes
        beside its marker — `consumed <id> <rev>`, its own checkout's revision — or None. The
        marker itself stays the static text, because an artifact ships it; `is_built` checks the
        side file only while the stack is not on the binary channel. Read fresh (under the
        build's locks); `is_built` uses `_own_rev_expected`."""
        from . import build_plan
        own = self._own_rev_source(comp)
        return (own[0], build_plan.consumed_lines(own[1], self._git_out)) if own else None

    def _own_rev_source(self, comp):
        """`(side file path, [(id, source dir)])` for `_own_rev`, or None — the console's
        detached build reads the revision itself, under its locks."""
        if not (comp.build_marker and comp.source and self.binary_capable(comp.id)):
            return None
        src = self._lifecycle().source_dir(comp)
        return src / (comp.build_marker + ".rev"), [(comp.id, str(src))]

    def _own_rev_expected(self, comp):
        return self._request_memo(("own-rev", comp.id), lambda: self._own_rev(comp))

    def _consumed_source_lines_uncached(self, comp) -> str:
        # The build's own computation (`build_plan`), so what is compared is what was written.
        from . import build_plan
        by_id = {c.id: c for st in self.stacks() for c in st.components}
        return build_plan.consumed_lines(
            build_plan.consumed_sources(comp, by_id, self._lifecycle().source_dir,
                                        self.binary_capable(comp.id)), self._git_out)

    def _build_inputs_to_record(self, comp):
        """`(path, text)` for the sidecar a build must write, or None when the component records
        nothing. A packaged asset that is missing makes the digest unreadable; the build step that
        consumes it fails on its own (`_asset_token`), so nothing is recorded rather than a lie."""
        try:
            text = self.build_inputs_text(comp)
        except FileNotFoundError:
            return None
        return (self.build_inputs_path(comp), text) if text else None

    def build_inputs_path(self, comp):
        """Where a component's recorded non-source build inputs live: beside its BUILT ARTIFACT.

        A separate file, and in that particular place, for two different compatibility reasons
        that were each learned the hard way.

        Separate, because the marker's content is compared byte for byte by every version of this
        controller that ever shipped: adding to it would make a published artifact read NOT built
        on every box that had not upgraded yet.

        Beside `bin` rather than beside the marker, because a published artifact may only contain
        members inside the PUBLISH ROOTS of the manifest doing the installing — and those come
        from the installed controller, not from the artifact. A file next to the source marker
        needed a new root, which a released controller does not have, so the whole artifact was
        refused on every box while installing perfectly on the candidate that had widened its own
        roots. `bin` is inside a publish root by definition for a stack with a binary channel.
        """
        rel = comp.bin
        if "{" in rel:                       # the same run-param substitution `is_built` applies to `bin`
            cfg = self.stack_config(self.stack_of(comp.id) or "")
            for prm in comp.run_params:
                rel = rel.replace("{" + prm.name + "}", cfg.get(prm.name, prm.default))
        return (self._artifact_anchor(comp, rel) / rel).with_name(".lhpc-build-inputs")

    def _artifact_anchor(self, comp, rel: str):
        """What a component's `bin` is relative to. The recipes use one convention: a build output
        under the runtime's shared tool areas (`build/tools/…`, `build/tool-cache/…` — the
        `{runtime}/build/tools/…` paths the run lines name) is runtime-anchored, and so is anything
        of a component without a source checkout; every other `bin` (a venv interpreter, a `.work`
        firmware image, a library in the checkout's own `build/`) lives in the checkout. The
        sidecar follows the artifact: beside `build/tools/meshtasticd/meshtasticd` (the published
        location every released controller expects) or beside `src/openhop-core/.venv/bin/python`."""
        runtime = Path(self._paths.runtime_root)
        if not comp.source or rel.startswith(("build/tools/", "build/tool-cache/")):
            return runtime
        return self._lifecycle().source_dir(comp)

    def build_inputs_text(self, comp) -> str:
        """The sidecar's exact content: the declared `build_inputs`, then one `asset <rel> <sha256>`
        line per packaged asset the build steps consume (`Component.asset_inputs`), digested from
        the asset's CURRENT content. Compared byte for byte by `is_built`, so a changed asset —
        an lhpc update that ships a different meshcore_host, patch or fetch script — reads NOT
        built until the component is rebuilt. Raises FileNotFoundError for a missing asset."""
        lines = [f"input {n} {v}\n" for n, v in comp.build_inputs]
        lines += [f"asset {rel} {self._asset_digest(rel)}\n" for rel in comp.asset_inputs]
        return "".join(lines)

    def _asset_digest(self, rel: str) -> str:
        from . import assets
        return self._request_memo(("asset-digest", rel), lambda: assets.asset_digest(rel))

    def _inputs_recorded(self, comp) -> bool:
        """The recorded inputs are present and are the ones the manifest names."""
        from . import runtime_fs
        from .lifecycle import _BUILD_INPUTS_MAX
        try:
            return (runtime_fs.read_text_regular(self._paths, self.build_inputs_path(comp),
                                                 max_bytes=_BUILD_INPUTS_MAX)
                    == self.build_inputs_text(comp))
        except (FileNotFoundError, OSError, PathContainmentError):
            return False

    def is_built(self, comp) -> bool:
        """True if the component needs no build, or its built artifact is present.
        The artifact path may carry run-param placeholders (e.g. {env} for the
        firmware build dir), substituted from the stack's saved config.

        A `build_marker` (written by build() ONLY after the last step succeeds) takes precedence over
        `bin`: it is the authoritative completion signal, so a build killed mid-way — leaving `bin`
        present but the build incomplete (e.g. a venv whose interpreter exists but whose pip installs
        never finished) — correctly reads NOT built."""
        if comp.build_marker and comp.build_steps:
            from . import runtime_fs
            from .lifecycle import _BUILD_MARKER_MAX, BUILD_MARKER_TEXT
            marker = self._lifecycle().source_dir(comp) / comp.build_marker
            try:
                # Descriptor-anchored O_NOFOLLOW regular-file read: a symlink, directory, FIFO/device,
                # oversize/malformed content, or an unsafe/escaping parent all read as NOT built (never
                # a traceback / web 500). Only a regular file whose ENTIRE content is the exact marker
                # text counts as built.
                # Expected = the static text + the CURRENT consumed-source SHAs. A marker
                # written against older sources (or the pre-receipt static form) mismatches
                # and reads NOT built — `lhpc build reticulum` is then surfaced as required.
                # A revision that cannot be read verifies nothing: such a receipt is never
                # current.
                expected = BUILD_MARKER_TEXT + self._consumed_source_lines(comp)
                if " unknown\n" in expected or (runtime_fs.read_text_regular(
                        self._paths, marker, max_bytes=_BUILD_MARKER_MAX) != expected):
                    return False
                # A source build of a binary-capable component also records its own revision
                # beside the marker (`_own_rev`): checked only off the binary channel, where an
                # artifact's static marker (and any side file inside it) is never compared.
                own = self._own_rev_expected(comp)
                if (own is not None
                        and not self.on_binary_channel(self.stack_of(comp.id) or comp.id)
                        and (" unknown\n" in own[1] or runtime_fs.read_text_regular(
                            self._paths, own[0], max_bytes=_BUILD_MARKER_MAX) != own[1])):
                    return False
                # Declared inputs AND consumed assets: an old sidecar without the asset
                # lines mismatches and reads NOT built, exactly like a missing one.
                return not (comp.build_inputs or comp.asset_inputs) or self._inputs_recorded(comp)
            except (FileNotFoundError, OSError, PathContainmentError):
                return False
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
        sid = self.stack_of(comp.id) or comp.id
        # A binary-covered component has no clone by design (the artifact provides its build
        # output directly) — only the physical artifact check below decides readiness. Every
        # other component keeps the byte-identical source-dir requirement.
        covered = self.binary_covers(comp.id)
        if comp.source and not covered and not self._source_present(comp):
            return f"not installed — run: lhpc install {sid}"
        if (behind := self.binary_behind(comp)):
            return behind
        if not self.is_built(comp):
            return f"not built — run: {self.build_remedy(sid, comp.id)}"
        return ""

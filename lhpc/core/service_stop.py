"""ControllerService mixin: stop and restart, their dependents and optional components, and the band release.

Adapters import lhpc.core.services only. Split from service_lifecycle_ops.py unchanged."""
from __future__ import annotations

import time

from . import restart_required as _rr
from .daemon_control import ALLOWED_BANDS
from .model import ComponentKind, RunState
from .outcomes import CompResult, Outcome, applied_ok
from .paths import PathContainmentError
from .service_base import ActionResult, AdmissionRefused, SourceTxnBlocked
from .service_lifecycle_ops import _daemon_presence
from .snapshot_memo import invalidates_snapshot


class StopOpsMixin:

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
        its components) — e.g. stopping the daemon orphans kiss/…

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
        for b in sorted(bb for bb in active_bands if bb in ALLOWED_BANDS):
            others = [d for d in self.stop_dependents(daemon_sid, bands={b}) if d != sid]
            if not others and self.daemon_view(b).reachable:
                release.append(b)
        return daemon_sid, release

    def _dep_stacks_to_release(self, stk, sid) -> list[str]:
        """Non-daemon dependency STACKS a stopping client no longer needs — stacks a component
        of `stk` depends on that are running with NO other running dependent (the stopping
        client itself excluded). The daemon is deliberately NOT in this set: its teardown
        stays band-scoped via `_daemon_bands_to_release`. Stopping graywolf left
        the KISS TNC (and through it the daemon) running — the chain its start pulled up must
        come down with the stack that needed it. Each released stack re-enters stop() with
        release_daemon=True, so KISS in turn releases the daemon band it alone was using."""
        if stk is None:
            return []
        daemon_sid = next((s.id for s in self.stacks() if s.main == self.DAEMON_ID), None)
        dep_sids: list[str] = []
        for c in stk.components:
            for d in (c.depends_on or ()):
                dsid = self.stack_of(d)
                if (dsid and dsid not in dep_sids and dsid != sid and dsid != daemon_sid
                        and self.stack(dsid) is not None):
                    dep_sids.append(dsid)
        up = (RunState.RUNNING, RunState.DEGRADED)
        out = []
        for dsid in dep_sids:
            ss = next((s for s in self.build_snapshot().stacks if s.stack.id == dsid), None)
            if ss is None or not any(ss.components[c.id].run_state in up
                                     for c in ss.stack.components):
                continue                               # dependency not running -> nothing to do
            if [d for d in self.stop_dependents(dsid) if d != sid]:
                continue                               # another running stack still needs it
            out.append(dsid)
        return out

    @invalidates_snapshot
    def stop(self, target: str, apply: bool = False, cascade: bool = False,
             band: str = "", release_daemon: bool = True, auto_install_ctx=None,
             *, _operator: bool = True) -> ActionResult:
        """Public, LOCKED entry — acquires the lifecycle bundle (incl. dependents on
        cascade) so a DIRECT call gets the same coordination as CLI/web. `release_daemon=False`
        is the INTERNAL cascade path: a client stopped as part of a daemon cascade must not itself
        release the daemon (the outer daemon stop is the sole owner of daemon teardown)."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        from . import reslock
        if auto_install_ctx is not None:
            ctx_err = self._auto_install_ctx_error(auto_install_ctx, set())
            if ctx_err:
                return ActionResult(False, f"Refusing to stop '{target}': {ctx_err}")
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
                res = self._stop_impl(target, apply=True, cascade=cascade, band=band,
                                      release_daemon=release_daemon, _operator=_operator)
                # Durable OPERATOR INTENT — ONLY for a DIRECT, WHOLE-STACK operator stop.
                # `_operator` is False on every internal route (cascade dependents, the daemon
                # band release, owner stops for a start, restart's stop leg, uninstall/clean,
                # auto-install): those tombstoned the shared daemon and whole
                # stacks the operator never named, and boot-restore then pruned evidence for
                # things legitimately running at shutdown. Band- and component-scoped stops
                # never tombstone — a band-scoped stop only for the daemon. Written when the stop
                # stopped something (verified or
                # UNVERIFIED — the key case) or there was nothing to stop; a REFUSED stop that
                # left the stack running writes nothing, so a still-running stack is never
                # silently dropped from restore.
                # The UNVERIFIED escape hatch counts ONLY the target stack's OWN components
                # (a dependent's UNVERIFIED row satisfied it while the daemon's
                # own stop was BLOCKED and the daemon kept running — tombstoning a live stack).
                # Only the DAEMON's per-band stop is exempt: a client the console stops with its
                # band is still the operator's stop (finding 143), or boot restore restarts it.
                if (_operator and not (band and self._is_daemon_target(target))
                        and self.stack(target) is not None
                        and (res.ok
                             or any(r.action == "stop" and r.outcome == Outcome.UNVERIFIED
                                    and (self.stack_of(r.component) or r.stack) == target
                                    for r in (res.results or ())))):
                    self._write_stop_intent([target])
                return res
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot stop '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])

    def _stop_impl(self, target: str, apply: bool = False, cascade: bool = False,
                   band: str = "", release_daemon: bool = True,
                   _operator: bool = False) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        # Stop in reverse start order.
        items = list(reversed(items))
        _sid = self.stack_of(target)
        _stk = self.stack(_sid) if _sid else None
        # Only a WHOLE-STACK stop may release what other stacks share (a daemon band, a dependency
        # stack): the release helpers ask "does anything else still need this?" with the target's
        # own stack excluded, which is only sound when that whole stack just stopped. A component-
        # scoped stop leaves its siblings running and releases nothing.
        _target_is_stack = self.stack(target) is not None
        target_is_daemon = bool(_stk and _stk.main == self.DAEMON_ID)
        # The daemon is shared infrastructure: stopping it ALWAYS orphans its dependents, so the
        # cascade is forced (a client is never left pointing at a dead daemon).
        if target_is_daemon:
            cascade = True
        # Bands ACTUALLY being stopped — from the authoritative topology resolver (per-band daemon
        # stop includes its dual-band collateral; separate per-band instances are unaffected).
        _daemon_band_stop = bool(target_is_daemon and band in ALLOWED_BANDS)
        stopped_bands = self._operation_bands(target, band, "", "stop") if _daemon_band_stop else None
        other_bands = sorted((stopped_bands or set()) - {band}) if _daemon_band_stop else []
        # Fail closed: a PER-BAND daemon stop must not guess the band of a running band-switchable
        # dependent that has no trustworthy marker — block, name it, and stop NOTHING.
        if _daemon_band_stop:
            uncertain = self._uncertain_daemon_dependents(_sid or target)
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
        dependents = self.stop_dependents(_sid or target, bands=stopped_bands)
        # systemd services lhpc doesn't own (e.g. meshtasticd) — stop them as root.
        sysd = [f"sudo systemctl stop {c.units[0].name}"
                for _, c in items if c.units and not c.run_argv]
        if not apply:
            details = [f"  [stop] {comp.id}" for _, comp in items]
            for cmd in sysd:
                details.append(f"    {cmd}")
            # The plan hid the collateral — disclose the dependency stacks and the
            # daemon band this stop would also take down (predicted from current topology; the
            # apply re-decides under its locks). Only a stop that will actually stop something
            # releases anything (see the actually-stopped gate below).
            if not target_is_daemon and release_daemon and self.stack_running(target):
                _psid = self.stack_of(target)
                _deps_rel = self._dep_stacks_to_release(_stk, _psid)
                for _dep in _deps_rel:
                    details.append(f"  [also stops] {_dep}: dependency no other stack needs")
                # Daemon prediction must look PAST the dependency releases above (kiss still
                # counts as a daemon dependent right now, but its own release will free it).
                _dsid = next((s.id for s in self.stacks() if s.main == self.DAEMON_ID), None)
                if _dsid:
                    for _b in sorted(b for b in self._operation_bands(target, band, "", "stop")
                                     if b in ALLOWED_BANDS):
                        _others = [d for d in self.stop_dependents(_dsid, bands={_b})
                                   if d != _psid and d not in _deps_rel]
                        if not _others and self.daemon_view(_b).reachable:
                            details.append(f"  [also stops] {_dsid} {_b} MHz: "
                                           "no other stack needs it")
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
        blocked_deps: list[str] = []            # dependents whose stop did not verify
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
                # ONE blocked-dependent collection for both branches. An interactive dependent
                # is the operator's OWN process (chat, meshcore-cli) — LHPC never signals one it
                # does not own — so this is exactly the case the `kill <pid>` hint exists for.
                # Recording it only on the automatic-stop path left that hint empty here.
                blocked_deps.extend(interactive_deps)
                dep_block = True                 # preflight block: stop NO automatic dependent
            else:
                for dep in dependents:
                    # release_daemon=False: the OUTER daemon stop owns teardown — a dependent must
                    # not recursively stop the daemon (it just clears its own marker on cessation).
                    # a dependent stopped by an OPERATOR cascade (stop daemon) was the
                    # operator's word too — dropping its tombstone reintroduced resurrect-after-
                    # reboot for exactly the unverified-dependent case. Internal cascades
                    # (restart, uninstall, auto-install) still pass False from the outer stop.
                    # Only a WHOLE-STACK operator stop passes its operator-ness down: a
                    # band-scoped DAEMON stop (band switch) names no dependent for good —
                    # tombstoning them dropped stacks from restore the operator never stopped. A
                    # client's stop with its band (the console's) is the operator's (finding 143).
                    dep_res = self.stop(dep, apply=True, release_daemon=False,
                                        _operator=_operator and not (
                                            band and self._is_daemon_target(target)))
                    results.append(CompResult(component=dep, stack=dep, action="stop",
                        outcome=Outcome.STOPPED if dep_res.ok else Outcome.UNVERIFIED,
                        summary=dep_res.summary))
                    details.append(f"  [{'stopped' if dep_res.ok else 'unverified'} dependent] {dep}")
                    if not dep_res.ok:
                        dep_block = True
                        blocked_deps.append(dep)

        for _, comp in items:
            # Never stop the daemon while a dependent is still running / not verified stopped.
            if target_is_daemon and comp.id == self.DAEMON_ID and dep_block:
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.BLOCKED,
                    summary="not attempted — a dependent is still running or not verified stopped")
                results.append(cr)
                own_results.append(cr)
                details.append(f"  [blocked] {comp.id}: a dependent is still running — daemon left up")
                # NAME what is still up, and how to end it. An interactive client (meshcore-cli,
                # chat) is the operator's OWN process — LHPC never signals one it does not own —
                # so "a dependent is still running" alone leaves nothing to act on. The stop
                # BUTTON already reports this well; make the daemon-stop path just as readable.
                for hint in self._still_running_hints(blocked_deps):
                    details.append(f"    {hint}")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised: LHPC cannot verify the stop -> MANUAL_REQUIRED.
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.MANUAL_REQUIRED,
                    summary=f"system service — stop as root: sudo systemctl stop {comp.units[0].name}")
                results.append(cr)
                own_results.append(cr)
                details.append(f"  [manual_required] {comp.id}: stop it as root: "
                               f"sudo systemctl stop {comp.units[0].name}")
                continue
            # The daemon is multi-instance (one process per band). A per-band stop
            # signals ONLY the owned instance(s) serving that band. Record-driven +
            # identity-verified inside Lifecycle.stop, which returns a typed result.
            if comp.id == self.DAEMON_ID and band in ALLOWED_BANDS:
                cr = life.stop(comp, band=band)
            else:
                cr = life.stop(comp)
            results.append(cr)
            own_results.append(cr)
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
            # Only the BAND OWNER's cessation retires the band marker: stopping an
            # optional client (sideband, lxmd) must not wipe it while `rns` is still
            # on 868 — that erases the stack's band evidence and the dashboard has
            # nothing to place it by.
            #
            # Decided from the TYPED STOP RESULTS, not from a snapshot: the snapshot
            # is memoised per operation, so a read taken here still showed the
            # just-stopped owner as running and kept the marker alive forever.
            if self._band_owners_stopped(sid, own_results):
                self._safe_unlink(self._band_marker(sid))
            self.dismiss_interactive(sid)
        # A CLIENT stop also takes down the non-daemon dependency stacks it ALONE was using
        # (the KISS TNC under graywolf) — the chain its start pulled up comes down with it.
        # Internal (_operator=False), so released stacks are never tombstoned; each release
        # re-enters stop() with release_daemon=True and thereby releases the daemon band it
        # used, after which the daemon-release below no-ops via its reachable guard. A failed
        # release feeds the aggregate success like every other typed result.
        # Gated on this stop having ACTUALLY stopped something — an
        # entirely ALREADY_STOPPED target (stop of a not-running graywolf) must never tear
        # down a dependency stack the operator may have started independently. The daemon
        # release below keeps its long-standing semantics unchanged (operator ruling).
        _own_stopped = any(r.outcome is Outcome.STOPPED for r in own_results)
        if own_ok and not target_is_daemon and release_daemon and _own_stopped and _target_is_stack:
            for dep_sid in self._dep_stacks_to_release(_stk, sid):
                dep_stk = self.stack(dep_sid)
                rres = self.stop(dep_sid, apply=True, _operator=False)
                results.append(CompResult(
                    component=(dep_stk.main if dep_stk else dep_sid), stack=dep_sid,
                    action="stop",
                    outcome=Outcome.STOPPED if rres.ok else Outcome.UNVERIFIED,
                    summary=(f"released {dep_sid} (no other stack needs it)" if rres.ok
                             else f"{dep_sid} release NOT verified — {rres.summary}")))
                details.append(f"  [{'stopped' if rres.ok else 'unverified'} dependency] "
                               f"{dep_sid}: no other stack needs it")
        # A CLIENT stop also releases the daemon band it used — only where no other running
        # dependent needs it. Its typed result feeds the aggregate success (a failed release makes
        # the whole client stop non-success). The just-stopped client is excluded from that check,
        # so there is no recursive re-stop of it.
        if own_ok and not target_is_daemon and release_daemon and _target_is_stack:
            daemon_sid, release = self._daemon_bands_to_release(_stk, sid, active_bands)
            for b in release:
                dres = self.stop(daemon_sid, apply=True, band=b, _operator=False)
                results.append(CompResult(component=self.DAEMON_ID, stack=daemon_sid, action="stop",
                    outcome=Outcome.STOPPED if dres.ok else Outcome.UNVERIFIED,
                    summary=(f"released {daemon_sid} {b} (no other stack needs it)" if dres.ok
                             else f"{daemon_sid} {b} release NOT verified — {dres.summary}")))
                details.append(f"  [{'stopped' if dres.ok else 'unverified'} daemon] "
                               f"{daemon_sid} {b}: no other stack needs it")
        # A verified daemon stop clears the RX/TX activity window for the band(s) it served — a
        # per-band stop clears that band (+ its dual-band collateral), a whole-daemon stop clears
        # every served band. (A client that releases a daemon band recurses here as a daemon stop.)
        if target_is_daemon and own_ok:
            for _b in ({band, *other_bands} if band in ALLOWED_BANDS
                       else set(self.active_bands())):
                self.clear_daemon_feed(_b)
        # ok only when every result (dependents + own + daemon release) is a verified stop.
        ok = applied_ok(results) if results else True
        summary = (f"Stop applied for '{target}'." if ok else
                   f"Stop for '{target}' is NOT fully verified — see details.")
        # A VERIFIED stack stop retires the last-start candidate (the running state it
        # captured no longer exists, so the confirm-known-working offer must disappear) and
        # clears the restart-required flag (the stale processes are gone; the next start uses
        # the saved config). A per-band daemon stop counts only when the process table, read
        # after the stop, shows no daemon: another instance still runs the old config, and an
        # unreadable table proves nothing.
        _whole = not _daemon_band_stop or _daemon_presence(self._system.procfs.cmdlines()) == "none"
        if ok and apply and self.stack(target) is not None and _whole:
            from . import known_working
            # A candidate-clear failure is reported, never swallowed: a still-present candidate
            # marker keeps the "confirm this stack as working" offer eligible for a stack that
            # was just stopped (`read_candidate` does not check liveness), so the operator could
            # confirm a no-longer-running composition. A failed clear downgrades the stop to
            # NOT-fully-verified.
            cleared, why = known_working.clear_candidate_checked(self._paths, target)
            if not cleared:
                ok = False
                summary = (f"Stop for '{target}' applied but the known-working candidate "
                           f"could not be retired — see details.")
                details = [*list(details), f"  [candidate] not cleared: {why}"]
            _rr.clear_marker(self._paths, target)
        return ActionResult(ok, summary, details=details, results=tuple(results),
                            next_commands=[f"lhpc status {target}"])

    @invalidates_snapshot
    def restart(self, target: str, apply: bool = False, stop_owners: bool = False,
                band: str = "", cascade: bool = False, *,
                _before_restart_locked=None) -> ActionResult:
        """Public, LOCKED entry — holds ONE bundle across the internal stop+start so a
        DIRECT call gets the same coordination as CLI/web. A restart runs the SAVED configuration
        (no per-launch values); its plan (`apply=False`) combines the start plan with the stop's
        collateral (dependents, cascade, other bands) so the operator sees every consequence before
        confirming. `cascade` is that consent, carried to the stop leg: the lock bundle covers the
        dependents and the stop takes them down (a daemon stop cascades regardless); without it a
        non-daemon provider's running dependents are left as they are, exactly like `stop`.

        `_before_restart_locked` (PRIVATE, keyword-only) is the detached web job's admission hook:
        invoked on the apply path at the restart transaction's pre-mutation boundary — admission +
        config stability + the restart lifecycle bundle held, preflight and the GPS position done —
        and BEFORE the stop; returning an ActionResult cancels the restart with ZERO side effects
        (the running stack is never stopped). It is NOT handed to the nested start."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        if (_r := self._gui_fallback_refusal(target)) is not None:
            return _r
        if (_r := self._meshcore_mode_refusal(target)) is not None:
            return _r
        if (_bref := self.band_refusal(target, band, "restart")) is not None:
            return _bref            # plan and apply take the SAME decision on an explicit band
        from . import reslock
        if not apply:
            # The apply's preflight, in its order and on its band: identity, saved launch values,
            # MeshCore position.
            _rband = self.operation_band(target, band)
            if (_pf_err := self._identity_refusal(target, _rband, "restart")) is not None:
                return _pf_err
            if (_pf_err := self._saved_launch_refusal(target, _rband, "restart")) is not None:
                return _pf_err
            if (_pf_err := self._position_refusal(target, "restart")[0]) is not None:
                return _pf_err
            return self._restart_impl(target, apply=False, stop_owners=stop_owners, band=band,
                                      cascade=cascade)
        # A non-daemon restart with NO explicit band restarts on the band it is ACTUALLY running on
        # (not the configured default) — resolve it BEFORE the guard so locking and the restart use
        # the same band (KISS/Voice on 868, restart no-band → lock 868 only, not 433). It uses THE
        # ONE resolver — the same explicit → running → ACTIVE-fallback-marker → primary rule as
        # start() and the identity form (never `_effective_band`, which takes an interactive
        # marker UNCONDITIONALLY: a stopped Voice with a stale 868 marker would read the 433
        # identity on the panel and write it to the 868 store — persisted config drift).
        # Hold saved configuration STABLE from PREFLIGHT through the whole stop→start transition, so
        # a valid target is never stopped and then rejected by a concurrently-mutated config (LOCK
        # ORDER: config guard BEFORE the lifecycle/resource lock; re-entrant with _restart_impl).
        # LOCK ORDER #1: task admission OUTSIDE config-stability and BEFORE preflight/any stop — a
        # restart refused by a pending self-update/uninstall never stops the running stack. The inner
        # _lifecycle_guard (and the nested stop's) reuse this admission reentrantly.
        try:
            with self._admission_guard("restart", target):
                # ONE CONCRETE OPERATION BAND, resolved ONCE and only HERE — under admission, so the
                # running/marker state it reads cannot change under another admitted operation before
                # this one owns the box (resolving before admission let a concurrently
                # admitted start put the stack on 868 in the window, after which the identity was
                # saved on the primary 433 while stop/preflight/launch ran on 868). `_config_band`
                # maps the primary choice to its CONCRETE band, so "" never travels onward for a
                # band-switchable stack and no inner frame can reinterpret it; "" survives only for a
                # genuinely bandless stack, which nothing downstream can reinterpret either.
                _rband = self.operation_band(target, band)
                with self._config_stable():
                    # PREFLIGHT the saved identity BEFORE lock planning, the guard, owner handling
                    # or any stop: a restart must never stop a running stack only to be refused by
                    # the start that follows.
                    if (_pf_err := self._identity_refusal(target, _rband, "restart")) is not None:
                        return _pf_err
                    _order, _radio = self._order_radio(target, _rband)
                    if (_pf_err := self._saved_launch_refusal(target, _rband, "restart")) is not None:
                        return _pf_err
                    with self._lifecycle_guard("restart", target, _rband,
                                               stop_owners=stop_owners, cascade=cascade,
                                               radio=_radio):
                        # MESHCORE POSITION: taken HERE — under the guard, before the stop —
                        # and handed to the nested start, so a restart queries gpsd exactly
                        # once and a refusal leaves the running node up rather than stopping
                        # it and only then discovering there is no position.
                        _pf_err, _position, _pos_note = self._position_refusal(target,
                                                                               "restart")
                        if _pf_err is not None:
                            return _pf_err
                        # PRE-MUTATION HOOK: every lock held, nothing stopped yet. A refusal
                        # cancels the restart with the running stack untouched.
                        if _before_restart_locked is not None:
                            _hook_refusal = _before_restart_locked()
                            if _hook_refusal is not None:
                                return _hook_refusal
                        return self._restart_impl(target, apply=True,
                                                  stop_owners=stop_owners, band=_rband,
                                                  cascade=cascade, position=_position,
                                                  position_note=_pos_note)
        except (AdmissionRefused, reslock.ResourceBusy, SourceTxnBlocked, OSError,
                PathContainmentError) as exc:
            return self._guard_refusal("restart", target, exc)

    def owner_stops(self, blockers) -> list[tuple[str, str]]:
        """`(label, command)` per stop the holders of `blockers` need, in the order a start with
        stop_owners runs them: the app stacks first, then the daemon per radio band it holds (a
        bare `lhpc stack stop daemon` would stop every client on every band, finding 140)."""
        daemon_sid = self.stack_of(self.DAEMON_ID) or "daemon"
        holders = {bl["holder_stack"] for bl in blockers}
        out = [(o, f"lhpc stack stop {o}") for o in sorted(holders - {daemon_sid})]
        if daemon_sid in holders:
            bands = sorted({bl.get("band", "") for bl in blockers if bl["holder_stack"] == daemon_sid})
            out += ([(daemon_sid, f"lhpc stack stop {daemon_sid}")] if "" in bands else
                    [(f"{daemon_sid} on {b}", f"lhpc stack stop {daemon_sid} --band {b}") for b in bands])
        return out

    def owner_stop_commands(self, blockers) -> list[str]:
        """The commands of `owner_stops`."""
        return [cmd for _label, cmd in self.owner_stops(blockers)]

    def _restart_impl(self, target: str, apply: bool = False, stop_owners: bool = False,
                      band: str = "", cascade: bool = False, position: dict | None = None,
                      position_note: str = "") -> ActionResult:
        """Applied restarts run the WHOLE preflight→stop→start transition under the configuration-
        stability guard, so a concurrent save can never change the inputs mid-transition (and a valid
        target is never stopped only to be rejected by the later start). A direct/internal apply=True
        call cannot bypass it; dry-run holds no long-lived guard."""
        if not apply:
            return self._restart_impl_inner(target, apply=False, stop_owners=stop_owners,
                                            band=band, cascade=cascade)
        try:
            with self._config_stable():                          # re-entrant (no-op if restart() holds it)
                return self._restart_impl_inner(target, apply=True, stop_owners=stop_owners,
                                                band=band, cascade=cascade, position=position,
                                                position_note=position_note)
        except (AdmissionRefused, SourceTxnBlocked, OSError, PathContainmentError) as exc:
            return self._guard_refusal("restart", target, exc)

    def _restart_impl_inner(self, target: str, apply: bool = False, stop_owners: bool = False,
                            band: str = "", cascade: bool = False,
                            position: dict | None = None,
                            position_note: str = "") -> ActionResult:
        """Stop then start a target — used to apply a config change to a running stack.
        With no band given, keep the band the stack is currently running on (so a
        restart doesn't move a band-switchable stack back to its default band).

        THE RESTART, its legs in order: PREFLIGHT (`_restart_preflight`: every refusal the start that
        follows would take, bar MeshCore's position refusal (public `restart`'s, unchanged), before any stop) → STOP (the public `stop`;
        one that is not verified ends the restart, nothing started) → START (the public `start`,
        its five phases and roll-back included) → RESTORE (`_restore_optional`: the optional
        components that were up and the start did not raise)."""
        # DEFENSIVE twin of the public-entry guard: an internal/direct restart of voice's
        # terminal fallback must refuse BEFORE stop() — the later start can only ever refuse
        # it, and by then the component and its daemon would already be down (restart
        # degrading to stop-only, exactly the promise this method documents).
        if (_r := self._gui_fallback_refusal(target)) is not None:
            return _r
        if (_r := self._meshcore_mode_refusal(target)) is not None:
            return _r
        if not band:
            # THE ONE resolver, same as public restart(), start() and the identity form (otherwise
            # this kept `_effective_band`, which takes an interactive marker UNCONDITIONALLY. Once the
            # public entry stopped doing that, a stale marker made the inner launch pick 868 while the
            # identity was saved and judged on 433 — the store/launch divergence this feature exists
            # to eliminate, and the preflight then materialized the inherited global over a deliberate
            # local callsign. It also split the dry-run plan from the applied band.)
            band = self.operation_band(target, band)
        if (_r := self._restart_preflight(target, band, stop_owners, cascade, apply)) is not None:
            return _r
        if not apply:
            return self._restart_preview(target, band, cascade)
        # OPTIONAL components that are UP inside the stack (MeshChat beside rns, an optional
        # client started by name) come back after the restart: a stack start raises only the
        # run order, so before 0.9.2 `lhpc stack restart reticulum` left a running MeshChat
        # stopped (F-R3, reticulum-rnode-test-2026-09-24). Captured BEFORE the stop leg — after
        # it nothing is running — and re-raised by name after the stack is back.
        _optional_up = self._running_optional_components(target) if self.stack(target) else []
        stopped = self.stop(target, apply=True, cascade=cascade, band=band, _operator=False)
        if not stopped.ok:
            # Strict transition: never start after an unverified/failed stop. Preserve
            # the failed-stop typed results as the restart evidence.
            return ActionResult(False,
                                f"Restart aborted for '{target}': stop was not verified.",
                                details=[*list(stopped.details), "  [aborted] not starting " "after an unverified stop — resolve the stop first"],
                                results=tuple(stopped.results),
                                next_commands=[f"lhpc status {target}"])
        time.sleep(1.0)  # let sockets/locks release before re-starting
        res = self.start(target, apply=True, stop_owners=stop_owners, band=band,
                         position=position, position_note=position_note)
        return self._restore_optional(target, band, stopped, res, _optional_up)

    def _restart_preflight(self, target: str, band: str, stop_owners: bool, cascade: bool,
                           apply: bool):
        """PREFLIGHT of a restart, plan and apply alike, BEFORE the stop: every refusal the start that
        follows would take (except MeshCore's position refusal, taken by public `restart` before, as
        before) — never stop a running target, then find the start refused. Returns it, or None."""
        # On apply `band` is already the operation band; on the plan an explicit band
        # is still raw, so it is resolved here the way the apply resolved it.
        _rband = self.operation_band(target, band)
        if (_r := self._start_static_refusal(target, "restart")) is not None:
            return _r
        if (_pf_err := self._identity_refusal(target, _rband, "restart")) is not None:
            return _pf_err
        if (_pf_err := self._saved_launch_refusal(target, _rband, "restart")) is not None:
            return _pf_err
        # The dependency band rule: a chain member of another stack live on a different band.
        # `stop_owners` cannot clear that state: owner-stop acts on resource-conflict HOLDERS, and
        # chain members are exempt from that set precisely because they are in the run order.
        _pre_order = self._run_order(target)
        if _pre_order:
            _bb = self._dep_band_block(target, _pre_order,
                                       _rband or self._config_band(target, _rband))
            if _bb is not None:
                return _bb
            # The firewall gate (no apply-script write on the plan), an ambiguous saved value and
            # — on apply without `stop_owners` — the running holders of a needed resource, except
            # those this restart's own stop takes down: the target stack's optional components
            # and the dependents its stop cascades.
            radio = (self._daemon_needs(_pre_order, _rband)[0] or "")
            _check = apply and not stop_owners
            _ex = set()
            if _check:
                _ex = {target} if self.stack(target) else set()
                if self._restart_stops_dependents(target, cascade):
                    _ex |= set(self._stop_impl(target, apply=False, cascade=cascade,
                                               band=_rband).data.get("dependents") or [])
            if (_r := self._start_preflight_refusal(target, _pre_order, _rband, radio, "restart",
                                                    check_blockers=_check, render=apply,
                                                    exclude_holders=_ex)) is not None:
                return _r
        return None

    def _restart_preview(self, target: str, band: str, cascade: bool) -> ActionResult:
        """The restart PLAN (after its preflight passed). Advisory only: the apply decides
        everything again under its own guards and never reads this result."""
        # THE COMBINED restart plan: the start plan (preflight, blockers, identity — a refusal
        # there IS the restart's refusal) plus the stop plan's consequential collateral
        # (dependents that stop too, the forced daemon cascade, the other served bands) — the
        # two existing planners, merged, so a web Restart that would take OTHER stacks down can
        # ask for confirmation while an isolated restart runs directly. The INNER planners:
        # `restart()` (the public, snapshot-invalidating entry) has already taken the
        # controller/GUI/mode/band refusals the public `start()`/`stop()` would repeat, and
        # going through them again would drop the request memo and the snapshot between the
        # two legs — a second full assessment inside the web Restart click.
        res = self._start_impl(target, apply=False, band=band)
        if not res.ok:
            return ActionResult(False, res.summary.replace("start", "restart", 1),
                                details=res.details, data=res.data,
                                next_commands=res.next_commands)
        stp = self._stop_impl(target, apply=False, band=band)
        data = dict(res.data)
        data["dependents"] = list(stp.data.get("dependents") or [])
        data["other_bands"] = list(stp.data.get("other_bands") or [])
        # The plan says what THIS restart will do with the dependents: a cascading restart
        # (the confirmed web choice; a daemon always cascades) stops them first; a plain one
        # (the CLI, an unconfirmed web click) leaves them running across the restart — the
        # same decision the apply takes, never a `[stop]` the stop leg will not perform.
        _will_stop = self._restart_stops_dependents(target, cascade)
        _opt = self._running_optional_components(target) if self.stack(target) else []
        data["optional_restarted"] = list(_opt)
        details = [*[(f"  [stop] {d}" if _will_stop else
                      f"  [running dependent] {d}: left running across this restart "
                      "(a cascading restart stops it first)") for d in data["dependents"]],
                   *[f"  [optional] {c}: running — restarted with the stack" for c in _opt],
                   *res.details]
        return ActionResult(res.ok, f"Restart plan for '{target}': stop then run.",
                            details=details, data=data,
                            next_commands=[f"lhpc stack restart {target} --yes"])

    def _restore_optional(self, target: str, band: str, stopped: ActionResult,
                          res: ActionResult, optional_up: list[str]) -> ActionResult:
        """RESTORE, the restart's last leg: after a successful start, the OPTIONAL components
        that were up before the stop (`optional_up`) and that the start did not bring back
        itself are started again by name; one that does not come back fails the restart. The
        result is the stop's details and rows followed by the start's (and the re-raised)."""
        ok, details, results = res.ok, [*stopped.details, *res.details], list(res.results)
        _reraised, _lost = [], []
        if ok and optional_up:
            # Start ONLY what the stack start did not bring back itself (an optional component
            # that is also in the run order — a GPS feed, a web UI — is up again already; a
            # second start() would answer "already healthy" and the report would lie).
            _still_up = set(self._running_optional_components(target))
            for cid in [c for c in optional_up if c not in _still_up]:
                r2 = self.start(cid, apply=True, band=band)
                (_reraised if r2.ok else _lost).append(cid)
                details += [f"  [optional] {cid}: was running — "
                            + ("started again" if r2.ok else f"NOT back: {r2.summary}"),
                            *r2.details]
                results += list(r2.results)
            ok = ok and not _lost
        summary = f"Restarted '{target}'. {res.summary}"
        if _reraised:
            summary += f" Optional components restarted: {', '.join(_reraised)}."
        if _lost:
            summary += f" An optional component did not come back: {', '.join(_lost)}."
        # Restart's typed results are the stop results followed by the start results — and so
        # are its details: a cascading restart's job log shows the dependents stopping BEFORE
        # the target comes back (the stop leg's lines were dropped).
        return ActionResult(ok, summary, details=details,
                            results=tuple(stopped.results) + tuple(results),
                            data=res.data,     # the start's facts (the shared-SPI warning)
                            next_commands=res.next_commands)

    def _restart_stops_dependents(self, target: str, cascade: bool) -> bool:
        """Whether a restart's stop leg takes the running dependents down: a cascading restart
        (the confirmed web choice) does, and a daemon restart always does."""
        _sid = self.stack_of(target)
        _daemon = bool(_sid and self.stack(_sid) and self.stack(_sid).main == self.DAEMON_ID)
        return cascade or _daemon

    def _running_optional_components(self, target: str) -> list[str]:
        """Ids of the stack's OPTIONAL service components currently running/degraded that a stack
        start does NOT raise on its own — so a restart must bring them back by name.

        Excluded on purpose: components in the stack's own run order (the start leg raises
        them); interactive components (an operator's terminal session — a start() by the
        controller is refused by design and must not fail the restart); non-service kinds."""
        s = self.stack(target)
        if s is None:
            return []
        up = (RunState.RUNNING, RunState.DEGRADED)
        planned = {c.id for _, c in (self._run_order(s.id) or [])}
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == s.id:
                return [c.id for c in s.components
                        if c.optional and c.run_argv and not c.interactive
                        and c.kind == ComponentKind.SERVICE and c.id not in planned
                        and (st := ss.components.get(c.id)) is not None and st.run_state in up]
        return []

    def _still_running_hints(self, stack_ids) -> list[str]:
        """`kill <pid> to stop <component>` for each component of `stack_ids` still up.

        These are the processes LHPC will not signal itself — an interactive client is started by
        the operator in their own terminal and is not LHPC-owned, so the only honest advice is the
        pid. Best-effort: a component whose pid is unknown is still named, without a kill line."""
        from .model import RunState
        out: list[str] = []
        try:
            snap = self.build_snapshot()
        except Exception:
            return out
        for ss in snap.stacks:
            if ss.stack.id not in (stack_ids or ()):
                continue
            for cid, st in ss.components.items():
                if st.run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                pids = [p for p in (getattr(st, "pids", None) or []) if p]
                out.append(f"kill {pids[0]} to stop {cid}" if pids
                           else f"{cid} is still running — stop it yourself")
        return out

    def _band_owners_stopped(self, stack_id: str, results) -> bool:
        """Whether THIS stop ended every band-carrying component of the stack.

        Evidence is the typed result of the stop we just performed — the only
        thing that is guaranteed post-mutation truth.
        """
        stack = self.stack(stack_id)
        owners = {c.id for c in (stack.components if stack else ()) if c.band or c.bands}
        if not owners:
            return True                    # nothing carries a band: nothing to keep
        ceased = {r.component for r in results
                  if r.outcome in (Outcome.STOPPED, Outcome.ALREADY_STOPPED)}
        return owners <= ceased

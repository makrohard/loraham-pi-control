"""Boot auto-restore driver (`lhpc autostart --run-service`, run by lhpc-boot-restore.service).

Restores the stacks that were LHPC-owned and never verifiably stopped before the last reboot —
NOT literally "alive at power-off" (a pre-reboot crash may be restored; the start gates make that
safe). The driver is a thin loop over the PUBLIC `start()` — every gate (admission, hardware,
CALL, band arbitration, firewall exposure, TXMODE-from-saved-config) applies unchanged.

Driver sequence (fixed): whole-run task admission → current-boot-id validation (unavailable =
TEMPORARY integrity failure: exit nonzero, journal untouched, NOTHING deleted) → journal recovery
phase (consumed evidence must never resurrect) → config + web-integration gates (off = retire
foreign evidence, terminal `disabled`) → classify evidence → run the plan. Items are consumed the
moment their journal state leaves `pending` (durable `attempting` BEFORE the nested start, via
the `_before_start_locked` hook that runs under every start lock). No automatic retries.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import boot_restore, known_working, runtime_fs, updater_units
from .boot_restore import Evidence, MarkerView, StackMeta
from .lifecycle import current_boot_id
from .outcomes import manual_required_only
from .paths import PathContainmentError
from .service_base import ActionResult, AdmissionRefused


class BootRestoreOpsMixin:
    DAEMON_STACK_ID = "daemon"

    # ---- gates ----------------------------------------------------------------------------------

    def _web_integration_proven(self) -> tuple[bool, str]:
        """BOTH proofs, bus-free and descriptor-safe: the enablement symlink resolves to the
        expected managed unit path, AND lhpc-web.service verifies byte-exact/no-drop-in via the
        PER-UNIT canonical verifier (never the aggregate verdict, never unrelated units)."""
        user_dir = self._user_unit_dir()
        root = Path(self._paths.runtime_root)
        _root, checkout, venv = updater_units.deployment_paths(str(root))
        link = user_dir / "default.target.wants" / updater_units.WEB_UNIT
        try:
            if not link.is_symlink():
                return False, "lhpc-web.service is not enabled (no wants symlink)"
            target = os.readlink(link)
            resolved = (link.parent / target).resolve() if not os.path.isabs(target) \
                else Path(target).resolve()
            expected = (user_dir / updater_units.WEB_UNIT).resolve()
            if resolved != expected:
                return False, f"wants symlink points at {resolved} (expected {expected})"
        except OSError as exc:
            return False, f"cannot inspect the enablement symlink: {exc}"
        verdict = updater_units.verify(Path(user_dir), updater_units.WEB_UNIT,
                                       str(root), checkout, venv)
        if verdict != updater_units.OK:
            return False, f"lhpc-web.service is not canonical ({verdict})"
        return True, ""

    # ---- evidence classification ---------------------------------------------------------------

    def _boot_marker_view(self, stack_id: str) -> MarkerView:
        """Tri-state reads of the running-band marker and the last-start candidate."""
        rb_state, rb = "absent", ""
        marker = self._paths.under("state", "running", f"{stack_id}.band")
        try:
            if os.path.lexists(marker):
                raw = runtime_fs.read_text(self._paths, marker, max_bytes=64).strip()
                if raw in ("433", "868"):
                    rb_state, rb = "valid", raw
                else:
                    rb_state = "unsafe"
        except (OSError, PathContainmentError):
            rb_state = "unsafe"
        ls_state, ls_band, ls_at = "absent", "", 0.0
        cpath = known_working.candidate_path(self._paths, stack_id)
        try:
            if os.path.lexists(cpath):
                cand = known_working.read_candidate(self._paths, stack_id)
                started = cand.get("started_at") if cand else None
                if (cand is None or not isinstance(started, (int, float))
                        or isinstance(started, bool) or started != started  # NaN guard: NaN != NaN  # noqa: PLR0124
                        or started in (float("inf"), float("-inf"))):
                    ls_state = "unsafe"
                else:
                    ls_state = "valid"
                    ls_band = str(cand.get("band") or "")
                    ls_at = float(started)
        except (OSError, PathContainmentError):
            ls_state = "unsafe"
        return MarkerView(running_band_state=rb_state, running_band=rb,
                          last_start_state=ls_state, last_start_band=ls_band,
                          last_start_at=ls_at)

    # ---- operator stop intent -------------------------------------------------------------
    #
    # LIVE-FOUND (voice resurrecting on every reboot): an operator stop whose cessation could
    # not be verified RETAINS the ownership records — correct for the running system (never
    # assume a process died), but after a reboot those leftovers are indistinguishable from
    # "was running at shutdown", so boot-restore resurrected a stack the operator had
    # explicitly stopped. And once restored, each run leaves fresh shutdown evidence — one
    # missed cleanup made the stack immortal. The intent tombstone is the missing bit of
    # truth: an applied STACK stop writes it, an applied STACK start clears it, and restore
    # skips (and prunes) any evidence for a stack whose tombstone exists. EXISTENCE is the
    # signal, never timestamps — an RTC-less Pi's clock restarts from the image build date
    # every boot, so cross-boot time comparisons would point backwards.

    def _stop_intent_path(self, stack_id: str):
        from . import validators
        safe = validators.path_component(stack_id, field="stop-intent stack")
        return self._paths.under("state", "stop-intent", f"{safe}.json")

    def _write_stop_intent(self, stack_ids) -> None:
        """Best-effort, never raises: intent must not turn a completed stop into an error."""
        import json as _json
        import time as _time

        from . import runtime_fs, validators
        from .lifecycle import current_boot_id as _boot_id
        from .paths import PathContainmentError
        for sid in stack_ids:
            try:
                runtime_fs.mkdir(self._paths, "state", "stop-intent")
                runtime_fs.atomic_write(
                    self._paths, self._stop_intent_path(sid),
                    _json.dumps({"stack": sid, "at": _time.time(),
                                 "boot_id": _boot_id()}) + "\n", 0o644)
            except (OSError, PathContainmentError, validators.ValidationError):
                pass

    def _clear_stop_intent(self, stack_id: str) -> None:
        """Best-effort: an operator start supersedes any previous stop intent."""
        from . import runtime_fs, validators
        from .paths import PathContainmentError
        try:
            runtime_fs.unlink(self._paths, self._stop_intent_path(stack_id))
        except (OSError, PathContainmentError, validators.ValidationError):
            pass

    def _stop_intent_stacks(self) -> set:
        """Stacks with a standing operator stop intent. Unreadable/malformed files count as
        INTENT PRESENT — the file's existence is the signal; failing toward restore would
        resurrect a stack the operator stopped, which is the exact bug this exists to end."""
        try:
            d = self._paths.under("state", "stop-intent")
            return {p.name[:-5] for p in d.iterdir()
                    if p.name.endswith(".json")} if d.is_dir() else set()
        except OSError:
            return set()

    def _classify_boot_evidence(self, cur_boot: str):
        """(evidence, skipped, integrity_issues, dir_state). Same-boot stamped records are NEVER
        evidence; legacy (v0) records must be provably stale AND scope-proven via a matching
        full-stack last-start candidate (started_at >= launched_at, whole-second precision)."""
        life = self._lifecycle()
        valid, issues, dir_state = life.owned_inventory()
        evidence, skipped = [], []
        for rec in valid:
            if rec.get("role", "") != "":
                continue
            rec_boot = str(rec.get("boot_id") or "")
            if rec_boot:
                if rec_boot == cur_boot:
                    continue                        # same-boot: never restore evidence
                scope = rec.get("start_scope", "")
                req = rec.get("requested_target", "")
            else:
                ok, _why = life.verify_owned(rec)
                if ok:
                    continue                        # legacy record of a LIVE process
                scope, req = "", ""
                cand = None
                try:
                    cand = known_working.read_candidate(self._paths, rec.get("stack", ""))
                except (OSError, PathContainmentError):
                    cand = None
                if (cand and isinstance(cand.get("started_at"), (int, float))
                        and cand["started_at"] >= rec.get("launched_at", 0)):
                    scope, req = "stack", rec.get("stack", "")
                else:
                    skipped.append({"stack": rec.get("stack", "?"),
                                    "reason": "legacy record scope unknown "
                                              "(no matching full-stack last-start)",
                                    "evidence_ids": [rec["launch_id"]]})
                    continue
            evidence.append(Evidence(
                launch_id=rec["launch_id"], stack=rec.get("stack", ""),
                component=rec.get("component", ""), band=rec.get("band", ""),
                launched_at=float(rec.get("launched_at", 0)),
                start_scope=scope, requested_target=req))
        # OPERATOR STOP INTENT beats leftover evidence: an unverified stop retains ownership
        # records (correct for the live system), which after a reboot read exactly like
        # "was running at shutdown" — restoring them resurrects a stack the operator
        # explicitly stopped, and each restored run re-seeds the evidence forever. The
        # skipped records are pruned by the caller (they are dead prior-boot leftovers), and
        # the standing intent is cleared only by an applied operator start.
        intents = self._stop_intent_stacks()
        if intents:
            kept = []
            for ev in evidence:
                if ev.stack in intents:
                    # NOT pruned here: like every other skip reason, the CALLER prunes —
                    # after the admission gates and with the journal in place (REVIEW-FOUND:
                    # deleting inside classification destroyed evidence even when the run was
                    # subsequently disabled, outside the journalled prune pattern).
                    skipped.append({"stack": ev.stack,
                                    "reason": "operator stop intent stands — stopped after "
                                              "the recorded launch, not restored",
                                    "evidence_ids": [ev.launch_id]})
                else:
                    kept.append(ev)
            evidence = kept
        return evidence, skipped, issues, dir_state

    def _boot_stack_metas(self) -> dict:
        metas = {}
        for st in self.stacks():
            main = next((c for c in st.components if c.id == st.main), None)
            if main is None:
                continue
            declared = tuple(self.stack_bands(st.id))
            metas[st.id] = StackMeta(
                stack_id=st.id, main=st.main,
                interactive_main=bool(getattr(main, "interactive", False)),
                declared_bands=declared,
                fixed_band="" if declared else (main.band or ""))
        return metas

    # ---- journal helpers -----------------------------------------------------------------------

    def _boot_prune_evidence(self, evidence_ids) -> dict:
        """Compare-before-delete removal of consumed evidence records. {"ok": bool, "left": [...]}

        TRI-STATE per expected id: proven ABSENT (not on disk under its canonical name) ·
        present VALID (compare-before-delete) · present-but-UNSAFE (malformed/unreadable/
        symlinked — reported as an inventory issue). Only proven-absent or a successful delete
        counts as pruned: a temporarily unreadable leaf stays in `left`, blocking journal
        replacement — otherwise it would come back readable, classify as foreign evidence, and
        be restored AGAIN (violating consume-exactly-once)."""
        life = self._lifecycle()
        valid, issues, dir_state = life.owned_inventory()
        if dir_state == "unsafe":
            return {"ok": False, "left": list(evidence_ids), "reason": "owned dir unsafe"}
        by_id = {r["launch_id"]: r for r in valid}
        troubled = {i.get("name", "") for i in issues}
        left = []
        for lid in evidence_ids:
            rec = by_id.get(lid)
            if rec is not None:
                if not life._remove_record(rec):
                    left.append(lid)
                continue
            if f"{lid}.json" in troubled:
                left.append(lid)                    # present but unsafe — NOT proven gone
                continue
            # proven absent — pruned is pruned
        return ({"ok": True, "left": []} if not left
                else {"ok": False, "left": left})

    def _boot_journal_recovery(self, journal) -> tuple[bool, str]:
        """Cleanup-only pass over a prior journal: prune every consumed-but-unpruned item's
        evidence, persisting each result. (True, "") when the journal may be replaced."""
        pending_cleanup = boot_restore.unpruned_consumed(journal)
        if not pending_cleanup:
            return True, ""
        for item in pending_cleanup:
            item["prune"] = self._boot_prune_evidence(item.get("evidence_ids", []))
        if not boot_restore.write_journal(self._paths, journal):
            return False, "journal unwritable during recovery"
        if any((it.get("prune") or {}).get("ok") is not True
               for it in boot_restore.unpruned_consumed(journal)):
            journal["state"] = "unsafe"
            journal["reason"] = ("consumed evidence could not be cleaned up — inspect "
                                 "state/owned/ and state/boot-restore.json")
            boot_restore.write_journal(self._paths, journal)
            return False, "consumed evidence could not be cleaned up"
        return True, ""

    # ---- the driver -----------------------------------------------------------------------------

    def boot_restore_run(self) -> ActionResult:
        """The unit body. `data["driver_completed"]` gates the service exit code: True whenever
        every terminal result was durably recorded (even with failed items — the RemainAfterExit
        unit must stay active); absent/False only on driver/integrity failures."""
        try:
            with self._admission_guard("boot-restore", "controller"):
                return self._boot_restore_run_admitted()
        except AdmissionRefused as adm:
            # Whole-run refusal consumes NOTHING; pending evidence stays restorable. This run
            # did NOT complete — the unit must show failed (exit nonzero) so the miss is never
            # silent: RemainAfterExit would otherwise report success and a plain `start` would
            # no-op past the skipped restore.
            return ActionResult(False, f"Boot restore not started: {adm.reason} — no stack was "
                                       "restored and nothing was consumed; rerun with: "
                                       "systemctl --user restart lhpc-boot-restore.service",
                                data={"admission_blocked": adm.tag},
                                next_commands=["systemctl --user restart lhpc-boot-restore.service"])
        except Exception as exc:
            # A driver defect must surface as a CLEAN nonzero integrity failure (unit red,
            # journal/evidence in whatever durable state the crash point left — the recovery
            # phase handles that), never a raw traceback that hides the remedy.
            import traceback
            traceback.print_exc()
            return ActionResult(False, "Boot restore: driver failure "
                                       f"({type(exc).__name__}: {exc}) — nothing further was "
                                       "started; the journal recovers on the next run.")

    def _boot_restore_run_admitted(self) -> ActionResult:
        cur_boot = current_boot_id()
        if not cur_boot:
            # TEMPORARY integrity failure: journal untouched, nothing deleted, exit nonzero —
            # a later invocation with a readable boot id resumes recovery from the same journal.
            return ActionResult(False, "Boot restore blocked: current boot identity unavailable "
                                       "(/proc/sys/kernel/random/boot_id unreadable) — nothing "
                                       "was started, consumed or deleted.")
        journal, jstate = boot_restore.load_journal(self._paths)
        if jstate.startswith("unsafe"):
            return ActionResult(False, f"Boot restore blocked: existing journal is {jstate} — "
                                       "repair/remove state/boot-restore.json by hand; no "
                                       "evidence was consumed.")
        if journal is not None:
            ok, why = self._boot_journal_recovery(journal)
            if not ok:
                return ActionResult(False, f"Boot restore blocked: {why}.")

        evidence, skipped, issues, dir_state = self._classify_boot_evidence(cur_boot)
        if dir_state == "unsafe":
            return ActionResult(False, "Boot restore blocked: state/owned/ is unsafe "
                                       "(containment/symlink failure) — nothing was consumed.")

        enabled, en_reason = self.boot_restore_enabled()
        web_ok, web_reason = self._web_integration_proven()
        if not enabled or not web_ok:
            reason = en_reason if not enabled else f"web console integration not proven: {web_reason}"
            return self._boot_finish_disabled(cur_boot, evidence, reason, skipped=skipped)

        metas = self._boot_stack_metas()
        markers = {sid: self._boot_marker_view(sid) for sid in
                   {e.stack for e in evidence} | set()}
        plan = boot_restore.derive_plan(evidence, metas, markers, self.DAEMON_STACK_ID)
        plan.skipped.extend(skipped)

        journal = boot_restore.new_journal(boot_id=cur_boot, pid=os.getpid(),
                                           process_start_time=self._own_start_time(),
                                           items=plan.items)
        journal["skipped"] = plan.skipped
        journal["issues"] = issues
        if not plan.items:
            journal["state"] = "no-plan"
            journal["finished_at"] = time.time()
            if not boot_restore.write_journal(self._paths, journal):
                return ActionResult(False, "Boot restore: journal unwritable.")
            self._prune_intent_skips(journal)
            n = len(plan.skipped)
            return ActionResult(True, "Boot restore: nothing to restore"
                                      + (f" ({n} skipped — see the log)." if n else "."),
                                data={"driver_completed": True})
        if not boot_restore.write_journal(self._paths, journal):
            return ActionResult(False, "Boot restore: journal unwritable.")
        self._prune_intent_skips(journal)

        integrity_failure = ""
        for item in journal["items"]:
            if integrity_failure:
                break
            if item["kind"] == "stack":
                self._boot_run_stack_item(journal, item)
            else:
                self._boot_run_daemon_item(journal, item)
            if item.get("_integrity"):
                integrity_failure = item.pop("_integrity")
                break
            if item["state"] == "pending":
                # start() returned before the claim hook ever ran (pre-hook lock/validation
                # failure) — the item is NOT consumed; leave it pending and truncate the run.
                break

        pending = [i for i in journal["items"] if i["state"] == "pending"]
        failed = [i for i in journal["items"] if i["state"] == "failed"]
        journal["state"] = "failed" if (failed or pending) else "done"
        journal["finished_at"] = time.time()
        wrote = boot_restore.write_journal(self._paths, journal)
        if integrity_failure or not wrote:
            return ActionResult(False, "Boot restore: driver integrity failure "
                                       f"({integrity_failure or 'journal unwritable'}).")
        done = [i for i in journal["items"] if i["state"] == "succeeded"]
        cancelled = [i for i in journal["items"] if i["state"] == "cancelled"]
        bits = [f"{len(done)} restored"]
        if failed:
            bits.append(f"{len(failed)} failed (evidence consumed — lhpc stack start <id>)")
        if cancelled:
            bits.append(f"{len(cancelled)} cancelled by current state")
        if pending:
            bits.append(f"{len(pending)} pending (run truncated — restart the unit to continue)")
        if plan.skipped:
            bits.append(f"{len(plan.skipped)} skipped")
        return ActionResult(not (failed or pending),
                            "Boot restore: " + ", ".join(bits) + ".",
                            data={"driver_completed": True})

    def _own_start_time(self):
        from . import procident
        ident = procident.proc_identity(os.getpid()) or {}
        return ident.get("starttime", 0)

    def _prune_intent_skips(self, journal) -> None:
        """Prune intent-skipped leftovers and persist the results — AFTER the journal is
        durable (REVIEW-FOUND: pruning first left destruction unrecorded on a crash or an
        unwritable journal; the disabled path documents the same journal-first order). They
        are dead prior-boot records of a stack the operator stopped; leaving them would
        re-classify them on every boot."""
        touched = False
        for _sk in journal.get("skipped", ()):
            if _sk.get("reason", "").startswith("operator stop intent") and "prune" not in _sk:
                _sk["prune"] = self._boot_prune_evidence(_sk.get("evidence_ids", []))
                touched = True
        if touched:
            boot_restore.write_journal(self._paths, journal)   # best-effort refresh

    def _boot_finish_disabled(self, cur_boot: str, evidence, reason: str,
                              skipped=()) -> ActionResult:
        """Gate off: start NOTHING, retire the foreign evidence, terminal `disabled` result —
        a later re-enable must not resurrect stacks from an older boot."""
        items = []
        by_stack: dict[str, list[str]] = {}
        for ev in evidence:
            by_stack.setdefault(ev.stack, []).append(ev.launch_id)
        for sid, ids in sorted(by_stack.items()):
            it = boot_restore.new_item(os.urandom(6).hex(), "stack", target=sid,
                                       evidence_ids=tuple(ids))
            it["state"] = "cancelled"
            it["result"] = {"reason": f"not restored: {reason}"}
            items.append(it)
        journal = boot_restore.new_journal(boot_id=cur_boot, pid=os.getpid(),
                                           process_start_time=self._own_start_time(),
                                           items=items)
        # REVIEW-FOUND: intent-skipped records must be retired here too — every OTHER
        # foreign record is, and "a later re-enable must not resurrect stacks from an older
        # boot" applies doubly to a stack the operator explicitly stopped.
        journal["skipped"] = list(skipped)
        journal["state"] = "disabled"
        journal["reason"] = reason
        journal["finished_at"] = time.time()
        # Durable retirement intent FIRST: a crash mid-prune must leave consumed-but-unpruned
        # items for the next recovery pass — never partially-retired evidence with no record
        # (which a later re-enable would partially resurrect).
        if not boot_restore.write_journal(self._paths, journal):
            return ActionResult(False, "Boot restore: journal unwritable.")
        for it in items:
            it["prune"] = self._boot_prune_evidence(it["evidence_ids"])
        for _sk in journal["skipped"]:
            if _sk.get("reason", "").startswith("operator stop intent"):
                _sk["prune"] = self._boot_prune_evidence(_sk.get("evidence_ids", []))
        if not boot_restore.write_journal(self._paths, journal):
            return ActionResult(False, "Boot restore: journal unwritable.")
        retired = sum(len(i["evidence_ids"]) for i in items)
        return ActionResult(True, f"Boot restore disabled ({reason}) — nothing started; "
                                  f"{retired} old evidence record(s) retired.",
                            data={"driver_completed": True})

    # ---- per-item execution ---------------------------------------------------------------------

    def _boot_claim_hook(self, journal, item):
        """The `_before_start_locked` closure for ONE item: under every start lock, revalidate the
        exact evidence and durably transition pending -> attempting. Returning an ActionResult
        cancels the start with zero side effects."""
        def hook():
            life = self._lifecycle()
            valid, _issues, dir_state = life.owned_inventory()
            if dir_state == "unsafe":
                # TRANSIENT integrity failure — the evidence may well still exist. Consuming the
                # item here would let the next recovery pass delete evidence that was never
                # attempted. Leave it pending (run truncates) and surface the integrity failure.
                item["_integrity"] = "owned dir unsafe at claim"
                return ActionResult(False, "boot-restore: ownership records unreadable — "
                                           "start cancelled, item left restorable")
            present = {r["launch_id"] for r in valid}
            troubled = {i.get("name", "") for i in _issues}
            if any(f"{e}.json" in troubled for e in item["evidence_ids"]):
                # Present-but-unreadable evidence is a TRANSIENT integrity state, not an
                # operator removal — cancelling would consume the item and let recovery delete
                # evidence that was never attempted. Leave it pending; the run truncates.
                item["_integrity"] = "evidence leaf unreadable at claim"
                return ActionResult(False, "boot-restore: evidence record unreadable — "
                                           "start cancelled, item left restorable")
            if not all(e in present for e in item["evidence_ids"]):
                item["state"] = "cancelled"
                item["result"] = {"reason": "cancelled by operator/current state "
                                            "(evidence removed before the attempt)"}
                boot_restore.write_journal(self._paths, journal)   # durable BEFORE refusing
                return ActionResult(False, f"boot-restore item {item['target'] or 'daemon'} "
                                           "cancelled: evidence no longer present")
            item["state"] = "attempting"
            if not boot_restore.write_journal(self._paths, journal):
                # The durable claim NEVER happened — the on-disk journal still says `pending`,
                # and only a durable `attempting` may consume an item. Keep the in-memory state
                # pending too (a later successful final write must not fabricate consumption).
                item["state"] = "pending"
                item["_integrity"] = "journal unwritable at claim"
                return ActionResult(False, "boot-restore: journal unwritable — start cancelled, "
                                           "item left restorable")
            return None
        return hook

    def _boot_settle_item(self, journal, item, res) -> None:
        if item["state"] != "attempting":
            return                                   # cancelled by the hook (already durable)
        ok = bool(res.ok or manual_required_only(getattr(res, "results", ()) or ()))
        item["state"] = "succeeded" if ok else "failed"
        item["result"] = {"ok": ok, "summary": res.summary}
        item["prune"] = self._boot_prune_evidence(item["evidence_ids"])
        if not boot_restore.write_journal(self._paths, journal):
            item["_integrity"] = "journal unwritable at settle"

    def _boot_run_stack_item(self, journal, item) -> None:
        res = self.start(item["target"], apply=True, band=item.get("band", ""),
                         _before_start_locked=self._boot_claim_hook(journal, item))
        self._boot_settle_item(journal, item, res)

    def _boot_run_daemon_item(self, journal, item) -> None:
        recorded = [b for b in item.get("bands", []) if b in ("433", "868")]
        kept, _owners = self._daemon_arbitrated_bands("")
        served = set(self._daemon_claimed_bands())
        residual = [b for b in recorded if b in kept and b not in served]
        if not residual:
            item["state"] = "succeeded"
            item["result"] = {"ok": True, "summary": "daemon reconciliation: no residual band "
                                                     "(already served / inactive / owned)"}
            # Durable consumption FIRST — pruning while the on-disk item still says `pending`
            # would, on a crash, destroy evidence that was never consumed (silent band loss).
            if not boot_restore.write_journal(self._paths, journal):
                item["state"] = "pending"
                item["_integrity"] = "journal unwritable at settle"
                return
            item["prune"] = self._boot_prune_evidence(item["evidence_ids"])
            if not boot_restore.write_journal(self._paths, journal):
                item["_integrity"] = "journal unwritable at settle"
            return
        params = {"radio": residual[0]} if len(residual) == 1 else None
        res = self.start(self.DAEMON_STACK_ID, apply=True, params=params,
                         _before_start_locked=self._boot_claim_hook(journal, item))
        self._boot_settle_item(journal, item, res)

    # ---- status projection (banner + CLI, file+/proc reads only) --------------------------------

    def boot_restore_status(self) -> dict | None:
        """Read-only projection for the banner and `lhpc autostart`. None = no journal."""
        from . import procident
        cur_boot = current_boot_id()
        if not cur_boot:
            # LIVE ephemeral projection (nothing persisted, the journal is NOT reinterpreted):
            # while the current boot identity is unreadable the driver refuses to run, and
            # showing an older terminal result — or nothing — would conceal why every stack
            # stayed down after this boot.
            return {"state": "blocked",
                    "reason": "current boot identity unavailable "
                              "(/proc/sys/kernel/random/boot_id unreadable) — automatic "
                              "restore is blocked until it can be read",
                    "run_id": "", "finished_at": None,
                    "counts": dict.fromkeys(boot_restore.ITEM_STATES, 0), "skipped": 0}
        journal, jstate = boot_restore.load_journal(self._paths)
        if jstate == "absent":
            return None
        if journal is None:
            return {"state": "unsafe", "reason": jstate}
        out = {"state": journal["state"], "run_id": journal["run_id"],
               "finished_at": journal.get("finished_at"),
               "reason": journal.get("reason", ""),
               "counts": {s: sum(1 for i in journal["items"] if i["state"] == s)
                          for s in boot_restore.ITEM_STATES},
               "skipped": len(journal.get("skipped", []))}
        if journal["state"] == "running":
            ident = procident.proc_identity(journal["pid"])
            foreign = journal.get("boot_id") != cur_boot   # cur_boot proven non-empty above
            live = (ident is not None
                    and str(ident.get("starttime")) == str(journal["process_start_time"])
                    and not foreign)
            if not live:
                out["state"] = "truncated"          # dead/reused/foreign driver
        elif journal["state"] == "failed" and out["counts"].get("pending"):
            # A COMPLETED run that left pending items (pre-hook lock failure) is a truncation:
            # nothing was consumed for those items and a unit restart restores the remainder.
            out["state"] = "truncated"
        return out

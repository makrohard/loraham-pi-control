"""Controller self-update orchestration + updater integration (helper units, markers, promote).

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import json
import os

from .paths import PathContainmentError
from .service_base import ActionResult, _StopRun, _proc_ceased, _proc_start_time


class SelfUpdateOpsMixin:

    def controller_log_tail(self, source: str = "web", lines: int = 300):
        """Raw (path, lines) for the controller's OWN process logs — the lhpc-web / lhpc-selfupdate
        units now log to on-disk FILES under logs/ (StandardOutput=append:), so the GUI reads them
        the same way as the nginx logs (the box's user journal is not reliably populated). `source`
        selects the file: 'selfupdate' -> lhpc-selfupdate.log, anything else -> lhpc-web.log. Same
        containment-safe, O_NOFOLLOW, bounded read as `webserver_log_tail`; never raises into GET."""
        from . import runtime_fs, updater_units
        const = updater_units.HELPER_LOG_REL if source == "selfupdate" else updater_units.WEB_LOG_REL
        try:
            n = max(1, min(int(lines), 5000))                 # clamp to a sane bounded range
        except (TypeError, ValueError):
            n = 300
        try:
            p = self._paths.under(*const)
        except PathContainmentError:
            return "", []
        if p.is_symlink() or (p.exists() and not p.is_file()):
            return str(p), []
        return str(p), runtime_fs.tail(self._paths, p, n)

    # ---- self-update (lhpc's own version/head/upstream) ----------------------

    def self_update_status(self) -> dict:
        """Cached, NETWORK-FREE self-update view for the footer/pages (reads the state marker only —
        never git, never network, safe for GET)."""
        from . import selfupdate
        return selfupdate.status_view(self._paths)

    def controller_status(self) -> dict | None:
        """Cached-only presentation of the controller (its OWN checkout) as a distinct
        NON-stack row for CLI status + the dashboard. Returns None if no controller is
        declared. Reads the cached self-update envelope ONLY — NO git, NO network, NO live
        identity check, NO blocking read (safe for GET). Points to `lhpc self-update`."""
        spec = self.controller()
        if spec is None:
            return None
        from . import selfupdate
        view = selfupdate.status_view(self._paths)        # cached envelope only
        return {
            "id": spec.id,
            "display_name": spec.display_name,
            "branch": spec.branch,
            "version": view.get("version", ""),
            "head_short": view.get("head_short", ""),
            "update_available": bool(view.get("update_available", False)),
            "identity": view.get("identity"),             # {ok, reason, checked_at} or None
            "self_update_cmd": "lhpc self-update",
        }

    def controller_system_deps(self) -> list[dict]:
        """LHPC's OWN system/runtime dependencies (git, nginx, systemd, install-time tools, venv deps),
        grouped, each with presence + install command. The SINGLE source of truth for both the
        controller System-dependencies panel (/stacks) and `lhpc doctor`, so the two never drift.
        GET-SAFE: presence probes only — `shutil.which` / `System.fs.exists` / `importlib.util.find_spec`
        — never a subprocess (git/nginx/systemctl are NOT executed)."""
        import shutil
        import sys
        import importlib.util
        from . import webserver as _ws
        # Python venv deps must be (re)installed into the SAME interpreter that runs LHPC — never a bare
        # `pip install` (wrong env / PEP-668). Version floors mirror pyproject.toml.
        _pipi = f"{sys.executable} -m pip install"
        fs = self._system.fs

        def have_cmd(cmd: str, *fallbacks: str) -> bool:
            # PATH first (a managed unit's PATH can be narrower than a shell), then safe absolute-path
            # fallbacks via the injectable fs.exists — NEVER executes the binary. No subprocess.
            return shutil.which(cmd) is not None or any(fs.exists(p) for p in fallbacks)

        def have_mod(mod: str) -> bool:
            try:
                return importlib.util.find_spec(mod) is not None
            except (ImportError, ValueError):
                return False

        return [
            {"title": "System packages (apt)", "deps": [
                {"what": "git", "required": True,
                 "satisfied": have_cmd("git", "/usr/bin/git", "/usr/local/bin/git"),
                 "install": "sudo apt install -y git",
                 "purpose": "self-update fast-forward, initial clone, source adoption"},
                {"what": "nginx", "required": False,
                 "satisfied": have_cmd("nginx", "/usr/sbin/nginx", "/usr/bin/nginx"),
                 "install": _ws.NGINX_INSTALL_CMD,
                 "purpose": "HTTPS + mTLS front-end — the console runs over loopback without it; "
                            "exposed/HTTPS access needs it"},
                {"what": "systemd (systemctl, loginctl)", "required": False,
                 "satisfied": (have_cmd("systemctl", "/usr/bin/systemctl", "/bin/systemctl")
                               and have_cmd("loginctl", "/usr/bin/loginctl", "/bin/loginctl")),
                 # NOT installable by command: if systemctl/loginctl are absent this host is not systemd,
                 # and `apt install systemd` is not the fix. Explain instead of offering nonsense advice.
                 "install": "",
                 "note": "managed-service mode is unavailable here — provide systemd (boot the OS with "
                         "systemd / enable the `systemctl --user` session), or run without it: `lhpc web` "
                         "in the foreground now, then `lhpc self-update --repair-integration` once systemd "
                         "is available. No package can add systemd (`apt install systemd` is not the fix).",
                 "purpose": "the managed --user service + boot linger (only for managed-service mode)"},
            ]},
            {"title": "Install-time", "deps": [
                {"what": "python3 (>= 3.11)", "required": True,
                 "satisfied": have_cmd("python3", "/usr/bin/python3", "/usr/local/bin/python3"),
                 "install": "sudo apt install -y python3", "purpose": "the controller runtime"},
                {"what": "python3-venv", "required": True,
                 "satisfied": have_mod("venv") and have_mod("ensurepip"),
                 "install": "sudo apt install -y python3-venv",
                 "purpose": "builds the LHPC virtualenv (venv + ensurepip)"},
                {"what": "pip", "required": True, "satisfied": have_mod("pip"),
                 "install": "sudo apt install -y python3-pip",
                 "purpose": "editable install + venv sync on self-update"},
            ]},
            {"title": "Python venv dependencies (pip, in venv/lhpc)", "deps": [
                {"what": "flask", "required": True, "satisfied": have_mod("flask"),
                 "install": f"{_pipi} 'flask>=3,<4'", "purpose": "web console"},
                {"what": "waitress", "required": True, "satisfied": have_mod("waitress"),
                 "install": f"{_pipi} 'waitress>=3,<4'",
                 "purpose": "production WSGI server (no dev-server fallback)"},
                {"what": "cryptography", "required": True, "satisfied": have_mod("cryptography"),
                 "install": f"{_pipi} 'cryptography>=42'", "purpose": "all PKI (CA / cert / PKCS#12 / CRL)"},
            ]},
        ]

    def self_update_check(self) -> ActionResult:
        """Explicit upstream freshness check (NETWORK: `git fetch`) — refreshes the cached marker so
        the footer/pages reflect it. Serialized with apply through the self-update lock: if an apply is
        in progress it DEFERS (nonfatal) with the last cached status instead of racing its refs/cache.
        Under the lock it applies the SAME pure recovery-state gate as apply (`classify_journal`)
        BEFORE `refresh_cache`/`check_upstream`/fetch/cache write: an unreadable/corrupt/unsafe OR
        recovery-blocked journal blocks the check with NO fetch and NO cache/journal/config/source
        mutation. Fail-soft."""
        from . import selfupdate
        try:
            with selfupdate.update_lock(self._paths):
                status, _env, _head = selfupdate.classify_journal(self._paths, self._system)  # BEFORE any fetch
                if status == "blocked":
                    return ActionResult(False, "Self-update check blocked: the migration journal is "
                                        "unreadable, corrupt or unsafe. No upstream check was made — "
                                        "recovery needed (inspect state/selfupdate-migrate.json).",
                                        data={"journal_corrupt": True,
                                              **selfupdate.status_view(self._paths)})
                if status == "recovery_required":
                    return ActionResult(False, "Self-update check blocked: the checkout is at an "
                                        "unexpected commit for a recorded migration transition. No "
                                        "upstream check was made — recovery required (inspect "
                                        "state/selfupdate-migrate.json).",
                                        data={"recovery_required": True,
                                              **selfupdate.status_view(self._paths)})
                # Embed the LIVE controller-identity verdict into the SAME atomic envelope
                # write (a separate field could be dropped by a later refresh). GET/status
                # then renders the cached verdict only.
                identity = self.controller_identity_live() if self.controller() else None
                view = selfupdate.refresh_cache(self._system, self._paths, identity=identity)
        except selfupdate.SelfUpdateBusy:
            view = selfupdate.status_view(self._paths)        # no fetch, no cache write
            return ActionResult(True, "A self-update is in progress — showing the last known status.",
                                data={**view, "deferred": True})
        except selfupdate.UpdateLockError:
            return ActionResult(False, "Could not check upstream (unsafe runtime state).",
                                data=selfupdate.status_view(self._paths))
        if not view["is_git"]:
            return ActionResult(False, "Self-update is unavailable (lhpc is not a git checkout).",
                                data=view)
        if not view["have_upstream"]:
            return ActionResult(False, f"Could not reach upstream: {view.get('upstream_error', '')}.",
                                data=view)
        if view["update_available"]:
            msg = (f"Update available — upstream {view['upstream_head_short']}"
                   f" (v{view['upstream_version'] or '?'}).")
            if view.get("ff_blocked"):
                msg += (" — local HEAD is not an ancestor of the current upstream; a normal "
                        "fast-forward update will be REFUSED. Review the divergence, then use "
                        "`--overwrite` (or the web confirmation) to reset onto upstream.")
            return ActionResult(True, msg, data=view)
        return ActionResult(True, "Up to date.", data=view)

    def self_update_apply(self, *, force: bool = False) -> ActionResult:
        """Apply the update as ONE serialized, fail-closed transaction (the interprocess self-update
        lock covers candidate capture, journal persistence, fetch/ref resolution, merge/reset/clean,
        cache writes, config migration and journal finalization). BLOCKED while an lhpc job is active;
        a concurrent apply returns 'busy' with zero mutation. A DIRTY tree is refused unless
        `force=True`. Legacy default-equal config is migrated to the new defaults only when the source
        transition it was captured against actually completed — recorded DURABLY before source changes
        and recovered from the journal after a crash. Cleanup failure on force is a truthful partial."""
        from . import reslock, selfupdate
        from .service_base import AdmissionRefused
        # LOCK ORDER: (1) task admission FIRST — held across the WHOLE mutation so no task can be
        # reserved/spawned/started while the checkout + venv are changing; (2) controller-runtime +
        # self-update locks. `_admission_guard`'s strict check ALSO enforces "direct/operator apply
        # requires the request state ABSENT" (a pending/in-flight/malformed request blocks) and refuses
        # during an uninstall — with zero mutation. An already-admitted task -> typed busy.
        try:
            with self._admission_guard("self-update-apply"):
                # Re-run ALL authoritative blocker checks AFTER admission is held (nothing can have
                # started a job/auto-install/HMAC in the acquisition window).
                blk = self._self_update_blockers()
                if blk:
                    return ActionResult(False, f"Self-update blocked: {blk[0]} — resolve it before "
                                        "self-updating.", data={f"blocked_by_{blk[1]}": True})
                # LIVE identity gate (recomputed here, NEVER trusting the cache): only a genuinely
                # UNSAFE self-hosted checkout blocks apply before any mutation.
                if self.controller() is not None:
                    idv = self.controller_identity_live()
                    if idv.get("status") == "unsafe":
                        return ActionResult(False, f"Self-update blocked: unsafe controller identity "
                                            f"({idv['reason']}). No changes were made.",
                                            data={"identity_unsafe": True, "identity": idv})
                # controller-runtime EXCLUSIVE (so the running web server, holding it SHARED, can never
                # have its source mutated underneath it), THEN the self-update lock. Both non-blocking.
                with selfupdate.controller_runtime_lock(self._paths, exclusive=True):
                    with selfupdate.update_lock(self._paths):
                        return self._self_update_locked(force)
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
        except reslock.ResourceBusy:
            return ActionResult(False, "A task is starting right now (admission contended) — try the "
                                "update again shortly.", data={"contended": True})
        except selfupdate.ControllerRuntimeBusy:
            return ActionResult(
                False, "lhpc-web.service is running — stop it, update, then start it again.",
                details=["systemctl --user stop lhpc-web",
                         "lhpc self-update --apply",
                         "systemctl --user start lhpc-web",
                         "(or just click 'Update now' in the web console — it does all this)"],
                data={"web_running": True})
        except selfupdate.ControllerRuntimeLockError:
            return ActionResult(False, "Could not acquire the controller-runtime lock (unsafe runtime "
                                "state) — aborting without changes.", data={"lock_error": True})
        except selfupdate.SelfUpdateBusy:
            return ActionResult(False, "A self-update is already in progress — try again shortly.",
                                data={"busy": True})
        except selfupdate.UpdateLockError:
            return ActionResult(False, "Could not acquire the self-update lock (unsafe runtime state) "
                                "— aborting without changes.", data={"lock_error": True})

    def _apply_and_sync(self, force: bool) -> ActionResult:
        """Apply the source update, then — ONLY on a REAL advance — synchronize the editable venv install
        with the SAME `sys.executable -m pip install -e <repo-root>` the managed helper runs, so a
        dependency change never leaves the install unusable. MUST be called with task admission already
        HELD (by the operator flow) so nothing starts between apply and sync. A no-op/already-current or a
        failed/refused apply runs NO pip. On a real advance the result carries `update_applied=True`; a
        pip failure returns `ok=False` + `venv_sync_failed=True`, preserving the apply-result data."""
        import dataclasses as _dc
        import sys as _sys
        from . import selfupdate
        res = self.self_update_apply(force=force)
        if not (res.ok and not res.data.get("already")):
            return res                                        # no-op / already-current / failed / refused
        res = _dc.replace(res, data={**res.data, "update_applied": True})   # a real source advance
        root = selfupdate.repo_root()
        if root is not None:
            pip = self._system.runner.run(
                [_sys.executable, "-m", "pip", "install", "-e", str(root)], self._PIP_SYNC_TIMEOUT_S)
            if pip.returncode != 0:
                detail = selfupdate._summarize_output(pip.stderr or pip.stdout)
                return _dc.replace(res, ok=False,
                                   summary="Update applied but the venv sync FAILED — run "
                                   f"{_sys.executable} -m pip install -e {root} manually."
                                   + (f" ({detail})" if detail else ""),
                                   data={**res.data, "venv_sync_failed": True})
        return res

    def self_update_apply_operator(self, *, force: bool = False) -> ActionResult:
        """OPERATOR-CONTEXT `lhpc self-update --apply`: WARN-then-DO under a CONTINUOUSLY-held task
        admission lock. If the managed web console is running it holds the controller-runtime lock
        SHARED, so we STOP lhpc-web, apply + sync the venv, then START it again. When the console is NOT
        running we STILL apply AND sync the venv (a dependency change must never leave it unusable) — the
        only difference is no service control. Admission is NEVER released between apply and sync in
        either case. REFUSES inside a managed unit (a managed process must never drive systemctl)."""
        import os as _os
        from . import reslock, updater_units
        from .service_base import AdmissionRefused
        if _os.environ.get("INVOCATION_ID"):
            return ActionResult(False, "refusing to stop/start services from a managed unit — run "
                                "`lhpc self-update --apply` from an interactive operator shell")
        _S = 30.0
        act = self._system.runner.run(
            ["systemctl", "--user", "is-active", "--quiet", updater_units.WEB_UNIT], _S)
        web_active = (not getattr(act, "not_found", False)) and act.returncode == 0
        try:
            with self._admission_guard("self-update-operator"):
                if not web_active:
                    # No service to orchestrate — but STILL apply AND sync the venv, under held admission.
                    return self._apply_and_sync(force)
                stop = self._system.runner.run(["systemctl", "--user", "stop", updater_units.WEB_UNIT], _S)
                if getattr(stop, "not_found", False) or stop.returncode != 0:
                    return ActionResult(False, "could not stop lhpc-web.service — stop it manually then retry",
                                        details=["systemctl --user stop lhpc-web",
                                                 "lhpc self-update --apply",
                                                 "systemctl --user start lhpc-web"],
                                        data={"stop_failed": True})
                try:
                    res = self._apply_and_sync(force)         # admission still held; venv synced here
                finally:
                    start = self._system.runner.run(
                        ["systemctl", "--user", "start", updater_units.WEB_UNIT], _S)
                    restart_failed = getattr(start, "not_found", False) or start.returncode != 0
                if restart_failed:
                    # A failed REQUIRED restart is ALWAYS ok=False (the console is unavailable). Distinguish
                    # partial success: the source update may have applied (update_applied) — preserve that
                    # AND any venv_sync failure. The summary gives the exact recovery command, no raw output.
                    return ActionResult(False, res.summary + "  — AND lhpc-web did NOT restart. Recover "
                                        "with: systemctl --user start lhpc-web.service",
                                        details=tuple(res.details),
                                        data={**dict(res.data), "web_restart_failed": True,
                                              "update_applied": bool(res.data.get("update_applied"))})
                return res
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
        except reslock.ResourceBusy:
            return ActionResult(False, "A task is starting right now (admission contended) — retry the "
                                "update.", data={"contended": True})

    def _self_update_locked(self, force: bool) -> ActionResult:
        from . import selfupdate
        # 1. PURE classification of the untrusted envelope against the ACTUAL head (shared with the
        #    freshness check). A blocked/recovery-required state stops here BEFORE any fetch / source /
        #    cache / config / journal mutation.
        status, env, head_now = selfupdate.classify_journal(self._paths, self._system)
        if status == "blocked":
            return ActionResult(False, "Self-update blocked: the migration journal is missing-but-"
                                "present-unreadable, corrupt or unsafe. No changes were made — recovery "
                                "needed (inspect / remove state/selfupdate-migrate.json).",
                                data={"journal_corrupt": True})
        if status == "recovery_required":
            return ActionResult(False, "Self-update blocked: the checkout is at an unexpected commit for "
                                "a recorded migration transition. No changes were made — recovery "
                                "required (inspect state/selfupdate-migrate.json).",
                                data={"recovery_required": True})
        completed = env.get("completed") if env else None
        prepared = env.get("prepared") if env else None

        # 2. Reconcile a prior PREPARED attempt (classifier verified its ANCHOR + endpoint): its to_head
        #    reached -> promote (carrying the anchor txid); still at from_head -> the git never happened,
        #    so delete its anchor and drop it, keeping the prior completed intact.
        if prepared:
            if head_now == prepared["to_head"]:
                completed = self._promote(completed, prepared)          # keep the anchor for the completed slot
                self._write_envelope(completed, None)
            elif selfupdate.clear_migration_journal(self._paths):       # git never happened -> drop stale
                selfupdate.delete_anchor(self._system, prepared.get("txid"))   # prepared + its anchor

        # 3. Migrate the PRIOR completed transition FIRST, obtaining AUTHORITATIVE from_head + candidate
        #    payload from its durable ANCHOR (never the journal fields alone). The journal/anchor are
        #    IMMUTABLE while pending — migration is idempotent (already-removed keys no-op) — and are
        #    cleared only once fully resolved; otherwise the update is DEFERRED.
        migrated = 0
        if completed and completed["pending"]:
            anchor = selfupdate.anchored_record(self._system, completed)
            if anchor is None:                                           # defensive (classifier verified)
                return ActionResult(False, "Self-update blocked: the recorded migration transition is "
                                    "not authorised by a matching durable anchor. No changes were made "
                                    "— recovery required.", data={"recovery_required": True})
            m, remaining = self._run_migration(anchor["pending"], anchor["from_head"])
            migrated += m
            if remaining:
                return ActionResult(True, "Prior config migration is incomplete — deferring the update "
                                    "until it completes; it will be retried on the next self-update.",
                                    data={"migrated": migrated, "pending_migrations": len(remaining),
                                          "deferred_recovery": True})
            if selfupdate.clear_migration_journal(self._paths):         # clear FIRST; drop anchor only if
                selfupdate.delete_anchor(self._system, completed.get("txid"))   # the journal is really gone

        # 4. Attempt a NEW update. Fail-closed PREPARE hook creates the durable git ANCHOR, then the
        #    runtime journal referencing it, BOTH atomically BEFORE the checkout is advanced.
        new_candidates = self._migration_candidates()
        hook = {"written": False, "from": "", "to": "", "branch": "", "intent": [], "txid": ""}

        def _before_mutation(from_head, to_head, branch, _deps):
            intent = self._stamp(new_candidates, from_head)
            hook.update(**{"from": from_head, "to": to_head, "branch": branch, "intent": intent})
            if not intent:
                return
            txid = selfupdate.new_txid()
            payload = {"from_head": from_head, "to_head": to_head, "branch": branch, "pending": intent}
            if not selfupdate.create_anchor(self._system, txid, payload):     # durable provenance FIRST
                raise selfupdate.JournalPersistError()
            rec = {**payload, "txid": txid}
            if not selfupdate.write_migration_journal(self._paths, {"completed": None, "prepared": rec}):
                selfupdate.delete_anchor(self._system, txid)
                raise selfupdate.JournalPersistError()
            hook.update(written=True, txid=txid)

        try:
            res = selfupdate.apply_update(self._system, self._paths, force=force,
                                          before_mutation=_before_mutation)
        except selfupdate.JournalPersistError:
            return ActionResult(False, "Refusing to self-update: could not durably record the config-"
                                "migration intent before changing source. No changes were made.",
                                data={"journal_write_failed": True})

        # 5. On a REAL advance WITH candidates (hook.written -> a valid anchor + journal exist), promote
        #    the prepared transition to `completed` (keeping the anchor), then migrate. Fully resolved ->
        #    clear journal + delete anchor; else keep for retry. A NO-CANDIDATE advance wrote no anchor
        #    and no journal, so there is nothing to promote (never write a record with an empty txid).
        #    Failed/refused leaves config untouched and drops the fresh anchor + journal.
        remaining: list = []
        if res.get("ok") and not res.get("already") and hook["written"]:
            rec = {"from_head": hook["from"], "to_head": hook["to"], "branch": hook["branch"],
                   "pending": hook["intent"], "txid": hook["txid"]}
            self._write_envelope(rec, None)                              # promote prepared -> completed
            m, remaining = self._run_migration(hook["intent"], hook["from"])
            migrated += m
            if not remaining and selfupdate.clear_migration_journal(self._paths):
                selfupdate.delete_anchor(self._system, hook["txid"])
        elif not res.get("ok") and hook["written"]:          # git failed after prepare -> drop anchor+journal
            if selfupdate.clear_migration_journal(self._paths):
                selfupdate.delete_anchor(self._system, hook["txid"])

        instr = selfupdate.restart_instructions(res.get("deps_changed", False),
                                                self._controller_deps_sync_cmd())
        data = {**res, "restart": instr, "migrated": migrated, "pending_migrations": len(remaining)}
        migrated_note = f"{migrated} legacy default(s) migrated to the new defaults." if migrated else ""
        pending_note = (f"{len(remaining)} config default migration(s) could NOT be completed and will "
                        "be retried on the next self-update.") if remaining else ""

        if not res["ok"]:                                    # git failure: dirty refusal / diverged / fetch
            # On a DIRTY refusal, name the paths a force would discard. `message` stays single-line
            # (it is flashed verbatim); the evidence rides in details.
            refusal = [f"  {ln}" for ln in res.get("changes", ())]
            if refusal:
                refusal.insert(0, "These paths would be discarded by 'overwrite local changes':")
            return ActionResult(False, res["message"], details=refusal, data=data)
        if res.get("cleanup_failed"):                        # updated, but untracked cleanup failed -> partial
            details = [res.get("cleanup_error", ""), instr.get("note", ""),
                       "Restart the web console after cleaning up:"]
            details += ["  " + c for c in instr["commands"]]
            details += [n for n in (migrated_note, pending_note) if n]
            return ActionResult(False, res["message"], data=data,
                                details=tuple(d for d in details if d))
        if res.get("already"):                               # nothing to update; may have recovered pending
            details = tuple(n for n in (migrated_note, pending_note) if n)
            return ActionResult(True, res["message"], data=data, details=details)
        details = [n for n in (instr.get("note", ""),) if n]
        details += ["Restart the web console to load the new version:"]
        details += ["  " + c for c in instr["commands"]]
        details += [n for n in (migrated_note, pending_note) if n]
        return ActionResult(True, res["message"], data=data, details=tuple(details),
                            next_commands=list(instr["commands"]))

    def _user_unit_dir(self):
        from pathlib import Path
        return Path(os.path.expanduser("~")) / ".config" / "systemd" / "user"

    def _marker_present(self, name: str) -> bool:
        from . import runtime_fs
        try:
            return runtime_fs.stat_leaf_nofollow(self._paths, self._paths.under(name)) is not None
        except Exception:
            return False

    def updater_integration(self) -> dict:
        """GET-safe (file reads only, no subprocess/bus): status of the managed web+updater unit
        set for THIS runtime root, plus request-state so the UI can surface 'recovery required'."""
        from . import updater_units
        root = str(self._paths.runtime_root)
        integ = updater_units.integration(self._user_unit_dir(), root)
        req = self.classify_request()
        if req in ("in_flight", "malformed") or self.uninstall_guard_blocks():
            integ = dict(integ, status="recovery_required", request=req)
        else:
            integ["request"] = req
        # `fixable`: a non-canonical set the console can auto-migrate in one click — every unit is
        # ok/missing/modified_ours (this deployment's), and no recovery is pending.
        _fixable = (updater_units.OK, updater_units.MISSING, updater_units.MODIFIED_OURS)
        integ["fixable"] = (integ["status"] != "recovery_required"
                            and all(v in _fixable for v in integ["per_unit"].values()))
        # Is THIS console the managed systemd unit? (INVOCATION_ID is set only by systemd.) The unit
        # FILES can verify 'ok' while the console actually runs in a foreground shell — one-click
        # update and boot autostart both need the managed service, so surface the distinction.
        integ["managed"] = bool(os.environ.get("INVOCATION_ID"))
        return integ

    def self_update_local_dirty(self) -> bool:
        """FRESH local dirty check for the one-click confirm step (git status only — local,
        no network). POST-time only; GET rendering stays cached-only."""
        from . import selfupdate
        return bool(selfupdate.local_state(self._system).get("dirty") is True)

    def self_update_ff_blocked(self) -> bool:
        """FRESH check for the one-click confirm step: has the local history DIVERGED so a normal
        fast-forward update would be refused (only a force/reset can update)? Network-free (uses the
        already-fetched remote-tracking ref). POST-time only; GET rendering stays cached-only."""
        from . import selfupdate
        return selfupdate.ff_blocked(self._system)

    def self_update_local_changes(self, limit: int = 20) -> tuple:
        """The paths an overwrite would discard (`git status --porcelain`). Local git, POST-time
        only — the confirm must SHOW what it is about to reset, not just assert that it must."""
        from . import selfupdate
        return selfupdate.local_changes(self._system, limit)

    def self_update_divergence(self) -> tuple:
        """`(ahead, behind)` of the local history vs the fetched upstream ref. Local git, POST-time
        only. Names the size of the divergence the confirm otherwise only alludes to."""
        from . import selfupdate
        return selfupdate.divergence(self._system)

    def self_update_branch(self) -> str:
        """The checkout's branch, for naming the upstream ref in the confirm (`origin/<branch>`)."""
        from . import selfupdate
        return str(selfupdate.local_state(self._system).get("branch") or "main")

    # ---- web trigger: write the exclusive request marker (NO systemctl, NO bus) ---------------

    def self_update_trigger(self, *, overwrite: bool = False) -> ActionResult:
        """WEB stage-2: admit exactly one update request by EXCLUSIVELY creating the in-root
        request marker (payload `normal`|`overwrite` — a 1-bit selector the helper re-validates).
        A static .path unit consumes it. Refuses unless this process is the MANAGED web unit
        (INVOCATION_ID) with a byte-exact integration, no active job, an available+safe checkout,
        and no pending/in-flight/uninstall evidence — so a foreground console or a tampered unit
        never writes a request nobody safely consumes."""
        from . import runtime_fs, updater_units
        if not os.environ.get("INVOCATION_ID"):
            return ActionResult(
                False, "One-click update needs the managed web service (systemd). This console is "
                "running in the foreground.",
                details=["  lhpc self-update --repair-integration   "
                         "# installs + enables + starts the service, and enables boot autostart",
                         "  lhpc self-update --apply                # then update"],
                next_commands=["lhpc self-update --repair-integration", "lhpc self-update --apply"],
                data={"not_managed": True})
        integ = self.updater_integration()
        if integ["status"] == "recovery_required":
            return ActionResult(False, "A previous update needs recovery first — run "
                                "`lhpc self-update --recover-request`.", data={"recovery_required": True})
        if integ["status"] != "ok":
            return ActionResult(False, "One-click update is unavailable — the web/updater units are "
                                f"not the canonical managed set ({integ['status']}). Run `lhpc "
                                "self-update --repair-integration`, or `lhpc self-update --apply`.",
                                data={"integration": integ["status"]})
        st = self.self_update_status()
        if not st.get("available"):
            return ActionResult(False, "Self-update is unavailable — lhpc is not running from a "
                                "git checkout.", data={"unavailable": True})
        idv = st.get("identity")
        if isinstance(idv, dict) and idv.get("status") == "unsafe":
            return ActionResult(False, "Self-update blocked: unsafe controller identity "
                                f"({idv.get('reason', '')}).", data={"identity_unsafe": True})
        mode = "overwrite" if overwrite else "normal"
        import contextlib
        from . import reslock
        # Hold task admission through the EXCLUSIVE request-marker creation (lock order #1): a new task
        # cannot start while we create it. Recheck the uninstall guard + request state AND run the
        # complete strict blocker scan UNDER the lock, so nothing slips in between the checks and the
        # atomic marker create.
        with contextlib.ExitStack() as adm:
            try:
                self._acquire_key(adm, self.ADMISSION_KEY, "self-update-trigger", "")
            except reslock.ResourceBusy:
                return ActionResult(False, "A task is starting right now (admission contended) — retry "
                                    "the update.", data={"contended": True})
            if self.uninstall_guard_blocks():
                return ActionResult(False, "A controller uninstall is in progress — cannot self-update.",
                                    data={"uninstalling": True})
            if self.classify_request() != "absent":
                return ActionResult(False, "An update request is already pending — the console is about "
                                    "to update.", data={"already_pending": True})
            blk = self._self_update_blockers()
            if blk:
                return ActionResult(False, f"Self-update blocked: {blk[0]}.",
                                    data={f"blocked_by_{blk[1]}": True})
            try:
                m = runtime_fs.open_marker_excl(self._paths,
                                                self._paths.under(*updater_units.REQUEST_REL),
                                                mode + "\n")
                m.close()
            except FileExistsError:
                return ActionResult(False, "An update request is already pending — the console is about "
                                    "to update.", data={"already_pending": True})
            except Exception as exc:                           # containment / fs error
                return ActionResult(False, f"Could not queue the update request: {exc}",
                                    data={"trigger_failed": True})
        return ActionResult(True, "Update queued — the console will stop, update itself and come "
                            "back automatically.", data={"triggered": True, "mode": mode})

    # ---- the helper (unit ExecStart): claim -> apply -> sync -> record -> release -------------

    def self_update_run_service(self) -> ActionResult:
        """PLUMBING, run ONLY by lhpc-selfupdate.service. Holds task ADMISSION across the COMPLETE
        transaction (claim -> apply -> venv sync -> durable record -> in-flight release) so no task can
        be started while the checkout/venv are still changing (closing the post-update window).
        Admission is acquired RAW — the helper OWNS the in-flight record it is about to write, so the
        strict request self-check would wrongly refuse it — but it is still reentrant, so the inner
        `self_update_apply` reuses the SAME lock. A concurrent holder -> typed busy, zero mutation."""
        import contextlib
        from . import reslock
        with contextlib.ExitStack() as adm:
            try:
                self._admit_raw(adm, "self-update-helper")
            except reslock.ResourceBusy:
                return ActionResult(False, "A task is starting right now (admission contended) — the "
                                    "update helper will retry on the next request.", data={"contended": True})
            return self._self_update_run_service_locked()

    def _self_update_run_service_locked(self) -> ActionResult:
        """The helper body, run UNDER the held task-admission lock (see `self_update_run_service`):
        claim -> prove ownership -> apply -> venv sync -> durable record -> in-flight release."""
        import sys
        import time as _time
        from . import runtime_fs, selfupdate, updater_units
        req_path = self._paths.under(*updater_units.REQUEST_REL)
        inflight = self._paths.under(*updater_units.INFLIGHT_REL)
        # CLAIM: atomic NO-OVERWRITE rename request -> inflight. Absent request = stray start ->
        # clean no-op. A pre-existing in-flight record (prior interrupted run) means the claim
        # fails closed (FileExistsError) and BOTH markers are preserved for recovery — the helper
        # is the security boundary and never clobbers in-flight evidence.
        try:
            runtime_fs.rename_leaf(self._paths, req_path, inflight, replace=False)
        except FileNotFoundError:
            return ActionResult(True, "No update request to service.", data={"noop": True})
        except FileExistsError:
            return ActionResult(False, "A previous update is already in flight — recovery required "
                                "(`lhpc self-update --recover-request`).",
                                data={"recovery_required": True})
        except Exception as exc:
            return ActionResult(False, f"Could not claim the update request: {exc}",
                                data={"claim_failed": True})
        # Read mode, then overwrite the in-flight record with a process-identity claim.
        try:
            mode = runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096).strip()
        except Exception:
            mode = ""
        if mode not in ("normal", "overwrite"):
            runtime_fs.write_marker(self._paths, inflight,
                                    json.dumps({"mode": None, "error": "malformed-request"}))
            selfupdate.record_last_apply_strict(self._paths, ok=False,
                                                summary="Update request was malformed — recovery required.")
            return ActionResult(False, "Malformed update request — recovery required.",
                                data={"malformed": True})
        force = (mode == "overwrite")
        runtime_fs.write_marker(self._paths, inflight, json.dumps(self._helper_identity(mode)))
        # PROVE this exact helper owns the in-flight record it just wrote (durable PID + /proc start
        # time) — defends against a concurrent clobber between claim and write, and against a foreign/
        # forged/PID-reused owner. Unproven -> block, retain evidence, recovery required.
        if not self._helper_owns_inflight():
            selfupdate.record_last_apply_strict(
                self._paths, ok=False,
                summary="In-flight update ownership could not be proven — recovery required.")
            return ActionResult(False, "In-flight update ownership could not be proven — recovery "
                                "required (`lhpc self-update --recover-request`).",
                                data={"ownership_unproven": True})

        res = ActionResult(False, "Self-update service did not run.", data={})
        try:
            # Web is stopped (Conflicts+After) so its SHARED lock is released; take EXCLUSIVE with
            # a short bounded retry to cover the stop-completion window, then apply.
            deadline = _time.monotonic() + self._LOCK_WAIT_S
            while True:
                try:
                    with selfupdate.controller_runtime_lock(self._paths, exclusive=True):
                        break
                except selfupdate.ControllerRuntimeBusy:
                    if _time.monotonic() >= deadline:
                        res = ActionResult(False, "The console did not release the controller-runtime "
                                           "lock — no changes made.", data={"web_running": True})
                        raise _StopRun()
                    _time.sleep(0.5)
                except selfupdate.ControllerRuntimeLockError:
                    res = ActionResult(False, "Could not acquire the controller-runtime lock "
                                       "(unsafe runtime state) — no changes made.",
                                       data={"lock_error": True})
                    raise _StopRun()
            res = self.self_update_apply(force=force)
            if res.ok and not res.data.get("already"):
                root = selfupdate.repo_root()
                if root is not None:
                    pip = self._system.runner.run(
                        [sys.executable, "-m", "pip", "install", "-e", str(root)],
                        timeout=self._PIP_SYNC_TIMEOUT_S)
                    if pip.returncode != 0:                     # P2: a failed sync FAILS the update
                        # First line of pip's diagnostics, stripped of box-drawing/ANSI so the
                        # persisted summary reads cleanly in the GUI flash (never a mid-box tail).
                        detail = selfupdate._summarize_output(pip.stderr or pip.stdout)
                        res = ActionResult(False, "Update applied, but the venv sync FAILED — run "
                                           f"{sys.executable} -m pip install -e {root} manually, then "
                                           f"restart the console." + (f" ({detail})" if detail else ""),
                                           data={**dict(res.data), "venv_sync_failed": True})
        except _StopRun:
            pass
        # Record the outcome DURABLY, then release the in-flight record. If the STRICT record does
        # not persist, retain in-flight and report incomplete (one-click blocked until recovery) —
        # never delete the evidence on an unrecorded outcome.
        if not selfupdate.record_last_apply_strict(self._paths, ok=bool(res.ok), summary=res.summary):
            return ActionResult(False, "Update outcome could not be recorded durably — recovery "
                                "required (`lhpc self-update --recover-request`).",
                                data={**dict(res.data), "record_failed": True})
        try:
            runtime_fs.unlink(self._paths, inflight)
        except Exception as exc:
            return ActionResult(False, res.summary + f" (in-flight marker cleanup FAILED: {exc} — "
                                "recovery required)", data={**dict(res.data), "cleanup_failed": True})
        return res

    def _helper_owns_inflight(self) -> bool:
        """True iff the in-flight record's identity matches THIS process — its recorded PID AND that
        PID's current /proc start time both equal this process's. Fail-closed: a missing/malformed/
        unreadable record, a foreign PID, or a PID whose start time differs (PID reuse) all return
        False. Never trusts PID alone."""
        from . import runtime_fs, updater_units
        try:
            rec = json.loads(runtime_fs.read_text_regular(
                self._paths, self._paths.under(*updater_units.INFLIGHT_REL), max_bytes=4096))
        except Exception:                                  # noqa: BLE001 — cannot prove -> not owner
            return False
        if not isinstance(rec, dict):
            return False
        pid = rec.get("pid")
        return (pid == os.getpid()
                and rec.get("start_time") == _proc_start_time(os.getpid()))

    def _helper_identity(self, mode: str) -> dict:
        """Bounded process-identity record stored in the in-flight marker so recovery can prove
        the original helper has ceased before clearing it (never age-based)."""
        import hashlib
        import sys
        import time as _time
        pid = os.getpid()
        return {"mode": mode, "pid": pid, "start_time": _proc_start_time(pid),
                "exe": (os.readlink(f"/proc/{pid}/exe") if os.path.exists(f"/proc/{pid}/exe") else ""),
                "argv_hash": hashlib.sha256(("\0".join(sys.argv)).encode()).hexdigest()[:16],
                "claimed_at": int(_time.time())}

    def uninstall_guard_blocks(self) -> bool:
        """STRICT, fail-CLOSED uninstall-guard inspection for admission/teardown decisions. Absence is
        the ONLY 'not uninstalling' result: a present guard of ANY kind (regular, symlink, directory,
        FIFO, device) blocks new work, and an inspection failure (cannot prove absent) ALSO blocks.
        Descriptor-anchored, no-follow. Do NOT use the fail-soft `_marker_present` (which returns False
        on any exception, and cannot tell absent from unreadable) for a security decision."""
        from . import runtime_fs, updater_units
        return runtime_fs.guard_state(
            self._paths, self._paths.under(updater_units.UNINSTALL_GUARD)) != "absent"

    def _task_admission_blocked(self) -> "tuple[str, str] | None":
        """STRICT reason a NEW task start is refused under admission — `(reason, tag)` or None. Blocks
        when the uninstall guard is present/unsafe (strict) OR a self-update request is pending/in
        flight/malformed. Runs UNDER the held admission lock. Absent runtime root -> no markers -> None
        (callers skip the whole guard when the root is absent, so there is zero filesystem mutation)."""
        if self.uninstall_guard_blocks():
            return ("A controller uninstall is in progress (.lhpc-uninstalling) — refusing to start "
                    "new work. Let it finish, or recover it.", "uninstalling")
        try:
            req = self.classify_request()
        except Exception as exc:                          # noqa: BLE001 — cannot prove safe -> refuse
            return (f"Could not verify controller update state ({exc}) — refusing to start new work.",
                    "unverifiable")
        if req in ("pending", "in_flight", "malformed"):
            return ("A controller self-update is pending or in progress — refusing to start new work "
                    "until it completes (`lhpc self-update --recover-request` if stuck).", req)
        return None

    def _self_update_blockers(self) -> "tuple[str, str] | None":
        """The COMPLETE strict blocker scan SHARED by self_update_trigger and self_update_apply so both
        gate on identical logic. Blocks on: an active OR unprovable job (active_jobs(include_unsafe=True)
        — unsafe jobs dir / symlinked / non-regular / oversized / disappeared / malformed marker),
        unresolved auto-install, and running/interrupted/malformed/unsafe HMAC. ANY inspection exception
        fails CLOSED. Returns (reason, tag) or None. It does NOT check the request/uninstall markers —
        those are the admission (trigger) / request-ownership (apply) concern of each caller."""
        try:
            jobs = self.active_jobs(include_unsafe=True)
        except Exception as exc:                          # noqa: BLE001 — cannot prove safe -> block
            return (f"could not inspect running jobs ({exc})", "jobs")
        if jobs:
            return ("an lhpc build/test/web job is running or its state cannot be proven safe", "jobs")
        try:
            ai = self._auto_install_gate()
        except Exception as exc:                          # noqa: BLE001 — cannot prove safe -> block
            ai = f"unverifiable ({exc})"
        if ai:
            return (f"an auto-install run is unresolved — {ai}", "auto_install")
        try:
            hst = self.hmac_apply_status()
            if hst and (hst.get("unsafe") or hst.get("phase") in ("running", "interrupted")):
                return ("an HMAC apply is running or its state is unresolved/unsafe", "hmac")
        except Exception as exc:                          # noqa: BLE001 — cannot prove safe -> block
            return (f"could not inspect HMAC state ({exc})", "hmac")
        return None

    # ---- request-state recovery (operator shell) ---------------------------------------------

    def classify_request(self) -> str:
        """`absent | pending | in_flight | malformed` — file reads only (GET-safe)."""
        from . import runtime_fs, updater_units
        inflight = self._paths.under(*updater_units.INFLIGHT_REL)
        req = self._paths.under(*updater_units.REQUEST_REL)
        try:
            if runtime_fs.stat_leaf_nofollow(self._paths, inflight) is not None:
                try:
                    rec = json.loads(runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096))
                    if isinstance(rec, dict) and isinstance(rec.get("pid"), int) and rec.get("mode"):
                        return "in_flight"
                except Exception:
                    pass
                return "malformed"
            if runtime_fs.stat_leaf_nofollow(self._paths, req) is not None:
                return "pending"
        except Exception:
            return "malformed"
        return "absent"

    def self_update_recover_request(self) -> ActionResult:
        """OPERATOR: one invocation inspects BOTH recoverable states — the update request/in-flight
        record AND the uninstall guard — never returning early after handling only one; a partial
        recovery (one cleared, the other still blocked) is reported truthfully. Request semantics
        are unchanged: pending is cleared; in-flight only when the recorded helper identity is
        proven ceased (never age-based). The uninstall guard is released ONLY when its recorded
        pid + process start time PROVE the owner ceased (a refused/interrupted uninstall leaves a
        guard whose owner is dead — this is the documented escape for a kept guard)."""
        # HALF-ISOLATION: an unexpected exception in one half (e.g. a corrupt in-flight record
        # raising out of json.loads, or an unlink OSError) must NEVER prevent the other half from
        # being attempted — each converts to a typed failed result instead of propagating.
        try:
            req_res = self._recover_update_state()
        except Exception as exc:                    # noqa: BLE001 — isolate; the guard half still runs
            req_res = ActionResult(False, f"Update-state recovery failed unexpectedly: {exc}",
                                   data={"request_recovery_error": True})
        try:
            guard_res = self._recover_uninstall_guard()
        except Exception as exc:                    # noqa: BLE001 — isolate; report truthfully
            guard_res = ActionResult(False, f"Uninstall-guard recovery failed unexpectedly: {exc}",
                                     data={"guard": "error"})
        if guard_res is None:
            return req_res                          # no guard: exact legacy behavior + wording
        ok = req_res.ok and guard_res.ok
        return ActionResult(ok, f"{req_res.summary} {guard_res.summary}",
                            data={**req_res.data, **guard_res.data})

    def _recover_uninstall_guard(self):
        """Release a STALE uninstall guard, identity-proven: accepts the controller schema
        (`start_time`), the legacy shell-fallback field (`started`), and legacy DECIMAL strings;
        REJECTS booleans, non-decimal values, and non-positive pid/start times (strict
        `_guard_owner_ints`) — malformed/unprovable keeps the guard with its path named. The whole
        read -> prove -> unlink sequence runs under the ONE per-root guard lock that also serializes
        claim/reclaim/release, so the guard proven stale is GUARANTEED to be the same guard removed —
        recovery can never delete a replacement guard a concurrent uninstall just claimed. Returns
        None when no guard exists (callers keep legacy request-only wording)."""
        from . import reslock, runtime_fs, updater_units
        from .paths import PathContainmentError
        from .service_base import _guard_owner_ints
        path = self._paths.under(updater_units.UNINSTALL_GUARD)
        try:
            with reslock.operation_lock(self._paths, "uninstall.guard", "guard-recover"):
                try:
                    raw = runtime_fs.read_text_regular(self._paths, path, max_bytes=4096)
                except FileNotFoundError:
                    return None
                except (OSError, PathContainmentError):
                    return ActionResult(False, f"An uninstall guard exists but is unreadable/unsafe "
                                        f"— NOT removing it ({path}).", data={"guard": "unsafe"})
                try:
                    rec = json.loads(raw)
                    pid, start = _guard_owner_ints(rec)
                except (ValueError, TypeError, KeyError):
                    return ActionResult(False, f"An uninstall guard exists but its owner record is "
                                        f"malformed — cannot prove the owner ceased; verify no "
                                        f"uninstall is running, then remove {path} by hand.",
                                        data={"guard": "malformed"})
                if not _proc_ceased(pid, start):
                    return ActionResult(False, "An uninstall guard is held by a LIVE process (an "
                                        "uninstall may be running) — not removing it.",
                                        data={"guard": "live"})
                try:
                    runtime_fs.unlink(self._paths, path)
                except (OSError, PathContainmentError) as exc:
                    return ActionResult(False, f"Could not remove the stale uninstall guard: {exc}",
                                        data={"guard": "unlink_failed"})
                return ActionResult(True, f"Cleared a stale uninstall guard (pid {pid} proven "
                                    "ceased).", data={"guard": "cleared"})
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"another uninstall-guard operation is in progress ({busy}) — "
                                "retry.", data={"guard": "contended"})

    def _recover_update_state(self) -> ActionResult:
        """The request/in-flight half of recovery (legacy semantics + wording, unchanged)."""
        from . import runtime_fs, updater_units
        state = self.classify_request()
        if state == "absent":
            return ActionResult(True, "No stuck update request.", data={"state": "absent"})
        inflight = self._paths.under(*updater_units.INFLIGHT_REL)
        req = self._paths.under(*updater_units.REQUEST_REL)
        if state == "pending":
            runtime_fs.unlink(self._paths, req)
            return ActionResult(True, "Cleared a pending update request (it was never claimed).",
                                data={"cleared": "pending"})
        if state == "malformed":
            return ActionResult(False, "The in-flight update record is unreadable/malformed — its "
                                "helper cannot be proven stopped. Ensure lhpc-selfupdate.service is "
                                "not active, then remove state/selfupdate.inflight by hand.",
                                data={"state": "malformed"})
        # in_flight: verify the recorded process is gone.
        rec = json.loads(runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096))
        if not _proc_ceased(rec.get("pid"), rec.get("start_time")):
            return ActionResult(False, "An update is still running (helper process alive) — wait "
                                "for it to finish before recovering.", data={"state": "running"})
        # Record the interrupted outcome DURABLY *before* removing the evidence — if the strict
        # write fails, keep the in-flight marker so recovery can be retried (never silently clear).
        from . import selfupdate as _su
        if not _su.record_last_apply_strict(self._paths, ok=False,
                summary="A previous update was interrupted and did not complete."):
            return ActionResult(False, "Could not record the interrupted outcome durably — the "
                                "in-flight record is kept; try recovery again.",
                                data={"record_failed": True})
        runtime_fs.unlink(self._paths, inflight)
        return ActionResult(True, "Cleared an interrupted update (helper had stopped); recorded it "
                            "as incomplete.", data={"cleared": "in_flight"})

    # ---- integration repair (operator shell, HAS bus) ----------------------------------------

    def self_update_repair_integration(self, *, restart: bool = True) -> ActionResult:
        """OPERATOR / migration: install/restore the COMPLETE canonical unit set (web + helper +
        path) for this runtime root, then daemon-reload, verify the active fragments, enable the
        watcher (`--now`) + web. With `restart=True` (CLI default) also restart the console; with
        `restart=False` (the web self-repair bridge) leave the running console alone so the update
        itself bounces it. Refuses while an uninstall guard or request/in-flight evidence exists,
        or when an existing unit is not provably this deployment's."""
        from . import runtime_fs, updater_units
        root = str(self._paths.runtime_root)
        _, checkout, venv = updater_units.deployment_paths(root)
        import os.path as _op
        if not _op.isdir(_op.join(checkout, ".git")):
            return ActionResult(False, "Not a self-hosted deployment (no checkout at "
                                f"{checkout}) — cannot manage web/updater units.",
                                data={"not_self_hosted": True})
        if self.uninstall_guard_blocks():
            return ActionResult(False, "An uninstall is in progress (.lhpc-uninstalling present) — "
                                "recover it first (`lhpc self-update --recover-request`).",
                                data={"uninstalling": True})
        if self.classify_request() != "absent":
            return ActionResult(False, "An update request is pending/in-flight — run "
                                "`lhpc self-update --recover-request` first.",
                                data={"request_present": True})
        ud = self._user_unit_dir()
        # The units log with StandardOutput=append:{root}/logs/... — systemd creates the FILE but not
        # the directory, and a repaired root may predate/have lost it (bootstrap normally makes it).
        # Without this the web unit fails to start on `append:` open.
        try:
            runtime_fs.mkdir(self._paths, "logs")
        except Exception:                                    # noqa: BLE001 — best effort, never fatal
            pass
        try:
            actions = updater_units.write_set(ud, root)
        except ValueError as exc:
            return ActionResult(False, str(exc), data={"write_refused": True})
        S = 20.0
        reload_res = self._system.runner.run(["systemctl", "--user", "daemon-reload"], timeout=S)
        if reload_res.returncode != 0:
            return ActionResult(False, "systemctl --user daemon-reload failed after writing the units "
                                "— not proceeding (the units are on disk but not activated). Check "
                                "`systemctl --user status`.", data={"daemon_reload_failed": True})
        # Authoritative loader check (operator shell HAS the bus): the ACTIVE fragment must be our
        # file AND carry NO drop-ins — a drop-in can override the sandbox / ExecStart /
        # InaccessiblePaths of the vetted unit, so either condition FAILS the repair before we
        # enable/restart/write the marker.
        for kind in updater_units.ALL_UNITS:
            show = self._system.runner.run(
                ["systemctl", "--user", "show", "-p", "FragmentPath", "-p", "DropInPaths", kind],
                timeout=S)
            out = (show.stdout or "")
            props = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
            want = str(ud / kind)
            if props.get("FragmentPath") != want:
                return ActionResult(False, f"After writing units, {kind} still loads a different "
                                    f"fragment ({out.strip()[:120]}). A higher-priority unit or "
                                    "mask shadows it — resolve manually.", data={"shadowed": kind})
            if props.get("DropInPaths", "").strip():
                return ActionResult(False, f"{kind} has an active drop-in override "
                                    f"({props['DropInPaths'].strip()[:120]}) — it can override the "
                                    "sandbox; remove it, then repair.", data={"dropin": kind})
        # Enable both, and START the watcher now so a request marker is caught even before the web
        # is (re)started under the new unit. Every step's return code is CHECKED and fails the repair
        # truthfully — a partial integration is never reported as success, and the root marker is
        # written ONLY after all required steps prove they succeeded.
        en = self._system.runner.run(["systemctl", "--user", "enable", "--now",
                                      updater_units.PATH_UNIT], timeout=S)
        if en.returncode != 0:
            return ActionResult(False, "Installed the units but could not enable/start the request "
                                "watcher (lhpc-selfupdate.path) — not proceeding. Check "
                                "`systemctl --user status lhpc-selfupdate.path`.",
                                data={"path_watcher_failed": True})
        # Same for the nginx-restart watcher (the web console's bind-change escape hatch). NOTE the
        # deliberate startup-recovery semantics: `--now` with a stale request present fires ONE
        # restart immediately — marker consumed, fresh nginx (rate-limited; chosen, not accidental).
        ren = self._system.runner.run(["systemctl", "--user", "enable", "--now",
                                       updater_units.RESTART_PATH_UNIT], timeout=S)
        if ren.returncode != 0:
            return ActionResult(False, "Installed the units but could not enable/start the "
                                "nginx-restart watcher (lhpc-nginx-restart.path) — not proceeding. "
                                "Check `systemctl --user status lhpc-nginx-restart.path`.",
                                data={"restart_watcher_failed": True})
        web_en = self._system.runner.run(["systemctl", "--user", "enable", updater_units.WEB_UNIT],
                                         timeout=S)
        if web_en.returncode != 0:
            return ActionResult(False, "Could not enable the web service (lhpc-web.service) — not "
                                "proceeding.", data={"web_enable_failed": True})
        # The watcher MUST be active now — in BOTH modes (a migration's still-running OLD web does not
        # pull it up via Wants=, and a CLI repair must not silently leave it down) — otherwise a queued
        # request is never consumed. Fail BEFORE writing the root marker / restarting.
        act = self._system.runner.run(["systemctl", "--user", "is-active", "--quiet",
                                       updater_units.PATH_UNIT], timeout=S)
        if act.returncode != 0:
            return ActionResult(False, "The update path watcher (lhpc-selfupdate.path) is not active "
                                "after enable --now — not proceeding. Check "
                                "`systemctl --user status lhpc-selfupdate.path`.",
                                data={"path_watcher_failed": True})
        ract = self._system.runner.run(["systemctl", "--user", "is-active", "--quiet",
                                        updater_units.RESTART_PATH_UNIT], timeout=S)
        if ract.returncode != 0:
            return ActionResult(False, "The nginx-restart watcher (lhpc-nginx-restart.path) is not "
                                "active after enable --now — not proceeding. Check "
                                "`systemctl --user status lhpc-nginx-restart.path`.",
                                data={"restart_watcher_failed": True})
        if restart:
            rst = self._system.runner.run(["systemctl", "--user", "restart", updater_units.WEB_UNIT],
                                          timeout=S)
            if rst.returncode != 0:
                return ActionResult(False, "Installed and enabled the units but the web console "
                                    "restart FAILED — the repair is NOT marked complete. Check "
                                    "`journalctl --user -u lhpc-web.service`.",
                                    data={"web_restart_failed": True})
        ov_note = self._remove_stale_overwrite_unit(ud)
        self._write_root_marker()          # ONLY after every required integration step succeeded
        details = [f"  {k}: {a}" for k, a in actions]
        if ov_note:
            details.append(f"  {ov_note}")
        details.append(self._enable_linger(S))
        return ActionResult(True, "Web + one-click updater integration installed/repaired.",
                            details=tuple(details), data={"actions": dict(actions)})

    def _enable_linger(self, timeout: float) -> str:
        """Boot autostart: a `systemctl --user` unit only starts at LOGIN unless the user lingers.
        Installed roots get this from install.sh; a repaired one did not — so repair enables it too.

        ALWAYS attempted (never gated on INVOCATION_ID): the web self-repair bridge runs from a
        managed LEGACY web unit that still has the user bus, and gating would silently deny it boot
        autostart. FAIL-SOFT by contract — a linger failure NEVER fails the repair/update; where the
        bus is unavailable (the canonical web unit blocks %t/bus) we return the shell command."""
        import getpass
        try:
            user = getpass.getuser()
        except Exception:                                    # noqa: BLE001 — no pwent / no env
            return "  linger: could not resolve the user — run: loginctl enable-linger $USER"
        r = self._system.runner.run(["loginctl", "enable-linger", user], timeout=timeout)
        if getattr(r, "not_found", False) or r.returncode != 0:
            return (f"  linger: NOT enabled (no user bus here) — for autostart at boot run: "
                    f"loginctl enable-linger {user}")
        return f"  linger: enabled for {user} — the console now autostarts at boot"

    def _remove_stale_overwrite_unit(self, ud) -> str | None:
        """Remove the obsolete `lhpc-selfupdate-overwrite.service` ONLY when it is PROVABLY this
        deployment's old overwrite helper variant — BOTH a same-root `LHPC_RUNTIME_ROOT` (literal
        or `%h`) AND the old variant's exact `ExecStart` shape
        (`<root>/venv/lhpc/bin/lhpc self-update --run-service --overwrite`). Anything else (edited /
        foreign / unreadable / symlinked) is LEFT untouched. Returns a manual-cleanup note when a
        same-named unit is present but not proven ours, else None."""
        from . import updater_units
        name = "lhpc-selfupdate-overwrite.service"
        p = ud / name
        try:
            text = updater_units._read_unit(p)               # no-follow, bounded
        except FileNotFoundError:
            # ABSENT is the normal case — nothing to clean up, and NOT evidence of anything. A bare
            # `except Exception` here reported every clean box as "present but unreadable/symlinked",
            # because `_read_unit` raises (never returns None) when the unit does not exist.
            return None
        except OSError:
            # Genuinely present but unusable: symlinked (O_NOFOLLOW -> ELOOP), non-regular,
            # oversized, or unreadable. Never touch it; tell the operator.
            return f"{name} is present but unreadable/symlinked — remove it by hand if unused."
        home = os.path.expanduser("~")
        root = str(self._paths.runtime_root)
        lines = text.splitlines()
        envs = [ln[len("Environment=LHPC_RUNTIME_ROOT="):] for ln in lines
                if ln.startswith("Environment=LHPC_RUNTIME_ROOT=")]
        execs = [ln[len("ExecStart="):] for ln in lines if ln.startswith("ExecStart=")]
        want_exec = f"{root}/venv/lhpc/bin/lhpc self-update --run-service --overwrite"
        root_ours = any(updater_units._expand_h(v, home) == root for v in envs)
        exec_ours = any(updater_units._expand_h(v, home) == want_exec for v in execs)
        if not (root_ours and exec_ours):
            return (f"{name} is present but not the recognised old overwrite helper — left in "
                    "place; remove it by hand if unused.")
        self._system.runner.run(["systemctl", "--user", "disable", "--now", name], timeout=20.0)
        try:
            os.remove(str(p))                                # regular file proven by _read_unit
        except OSError:
            pass
        return None

    def self_update_repair_and_trigger(self, *, overwrite: bool = False) -> ActionResult:
        """WEB one-click that also MIGRATES a legacy same-root deployment (old/`%h` units, no
        `.path`) to the canonical set, then updates — in one click. Compatibility bridge ONLY: it
        needs the user bus, which succeeds only while the console runs the not-yet-hardened unit;
        once the canonical bus-blocked web unit is active the bus preflight fails and this returns
        shell guidance WITHOUT writing anything. Auto-repair is allowed ONLY for
        `missing`/`modified_ours` units — never `ambiguous`/`foreign`/`overridden`/`unsafe`/
        `unreadable`/recovery states."""
        from . import updater_units
        # Managed-service gate FIRST — the web->systemctl bridge must run only for the legacy
        # managed unit, never a foreground `lhpc web`, so no units/marker are written by one.
        if not os.environ.get("INVOCATION_ID"):
            return ActionResult(
                False, "One-click update needs the managed web service (systemd). This console is "
                "running in the foreground.",
                details=["  lhpc self-update --repair-integration   "
                         "# installs + enables + starts the service, and enables boot autostart",
                         "  lhpc self-update --apply                # then update"],
                next_commands=["lhpc self-update --repair-integration", "lhpc self-update --apply"],
                data={"not_managed": True})
        integ = self.updater_integration()
        status = integ["status"]
        if status == "ok":
            return self.self_update_trigger(overwrite=overwrite)         # nothing to migrate
        # Refuse recovery / pending-request / uninstall BEFORE any preflight or write.
        if status == "recovery_required":
            return ActionResult(False, "A previous update needs recovery first — run "
                                "`lhpc self-update --recover-request`.", data={"recovery_required": True})
        if self.uninstall_guard_blocks():
            return ActionResult(False, "An uninstall is in progress — recover it first.",
                                data={"uninstalling": True})
        if self.classify_request() != "absent":
            return ActionResult(False, "An update request is already pending — the console is about "
                                "to update.", data={"request_present": True})
        # Fixable ONLY when every non-OK unit is missing/modified_ours (an ambiguous/foreign/
        # overridden/unsafe/unreadable unit is NOT auto-repairable).
        fixable_set = (updater_units.OK, updater_units.MISSING, updater_units.MODIFIED_OURS)
        per = integ.get("per_unit", {})
        bad = {k: v for k, v in per.items() if v not in fixable_set}
        if bad:
            detail = ", ".join(f"{k}: {v}" for k, v in bad.items())
            return ActionResult(False, "The web/updater units are not safely this deployment's "
                                f"({detail}) — resolve them manually, then update.",
                                data={"integration": status, "unfixable": bad})
        # Bus preflight — cheap, read-only. A hardened (bus-blocked) console fails here BEFORE any
        # write and gets shell guidance.
        probe = self._system.runner.run(["systemctl", "--user", "show", "-p", "Version"], timeout=20.0)
        if probe.returncode != 0:
            return ActionResult(False, "This console can't install systemd units itself (the user "
                                "bus is unavailable). From a shell on this machine run "
                                "`lhpc self-update --repair-integration`, then click Update.",
                                data={"bus_unavailable": True})
        rep = self.self_update_repair_integration(restart=False)
        if not rep.ok:
            return rep
        if self.updater_integration()["status"] != "ok":                # repair must have converged
            return ActionResult(False, "Unit repair did not fully converge — run "
                                "`lhpc self-update --repair-integration` from a shell.",
                                data={"repair_incomplete": True})
        return self.self_update_trigger(overwrite=overwrite)

    def _write_root_marker(self) -> None:
        from . import runtime_fs, updater_units
        import time as _time
        payload = json.dumps({"schema_version": 1, "root": str(self._paths.runtime_root),
                              "created": int(_time.time())})
        runtime_fs.write_marker(self._paths, self._paths.under(updater_units.ROOT_MARKER), payload)

    def _write_envelope(self, completed, prepared) -> None:
        """Persist the two-slot envelope (or clear it when both slots are empty). Best-effort at
        finalization: a failed write is self-healed on the next invocation's prepared-reconciliation,
        so a still-pending `completed` is never silently lost."""
        from . import selfupdate
        if not completed and not prepared:
            selfupdate.clear_migration_journal(self._paths)
        else:
            selfupdate.write_migration_journal(self._paths, {"completed": completed,
                                                             "prepared": prepared})

    @staticmethod
    def _promote(completed, prepared) -> dict:
        """Promote a prepared transition whose git DID complete (head reached its to_head) into the
        completed slot, carrying its durable-anchor `txid` and its (immutable, anchor-matched) pending
        payload. The classifier's invariant guarantees no prior completed pending coexists."""
        return {"from_head": prepared["from_head"], "to_head": prepared["to_head"],
                "branch": prepared["branch"], "pending": prepared["pending"],
                "txid": prepared.get("txid")}

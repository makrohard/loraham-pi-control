"""Binary channel operations — install, retire (switch back), and update dispatch (B7/B8).

The install transaction lives in `binary_install.py` (pure, testable); this mixin is the
service-side driver: locks, gates, manifest facts, receipt bookkeeping and typed results.

Switching back to a source channel SETS THE ARTIFACT ASIDE first — exactly the receipt's files
and owned directories are moved into the switch transaction, then the receipt — so the ordinary
clone path runs against a clean destination. No special ownership case is taught to
`adopt_source`/`verify_identity`, which is what keeps the source path byte-identical to today.
The retirement is committed only once the whole switch has succeeded; until then `binary_recover`
can put the previous install back from local disk alone.
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import traceback

from . import binary_install as bi
from . import binary_receipt as brx
from . import instances, reslock, runtime_fs, web_client
from .best_effort import best_effort
from .paths import PathContainmentError
from .service_base import (
    TXN_BLOCKED_REMEDY,
    ActionResult,
    AdmissionRefused,
    SourceTxnBlocked,
    admission_refusal,
    busy_remedy,
)
from .snapshot_memo import invalidates_snapshot

# Each step of the managed meshtastic CLI venv on a binary install (L5 of the slow-target
# budget, tests/install/test_slow_target_budget.py): a pip install on the Zero's default channel.
CLI_VENV_TIMEOUT_S = 900.0




class BinaryOpsMixin:

    # ---- helpers ----------------------------------------------------------------------------

    def _binary_pins(self, stack_id: str) -> dict:
        """{component id: manifest pin_commit} for every covered component — THE comparison
        set for an artifact's index components map."""
        st = self.stack(stack_id)
        spec = getattr(st, "binary", None)
        if st is None or spec is None:
            return {}
        by_id = {c.id: c for c in st.components}
        return {cid: by_id[cid].source.pin_commit
                for cid in spec.covers
                if by_id.get(cid) is not None and by_id[cid].source is not None}

    def _binary_source_paths(self, stack_id: str) -> list:
        """Runtime-relative source paths of the covered components (the registry baseline
        keys — a later source adoption of any of them supersedes the receipt)."""
        st = self.stack(stack_id)
        spec = getattr(st, "binary", None)
        if st is None or spec is None:
            return []
        by_id = {c.id: c for c in st.components}
        return sorted({by_id[cid].source.path for cid in spec.covers
                       if by_id.get(cid) is not None and by_id[cid].source is not None})

    def _dpkg_installed(self, pkg: str) -> bool:
        res = self._system.runner.run(["dpkg-query", "-W", "-f=${Status}", pkg], timeout=10.0)
        return bool(getattr(res, "returncode", 1) == 0
                    and "install ok installed" in (res.stdout or ""))

    # ---- install -----------------------------------------------------------------------------

    @invalidates_snapshot
    def binary_install(self, stack_id: str, apply: bool = False, **kw) -> ActionResult:
        """`_binary_install`; meshtastic's web client then follows the daemon in the same run,
        outside the install's locks (web_client.after_daemon)."""
        res = self._binary_install(stack_id, apply, **kw)
        return web_client.after_daemon(self, res) if apply and stack_id == web_client.DAEMON else res

    @invalidates_snapshot
    def _binary_install(self, stack_id: str, apply: bool = False, *,
                        locked: bool = False) -> ActionResult:
        """Install `stack_id` from the newest published artifact for this box (`bi.newest_entry`, plan R2), when
        it is newer than the installed one; none that covers this lhpc is ONE typed refusal (`none_compatible`). Every
        refusal offers the source channel explicitly. The APPLY path holds a source install's
        boundary (task admission + the covered source paths), so a concurrent install/update/
        uninstall of the stack cannot interleave with the publish; `locked=True` means an OUTER
        boundary (the auto-install run) holds them."""
        spec = self.binary_spec(stack_id)
        ok, why = self.binary_available(stack_id)
        if not ok:
            return ActionResult(False, f"Cannot install '{stack_id}' from binary: {why}",
                                next_commands=[f"lhpc install {stack_id} --source pinned --yes"])
        src_cmd = f"lhpc install {stack_id} --source pinned --yes"
        try:
            bi.require_zstd()
            entries = bi.index_entries(bi.fetch_index(spec.index_url), stack_id)
            entry = bi.newest_entry(entries, self.binary_target(), spec.covers)
        except bi.BinaryNoneCompatible as exc:
            return self._binary_none_compatible(stack_id, exc, src_cmd)
        except bi.BinaryInstallError as exc:
            return ActionResult(False, f"Binary install of '{stack_id}' refused: {exc.message}",
                                details=["  Switching to the source channel means a full local "
                                         "build (this can take hours on a Pi).",
                                         f"  Staying on the binary keeps {stack_id} exactly as it is."],
                                next_commands=[src_cmd],
                                data={"binary_failed": True, "offer_source": True})
        size_mb = entry.size / (1024 * 1024)
        if not apply:
            # PLAN text only, in the apply's order (D10: an installed binary the entry would not replace stays, then
            # the packages, then the same artifact). The apply decides nothing and moves nothing before its locks:
            # it takes them, recovers, then decides (below).
            _settled = bi.read_journal(self._paths)[1] == "absent"
            _how, _rec, _stuck = self._binary_current(stack_id, entry, entries) if _settled else ("", None, "")
            if _stuck:
                return self._binary_changed(stack_id, _stuck, src_cmd)
            if _how in ("kept", "unordered"):
                return self._binary_already_installed(stack_id, entry, _rec, _how, [])
            if (_deps := self._binary_deps_refusal(stack_id, entry)) is not None:
                return _deps
            if _how == "same":   # the same intact artifact: what its checkout repair would move (or refuse), as the apply
                _moves, _refuse = bi.inspect_clones(self, stack_id, spec, _rec.components, src_cmd)
                return _refuse if _refuse is not None else self._binary_already_installed(
                    stack_id, entry, _rec, _how, [f"  moves the pinned source for {c.id} to {w[:9]} (run scripts)"
                                                  for c, w, _p in _moves])
            details = [f"  download {entry.filename} ({size_mb:.1f} MB, sha256-verified)",
                       f"  from {entry.url}",
                       "  components: " + ", ".join(f"{k}@{v[:9]}"
                                                    for k, v in sorted(entry.components.items())),
                       "  replaces: " + ", ".join(spec.publish_roots)]
            if spec.clone_required:
                details.append("  keeps the pinned source clone for: "
                               + ", ".join(spec.clone_required))
            return ActionResult(True, f"Binary install plan for '{stack_id}'.", details=details,
                                next_commands=[f"lhpc install {stack_id} --yes"],
                                data={"changes": 1, "channel": "binary"})

        # LOCK ORDER (mirrors the source ops): admission, then the covered source paths.
        import contextlib as _ctx
        _stack = _ctx.ExitStack()
        try:
            if not locked:
                self._admit(_stack, "install", stack_id)
                # DEC-W409u: config held for the whole run (the lock order of record, as an auto-install): no save
                # lands between the password snapshot, its clear, the commit and a rollback
                _stack.enter_context(self._config_stable(exclusive=True))
                _srcs = self._binary_source_paths(stack_id)
                if _srcs:
                    _stack.enter_context(
                        self._source_operation_guard(_srcs, op="binary-install"))
        except AdmissionRefused as _adm:
            _stack.close()
            return admission_refusal(_adm)
        except reslock.ResourceBusy as _busy:
            _stack.close()
            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_busy}",
                                details=[busy_remedy(_busy)],
                                next_commands=[f"lhpc install {stack_id} --source binary --yes"])
        except SourceTxnBlocked as _blocked:
            _stack.close()
            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_blocked}",
                                details=[TXN_BLOCKED_REMEDY],
                                next_commands=[f"lhpc install {stack_id} --source binary --yes"])
        with _stack:
            # ---- ONE lock-held boundary. Everything that reads or changes state lives
            # here, in this order: recover an interrupted transaction, read the current
            # receipt + registry baseline, verify/adopt the required clone, prove the stack is
            # stopped, THEN open the journal and mutate.
            _rec_ok, _rec_why = self.binary_recover(sources_locked=True)
            if not _rec_ok:
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_rec_why}",
                                    details=["  nothing to run here — an earlier binary install left "
                                             "state under state/binary/ that lhpc could not undo; "
                                             "the box's operator fixes what is named above, then "
                                             "the next binary install recovers it"])
            _pstate, _prec, _pwhy = self.binary_receipt_state(stack_id)
            if _pstate == "unsafe" and _prec is None:
                # UNREADABLE ownership evidence: we cannot know which files the previous
                # install owns, so we must not displace or overwrite anything. (A receipt that
                # merely DRIFTED — a file gone or changed — is readable, and re-installing is
                # exactly the documented repair, so it flows on below.)
                return ActionResult(
                    False, f"Binary install of '{stack_id}' blocked: {_pwhy}",
                    next_commands=[f"lhpc clean {stack_id} --purge --yes"])
            # A SUPERSEDED receipt still names files this box owns — they are displaced into
            # the transaction below, never left behind unowned.
            prev_files = list(_prec.files) if (_prec and _pstate in ("valid", "superseded")) else []
            prev_receipt_raw = brx.read_raw(self._paths, stack_id)
            # THE decision, under the locks after recovery and before any clone is touched (D10): another
            # installed binary `entry` is not newer than stays, clones and files untouched; then the packages.
            _how, _rec, _stuck = self._binary_current(stack_id, entry, entries)
            if _stuck:
                return self._binary_changed(stack_id, _stuck, src_cmd)
            if _how in ("kept", "unordered"):
                return self._binary_already_installed(stack_id, entry, _rec, _how, [])
            if (_deps := self._binary_deps_refusal(stack_id, entry)) is not None:
                return _deps
            _moves, _refuse = bi.inspect_clones(self, stack_id, spec,
                                                _rec.components if _how == "same" else entry.components, src_cmd)
            if _refuse is not None:
                return _refuse
            # Nothing is moved here. A running stack runs its scripts from these checkouts: refused.
            if _moves and (_run := self._binary_running_refusal(stack_id)) is not None:
                return _run
            if _how == "same":   # the same artifact, intact: only its checkouts may need the repair (journaled, undoable)
                notes, _cerr = bi.repair_clones(self, stack_id, _moves, locked, prev_receipt_raw) if _moves else ([], "")
                if _cerr:
                    return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_cerr}",
                                        next_commands=[src_cmd], data={"binary_failed": True})
                return self._binary_already_installed(stack_id, entry, _rec, _how, notes)

            baseline, berr = bi.registry_baseline(self._paths, self._binary_source_paths(stack_id))
            if berr:
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {berr}",
                                    details=["  nothing to run here — the source ownership record "
                                             "named above is damaged; the box's operator inspects "
                                             "and removes it by hand, then retries"])
            txn = secrets.token_hex(8)
            try:
                runtime_fs.mkdir(self._paths, "state")     # a not-yet-bootstrapped root has none
                tmpdir = tempfile.mkdtemp(prefix="lhpc-binary-",
                                          dir=str(self._paths.under("state")))
            except (OSError, PathContainmentError, ValueError) as exc:
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: cannot create a "
                                           f"staging directory ({exc})",
                                    details=["  nothing to run here — the error named above (a "
                                             "filesystem error, a path under the runtime root "
                                             "that is a symlink or escapes it, or a path lhpc "
                                             "rejects as invalid) kept lhpc from creating a "
                                             "staging directory under state/; the box's operator "
                                             "fixes what it names, then retries"])
            # AUTHORITATIVE running recheck under the held locks (the source-update pattern):
            # a start that slipped in before the locks must refuse with ZERO mutation — a
            # binary update replaces the executable/firmware the stack is running from.
            if (_run := self._binary_running_refusal(stack_id)) is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)   # nothing was opened yet; leave nothing
                return _run

            # OPEN THE TRANSACTION FIRST — before the auth change and before any file moves.
            # The journal carries the previous receipt and the previous mesh-password value, so
            # even a hard crash during download/extract is recoverable.
            _auth_restore = None
            _hc = self._hmac_component(stack_id) if getattr(self, "hmac_applies", None) and self.hmac_applies(stack_id) else None
            _pw = self._mesh_passwords(stack_id, _hc.id) if _hc is not None else {}   # K8f S4-4c: per saved instance
            auth_journal = {"param": "password_file", "previous": {k: v for k, (_i, v) in _pw.items()}} if _pw else {}
            try:
                bi.open_txn(self._paths, stack_id, txn,
                            old_receipt=prev_receipt_raw, auth=auth_journal)
            except bi.BinaryInstallError as exc:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {exc.message}",
                                    next_commands=[src_cmd], data={"binary_failed": True})
            clone_notes: list = []
            try:
                # STAGED FIRST: download, verify and extract change nothing outside the staging directory.
                tar_path = os.path.join(tmpdir, entry.filename)
                stage = os.path.join(tmpdir, "stage")
                os.makedirs(stage, exist_ok=True)
                bi.download_artifact(entry, tar_path)
                files = bi.validate_and_extract(tar_path, stage, spec.publish_roots)
                if auth_journal:
                    # The published firmware has no mesh password: the bridge must not be launched
                    # with --password-file. Journaled above, so this is undoable either way.
                    for k, (i, _v) in _pw.items():          # the instances read above, not re-resolved
                        _r = self.save_config_bundle(stack_id, values={"password_file": ""}, inst=i,
                                                     _allow_managed_params=frozenset({"password_file"}))
                        if not _r.ok:
                            raise bi.BinaryInstallError(f"could not switch the mesh password of {k} off ({_r.summary})")
                    _auth_restore = auth_journal["previous"]
                # The supporting checkouts move WITH the binary swap, inside this transaction (journaled first).
                clone_notes, _cerr = bi.move_clones(self, stack_id, _moves, locked)
                if _cerr:
                    raise bi.BinaryInstallError(_cerr)
                # The receipt's ownership baseline is read AFTER the moves: they are the binary's, not a source adoption.
                baseline, _berr = bi.registry_baseline(self._paths, self._binary_source_paths(stack_id))
                if _berr:
                    raise bi.BinaryInstallError(_berr)
                # The transaction stays OPEN across promotion, probes and the receipt write:
                # the journal carries the displaced files, the newly created ones, the PREVIOUS
                # receipt and the previous auth value, so any failure below restores the old
                # working install instead of destroying it.
                bi.publish(self._paths, stack_id, stage, files, txn)
                # PROOF and PROBE paths must be supplied by THIS artifact — a stale file left
                # by the previous install must never satisfy them.
                _fileset = set(files)
                _missing = sorted({p for p in spec.proof_paths if p not in _fileset}
                                  | {next(iter(a)) for a in spec.probes if next(iter(a)) not in _fileset})
                if _missing:
                    raise bi.BinaryInstallError(
                        "the artifact did not provide " + ", ".join(_missing))
                probe_out = ""
                for argv in spec.probes:
                    probe_out = bi.run_probe(self._paths, list(argv)) or probe_out
                # Directories provisioning owns join the receipt, so retirement removes them.
                owned_dirs = self._binary_provision(stack_id, spec, files, txn)
                rec = bi.build_receipt(self._paths, stack_id, entry, files, spec.proof_paths,
                                       baseline, probe_out, owned_dirs=owned_dirs)
                if not brx.write_receipt(self._paths, rec):
                    raise bi.BinaryInstallError("could not write the binary receipt")
                if brx.receipt_state(self._paths, stack_id)[0] != "valid":
                    raise bi.BinaryInstallError("the written receipt does not read back valid")
                # Files the PREVIOUS artifact owned that this one no longer ships are DISPLACED
                # into the transaction (journaled + backed up), never unlinked: a later rollback
                # must be able to put the old install back completely.
                bi.displace(self._paths, txn,
                            self._stale_paths(prev_files, files, owned_dirs))
                # G1-K8F-S4-4C B2: a mesh password set meanwhile by hand (the store read is keyed by its stat)
                if _hc is not None and (_set := list(self._mesh_passwords(stack_id, _hc.id))):
                    raise bi.BinaryInstallError(f"the mesh password of {', '.join(_set)} was set during the install: install again")
                if not bi.commit(self._paths):        # THE commit point
                    raise bi.BinaryInstallError("could not commit the binary transaction")
            except Exception as exc:
                # A failure AFTER publish (probe, missing proof path, unwritable receipt) must not
                # leave published files behind: with no receipt nothing would ever remove them, and
                # a later `--source pinned` install would see a "healthy" directory and skip the
                # clone. Remove exactly what we put there.
                # An OSError here (disk full, permissions) is a failed install like any other:
                # unwind and offer the source channel — never a raw traceback. So is a corrupt
                # stream after the sha check (TarError) or an escaping path (containment).
                # Anything else is a programming error: unwind like any other failure, then keep
                # its traceback on stderr (the CLI terminal or the web unit's journal).
                # UNWIND the open transaction FIRST: displaced files return, files this run
                # created are removed, the previous receipt and auth are restored. A failed update
                # never costs the operator their working install — not even when the diagnostic
                # below fails (closed stderr, a raising __repr__/__str__).
                _rb_ok, _rb_why = self.binary_recover(sources_locked=True)
                _unexpected = {}
                if not isinstance(exc, (bi.BinaryInstallError, OSError, tarfile.TarError,
                                        PathContainmentError)):
                    _name = type(exc).__name__
                    best_effort(traceback.print_exc,
                                what=f"binary install of '{stack_id}': printing the traceback failed")
                    _unexpected = {"unexpected": _name}
                    _orig, exc = exc, bi.BinaryInstallError(f"unexpected {_name}")
                    exc.__cause__ = _orig
                elif not isinstance(exc, bi.BinaryInstallError):
                    _orig = exc
                    try:
                        exc = bi.BinaryInstallError(f"filesystem error during install ({exc})")
                    except Exception:
                        exc = bi.BinaryInstallError(
                            f"filesystem error during install ({type(_orig).__name__})")
                    exc.__cause__ = _orig
                _detail = ([f"  {_rb_why}"] if _rb_why else []) if _rb_ok else [
                    f"  the previous install could NOT be fully restored: {_rb_why}"]
                return ActionResult(False, f"Binary install of '{stack_id}' failed: {exc.message}",
                                    details=_detail, next_commands=[src_cmd],
                                    data={"binary_failed": True, "offer_source": True,
                                          "rolled_back": _rb_ok, **_unexpected})
            except BaseException:
                # Ctrl-C / SystemExit inside the transaction: unwind NOW (a half-switched install
                # must not wait for the next command), then let the original propagate unchanged.
                # A failing unwind leaves the journal open for that next command to recover.
                best_effort(lambda: self.binary_recover(sources_locked=True),
                            what=f"binary install of '{stack_id}': the unwind after the interrupt "
                                 "failed — the next command recovers it")
                raise
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            self.invalidate_snapshot()
            return ActionResult(
                True, f"Installed '{stack_id}' from the published binary ({size_mb:.1f} MB).",
                details=[*clone_notes, "  open auth (binary channel): the published firmware has no mesh " "password" if _auth_restore is not None else f"  {probe_out}" if probe_out else "  installed", "  provenance: " + ", ".join(f"{k}@{v[:9]}" for k, v in sorted(entry.components.items())), f"  artifact sha256 {entry.sha256[:12]}…"],
                next_commands=[f"lhpc status {stack_id}", f"lhpc stack start {instances.named(self, stack_id)}"],
                data={"channel": "binary", "changes": 1})

    @staticmethod
    def _binary_already_installed(stack_id: str, entry, rec, how: str, notes: list) -> ActionResult:
        older = {"kept": [f"  newest published: {entry.filename} — not newer than the installed one"],
                 "unordered": [f"  newest published: {entry.filename} — the installed one is no longer listed and "
                               "records no publication time, so which is newer could not be checked: kept"]}.get(how, [])
        return ActionResult(True, f"'{stack_id}' already has the published binary "
                                  f"{rec.filename} installed — nothing to download.",
                            details=[*notes, *older], next_commands=[f"lhpc status {stack_id}"],
                            data={"changes": 1 if notes else 0, "channel": "binary"})

    def _binary_current(self, stack_id: str, entry, entries) -> tuple[str, object, str]:
        """(how, the installed receipt, refusal): the one "nothing to install" decision, by the artifact's sha256,
        never its file name. "same": `entry` IS the installed artifact; "kept": another one `entry` is not newer
        than (D10, also when its own entry was withdrawn); "unordered": another one whose order against `entry`
        cannot be checked (`bi.is_newer` None: never replaced by what may be older); all only while the files hash as
        installed, verified whatever the entry's age. ("", None, "") installs `entry`: changed files of the same
        artifact (the reinstall is the repair) or a newer entry. Changed files and nothing newer: the refusal, never
        an older replacement. Plan and apply branch on `how`; a kept or same artifact's checkouts are checked
        against ITS receipt's commits, never another entry's."""
        state, rec, _why = self.binary_receipt_state(stack_id)
        if state == "valid" and self.binary_missing(stack_id, rec):
            return "", None, ""                  # the start refuses it (one readiness rule): never kept, the entry covers it
        same = state == "valid" and rec.artifact_sha256 == entry.sha256
        newer = False if same or state != "valid" else bi.is_newer(entry, rec, entries)
        if state != "valid" or newer:
            return "", None, ""
        if brx.verify_files(self._paths, rec)[0]:
            return ("same" if same else "kept" if newer is False else "unordered"), rec, ""
        if same:
            return "", None, ""
        return "", None, f"the installed binary {rec.filename} changed since its install and no newer one is published"

    @staticmethod
    def _binary_changed(stack_id: str, why: str, src_cmd: str) -> ActionResult:
        return ActionResult(False, f"Binary install of '{stack_id}' refused: {why}",
                            details=[f"  nothing was changed: {stack_id} stays as it is",
                                     f"  install it from source: {src_cmd} (a full local build, this can take "
                                     "hours on a Pi)"],
                            next_commands=[src_cmd], data={"binary_failed": True, "offer_source": True})

    @staticmethod
    def _binary_none_compatible(stack_id: str, exc, src_cmd: str) -> ActionResult:
        """No published binary this lhpc can install: ONE typed refusal (`none_compatible`), self-update first,
        source second."""
        return ActionResult(
            False, f"Binary install of '{stack_id}' refused: {exc.message}",
            details=["  1. Update LHPC first, then install again (about a minute): lhpc self-update --apply",
                     f"  2. Or build from source: {src_cmd} (a full local build, this can take hours on a Pi).",
                     f"  Staying on the binary keeps {stack_id} exactly as it is."],
            next_commands=["lhpc self-update --apply", src_cmd],
            data={"binary_failed": True, "offer_source": True, "binary_refusal": "none_compatible"})

    def _binary_deps_refusal(self, stack_id: str, entry) -> ActionResult | None:
        """The refusal for `entry`'s missing system packages; lhpc never installs them itself, it names the command."""
        missing = bi.missing_runtime_deps(entry, self._dpkg_installed)
        if not missing:
            return None
        return ActionResult(False, f"Binary install of '{stack_id}' needs system packages that are not installed.",
                            details=[f"  missing: {' '.join(missing)}"],
                            next_commands=[f"sudo apt install -y {' '.join(missing)}", f"lhpc install {stack_id} --yes"],
                            data={"binary_failed": True, "missing_runtime_deps": missing})

    def _binary_running_refusal(self, stack_id: str) -> ActionResult | None:
        """The refusal while a component of `stack_id` runs (checked under the held locks, before any change)."""
        if not (running := self._binary_running_components(stack_id)):
            return None
        return ActionResult(False, f"Refusing to install '{stack_id}' from binary: component(s) running.",
                            details=[f"  running: {', '.join(running)} — stop them first"],
                            next_commands=[f"lhpc stack stop {k} --yes" for k in instances.stop_keys(self, stack_id)])

    def _binary_running_components(self, stack_id: str) -> list:
        """Components of `stack_id` that are RUNNING/DEGRADED right now — the gate every binary
        mutation checks under its held locks."""
        from .model import RunState
        st = self.stack(stack_id)
        ids = {c.id for c in (st.components if st else ())}
        snap = self.build_snapshot(fresh=True)   # UNDER the locks: never a cached read
        up = (RunState.RUNNING, RunState.DEGRADED)
        return sorted(cid for ss in snap.stacks for cid, cs in ss.components.items()
                      if cid in ids and cs.run_state in up)

    @invalidates_snapshot
    def binary_recover(self, *, sources_locked: bool = False) -> tuple[bool, str]:
        """THE authoritative recovery for an open/interrupted binary transaction — files,
        receipt, auth AND the supporting checkouts it moved, in one operation. Called under the locks before any
        new binary work and by the in-process failure path, so a crash and an exception recover identically.
        A moved checkout goes back only in a binary install (`sources_locked`), under that checkout's own source
        locks (`restore_clones`); any other caller leaves the journal for the next binary install, which recovers it."""
        # A KILLED run (OOM, power cut) cannot run its own cleanup, and each staging directory
        # holds a whole artifact — tens of megabytes on an SD card. Every binary operation is
        # serialized under the same locks, so anything left here is stale by definition
        # (one orphan from an earlier crashed run).
        self._sweep_binary_staging()
        j, jstate = bi.read_journal(self._paths)
        if jstate == "absent":
            return True, ""
        prev_auth = (a.get("previous") or {}) if j is not None and isinstance(a := j.get("auth") or {}, dict) else None
        if not isinstance(prev_auth, dict):      # unreadable, or G1-K8F-S4-4C B1: an earlier version's (W269, refused)
            return False, ("an unreadable binary-install journal is present (an earlier version's: CHANGELOG 0.14.0 "
                           "upgrade steps) — resolve state/binary/install.journal.json by hand")
        stack_id = j.get("stack", "")
        if j.get("state") == "committed":
            # The commit is durable: the NEW install is the truth. Only bookkeeping remains.
            return bi.drop_txn(self._paths, j), ""
        if (clones := j.get("clones") or {}) and not sources_locked:
            return False, (f"a binary install of '{stack_id}' was interrupted after moving its supporting checkout: "
                           f"lhpc install {stack_id} --source binary --yes moves it back")
        _ok, _why, _now = bi.restore_clones(self, stack_id, clones) if clones else (True, "", {})
        if not _ok:
            return False, _why                   # journal RETAINED: the next binary install retries
        ok, why = bi.rollback_files(self._paths, j)
        if not ok:
            return False, why                    # journal RETAINED as evidence
        old_receipt = j.get("old_receipt")
        if old_receipt:
            if not brx.write_raw(self._paths, stack_id, old_receipt):
                return False, "the previous binary receipt could not be restored"
            if clones and (_why := bi.rebase_receipt(self, stack_id, [c["path"] for c in clones.values()], _now)):
                return False, _why               # journal RETAINED: read after the checkouts moved back
        elif not brx.remove_receipt(self._paths, stack_id):
            # There was no receipt before this run, so the failed install's one must go. If it
            # cannot, the journal and backups MUST stay: dropping them would discard the only
            # evidence a later attempt could converge from.
            return False, "the failed install's binary receipt could not be removed"
        bad, kept = [], []                       # G1-K8F-S4-4C B2/B3: every key tried, a radio gone refused by name
        _hc = self._hmac_component(stack_id) if prev_auth else None
        for k, v in prev_auth.items():
            if (i := instances.resolve(self, k)[1]) is None and "@" in k:
                bad.append(f"{k} names no radio now")
            elif _hc is not None and self._resolved_param_value(stack_id, "run", _hc.id, "password_file", "", i):
                kept.append(k)                   # G1-K8F-S4-4C-RE B2: saved during the install, never overwritten
            elif not (r := self.save_config_bundle(stack_id, values={"password_file": v}, inst=i,
                                                   _allow_managed_params=frozenset({"password_file"}))).ok:
                bad.append(f"{k}: {r.summary}")
        if bad:                                  # journal RETAINED
            return False, f"the mesh password setting could not be restored ({'; '.join(bad)})"
        if not bi.drop_txn(self._paths, j):
            return False, "the binary-install journal could not be cleared"
        self.invalidate_snapshot()
        notes = [f"the {c['path']} checkout this install created was kept: nothing older to return to"
                 for c in clones.values() if c["commit"] == ""]    # restore_clones accepted only {commit, path}
        if kept:
            notes.append(f"the mesh password of {', '.join(kept)} was changed during the install: its new value kept")
        return True, "; ".join(notes)

    def _mesh_passwords(self, stack_id: str, comp_id: str) -> dict:
        """{instance key: (its instance, its saved password_file)} of every saved instance with one (K8f S4-4c)."""
        return {instances.key(stack_id, i): (i, v) for i in instances.saved_insts(self, stack_id)
                if (v := self._resolved_param_value(stack_id, "run", comp_id, "password_file", "", i))}

    @staticmethod
    def _stale_paths(prev_files, new_files, owned_dirs) -> list:
        """Paths the PREVIOUS receipt owned that the NEW install does not — by file OR through
        an owned directory.

        The directory clause matters: an older receipt listed the provisioned venv file by file
        while the new one owns it as a directory, and treating those entries as stale deleted
        the CLI the very same run had just provisioned."""
        owned = tuple(owned_dirs or ())
        return sorted(rel for rel in set(prev_files) - set(new_files)
                      if not any(rel == d or rel.startswith(d + "/") for d in owned))

    def _rel_files_under(self, rel_dir: str) -> list:
        """Every non-directory leaf under a runtime-root-relative directory, as relative paths.

        Symlinks COUNT: a virtualenv is half symlinks (`bin/python3`), and owning only the
        regular files left them behind on removal — enough for `python3 -m venv` to treat the
        environment as existing, skip ensurepip, and fail the next step. Raises `OSError` when
        the tree cannot be fully listed: an unread directory is not an empty one."""
        base = self._paths.under(*rel_dir.split("/"))
        state, why, st = runtime_fs.probe_stat(base, follow=True)   # what os.walk(base) lists
        if state == "unknown":
            raise OSError(why)
        if state == "absent" or not stat.S_ISDIR(st.st_mode):
            return []
        out = []

        def _unlistable(exc):
            raise exc
        for root, _dirs, names in os.walk(base, onerror=_unlistable):
            for n in names:
                full = os.path.join(root, n)
                if not os.path.isdir(full):          # regular file OR symlink
                    out.append(rel_dir + "/" + os.path.relpath(full, base).replace(os.sep, "/"))
        return sorted(out)

    def _prune_empty_dirs(self, rels) -> None:
        """Remove directories that the artifact's own files left EMPTY, DEEPEST FIRST.

        `rmdir` refuses a non-empty directory, so a shared one (the daemon binary sits inside a
        git checkout) is never touched — and we only consider ancestors of the receipt's own file
        paths, never whole publish roots. DEPTH ORDER is the contract: a parent must be tried only
        after every child, or an emptied tree keeps its upper levels (four empty
        directories survived a meshtastic retire because a first, failed attempt on a
        not-yet-empty parent was never retried). Leaving an emptied publish directory behind
        would read as "destination already exists" to a following source adoption. The runtime
        root's own top-level skeleton (build/, src/, …) is never a candidate."""
        dirs = set()
        for rel in rels:
            parts = rel.split("/")[:-1]
            while len(parts) > 1:
                dirs.add("/".join(parts))
                parts = parts[:-1]
        for d in sorted(dirs, key=lambda x: x.count("/"), reverse=True):
            try:
                os.rmdir(self._paths.under(*d.split("/")))
            except (OSError, PathContainmentError, ValueError):
                pass                   # non-empty, shared, or already gone — leave it

    def _sweep_binary_staging(self) -> None:
        """Remove staging directories left by a KILLED install (never by a normal failure — that
        path removes its own). Called under the operation locks only."""
        try:
            base = self._paths.under("state")
        except (PathContainmentError, ValueError):
            return
        try:
            names = [n for n in os.listdir(base) if n.startswith("lhpc-binary-")]
        except OSError:
            return
        for n in names:
            d = os.path.join(base, n)
            if os.path.isdir(d) and not os.path.islink(d):
                shutil.rmtree(d, ignore_errors=True)

    def _binary_provision(self, stack_id: str, spec, files, txn: str) -> list:
        """Per-stack post-extract provisioning that CANNOT ship in a tarball. Returns the
        runtime-relative DIRECTORIES it owns (they join the receipt, which is what retirement
        removes). Deliberately ONE explicit branch, not a generalized post-install language.

        Runs INSIDE the open transaction: the previous venv is moved aside (journaled) before
        the new one is built, so a failure below restores the operator's WORKING venv instead
        of leaving a half-built one."""
        if stack_id != "meshtastic":
            return []
        # The managed meshtastic CLI venv embeds ABSOLUTE shebang paths, so it can never be
        # packaged — yet `missing_requirements` blocks START without it (the mandatory
        # post-start region call runs it). Provision it at the FINAL path, from the SAME
        # manifest-pinned steps the source build uses.
        from . import commands as _cmds
        st = self.stack(stack_id)
        comp = next((c for c in (st.components if st else ()) if c.id == stack_id), None)
        steps = [s for s in (getattr(comp, "build_steps", ()) or ())
                 if any("meshtastic-cli" in str(tok) for tok in (s.get("argv") or ()))]
        if comp is None or not steps:
            return []
        venv_rel = "build/tools/meshtastic-cli"
        cli = venv_rel + "/.venv/bin/meshtastic"
        # A LEFTOVER venv (from a source build or an older artifact) must not be reused or
        # half-overwritten: move the WHOLE directory into the transaction with one rename.
        # Emptying it leaf-by-leaf is not an option — half a venv is symlinks pointing outside
        # the runtime root — and leaving those behind made `python3 -m venv` skip ensurepip, so
        # the next step failed with "pip install failed".
        # When there was NO previous venv, journal the directory we are about to create instead:
        # a hard crash mid-provisioning would otherwise leave a half-built one that no journal,
        # receipt or recovery knows about. The `except` below covers the
        # in-process failure; this covers the power cut.
        if not bi.displace_dir(self._paths, txn, venv_rel):
            bi.note_created_dir(self._paths, venv_rel)
        # The binary channel has NO source checkout for this component — the build steps must
        # run from the runtime root, not from a directory that does not exist.
        life = self._lifecycle()
        src = str(self._paths.runtime_root)
        if comp.source is not None:
            _sd = life.source_dir(comp)
            if os.path.isdir(_sd):
                src = str(_sd)
        try:
            for step in steps:
                argv = _cmds.build_step_argv(step, self._system.runner,
                                             str(self._paths.runtime_root), src)
                t0 = time.monotonic()
                res = self._system.runner.run(argv, timeout=CLI_VENV_TIMEOUT_S, cwd=src)
                # The slow-target budget's L5 quantity (docs/test-matrix.md#slow-target-baseline).
                print(f"[venv] {time.monotonic() - t0:.1f} s", file=sys.stderr, flush=True)
                if getattr(res, "returncode", 1) != 0:
                    raise bi.BinaryInstallError(
                        "the managed meshtastic CLI could not be provisioned "
                        f"({' '.join(argv[:2])} failed) — the stack cannot start without it")
            if not os.path.exists(self._paths.under(*cli.split("/"))):
                raise bi.BinaryInstallError(
                    "the managed meshtastic CLI is missing after provisioning")
            # Prove it RUNS from its final path (a venv whose interpreter path is wrong
            # imports nothing) — the same standard the artifact's binaries are held to.
            bi.run_probe(self._paths, [cli, "--version"])
        except BaseException:
            # A partially built venv is OURS and unfinished — drop it so the unwind's directory
            # restore puts the operator's working one back on a clean slot.
            shutil.rmtree(self._paths.under(*venv_rel.split("/")), ignore_errors=True)
            raise
        return [venv_rel]

    # ---- switch to a source channel --------------------------------------------------------

    def _switch_head(self, dest) -> str:
        r = self._system.runner.run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
        return (r.stdout or "").strip() if getattr(r, "returncode", 1) == 0 else ""

    def _artifact_only_dir(self, rel_dir: str, owned: set) -> bool:
        """True when every file under a source path belongs to the BINARY RECEIPT.

        On a box that only ever ran the binary channel, `src/<stack>/` exists because the
        artifact published into it — it is not a checkout and not a foreign tree. Setting the
        artifact aside empties it (the retirement prunes it), so the ordinary adoption path
        clones there. Judging it as an unprovable checkout would refuse every first switch."""
        try:
            return all(rel in owned for rel in self._rel_files_under(rel_dir))
        except OSError:
            return False                   # not fully listable: not proven the artifact's own

    def switch_source_plan(self, groups, owned_files=()) -> tuple:
        """PRE-FLIGHT for a binary -> source switch: `(paths_to_replace, refusals)`.

        The ordinary install treats an existing managed directory as "already installed". On a
        CHANNEL SWITCH that is not enough: the operator asked for a specific selector, and a
        checkout left over from the binary install (meshcom keeps its pinned clone) can sit at a
        completely different commit. Reporting a switch to `dev` while the tree stays pinned is
        a lie about provenance.

        Every judgement here reuses the existing mechanisms — `verify_identity` for ownership
        and the canonical remote, `dirty_report` for cleanliness, `_frozen_ref` for the
        selector's commit. Nothing binary-specific is invented, and the authoritative checks
        still run inside adoption; this runs BEFORE the artifact is set aside so an unprovable
        checkout refuses with the binary untouched.
        """
        from . import source_fs, source_registry
        inst = self._installer()
        owned = set(owned_files)
        replace, refusals = set(), []
        for path, comp, sel, resolved in groups:
            dest = self._paths.resolve_source(path)
            try:
                if source_fs.leaf_kind(self._paths, dest) != "dir":
                    continue           # absent (or refused later) — ordinary adoption decides
            except PathContainmentError as exc:
                refusals.append(f"{path}: unsafe source parent ({exc})")
                continue
            if self._artifact_only_dir(path, owned):
                continue               # the artifact's own leftover — the retirement clears it
            rec, why = source_registry.verify_identity(
                self._paths, self._system, self.config(), comp, dest, components=(comp.id,))
            if rec is None:
                refusals.append(f"{path}: {why}")
                continue
            if (dirty := inst.dirty_report(dest, path)).blocks_update():
                refusals.append(f"{path}: upstream source modified locally — "
                                + ", ".join(dirty.tracked[:3]))
                continue
            # The selector's commit, resolved by the EXISTING mechanisms: the plan's frozen
            # commit, else the manifest pin for `pinned`, else `_frozen_ref`
            # (bounded ls-remote) for dev/stable/artifact.
            want = resolved[0] if resolved and resolved[0] else ""
            if not want and sel == "pinned" and comp.source is not None \
                    and not comp.source.artifact:
                want = comp.source.pin_commit or ""
            if not want:
                (want, _label), _why = self._frozen_ref(comp, sel)
            if not want:
                # The selector could not be resolved (offline / no remote): hand the group to
                # adoption, which owns the documented fallback policy for exactly this case.
                replace.add(path)
                continue
            if self._switch_head(dest) != want:
                replace.add(path)
        return replace, refusals

    # ---- retire (switch back to source) --------------------------------------------------

    @invalidates_snapshot
    def binary_retire(self, stack_id: str, *, force: bool = False,
                      locked: bool = False, txn: str = "") -> ActionResult:
        """Remove a binary install's files + receipt so a source install can proceed on a
        clean destination. STRICT: every recorded file must still hash as recorded (unless
        `force`), so we never delete something the operator replaced by hand.

        `txn` names an OPEN transaction whose journal already carries the previous receipt (the
        channel switch). The artifact is then MOVED ASIDE into that transaction rather than
        deleted, so a failed source adoption can restore it from disk — with no network, no
        release lookup and no pin re-check."""
        if txn:
            # The CALLER owns the open transaction (the channel switch): its journal already
            # carries the receipt, so the openness is ours and the raw receipt is the truth.
            _j, _js = bi.read_journal(self._paths)
            if _js != "valid" or _j is None:          # the box's state, not a caller defect
                return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
                                           f"recovery-required — the binary install journal is "
                                           f"{_js}, so the open transaction cannot be proven",
                                    details=["  nothing to run here — the journal must be "
                                             "readable first: inspect state/binary/install.journal"
                                             ".json (`lhpc doctor` lists it), then run the switch again"])
            if _j.get("stack") != stack_id or _j.get("txn") != txn:
                return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
                                           "the open transaction is not this one",
                                    details=["  nothing to run here — this is an lhpc defect (a "
                                             "caller passed another transaction), not a problem "
                                             "on the box; report it with this message"])
            state, rec, why = brx.receipt_state(self._paths, stack_id)
            return self._retire_body(stack_id, state, rec, why, force=force, locked=locked,
                                     txn=txn)
        # An OPEN transaction makes every receipt non-authoritative: recover FIRST, so what we
        # retire is the settled install and not a half-published one.
        if bi.read_journal(self._paths)[1] != "absent":
            _rok, _rwhy = self.binary_recover()
            if not _rok:
                if not force:
                    return ActionResult(
                        False, f"Cannot retire the binary install of '{stack_id}': {_rwhy}",
                        next_commands=[f"lhpc clean {stack_id} --purge --yes"])
                # `force` is the explicit escape hatch (clean/uninstall): drop the unusable
                # journal and remove whatever the receipt still claims.
                bi.clear_journal(self._paths)
        state, rec, why = self.binary_receipt_state(stack_id)
        return self._retire_body(stack_id, state, rec, why, force=force, locked=locked, txn="")

    def _receipt_leaf_present(self, rel: str) -> bool:
        """Anything at a receipt path, readable or not, counts as present; so does a path that
        cannot even be resolved inside the runtime root (never "gone" on uncertainty)."""
        try:
            return runtime_fs.probe_exists(self._paths.under(*rel.split("/")))[0] != "absent"
        except (ValueError, PathContainmentError):
            return True

    def _retire_body(self, stack_id: str, state: str, rec, why: str, *, force: bool,
                     locked: bool, txn: str) -> ActionResult:
        """The retirement itself, once the receipt state is established (see `binary_retire`)."""
        if state == "absent":
            return ActionResult(True, f"'{stack_id}' has no binary install to retire.")
        if state == "unsafe" and rec is None and not force:
            return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': {why}",
                                next_commands=[f"lhpc clean {stack_id} --purge --yes"])
        if rec is None:
            # "Ownership unknown" is never a successful cleanup: the receipt is KEPT as the only
            # remaining evidence, and the caller reports INCOMPLETE.
            return ActionResult(
                False, f"Retirement of '{stack_id}' is INCOMPLETE — its binary receipt cannot "
                       "be read, so the installed files cannot be identified.",
                details=[f"  {why}",
                         "  the receipt is kept as evidence — inspect state/binary and this "
                         "stack's publish roots, then remove them by hand"],
                next_commands=["lhpc doctor"])
        # A SUPERSEDED receipt is a normal retire target: the source channel took over the
        # checkout, but the artifact's files and the receipt are still ours to remove. The
        # hash check below is what protects them — a file the source install replaced reports
        # as changed and stops the retirement, which is the correct outcome.
        if not force:
            _ok, bad = brx.verify_files(self._paths, rec)
            # A file that is simply GONE is nothing to protect — removing the rest and the
            # receipt is the honest outcome. Only a MODIFIED file stops us: that is operator
            # content we must not delete. "Gone" means absent: an unhashable leaf (unreadable,
            # over the hash bound, a symlink) also has no hash, and it is still there.
            bad = [b for b in bad if self._receipt_leaf_present(b["path"])]
            if bad:
                changed = ", ".join(b["path"] for b in bad[:3])
                return ActionResult(
                    False,
                    f"Refusing to retire the binary install of '{stack_id}': installed files "
                    f"changed since installation ({changed}).",
                    details=["  Remove them by hand, or re-run with the force option."],
                    next_commands=[f"lhpc uninstall {stack_id} --yes"])
        if not locked and (_running := self._binary_running_components(stack_id)):
            return ActionResult(
                False, f"Refusing to retire the binary install of '{stack_id}': component(s) running.",
                details=[f"  running: {', '.join(_running)} — stop them first"],
                next_commands=[f"lhpc stack stop {k} --yes" for k in instances.stop_keys(self, stack_id)])
        if txn:
            # SWITCH path: move the artifact into the caller's open transaction instead of
            # deleting it. The journal already carries the receipt, so `binary_recover()` can
            # put the whole install back byte-for-byte if the source adoption fails.
            try:
                bi.displace(self._paths, txn, sorted(rec.files))
                for rel_dir in rec.owned_dirs:
                    bi.displace_dir(self._paths, txn, rel_dir)
            except (bi.BinaryInstallError, OSError, PathContainmentError, ValueError) as exc:
                return ActionResult(
                    False, f"Cannot retire the binary install of '{stack_id}': its files could "
                           f"not be moved aside ({exc})",
                    details=["  nothing to run here — the error named above (a binary-install "
                             "error, a filesystem error, a path under the runtime root that is a "
                             "symlink or escapes it, or a path lhpc rejects as invalid) kept lhpc "
                             "from moving the files aside; the box's operator fixes what it names, then retries"])
            if not brx.remove_receipt(self._paths, stack_id):
                return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
                                           "its receipt could not be removed",
                                    details=["  nothing to run here — the receipt under "
                                             "state/binary/ could not be deleted (a filesystem "
                                             "error, or a path there that is a symlink or "
                                             "escapes the runtime root); the box's operator fixes it, then retries",
                                             "  if it is a path lhpc rejects as invalid: nothing "
                                             "to run here — the receipt path is invalid: "
                                             f"state/binary/{stack_id}.json; report it"])
            # Prune here too: an emptied publish directory left behind reads as "destination
            # already exists" and the source adoption would SKIP it — a silent no-op install.
            self._prune_empty_dirs(rec.files)
            self.invalidate_snapshot()
            return ActionResult(True, f"Retired the binary install of '{stack_id}' "
                                      f"({len(rec.files)} file(s) set aside).")
        removed, failed = 0, []
        for rel in sorted(rec.files, key=len, reverse=True):
            p = self._paths.under(*rel.split("/"))
            try:
                if os.path.isfile(p) or os.path.islink(p):
                    os.unlink(p)
                    removed += 1
            except OSError as exc:
                failed.append(f"{rel} ({exc})")
        # PROVE removal before dropping the receipt: a swallowed unlink failure would leave
        # binary files behind with no ownership record at all.
        still_there = [rel for rel in rec.files if self._receipt_leaf_present(rel)]
        if still_there or failed:
            return ActionResult(
                False,
                f"Retirement of '{stack_id}' is INCOMPLETE — the receipt was kept so the remaining files stay owned.",
                details=[f"  still present: {r}" for r in still_there[:5]]
                        + [f"  failed: {f}" for f in failed[:5]],
                next_commands=[f"lhpc clean {stack_id} --purge --yes"])
        self._prune_empty_dirs(rec.files)
        # Directories this install OWNS (a provisioned venv) go whole — they were created by
        # lhpc, contain symlinks the per-file path guard cannot touch, and would otherwise be
        # left behind as a broken environment.
        for rel_dir in getattr(rec, "owned_dirs", ()):
            try:
                d = self._paths.under(*rel_dir.split("/"))
                # The kind comes from the probes' own stat results: an error is never "not a
                # dir" (skip, receipt dropped) — not proven gone, the receipt keeps owning it.
                state, why, lst = runtime_fs.probe_stat(d)
                if state == "present" and not stat.S_ISLNK(lst.st_mode):
                    state, why, st = runtime_fs.probe_stat(d, follow=True)
                    if state == "present" and stat.S_ISDIR(st.st_mode):
                        shutil.rmtree(d)
                if state == "unknown":
                    raise OSError(why)
            except (OSError, PathContainmentError, ValueError) as exc:
                return ActionResult(
                    False, f"Retirement of '{stack_id}' is INCOMPLETE — the receipt was kept "
                           f"so {rel_dir} stays owned.",
                    details=[f"  could not remove {rel_dir} ({exc})"],
                    next_commands=[f"lhpc clean {stack_id} --purge --yes"])
        if not brx.remove_receipt(self._paths, stack_id):
            return ActionResult(False, f"Removed the binary files of '{stack_id}' but could not "
                                       "remove its receipt — resolve state/binary by hand.",
                                details=["  nothing to run here — the receipt under "
                                         "state/binary/ could not be deleted (a filesystem "
                                         "error, or a path there that is a symlink or escapes "
                                         "the runtime root); the box's operator fixes it and "
                                         "removes the receipt by hand",
                                         "  if it is a path lhpc rejects as invalid: nothing to "
                                         "run here — the receipt path is invalid: "
                                         f"state/binary/{stack_id}.json; report it"])
        self.invalidate_snapshot()
        return ActionResult(True, f"Retired the binary install of '{stack_id}' "
                                  f"({removed} file(s) removed).")

    # ---- freshness -------------------------------------------------------------------------

    def binary_freshness(self, stack_id: str) -> dict:
        """NETWORK (the index read the install makes): the binary lane's verdict from the SAME target the install
        takes (`bi.newest_entry`, plan R2). {state: current|behind|error|n/a, offer: file name[, why]}: `behind` = a
        newer entry is published; `error` = the index could not be read, or (with `why`) the newest entry cannot be
        ordered against the installed one (`bi.is_newer` None) (could not check). No entry for this box, or a
        newest one that does not cover this lhpc, offers nothing: the installed binary stays current (D10), unless
        it lacks a covered component the start then refuses (`binary_missing`): a covering entry is offered, else
        could not check with the start's reason."""
        state, rec, _why = self.binary_receipt_state(stack_id)
        spec = self.binary_spec(stack_id)
        if state != "valid" or rec is None or spec is None:
            return {"state": "n/a", "offer": ""}
        missing = self.binary_missing(stack_id, rec)          # the start refuses this install (one readiness rule)
        try:
            entries = bi.index_entries(bi.fetch_index(spec.index_url), stack_id)
            entry = bi.newest_entry(entries, self.binary_target(), spec.covers)
        except bi.BinaryNoneCompatible:
            if missing:                                      # no update exists: never "update available"
                return {"state": "error", "offer": "", "why": (
                    f"{self.binary_predates(stack_id, missing)} — no published binary covers "
                    f"{', '.join(missing)} yet, so install it from source")}
            return {"state": "current", "offer": ""}
        except bi.BinaryInstallError:
            return {"state": "error", "offer": ""}
        if missing:                                          # the covering entry is the update, newer or not
            return {"state": "behind", "offer": entry.filename}
        newer = entry.sha256 != rec.artifact_sha256 and bi.is_newer(entry, rec, entries)
        if newer:
            return {"state": "behind", "offer": entry.filename}
        if newer is None:
            return {"state": "error", "offer": "", "why": (
                f"the installed binary is no longer listed and records no publication time, so whether "
                f"{entry.filename} is newer could not be checked")}
        return {"state": "current", "offer": ""}

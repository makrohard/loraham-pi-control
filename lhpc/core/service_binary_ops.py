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
import tempfile

from . import binary_install as bi
from . import binary_receipt as brx
from . import reslock, runtime_fs, source_registry
from .paths import PathContainmentError
from .service_base import ActionResult, AdmissionRefused, SourceTxnBlocked


class BinaryOpsMixin:

    # ---- helpers ----------------------------------------------------------------------------

    def _binary_pins(self, stack_id: str) -> dict:
        """{component id: manifest pin_commit} for every covered component — THE comparison
        set for an artifact's index-v2 components map."""
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

    def _binary_registry_baseline(self, stack_id: str) -> tuple:
        """({source_rel: txn_id}, error). An UNSAFE ownership record blocks the install: we
        must be able to record a trustworthy baseline or supersession cannot be judged later."""
        baseline = {}
        for rel in self._binary_source_paths(stack_id):
            state, rec, why = source_registry.record_state(self._paths, rel)
            if state == "unsafe":
                return {}, why
            baseline[rel] = rec.txn_id if (state == "valid" and rec) else ""
        return baseline, ""

    # ---- install -----------------------------------------------------------------------------

    def binary_install(self, stack_id: str, apply: bool = False, *,
                       locked: bool = False) -> ActionResult:
        """Install `stack_id` from its published artifact. Every refusal is typed and offers
        the source channel explicitly (never a silent fallback).

        The APPLY path holds the same boundary a source install does — task admission plus the
        covered source paths — so a concurrent install/update/uninstall of the same stack cannot
        interleave with the publish. `locked=True` means an OUTER boundary (the auto-install
        run) already holds them."""
        spec = self.binary_spec(stack_id)
        ok, why = self.binary_available(stack_id)
        if not ok:
            return ActionResult(False, f"Cannot install '{stack_id}' from binary: {why}",
                                next_commands=[f"lhpc install {stack_id} --source pinned --yes"])
        src_cmd = f"lhpc install {stack_id} --source pinned --yes"
        try:
            bi.require_zstd()
            idx = bi.fetch_index(spec.index_url)
            entry = bi.index_entry(idx, stack_id)
            bi.check_target(entry, self.binary_target())
            bi.check_pins(entry, self._binary_pins(stack_id))
        except bi.BinaryInstallError as exc:
            return ActionResult(False, f"Binary install of '{stack_id}' refused: {exc.message}",
                                next_commands=[src_cmd],
                                data={"binary_failed": True, "offer_source": True})
        missing = bi.missing_runtime_deps(entry, self._dpkg_installed)
        if missing:
            # lhpc never installs system packages itself — name the exact command.
            return ActionResult(
                False,
                f"Binary install of '{stack_id}' needs system packages that are not installed.",
                details=[f"  missing: {' '.join(missing)}"],
                next_commands=[f"sudo apt install -y {' '.join(missing)}",
                               f"lhpc install {stack_id} --yes"],
                data={"binary_failed": True, "missing_runtime_deps": missing})

        size_mb = entry.size / (1024 * 1024)
        if not apply:
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
                _srcs = self._binary_source_paths(stack_id)
                if _srcs:
                    _stack.enter_context(
                        self._source_operation_guard(_srcs, op="binary-install"))
        except AdmissionRefused as _adm:
            _stack.close()
            return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
        except reslock.ResourceBusy as _busy:
            _stack.close()
            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_busy}")
        except SourceTxnBlocked as _blocked:
            _stack.close()
            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_blocked}")
        with _stack:
            # ---- ONE lock-held boundary. Everything that reads or changes state lives
            # here, in this order (audit): recover an interrupted transaction, read the current
            # receipt + registry baseline, verify/adopt the required clone, prove the stack is
            # stopped, THEN open the journal and mutate.
            _rec_ok, _rec_why = self.binary_recover()
            if not _rec_ok:
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_rec_why}")
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
            # HYBRID stacks: some covered components still need their PINNED clone, because the
            # artifact only overlays build OUTPUT while the run scripts live in the repo (meshcom's
            # run.sh / gps-relay.py). Adopt them first — without this the stack installs "fine" and
            # then cannot start on a box that never had the checkout (live-found on the Zero, where
            # an older clone had masked it).
            clone_notes = []
            if spec.clone_required:
                st = self.stack(stack_id)
                by_id = {c.id: c for c in (st.components if st else ())}
                inst = self._installer()
                for cid in spec.clone_required:
                    comp = by_id.get(cid)
                    if comp is None or comp.source is None:
                        continue
                    dest = self._paths.resolve_source(comp.source.path)
                    if dest.is_dir():
                        # PRESENT is not PROVEN: the artifact's binaries run scripts FROM this
                        # checkout, so a dev/stale/foreign tree would be executed by a supposedly
                        # pinned install. Require the manifest pin via the ownership verifier.
                        rec, why = source_registry.verify_identity(
                            self._paths, self._system, self.config(), comp, dest,
                            components=(cid,))
                        if rec is None:
                            return ActionResult(
                                False,
                                f"Binary install of '{stack_id}' blocked: the existing "
                                f"{comp.source.path} checkout is not provably ours ({why}).",
                                next_commands=[f"lhpc install {stack_id} --source pinned --yes"],
                                data={"binary_failed": True})
                        if (rec.resolved_commit or "") != comp.source.pin_commit:
                            return ActionResult(
                                False,
                                f"Binary install of '{stack_id}' blocked: {comp.source.path} is at "
                                f"{(rec.resolved_commit or '?')[:9]}, the pin is "
                                f"{comp.source.pin_commit[:9]} — the artifact's run scripts must "
                                "come from the pinned checkout.",
                                next_commands=[f"lhpc update {cid} --source pinned --yes"],
                                data={"binary_failed": True})
                        # AT the pin is not the same as UNMODIFIED: the artifact's binaries
                        # execute run.sh / gps-relay.py FROM this tree, so a locally edited
                        # working copy would run operator code under a "pinned" install.
                        _dirty = inst.dirty_report(dest, comp.source.path)
                        if _dirty:
                            _ch = list(_dirty.tracked) + list(_dirty.untracked)
                            return ActionResult(
                                False,
                                f"Binary install of '{stack_id}' blocked: {comp.source.path} has "
                                "local changes — the artifact runs scripts from that checkout.",
                                details=[f"  changed: {c}" for c in _ch[:5]],
                                next_commands=[src_cmd], data={"binary_failed": True})
                        continue                       # proven at the pin, clean — reuse it
                    # `locked=True` when an OUTER boundary (the auto-install run) already holds
                    # this path's source lock — re-acquiring it would self-contend and fail.
                    act = inst.adopt_source(comp, source="pinned", locked=locked)
                    if act.status == "failed":
                        return ActionResult(
                            False,
                            f"Binary install of '{stack_id}' blocked: {cid} needs its pinned source "
                            f"checkout (run scripts live there) and it could not be adopted "
                            f"({act.detail}).",
                            next_commands=[src_cmd], data={"binary_failed": True})
                    clone_notes.append(f"  adopted pinned source for {cid} (run scripts)")

            baseline, berr = self._binary_registry_baseline(stack_id)
            if berr:
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {berr}")
            txn = secrets.token_hex(8)
            try:
                runtime_fs.mkdir(self._paths, "state")     # a not-yet-bootstrapped root has none
                tmpdir = tempfile.mkdtemp(prefix="lhpc-binary-",
                                          dir=str(self._paths.under("state")))
            except (OSError, PathContainmentError, ValueError) as exc:
                return ActionResult(False, f"Binary install of '{stack_id}' blocked: cannot create a "
                                           f"staging directory ({exc})")
            # AUTHORITATIVE running recheck under the held locks (the source-update pattern):
            # a start that slipped in before the locks must refuse with ZERO mutation — a
            # binary update replaces the executable/firmware the stack is running from.
            _running = self._binary_running_components(stack_id)
            if _running:
                return ActionResult(
                    False, f"Refusing to install '{stack_id}' from binary: component(s) "
                           "running.",
                    details=[f"  running: {', '.join(_running)} — stop them first"],
                    next_commands=[f"lhpc stack stop {stack_id} --yes"])

            # OPEN THE TRANSACTION FIRST — before the auth change and before any file moves.
            # The journal carries the previous receipt and the previous mesh-password value, so
            # even a hard crash during download/extract is recoverable (audit finding: auth used
            # to be blanked before any journal existed).
            auth_journal: dict = {}
            _auth_restore = None
            if getattr(self, "hmac_applies", None) and self.hmac_applies(stack_id):
                _hc = self._hmac_component(stack_id)
                if _hc is not None:
                    _prev = self._resolved_param_value(stack_id, "run", _hc.id, "password_file")
                    if _prev:
                        auth_journal = {"param": "password_file", "previous": _prev}
            bi.open_txn(self._paths, stack_id, txn,
                        old_receipt=prev_receipt_raw, auth=auth_journal)
            if auth_journal:
                # The published firmware has no mesh password: the bridge must not be launched
                # with --password-file. Journaled above, so this is undoable either way.
                _r = self.save_config_bundle(
                    stack_id, values={"password_file": ""},
                    _allow_managed_params=frozenset({"password_file"}))
                if not _r.ok:
                    self.binary_recover()
                    return ActionResult(
                        False, f"Binary install of '{stack_id}' blocked: could not switch the "
                               f"mesh password off ({_r.summary})")
                _auth_restore = auth_journal["previous"]
            try:
                tar_path = os.path.join(tmpdir, entry.filename)
                stage = os.path.join(tmpdir, "stage")
                os.makedirs(stage, exist_ok=True)
                bi.download_artifact(entry, tar_path)
                files = bi.validate_and_extract(tar_path, stage, spec.publish_roots)
                # The transaction stays OPEN across promotion, probes and the receipt write:
                # the journal carries the displaced files, the newly created ones, the PREVIOUS
                # receipt and the previous auth value, so any failure below restores the old
                # working install instead of destroying it (audit finding).
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
                # must be able to put the old install back completely (audit finding).
                bi.displace(self._paths, txn,
                            self._stale_paths(prev_files, files, owned_dirs))
                if not bi.commit(self._paths):        # THE commit point
                    raise bi.BinaryInstallError("could not commit the binary transaction")
            except (bi.BinaryInstallError, OSError) as exc:
                # A failure AFTER publish (probe, missing proof path, unwritable receipt) must not
                # leave published files behind: with no receipt nothing would ever remove them, and
                # a later `--source pinned` install would see a "healthy" directory and skip the
                # clone (live-audit finding). Remove exactly what we put there.
                # An OSError here (disk full, permissions) is a failed install like any other:
                # unwind and offer the source channel — never a raw traceback.
                exc = exc if isinstance(exc, bi.BinaryInstallError) else \
                    bi.BinaryInstallError(f"filesystem error during install ({exc})")
                # UNWIND the open transaction: displaced files return, files this run created are
                # removed, the previous receipt and auth are restored. A failed update never
                # costs the operator their working install.
                _rb_ok, _rb_why = self.binary_recover()
                _detail = ([] if _rb_ok else
                           [f"  the previous install could NOT be fully restored: {_rb_why}"])
                return ActionResult(False, f"Binary install of '{stack_id}' failed: {exc.message}",
                                    details=_detail, next_commands=[src_cmd],
                                    data={"binary_failed": True, "offer_source": True,
                                          "rolled_back": _rb_ok})
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            self.invalidate_snapshot()
            return ActionResult(
                True, f"Installed '{stack_id}' from the published binary ({size_mb:.1f} MB).",
                details=[*clone_notes, "  open auth (binary channel): the published firmware has no mesh " "password" if _auth_restore is not None else f"  {probe_out}" if probe_out else "  installed", "  provenance: " + ", ".join(f"{k}@{v[:9]}" for k, v in sorted(entry.components.items())), f"  artifact sha256 {entry.sha256[:12]}…"],
                next_commands=[f"lhpc status {stack_id}", f"lhpc stack start {stack_id}"],
                data={"channel": "binary", "changes": 1})

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

    def binary_recover(self) -> tuple[bool, str]:
        """THE authoritative recovery for an open/interrupted binary transaction — files,
        receipt AND auth in one operation. Called under the locks before any new binary work
        and by the in-process failure path, so a crash and an exception recover identically
        (they used to have separate, unequal paths — audit finding)."""
        # A KILLED run (OOM, power cut) cannot run its own cleanup, and each staging directory
        # holds a whole artifact — tens of megabytes on an SD card. Every binary operation is
        # serialized under the same locks, so anything left here is stale by definition
        # (live-found on the Zero: one orphan from an earlier crashed run).
        self._sweep_binary_staging()
        j, jstate = bi.read_journal(self._paths)
        if jstate == "absent":
            return True, ""
        if jstate == "unsafe" or j is None:
            return False, ("an unreadable binary-install journal is present — resolve "
                           "state/binary/install.journal.json by hand")
        stack_id = j.get("stack", "")
        if j.get("state") == "committed":
            # The commit is durable: the NEW install is the truth. Only bookkeeping remains.
            return bi.drop_txn(self._paths, j), ""
        ok, why = bi.rollback_files(self._paths, j)
        if not ok:
            return False, why                    # journal RETAINED as evidence
        old_receipt = j.get("old_receipt")
        if old_receipt:
            if not brx.write_raw(self._paths, stack_id, old_receipt):
                return False, "the previous binary receipt could not be restored"
        elif not brx.remove_receipt(self._paths, stack_id):
            # There was no receipt before this run, so the failed install's one must go. If it
            # cannot, the journal and backups MUST stay: dropping them would discard the only
            # evidence a later attempt could converge from (audit finding).
            return False, "the failed install's binary receipt could not be removed"
        prev_auth = (j.get("auth") or {}).get("previous")
        if prev_auth:
            r = self.save_config_bundle(stack_id, values={"password_file": prev_auth},
                                        _allow_managed_params=frozenset({"password_file"}))
            if not r.ok:
                return False, f"the mesh password setting could not be restored ({r.summary})"
        if not bi.drop_txn(self._paths, j):
            return False, "the binary-install journal could not be cleared"
        self.invalidate_snapshot()
        return True, ""

    @staticmethod
    def _stale_paths(prev_files, new_files, owned_dirs) -> list:
        """Paths the PREVIOUS receipt owned that the NEW install does not — by file OR through
        an owned directory.

        The directory clause matters: an older receipt listed the provisioned venv file by file
        while the new one owns it as a directory, and treating those entries as stale deleted
        the CLI the very same run had just provisioned (live-found on the Zero)."""
        owned = tuple(owned_dirs or ())
        return sorted(rel for rel in set(prev_files) - set(new_files)
                      if not any(rel == d or rel.startswith(d + "/") for d in owned))

    def _rel_files_under(self, rel_dir: str) -> list:
        """Every non-directory leaf under a runtime-root-relative directory, as relative paths.

        Symlinks COUNT: a virtualenv is half symlinks (`bin/python3`), and owning only the
        regular files left them behind on removal — enough for `python3 -m venv` to treat the
        environment as existing, skip ensurepip, and fail the next step (live-found)."""
        base = self._paths.under(*rel_dir.split("/"))
        if not os.path.isdir(base):
            return []
        out = []
        for root, _dirs, names in os.walk(base):
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
        after every child, or an emptied tree keeps its upper levels (live-found — four empty
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
        # the next step failed with "pip install failed" (live-found on the Zero).
        # When there was NO previous venv, journal the directory we are about to create instead:
        # a hard crash mid-provisioning would otherwise leave a half-built one that no journal,
        # receipt or recovery knows about (audit finding). The `except` below covers the
        # in-process failure; this covers the power cut.
        if not bi.displace_dir(self._paths, txn, venv_rel):
            bi.note_created_dir(self._paths, venv_rel)
        # The binary channel has NO source checkout for this component — the build steps must
        # run from the runtime root, not from a directory that does not exist (audit finding).
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
                res = self._system.runner.run(argv, timeout=900.0, cwd=src)
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
        return all(rel in owned for rel in self._rel_files_under(rel_dir))

    def switch_source_plan(self, groups, owned_files=()) -> tuple:
        """PRE-FLIGHT for a binary -> source switch: `(paths_to_replace, refusals)`.

        The ordinary install treats an existing managed directory as "already installed". On a
        CHANNEL SWITCH that is not enough: the operator asked for a specific selector, and a
        checkout left over from the binary install (meshcom keeps its pinned clone) can sit at a
        completely different commit. Reporting a switch to `dev` while the tree stays pinned is
        a lie about provenance (audit finding).

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
            if (dirty := inst.dirty_report(dest, path)):
                refusals.append(f"{path}: local modifications — "
                                + ", ".join((list(dirty.tracked) + list(dirty.untracked))[:3]))
                continue
            # The selector's commit, resolved by the EXISTING mechanisms: the plan's frozen
            # known-working commit, else the manifest pin for `pinned`, else `_frozen_ref`
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
            if _js != "valid" or _j is None or _j.get("stack") != stack_id \
                    or _j.get("txn") != txn:
                return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
                                           "the open transaction is not this one")
            state, rec, why = brx.receipt_state(self._paths, stack_id)
            return self._retire_body(stack_id, state, rec, why, force=force, locked=locked,
                                     txn=txn)
        # An OPEN transaction makes every receipt non-authoritative: recover FIRST, so what we
        # retire is the settled install and not a half-published one (audit finding).
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
            # remaining evidence, and the caller reports INCOMPLETE (audit finding).
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
            # content we must not delete.
            bad = [b for b in bad if b.get("actual")]
            if bad:
                changed = ", ".join(b["path"] for b in bad[:3])
                return ActionResult(
                    False,
                    f"Refusing to retire the binary install of '{stack_id}': installed files "
                    f"changed since installation ({changed}).",
                    details=["  Remove them by hand, or re-run with the force option."])
        if not locked and (_running := self._binary_running_components(stack_id)):
            return ActionResult(
                False, f"Refusing to retire the binary install of '{stack_id}': component(s) "
                       "running.",
                details=[f"  running: {', '.join(_running)} — stop them first"],
                next_commands=[f"lhpc stack stop {stack_id} --yes"])
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
                           f"not be moved aside ({exc})")
            if not brx.remove_receipt(self._paths, stack_id):
                return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
                                           "its receipt could not be removed")
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
        # binary files behind with no ownership record at all (audit finding).
        still_there = [rel for rel in rec.files
                       if os.path.exists(self._paths.under(*rel.split("/")))]
        if still_there or failed:
            return ActionResult(
                False,
                f"Retirement of '{stack_id}' is INCOMPLETE — the receipt was kept so the "
                "remaining files stay owned.",
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
                if os.path.isdir(d) and not os.path.islink(d):
                    shutil.rmtree(d)
            except (OSError, PathContainmentError, ValueError) as exc:
                return ActionResult(
                    False, f"Retirement of '{stack_id}' is INCOMPLETE — the receipt was kept "
                           f"so {rel_dir} stays owned.",
                    details=[f"  could not remove {rel_dir} ({exc})"],
                    next_commands=[f"lhpc clean {stack_id} --purge --yes"])
        if not brx.remove_receipt(self._paths, stack_id):
            return ActionResult(False, f"Removed the binary files of '{stack_id}' but could not "
                                       "remove its receipt — resolve state/binary by hand.")
        self.invalidate_snapshot()
        return ActionResult(True, f"Retired the binary install of '{stack_id}' "
                                  f"({removed} file(s) removed).")

    # ---- freshness -------------------------------------------------------------------------

    def binary_freshness(self, stack_id: str) -> dict:
        """LOCAL, network-free freshness for a binary-installed stack: the receipt's component
        commits vs the CURRENT manifest pins. {state: current|behind|n/a, behind: [...]}."""
        state, rec, _why = self.binary_receipt_state(stack_id)
        if state != "valid" or rec is None:
            return {"state": "n/a", "behind": []}
        pins = self._binary_pins(stack_id)
        behind = [cid for cid, want in sorted(pins.items())
                  if rec.components.get(cid) != want]
        return {"state": "behind" if behind else "current", "behind": behind}

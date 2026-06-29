"""Runtime-root bootstrap, readable wrappers, and safe source adoption.

This is the first *mutating* layer, but it is deliberately conservative:

  * bootstrap is idempotent and NEVER overwrites local config or secrets;
  * generated `start/` wrappers point at real commands, forward `$@`, and use
    RX-safe defaults — no opaque logic is hidden in them;
  * source adoption copies the operator's locally verified checkout into the
    runtime root (or clones a pin) and then VERIFIES the pin; it never edits,
    resets or cleans the original source, and refuses to overwrite an existing
    runtime checkout unless explicitly forced;
  * a dirty source is reported, never silently "repaired".

All operations are expressed as a `Plan` of `PlanAction`s so the CLI (and, later,
the web confirmation screen) can show the exact intended effect before applying.
Nothing here builds, starts a service, or transmits.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .assets import asset_text
from .config import Config
from .model import Component, Stack
from .paths import Paths, PathContainmentError
from .probes import System
from .probes.source import probe_source

RUNTIME_SUBDIRS = (
    "bin", "src", "build", "start", "config", "profiles", "systemd", "state", "logs", "docs",
)

# Heavy, regenerable directories we skip when adopting a local checkout.
_ADOPT_IGNORE = shutil.ignore_patterns(
    ".pio", ".venv", "build", ".work", ".run", "__pycache__", "node_modules",
)


class _Substituted(Exception):
    """Internal signal: the staging candidate/link leaf no longer matches the captured
    identity handle (it was swapped). The transaction retains the substituted leaf + journal
    as evidence and returns recovery-required — it NEVER recursively deletes the substitute."""


class _JournalLost(Exception):
    """Internal signal: an owned journal state update proved the visible journal leaf is no
    longer this transaction's inode (a replacement). The journal is RETAINED (never removed)
    and the transaction returns recovery-required, restoring the prior source where freed."""

# Config templates ship as package data (wheel-safe), not a repo-root path.

# Commented starter for the runtime-local config. Operator fills this in; until
# then the callsign is unset (not the example placeholder). Never tracked.
_LOCAL_STARTER = """\
# LoRaHAM Pi Control — local operator overrides (runtime-local, git-ignored).
# Fill in your details. See docs/operations.md and lhpc/data/local.example.toml.

# [operator]
# callsign = "YOURCALL"
# locator  = "JO00aa"

# [web]
# host = "127.0.0.1"
# port = 8770
"""


@dataclass
class PlanAction:
    kind: str                 # mkdir | config | secret | wrapper | adopt | clone | verify
    target: str
    description: str
    status: str = "planned"   # planned | exists | done | failed | skipped
    detail: str = ""
    provenance: str = ""      # provenance state of an activated source (see provenance.py)


@dataclass
class Plan:
    title: str
    actions: list[PlanAction] = field(default_factory=list)

    @property
    def changes(self) -> list[PlanAction]:
        return [a for a in self.actions if a.status == "planned"]

    @property
    def ok(self) -> bool:
        return all(a.status != "failed" for a in self.actions)


class Installer:
    def __init__(self, paths: Paths, stacks: tuple[Stack, ...], config: Config,
                 system: System) -> None:
        self.paths = paths
        self.stacks = stacks
        self.config = config
        self.system = system

    # -- layout ------------------------------------------------------------

    def subdir(self, name: str) -> Path:
        return self.paths.runtime_root / name

    # -- bootstrap ---------------------------------------------------------

    def plan_bootstrap(self) -> Plan:
        plan = Plan(title=f"Bootstrap runtime root {self.paths.runtime_root}")
        for name in RUNTIME_SUBDIRS:
            d = self.subdir(name)
            plan.actions.append(PlanAction(
                "mkdir", str(d), f"create {name}/",
                status="exists" if d.is_dir() else "planned"))
        # Local config + secrets: create from templates only if absent.
        local = self.subdir("config") / "local.toml"
        plan.actions.append(PlanAction(
            "config", str(local), "write config/local.toml (operator settings)",
            status="exists" if local.exists() else "planned"))
        secret = self.subdir("config") / "secrets.toml"
        plan.actions.append(PlanAction(
            "secret", str(secret), "write config/secrets.toml (0600)",
            status="exists" if secret.exists() else "planned"))
        # Readable wrappers (regenerated).
        expected = set()
        for stack, comp, fname in self._wrappers():
            expected.add(fname)
            plan.actions.append(PlanAction(
                "wrapper", str(self.subdir("start") / fname),
                f"generate start/{fname}"))
        # Prune stale wrappers (e.g. left over after a stack/component rename).
        start_dir = self.subdir("start")
        if start_dir.is_dir():
            for existing in sorted(start_dir.glob("*-start")):
                if existing.name not in expected:
                    plan.actions.append(PlanAction(
                        "prune-wrapper", str(existing),
                        f"remove stale start/{existing.name}"))
        return plan

    def apply_bootstrap(self, plan: Plan | None = None) -> Plan:
        plan = plan or self.plan_bootstrap()
        for action in plan.actions:
            if action.status in ("exists", "skipped", "done"):
                continue
            try:
                self._apply_action(action)
            except (OSError, PathContainmentError) as exc:
                action.status, action.detail = "failed", str(exc)
        return plan

    def _apply_action(self, action: PlanAction) -> None:
        from . import runtime_fs
        if action.kind == "mkdir":
            runtime_fs.ensure_dir(self.paths, Path(action.target))
            action.status = "done"
        elif action.kind == "config":
            dest = Path(action.target)
            if not dest.exists():       # preserve an existing operator config
                runtime_fs.atomic_write(self.paths, dest, _LOCAL_STARTER, 0o644)
            action.status = "done"
        elif action.kind == "secret":
            dest = Path(action.target)
            if not dest.exists():       # atomic write, no symlink-follow, mode 0600
                runtime_fs.atomic_write(self.paths, dest, asset_text("secrets.example.toml"), 0o600)
            action.status = "done"
            action.detail = "mode 0600"
        elif action.kind == "wrapper":
            self._write_wrapper_by_path(Path(action.target))
            action.status = "done"
        elif action.kind == "prune-wrapper":
            runtime_fs.unlink(self.paths, Path(action.target))
            action.status = "done"

    # -- wrappers ----------------------------------------------------------

    def _runnable(self):
        for stack in self.stacks:
            for comp in stack.components:
                if comp.run_argv and comp.source is not None:
                    yield stack, comp

    def _wrapper_name(self, stack: Stack, comp: Component) -> str:
        order = "0" if comp.start_order is None else str(comp.start_order)
        short = comp.id[len(stack.id) + 1:] if comp.id.startswith(stack.id + "-") else comp.id
        return f"{stack.id}-{order}-{short}-start"

    def _wrappers(self):
        for stack, comp in self._runnable():
            yield stack, comp, self._wrapper_name(stack, comp)

    def _write_wrapper_by_path(self, path: Path) -> None:
        for stack, comp, fname in self._wrappers():
            if self.subdir("start") / fname == path:
                self._write_wrapper(stack, comp, path)
                return

    def _write_wrapper(self, stack: Stack, comp: Component, path: Path) -> None:
        """Generate a manual launcher as PYTHON (os.execvpe, no shell) from the SAME
        structured command spec. Written via the containment-checked mutable-path API
        so a wrapper can never be created through a symlink that escapes the root."""
        from . import commands, runtime_fs
        src_dir = str(self.paths.resolve_source(comp.source.path))
        runtime = str(self.paths.runtime_root)
        body = commands.render_wrapper(comp, self.config.operator, runtime, src_dir)
        # Confine the wrapper under the runtime root and write it atomically THROUGH the
        # safe runtime FS (containment + no-follow leaf + fsync), executable.
        safe = self.paths.under("start", path.name)
        runtime_fs.atomic_write(self.paths, safe, body, 0o755)

    # -- source adoption ---------------------------------------------------

    def plan_install(self, stack_id: str | None = None) -> Plan:
        plan = Plan(title="Install (adopt/verify sources)")
        for stack in self.stacks:
            if stack_id and stack.id != stack_id:
                continue
            for comp in stack.components:
                if comp.source is None:
                    continue
                dest = self.paths.resolve_source(comp.source.path)
                if dest.exists():
                    probe = probe_source(self.system, comp.source, str(dest))
                    plan.actions.append(PlanAction(
                        "verify", str(dest),
                        f"{comp.id}: source present ({probe.state.value})",
                        status="exists", detail=probe.state.value))
                else:
                    plan.actions.append(PlanAction(
                        "adopt", str(dest),
                        f"{comp.id}: adopt {comp.source.adopt_dir} -> {comp.source.path}"))
        return plan

    def adopt_source(self, comp: Component, *, force: bool = False,
                     source: str = "pinned") -> PlanAction:
        """Install a component's source. `source` selects the version:
          * "dev"    — newest commit on the remote branch (default);
          * "stable" — the latest release tag;
          * "pinned" — the manifest's pinned known-good commit.
        It clones from GitHub for that version and, on failure, falls back to the
        operator's local checkout. Components with a `link` strategy (prebuilt
        venvs/artifacts) are linked to the local working tree instead.
        Never alters the local source; refuses to overwrite unless forced.
        """
        spec = comp.source
        dest = self.paths.resolve_source(spec.path)
        action = PlanAction("adopt", str(dest), f"adopt {comp.id}")
        from . import reslock
        # P0.2: ONE operation boundary, deadlock-free order (index THEN source path).
        # Recovery runs under the INDEX lock ONLY — the per-source locks it takes must
        # NOT self-contend with a source lock adopt itself holds, so adopt acquires the
        # target source-path lock AFTER recovery completes. The index lock is held
        # throughout, so nothing can create a new journal between recovery and mutation.
        try:
            with reslock.operation_lock(self.paths, self._index_key(), "adopt", comp.id):
                # Recover any interrupted activation (incl. THIS source's own valid
                # journal), then block on ANY unresolved/malformed journal — not just this
                # source's: an unknown or filename-mismatched journal blocks ALL mutation.
                self._recover_scan()
                if self._pending_journals():
                    action.status = "failed"
                    action.detail = ("recovery-required: an unresolved source-transaction "
                                     "journal is present — resolve it before any mutation")
                    return action
                with reslock.operation_lock(self.paths, self._source_lock_key(spec.path),
                                            "update", comp.id):
                    return self._adopt_locked(comp, spec, dest, action, force, source)
        except reslock.ResourceBusy as busy:
            action.status, action.detail = "failed", f"another source operation is in progress: {busy}"
            return action

    def _adopt_locked(self, comp, spec, dest, action, force, source):
        # Index + target source-path locks are held by the caller. Recovery + the global
        # blocking decision already ran under the index lock.
        if dest.exists() and not force:
            action.status, action.detail = "skipped", "destination already exists"
            return action
        # Never overwrite a working tree with local modifications, or a linked dev
        # tree, on update — staging into it would be silent data loss.
        if dest.exists() and force:
            if dest.is_symlink():
                action.status, action.detail = "skipped", "linked dev tree — left as-is"
                return action
            if self._is_dirty(dest):
                action.status, action.detail = "failed", "local modifications present — not overwritten"
                return action
        try:
            from . import runtime_fs
            runtime_fs.ensure_dir(self.paths, dest.parent)   # descriptor-anchored, no-follow
        except (OSError, PathContainmentError) as exc:
            action.status, action.detail = "failed", str(exc)
            return action

        search = Path(self.config.get("install", "adopt_search_root", "~/src")).expanduser()
        local = search / spec.adopt_dir
        strategy = spec.strategy or self.config.get("install", "source_strategy", "adopt")
        # The INDEX + SOURCE-PATH locks are already held by adopt_source across candidate
        # creation, verification, activation, and cleanup.
        return self._stage_and_activate(comp, source, action, dest, spec, local, strategy)

    def _stage_and_activate(self, comp: Component, source: str, action: "PlanAction",
                            dest: Path, spec, local: Path, strategy: str) -> "PlanAction":
        """Create, verify, and atomically activate a candidate under ONE held source-parent
        FD spanning journal preflight → exclusive candidate creation → staging → candidate
        provenance → activation → active-source provenance → rollback/cleanup. The active
        source is touched only at the final rename, so any failure leaves it untouched. Git,
        copy, and provenance receive ONLY the controller-pinned `/proc/<pid>/fd/<held>/<name>`
        path — never a runtime pathname that a parent swap could redirect."""
        import time as _time
        from . import source_fs, provenance
        staging = dest.with_name(f".{dest.name}.candidate-{os.getpid()}-{_time.monotonic_ns()}")
        trusted, signer_diags = provenance.load_trusted_signers(self.config)
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                # (1) Journal preflight: only an ABSENT journal may begin a new transaction;
                # any existing journal must be resolved by recovery first (never overwritten).
                if source_fs.leaf_kind(self.paths, self._journal_path(dest)) != "absent":
                    action.status, action.detail = "failed", (
                        "recovery-required: an unresolved source-transaction journal exists "
                        "for this source — resolve it before installing/updating")
                    return action
                # (2-3) Exclusive candidate creation + staging, all through the held FD.
                desc, handle = self._stage_candidate(txn, comp, source, dest, staging, spec,
                                                     local, strategy, action)
                if desc is None:
                    return action          # `_stage_candidate` recorded the typed failure
                # Provenance path per handle type:
                #  * CandidateHandle -> the candidate FD-pinned path (follows the inode through
                #    the activation rename);
                #  * LinkHandle -> the VERIFIED external target (`local_target`), evaluated only
                #    after the leaf is proven to still be OUR captured symlink (never a
                #    staging/dest symlink whose identity has not just been proven).
                is_link = isinstance(handle, source_fs.LinkHandle)

                def _prov_path(leaf_name: str) -> str | None:
                    if is_link:
                        if not txn.verify_link(handle, leaf_name):
                            return None                       # unproven link leaf -> block
                        return str(handle.local_target)
                    return handle.pinned_path()

                # (4) Candidate/link provenance gate.
                pre_pinned = _prov_path(staging.name)
                if pre_pinned is None:
                    action.status, action.detail = "failed", (
                        "recovery-required: link staging leaf identity could not be proven "
                        "(evidence retained)")
                    return action
                pre = provenance.evaluate(self.system.runner, pre_pinned, spec, source, trusted)
                if not pre.ok:
                    # Handle-safe cleanup: a candidate/link substituted during provenance is
                    # retained as evidence, never deleted. dest untouched either way.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status, action.provenance = "failed", pre.status
                    action.detail = f"provenance blocked before activation: {pre.detail} [{pre.status}]"
                    return action
                # (5-9) Activate + final provenance (post-rename, on the VERIFIED active leaf)
                # + cleanup — all under the SAME held FD. For a link, `_activate_held` has
                # already proven the active leaf is our captured symlink before this runs.
                def _post_ok() -> bool:
                    p = _prov_path(dest.name)
                    return p is not None and provenance.evaluate(
                        self.system.runner, p, spec, source, trusted).ok
                outcome = self._activate_held(txn, dest, staging, verify_active=_post_ok,
                                              handle=handle)
                if outcome == "provenance-blocked":
                    post = provenance.evaluate(self.system.runner,
                                               txn.child_pinned_path(dest.name), spec, source,
                                               trusted)
                    action.status, action.provenance = "failed", post.status
                    action.detail = ("post-activation provenance mismatch — rolled back to the "
                                     "prior source (journal retained); the new version was NOT adopted")
                    return action
                if outcome == "recovery-required":
                    action.status = "failed"
                    action.detail = ("recovery-required: source transaction left a retained "
                                     "journal — candidate/prior evidence preserved")
                    return action
                if outcome != "activated":         # "failed-clean": no journal, safe to drop
                    self._cleanup_owned_staging(txn, handle, staging.name)   # handle-safe
                    action.status, action.detail = "failed", "activation failed — active source untouched"
                    return action
                return self._adopt_done(action, spec, dest, desc, source, signer_diags)
        except PathContainmentError:
            action.status, action.detail = "failed", (
                "managed source parent is unsafe (symlinked/swapped) — active source untouched")
            return action

    def _cleanup_owned_staging(self, txn, handle, staging_name: str) -> str:
        """THE authoritative handle-safe staging cleanup. Removes the staging leaf ONLY when it
        still matches its `CandidateHandle`/`LinkHandle`; a substituted replacement is RETAINED
        as evidence, never recursively deleted merely because it kept the expected name. Returns
        'removed' | 'absent' | 'identity-lost'."""
        if txn.leaf_kind(staging_name) == "absent":
            return "absent"
        if not self._verify_staged(txn, handle, staging_name):
            return "identity-lost"                    # substituted -> retain, never delete
        try:
            txn.rmtree(staging_name)
        except (OSError, PathContainmentError):
            return "identity-lost"                    # removal not provable -> retain
        return "removed"

    def _stage_candidate(self, txn, comp, source: str, dest: Path, staging: Path, spec,
                         local: Path, strategy: str, action):
        """Stage the candidate through the held transaction. Returns `(desc, handle)` — a
        description plus the `CandidateHandle` (a retained FD on the candidate dir; None for
        the link strategy, whose leaf is a symlink). On failure returns `(None, None)` with a
        typed failure recorded on `action`. Git/copy write ONLY through the candidate FD-pinned
        path (`handle.pinned_path()`), never the mutable candidate leaf name."""
        if strategy == "link":
            if not local.is_dir():
                action.status, action.detail = "failed", f"local checkout not found: {local}"
                return None, None
            # A linked checkout must STILL satisfy the requested version (same policy as
            # copy/clone) — never report a version-selected adoption it cannot prove.
            if not self._fallback_satisfies(spec, local, source):
                action.status, action.detail = "failed", (
                    f"linked checkout does not satisfy the requested {source} version "
                    "(link strategy cannot prove it) — active source untouched")
                return None, None
            try:
                lh = txn.create_link(local, staging.name)  # symlink leaf + captured identity
            except OSError as exc:
                action.status, action.detail = "failed", str(exc)
                return None, None
            return "linked local dev", lh
        # Clone / copy: EXCLUSIVELY create the empty candidate dir via the held FD (any
        # pre-existing leaf of any kind fails closed) and RETAIN its fd, then write INTO the
        # candidate FD-pinned path — Git/copy never re-resolve the leaf by name.
        remote = self.config.remotes.get(comp.id) or spec.remote
        handle = txn.create_candidate(staging.name)
        if remote and self._clone(spec, Path(handle.pinned_path()), source, remote):
            return f"GitHub {source}", handle
        # Clone failed (or no remote) -> reset the (intact controller-owned) candidate via the
        # held FD, then try the local fallback. If the candidate was SUBSTITUTED, do NOT delete
        # the replacement and do NOT recreate a candidate through this flow — fail closed.
        if self._cleanup_owned_staging(txn, handle, staging.name) == "identity-lost":
            action.status, action.detail = "failed", (
                "recovery-required: staging candidate was substituted (evidence retained)")
            return None, None
        handle = txn.create_candidate(staging.name)
        if local.is_dir():
            if not self._fallback_satisfies(spec, local, source):
                self._cleanup_owned_staging(txn, handle, staging.name)   # drop empty candidate
                action.status, action.detail = "failed", (
                    "GitHub clone failed and the local checkout does not satisfy the "
                    f"requested {source} version — active source untouched")
                return None, None
            try:
                self._copy_into_candidate(local, handle.pinned_path())
            except OSError as exc:
                self._cleanup_owned_staging(txn, handle, staging.name)   # drop partial copy
                action.status, action.detail = "failed", f"{exc} (active source untouched)"
                return None, None
            return "local fallback", handle
        self._cleanup_owned_staging(txn, handle, staging.name)           # drop empty candidate
        action.status, action.detail = "failed", (
            "GitHub clone failed and no local checkout — active source untouched")
        return None, None

    @staticmethod
    def _copy_into_candidate(local: Path, cand_pinned: str) -> None:
        """Copy the CONTENTS of `local` into the already-created empty candidate (the
        controller-pinned path), entry by entry — NO `dirs_exist_ok` merge into the candidate
        root — honoring the same ignore set as a clone and preserving symlinks unfollowed."""
        names = os.listdir(local)
        ignored = _ADOPT_IGNORE(str(local), names)
        for entry in names:
            if entry in ignored:
                continue
            s = local / entry
            d = f"{cand_pinned}/{entry}"
            if s.is_symlink():
                os.symlink(os.readlink(s), d)
            elif s.is_dir():
                shutil.copytree(s, d, ignore=_ADOPT_IGNORE, symlinks=True)
            else:
                shutil.copy2(s, d, follow_symlinks=False)

    def _fallback_satisfies(self, spec, local: Path, source: str) -> bool:
        """A local-fallback / linked checkout may activate only if it PROVABLY satisfies
        the requested version — fail closed:
          * `pinned` REQUIRES a configured exact pin AND HEAD == that pin;
          * `stable` REQUIRES a configured tag AND the checkout is exactly at that tag;
          * `dev` requires the configured branch if one is set; with no branch this is the
            documented permissive policy (dev = whatever the operator's tree is on).
        A version-selected request whose selector is not configured can never be proven,
        so it is rejected rather than reported as a successful selected adoption."""
        run = self.system.runner.run
        if source == "pinned":
            if not spec.pin_commit:               # no configured pin -> cannot prove
                return False
            head = run(["git", "-C", str(local), "rev-parse", "HEAD"], 5.0)
            return head.returncode == 0 and head.stdout.strip() == spec.pin_commit
        if source == "stable":
            if not spec.pin_tag:                  # no configured tag -> cannot prove
                return False
            r = run(["git", "-C", str(local), "describe", "--tags", "--exact-match"], 5.0)
            return r.returncode == 0 and r.stdout.strip() == spec.pin_tag
        if source == "dev" and spec.branch:
            r = run(["git", "-C", str(local), "rev-parse", "--abbrev-ref", "HEAD"], 5.0)
            return r.returncode == 0 and r.stdout.strip() == spec.branch
        return True                               # dev with no branch: documented permissive

    def _is_dirty(self, dest: Path) -> bool:
        if not (dest / ".git").exists():
            return False
        r = self.system.runner.run(["git", "-C", str(dest), "status", "--porcelain",
                                     "--untracked-files=no"], 5.0)
        return r.returncode == 0 and bool(r.stdout.strip())

    # -- source activation transaction (durable + recoverable) -------------

    # -- source activation transaction (durable, strictly-trusted journal) --
    #
    # The journal NEVER stores trusted absolute paths. It records logical, validated
    # RUNTIME-RELATIVE names; recovery derives the real paths from the runtime root and
    # rejects anything that is absolute, escaping, symlinked, or that does not match the
    # controller's candidate/prior naming patterns. An invalid journal is RETAINED and
    # blocks the affected source — it is never followed or deleted blindly.

    _VALID_STATES = ("planned", "prior-archived", "activated")

    def _txn_dir(self) -> Path:
        return self.paths.under("state", "source-txn")

    def _journal_path(self, dest: Path) -> Path:
        # Journal identity is bound to the FULL managed runtime-relative source path, not the
        # basename: `src/a/app` and `src/b/app` get distinct journals (readable prefix +
        # SHA-256 digest of `source_rel`). Recovery re-derives this and refuses any journal
        # whose filename does not match its declared source (so a legacy basename-only
        # `app.json` is retained and blocks, never silently migrated).
        from . import validators
        import hashlib
        rel = self._source_rel(dest)
        # FULL SHA-256 (domain-separated) of the normalized source_rel — collision-resistant,
        # not a truncated prefix.
        digest = hashlib.sha256(("lhpc-journal:" + rel).encode("utf-8")).hexdigest()
        stem = validators.path_component(dest.name, field="source")
        return self._txn_dir() / f"{stem}-{digest}.json"

    @staticmethod
    def _txn_id(candidate_rel: str) -> str:
        """A transaction identifier bound to the per-transaction candidate name (which carries
        a unique pid+monotonic nonce). Recorded in the journal and required to match on every
        state update / recovery — so a journal cannot be re-pointed at a different transaction."""
        import hashlib
        return hashlib.sha256(("lhpc-source-txn:" + candidate_rel).encode("utf-8")).hexdigest()

    def _source_rel(self, p: Path) -> str:
        return os.path.relpath(str(p), str(self.paths.runtime_root))

    def _source_lock_key(self, source_path: str) -> str:
        # THE canonical source lock — by the managed source PATH (not component id), so
        # every consumer of one shared checkout (chat + igate -> src/LoRaHAM_Daemon)
        # serialises on the same lock.
        from . import reslock
        return reslock.source_lock_key(source_path)

    def _resolve_rel(self, rel) -> Path:
        """A runtime-relative path from the journal -> a contained absolute path. Raises
        ValueError on absolute/traversal/escape (never trust the stored string)."""
        if (not isinstance(rel, str) or not rel or os.path.isabs(rel)
                or rel != os.path.normpath(rel) or ".." in rel.split(os.sep)):
            raise ValueError(f"unsafe journal path {rel!r}")
        return self.paths.under(*rel.split(os.sep))

    @staticmethod
    def _is_prev_name(dest: Path, prev: Path) -> bool:
        return prev.parent == dest.parent and prev.name == f".{dest.name}.prev"

    @staticmethod
    def _is_candidate_name(dest: Path, cand: Path) -> bool:
        return cand.parent == dest.parent and bool(
            re.fullmatch(rf"\.{re.escape(dest.name)}\.candidate-\d+-\d+", cand.name))

    def _journal_payload(self, dest: Path, prev: Path, staging: Path, state: str,
                         txn_id: str) -> str:
        import json
        return json.dumps({
            "version": 2, "state": state,
            "source_rel": self._source_rel(dest),
            "prev_rel": self._source_rel(prev),
            "candidate_rel": self._source_rel(staging),
            "txn_id": txn_id,
        })

    def _create_journal(self, dest: Path, prev: Path, staging: Path):
        """EXCLUSIVELY create the initial (`planned`) journal (`O_CREAT|O_EXCL|O_NOFOLLOW`,
        fsync'd) and RETAIN its file + parent fds. Returns a journal handle
        `{marker: OwnedMarker, txn_id, path}`, or None if ANY journal leaf already exists
        (injected after preflight, or stale) — the caller then returns recovery-required
        WITHOUT touching candidate/dest/`.prev`. The caller MUST close the handle."""
        from . import runtime_fs
        jp = self._journal_path(dest)
        txn_id = self._txn_id(self._source_rel(staging))
        try:
            marker = runtime_fs.open_marker_excl(
                self.paths, jp, self._journal_payload(dest, prev, staging, "planned", txn_id))
        except (FileExistsError, OSError, PathContainmentError):
            return None
        return {"marker": marker, "txn_id": txn_id, "path": jp}

    @staticmethod
    def _close_journal(jh) -> None:
        if jh is not None:
            jh["marker"].close()

    def _update_journal(self, jh, dest: Path, prev: Path, staging: Path, state: str) -> bool:
        """Rewrite the journal to `state` through the RETAINED file fd, ONLY while the visible
        leaf is still this transaction's inode (verified before AND after the write). Returns
        False if ownership was lost (a leaf swap) — the caller then rolls back and retains
        the replacement evidence."""
        return jh["marker"].rewrite(
            self._journal_payload(dest, prev, staging, state, jh["txn_id"]))

    def _managed_source_dests(self) -> set:
        """The EXACT set of resolved managed-source destination paths from the loaded
        manifest. Recovery only ever operates on one of these; a journal whose destination
        is a contained-but-non-source runtime path is retained and blocked."""
        out = set()
        for stack in self.stacks:
            for c in stack.components:
                if c.source:
                    try:
                        out.add(str(self.paths.resolve_source(c.source.path)))
                    except (ValueError, PathContainmentError):
                        pass
        return out

    @staticmethod
    def _index_key() -> str:
        """THE single source-transaction index lock. Held across journal scan,
        validation, the blocking decision, and recovery, BEFORE any per-source-path lock
        (stable global order: index first, then source paths sorted)."""
        return "source-txn-index"

    def _pending_journals(self) -> bool:
        """True if ANY unresolved journal remains in the txn dir (blocks ALL source
        mutation until resolved). Descriptor-anchored: a symlinked/escaping txn dir or a
        symlinked journal entry is UNSAFE and blocks — never a `glob` that could follow a
        swapped directory, and never treating an unsafe container as 'no journals'."""
        from . import runtime_fs
        try:
            d = self._txn_dir()             # paths.under rejects an ESCAPING txn symlink
            entries = runtime_fs.scandir_nofollow(self.paths, d)
        except PathContainmentError:
            return True                     # unsafe txn dir -> block (recovery-required)
        return any(is_link or name.endswith(".json") for name, is_link in entries)

    def recover_source_activations(self) -> list[str]:
        """Public entry: acquire the source-transaction INDEX lock, then scan + recover.
        Serializes the whole scan against any other source operation."""
        from . import reslock
        try:
            with reslock.operation_lock(self.paths, self._index_key(), "recover", ""):
                return self._recover_scan()
        except reslock.ResourceBusy as busy:
            return [f"recovery-required: source-transaction index busy ({busy})"]

    def _recover_scan(self) -> list[str]:
        """Finish or roll back each INTERRUPTED source activation so the active source is
        never left missing. Assumes the INDEX lock is held by the caller. Validates every
        journal field; an invalid/malicious journal is retained and blocks. Each per-source
        recovery takes the source-path lock and only ever renames controller-named
        candidate/prior siblings — never an arbitrary or symlinked path."""
        import json
        from . import runtime_fs, reslock
        # Descriptor-anchored enumeration: a symlinked/escaping txn DIR blocks (never
        # followed); a MISSING dir is genuinely empty. A symlinked journal ENTRY is
        # retained and blocks (recovery-required) — it is NOT skipped/treated as absent.
        # `_txn_dir()` (paths.under) itself rejects an ESCAPING txn-dir symlink, so catch
        # that here too rather than let it escape as an untyped error.
        try:
            d = self._txn_dir()
            entries = runtime_fs.scandir_nofollow(self.paths, d)
        except PathContainmentError as exc:
            return [f"recovery-required: source-txn dir is symlinked/unsafe ({exc}) — retained"]
        out: list[str] = []
        for name, is_link in entries:
            if is_link:
                out.append(f"recovery-required: journal {name} is a symlink (retained)")
                continue
            if not name.endswith(".json"):
                continue
            out.append(self._recover_one(d / name))
        return out

    def _recover_one(self, jf: Path) -> str:
        """Resolve ONE journal under an OWNED marker handle: open the existing regular journal
        no-follow (retaining its file + parent fds), read+validate the payload THROUGH that fd,
        and — if valid — finish/roll back, removing the journal via `OwnedMarker.remove()` so a
        journal replaced after validation but before removal is never removed (the replacement
        is retained). The marker fds always close."""
        import json
        from . import runtime_fs, reslock
        try:
            marker = runtime_fs.open_existing_marker(self.paths, jf)
        except (OSError, PathContainmentError):
            return f"recovery-required: journal {jf.name} unreadable/unsafe (retained)"
        try:
            try:
                j = json.loads(marker.read())                  # read THROUGH the retained fd
                if j.get("version") != 2 or j.get("state") not in self._VALID_STATES:
                    raise ValueError("bad version/state")
                dest = self._resolve_rel(j["source_rel"])
                prev = self._resolve_rel(j["prev_rel"])
                staging = self._resolve_rel(j["candidate_rel"])
            except (OSError, ValueError, KeyError, TypeError):
                return f"recovery-required: invalid activation journal {jf.name} (retained)"
            # The destination must be an EXACT known managed-source path from the loaded
            # manifest — not merely a contained runtime path (defence beyond the filename).
            if str(dest) not in self._managed_source_dests():
                return (f"recovery-required: journal {jf.name} destination "
                        f"{self._source_rel(dest)} is not a known managed source (retained)")
            # The journal FILENAME must match the managed-source identity it claims.
            if jf.name != self._journal_path(dest).name:
                return (f"recovery-required: journal {jf.name} filename does not match "
                        "its declared source identity (retained)")
            if not (self._is_prev_name(dest, prev) and self._is_candidate_name(dest, staging)):
                return (f"recovery-required: journal {jf.name} has non-controller "
                        "candidate/prior names (retained)")
            # The recorded txn_id must match the one derived from the candidate name.
            if j.get("txn_id") != self._txn_id(j["candidate_rel"]):
                return (f"recovery-required: journal {jf.name} transaction id missing/"
                        "mismatched (retained)")
            try:
                with reslock.operation_lock(self.paths,
                                            self._source_lock_key(self._source_rel(dest)),
                                            "recover", dest.name):
                    return self._finish_or_rollback(dest, prev, staging, marker)
            except reslock.ResourceBusy:
                return f"recovery-required: source {dest.name} is busy (retained)"
        finally:
            marker.close()

    def _prev_cleanup_ok(self, txn, prev: Path) -> bool:
        """Remove the archived `.prev` via the HELD FD and CONFIRM it is gone (never silently
        'succeed' on a removal that left it behind)."""
        try:
            txn.rmtree(prev.name)
        except (OSError, PathContainmentError):
            return False
        return txn.leaf_kind(prev.name) == "absent"

    def _finish_or_rollback(self, dest: Path, prev: Path, staging: Path, marker) -> str:
        """Resolve one validated journal under ONE held source-parent FD across verification,
        rename, and cleanup. The journal is removed (via the OWNED `marker`, identity re-
        verified) ONLY once the active source is proven USABLE (via the held FD) and the
        archived prior is proven removed. Any uncertainty — including a journal replaced after
        validation but before removal — RETAINS the journal + candidate/prior evidence and
        yields recovery-required."""
        from . import source_fs

        def _cleared(kind: str) -> str:
            return (f"recovered {dest.name}: {kind}" if marker.remove()
                    else f"recovery-required for {dest.name}: journal could not be removed (retained)")
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                if txn.usable(dest.name):
                    # Completed activation: the archived prior must be PROVEN removed (held FD)
                    # before the journal is cleared — a failed prev removal retains the journal.
                    if not self._prev_cleanup_ok(txn, prev):
                        return (f"recovery-required for {dest.name}: archived prior could not be "
                                "removed (journal + prior retained)")
                    return _cleared("active source intact")
                if txn.leaf_kind(staging.name) != "absent" and txn.leaf_kind(dest.name) == "absent":
                    try:                                # died before staging->dest
                        txn.rename(staging.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        pass                            # fall through to prior restore
                    else:
                        if txn.usable(dest.name):
                            return _cleared("completed interrupted activation")
                if txn.leaf_kind(prev.name) != "absent":     # died after dest->prev: roll back
                    # Free the dest slot if it holds only a dangling symlink (not usable).
                    if txn.leaf_kind(dest.name) == "symlink" and not txn.usable(dest.name):
                        try:
                            txn.rmtree(dest.name)
                        except PathContainmentError:
                            return (f"recovery-required for {dest.name}: dest slot unsafe "
                                    "(journal retained)")
                    try:
                        txn.rename(prev.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        return (f"recovery-required for {dest.name}: could not restore prior "
                                "(journal + candidate/prior retained)")
                    if not txn.usable(dest.name):
                        return (f"recovery-required for {dest.name}: restored prior is not "
                                "usable (journal retained)")
                    return _cleared("rolled back to prior version")
        except PathContainmentError:
            return f"recovery-required for {dest.name}: source parent unsafe (journal retained)"
        return f"recovery-required for {dest.name}: nothing to restore (journal retained)"

    def _activate(self, dest: Path, staging: Path, verify_active=None) -> str:
        """Swap the verified candidate into place, archiving the prior source as a sibling
        `.prev`, using ONE held source-parent FD for BOTH renames + any rollback (a parent
        swap after the first rename cannot redirect the second into another inode). A durable
        journal records the in-flight state so an interruption is finished or rolled back by
        `recover_source_activations()`.

        `verify_active`, when given, is called on the newly-active source INSIDE the durable
        transaction (after the renames, before ANY `.prev`/journal cleanup): if it returns
        False the activation is rolled back to the prior via the same held FD and the journal
        is retained. The journal is cleared only after the active source is proven usable AND
        (if checked) provenance-verified."""
        from . import source_fs
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                return self._activate_held(txn, dest, staging, verify_active)
        except PathContainmentError:
            return "recovery-required"          # unsafe/swapped source parent -> fail closed

    def _rollback_bad_active(self, txn, dest: Path, prev: Path, handle=None) -> str:
        """Post-activation provenance failed: undo the activation via the held FD. `dest` is
        removed ONLY after re-proving it is still our captured candidate/link handle — never a
        pathname-only `rmtree(dest.name)` of an unverified replacement. On identity loss the
        destination, `.prev`, and journal are RETAINED and the outcome is recovery-required.
        Returns 'provenance-blocked' on a proven rollback, else 'recovery-required'."""
        # The active `dest` must still be our captured leaf before we destroy it.
        dest_is_ours = self._verify_staged(txn, handle, dest.name)
        try:
            if txn.leaf_kind(prev.name) != "absent":
                if txn.leaf_kind(dest.name) != "absent":
                    if not dest_is_ours:
                        return "recovery-required"       # unverified active leaf -> RETAIN it
                    txn.rmtree(dest.name)                # drop the (verified) bad candidate
                txn.rename(prev.name, dest.name)         # restore the prior
                txn.fsync()
                return "provenance-blocked" if txn.usable(dest.name) else "recovery-required"
            # Fresh install (no prior to restore): drop the bad candidate ONLY if it is still
            # ours; otherwise retain the unverified destination + journal.
            if txn.leaf_kind(dest.name) != "absent":
                if not dest_is_ours:
                    return "recovery-required"
                txn.rmtree(dest.name)
            txn.fsync()
            return "recovery-required"
        except (OSError, PathContainmentError):
            return "recovery-required"                   # rollback unproven -> retain everything

    def _verify_staged(self, txn, handle, name: str) -> bool:
        """Identity re-check of the staging/active leaf (candidate dir OR link) by NAME."""
        from . import source_fs
        if handle is None:
            return True
        if isinstance(handle, source_fs.LinkHandle):
            return txn.verify_link(handle, name)
        return txn.verify_candidate(handle, name)

    def _activate_held(self, txn, dest: Path, staging: Path, verify_active=None, handle=None) -> str:
        prev = dest.with_name(f".{dest.name}.prev")
        # A pre-existing `.prev` is an UNOWNED orphan (the journal is created EXCLUSIVELY just
        # below, so none exists yet): block rather than blind-remove a prior run's artifact.
        if txn.leaf_kind(prev.name) != "absent":
            return "failed-clean"
        # (1) EXCLUSIVE journal creation (`O_CREAT|O_EXCL|O_NOFOLLOW`) + fsync of the journal
        # and its parent, RETAINING the journal file + parent fds. A journal INJECTED after the
        # absent-preflight (regular/symlink/special/stale) makes the create fail -> block BEFORE
        # any candidate/dest/`.prev` mutation; the injected leaf is preserved for recovery.
        jh = self._create_journal(dest, prev, staging)
        if jh is None:
            return "recovery-required"
        try:
            archived = False
            try:
                # Candidate/link identity BEFORE archiving anything — a substituted staging leaf
                # blocks immediately with dest untouched.
                if not self._verify_staged(txn, handle, staging.name):
                    raise _Substituted()
                # (2) dest -> .prev ; (3) fsync parent ; (4) journal 'prior-archived'.
                if txn.leaf_kind(dest.name) != "absent":
                    txn.rename(dest.name, prev.name)
                    archived = True                     # the prior IS archived now — set BEFORE
                    txn.fsync()                         # the journal write, so a later failure
                    if not self._update_journal(jh, dest, prev, staging, "prior-archived"):
                        raise _JournalLost()
                # TIGHT re-check IMMEDIATELY before promotion (bounded only by kernel rename
                # atomicity): a substituted candidate/link leaf is never promoted.
                if not self._verify_staged(txn, handle, staging.name):
                    raise _Substituted()
                # (5) candidate -> dest ; (6) fsync parent ; (7) journal 'activated'.
                txn.rename(staging.name, dest.name)
                txn.fsync()
                if not self._update_journal(jh, dest, prev, staging, "activated"):
                    raise _JournalLost()
                # POST-rename: the ACTIVE leaf must be exactly our captured candidate/link.
                if not self._verify_staged(txn, handle, dest.name):
                    raise _Substituted()
            except (_Substituted, _JournalLost):
                # A substituted candidate/link leaf OR a lost-ownership journal: RETAIN the
                # substitute + journal as evidence (never remove them); restore the prior where
                # a slot was freed. recovery-required.
                if archived and txn.leaf_kind(dest.name) == "absent":
                    try:
                        txn.rename(prev.name, dest.name)     # dest freed -> restore original
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        pass                                 # unproven restore -> retain all
                return "recovery-required"
            except (OSError, PathContainmentError):
                # Generic activation/journal failure. Restore the prior if we archived one and
                # the dest slot is a NON-usable occupant (e.g. a dangling symlink).
                if archived and not txn.usable(dest.name):
                    if txn.leaf_kind(dest.name) != "absent" and not txn.usable(dest.name):
                        try:
                            txn.rmtree(dest.name)
                        except PathContainmentError:
                            return "recovery-required"
                    try:
                        txn.rename(prev.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        return "recovery-required"
                if archived and not txn.usable(dest.name):
                    return "recovery-required"
                return "failed-clean" if jh["marker"].remove() else "recovery-required"
            # (8) confirm the active source is a USABLE DIRECTORY (held FD), then verify final
            # provenance — which can take time, so a candidate/link swap can occur DURING it.
            if not txn.usable(dest.name):
                return "recovery-required"
            if verify_active is not None and not verify_active():
                # Provenance FAILED -> destructive rollback, but only after re-proving `dest`
                # is still our captured handle (never rmtree an unverified replacement).
                return self._rollback_bad_active(txn, dest, prev, handle)
            # Provenance SUCCEEDED, but re-verify the ACTIVE leaf is STILL our captured
            # candidate/link (a swap during provenance evaluation) BEFORE any `.prev`/journal
            # removal. On mismatch: retain journal + `.prev` + substituted active leaf.
            if not self._verify_staged(txn, handle, dest.name):
                return "recovery-required"
            # (9) remove `.prev` (held FD, re-verified) + fsync parent ; (10) remove the journal
            # (owned-handle: identity re-verified, then parent fsync) ONLY after cleanup.
            if txn.leaf_kind(prev.name) != "absent" and not self._prev_cleanup_ok(txn, prev):
                return "recovery-required"
            txn.fsync()
            return "activated" if jh["marker"].remove() else "recovery-required"
        finally:
            self._close_journal(jh)


    def _clone(self, spec, dest: Path, source: str, remote: str | None = None) -> bool:
        from . import validators
        run = self.system.runner.run
        remote = remote or spec.remote
        # Revalidate the remote IMMEDIATELY before Git — a hand-edited local.toml override
        # (or any runtime-supplied remote) must satisfy the safe remote-URL policy or it is
        # refused here; a malformed remote NEVER reaches `git clone`/`git ls-remote`.
        try:
            remote = validators.remote_url(remote or "", field="remote")
        except validators.ValidationError:
            return False
        if not remote:
            return False
        ok = False
        if source == "dev":
            argv = ["git", "clone", "--depth", "1"]
            if spec.branch:
                argv += ["--branch", spec.branch]
            argv += [remote, str(dest)]
            ok = run(argv, timeout=120.0).returncode == 0 and dest.exists()
        else:
            # full clone so an arbitrary tag/commit can be checked out
            if run(["git", "clone", remote, str(dest)], timeout=240.0).returncode == 0 \
                    and dest.exists():
                if source == "pinned":
                    # P0.5: `pinned` REQUIRES a configured pin and must resolve EXACTLY to
                    # it — never a silent "pinned" adoption of the default branch.
                    if not spec.pin_commit:
                        ok = False
                    else:
                        ok = run(["git", "-C", str(dest), "checkout", spec.pin_commit],
                                 30.0).returncode == 0
                        if ok:
                            head = run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
                            ok = head.returncode == 0 and head.stdout.strip() == spec.pin_commit
                elif source == "stable":
                    # The configured tag, else the latest tag (an independently selected
                    # tag). With NO verifiable tag at all, `stable` fails closed.
                    tag = spec.pin_tag
                    if not tag:
                        desc = run(["git", "-C", str(dest), "describe", "--tags", "--abbrev=0"], 10.0)
                        tag = desc.stdout.strip() if desc.returncode == 0 else ""
                    if not tag:
                        ok = False
                    else:
                        ok = run(["git", "-C", str(dest), "checkout", tag], 30.0).returncode == 0
                        if ok:
                            chk = run(["git", "-C", str(dest), "describe", "--tags",
                                       "--exact-match"], 10.0)
                            ok = chk.returncode == 0 and chk.stdout.strip() == tag
                else:                       # dev
                    ref = spec.branch or ""
                    ok = (not ref or
                          run(["git", "-C", str(dest), "checkout", ref], 30.0).returncode == 0)
        # On failure the CALLER (`_stage_and_activate`) discards the real `staging` leaf via
        # the descriptor-safe path — `dest` here is the controller-pinned `/proc/<pid>/fd/…`
        # path, which is NOT a runtime-root path, so cleaning it here would be a no-op. Leave
        # cleanup to the caller (single, descriptor-relative owner) rather than a misleading
        # local discard.
        return ok

    def _adopt_done(self, action, spec, dest, source_desc: str, source: str = "pinned",
                    signer_diags=()) -> PlanAction:
        probe = probe_source(self.system, spec, str(dest))
        version = probe.version or probe.head[:12]
        # DISPLAY-ONLY provenance status: enforcement already happened INSIDE the durable
        # transaction (`_activate`'s `verify_active`), which rolled back and retained evidence
        # on a mismatch — so reaching here means the active source is provenance-verified. We
        # re-evaluate ONLY to report the status; we never raise a NEW failure here (that would
        # be after `.prev`/journal were already cleared, with no rollback evidence left).
        from . import provenance
        trusted, _diags = provenance.load_trusted_signers(self.config)
        post = provenance.evaluate(self.system.runner, str(dest), spec, source, trusted)
        action.provenance = post.status
        action.status = "done"
        action.detail = f"{source_desc}: {probe.state.value} (version {version}) [provenance: {post.status}]"
        if signer_diags:
            action.detail += " | signer-config: " + "; ".join(signer_diags)
        return action

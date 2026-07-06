"""Runtime-root bootstrap and safe source adoption.

This is the first *mutating* layer, but it is deliberately conservative:

  * bootstrap is idempotent and NEVER overwrites local config or secrets;
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
    "bin", "src", "build", "config", "profiles", "systemd", "state", "logs", "docs",
    "config/secrets",
)

# Heavy, regenerable directories we skip when adopting a local checkout — and that never
# count as "local changes" in the destructive-operation dirty check (they are build/runtime
# artifacts LHPC itself regenerates).
_ADOPT_IGNORE_NAMES = (
    ".pio", ".venv", "build", ".work", ".run", "__pycache__", "node_modules",
)
_ADOPT_IGNORE = shutil.ignore_patterns(*_ADOPT_IGNORE_NAMES)


@dataclass(frozen=True)
class DirtyReport:
    """Local changes that a destructive source operation would discard. `tracked` =
    modified/staged/deleted tracked files; `untracked` = non-ignored untracked files EXCLUDING
    LHPC-regenerable artifacts (`_ADOPT_IGNORE_NAMES` + every consumer component's declared
    built binary). Either list non-empty => the tree is dirty for update/uninstall purposes."""
    tracked: tuple = ()
    untracked: tuple = ()

    def __bool__(self) -> bool:
        return bool(self.tracked or self.untracked)

    def lines(self, limit: int = 8) -> list:
        out = [f"    modified: {p}" for p in self.tracked[:limit]]
        out += [f"    untracked: {p}" for p in self.untracked[:limit]]
        hidden = max(0, len(self.tracked) - limit) + max(0, len(self.untracked) - limit)
        if hidden:
            out.append(f"    … and {hidden} more")
        return out


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
    kind: str                 # mkdir | config | secret | adopt | clone | verify
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

    def _needs_harden(self, d: Path) -> bool:
        """True if the directory exists but is group/other-writable (mode has 0o022 set)
        — the identity/security boundary wants the runtime root owner-only (0700)."""
        try:
            return bool(d.is_dir() and (d.stat().st_mode & 0o022))
        except OSError:
            return False

    def plan_bootstrap(self) -> Plan:
        plan = Plan(title=f"Bootstrap runtime root {self.paths.runtime_root}")
        for name in RUNTIME_SUBDIRS:
            d = self.subdir(name)
            plan.actions.append(PlanAction(
                "mkdir", str(d), f"create {name}/",
                status="exists" if d.is_dir() else "planned"))
        # HARDEN the runtime root (and src/, the controller-checkout parent) to 0700 AFTER
        # the dirs exist — the documented security boundary behind the controller-identity
        # proof. Default umask makes fresh dirs group-writable (0775), which surfaces as
        # "identity UNSAFE: runtime root is group/other-writable"; enforcing it here fixes
        # it at install time (idempotent — re-bootstrap tightens an existing loose root).
        root = self.paths.runtime_root
        plan.actions.append(PlanAction(
            "harden", str(root), "restrict runtime root to 0700 (owner-only)",
            status="planned" if (not root.is_dir() or self._needs_harden(root)) else "exists"))
        src = self.subdir("src")
        plan.actions.append(PlanAction(
            "harden", str(src), "restrict src/ to 0700 (owner-only)",
            status="planned" if (not src.is_dir() or self._needs_harden(src)) else "exists"))
        # In a SELF-HOSTED deployment the controller's own checkout lives at
        # src/loraham-pi-control (git clone leaves it group-writable under a 0002 umask,
        # which trips "identity UNSAFE: checkout is group/other-writable"). Harden it too
        # when present — a no-op for a non-self-hosted root where it does not exist.
        checkout = src / "loraham-pi-control"
        if checkout.is_dir():
            plan.actions.append(PlanAction(
                "harden", str(checkout), "restrict the controller checkout to 0700 (owner-only)",
                status="planned" if self._needs_harden(checkout) else "exists"))
        # Local config + secrets: create from templates only if absent.
        local = self.subdir("config") / "local.toml"
        plan.actions.append(PlanAction(
            "config", str(local), "write config/local.toml (operator settings)",
            status="exists" if local.exists() else "planned"))
        secret = self.subdir("config") / "secrets.toml"
        plan.actions.append(PlanAction(
            "secret", str(secret), "write config/secrets.toml (0600)",
            status="exists" if secret.exists() else "planned"))
        # Manual start wrappers are RETIRED: lhpc starts services itself and the
        # dashboard shows interactive components' copy-paste commands (rendered from
        # the same structured spec) — a wrapper-started service would bypass LHPC
        # ownership. Legacy wrappers are pruned on bootstrap.
        start_dir = self.subdir("start")
        if start_dir.is_dir():
            for existing in sorted(start_dir.glob("*-start")):
                plan.actions.append(PlanAction(
                    "prune-wrapper", str(existing),
                    f"remove legacy start/{existing.name}"))
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
        elif action.kind == "harden":
            # Owner-only 0700 on the runtime root / src (the controller-identity boundary).
            # No-follow: refuse to chmod through a symlink leaf that leaves the root.
            target = Path(action.target)
            if target.is_symlink():
                action.status, action.detail = "failed", "refusing to chmod a symlink"
            else:
                os.chmod(target, 0o700)
                action.status = "done"
                action.detail = "mode 0700"
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
        elif action.kind == "prune-wrapper":
            runtime_fs.unlink(self.paths, Path(action.target))
            action.status = "done"

    # -- source adoption ---------------------------------------------------

    def plan_install(self, stack_id: str | None = None) -> Plan:
        from . import source_fs
        plan = Plan(title="Install (adopt/verify sources)")
        for stack in self.stacks:
            if stack_id and stack.id != stack_id:
                continue
            for comp in stack.components:
                if comp.source is None:
                    continue
                dest = self.paths.resolve_source(comp.source.path)
                try:
                    kind = source_fs.leaf_kind(self.paths, dest)   # no-follow, never exists()
                except PathContainmentError:
                    kind = "special"                               # unsafe parent -> not adoptable
                if kind in ("dir", "symlink"):
                    probe = probe_source(self.system, comp.source, str(dest))
                    plan.actions.append(PlanAction(
                        "verify", str(dest),
                        f"{comp.id}: source present ({probe.state.value})",
                        status="exists", detail=probe.state.value))
                elif kind == "absent":
                    plan.actions.append(PlanAction(
                        "adopt", str(dest),
                        f"{comp.id}: adopt {comp.source.adopt_dir} -> {comp.source.path}"))
                else:
                    plan.actions.append(PlanAction(
                        "verify", str(dest),
                        f"{comp.id}: destination is a {kind} leaf — not an installable "
                        "destination (resolve manually)", status="failed", detail=kind))
        return plan

    def adopt_source(self, comp: Component, *, force: bool = False,
                     source: str = "pinned", pinned_expected: tuple = None,
                     locked: bool = False) -> PlanAction:
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
        from . import reslock, source_fs
        # HARD PREREQUISITE: the atomic no-clobber rename primitive. Without it the
        # activation protocol cannot exclude clobbering races — refuse BEFORE any journal,
        # candidate, source, or registry change (no check-then-plain-rename fallback).
        unavailable = source_fs.require_atomic_rename()
        if unavailable:
            action.status, action.detail = "failed", unavailable
            return action
        if locked:
            # OUTER-HELD OPERATION: the caller's source-operation guard already performed the
            # index-lock -> recovery -> journal-block -> source-path-lock handoff and STILL
            # HOLDS the index-successor source locks for every affected path — re-acquiring
            # them here would self-contend. Mutate directly under the caller's boundary.
            return self._adopt_locked(comp, spec, dest, action, force, source,
                                      pinned_expected)
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
                    return self._adopt_locked(comp, spec, dest, action, force, source,
                                              pinned_expected)
        except reslock.ResourceBusy as busy:
            action.status, action.detail = "failed", f"another source operation is in progress: {busy}"
            return action

    def _adopt_locked(self, comp, spec, dest, action, force, source,
                      pinned_expected: tuple = None):
        # Index + target source-path locks are held by the caller. Recovery + the global
        # blocking decision already ran under the index lock.
        from . import runtime_fs, source_fs, source_registry
        # DESCRIPTOR-PROVEN destination state: only a no-follow-proven ABSENT leaf is an
        # installable empty destination. `Path.exists()` (which follows symlinks) is never a
        # mutation authority here.
        try:
            runtime_fs.ensure_dir(self.paths, dest.parent)   # descriptor-anchored, no-follow
            kind = source_fs.leaf_kind(self.paths, dest)
        except (OSError, PathContainmentError) as exc:
            action.status, action.detail = "failed", str(exc)
            return action
        # RUNTIME-PROBED atomic-rename capability for THIS source parent's filesystem —
        # a libc symbol alone is not a precondition. Refused BEFORE candidate/journal/
        # source/registry mutation; never a plain-rename fallback.
        unavailable = source_fs.require_atomic_rename(self.paths, dest.parent)
        if unavailable:
            action.status, action.detail = "failed", unavailable
            return action
        had_prior = kind != "absent"
        if kind == "symlink":
            # A symlink leaf is legitimate ONLY for the declared link strategy — and a linked
            # source is never updated in place (skip). Any other symlink (dangling, unknown,
            # injected) is NOT an installable destination: refuse with zero mutation.
            if (spec.strategy or "") == "link":
                # FROZEN BULK IDENTITY: even the leave-as-is skip must prove the linked
                # tree is exactly at the frozen commit — a moved external checkout is a
                # refusal, never a silent success under a frozen plan.
                if pinned_expected is not None and pinned_expected[0]:
                    head = self.system.runner.run(
                        ["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
                    if head.returncode != 0 or \
                            head.stdout.strip() != pinned_expected[0]:
                        action.status = "failed"
                        action.detail = (
                            "linked tree is not at the bulk-frozen commit "
                            f"{pinned_expected[0][:9]} — refusing (frozen plan is "
                            "authoritative; update the external checkout)")
                        return action
                    action.status, action.detail = "skipped", (
                        "linked dev tree — left as-is (exact bulk-frozen commit "
                        f"{pinned_expected[0][:9]} verified)")
                    return action
                action.status, action.detail = "skipped", "linked dev tree — left as-is"
                return action
            action.status = "failed"
            action.detail = ("destination is an unexpected symlink leaf — not an LHPC "
                             "adoption; refusing (nothing renamed or deleted)")
            return action
        if kind in ("file", "special"):
            # A regular or special file at a managed source destination is never installable
            # and never LHPC's to remove.
            action.status = "failed"
            action.detail = (f"destination is a {kind} leaf, not a managed source directory — "
                             "refusing (nothing renamed or deleted)")
            return action
        if kind == "dir" and not force:
            action.status, action.detail = "skipped", "destination already exists"
            return action
        prior = None
        try:
            if kind == "dir" and force:
                # UPDATE of an existing source: CAPTURE the leaf first (retained no-follow
                # fd), then prove CURRENT ownership + identity and cleanliness AGAINST THE
                # CAPTURED INODE (fd-pinned path) — so the leaf later archived at the
                # irreversible rename is exactly the leaf that was verified; an external
                # substitution in between is detected, never archived or destroyed.
                try:
                    prior = source_fs.capture_leaf(self.paths, dest)
                except (OSError, PathContainmentError) as exc:
                    action.status, action.detail = "failed", f"could not capture leaf: {exc}"
                    return action
                rec, why = source_registry.verify_identity(
                    self.paths, self.system, self.config, comp, dest,
                    components=self._path_consumers(spec.path), handle=prior)
                if rec is None:
                    action.status = "failed"
                    action.detail = f"ownership/identity not proven — not overwritten: {why}"
                    return action
                # Never overwrite a working tree with local modifications — TRACKED changes
                # AND non-ignored, non-artifact UNTRACKED files: staging into it would be
                # silent data loss. Checked on the CAPTURED inode.
                dirty = self.dirty_report(Path(prior.pinned_path()), spec.path)
                if dirty:
                    action.status = "failed"
                    action.detail = ("local modifications present — not overwritten:\n"
                                     + "\n".join(dirty.lines()))
                    return action

            # CONTAINMENT: the local-adoption fallback is DISABLED unless configured,
            # and a configured root must lie INSIDE the runtime root — LHPC never reads
            # an outside-root tree. `local=None` means "no fallback exists at all".
            raw_search = str(self.config.get("install", "adopt_search_root", "")).strip()
            local = None
            if raw_search:
                search = Path(raw_search).expanduser()
                if not self.paths.contains(search):
                    action.status, action.detail = "failed", (
                        f"adopt_search_root {raw_search!r} escapes the runtime root — "
                        "refusing (LHPC never touches anything outside the root)")
                    return action
                local = search / spec.adopt_dir
            strategy = spec.strategy or self.config.get("install", "source_strategy", "adopt")
            # The INDEX + SOURCE-PATH locks are already held by adopt_source across candidate
            # creation, verification, activation, and cleanup.
            return self._stage_and_activate(comp, source, action, dest, spec, local, strategy,
                                            had_prior=had_prior, prior=prior,
                                            pinned_expected=pinned_expected)
        finally:
            if prior is not None:
                prior.close()

    def _pinned_expected(self, comp) -> tuple:
        """What the 'Known working' (pinned) selector must resolve this component to:
        `(commit, label)`. Resolution is STACK-LEVEL: the single newest COMPLETE composition
        compatible with the current manifest/config identity (`compatible_composition`)
        supplies the commit for EVERY component of the stack; when none qualifies, every
        component takes `("", "fallback…")` — the manifest pin, clearly labelled, never a
        mix of known-working and fallback commits."""
        from . import known_working
        spec = comp.source
        if spec is None or spec.artifact:
            return "", ""
        stack = next((s for s in self.stacks for c in s.components if c.id == comp.id), None)
        entries = None
        if stack is not None:
            entries = known_working.compatible_composition(
                self.paths, stack,
                lambda c: self.config.remotes.get(c.id) or (c.source.remote if c.source
                                                            else "") or "")
        if entries and comp.id in entries:
            return (entries[comp.id]["commit"],
                    "known working (operator-confirmed composition)")
        return "", "fallback: manifest pin — no known-working record"

    def _stage_and_activate(self, comp: Component, source: str, action: "PlanAction",
                            dest: Path, spec, local: "Path | None", strategy: str,
                            had_prior: bool = False, prior=None,
                            pinned_expected: tuple = None) -> "PlanAction":
        """Create, verify, and atomically activate a candidate under ONE held source-parent
        FD spanning journal preflight → exclusive candidate creation → staging → candidate
        provenance → activation → active-source provenance → rollback/cleanup. The active
        source is touched only at the final rename, so any failure leaves it untouched. Git,
        copy, and provenance receive ONLY the controller-pinned `/proc/<pid>/fd/<held>/<name>`
        path — never a runtime pathname that a parent swap could redirect. `had_prior`
        (descriptor-proven by the caller) rides in the journal so RECOVERY can distinguish
        an update (roll back to `.prev`) from a fresh install (remove the candidate) when the
        ownership record cannot be persisted."""
        import time as _time
        from . import source_fs, provenance
        staging = dest.with_name(f".{dest.name}.candidate-{os.getpid()}-{_time.monotonic_ns()}")
        trusted, signer_diags = provenance.load_trusted_signers(self.config)
        # 'Known working' resolution: a FROZEN per-operation plan value when the caller
        # planned the whole install/update up front (`pinned_expected`) — a concurrent
        # operator confirmation cannot alter an already-planned operation — else resolved
        # here (single-component adoption).
        if pinned_expected is not None and pinned_expected[0]:
            # FROZEN plan identity (bulk): one exact immutable commit resolved at plan
            # time — used verbatim for EVERY selector; no second selector lookup here.
            expected, kw_label = pinned_expected
        elif source == "pinned":
            expected, kw_label = (pinned_expected if pinned_expected is not None
                                  else self._pinned_expected(comp))
        else:
            expected, kw_label = "", ""
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
                                                     local, strategy, action,
                                                     expected_pin=expected)
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
                pre = provenance.evaluate(self.system.runner, pre_pinned, spec, source, trusted,
                                          expected_commit=expected)
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
                        self.system.runner, p, spec, source, trusted,
                        expected_commit=expected).ok
                # Ownership metadata rides in the journal (v3) so the registry record is part
                # of the SAME durable transaction: written after the activation rename, and
                # completable by recovery from the journal alone.
                meta = self._txn_meta(comp, spec, source, strategy, pre_pinned)
                meta["had_prior"] = bool(had_prior)
                if is_link:
                    # durable link identity: the EXACT runtime symlink target
                    meta["link_target"] = handle.target
                # FINAL dirty recheck, run immediately before the prior is archived: a
                # tracked or non-ignored untracked file created AFTER the initial check
                # (e.g. while the candidate was cloning/building) must block the archive.
                final_dirty = (
                    (lambda: self.dirty_report(Path(prior.pinned_path()), spec.path))
                    if prior is not None and prior.kind == "dir" else None)
                outcome = self._activate_held(txn, dest, staging, verify_active=_post_ok,
                                              handle=handle, meta=meta, prior=prior,
                                              final_dirty=final_dirty)
                if outcome == "substituted":
                    # An EXTERNAL process replaced the destination leaf between verification
                    # and the irreversible step: nothing of the substitute was archived,
                    # replaced, or deleted — the staging candidate (ours) was discarded.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status = "failed"
                    action.detail = ("destination was concurrently replaced — refusing "
                                     "(the substituted content is untouched; re-run the "
                                     "update after inspecting it)")
                    return action
                if outcome == "injected":
                    # A leaf APPEARED at the destination after absence was observed (fresh
                    # install) or after the prior was archived (update): never overwritten.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status = "failed"
                    action.detail = ("a leaf appeared at the destination during activation — "
                                     "refusing to overwrite it (injected content untouched)")
                    return action
                if outcome == "prior-dirty":
                    # The NEW source IS active and its ownership record is coherent — but
                    # the archived prior gained late local changes and is RETAINED with the
                    # journal (operator recovery required; never auto-deleted).
                    action.status = "failed"
                    action.provenance = ""
                    action.detail = (
                        "prior-dirty: the update activated the new source, but the archived "
                        f"prior at {self._source_rel(dest.with_name('.' + dest.name + '.prev'))} "
                        "gained late local changes — it is RETAINED with the transaction "
                        "journal (inspect/salvage, then remove the .prev directory and the "
                        "journal manually; automatic recovery will not delete it)")
                    return action
                if outcome == "dirty":
                    # The owned candidate is discarded ONLY through its bound identity.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status = "failed"
                    action.detail = ("local modifications appeared during staging — the "
                                     "prior source is intact at its original path (nothing "
                                     "was overwritten); commit/stash or remove the new "
                                     "files and retry")
                    return action
                if outcome == "provenance-blocked":
                    post = provenance.evaluate(self.system.runner,
                                               txn.child_pinned_path(dest.name), spec, source,
                                               trusted, expected_commit=expected)
                    action.status, action.provenance = "failed", post.status
                    action.detail = ("post-activation provenance mismatch — rolled back; "
                                     "the new version was NOT adopted")
                    return action
                if outcome == "registry-blocked":
                    action.status = "failed"
                    action.detail = ("ownership record could not be persisted — rolled back "
                                     "(prior source and its record intact; a fresh install was "
                                     "fully undone); the new version was NOT adopted")
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
                return self._adopt_done(action, spec, dest, desc, source, signer_diags,
                                        expected=expected, kw_label=kw_label)
        except PathContainmentError:
            action.status, action.detail = "failed", (
                "managed source parent is unsafe (symlinked/swapped) — active source untouched")
            return action

    def _cleanup_owned_staging(self, txn, handle, staging_name: str) -> str:
        """THE authoritative handle-safe staging cleanup. Removes the staging leaf ONLY when it
        still matches its `CandidateHandle`/`LinkHandle`; a substituted replacement is RETAINED
        as evidence, never recursively deleted merely because it kept the expected name. Returns
        'removed' | 'absent' | 'identity-lost'."""
        from . import source_fs
        if txn.leaf_kind(staging_name) == "absent":
            return "absent"
        if not self._verify_staged(txn, handle, staging_name):
            return "identity-lost"                    # substituted -> retain, never delete
        if handle is None:
            return "identity-lost"                    # no identity evidence -> retain
        source_fs.race_seam("pre-staging-delete", staging_name)
        ok, _why = source_fs.remove_bound(txn.fd, staging_name,
                                          [handle.st_dev, handle.st_ino])
        if not ok:
            return "identity-lost"                    # removal not provable -> retain
        return "removed"

    def _stage_candidate(self, txn, comp, source: str, dest: Path, staging: Path, spec,
                         local: "Path | None", strategy: str, action,
                         expected_pin: str = ""):
        """Stage the candidate through the held transaction. Returns `(desc, handle)` — a
        description plus the `CandidateHandle` (a retained FD on the candidate dir; None for
        the link strategy, whose leaf is a symlink). On failure returns `(None, None)` with a
        typed failure recorded on `action`. Git/copy write ONLY through the candidate FD-pinned
        path (`handle.pinned_path()`), never the mutable candidate leaf name."""
        if strategy == "link":
            if local is None:
                action.status, action.detail = "failed", (
                    "link strategy requires a configured IN-ROOT adopt_search_root — "
                    "no local checkout configured")
                return None, None
            if not local.is_dir():
                action.status, action.detail = "failed", f"local checkout not found: {local}"
                return None, None
            # A linked checkout must STILL satisfy the requested version (same policy as
            # copy/clone) — never report a version-selected adoption it cannot prove.
            if not self._fallback_satisfies(spec, local, source, expected_pin):
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
        if remote and self._clone(spec, Path(handle.pinned_path()), source, remote,
                                  expected_pin=expected_pin):
            return f"GitHub {source}", handle
        # Clone failed (or no remote) -> reset the (intact controller-owned) candidate via the
        # held FD, then try the local fallback. If the candidate was SUBSTITUTED, do NOT delete
        # the replacement and do NOT recreate a candidate through this flow — fail closed.
        if self._cleanup_owned_staging(txn, handle, staging.name) == "identity-lost":
            action.status, action.detail = "failed", (
                "recovery-required: staging candidate was substituted (evidence retained)")
            return None, None
        handle = txn.create_candidate(staging.name)

        def _unavailable(why: str) -> str:
            # `dev` NEVER silently uses a different ref: when the configured branch cannot be
            # obtained (clone failed, local fallback not on it), the SELECTOR is unavailable.
            if source == "dev" and spec.branch and not spec.artifact:
                return (f"selector unavailable: branch {spec.branch!r} could not be obtained "
                        f"({why}) — active source untouched")
            return f"{why} — active source untouched"

        if local is not None and local.is_dir():
            if not self._fallback_satisfies(spec, local, source, expected_pin):
                self._cleanup_owned_staging(txn, handle, staging.name)   # drop empty candidate
                action.status = "failed"
                action.detail = _unavailable(
                    "GitHub clone failed and the local checkout does not satisfy the "
                    f"requested {source} version")
                return None, None
            try:
                self._copy_into_candidate(local, handle.pinned_path())
            except OSError as exc:
                self._cleanup_owned_staging(txn, handle, staging.name)   # drop partial copy
                action.status, action.detail = "failed", f"{exc} (active source untouched)"
                return None, None
            return "local fallback", handle
        self._cleanup_owned_staging(txn, handle, staging.name)           # drop empty candidate
        action.status = "failed"
        action.detail = _unavailable("GitHub clone failed and no local checkout")
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

    def _fallback_satisfies(self, spec, local: Path, source: str,
                            expected_pin: str = "") -> bool:
        """A local-fallback / linked checkout may activate only if it PROVABLY satisfies
        the requested version — fail closed:
          * an ARTIFACT source is the same declared artifact for every selector — any local
            copy of it satisfies (there are no version semantics to prove);
          * `pinned` REQUIRES an exact expected commit (the known-working composition entry
            when one exists, else the configured manifest pin) AND HEAD == it;
          * `stable` REQUIRES a configured tag AND the checkout is exactly at that tag
            ("newest" cannot be proven offline — documented conservative fallback);
          * `dev` requires the configured branch if one is set; with no branch this is the
            documented permissive policy (dev = whatever the operator's tree is on).
        A version-selected request whose selector is not configured can never be proven,
        so it is rejected rather than reported as a successful selected adoption."""
        run = self.system.runner.run
        if expected_pin:
            # FROZEN BULK IDENTITY: link/copy/local fallback may activate ONLY at exactly
            # the frozen commit, for EVERY selector — branch/tag/artifact shortcuts never
            # substitute. A non-Git tree has no verifiable identity: refuse.
            head = run(["git", "-C", str(local), "rev-parse", "HEAD"], 5.0)
            return head.returncode == 0 and head.stdout.strip() == expected_pin
        if spec.artifact:
            return True
        if source == "pinned":
            pin = expected_pin or spec.pin_commit
            if not pin:                           # no provable expectation -> cannot prove
                return False
            head = run(["git", "-C", str(local), "rev-parse", "HEAD"], 5.0)
            return head.returncode == 0 and head.stdout.strip() == pin
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

    def _path_bins(self, source_path: str) -> set:
        """Every consumer component's declared built-binary path inside `source_path` — these
        are LHPC-regenerated artifacts, never operator changes."""
        return {c.bin for stack in self.stacks for c in stack.components
                if c.source and c.source.path == source_path and c.bin}

    def dirty_report(self, dest: Path, source_path: str) -> DirtyReport:
        """Local changes a destructive operation (update overwrite / uninstall) would discard:
        TRACKED modifications AND non-ignored UNTRACKED files — `--untracked-files=normal`
        honours .gitignore, and LHPC-regenerable artifacts (`_ADOPT_IGNORE_NAMES` dirs + every
        consumer's declared `bin`) are excluded so a built tree stays updatable. A tree that is
        not a git checkout reports clean here (ownership verification handles unknown trees).
        A FAILED git status reports the failure as a tracked entry — fail toward dirty, never
        silently clean."""
        if not (dest / ".git").exists():
            return DirtyReport()
        # NUL-SAFE, ENTRY-EXACT status: `-z` terminates every path with NUL (no quoting, so
        # newline/quote-containing names parse exactly), and `--untracked-files=all`
        # enumerates every INDIVIDUAL untracked file — git never collapses a directory, so
        # the generated-binary carve-out can only ever match the exact declared leaf, never
        # a parent directory that also shelters unknown sibling/nested files.
        r = self.system.runner.run(["git", "-C", str(dest), "status", "--porcelain", "-z",
                                     "--untracked-files=all"], 10.0)
        if r.returncode != 0:
            return DirtyReport(tracked=("(git status failed — treating as dirty)",))
        bins = self._path_bins(source_path)

        def _is_artifact(path: str) -> bool:
            # regenerable dirs (build/, .pio/, …) by first segment, or the EXACT declared
            # generated leaf — nothing else (siblings/nested files under bin's parent block)
            return path.split("/", 1)[0] in _ADOPT_IGNORE_NAMES or path in bins

        tracked, untracked = [], []
        fields = (r.stdout or "").split("\0")
        i = 0
        while i < len(fields):
            entry = fields[i]
            i += 1
            if len(entry) < 4:
                continue
            status, path = entry[:2], entry[3:]
            if status[0] in ("R", "C"):
                i += 1                                     # rename/copy carries a second field
            if status == "??":
                if _is_artifact(path):
                    continue                               # regenerable artifact — not a change
                untracked.append(path)
            else:
                tracked.append(path)
        return DirtyReport(tracked=tuple(tracked), untracked=tuple(untracked))

    # -- source ownership registry (transactional with activation) ----------

    def _path_consumers(self, source_path: str) -> tuple:
        """Every manifest component id consuming `source_path` (the shared-checkout set)."""
        out = []
        for stack in self.stacks:
            for c in stack.components:
                if c.source and c.source.path == source_path:
                    out.append(c.id)
        return tuple(out)

    def _txn_meta(self, comp, spec, source: str, strategy: str, git_path: str) -> dict:
        """The ownership metadata carried by the v3 journal — the AUTHORITY recovery uses to
        complete the registry record. `git_path` points at the staged tree (candidate FD-pinned
        path, or a link's external target)."""
        head = self.system.runner.run(["git", "-C", git_path, "rev-parse", "HEAD"], 5.0)
        return {
            "selector": source,
            "resolved_commit": (head.stdout or "").strip() if head.returncode == 0 else "",
            "remote": self.config.remotes.get(comp.id) or spec.remote or "",
            "strategy": strategy if strategy == "link" else (spec.strategy or ""),
            # LIVE membership merge: an updated shared checkout factually serves every
            # DECLARED consumer again, PLUS whoever the existing record already lists —
            # a departure (uninstall of one sharer) survives unrelated re-adopts only
            # until the checkout is genuinely refreshed for everyone.
            "components": sorted(set(self._path_consumers(spec.path))
                                 | self._record_members(spec.path)),
            "link_target": "",           # set by the caller for link-strategy staging
        }

    def _record_members(self, source_rel: str) -> set:
        from . import source_registry
        state, rec, _why = source_registry.record_state(self.paths, source_rel)
        return set(rec.components) if state == "valid" else set()

    @staticmethod
    def _valid_meta(meta) -> bool:
        """Strict validation of a v3 journal's ownership metadata (untrusted persisted input).
        `had_prior` (update vs fresh-install evidence for recovery rollback) must be a bool
        when present; an older v3 journal without it stays valid (recovery then treats the
        transaction conservatively, as an update)."""
        if not isinstance(meta, dict):
            return False
        for f in ("selector", "resolved_commit", "remote", "strategy"):
            if not isinstance(meta.get(f), str):
                return False
        if meta["selector"] not in ("pinned", "dev", "stable", "legacy"):
            return False
        if "had_prior" in meta and not isinstance(meta["had_prior"], bool):
            return False
        if "link_target" in meta and not isinstance(meta["link_target"], str):
            return False
        comps = meta.get("components")
        return isinstance(comps, list) and all(isinstance(c, str) and c for c in comps)

    def _write_registry_record(self, dest: Path, meta: dict, txn_id: str) -> bool:
        """Persist the ownership record for an activated source from journal metadata.
        Called INSIDE the activation transaction (before journal removal) and again by
        RECOVERY when completing an interrupted activation. Returns False on failure —
        the caller must then RETAIN the journal (never report an un-owned activation)."""
        import time as _time
        from . import source_registry
        return source_registry.write_record(self.paths, source_registry.RegistryRecord(
            source_rel=self._source_rel(dest), remote=meta["remote"],
            selector=meta["selector"], resolved_commit=meta["resolved_commit"],
            adopted_at=_time.time(), txn_id=txn_id, strategy=meta["strategy"],
            components=tuple(meta["components"]),
            link_target=meta.get("link_target", "")))

    # -- source activation transaction (durable + recoverable) -------------

    # -- source activation transaction (durable, strictly-trusted journal) --
    #
    # The journal NEVER stores trusted absolute paths. It records logical, validated
    # RUNTIME-RELATIVE names; recovery derives the real paths from the runtime root and
    # rejects anything that is absolute, escaping, symlinked, or that does not match the
    # controller's candidate/prior naming patterns. An invalid journal is RETAINED and
    # blocks the affected source — it is never followed or deleted blindly.

    _VALID_STATES = ("planned", "prior-archived", "activated", "prior-dirty-retained")

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
                         txn_id: str, meta: dict | None = None,
                         idents: dict | None = None) -> str:
        """v4 journal: carries the OWNERSHIP metadata (`meta`) AND strict leaf-identity
        evidence (`idents`: no-follow [dev, ino] for the CANDIDATE and the archived PRIOR),
        so crash recovery can re-prove the exact leaves before any destructive step —
        candidate promotion, prior restore, and prior cleanup all verify identity first.
        `meta=None` renders a v2-shaped payload; meta-without-idents renders v3 (both are
        still recoverable, with the older journals' destructive steps degraded to the
        pre-identity checks — never an unsafe automatic cleanup)."""
        import json
        version = 2 if meta is None else (4 if idents is not None else 3)
        payload = {
            "version": version, "state": state,
            "source_rel": self._source_rel(dest),
            "prev_rel": self._source_rel(prev),
            "candidate_rel": self._source_rel(staging),
            "txn_id": txn_id,
        }
        if meta is not None:
            payload["meta"] = meta
        if idents is not None:
            payload["idents"] = idents
        return json.dumps(payload)

    def _create_journal(self, dest: Path, prev: Path, staging: Path, meta: dict | None = None,
                        idents: dict | None = None):
        """EXCLUSIVELY create the initial (`planned`) journal (`O_CREAT|O_EXCL|O_NOFOLLOW`,
        fsync'd) and RETAIN its file + parent fds. Returns a journal handle
        `{marker: OwnedMarker, txn_id, path, meta, idents}`, or None if ANY journal leaf
        already exists (injected after preflight, or stale) — the caller then returns
        recovery-required WITHOUT touching candidate/dest/`.prev`. The caller MUST close it."""
        from . import runtime_fs
        jp = self._journal_path(dest)
        txn_id = self._txn_id(self._source_rel(staging))
        try:
            marker = runtime_fs.open_marker_excl(
                self.paths, jp,
                self._journal_payload(dest, prev, staging, "planned", txn_id, meta, idents))
        except (FileExistsError, OSError, PathContainmentError):
            return None
        return {"marker": marker, "txn_id": txn_id, "path": jp, "meta": meta,
                "idents": idents}

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
            self._journal_payload(dest, prev, staging, state, jh["txn_id"], jh.get("meta"),
                                  jh.get("idents")))

    @staticmethod
    def _valid_idents(idents) -> bool:
        """Strict validation of v4 leaf-identity evidence (untrusted persisted input)."""
        if not isinstance(idents, dict):
            return False
        for key in ("candidate", "prev"):
            v = idents.get(key)
            if v is None:
                continue
            if (not isinstance(v, list) or len(v) != 2
                    or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
                return False
        return True

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
                if j.get("version") not in (2, 3, 4) or j.get("state") not in self._VALID_STATES:
                    raise ValueError("bad version/state")
                meta = None
                idents = None
                if j.get("version") == 4:
                    meta = j.get("meta")
                    if not self._valid_meta(meta):
                        raise ValueError("bad ownership metadata")
                    idents = j.get("idents")
                    if not self._valid_idents(idents):
                        raise ValueError("bad leaf-identity evidence")
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
            # PRIOR-DIRTY RETENTION: a transaction explicitly marked prior-dirty-retained
            # holds an archived `.prev` containing LATE LOCAL CHANGES. Automatic recovery
            # NEVER retries its deletion — the journal and `.prev` stay until the operator
            # inspects/salvages the changes and removes them manually.
            if j.get("state") == "prior-dirty-retained":
                return (f"recovery-required: {self._source_rel(dest)} finished activating, "
                        f"but its archived prior at {self._source_rel(prev)} contains late "
                        "local changes — retained for the OPERATOR (inspect/salvage, then "
                        "remove the .prev directory and this journal manually); automatic "
                        "recovery will not delete it")
            # GENERATIONAL FAIL-CLOSED: only a v4 journal carries the complete, current
            # leaf-identity evidence automatic recovery requires. Structurally-valid v2/v3
            # journals are NEVER silently upgraded into authority for destructive work —
            # every leaf and the journal are retained with a truthful operator diagnostic.
            if j.get("version") in (2, 3):
                return (f"recovery-required: journal {jf.name} is generation "
                        f"v{j['version']} (no leaf-identity evidence) — automatic recovery "
                        "refused; all leaves and the journal are retained. Inspect "
                        f"{self._source_rel(dest)} and its .prev/candidate siblings "
                        "manually, then remove the journal and re-adopt/update the source.")
            try:
                with reslock.operation_lock(self.paths,
                                            self._source_lock_key(self._source_rel(dest)),
                                            "recover", dest.name):
                    return self._finish_or_rollback(dest, prev, staging, marker,
                                                    meta=meta, txn_id=j["txn_id"],
                                                    idents=idents)
            except reslock.ResourceBusy:
                return f"recovery-required: source {dest.name} is busy (retained)"
        finally:
            marker.close()

    def _prev_dirty_scan(self, txn, dest: Path, prev: Path, prev_ident=None):
        """FINAL dirty scan of the archived prior, BOUND to its leaf: capture the `.prev`
        leaf no-follow, prove its identity (v4 evidence when available), and scan through
        the captured fd-pinned path. Returns True (dirty — late tracked/non-ignored
        untracked changes), False (clean / not dirty-capable), or None (unprovable —
        the caller retains everything)."""
        from . import source_fs
        try:
            if txn.leaf_kind(prev.name) != "dir":
                return False                       # symlink/absent prior: nothing scannable
            h = txn.capture_leaf(prev.name)
        except (OSError, PathContainmentError):
            return None
        try:
            if prev_ident is not None and [h.st_dev, h.st_ino] != list(prev_ident):
                return None                        # substituted -> existing retention path
            return bool(self.dirty_report(Path(h.pinned_path()), self._source_rel(dest)))
        finally:
            h.close()

    def _prev_cleanup_ok(self, txn, prev: Path, ident=None) -> bool:
        """Remove the archived `.prev` — IDENT-BOUND ONLY. `.prev` is the transaction's own
        quarantine (atomically detached from dest with identity proof at archive time); its
        deletion binds to the recorded (dev, ino) through content removal and re-proves it
        before the final rmdir. WITHOUT identity evidence nothing is deleted (the caller
        retains `.prev` + journal); an ABSENT `.prev` is already-clean; a substituted one
        is retained untouched."""
        from . import source_fs
        if txn.leaf_kind(prev.name) == "absent":
            return True
        if ident is None:
            return False                           # no identity evidence -> RETAIN
        source_fs.race_seam("pre-prev-delete", prev.name)
        ok, _why = source_fs.remove_bound(txn.fd, prev.name, ident)
        if not ok:
            return False                           # substituted/unprovable -> RETAIN
        return txn.leaf_kind(prev.name) == "absent"

    def _finish_or_rollback(self, dest: Path, prev: Path, staging: Path, marker,
                            meta: dict | None = None, txn_id: str = "",
                            idents: dict | None = None) -> str:
        """Resolve one validated journal under ONE held source-parent FD across verification,
        rename, and cleanup. The journal is removed (via the OWNED `marker`, identity re-
        verified) ONLY once the active source is proven USABLE (via the held FD), the archived
        prior is proven removed, AND — for a v3 journal — the OWNERSHIP RECORD is completed.
        Any uncertainty — including a journal replaced after validation but before removal —
        RETAINS the journal + candidate/prior evidence and yields recovery-required."""
        from . import source_fs

        def _cleared(kind: str) -> str:
            return (f"recovered {dest.name}: {kind}" if marker.remove()
                    else f"recovery-required for {dest.name}: journal could not be removed (retained)")

        def _head_state() -> object:
            """Whether dest is THIS transaction's tree: True (HEAD == journal commit),
            False (a DIFFERENT tree — rolled-back prior or a foreign occupant), or
            None (v2 journal / unprovable — no judgement possible)."""
            if meta is None or not meta.get("resolved_commit"):
                return None
            head = self.system.runner.run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
            actual = (head.stdout or "").strip() if head.returncode == 0 else ""
            return actual == meta["resolved_commit"]

        def _record_ok(ours) -> bool:
            """Complete the ownership record for a PROVEN-completed activation. The journal's
            resolved_commit is the AUTHORITY: only a dest whose actual HEAD equals it gets the
            record (a ROLLED-BACK prior — restored by an in-process rollback that retained the
            journal — must never be re-registered under the new transaction's metadata; the
            prior's own older record still describes it). A v2 journal or an unprovable tree
            writes nothing (ownership is later provable via the legacy backfill path)."""
            if ours is not True:
                return True                       # rolled-back / v2 -> no new record
            return self._write_registry_record(dest, meta, txn_id)

        def _rollback_record_failure(txn) -> str:
            """The record could STILL not be persisted after the recovery retry: perform the
            same safe rollback the in-process path does, so the new tree is never left active
            under old/absent metadata. Identity proof for the destructive step: the journal is
            txn-bound + dest-validated, its state is `activated`, and `_record_ok` just proved
            the actual HEAD equals the journal's resolved commit — dest IS this transaction's
            tree. An UPDATE (`.prev` present) restores the prior (whose own record was never
            touched); a FRESH INSTALL (journal `had_prior` false) removes the candidate; an
            ambiguous state retains the journal (fail closed)."""
            had_prior = (meta or {}).get("had_prior", None)
            cand_ident = (idents or {}).get("candidate")
            prev_ident = (idents or {}).get("prev")

            try:
                if txn.leaf_kind(prev.name) != "absent":
                    if prev_ident is not None and not source_fs.ident_matches(
                            txn.fd, prev.name, prev_ident):
                        return (f"recovery-required for {dest.name}: archived prior was "
                                "substituted (everything retained)")
                    # IDENT-BOUND destructive step: the recorded candidate identity is
                    # REQUIRED (recovery runs only for v4 journals) and stays bound
                    # through the deletion.
                    source_fs.race_seam("pre-recovery-rollback-delete", dest.name)
                    ok, _w = source_fs.remove_bound(txn.fd, dest.name, cand_ident)
                    if not ok:
                        return (f"recovery-required for {dest.name}: active leaf is not the "
                                "recorded candidate (everything retained)")
                    txn.rename_noreplace(prev.name, dest.name)
                    txn.fsync()
                    if not txn.usable(dest.name):
                        return (f"recovery-required for {dest.name}: rollback restore not "
                                "usable (journal retained)")
                    return _cleared("rolled back — ownership record could not be persisted; "
                                    "prior source and its record intact")
                if had_prior is False:                   # PROVEN fresh install -> full undo
                    source_fs.race_seam("pre-recovery-rollback-delete", dest.name)
                    ok, _w = source_fs.remove_bound(txn.fd, dest.name, cand_ident)
                    if not ok:
                        return (f"recovery-required for {dest.name}: active leaf is not the "
                                "recorded candidate (everything retained)")
                    txn.fsync()
                    if txn.leaf_kind(dest.name) != "absent":
                        return (f"recovery-required for {dest.name}: fresh-install rollback "
                                "not proven (journal retained)")
                    return _cleared("rolled back fresh install — ownership record could not "
                                    "be persisted; no active source remains")
            except (OSError, PathContainmentError):
                pass
            return (f"recovery-required for {dest.name}: ownership record could not be "
                    "persisted and rollback is not provable (journal retained)")
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                if txn.usable(dest.name):
                    # Completed activation: the ownership record must be completed (ONE retry —
                    # this call) and the archived prior PROVEN removed (held FD) before the
                    # journal is cleared. A still-failing record write rolls the activation
                    # back rather than leaving the new tree active under old/absent metadata.
                    # A dest PROVEN to be a DIFFERENT tree while an archived prior still
                    # exists is a FOREIGN occupant: retain journal + prior + occupant as
                    # evidence — never delete the archived prior underneath it.
                    ours = _head_state()
                    if ours is False and txn.leaf_kind(prev.name) != "absent":
                        return (f"recovery-required for {dest.name}: the active leaf is not "
                                "this transaction's tree while its archived prior still "
                                "exists — everything retained (unverified occupant)")
                    if not _record_ok(ours):
                        return _rollback_record_failure(txn)
                    if txn.leaf_kind(prev.name) != "absent":
                        source_fs.race_seam("pre-prev-cleanup", str(dest))
                        dirty = self._prev_dirty_scan(txn, dest, prev,
                                                      (idents or {}).get("prev"))
                        if dirty is None:
                            return (f"recovery-required for {dest.name}: archived prior "
                                    "could not be proven (journal + prior retained)")
                        if dirty:
                            # LATE LOCAL CHANGES inside the archived prior: mark the
                            # transaction operator-only so no automatic recovery ever
                            # deletes it; the active source + its record stay coherent.
                            marker.rewrite(self._journal_payload(
                                dest, prev, staging, "prior-dirty-retained", txn_id,
                                meta, idents))
                            return (f"recovery-required for {dest.name}: activation is "
                                    f"complete, but the archived prior at "
                                    f"{self._source_rel(prev)} contains late local changes "
                                    "— retained for the operator (never auto-deleted)")
                        if not self._prev_cleanup_ok(txn, prev, (idents or {}).get("prev")):
                            return (f"recovery-required for {dest.name}: archived prior "
                                    "could not be removed or was substituted (journal + "
                                    "prior retained)")
                    return _cleared("active source intact")
                if txn.leaf_kind(staging.name) != "absent" and txn.leaf_kind(dest.name) == "absent":
                    cand_ident = (idents or {}).get("candidate")
                    if cand_ident is None:
                        return (f"recovery-required for {dest.name}: no candidate identity "
                                "evidence — automatic promotion refused (retained)")
                    if not source_fs.ident_matches(txn.fd, staging.name, cand_ident):
                        return (f"recovery-required for {dest.name}: staged candidate was "
                                "substituted (everything retained)")
                    source_fs.race_seam("pre-recovery-promote", str(dest))
                    try:                                # died before staging->dest
                        txn.rename_noreplace(staging.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        pass                            # fall through to prior restore
                    else:
                        # POST-promotion re-proof: the destination must STILL be the
                        # recorded candidate (a swap immediately after the rename is
                        # detected; everything retained).
                        if not source_fs.ident_matches(txn.fd, dest.name, cand_ident):
                            return (f"recovery-required for {dest.name}: destination is no "
                                    "longer the recorded candidate after promotion "
                                    "(everything retained)")
                        if txn.usable(dest.name):
                            if not _record_ok(_head_state()):
                                return _rollback_record_failure(txn)
                            return _cleared("completed interrupted activation")
                if txn.leaf_kind(prev.name) != "absent":     # died after dest->prev: roll back
                    # An OCCUPIED dest slot (dangling symlink, file, injected dir, special
                    # leaf) is NEVER deleted to continue — retain it + `.prev` + journal.
                    if txn.leaf_kind(dest.name) != "absent":
                        return (f"recovery-required for {dest.name}: destination is occupied "
                                "by an unverified leaf (everything retained)")
                    prev_ident = (idents or {}).get("prev")
                    if prev_ident is None:
                        return (f"recovery-required for {dest.name}: no prior identity "
                                "evidence — automatic restore refused (retained)")
                    if not source_fs.ident_matches(txn.fd, prev.name, prev_ident):
                        return (f"recovery-required for {dest.name}: archived prior was "
                                "substituted (everything retained)")
                    try:
                        txn.rename_noreplace(prev.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        return (f"recovery-required for {dest.name}: could not restore prior "
                                "(journal + candidate/prior retained)")
                    # POST-restore re-proof: the destination must be the restored prior.
                    if not source_fs.ident_matches(txn.fd, dest.name, prev_ident):
                        return (f"recovery-required for {dest.name}: destination is not the "
                                "restored prior (everything retained)")
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
        prior = cand = None
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                # Synthesize the v4 evidence (minimal valid meta + leaf idents) so even this
                # low-level entry produces journals current recovery can act on — there is
                # no journal generation without identity evidence anymore.
                meta = {"selector": "legacy", "resolved_commit": "", "remote": "",
                        "strategy": "", "components": [dest.name or "src"],
                        "had_prior": txn.leaf_kind(dest.name) != "absent"}
                try:
                    if txn.leaf_kind(dest.name) == "dir":
                        prior = txn.capture_leaf(dest.name)
                    if txn.leaf_kind(staging.name) == "dir":
                        cand = txn.capture_leaf(staging.name)
                except (OSError, PathContainmentError):
                    return "recovery-required"
                return self._activate_held(txn, dest, staging, verify_active,
                                           handle=cand, meta=meta, prior=prior)
        except PathContainmentError:
            return "recovery-required"          # unsafe/swapped source parent -> fail closed
        finally:
            for h in (prior, cand):
                if h is not None:
                    h.close()

    def _rollback_bad_active(self, txn, dest: Path, prev: Path, handle=None) -> str:
        """Undo a just-completed activation (post-activation provenance failure, or an
        ownership-record persistence failure) via the held FD. `dest` is removed ONLY after
        re-proving it is still our captured candidate/link handle — never a pathname-only
        `rmtree(dest.name)` of an unverified replacement. On identity loss the destination,
        `.prev`, and journal are RETAINED. Returns a PROVEN outcome:
          * 'restored-prior' — the archived prior is back in place and usable;
          * 'removed-fresh'  — a fresh install's candidate was removed (no active source);
          * 'recovery-required' — rollback could not be proven (evidence retained)."""
        # The active `dest` must still be our captured leaf before we destroy it — and the
        # destruction itself stays BOUND to that identity (never a name-only rmtree).
        from . import source_fs
        dest_is_ours = self._verify_staged(txn, handle, dest.name)
        dest_ident = ([handle.st_dev, handle.st_ino] if handle is not None else None)
        try:
            if txn.leaf_kind(prev.name) != "absent":
                if txn.leaf_kind(dest.name) != "absent":
                    if not dest_is_ours:
                        return "recovery-required"       # unverified active leaf -> RETAIN it
                    source_fs.race_seam("pre-rollback-delete", dest.name)
                    ok, _w = source_fs.remove_bound(txn.fd, dest.name, dest_ident)
                    if not ok:
                        return "recovery-required"       # substituted mid-removal -> retain
                txn.rename_noreplace(prev.name, dest.name)   # restore into the FREED slot only
                txn.fsync()
                return "restored-prior" if txn.usable(dest.name) else "recovery-required"
            # Fresh install (no prior to restore): drop the bad candidate ONLY if it is still
            # ours; otherwise retain the unverified destination + journal.
            if txn.leaf_kind(dest.name) != "absent":
                if not dest_is_ours:
                    return "recovery-required"
                source_fs.race_seam("pre-rollback-delete", dest.name)
                ok, _w = source_fs.remove_bound(txn.fd, dest.name, dest_ident)
                if not ok:
                    return "recovery-required"
            txn.fsync()
            return "removed-fresh"
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

    def _activate_held(self, txn, dest: Path, staging: Path, verify_active=None, handle=None,
                       meta: dict | None = None, prior=None, final_dirty=None) -> str:
        from . import source_fs
        prev = dest.with_name(f".{dest.name}.prev")
        # A pre-existing `.prev` is an UNOWNED orphan (the journal is created EXCLUSIVELY just
        # below, so none exists yet): block rather than blind-remove a prior run's artifact.
        if txn.leaf_kind(prev.name) != "absent":
            return "failed-clean"
        # (1) EXCLUSIVE journal creation (`O_CREAT|O_EXCL|O_NOFOLLOW`) + fsync of the journal
        # and its parent, RETAINING the journal file + parent fds. A journal INJECTED after the
        # absent-preflight (regular/symlink/special/stale) makes the create fail -> block BEFORE
        # any candidate/dest/`.prev` mutation; the injected leaf is preserved for recovery.
        idents = None
        if meta is not None:
            idents = {"candidate": ([handle.st_dev, handle.st_ino] if handle is not None
                                    else None),
                      "prev": ([prior.st_dev, prior.st_ino] if prior is not None else None)}
        jh = self._create_journal(dest, prev, staging, meta, idents)
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
                # RACE-SAFE ARCHIVE: the leaf renamed to `.prev` must be exactly the CAPTURED
                # verified prior — proven immediately before AND immediately after the rename
                # (the retained handle identifies the inode through the rename). A mismatch
                # means an EXTERNAL process substituted the destination: nothing of the
                # substitute is archived or destroyed.
                if txn.leaf_kind(dest.name) != "absent":
                    source_fs.race_seam("pre-archive", str(dest))
                    if prior is not None and not txn.verify_leaf(prior, dest.name):
                        if jh["marker"].remove():
                            return "substituted"        # nothing mutated; substitute untouched
                        return "recovery-required"
                    # FINAL dirty recheck against the CAPTURED prior — new local changes
                    # since the initial check block the archive with zero mutation.
                    if final_dirty is not None and final_dirty():
                        if jh["marker"].remove():
                            return "dirty"
                        return "recovery-required"
                    try:
                        txn.rename_noreplace(dest.name, prev.name)
                    except FileExistsError:
                        # a leaf was INJECTED at `.prev` after the preflight: nothing mutated;
                        # retain the injected leaf, drop the journal (clean refusal)
                        if jh["marker"].remove():
                            return "injected"
                        return "recovery-required"
                    if prior is not None and not txn.verify_leaf(prior, prev.name):
                        # The rename raced a substitution: what landed at `.prev` is NOT the
                        # verified prior. Put it back (NOREPLACE — dest was just freed) and
                        # refuse; if the slot was re-occupied, retain everything as evidence.
                        try:
                            txn.rename_noreplace(prev.name, dest.name)
                        except (OSError, PathContainmentError):
                            return "recovery-required"   # quarantined at .prev + journal
                        txn.fsync()
                        if jh["marker"].remove():
                            return "substituted"
                        return "recovery-required"
                    archived = True                     # the prior IS archived now — set BEFORE
                    txn.fsync()                         # the journal write, so a later failure
                    if not self._update_journal(jh, dest, prev, staging, "prior-archived"):
                        raise _JournalLost()
                    # SECOND dirty scan THROUGH THE CAPTURED PRIOR HANDLE, after the archive
                    # and before promotion: a file created INSIDE the unchanged directory
                    # after the pre-archive check (pathname-based writer) is caught here.
                    # If dirty: no promotion — restore `.prev` no-clobber, re-prove its
                    # identity at the destination, and refuse truthfully (the candidate is
                    # discarded by the caller through its bound identity; the prior source
                    # and its registry record stay authoritative and consistent).
                    source_fs.race_seam("post-archive", str(dest))
                    if final_dirty is not None and final_dirty():
                        try:
                            txn.rename_noreplace(prev.name, dest.name)
                        except (OSError, PathContainmentError):
                            return "recovery-required"   # slot reoccupied -> retain evidence
                        txn.fsync()
                        if prior is not None and not txn.verify_leaf(prior, dest.name):
                            return "recovery-required"   # unproven restore -> retain journal
                        if jh["marker"].remove():
                            return "dirty"               # truthful refusal; prior restored
                        return "recovery-required"
                # TIGHT re-check IMMEDIATELY before promotion (bounded only by kernel rename
                # atomicity): a substituted candidate/link leaf is never promoted.
                if not self._verify_staged(txn, handle, staging.name):
                    raise _Substituted()
                # (5) candidate -> dest, ATOMICALLY refusing to replace an injected leaf
                # (renameat2 RENAME_NOREPLACE — plain rename would silently replace an
                # injected EMPTY directory); (6) fsync parent ; (7) journal 'activated'.
                source_fs.race_seam("pre-promote", str(dest))
                try:
                    txn.rename_noreplace(staging.name, dest.name)
                except FileExistsError:
                    # A leaf APPEARED at dest after absence was observed. Fresh install: drop
                    # our candidate, clear the journal, refuse — the injected leaf untouched.
                    # Update: restore the archived prior to its slot first (NOREPLACE cannot —
                    # the slot is occupied), so retain journal + .prev as evidence.
                    if not archived:
                        if jh["marker"].remove():
                            return "injected"
                        return "recovery-required"
                    return "recovery-required"           # .prev + journal retained (evidence)
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
                        # NOREPLACE: never clobber a leaf injected into the freed slot
                        txn.rename_noreplace(prev.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        pass                                 # unproven restore -> retain all
                return "recovery-required"
            except (OSError, PathContainmentError):
                # Generic activation/journal failure. Restore the prior ONLY into a freed
                # slot (NOREPLACE) — an injected occupant (dangling symlink, file, directory,
                # special leaf) is NEVER deleted to continue: retain it + `.prev` + journal
                # as evidence (recovery-required).
                if archived:
                    if txn.leaf_kind(dest.name) != "absent":
                        return "recovery-required"       # foreign occupant retained
                    try:
                        txn.rename_noreplace(prev.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        return "recovery-required"
                    if not txn.usable(dest.name):
                        return "recovery-required"
                return "failed-clean" if jh["marker"].remove() else "recovery-required"
            # (8) confirm the active source is a USABLE DIRECTORY (held FD), then verify final
            # provenance — which can take time, so a candidate/link swap can occur DURING it.
            if not txn.usable(dest.name):
                return "recovery-required"
            if verify_active is not None and not verify_active():
                # Provenance FAILED -> destructive rollback, but only after re-proving `dest`
                # is still our captured handle (never rmtree an unverified replacement). After
                # a PROVEN rollback the state is coherent (prior restored with its own record,
                # or a fresh install fully undone) -> the journal is removed; only an unproven
                # rollback retains it.
                rb = self._rollback_bad_active(txn, dest, prev, handle)
                if rb in ("restored-prior", "removed-fresh"):
                    return "provenance-blocked" if jh["marker"].remove() else "recovery-required"
                return "recovery-required"
            # Provenance SUCCEEDED, but re-verify the ACTIVE leaf is STILL our captured
            # candidate/link (a swap during provenance evaluation) BEFORE any `.prev`/journal
            # removal. On mismatch: retain journal + `.prev` + substituted active leaf.
            if not self._verify_staged(txn, handle, dest.name):
                return "recovery-required"
            # (8b) OWNERSHIP RECORD — transactional: the durable registry record is written from
            # the journal metadata BEFORE any `.prev`/journal cleanup. A write FAILURE must not
            # leave the new tree active under old/absent metadata: roll back to the verified
            # `.prev` (its prior record was never touched, so it still matches), or fully remove
            # a fresh install's candidate. Only an UNPROVEN rollback retains the journal —
            # recovery then retries the record once and performs the same rollback.
            if meta is not None and not self._write_registry_record(dest, meta, jh["txn_id"]):
                rb = self._rollback_bad_active(txn, dest, prev, handle)
                if rb in ("restored-prior", "removed-fresh"):
                    return "registry-blocked" if jh["marker"].remove() else "recovery-required"
                return "recovery-required"
            # (9) remove `.prev` — IDENT-BOUND to the captured prior handle (never a
            # name-only rmtree); a substituted `.prev` is retained + journal kept.
            # FINAL PRIOR DIRTY SCAN first: a pathname-based writer that created a file
            # inside the (unchanged) archived prior after the post-archive recheck must
            # never lose it to the cleanup — the transaction is marked
            # `prior-dirty-retained` (automatic recovery never retries the deletion), the
            # ACTIVE NEW SOURCE stays (its registry record is already coherent), and the
            # result is a truthful incomplete naming the retained `.prev`.
            # (10) remove the journal ONLY after cleanup.
            prior_ident = ([prior.st_dev, prior.st_ino] if prior is not None else None)
            if txn.leaf_kind(prev.name) != "absent":
                source_fs.race_seam("pre-prev-cleanup", str(dest))
                dirty = self._prev_dirty_scan(txn, dest, prev, prior_ident)
                if dirty is None:
                    return "recovery-required"
                if dirty:
                    self._update_journal(jh, dest, prev, staging, "prior-dirty-retained")
                    return "prior-dirty"
                if not self._prev_cleanup_ok(txn, prev, prior_ident):
                    return "recovery-required"
            txn.fsync()
            return "activated" if jh["marker"].remove() else "recovery-required"
        finally:
            self._close_journal(jh)


    # A "release" for the git-only Latest-stable resolution: a version-shaped tag.
    _VERSION_TAG = re.compile(r"^v?(\d+(?:\.\d+)+)")

    def _resolve_stable_tag(self, dest: str) -> str:
        """Git-only Latest-stable tag selection in a FULL clone at `dest`:
          * the newest VERSION-SHAPED tag (v?X.Y[.Z…], highest by numeric version sort) —
            the published-release form;
          * else the newest tag by creation date (a "suitable tag");
          * else "" — the caller stays on the default-branch HEAD (latest main commit)."""
        run = self.system.runner.run
        tags = run(["git", "-C", dest, "tag", "--list"], 10.0)
        names = [t.strip() for t in (tags.stdout or "").splitlines() if t.strip()] \
            if tags.returncode == 0 else []
        versioned = []
        for name in names:
            m = self._VERSION_TAG.match(name)
            if m:
                versioned.append((tuple(int(x) for x in m.group(1).split(".")), name))
        if versioned:
            return max(versioned)[1]
        newest = run(["git", "-C", dest, "for-each-ref", "--sort=-creatordate",
                      "--format=%(refname:short)", "refs/tags"], 10.0)
        if newest.returncode == 0:
            for line in (newest.stdout or "").splitlines():
                if line.strip():
                    return line.strip()
        return ""

    def _clone(self, spec, dest: Path, source: str, remote: str | None = None,
               expected_pin: str = "") -> bool:
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
        if expected_pin:
            # FROZEN exact identity (any selector): full clone + exact checkout + verify —
            # the remote's CURRENT refs are irrelevant; no selector lookup happens here.
            if run(["git", "clone", remote, str(dest)], timeout=240.0).returncode == 0 \
                    and dest.exists():
                ok = run(["git", "-C", str(dest), "checkout", expected_pin],
                         30.0).returncode == 0
                if ok:
                    head = run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
                    ok = head.returncode == 0 and head.stdout.strip() == expected_pin
        elif spec.artifact:
            # Declared artifact source: EVERY selector resolves to the same declared artifact
            # (the maintainer's default branch) — no pin/branch/tag semantics are invented.
            ok = (run(["git", "clone", "--depth", "1", remote, str(dest)],
                      timeout=120.0).returncode == 0 and dest.exists())
        elif source == "dev":
            # STRICT branch semantics: with a configured branch, `--branch` makes git fail
            # when it does not exist — dev NEVER silently falls back to another ref.
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
                    # P0.5: 'Known working' REQUIRES an exact expected commit — the newest
                    # operator-confirmed composition entry when one exists, else the manifest
                    # pin — and must resolve EXACTLY to it; never a silent adoption of the
                    # default branch.
                    pin = expected_pin or spec.pin_commit
                    if not pin:
                        ok = False
                    else:
                        ok = run(["git", "-C", str(dest), "checkout", pin],
                                 30.0).returncode == 0
                        if ok:
                            head = run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
                            ok = head.returncode == 0 and head.stdout.strip() == pin
                elif source == "stable":
                    # Latest stable, GIT-ONLY: newest version-shaped tag ("release") ->
                    # newest tag -> default-branch HEAD (latest main commit). The resolved
                    # commit is recorded by the ownership registry either way.
                    tag = self._resolve_stable_tag(str(dest))
                    if not tag:
                        ok = True                      # no tags at all -> default-branch HEAD
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
                    signer_diags=(), expected: str = "", kw_label: str = "") -> PlanAction:
        probe = probe_source(self.system, spec, str(dest))
        version = probe.version or probe.head[:12]
        # DISPLAY-ONLY provenance status: enforcement already happened INSIDE the durable
        # transaction (`_activate`'s `verify_active`), which rolled back and retained evidence
        # on a mismatch — so reaching here means the active source is provenance-verified. We
        # re-evaluate ONLY to report the status; we never raise a NEW failure here (that would
        # be after `.prev`/journal were already cleared, with no rollback evidence left).
        from . import provenance
        trusted, _diags = provenance.load_trusted_signers(self.config)
        post = provenance.evaluate(self.system.runner, str(dest), spec, source, trusted,
                                   expected_commit=expected)
        action.provenance = post.status
        action.status = "done"
        action.detail = f"{source_desc}: {probe.state.value} (version {version}) [provenance: {post.status}]"
        if source == "pinned" and kw_label:
            # 'Known working' truthfulness: composition-resolved vs manifest-pin FALLBACK.
            action.detail += f" [{kw_label}]"
        if signer_diags:
            action.detail += " | signer-config: " + "; ".join(signer_diags)
        return action

"""Runtime-root bootstrap and safe source adoption.

This is the first *mutating* layer, but it is deliberately conservative:

  * bootstrap is idempotent and NEVER overwrites local config or secrets;
  * source adoption clones the selected version into the runtime root (a configured in-root local checkout is only a fallback) and then VERIFIES the pin; it never edits,
    resets or cleans the original source, and refuses to overwrite an existing
    runtime checkout unless explicitly forced;
  * a dirty source is reported, never silently "repaired".

All operations are expressed as a `Plan` of `PlanAction`s so the CLI and the web confirmation page can show the exact intended effect before applying.
Nothing here builds, starts a service, or transmits.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import provenance
from .assets import asset_text
from .best_effort import best_effort, stderr_line
from .config import Config
from .model import Component, Stack
from .paths import PathContainmentError, Paths
from .probes import System
from .probes.source import lhpc_patched_only, probe_source

RUNTIME_SUBDIRS = (
    "bin", "src", "build", "config", "profiles", "systemd", "state", "logs",
    "config/secrets",
)

# Operator-facing pointers at the runtime root, as RELATIVE symlinks into the self-hosted
# checkout. A dangling link on a non-self-hosted root is deliberate: it still
# names where the documentation lives.
DOC_LINKS = (
    ("docs", "src/loraham-pi-control/docs"),
    ("README.md", "src/loraham-pi-control/README.md"),
)

# Heavy, regenerable directories we skip when adopting a local checkout — and that never
# count as "local changes" in the destructive-operation dirty check (they are build/runtime
# artifacts LHPC itself regenerates).
_ADOPT_IGNORE_NAMES = (
    ".pio", ".venv", "build", ".work", ".run", "__pycache__", "node_modules",
)
_ADOPT_IGNORE = shutil.ignore_patterns(*_ADOPT_IGNORE_NAMES)

_ENOENT_PREFIX = f"[Errno {errno.ENOENT}]"

# The waits between `.git` copy attempts while git repacks the checkout: 7 retries, 6.35 s at
# most. A repack measured 0.92 s on a Pi 5; a longer one still fails the adoption, loudly.
_GIT_COPY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2)
_sleep = time.sleep


def _every_failure_is_enoent(err: shutil.Error) -> bool:
    """True only when EVERY failure `copytree` collected is a missing path.

    `copytree` aggregates nested failures into one `shutil.Error` carrying
    `(src, dst, why)` triples whose `why` is the string form of the original error. A
    mixed error -- a missing path AND a permission or I/O failure -- must NOT read as
    transient: retrying it could hide a genuine copy failure behind the benign one and
    activate an incomplete tree. Hence `all`, never `any`."""
    entries = err.args[0] if err.args else ()
    return bool(entries) and all(
        str(why).startswith(_ENOENT_PREFIX) for _src, _dst, why in entries)


@dataclass(frozen=True)
class DirtyReport:
    """Local changes in a managed checkout. `tracked` = modified/staged/deleted tracked files;
    `untracked` = non-ignored untracked files EXCLUDING LHPC-regenerable artifacts
    (`_ADOPT_IGNORE_NAMES` + every consumer component's declared built binary).

    Two readings, because two kinds of operation are at stake: `__bool__` (either list) is what a
    DESTRUCTIVE operation asks — uninstall and clean carry nothing forward, so an added file there
    really would be lost. `blocks_update()` (tracked only) is what a source UPDATE asks, because
    an update carries additions into the new source."""
    tracked: tuple = ()
    untracked: tuple = ()

    def __bool__(self) -> bool:
        return bool(self.tracked or self.untracked)

    def blocks_update(self) -> bool:
        """Does this tree refuse a source UPDATE? Only TRACKED changes do.

        An update replaces the checkout with a fresh candidate and CARRIES local additions
        across (`Installer.extra_files` → `source_fs.carry_extras`), so an added file is not
        data an update would discard. A tracked modification still is: the upstream file it
        edits does not survive the replacement, and LHPC will not guess how to merge it.

        DESTRUCTIVE operations (uninstall, clean) keep the full `__bool__` rule — they carry
        nothing, so an added file there really would be lost."""
        return bool(self.tracked)

    def lines(self, limit: int = 8) -> list:
        out = [f"    modified: {p}" for p in self.tracked[:limit]]
        out += [f"    untracked: {p}" for p in self.untracked[:limit]]
        hidden = max(0, len(self.tracked) - limit) + max(0, len(self.untracked) - limit)
        if hidden:
            out.append(f"    … and {hidden} more")
        return out


class _Substituted(Exception):
    """Internal signal: the staging candidate leaf no longer matches the captured
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

"""


@dataclass
class PlanAction:
    kind: str                 # mkdir | harden | doclink | config | secret | adopt | verify
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


def _git_marker_anomaly(dest: Path) -> str | None:
    """The `.git` of a checkout for the dirty/carry inventory: None = absent (not a checkout),
    "" = present and reachable, else the anomaly. A `.git` that cannot be examined, or a symlink
    that does not resolve (dangling, a loop), is never "absent" and never handed to git: git
    would walk up to an enclosing repository and report on that one."""
    from . import runtime_fs
    state, why, st = runtime_fs.probe_stat(dest / ".git")
    if state == "absent":
        return None
    if state == "present" and stat.S_ISLNK(st.st_mode):
        state, why, _st = runtime_fs.probe_stat(dest / ".git", follow=True)
        if state == "absent":
            return ".git is a dangling symlink"
        if state == "unknown":
            return f".git is a symlink that cannot be followed ({why})"
        return ""
    return f"cannot examine .git ({why})" if state == "unknown" else ""


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
        for name, rel in DOC_LINKS:
            d = self.subdir(name)
            try:
                current = os.readlink(d) if d.is_symlink() else None
            except OSError:
                current = None
            plan.actions.append(PlanAction(
                "doclink", str(d), f"link {name} -> {rel}",
                status="exists" if current == rel else "planned"))
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
        elif action.kind == "doclink":
            dest = Path(action.target)
            rel = dict(DOC_LINKS)[dest.name]
            if dest.is_symlink():
                # Wrong-target links are corrected (they are OURS — nothing else writes
                # here); a non-empty real directory or a regular file is the operator's and
                # is left alone.
                if os.readlink(dest) != rel:
                    dest.unlink()
                    os.symlink(rel, dest)
                action.status = "done"
            elif dest.is_dir():
                try:
                    dest.rmdir()                       # only succeeds on an EMPTY dir
                except OSError:
                    action.status, action.detail = "skipped", "directory not empty — left as is"
                    return
                os.symlink(rel, dest)
                action.status = "done"
            elif dest.exists():
                action.status, action.detail = "skipped", "exists and is not a symlink"
            else:
                os.symlink(rel, dest)
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
                if kind == "dir":
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
                    # The same refusal `adopt_source` gives: a symlink/file/special leaf is
                    # never an LHPC adoption, so the plan and the apply agree.
                    plan.actions.append(PlanAction(
                        "verify", str(dest),
                        f"{comp.id}: destination is an unexpected {kind} leaf — not an LHPC "
                        "adoption (resolve manually)", status="failed", detail=kind))
        return plan

    def adopt_source(self, comp: Component, *, force: bool = False,
                     source: str = "pinned", pinned_expected: tuple | None = None,
                     locked: bool = False) -> PlanAction:
        """Install a component's source. `source` selects the version:
          * "pinned" (default) — the stack's compatible known-working composition entry, else the manifest pin;
          * "dev"    — newest commit on the configured branch;
          * "stable" — the newest version-shaped tag, else the default-branch HEAD.
        Clones from the remote; a local checkout under `[install].adopt_search_root` is used only
        when that is configured and provably satisfies the selector.
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
        # ONE operation boundary, deadlock-free order (index THEN source path).
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
                      pinned_expected: tuple | None = None):
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
            # CONTAINMENT: a symlink (dangling, unknown, injected) is NOT an installable
            # destination — a managed source is a directory under the runtime root. Refuse
            # with zero mutation.
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
                    # Say how to RECOVER. Refusing to overwrite a tree lhpc did not move is
                    # right, but every route (update, update --source pinned, install) then
                    # refuses and the operator is stuck with no documented way out.
                    action.detail = (
                        f"ownership/identity not proven — not overwritten: {why}. "
                        f"Recover by removing the checkout ({spec.path}) and re-running "
                        f"install, which re-clones it at the pin.")
                    return action
                # Never overwrite the operator's changes to the UPSTREAM source (tracked
                # modifications/deletions/staged content): the replacement would discard them and
                # LHPC will not guess how to merge. Added files are carried instead of blocking —
                # `blocks_update()`. Checked on the CAPTURED inode.
                dirty = self.dirty_report(Path(prior.pinned_path()), spec.path)
                if dirty.blocks_update():
                    action.status = "failed"
                    action.detail = ("local modifications to the upstream source — not "
                                     "overwritten:\n"
                                     + "\n".join(f"    modified: {p}" for p in dirty.tracked[:8])
                                     + "\n    Preserve or reconcile these changes first — they "
                                       "are yours and nothing here will merge them. ONLY if you "
                                       f"mean to discard them: remove the checkout ({spec.path}) "
                                       "and re-run install, which re-clones it at the pin.")
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
            # The INDEX + SOURCE-PATH locks are already held by adopt_source across candidate
            # creation, verification, activation, and cleanup.
            return self._stage_and_activate(comp, source, action, dest, spec, local,
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
        return "", "fallback: manifest pin — no compatible known-working record"

    def _stage_and_activate(self, comp: Component, source: str, action: PlanAction,
                            dest: Path, spec, local: Path | None,
                            had_prior: bool = False, prior=None,
                            pinned_expected: tuple | None = None) -> PlanAction:
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

        from . import provenance, source_fs
        staging = dest.with_name(f".{dest.name}.candidate-{os.getpid()}-{_time.monotonic_ns()}")
        trusted, signer_diags = provenance.load_trusted_signers(self.config)
        # 'Known working' resolution: a FROZEN per-operation plan value when the caller
        # planned the whole install/update up front (`pinned_expected`) — a concurrent
        # operator confirmation cannot alter an already-planned operation — else resolved
        # here (single-component adoption).
        if pinned_expected is not None and pinned_expected[0]:
            # FROZEN plan identity (auto-install): one exact immutable commit resolved at plan
            # time — used verbatim for EVERY selector; no second selector lookup here.
            expected, kw_label = pinned_expected
        elif source == "pinned":
            expected, kw_label = (pinned_expected if pinned_expected is not None
                                  else self._pinned_expected(comp))
        else:
            expected, kw_label = "", ""
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn, \
                    self._staged_clone_record(dest, staging) as clone_rec:
                # (1) Journal preflight: only an ABSENT journal may begin a new transaction;
                # any existing journal must be resolved by recovery first (never overwritten).
                if source_fs.leaf_kind(self.paths, self._journal_path(dest)) != "absent":
                    action.status, action.detail = "failed", (
                        "recovery-required: an unresolved source-transaction journal exists "
                        "for this source — resolve it before installing/updating")
                    return action
                # (2-3) Exclusive candidate creation + staging, all through the held FD.
                desc, handle = self._stage_candidate(txn, comp, source, dest, staging, spec,
                                                     local, action, expected_pin=expected,
                                                     clone_rec=clone_rec)
                if desc is None:
                    return action          # `_stage_candidate` recorded the typed failure
                # (4) Candidate provenance gate, on the candidate FD-pinned path (it follows the
                # inode through the activation rename).
                pre_pinned = handle.pinned_path()
                pre = provenance.evaluate(self.system.runner, pre_pinned, spec, source, trusted,
                                          expected_commit=expected)
                if not pre.ok:
                    # Handle-safe cleanup: a candidate substituted during provenance is
                    # retained as evidence, never deleted. dest untouched either way.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status, action.provenance = "failed", pre.status
                    action.detail = f"provenance blocked before activation: {pre.detail} [{pre.status}]"
                    return action
                # (5-9) Activate + final provenance (post-rename, on the VERIFIED active leaf)
                # + cleanup — all under the SAME held FD.
                def _post_ok() -> bool:
                    return provenance.evaluate(
                        self.system.runner, handle.pinned_path(), spec, source, trusted,
                        expected_commit=expected).ok
                # Ownership metadata rides in the journal so the registry record is part
                # of the SAME durable transaction: written after the activation rename, and
                # completable by recovery from the journal alone.
                meta = self._txn_meta(comp, spec, source, pre_pinned)
                meta["had_prior"] = bool(had_prior)
                # FINAL dirty recheck, run immediately before the prior is archived: an
                # upstream file MODIFIED after the initial check (e.g. while the candidate was
                # cloning/building) must block the archive. A file merely ADDED in that window
                # does not — it is carried like any other addition.
                final_dirty = (
                    (lambda: self.dirty_report(Path(prior.pinned_path()),
                                               spec.path).blocks_update())
                    if prior is not None and prior.kind == "dir" else None)
                # LOCAL ADDITIONS: inventory the prior's untracked files and reproduce them in
                # the candidate at identical paths. Runs INSIDE the activation, after the prior
                # is archived, so the set is final. `carry_why` carries the first conflict out
                # for a truthful message.
                carry_why: dict = {}
                # ...and `prev_why` the reason the archived prior had to be RETAINED, which is
                # decided only at the very end, by re-proving the carry against the live tree.
                prev_why: dict = {}

                def _carry() -> bool:
                    if prior is None or prior.kind != "dir" or handle is None:
                        return False
                    rels = self.extra_files(Path(prior.pinned_path()), spec.path)
                    if rels is None:
                        carry_why["why"] = ("local additions could not be inventoried "
                                            "(git failed) — refusing rather than dropping them")
                        return True
                    why = source_fs.carry_extras(prior.fd, handle.fd, rels)
                    if why:
                        carry_why["why"] = why
                    return bool(why)

                outcome = self._activate_held(txn, dest, staging, verify_active=_post_ok,
                                              handle=handle, meta=meta, prior=prior,
                                              final_dirty=final_dirty, carry=_carry,
                                              prev_why=prev_why)
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
                if outcome == "prior-in-use":
                    # The NEW source IS active and its record coherent; the archived prior is
                    # kept only while another process can still write into it. The transaction
                    # stays journaled: the next source command's recovery removes it once free.
                    action.status = "failed"
                    action.provenance = ""
                    action.detail = (
                        "prior-in-use: the update activated the new source, but its archived "
                        f"prior at {self._source_rel(dest.with_name('.' + dest.name + '.prev'))} "
                        f"is kept — {prev_why.get('why', 'still in use')}. Once that process has "
                        "ended, the next lhpc source command removes it: "
                        f"lhpc update {comp.id} --yes")
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
                        "still holds local content — "
                        f"{prev_why.get('why', 'late local changes')} — it is RETAINED with the "
                        "transaction journal and never deleted by lhpc; "
                        + self._prior_dirty_remedy(dest))
                    return action
                if outcome == "carry-failed":
                    # A local addition could not be reproduced in the new source. The prior is
                    # restored and authoritative; nothing was merged, renamed or overwritten.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status = "failed"
                    action.detail = ("update refused: "
                                     + carry_why.get("why", "local files could not be preserved")
                                     + " — the previous source is active again; resolve what is "
                                       "named (a file in the way: move or remove it in the "
                                       "checkout; a write error: free space under src/), then "
                                       f"retry: lhpc update {comp.id} --yes")
                    return action
                if outcome == "dirty":
                    # The owned candidate is discarded ONLY through its bound identity.
                    self._cleanup_owned_staging(txn, handle, staging.name)
                    action.status = "failed"
                    action.detail = ("upstream source was modified during staging — the "
                                     "prior source is intact at its original path (nothing "
                                     "was overwritten); revert or stash the change and retry "
                                     "(for a permanent source change, fork the project and "
                                     "point the remote and pin at your fork)")
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
                if outcome == "journal-failed":
                    self._cleanup_owned_staging(txn, handle, staging.name)   # handle-safe
                    action.status = "failed"
                    action.detail = (
                        "the transaction journal could not be created under state/source-txn "
                        "(disk full or not writable?) — active source untouched; free space or "
                        f"fix that directory, then retry: lhpc update {comp.id} --yes")
                    return action
                if outcome == "recovery-required":
                    action.status = "failed"
                    action.detail = prev_why.get("unrecorded") or (
                        "recovery-required: source transaction left a retained "
                        "journal — candidate/prior evidence preserved")
                    return action
                if outcome != "activated":         # "failed-clean": no journal, safe to drop
                    self._cleanup_owned_staging(txn, handle, staging.name)   # handle-safe
                    left = dest.with_name(f".{dest.name}.prev")
                    action.status, action.detail = "failed", (
                        f"refused: {left} is left over from an earlier interrupted update and "
                        "blocks every update of this source — active source untouched; lhpc no "
                        "longer uses it (it may hold your files): move it out of src/ (mv "
                        f"{left} {self.paths.runtime_root.parent / (dest.name + '.prev.saved')}), "
                        f"then retry: lhpc update {comp.id} --yes"
                        if txn.leaf_kind(left.name) != "absent" else
                        "activation failed — active source untouched; retry: "
                        f"lhpc update {comp.id} --yes")
                    return action
                return self._adopt_done(action, spec, dest, desc, source, signer_diags,
                                        expected=expected, kw_label=kw_label)
        except PathContainmentError:
            action.status, action.detail = "failed", (
                "managed source parent is unsafe (symlinked/swapped) — active source untouched")
            return action

    def _cleanup_owned_staging(self, txn, handle, staging_name: str) -> str:
        """THE authoritative handle-safe staging cleanup. Removes the staging leaf ONLY when it
        still matches its `CandidateHandle`; a substituted replacement is RETAINED
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
                         local: Path | None, action, expected_pin: str = "", clone_rec=None):
        """Stage the candidate through the held transaction. Returns `(desc, handle)` — a
        description plus the `CandidateHandle` (a retained FD on the candidate dir). On failure
        returns `(None, None)` with a typed failure recorded on `action`. Git/copy write ONLY
        through the candidate FD-pinned path (`handle.pinned_path()`), never the mutable
        candidate leaf name."""
        # Clone / copy: EXCLUSIVELY create the empty candidate dir via the held FD (any
        # pre-existing leaf of any kind fails closed) and RETAIN its fd, then write INTO the
        # candidate FD-pinned path — Git/copy never re-resolve the leaf by name.
        remote = self.config.remotes.get(comp.id) or spec.remote
        handle = self._create_recorded(txn, clone_rec, dest, staging)
        # Adoption is the auto-install's FIRST long phase and git is silent off-TTY — give the
        # clone a tail-able `logs/adopt-<comp>.log` whose first content says what is happening
        # (quiet-step preamble), with `git clone --progress` streamed below it. BEST-EFFORT:
        # the registry/provenance machinery is the adoption's evidence, so a log failure must
        # never fail the adoption (unlike run_job, where the log IS the job's evidence).
        log_fh = None
        if remote:
            try:
                from . import runtime_fs
                log_path = self.paths.under("logs", f"adopt-{comp.id}.log")
                runtime_fs.ensure_dir(self.paths, log_path.parent)
                log_fh = runtime_fs.open_log_truncate(self.paths, log_path)
                log_fh.write(f"[clone] {comp.id} from {remote} — git is quiet without a TTY; "
                             "progress appears below (git clone --progress)\n")
                log_fh.flush()
            except (OSError, PathContainmentError):
                log_fh = None
        try:
            if remote and self._clone(spec, Path(handle.pinned_path()), source, remote,
                                      expected_pin=expected_pin, log_fh=log_fh):
                return f"GitHub {source}", handle
        finally:
            if log_fh is not None:
                try:
                    log_fh.close()
                except OSError:
                    pass
        # Clone failed (or no remote) -> reset the (intact controller-owned) candidate via the
        # held FD, then try the local fallback. If the candidate was SUBSTITUTED, do NOT delete
        # the replacement and do NOT recreate a candidate through this flow — fail closed.
        if self._cleanup_owned_staging(txn, handle, staging.name) == "identity-lost":
            action.status, action.detail = "failed", (
                "recovery-required: staging candidate was substituted (evidence retained)")
            return None, None
        # The record still names the removed candidate's inode; it names none before the next one
        # exists, so a stop between that creation and its record leaves an UNRECORDED candidate,
        # which recovery keeps and names (as it would another inode at the recorded name).
        self._note_staged(clone_rec, dest, staging, None)
        handle = self._create_recorded(txn, clone_rec, dest, staging)

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
                try:
                    shutil.copytree(s, d, ignore=_ADOPT_IGNORE, symlinks=True)
                except shutil.Error as err:
                    # A live `.git` can repack mid-copy: git packs loose objects and prunes
                    # their fan-out directories between `copytree`'s listing and its read, so
                    # an entry vanishes. That is the SAME repository in a different physical
                    # representation, so copy it again, with a bounded backoff while the
                    # repack runs. Everything else escapes: a vanishing working-tree file means
                    # the source itself is being modified under us, and failing is the safe
                    # answer; so does a mixed error at any attempt.
                    if entry != ".git" or not _every_failure_is_enoent(err):
                        raise
                    for delay in _GIT_COPY_DELAYS:
                        shutil.rmtree(d, ignore_errors=True)   # `copytree` left a partial copy
                        _sleep(delay)
                        try:
                            shutil.copytree(s, d, ignore=_ADOPT_IGNORE, symlinks=True)
                            break
                        except shutil.Error as again:
                            if not _every_failure_is_enoent(again):
                                raise
                    else:
                        raise                             # the budget is spent: the first error
            else:
                shutil.copy2(s, d, follow_symlinks=False)

    def _fallback_satisfies(self, spec, local: Path, source: str,
                            expected_pin: str = "") -> bool:
        """A local-fallback checkout may activate only if it PROVABLY satisfies
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
            # FROZEN auto-install IDENTITY: the copy/local fallback may activate ONLY at exactly
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
        anomaly = _git_marker_anomaly(dest)
        if anomaly is None:
            return DirtyReport()
        if anomaly:
            return DirtyReport(tracked=(f"({anomaly} — treating as dirty)",))
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
        if tracked:
            # Tracked changes that are exactly LHPC's own build-time patch are not operator
            # work: the next build re-applies the patch to a fresh clone. Judged on the TRACKED
            # diff alone (`lhpc_patched_only` runs `git diff HEAD`), so a local ADDITION beside
            # the patch cannot make the patch look like operator work — the openHop checkout is
            # patched by a build step and may still hold a stack's own files.
            patches = self._path_patches(source_path)
            if patches and lhpc_patched_only(self.system, str(dest), patches):
                tracked = []
        return DirtyReport(tracked=tuple(tracked), untracked=tuple(untracked))

    def extra_files(self, dest: Path, source_path: str) -> tuple | None:
        """Files present in the checkout but NOT tracked by git — the local additions an update
        carries into the fresh candidate. `None` means the inventory could not be taken (fail
        closed: the caller refuses rather than silently dropping the operator's files).

        `ls-files --others` WITHOUT `--exclude-standard`, so `.gitignore`d files are included
        too: a stack's own log or settings file is usually ignored, and it is exactly what has
        to survive. It also lists every file INDIVIDUALLY (`git status --ignored` collapses an
        ignored directory to `logs/`), never reports FIFOs/sockets/devices, and skips empty
        directories — we carry files, not empty trees. The one collapsed entry is an untracked
        nested Git repository (`lib/foo/`), which `carry_extras` refuses by name.

        Regenerable artifacts are filtered by the SAME predicate `dirty_report` uses, so
        `build/`, `.run/` and a component's declared `bin` stay disposable in both."""
        anomaly = _git_marker_anomaly(dest)
        if anomaly is None:
            return ()
        if anomaly:
            return None
        r = self.system.runner.run(["git", "-C", str(dest), "ls-files", "-z", "--others"], 10.0)
        if r.returncode != 0:
            return None
        bins = self._path_bins(source_path)
        return tuple(sorted(
            p for p in (r.stdout or "").split("\0")
            if p and p.split("/", 1)[0] not in _ADOPT_IGNORE_NAMES and p not in bins))

    # -- source ownership registry (transactional with activation) ----------

    def _path_patches(self, source_path: str) -> tuple:
        """Every LHPC-shipped patch a consumer's build step applies to `source_path`."""
        return tuple(sorted({p for stack in self.stacks for c in stack.components
                             if c.source and c.source.path == source_path
                             for p in c.source.patches}))

    def _path_consumers(self, source_path: str) -> tuple:
        """Every manifest component id consuming `source_path` (the shared-checkout set)."""
        out = []
        for stack in self.stacks:
            for c in stack.components:
                if c.source and c.source.path == source_path:
                    out.append(c.id)
        return tuple(out)

    def _txn_meta(self, comp, spec, source: str, git_path: str) -> dict:
        """The ownership metadata carried by the journal — the AUTHORITY recovery uses to
        complete the registry record. `git_path` points at the staged tree (candidate FD-pinned
        path)."""
        head = self.system.runner.run(["git", "-C", git_path, "rev-parse", "HEAD"], 5.0)
        return {
            "selector": source,
            "resolved_commit": (head.stdout or "").strip() if head.returncode == 0 else "",
            "remote": self.config.remotes.get(comp.id) or spec.remote or "",
            # LIVE membership merge: an updated shared checkout factually serves every
            # DECLARED consumer again, PLUS whoever the existing record already lists —
            # a departure (uninstall of one sharer) survives unrelated re-adopts only
            # until the checkout is genuinely refreshed for everyone.
            "components": sorted(set(self._path_consumers(spec.path))
                                 | self._record_members(spec.path)),
        }

    def _record_members(self, source_rel: str) -> set:
        from . import source_registry
        state, rec, _why = source_registry.record_state(self.paths, source_rel)
        return set(rec.components) if state == "valid" else set()

    @staticmethod
    def _valid_meta(meta) -> bool:
        """Strict validation of a journal's ownership metadata (untrusted persisted input).
        `had_prior` (update vs fresh-install evidence for recovery rollback) is a required
        bool."""
        if not isinstance(meta, dict):
            return False
        # Unknown extra fields (e.g. `strategy`) are ignored, not required.
        for f in ("selector", "resolved_commit", "remote"):
            if not isinstance(meta.get(f), str):
                return False
        if meta["selector"] not in ("pinned", "dev", "stable"):
            return False
        if not isinstance(meta.get("had_prior"), bool):
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
            adopted_at=_time.time(), txn_id=txn_id,
            components=tuple(meta["components"])))

    # -- source activation transaction (durable + recoverable) -------------

    # -- source activation transaction (durable, strictly-trusted journal) --
    #
    # The journal NEVER stores trusted absolute paths. It records logical, validated
    # RUNTIME-RELATIVE names; recovery derives the real paths from the runtime root and
    # rejects anything that is absolute, escaping, symlinked, or that does not match the
    # controller's candidate/prior naming patterns. An invalid journal is RETAINED and
    # blocks the affected source — it is never followed or deleted blindly.

    _VALID_STATES = ("planned", "prior-archived", "carrying", "activated", "prior-dirty-retained")

    def _txn_dir(self) -> Path:
        return self.paths.under("state", "source-txn")

    def _journal_path(self, dest: Path) -> Path:
        # Journal identity is bound to the FULL managed runtime-relative source path, not the
        # basename: `src/a/app` and `src/b/app` get distinct journals (readable prefix +
        # SHA-256 digest of `source_rel`). Recovery re-derives this and refuses any journal
        # whose filename does not match its declared source (a basename-only
        # `app.json` is retained and blocks, never silently migrated).
        import hashlib

        from . import validators
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
        # every consumer of one shared checkout (kiss-tnc + kiss-serial -> src/loraham-kiss-tnc)
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
                         txn_id: str, meta: dict, idents: dict) -> str:
        """v5 journal: carries the OWNERSHIP metadata (`meta`) AND ctime-hardened leaf-identity
        evidence (`idents`: no-follow [dev, ino, ctime_ns] for the CANDIDATE and the archived
        PRIOR), so crash recovery can re-prove the exact leaves before any destructive step —
        candidate promotion, prior restore, and prior cleanup all verify identity first. `ctime_ns`
        defeats inode recycling (dev+ino alone is forgeable). Only v5 is ever written; older
        v2/v3/v4 journals are still PARSED at recovery but retained-as-unprovable (never an
        unsafe automatic cleanup)."""
        import json
        payload = {
            "version": 5, "state": state,
            "source_rel": self._source_rel(dest),
            "prev_rel": self._source_rel(prev),
            "candidate_rel": self._source_rel(staging),
            "txn_id": txn_id, "meta": meta, "idents": idents,
        }
        return json.dumps(payload)

    def _create_journal(self, dest: Path, prev: Path, staging: Path, meta: dict, idents: dict):
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
            self._journal_payload(dest, prev, staging, state, jh["txn_id"], jh["meta"],
                                  jh["idents"]))

    @staticmethod
    def _valid_idents(idents) -> bool:
        """Strict validation of v5 leaf-identity evidence (untrusted persisted input): each present
        leaf ident is [dev, ino, ctime_ns] — exactly THREE ints (bools rejected)."""
        if not isinstance(idents, dict):
            return False
        for key in ("candidate", "prev"):
            v = idents.get(key)
            if v is None:
                continue
            if (not isinstance(v, list) or len(v) != 3
                    or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
                return False
        return True

    @staticmethod
    def _v5_leaf_ident(handle):
        """Ctime-hardened [dev, ino, ctime_ns] for a captured leaf, from a FRESH view of its inode at
        THIS moment — NOT the stale creation-time handle ctime (a candidate dir's ctime changes as it
        is populated, and a rename bumps ctime). The handle's retained fd follows the inode through
        renames, so `fstat` gives the live ctime. Returns None if unreadable (the caller then records
        no ident and recovery retains)."""
        try:
            ctime = os.fstat(handle.fd).st_ctime_ns
        except OSError:
            return None
        return [handle.st_dev, handle.st_ino, ctime]

    def _v5_idents(self, handle, prior):
        """The journal idents dict — candidate and archived prior, each ctime-hardened from a FRESH
        view at this journal transition (see `_v5_leaf_ident`)."""
        return {
            "candidate": (self._v5_leaf_ident(handle) if handle is not None else None),
            "prev": (self._v5_leaf_ident(prior) if prior is not None else None),
        }

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

    def _update_cmd(self, dest: Path) -> str:
        """`lhpc update <component> --yes` for the component(s) whose source is `dest`."""
        ids = sorted({c.id for st in self.stacks for c in st.components
                      if c.source and c.source.path == self._source_rel(dest)})
        return f"lhpc update {ids[0] if ids else '<stack>'} --yes"

    def _prior_left(self, dest: Path, prev: Path, staging: Path, pident=None) -> tuple[str, list]:
        """Where a finished activation's archived prior stands (status only): ("gone", []) —
        moved away or removed, the active source a directory; ("kept", holders) — still at
        `.prev` AS RECORDED (`pident`, compared as the caller passes it), with what the in-use
        probe finds can write into it now (none: []); ("unproven", []) — at `.prev` but not the
        identity the journal recorded; ("", []) — anything else (an interrupted removal, an
        unsafe parent): the next command decides."""
        from . import source_fs
        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                if txn.leaf_kind(prev.name) == "absent":
                    gone = (txn.leaf_kind(self._prev_quarantine(prev, staging)) == "absent"
                            and txn.usable(dest.name))
                    return ("gone" if gone else ""), []
                if txn.leaf_kind(prev.name) != "dir":
                    return "", []
                if not source_fs.ident_matches(txn.fd, prev.name, pident):
                    return "unproven", []
                return "kept", source_fs.holders(txn.fd, prev.name)
        except (OSError, PathContainmentError):
            return "", []

    def pending_states(self) -> list[tuple[str, str, str]]:
        """`(source path, state word, what resolves it)` for every source transaction still
        journaled — what `lhpc status` shows (file reads and the in-use probe; nothing written).
        A finished activation whose archived prior is kept is `prior-in-use` only while the probe
        finds a process that can still write into it, else `prior-dirty`; once the prior is gone
        only the journal is left, which the next lhpc source command clears before anything
        else, so nothing is shown."""
        import json

        from . import runtime_fs
        out = []
        try:
            d = self._txn_dir()
            entries = runtime_fs.scandir_nofollow(self.paths, d)
        except (OSError, PathContainmentError):
            return [("state/source-txn", "recovery-required",
                     "the transaction directory is unsafe — inspect it by hand")]
        for name, is_link in sorted(entries):
            if not name.endswith(".json"):
                continue
            try:
                if is_link:
                    raise ValueError("a symlink")
                j = json.loads(runtime_fs.read_text_regular(self.paths, d / name,
                                                            max_bytes=1 << 20))
                dest = self._resolve_rel(j["source_rel"])
                prev = self._resolve_rel(j["prev_rel"])
                staging = self._resolve_rel(j["candidate_rel"])
                rel, state = self._source_rel(dest), j.get("state")
                # The recorded prior identity, compared as recovery compares it: an `activated`
                # journal's full [dev, ino, ctime] (its removal gate), a `prior-dirty-retained`
                # one's dev+ino (late local changes are expected there). A journal rewrite that
                # failed after our own rename moved the prior's ctime leaves an `activated` journal
                # that does not match: recovery-required, never a named state the journal does not
                # hold.
                pi = (j.get("idents") or {}).get("prev")
            except (OSError, PathContainmentError, ValueError, KeyError, TypeError, AttributeError):
                out.append((f"state/source-txn/{name}", "recovery-required",
                            "an unreadable transaction journal — inspect it by hand"))
                continue
            pi = pi if state == "activated" or not isinstance(pi, list) else pi[:2]
            where, held = (self._prior_left(dest, prev, staging, pi)
                           if state in ("prior-dirty-retained", "activated") else ("", []))
            if where == "gone":
                continue                            # only the journal: cleared by the next command
            if where == "unproven":
                out.append((rel, "recovery-required", f"the archived prior at "
                            f"{self._source_rel(prev)} is kept but is not provably the one the "
                            "journal recorded (a journal update did not complete) — nothing was "
                            "deleted; once nothing uses it, " + self._prior_dirty_remedy(dest)))
                continue
            if where == "kept" and state == "activated" and held:
                out.append((rel, "prior-in-use", f"the archived prior at {self._source_rel(prev)} "
                            "is kept while another process can still write into it ("
                            + ", ".join(held) + "); once that has ended, the next lhpc source "
                            "command removes it: " + self._update_cmd(dest)))
            elif where == "kept":
                out.append((rel, "prior-dirty", f"the archived prior at {self._source_rel(prev)} "
                            "is kept: it may hold changes of yours and lhpc never deletes it; "
                            + self._prior_dirty_remedy(dest)))
            else:
                out.append((rel, "update-interrupted", "the next lhpc source command finishes "
                            "or rolls it back (" + self._update_cmd(dest) + "); if it cannot, it "
                            "says recovery-required and names what to inspect"))
        return out

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
        from . import runtime_fs
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
        # Pre-clone records FIRST: a record whose source still has a journal is cleared before that
        # journal's recovery can remove the journal (the journal, not the record, owns the candidate).
        for name, is_link in entries:
            if not is_link and name.endswith(".staging"):
                msg = self._recover_staged_clone(d / name)
                if msg:
                    out.append(msg)
        for name, is_link in entries:
            if is_link:
                out.append(f"recovery-required: journal {name} is a symlink (retained)")
                continue
            if not name.endswith(".json"):
                continue
            out.append(self._recover_one(d / name))
        return out

    # -- the pre-clone record --
    #
    # A candidate is staged (cloned or copied, up to `_CLONE_TIMEOUT_S`) BEFORE its journal exists,
    # so a crash in that window left a tree nothing names — and without identity evidence nothing is
    # deleted. A `<journal stem><candidate name>.staging` leaf names it for that window. It is not
    # `*.json`, so nothing that blocks on a pending journal ever sees a clone in progress.

    def _staged_clone_path(self, dest: Path, staging: Path) -> Path:
        return self._txn_dir() / f"{self._journal_path(dest).stem}{staging.name}.staging"

    def _staged_clone_payload(self, dest: Path, staging: Path, ident) -> str:
        import json
        return json.dumps({"state": "staging", "source_rel": self._source_rel(dest),
                           "candidate_rel": self._source_rel(staging), "ident": ident})

    @contextmanager
    def _staged_clone_record(self, dest: Path, staging: Path):
        """Hold the record of one staging, written before the candidate exists; yields its
        `OwnedMarker`, or None when it cannot be written (best-effort, like the clone log: the
        staging then runs as it did before records existed). On exit — normal, an exception or a
        Ctrl-C — it is removed only once the candidate is gone or a journal owns it; otherwise it
        stays, and recovery resolves the candidate it names (`_recover_staged_clone`)."""
        from . import runtime_fs, source_fs
        try:
            rec = runtime_fs.open_marker_excl(self.paths, self._staged_clone_path(dest, staging),
                                              self._staged_clone_payload(dest, staging, None))
        except (OSError, PathContainmentError) as exc:
            rec = None
            stderr_line(f"staging record for {staging.name} could not be created — install "
                        f"continues without it: {type(exc).__name__}: {' '.join(str(exc).split())}")
        try:
            yield rec
        finally:
            if rec is not None:
                try:
                    settled = (source_fs.leaf_kind(self.paths, staging) == "absent"
                               or source_fs.leaf_kind(self.paths,
                                                      self._journal_path(dest)) != "absent")
                except (OSError, PathContainmentError):
                    settled = False                  # unprovable: keep the record
                if settled:
                    rec.remove()
                rec.close()

    def _create_recorded(self, txn, rec, dest: Path, staging: Path):
        """Create the candidate and record its identity. A stop while it is recorded (Ctrl-C)
        removes it here, on the live handle: an unrecorded candidate is one recovery can never
        prove, so it would stay for the operator."""
        handle = txn.create_candidate(staging.name)
        try:
            self._note_staged(rec, dest, staging, handle)
        except BaseException:
            self._cleanup_owned_staging(txn, handle, staging.name)
            raise
        return handle

    def _note_staged(self, rec, dest: Path, staging: Path, handle) -> None:
        """Record the candidate's [dev, ino] right after its creation, before anything is written
        into it (No ctime: the clone itself changes the directory's.); `handle=None` clears it
        before a restage creates the next one. Best-effort: a failure is one stderr line and the
        staging goes on; recovery then finds the previous value — no inode, or the removed
        candidate's (another inode at that name) — and keeps the candidate and names it, never
        removes it."""
        if rec is None:
            return
        what = f"staging record {rec.name} could not record the candidate — install continues"
        ident = None if handle is None else [handle.st_dev, handle.st_ino]
        if best_effort(lambda: rec.rewrite(self._staged_clone_payload(dest, staging, ident)),
                       what=what) is False:
            stderr_line(what)

    def _recover_staged_clone(self, jf: Path) -> str:
        """Resolve ONE pre-clone record ("" = nothing to report). Its writer held the source-path
        lock for the whole staging, and a flock dies with its process, so holding that lock here
        proves the staging dead; a busy lock leaves everything alone. A source with a journal: the
        journal owns the candidate, so only the record is cleared. Otherwise the named candidate is
        removed only on the identity its record holds ([dev, ino], `_note_staged`), and the record
        is cleared once the candidate is gone. A candidate with no recorded identity, or another
        inode at the recorded name, is never removed: it is kept with its record and the result
        names the path for the operator."""
        import json

        from . import reslock, runtime_fs, source_fs
        try:
            marker = runtime_fs.open_existing_marker(self.paths, jf)
        except (OSError, PathContainmentError):
            return f"staging record {jf.name} unreadable/unsafe (retained)"
        try:
            try:
                j = json.loads(marker.read())
                dest = self._resolve_rel(j["source_rel"])
                staging = self._resolve_rel(j["candidate_rel"])
                ident = j["ident"]
                if j.get("state") != "staging" or not (ident is None or (
                        isinstance(ident, list) and len(ident) == 2 and all(
                            isinstance(x, int) and not isinstance(x, bool) for x in ident))):
                    raise ValueError("bad state/ident")
            except (OSError, ValueError, KeyError, TypeError):
                return f"staging record {jf.name} invalid (retained)"
            if (str(dest) not in self._managed_source_dests()
                    or not self._is_candidate_name(dest, staging)
                    or jf.name != self._staged_clone_path(dest, staging).name):
                return f"staging record {jf.name} names no managed candidate (retained)"
            try:
                with reslock.operation_lock(self.paths,
                                            self._source_lock_key(self._source_rel(dest)),
                                            "recover", dest.name):
                    if source_fs.leaf_kind(self.paths, self._journal_path(dest)) != "absent":
                        why = self._journal_owns_staging(dest, staging)
                        if why:
                            return (f"staging record {jf.name} kept: {why}; check {staging} "
                                    "and its journal by hand (the record is cleared by the "
                                    "next lhpc source command once one of them proves it)")
                        kind = "staging record cleared: its journal owns the candidate"
                    else:
                        with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                            if txn.leaf_kind(staging.name) != "absent":
                                # Only the recorded identity proves the directory is this
                                # staging's. None recorded (a stop between its creation and
                                # its record), or another inode at the name: never removed —
                                # not by its pathname, its owner, its emptiness or its age.
                                ok = ident is not None and source_fs.remove_bound(
                                    txn.fd, staging.name, ident)[0]
                                if not ok:
                                    return (f"a staging directory this run cannot prove as its "
                                            f"own: {staging}; remove it by hand after checking "
                                            f"(its record {jf.name} is kept until then and "
                                            "cleared by the next lhpc source command)")
                                txn.fsync()
                        kind = "removed an interrupted clone"
                    return (f"recovered {dest.name}: {kind}" if marker.remove()
                            else f"staging record {jf.name} could not be removed (retained)")
            except reslock.ResourceBusy:
                return ""                               # the staging is alive: left alone
            except (OSError, PathContainmentError) as exc:
                return f"staging record {jf.name} not resolvable now ({exc}) (retained)"
        finally:
            marker.close()

    def _journal_owns_staging(self, dest: Path, staging: Path) -> str:
        """"" when the journal of `dest` provably owns the candidate a staging record names: no
        directory is left at that name, or the journal reads, names that candidate, and records
        the [dev, ino] of the directory there (not its ctime: the carry moves it). Otherwise why
        not — a journal PATH alone never clears a record. Assumes the source-path lock is held."""
        import json

        from . import runtime_fs, source_fs
        jp = self._journal_path(dest)
        with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
            try:
                st = os.stat(staging.name, dir_fd=txn.fd, follow_symlinks=False)
            except FileNotFoundError:
                return ""                               # the candidate is gone
        try:
            marker = runtime_fs.open_existing_marker(self.paths, jp)
        except (OSError, PathContainmentError):
            return f"its journal {jp} is unreadable/unsafe"
        try:
            j = json.loads(marker.read())
            ident = (j.get("idents") or {}).get("candidate")
            named = self._resolve_rel(j["candidate_rel"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return f"its journal {jp} is invalid"
        finally:
            marker.close()
        if named != staging:
            return f"its journal {jp} names another candidate"
        if not (stat.S_ISDIR(st.st_mode) and isinstance(ident, list) and len(ident) >= 2
                and ident[:2] == [st.st_dev, st.st_ino]):
            return f"its journal {jp} records no identity matching {staging}"
        return ""

    def _recover_one(self, jf: Path) -> str:
        """Resolve ONE journal under an OWNED marker handle: open the existing regular journal
        no-follow (retaining its file + parent fds), read+validate the payload THROUGH that fd,
        and — if valid — finish/roll back, removing the journal via `OwnedMarker.remove()` so a
        journal replaced after validation but before removal is never removed (the replacement
        is retained). The marker fds always close."""
        import json

        from . import reslock, runtime_fs
        try:
            marker = runtime_fs.open_existing_marker(self.paths, jf)
        except (OSError, PathContainmentError):
            return f"recovery-required: journal {jf.name} unreadable/unsafe (retained)"
        try:
            try:
                j = json.loads(marker.read())                  # read THROUGH the retained fd
                if j.get("version") not in (2, 3, 4, 5) or j.get("state") not in self._VALID_STATES:
                    raise ValueError("bad version/state")
                meta = None
                idents = None
                if j.get("version") == 5:
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
                return self._recover_prior_dirty(dest, prev, staging, marker)
            # GENERATIONAL FAIL-CLOSED: only a v5 journal carries ctime-hardened leaf-identity
            # evidence automatic recovery can trust. v2/v3 have no leaf identity at all; v4's
            # [dev, ino]-only identity is FORGEABLE via inode recycling (ext4 hands a substituted
            # leaf recreated on the recycled inode the same dev+ino), so a v4 journal must NOT
            # authorize a destructive restore/cleanup either. All of v2/v3/v4 retain-as-unprovable:
            # every leaf and the journal are kept with a truthful operator diagnostic.
            if j.get("version") in (2, 3, 4):
                return (f"recovery-required: journal {jf.name} is generation "
                        f"v{j['version']} (no ctime-hardened leaf-identity evidence — v4's "
                        "dev+ino identity is defeatable by inode recycling) — automatic recovery "
                        "refused; all leaves and the journal are retained. Inspect "
                        f"{self._source_rel(dest)} and its .prev/candidate siblings "
                        "manually, then remove the journal and re-adopt/update the source.")
            try:
                with reslock.operation_lock(self.paths,
                                            self._source_lock_key(self._source_rel(dest)),
                                            "recover", dest.name):
                    return self._finish_or_rollback(dest, prev, staging, marker,
                                                    meta=meta, txn_id=j["txn_id"],
                                                    idents=idents, state=j["state"])
            except reslock.ResourceBusy:
                return f"recovery-required: source {dest.name} is busy (retained)"
        finally:
            marker.close()

    def _prior_dirty_remedy(self, dest: Path) -> str:
        """The one command that resolves `prior-dirty`: the operator moves the archived prior out
        of src/ (keeping what is in it). Nothing else is needed: `status` no longer shows it, and
        the next lhpc source command clears the journal before it does anything else."""
        prev = dest.with_name(f".{dest.name}.prev")
        return (f"resolve it with one command, which keeps its content out of src/: mv {prev} "
                f"{self.paths.runtime_root.parent / (dest.name + '.prev.saved')}")

    def _prior_unrecorded(self, dest: Path, prev: Path, why: str) -> str:
        """The archived prior is kept, but the journal could not record why: the journal is the
        truth, so this is `recovery-required`, never `prior-in-use` / `prior-dirty`."""
        return (f"recovery-required for {dest.name}: the update activated the new source and "
                f"its archived prior at {self._source_rel(prev)} is kept ({why}), but the "
                "transaction journal under state/source-txn could not record that (left as it "
                "was) — nothing was deleted; once nothing uses the prior, "
                + self._prior_dirty_remedy(dest))

    def _recover_prior_dirty(self, dest: Path, prev: Path, staging: Path, marker) -> str:
        """`prior-dirty`: the new source is active, the archived prior holds late local changes
        and is never deleted here. Once the operator has moved it away (`_prior_dirty_remedy`) —
        no `.prev` and no quarantine of it left, the active source a directory — the journal is
        cleared; until then it is reported, with that command."""
        from . import reslock, source_fs
        try:
            with reslock.operation_lock(self.paths,
                                        self._source_lock_key(self._source_rel(dest)),
                                        "recover", dest.name):
                gone = (source_fs.leaf_kind(self.paths, prev) == "absent"
                        and source_fs.leaf_kind(self.paths, prev.with_name(
                            self._prev_quarantine(prev, staging))) == "absent"
                        and source_fs.leaf_kind(self.paths, dest) == "dir")
                if gone:
                    return (f"recovered {dest.name}: prior-dirty cleared (the archived prior was "
                            "moved away)" if marker.remove() else
                            f"recovery-required for {dest.name}: journal could not be removed "
                            "(retained)")
        except reslock.ResourceBusy:
            return f"recovery-required: source {dest.name} is busy (retained)"
        except (OSError, PathContainmentError):
            pass
        return (f"recovery-required: prior-dirty — {self._source_rel(dest)} finished activating, "
                f"but its archived prior at {self._source_rel(prev)} contains late local changes "
                "and is never deleted by lhpc; " + self._prior_dirty_remedy(dest))

    def _prev_dirty_scan(self, txn, dest: Path, prev: Path, prev_ident=None, why=None):
        """FINAL scan of the archived prior, BOUND to its leaf: capture the `.prev` leaf
        no-follow, prove its identity (v4 evidence when available), and scan through the
        captured fd-pinned path. Returns True (RETAIN `.prev` — it still holds something the
        operator would lose), False (nothing to keep; the cleanup may destroy it), or None
        (unprovable — the caller retains everything).

        The archive is about to be DESTROYED, so "keep it" is decided by proof, in two parts:
          * an upstream file modified inside `.prev` after the earlier checks — the operator's
            edit, never silently discarded;
          * a local ADDITION `.prev` still holds that is not provably present in the ACTIVE
            source. Carrying additions forward does not license trusting that the carry ran:
            an activation completed by crash RECOVERY may have died between the archive and
            the carry, and a file added to `.prev` after the carry is not in the active tree
            either. `source_fs.extras_preserved` re-proves each one against the live tree.

        (An update requires the affected stacks stopped, so nothing is appending to those files
        while the proof runs.)"""
        from . import source_fs
        try:
            if txn.leaf_kind(prev.name) != "dir":
                return False                       # symlink/absent prior: nothing scannable
            h = txn.capture_leaf(prev.name)
        except (OSError, PathContainmentError):
            return None
        try:
            # dev+ino here (a read-only dirty probe); the DESTRUCTIVE gate `_prev_cleanup_ok`
            # enforces the full v5 ctime before any removal. `prev_ident` may be v5 (3-element).
            if prev_ident is not None and [h.st_dev, h.st_ino] != list(prev_ident[:2]):
                return None                        # substituted -> existing retention path
            pinned, rel = Path(h.pinned_path()), self._source_rel(dest)
            if self.dirty_report(pinned, rel).blocks_update():
                if why is not None:
                    why["why"] = ("the archived prior holds modifications to the upstream "
                                  "source")
                return True
            rels = self.extra_files(pinned, rel)
            if rels is None:
                return None                        # cannot inventory -> retain everything
            if not rels:
                return False
            if txn.leaf_kind(dest.name) != "dir":
                return None                        # nothing to prove the additions against
            dh = txn.capture_leaf(dest.name)
            try:
                unproven = source_fs.extras_preserved(h.fd, dh.fd, rels)
            finally:
                dh.close()
            if unproven:
                if why is not None:
                    why["why"] = f"a local addition is not preserved in the new source: {unproven}"
                return True
            return False
        except (OSError, PathContainmentError):
            return None
        finally:
            h.close()

    def _prev_extras(self, txn, dest: Path, prev: Path):
        """Local additions the archived prior still holds: `()` (none, or nothing scannable),
        a tuple of relative paths, or None (unprovable). Recovery asks this BEFORE promoting a
        staged candidate — see `_finish_or_rollback`."""
        try:
            if txn.leaf_kind(prev.name) != "dir":
                return ()
            h = txn.capture_leaf(prev.name)
        except (OSError, PathContainmentError):
            return None
        try:
            return self.extra_files(Path(h.pinned_path()), self._source_rel(dest))
        except (OSError, PathContainmentError):
            return None
        finally:
            h.close()

    @staticmethod
    def _prev_quarantine(prev: Path, staging: Path) -> str:
        """Where this transaction's archived prior is renamed for its removal: named after the
        candidate the journal records before that rename, so recovery finds an interrupted
        removal (`.<prev>.quarantine-<pid>-<ns>`, the shape `source_fs.is_quarantine_name`
        knows)."""
        return f".{prev.name}.quarantine-{staging.name.rsplit('.candidate-', 1)[1]}"

    def _prev_cleanup_ok(self, txn, prev: Path, ident=None, active=None,
                         dest: Path | None = None, why=None, qname: str = "") -> bool:
        """Remove the archived `.prev` — IDENT-BOUND ONLY. `.prev` is the transaction's own
        quarantine (atomically detached from dest with identity proof at archive time); its
        deletion binds to the recorded (dev, ino) through content removal and re-proves it
        before the final rmdir. WITHOUT identity evidence nothing is deleted (the caller
        retains `.prev` + journal); an ABSENT `.prev` is already-clean; a substituted one
        is retained untouched.

        `active` is `(name, ident)` for the ACTIVE source, re-proven immediately before the
        removal. What licenses destroying this archive is that the new tree carries everything
        the archive held — a fact established about ONE inode. If the destination is swapped
        after that proof and before this delete, the licence belonged to a tree that is no
        longer there, so the archive is retained instead. A caller with no identity evidence
        passes `active=None` and gets the historical behaviour.

        The ident is the DIRECTORY's, so an operator's edit to a FILE inside `.prev` after the
        caller's dirty scan does not change it. With `dest`, the removal is therefore made
        atomic with respect to that scan, as `source_fs.detach_and_remove` does for uninstall:
        `.prev` is first renamed aside (a pathname writer can no longer reach it), the dirty
        scan is re-run on the renamed tree, and it is deleted only if still clean. Otherwise it
        is renamed back and retained — `why["dirty"]` set when the scan found late changes."""
        from . import source_fs
        if txn.leaf_kind(prev.name) == "absent":
            return True
        if ident is None:
            return False                           # no identity evidence -> RETAIN
        source_fs.race_seam("pre-prev-delete", prev.name)
        if active is not None:
            name, aident = active
            if aident is None or not source_fs.ident_matches(txn.fd, name, aident):
                return False                       # active leaf swapped/unprovable -> RETAIN
        target, bound = prev.name, ident
        if dest is not None:
            # The full (v5) ident is proven at the name; the detach then moves the ctime, so
            # the quarantined leaf is bound by dev+ino (the same re-proof `remove_bound` uses
            # before its rmdir).
            if not source_fs.ident_matches(txn.fd, prev.name, ident):
                return False                       # substituted -> RETAIN
            target, bound = qname or source_fs._quarantine_name(prev.name), list(ident[:2])
            try:
                txn.rename_noreplace(prev.name, target)
            except (OSError, PathContainmentError):
                return False                       # nothing moved -> RETAIN
            scan = {}
            # The rename stops pathname writers, not a process that reaches the prior another
            # way — an open descriptor, a working directory in it, a shared writable mapping:
            # one is checked BEFORE the last scan, so a write it makes later cannot land after
            # that scan in a tree about to be deleted.
            holders = source_fs.holders(txn.fd, target)
            if holders:
                scan["why"] = ("the archived prior is still in use by another process ("
                               + ", ".join(holders) + "); a later write there would be lost")
                scan["in_use"] = True
                dirty = True
            else:
                dirty = (self._prev_dirty_scan(txn, dest, prev.with_name(target), bound, scan)
                         if source_fs.ident_matches(txn.fd, target, bound) else None)
            if dirty is not False:
                try:
                    txn.rename_noreplace(target, prev.name)
                except (OSError, PathContainmentError):
                    scan["why"] = (f"{scan.get('why', 'unprovable')}; preserved at "
                                   f"{target!r} (its original path was reoccupied)")
                    scan.pop("in_use", None)        # not back at `.prev`: not retryable
                    dirty = True
                if dirty and why is not None:
                    why.update(scan, dirty=True)
                return False                       # late change/unprovable -> RETAIN
        # `allow_ipc`: `.prev` is THIS transaction's own inode-bound quarantine — a checkout a
        # stack runs from legitimately holds a runtime socket (meshcom's `.run/`), and refusing
        # it left the archive half-deleted and the whole box blocked.
        ok, _why = source_fs.remove_bound(txn.fd, target, bound, allow_ipc=True)
        if not ok:
            return False                           # substituted/unprovable -> RETAIN
        return txn.leaf_kind(target) == "absent"

    def _finish_or_rollback(self, dest: Path, prev: Path, staging: Path, marker,
                            meta: dict, txn_id: str, idents: dict, state: str = "") -> str:
        """Resolve one validated journal under ONE held source-parent FD across verification,
        rename, and cleanup. The journal is removed (via the OWNED `marker`, identity re-
        verified) ONLY once the active source is proven USABLE (via the held FD), the archived
        prior is proven removed, AND the OWNERSHIP RECORD is completed.
        Any uncertainty — including a journal replaced after validation but before removal —
        RETAINS the journal + candidate/prior evidence and yields recovery-required."""
        from . import source_fs

        def _cleared(kind: str) -> str:
            return (f"recovered {dest.name}: {kind}" if marker.remove()
                    else f"recovery-required for {dest.name}: journal could not be removed (retained)")

        def _head_state() -> object:
            """Whether dest is THIS transaction's tree: True (HEAD == journal commit),
            False (a DIFFERENT tree — rolled-back prior or a foreign occupant), or
            None (no commit recorded — unprovable, no judgement possible)."""
            if not meta.get("resolved_commit"):
                return None
            head = self.system.runner.run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
            actual = (head.stdout or "").strip() if head.returncode == 0 else ""
            return actual == meta["resolved_commit"]

        def _record_ok(ours) -> bool:
            """Complete the ownership record for a PROVEN-completed activation. The journal's
            resolved_commit is the AUTHORITY: only a dest whose actual HEAD equals it gets the
            record (a ROLLED-BACK prior — restored by an in-process rollback that retained the
            journal — must never be re-registered under the new transaction's metadata; the
            prior's own older record still describes it). An unprovable tree writes nothing."""
            if ours is not True:
                return True                       # rolled-back / unprovable -> no new record
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
            had_prior = meta.get("had_prior")
            cand_ident = idents.get("candidate")
            prev_ident = idents.get("prev")

            try:
                if txn.leaf_kind(prev.name) != "absent":
                    if prev_ident is not None and not source_fs.ident_matches(
                            txn.fd, prev.name, prev_ident):
                        return (f"recovery-required for {dest.name}: archived prior was "
                                "substituted (everything retained)")
                    # IDENT-BOUND destructive step: the recorded candidate identity is
                    # REQUIRED (recovery runs only for v5 journals) and stays bound
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

        def _drop_prev(txn, active_ident):
            """Remove the archived prior of a COMPLETED activation: None when it is gone, else
            the recovery-required verdict. Late local changes in it retain it for the operator
            (journal marked operator-only); an unprovable or substituted prior is retained.
            A removal that was interrupted after the prior was renamed to its quarantine is put
            back first (dev+ino: the rename moved its ctime) and finished the same way."""
            pident = idents.get("prev")
            if txn.leaf_kind(prev.name) == "absent":
                qname = self._prev_quarantine(prev, staging)
                if txn.leaf_kind(qname) == "absent":
                    return None
                held = prev.with_name(qname)
                why = "not provably the archived prior"
                if pident is not None and source_fs.ident_matches(txn.fd, qname, pident[:2]):
                    try:
                        txn.rename_noreplace(qname, prev.name)
                        txn.fsync()
                        why = ""
                    except (OSError, PathContainmentError) as exc:
                        why = str(exc)
                if why:
                    return (f"recovery-required for {dest.name}: the removal of the archived "
                            f"prior was interrupted and {held} cannot be finished ({why}) — "
                            f"retained; inspect it, then remove it by hand (rm -rf {held}) and "
                            "the journal under state/source-txn")
                pident = list(pident[:2])
            source_fs.race_seam("pre-prev-cleanup", str(dest))
            prev_why: dict = {}
            dirty = self._prev_dirty_scan(txn, dest, prev, pident, prev_why)
            if dirty is None:
                return (f"recovery-required for {dest.name}: archived prior "
                        "could not be proven (journal + prior retained)")
            if not dirty and not self._prev_cleanup_ok(
                    txn, prev, pident, active=(dest.name, active_ident),
                    dest=dest, why=prev_why, qname=self._prev_quarantine(prev, staging)):
                if not prev_why.get("dirty"):
                    return (f"recovery-required for {dest.name}: archived prior "
                            "could not be removed or was substituted (journal + "
                            "prior retained)")
                dirty = True
            if dirty and prev_why.get("in_use"):
                # Only in use: the journal stays `activated` and the next recovery retries. The
                # rename aside and back moved the prior's ctime: it is recorded again, bound by
                # the dev+ino just proven (our own rename, as after every rename here).
                recorded = False
                try:
                    st = os.stat(prev.name, dir_fd=txn.fd, follow_symlinks=False)
                    if pident is not None and [st.st_dev, st.st_ino] == list(pident[:2]):
                        recorded = marker.rewrite(self._journal_payload(
                            dest, prev, staging, "activated", txn_id, meta,
                            {**idents, "prev": [st.st_dev, st.st_ino, st.st_ctime_ns]}))
                except OSError:
                    pass
                if not recorded:                      # the journal is the truth: no named state
                    return self._prior_unrecorded(dest, prev, prev_why.get("why", "in use"))
                return (f"recovery-required for {dest.name}: prior-in-use — the archived prior "
                        f"at {self._source_rel(prev)} is kept ({prev_why.get('why', '')}); "
                        "once that process has ended, the next lhpc source command removes it: "
                        + self._update_cmd(dest))
            if dirty:
                # LATE LOCAL CHANGES inside the archived prior: mark the
                # transaction operator-only so no automatic recovery ever
                # deletes it; the active source + its record stay coherent.
                if not marker.rewrite(self._journal_payload(
                        dest, prev, staging, "prior-dirty-retained", txn_id, meta, idents)):
                    return self._prior_unrecorded(dest, prev,
                                                  prev_why.get("why", "late local changes"))
                return (f"recovery-required for {dest.name}: prior-dirty — activation is "
                        f"complete, but the archived prior at "
                        f"{self._source_rel(prev)} contains late local changes "
                        f"({prev_why.get('why', 'unprovable')}) and is never deleted by lhpc; "
                        + self._prior_dirty_remedy(dest))
            return None

        try:
            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
                unproven_carry = False
                # The archive rename (dest -> .prev) happens BEFORE the journal records the prior's
                # new ctime. A crash in between leaves a `planned` journal, no dest, and the prior
                # at `.prev` provable by dev+ino only — for exactly that state and leaf the
                # recorded ctime is not required. With dest present the full ident stays.
                if (state == "planned" and idents and idents.get("prev")
                        and txn.leaf_kind(dest.name) == "absent"):
                    idents = {**idents, "prev": list(idents["prev"][:2])}
                # Likewise the promotion (staging -> dest) before the `activated` refresh: with no
                # staging leaf left, the candidate can only be at dest, reached through that rename.
                # A candidate still at `staging` keeps its full proof (promotion, rollback-delete).
                if (state == "prior-archived" and idents and idents.get("candidate")
                        and txn.leaf_kind(staging.name) == "absent"):
                    idents = {**idents, "candidate": list(idents["candidate"][:2])}
                # Not the carry: `carrying` is written before the carry writes into the candidate,
                # which moves its ctime for as long as the carry runs. A candidate in `carrying`
                # keeps its FULL recorded identity — a stop inside the carry leaves one that no
                # longer matches, and it is retained and named, never removed on dev+ino.
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
                    why = _drop_prev(txn, idents.get("candidate"))
                    if why:
                        return why
                    # This transaction's own candidate never became the active tree: remove it on
                    # its FULL v5 identity, or it stays on disk for good. An unprovable leaf stays.
                    cand_ident = idents.get("candidate") if idents else None
                    if cand_ident is not None and txn.leaf_kind(staging.name) != "absent":
                        source_fs.remove_bound(txn.fd, staging.name, cand_ident)
                    return _cleared("active source intact")
                if txn.leaf_kind(staging.name) != "absent" and txn.leaf_kind(dest.name) == "absent":
                    # PROMOTION IS CARRY-BLIND. The interruption may have landed anywhere between
                    # the archive and the end of the carry (`carrying` says a carry was due, not
                    # how far it got), so a candidate promoted here cannot be shown to hold the
                    # local additions the archived prior still has. Rather than complete an activation
                    # that would then have `.prev` (and the additions with it) cleaned away, roll
                    # the transaction back below: the prior returns intact WITH its additions, and
                    # the next update stages afresh and carries them again. An UNPROVABLE
                    # inventory takes the same path — never the destructive one.
                    #
                    # IDENTITY FIRST: an archived prior that cannot be proven to be the journal's
                    # own is not a tree this recovery may read a decision out of, let alone act
                    # on. Retain everything before inventorying it — the same fail-closed order
                    # every other destructive step here follows.
                    extras = ()
                    if txn.leaf_kind(prev.name) != "absent":
                        pi = idents.get("prev")
                        if pi is None or not source_fs.ident_matches(txn.fd, prev.name, pi):
                            return (f"recovery-required for {dest.name}: archived prior could "
                                    "not be proven before deciding the interrupted update "
                                    "(everything retained)")
                        extras = self._prev_extras(txn, dest, prev)
                    unproven_carry = extras != ()
                    if not unproven_carry:
                        cand_ident = idents.get("candidate")
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
                            # POST-promotion re-proof is dev+ino ONLY: our own rename just bumped
                            # the candidate's ctime, so the v5 ctime was already proven on
                            # `staging` above; here we only confirm the name still resolves to
                            # THAT inode (swap detection).
                            if not source_fs.ident_matches(txn.fd, dest.name,
                                                           list(cand_ident[:2])):
                                return (f"recovery-required for {dest.name}: destination is no "
                                        "longer the recorded candidate after promotion "
                                        "(everything retained)")
                            if txn.usable(dest.name):
                                if not _record_ok(_head_state()):
                                    return _rollback_record_failure(txn)
                                # The prior is no longer needed: the same proven cleanup a
                                # completed activation gets, or `.prev` blocks every later
                                # update. dev+ino, as the re-proof above.
                                return (_drop_prev(txn, list(cand_ident[:2]))
                                        or _cleared("completed interrupted activation"))
                if txn.leaf_kind(prev.name) != "absent":     # died after dest->prev: roll back
                    # An OCCUPIED dest slot (dangling symlink, file, injected dir, special
                    # leaf) is NEVER deleted to continue — retain it + `.prev` + journal.
                    if txn.leaf_kind(dest.name) != "absent":
                        return (f"recovery-required for {dest.name}: destination is occupied "
                                "by an unverified leaf (everything retained)")
                    prev_ident = idents.get("prev")
                    if prev_ident is None:
                        return (f"recovery-required for {dest.name}: no prior identity "
                                "evidence — automatic restore refused (retained)")
                    if not source_fs.ident_matches(txn.fd, prev.name, prev_ident):
                        return (f"recovery-required for {dest.name}: archived prior was "
                                "substituted (everything retained)")
                    if unproven_carry and txn.leaf_kind(staging.name) != "absent":
                        # Discard THIS transaction's candidate, ident-bound, so the rollback
                        # leaves no half-updated tree beside the restored source. Ordered AFTER
                        # the prior's identity proof above: nothing is destroyed until the tree
                        # we are rolling back TO is known to be ours. Anything unprovable
                        # retains everything instead.
                        cand_ident = idents.get("candidate")
                        if cand_ident is None:
                            return (f"recovery-required for {dest.name}: the interrupted "
                                    "update cannot be proven to have preserved local "
                                    "additions and its candidate has no identity evidence "
                                    "(everything retained)")
                        # FULL v5 identity, in every state. A candidate whose ctime does not
                        # match is not proven (dev+ino alone is forgeable through inode
                        # recycling) and is retained as evidence, the path named — `carrying`
                        # included: a stop inside the carry leaves it so, and the operator
                        # removes it after checking; the next source command then restores the
                        # prior (proven above).
                        source_fs.race_seam("pre-recovery-rollback-delete", staging.name)
                        ok, _w = source_fs.remove_bound(txn.fd, staging.name, cand_ident)
                        if not ok:
                            return (f"recovery-required for {dest.name}: the staged candidate "
                                    f"{staging} is not provably the one the journal recorded "
                                    "(or could not be removed) — everything retained; remove it "
                                    "by hand after checking, then the next lhpc source command "
                                    f"restores the prior ({self._update_cmd(dest)})")
                    try:
                        txn.rename_noreplace(prev.name, dest.name)
                        txn.fsync()
                    except (OSError, PathContainmentError):
                        return (f"recovery-required for {dest.name}: could not restore prior "
                                "(journal + candidate/prior retained)")
                    # POST-restore re-proof is dev+ino ONLY: our own rename just bumped the prior's
                    # ctime (the v5 ctime was proven on `.prev` above); confirm the name resolves to
                    # THAT inode.
                    if not source_fs.ident_matches(txn.fd, dest.name, list(prev_ident[:2])):
                        return (f"recovery-required for {dest.name}: destination is not the "
                                "restored prior (everything retained)")
                    if not txn.usable(dest.name):
                        return (f"recovery-required for {dest.name}: restored prior is not "
                                "usable (journal retained)")
                    return _cleared("rolled back to prior version" + (
                        " — the interrupted update could not be proven to have preserved the "
                        "local additions in the prior source, so it was undone rather than "
                        "completed (retry the update)" if unproven_carry else ""))
        except PathContainmentError:
            return f"recovery-required for {dest.name}: source parent unsafe (journal retained)"
        return f"recovery-required for {dest.name}: nothing to restore (journal retained)"

    def _rollback_bad_active(self, txn, dest: Path, prev: Path, handle=None) -> str:
        """Undo a just-completed activation (post-activation provenance failure, or an
        ownership-record persistence failure) via the held FD. `dest` is removed ONLY after
        re-proving it is still our captured candidate handle — never a pathname-only
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
        """Identity re-check of the staging/active candidate leaf by NAME."""
        if handle is None:
            return True
        return txn.verify_candidate(handle, name)

    def _activate_held(self, txn, dest: Path, staging: Path, meta: dict, verify_active=None,
                       handle=None, prior=None, final_dirty=None, carry=None,
                       prev_why=None) -> str:
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
            # v5 ctime-hardened idents: candidate still at `staging`, prior still at `dest`.
            idents = self._v5_idents(handle, prior)
        jh = self._create_journal(dest, prev, staging, meta, idents)
        if jh is None:
            # A journal leaf that exists blocks (injected or stale); one that could not be
            # created at all left nothing behind: the caller drops the candidate and refuses.
            return ("recovery-required"
                    if source_fs.leaf_kind(self.paths, self._journal_path(dest)) != "absent"
                    else "journal-failed")
        try:
            archived = False
            try:
                # Candidate identity BEFORE archiving anything — a substituted staging leaf
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
                    # REFRESH the prior ident: dest -> .prev renamed the prior, which bumps its
                    # ctime, so the journal must record the .prev's CURRENT ctime for recovery to
                    # re-prove it. (Candidate is untouched — still at `staging`.)
                    # With a carry to come the state is `carrying`: the carry writes into the
                    # candidate after this record, so recovery knows its ctime may have moved.
                    jh["idents"] = self._v5_idents(handle, prior)
                    if not self._update_journal(jh, dest, prev, staging,
                                                "carrying" if carry is not None
                                                else "prior-archived"):
                        raise _JournalLost()
                    # SECOND dirty scan THROUGH THE CAPTURED PRIOR HANDLE, after the archive
                    # and before promotion: a file created INSIDE the unchanged directory
                    # after the pre-archive check (pathname-based writer) is caught here.
                    # If dirty: no promotion — restore `.prev` no-clobber, re-prove its
                    # identity at the destination, and refuse truthfully (the candidate is
                    # discarded by the caller through its bound identity; the prior source
                    # and its registry record stay authoritative and consistent).
                    source_fs.race_seam("post-archive", str(dest))

                    def _restore(outcome: str) -> str:
                        """Put the archived prior back and refuse. The candidate is discarded
                        by the caller through its bound identity."""
                        try:
                            txn.rename_noreplace(prev.name, dest.name)
                        except (OSError, PathContainmentError):
                            return "recovery-required"   # slot reoccupied -> retain evidence
                        txn.fsync()
                        if prior is not None and not txn.verify_leaf(prior, dest.name):
                            return "recovery-required"   # unproven restore -> retain journal
                        if jh["marker"].remove():
                            return outcome               # truthful refusal; prior restored
                        return "recovery-required"

                    if final_dirty is not None and final_dirty():
                        return _restore("dirty")
                    # AUTHORITATIVE CARRY: the prior pathname is detached, so no pathname-based
                    # writer can add another file to it before activation — this inventory is
                    # final. A collision or an unprovable copy restores the prior and refuses;
                    # nothing is ever merged or overwritten.
                    if carry is not None:
                        # The journal says `carrying` (above), so a stop anywhere in the carry —
                        # a power loss included — is found by recovery: the candidate no longer
                        # matches its FULL recorded identity (the carry wrote into it), so it is
                        # retained and named, never removed on dev+ino, and the next source
                        # command restores the prior once it is removed by hand. Once the carry is
                        # durable (it fsyncs what it wrote), the
                        # journal records the candidate's full identity again, before the
                        # activation rename. A Ctrl-C in that window, or a record that cannot be
                        # written, is undone here while the handle still proves it.
                        try:
                            if carry():
                                return _restore("carry-failed")
                            jh["idents"] = self._v5_idents(handle, prior)
                            recorded = self._update_journal(jh, dest, prev, staging,
                                                            "prior-archived")
                        except KeyboardInterrupt:
                            if _restore("restored") == "restored":
                                self._cleanup_owned_staging(txn, handle, staging.name)
                            raise
                        if not recorded:
                            return _restore("failed-clean")
                # TIGHT re-check IMMEDIATELY before promotion (bounded only by kernel rename
                # atomicity): a substituted candidate leaf is never promoted.
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
                # REFRESH the candidate ident: candidate -> dest renamed it, bumping its ctime, so
                # the journal records the active leaf's CURRENT ctime. (Prior untouched — at `.prev`.)
                jh["idents"] = self._v5_idents(handle, prior)
                if not self._update_journal(jh, dest, prev, staging, "activated"):
                    raise _JournalLost()
                # POST-rename: the ACTIVE leaf must be exactly our captured candidate.
                if not self._verify_staged(txn, handle, dest.name):
                    raise _Substituted()
            except (_Substituted, _JournalLost):
                # A substituted candidate leaf OR a lost-ownership journal: RETAIN the
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
            # provenance — which can take time, so a candidate swap can occur DURING it.
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
            # candidate (a swap during provenance evaluation) BEFORE any `.prev`/journal
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
                prev_why = {} if prev_why is None else prev_why
                dirty = self._prev_dirty_scan(txn, dest, prev, prior_ident, prev_why)
                if dirty is None:
                    return "recovery-required"
                active = ((dest.name, [handle.st_dev, handle.st_ino])
                          if handle is not None else None)
                if not dirty and not self._prev_cleanup_ok(
                        txn, prev, prior_ident, active=active, dest=dest, why=prev_why,
                        qname=self._prev_quarantine(prev, staging)):
                    if not prev_why.get("dirty"):
                        return "recovery-required"
                    dirty = True
                if dirty and prev_why.get("in_use"):
                    # Only IN USE (no late change found): the journal stays `activated`, so the
                    # next lhpc source command's recovery retries the removal — `prior-in-use`.
                    # The rename aside and back moved the prior's ctime: recorded again, as after
                    # every rename of ours. Unrecorded, the outcome is recovery-required (below).
                    jh["idents"] = self._v5_idents(handle, prior)
                    word = "prior-in-use"
                    recorded = self._update_journal(jh, dest, prev, staging, "activated")
                elif dirty:
                    word = "prior-dirty"
                    recorded = self._update_journal(jh, dest, prev, staging,
                                                    "prior-dirty-retained")
                if dirty and not recorded:            # the journal is the truth: no named state
                    prev_why["unrecorded"] = self._prior_unrecorded(
                        dest, prev, prev_why.get("why", "late local changes"))
                    return "recovery-required"
                if dirty:
                    return word
            txn.fsync()
            return "activated" if jh["marker"].remove() else "recovery-required"
        finally:
            self._close_journal(jh)


    def _resolve_stable_tag(self, dest: str) -> str:
        """Git-only Latest-stable tag selection in a FULL clone at `dest`: the newest
        VERSION-SHAPED tag — a tag whose WHOLE name is a version, `v112` and `v1.2` and `1.5.2`
        but not `v2.8.0.7239fe8` or `1.8.2-pre` (`provenance.stable_version_tag`) — else "", and
        the caller stays on the default-branch HEAD. The remote freeze path (`service_maintenance._frozen_ref`) applies
        the SAME rule to `git ls-remote --tags`, so one selector resolves to one commit however
        the operator reaches it."""
        run = self.system.runner.run
        tags = run(["git", "-C", dest, "tag", "--list"], 10.0)
        names = [t.strip() for t in (tags.stdout or "").splitlines() if t.strip()] \
            if tags.returncode == 0 else []
        return provenance.stable_version_tag(names)

    # Slow-box budgets. A Pi 5 clones RadioLib (94k objects, 114 MB) in ~15 s and checks the
    # pin out in <1 s; a Zero 2W over Wi-Fi is roughly an order of magnitude slower on both, and
    # a checkout that writes the whole worktree to an SD card is not a 30-second operation. Both
    # stay BOUNDED — a hung git is still killed, just not a working one.
    _CLONE_TIMEOUT_S = 900.0
    _CHECKOUT_TIMEOUT_S = 300.0

    def _clone(self, spec, dest: Path, source: str, remote: str | None = None,
               expected_pin: str = "", log_fh=None) -> bool:
        from . import validators
        run = self.system.runner.run
        run_streaming = getattr(self.system.runner, "run_streaming", None)

        def took(what: str, t0: float) -> None:
            # `[git] <what> <n> s` on every return: the slow-target budget reads clone and
            # checkout times from this line (docs/test-matrix.md#slow-target-baseline).
            if log_fh is None:
                return
            try:
                log_fh.write(f"\n[git] {what} {time.monotonic() - t0:.1f} s\n")
                log_fh.flush()
            except (OSError, ValueError):
                pass

        def step(argv, timeout, what: str):
            """A post-clone git step (checkout/rev-parse/describe). Records WHY it failed in the
            adoption log: the caller can only report "clone failed", which reads as a network
            fault even when the clone finished and a LATER step timed out (a switch
            failed after 'Resolving deltas: 100%')."""
            t0 = time.monotonic()
            res = run(argv, timeout)
            took(what, t0)
            if res.returncode != 0 and log_fh is not None:
                why = (f"timed out after {timeout:.0f}s" if getattr(res, "timed_out", False)
                       else f"exit {res.returncode}")
                try:
                    log_fh.write(f"\n[fail] {what}: {why}\n"
                                 + (res.stderr.strip()[-400:] + "\n" if res.stderr.strip() else ""))
                    log_fh.flush()
                except (OSError, ValueError):
                    pass
            return res

        def clone(argv, timeout):
            # Stream `git clone --progress` LIVE into the adoption log when the runner supports
            # it: git is silent off-TTY, so a multi-minute clone over slow Wi-Fi otherwise looks
            # hung with its output buffered invisibly until completion. checkout/rev-parse/
            # describe stay on the buffered run() — their stdout is parsed.
            t0 = time.monotonic()
            if log_fh is not None and run_streaming is not None:
                res = run_streaming([*argv[:2], "--progress", *argv[2:]], timeout, log_fh)
            else:
                res = run(argv, timeout)
            took("clone", t0)
            if res.returncode != 0 and log_fh is not None and getattr(res, "timed_out", False):
                # git's own output is already in the log; a KILLED clone would otherwise just
                # stop mid-progress with no reason given.
                try:
                    log_fh.write(f"\n[fail] clone: timed out after {timeout:.0f}s\n")
                    log_fh.flush()
                except (OSError, ValueError):
                    pass
            return res

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
            if clone(["git", "clone", remote, str(dest)],
                     timeout=self._CLONE_TIMEOUT_S).returncode == 0 \
                    and dest.exists():
                ok = step(["git", "-C", str(dest), "checkout", expected_pin],
                          self._CHECKOUT_TIMEOUT_S, f"checkout {expected_pin[:12]}").returncode == 0
                if ok:
                    head = step(["git", "-C", str(dest), "rev-parse", "HEAD"], 15.0, "rev-parse")
                    ok = head.returncode == 0 and head.stdout.strip() == expected_pin
        elif spec.artifact:
            # Declared artifact source: EVERY selector resolves to the same declared artifact
            # (the maintainer's default branch) — no pin/branch/tag semantics are invented.
            ok = (clone(["git", "clone", "--depth", "1", remote, str(dest)],
                        timeout=self._CLONE_TIMEOUT_S).returncode == 0 and dest.exists())
        elif source == "dev":
            # STRICT branch semantics: with a configured branch, `--branch` makes git fail
            # when it does not exist — dev NEVER silently falls back to another ref.
            argv = ["git", "clone", "--depth", "1"]
            if spec.branch:
                argv += ["--branch", spec.branch]
            argv += [remote, str(dest)]
            ok = clone(argv, timeout=self._CLONE_TIMEOUT_S).returncode == 0 and dest.exists()
        else:
            # full clone so an arbitrary tag/commit can be checked out
            if clone(["git", "clone", remote, str(dest)],
                     timeout=self._CLONE_TIMEOUT_S).returncode == 0 \
                    and dest.exists():
                if source == "pinned":
                    # 'Known working' REQUIRES an exact expected commit — the newest
                    # operator-confirmed composition entry when one exists, else the manifest
                    # pin — and must resolve EXACTLY to it; never a silent adoption of the
                    # default branch.
                    pin = expected_pin or spec.pin_commit
                    if not pin:
                        ok = False
                    else:
                        ok = step(["git", "-C", str(dest), "checkout", pin],
                                  self._CHECKOUT_TIMEOUT_S, f"checkout {pin[:12]}").returncode == 0
                        if ok:
                            head = step(["git", "-C", str(dest), "rev-parse", "HEAD"], 15.0,
                                        "rev-parse")
                            ok = head.returncode == 0 and head.stdout.strip() == pin
                elif source == "stable":
                    # Latest stable, GIT-ONLY: the newest version-shaped tag ("release"),
                    # else the default-branch HEAD (latest main commit). The resolved commit
                    # is recorded by the ownership registry either way.
                    tag = self._resolve_stable_tag(str(dest))
                    if not tag:
                        ok = True                      # no tags at all -> default-branch HEAD
                    else:
                        ok = step(["git", "-C", str(dest), "checkout", tag],
                                  self._CHECKOUT_TIMEOUT_S, f"checkout {tag}").returncode == 0
                        if ok:
                            chk = step(["git", "-C", str(dest), "describe", "--tags",
                                        "--exact-match"], 15.0, "describe")
                            ok = chk.returncode == 0 and chk.stdout.strip() == tag
                else:                       # dev
                    ref = spec.branch or ""
                    ok = (not ref or
                          step(["git", "-C", str(dest), "checkout", ref],
                               self._CHECKOUT_TIMEOUT_S, f"checkout {ref}").returncode == 0)
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

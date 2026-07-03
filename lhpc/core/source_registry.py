"""Durable source ownership registry — LHPC's record of WHAT it adopted and WHY.

One JSON record per managed source path under `state/source-registry/`, written INSIDE the
source-activation transaction (install.py): the journal carries the record data (v3 payload), the
record is persisted after the activation rename and BEFORE the journal is cleared, and recovery
re-completes the write from the journal — so an activated source always has an ownership record,
and a source without one is either a pre-registry (legacy) adoption that must pass origin-URL
verification (`verify_or_backfill`) or is NOT LHPC's to update/uninstall.

Records are strictly-validated, descriptor-safe reads (`runtime_fs.read_text_regular`): a
symlinked/malformed/unversioned record is treated as ABSENT (fail toward "no ownership proven",
which refuses destructive operations), never followed or trusted.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import runtime_fs, validators
from .paths import Paths, PathContainmentError

REGISTRY_VERSION = 2
_SELECTORS = ("pinned", "dev", "stable", "legacy")
_STRATEGIES = ("", "adopt", "copy", "link")


@dataclass(frozen=True)
class RegistryRecord:
    source_rel: str            # runtime-relative managed source path (e.g. "src/RadioLib")
    remote: str                # the remote URL actually used ("" for pure local adoptions)
    selector: str              # pinned | dev | stable | legacy (backfilled pre-registry adoption)
    resolved_commit: str       # exact commit adopted ("" when the tree is not a git checkout)
    adopted_at: float
    txn_id: str                # source-transaction id ("" for backfilled records)
    strategy: str              # "" (config default) | adopt | copy | link
    components: tuple[str, ...]  # every manifest component consuming this source path
    link_target: str = ""      # link strategy: the EXACT validated runtime symlink target
    version: int = REGISTRY_VERSION  # records loaded from disk keep their on-disk version


def registry_dir(paths: Paths) -> Path:
    return paths.under("state", "source-registry")


def record_path(paths: Paths, source_rel: str) -> Path:
    """Record identity mirrors the journal identity: readable stem + full domain-separated
    SHA-256 of the runtime-relative source path (distinct paths can never collide)."""
    digest = hashlib.sha256(("lhpc-registry:" + source_rel).encode("utf-8")).hexdigest()
    stem = validators.path_component(source_rel.rsplit("/", 1)[-1], field="source")
    return registry_dir(paths) / f"{stem}-{digest}.json"


def _valid(d: object, source_rel: str) -> bool:
    # v1 (no link_target) records stay READABLE — but strategy identities they cannot prove
    # keep them NON-DESTRUCTIVE (see verify_identity) until re-adopted/re-confirmed.
    if not isinstance(d, dict) or d.get("version") not in (1, REGISTRY_VERSION):
        return False
    if d.get("version") == REGISTRY_VERSION and not isinstance(d.get("link_target"), str):
        return False
    for f in ("source_rel", "remote", "selector", "resolved_commit", "txn_id", "strategy"):
        if not isinstance(d.get(f), str):
            return False
    if d["source_rel"] != source_rel:                       # record must describe ITS path
        return False
    if d["selector"] not in _SELECTORS or d["strategy"] not in _STRATEGIES:
        return False
    if not isinstance(d.get("adopted_at"), (int, float)) or isinstance(d.get("adopted_at"), bool):
        return False
    comps = d.get("components")
    return isinstance(comps, list) and all(isinstance(c, str) and c for c in comps)


def write_record(paths: Paths, rec: RegistryRecord) -> bool:
    """Atomically persist (replace) the ownership record. Returns False on ANY failure so the
    activation transaction can fail closed instead of reporting an un-owned activation."""
    payload = {
        "version": REGISTRY_VERSION, "source_rel": rec.source_rel, "remote": rec.remote,
        "selector": rec.selector, "resolved_commit": rec.resolved_commit,
        "adopted_at": rec.adopted_at, "txn_id": rec.txn_id, "strategy": rec.strategy,
        "components": list(rec.components), "link_target": rec.link_target,
    }
    try:
        runtime_fs.write_marker(paths, record_path(paths, rec.source_rel),
                                json.dumps(payload), 0o644)
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def record_state(paths: Paths, source_rel: str) -> tuple:
    """TRI-STATE registry read — the registry is UNTRUSTED persistent state:

      * ("absent", None, "")       — safely proven absent (FileNotFoundError only);
      * ("valid",  record, "")     — safely readable, strictly valid;
      * ("unsafe", None, reason)   — PRESENT but malformed, symlinked, a directory, special,
                                     inaccessible, mismatched, or otherwise unreadable.

    Only "absent" may permit legacy backfill; every "unsafe" state must BLOCK destructive
    operations and confirmation with zero mutation (the leaf is retained as evidence)."""
    rp = record_path(paths, source_rel)
    try:
        raw = runtime_fs.read_text_regular(paths, rp)
    except FileNotFoundError:
        return "absent", None, ""
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unsafe", None, (f"ownership record for {source_rel!r} is present but "
                                f"unreadable/unsafe ({exc}) — resolve it manually")
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return "unsafe", None, (f"ownership record for {source_rel!r} is malformed — "
                                "resolve it manually")
    if not _valid(d, source_rel):
        return "unsafe", None, (f"ownership record for {source_rel!r} fails strict "
                                "validation — resolve it manually")
    rec = RegistryRecord(
        source_rel=d["source_rel"], remote=d["remote"], selector=d["selector"],
        resolved_commit=d["resolved_commit"], adopted_at=float(d["adopted_at"]),
        txn_id=d["txn_id"], strategy=d["strategy"], components=tuple(d["components"]),
        link_target=d.get("link_target", ""), version=int(d.get("version", 1)))
    return "valid", rec, ""


def read_record(paths: Paths, source_rel: str) -> RegistryRecord | None:
    """Convenience non-destructive read: a record only when SAFELY VALID; absent/unsafe ->
    None. Mutation authorities must use `record_state` (tri-state) instead — an unsafe
    record must BLOCK, never look absent."""
    state, rec, _ = record_state(paths, source_rel)
    return rec if state == "valid" else None


def remove_record(paths: Paths, source_rel: str) -> bool:
    """Remove the record for an uninstalled source (no-follow; missing is success)."""
    try:
        runtime_fs.unlink(paths, record_path(paths, source_rel))
        return True
    except FileNotFoundError:
        return True
    except (OSError, PathContainmentError):
        return False


def verify_or_backfill(paths: Paths, system, config, comp, dest: Path,
                       components: tuple = (), handle=None) -> tuple:
    """Ownership proof for a destructive operation on `dest`. Returns `(record, reason)`:

      * registry SAFELY VALID    -> (record, "registered");
      * registry SAFELY ABSENT   -> legacy origin-verified backfill, HANDLE-BOUND: every
        Git/origin inspection runs against the captured leaf's fd-pinned path, and the SAME
        handle is re-proven immediately before the record is persisted — a substituted path
        leaf is never inspected, authorized, or registered;
      * registry PRESENT-BUT-UNSAFE (malformed/symlinked/special/inaccessible) -> BLOCK with
        a typed reason and zero mutation;
      * anything else -> (None, reason): ownership NOT proven, the caller must refuse.

    Never mutates the source tree; the only write is the backfill record — which MUST persist
    (a failed backfill never authorizes mutation)."""
    from . import source_fs
    spec = comp.source
    rel = _rel(paths, dest)
    state, rec, why = record_state(paths, rel)
    if state == "unsafe":
        return None, why
    if state == "valid":
        return rec, "registered"
    expected = (config.remotes.get(comp.id) or spec.remote or "")
    try:
        expected = validators.remote_url(expected, field="remote") if expected else ""
    except validators.ValidationError:
        return None, "configured remote is invalid — ownership not provable"
    comps = tuple(components) or (comp.id,)
    own_handle = False
    if handle is None:
        try:
            handle = source_fs.capture_leaf(paths, dest)
            own_handle = True
        except (OSError, PathContainmentError) as exc:
            return None, f"no ownership record and the leaf is not capturable: {exc}"
    try:
        def _persist(record) -> tuple:
            # RE-PROVE the captured handle immediately before persistence: a leaf replaced
            # after inspection is never registered.
            if not source_fs.verify_leaf_path(paths, dest, handle):
                return None, ("destination was concurrently replaced during ownership "
                              "verification — nothing registered")
            if not write_record(paths, record):
                return None, ("ownership verified but the record could not be persisted — "
                              "refusing")
            return record, None
        if handle.kind == "symlink":
            # A linked adoption: LHPC owns only the symlink leaf — legitimate ONLY when the
            # manifest declares the link strategy. The EXACT captured target becomes part of
            # the durable identity.
            if (spec.strategy or "") != "link":
                return None, ("unexpected symlink at a non-linked source destination — "
                              "not an LHPC adoption; refusing")
            rec = RegistryRecord(rel, expected, "legacy", "", time.time(), "",
                                 "link", comps, link_target=handle.target)
            got, err = _persist(rec)
            return (got, "backfilled-link") if err is None else (None, err)
        if handle.kind != "dir":
            return None, (f"no ownership record and the destination is a {handle.kind} "
                          "leaf — refusing")
        pinned = Path(handle.pinned_path())
        if not (pinned / ".git").exists():
            return None, "no ownership record and not a git checkout — refusing (unknown tree)"
        if not expected:
            return None, "no ownership record and no configured remote — ownership not provable"
        r = system.runner.run(["git", "-C", str(pinned), "config", "--get",
                               "remote.origin.url"], 5.0)
        actual = (r.stdout or "").strip()
        if r.returncode != 0 or not actual:
            return None, "no ownership record and the tree has no origin remote — refusing"
        if _norm_remote(actual) != _norm_remote(expected):
            return None, (f"origin remote {actual!r} does not match the configured remote "
                          f"{expected!r} — not an LHPC-adopted tree")
        rec = RegistryRecord(rel, expected, "legacy", _head(system, pinned), time.time(), "",
                             spec.strategy or "", comps)
        got, err = _persist(rec)
        return (got, "backfilled") if err is None else (None, err)
    finally:
        if own_handle:
            handle.close()


def verify_identity(paths: Paths, system, config, comp, dest: Path,
                    components: tuple = (), handle=None) -> tuple:
    """THE authoritative CURRENT ownership-and-identity proof for an EXISTING managed source
    leaf — required (under the applicable source lock) before every destructive action
    (update overwrite, uninstall, clean) and before a known-working confirmation. A valid
    registry file alone is NOT sufficient: the leaf must match it NOW, with a POSITIVE
    identity proof per strategy. Returns `(record, "verified")` or `(None, typed-reason)` —
    the caller must fail closed.

      * registry PRESENT-BUT-UNSAFE -> BLOCK (never treated as absent);
      * `link`: the leaf must be a symlink whose EXACT readlink equals the recorded
        `link_target`; a legacy (v1) link record without a recorded target is
        NON-DESTRUCTIVE until re-adopted/re-confirmed;
      * managed directory: at least ONE positive proof must succeed — HEAD equals the
        recorded resolved commit, or the actual origin matches the recorded/effective
        remote. Path + leaf kind alone (a non-Git directory with nothing provable) is NOT
        ownership: destructive authorization is refused (re-adopt or remove manually);
      * a source without a record takes the handle-bound BACKFILL path.

    `handle` (a `source_fs.SourceLeafHandle`) BINDS the proof to a captured leaf: the kind
    comes from the capture and every git query runs against the handle's fd-pinned path —
    so the identity verified is EXACTLY the inode later re-proven at the irreversible step."""
    from . import source_fs
    rel = _rel(paths, dest)
    state, rec, why = record_state(paths, rel)
    if state == "unsafe":
        return None, why
    if state == "absent":
        return verify_or_backfill(paths, system, config, comp, dest, components,
                                  handle=handle)
    git_dest = dest if handle is None or handle.kind != "dir" else Path(handle.pinned_path())
    if handle is not None:
        kind = handle.kind
    else:
        try:
            kind = source_fs.leaf_kind(paths, dest)   # descriptor-proven, no-follow
        except PathContainmentError as exc:
            return None, f"source parent unsafe: {exc}"
    if rec.strategy == "link":
        # A LINKED adoption: LHPC's identity is the runtime symlink LEAF itself, INCLUDING
        # its exact target — a re-pointed symlink is drift. The external target tree stays
        # out of scope (mutable dev checkout, never LHPC's to pin or remove).
        if kind != "symlink":
            return None, (f"identity drift: recorded a LINKED source but the leaf is "
                          f"{kind} — refusing")
        if rec.version < 2 or not rec.link_target:
            return None, ("legacy link record without a recorded link target — "
                          "non-destructive until re-adopted (re-run install/adopt)")
        if handle is not None:
            actual_target = handle.target
        else:
            try:
                import os as _os
                actual_target = _os.readlink(dest)
            except OSError:
                return None, "identity drift: link target unreadable — refusing"
        if actual_target != rec.link_target:
            return None, (f"identity drift: link target {actual_target!r} != recorded "
                          f"{rec.link_target!r} — refusing")
        return rec, "verified"
    if kind != "dir":
        return None, (f"identity drift: recorded a managed directory but the leaf is "
                      f"{kind} — refusing")
    proven = False
    # Remote checks: the recorded remote AND the current effective remote must both match
    # the tree's actual origin (where non-empty) — a changed remote is identity drift.
    spec = comp.source
    effective = (config.remotes.get(comp.id) or (spec.remote if spec else "") or "")
    try:
        effective = validators.remote_url(effective, field="remote") if effective else ""
    except validators.ValidationError:
        return None, "configured remote is invalid — identity not provable"
    if rec.remote or effective:
        r = system.runner.run(["git", "-C", str(git_dest), "config", "--get",
                               "remote.origin.url"], 5.0)
        actual = (r.stdout or "").strip() if r.returncode == 0 else ""
        if rec.remote and _norm_remote(actual) != _norm_remote(rec.remote):
            return None, (f"identity drift: origin {actual or '(none)'!r} != recorded "
                          f"remote {rec.remote!r} — refusing")
        if effective and _norm_remote(actual) != _norm_remote(effective):
            return None, (f"identity drift: origin {actual or '(none)'!r} != configured "
                          f"remote {effective!r} — refusing")
        proven = bool(actual)                     # a positive, non-vacuous origin match
    # Commit check: the tree must still be at the exact recorded resolved commit.
    if rec.resolved_commit:
        actual_head = _head(system, git_dest)
        if actual_head != rec.resolved_commit:
            return None, (f"identity drift: HEAD {actual_head[:12] or '(unknown)'} != recorded "
                          f"{rec.resolved_commit[:12]} — the tree changed since LHPC adopted "
                          "it; refusing")
        proven = True
    if not proven:
        # Path + leaf kind alone is NOT ownership: a manually replaced clean non-Git
        # directory must never be destroyed merely because it occupies a registered path.
        return None, ("content identity unprovable (no commit or origin proof for this "
                      "managed directory) — non-destructive; re-adopt or remove it manually")
    return rec, "verified"


def _rel(paths: Paths, dest: Path) -> str:
    import os
    return os.path.relpath(str(dest), str(paths.runtime_root))


def _head(system, dest: Path) -> str:
    r = system.runner.run(["git", "-C", str(dest), "rev-parse", "HEAD"], 5.0)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def norm_remote(url: str) -> str:
    """Public alias of the remote normalizer (shared-remote coherence + known-working
    composition compatibility use the same comparison)."""
    return _norm_remote(url)


def _norm_remote(url: str) -> str:
    """Compare remotes ignoring trivial spelling differences (trailing `/`, `.git`,
    `https://` vs `git@host:` forms)."""
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("git@") and ":" in u:
        host, _, path = u[4:].partition(":")
        u = f"https://{host}/{path}"
    return u.lower()

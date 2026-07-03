"""Known-working stack compositions — operator-confirmed, coherent, durable.

A COMPOSITION is one stack's complete source state captured at a single healthy start:
every source component's exact resolved commit + selector + remote identity, plus validation
metadata (when it started, which band, and when the OPERATOR confirmed it). Compositions are
never assembled by mixing components from different starts, and are never recorded
automatically: a healthy start only persists a CANDIDATE marker; the operator's explicit
"Confirm this stack as working" action records it (deduped, newest three kept).

The "Known working" install selector resolves through ONE stack-level COMPATIBLE
composition (`compatible_composition`): the newest stored composition that covers the EXACT
current set of source-bearing component ids and whose every entry still matches the current
manifest/config identity (component id, source path, normalized effective remote, strategy,
immutable commit). Every component of the stack resolves from that single composition; when
none qualifies, EVERY component uses the manifest-pin fallback — records are never mixed.

Storage (all strict, descriptor-safe, no git on read):
  * store:     profiles/known-working/<stack>.json   {"version": 1, "compositions": [...]}
  * candidate: state/last-start/<stack>.json          (written by the start path only)
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import runtime_fs
from .paths import Paths, PathContainmentError

STORE_VERSION = 1
KEEP = 3                        # newest N distinct compositions per stack


def store_path(paths: Paths, stack_id: str) -> Path:
    from . import validators
    return paths.under("profiles", "known-working",
                       f"{validators.path_component(stack_id, field='stack')}.json")


def candidate_path(paths: Paths, stack_id: str) -> Path:
    from . import validators
    return paths.under("state", "last-start",
                       f"{validators.path_component(stack_id, field='stack')}.json")


def composition_hash(entries: dict) -> str:
    """Identity of a composition: SHA-256 over the COMPLETE sorted source identity of every
    entry — component id, commit, source path, NORMALIZED remote, and strategy — not just
    `component=commit`. Two starts are ONE composition only when their full source identities
    coincide."""
    from . import source_registry
    parts = []
    for c in sorted(entries):
        e = entries[c] or {}
        parts.append("|".join((
            c, e.get("commit", ""), e.get("source_rel", ""),
            source_registry.norm_remote(e.get("remote", "")), e.get("strategy", ""))))
    blob = ";".join(parts)
    return hashlib.sha256(("lhpc-composition:v2:" + blob).encode("utf-8")).hexdigest()


def _valid_entry(e) -> bool:
    return (isinstance(e, dict)
            and all(isinstance(e.get(f), str)
                    for f in ("commit", "selector", "remote", "source_rel")))


def _valid_composition(c) -> bool:
    if not isinstance(c, dict):
        return False
    entries = c.get("entries")
    if not isinstance(entries, dict) or not entries:
        return False
    if not all(isinstance(k, str) and k and _valid_entry(v) for k, v in entries.items()):
        return False
    return isinstance(c.get("validated"), dict) and isinstance(c.get("hash"), str)


def load(paths: Paths, stack_id: str) -> list:
    """The stored compositions, newest first. File-only + strict: a missing, symlinked,
    malformed or wrong-version store yields [] (fail toward 'no known-working history')."""
    try:
        raw = runtime_fs.read_text_regular(paths, store_path(paths, stack_id))
    except (OSError, PathContainmentError, ValueError):
        return []
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(d, dict) or d.get("version") != STORE_VERSION:
        return []
    comps = d.get("compositions")
    if not isinstance(comps, list):
        return []
    return [c for c in comps if _valid_composition(c)]


def record(paths: Paths, stack_id: str, entries: dict, validated: dict) -> tuple:
    """Record an operator-confirmed composition: dedupe by hash, prepend, keep the newest
    KEEP. Serialized by a dedicated LEAF lock — ordered strictly AFTER the source-operation
    locks (confirm_known_working holds source locks first; nothing acquires source locks
    while holding this one). Returns (ok, message)."""
    from . import reslock
    if not entries:
        return False, "no source components in the composition"
    comp = {
        "hash": composition_hash(entries),
        "entries": entries,
        "validated": dict(validated),
    }
    if not _valid_composition(comp):
        return False, "composition entries are incomplete"
    try:
        with reslock.operation_lock(paths, f"known-working-{stack_id}", "confirm", stack_id):
            existing = load(paths, stack_id)
            merged = [comp] + [c for c in existing if c.get("hash") != comp["hash"]]
            payload = {"version": STORE_VERSION, "compositions": merged[:KEEP]}
            runtime_fs.write_marker(paths, store_path(paths, stack_id),
                                    json.dumps(payload), 0o644)
    except reslock.ResourceBusy:
        return False, "another confirmation is in progress — try again"
    except (OSError, PathContainmentError, ValueError) as exc:
        return False, f"could not persist the known-working record: {exc}"
    return True, f"recorded (keeping {min(len(merged), KEEP)} of {len(merged)})"


def hashes(paths: Paths, stack_id: str) -> set:
    return {c["hash"] for c in load(paths, stack_id)}


def compatible_composition(paths: Paths, stack, effective_remote) -> dict | None:
    """THE stack-level 'Known working' resolver: the NEWEST stored composition that is a
    COMPLETE, COMPATIBLE image of the stack's current source layout — eligible only when:

      * its entry set covers EXACTLY the current source-bearing component ids;
      * every entry still matches the current manifest/config identity: same source path,
        same normalized effective remote (`effective_remote(comp)` — config override or
        manifest), same strategy form, and a non-empty immutable commit;
      * the entry carries the full identity fields (an OLDER record without them stays
        visible as history but is INELIGIBLE for source selection until re-confirmed).

    Returns that single composition's entries (used for EVERY component of the stack), or
    None — in which case every component takes the manifest-pin fallback; known-working and
    fallback commits are never mixed."""
    from . import source_registry
    current = {c.id: c for c in stack.components if c.source is not None}
    if not current:
        return None
    for comp in load(paths, stack_id=stack.id):
        entries = comp["entries"]
        if set(entries) != set(current):
            continue                                   # incomplete/over-complete -> ineligible
        eligible = True
        for cid, e in entries.items():
            spec = current[cid].source
            if "strategy" not in e or not e.get("commit"):
                eligible = False                       # pre-identity record -> re-confirm first
                break
            if e.get("source_rel") != spec.path:
                eligible = False
                break
            if e.get("strategy", "") != (spec.strategy or ""):
                eligible = False
                break
            if (source_registry.norm_remote(e.get("remote", ""))
                    != source_registry.norm_remote(effective_remote(current[cid]))):
                eligible = False
                break
        if eligible:
            return entries
    return None


def newest_commit_for(paths: Paths, stack_id: str, comp_id: str) -> str:
    """The component's commit in the NEWEST stored composition containing it ("" when none).
    HISTORY DISPLAY ONLY — source selection goes through `compatible_composition` (one
    complete compatible record for the whole stack, never per-component)."""
    for comp in load(paths, stack_id):
        entry = comp["entries"].get(comp_id)
        if entry and entry.get("commit"):
            return entry["commit"]
    return ""


def remove_store(paths: Paths, stack_id: str) -> bool:
    """Drop the whole store for a stack (Clean all). Missing is success; no-follow."""
    try:
        runtime_fs.unlink(paths, store_path(paths, stack_id))
        return True
    except (OSError, PathContainmentError):
        return False


# ---- last-start candidate marker (written by the START path, read by GETs) --------------------

def write_candidate(paths: Paths, stack_id: str, entries: dict, band: str) -> bool:
    payload = {"version": 1, "stack": stack_id, "band": band, "started_at": time.time(),
               "entries": entries, "hash": composition_hash(entries)}
    try:
        runtime_fs.write_marker(paths, candidate_path(paths, stack_id),
                                json.dumps(payload), 0o600)
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def read_candidate(paths: Paths, stack_id: str) -> dict | None:
    """Strict file-only read; malformed/symlinked/missing -> None."""
    try:
        raw = runtime_fs.read_text_regular(paths, candidate_path(paths, stack_id))
    except (OSError, PathContainmentError, ValueError):
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if (not isinstance(d, dict) or d.get("version") != 1 or d.get("stack") != stack_id
            or not isinstance(d.get("entries"), dict) or not isinstance(d.get("hash"), str)
            or not all(isinstance(k, str) and _valid_entry(v)
                       for k, v in d["entries"].items())):
        return None
    return d


def clear_candidate(paths: Paths, stack_id: str) -> None:
    try:
        runtime_fs.unlink(paths, candidate_path(paths, stack_id))
    except (OSError, PathContainmentError):
        pass                                     # a stale candidate is re-validated on read


def clear_candidate_checked(paths: Paths, stack_id: str) -> tuple:
    """Candidate-marker clearing whose FAILURE is reportable: (True, "") when the marker is
    absent or was removed; (False, reason) when a present marker could not be cleared —
    the caller must surface an INCOMPLETE result (never a fully successful operation with a
    stale, still-eligible candidate)."""
    try:
        runtime_fs.unlink(paths, candidate_path(paths, stack_id))
        return True, ""
    except FileNotFoundError:
        return True, ""
    except (OSError, PathContainmentError) as exc:
        return False, (f"last-start candidate for '{stack_id}' could not be cleared "
                       f"({exc}) — resolve the marker; it must not stay eligible for "
                       "confirmation")
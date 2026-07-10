"""Per-component source freshness cache — `state/stackupdates.json`.

A peer of `selfupdate.py`, for the SAME reason: the probe that answers "is this component's
installed source behind its remote?" runs `git ls-remote`, and no GET route may run a network
command (P0.6, `tests/test_web.py::test_get_routes_make_no_network_calls`). So the probe writes a
cached marker and GET pages read only that.

TRUTHFULNESS: every entry records the local head it was computed against (`local_head_at_check`).
Rendering resolves an entry through `effective_status()`, which returns `unchecked` whenever that
head no longer matches the component's CURRENT `ComponentStatus.source_head`. A corrupt, absent, or
stale cache therefore degrades to `unchecked` — never a false `up_to_date`, and never a stale
`behind` that keeps nagging after the operator has already updated.
"""

from __future__ import annotations

import json
import re
import threading
import time

from . import runtime_fs
from .paths import Paths, PathContainmentError

_MARKER = ("state", "stackupdates.json")

CACHE_MAX_BYTES = 64 * 1024          # a tiny status marker; anything larger is untrusted
CACHE_SCHEMA_VERSION = 1

# The recorded verdicts. `unchecked` is NEVER stored — it is what rendering derives for an absent
# or stale entry, so a missing check can never be mistaken for a passing one.
UNCHECKED = "unchecked"
UNKNOWN = "unknown"                  # nothing to compare: no remote / not installed / bad remote
UP_TO_DATE = "up_to_date"
BEHIND = "behind"
ERROR = "error"                      # the probe RAN and failed (ls-remote / rev-parse) — not "unknown"

_STORED = frozenset({UNKNOWN, UP_TO_DATE, BEHIND, ERROR})

_STR_MAX = 512
_HEX = re.compile(r"\A[0-9a-f]{4,64}\Z")

# Two writers share one process: the background check thread and the /source-check POST handler.
# The read-modify-write merge in `record()` must not interleave. (The CLI never writes this file;
# across processes the atomic marker write makes it last-writer-wins, which a status cache tolerates.)
_LOCK = threading.Lock()


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v) -> bool:
    return isinstance(v, str) and len(v) <= _STR_MAX


def _valid_entry(e) -> bool:
    """One component's record. A head must be lowercase hex (it is compared against a git sha);
    a status must be one of the STORED verdicts. Anything else means the entry cannot be
    interpreted, so it is dropped — see `_clean_components`."""
    if not isinstance(e, dict):
        return False
    if e.get("status") not in _STORED:
        return False
    for k in ("local_head_at_check", "upstream_head"):
        if k in e and not (_is_str(e[k]) and (e[k] == "" or _HEX.match(e[k]))):
            return False
    for k in ("remote", "source_path"):
        if k in e and not _is_str(e[k]):
            return False
    if "checked_at" in e and not _is_int(e["checked_at"]):
        return False
    return True


def _clean_components(comps) -> dict:
    """Keep the entries we can trust and DROP the ones we cannot — a single corrupt component
    record must not blind the operator to every other stack's status."""
    if not isinstance(comps, dict):
        return {}
    return {k: v for k, v in comps.items()
            if _is_str(k) and _valid_entry(v)}


def _valid_cache(data) -> bool:
    """Envelope-level schema check. A present `schema_version` must be exactly ours (an unknown or
    future version is rejected outright); a present `checked_at` must be an int (never a bool)."""
    if not isinstance(data, dict):
        return False
    if "schema_version" in data:
        sv = data["schema_version"]
        if not _is_int(sv) or sv != CACHE_SCHEMA_VERSION:
            return False
    if "checked_at" in data and not _is_int(data["checked_at"]):
        return False
    if "components" in data and not isinstance(data["components"], dict):
        return False
    return True


def read_cache(paths: Paths) -> dict:
    """FILE-SAFE AND SCHEMA-SAFE cached read (the ONLY thing GET pages touch). Regular-file only,
    no-follow, size-gated BEFORE the bounded read (so an oversized cache is rejected, never
    truncated). Any unsafe / missing / malformed payload returns `{}` — rendered as unchecked."""
    from . import runtime_fs as _rfs
    import stat as _stat
    try:
        # `paths.under()` realpath-checks the leaf and raises PathContainmentError (a ValueError)
        # for an ESCAPING symlink — it MUST be inside the try, so that case returns {} rather than
        # 500-ing the page (the very case no-follow defends against).
        path = paths.under(*_MARKER)
        stt = _rfs.stat_leaf_nofollow(paths, path)
        if stt is None or not _stat.S_ISREG(stt.st_mode) or stt.st_size > CACHE_MAX_BYTES:
            return {}                                # absent / symlink / non-regular / oversized
        raw = _rfs.read_text_regular(paths, path, max_bytes=CACHE_MAX_BYTES)
        data = json.loads(raw)
    except (OSError, PathContainmentError, ValueError):
        return {}
    return data if _valid_cache(data) else {}


def write_cache(paths: Paths, data: dict) -> None:
    try:
        runtime_fs.write_marker(paths, paths.under(*_MARKER), json.dumps(data), 0o600)
    except (OSError, PathContainmentError, ValueError, TypeError):
        pass                                         # cache is best-effort, never fatal


def view(paths: Paths) -> dict:
    """The cached, network-free reader for GET pages."""
    data = read_cache(paths)
    return {"checked_at": data.get("checked_at") if _is_int(data.get("checked_at")) else 0,
            "components": _clean_components(data.get("components"))}


def effective_status(entry, current_head: str) -> str:
    """Resolve a cached entry against the component's CURRENT local head.

    The verdict was computed against `local_head_at_check`. If the source has moved since (the
    operator ran Update, or Clean re-cloned it), the stored verdict describes a commit that is no
    longer installed and must NOT be shown — neither its green nor its yellow. Returns `unchecked`.
    """
    if not isinstance(entry, dict):
        return UNCHECKED
    status = entry.get("status")
    if status not in _STORED:
        return UNCHECKED
    if status == UNKNOWN:
        return UNKNOWN                               # "nothing to compare" needs no head to be valid
    at = entry.get("local_head_at_check") or ""
    if not at or not current_head or at != current_head:
        return UNCHECKED
    return status


def record(paths: Paths, results: dict, now: int | None = None) -> dict:
    """MERGE `results` (component_id -> entry) into the cache and write it atomically.

    A merge, not a replace: a single-stack check must not erase every other stack's verdict. Held
    under `_LOCK` so the background sweep and a concurrent POST cannot lose each other's writes.
    """
    stamp = int(time.time()) if now is None else int(now)
    with _LOCK:
        data = read_cache(paths)
        comps = _clean_components(data.get("components"))
        for cid, entry in results.items():
            e = dict(entry)
            e.setdefault("checked_at", stamp)
            if _valid_entry(e):
                comps[cid] = e
        out = {"schema_version": CACHE_SCHEMA_VERSION, "checked_at": stamp, "components": comps}
        write_cache(paths, out)
    return out

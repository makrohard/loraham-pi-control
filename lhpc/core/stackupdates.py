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
from .paths import PathContainmentError, Paths

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
LHPC_FIRST = "lhpc_first"            # at this LHPC's pin, and the fetched next LHPC pins another commit

_STORED = frozenset({UNKNOWN, UP_TO_DATE, BEHIND, ERROR, LHPC_FIRST})

# The lane verdict as the operator reads it (CLI detail, stack page, `status --versions`), one wording.
_LABELS = {UP_TO_DATE: "current", BEHIND: "update available", LHPC_FIRST: "update LHPC first",
           ERROR: "could not check", UNKNOWN: "unknown", UNCHECKED: "unchecked"}


def label(status: str) -> str:
    """The words for a stored or derived verdict."""
    return _LABELS.get(status, UNCHECKED)

_STR_MAX = 512
_HEX = re.compile(r"\A[0-9a-f]{4,64}\Z")

# Two writers share one process: the background check thread and the /source-check POST handler.
# The read-modify-write merge in `record()` must not interleave. (The CLI never writes this file;
# across processes the atomic marker write makes it last-writer-wins, which a status cache tolerates.)
_LOCK = threading.Lock()


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_stamp(v) -> bool:
    """A time stamp: a non-negative int below 2**63 (L24 F7, as selfupdate's)."""
    return _is_int(v) and 0 <= v < 1 << 63


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
    for k in ("local_head_at_check", "upstream_head", "pin_at_check", "lhpc_head_at_check"):
        if k in e and not (_is_str(e[k]) and (e[k] == "" or _HEX.match(e[k]))):
            return False
    for k in ("remote", "source_path"):
        if k in e and not _is_str(e[k]):
            return False
    return not ("checked_at" in e and not _is_stamp(e["checked_at"]))


def _clean_components(comps) -> dict:
    """Keep the entries we can trust and DROP the ones we cannot — a single corrupt component
    record must not blind the operator to every other stack's status."""
    if not isinstance(comps, dict):
        return {}
    return {k: v for k, v in comps.items()
            if _is_str(k) and _valid_entry(v)}


def _valid_cache(data) -> bool:
    """Envelope-level schema check. A present `schema_version` must be exactly ours (an unknown or
    future version is rejected outright); a present `checked_at` must be a stamp (_is_stamp)."""
    if not isinstance(data, dict):
        return False
    if "schema_version" in data:
        sv = data["schema_version"]
        if not _is_int(sv) or sv != CACHE_SCHEMA_VERSION:
            return False
    if "checked_at" in data and not _is_stamp(data["checked_at"]):
        return False
    return not ("components" in data and not isinstance(data["components"], dict))


def read_cache(paths: Paths) -> dict:
    """FILE-SAFE AND SCHEMA-SAFE cached read (the ONLY thing GET pages touch). Regular-file only,
    no-follow, size-gated BEFORE the bounded read (so an oversized cache is rejected, never
    truncated). Any unsafe / missing / malformed payload returns `{}` — rendered as unchecked."""
    import stat as _stat

    from . import runtime_fs as _rfs
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
    return {"checked_at": data.get("checked_at") if _is_stamp(data.get("checked_at")) else 0,
            "components": _clean_components(data.get("components"))}


def fetched_lhpc_head(paths: Paths) -> str:
    """The LHPC head the last self-update check fetched (its cache), "" without one."""
    from . import selfupdate
    return (selfupdate.read_cache(paths).get("upstream") or {}).get("upstream_head") or ""


def effective_status(entry, current_head: str, pin: str | None = None, lhpc_head: str | None = None) -> str:
    """Resolve a cached entry against the component's CURRENT local head.

    The verdict was computed against `local_head_at_check`. If the source has moved since (the
    operator ran Update, or Clean re-cloned it), the stored verdict describes a commit that is no
    longer installed and must NOT be shown — neither its green nor its yellow. Returns `unchecked`.
    The ONE rule for every reader (the page, `status --versions`): a verdict stands only on all the evidence it
    was computed from. A pinned-lane verdict also records the manifest pin (`pin_at_check`) and the LHPC head the
    self-update check had fetched (`lhpc_head_at_check`); once either moved (`pin`, `lhpc_head`: today's), it
    reads `unchecked`. An entry without that evidence (written before one verdict per lane) reads `unchecked`.
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
    if "pin_at_check" not in entry or "lhpc_head_at_check" not in entry:
        return UNCHECKED
    if entry["pin_at_check"] and (entry["pin_at_check"] != pin or entry["lhpc_head_at_check"] != (lhpc_head or "")):
        return UNCHECKED
    return status


def verdict(fresh: dict, comp, status, lhpc_head: str) -> str:
    """The ONE lane verdict of `comp` that every surface shows: its last check, on today's head, pin and LHPC."""
    return effective_status(fresh.get(comp.id), status.source_head or "", comp.source.pin_commit if comp.source else None,
                            lhpc_head)


def source_words(lane: str, status) -> tuple[str, str]:
    """(source, profile) for `lhpc status`, `status --versions` and the info card: a clean checkout reads its lane
    verdict and is a candidate only on "update available"; the probe's own facts (dirty, missing) stay."""
    if status.source_state.value not in ("match", "differs"):
        return status.source_state.value, status.profile_state.value
    return label(lane), "candidate-available" if lane == BEHIND else status.profile_state.value


def surface(paths: Paths):
    """`(comp, status) -> (source, profile)` on today's check view, for the CLI readers."""
    fresh, lhpc_head = view(paths)["components"], fetched_lhpc_head(paths)
    return lambda comp, status: source_words(verdict(fresh, comp, status, lhpc_head), status)


def pinned_target(paths: Paths, comp) -> str:
    """The commit a bare Update installs for `comp` when its source follows the PIN, else "".

    A bare Update of a pinned source is "Known working": the manifest pin. A source follows the pin when
    its ownership record says it was installed from the `pinned` selector; one from dev or stable follows
    its branch, and one without a valid record keeps the branch tip as before.
    The freshness check compares the head with this commit instead of the branch tip, which a pinned source
    never reaches, so an Update offered there could never clear itself (W204, W208)."""
    from . import source_registry
    spec = getattr(comp, "source", None)
    if spec is None or spec.artifact:
        return ""
    rec = source_registry.read_record(paths, spec.path)
    if rec is None or rec.selector != "pinned":
        return ""
    return spec.pin_commit or ""


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


def next_lhpc_pins(paths: Paths, run, head: str) -> dict:
    """{component id: pin} of the LHPC the last self-update check fetched: the shipped manifest at `head` (that
    check's `upstream_head`, `fetched_lhpc_head`), read with `git show` in this checkout (local, no network), only when that head is
    ahead of the running checkout. {} for no cache, no checkout, a head not ahead, or a manifest that cannot be
    read: no hint, never "could not check" (plan row U6)."""
    import tomllib

    from . import selfupdate
    from .manifest import ManifestError, parse_manifest
    root = selfupdate.repo_root()
    if root is None or not re.fullmatch(r"[0-9a-f]{40}", head):
        return {}
    here = run(["git", "-C", str(root), "rev-parse", "HEAD"], timeout=5.0)
    if here.returncode != 0 or here.stdout.strip() == head or run(          # strictly ahead: a newer LHPC
            ["git", "-C", str(root), "merge-base", "--is-ancestor", "HEAD", head], timeout=5.0).returncode != 0:
        return {}
    shown = run(["git", "-C", str(root), "show", f"{head}:lhpc/data/manifest.example.toml"], timeout=10.0)
    try:
        return {c.id: c.source.pin_commit for st in parse_manifest(tomllib.loads(shown.stdout))
                for c in st.components if c.source is not None and c.source.pin_commit} if shown.returncode == 0 else {}
    except (ManifestError, ValueError, KeyError, TypeError):
        return {}

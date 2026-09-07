"""The durable restart-required marker: one file per stack under `state/restart-required/`.

Set atomically WITH the config save that changed restart/build-mode parameters while the stack
ran (the config transaction renders it as its one allowed `state` target — see
`config._resolve_journal_target`), cleared on a verified stop or a successful start/restart.
This module owns the path, the schema (version 1), the safe tri-state read, the merge rules
and the best-effort clear; the service decides WHEN to write, read or clear.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import runtime_fs, validators
from .paths import PathContainmentError, Paths

MARKER_DIR = ("state", "restart-required")     # the ONLY state target of the config transaction
MARKER_VERSION = 1
MODES = ("restart", "build")


def marker_path(paths: Paths, stack_id: str) -> Path:
    return paths.under(*MARKER_DIR, f"{validators.path_component(stack_id, field='stack')}.json")


def read_marker(paths: Paths, stack_id: str) -> dict | None:
    """FILE READ ONLY, GET-safe, TRI-STATE:

      * SAFELY ABSENT (FileNotFoundError only)  -> None: no warning;
      * SAFELY VALID                            -> the marker dict;
      * PRESENT BUT UNSAFE (malformed, symlinked, a directory, special, inaccessible,
        or claiming another stack) -> {"unsafe": True, "stack": …, "reason": …}: the
        warning stays visible SAFE-SIDE with the explicit Restart action and a
        diagnostic — an unreadable marker must never look like "no restart required".
        The marker is NEVER silently cleared here (a read stays non-mutating)."""
    def _unsafe(reason: str) -> dict:
        return {"unsafe": True, "stack": stack_id, "mode": "restart", "params": [],
                "reason": reason}
    try:
        raw = runtime_fs.read_text_regular(paths, marker_path(paths, stack_id))
    except FileNotFoundError:
        return None                                   # SAFELY absent — proven
    except (OSError, PathContainmentError, ValueError) as exc:
        return _unsafe(f"restart-required marker is present but unreadable/unsafe "
                       f"({exc}) — treat as restart required; resolve the marker")
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return _unsafe("restart-required marker is malformed — treat as restart "
                       "required; resolve the marker")
    if not isinstance(d, dict) or d.get("version") != MARKER_VERSION or d.get("stack") != stack_id:
        return _unsafe("restart-required marker fails validation — treat as restart "
                       "required; resolve the marker")
    # FULL field schema (a structurally-invalid-but-parseable marker was
    # trusted — an unknown mode silently downgraded to restart, a string params value
    # iterated character-by-character in the merge, an integer raised uncaught). ONE
    # schema, here: every consumer (display AND the global-change merge) reads through
    # this function, and any invalid shape stays byte-identical, safe-side UNSAFE.
    if d.get("mode") not in MODES:
        return _unsafe("restart-required marker carries an unknown mode — treat as "
                       "restart required; resolve the marker")
    params_v = d.get("params")
    if (not isinstance(params_v, list)
            or not all(isinstance(x, str) and x
                       and not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in x)
                       for x in params_v)):
        return _unsafe("restart-required marker carries malformed params — treat as "
                       "restart required; resolve the marker")
    band_v = d.get("band", "")
    if band_v != "":
        try:
            validators.band(str(band_v), allow_both=False)
        except validators.ValidationError:
            return _unsafe("restart-required marker carries an invalid band — treat "
                           "as restart required; resolve the marker")
    if not isinstance(d.get("created_at"), (int, float)):
        return _unsafe("restart-required marker carries an invalid timestamp — treat "
                       "as restart required; resolve the marker")
    return d


def merged_payload(current: dict | None, stack_id: str, params, band: str,
                   mode: str = "restart", now: float | None = None) -> str:
    """The MERGED marker JSON for `stack_id`, given the marker committed RIGHT NOW (`current`,
    as `read_marker` returned it) so a concurrent writer's reason is merged, not overwritten.

    MERGE (a blind replace destroyed a stronger build requirement, its reasons and its band):
    build outranks restart, reasons are unioned without duplication, an existing CONCRETE band is
    retained. Unreadable CONTENT (an unsafe `current`) is replaced, never merged."""
    cur = None if current is not None and current.get("unsafe") else current
    merged_mode = "build" if (mode == "build" or (cur or {}).get("mode") == "build") else "restart"
    return json.dumps({
        "version": MARKER_VERSION, "stack": stack_id, "mode": merged_mode,
        "params": list(dict.fromkeys([*(cur or {}).get("params", []), *params])),
        "band": (cur or {}).get("band") or band,
        "created_at": time.time() if now is None else now})


def clear_marker(paths: Paths, stack_id: str) -> None:
    """Best effort — a stale marker is safe-side: the operator sees a yellow action that a
    fresh restart simply satisfies."""
    try:
        runtime_fs.unlink(paths, marker_path(paths, stack_id))
    except (OSError, PathContainmentError):
        pass

"""The durable restart-required marker: one file per stack under `state/restart-required/`.

Set atomically WITH the config save that changed restart/build-mode parameters while the stack
ran (the config transaction renders it as its one allowed `state` target — see
`config._resolve_journal_target`), cleared on a verified stop or a successful start/restart.
This module owns the path, the schema (version 1), the safe tri-state read, the merge rules
and the best-effort clear; the service decides WHEN to write, read or clear.

`launched` (optional) maps a parameter key to the value the running stack was launched with,
recorded when the save that first changed it wrote the marker. A later save that restores that
value takes the parameter off the marker again; a parameter without a recorded value (an older
marker, the global identity path) stays until the restart.
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
    if not isinstance(params_v, list) or not all(_clean(x) for x in params_v):
        return _unsafe("restart-required marker carries malformed params — treat as "
                       "restart required; resolve the marker")
    band_v = d.get("band", "")
    if band_v != "":
        try:
            validators.band(str(band_v), allow_both=False)
        except validators.ValidationError:
            return _unsafe("restart-required marker carries an invalid band — treat "
                           "as restart required; resolve the marker")
    launched_v = d.get("launched", {})
    if (not isinstance(launched_v, dict)
            or not all(_clean(k) and isinstance(v, str) for k, v in launched_v.items())):
        return _unsafe("restart-required marker carries malformed launch values — treat as "
                       "restart required; resolve the marker")
    if not isinstance(d.get("created_at"), (int, float)):
        return _unsafe("restart-required marker carries an invalid timestamp — treat "
                       "as restart required; resolve the marker")
    return d


def _clean(x) -> bool:
    """A non-empty string without control characters (a params name or a launched key)."""
    return (isinstance(x, str) and bool(x)
            and not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in x))


def key_name(key: str) -> str:
    """The parameter name a `launched` key (`<band>|<component>|<name>`) stands for."""
    return key.rsplit("|", 1)[-1]


def merged_payload(current: dict | None, stack_id: str, params, band: str,
                   mode: str = "restart", now: float | None = None,
                   launched: dict | None = None) -> str:
    """The MERGED marker JSON for `stack_id`, given the marker committed RIGHT NOW (`current`,
    as `read_marker` returned it) so a concurrent writer's reason is merged, not overwritten.

    MERGE (a blind replace destroyed a stronger build requirement, its reasons and its band):
    build outranks restart, reasons are unioned without duplication, an existing CONCRETE band is
    retained. Unreadable CONTENT (an unsafe `current`) is replaced, never merged.

    `launched`: the launch values of the keys this write adds. An already-recorded value is kept
    (the first one IS the launch value); a name added here without one makes every recorded key
    of that name untracked, so no later revert can clear it."""
    cur = None if current is not None and current.get("unsafe") else current
    merged_mode = "build" if (mode == "build" or (cur or {}).get("mode") == "build") else "restart"
    new = dict(launched or {})
    untracked = {n for n in params if not any(key_name(k) == n for k in new)}
    kept = {k: v for k, v in ((cur or {}).get("launched") or {}).items()
            if key_name(k) not in untracked}
    known = set((cur or {}).get("params") or [])
    for k, v in new.items():
        # Record only a FIRST change: a name already on the marker without a value is untracked.
        if k not in kept and (key_name(k) not in known
                              or any(key_name(x) == key_name(k) for x in kept)):
            kept[k] = v
    out = {"version": MARKER_VERSION, "stack": stack_id, "mode": merged_mode,
           "params": list(dict.fromkeys([*(cur or {}).get("params", []), *params])),
           "band": (cur or {}).get("band") or band,
           "created_at": time.time() if now is None else now}
    if kept:
        out["launched"] = kept
    return json.dumps(out)


def clear_marker(paths: Paths, stack_id: str) -> None:
    """Best effort — a stale marker is safe-side: the operator sees a yellow action that a
    fresh restart simply satisfies."""
    try:
        runtime_fs.unlink(paths, marker_path(paths, stack_id))
    except (OSError, PathContainmentError):
        pass

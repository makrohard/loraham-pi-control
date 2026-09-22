"""The controller's view of the MeshCore repeater's plugin-manager marker.

`meshcore_host` (the stack's own process, in the stack's venv) spawns upstream's plugin manager
beside the repeater and records, in `state/openhop/.lhpc-plugin-manager-active`, that a manager
owned plugins in THIS boot. The host clears the file only when the manager shut down cleanly.
A file from the current boot therefore means an unclean manager or host death may have left
plugins running out of the MeshCore venv — processes LHPC never spawned and cannot see.

The controller reads the same file before the operations that would delete or mutate what those
processes run from: the controller uninstall (`uninstall.sh --purge` removes `state/openhop`, so a
reinstall in the same boot would start a second manager beside the survivors) and the stack's
`update`, `uninstall` and `clean`. It never inspects processes: a refusal costs a reboot, never a
duplicate plugin tree.

`marker_verdict` is kept in LOCKSTEP with `meshcore_host/plugin_manager.py` (a test drives both
on the same fixtures) — the host cannot import `lhpc`.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import meshcore_mode as _meshcore_mode
from .lifecycle import current_boot_id
from .service_base import ActionResult

MARKER_NAME = ".lhpc-plugin-manager-active"
MARKER_VERSION = 1
REBOOT_HINT = ("unclean MeshCore plugin-manager ownership in this boot (a plugin manager may still "
               "own plugins) — reboot before modifying the MeshCore installation")


def marker_path(paths) -> Path:
    return paths.under("state", "openhop", MARKER_NAME)


# LOCKSTEP with meshcore_host/plugin_manager.py::marker_verdict — same text, same verdicts.
def marker_verdict(path: Path | str, current_boot: str) -> str:
    """One of "absent" (no marker), "safe" (a marker from another boot: its processes cannot have
    survived), "unsafe" (a marker from THIS boot: an unclean manager may still own plugins), or
    "unprovable" (malformed, unreadable, unknown version, or no boot id to compare against)."""
    if not current_boot:
        return "unprovable"
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unprovable"
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "unprovable"
    if not isinstance(rec, dict) or rec.get("version") != MARKER_VERSION:
        return "unprovable"
    recorded = rec.get("boot_id")
    if not isinstance(recorded, str) or not recorded:
        return "unprovable"
    return "unsafe" if recorded == current_boot else "safe"


def unclean_refusal(paths, action: str) -> ActionResult | None:
    """The refusal for `action` when the marker says an unclean manager may own plugins in this
    boot, or when that cannot be proven; None when it is safe to proceed."""
    verdict = marker_verdict(marker_path(paths), current_boot_id())
    if verdict in ("absent", "safe"):
        return None
    why = (REBOOT_HINT if verdict == "unsafe"
           else "cannot prove the MeshCore plugin-manager state (its marker is unreadable or "
                "malformed, or the boot id is unavailable) — reboot before modifying the "
                "MeshCore installation")
    return ActionResult(False, f"Refusing to {action}: {why}.",
                        details=[f"  marker: {marker_path(paths)}"],
                        data={"prep_blocked": "meshcore_plugins", "reason": "meshcore-plugins"})


def touches_meshcore(service, component_ids) -> bool:
    """Whether an operation over these component ids reaches the MeshCore stack — the stack id
    itself, any of its components (the repeater and the manager run from its venv), or a source
    another of its components consumes."""
    stack = service.stack(_meshcore_mode.STACK_ID)
    if stack is None:
        return False
    ids = {c.id for c in stack.components} | {stack.id}
    return bool(ids & set(component_ids))


__all__ = ["MARKER_NAME", "MARKER_VERSION", "REBOOT_HINT", "marker_path", "marker_verdict",
           "touches_meshcore", "unclean_refusal"]

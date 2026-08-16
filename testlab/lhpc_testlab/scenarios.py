"""Deterministic lab scenarios: one atomically-written JSON file under the runtime root,
polled by every reader (web, CLI, detached helpers, the fake daemon and fake gpsd) — no
control sockets, no re-exec. `auto_revert_s` turns a fault scenario into "recovery"
without a second write: `effective_state()` compares against CLOCK_BOOTTIME.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from lhpc.core import runtime_fs

DEFAULT = "healthy"

# Flag vocabulary (every scenario carries the complete set):
#   wifi: connected | disconnected | ap-fallback     join_result: ok | wrong-password
#   radio_433 / radio_868: READY | FAILED            gpsd: bool
#   fw_receipt: fresh | stale | absent               power_auth: yes | no
#   missing_files: [paths the LabFs reports absent]
_BASE = {"wifi": "connected", "join_result": "ok", "radio_433": "READY",
         "radio_868": "READY", "gpsd": True, "fw_receipt": "fresh",
         "missing_files": [], "power_auth": "yes"}

SCENARIOS: dict[str, dict] = {
    "healthy": dict(_BASE),
    "disconnected": dict(_BASE, wifi="ap-fallback"),
    "wrong-password": dict(_BASE, wifi="ap-fallback", join_result="wrong-password"),
    "hardware-missing": dict(_BASE, radio_433="FAILED", radio_868="FAILED",
                             missing_files=["/usr/include/lgpio.h"]),
    "degraded": dict(_BASE, radio_868="FAILED", gpsd=False, fw_receipt="stale"),
    # Starts faulty, self-heals after auto_revert_s — demonstrates the watchdog/retry
    # machinery without operator action.
    "recovery": dict(_BASE, wifi="ap-fallback", radio_868="FAILED"),
}
_AUTO_REVERT = {"recovery": 60.0}


def scenario_path(paths) -> Path:
    return paths.under("state", "testlab", "scenario.json")


def _boottime() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def apply(paths, name: str) -> dict:
    """Atomically select a scenario; returns the record. KeyError on unknown names."""
    flags = SCENARIOS[name]
    rec = {"name": name, "applied_boottime": _boottime(),
           "auto_revert_s": _AUTO_REVERT.get(name), "flags": flags}
    runtime_fs.atomic_write(paths, scenario_path(paths), json.dumps(rec, indent=1), 0o600)
    log_event(paths, f"scenario -> {name}")
    return rec


def load(paths) -> dict:
    """The stored record; a missing or malformed file is the healthy default (the lab
    must render, never crash, on a torn state file)."""
    try:
        rec = json.loads(runtime_fs.read_text_regular(paths, scenario_path(paths),
                                                      max_bytes=65536) or "")
        if isinstance(rec, dict) and isinstance(rec.get("flags"), dict):
            return rec
    except Exception:
        pass
    return {"name": DEFAULT, "applied_boottime": 0.0, "auto_revert_s": None,
            "flags": dict(SCENARIOS[DEFAULT])}


def effective_state(paths) -> dict:
    """The flags every simulator answers from, with auto-revert applied."""
    rec = load(paths)
    revert = rec.get("auto_revert_s")
    if (isinstance(revert, (int, float)) and not isinstance(revert, bool)
            and _boottime() - float(rec.get("applied_boottime") or 0.0) >= float(revert)):
        return {**dict(SCENARIOS[DEFAULT]), "_name": f"{rec.get('name')}(reverted)"}
    flags = {**dict(SCENARIOS[DEFAULT]), **rec.get("flags", {})}
    flags["_name"] = rec.get("name", DEFAULT)
    return flags


def log_event(paths, text: str) -> None:
    """Append one line to the panel's event log; never raises."""
    try:
        fh = runtime_fs.open_log_append(paths, paths.under("state", "testlab",
                                                           "events.log"))
        try:
            stamp = time.strftime("%H:%M:%S")
            fh.write(f"{stamp} {text}\n".encode())
        finally:
            fh.close()
    except Exception:
        pass


def log_command(paths, argv) -> None:
    """Append one passthrough argv to the audit trail; never raises."""
    try:
        fh = runtime_fs.open_log_append(paths, paths.under("state", "testlab",
                                                           "commands.log"))
        try:
            fh.write((" ".join(str(a) for a in argv) + "\n").encode())
        finally:
            fh.close()
    except Exception:
        pass

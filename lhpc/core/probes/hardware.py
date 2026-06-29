"""Read-only host prerequisite checks for `lhpc doctor`.

Reports presence/accessibility of relevant host facilities. It NEVER initializes
a radio or opens SPI/GPIO for operation — it only checks that device nodes and
tools exist, that `systemctl` (system and user scope) responds, and whether the
runtime root and configured source paths are present.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import System

_TIMEOUT_S = 3.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_char_device(system: System, path: str) -> Check:
    if not system.fs.exists(path):
        return Check(path, False, "absent")
    if system.fs.is_char_device(path):
        return Check(path, True, "present (character device)")
    return Check(path, False, "present but not a character device")


def check_systemctl(system: System, user: bool) -> Check:
    argv = ["systemctl"]
    if user:
        argv.append("--user")
    argv.append("is-system-running")
    res = system.runner.run(argv, timeout=_TIMEOUT_S)
    label = "systemctl --user" if user else "systemctl"
    if res.not_found:
        return Check(label, False, "not found")
    if res.timed_out:
        return Check(label, False, "timeout")
    stderr = res.stderr.lower()
    if "failed to connect to" in stderr and "bus" in stderr:
        return Check(label, False, "no bus / unavailable")
    # is-system-running may exit non-zero (e.g. "degraded") yet still prove the
    # manager responds; treat any parseable word as "responds".
    word = res.stdout.strip() or res.stderr.strip()
    return Check(label, True, f"responds ({word or 'ok'})")

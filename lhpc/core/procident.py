"""Common process-identity helpers (Linux /proc) — one tested implementation shared
by lifecycle ownership records and job markers, so PID-reuse resistance is consistent.

A bare PID is not an identity: the kernel reuses PIDs. `proc_identity()` captures the
reuse-resistant tuple (start time, pgid, sid, exec basename, argv fingerprint); a later
check compares it so a recycled PID running an unrelated process is never mistaken for
the one we launched.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path


def proc_identity(pid: int) -> dict | None:
    """Reuse-resistant identity of a LIVE pid from /proc, or None if it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        rest = stat[stat.rindex(")") + 2:].split()
        pgid, sid, starttime = int(rest[2]), int(rest[3]), int(rest[19])
    except (OSError, ValueError, IndexError):
        return None
    try:
        exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        exe = ""
    # The kernel can briefly present an EMPTY cmdline for a live process (during exec /
    # under load). An empty read is a glitch, not a different identity — retry so a
    # record and a later verify see the same stable fingerprint.
    cmd = b""
    for _ in range(5):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            cmd = b""
        if cmd:
            break
        time.sleep(0.01)
    # `argv_len` records the number of OBSERVED argv bytes: an SHA-256 of an empty
    # cmdline is still a non-empty digest, so the fingerprint alone cannot prove a real
    # argv was seen. A complete launch identity requires argv_len > 0.
    return {"starttime": starttime, "pgid": pgid, "sid": sid,
            "exec": exe, "argv_fp": hashlib.sha256(cmd).hexdigest(), "argv_len": len(cmd)}


def proc_alive(pid: int) -> bool:
    """True only if the pid exists AND is not a zombie/dead — a reaped-pending zombie
    (state Z/X) has ceased even though its pid still exists in /proc."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    try:
        state = data[data.rindex(")") + 2]
    except (ValueError, IndexError):
        return False
    return state not in ("Z", "X", "x")


def identity_complete(ident) -> bool:
    """THE single identity-completeness predicate, shared by lifecycle ownership records and
    detached job markers. A usable identity needs POSITIVE start time / pgid / sid, a
    non-empty executable basename, a non-empty argv fingerprint, and argv_len > 0. `None`, a
    missing field, or a sentinel value (e.g. `-1`) is NEVER complete — a process we cannot
    fully identify is one we cannot safely own, stop, or attribute a job to."""
    if not isinstance(ident, dict):
        return False
    for k in ("starttime", "pgid", "sid", "argv_len"):
        v = ident.get(k)
        if not isinstance(v, int) or v <= 0:
            return False
    return bool(ident.get("exec")) and bool(ident.get("argv_fp"))


def identity_matches(recorded: dict, pid: int) -> bool:
    """True if `pid` is alive and STILL the process described by `recorded`. START TIME is the
    reuse-proof identity (a recycled pid gets a NEW start time), so a reused PID fails this even
    though the number exists. exec/argv are NOT required to match: a process may legitimately exec
    into a different image after launch (e.g. a `#!/usr/bin/env bash` script: env → bash), and the
    matching start time already proves it is the same process. An INCOMPLETE record is rejected."""
    if not identity_complete(recorded):
        return False
    if not proc_alive(pid):
        return False
    now = proc_identity(pid)
    if now is None:
        return False
    return int(recorded.get("starttime", -1)) == now["starttime"]

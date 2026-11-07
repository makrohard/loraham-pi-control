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


def signal0_state(pid) -> str:
    """Signal-0 probe of a pid read from a file or another process's record: "alive", "denied" (it exists and is
    someone else's: PermissionError) or "gone". A non-positive or non-int pid is never signalled; a pid the kernel
    cannot hold (OverflowError, ValueError: 2**31 and above) names no process, so it is "gone", never an exception
    out of a status read."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "gone"
    try:
        os.kill(pid, 0)
    except PermissionError:
        return "denied"
    except (OSError, OverflowError, ValueError):
        return "gone"
    return "alive"


def pid_exists_signal0(pid: int) -> bool:
    """Signal-0 liveness with the start gate's semantics — distinct from `proc_alive()` (which
    reads /proc and treats a zombie as dead): `signal0_state`, with someone else's process alive."""
    return signal0_state(pid) != "gone"


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


PID_MAX_LIMIT = 1 << 22         # the kernel's highest possible pid (PID_MAX_LIMIT on 64-bit Linux)


def marker_pid(rec: dict) -> int:
    """The pid a marker records, or ValueError: an int (never a bool, a float or a string, which
    int() once coerced) in 1..PID_MAX_LIMIT. `pid = inf` once raised OverflowError and `pid = [1]`
    TypeError out of the job listing (L24 F3)."""
    pid = rec.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= PID_MAX_LIMIT:
        raise ValueError(f"not a pid: {pid!r}")
    return pid


def stat_start_time(pid: int) -> int:
    """Field 22 of /proc/<pid>/stat (starttime, clock ticks since boot) — the stable half of a
    (pid, start_time) process identity. 0 if unavailable."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        # comm (field 2) is parenthesized and may contain spaces/parens — split after the LAST ')'.
        fields = data[data.rindex(b")") + 2:].split()
        return int(fields[19])                            # starttime = 22nd overall; index 19 here
    except (OSError, ValueError, IndexError):
        return 0


# The kernel's starttime (clock ticks since boot) is an unsigned 64-bit field; anything at or
# beyond it is no process's start time.
_START_TIME_LIMIT = 1 << 64


def recorded_ceased(pid, start_time) -> bool:
    """True when the recorded (pid, start_time) process no longer exists (dead, or the PID was
    reused by a different process). Conservative: unknown/malformed identity → NOT ceased.

    A pid with no /proc entry is ceased whatever the start time says. A pid that exists is
    compared by its start time, a positive int below 2**64 (never a bool, a float or a string;
    those once read as "a different process", so a live helper read as ceased, L24 F2). An
    unreadable stat of an existing process proves nothing."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if not os.path.exists(f"/proc/{pid}"):
        return True
    if (isinstance(start_time, bool) or not isinstance(start_time, int)
            or not 0 < start_time < _START_TIME_LIMIT):
        return False
    live = stat_start_time(pid)
    if live == 0:                                   # stat unreadable: ceased only if it vanished meanwhile
        return not os.path.exists(f"/proc/{pid}")
    return live != start_time

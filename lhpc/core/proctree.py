"""Shared process-tree termination — the ONE tested implementation used by both the
bounded command runner and the detached build/test launcher runtime, so timeout escalation
logic is never duplicated in a generated string.

A step is spawned with `start_new_session=True`, so its session id equals the spawned leader
pid and every descendant inherits it. Termination therefore checks the whole SESSION (a
TERM-ignoring child can outlive its parent), and never signals a recycled/unrelated group.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SessionToken:
    """Immutable session-ownership token captured immediately after spawn. All four fields
    are positive for a valid token; equality (frozen dataclass) is used to prove a live
    leader pid is still the SAME process, not a recycled one."""
    pid: int
    starttime: int
    sid: int
    pgid: int

    @property
    def complete(self) -> bool:
        return all(isinstance(v, int) and v > 0
                   for v in (self.pid, self.starttime, self.sid, self.pgid))


class Termination(Enum):
    """Typed outcome of `terminate_session`.

    SCOPE: termination is scoped to the ORIGINAL verified private session (the leader plus
    every process group that remains in it, including `setpgrp()` descendants). `TERMINATED`
    proves that verified original session is EMPTY — it does NOT prove that every descendant
    ever spawned has died: a descendant that calls `setsid()` leaves the session and becomes
    unobservable to this mechanism, so it is outside the proven ownership set (documented, not
    claimed killed)."""
    TERMINATED = "terminated"          # the ORIGINAL verified session is empty (not a universal
                                       # descendant-liveness guarantee — see SCOPE)
    ALREADY_CEASED = "already-ceased"  # nothing owned to signal — the session had ended
    UNVERIFIED = "unverified"          # no/incomplete token or ownership unprovable -> no signal
    INCOMPLETE = "incomplete"          # signalled, but members survived escalation

    @property
    def ok(self) -> bool:
        """True when the owned session is provably empty (terminated or already ceased).
        NOTE: this is about the original verified session, not escaped (`setsid`) descendants."""
        return self in (Termination.TERMINATED, Termination.ALREADY_CEASED)


def session_member_details(sid: int, exclude_pid: int) -> list[tuple[int, int]]:
    """(pid, pgid) for each LIVE member of session `sid` (excluding `exclude_pid`, skipping
    zombies). Used to signal EVERY process group in our private session — a descendant that
    calls `setpgrp()` stays in the session but moves to a new group, so signalling only the
    leader's group would miss it."""
    out: list[tuple[int, int]] = []
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return out
    for e in entries:
        pid = int(e)
        if pid == exclude_pid:
            continue
        try:
            with open(f"/proc/{pid}/stat") as fh:
                data = fh.read()
            rest = data[data.rindex(")") + 2:].split()   # state ppid pgrp session…
            if rest[0] in ("Z", "X", "x"):
                continue
            if int(rest[3]) == sid:
                out.append((pid, int(rest[2])))
        except (OSError, ValueError, IndexError):
            continue
    return out


def _signal_session_groups(token: "SessionToken", exclude_pid: int, sig: int) -> None:
    """Signal EVERY process group that belongs to our private session (`token.sid`), so a
    `setpgrp()` descendant is reached too — but NEVER the controller's own group."""
    try:
        controller_pgid = os.getpgid(exclude_pid) if exclude_pid > 0 else -1
    except OSError:
        controller_pgid = -1
    pgids = {token.pgid} | {pgid for _pid, pgid in session_member_details(token.sid, exclude_pid)}
    for pgid in pgids:
        if pgid <= 0 or pgid == controller_pgid:
            continue                                     # never signal the controller group
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass


def session_members(sid: int, exclude_pid: int) -> list[int]:
    """PIDs (excluding `exclude_pid`) still in session `sid` — i.e. the surviving members of
    the process tree we spawned. A recycled unrelated pid has a different session id and is
    never counted (hence never signalled)."""
    out: list[int] = []
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return out
    for e in entries:
        pid = int(e)
        if pid == exclude_pid:
            continue
        try:
            with open(f"/proc/{pid}/stat") as fh:
                data = fh.read()
            rest = data[data.rindex(")") + 2:].split()   # after comm: state ppid pgrp session…
            if rest[0] in ("Z", "X", "x"):
                continue                                  # zombie/dead -> already ceased
            if int(rest[3]) == sid:
                out.append(pid)
        except (OSError, ValueError, IndexError):
            continue
    return out



def capture_session_token(pid: int) -> SessionToken | None:
    """Capture the FULL session-ownership token immediately after spawn: leader pid, start
    time, sid, pgid (all positive). Returns None if ANY field can't be read — an incomplete
    token means we can never prove ownership, so termination must fail closed and NOT signal."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
        rest = data[data.rindex(")") + 2:].split()   # after comm: state ppid pgrp session…
        pgid, sid, start = int(rest[2]), int(rest[3]), int(rest[19])
    except (OSError, ValueError, IndexError):
        return None
    if min(pid, pgid, sid, start) <= 0:
        return None
    # AUDIT S3: every process we capture was spawned with start_new_session=True, so it
    # MUST be its own session AND group leader (sid == pgid == pid). If the pid was
    # recycled — in the microseconds before this read — by a mere MEMBER of a foreign
    # session, sid/pgid would point elsewhere and a later terminate_session could signal
    # that foreign session. Refuse a token that isn't a self-led session (fail closed).
    if sid != pid or pgid != pid:
        return None
    return SessionToken(pid=pid, starttime=start, sid=sid, pgid=pgid)


def _ownership_state(token: SessionToken, exclude_pid: int) -> str:
    """Classify whether the ORIGINAL owned session is signal-eligible:
      * 'owned'      — the leader pid is alive and EQUALS the token (same process), or the
                       leader is gone but a live session member remains;
      * 'unverified' — the leader pid is alive but does NOT match the token (recycled pid):
                       its session id is not ours, so we refuse to trust session members;
      * 'ceased'     — the leader is gone and no session member remains (session ended)."""
    leader = capture_session_token(token.pid)
    if leader is not None:                # leader pid alive -> must BE our leader, exactly
        return "owned" if leader == token else "unverified"
    # `capture_session_token` returns None BOTH when the pid is gone AND when a live pid
    # is not a self-led session leader (the S3 invariant). Those are OPPOSITE cases for
    # signalling: a LIVE non-leader pid means our leader's pid was recycled by a foreign
    # process — refuse to trust session members ('unverified', NEVER signal). Only a truly
    # ABSENT leader falls through to the session-member liveness check. (Without this, the
    # strict-capture change would misread a recycled pid as 'owned' and signal a foreign
    # — or our own — session.)
    try:
        os.kill(token.pid, 0)
        return "unverified"               # live pid, not our self-led leader -> recycled
    except ProcessLookupError:
        pass                              # leader genuinely gone
    except PermissionError:
        return "unverified"               # exists but not ours -> recycled, don't signal
    return "owned" if session_members(token.sid, exclude_pid) else "ceased"


def session_ceased(token: SessionToken | None, exclude_pid: int) -> bool:
    """Non-signalling proof that the ORIGINAL owned session is EMPTY (leader gone AND no member remains).
    Used by HMAC recovery to auto-clear a `session-unverified` block only when the tracked session is
    provably gone. A recycled/unprovable leader pid returns False (fail-closed — never claim ceased)."""
    if not isinstance(token, SessionToken) or not token.complete:
        return False
    return _ownership_state(token, exclude_pid) == "ceased"


def terminate_session(token: SessionToken | None, exclude_pid: int, *,
                      term_grace: float = 2.0, kill_grace: float = 1.0) -> Termination:
    """TERM then bounded-KILL the owned process group described by `token`, checking the whole
    SESSION so a TERM-ignoring child that outlives its parent is still killed.

    FAIL CLOSED: with NO or an INCOMPLETE token, or when ownership cannot be proven (a
    recycled leader pid), we signal NOTHING and return `UNVERIFIED`. A genuinely-ended session
    returns `ALREADY_CEASED` (also no signal). Only a provably-owned session is signalled —
    `TERMINATED` when it empties, `INCOMPLETE` when members survive escalation. A bare pid /
    sid / pgid is NEVER accepted as authority; only a complete `SessionToken` is."""
    if not isinstance(token, SessionToken) or not token.complete:
        return Termination.UNVERIFIED     # no valid token -> never signal (fail closed)
    state = _ownership_state(token, exclude_pid)
    if state == "unverified":
        return Termination.UNVERIFIED     # recycled/unprovable -> never signal
    if state == "ceased":
        return Termination.ALREADY_CEASED  # session already gone -> nothing to signal
    sid = token.sid
    _signal_session_groups(token, exclude_pid, signal.SIGTERM)   # all groups in our session
    deadline = time.monotonic() + term_grace
    while time.monotonic() < deadline and session_members(sid, exclude_pid):
        time.sleep(0.05)
    if session_members(sid, exclude_pid):
        _signal_session_groups(token, exclude_pid, signal.SIGKILL)
        k_deadline = time.monotonic() + kill_grace
        while time.monotonic() < k_deadline and session_members(sid, exclude_pid):
            time.sleep(0.05)
    return Termination.TERMINATED if not session_members(sid, exclude_pid) else Termination.INCOMPLETE

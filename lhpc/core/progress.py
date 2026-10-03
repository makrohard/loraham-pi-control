"""Build-step liveness: a build step is ended when it shows no activity, not for being slow.

One rule for every build path (CLI `lhpc build`, auto-install, web Build): a step is STALLED after
`stall_s` with no progress sample, and OVER BUDGET when its runtime exceeds `ceiling_s`; whichever
fires first ends it. Without an explicit or env ceiling the budget is a 24 h runaway guard, not a
performance limit. Host tests keep their plain wall-clock timeout (no stall rule).

The unit of measurement is the step's SESSION (every runner spawns with `start_new_session=True`),
read from `/proc` the same way `proctree` does. A sample is PROGRESS when, against the previous
sample, any member counter grew (CPU ticks incl. reaped children, rchar+wchar), a new
`(pid, starttime)` appeared, the step's log grew, or the sample could not be taken (unknown). The
counters are cumulative and the baseline is simply the previous sample, so no gain is ever
discarded; unknown biases toward "alive", never toward a false kill.
"""

from __future__ import annotations

import math
import os
import time

STALL_S = 600.0             # policy: 10 min with no observable activity counts as stalled
BUILD_CEILING_S = 86400.0   # 24 h runaway guard for a busy-looping step; no supported build nears it
SAMPLE_S = 15.0             # seconds between samples


def parse(value) -> float:
    """A strict positive, finite number of seconds; anything else ("0", "-1", "nan", "inf", "x",
    None) raises ValueError — a malformed limit never means "no limit"."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"not a number of seconds: {value!r}") from None
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f"not a positive, finite number of seconds: {value!r}")
    return v


def build_limits(manifest_s, environ, explicit_s=None) -> tuple[float, float]:
    """(stall_s, ceiling_s) for one build — the ONE precedence rule every build path calls.

    stall_s:   LHPC_BUILD_STALL_S if set, else STALL_S.
    ceiling_s: an explicit caller value if given; else LHPC_BUILD_STEP_TIMEOUT_S if set (used AS IS,
               the operator/test override); else max(manifest value, BUILD_CEILING_S). A manifest
               value of 0/None means "absent" (the manifest's own default). Every value goes through
               `parse`, so a malformed one raises ValueError instead of disabling a limit."""
    raw = environ.get("LHPC_BUILD_STALL_S")
    stall_s = STALL_S if raw is None else parse(raw)
    # Every supplied value is validated BEFORE the precedence is applied, so a malformed value is
    # refused even when a higher-precedence one would mask it.
    explicit = None if explicit_s is None else parse(explicit_s)
    raw = environ.get("LHPC_BUILD_STEP_TIMEOUT_S")
    env_s = None if raw is None else parse(raw)
    try:
        absent = manifest_s in (None, "") or float(manifest_s) == 0
    except (TypeError, ValueError):
        absent = False                              # not a number: parse() below refuses it
    manifest = 0.0 if absent else parse(manifest_s)
    if explicit is not None:
        return stall_s, explicit
    if env_s is not None:
        return stall_s, env_s
    return stall_s, max(manifest, BUILD_CEILING_S)


def span(seconds: float) -> str:
    """Operator wording for a limit: '24 h', '10 min', '2 s'."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds / 3600:g} h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g} min"
    return f"{seconds:g} s"


class _Unknown(Exception):
    pass


def _read_text(path: str) -> str:
    with open(path) as fh:
        return fh.read()


class SessionSampler:
    """Samples session `sid` (minus `exclude_pid`) plus the step's output size (`out_size()`, bytes).
    `sample()` returns True when the sample is progress or unknown."""

    def __init__(self, sid: int, exclude_pid: int, out_size, proc_root: str = "/proc"):
        self._sid, self._exclude, self._out_size, self._proc = sid, exclude_pid, out_size, proc_root
        self._prev: tuple[dict, int] | None = None

    def _members(self) -> dict:
        """{(pid, starttime): (cpu ticks, io bytes)} for each live member. A member that vanishes
        mid-sample (it was a member last time), an unreadable stat/io or an unparsable stat is
        _Unknown; a pid that was not a member and vanished before its stat was read is skipped."""
        try:
            entries = [e for e in os.listdir(self._proc) if e.isdigit()]
        except OSError:
            raise _Unknown from None
        known = {pid for pid, _start in (self._prev[0] if self._prev else {})}
        out: dict = {}
        for e in entries:
            pid = int(e)
            if pid == self._exclude:
                continue
            try:
                data = _read_text(f"{self._proc}/{pid}/stat")
            except FileNotFoundError:
                if pid in known:
                    raise _Unknown from None        # a member exited mid-sample
                continue                            # some other process exited: not ours
            except OSError:
                raise _Unknown from None
            try:
                rest = data[data.rindex(")") + 2:].split()   # state ppid pgrp session…
                if rest[0] in ("Z", "X", "x") or int(rest[3]) != self._sid:
                    continue
                cpu = sum(int(rest[i]) for i in (11, 12, 13, 14))   # utime stime cutime cstime
                start = int(rest[19])
            except (ValueError, IndexError):
                raise _Unknown from None
            try:
                fields = dict(line.split(":", 1) for line in
                              _read_text(f"{self._proc}/{pid}/io").splitlines() if ":" in line)
                io = int(fields["rchar"]) + int(fields["wchar"])
            except (OSError, KeyError, ValueError):
                raise _Unknown from None
            out[(pid, start)] = (cpu, io)
        return out

    def sample(self) -> bool:
        try:
            members, size = self._members(), int(self._out_size())
        except (_Unknown, OSError, TypeError, ValueError):
            self._prev = None                       # no trustworthy baseline: the next one is fresh
            return True
        prev, self._prev = self._prev, (members, size)
        if prev is None:
            return True                             # every member is new against an empty baseline
        old, old_size = prev
        if size > old_size or any(key not in old for key in members):
            return True
        return any(cpu > old[key][0] or io > old[key][1] for key, (cpu, io) in members.items())


class Watch:
    """`check()` -> None, "stalled" or "budget". Samples only when `sample_s` has elapsed; the stall
    decision is taken right after a sample. With `sampler=None` (or `stall_s=None`) only the ceiling
    applies."""

    def __init__(self, stall_s, ceiling_s: float, sample_s: float = SAMPLE_S,
                 clock=time.monotonic, sampler=None):
        self.stall_s, self.ceiling_s, self._sample_s = stall_s, ceiling_s, sample_s
        self._clock, self._sampler = clock, sampler
        self._start = self._last_sample = self._last_progress = clock()
        self._quiet = 0.0

    def check(self) -> str | None:
        now = self._clock()
        if now - self._start >= self.ceiling_s:
            return "budget"
        if self.stall_s is None or self._sampler is None or now - self._last_sample < self._sample_s:
            return None
        self._last_sample = now
        if self._sampler.sample():
            self._last_progress = now
            return None
        self._quiet = max(self._quiet, now - self._last_progress)
        return "stalled" if now - self._last_progress >= self.stall_s else None

    def longest_quiet(self) -> float:
        """The longest quiet period so far: the largest `now - last progress` the stall rule compared
        at a sample without progress, or the quiet tail up to now. Its margin to `stall_s` is the
        slow-target budget's L1 quantity, at the sampling resolution; without a sampler nothing is
        observed after the start, so it is the whole runtime."""
        return max(self._quiet, self._clock() - self._last_progress)

    def remaining(self) -> float:
        """Seconds left to the ceiling (never negative): a runner caps each wait slice with it, so a
        step can never outlive its ceiling by a wait slice."""
        return max(0.0, self._start + self.ceiling_s - self._clock())


def quiet_line(seconds: float) -> str:
    """The last line of a build step's log (`Watch.longest_quiet`); the slow-build lane and row A
    read it."""
    return f"[progress] longest quiet {seconds:.1f} s"

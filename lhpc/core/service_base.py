"""Shared service-layer types and helpers used across the ControllerService facade and its
service_* mixins: the uniform ActionResult/ConfigWrite result objects, typed control-flow
exceptions, git-URL canonicalization, and /proc process-identity helpers. Kept dependency-light
(stdlib only) so every service_* module imports from here without an import cycle."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _canon_git_url(url: str) -> str:
    """Normalize a git remote URL for identity comparison: the `git@host:path` / `ssh://` /
    `https://` forms are reduced to a common `host/path` (so an approved canonical origin
    matches regardless of transport), trailing `.git`/`/` stripped. Only the HOST is
    lowercased — the path is left case-exact, since on case-sensitive hosts `host/Foo` and
    `host/foo` are DIFFERENT repos (avoids a cross-repo false-accept). Returns `""` for a
    degenerate/empty input (the caller must reject an empty canonical, never treat two
    empties as a match)."""
    u = (url or "").strip()
    low = u.lower()
    for pre in ("https://", "http://", "ssh://", "git://"):
        if low.startswith(pre):
            u = u[len(pre):]
            break
    else:
        if low.startswith("git@"):
            u = u[len("git@"):].replace(":", "/", 1)
    if "@" in u.split("/", 1)[0]:                # strip user@ credentials in host part
        u = u.split("@", 1)[1]
    u = u.rstrip("/")
    if u.lower().endswith(".git"):
        u = u[:-4]
    u = u.rstrip("/")
    host, sep, rest = u.partition("/")
    return host.lower() + sep + rest             # host case-folded; path preserved


class _StopRun(Exception):
    """Internal control-flow signal to break out of the run-service body to the record/release
    stage (keeps the finalization in one place)."""


def _proc_start_time(pid: int) -> int:
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


def _proc_ceased(pid, start_time) -> bool:
    """True when the recorded (pid, start_time) process no longer exists (dead, or the PID was
    reused by a different process). Conservative: unknown/malformed identity → NOT ceased."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if not os.path.exists(f"/proc/{pid}"):
        return True
    return _proc_start_time(pid) != (start_time if isinstance(start_time, int) else -1)


class SourceTxnBlocked(Exception):
    """Raised by the source-operation guard when an unresolved source-transaction journal
    is present — every source-mutating op fails closed until an operator resolves it."""


@dataclass(frozen=True)
class ConfigWrite:
    """Structured result of generating one component's config file."""

    component: str
    path: str
    status: str            # "written" | "linked-readonly" | "no-base" | "failed"
    detail: str = ""


@dataclass
class ActionResult:
    """Uniform result object rendered identically by every adapter."""

    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    # Typed per-component lifecycle results (start/stop/restart). Adapters may render
    # from these; `ok` for an applied lifecycle action is derived from them.
    results: tuple = field(default_factory=tuple)

"""Typed lifecycle outcomes (§3).

A component lifecycle action resolves to exactly one `Outcome`. Success and
"verified" are derived from the typed outcome — never from matching free-form text.
A top-level applied action is successful only when every required component result is
both successful AND verified; `MANUAL_REQUIRED`, `UNVERIFIED`, `STILL_RUNNING`, and
`ENDPOINT_STILL_PRESENT` are never successful applied outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    PLANNED = "planned"                       # dry-run only
    SKIPPED = "skipped"                       # not applicable, intentionally not acted on
    BLOCKED = "blocked"                       # a precondition prevented action
    MANUAL_REQUIRED = "manual_required"       # operator must act (interactive/systemd)
    ALREADY_HEALTHY = "already_healthy"       # already running and verified
    STARTED = "started"                       # launched, not yet verified
    VERIFIED = "verified"                     # launched and readiness verified
    STOPPED = "stopped"                       # ceased and (where applicable) endpoints gone
    ALREADY_STOPPED = "already_stopped"
    FAILED = "failed"
    UNVERIFIED = "unverified"                 # acted but could not verify the result
    STILL_RUNNING = "still_running"           # stop signalled but process persists
    ENDPOINT_STILL_PRESENT = "endpoint_still_present"   # process gone but endpoint lingers


# Outcomes that are NOT a successful applied result.
_NON_SUCCESS = frozenset({
    Outcome.BLOCKED, Outcome.MANUAL_REQUIRED, Outcome.FAILED, Outcome.UNVERIFIED,
    Outcome.STILL_RUNNING, Outcome.ENDPOINT_STILL_PRESENT,
})
# Outcomes that represent a verified end-state.
_VERIFIED = frozenset({
    Outcome.VERIFIED, Outcome.ALREADY_HEALTHY, Outcome.STOPPED, Outcome.ALREADY_STOPPED,
    Outcome.SKIPPED, Outcome.PLANNED,
})


@dataclass(frozen=True)
class CompResult:
    """A typed, immutable per-component lifecycle result."""

    component: str
    action: str                               # "start" | "stop" | "restart" | …
    outcome: Outcome
    stack: str = ""
    summary: str = ""
    details: tuple = ()
    pid: int | None = None
    endpoints: tuple = ()                     # endpoint evidence (addresses / states)

    @property
    def ok(self) -> bool:
        return self.outcome not in _NON_SUCCESS

    @property
    def verified(self) -> bool:
        return self.outcome in _VERIFIED

    def line(self) -> str:
        """Compact human line derived from the typed outcome (for CLI/web/logs)."""
        head = f"  [{self.outcome.value}] {self.component}"
        return f"{head}: {self.summary}" if self.summary else head


def applied_ok(results) -> bool:
    """A top-level APPLIED action is successful only when every result is successful
    and verified (a non-success or merely-unverified result fails the whole action)."""
    results = list(results)
    return bool(results) and all(r.ok and r.verified for r in results)


def any_blocking(results) -> bool:
    """True if any result is a hard non-success (blocks dependent/parent actions)."""
    return any(r.outcome in _NON_SUCCESS for r in results)

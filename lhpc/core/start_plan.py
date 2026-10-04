"""The pure decisions of a start, each from explicit inputs (no service state, no I/O): the
prepared-start value, the verdict over the typed outcomes, and the roll-back's selection and
the reason it gives for a daemon band it keeps.

`service_lifecycle_ops` runs the phases (admit and acquire guards → prepare from fresh evidence →
execute → verify → finalize); this module holds what those phases decide without touching the
box, so each decision is testable on its own."""
from __future__ import annotations

import dataclasses

from .outcomes import Outcome


@dataclasses.dataclass(frozen=True)
class PreparedStart:
    """What one start will do, resolved once from the SAVED configuration (on the apply, under
    the start's guards). It carries no process snapshot: `execute` takes its own after the
    conflicting owners are stopped.

    The required actions it names: stop the conflicting owners (`stop_owners`), ensure the
    daemon on `radio` with TX mode `tx` for `start_sid`, then start `order` in run order on
    `cfg_band` with the launch-only inherited identity (`params`, `file_over`)."""
    target: str
    band: str             # the band the caller asked for ("" = none); the public start passes its op band
    cfg_band: str         # the config-store band every component renders and launches on
    op_band: str          # the operating band (differs from cfg_band only for the daemon)
    order: list           # [(stack, component), ...] in run order
    radio: str            # the daemon's requested radio ("" = every active band)
    tx: str | None        # the daemon TX mode this start sets, or None/""
    start_sid: str        # the stack whose daemon parameters apply once the daemon is up
    scope: str            # "stack" | "component": the ownership scope recorded on each launch
    params: dict          # inherited-identity run values (launch-only, never persisted)
    file_over: dict       # inherited-identity config-file values
    position: dict | None  # the MeshCore position decided before the start, or None
    position_note: str
    stop_owners: bool
    optional_ids: frozenset
    gui_optional_ids: frozenset
    nonmain_interactive_ids: frozenset

    @property
    def target_is_stack(self) -> bool:
        return self.scope == "stack"


def prepare_start(*, target: str, band: str, cfg_band: str, op_band: str, order, radio: str,
                  tx: str | None, start_sid: str, target_is_stack: bool, params: dict, file_over: dict,
                  position: dict | None, position_note: str,
                  stop_owners: bool) -> PreparedStart:
    """The prepared-start value from its resolved inputs; derives the ownership scope and the
    component classes the verdict reads from the run order."""
    return PreparedStart(
        target=target, band=band, cfg_band=cfg_band, op_band=op_band, order=order, radio=radio,
        tx=tx, start_sid=start_sid, scope="stack" if target_is_stack else "component",
        params=params, file_over=file_over, position=position, position_note=position_note,
        stop_owners=stop_owners,
        optional_ids=frozenset(c.id for _, c in order if c.optional),
        gui_optional_ids=frozenset(c.id for _, c in order if c.gui_optional),
        nonmain_interactive_ids=frozenset(c.id for s, c in order
                                          if c.interactive and c.id != s.main))


@dataclasses.dataclass
class StartRun:
    """What `execute` did: the detail lines, the typed per-component rows (the source of truth),
    the components whose copy-paste command was shown, the daemon bands it launched, the
    `Lifecycle` it launched with and the restart-required marker it read before launching."""
    out: list
    results: list
    presented: set
    daemon_launched: set
    life: object
    pre_marker: object


def verify_start(prep: PreparedStart, results, presented) -> tuple[list[str], list[str]]:
    """`(failed, required_manual)`: the blocking components of a start, from its typed outcomes
    alone; the start is ok exactly when both are empty.

    A MANUAL_REQUIRED or SKIPPED optional component does not block; nor does a non-main
    interactive component whose command WAS presented (voice's terminal variant: its card
    command is the expected outcome — the MAIN stays blocking, so chat keeps its manual-start
    presentation), nor a gui_optional component's headless SKIPPED. Every other row that is not
    ok blocks."""
    def blocks(r):
        if r.outcome in (Outcome.MANUAL_REQUIRED, Outcome.SKIPPED) \
                and r.component in prep.optional_ids:
            return False
        if r.outcome == Outcome.MANUAL_REQUIRED \
                and r.component in prep.nonmain_interactive_ids and r.component in presented:
            return False
        if r.outcome == Outcome.SKIPPED and r.component in prep.gui_optional_ids:
            return False
        return not r.ok
    blocking = [r for r in results if blocks(r)]
    return ([r.component for r in blocking if r.outcome != Outcome.MANUAL_REQUIRED],
            [r.component for r in blocking if r.outcome == Outcome.MANUAL_REQUIRED])


def rollback_ids(results, keep=()) -> list[str]:
    """Ids of the components a start launched and verified (its `start` rows reading VERIFIED),
    last started first: what a FAILED start stops again. An ALREADY_HEALTHY row ran before that
    start and is never in it; `keep` names rows whose VERIFIED does not mean the start launched
    the process (the daemon's ensure row reads VERIFIED for a band that was already served)."""
    return [r.component for r in reversed(list(results))
            if r.action == "start" and r.outcome is Outcome.VERIFIED and r.component not in keep]


def residual_reason(residual, rolled, results) -> str:
    """Why a daemon band a failed start launched stays up: each run-order component that still
    holds an ownership record (`residual`), named by ORIGIN from this start's own rows. One this
    start launched — rolled back (`rolled`), or left UNVERIFIED / FAILED by its own cleanup — "is
    not verified stopped"; any other ran before this start and is "still running (not started by
    this call)"."""
    launched = set(rolled) | {r.component for r in results if r.action == "start"
                              and r.outcome in (Outcome.UNVERIFIED, Outcome.FAILED)}
    left = [c for c in residual if c in launched]
    kept = [c for c in residual if c not in launched]
    return "; ".join(w for w in (
        f"{', '.join(left)} {'is' if len(left) == 1 else 'are'} not verified stopped"
        if left else "",
        f"{', '.join(kept)} still running (not started by this call)" if kept else "") if w)

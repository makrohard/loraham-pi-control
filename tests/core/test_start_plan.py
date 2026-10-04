"""The pure decisions of a start (`lhpc.core.start_plan`), driven from plain values: the
prepared-start value, the verdict over the typed per-component outcomes, and the reason a failed
start gives for a daemon band it keeps. The start and restart coordinators that use them are
driven through the service elsewhere (the golden set, `test_run_order.py`, `test_stop_propagation.py`,
`test_restart_required.py`, `test_restart_optional.py`, `test_process_ownership.py`)."""

import types

import pytest

from lhpc.core.model import ComponentKind
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.start_plan import (
    PreparedStart,
    prepare_start,
    residual_reason,
    verify_start,
)


def _comp(cid, **kw):
    base = dict(id=cid, optional=False, gui_optional=False, interactive=False,
                kind=ComponentKind.SERVICE)
    return types.SimpleNamespace(**{**base, **kw})


STACK = types.SimpleNamespace(id="s", main="main")
ORDER = [(STACK, _comp("loraham-daemon")), (STACK, _comp("main")),
         (STACK, _comp("opt", optional=True)), (STACK, _comp("gui", gui_optional=True)),
         (STACK, _comp("cli", interactive=True))]


def _prep(**kw):
    args = dict(target="s", band="433", cfg_band="433", op_band="433", order=ORDER, radio="433",
                tx="", start_sid="s", target_is_stack=True, params={"call": "X"},
                file_over={}, position=None, position_note="", stop_owners=False)
    return prepare_start(**{**args, **kw})


def _row(cid, outcome, action="start"):
    return CompResult(component=cid, action=action, outcome=outcome)


# ── the prepared-start value ──

def test_the_prepared_start_is_built_from_its_inputs():
    prep = _prep()
    assert isinstance(prep, PreparedStart)
    assert (prep.target, prep.band, prep.cfg_band, prep.op_band, prep.radio, prep.start_sid) == \
        ("s", "433", "433", "433", "433", "s")
    assert prep.scope == "stack" and prep.target_is_stack
    assert prep.optional_ids == {"opt"} and prep.gui_optional_ids == {"gui"}
    assert prep.nonmain_interactive_ids == {"cli"}       # the interactive MAIN is not in it
    assert _prep(target_is_stack=False).scope == "component"
    # Frozen: the inputs are resolved once, under the start's locks, and every later phase reads
    # the same value — a phase that could rebind one would act on a band nobody checked.
    with pytest.raises(AttributeError):
        prep.cfg_band = "868"


# ── the verdict ──

@pytest.mark.parametrize("rows, presented, verdict", [
    ([("main", Outcome.VERIFIED)], set(), ([], [])),
    ([("main", Outcome.ALREADY_HEALTHY), ("opt", Outcome.SKIPPED)], set(), ([], [])),
    ([("opt", Outcome.MANUAL_REQUIRED), ("gui", Outcome.SKIPPED)], set(), ([], [])),
    ([("cli", Outcome.MANUAL_REQUIRED)], {"cli"}, ([], [])),
    ([("cli", Outcome.MANUAL_REQUIRED)], set(), ([], ["cli"])),
    ([("main", Outcome.MANUAL_REQUIRED)], {"main"}, ([], ["main"])),
    ([("main", Outcome.UNVERIFIED), ("gui", Outcome.FAILED)], set(), (["main", "gui"], [])),
    ([("main", Outcome.BLOCKED), ("cli", Outcome.MANUAL_REQUIRED)], set(), (["main"], ["cli"])),
])
def test_verify_decides_from_the_typed_outcomes_alone(rows, presented, verdict):
    # The verdict reads only outcomes and which commands were presented — never a summary — so a
    # reworded message cannot change whether a start failed or waits on a manual step.
    results = [_row(c, o) for c, o in rows]
    assert verify_start(_prep(), results, presented) == verdict


def test_an_interactive_main_stays_blocking_when_its_command_was_presented():
    # ORDER's main is a service; chat's is interactive. A presented command clears an interactive
    # sidecar (voice's terminal variant), never an interactive MAIN: it keeps its manual-start
    # verdict, which the entry paths then show as success (finding 122).
    order = [(STACK, _comp("loraham-daemon")), (STACK, _comp("main", interactive=True))]
    prep = _prep(order=order)
    assert prep.nonmain_interactive_ids == set()
    assert verify_start(prep, [_row("main", Outcome.MANUAL_REQUIRED)], {"main"}) == ([], ["main"])


# ── the reason a failed start gives for a daemon band it keeps ──

def _named(reason: str) -> dict:
    """{"stopped?": [names], "before": [names]}: which components the reason puts in each class."""
    out = {"stopped?": [], "before": []}
    for part in reason.split("; "):
        names = part.split(" are ")[0].split(" is ")[0].split(" still ")[0].split(", ")
        out["before" if "not started by this call" in part else "stopped?"] += names
    return out


def test_the_kept_daemon_band_names_each_residual_component_by_origin():
    # The origin decides the class: one this start launched (rolled back, or its own cleanup did
    # not cease, or it failed) is "not verified stopped"; one that ran before the start
    # (ALREADY_HEALTHY, never asked to stop) is "not started by this call" — the operator must not
    # read a component the start never touched as one it failed to stop.
    results = [_row("web", Outcome.ALREADY_HEALTHY), _row("ui", Outcome.STILL_RUNNING),
               _row("node", Outcome.UNVERIFIED), _row("feed", Outcome.FAILED)]
    assert _named(residual_reason(["feed", "node", "ui", "web"], ["ui"], results)) == \
        {"stopped?": ["feed", "node", "ui"], "before": ["web"]}
    assert _named(residual_reason(["node"], [], results)) == {"stopped?": ["node"], "before": []}
    assert _named(residual_reason(["web"], [], results)) == {"stopped?": [], "before": ["web"]}

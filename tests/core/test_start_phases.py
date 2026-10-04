"""The phases of a start and the legs of a restart, each driven directly from explicit inputs.

The pure decisions (`lhpc.core.start_plan`) take plain values. The phase methods are called on a
stand-in `self` that provides only the collaborators the phase may use: a phase reaching for any
other one fails with AttributeError, so each test also proves what that phase does NOT reach
(rule 9: the stand-in stubs collaborators only; the one order asserted is the documented lock
order)."""

import contextlib
import types

import pytest

from lhpc.core import service_lifecycle_ops as ops
from lhpc.core.model import ComponentKind
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.service_base import ActionResult
from lhpc.core.service_lifecycle_ops import LifecycleOpsMixin as M
from lhpc.core.start_plan import (
    PreparedStart,
    StartRun,
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


class _Self:
    """A stand-in `self`: the keyword arguments become its attributes; `calls` records the
    collaborators a phase called, in order."""

    def __init__(self, **attrs):
        self.calls = []
        for name, value in attrs.items():
            setattr(self, name, self._recorded(name, value) if callable(value) else value)

    def _recorded(self, name, fn):
        def call(*a, **kw):
            self.calls.append(name)
            return fn(*a, **kw)
        return call


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
    with pytest.raises(AttributeError):                   # frozen: decided once
        prep.cfg_band = "868"


# ── PHASE 4 VERIFY ──

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
    results = [_row(c, o) for c, o in rows]
    assert verify_start(_prep(), results, presented) == verdict


# ── the roll-back's reason for a daemon band it keeps (finding A) ──

def test_the_kept_daemon_band_names_each_residual_component_by_origin():
    results = [_row("web", Outcome.ALREADY_HEALTHY), _row("ui", Outcome.STILL_RUNNING),
               _row("node", Outcome.UNVERIFIED), _row("feed", Outcome.FAILED)]
    assert residual_reason(["feed", "node", "ui", "web"], ["ui"], results) == (
        "feed, node, ui are not verified stopped; web still running (not started by this call)")
    assert residual_reason(["node"], [], results) == "node is not verified stopped"
    assert residual_reason(["web"], [], results) == "web still running (not started by this call)"


# ── PHASE 1 ADMIT AND ACQUIRE GUARDS ──

def test_the_guards_are_taken_admission_config_bundle_and_released_in_reverse():
    log = []

    def guard(name):
        @contextlib.contextmanager
        def cm(*a, **kw):
            log.append(f"enter {name} {a} {kw}")
            yield
            log.append(f"exit {name}")
        return cm
    me = _Self(_admission_guard=guard("admission"), _config_stable=guard("config"),
               _lifecycle_guard=guard("bundle"),
               operation_band=lambda t, b: log.append("op-band") or "868",
               _order_radio=lambda t, ob: log.append(f"order {ob}") or (ORDER, "868"))
    with M._admit_and_acquire_guards(me, "start", "s", "", True) as got:
        log.append("body")
    assert got == ("868", ORDER, "868")
    assert log == ["enter admission ('start', 's') {}", "op-band", "enter config () {}",
                   "order 868",
                   "enter bundle ('start', 's', '868') {'stop_owners': True, 'radio': '868'}",
                   "body", "exit bundle", "exit config", "exit admission"]


# ── PHASE 2 PREPARE FROM FRESH EVIDENCE ──

def _prepare_self(**over):
    attrs = dict(
        _run_order=lambda t: ORDER, _gui_fallback_refusal=lambda t: None,
        _meshcore_mode_refusal=lambda t: None, _start_static_refusal=lambda t, op: None,
        _launch_band_hint=lambda t, b: b, _config_band=lambda t, b: b,
        operation_band=lambda t, b: b, stack_of=lambda t: t, running_band=lambda s, d: "",
        _dep_band_block=lambda t, o, b: None, _daemon_needs=lambda o, b: (b, "MANAGED"),
        _owner_stack_id=lambda t: t, enforce_identity=lambda t, b: (True, [], ""),
        _materialize_inherited_identity=lambda t, b: ({"call": "X"}, {}),
        _saved_launch_refusal=lambda t, b, op: None, stack=lambda t: STACK,
        _order_already_healthy=lambda o, r: False,
        _start_preflight_refusal=lambda *a, **kw: None)
    return _Self(**{**attrs, **over})


def test_prepare_resolves_the_plan_inputs_and_stops_before_the_apply_only_checks():
    me = _prepare_self()
    prep = M._prepare_from_fresh_evidence(me, "s", "433", False, None, "", apply=False)
    assert prep == _prep(tx="MANAGED")
    assert "_order_already_healthy" not in me.calls and "_start_preflight_refusal" not in me.calls


def test_prepare_refuses_on_a_saved_value_before_the_apply_checks():
    refusal = ActionResult(False, "Cannot start 's': bad saved value")
    me = _prepare_self(_saved_launch_refusal=lambda t, b, op: refusal)
    assert M._prepare_from_fresh_evidence(me, "s", "433", False, None, "",
                                          apply=True) is refusal
    assert "_order_already_healthy" not in me.calls and "_start_preflight_refusal" not in me.calls


def test_prepare_on_the_apply_ends_with_the_preflight_and_hands_it_the_owner_choice():
    seen = {}
    refusal = ActionResult(False, "Cannot run 's': x must be stopped first.")

    def preflight(*a, **kw):
        seen.update(kw)
        return refusal
    me = _prepare_self(_start_preflight_refusal=preflight)
    assert M._prepare_from_fresh_evidence(me, "s", "433", True, None, "", apply=True) is refusal
    assert seen == {"check_blockers": False, "render": True}


# ── PHASE 3 EXECUTE ──

def test_execute_refuses_when_a_conflicting_owner_does_not_verify_stopped():
    """The owner stop is the first destructive step; one that is not verified ends the start
    there: no snapshot is taken and nothing of the target is launched (the stand-in has neither
    `build_snapshot` nor anything that launches)."""
    me = _Self(_lifecycle=lambda: object(),
               run_blockers=lambda t, b, r: [{"holder_stack": "x"}],
               stop=lambda o, apply, _operator: ActionResult(False, "x still running"))
    res = M._execute_start(me, _prep(stop_owners=True))
    assert isinstance(res, ActionResult) and not res.ok
    assert res.summary == "Cannot run 's': conflicting stack(s) x could not be verified stopped."
    assert res.details == ["  [blocked] conflicting stack 'x' did not stop (verified): "
                           "x still running"]


# ── PHASE 5 FINALIZE ──

@pytest.mark.parametrize("marker_now, cleared", [("saved-at-1", True), ("saved-at-2", False)])
def test_finalize_keeps_a_restart_marker_saved_during_the_launch(monkeypatch, marker_now,
                                                                  cleared):
    """A configuration save that landed while the launch ran (the marker changed since execute
    read it) keeps its newer restart marker; an unchanged one is cleared by the start."""
    cleared_for = []
    monkeypatch.setattr(ops._rr, "clear_marker", lambda paths, t: cleared_for.append(t))
    me = _Self(_capture_start_composition=lambda t, b: None, stack_of=lambda c: "s",
               restart_required=lambda t: marker_now, _paths=None,
               _spi_shared_warning=lambda t, b, r: None)
    run = StartRun(out=["  [verified] main: started"], results=[_row("main", Outcome.VERIFIED)],
                   presented=set(), daemon_launched=set(), life=None, pre_marker="saved-at-1")
    res = M._finalize_start(me, _prep(), run, [], [])
    assert res.ok and res.summary == "Run applied for 's'."
    assert cleared_for == (["s"] if cleared else [])


@pytest.mark.parametrize("target, band", [("meshtastic", "868"), ("daemon", "433")])
def test_a_successful_applied_start_beside_the_other_side_is_warned(monkeypatch, target, band):
    """The plan's shared-SPI warning has its pair in the result: an applied start that came up
    beside the other side of the bus carries the one `[warning]` line. The finalize step passes the
    pair to the warning in both orders (asked with this start's own values); the direction decision
    itself is `_spi_shared_warning`'s, covered by S5's tests in tests/core/test_run_order.py."""
    monkeypatch.setattr(ops._rr, "clear_marker", lambda paths, t: None)
    asked = []
    me = _Self(_capture_start_composition=lambda t, b: None, stack_of=lambda c: target,
               restart_required=lambda t: None, _paths=None,
               _spi_shared_warning=lambda t, b, r: asked.append((t, b, r)) or ops.SPI_SHARED_WARNING)
    run = StartRun(out=["  [verified] main: started"], results=[_row("main", Outcome.VERIFIED)],
                   presented=set(), daemon_launched=set(), life=None, pre_marker=None)
    res = M._finalize_start(me, _prep(target=target, band=band, radio=band, start_sid=target),
                            run, [], [])
    assert res.ok and f"  [warning] {ops.SPI_SHARED_WARNING}" in res.details, res.details
    assert asked == [(target, band, band)]


# ── the restart's legs ──

def test_restart_preflight_refuses_before_any_stop():
    """The stand-in has no `stop`, `_stop_impl` or later check: the refusal is returned first."""
    refusal = ActionResult(False, "Cannot restart 's': a callsign is required")
    me = _Self(operation_band=lambda t, b: b, _start_static_refusal=lambda t, op: None,
               _identity_refusal=lambda t, b, op: refusal)
    assert M._restart_preflight(me, "s", "433", False, False, True) is refusal


def test_restore_raises_only_what_the_start_did_not_bring_back():
    started = []
    stopped = ActionResult(True, "stopped", details=["  [stopped] main"],
                           results=(_row("main", Outcome.STOPPED, "stop"),))
    res = ActionResult(True, "Run applied for 's'.", details=["  [verified] main"],
                       results=(_row("main", Outcome.VERIFIED),),
                       next_commands=["lhpc status s"])
    me = _Self(_running_optional_components=lambda t: ["web"],
               start=lambda cid, apply, band: started.append(cid) or ActionResult(True, "ok"))
    out = M._restore_optional(me, "s", "433", stopped, res, ["web", "ui"])
    assert started == ["ui"]
    assert out.ok and out.summary == ("Restarted 's'. Run applied for 's'. Optional components "
                                      "restarted: ui.")
    assert [(r.component, r.action) for r in out.results] == [("main", "stop"),
                                                              ("main", "start")]

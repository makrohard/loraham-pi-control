"""§3 — typed lifecycle outcomes: success/verified derived from the typed outcome,
never from prose; top-level applied success requires all results ok AND verified."""

from lhpc.core.outcomes import Outcome, CompResult, applied_ok, any_blocking


def _r(outcome):
    return CompResult(component="c", action="start", outcome=outcome)


def test_success_and_verified_are_typed():
    assert _r(Outcome.VERIFIED).ok and _r(Outcome.VERIFIED).verified
    assert _r(Outcome.ALREADY_HEALTHY).ok and _r(Outcome.ALREADY_HEALTHY).verified
    assert _r(Outcome.STARTED).ok and not _r(Outcome.STARTED).verified


def test_non_success_outcomes():
    for o in (Outcome.BLOCKED, Outcome.MANUAL_REQUIRED, Outcome.FAILED,
              Outcome.UNVERIFIED, Outcome.STILL_RUNNING, Outcome.ENDPOINT_STILL_PRESENT):
        assert not _r(o).ok, o


def test_applied_ok_requires_all_verified():
    assert applied_ok([_r(Outcome.VERIFIED), _r(Outcome.ALREADY_HEALTHY)])
    assert not applied_ok([_r(Outcome.VERIFIED), _r(Outcome.STARTED)])   # not verified
    assert not applied_ok([_r(Outcome.VERIFIED), _r(Outcome.MANUAL_REQUIRED)])
    assert not applied_ok([])                                            # nothing applied


def test_any_blocking():
    assert any_blocking([_r(Outcome.VERIFIED), _r(Outcome.ENDPOINT_STILL_PRESENT)])
    assert not any_blocking([_r(Outcome.VERIFIED), _r(Outcome.STOPPED)])


def test_line_is_derived_from_outcome():
    r = CompResult(component="daemon", action="start", outcome=Outcome.VERIFIED,
                   summary="up on 433")
    assert r.line() == "  [verified] daemon: up on 433"

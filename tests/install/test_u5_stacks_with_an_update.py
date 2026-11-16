"""After a real self-update advance and a synced venv, the result names the stacks with an update on
their lane: one run of the NEW code's `lhpc source-check` (the updating process still holds the old
code). It never stops, starts or updates a stack; a run that cannot finish is one line naming it, and
the update's own outcome is unchanged."""
from __future__ import annotations

import sys

from selfupdate_fakes import WEB_INACTIVE

from lhpc.core import selfupdate
from lhpc.core.paths import ENV_RUNTIME_ROOT
from lhpc.core.probes.backends import CommandResult
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

SWEEP = (sys.executable, "-m", "lhpc", "source-check")


def _advancing(op_svc, monkeypatch, tmp_path, *, invocation, sweep=None, apply=None):
    svc, fake = op_svc(WEB_INACTIVE, units=True, invocation=invocation)
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: apply or
                        ActionResult(True, "Update applied.", data={"already": False}))
    if sweep is not None:
        fake.commands[SWEEP] = sweep
    if invocation:
        (tmp_path / "state" / "selfupdate.request").write_text("normal\n")
    return svc, fake


def _behind(svc):
    """A component whose id is not its stack's, reported behind by the sweep, and that stack."""
    stack, comp = next((s, c) for s in svc.stacks() for c in s.components if c.id != s.id)
    out = (f"OK    1 of 2 source(s) behind their remote for 'all'.\n  {comp.id}: behind\n"
           "  other: up to date\n")
    return stack.id, CommandResult(0, out, "")


def test_the_operator_update_names_the_stack_whose_source_is_behind(op_svc, monkeypatch, tmp_path):
    svc, fake = op_svc(WEB_INACTIVE, units=True, invocation=False)
    sid, sweep = _behind(svc)
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
    seen = []
    run = fake.run
    monkeypatch.setattr(fake, "run", lambda argv, timeout, cwd=None, env=None:
                        (seen.append((tuple(argv), timeout, env)), run(argv, timeout, cwd, env))[1])
    res = svc.self_update_apply_operator()
    assert res.ok and res.summary == "Update applied.", res
    assert f"Stacks with an update: {sid}." in res.details
    assert (SWEEP, 60.0, {ENV_RUNTIME_ROOT: str(tmp_path)}) in seen
    assert not any(a[:1] == ("systemctl",) and a[2:3] in (("start",), ("stop",)) for a, _t, _e in seen)


def test_the_one_click_update_records_the_stacks_with_an_update(op_svc, monkeypatch, tmp_path):
    svc, _fake = op_svc(WEB_INACTIVE, units=True, invocation=True)
    sid, sweep = _behind(svc)
    svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=True, sweep=sweep)
    res = svc.self_update_run_service()
    assert res.ok, res
    assert selfupdate.status_view(svc._paths)["last_apply"]["summary"] == (
        f"Update applied. Stacks with an update: {sid}.")


def test_nothing_behind_lists_nothing(op_svc, monkeypatch, tmp_path):
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False)
    res = svc.self_update_apply_operator()
    assert res.ok and list(SWEEP) in fake.calls
    assert not [d for d in res.details if "with an update" in d], res.details


def test_a_sweep_that_cannot_finish_is_one_line_and_the_update_stands(op_svc, monkeypatch, tmp_path):
    for sweep, why in ((CommandResult(124, "", "", timed_out=True), "timed out after 60 s"),
                       (CommandResult(1, "", "Traceback (most recent call last):\n"), "failed (exit 1)"),
                       (CommandResult(2, "usage: lhpc [-h] {status,list} ...\n", ""), "failed (exit 2)")):
        svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
        res = svc.self_update_apply_operator()
        assert res.ok and res.summary == "Update applied.", res
        assert [d for d in res.details if "with an update" in d] == [
            f"Could not list the stacks with an update: `lhpc source-check` {why}."]


def test_no_sweep_without_a_real_advance_or_after_a_failed_dependency_sync(op_svc, monkeypatch, tmp_path):
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False,
                           apply=ActionResult(True, "Already up to date.", data={"already": True}))
    svc.self_update_apply_operator()
    assert list(SWEEP) not in fake.calls
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False)
    pip = next(k for k in fake.commands if k[1:4] == ("-m", "pip", "install"))
    fake.commands[pip] = CommandResult(1, "", "boom")
    res = svc.self_update_apply_operator()
    assert not res.ok and list(pip) in fake.calls
    assert list(SWEEP) not in fake.calls

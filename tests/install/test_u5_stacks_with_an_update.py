"""After a real self-update advance and a synced venv, the result names the stacks with an update on
their lane: one run of the NEW code's `lhpc source-check --json` (the updating process still holds the
old code), whose stack ids come from the new code's own manifest. It never stops, starts or updates a
stack; a run that cannot finish is one line naming it, and the update's own outcome is unchanged."""
from __future__ import annotations

import json
import sys

from selfupdate_fakes import WEB_INACTIVE

from lhpc.adapters.cli import main as cli_main
from lhpc.core import selfupdate
from lhpc.core.paths import ENV_RUNTIME_ROOT, Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

SWEEP = (sys.executable, "-m", "lhpc", "source-check", "--json")


def _cli_sweep(tmp_path, monkeypatch, capsys, remote_head):
    """What `lhpc source-check --json` prints on a box whose daemon checkout (the manifest's) is at a*40 and
    whose remote answers `remote_head` (a CommandResult of `git ls-remote`), through the real source check."""
    paths, cmds = Paths(runtime_root=tmp_path / "box"), {}
    box = ControllerService(system=FakeSystem(commands=cmds).system, paths=paths)
    spec = next(c for s in box.stacks() for c in s.components if c.id == "loraham-daemon").source
    src = paths.resolve_source(spec.path)
    src.mkdir(parents=True)
    cmds.update({("git", "ls-remote", spec.remote, spec.branch or "HEAD"): remote_head,
                 ("git", "-C", str(src), "rev-parse", "HEAD"): CommandResult(0, "a" * 40 + "\n", "")})
    with monkeypatch.context() as m:
        m.setattr(cli_main, "ControllerService", lambda *a, **k: box)
        rc = cli_main.main(["source-check", "--json"])
    return box, CommandResult(rc, capsys.readouterr().out, "")


def _behind(tmp_path, monkeypatch, capsys):
    return _cli_sweep(tmp_path, monkeypatch, capsys, CommandResult(0, "b" * 40 + "\trefs/heads/main\n", ""))


def _advancing(op_svc, monkeypatch, tmp_path, *, invocation, sweep=None, apply=None):
    svc, fake = op_svc(WEB_INACTIVE, units=True, invocation=invocation)
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False: apply or
                        ActionResult(True, "Update applied.", data={"already": False}))
    if sweep is not None:
        fake.commands[SWEEP] = sweep
    if invocation:
        (tmp_path / "state" / "selfupdate.request").write_text("normal\n")
    return svc, fake


def test_source_check_json_names_the_stack_behind_by_its_own_manifest(tmp_path, monkeypatch, capsys):
    box, sweep = _behind(tmp_path, monkeypatch, capsys)
    doc = json.loads(sweep.stdout)
    assert doc["data"]["stacks_behind"] == [box.stack_of("loraham-daemon")] != ["loraham-daemon"], doc


def test_the_operator_update_names_the_stack_whose_source_is_behind(op_svc, monkeypatch, tmp_path, capsys):
    box, sweep = _behind(tmp_path, monkeypatch, capsys)
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
    seen = []
    run = fake.run
    monkeypatch.setattr(fake, "run", lambda argv, timeout, cwd=None, env=None:
                        (seen.append((tuple(argv), timeout, env)), run(argv, timeout, cwd, env))[1])
    res = svc.self_update_apply_operator()
    assert res.ok and res.summary == "Update applied.", res
    assert f"Stacks with an update: {box.stack_of('loraham-daemon')}." in res.details
    assert (SWEEP, 60.0, {ENV_RUNTIME_ROOT: str(tmp_path)}) in seen
    assert not any(a[:1] == ("systemctl",) and a[2:3] in (("start",), ("stop",)) for a, _t, _e in seen)


def test_the_one_click_update_records_the_stacks_with_an_update(op_svc, monkeypatch, tmp_path, capsys):
    box, sweep = _behind(tmp_path, monkeypatch, capsys)
    svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=True, sweep=sweep)
    res = svc.self_update_run_service()
    assert res.ok, res
    assert selfupdate.status_view(svc._paths)["last_apply"]["summary"] == (
        f"Update applied. Stacks with an update: {box.stack_of('loraham-daemon')}.")


def test_a_stack_the_old_manifest_does_not_know_is_named_as_the_new_code_names_it(op_svc, monkeypatch, tmp_path):
    out = json.dumps({"ok": True, "summary": "", "data": {"stacks_behind": ["a-stack-added-by-the-update"]}})
    svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=CommandResult(0, out, ""))
    assert svc.stack("a-stack-added-by-the-update") is None
    res = svc.self_update_apply_operator()
    assert "Stacks with an update: a-stack-added-by-the-update." in res.details, res.details


def test_a_stack_whose_check_failed_is_named_and_the_update_stands(op_svc, monkeypatch, tmp_path, capsys):
    box, sweep = _cli_sweep(tmp_path, monkeypatch, capsys, CommandResult(128, "", "fatal: unable to access"))
    sid = box.stack_of("loraham-daemon")
    assert sweep.returncode == 1 and json.loads(sweep.stdout)["data"]["stacks_unchecked"] == [sid], sweep
    svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
    res = svc.self_update_apply_operator()
    assert res.ok and res.summary == "Update applied.", res
    assert [d for d in res.details if sid in d] == [f"Could not check every source of: {sid}."], res.details


def test_nothing_behind_lists_nothing(op_svc, monkeypatch, tmp_path):
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False)
    res = svc.self_update_apply_operator()
    assert res.ok and list(SWEEP) in fake.calls
    assert not [d for d in res.details if "with an update" in d], res.details


def test_a_sweep_that_cannot_finish_is_one_line_and_the_update_stands(op_svc, monkeypatch, tmp_path):
    for sweep, why in ((CommandResult(124, "", "", timed_out=True), "timed out after 60 s"),
                       (CommandResult(1, "", "Traceback (most recent call last):\n"), "failed (exit 1)"),
                       (CommandResult(2, "", "usage: lhpc [-h] {status,list} ...\n"), "failed (exit 2)")):
        svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
        res = svc.self_update_apply_operator()
        assert res.ok and res.summary == "Update applied.", res
        assert [d for d in res.details if "with an update" in d] == [
            f"Could not list the stacks with an update: `lhpc source-check` {why}."]


def test_no_sweep_without_a_real_advance_or_after_a_failed_apply_or_dependency_sync(op_svc, monkeypatch, tmp_path):
    for apply in (ActionResult(True, "Already up to date.", data={"already": True}),
                  ActionResult(False, "Update could not be applied — git refused the fast-forward.")):
        svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, apply=apply)
        svc.self_update_apply_operator()
        assert list(SWEEP) not in fake.calls
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False)
    pip = next(k for k in fake.commands if k[1:4] == ("-m", "pip", "install"))
    fake.commands[pip] = CommandResult(1, "", "boom")
    res = svc.self_update_apply_operator()
    assert not res.ok and list(pip) in fake.calls
    assert list(SWEEP) not in fake.calls

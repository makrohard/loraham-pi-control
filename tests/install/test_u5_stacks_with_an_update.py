"""After a real self-update advance and a synced venv, the result names the stacks with an update on
their lane: one run of the NEW code's `lhpc source-check --json` (the updating process still holds the
old code), whose stack ids come from the new code's own manifest. It never stops, starts or updates a
stack; a run that cannot finish is one line naming it, and the update's own outcome is unchanged."""
from __future__ import annotations

import json
import sys

import pytest
from selfupdate_fakes import WEB_INACTIVE

from lhpc.adapters.cli import main as cli_main
from lhpc.core import selfupdate, stackupdates
from lhpc.core.paths import ENV_RUNTIME_ROOT, Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

SWEEP = (sys.executable, "-m", "lhpc", "source-check", "--json")
FAILED = "Could not list the stacks with an update: `lhpc source-check` {}."


def _result(ok=True, **lists):
    """A complete `source-check --json` result: the three lists a self-update reads, each [] unless given."""
    return json.dumps({"ok": ok, "summary": "", "data": {
        "stacks_behind": [], "stacks_lhpc_first": [], "stacks_unchecked": [], **lists}}) + "\n"


def _cli_sweep(tmp_path, monkeypatch, capsys, remote_head, local_toml="", journal="", cid="loraham-daemon",
               checkout=True, target=()):
    """What `lhpc source-check [target] --json` prints on a box whose `cid` checkout (the manifest's) is at a*40
    and whose remote answers `remote_head` (a CommandResult of `git ls-remote`), through the real source check;
    `local_toml` is the box's runtime-local config, `journal` its binary install journal."""
    paths, cmds = Paths(runtime_root=tmp_path / "box"), {}
    for rel, text in (("config/local.toml", local_toml), ("state/binary/install.journal.json", journal)):
        if text:
            (tmp_path / "box" / rel).parent.mkdir(parents=True)
            (tmp_path / "box" / rel).write_text(text)
    box = ControllerService(system=FakeSystem(commands=cmds).system, paths=paths)
    spec = next(c for s in box.stacks() for c in s.components if c.id == cid).source
    src = paths.resolve_source(spec.path)
    if checkout:
        src.mkdir(parents=True)
    cmds.update({("git", "ls-remote", spec.remote, f"refs/heads/{spec.branch}" if spec.branch else "HEAD"): remote_head,
                 ("git", "-C", str(src), "rev-parse", "HEAD"): CommandResult(0, "a" * 40 + "\n", "")})
    with monkeypatch.context() as m:
        m.setattr(cli_main, "ControllerService", lambda *a, **k: box)
        rc = cli_main.main(["source-check", *target, "--json"])
    return box, CommandResult(rc, capsys.readouterr().out, "")


def _behind(tmp_path, monkeypatch, capsys):
    return _cli_sweep(tmp_path, monkeypatch, capsys, CommandResult(0, "b" * 40 + "\trefs/heads/main\n", ""))


def _advancing(op_svc, monkeypatch, tmp_path, *, invocation, sweep=None, apply=None):
    svc, fake = op_svc(WEB_INACTIVE, units=True, invocation=invocation)
    monkeypatch.setattr(ControllerService, "self_update_apply", lambda self, *, force=False, stop_console=None: apply or
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
    out = _result(stacks_behind=["a-stack-added-by-the-update"])
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


def test_an_installed_source_with_an_invalid_remote_is_named_and_uninstalled_ones_are_not(
        op_svc, monkeypatch, tmp_path, capsys):
    # The probe answers `unknown` for the installed checkout (the http:// override is refused before any git
    # call) just as for every source never installed; only the installed one could not be checked.
    box, sweep = _cli_sweep(tmp_path, monkeypatch, capsys, CommandResult(0, "b" * 40 + "\trefs/heads/main\n", ""),
                            local_toml='[remotes]\nloraham-daemon = "http://insecure.example/LoRaHAM_Daemon.git"\n')
    sid = box.stack_of("loraham-daemon")
    data = json.loads(sweep.stdout)["data"]
    assert data["stacks_unchecked"] == [sid] and data["counts"]["error"] == 0, sweep
    svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
    res = svc.self_update_apply_operator()
    assert res.ok and res.summary == "Update applied.", res
    assert [d for d in res.details if "with an update" in d or "Could not" in d] == [
        f"Could not check every source of: {sid}."], res.details


def test_a_stack_whose_binary_install_is_unsafe_is_named_and_the_others_are_not(op_svc, monkeypatch, tmp_path, capsys):
    # A binary-installed daemon has no checkout; an interrupted install journal for it makes its receipt unsafe
    # (the start refuses it). The probe answers `unknown` there, as for a source never installed.
    journal = json.dumps({"state": "prepared", "stack": "daemon", "txn": "t1", "roots": [], "backups": {}})
    box, sweep = _cli_sweep(tmp_path, monkeypatch, capsys, CommandResult(0, "", ""), journal=journal,
                            checkout=False)
    assert box.binary_receipt_state("daemon")[0] == "unsafe" and box.binary_receipt_state("meshcom")[0] != "unsafe"
    assert json.loads(sweep.stdout)["data"]["stacks_unchecked"] == ["daemon"], sweep
    svc, _fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False, sweep=sweep)
    res = svc.self_update_apply_operator()
    assert res.ok and [d for d in res.details if "Could not" in d] == ["Could not check every source of: daemon."]


def test_an_unsafe_receipt_leaves_a_source_its_binary_does_not_cover_checked(tmp_path, monkeypatch, capsys):
    # meshcom's binary does not cover its GPS relay: a source-installed relay is compared on its own lane even
    # while the meshcom receipt is unsafe (an interrupted install journal).
    journal = json.dumps({"state": "prepared", "stack": "meshcom", "txn": "t1", "roots": [], "backups": {}})
    box, sweep = _cli_sweep(tmp_path, monkeypatch, capsys, CommandResult(0, "a" * 40 + "\trefs/heads/main\n", ""),
                            journal=journal, cid="meshcom-gps-relay", target=("meshcom-gps-relay",))
    assert box.binary_receipt_state("meshcom")[0] == "unsafe" and "meshcom-gps-relay" not in box.binary_spec(
        "meshcom").covers
    data = json.loads(sweep.stdout)["data"]
    assert data["counts"]["error"] == 0 and data["stacks_unchecked"] == [], sweep
    assert stackupdates.view(box._paths)["components"]["meshcom-gps-relay"]["status"] == stackupdates.UP_TO_DATE


def test_nothing_behind_lists_nothing(op_svc, monkeypatch, tmp_path):
    svc, fake = _advancing(op_svc, monkeypatch, tmp_path, invocation=False)
    res = svc.self_update_apply_operator()
    assert res.ok and list(SWEEP) in fake.calls
    assert not [d for d in res.details if "with an update" in d], res.details


def test_a_sweep_that_cannot_finish_is_one_line_and_the_update_stands(op_svc, monkeypatch, tmp_path):
    for sweep, why in ((CommandResult(124, "", "", timed_out=True), "timed out after 60 s"),
                       (CommandResult(1, "", "Traceback (most recent call last):\n"), "failed (exit 1)"),
                       (CommandResult(2, "", "usage: lhpc [-h] {status,list} ...\n"), "failed (exit 2)"),
                       *((CommandResult(0, other, ""), "failed (exit 0)")       # JSON, not the result's shape
                         for other in ('{"ok": true}\n', "null\n", '{"data": null}\n', "[1]\n"))):
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


def _without(gone):
    return json.dumps({"ok": True, "data": {k: v for k, v in json.loads(_result())["data"].items() if k != gone}})


@pytest.mark.parametrize("rc, out", [
    (1, '{"ok": false, "data": {}}\n'), (0, _without("stacks_unchecked")), (0, _result(stacks_behind="daemon")),
    (0, _result(stacks_behind=[1])), (0, _without("stacks_lhpc_first")), (0, _without("stacks_behind")),
    (0, _result(stacks_unchecked=None)), (0, '{"ok": true, "data": [[], [], []]}\n')])
def test_a_result_that_is_not_the_complete_contract_is_the_failure_line(swept_update, rc, out):
    details = swept_update(CommandResult(rc, out, ""))
    assert [d for d in details if "with an update" in d or "Could not" in d or "LHPC" in d] == [
        FAILED.format(f"failed (exit {rc})")], details


def test_a_complete_result_is_read_on_exit_1_and_with_a_key_a_later_release_adds(swept_update):
    for rc, out in ((1, _result(ok=False, stacks_unchecked=["daemon"])),
                    (0, _result(stacks_unchecked=["daemon"], stacks_added_later=["x"]))):
        details = swept_update(CommandResult(rc, out, ""))
        assert [d for d in details if "daemon" in d] == ["Could not check every source of: daemon."], details


def test_nothing_to_check_still_prints_the_three_empty_lists(tmp_path, monkeypatch, capsys, swept_update):
    # graywolf declares no source remote: the sweep's own "Nothing to check" path, through the CLI.
    box, sweep = _cli_sweep(tmp_path / "b", monkeypatch, capsys, CommandResult(0, "", ""), target=("graywolf",))
    assert not box._source_check_targets("graywolf")[0] and sweep.returncode == 0, sweep
    assert json.loads(sweep.stdout)["data"] == json.loads(_result())["data"], sweep
    details = swept_update(sweep)
    assert not [d for d in details if "with an update" in d or "Could not" in d], details


def test_a_refused_target_exits_1_with_no_lists_and_is_the_failure_line(tmp_path, monkeypatch, capsys, swept_update):
    _box, sweep = _cli_sweep(tmp_path / "b", monkeypatch, capsys, CommandResult(0, "", ""), target=("no-such",))
    doc = json.loads(sweep.stdout)
    assert sweep.returncode == 1 and doc["ok"] is False and "stacks_behind" not in doc["data"], sweep
    details = swept_update(sweep)
    assert [d for d in details if "with an update" in d] == [FAILED.format("failed (exit 1)")], details


@pytest.mark.parametrize("sweep, why", [
    (CommandResult(124, _result(), "", timed_out=True), "timed out after 60 s"),
    (CommandResult(0, _result(stacks_behind=["daemon"]), "", timed_out=True), "timed out after 60 s"),
    (CommandResult(-11, _result(stacks_behind=["daemon"]), ""), "failed (exit -11)"),
    (CommandResult(2, _result(stacks_behind=["daemon"]), ""), "failed (exit 2)")])
def test_how_the_run_ended_is_read_before_its_output(swept_update, sweep, why):
    details = swept_update(sweep)
    assert [d for d in details if "with an update" in d or "daemon" in d] == [FAILED.format(why)], details

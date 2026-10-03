"""Golden: `ControllerService.save_config_bundle` — what a Settings save does today, in order.

Phases: validation of the whole submission (no lock, no write on a refusal) → the exclusive
config lock → finish/refuse a pending journal → write the journal (`state/config-txn.json`) →
render every target under the lock (the rechecks live in the renderers) → atomic writes in order
(per-stack file, then the restart-required marker) → journal removed. Any failure restores every
pre-image. There is no admission and no operation lock: a save never touches a process.
"""

import json
import re

from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

FILE = "config/stacks/chat.toml"
WRITES = ["lock:config", "recheck:config-journal", "mutate:write:state/config-txn.json",
          "mutate:write:" + FILE]
SAVED = {"ok": True, "summary": "Config saved for 'chat'.", "data_keys": [],
         "next_commands": ["lhpc stack start chat"], "heads": [], "outcomes": []}
REFUSED = {"ok": False, "summary": "Config not saved for 'chat'.", "next_commands": [],
           "heads": [], "outcomes": []}
NOTHING = {"added": [], "removed": [], "changed": []}


def _svc(root, running=False):
    cmd = {555: ["loraham_chat"]} if running else {}
    svc = ControllerService(system=FakeSystem(cmdlines_data=cmd).system,
                            paths=Paths(runtime_root=root))
    svc.bootstrap(apply=True)
    return svc


def _text(freq):
    return f'# chat configuration (managed by lhpc — git-ignored).\nfile_tx_freq = "{freq}"\n'


def test_save_while_stopped(tmp_path, run_op):
    """intended: journal first, then the per-stack file; the journal is gone afterwards; a stopped
    stack gets no restart marker, only the "applies on the next Run" hint."""
    svc = _svc(tmp_path)
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}))
    assert run.fields == SAVED
    assert run.res.details == ["Start-time change — applies on the next Run."]
    assert run.phases == WRITES
    assert run.files == {"added": [FILE], "removed": [], "changed": []}
    assert (tmp_path / FILE).read_text() == _text("434.500")


def test_save_while_running_writes_the_restart_marker(tmp_path, run_op):
    """intended: a live consumer turns a restart-mode change into the restart-required marker,
    written in the same transaction after the per-stack file, carrying the launched value."""
    svc = _svc(tmp_path, running=True)
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}))
    assert run.fields == SAVED
    assert run.res.details == ["Restart the stack to apply."]
    assert run.phases == WRITES + ["mutate:write:state/restart-required/chat.json"]
    assert run.files == {"added": [FILE, "state/restart-required/chat.json"], "removed": [],
                         "changed": []}
    marker = json.loads((tmp_path / "state/restart-required/chat.json").read_text())
    marker.pop("created_at")
    assert marker == {"version": 1, "stack": "chat", "mode": "restart", "params": ["tx_freq"],
                      "band": "", "launched": {"|loraham-chat|tx_freq": "433.775"}}


def test_refused_by_validation(tmp_path, run_op):
    """intended: an unknown field refuses the whole submission before the lock — zero writes."""
    svc = _svc(tmp_path)
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"bogus": "1"}))
    assert run.fields == {**REFUSED, "data_keys": []}
    assert run.res.details == ["unknown config field: 'bogus'"]
    assert run.phases == [] and run.files == NOTHING


def test_pending_journal_is_recovered_first(tmp_path, run_op):
    """intended: a journal a crashed save left behind is finished under the lock (the pre-image
    restored) BEFORE the new save journals and writes; no journal survives."""
    svc = _svc(tmp_path)
    assert svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}).ok
    (tmp_path / FILE).write_text("# torn\n")
    (tmp_path / "state/config-txn.json").write_text(json.dumps({"version": 1, "targets": [
        {"kind": "stack", "rel": FILE, "pre": _text("434.500"), "existed": True,
         "mode": 0o644}]}))
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.600"}))
    assert run.fields == SAVED
    assert run.phases == ["lock:config", "recheck:config-journal", "mutate:write:" + FILE,
                          "mutate:write:state/config-txn.json", "mutate:write:" + FILE]
    assert run.files == {"added": [], "removed": ["state/config-txn.json"], "changed": [FILE]}
    assert (tmp_path / FILE).read_text() == _text("434.600")


def test_unrecoverable_journal_refuses(tmp_path, run_op):
    """intended: a malformed journal refuses the save (typed: data["reason"] ==
    "recovery-required") under the lock; the journal is retained and nothing is written."""
    svc = _svc(tmp_path)
    (tmp_path / "state/config-txn.json").write_text("{ not json")
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.5"}))
    assert run.fields == {**REFUSED, "data_keys": ["reason"]}
    assert run.res.data == {"reason": "recovery-required"}
    assert run.phases == ["lock:config", "recheck:config-journal"]
    assert run.files == NOTHING


def test_failed_write_rolls_back(tmp_path, run_op, monkeypatch):
    """intended: a write failing mid-transaction (here the marker, the third write) restores
    every pre-image — the per-stack file that did not exist is removed again — and drops the
    journal; the refusal names the rollback."""
    svc = _svc(tmp_path, running=True)
    real, calls = cfgmod._atomic_write, []

    def failing_third(paths, path, *a, **k):
        calls.append(path)
        if len(calls) == 3:
            raise OSError(28, "No space left on device")
        return real(paths, path, *a, **k)
    monkeypatch.setattr(cfgmod, "_atomic_write", failing_third)
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.5"}))
    assert run.fields == {**REFUSED, "data_keys": []}
    assert run.res.details == [
        "config transaction failed and was rolled back: [Errno 28] No space left on device"]
    assert run.phases == WRITES
    assert run.files == NOTHING


def test_malformed_stack_file_is_preserved(tmp_path, run_op):
    """intended: a present-but-malformed per-stack file is a typed refusal raised while rendering
    under the lock; the rollback writes its pre-image back byte for byte."""
    svc = _svc(tmp_path)
    (tmp_path / "config/stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / FILE).write_text("this is = = not toml\n")
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.5"}))
    assert run.fields == {**REFUSED, "data_keys": []}
    assert len(run.res.details) == 1 and re.fullmatch(
        r"config transaction failed and was rolled back: /\S+/config/stacks/chat\.toml: "
        r"Expected '=' after a key in a key/value pair \(at line 1, column 6\)",
        run.res.details[0])
    assert run.phases == WRITES
    assert run.files == NOTHING
    assert (tmp_path / FILE).read_text() == "this is = = not toml\n"

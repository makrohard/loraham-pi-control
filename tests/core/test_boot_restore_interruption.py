"""A boot restore interrupted at every durable write is recovered by the next run, which starts
the saved stack exactly once in total.

The driver (`service_boot_restore.boot_restore_run`) takes the controller admission marker,
journals its plan in `state/boot-restore.json`, claims an item `attempting` before the start,
prunes the consumed ownership evidence, settles the item and closes the journal. Each case fails
ONE of those writes (disk full, I/O error, Ctrl-C) and proves: the journal left behind is one the
next run reads (never `unsafe`), that run finishes the cleanup — evidence pruned, no admission
marker left, journal closed — and across both runs the stack was started exactly once: an item
claimed before the interruption is never started again, one still pending is started by the next
run.
"""
from __future__ import annotations

import json

import pytest

from interrupts import FAILURES, durable_writes, run_interrupted, tree
from lhpc.core import boot_restore as br
from lhpc.core import service_boot_restore as sbr
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

pytestmark = [pytest.mark.needs_session, pytest.mark.safety("boot-restore-once")]

LAUNCH = "loraham-kiss-tnc__433__999999__" + "ab" * 16
# The run's durable writes, in order (service_boot_restore.py `boot_restore_run`).
POINTS = [
    ("write_marker", "state/locks/controller-task-admission.owner"),   # admission
    ("atomic_write", "state/boot-restore.json"),                        # the plan
    ("atomic_write", "state/boot-restore.json"),                        # claim: attempting
    ("unlink", "state/owned/loraham-kiss-tnc__*__*__*.json"),           # prune the evidence
    ("atomic_write", "state/boot-restore.json"),                        # settle the item
    ("atomic_write", "state/boot-restore.json"),                        # close the run
    ("unlink", "state/locks/controller-task-admission.owner"),         # release admission
]


@pytest.fixture
def boot(tmp_path, monkeypatch):
    """A driver in a new boot with one stack (kiss on 433) the previous boot had running; the
    start is stubbed with the REAL signature and records only a start that passed its claim."""
    started = []

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, _operator=True, position=None, position_note=""):
        if _before_start_locked is not None:
            refusal = _before_start_locked()
            if refusal is not None:
                return refusal
        started.append((target, band))
        return ActionResult(True, "started")

    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "start", start)
    rec = {"launch_id": LAUNCH, "stack": "kiss", "component": "loraham-kiss-tnc", "band": "433",
           "pid": 999999, "role": "", "launched_at": 1000, "version": 1,
           "requested_target": "kiss", "start_scope": "stack", "boot_id": "OLDBOOT",
           "starttime": "123", "pgid": 999999, "sid": 999999}
    (tmp_path / "state" / "owned").mkdir(parents=True)
    (tmp_path / "state" / "owned" / f"{LAUNCH}.json").write_text(json.dumps(rec))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return svc, started


def test_the_run_writes_exactly_the_pinned_points(tmp_path, boot):
    svc, started = boot
    with durable_writes(tmp_path) as log:
        assert svc.boot_restore_run().ok
    assert log == POINTS and started == [("kiss", "433")]


@pytest.mark.parametrize("failure", sorted(FAILURES))
@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
def test_an_interrupted_run_is_finished_by_the_next(tmp_path, boot, point, failure):
    svc, started = boot
    _, outcome = run_interrupted(tmp_path, svc.boot_restore_run, fail_at=point,
                                 exc=FAILURES[failure])
    # (a) what is left is readable by the next run — never a journal that blocks restoration —
    # and evidence a claimed item left behind is on the next run's cleanup list. A run whose
    # only failure is that prune reports the restore it did (the prune is recorded, not lost).
    journal, state = br.load_journal(svc._paths)
    assert state in ("absent", "valid")
    evidence_left = (tmp_path / "state" / "owned" / f"{LAUNCH}.json").exists()
    claimed = bool(journal) and any(i["state"] != "pending" for i in journal["items"])
    if evidence_left and claimed:
        assert br.unpruned_consumed(journal), "consumed evidence the next run would not prune"
    if POINTS[point][0] != "unlink":
        assert not getattr(outcome, "ok", False), "an interrupted run must not report success"

    # (b)+(c) the next run recovers and completes
    assert svc.boot_restore_run().ok
    journal, state = br.load_journal(svc._paths)
    assert state == "valid" and journal["state"] in ("done", "no-plan")
    assert all(i["state"] not in ("pending", "attempting") for i in journal["items"])
    assert set(tree(tmp_path)) == {"state", "state/owned", "state/locks", "state/boot-restore.json",
                                   "state/locks/controller-task-admission.lock"}
    assert started == [("kiss", "433")], "the saved stack is started exactly once in total"

"""Golden: `ControllerService.boot_restore_run` — what the boot-restore driver does today, in order.

Phases: driver admission → the journal (`state/boot-restore.json`) written `running` with every
item `pending` → per item the normal gated start, whose hook (after the start's locks and
recheck) durably marks the item `attempting` → the item settled `succeeded`/`failed` with its
evidence (the prior boot's ownership record) pruned → the run's final state. A start refused
BEFORE its hook leaves the item `pending` and its evidence in place (the run is truncated); one
refused after it consumes the evidence. The two host gates (restore enabled, web integration
proven) and the boot id are pinned by `prior_boot`.
"""

import json

import pytest

from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session

EVIDENCE = "state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json"
START_LOCKS = ["config-stable", "lock:source-txn-index", "lock:claim.loraham.daemon-socket.433",
               "lock:claim.loraham.radio.433", "lock:claim.tcp.port.8001", "lock:lifecycle.kiss",
               "lock:source.src/loraham-daemon", "lock:source.src/loraham-kiss-tnc",
               "recheck:start", "recheck:identity"]
DRIVER = ["admission", "lock:controller-task-admission"]


def _journal(root):
    j = json.loads((root / "state" / "boot-restore.json").read_text())
    return j["state"], [(i["target"], i["band"], i["state"]) for i in j["items"]], j


def _fields(summary, ok=True, heads=()):
    return {"ok": ok, "summary": summary, "data_keys": ["driver_completed"], "next_commands": [],
            "heads": list(heads), "outcomes": []}


def test_restores_the_prior_boot(kiss_box, prior_boot, run_op):
    """intended: pending → attempting (inside the start, after its locks and recheck, before its
    first mutation) → succeeded → done; the old evidence is replaced by the new launch."""
    box = kiss_box()
    prior_boot(box.root)
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == _fields("Boot restore: 1 restored.")
    assert run.res.data == {"driver_completed": True}
    assert run.phases == DRIVER + ["journal:running[kiss=pending]"] + START_LOCKS + [
        "journal:running[kiss=attempting]", "mutate:feed-floor:433", "recheck:preflight",
        "mutate:spawn:loraham-kiss-tnc", "verify:endpoints:loraham-kiss-tnc", "verify:post-start",
        "final:running-band:433", "final:known-working:kiss", "final:clear-restart-marker:kiss",
        "final:clear-stop-intent:kiss", "journal:running[kiss=succeeded]",
        "journal:done[kiss=succeeded]"]
    state, items, j = _journal(box.root)
    assert (state, items) == ("done", [("kiss", "433", "succeeded")])
    assert j["items"][0]["result"] == {"ok": True, "summary": "Run applied for 'kiss'."}
    # the prior boot's record (pid 999999) is gone; the new launch's record has the same shape
    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
                                   "state/boot-restore.json", "state/daemon-feed-floor-433",
                                   "state/running/kiss.band"],
                         "removed": [], "changed": [EVIDENCE]}
    assert box.owned() == ["loraham-kiss-tnc"]


def test_nothing_to_restore(kiss_box, prior_boot, run_op):
    """intended: no evidence → a `no-plan` journal, ok, driver completed."""
    box = kiss_box()
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == _fields("Boot restore: nothing to restore.")
    assert run.phases == DRIVER + ["journal:no-plan[]"]
    assert _journal(box.root)[:2] == ("no-plan", [])
    assert run.files == {"added": ["state/boot-restore.json"], "removed": [], "changed": []}


def test_disabled_retires_the_evidence(kiss_box, prior_boot, run_op, monkeypatch):
    """intended: restore disabled → nothing started, the item `cancelled` with the reason, the
    old evidence retired, journal `disabled`."""
    box = kiss_box()
    prior_boot(box.root)
    monkeypatch.setattr(ControllerService, "boot_restore_enabled",
                        lambda self: (False, "disabled by [boot] restore"))
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == _fields("Boot restore disabled (disabled by [boot] restore) — nothing "
                                 "started; 1 old evidence record(s) retired.")
    assert run.phases == DRIVER + ["journal:disabled[kiss=cancelled]"] * 2
    assert _journal(box.root)[:2] == ("disabled", [("kiss", "", "cancelled")])
    assert run.files == {"added": ["state/boot-restore.json"], "removed": [EVIDENCE],
                         "changed": []}


def test_operator_stop_intent_is_honoured(kiss_box, prior_boot, run_op):
    """intended: a stack the operator stopped after its recorded launch is skipped (reason in the
    journal) and its evidence pruned; nothing is started."""
    box = kiss_box()
    prior_boot(box.root)
    box.svc._write_stop_intent(["kiss"])
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == _fields("Boot restore: nothing to restore (1 skipped — see the log).")
    assert run.phases == DRIVER + ["journal:no-plan[]"] * 2
    state, items, j = _journal(box.root)
    assert (state, items) == ("no-plan", [])
    assert [s["stack"] for s in j["skipped"]] == ["kiss"]
    assert run.files == {"added": ["state/boot-restore.json"], "removed": [EVIDENCE],
                         "changed": []}


def test_refused_before_the_hook_stays_pending(kiss_box, prior_boot, run_op,
                                               interrupted_install):
    """intended: a start refused before its hook (here an interrupted install, decided under the
    index lock) leaves the item `pending` and the evidence in place; the run reads truncated."""
    box = kiss_box()
    prior_boot(box.root)
    interrupted_install(box.root)
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == _fields("Boot restore: 0 restored, 1 pending (run truncated — restart "
                                 "the unit to continue).", ok=False)
    assert run.phases == DRIVER + ["journal:running[kiss=pending]", "config-stable",
                                   "lock:source-txn-index", "journal:failed[kiss=pending]"]
    assert _journal(box.root)[:2] == ("failed", [("kiss", "433", "pending")])
    assert run.files == {"added": ["state/boot-restore.json"], "removed": [], "changed": []}


def test_failed_after_the_hook_consumes_the_evidence(kiss_box, prior_boot, run_op):
    """intended: a start that fails after its hook (the endpoint never came up — UNVERIFIED,
    cleaned up) settles the item `failed` with the component outcome, and consumes the
    evidence: the next boot does not retry it."""
    box = kiss_box()
    prior_boot(box.root)
    box.endpoint_never_up()
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == _fields("Boot restore: 0 restored, 1 failed (evidence consumed — lhpc "
                                 "stack start <id>).", ok=False, heads=())
    assert run.res.details == [
        "kiss: loraham-kiss-tnc unverified — ready endpoint(s) never came up (127.0.0.1:8001: "
        "absent (family=ipv4)); cleanup: stopped"]
    assert run.phases[-3:] == ["mutate:signal:loraham-kiss-tnc", "journal:running[kiss=failed]",
                               "journal:failed[kiss=failed]"]
    state, items, j = _journal(box.root)
    assert (state, items) == ("failed", [("kiss", "433", "failed")])
    assert j["items"][0]["result"]["components"] == [
        {"component": "loraham-kiss-tnc", "outcome": "unverified",
         "reason": "ready endpoint(s) never came up (127.0.0.1:8001: absent (family=ipv4)); "
                   "cleanup: stopped"}]
    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
                                   "state/boot-restore.json", "state/daemon-feed-floor-433"],
                         "removed": [EVIDENCE], "changed": []}
    assert box.owned() == []


def test_refused_by_admission(kiss_box, prior_boot, run_op, uninstall_guard):
    """intended: driver admission refused → no journal, nothing consumed, the rerun command named
    (typed: data["admission_blocked"]; no driver_completed, so the unit fails)."""
    box = kiss_box()
    prior_boot(box.root)
    uninstall_guard(box.root)
    run = run_op(box.root, box.svc.boot_restore_run)
    assert run.fields == {
        "ok": False, "data_keys": ["admission_blocked"], "heads": [], "outcomes": [],
        "next_commands": ["systemctl --user restart lhpc-boot-restore.service"],
        "summary": "Boot restore not started: A controller uninstall is in progress "
                   "(.lhpc-uninstalling) — refusing to start new work. Let it finish, or recover "
                   "it. — no stack was restored and nothing was consumed; rerun with: systemctl "
                   "--user restart lhpc-boot-restore.service"}
    assert run.phases == DRIVER
    assert run.files == {"added": [], "removed": [], "changed": []}

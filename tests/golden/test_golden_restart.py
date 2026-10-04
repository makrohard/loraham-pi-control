"""Golden: `ControllerService.restart` — what a restart does today, in order.

Phases: admission → config-stability guard → the identity recheck (BEFORE the operation locks,
unlike start) → the union of the stop and start locks → the hook (`_before_restart_locked`) →
the start preflight (`_start_preflight_refusal`: dependency band, firewall gate, ambiguity, band
owners) → the public `stop` (signal, daemon release, feed floor, restart-marker clear) → the
public `start` (spawn, verify, finalize). A stop that is not verified aborts the restart before
anything is started.
"""

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.service_base import ActionResult

pytestmark = pytest.mark.needs_session

LOCKS = ["admission", "lock:controller-task-admission", "config-stable", "recheck:identity",
         "lock:source-txn-index", "lock:claim.loraham.daemon-socket.433",
         "lock:claim.loraham.radio.433", "lock:claim.tcp.port.8001", "lock:lifecycle.kiss",
         "lock:source.src/loraham-daemon", "lock:source.src/loraham-kiss-tnc"]
STOP_LEG = ["mutate:signal:loraham-kiss-serial", "mutate:signal:loraham-kiss-tnc",
            "lock:claim.audio.default", "lock:claim.tcp.port.12323", "lock:claim.tcp.port.18083",
            "lock:claim.tcp.port.7000", "lock:claim.tcp.port.8080", "lock:lifecycle.chat",
            "lock:lifecycle.daemon", "lock:lifecycle.graywolf", "lock:lifecycle.meshcom",
            "lock:lifecycle.voice", "mutate:signal:loraham-daemon", "mutate:feed-floor:433",
            "final:clear-restart-marker:kiss"]
START_LEG = ["recheck:start", "mutate:feed-floor:433", "mutate:spawn:loraham-kiss-tnc",
             "verify:endpoints:loraham-kiss-tnc", "verify:post-start", "final:running-band:433",
             "final:known-working:kiss", "final:clear-restart-marker:kiss",
             "final:clear-stop-intent:kiss"]
NOTHING = {"added": [], "removed": [], "changed": []}


def _running(kiss_box, **kw):
    box = kiss_box(**kw)
    assert box.svc.start("kiss", apply=True).ok
    return box


def test_plan_is_read_only(kiss_box, run_op):
    """intended: the restart plan merges the stop and start plans; no lock, no write."""
    box = _running(kiss_box)
    plan = run_op(box.root, lambda: box.svc.restart("kiss"))
    assert plan.fields == {
        "ok": True, "summary": "Restart plan for 'kiss': stop then run.",
        "data_keys": ["blockers", "changes", "commands", "dependents", "optional_restarted",
                      "other_bands"],
        "next_commands": ["lhpc stack restart kiss --yes"],
        "heads": ["[daemon] start/ensure", "[start] loraham-kiss-tnc"], "outcomes": []}
    assert plan.phases == ["recheck:identity", "recheck:preflight"]
    assert plan.files == NOTHING


def test_restart_a_running_stack(kiss_box, run_op):
    """intended: all locks and rechecks first, then a verified stop, then a verified start; the
    old ownership record is replaced by the new one."""
    box = _running(kiss_box)
    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
    assert run.fields == {
        "ok": True, "summary": "Restarted 'kiss'. Run applied for 'kiss'.", "data_keys": [],
        "next_commands": ["lhpc status kiss", "lhpc logs kiss", "lhpc stack stop kiss"],
        "heads": ["[already_stopped] loraham-kiss-serial", "[stopped] loraham-kiss-tnc",
                  "[stopped daemon] daemon", "[ok] daemon", "[log] loraham-kiss-tnc",
                  "[verified] loraham-kiss-tnc"],
        "outcomes": [("loraham-kiss-serial", "already_stopped"), ("loraham-kiss-tnc", "stopped"),
                     ("loraham-daemon", "stopped"), ("loraham-daemon", "verified"),
                     ("loraham-kiss-tnc", "verified")]}
    assert run.phases == LOCKS + ["recheck:preflight"] + STOP_LEG + START_LEG
    assert run.kinds == ["admission", "lock", "recheck", "lock", "recheck", "mutate", "lock",
                         "mutate", "final", "recheck", "mutate", "verify", "final"]
    # the record's pid/nonce changed; the normalized name did not
    assert run.files == {"added": [], "removed": [], "changed": [
        "state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json"]}
    assert box.owned() == ["loraham-kiss-tnc"]


def test_unverified_stop_aborts_before_any_start(kiss_box, run_op, monkeypatch):
    """intended: a stop that is not verified (the TNC really ignores SIGTERM and outlives the
    bounded wait) aborts the restart — nothing is spawned, the ownership record is retained and
    the original process is still alive."""
    box = kiss_box()
    box.survives_sigterm()
    assert box.svc.start("kiss", apply=True).ok
    monkeypatch.setattr(Lifecycle, "STOP_WAIT_S", 1)     # the bounded wait, shortened (prod: 5 s)
    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": "Restart aborted for 'kiss': stop was not verified.",
        "data_keys": [], "next_commands": ["lhpc status kiss"],
        "heads": ["[already_stopped] loraham-kiss-serial", "[still_running] loraham-kiss-tnc",
                  "[aborted] not"],
        "outcomes": [("loraham-kiss-serial", "already_stopped"),
                     ("loraham-kiss-tnc", "still_running")]}
    assert run.phases == LOCKS + ["recheck:preflight"] + STOP_LEG[:2]
    assert run.files == NOTHING
    assert box.owned() == ["loraham-kiss-tnc"] and box.tnc_alive()


def test_entry_hook_refusal_stops_nothing(kiss_box, run_op, phases):
    """intended: the job runner's hook (`_before_restart_locked`) runs after every lock and the
    identity recheck, and BEFORE the start preflight (`_start_preflight_refusal`, which the restart
    runs only afterwards, in `_restart_impl`) and the stop; its refusal leaves the running stack
    up and the preflight never runs."""
    box = _running(kiss_box)
    seen = []

    def hook():
        seen.append(list(phases))
        return ActionResult(False, "superseded")
    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True,
                                                   _before_restart_locked=hook))
    assert run.fields == {"ok": False, "summary": "superseded", "data_keys": [],
                          "next_commands": [], "heads": [], "outcomes": []}
    assert seen == [LOCKS] and run.phases == LOCKS
    assert run.files == NOTHING and box.owned() == ["loraham-kiss-tnc"]


def test_refused_by_admission(kiss_box, run_op, uninstall_guard):
    """intended: admission refuses the restart before any other lock; the stack stays up."""
    box = _running(kiss_box)
    uninstall_guard(box.root)
    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
    assert run.fields == {
        "ok": False, "data_keys": ["admission_blocked"], "next_commands": [], "heads": [],
        "outcomes": [],
        "summary": "A controller uninstall is in progress (.lhpc-uninstalling) — refusing to "
                   "start new work. Let it finish, or recover it."}
    assert run.phases == ["admission", "lock:controller-task-admission"]
    assert run.files == NOTHING and box.owned() == ["loraham-kiss-tnc"]


def test_band_owner_refuses_before_the_stop(kiss_box, run_op):
    """intended: a band owner refuses the restart in its preflight, before the stop — the
    running stack is left up and nothing is written."""
    box = _running(kiss_box)
    box.fake.cmdlines_data[300] = ["meshtasticd"]
    run = run_op(box.root, lambda: box.svc.restart("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": "Cannot run 'kiss': meshtastic must be stopped first.",
        "data_keys": [], "next_commands": ["lhpc stack stop meshtastic"], "heads": [],
        "outcomes": []}
    assert run.phases == LOCKS + ["recheck:preflight"]
    assert run.files == NOTHING and box.owned() == ["loraham-kiss-tnc"]

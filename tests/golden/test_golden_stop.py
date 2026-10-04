"""Golden: `ControllerService.stop` — what a stop does today, in order.

Phases: the operation locks (stop takes no admission and no config-stability guard) →
identity-verified signal per component → the client stop releases the daemon band it no longer
needs (its own lock set: every daemon client) → feed-floor reset → finalization (restart-required
marker cleared, operator stop intent written). A stop is verified only when the process ceased
AND its ready endpoint is gone; only then are the ownership record and markers removed.
"""

import pytest

from lhpc.core.lifecycle import Lifecycle

pytestmark = pytest.mark.needs_session

STOP_LOCKS = ["lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.radio.433",
              "lock:claim.tcp.port.8001", "lock:lifecycle.kiss"]
SIGNAL = ["mutate:signal:loraham-kiss-serial", "mutate:signal:loraham-kiss-tnc"]
RELEASE_DAEMON = ["lock:claim.audio.default", "lock:claim.tcp.port.12323",
                  "lock:claim.tcp.port.18083", "lock:claim.tcp.port.7000",
                  "lock:claim.tcp.port.8080", "lock:lifecycle.chat", "lock:lifecycle.daemon",
                  "lock:lifecycle.graywolf", "lock:lifecycle.meshcom", "lock:lifecycle.voice",
                  "mutate:signal:loraham-daemon", "mutate:feed-floor:433"]
FINAL = ["final:clear-restart-marker:kiss", "final:stop-intent:['kiss']"]
APPLIED = {"ok": True, "summary": "Stop applied for 'kiss'.", "data_keys": [],
           "next_commands": ["lhpc status kiss"]}


def test_plan_is_read_only(kiss_box, run_op):
    """intended: the stop plan lists the components, takes no lock and writes nothing."""
    box = kiss_box()
    plan = run_op(box.root, lambda: box.svc.stop("kiss"))
    assert plan.fields == {
        "ok": True, "summary": "Stop plan for 'kiss': 2 component(s).",
        "data_keys": ["changes", "commands", "dependents", "other_bands"],
        "next_commands": ["lhpc stack stop kiss --yes"],
        "heads": ["[stop] loraham-kiss-serial", "[stop] loraham-kiss-tnc"], "outcomes": []}
    assert plan.phases == [] and plan.files == {"added": [], "removed": [], "changed": []}


def test_stop_a_running_stack(kiss_box, run_op):
    """intended: locks → verified SIGTERM → daemon band released → finalization; the ownership
    record and the running-band marker go, the operator's stop intent is written."""
    box = kiss_box()
    assert box.svc.start("kiss", apply=True).ok
    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
    assert run.fields == {**APPLIED,
                          "heads": ["[already_stopped] loraham-kiss-serial",
                                    "[stopped] loraham-kiss-tnc", "[stopped daemon] daemon"],
                          "outcomes": [("loraham-kiss-serial", "already_stopped"),
                                       ("loraham-kiss-tnc", "stopped"),
                                       ("loraham-daemon", "stopped")]}
    assert run.phases == STOP_LOCKS + SIGNAL + RELEASE_DAEMON + FINAL
    assert run.kinds == ["lock", "mutate", "lock", "mutate", "final"]
    assert run.files == {"added": ["state/stop-intent/kiss.json"],
                         "removed": ["state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json",
                                     "state/running/kiss.band"],
                         "changed": []}
    assert box.owned() == []


def test_stop_when_nothing_runs(kiss_box, run_op):
    """intended: nothing owned is ALREADY_STOPPED — a verified stop, so the stop intent is still
    recorded (a later boot must not restore it), and the daemon band no client needs is
    released (its feed floor reset) as after a real stop."""
    box = kiss_box()
    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
    assert run.fields == {**APPLIED,
                          "heads": ["[already_stopped] loraham-kiss-serial",
                                    "[already_stopped] loraham-kiss-tnc",
                                    "[stopped daemon] daemon"],
                          "outcomes": [("loraham-kiss-serial", "already_stopped"),
                                       ("loraham-kiss-tnc", "already_stopped"),
                                       ("loraham-daemon", "stopped")]}
    assert run.phases == STOP_LOCKS + SIGNAL + RELEASE_DAEMON + FINAL
    assert run.files == {"added": ["state/daemon-feed-floor-433", "state/stop-intent/kiss.json"],
                         "removed": [], "changed": []}


def test_unowned_process_is_manual_required(kiss_box, run_op):
    """intended: a matching process LHPC does not own is never signalled — MANUAL_REQUIRED, ok
    False, no daemon release, no markers touched, no stop intent."""
    box = kiss_box()
    box.fake.cmdlines_data[4242] = ["loraham-kiss-tnc"]
    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": "Stop for 'kiss' is NOT fully verified — see details.",
        "data_keys": [], "next_commands": ["lhpc status kiss"],
        "heads": ["[already_stopped] loraham-kiss-serial", "[manual_required] loraham-kiss-tnc"],
        "outcomes": [("loraham-kiss-serial", "already_stopped"),
                     ("loraham-kiss-tnc", "manual_required")]}
    assert run.phases == [
        "lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.daemon-socket.868",
        "lock:claim.loraham.radio.433", "lock:claim.loraham.radio.868",
        "lock:claim.tcp.port.8001", "lock:lifecycle.kiss"] + SIGNAL
    assert run.files == {"added": [], "removed": [], "changed": []}


def test_process_that_does_not_cease(kiss_box, run_op, monkeypatch):
    """intended: a TNC that really ignores SIGTERM is STILL_RUNNING after the bounded wait — no
    SIGKILL, the ownership record and the running-band marker are retained, no stop intent, no
    daemon release, and the process is still alive."""
    box = kiss_box()
    box.survives_sigterm()
    assert box.svc.start("kiss", apply=True).ok
    monkeypatch.setattr(Lifecycle, "STOP_WAIT_S", 1)     # the bounded wait, shortened (prod: 5 s)
    run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": "Stop for 'kiss' is NOT fully verified — see details.",
        "data_keys": [], "next_commands": ["lhpc status kiss"],
        "heads": ["[already_stopped] loraham-kiss-serial", "[still_running] loraham-kiss-tnc"],
        "outcomes": [("loraham-kiss-serial", "already_stopped"),
                     ("loraham-kiss-tnc", "still_running")]}
    assert run.res.details[-1] == ("  [still_running] loraham-kiss-tnc: process did not cease "
                                   "after SIGTERM (no SIGKILL) — ownership retained")
    assert run.phases == STOP_LOCKS + SIGNAL
    assert run.files == {"added": [], "removed": [], "changed": []}
    assert box.owned() == ["loraham-kiss-tnc"] and box.tnc_alive()


def test_contended_lock_refuses(kiss_box, run_op, held_lock):
    """intended: another process holding the stack's lifecycle lock refuses the stop at once,
    naming the holder, with nothing signalled or written."""
    box = kiss_box()
    with held_lock(box.svc._paths, "lifecycle.kiss") as holder:
        run = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": f"Cannot stop 'kiss': resource 'lifecycle.kiss' is busy: golden on "
                                f"'x' (pid {holder})",
        "data_keys": [], "next_commands": ["lhpc status kiss"], "heads": [], "outcomes": []}
    assert run.phases == STOP_LOCKS
    assert run.files == {"added": [], "removed": [], "changed": []}

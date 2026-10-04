"""Golden: `ControllerService.start` — what a start does today, in order.

The box (`kiss_box`): kiss installed and built over a daemon already serving 433; the TNC is a
real process, and its ready endpoint is the real loopback listener that process opens (or never
opens), read from the kernel's TCP table. Phases: admission → the config-stability guard and
the operation locks (sorted) → the authoritative identity recheck → the preflight (firewall gate,
config ambiguity, band owners) → the daemon feed-floor reset → spawn + ownership record →
endpoint/post-start verification → finalization (running band, known-working candidate,
restart-required marker, stop intent).
"""

import pytest

from lhpc.core.service_base import ActionResult

pytestmark = pytest.mark.needs_session

KISS_LOCKS = [
    "admission", "lock:controller-task-admission", "config-stable", "lock:source-txn-index",
    "lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.radio.433",
    "lock:claim.tcp.port.8001", "lock:lifecycle.kiss", "lock:source.src/loraham-daemon",
    "lock:source.src/loraham-kiss-tnc",
]
RECHECK = ["recheck:start", "recheck:identity"]
KISS_RUN = ["recheck:preflight", "mutate:feed-floor:433", "mutate:spawn:loraham-kiss-tnc",
            "verify:endpoints:loraham-kiss-tnc", "verify:post-start", "final:running-band:433",
            "final:known-working:kiss", "final:clear-restart-marker:kiss",
            "final:clear-stop-intent:kiss"]
PLAN = {"ok": True, "summary": "Run plan for 'kiss': 2 component(s) in order.",
        "data_keys": ["blockers", "changes", "commands"],
        "next_commands": ["lhpc stack start kiss --yes"],
        "heads": ["[daemon] start/ensure", "[start] loraham-kiss-tnc"], "outcomes": []}
NOTHING = {"added": [], "removed": [], "changed": []}


def test_plan_then_apply_happy_path(kiss_box, run_op):
    """intended: the plan is read-only and lock-free; the apply takes admission and every lock
    before the recheck, mutates, verifies the endpoint, then finalizes."""
    box = kiss_box()
    plan = run_op(box.root, lambda: box.svc.start("kiss"))
    assert plan.fields == PLAN
    assert plan.phases == ["recheck:start", "recheck:identity", "recheck:preflight"]
    assert plan.files == NOTHING

    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert run.fields == {
        "ok": True, "summary": "Run applied for 'kiss'.", "data_keys": [],
        "next_commands": ["lhpc status kiss", "lhpc logs kiss", "lhpc stack stop kiss"],
        "heads": ["[ok] daemon", "[log] loraham-kiss-tnc", "[verified] loraham-kiss-tnc"],
        "outcomes": [("loraham-daemon", "verified"), ("loraham-kiss-tnc", "verified")]}
    assert run.phases == KISS_LOCKS + RECHECK + KISS_RUN
    assert run.res.details[-1] == ("  [verified] loraham-kiss-tnc: started; ready endpoint(s) up "
                                   "(127.0.0.1:8001: present (family=ipv4))")
    assert run.kinds == ["admission", "lock", "recheck", "mutate", "verify", "final"]
    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
                                   "state/daemon-feed-floor-433",
                                   "state/owned/loraham-kiss-tnc__433__<pid>__<nonce>.json",
                                   "state/running/kiss.band"],
                         "removed": [], "changed": []}
    assert box.owned() == ["loraham-kiss-tnc"]


def test_refused_missing_callsign(kiss_box, run_op):
    """intended: a licensed stack with no callsign is refused alike by the plan and, under every
    lock, by the apply's recheck — before any mutation (typed: data["enforce_fields"])."""
    box = kiss_box(callsign=False)
    (box.root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    (box.root / "src" / "LoRaHAM_Daemon" / "loraham_chat").write_text("#bin")
    refusal = {
        "ok": False, "data_keys": ["enforce_fields"], "heads": [], "outcomes": [],
        "summary": "Cannot start 'chat': a callsign is required to start 'chat' — set 'call' "
                   "(or the global operator callsign)",
        "next_commands": ["lhpc config chat call YOURCALL-10   # YOURCALL-10 = your callsign "
                          "(+optional SSID); or once for every licensed stack: lhpc config "
                          "operator --callsign YOURCALL"]}
    plan = run_op(box.root, lambda: box.svc.start("chat"))
    assert plan.fields == refusal and plan.files == NOTHING
    run = run_op(box.root, lambda: box.svc.start("chat", apply=True))
    assert run.fields == refusal
    assert run.res.data["enforce_fields"]
    assert run.phases == [
        "admission", "lock:controller-task-admission", "config-stable", "lock:source-txn-index",
        "lock:claim.loraham.daemon-socket.433", "lock:claim.loraham.radio.433",
        "lock:lifecycle.chat", "lock:source.src/loraham-daemon", "lock:source.src/LoRaHAM_Daemon",
        "recheck:start", "recheck:identity"]
    assert run.files == NOTHING


def test_refused_by_admission(kiss_box, run_op, uninstall_guard):
    """intended: admission is apply-only — the plan passes, the apply refuses at admission
    (typed: data["admission_blocked"]) before any other lock, with nothing written."""
    box = kiss_box()
    uninstall_guard(box.root)
    assert run_op(box.root, lambda: box.svc.start("kiss")).fields == PLAN
    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert run.fields == {
        "ok": False, "data_keys": ["admission_blocked"], "next_commands": [], "heads": [],
        "outcomes": [],
        "summary": "A controller uninstall is in progress (.lhpc-uninstalling) — refusing to "
                   "start new work. Let it finish, or recover it."}
    assert run.res.data["admission_blocked"] == "uninstalling"
    assert run.phases == ["admission", "lock:controller-task-admission"]
    assert run.files == NOTHING


def test_refused_by_interrupted_install(kiss_box, run_op, interrupted_install):
    """intended: an unresolved source-transaction journal is an apply-only refusal, decided under
    the source-txn index lock before the operation locks; the plan does not check it."""
    box = kiss_box()
    interrupted_install(box.root)
    assert run_op(box.root, lambda: box.svc.start("kiss")).fields == PLAN
    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert run.fields == {
        "ok": False, "data_keys": [], "next_commands": ["lhpc status kiss"], "heads": [],
        "outcomes": [],
        "summary": "Cannot start 'kiss': an unresolved source-transaction journal is present — "
                   "resolve it before starting"}
    assert run.phases == ["admission", "lock:controller-task-admission", "config-stable",
                          "lock:source-txn-index"]
    assert run.files == NOTHING


def test_refused_by_band_owner(kiss_box, run_op):
    """intended: a running stack owning the band is listed by the plan (ok, with blockers) and
    refuses the apply without stop_owners in the preflight — before the feed-floor reset, so the
    refused start writes nothing."""
    box = kiss_box()
    box.fake.cmdlines_data[300] = ["meshtasticd"]
    plan = run_op(box.root, lambda: box.svc.start("kiss"))
    assert plan.fields == {**PLAN, "heads": PLAN["heads"] + ["[conflict] loraham.radio.433",
                                                             "[conflict] radio"]}
    assert [b["holder_stack"] for b in plan.res.data["blockers"]] == ["meshtastic", "meshtastic"]
    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": "Cannot run 'kiss': meshtastic must be stopped first.",
        "data_keys": [], "next_commands": ["lhpc stack stop meshtastic"], "heads": [],
        "outcomes": []}
    assert run.phases == KISS_LOCKS + RECHECK + ["recheck:preflight"]
    assert run.files == NOTHING


def test_unverified_termination_is_cleaned_up(kiss_box, run_op):
    """intended: a TNC that is alive but never opens its ready endpoint (observed absent through
    the whole bounded wait) is stopped again (identity-verified) and reported UNVERIFIED; no
    ownership record and no running-band marker survive."""
    box = kiss_box()
    box.endpoint_never_up()
    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert run.fields == {
        "ok": False, "summary": "Run FAILED for 'kiss': loraham-kiss-tnc did not start/verify.",
        "data_keys": [],
        "next_commands": ["lhpc status kiss", "lhpc logs kiss", "lhpc stack stop kiss"],
        "heads": ["[ok] daemon", "[log] loraham-kiss-tnc", "[unverified] loraham-kiss-tnc"],
        "outcomes": [("loraham-daemon", "verified"), ("loraham-kiss-tnc", "unverified")]}
    assert run.res.details[-1] == (
        "  [unverified] loraham-kiss-tnc: ready endpoint(s) never came up "
        "(127.0.0.1:8001: absent (family=ipv4)); cleanup: stopped")
    assert run.phases == KISS_LOCKS + RECHECK + [
        "recheck:preflight", "mutate:feed-floor:433", "mutate:spawn:loraham-kiss-tnc",
        "verify:endpoints:loraham-kiss-tnc", "mutate:signal:loraham-kiss-tnc"]
    assert run.files == {"added": ["logs/start-loraham-kiss-tnc-433.log",
                                   "state/daemon-feed-floor-433"], "removed": [], "changed": []}
    assert box.owned() == []


def test_entry_hook_runs_after_locks_and_rechecks(kiss_box, run_op, phases):
    """intended: the hook the detached job runner and boot-restore pass (`_before_start_locked`)
    runs after every lock and the recheck, before the first mutation; its refusal is returned
    as is and nothing is written."""
    box = kiss_box()
    seen = []

    def hook():
        seen.append(list(phases))
        return ActionResult(False, "superseded")
    run = run_op(box.root, lambda: box.svc.start("kiss", apply=True, _before_start_locked=hook))
    assert run.fields == {"ok": False, "summary": "superseded", "data_keys": [],
                          "next_commands": [], "heads": [], "outcomes": []}
    assert seen == [KISS_LOCKS + RECHECK] and run.phases == KISS_LOCKS + RECHECK
    assert run.files == NOTHING


def test_interactive_launch_is_manual_required(kiss_box, run_op):
    """intended: an interactive main component is never spawned — its config is generated, the
    dashboard marker set, and the start reports MANUAL_REQUIRED with ok False (the CLI, the job
    runner and boot-restore count that alone as success: see the cross-path set)."""
    box = kiss_box()
    (box.root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    (box.root / "src" / "LoRaHAM_Daemon" / "loraham_chat").write_text("#bin")
    run = run_op(box.root, lambda: box.svc.start("chat", apply=True))
    assert run.fields == {
        "ok": False,
        "summary": "Run for 'chat': manual start required for loraham-chat — see the dashboard.",
        "data_keys": [],
        "next_commands": ["lhpc status chat", "lhpc logs chat", "lhpc stack stop chat"],
        "heads": ["[ok] daemon", "[manual_required] loraham-chat"],
        "outcomes": [("loraham-daemon", "verified"), ("loraham-chat", "manual_required")]}
    assert run.phases[-4:] == ["recheck:identity", "recheck:preflight", "mutate:feed-floor:433",
                               "mutate:config-files:loraham-chat"]
    assert run.files == {"added": ["config/files/lorachat.conf", "state/daemon-feed-floor-433",
                                   "state/interactive/chat.show"], "removed": [], "changed": []}
    assert box.owned() == []

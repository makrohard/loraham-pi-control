"""The console's maintenance pass: the cross-stack housekeeping the web's watchdog thread runs every
60 s (300 s on a box without the Wi-Fi feature), as one plain function with one outcome per task,
and the record of each task's last success and last failure (`state/maintenance.json`), which
`lhpc doctor` and the dashboard show.

The tasks themselves live in the service (`ControllerService`); this module only runs them in a
fixed order, contains each failure to its task, classifies it and records it. Absent record = the
pass has never run on this box (a box upgraded from a release without it, or a console that has not
started since)."""

from __future__ import annotations

import json
import time
from enum import Enum

from . import runtime_fs
from .paths import PathContainmentError

RECORD = ("state", "maintenance.json")
_RECORD_MAX = 64 * 1024
_MESSAGE_MAX = 300


class Outcome(str, Enum):
    OK = "ok"
    FAILED = "failed"


def _apply(svc):
    r = svc.webserver_apply_complete_pending()
    if r is None:
        return Outcome.OK, "no deferred Apply ready"
    return (Outcome.OK if r.ok else Outcome.FAILED), r.summary


def _crl(svc):
    return Outcome.OK, ("rebuilt or reload retried" if svc.crl_refresh_if_expired() else "current")


def _clock(svc):
    state = svc.pki_clock_normalise()
    if state == "reload-pending":
        return Outcome.FAILED, "certificates normalised, nginx did not reload (reload-pending)"
    return Outcome.OK, state


def _caps(results: dict):
    errors = [f"{name}: {res}" for name, res in results.items() if str(res).startswith("error")]
    if errors:
        return Outcome.FAILED, "; ".join(errors)
    return Outcome.OK, f"{len(results)} checked"


def _rf_logs(svc):
    svc.rflog_roll_native_all()
    return Outcome.OK, "rolled where due"


# The pass, in its order: (task name, the call). The names are the record's keys.
TASKS = (
    ("deferred-apply", _apply),
    ("crl-refresh", _crl),
    ("clock-normalise", _clock),
    ("start-logs", lambda svc: _caps(svc.cap_start_logs())),
    ("controller-logs", lambda svc: _caps(svc.cap_controller_logs())),
    ("rf-logs", _rf_logs),
)


def _one_line(text) -> str:
    """Whitespace runs (newlines included) to one space, bounded."""
    return " ".join(str(text).split())[:_MESSAGE_MAX]


def run(svc) -> dict:
    """Run every task once, in order; return `{task: (Outcome, message)}`. A task that raises an
    `Exception` is that task's FAILED outcome and the next task still runs (a `BaseException`, e.g.
    KeyboardInterrupt, propagates). The outcomes are recorded (`record`); a record that cannot be
    written does not stop the pass and is reported as the `record` key's FAILED outcome."""
    out: dict = {}
    for name, task in TASKS:
        try:
            outcome, message = task(svc)
        except Exception as exc:
            outcome, message = Outcome.FAILED, f"{type(exc).__name__}: {exc}"
        out[name] = (outcome, _one_line(message))
    try:
        record(svc._paths, out, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except (OSError, PathContainmentError) as exc:
        out["record"] = (Outcome.FAILED, _one_line(f"{RECORD[-1]} not written: {exc}"))
    return out


def _entry_ok(entry) -> bool:
    """One task's record has the shape `read` promises (a hand-edited one may not)."""
    return (isinstance(entry, dict) and isinstance(entry.get("last"), str)
            and all(isinstance(entry.get(k, {}), dict)
                    and all(isinstance(v, str) for v in entry.get(k, {}).values())
                    for k in ("last_success", "last_failure")))


def read(paths) -> tuple[str, dict]:
    """`(state, tasks)`: state "absent" (never run), "unreadable" (with the reason as the only
    task's message under the key "record") or "ok"; tasks `{name: {"last": "ok"|"failed",
    "last_success": {"at", "message"}, "last_failure": {"at", "message"}}}` (either may be
    missing)."""
    path = paths.under(*RECORD)
    try:
        data = json.loads(runtime_fs.read_text_regular(paths, path, max_bytes=_RECORD_MAX))
    except FileNotFoundError:
        return "absent", {}
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unreadable", {"record": {"last": "failed", "last_failure": {
            "at": "", "message": _one_line(exc)}}}
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, dict) or not all(_entry_ok(v) for v in tasks.values()):
        return "unreadable", {"record": {"last": "failed", "last_failure": {
            "at": "", "message": "not a maintenance record"}}}
    return "ok", tasks


def record(paths, outcomes: dict, at: str) -> None:
    """Merge one pass's outcomes into the record: each task's `last` outcome, and its
    `last_success` or `last_failure` replaced by this pass's. An unreadable record is replaced."""
    state, tasks = read(paths)
    tasks = tasks if state == "ok" else {}
    for name, (outcome, message) in outcomes.items():
        entry = dict(tasks.get(name) or {})
        entry["last"] = outcome.value
        entry["last_success" if outcome is Outcome.OK else "last_failure"] = {
            "at": at, "message": message}
        tasks[name] = entry
    runtime_fs.atomic_write(paths, paths.under(*RECORD),
                            json.dumps({"version": 1, "tasks": tasks}, sort_keys=True) + "\n", 0o600)


def failing(paths) -> list[tuple[str, str, str]]:
    """`(task, UTC time, message)` per task whose last outcome failed — an unreadable record is
    one such row (task "record") — in pass order. Empty when every task's last pass succeeded or
    the pass never ran (`read` tells those apart)."""
    _state, tasks = read(paths)
    order = [n for n, _ in TASKS] + sorted(set(tasks) - {n for n, _ in TASKS})
    return [(n, tasks[n].get("last_failure", {}).get("at", ""),
             tasks[n].get("last_failure", {}).get("message", ""))
            for n in order if n in tasks and tasks[n].get("last") != "ok"]

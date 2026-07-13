"""auto-install install/update run: operation lease, immutable-run context, and durable run state.

The auto-install driver ("Install and Build all Stacks") holds ONE outer operation boundary —
config-stable shared lock + the source-transaction index handoff + ALL affected source-path
locks — for its whole lifetime, and composes the existing typed lifecycle operations under
it. This module owns the pure data pieces:

  * `AutoInstallOperationContext` — the EXPLICIT proof object passed to every composed operation
    (install/update/build/test and the TX phase's start/stop). Ops validate that the
    context is the thread's ACTIVE auto-install boundary and that it covers their source paths;
    they never reacquire or release the covered locks.
  * the LEASE marker `state/auto-install-lease.json` — durable visibility + crash evidence of the
    held boundary, bound to the driver's full process identity. A DEAD lease remains
    MUTATION-BLOCKING for new auto-install runs until the explicit acknowledgement flow verifies
    the dead identity, verifies no pending source transaction, and archives it (no-follow).
  * the RUN marker `state/auto-install.json` — the task-list state machine, written at
    EVERY transition; a write failure STOPS the run (no progress without durable
    evidence). Read tri-state: absent | valid | unsafe — malformed is NEVER "absent".

All reads use `runtime_fs.read_text_regular` (descriptor-safe, no-follow); all writes use
`runtime_fs.write_marker` (atomic, contained). GET routes only ever call the read side.
"""
from __future__ import annotations

import json
import re
import time

from .paths import Paths, PathContainmentError
from . import runtime_fs

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

LEASE = ("state", "auto-install-lease.json")
MARKER = ("state", "auto-install.json")
RESERVATION = ("state", "auto-install-start.json")
PLAN = ("state", "auto-install-plan.json")

RUN_STATES = ("preparing", "running", "completed", "completed-with-failures",
              "aborted", "interrupted", "unsafe")
STACK_STATUSES = ("pending", "downloading", "building", "testing",
                  "success", "fail", "blocked")
TX_STATUSES = ("skipped", "pending", "running", "success", "fail")

# Terminal run states: a NEW run may start over these without acknowledgement. `aborted` is a clean
# operator-requested stop (retryable, no orphan-ack needed); `unsafe`/`interrupted` are NOT here.
TERMINAL_OK = ("completed", "completed-with-failures", "aborted")


class AutoInstallOperationContext:
    """Explicit outer auto-install-operation boundary handed to every composed operation.

    Carries the run identity and the EXACT set of source paths whose locks (plus the
    config-stability lock) the driver holds. `covers()` is the fail-closed check each
    composed op performs before trusting the boundary."""

    __slots__ = ("run_id", "source_paths")

    def __init__(self, run_id: str, source_paths):
        self.run_id = run_id
        self.source_paths = frozenset(source_paths)

    def covers(self, source_paths) -> bool:
        return frozenset(source_paths) <= self.source_paths


def log_name_for(run_id: str) -> str:
    """The run's log filename — derived EXCLUSIVELY from a validated run_id (marker `log`
    fields are informational and never opened). Raises ValueError on a bad id."""
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("invalid auto-install run id")
    return f"auto-install-{run_id[:8]}"


# A component build/test log created BY a auto-install run: a single flat leaf under logs/ whose
# name embeds the FULL 32-hex run id, so it is EXACTLY owned by one run (a prior run's log
# can never collide with — or be mistaken for — this run's, even when two run ids share
# their first eight hex characters) and is a strict, controller-derived character set.
# Browser-supplied strings are NEVER used to build these.
_LOG_BASE_RE = re.compile(r"^[0-9A-Za-z._-]{1,80}$")
COMPONENT_LOG_RE = re.compile(r"^auto-install-[0-9a-f]{32}-[0-9A-Za-z._-]{1,80}\.log$")


def component_log_prefix(run_id: str) -> str:
    """The exact `auto-install-<full-run-id>-` filename prefix that binds a component log to
    ONE run — used both to build names and to protect/recognise a run's logs. Raises
    ValueError on a bad run id."""
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("invalid auto-install run id")
    return f"auto-install-{run_id}-"


def component_log_name(run_id: str, base: str) -> str:
    """Run-specific component-log filename `auto-install-<full-run-id>-<base>.log`. `base`
    is a controller-derived job base (e.g. `build-loraham-daemon`, `test-<comp>`, with an
    optional `-<step>` suffix) restricted to a strict charset. Raises ValueError on a bad
    run id or base — never produces a path with separators, `..`, or NULs."""
    if not _LOG_BASE_RE.match(base or ""):
        raise ValueError(f"invalid component-log base: {base!r}")
    return f"{component_log_prefix(run_id)}{base}.log"


def component_log_base(run_id: str, base: str) -> str:
    """The job-NAME base (no `.log`) for a run-specific component log — `run_job` appends
    `.log`, and a multi-step build appends `-<i>` before that. Kept in lock-step with
    `component_log_name` so the registered filename equals what the job actually writes."""
    return component_log_name(run_id, base)[:-len(".log")]


def is_component_log_for(run_id: str, name: str) -> bool:
    """True iff `name` is a well-formed component-log leaf OWNED by `run_id` (bound to the
    FULL 32-hex id) — used to protect a live run's logs from pruning and to fail-closed on
    any other name (including a different run that shares the first eight hex chars)."""
    if not RUN_ID_RE.match(run_id or "") or not COMPONENT_LOG_RE.match(name or ""):
        return False
    return name.startswith(f"auto-install-{run_id}-")


def component_logs(marker) -> list:
    """The marker's DURABLE ordered component-log descriptors as validated
    (title, filename) tuples. Fail-closed: a non-list field, a non-dict entry, a missing
    field, or a filename that is not a well-formed component-log leaf is SKIPPED (never
    raised, never followed) — the browser never influences this list. The run_id binding
    is enforced against the marker's own run_id."""
    run_id = str((marker or {}).get("run_id", ""))
    raw = (marker or {}).get("component_logs")
    out = []
    if not isinstance(raw, list):
        return out
    for e in raw:
        if not isinstance(e, dict):
            continue
        title, log = e.get("title"), e.get("log")
        if isinstance(title, str) and isinstance(log, str) \
                and is_component_log_for(run_id, log):
            out.append((title, log))
    return out


# ---- lease -----------------------------------------------------------------------------


def write_lease(paths: Paths, run_id: str, pid: int, ident: dict,
                stacks, source_paths) -> bool:
    body = json.dumps({
        "version": 1, "run_id": run_id, "pid": int(pid),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ident": {k: ident[k] for k in
                  ("starttime", "pgid", "sid", "exec", "argv_fp", "argv_len")},
        "stacks": sorted(stacks), "source_paths": sorted(source_paths)}, indent=1)
    try:
        runtime_fs.write_marker(paths, paths.under(*LEASE), body)
        return True
    except (OSError, PathContainmentError):
        return False


def read_lease(paths: Paths):
    """Tri-state: (state, data) with state ∈ absent|valid|unsafe. Malformed/symlinked/
    unreadable is UNSAFE (mutation-blocking), never treated as absent."""
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*LEASE))
    except FileNotFoundError:
        return "absent", None
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unsafe", {"reason": f"auto-install lease unreadable ({exc})"}
    try:
        d = json.loads(raw)
        if (d.get("version") == 1 and RUN_ID_RE.match(str(d.get("run_id", "")))
                and isinstance(d.get("pid"), int) and d["pid"] > 0
                and isinstance(d.get("ident"), dict)
                and isinstance(d.get("source_paths"), list)):
            return "valid", d
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return "unsafe", {"reason": "auto-install lease malformed"}


def clear_lease(paths: Paths) -> bool:
    """Remove OUR OWN lease at the end of the held boundary (driver only)."""
    try:
        runtime_fs.unlink(paths, paths.under(*LEASE))
        return True
    except FileNotFoundError:
        return True
    except (OSError, PathContainmentError):
        return False


# ---- per-stack selection plan (fixed file, written under the start lock before the reservation) ----

def write_plan(paths: Paths, run_id: str, stacks: dict) -> bool:
    """Persist the per-stack auto-install selection for `run_id`. `stacks` =
    {sid: {install: bool, version: str, tests: bool, tx: bool}}. Written atomically under the held
    `auto-install-start` lock, immediately before the reservation, so concurrent starts cannot
    clobber the single fixed file."""
    body = json.dumps({"version": 1, "run_id": run_id, "stacks": stacks}, indent=1)
    try:
        runtime_fs.write_marker(paths, paths.under(*PLAN), body)
        return True
    except (OSError, PathContainmentError):
        return False


def read_plan(paths: Paths):
    """(state, data), state ∈ absent|valid|unsafe. STRUCTURAL validation only (run_id + a stacks
    dict whose entries carry a str `version` and bool `install`/`tests`/`tx`); the service layer
    revalidates run_id-match, keys==scope, and version∈SOURCE_CHOICES."""
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*PLAN))
    except FileNotFoundError:
        return "absent", None
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unsafe", {"reason": f"auto-install plan unreadable ({exc})"}
    try:
        d = json.loads(raw)
        stacks = d.get("stacks")
        if (d.get("version") == 1 and RUN_ID_RE.match(str(d.get("run_id", "")))
                and isinstance(stacks, dict) and stacks
                and all(isinstance(v, dict) and isinstance(v.get("version"), str)
                        and isinstance(v.get("install"), bool)
                        and isinstance(v.get("tests"), bool)
                        and isinstance(v.get("tx"), bool)
                        for v in stacks.values())):
            return "valid", d
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return "unsafe", {"reason": "auto-install plan malformed"}


def clear_plan(paths: Paths, run_id: str) -> bool:
    """Remove the plan ONLY when it belongs to `run_id` (so an old/stale driver can never delete a
    newer run's plan). Absent is success."""
    state, d = read_plan(paths)
    if state == "valid" and d.get("run_id") != run_id:
        return True                                   # not ours — leave it
    try:
        runtime_fs.unlink(paths, paths.under(*PLAN))
        return True
    except FileNotFoundError:
        return True
    except (OSError, PathContainmentError):
        return False


def archive(paths: Paths, which, suffix: str) -> tuple:
    """DESCRIPTOR-SAFE acknowledgement archive of a lease/marker/reservation leaf: the
    parent is reached by a no-follow descriptor walk and the leaf renamed with
    RENAME_NOREPLACE to a UNIQUE `<name>.<suffix>-<ts>-<nonce>.acked` sibling — an
    existing acknowledgement/evidence leaf is NEVER overwritten and no symlink is ever
    followed. Evidence is retained, never deleted. Returns (ok, detail); an unprovable
    archive is a truthful (False, reason)."""
    import os
    from . import source_fs
    src = paths.under(*which)
    try:
        with runtime_fs._walk_parent(paths, src, create=False) as (parent_fd, name):
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return True, "absent"
            for attempt in range(8):
                nonce = f"{os.getpid():x}-{attempt}-{int(time.monotonic() * 1e6) & 0xffffff:x}"
                dst = f"{name}.{suffix}-{nonce}.acked"
                try:
                    source_fs._rename_noreplace_at(parent_fd, name, dst)
                except FileExistsError:
                    continue                     # concurrent archive-name creation: retry
                except FileNotFoundError:
                    return True, "absent"
                try:
                    os.fsync(parent_fd)
                except OSError:
                    pass
                return True, dst
            return False, f"could not archive {name}: no free acknowledgement name"
    except FileNotFoundError:
        return True, "absent"
    except (OSError, PathContainmentError, source_fs.AtomicRenameUnavailable) as exc:
        return False, f"could not archive {src.name}: {exc}"


# ---- auto-install-start reservation --------------------------------------------------------------


RES_PHASES = ("spawning", "spawned", "claimed", "orphan-risk")


def _reservation_body(run_id: str, pid: int, ident: dict, phase: str,
                      child: str = "") -> str:
    d = {"version": 1, "run_id": run_id, "pid": int(pid), "phase": phase,
         "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "ident": {k: ident[k] for k in
                   ("starttime", "pgid", "sid", "exec", "argv_fp", "argv_len")}}
    if child:
        d["child"] = child                       # "none" | "uncertain" (spawning phase)
    return json.dumps(d, indent=1)


def write_reservation(paths: Paths, run_id: str, pid: int, ident: dict,
                      phase: str = "spawning") -> tuple:
    """EXCLUSIVE no-clobber, no-follow creation of the auto-install-start reservation (the launch
    slot binding the validated run_id). Phase `spawning` exists only INSIDE the held
    auto-install-start lock; before the lock releases the slot is REBOUND to the spawned child
    (`bind_reservation`) so the durable liveness authority is the actual run process,
    never the long-lived web server. (True, "") on success; (False, reason) otherwise."""
    if not RUN_ID_RE.match(run_id or ""):
        return False, "invalid run id"
    if phase not in RES_PHASES:
        return False, "invalid reservation phase"
    try:
        m = runtime_fs.open_marker_excl(paths, paths.under(*RESERVATION),
                                        _reservation_body(run_id, pid, ident, phase,
                                                          child="none"))
        m.close()
        return True, ""
    except FileExistsError:
        return False, "a auto-install-start reservation already exists"
    except (OSError, PathContainmentError) as exc:
        return False, f"auto-install-start reservation could not be persisted ({exc})"


def mark_reservation_child(paths: Paths, run_id: str, pid: int, ident: dict,
                           child: str) -> bool:
    """Record whether a child process may exist for a still-`spawning` slot: "uncertain"
    IMMEDIATELY BEFORE spawn_job (so any later residual record demands the operator's
    orphan confirmation), "none" when it is durably known no live child can remain
    (nothing spawned, or cessation identity-proven). Caller holds the auto-install-start lock."""
    if child not in ("none", "uncertain"):
        return False
    try:
        runtime_fs.write_marker(paths, paths.under(*RESERVATION),
                                _reservation_body(run_id, pid, ident, "spawning",
                                                  child=child))
        return True
    except (OSError, PathContainmentError):
        return False


def bind_reservation(paths: Paths, run_id: str, pid: int, ident: dict,
                     phase: str) -> bool:
    """Atomic rebind of the reservation to the CHILD's pid + complete identity (phase
    `spawned`, performed before the launch lock releases) or to the claiming driver
    (phase `claimed`). Caller holds the auto-install-start lock and has verified run_id/phase."""
    if phase not in RES_PHASES:
        return False
    try:
        runtime_fs.write_marker(paths, paths.under(*RESERVATION),
                                _reservation_body(run_id, pid, ident, phase))
        return True
    except (OSError, PathContainmentError):
        return False


def read_reservation(paths: Paths):
    """Tri-state: (state, data) with state ∈ absent|valid|unsafe. Malformed/symlinked/
    unreadable is UNSAFE (mutation-blocking), never absent."""
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*RESERVATION))
    except FileNotFoundError:
        return "absent", None
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unsafe", {"reason": f"auto-install-start reservation unreadable ({exc})"}
    try:
        d = json.loads(raw)
        if (d.get("version") == 1 and RUN_ID_RE.match(str(d.get("run_id", "")))
                and isinstance(d.get("pid"), int) and d["pid"] > 0
                and d.get("phase") in RES_PHASES
                and d.get("child", "") in ("", "none", "uncertain")
                and isinstance(d.get("ident"), dict)):
            return "valid", d
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return "unsafe", {"reason": "auto-install-start reservation malformed"}


def write_orphan_risk(paths: Paths, run_id: str, pid: int, reason: str,
                      ident: dict | None) -> bool:
    """Record the TERMINAL `orphan-risk` reservation phase: a spawned child whose
    cessation could not be proven. Durable, mutation-blocking evidence carrying the child
    PID when known and the exact reason; acknowledgement requires the operator's explicit
    inspection confirmation. Overwrites OUR slot under the held auto-install-start lock."""
    body = json.dumps({
        "version": 1, "run_id": run_id, "pid": int(pid) if pid else 1,
        "phase": "orphan-risk", "reason": reason,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ident": {k: (ident or {}).get(k, "") for k in
                  ("starttime", "pgid", "sid", "exec", "argv_fp", "argv_len")}},
        indent=1)
    try:
        runtime_fs.write_marker(paths, paths.under(*RESERVATION), body)
        return True
    except (OSError, PathContainmentError):
        return False


def clear_reservation(paths: Paths) -> bool:
    """No-follow removal of OUR reservation (spawn-failure rollback / end of run)."""
    try:
        runtime_fs.unlink(paths, paths.under(*RESERVATION))
        return True
    except (OSError, PathContainmentError):
        return False


# ---- run marker ------------------------------------------------------------------------


def new_marker(run_id: str, mode: str, source: str, tests: bool, tx: bool,
               stacks: list) -> dict:
    return {"version": 1, "run_id": run_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "finished_at": "", "state": "preparing", "mode": mode, "source": source,
            "tests": bool(tests), "tx": bool(tx),
            "log": log_name_for(run_id) + ".log",       # informational only, never opened
            "error": "",
            # DURABLE, run-owned, APPEND-ONLY ordered component build/test log descriptors
            # (each {"title", "log"}), recorded as each log is about to be created. Run
            # membership/order come from THIS list — never from mtime/glob/manifest.
            "component_logs": [],
            "tx_phase": {"status": "pending" if tx else "skipped", "detail": ""},
            "stacks": [{"id": s["id"], "name": s.get("name", s["id"]),
                        "status": "pending", "detail": "", "op": s.get("op", ""),
                        "tests": {"ran": False, "ok": None, "detail": ""},
                        "tx": {"ran": False, "ok": None, "detail": ""},
                        # Per-stack SELECTION (version/tests/tx) for the historical view. Carried only
                        # when the driver supplied it — never synthesized (a version="" would fail
                        # valid_marker); older markers simply omit it.
                        **({"selected": s["selected"]} if "selected" in s else {})}
                       for s in stacks]}


def valid_marker(d) -> bool:
    try:
        return (isinstance(d, dict) and d.get("version") == 1
                and bool(RUN_ID_RE.match(str(d.get("run_id", ""))))
                and d.get("state") in RUN_STATES
                and d.get("mode") in ("install", "update", "mixed")
                and isinstance(d.get("tests"), bool) and isinstance(d.get("tx"), bool)
                and isinstance(d.get("tx_phase"), dict)
                and d["tx_phase"].get("status") in TX_STATUSES
                and isinstance(d.get("stacks"), list)
                and all(isinstance(st, dict) and isinstance(st.get("id"), str)
                        and st.get("status") in STACK_STATUSES
                        and isinstance(st.get("tests"), dict)
                        and isinstance(st.get("tx"), dict)
                        # `selected` is OPTIONAL (back-compat with pre-selection markers); when
                        # present it must be complete + valid.
                        and ("selected" not in st
                             or (isinstance(st["selected"], dict)
                                 and st["selected"].get("version") in ("pinned", "dev", "stable")
                                 and isinstance(st["selected"].get("tests"), bool)
                                 and isinstance(st["selected"].get("tx"), bool)))
                        for st in d["stacks"]))
    except (TypeError, AttributeError):
        return False


def write_marker(paths: Paths, d: dict) -> bool:
    """Durable progress evidence. False on failure — THE RUN MUST STOP (the driver treats
    a failed transition write as a typed abort; never continues untracked)."""
    try:
        runtime_fs.write_marker(paths, paths.under(*MARKER), json.dumps(d, indent=1))
        return True
    except (OSError, PathContainmentError):
        return False


def read_marker(paths: Paths):
    """Tri-state: (state, data). Malformed/symlinked/unreadable → unsafe (never absent).
    Pure file read — the caller (services) layers the interrupted derivation on top."""
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*MARKER))
    except FileNotFoundError:
        return "absent", None
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unsafe", {"reason": f"auto-install run marker unreadable ({exc})"}
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return "unsafe", {"reason": "auto-install run marker malformed (not JSON)"}
    if not valid_marker(d):
        return "unsafe", {"reason": "auto-install run marker malformed (schema)"}
    return "valid", d

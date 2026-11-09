"""`kill`, the operator's hard stop (A §2.12, stage L3-K): its final state is the only promise, no process of
the stack (or of the one component) is left; data loss and inconsistent states are accepted (W45); nothing else
calls it (T-K6). Functions over the controller service: the CLI and the console call `kill(service, …)`."""
from __future__ import annotations

import contextlib
import os
import posixpath
import signal
import time
from pathlib import Path

from . import instances, procident
from .outcomes import CompResult, Outcome
from .service_base import ActionResult
from .snapshot_memo import invalidates_snapshot

GRACE_S = 5.0          # W47a: SIGTERM, then SIGKILL after 5 s to whatever is left
# The NO_INSTANCE_ARG exemptions whose argv names no instance before L5, so only their verified ownership record
# says which band they serve (G2-B2 F4).
BAND_BY_RECORD = {"loraham-voice", "loraham-voice-cli", "loraham-kiss-serial"}
sleep = time.sleep     # the injected clock (H3); tests replace it
monotonic = time.monotonic     # its reading, for the admission deadline; tests replace it with the same clock
_TERM_BUDGET_S = GRACE_S + 2.5  # one admission holder's termination: the SIGTERM grace, then the SIGKILL wait


def _parse(svc, target: str, component: str):
    """`<stack>[@<rid>]` and an optional component of that stack → (stack, copy, comps) or an error (W298: a kill is
    per radio). `<stack>` is the whole stack (copy None; the CLI refuses it first, W323, and the console follows);
    `<stack>@<rid>` is parse_target's instance on that radio, for the daemon that radio's own. A disabled radio's is
    still addressed."""
    sid, at, _rid = target.partition("@")
    stk = svc.stack(sid)
    if stk is None:
        return None, None, [], f"Unknown stack '{sid}' (kill takes a stack id)."
    copy, why = (instances.parse_target(svc, target, "kill")[1::2]) if at else (None, "")
    if at and copy is None:
        return None, None, [], f"Cannot kill {target}: {why}."
    comps = [c for c in stk.components if c.process is not None]
    if component:
        comps = [c for c in comps if c.id == component]
        if not comps:
            return None, None, [], f"'{component}' is no process component of '{sid}'."
    return stk, copy, comps, ""


def _targets(svc, stk, copy, comps) -> tuple[list[dict], list[str]]:
    """The instance's processes: the exact matcher's hits over /proc (incl. a user-started
    component, W46) plus the processes its verified ownership records name (T-R6, W88), scoped
    to `comps`. Each target carries the identity it was verified by, so nothing reused is signalled.
    → (targets, unresolved): a candidate that cannot be verified is named, never dropped (G2-B2 F3)."""
    from . import commands
    from .probes.process import matches
    life = svc._lifecycle()
    band = copy.band if copy is not None else ""
    hint = svc._launch_band_hint(stk.id, band)
    cfg_band = svc._config_band(stk.id, hint)
    table = svc._system.procfs.cmdlines()
    found: dict[int, dict] = {}
    unresolved: list[str] = []
    for comp in comps:
        recs = {r["pid"]: r for r in life.owned_records(comp.id) if life.verify_owned(r)[0]}   # one verified scan
        if comp.id == svc.DAEMON_ID:
            # The daemon of one radio by its exact `--id <rid>` (L5), else every daemon.
            pids = [p for p, a in table.items() if a and posixpath.basename(a[0]) == "loraham_daemon"
                    and (copy is None or _arg(a, "--id") == copy.rid)]
        else:
            # A spec exempt in NO_INSTANCE_ARG (voice, voice_cli, kiss-serial) matches by its
            # stack identity, i.e. any instance: sound only while today's one-band rule allows
            # one instance per box, until L5 gives them a socket token (W98).
            # stage L5 (W3): an instance's value names its radio, as its start renders it (the one token rule,
            # instances._token). G1-K4B 5: a bare kill takes every instance's token, never one radio's.
            tokens = []
            for inst in ([copy] if copy is not None else commands.stack_instances(stk, svc._paths) or (commands.TEMPLATE,)):
                tok = None
                with contextlib.suppress(Exception):       # an unresolvable value: records only
                    tok = instances._token(svc, comp, inst, inst.band or cfg_band or comp.band)
                tokens.append(tok)
            if comp.process.instance_arg and None in tokens:
                # A value cannot be resolved, so the matcher cannot scope this component: a process of
                # its spec that runs is a gap, not a non-target (G2-B2 F3).
                pids = []
                unresolved += [f"pid {p} {comp.id}: its instance value cannot be resolved"
                               for p, a in sorted(table.items()) if matches(comp.process, a, None)]
            else:
                pids = [p for p, a in sorted(table.items()) if any(matches(comp.process, a, t) for t in tokens)]
            # One ownership decision per pid (G1-C35): another instance's verified process is not this one's; with no
            # record a token hit is this instance's; a bare kill of the stack takes every instance's (its promise).
            pids = [p for p in pids if p not in recs or _mine(recs[p], copy)]
            if comp.id in BAND_BY_RECORD and copy is not None:
                unresolved += [f"pid {p} {comp.id}: cannot verify band {band} (no verified ownership record of it)"
                               for p in pids if p not in recs]
                pids = []
        for pid in pids:
            found.setdefault(pid, {"pid": pid, "component": comp.id, "group": False,
                                   "argv": " ".join(table.get(pid, []))})
        for rec in recs.values():
            if _mine(rec, copy):                        # a live record is one instance's
                found[rec["pid"]] = {"pid": rec["pid"], "component": comp.id, "group": True,
                                     "pgid": rec["pgid"], "record": rec,
                                     "argv": " ".join(table.get(rec["pid"], []))}
    # G2-B2 F1: a record target keeps the identity its record was verified against, never a later read. A
    # matcher hit is bound to one identity: read, the argv re-read, read again, the same start time.
    out = [{**t, "ident": {k: t["record"][k] for k in ("starttime", "pgid", "sid")}}
           for t in found.values() if "record" in t]
    hits = [t for t in found.values() if "record" not in t]
    first = {t["pid"]: procident.proc_identity(t["pid"]) for t in hits}
    again = svc._system.procfs.cmdlines() if hits else {}
    for t in hits:
        ident, pid = first[t["pid"]], t["pid"]
        if (ident is not None and again.get(pid) == table.get(pid)
                and (procident.proc_identity(pid) or {}).get("starttime") == ident["starttime"]):
            out.append({**t, "ident": ident})
        else:
            unresolved.append(f"pid {pid} {t['component']}: its identity cannot be read or changed while read")
    return out, unresolved


def _mine(rec: dict, copy) -> bool:
    """An ownership record is the selected instance's: its radio (13.12.5: by component and rid only)."""
    return copy is None or rec.get("rid", "") == copy.rid


def _arg(argv, opt: str) -> str:
    """The value of `opt` in `argv` (`--opt v` or `--opt=v`), else ""."""
    for i, tok in enumerate(argv):
        if tok == opt and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(opt + "="):
            return tok.split("=", 1)[1]
    return ""


def _holder(svc, holder, key: str) -> tuple[int, dict | None, str]:
    """Q-K2: the admission flock's holder from its own owner record, verified against /proc.
    → (pid, identity to terminate, "") for a start of this stack, else (pid or 0, None, why).
    No record, or a corrupt one, is an unverifiable holder: refused, never waited on."""
    if not isinstance(holder, dict) or not holder:      # ResourceBusy gives {} for none
        return 0, None, "unverifiable holder (no readable owner record)"
    pid = holder.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return 0, None, "unverifiable holder (corrupt owner record)"
    try:
        argv = [t for t in Path(f"/proc/{pid}/cmdline").read_bytes().decode(
            "utf-8", "replace").split("\0") if t]
    except OSError:
        argv = []
    named = (f"pid {pid} ({' '.join(argv) or 'cmdline unreadable'}; "
             f"{holder.get('operation', '?')} {holder.get('target', '')})")
    # The owner record names the identity of the process that took the admission; only that process is ever ended,
    # never a later one at the same pid (G1-B2-RG N1). A record without it (an earlier writer) proves nothing.
    took = holder.get("ident")
    if not procident.identity_complete(took):
        return pid, None, f"unverifiable holder {named}: its owner record carries no process identity"
    if not procident.identity_matches(took, pid):
        return pid, None, f"the task admission's owner record names another process than pid {named} now"
    sid, _, rid = key.partition("@")
    def same(target) -> bool:            # a start of this copy (G1-C35 (3)): its stack and its radio, `<stack>@<rid>`
        t_sid, _, t_rid = str(target).partition("@")
        return svc.stack_of(t_sid) == sid and t_rid == rid
    if holder.get("operation") not in ("start", "restart") or not same(holder.get("target", "")):
        return pid, None, f"the task admission is held by {named}"
    # A web start of this stack: its job marker carries the identity of its process.
    for job in svc.active_jobs(cleanup=False):
        raw = svc._read_job_marker(job.get("log") or "")
        if (raw and raw.get("pid") == pid and raw.get("op") in ("start", "restart")      # the owner record named the copy
                and procident.identity_matches(raw, pid)):       # the marker's operation agrees (G2-B2 N1)
            return pid, took, ""
    # A CLI `lhpc stack start|restart <stack>`: named by its own cmdline, identity read now.
    verb = argv[argv.index("stack") + 1:argv.index("stack") + 3] if "stack" in argv else []
    if (any(posixpath.basename(t) == "lhpc" for t in argv[:3]) and len(verb) == 2
            and verb[0] in ("start", "restart") and same(verb[1])):
        return pid, took, ""
    return pid, None, f"the task admission is held by {named}, not verifiable as a start of {key}"


def _admission(svc, stack, key: str, notes: list) -> str:
    """Flock first (A §2.12), on one deadline of the injected clock (G2-B2 N2): `_SELF_LOCK_WAIT_S` of waiting
    for the admission flock, extended by `_TERM_BUDGET_S` for the one start of this stack that may be ended
    while holding it (through the identity-verified terminate with its KILL leg). A second start taking it
    after that, or any other holder, is refused by name. "" once held, else the refusal."""
    from . import reslock
    deadline = monotonic() + svc._SELF_LOCK_WAIT_S
    ended: set[int] = set()
    while True:
        try:
            stack.enter_context(reslock.operation_lock(
                svc._paths, svc.ADMISSION_KEY, "kill", key))
        except reslock.ResourceBusy as busy:
            own = isinstance(busy.holder, dict) and busy.holder.get("pid") == os.getpid()
            if not own:                     # our own overlapping op releases shortly
                pid, ident, why = _holder(svc, busy.holder, key)
                if why:
                    return f"refused: {why}"
                if pid not in ended:
                    if ended:               # turnover: no second grace
                        return (f"refused: another start (pid {pid}) took the task admission after pid "
                                f"{min(ended)} was ended")
                    deadline += _TERM_BUDGET_S
                    if not svc._lifecycle()._terminate_unobserved(
                            pid, ident, kill_after=GRACE_S, sleep=sleep):
                        return (f"refused: the start holding the task admission (pid {pid}) "
                                "could not be ended")
                    ended.add(pid)
                    notes.append(f"pid {pid}: the in-flight start of {key} ended")
                    svc.active_jobs(cleanup=True)      # clears the dead start's job marker
            if deadline - monotonic() < 0.05:          # less than half a poll step left
                return "refused: the task admission was not freed in time"
            sleep(0.1)                  # the kernel frees the flock with the process
            continue
        counts = svc._held_counts()
        counts[svc.ADMISSION_KEY] = counts.get(svc.ADMISSION_KEY, 0) + 1
        stack.callback(svc._admit_release)
        return ""


def _signal(svc, targets: list[dict]) -> list[dict]:
    """SIGTERM every target, the 5 s grace, then SIGKILL each survivor; returns those not proven gone.
    Every signal follows an identity read: a target whose pid now runs another process is never sent one."""
    life = svc._lifecycle()

    def matches(t, now):
        # The identity the scan captured, against `now` (read just now): a pid target by its start time; a group
        # target (an ownership record's process group) by start time, pgid and sid, as `_terminate_unobserved`
        # checks it.
        if t["group"]:
            return life._same_session(t["pid"], now, t["ident"])
        return now["starttime"] == t["ident"]["starttime"]

    def same(t):                       # the guard for signalling: an unreadable identity is not ours (fail closed)
        now = procident.proc_identity(t["pid"])
        return now is not None and matches(t, now)

    def left_of(t):                    # a survivor unless provably gone or provably another process; an unreadable
        if t["group"]:                 # read is not proof of either, so it keeps the target. A group lives while a
            if reused(t):              # member does (G2-B2 F2); its leader's pid in another process proves it gone
                return False
            members = life._group_members(t["pgid"], int(t["ident"]["sid"]))
            return members is None or bool(members)
        if life._proc_ceased(t["pid"]):
            return False
        now = procident.proc_identity(t["pid"])
        return now is None or matches(t, now)

    def reused(t):                     # the leader's pid now runs another process: Linux reuses a pid only once no
        now = procident.proc_identity(t["pid"])    # live process has it as its pgid or sid, so the group is gone
        return now is not None and str(now["starttime"]) != str(t["ident"]["starttime"])

    def send(t, sig):
        if t["group"] and not same(t):  # the leader is gone: the group is reached by its members (G2-B2 F2)
            if not reused(t):
                life._signal_members(t["pgid"], int(t["ident"]["sid"]), sig)
            return
        if not same(t):
            return
        with contextlib.suppress(OSError):
            if t["group"]:
                os.killpg(t["pgid"], sig)
            else:
                os.kill(t["pid"], sig)

    for t in targets:
        send(t, signal.SIGTERM)
    left = targets
    for leg in (signal.SIGKILL, None):
        for _ in range(round((GRACE_S if leg else 2.5) / 0.1)):
            left = [t for t in left if left_of(t)]
            if not left:
                return []
            sleep(0.1)
        left = [t for t in left if left_of(t)]
        if leg is not None:
            for t in left:
                send(t, leg)
    return [t for t in left if left_of(t)]


@invalidates_snapshot
def kill(svc, target: str, component: str = "", apply: bool = False) -> ActionResult:
    """`lhpc kill <stack>[@<rid>] [<component>]`: SIGTERM, 5 s, SIGKILL over the instance's processes (every
    instance's for a bare stack), under the admission flock. A dry run names the processes (the confirm)."""
    stk, copy, comps, err = _parse(svc, target, component)
    if err:
        return ActionResult(False, err, next_commands=["lhpc list"])
    what = f"{component} of {target}" if component else target
    key = instances.key(stk.id, copy)   # the copy whose in-flight start may end
    if not apply:
        targets, unresolved = _targets(svc, stk, copy, comps)
        details = [f"  [kill] pid {t['pid']} {t['component']}: {t['argv']}" for t in targets]
        pids = [t["pid"] for t in targets]
        # A start of this stack holding the admission flock is ended first (the apply's
        # `_admission`), so the plan names it too: with no process yet it is the one change.
        from . import reslock
        owner = reslock.read_owner(svc._paths, svc.ADMISSION_KEY)
        if owner:
            pid, _ident, why = _holder(svc, owner, key)
            if not why:
                details.append(f"  [kill] pid {pid} the in-flight start of {stk.id}")
                pids.append(pid)
        if unresolved:          # never a successful plan while a candidate is unverifiable (G2-B2 F3)
            return ActionResult(False, f"Kill {what}: {len(unresolved)} candidate(s) cannot be verified; "
                                "nothing is signalled until they can.",
                                details=details + [f"  [unverifiable] {u}" for u in unresolved],
                                data={"changes": len(pids), "pids": pids, "unresolved": unresolved})
        return ActionResult(True, f"Kill {what}: {len(pids)} process(es).", details=details,
                            data={"changes": len(pids), "pids": pids})
    notes: list[str] = []
    with contextlib.ExitStack() as stack:
        refused = _admission(svc, stack, key, notes)
        if refused:
            return ActionResult(False, f"Cannot kill {what}: {refused}", details=notes,
                                next_commands=[f"lhpc status {stk.id}"])
        targets, unresolved = _targets(svc, stk, copy, comps)
        left = _signal(svc, targets)
        gone = {t["pid"] for t in targets} - {t["pid"] for t in left}
        life = svc._lifecycle()
        for t in targets:
            if t["pid"] in gone and "record" in t:
                life._remove_record(t["record"])
        hint = svc._launch_band_hint(stk.id, copy.band if copy is not None else "")
        for comp in comps:
            _log(svc, life, comp, hint, copy.rid if copy is not None else "")
        if not component and copy is not None and copy.rid:
            svc._write_stop_intent([instances.key(stk.id, copy)])       # the instance's own intent
        elif not component and copy is None:
            svc._safe_unlink(svc._band_marker(stk.id))
            svc.dismiss_interactive(stk.id)
            svc._write_stop_intent([stk.id])
    results = tuple(CompResult(
        component=c.id, stack=stk.id, action="kill",
        outcome=(Outcome.STILL_RUNNING if any(t["component"] == c.id for t in left)
                 else Outcome.STOPPED),
        summary="killed by operator") for c in comps)
    details = notes + [f"  pid {t['pid']} {t['component']}: "
                       f"{'still running' if t in left else 'gone'}" for t in targets]
    if unresolved:              # an unverifiable candidate gets no signal, and no "none left" (G2-B2 F3)
        return ActionResult(False, f"Kill {what}: {len(unresolved)} candidate(s) could not be verified: "
                            f"{'; '.join(unresolved)}", details=details, results=results)
    if left:
        return ActionResult(False, f"Kill {what}: {len(left)} process(es) still running "
                            f"after SIGKILL: {', '.join(str(t['pid']) for t in left)}",
                            details=details, results=results)
    return ActionResult(True, f"Killed {what}: no process of it is left.",
                        details=details, results=results)


def _log(svc, life, comp, band: str, rid: str = "") -> None:
    """'killed by operator' in the component's start log, the copy's own radio's (best effort)."""
    from . import runtime_fs
    log = life.start_log(comp, band, rid)
    if log is None:
        return
    with contextlib.suppress(Exception), runtime_fs.open_log_append(svc._paths, log) as fh:
        fh.write(b"[lhpc] killed by operator\n")

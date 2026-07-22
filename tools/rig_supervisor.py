#!/usr/bin/env python3
"""Unattended SSH supervisor that drives `lhpc auto-install` on the acceptance rig to completion.

UNCOMMITTED dev tool — NOT part of the shipped lhpc package. Runs on the dev box, drives the rig
over KEY-ONLY SSH. It never handles the operator password: key access is bootstrapped once by the
operator (`ssh-copy-id`), and this script only ever uses BatchMode SSH. Any accidental echo of a
secret is scrubbed by `_redact` before it reaches a log or the report.

Model: observe -> classify -> act; never retry blindly.

  * Reachability is the ONE failure that never exhausts retries: unreachable -> backoff 30s..5min,
    indefinitely, one timestamped log line per attempt.
  * The heavy `lhpc auto-install --yes` run lives in a DETACHED tmux session ON THE RIG, so it
    survives this supervisor's SSH dropping (and the supervisor restarting).
  * A live run is never touched — only polled and logged.
  * Leftover run state is cleared via `lhpc auto-install --recover` (with `--confirm-orphan` ONLY
    when the refusal names ORPHAN RISK and no build process is proven alive); on an older rig CLI
    without that verb, the four state files are moved aside instead — again only with liveness
    proven. Which path was used is recorded.
  * A failed build captures evidence (failing log tail, dmesg OOM, free, swapon, throttling) BEFORE
    a resume-from-cache retry, and STOPS after 3 identical-signature failures (a defect, not a
    transient).

Success == the goal state is reached == the loop stops.

Usage:  python3 tools/rig_supervisor.py            # run the loop
        python3 tools/rig_supervisor.py --once     # one observe/classify/act cycle, then exit
        python3 tools/rig_supervisor.py --recon     # read-only stock-take, then exit (no mutation)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---- configuration -------------------------------------------------------------------------------
HOST = "192.168.178.105"
USER = "makro"
SSH_TARGET = f"{USER}@{HOST}"
TMUX_SESSION = "lhpc"
# Remote lhpc invocation. Calibrated during --recon (may be a venv path or need `~/.local/bin`).
LHPC = "/home/makro/.local/bin/lhpc"
# The auto-install run: ALL stacks, no TX (real RF), host tests at default (off).
AUTO_INSTALL_CMD = f"{LHPC} auto-install --yes >> ~/auto-install.log 2>&1"

# Backoff for the ONE indefinite failure (unreachable).
BACKOFF_START_S = 30
BACKOFF_CAP_S = 300
# Poll cadence while a run is alive.
POLL_ALIVE_S = 60
# Same-signature build failures before declaring a defect and stopping.
MAX_SAME_SIGNATURE = 3

# Process names that prove a build/run is genuinely alive on the rig — including the CHILDREN a
# build spawns (pip/cmake/meson/git/make/compilers/build scripts), so an orphaned child that is
# still writing never reads as "no live run".
LIVE_PROCS = (r"[l]hpc auto-install|[s]cons|[c]c1plus|[p]io run|[p]latformio|[n]inja"
              r"|[c]make|[m]eson|[p]ip3? |[g]it (clone|fetch|checkout)|[m]ake|[g]cc|[g]\+\+"
              r"|[b]uild-qemu\.sh|[b]uild\.sh|[f]etch-qemu\.sh")

RUNS_DIR = Path(__file__).resolve().parent / "rig-runs"

# Belt-and-suspenders: never let a secret reach a log even if some tool echoes one. We do NOT store
# the password here; this only scrubs obvious patterns if they ever appear in captured output.

_SECRET_PATTERNS = [
    re.compile(r"(password[:=]\s*)\S+", re.I),
    re.compile(r"(sshpass\s+-p\s*)\S+", re.I),
]


def _redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(r"\1[REDACTED]", text)
    return text


# ---- logging -------------------------------------------------------------------------------------
class Log:
    def __init__(self, run_dir: Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "supervisor.log"
        self.fh = self.path.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"{_now_iso()}  {_redact(msg)}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- SSH transport -------------------------------------------------------------------------------
# BatchMode: never prompt for a password (key-only). Distinguish TRANSPORT failure (board rebooting,
# Wi-Fi dropped) from a remote command's own non-zero exit.
_SSH_BASE = [
    "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    SSH_TARGET,
]
# ssh exits 255 on its OWN failures (connect/auth/transport). A remote command returns its own code.
_TRANSPORT_MARKERS = (
    "Connection timed out", "Connection refused", "No route to host",
    "Network is unreachable", "Host is down", "Connection closed",
    "Operation timed out", "kex_exchange_identification", "Permission denied",
    "Could not resolve hostname", "port 22: ",
)


class Unreachable(Exception):
    """SSH transport failed — the rig is not reachable right now."""


def ssh(remote_cmd: str, timeout: int = 120) -> tuple[int, str]:
    """Run a remote command key-only. Returns (remote_rc, combined_output).
    Raises Unreachable on an SSH TRANSPORT failure (never confuses it with a remote rc)."""
    try:
        p = subprocess.run(_SSH_BASE + [remote_cmd], capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Unreachable(f"ssh timed out after {timeout}s")
    out = _redact((p.stdout or "") + (p.stderr or ""))
    if p.returncode == 255 and any(m in out for m in _TRANSPORT_MARKERS):
        raise Unreachable(out.strip().splitlines()[-1] if out.strip() else "ssh transport failure")
    return p.returncode, out


def reachable() -> bool:
    try:
        rc, _ = ssh("true", timeout=15)
        return rc == 0
    except Unreachable:
        return False


def wait_until_reachable(log: Log) -> None:
    """The ONE never-exhausting retry loop. Backoff 30s..5min, indefinite, one line per attempt."""
    delay = BACKOFF_START_S
    attempt = 0
    while True:
        attempt += 1
        if reachable():
            if attempt > 1:
                log(f"REACHABLE again after {attempt} attempt(s).")
            return
        log(f"UNREACHABLE (attempt {attempt}) — retrying in {delay}s (board reboot / Wi-Fi drop?).")
        time.sleep(delay)
        delay = min(delay * 2, BACKOFF_CAP_S)


# ---- rig state model -----------------------------------------------------------------------------
class RigState:
    def __init__(self):
        self.status_rc = -1         # `lhpc status` exit code (-1 = never ran / transport-lost)
        self.status_text = ""
        self.live_procs = ""        # pgrep -af output for build/run processes
        self.ai_status = ""         # `lhpc auto-install --status` output ("" if OLD CLI)
        self.old_cli = False        # rig CLI predates `auto-install --status/--recover`
        self.runtime_root = ""      # resolved LHPC runtime root (for state-file fallback)
        self.run_log_tail = ""      # tail of the teed ~/auto-install.log (AUTHORITATIVE outcome)

    @property
    def run_alive(self) -> bool:
        return bool(self.live_procs.strip())

    @property
    def recovery_needed(self) -> bool:
        blob = (self.status_text + "\n" + self.ai_status).lower()
        return "acknowledge" in blob and "recover" in blob


def take_stock(log: Log) -> RigState:
    """Observe BEFORE acting: status, live build/run processes, auto-install run-state."""
    st = RigState()
    st.status_rc, st.status_text = ssh(f"{LHPC} status 2>&1", timeout=120)
    _, st.live_procs = ssh(f"pgrep -af '{LIVE_PROCS}' || true", timeout=30)
    # AUTHORITATIVE outcome lives in the run summary, NOT in `lhpc status` — a component whose BUILD
    # FAILED still shows "stopped" (source present) in status, which would falsely read as done. The
    # teed run log carries the per-stack [success]/[fail] summary.
    _, st.run_log_tail = ssh("tail -n 40 ~/auto-install.log 2>/dev/null || true", timeout=30)
    rc, ai = ssh(f"{LHPC} auto-install --status 2>&1", timeout=60)
    low = ai.lower()
    if any(m in low for m in ("unrecognized argument", "invalid choice", "no such option",
                              "unknown flag", "unrecognized arguments")):
        st.old_cli = True
        st.ai_status = ""
        log("rig CLI is OLD: `auto-install --status/--recover` absent — any recovery will STOP for "
            "explicit operator action (no automatic state-file surgery).")
    else:
        st.ai_status = ai
    # Resolve the runtime root once (for the OLD-CLI state-file fallback).
    _, rr = ssh(f"{LHPC} paths 2>/dev/null | awk '/runtime|root/{{print $NF; exit}}' "
                "|| echo $HOME/.local/share/lhpc", timeout=30)
    st.runtime_root = rr.strip().splitlines()[-1] if rr.strip() else ""
    return st


# ---- goal-state detection ------------------------------------------------------------------------
# CALIBRATE against the rig's real `lhpc status` before trusting this to declare success. The rig
# runs an OLDER lhpc than the dev checkout; token wording may differ. Until validated live, the
# supervisor LOGS its assessment every cycle but the operator confirms the first "DONE".
GUI_SKIP_COMPONENTS = ("loraham-voice", "meshcore-nodegui")
# A component line's SECOND field is exactly one of these state tokens. Anything else (the controller
# version footer `v0.1.4 @…`, the `Next:` command suggestions `lhpc explain …`) is NOT a component
# line — filtering on a KNOWN state avoids the false positives that would keep "done" from ever firing.
_PRESENT = {"running", "stopped", "degraded", "verified", "started", "active", "skipped"}
_OUTSTANDING = {"not-installed", "installing", "downloading", "building", "not-built",
                "build-required", "failed", "fail", "blocked", "unsafe"}
_NEUTRAL = {"not-applicable"}                          # e.g. a library on the wrong band
_KNOWN_STATES = _PRESENT | _OUTSTANDING | _NEUTRAL


def _newest_outcome_lines(run_log_tail: str) -> list[str]:
    """The lines belonging to the NEWEST terminal outcome: from the LAST completion banner onward
    (the per-stack `[success]/[fail]` summary rows FOLLOW their banner), or the whole tail when no
    banner exists yet (a run in progress / interrupted). Everything before the last banner belongs
    to OLDER runs and must never leak into the newest verdict — an old success must not override a
    newer completed-with-failures, nor old [fail] rows a newer clean completion."""
    lines = run_log_tail.splitlines()
    idxs = [i for i, ln in enumerate(lines) if "auto-install run completed" in ln.lower()]
    return lines if not idxs else lines[idxs[-1]:]


def run_log_failures(run_log_tail: str) -> list[str]:
    """Stacks the NEWEST outcome reported FAILED — read from the authoritative `[fail] <stack>`
    summary rows AFTER the last completion banner (older runs' rows are excluded). Empty when the
    newest run had no failures. This OVERRIDES `assess_goal`, because a failed-build component
    still shows a present state in status."""
    fails = []
    for ln in _newest_outcome_lines(run_log_tail):
        s = ln.strip()
        if s.startswith("[fail]"):
            fails.append(s.split()[1].rstrip(":") if len(s.split()) > 1 else "unknown")
    return fails


def assess_goal(status_text: str) -> tuple[bool, list[str], int]:
    """(reached, outstanding[], recognized_rows). A component is DONE when installed+built (a
    present/skipped state); GUI-skip components and not-applicable rows are excluded. Only lines
    whose 2nd field is a KNOWN state count as components, so the controller footer and the `Next:`
    block are ignored. `recognized_rows` is the TOTAL of recognized component rows — ZERO means the
    status output was empty/garbled and MUST be treated as inconclusive by the caller, never as
    "nothing outstanding" (an empty/failed status used to read as goal-reached)."""
    outstanding: list[str] = []
    rows = 0
    for raw in status_text.splitlines():
        line = raw.strip()
        if line.startswith("Next:"):
            break                                      # command suggestions follow — stop
        parts = line.split()
        if len(parts) < 2:
            continue
        comp, state = parts[0], parts[1].lower()
        if state not in _KNOWN_STATES:
            continue                                   # not a component row
        rows += 1
        if comp in GUI_SKIP_COMPONENTS or state in _NEUTRAL or state in _PRESENT:
            continue                                   # skipped-by-design / n/a / already done
        outstanding.append(f"{comp} [{state}]")
    return (not outstanding), outstanding, rows


def authoritative_success(run_log_tail: str) -> bool:
    """True ONLY when the NEWEST terminal outcome is the authoritative completion banner with ZERO
    failed/blocked stacks (GUI skips are fine). Only the LAST banner decides — an OLDER clean
    banner higher up the (possibly legacy, appended) log must never outvote a newer
    completed-with-failures run. No banner at all = unproven. Status rows alone can NEVER prove
    success: an INTERRUPTED build shows 'stopped' exactly like a finished one."""
    newest = _newest_outcome_lines(run_log_tail)
    if not newest:
        return False
    s = newest[0].strip().lower()                    # the last banner line itself (or a non-banner
    if "auto-install run completed" not in s:        # first line when no banner exists yet)
        return False
    return ("completed-with-failures" not in s and "0 blocked" in s and "0 failed" in s)


# ---- actions -------------------------------------------------------------------------------------
def start_run(log: Log) -> bool:
    """Start the run in a DETACHED tmux session so it survives our SSH dropping. Never --tx.
    Returns False when tmux could not start the session — the caller must STOP as inconclusive
    rather than poll a run that never began."""
    # Kill only a DEAD/empty session name collision, never a live one (we only reach here with no
    # live run proven).
    ssh(f"tmux kill-session -t {TMUX_SESSION} 2>/dev/null || true", timeout=30)
    # ROTATE the run log so ~/auto-install.log holds EXACTLY the run started here: an appended log
    # let an OLD success banner (or old [fail] rows) leak into the newest run's verdict. Rotation
    # happens only HERE — a supervisor restart during an already-running job never reaches this
    # (run_alive short-circuits above it), so the current run's log stays intact. FAIL-CLOSED: a
    # genuinely ABSENT log is valid (fresh rig), but an EXISTING log that cannot be rotated must
    # abort the start — otherwise the new run appends (>>) and, if it dies before its own banner,
    # the old banner would decide its verdict.
    rot_rc, rot_out = ssh("[ ! -e ~/auto-install.log ] || "
                          "mv ~/auto-install.log ~/auto-install.log.prev", timeout=30)
    if rot_rc != 0:
        log(f"START ABORTED: could not rotate ~/auto-install.log (rc={rot_rc}): {rot_out.strip()} "
            "— an unrotated log could let an OLD completion banner decide the NEW run's verdict.")
        return False
    rc, out = ssh(f"tmux new-session -d -s {TMUX_SESSION} '{AUTO_INSTALL_CMD}'", timeout=60)
    if rc != 0:
        log(f"START FAILED: tmux new-session rc={rc}: {out.strip()}")
        return False
    log(f"STARTED detached run: tmux[{TMUX_SESSION}] `{AUTO_INSTALL_CMD}`. {out.strip()}")
    return True


# Sentinel prefix: the modern `lhpc auto-install --recover` DECLINED for a non-orphan reason. The caller
# must recognise this and STOP the rig — never move state files aside behind lhpc's back, never loop.
RECOVER_REFUSED = "RECOVER REFUSED"


def do_recover(log: Log, st: RigState) -> str:
    """Clear leftover run state, liveness-proven. Returns a one-line record of the path used.

    EVERY decisive refusal — a modern-CLI decline, a failed orphan-confirm, or an OLD CLI (no safe
    automatic recovery exists without identity-proven liveness) — returns a record prefixed with
    RECOVER_REFUSED so the caller STOPS the rig for explicit operator action; there is no file
    move-aside fallback anywhere.
    """
    if st.run_alive:
        return "RECOVER SKIPPED: a build/run process is alive — refusing to clear live state."
    if st.old_cli:
        # NO automatic state-file surgery on an old CLI: our process-name liveness check cannot
        # IDENTITY-prove that no orphaned build child is still writing, and moving state files
        # aside under a live writer corrupts the next run. Explicit operator recovery only.
        return (f"{RECOVER_REFUSED}: rig CLI is OLD (`auto-install --recover` absent) — automatic "
                "recovery is unsafe without identity-proven liveness; recover on the rig by hand, "
                "or update its lhpc first.")
    # Preferred path: the verb.
    rc, out = ssh(f"{LHPC} auto-install --recover 2>&1", timeout=60)
    if rc == 0:
        return f"RECOVER via `lhpc auto-install --recover` OK: {out.strip().splitlines()[-1] if out.strip() else ''}"
    if "ORPHAN RISK" in out:
        # Only confirm an orphan when we PROVED no build/run process is alive (checked above).
        rc2, out2 = ssh(f"{LHPC} auto-install --recover --confirm-orphan 2>&1", timeout=60)
        tail = out2.strip().splitlines()[-1] if out2.strip() else ""
        if rc2 != 0:
            # The confirm itself REFUSED/failed — that is decisive, never something to sail past.
            return (f"{RECOVER_REFUSED}: `--recover --confirm-orphan` declined/failed "
                    f"(rc={rc2}): {tail}")
        return (f"RECOVER via `--recover --confirm-orphan` (ORPHAN RISK, no live proc) OK: {tail}")
    # The modern verb exists but DECLINED for a non-orphan reason (ownership / lease / other guard). Do
    # NOT move state files aside — that bypasses the very checks it just enforced. Return the refusal
    # sentinel so the caller stops this rig rather than re-recovering or looping into another run.
    log(f"--recover refused (rc={rc}): {out.strip()}")
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return f"{RECOVER_REFUSED}: `lhpc auto-install --recover` declined (rc={rc}, not an orphan): {tail}"




# ---- evidence capture ----------------------------------------------------------------------------
def capture_evidence(log: Log, run_dir: Path, label: str) -> str:
    """Snapshot the rig's failure context BEFORE a retry. Returns a signature for bounded-retry."""
    stamp = _now_iso().replace(":", "")
    bundle = run_dir / f"evidence-{stamp}-{label}"
    bundle.mkdir(parents=True, exist_ok=True)
    cmds = {
        "status.txt": f"{LHPC} status 2>&1",
        "auto-install-status.txt": f"{LHPC} auto-install --status 2>&1 || true",
        "dmesg-oom.txt": "dmesg -T 2>/dev/null | grep -iE 'out of memory|killed process' | tail -40 || true",
        "free.txt": "free -h",
        "swapon.txt": "/sbin/swapon --show 2>/dev/null || swapon --show 2>/dev/null || true",
        "throttled.txt": "vcgencmd get_throttled 2>/dev/null || true",
        "tmux-tail.txt": f"tmux capture-pane -pt {TMUX_SESSION} -S -200 2>/dev/null || true",
        "build-logs.txt": "for f in $(ls -t ~/.local/share/lhpc/logs/*.log 2>/dev/null | head -3); "
                          "do echo \"===== $f =====\"; tail -80 \"$f\"; done || true",
    }
    sig_source = ""
    for name, cmd in cmds.items():
        try:
            _, out = ssh(cmd, timeout=60)
        except Unreachable as e:
            out = f"[unreachable capturing this: {e}]"
        (bundle / name).write_text(_redact(out), encoding="utf-8")
        if name in ("status.txt", "build-logs.txt"):
            sig_source += out
    log(f"EVIDENCE captured -> {bundle}")
    # Signature = the failing component + last error-ish line (stable across transient retries).
    fail_lines = [ln for ln in sig_source.splitlines()
                  if any(k in ln.lower() for k in ("fail", "error", "killed", "rc "))]
    return _redact("|".join(fail_lines[-3:]))[:400]


# ---- run report ----------------------------------------------------------------------------------
class Report:
    """A markdown run report with the timings the README still needs. Never contains the password."""
    def __init__(self, run_dir: Path):
        self.path = run_dir / "run-report.md"
        self.events: list[tuple[str, str]] = []
        self.t0 = time.time()

    def event(self, kind: str, detail: str) -> None:
        self.events.append((_now_iso(), _redact(f"{kind}: {detail}")))
        self.flush()

    def flush(self, outcome: str = "in progress") -> None:
        lines = [
            "# Acceptance-rig install run report",
            "",
            f"- Rig: `{HOST}` (Pi Zero 2W, headless), user `{USER}`, KEY-ONLY SSH.",
            f"- Outcome: **{outcome}**",
            f"- Wall-clock so far: **{_dur(time.time() - self.t0)}**",
            "",
            "## Timeline",
            "",
            "| time (UTC) | event |",
            "| --- | --- |",
        ]
        lines += [f"| {t} | {d} |" for t, d in self.events]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dur(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


# ---- the loop ------------------------------------------------------------------------------------
def supervise(once: bool = False) -> int:
    run_dir = RUNS_DIR / _now_iso().replace(":", "")
    log = Log(run_dir)
    report = Report(run_dir)
    log(f"supervisor start; run dir {run_dir}")
    report.event("start", f"supervising {SSH_TARGET}")

    fail_signatures: dict[str, int] = {}
    while True:
        try:
            wait_until_reachable(log)
            st = take_stock(log)

            # INCONCLUSIVE evidence NEVER becomes a verdict: a failed/empty status (or one with no
            # recognizable component rows) used to read as "nothing outstanding" -> false SUCCESS.
            if st.status_rc != 0 or not st.status_text.strip():
                log(f"INCONCLUSIVE: `lhpc status` rc={st.status_rc}, "
                    f"{len(st.status_text.strip())} bytes of output — refusing to infer any state.")
                report.event("stop", f"inconclusive status (rc={st.status_rc})")
                report.flush(outcome="STOPPED (inconclusive)")
                return 4
            reached, outstanding, rows = assess_goal(st.status_text)
            if rows == 0:
                log("INCONCLUSIVE: status output contained NO recognizable component rows — "
                    "refusing to infer any state.")
                report.event("stop", "inconclusive status (0 recognized rows)")
                report.flush(outcome="STOPPED (inconclusive)")
                return 4
            failed = run_log_failures(st.run_log_tail)
            if failed:                                    # authoritative: a failed build overrides
                reached = False                           # a status that shows it merely "stopped"
                outstanding += [f"{f} [build-failed]" for f in failed if f not in str(outstanding)]
            if reached and not authoritative_success(st.run_log_tail):
                # Status rows cannot distinguish an INTERRUPTED build from a finished one (both show
                # "stopped"). Success additionally requires the run's own completion banner with
                # 0 failed / 0 blocked — absent that, run auto-install to (re)prove completion.
                log("status shows nothing outstanding but NO authoritative auto-install completion "
                    "banner — treating as UNPROVEN, not success; (re)running auto-install.")
                reached = False
            if reached:
                log("GOAL STATE REACHED: every stack installed+built (authoritative completion "
                    "banner present); GUI-only components skipped.")
                report.event("done", "goal state reached")
                report.flush(outcome="SUCCESS")
                return 0
            log(f"outstanding: {', '.join(outstanding) if outstanding else '(parser found none — verify!)'}")

            if st.run_alive:
                log(f"run ALIVE — not touching it:\n{st.live_procs.strip()}")
                report.event("poll", f"run alive; outstanding={len(outstanding)}")
                if once:
                    return 0
                time.sleep(POLL_ALIVE_S)
                continue

            if st.recovery_needed:
                record = do_recover(log, st)
                log(record)
                report.event("recover", record)
                if record.startswith(RECOVER_REFUSED):
                    # lhpc refused to clear state (not an orphan). Moving files aside would bypass its
                    # guards; re-polling would just refuse again. Stop this rig — do not loop into a run.
                    report.event("stop", "recover refused by lhpc — not bypassing its guards")
                    report.flush(outcome="STOPPED (recover refused)")
                    return 3
                if once:
                    return 0
                continue

            # No live run, recovery clear, goal not reached: was the LAST run a failure to bound?
            low = (st.ai_status + st.status_text).lower()
            if any(k in low for k in ("completed-with-failures", "fail", "unsafe", "blocked")):
                sig = capture_evidence(log, run_dir, "buildfail")
                fail_signatures[sig] = fail_signatures.get(sig, 0) + 1
                report.event("build-fail", f"signature seen {fail_signatures[sig]}x")
                if fail_signatures[sig] >= MAX_SAME_SIGNATURE:
                    log(f"STOP: same failure signature {MAX_SAME_SIGNATURE}x — a DEFECT, not transient.\n"
                        f"signature: {sig}")
                    report.event("stop", f"defect: signature x{MAX_SAME_SIGNATURE}: {sig}")
                    report.flush(outcome="STOPPED (defect)")
                    return 2
                log("retrying (builds resume from cached artifacts).")

            if not start_run(log):
                # tmux could not start the session — stopping beats polling a run that never began.
                report.event("stop", "tmux new-session failed")
                report.flush(outcome="STOPPED (start failed)")
                return 4
            report.event("start-run", AUTO_INSTALL_CMD)
            if once:
                return 0
            time.sleep(POLL_ALIVE_S)

        except Unreachable as e:
            log(f"transport dropped mid-cycle: {e} — returning to reachability wait.")
            report.event("unreachable", str(e))
            if once:
                return 0
            # loop back to wait_until_reachable


def recon_only() -> int:
    run_dir = RUNS_DIR / ("recon-" + _now_iso().replace(":", ""))
    log = Log(run_dir)
    if not reachable():
        log("RECON: rig not reachable (key access installed yet?).")
        return 1
    st = take_stock(log)
    log("RECON status:\n" + st.status_text)
    log(f"RECON live procs: {st.live_procs.strip() or '(none)'}")
    log(f"RECON auto-install --status: {st.ai_status.strip() or '(old CLI / none)'}")
    log(f"RECON old_cli={st.old_cli} runtime_root={st.runtime_root!r}")
    reached, outstanding, rows = assess_goal(st.status_text)
    log(f"RECON goal reached={reached}; recognized_rows={rows}; outstanding={outstanding}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="one observe/classify/act cycle, then exit")
    ap.add_argument("--recon", action="store_true", help="read-only stock-take, then exit")
    args = ap.parse_args()
    if args.recon:
        return recon_only()
    return supervise(once=args.once)


if __name__ == "__main__":
    sys.exit(main())

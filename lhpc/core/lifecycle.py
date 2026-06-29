"""Component lifecycle: build, start, stop, logs, host test, and a bounded TX test.

`lhpc` is not a permanent supervisor: `start` launches a component fully detached
(its own session via start_new_session, output redirected to a log) and returns,
recording the launch identity; state is later reconstructed by the probe layer,
never from a stale PID file. `stop` is record-driven and identity-verified — it
only ever SIGTERMs a process group whose full identity (pid, start time, pgid,
sid, executable, argv fingerprint) still matches an LHPC ownership record and that
is an LHPC-owned session leader, then waits for verified cessation.

TX safety: `test` runs RX-safe host tests by default. A TX-capable test is a
distinct, explicit path that returns a `TxTestPlan` (stack, band, parameters,
expected RF effect, dummy-load reminder); the adapter must confirm before
`run_daemon_tx_test` is called. The TX test sends exactly one bounded frame and
verifies it via the daemon's STATS counter — never a continuous transmission.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import commands
from . import procident
from . import runtime_fs
from . import validators
from .config import Config
from .model import Component, Stack
from .outcomes import CompResult, Outcome
from .paths import Paths, PathContainmentError
from .probes import System
from .probes.process import probe_process
from .jobs import JobResult, JobState, run_job, tail_log


@dataclass
class StartLaunch:
    """Result of the RAW launch only: did the shell-free Popen + identity observation
    succeed. This is an INPUT to the typed lifecycle decision — it does NOT decide
    top-level success. The authoritative outcome is the `CompResult` the service layer
    builds from this plus readiness + required-post-start, aggregated into
    `ActionResult.results`. (See §8: one typed result decides success.)"""

    ok: bool
    log_path: str
    detail: str = ""
    # True when the launch could not be owned AND the residual process could not be
    # proven ceased — the service layer maps this to a typed UNVERIFIED (not FAILED).
    unverified: bool = False


@dataclass
class PostStartSchedule:
    """Result of SCHEDULING an OPTIONAL (detached) post-start. Non-gating, but every
    scheduling-stage failure (render / launcher write / log+dir setup / spawn / runtime
    containment) is reported via `detail` rather than swallowed."""

    ok: bool
    detail: str = ""


@dataclass
class TxTestPlan:
    stack: str
    component: str
    band: str
    parameters: str
    payload: str
    expected: str = "one LoRa frame transmitted into the connected dummy load"


@dataclass
class TxTestResult:
    ok: bool
    band: str
    txok_before: int | None
    txok_after: int | None
    detail: str
    evidence: dict = field(default_factory=dict)


class Lifecycle:
    # TX test: poll TXOK for up to TX_TEST_POLLS * TX_TEST_POLL_S seconds (CAD/LBT
    # can delay a frame on a busy channel before it actually transmits).
    TX_TEST_POLLS = 12
    TX_TEST_POLL_S = 0.5
    # Bounded wait to OBSERVE the launched process's real identity before recording
    # ownership (the direct-exec Popen IS the target, but /proc may lag a moment).
    OBSERVE_TIMEOUT_S = 1.5
    OBSERVE_POLL_S = 0.05

    def __init__(self, paths: Paths, stacks: tuple[Stack, ...], config: Config,
                 system: System, spawn=None) -> None:
        self.paths = paths
        self.stacks = stacks
        self.config = config
        self.system = system
        # Default spawn opens the start log through the anchored runtime-FS API; tests
        # inject their own `spawn` (the seam is preserved).
        self._spawn = spawn if spawn is not None else self._real_spawn

    def _real_spawn(self, argv: list[str], log_path: Path,
                    cwd: str | None = None, env: dict | None = None) -> int | None:
        """Launch a process fully detached (own session) with NO shell, returning its PID.
        The start log is opened APPEND through `runtime_fs.open_log_append` — descriptor-
        anchored, O_NOFOLLOW — so output can never be redirected through a swapped
        symlink/parent out of the runtime root. No shell; the controller starts and returns."""
        log = runtime_fs.open_log_append(self.paths, log_path)
        try:
            proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True,
                                    cwd=cwd, env=env)
            return proc.pid
        finally:
            log.close()

    # -- locations ---------------------------------------------------------

    def missing_requirements(self, comp: Component) -> list:
        """Component dependencies not satisfied: a command not on PATH, or (for
        -dev packages) a `check_file` header that does not exist."""
        missing = []
        for req in comp.requires:
            if req.check_file:
                if not self.system.fs.exists(req.check_file):
                    missing.append(req)
                continue
            if req.cmd:
                # Structured PATH probe — no shell. (req.cmd is a manifest-owned
                # command name, never a user value.)
                if shutil.which(req.cmd) is None:
                    missing.append(req)
        return missing

    def source_dir(self, comp: Component) -> Path:
        return self.paths.resolve_source(comp.source.path) if comp.source else self.paths.runtime_root

    def is_linked_source(self, comp: Component) -> bool:
        """True when a component's runtime source is a SYMLINK to an external working
        tree (adopt-by-link). Such a tree is read-only to LHPC: it may be observed and
        its binary launched, but LHPC must never build/test/write into it."""
        if not comp.source:
            return False
        try:
            return self.source_dir(comp).is_symlink()
        except OSError:
            return False

    def logs_dir(self) -> Path:
        return self.paths.under("logs")

    # -- build / start / stop / logs --------------------------------------

    def build(self, comp: Component, timeout: float = 600.0) -> JobResult:
        """Run a component's typed build steps (structured argv, shell=False). Each
        step may carry env and a `{pkgconfig:NAME}` token (resolved via pkg-config)."""
        if self.is_linked_source(comp):       # never build INTO an external linked tree
            return JobResult(name=f"build-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=["BLOCKED: source is a linked external "
                             "tree — build it yourself in that checkout (lhpc never "
                             "writes into a linked source)"])
        src, runtime = str(self.source_dir(comp)), str(self.paths.runtime_root)
        steps = comp.build_steps
        last: JobResult | None = None
        for i, step in enumerate(steps):
            try:
                argv = commands.build_step_argv(step, self.system.runner, runtime, src)
                env = commands.build_env(tuple((step.get("env") or {}).items()), runtime, src)
            except commands.CommandError as exc:
                return JobResult(name=f"build-{comp.id}", state=JobState.FAILED,
                                 returncode=1, log_path="", tail=[str(exc)])
            name = f"build-{comp.id}" + (f"-{i}" if len(steps) > 1 else "")
            last = run_job(self.system.runner, name=name, argv=argv, cwd=src, paths=self.paths,
                           env=(env or None), logs_dir=self.logs_dir(), timeout=timeout)
            if not last.ok:
                return last
        if last is None:        # nothing to build
            return JobResult(name=f"build-{comp.id}", state=JobState.SUCCEEDED,
                             returncode=0, log_path="", tail=["(no build steps)"])
        return last


    def start(self, stack: Stack, comp: Component, params: dict | None = None,
              band: str = "") -> StartLaunch:
        """Launch a component detached with NO shell: typed pre-steps run in Python,
        then the target is exec'd directly via Popen(shell=False, cwd, env). The real
        process identity is observed before ownership is recorded (no shell-to-exec
        race). A component with no structured `run_argv` cannot be started."""
        # Log-path resolution AND directory setup are INSIDE the typed boundary: a
        # symlinked/non-directory `logs/` makes `under()`/`ensure_dir` raise
        # PathContainmentError, which must become a typed StartLaunch failure, never an
        # exception leaked to the service/web layer.
        try:
            log = self.logs_dir() / f"start-{comp.id}.log"
            runtime_fs.ensure_dir(self.paths, self.logs_dir())
        except (OSError, PathContainmentError) as exc:
            return StartLaunch(False, "", f"runtime log setup failed: {exc}")
        src = str(self.source_dir(comp))
        runtime = str(self.paths.runtime_root)
        if not comp.run_argv:
            return StartLaunch(False, str(log), "no structured run command (run_argv)")
        op = self.config.operator
        try:
            commands.run_pre_steps(comp.pre_steps, runtime, src, band)
            argv = commands.expand_argv(comp.run_argv, comp, params, op, runtime, src, band)
            extra = commands.build_env(comp.run_env, runtime, src, band)
            cwd = commands._paths_subst(comp.run_cwd, runtime, src, band) if comp.run_cwd else src
        except (commands.CommandError, validators.ValidationError) as exc:
            return StartLaunch(False, str(log), f"invalid configuration: {exc}")
        env = {**os.environ, **extra} if extra else None
        try:
            # The default spawn opens the start log via the anchored runtime_fs; a
            # symlinked log leaf/parent raises PathContainmentError, also typed here.
            pid = self._spawn(argv, log, cwd=cwd, env=env)
        except (OSError, PathContainmentError) as exc:
            return StartLaunch(False, str(log), str(exc))
        status = self._observe_and_record(stack, comp, pid, band, argv)
        if status == "ok":
            return StartLaunch(True, str(log))
        # Not owned. Distinguish a clean failure (residual verified gone) from an
        # UNVERIFIED leak (residual could not be proven ceased — no SIGKILL).
        if status == "unverified":
            return StartLaunch(False, str(log),
                               "ownership could not be recorded and the residual process "
                               "could NOT be proven ceased (no SIGKILL) — UNVERIFIED",
                               unverified=True)
        return StartLaunch(False, str(log),
                           "launch could not be owned — residual process terminated")

    def _observe_and_record(self, stack: Stack, comp: Component, pid: int | None,
                            band: str, intended_argv: list[str]) -> str:
        """Observe the launched pid's identity then persist ownership. Returns:
          * "ok"         — identity observed AND a durable ownership record persisted;
          * "ceased"     — could not be owned, but the residual process is verified gone;
          * "unverified" — could not be owned AND the residual process could NOT be
                           proven ceased (a leaked process — typed UNVERIFIED upstream).
        With OBSERVE_TIMEOUT_S=0 (tests) it records directly."""
        if self.OBSERVE_TIMEOUT_S <= 0:
            # Capture a COMPLETE identity first and pass it to both record and cleanup,
            # so the zero-observation path follows the SAME no-signal-without-identity
            # rule as the normal path.
            ident = self._capture_identity(pid)
            if self.record_launch(stack, comp, pid, band, ident=ident):
                return "ok"
            return "ceased" if self._terminate_unobserved(pid, ident) else "unverified"
        if not pid:
            return "ceased"
        # Capture the spawned process's COMPLETE identity as early as possible (retrying
        # past a transient empty exec/cmdline), so a later cleanup can prove it is STILL
        # the same process (full identity match, not session leadership alone).
        launch_ident = self._capture_identity(pid)
        waited = 0.0
        while waited < self.OBSERVE_TIMEOUT_S:
            if not self._proc_alive(pid):
                return "ceased"             # died on its own before we could observe
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            except OSError:
                cmd = b""
            if self._argv_confirms(cmd, intended_argv):
                # Capture ONE complete identity at the moment command identity is
                # verified, and persist exactly that — no re-read/substitution.
                obs_ident = self._capture_identity(pid)
                # PID-REUSE guard: the observed identity must be the SAME process we
                # spawned. If we captured a complete launch identity and the observed
                # start time differs, this PID exited and was reused between spawn and
                # now — never record ownership of a reused PID; report unverified.
                if (self._identity_complete(launch_ident) and obs_ident is not None
                        and str(obs_ident.get("starttime")) != str(launch_ident.get("starttime"))):
                    return "unverified"
                if self.record_launch(stack, comp, pid, band, ident=obs_ident):
                    return "ok"
                # Identity observed but ownership could NOT be persisted -> never claim
                # the launch; terminate the verified-owned session.
                return "ceased" if self._terminate_unobserved(pid, obs_ident or launch_ident) else "unverified"
            time.sleep(self.OBSERVE_POLL_S)
            waited += self.OBSERVE_POLL_S
        # Could not confirm identity within the bound — terminate; result depends on
        # whether cessation is actually verified.
        return "ceased" if self._terminate_unobserved(pid, launch_ident) else "unverified"

    @staticmethod
    def _argv_confirms(observed: bytes, intended_argv: list[str]) -> bool:
        """True when the launched pid's observed `/proc/<pid>/cmdline` confirms it is
        running the intended program. The observed argv must EQUAL the intended argv, or
        contain it as a trailing SUFFIX — the suffix case covers a shebang script, where
        the kernel prepends the interpreter (e.g. `/bin/bash scripts/run.sh --env x`)
        ahead of the argv we exec'd. A suffix still requires every intended token
        (program path + all args) verbatim, so an unrelated or substituted process is
        never mistaken for ours. An empty intended argv never confirms."""
        if not intended_argv:
            return False
        want = [a.encode() for a in intended_argv]
        got = observed.split(b"\x00")
        while got and got[-1] == b"":       # drop the trailing NUL terminator(s)
            got.pop()
        return len(got) >= len(want) and got[len(got) - len(want):] == want

    @staticmethod
    def _proc_ceased(pid: int) -> bool:
        """True ONLY when the process is provably no longer running: /proc/<pid> is gone
        (ENOENT) or its state is a reaped-pending zombie (Z/X). A TRANSIENT /proc read
        failure (any other OSError) is NOT proof of cessation -> False."""
        try:
            data = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError:
            return True                         # process is gone
        except OSError:
            return False                        # transient read failure — not proof
        rp = data.rfind(")")
        state = data[rp + 2:rp + 3] if rp != -1 else ""
        return state in ("Z", "X", "x")

    def _terminate_unobserved(self, pid: int, launch_ident: dict | None = None) -> bool:
        """SIGTERM a just-spawned but unobserved session — ONLY if the pid STILL has the
        complete identity we captured at launch. Never signal on PID/session leadership
        alone: without a COMPLETE captured launch identity (or on ANY field mismatch) we
        refuse to signal and report cessation only if it is independently PROVEN.
        Returns True only when cessation is verified (no SIGKILL)."""
        # No complete captured identity -> we cannot prove this pid is ours -> never
        # signal; "ceased" only if the process is provably gone.
        if not self._identity_complete(launch_ident):
            return self._proc_ceased(pid)
        now = self._proc_identity(pid)
        if now is None:
            # Identity unreadable: only "ceased" if PROVABLY gone — a transient /proc
            # read failure is never treated as proof of cessation.
            return self._proc_ceased(pid)
        # FULL identity match required (a reused pid differs in start time; an unrelated
        # process differs in exec/argv). ANY mismatch: do NOT signal, NOT proven ceased.
        for k in ("starttime", "pgid", "sid", "exec", "argv_fp"):
            if str(now.get(k)) != str(launch_ident.get(k)):
                return False
        try:
            if os.getpgid(pid) != pid:          # must still be its own session leader
                return False
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            return False
        for _ in range(25):                     # NO SIGKILL — bounded wait for SIGTERM
            if self._proc_ceased(pid):
                return True
            time.sleep(0.1)
        return self._proc_ceased(pid)           # True ONLY if cessation is PROVEN

    def spawn_post_start(self, stack: Stack, comp: Component,
                         params: dict | None = None, band: str = "") -> PostStartSchedule:
        """Schedule typed OPTIONAL post-start steps detached, with NO shell: a self-
        contained Python launcher (delay/exec/tcp_wait/tcp_send) is generated and spawned
        via `python3 <launcher>`. NON-GATING, but every scheduling-stage failure (render,
        launcher write, log/dir setup, spawn, runtime containment) is RETURNED typed —
        never silently swallowed."""
        if not comp.post_steps:
            return PostStartSchedule(True, "")
        op = self.config.operator
        runtime, src = str(self.paths.runtime_root), str(self.source_dir(comp))
        try:
            script = commands.render_post_launcher(comp.post_steps, comp, params, op,
                                                   runtime, src, band)
        except (commands.CommandError, validators.ValidationError) as exc:
            return PostStartSchedule(False, f"launcher render failed: {exc}")
        # Unique runtime-owned launcher so concurrent post-start actions never
        # overwrite each other (written via the safe no-follow path API).
        uid = self._launch_uid(comp.id)
        launcher = self.paths.under("state", "post", f"{uid}.py")
        try:
            runtime_fs.write_launcher(self.paths, launcher, script)
        except (OSError, PathContainmentError) as exc:
            return PostStartSchedule(False, f"launcher write failed: {exc}")
        try:
            log = self.paths.under("logs", f"post-{uid}.log")   # may raise on symlinked logs/
            runtime_fs.ensure_dir(self.paths, self.logs_dir())
            self._spawn(["python3", str(launcher)], log)
        except (OSError, PathContainmentError) as exc:
            return PostStartSchedule(False, f"launcher spawn/log setup failed: {exc}")
        return PostStartSchedule(True, "scheduled")

    @staticmethod
    def _launch_uid(comp_id: str) -> str:
        """A unique, filename-safe launch id for a generated launcher/log."""
        return f"{comp_id}-{os.getpid()}-{time.monotonic_ns()}"

    def has_required_post_start(self, comp: Component) -> bool:
        return any(s.get("required") for s in (comp.post_steps or ()))

    def run_required_post_start(self, stack: Stack, comp: Component,
                                params: dict | None = None, band: str = "",
                                timeout: float = 120.0) -> JobResult:
        """Run a component's REQUIRED post-start steps SYNCHRONOUSLY and bounded (no
        shell): the generated Python launcher exits non-zero if any required step
        fails, so its return code is a typed pass/fail the caller gates VERIFIED on."""
        op = self.config.operator
        runtime, src = str(self.paths.runtime_root), str(self.source_dir(comp))
        try:
            script = commands.render_post_launcher(comp.post_steps, comp, params, op,
                                                   runtime, src, band)
        except (commands.CommandError, validators.ValidationError) as exc:
            return JobResult(name=f"post-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=[f"post-start build error: {exc}"])
        uid = self._launch_uid(comp.id)
        launcher = self.paths.under("state", "post", f"{uid}.py")
        # Every failure source (safe launcher write, log creation, runner spawn,
        # execution) becomes a TYPED required-post-start failure — never an exception
        # raised past the lifecycle boundary.
        try:
            runtime_fs.write_launcher(self.paths, launcher, script)
        except (OSError, PathContainmentError) as exc:
            return JobResult(name=f"post-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=[f"post-start launcher write failed: {exc}"])
        try:
            return run_job(self.system.runner, name=f"post-{uid}", paths=self.paths,
                           argv=["python3", str(launcher)], cwd=src,
                           logs_dir=self.logs_dir(), timeout=timeout)
        except (OSError, PathContainmentError) as exc:
            return JobResult(name=f"post-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=[f"post-start runner failed to start: {exc}"])

    # -- verified owned-launch records (B) --------------------------------
    #
    # Each launch LHPC starts is recorded by a UNIQUE launch id (not one mutable
    # marker per component — a daemon can own independent 433/868/both instances).
    # A stop only ever signals a process group whose FULL identity (pid, start
    # time, pgid, sid, executable, argv fingerprint) still matches the record AND
    # that is an LHPC-owned session leader and not the controller's own group.
    # Process scanning detects manual processes but NEVER authorizes a kill.

    STOP_WAIT_S = 5.0          # bounded wait for verified cessation after SIGTERM
    STOP_POLL_S = 0.2

    def _owned_dir(self) -> Path:
        return self.paths.under("state", "owned")

    @staticmethod
    def _controller_pgid() -> int:
        return os.getpgid(0)

    # Process identity lives in the shared `procident` helper so ownership records and
    # job markers use ONE tested implementation (PID-reuse resistance is consistent).
    _proc_alive = staticmethod(procident.proc_alive)
    _proc_identity = staticmethod(procident.proc_identity)

    _identity_complete = staticmethod(procident.identity_complete)   # THE shared predicate

    def _capture_identity(self, pid: int) -> dict | None:
        """Capture one COMPLETE process identity, briefly retrying so a transient empty
        /proc/<pid>/exe or cmdline read under load doesn't record a weak identity."""
        ident = None
        for _ in range(10):
            ident = self._proc_identity(pid)
            if ident is None or (ident.get("exec") and ident.get("argv_len")):
                break
            time.sleep(0.02)
        return ident

    def record_launch(self, stack: Stack, comp: Component, pid: int | None,
                      band: str = "", ident: dict | None = None) -> bool:
        """Atomically persist an LHPC-owned launch with its full process identity, via
        the safe runtime FS (no symlink-leaf, fsync'd). Returns True ONLY when the
        record was durably written. A start must NOT be reported owned/verified unless
        this returns True (a process we cannot record is one we cannot later safely stop).

        `ident`, when given, is the COMPLETE identity captured at command-observation
        time — it is used as-is rather than re-reading a possibly-weaker one (no silent
        substitution before persistence). When omitted (direct callers) it is captured."""
        if not pid:
            return False
        if ident is None:
            ident = self._capture_identity(pid)
        # Ownership requires a COMPLETE identity (`ident is None`, a missing field, or an
        # empty observed argv is never a valid record).
        if not self._identity_complete(ident):
            return False
        # Persist the record. The WRITE is the mandatory guarantee (P0.2). A record
        # whose identity later fails to verify is treated as stale by `stop`/
        # `verify_owned`, never falsely owned.
        rec = {"launch_id": f"{comp.id}__{band or 'x'}__{pid}", "stack": stack.id,
               "component": comp.id, "band": band or "", "pid": pid,
               "launched_at": int(time.time()), **(ident or {})}
        path = self._owned_dir() / f"{rec['launch_id']}.json"
        try:
            runtime_fs.atomic_write(self.paths, path, json.dumps(rec), 0o600)
            return True
        except (OSError, PathContainmentError):
            return False                 # could not persist ownership -> not owned

    def owned_records(self, comp_id: str, band: str | None = None) -> list[dict]:
        """Records for a component, optionally scoped to a band (a `both` instance
        matches any band request)."""
        out = []
        d = self._owned_dir()
        # Descriptor-safe enumeration (no `is_dir()`/`glob`): a symlinked/escaping owned dir
        # fails closed (no trusted records); a symlinked record LEAF is skipped, never
        # followed. The per-record read is already no-follow.
        try:
            entries = runtime_fs.scandir_nofollow(self.paths, d)
        except PathContainmentError:
            return out
        for name, is_link in sorted(entries):
            if is_link or not name.endswith(".json"):
                continue
            f = d / name
            try:
                rec = json.loads(runtime_fs.read_text(self.paths, f))   # no-follow read
            except (OSError, ValueError):
                continue
            if rec.get("component") != comp_id:
                continue
            if band is not None and rec.get("band") not in (band, "both", ""):
                continue
            rec["_path"] = str(f)
            out.append(rec)
        return out

    def _remove_record(self, rec: dict) -> bool:
        """Delete an ownership record via the safe runtime FS. Returns False if the
        record could NOT be removed (so the typed stop result reflects that the on-disk
        ownership evidence persists, rather than silently swallowing the failure)."""
        try:
            p = Path(rec["_path"])
        except KeyError:
            return False
        try:
            runtime_fs.unlink(self.paths, p)
            return True
        except (OSError, PathContainmentError):
            return False

    def _original_ceased(self, rec: dict) -> bool:
        """True ONLY when the originally-launched process is PROVABLY gone: its /proc is
        gone or a zombie, OR the pid is alive but its start-time no longer matches (the
        number was recycled — confirmed PID reuse). A transient /proc read failure is
        NEVER treated as proof of cessation."""
        pid = rec.get("pid")
        if not pid:
            return True
        if self._proc_ceased(pid):
            return True
        live = self._proc_identity(pid)
        if live is not None and str(live.get("starttime")) != str(rec.get("starttime")):
            return True                     # confirmed PID reuse -> original ceased
        return False

    def verify_owned(self, rec: dict) -> tuple[bool, str]:
        """All-or-nothing identity check before any signal is sent."""
        pid = rec.get("pid")
        if not pid or not self._proc_alive(pid):
            return False, "process gone"
        live = self._proc_identity(pid)
        if live is None:
            return False, "no /proc identity"
        for k in ("starttime", "pgid", "sid", "exec", "argv_fp"):
            if str(rec.get(k)) != str(live.get(k)):
                return False, f"{k} mismatch (stale/reused pid)"
        if live["pgid"] == self._controller_pgid():
            return False, "would be the controller's own group"
        if live["sid"] != pid:
            return False, "not an LHPC-owned session leader"
        return True, "verified"

    def _wait_ceased(self, rec: dict) -> bool:
        """Bounded wait for PROVEN cessation of the original process. A transient /proc
        error during the wait does not count as cessation (it keeps waiting, and the
        final answer is still proof-based — UNVERIFIED if never proven)."""
        waited = 0.0
        while waited < self.STOP_WAIT_S:
            if self._original_ceased(rec):
                return True
            time.sleep(self.STOP_POLL_S)
            waited += self.STOP_POLL_S
        return self._original_ceased(rec)

    def _ready_endpoints_gone(self, comp: Component) -> tuple[bool, list[str]]:
        """Probe a component's `ready=true` endpoints after a stop. Returns
        (all_gone, lingering-evidence) — a readiness endpoint that is still present
        after the owned process ceased means the stop is NOT verified."""
        from .probes.endpoints import tcp_endpoint_present
        from .probes.unixsock import probe_socket
        lingering = []
        for e in comp.endpoints:
            if not getattr(e, "ready", False):
                continue
            if e.kind == "tcp":
                # Host/family-aware: only a listener of the DECLARED family on the port
                # counts as the endpoint still lingering.
                present, _ = tcp_endpoint_present(self.system, e.address)
            elif getattr(e, "external", False):
                continue                       # external never gates cessation
            elif not self.paths.contains(Path(e.address)):
                continue                       # outside-root ready endpoint can't gate
            elif e.kind == "unix":
                present = probe_socket(self.system, e.address).is_socket
            else:
                present = self.system.fs.exists(e.address)
            if present:
                lingering.append(e.address)
        return (not lingering), lingering

    def stop(self, comp: Component, band: str | None = None) -> CompResult:
        """Stop the LHPC-owned launch(es) for this component (optionally one band),
        returning a TYPED outcome.

        Record-driven: only a process whose full identity still matches an ownership
        record is signalled (SIGTERM only). A matching but UNOWNED process is never
        signalled (MANUAL_REQUIRED). A verified stop requires both process cessation
        AND disappearance of every `ready=true` endpoint — a lingering readiness
        endpoint yields ENDPOINT_STILL_PRESENT (record retained, not a green stop)."""
        def result(outcome, summary, killed=(), details=()):
            return CompResult(component=comp.id, action="stop", outcome=outcome,
                              summary=summary, details=tuple(details),
                              pid=(killed[0] if killed else None))
        recs = self.owned_records(comp.id, band)
        if not recs:
            # No ownership record. A matching foreign process is MANUAL_REQUIRED; a
            # lingering readiness endpoint means the stop is NOT verified (never claim
            # ALREADY_STOPPED while a ready=true endpoint is still present).
            if comp.process is not None:
                pm = probe_process(self.system, comp.process)
                if pm.pids:
                    return result(Outcome.MANUAL_REQUIRED,
                                  "a matching process is running but not owned by LHPC — "
                                  f"stop it yourself: kill {' '.join(map(str, pm.pids))}")
            gone, lingering = self._ready_endpoints_gone(comp)
            if not gone:
                return result(Outcome.ENDPOINT_STILL_PRESENT,
                              "no owned process, but readiness endpoint(s) still present: "
                              f"{', '.join(lingering)}")
            return result(Outcome.ALREADY_STOPPED, "no owned process")
        killed, notes, persisted, unverified = [], [], False, False
        ceased: list[dict] = []          # records whose process ceased (removed only
        stale: list[dict] = []           # after endpoint cessation is also confirmed)
        for rec in recs:
            ok, why = self.verify_owned(rec)
            if not ok:
                if self._original_ceased(rec):
                    # The originally-launched process is PROVABLY gone (dead/zombie/reused
                    # pid): this is a STALE record, not a live ownership we failed to verify.
                    # Drop it — do NOT flag unverified, so a stop of an already-dead process
                    # converges to STOPPED instead of being blocked forever by its own leftovers.
                    notes.append(f"pid {rec['pid']}: stale record ({why}) — original ceased, dropping")
                    stale.append(rec)
                else:
                    # Cannot PROVE cessation (transient /proc error, or a live process whose
                    # identity we can't verify) -> genuinely unverified; retain as evidence.
                    notes.append(f"pid {rec['pid']}: unverified ({why}) — not signalled")
                    unverified = True
                continue
            try:
                os.killpg(rec["pgid"], signal.SIGTERM)
            except OSError as exc:
                notes.append(f"pid {rec['pid']}: signal failed: {exc}")
                unverified = True
                continue
            if self._wait_ceased(rec):
                killed.append(rec["pid"])
                notes.append(f"pid {rec['pid']}: stopped")
                ceased.append(rec)
            else:
                notes.append(f"pid {rec['pid']}: SIGTERM sent but cessation NOT verified")
                persisted = True
        # On ANY unverified/persisting record, RETAIN all ownership evidence (so the
        # operator can still diagnose) — do not partially discard records.
        if persisted:
            return result(Outcome.STILL_RUNNING,
                          "process did not cease after SIGTERM (no SIGKILL) — "
                          "ownership retained", killed, notes)
        if unverified:
            return result(Outcome.UNVERIFIED,
                          "an owned record could not be verified — ownership retained",
                          killed, notes)
        # All owned processes ceased. A verified stop ALSO requires every ready=true
        # endpoint to be gone — only THEN are records/markers cleared.
        gone, lingering = self._ready_endpoints_gone(comp)
        if not gone:
            return result(Outcome.ENDPOINT_STILL_PRESENT,
                          f"process ceased but readiness endpoint(s) still present: "
                          f"{', '.join(lingering)} — ownership retained", killed, notes)
        remove_failed = [rec for rec in ceased + stale if not self._remove_record(rec)]
        if remove_failed:
            # The processes ceased, but ownership evidence could NOT be cleared — report
            # UNVERIFIED rather than a clean STOPPED, so the discrepancy is visible.
            notes.append(f"{len(remove_failed)} ownership record(s) could not be removed")
            return result(Outcome.UNVERIFIED,
                          "processes ceased but ownership record removal failed — "
                          "records retained", killed, notes)
        return result(Outcome.STOPPED, "; ".join(notes) if notes else "no owned process", killed)

    def logs(self, comp: Component, lines: int = 200) -> tuple[str, list[str]]:
        for path in comp.log_paths:
            p = Path(path)
            if p.exists():
                return str(p), tail_log(p, lines)
        job_log = self.logs_dir() / f"start-{comp.id}.log"
        if job_log.exists():
            # runtime-owned log -> no-follow tail through the authoritative API.
            return str(job_log), runtime_fs.tail(self.paths, job_log, lines)
        return "", []

    # -- host test ---------------------------------------------------------

    def spawn_job(self, name: str, argv: list[str], cwd: str | None,
                  env: dict | None = None):
        """Run a structured argv (shell=False) detached, streaming combined output to
        logs/<name>.log. The log is created/truncated WITHOUT following a symlink leaf
        (P0.3): if it cannot be created safely the job does not start (returns
        (None, None)). Returns (log_name, pid) or (None, None)."""
        try:
            safe_name = validators.path_component(name, field="job log")
            log = self.paths.under("logs", f"{safe_name}.log")     # contained leaf
        except (validators.ValidationError, PathContainmentError):
            return None, None
        try:
            # Descriptor-anchored create/truncate (O_NOFOLLOW, parent walked by dir_fd):
            # fresh log per run, never through a symlinked leaf OR a swapped parent.
            runtime_fs.open_log_truncate(self.paths, log).close()
        except (OSError, PathContainmentError):
            return None, None           # cannot safely create the log -> don't spawn
        full_env = {**os.environ, **env} if env else None
        cwd = cwd if (cwd and os.path.isdir(cwd)) else None   # absent source -> run from cwd, log the failure
        try:
            pid = self._spawn(argv, log, cwd=cwd, env=full_env)
        except OSError:
            return None, None
        return log.name, pid

    def host_test(self, comp: Component, timeout: float = 300.0) -> JobResult | None:
        if not comp.test_argv:
            return None
        if self.is_linked_source(comp):       # never run tests INTO an external linked tree
            return JobResult(name=f"test-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=["BLOCKED: source is a linked external "
                             "tree — test it yourself in that checkout"])
        src = str(self.source_dir(comp))
        try:
            argv = commands.build_step_argv({"argv": list(comp.test_argv)},
                                            self.system.runner, str(self.paths.runtime_root), src)
        except commands.CommandError as exc:
            return JobResult(name=f"test-{comp.id}", state=JobState.FAILED,
                             returncode=1, log_path="", tail=[str(exc)])
        return run_job(self.system.runner, name=f"test-{comp.id}", argv=argv, paths=self.paths,
                       cwd=src, logs_dir=self.logs_dir(), timeout=timeout)

    # -- daemon readiness + bounded TX test --------------------------------

    # The daemon's per-band sockets (daemon_protocol.h): raw data + CONF/status.
    def raw_socket(self, band: str) -> str:
        return f"/tmp/lora{band}.sock"

    def conf_socket(self, band: str) -> str:
        return f"/tmp/loraconf{band}.sock"

    def _stats_txok(self, band: str) -> int | None:
        """Read the daemon's TXOK counter through the ONE bounded CONF parser
        (`daemon_control._query`): an oversized/malformed/truncated STATS response
        fails closed to None rather than being parsed raw (P0.4)."""
        from . import daemon_control
        try:
            stats = daemon_control._query(self.system, band, b"GET STATS\n", "STATS")
        except OSError:
            return None
        val = stats.get("TXOK")
        if val is None:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def plan_tx_test(self, stack_id: str, band: str, payload: str) -> TxTestPlan:
        return TxTestPlan(
            stack=stack_id, component=f"loraham-daemon ({band})", band=band,
            parameters="daemon-managed CAD/LBT; single frame",
            payload=payload,
        )

    def run_daemon_tx_test(self, band: str, payload: str) -> TxTestResult:
        """Send exactly one frame on `band` via the daemon's raw socket and verify
        TXOK++ via its CONF STATS. Real RF. The caller MUST confirm and ensure a
        dummy load is attached."""
        before = self._stats_txok(band)
        try:
            # Raw data socket: bytes written are transmitted as one LoRa frame.
            # Fire-and-forget — the data socket transmits but sends no reply.
            self.system.unix.send(self.raw_socket(band), payload.encode(), 2.0)
        except OSError as exc:
            return TxTestResult(False, band, before, None, f"send failed: {exc}")
        # CAD/LBT can hold a frame for several seconds before it goes out, so poll
        # the TXOK counter rather than sampling once after a fixed wait.
        after = before
        for _ in range(self.TX_TEST_POLLS):
            time.sleep(self.TX_TEST_POLL_S)
            after = self._stats_txok(band)
            if before is not None and after is not None and after > before:
                break
        ok = before is not None and after is not None and after > before
        return TxTestResult(
            ok=ok, band=band, txok_before=before, txok_after=after,
            detail=("TXOK incremented — one frame transmitted"
                    if ok else "TXOK did not increment (see daemon log)"),
            evidence={"txok_before": str(before), "txok_after": str(after),
                      "payload": payload},
        )

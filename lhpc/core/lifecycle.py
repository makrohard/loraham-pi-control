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

# The ONE exact content a completion marker may hold; `is_built()` accepts nothing else. A build stamps
# it only after EVERY step succeeds, and a rebuild invalidates it (fail-closed) before the first step.
BUILD_MARKER_TEXT = "lhpc build complete\n"
_BUILD_MARKER_MAX = 64                         # bounded marker read — anything larger is malformed

# Surfaced when a groups grant is CONFIGURED (usermod done) but not yet EFFECTIVE in this process — the
# fix is a restart, not another usermod. Kept here so both dependency render sites use the one wording.
# Deliberately does NOT read as "missing": the grant IS made; only the running session must restart.
GROUP_RESTART_HINT = "granted — restart the session to apply"
# The copyable command that applies a restart-pending grant (re-derives the session's groups).
GROUP_RESTART_CMD = 'loginctl terminate-user "$USER"'
# Surfaced when the operator is NOT YET a member: grant it, then re-login. State-specific so it never
# co-appears with GROUP_RESTART_HINT (which would read as both "not granted" and "granted").
GROUP_MISSING_HINT = "not a member — grant it, then log out/in or reboot to apply"
# Surfaced for a GUI-ONLY dependency the headless-safe bootstrap deliberately did not install. ONE
# wording, reused by the start gate, the dependency views, explain, auto-install and direct build, so
# the operator reads the same sentence wherever the situation surfaces. It is NOT a defect report: a
# headless box is working as designed; it simply cannot run that component.
GUI_MISSING_HINT = ("GUI dependencies not installed — headless-safe default; on a machine with a "
                    "display, run sudo bash bootstrap-deps.sh --spi-mode <mode> --with-gui")


def module_present(name: str) -> bool:
    """Is a TOP-LEVEL python module importable? Uses `importlib.util.find_spec`, which LOCATES
    without importing — so this stays side-effect free and cheap enough for the read-only dependency
    and status paths that call it on every render. Never a subprocess.

    Manifest validation restricts `module` to a top-level name precisely because find_spec on a
    DOTTED name imports the parent package. Any exception (find_spec raises ModuleNotFoundError for a
    missing parent, ValueError for a malformed spec, and ImportError from a broken module's own
    import machinery) is treated as absent — fail-closed, never a traceback into a GET."""
    import importlib.util
    if not name:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def req_remediation(req, pending: bool) -> str:
    """One-line remediation for a (missing) Requirement — shared by the START gate and the dependency
    render sites. Owns the state framing so callers do NOT prefix "missing " (that would contradict a
    granted-but-pending grant). A groups grant that is merely restart-PENDING advises a RESTART (re-running
    `usermod` would NOT help); everything else reads "missing <label>" plus its grant/install command."""
    label = req.cmd or req.note or req.check_file or ""
    if req.groups and pending:
        return f"{label} — {GROUP_RESTART_HINT}: {GROUP_RESTART_CMD}"
    if req.absent_file:
        # INVERSE requirement: the problem is that a conflicting service is PRESENT, so never say
        # "missing" — state the conflict and the copyable command that clears it.
        return (req.note or "a conflicting service is enabled/active") \
            + (f" ({req.install})" if req.install else "")
    if getattr(req, "gui", False):
        # A GUI-only dep is opt-in by design, so it never reads as a plain "missing" defect.
        return f"{label} — {GUI_MISSING_HINT}"
    return f"missing {label}" + (f" ({req.install})" if req.install else "")


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
    """Result of SCHEDULING an OPTIONAL (detached) post-start. An ordinary transport failure
    (render / launcher write / log+dir setup / spawn / runtime containment) is NON-gating and
    reported via `detail`. `unverified=True` marks a lifecycle-INTEGRITY failure — a spawned runner
    we could neither own nor prove stopped — which DOES gate the main VERIFIED result."""

    ok: bool
    detail: str = ""
    unverified: bool = False
    # Runner log path when scheduling succeeded — lets the caller announce a tail-able
    # file for the (possibly minutes-long) detached retry window.
    log_path: str = ""


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

    def _spawn_post_runner(self, argv: list[str], log_path: Path) -> tuple[int, int]:
        """Spawn a detached post-start runner behind an ARM GATE, NO shell: it inherits a pipe on
        stdin and does NO post-step side effect until the controller writes the arm byte (only after
        its ownership record is durable). Returns (pid, arm_write_fd) — the caller MUST write b'1' to
        arm, or close the fd to leave it UNARMED (the runner then exits without any post step)."""
        r, w = os.pipe()
        try:
            log = runtime_fs.open_log_append(self.paths, log_path)
            try:
                proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=r,
                                        start_new_session=True)
            finally:
                log.close()
        except BaseException:
            os.close(w)
            raise
        finally:
            os.close(r)                    # parent keeps ONLY the arm (write) end
        return proc.pid, w

    def _arm(self, fd: int) -> bool:
        """Release the runner's arm gate with EXACTLY one byte. True ONLY on a complete 1-byte
        write; a short/zero write returns False and is never treated as armed."""
        return os.write(fd, b"1") == 1

    # -- locations ---------------------------------------------------------

    def _resolve_req_path(self, path: str) -> str:
        """Resolve a requirement's file path: substitute `{runtime}` (the runtime root) so a require can
        verify a MANAGED in-root artifact (`{runtime}/build/tool-cache/...`, `{runtime}/build/tools/...`),
        then expanduser for a per-user tool path (`~/.espressif`). Best-effort — an unset HOME leaves `~`
        literal, and the PATH `cmd` probe stays a reliable escape hatch/operator override."""
        return os.path.expanduser(path.replace("{runtime}", str(self.paths.runtime_root)))

    def missing_requirements(self, comp: Component) -> list:
        """Component dependencies not satisfied: a command not on PATH, or (for
        -dev packages) a `check_file` header that does not exist."""
        missing = []
        for req in comp.requires:
            if req.groups:
                # RUN-TIME capability: the CHILD we would spawn must be in ALL listed unix groups
                # (rootless device access). Gate on EFFECTIVE groups — what that child actually
                # inherits — so a grant that is configured but not yet effective (restart pending)
                # stays blocked (fail-closed), never spawning a child that dies on a raw permission
                # error. Read through the injectable seam so tests drive it with FakeSystem.
                if not set(req.groups) <= self.system.fs.effective_groups():
                    missing.append(req)
                continue
            if req.absent_file:
                # INVERSE run-time requirement: the named file (a systemd wants-symlink of a conflicting
                # root service) must NOT exist. Present => the service is enabled and will seize the shared
                # radio => unsatisfied, so START is blocked (fail-closed). expanduser for symmetry.
                if self.system.fs.exists(self._resolve_req_path(req.absent_file)):
                    missing.append(req)
                continue
            if req.module:
                # In-process import PROBE (find_spec, no subprocess) — used for a toolkit that ships
                # as a python module rather than a header/binary (e.g. tkinter from python3-tk).
                if not module_present(req.module):
                    missing.append(req)
                continue
            if req.check_file or req.cmd:
                # Satisfied if EITHER resolves: the binary is on PATH (`cmd`), OR the file exists
                # (`check_file`, expanduser'd for a per-user tool path like the Espressif qemu under
                # ~/.espressif). This mirrors a tool whose launcher resolves PATH > a fixed path (e.g.
                # meshcom run.sh: --qemu > PATH > ~/.espressif), so a copy on PATH counts even when the
                # fixed path is absent (and vice-versa). A require with only one of the two keys behaves
                # exactly as before. expanduser is best-effort: a wrong/unset HOME just falls back to the
                # PATH probe, so a PATH install is always a reliable escape hatch.
                on_path = bool(req.cmd) and shutil.which(req.cmd) is not None
                at_file = bool(req.check_file) and self.system.fs.exists(self._resolve_req_path(req.check_file))
                if not (on_path or at_file):
                    missing.append(req)
                continue
        return missing

    def group_grant_pending(self, req) -> bool:
        """A groups Requirement whose grant is CONFIGURED (present in the group database, e.g. after
        `usermod -aG`) but not yet EFFECTIVE in this process (restart/reboot pending). Such a req is
        still 'missing' for the START gate above (fail-closed), but the fix is a restart — NOT another
        usermod — so the render sites swap the grant command for GROUP_RESTART_HINT. False for a
        non-group req or a genuinely-ungranted one."""
        g = set(req.groups)
        return bool(g) and g <= self.system.fs.configured_groups() \
            and not g <= self.system.fs.effective_groups()

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

    BUILD_TIMEOUT_S = 900.0     # default per-STEP build timeout; hardware-realistic for a modest Pi.
                                # A known-slow component (e.g. a venv + many pip installs) overrides it
                                # with a manifest `build_timeout`.

    def _invalidate_build_marker(self, marker: Path) -> str | None:
        """Remove a stale completion marker FAIL-CLOSED before a rebuild. Returns None on success (a
        MISSING marker is the only ignored condition — it is already "not built"), else an error string.
        A present marker must be a SAFE REGULAR FILE we can remove; a symlink, directory, FIFO/device,
        oversize/malformed content, a permission failure, or a containment/escape error all fail closed
        (the caller turns this into a typed build failure BEFORE any step runs). Uses the descriptor-
        anchored O_NOFOLLOW runtime-fs primitives — no check-then-act, no symlink follow."""
        try:
            runtime_fs.read_text_regular(self.paths, marker, max_bytes=_BUILD_MARKER_MAX)
        except FileNotFoundError:
            return None                          # absent (or absent parent) -> nothing to invalidate
        except (OSError, PathContainmentError) as exc:
            return f"stale build marker is not a safe regular file ({exc})"
        try:
            runtime_fs.unlink(self.paths, marker)   # safe no-follow removal of the regular file
        except (OSError, PathContainmentError) as exc:
            return f"could not remove the stale build marker ({exc})"
        return None

    def build(self, comp: Component, timeout: float | None = None,
              log_base: str | None = None, redactor=None, should_cancel=None,
              on_log_open=None) -> JobResult:
        """Run a component's typed build steps (structured argv, shell=False). Each
        step may carry env and a `{pkgconfig:NAME}` token (resolved via pkg-config).

        `log_base` overrides the default `build-<comp.id>` job/log name — the auto-install driver
        passes a RUN-SPECIFIC base so a run's build log can never collide with a prior
        run's. Multi-step components append `-<i>` to whichever base is used.

        Timeout precedence: explicit `timeout` arg > the component's manifest `build_timeout` >
        `BUILD_TIMEOUT_S`. A component that declares a `build_marker` gets it REMOVED before the first
        step and WRITTEN only after the LAST step succeeds, so a build killed mid-way (e.g. a
        half-populated venv) can never read "built" — the marker is `is_built`'s gate."""
        base = log_base or f"build-{comp.id}"
        if self.is_linked_source(comp):       # never build INTO an external linked tree
            return JobResult(name=base, state=JobState.FAILED, returncode=1,
                             log_path="", tail=["BLOCKED: source is a linked external "
                             "tree — build it yourself in that checkout (lhpc never "
                             "writes into a linked source)"])
        eff_timeout = timeout if timeout is not None else (comp.build_timeout or self.BUILD_TIMEOUT_S)
        src, runtime = str(self.source_dir(comp)), str(self.paths.runtime_root)
        # A re-build must NEVER inherit a prior run's completion marker: invalidate it up front, FAIL
        # CLOSED. A surviving marker (permission error, unsafe symlink/dir/FIFO, containment error) would
        # falsely read "built"; refuse the build TYPED BEFORE executing any step or touching artifacts.
        marker = (self.source_dir(comp) / comp.build_marker) if comp.build_marker else None
        if marker is not None:
            err = self._invalidate_build_marker(marker)
            if err is not None:
                return JobResult(name=base, state=JobState.FAILED, returncode=1, log_path="",
                                 tail=[f"BLOCKED: {err} — refusing to build (a surviving marker would "
                                       "falsely read 'built')"])
        steps = comp.build_steps
        last: JobResult | None = None
        for i, step in enumerate(steps):
            try:
                argv = commands.build_step_argv(step, self.system.runner, runtime, src)
                env = commands.build_env(tuple((step.get("env") or {}).items()), runtime, src)
                # Optional quiet-step preamble ({runtime}/{source} substituted like argv/env);
                # a bad placeholder is the same typed FAILED as a bad argv token.
                ann = (commands._paths_subst(str(step["announce"]), runtime, src, "")
                       if step.get("announce") else None)
            except commands.CommandError as exc:
                return JobResult(name=base, state=JobState.FAILED,
                                 returncode=1, log_path="", tail=[str(exc)])
            name = base + (f"-{i}" if len(steps) > 1 else "")
            last = run_job(self.system.runner, name=name, argv=argv, cwd=src, paths=self.paths,
                           env=(env or None), logs_dir=self.logs_dir(), timeout=eff_timeout,
                           redactor=redactor, should_cancel=should_cancel, on_log_open=on_log_open,
                           announce=ann)
            if not last.ok:
                return last
        if last is None:        # nothing to build
            return JobResult(name=base, state=JobState.SUCCEEDED,
                             returncode=0, log_path="", tail=["(no build steps)"])
        # ALL steps succeeded -> stamp the completion marker (fail the build if it can't be written,
        # so is_built never reads "built" off an unstamped tree).
        if marker is not None:
            try:
                runtime_fs.atomic_write(self.paths, marker, BUILD_MARKER_TEXT, 0o644)
            except (OSError, PathContainmentError) as exc:
                return JobResult(name=base, state=JobState.FAILED, returncode=1,
                                 log_path=last.log_path,
                                 tail=(list(last.tail) if last.tail else [])
                                 + [f"build succeeded but the completion marker could not be "
                                    f"written ({exc}) — treating as NOT built"])
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
            # BAND-SCOPED log: the daemon runs one instance PER BAND simultaneously, and they must
            # not append to one shared file (it mixes bands and doubles the volume the RX/TX feed
            # has to scan). `band` is "" for band-agnostic components -> unchanged legacy name.
            log = self.logs_dir() / f"start-{comp.id}{('-' + band) if band else ''}.log"
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
        # PREFLIGHT: cancel/clean any stale post-start runner bound to a PRIOR launch of this
        # component+band BEFORE launching a new one (clean verified-ceased records, cancel live
        # stale runners). A runner that can't be verified stopped BLOCKS the new launch with typed
        # evidence — a stale runner must never survive into the new launch.
        pre_notes, pre_unverified = self._cancel_post_runners(comp, band)
        if pre_unverified:
            return StartLaunch(False, str(log),
                               "a prior post-start runner could not be verified stopped — resolve "
                               "it before starting (" + "; ".join(pre_notes) + ")")
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
        # STABLE identity: START TIME (reuse-proof) + session/group leadership. A legitimate later
        # exec (e.g. `#!/usr/bin/env bash`: env → bash) changes exec/argv but NOT start time, so
        # those are advisory — a matching start time already proves it is our launched process.
        # A changed start time / pgid / sid means it is NOT ours: do NOT signal, NOT proven ceased.
        for k in ("starttime", "pgid", "sid"):
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

    def _verified_main_record(self, comp_id: str, band: str):
        """The currently VERIFIED main (role="") ownership record for this component+band, or None.
        A detached post-runner is bound to this exact launch so it can never act for a later one."""
        for rec in self.owned_records(comp_id, band, role=""):
            ok, _why = self.verify_owned(rec)
            if ok:
                return rec
        return None

    def _binding_for(self, comp_id: str, band: str) -> dict | None:
        """Strictly-validated binding to the currently VERIFIED main launch, or None: a non-empty
        launch id and POSITIVE, non-boolean integer pid/starttime/pgid/sid."""
        main = self._verified_main_record(comp_id, band)
        if main is None:
            return None
        lid = main.get("launch_id")
        if not isinstance(lid, str) or not lid:
            return None
        out = {"main_launch_id": lid}
        for src, dst in (("pid", "main_pid"), ("starttime", "main_starttime"),
                         ("pgid", "main_pgid"), ("sid", "main_sid")):
            v = main.get(src)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                return None
            out[dst] = v
        return out

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
        # A detached runner MUST be bound to a currently verified main launch — never BINDING=None.
        # No verified main record -> spawn nothing (no launcher) and return a typed integrity
        # failure that gates the main VERIFIED result.
        binding = self._binding_for(comp.id, band)
        if binding is None:
            return PostStartSchedule(False, "no verified main launch to bind the post-start runner "
                                     "to — refusing to spawn an unbound runner", unverified=True)
        # Unique runtime-owned launcher so concurrent post-start actions never
        # overwrite each other (written via the safe no-follow path API). The uid-scoped
        # RESULT sidecar is where the runner reports its terminal outcome (acked/exhausted/…)
        # for the status view; its lifetime is tied to the ownership record.
        uid = self._launch_uid(comp.id)
        launcher = self.paths.under("state", "post", f"{uid}.py")
        result = self.paths.under("state", "post", f"{uid}.result.json")
        try:
            script = commands.render_post_launcher(comp.post_steps, comp, params, op,
                                                   runtime, src, band, binding=binding, gated=True,
                                                   result_path=str(result))
        except (commands.CommandError, validators.ValidationError) as exc:
            return PostStartSchedule(False, f"launcher render failed: {exc}")
        try:
            runtime_fs.write_launcher(self.paths, launcher, script)
        except (OSError, PathContainmentError) as exc:
            return PostStartSchedule(False, f"launcher write failed: {exc}")
        # ARM GATE: spawn the runner BLOCKED on a controller-owned pipe. It performs NO post-step
        # side effect until we capture its identity, durably write its ownership record, and ARM it.
        try:
            log = self.paths.under("logs", f"post-{uid}.log")   # may raise on symlinked logs/
            runtime_fs.ensure_dir(self.paths, self.logs_dir())
            pid, arm = self._spawn_post_runner(["python3", str(launcher)], log)
        except (OSError, PathContainmentError) as exc:
            return PostStartSchedule(False, f"launcher spawn/log setup failed: {exc}")
        ident = self._capture_identity(pid)
        if self.record_launch(stack, comp, pid, band, ident=ident, role="post", binding=binding,
                              extra={"post_uid": uid, "result_path": str(result),
                                     "launcher_path": str(launcher), "log_path": str(log)}):
            # Record durable -> ARM the runner with exactly one byte (it may now act for THIS launch
            # only). An arming FAILURE (exception OR a short/zero write) must NEVER be reported as
            # "scheduled": close the gate and unwind via the identity-safe SIGTERM cessation
            # machinery, dropping the just-created record only once cessation is proven.
            try:
                armed = self._arm(arm)
            except OSError:
                armed = False
            finally:
                try:
                    os.close(arm)
                except OSError:
                    pass
            if armed:
                return PostStartSchedule(True, "scheduled", log_path=str(log))
            notes, unverified = self._cancel_post_runners(comp, band)
            if unverified:
                return PostStartSchedule(False, "post-start runner could not be armed AND could not "
                                         "be verified stopped/removed — ownership retained ("
                                         + "; ".join(notes) + ")", unverified=True)
            return PostStartSchedule(False, "post-start runner could not be armed — terminated, no "
                                     "side effect (" + "; ".join(notes) + ")")
        # Record NOT durable -> do NOT arm: closing the gate makes the runner exit having done
        # NOTHING (no exec/--setcall). Prove cessation via the ACTUAL terminate result:
        #  * cessation PROVED     -> typed SCHEDULING failure, no runner left;
        #  * cessation NOT proved -> typed UNVERIFIED lifecycle-integrity failure (gates VERIFIED).
        os.close(arm)
        if self._terminate_unobserved(pid, ident):
            return PostStartSchedule(False, "post-start runner could not be owned — terminated "
                                     "(never armed, no side effect)")
        return PostStartSchedule(False, "post-start runner could not be owned AND could not be "
                                 "proven stopped", unverified=True)

    @staticmethod
    def _launch_uid(comp_id: str) -> str:
        """A unique, filename-safe launch id for a generated launcher/log."""
        return f"{comp_id}-{os.getpid()}-{time.monotonic_ns()}"

    def has_required_post_start(self, comp: Component) -> bool:
        return any(s.get("required") for s in (comp.post_steps or ()))

    @staticmethod
    def required_result_leaf(binding: dict) -> str:
        """The sidecar leaf for a SYNCHRONOUS (required) post-start run, derived from the main
        launch it belongs to. Unique per launch — a new launch writes a NEW leaf, so nothing
        pre-existing is ever removed and a previous launch's file is simply never looked up.
        Hashed so an arbitrary launch id can never shape a filename."""
        import hashlib
        lid = str((binding or {}).get("main_launch_id", ""))
        return f"required-{hashlib.sha256(lid.encode()).hexdigest()[:16]}.result.json"

    def run_required_post_start(self, stack: Stack, comp: Component,
                                params: dict | None = None, band: str = "",
                                timeout: float = 300.0, on_log_open=None) -> JobResult:
        """Run a component's REQUIRED post-start steps SYNCHRONOUSLY and bounded (no
        shell): the generated Python launcher exits non-zero if any required step
        fails, so its return code is a typed pass/fail the caller gates VERIFIED on.

        BOUND to the verified main launch exactly like the detached path: without a binding the
        runner's `_main_ok()` is a no-op and it would happily push settings at a main that died
        or was replaced mid-run. It also writes the same typed result sidecar, so an OPTIONAL
        step that failed inside an otherwise-passing required run stays visible in `lhpc status`.

        The default timeout must comfortably exceed the whole declared set (a 12 s delay plus a
        120 s per-exec cap twice over) — a shorter budget would kill the job mid-set and report a
        failure the steps did not cause."""
        op = self.config.operator
        runtime, src = str(self.paths.runtime_root), str(self.source_dir(comp))
        binding = self._binding_for(comp.id, band)
        if binding is None:
            return JobResult(name=f"post-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=["no verified main launch to bind the required "
                                                "post-start runner to — refusing to run unbound"])
        result = self.paths.under("state", "post", self.required_result_leaf(binding))
        try:
            script = commands.render_post_launcher(
                comp.post_steps, comp, params, op, runtime, src, band, binding=binding,
                result_path=str(result),
                meta={"comp": comp.id, "band": band or "", "role": "required"})
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
                           logs_dir=self.logs_dir(), timeout=timeout,
                           on_log_open=on_log_open)
        except (OSError, PathContainmentError) as exc:
            return JobResult(name=f"post-{comp.id}", state=JobState.FAILED, returncode=1,
                             log_path="", tail=[f"post-start runner failed to start: {exc}"])

    # -- verified owned-launch records (B) --------------------------------
    #
    # Each launch LHPC starts is recorded by a UNIQUE launch id (not one mutable
    # marker per component — a daemon can own independent 433/868 instances).
    # A stop only ever signals a process group whose FULL identity (pid, start
    # time, pgid, sid, executable, argv fingerprint) still matches the record AND
    # that is an LHPC-owned session leader and not the controller's own group.
    # Process scanning detects manual processes but NEVER authorizes a kill.

    STOP_WAIT_S = 5.0          # bounded wait for verified cessation after SIGTERM
    STOP_POLL_S = 0.2
    STOP_ENDPOINT_GRACE_S = 3.0  # bounded extra wait for a ready endpoint to close after the
                                 # owned process ceased (a child listener may lag the wrapper)

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
                      band: str = "", ident: dict | None = None, role: str = "",
                      binding: dict | None = None, extra: dict | None = None) -> bool:
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
        # A post-start RUNNER is recorded under the SAME component (so a component stop cancels it)
        # but tagged role="post" and given a role-scoped launch_id, so it never collides with the
        # main record and is filtered out of status (the main component never looks duplicated).
        tag = f"{role}-" if role else ""
        rec = {"launch_id": f"{comp.id}__{band or 'x'}__{tag}{pid}", "stack": stack.id,
               "component": comp.id, "band": band or "", "pid": pid, "role": role,
               "launched_at": int(time.time()), **(extra or {}),
               **(binding or {}), **(ident or {})}
        path = self._owned_dir() / f"{rec['launch_id']}.json"
        try:
            runtime_fs.atomic_write(self.paths, path, json.dumps(rec), 0o600)
            return True
        except (OSError, PathContainmentError):
            return False                 # could not persist ownership -> not owned

    def owned_records(self, comp_id: str, band: str | None = None,
                      role: str | None = "") -> list[dict]:
        """Records for a component, optionally scoped to a band (a band-less `""` record matches any
        band request). `role` selects the record class: "" = MAIN launches (default — so status and
        the ordinary stop never see auxiliary runners), "post" = detached post-start runners,
        None = every role."""
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
            if role is not None and rec.get("role", "") != role:
                continue
            if band is not None and rec.get("band") not in (band, ""):
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
        except (OSError, PathContainmentError):
            return False
        # A post-runner record owns uid-scoped sidecars (launcher + terminal-result file);
        # their lifetime is exactly the record's, so drop them with it. Best-effort: once
        # the record is gone the sidecars are unreachable — a leftover is disk noise, not
        # stale ownership evidence, and must never turn a proven removal into a failure.
        for key in ("result_path", "launcher_path"):
            sp = rec.get(key)
            if sp:
                try:
                    runtime_fs.unlink(self.paths, Path(sp))
                except (OSError, PathContainmentError):
                    pass
        return True

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
        # START TIME is the reuse-proof identity (a recycled pid gets a NEW start time), and an
        # LHPC-launched session leader has pgid == sid == pid. exec/argv are NOT part of the hard
        # identity: a process may legitimately exec into a different image AFTER launch (e.g. a
        # `#!/usr/bin/env bash` script goes env → bash), which a matching start time already proves
        # is the SAME process — requiring exec/argv here wrongly disowns it and blocks its stop.
        for k in ("starttime", "pgid", "sid"):
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

    def _await_ready_endpoints_gone(self, comp: Component) -> tuple[bool, list[str]]:
        """`_ready_endpoints_gone` with a BOUNDED grace poll. A ready endpoint is often owned
        by a CHILD that exits a beat after the tracked process: e.g. the meshcom-qemu wrapper
        `run.sh` backgrounds `qemu-system-xtensa` in the same process group, so a `killpg`
        SIGTERM reaches both, but the shell wrapper dies immediately while QEMU takes a moment
        to shut down and close its `127.0.0.1:12323` listener. A single immediate check would
        race and spuriously report ENDPOINT_STILL_PRESENT. Polling up to `STOP_WAIT_S` lets a
        normal slow port release converge to STOPPED; a genuinely orphaned listener (e.g. a
        detached child outside the killed group) still survives the wait and is reported."""
        gone, lingering = self._ready_endpoints_gone(comp)
        waited = 0.0
        while not gone and waited < self.STOP_ENDPOINT_GRACE_S:
            time.sleep(self.STOP_POLL_S)
            waited += self.STOP_POLL_S
            gone, lingering = self._ready_endpoints_gone(comp)
        return gone, lingering

    def _cancel_post_runners(self, comp: Component, band: str | None) -> tuple[list, bool]:
        """Stop every detached post-start runner owned for this component (identity-verified SIGTERM,
        no SIGKILL, bounded verified cessation). Returns (notes, unverified) — `unverified` True when
        a still-live runner could NOT be proven stopped (so the caller reports a non-success stop and
        never lets an old runner survive to mutate a subsequent launch)."""
        notes, unverified = [], False
        for rec in self.owned_records(comp.id, band, role="post"):
            ok, why = self.verify_owned(rec)
            if not ok:
                if self._original_ceased(rec):
                    # Runner already ended: harmless, BUT failing to remove its stale record is a
                    # typed UNVERIFIED (same as a lingering main record).
                    if self._remove_record(rec):
                        notes.append(f"post-runner pid {rec['pid']}: already ceased — record dropped")
                    else:
                        notes.append(f"post-runner pid {rec['pid']}: ceased but stale record could "
                                     "NOT be removed")
                        unverified = True
                else:
                    notes.append(f"post-runner pid {rec['pid']}: unverified ({why}) — not signalled")
                    unverified = True
                continue
            try:
                os.killpg(rec["pgid"], signal.SIGTERM)
            except OSError as exc:
                notes.append(f"post-runner pid {rec['pid']}: signal failed: {exc}")
                unverified = True
                continue
            if self._wait_ceased(rec) and self._remove_record(rec):
                notes.append(f"post-runner pid {rec['pid']}: cancelled")
            else:
                notes.append(f"post-runner pid {rec['pid']}: SIGTERM sent but NOT verified stopped")
                unverified = True
        # The SYNCHRONOUS (required) run leaves a sidecar but no ownership record, so
        # `_remove_record` — which reaps the detached ones — never sees it. Drop the leaf belonging
        # to the launch being cancelled, computed from ITS binding (never a guessed/shared name).
        binding = self._binding_for(comp.id, band or "")
        if binding is not None:
            try:
                runtime_fs.unlink(self.paths,
                                  self.paths.under("state", "post",
                                                   self.required_result_leaf(binding)))
            except (OSError, PathContainmentError):
                pass                    # best-effort: an orphan is inert, never ownership evidence
        return notes, unverified

    def stop(self, comp: Component, band: str | None = None) -> CompResult:
        """Stop the LHPC-owned launch(es) for this component (optionally one band),
        returning a TYPED outcome.

        Record-driven: only a process whose full identity still matches an ownership
        record is signalled (SIGTERM only). A matching but UNOWNED process is never
        signalled (MANUAL_REQUIRED). A verified stop requires both process cessation
        AND disappearance of every `ready=true` endpoint — a lingering readiness
        endpoint yields ENDPOINT_STILL_PRESENT (record retained, not a green stop)."""
        # FIRST cancel any detached post-start RUNNER bound to this component — it must never
        # outlive the component (an old `--setcall` retry could hit a freshly restarted node).
        # Identity-verified SIGTERM only, no SIGKILL, bounded verified cessation.
        post_notes, post_unverified = self._cancel_post_runners(comp, band)
        # If a live runner could NOT be verified stopped/removed, DO NOT touch the main component:
        # never orphan the main behind a rogue runner. Typed non-success, all evidence retained, no
        # markers cleared (the caller leaves band/interactive markers intact on a non-success stop).
        if post_unverified:
            return CompResult(component=comp.id, action="stop", outcome=Outcome.UNVERIFIED,
                summary="post-start runner(s) could NOT be verified stopped — main NOT signalled; "
                        "ownership retained", details=tuple(post_notes))

        def result(outcome, summary, killed=(), details=()):
            return CompResult(component=comp.id, action="stop", outcome=outcome,
                              summary=summary, details=tuple(details) + tuple(post_notes),
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
            gone, lingering = self._await_ready_endpoints_gone(comp)
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
        # endpoint to be gone — only THEN are records/markers cleared. Poll with a bounded
        # grace so a child that outlives the tracked wrapper by a beat (e.g. run.sh's
        # backgrounded qemu closing its 12323 listener) converges to STOPPED instead of a
        # spurious ENDPOINT_STILL_PRESENT.
        gone, lingering = self._await_ready_endpoints_gone(comp)
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

    def start_log(self, comp: Component, band: str = "") -> Path | None:
        """The captured process log for `comp`, band-aware. A band-scoped start writes
        `start-<id>-<band>.log`; a band-agnostic one writes `start-<id>.log`. Resolution order:

          1. the EXACT band's log, when the caller knows the band (the RX/TX feed, `?band=`);
          2. else the NEWEST `start-<id>-*.log` — a band-less caller (`lhpc logs`, the GUI "logs"
             link) has no band to offer and must not come up empty for a banded component;
          3. else the legacy band-less name — still being written by a pre-upgrade process that
             has not been restarted since the rename.

        Returns None when nothing exists. Symlinked entries are skipped (`runtime_fs.tail` would
        refuse them anyway); the read stays no-follow.
        """
        d = self.logs_dir()
        if band:
            p = d / f"start-{comp.id}-{band}.log"
            if p.is_file():
                return p
        prefix = f"start-{comp.id}-"
        newest: Path | None = None
        newest_mtime = -1.0
        try:
            entries = runtime_fs.scandir_nofollow(self.paths, d)
        except (OSError, PathContainmentError):
            entries = []
        for name, is_symlink in entries:
            if is_symlink or not name.startswith(prefix) or not name.endswith(".log"):
                continue
            p = d / name
            try:
                mtime = p.stat().st_mtime
            except OSError:                       # vanished between scandir and stat
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = p, mtime
        if newest is not None:
            return newest
        legacy = d / f"start-{comp.id}.log"
        return legacy if legacy.is_file() else None

    def _newest_job_log(self, comp_id: str,
                        kinds=("start", "build", "test", "post", "adopt")) -> Path | None:
        """The NEWEST (by mtime) non-symlink `<kind>-<comp_id>[-*].log` across the given job kinds —
        so `lhpc logs <comp>` names the SAME file the newest job actually wrote (a running start log,
        a fresh build/host-test log, a detached post-start runner's `post-<comp>-<pid>-<ns>.log`, or
        a source-adoption `adopt-<comp>.log`), never a stale unsuffixed sibling from a prior run.
        Every one of these is a file a `[log] … tail -f` line may have announced — `lhpc logs` must
        resolve to that same newest file."""
        d = self.logs_dir()
        try:
            entries = runtime_fs.scandir_nofollow(self.paths, d)
        except (OSError, PathContainmentError):
            return None
        bases = [f"{k}-{comp_id}" for k in kinds]
        newest, newest_mtime = None, -1.0
        for name, is_symlink in entries:
            if is_symlink or not name.endswith(".log"):
                continue
            stem = name[:-4]                      # drop ".log"
            if not any(stem == b or stem.startswith(b + "-") for b in bases):
                continue
            p = d / name
            try:
                mtime = p.stat().st_mtime
            except OSError:                       # vanished between scandir and stat
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = p, mtime
        return newest

    def logs(self, comp: Component, lines: int = 200,
             band: str = "") -> tuple[str, list[str]]:
        for path in comp.log_paths:
            p = Path(path)
            if p.exists():
                return str(p), tail_log(p, lines)
        # A band-scoped caller (the RX/TX feed) wants the exact band's start log; a band-less
        # `lhpc logs <comp>` wants the NEWEST job log across start/build/test — so it resolves to the
        # very file a just-finished build wrote, matching the [log] line the job announced.
        job_log = self.start_log(comp, band) if band else self._newest_job_log(comp.id)
        if job_log is not None:
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

    TEST_TIMEOUT_S = 600.0      # default host-test timeout; hardware-realistic for a modest Pi. A
                                # known-slow suite overrides it with a manifest `test_timeout`.

    def host_test(self, comp: Component, timeout: float | None = None,
                  log_base: str | None = None, should_cancel=None, on_log_open=None) -> JobResult | None:
        # `log_base` (auto-install driver) overrides the default `test-<comp.id>` job/log name
        # with a RUN-SPECIFIC one, so a run's test log never collides with a prior run's.
        # `should_cancel` (auto-install Abort) is polled while the test runs — like build().
        # Timeout precedence mirrors build(): explicit arg > manifest `test_timeout` > TEST_TIMEOUT_S.
        base = log_base or f"test-{comp.id}"
        if not comp.test_argv:
            return None
        eff_timeout = timeout if timeout is not None else (comp.test_timeout or self.TEST_TIMEOUT_S)
        if self.is_linked_source(comp):       # never run tests INTO an external linked tree
            return JobResult(name=base, state=JobState.FAILED, returncode=1,
                             log_path="", tail=["BLOCKED: source is a linked external "
                             "tree — test it yourself in that checkout"])
        src = str(self.source_dir(comp))
        try:
            argv = commands.build_step_argv({"argv": list(comp.test_argv)},
                                            self.system.runner, str(self.paths.runtime_root), src)
        except commands.CommandError as exc:
            return JobResult(name=base, state=JobState.FAILED,
                             returncode=1, log_path="", tail=[str(exc)])
        return run_job(self.system.runner, name=base, argv=argv, paths=self.paths,
                       cwd=src, logs_dir=self.logs_dir(), timeout=eff_timeout,
                       should_cancel=should_cancel, on_log_open=on_log_open)

    # -- daemon readiness + bounded TX test --------------------------------

    # The daemon's per-band sockets (daemon_protocol.h): raw data + CONF/status. Served under
    # /run/loraham (systemd) or /tmp (direct/user start, LORAHAM_SOCKET_DIR) — prefer /run/loraham
    # when the socket exists there, else the /tmp fallback (mirrors the daemon's clients).
    @staticmethod
    def _prefer_run_socket(run: str, tmp: str) -> str:
        import os
        import stat as _stat
        try:
            return run if _stat.S_ISSOCK(os.stat(run).st_mode) else tmp
        except OSError:
            return tmp

    def raw_socket(self, band: str) -> str:
        return self._prefer_run_socket(f"/run/loraham/lora{band}.sock", f"/tmp/lora{band}.sock")

    def conf_socket(self, band: str) -> str:
        return self._prefer_run_socket(f"/run/loraham/loraconf{band}.sock", f"/tmp/loraconf{band}.sock")

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

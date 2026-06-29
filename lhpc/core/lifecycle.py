"""Component lifecycle: build, start, stop, logs, host test, and a bounded TX test.

`lhpc` is not a permanent supervisor: `start` launches a component (the daemon
daemonizes itself with `-d`, or a wrapper backgrounds it) and returns; state is
later reconstructed by the probe layer, never from a stale PID file. `stop` sends
SIGTERM to the processes the controller can identify for that component.

TX safety: `test` runs RX-safe host tests by default. A TX-capable test is a
distinct, explicit path that returns a `TxTestPlan` (stack, band, parameters,
expected RF effect, dummy-load reminder); the adapter must confirm before
`run_daemon_tx_test` is called. The TX test sends exactly one bounded frame and
verifies it via the daemon's STATS counter — never a continuous transmission.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .model import Component, Stack, emit_param
from .paths import Paths
from .probes import System
from .probes.process import probe_process
from .jobs import JobResult, run_job, tail_log


@dataclass
class StartLaunch:
    ok: bool
    log_path: str
    detail: str = ""


def _real_spawn(argv: list[str], log_path: Path) -> int | None:
    """Launch a process fully detached so it outlives the controller, returning
    its PID. The controller is not a supervisor: it starts the process in a new
    session with output redirected to a log file, then returns.
    """
    log = open(log_path, "ab")
    try:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        return proc.pid
    finally:
        log.close()


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

    def __init__(self, paths: Paths, stacks: tuple[Stack, ...], config: Config,
                 system: System, spawn=_real_spawn) -> None:
        self.paths = paths
        self.stacks = stacks
        self.config = config
        self.system = system
        self._spawn = spawn

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
                res = self.system.runner.run(["/bin/sh", "-c", f"command -v {req.cmd}"], 3.0)
                if res.returncode != 0:
                    missing.append(req)
        return missing

    def source_dir(self, comp: Component) -> Path:
        return self.paths.resolve_source(comp.source.path) if comp.source else self.paths.runtime_root

    def logs_dir(self) -> Path:
        return self.paths.runtime_root / "logs"

    def wrapper_path(self, stack: Stack, comp: Component) -> Path:
        order = "0" if comp.start_order is None else str(comp.start_order)
        short = comp.id[len(stack.id) + 1:] if comp.id.startswith(stack.id + "-") else comp.id
        return self.paths.runtime_root / "start" / f"{stack.id}-{order}-{short}-start"

    # -- build / start / stop / logs --------------------------------------

    def build(self, comp: Component, timeout: float = 600.0) -> JobResult:
        return run_job(self.system.runner, name=f"build-{comp.id}",
                       argv=["/bin/sh", "-c", comp.build_cmd],
                       cwd=str(self.source_dir(comp)), logs_dir=self.logs_dir(),
                       timeout=timeout)

    def _subst(self, comp: Component, text: str, params: dict | None) -> str:
        op = self.config.operator
        # Run-params first: a param default may itself contain {callsign} etc.
        for p in comp.run_params:
            val = (params or {}).get(p.name, p.default)
            text = text.replace("{" + p.name + "}", emit_param(p, val))
        return (text.replace("{runtime}", str(self.paths.runtime_root))
                    .replace("{source}", str(self.source_dir(comp)))
                    .replace("{callsign}", op.callsign or "N0CALL")
                    .replace("{locator}", op.locator))

    def start(self, stack: Stack, comp: Component, params: dict | None = None) -> StartLaunch:
        """Launch the component detached, running the command directly with current
        substitution. (The readable start/ wrapper is kept for MANUAL use, but lhpc
        never depends on it — so a changed/not-yet-bootstrapped manifest always
        starts, and pre-commands like mkdir always reflect the live manifest.)"""
        log = self.logs_dir() / f"start-{comp.id}.log"
        self.logs_dir().mkdir(parents=True, exist_ok=True)
        src = self.source_dir(comp)
        pre = self._subst(comp, comp.pre_cmd, params) or ":"
        run = self._subst(comp, comp.run_cmd, params)
        argv = ["/bin/sh", "-c", f'set -e; {pre}; cd "{src}"; exec {run}']
        try:
            self._spawn(argv, log)
        except OSError as exc:
            return StartLaunch(False, str(log), str(exc))
        return StartLaunch(True, str(log))

    def spawn_post_start(self, stack: Stack, comp: Component,
                         params: dict | None = None) -> None:
        """Run the component's post_start command detached (best-effort), logging to
        its own file. Used for one-off setup that needs the service already up
        (e.g. setting the Meshtastic region via its API once it is listening)."""
        if not comp.post_start:
            return
        log = self.logs_dir() / f"post-{comp.id}.log"
        self.logs_dir().mkdir(parents=True, exist_ok=True)
        src = self.source_dir(comp)
        cmd = self._subst(comp, comp.post_start, params)
        argv = ["/bin/sh", "-c", f'cd "{src}"; {cmd}']
        try:
            self._spawn(argv, log)
        except OSError:
            pass

    def stop(self, comp: Component) -> tuple[list[int], str]:
        """SIGTERM the controller-identifiable processes for this component.

        Components started by `lhpc` run in their own session (start_new_session),
        so we signal the whole process GROUP — this also reaps wrapper/restart-loop
        children (e.g. the serial-KISS socat supervisor). Falls back to a single
        SIGTERM when the group cannot be resolved (e.g. an externally started peer).
        """
        if comp.process is None:
            return [], "no process identity declared"
        pm = probe_process(self.system, comp.process)
        return self.stop_pids(pm.pids)

    def stop_pids(self, pids: list[int]) -> tuple[list[int], str]:
        """SIGTERM the given PIDs by process GROUP (so wrapper/child processes go
        too). Used for targeting a SUBSET of a component's processes — e.g. one
        band's daemon instance out of several."""
        killed: list[int] = []
        signalled_groups: set[int] = set()
        for pid in pids:
            try:
                pgid = os.getpgid(pid)
                if pgid not in signalled_groups:
                    os.killpg(pgid, signal.SIGTERM)
                    signalled_groups.add(pgid)
                killed.append(pid)
            except OSError:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except OSError as exc:
                    return killed, f"failed to signal pid {pid}: {exc}"
        return killed, ("sent SIGTERM" if killed else "no matching process")

    def logs(self, comp: Component, lines: int = 200) -> tuple[str, list[str]]:
        for path in comp.log_paths:
            p = Path(path)
            if p.exists():
                return str(p), tail_log(p, lines)
        job_log = self.logs_dir() / f"start-{comp.id}.log"
        if job_log.exists():
            return str(job_log), tail_log(job_log, lines)
        return "", []

    # -- host test ---------------------------------------------------------

    def spawn_job(self, name: str, command: str, cwd: str | None):
        """Run a shell command detached, streaming combined output to
        logs/<name>.log (truncated first). Returns (log_name, pid) or (None, None)
        on spawn failure. Used by the web so long build/test output is live-viewable."""
        log = self.logs_dir() / f"{name}.log"
        self.logs_dir().mkdir(parents=True, exist_ok=True)
        try:
            log.write_text("")  # truncate previous run
        except OSError:
            pass
        inner = f'cd "{cwd}"; ' if cwd else ""
        argv = ["/bin/sh", "-c", f'{inner}exec {command}']
        try:
            pid = self._spawn(argv, log)
        except OSError:
            return None, None
        return log.name, pid

    def host_test(self, comp: Component, timeout: float = 300.0) -> JobResult | None:
        if not comp.test_cmd:
            return None
        return run_job(self.system.runner, name=f"test-{comp.id}",
                       argv=["/bin/sh", "-c", comp.test_cmd],
                       cwd=str(self.source_dir(comp)), logs_dir=self.logs_dir(),
                       timeout=timeout)

    # -- daemon readiness + bounded TX test --------------------------------

    # The daemon's per-band sockets (daemon_protocol.h): raw data + CONF/status.
    def raw_socket(self, band: str) -> str:
        return f"/tmp/lora{band}.sock"

    def conf_socket(self, band: str) -> str:
        return f"/tmp/loraconf{band}.sock"

    def _stats_txok(self, conf_sock: str) -> int | None:
        try:
            raw = self.system.unix.request(conf_sock, b"GET STATS\n", 1.0, 4096)
        except OSError:
            return None
        line = raw.split(b"\n", 1)[0].decode("ascii", "replace")
        for tok in line.split():
            key, sep, val = tok.partition("=")
            if sep and key == "TXOK":
                try:
                    return int(val)
                except ValueError:
                    return None
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
        conf = self.conf_socket(band)
        before = self._stats_txok(conf)
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
            after = self._stats_txok(conf)
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

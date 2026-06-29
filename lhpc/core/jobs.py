"""Bounded job execution and output.

Long or state-changing operations (build, start, stop, test) run as a tracked
`Job`: a single bounded command whose combined output is written to a log file
under the runtime `logs/` directory, with an in-memory tail for compact display.
The controller keeps no infinite copy of process output — the file is the record,
the tail is what the CLI/web show.

Execution reuses the probe layer's `CommandRunner` (real or fake), so jobs are
fully testable without real subprocesses. Nothing here transmits RF by itself;
TX safety is enforced by the lifecycle layer before a TX-capable job is built.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .probes.backends import CommandRunner

DEFAULT_MAX_TAIL = 2000
DEFAULT_TIMEOUT_S = 600.0


class JobState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class JobResult:
    name: str
    state: JobState
    returncode: int
    log_path: str
    tail: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state is JobState.SUCCEEDED


def run_job(
    runner: CommandRunner,
    *,
    name: str,
    argv: list[str],
    cwd: str | None,
    logs_dir: Path,
    paths,
    timeout: float = DEFAULT_TIMEOUT_S,
    env: dict | None = None,
) -> JobResult:
    """Run one bounded command (structured argv, shell=False), persist its output,
    return a compact result. `cwd`/`env` are passed to the runner directly — no shell.

    Log setup goes through the authoritative `runtime_fs` (contained, O_NOFOLLOW
    create/truncate) and happens BEFORE execution: a symlinked or inaccessible log leaf is
    a TYPED `FAILED` result and the command is NOT run. A failure to persist the output is
    likewise typed — never a silently-successful job with a missing log."""
    from . import runtime_fs
    from .paths import PathContainmentError
    # A job name is controller-derived, but guard the leaf so a planted symlinked log
    # can't redirect output elsewhere.
    safe_name = name if ("/" not in name and ".." not in name and "\x00" not in name) else "job"
    log_path = logs_dir / f"{safe_name}.log"
    try:
        runtime_fs.ensure_dir(paths, logs_dir)
        log_fh = runtime_fs.open_log_truncate(paths, log_path)
    except (OSError, PathContainmentError) as exc:
        # A symlinked/non-directory logs parent (PathContainmentError) is a TYPED failure;
        # the runner is NOT invoked when log setup failed.
        return JobResult(name=name, state=JobState.FAILED, returncode=126, log_path="",
                         tail=[f"job log could not be created safely: {exc}"])

    try:
        result = runner.run(argv, timeout=timeout, cwd=cwd, env=env)
        output = (result.stdout or "") + (result.stderr or "")
        try:
            log_fh.write(output)
            log_fh.flush()
            os.fsync(log_fh.fileno())
        except OSError as exc:
            return JobResult(name=name, state=JobState.FAILED, returncode=126,
                             log_path=str(log_path),
                             tail=[f"job output could not be persisted: {exc}"])
    finally:
        try:
            log_fh.close()
        except OSError:
            pass

    if result.timed_out:
        state = JobState.TIMEOUT
    elif result.returncode == 0:
        state = JobState.SUCCEEDED
    else:
        state = JobState.FAILED

    tail: deque[str] = deque(output.splitlines(), maxlen=DEFAULT_MAX_TAIL)
    return JobResult(name=name, state=state, returncode=result.returncode,
                     log_path=str(log_path), tail=list(tail))


def tail_log(log_path: Path, lines: int = 200, max_bytes: int = 256 * 1024) -> list[str]:
    """Bounded TAIL read of a log, opened with O_NOFOLLOW so a swapped-in symlink leaf is
    refused (never followed). Reads at most the last `max_bytes`, returns the last
    `lines` lines. Used for EXTERNAL logs (e.g. the daemon's /tmp log); runtime-owned logs
    tail through `runtime_fs.tail` (containment-checked)."""
    try:
        fd = os.open(str(log_path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return []
    try:
        with os.fdopen(fd, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]

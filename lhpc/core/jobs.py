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
    timeout: float = DEFAULT_TIMEOUT_S,
) -> JobResult:
    """Run one bounded command, persist its output, return a compact result."""
    # cwd is encoded into the command so a single CommandRunner.run call suffices
    # and stays fakeable (no separate chdir state).
    full = (["/bin/sh", "-c", f'cd {_shquote(cwd)} && exec "$@"', "lhpc-job", *argv]
            if cwd else argv)
    result = runner.run(full, timeout=timeout)

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{name}.log"
    output = (result.stdout or "") + (result.stderr or "")
    try:
        log_path.write_text(output, encoding="utf-8")
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
    """Bounded read of the TAIL of a job/service log — reads at most the last
    `max_bytes` (logs can grow large), then returns the last `lines` lines."""
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def _shquote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"

"""Driver-side tracking gate for detached WEB build/test/install children (parallels the HMAC driver
gate). The child MUST prove the parent identity-tracked it — its own job marker `state/jobs/<log>.job`
must match op/target/pid/complete-identity/attempt_id — BEFORE any source lock or mutation. Importable
by the launcher runtime AND the install CLI using only runtime_fs/procident/validators (no Services
object). Read-only; never raises.

Web job log names are REUSED across runs (`build-<c>`, `test-<c>`, `install-<t>`), so a STALE same-name
marker from a prior run may briefly exist; the gate therefore polls until OUR exact attempt matches (it
never refuses early on a mismatch — the parent overwrites the marker right after spawn)."""

from __future__ import annotations

import os
import stat as _stat
import time
import tomllib

from . import procident, runtime_fs, validators
from .paths import PathContainmentError

_JOB_MARKER_MAX = 64 * 1024
_DEFAULT_TIMEOUT_S = 8.0
_POLL_S = 0.05


def _timeout() -> float:
    """Gate poll window; overridable via env (tests set it short). Malformed/≤0 → the default."""
    try:
        t = float(os.environ.get("LHPC_WEBJOB_GATE_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)))
        return t if t > 0 else _DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _read_job_marker(paths, log: str):
    try:
        slug = validators.path_component(log, field="job log")
        f = paths.under("state", "jobs", slug + ".job")
    except (validators.ValidationError, PathContainmentError):
        return None
    stt = runtime_fs.stat_leaf_nofollow(paths, f)
    if stt is None or not _stat.S_ISREG(stt.st_mode) or stt.st_size > _JOB_MARKER_MAX:
        return None
    try:
        return tomllib.loads(runtime_fs.read_text_regular(paths, f, max_bytes=_JOB_MARKER_MAX))
    except (OSError, PathContainmentError, ValueError, tomllib.TOMLDecodeError):
        return None


def is_live_attempt(paths, log: str, attempt_id: str) -> bool:
    """Read-only, single-shot: is the tracked child for THIS exact attempt still alive? Requires the job
    marker's stored `log` == `log`, `attempt_id` match, AND complete-identity `identity_matches`. So a stale/
    inconsistent same-log marker belonging to a DIFFERENT attempt never masks attempt A's derived-unsafe."""
    raw = _read_job_marker(paths, log)
    if raw is None:
        return False
    try:
        return (raw.get("log") == log and str(raw.get("attempt_id", "")) == str(attempt_id)
                and procident.identity_matches(raw, int(raw["pid"])))
    except (KeyError, TypeError, ValueError):
        return False


def verify_tracked(paths, log: str, op: str, target: str, attempt_id: str, mypid: int,
                   timeout_s: float | None = None) -> bool:
    """Bounded-poll our job marker; return True only once it matches op/target/pid/complete-identity/
    attempt_id exactly. Timeout → False (the child then exits WITHOUT any mutation)."""
    if timeout_s is None:
        timeout_s = _timeout()
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        raw = _read_job_marker(paths, log)
        if raw is not None:
            try:
                if (raw.get("op") == op and raw.get("target") == target
                        and int(raw.get("pid")) == mypid
                        and str(raw.get("attempt_id", "")) == str(attempt_id)
                        and procident.identity_matches(raw, mypid)):
                    return True
            except (TypeError, ValueError):
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_S)

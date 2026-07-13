"""Attempt / terminal-result markers for detached WEB build/test/install jobs.

One marker per job LOG name — `state/jobresults/<log>.json` — driving the running-task banner's
green/red lifecycle. The PARENT `reserve()`s it (state=`starting`, `startup_unverified=True`) BEFORE
spawn; the CHILD advances it (`mark_gate_passed` → `mark_running` → `terminalize`). Every mutating op
is COMPARE-BEFORE-REPLACE on `attempt_id` and NEVER creates an absent marker, so a stale child can
never resurrect a dismissed/newer attempt. Reads are descriptor-safe (no-follow, regular-only,
byte-bounded) and STRUCTURALLY validated here; manifest-aware checks (target belongs to a stack) live
in the service projection. No secret ever enters a marker. Every function is best-effort and NEVER
raises (a GET must not 500)."""

from __future__ import annotations

import calendar
import contextlib
import fcntl
import json
import re
import stat as _stat
import time

from . import runtime_fs, validators
from .paths import PathContainmentError

_DIR = ("state", "jobresults")
_MARKER_MAX = 64 * 1024
_OPS = ("build", "test", "install")
_STATES = ("starting", "running", "done", "failed", "unsafe")
_TERMINAL = ("done", "failed", "unsafe")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ATTEMPT_RE = re.compile(r"^[0-9a-f]{8,64}$")
_LOG_RE = re.compile(r"^[0-9A-Za-z._-]{1,120}\.log$")
_KEY_RE = re.compile(r"^[0-9A-Za-z._:/-]{1,200}$")     # canonical reslock source keys
_MAX_KEYS = 64
_MAX_DETAIL = 400


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_epoch(ts) -> int | None:
    if not (isinstance(ts, str) and _TS_RE.match(ts)):
        return None
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return None


def _path(paths, log: str):
    slug = validators.path_component(log, field="job log")     # single component, no traversal
    if not _LOG_RE.match(slug):
        raise ValueError("bad job log name")
    return paths.under(*_DIR, slug + ".json")


@contextlib.contextmanager
def _locked(paths, log: str):
    """A small BLOCKING per-log flock so every compare-and-write/unlink is atomic against concurrent
    requests (kernel-released on death). Reads stay lock-free (write_marker is an atomic rename)."""
    try:
        slug = validators.path_component(log, field="job log")
    except (validators.ValidationError, PathContainmentError):
        yield False
        return
    try:
        runtime_fs.ensure_dir(paths, paths.under("state", "locks"))
        fh = runtime_fs.open_lock(paths, paths.under("state", "locks", "jobresult." + slug + ".lock"))
    except (OSError, PathContainmentError):
        yield False
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield True
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        except OSError:
            pass


def _valid(d, log: str) -> bool:
    """STRUCTURAL schema (no manifest knowledge). `log` is the leaf this marker MUST name."""
    if not isinstance(d, dict):
        return False
    if d.get("op") not in _OPS or d.get("state") not in _STATES:
        return False
    if d.get("log") != log or not (isinstance(log, str) and _LOG_RE.match(log)):
        return False
    if not (isinstance(d.get("attempt_id"), str) and _ATTEMPT_RE.match(d["attempt_id"])):
        return False
    for k in ("target", "stack"):
        v = d.get(k, "")
        if not (isinstance(v, str) and len(v) <= 120):
            return False
    if not isinstance(d.get("startup_unverified", False), bool):
        return False
    if not isinstance(d.get("admitted", False), bool):     # a malformed "false"/1/None → untrusted
        return False
    sk = d.get("source_keys", [])
    if not (isinstance(sk, list) and len(sk) <= _MAX_KEYS
            and all(isinstance(x, str) and _KEY_RE.match(x) for x in sk)):
        return False
    for tsf in ("started_at", "finished_at"):
        ts = d.get(tsf, "")
        if ts and not (isinstance(ts, str) and _TS_RE.match(ts)):
            return False
    if not (isinstance(d.get("detail", ""), str) and len(d.get("detail", "")) <= _MAX_DETAIL + 40):
        return False
    if d["state"] in _TERMINAL and not _TS_RE.match(d.get("finished_at", "")):
        return False        # a terminal record MUST carry a valid completion time
    di = d.get("driver_ident")
    if di is not None and not isinstance(di, dict):
        return False
    return True


def _read_raw(paths, log: str) -> dict | None:
    """Descriptor-safe read of one marker → validated dict, or None (absent/unsafe-leaf/oversized/malformed)."""
    try:
        p = _path(paths, log)
    except (ValueError, validators.ValidationError, PathContainmentError):
        return None
    stt = runtime_fs.stat_leaf_nofollow(paths, p)
    if stt is None or not _stat.S_ISREG(stt.st_mode) or stt.st_size > _MARKER_MAX:
        return None
    try:
        d = json.loads(runtime_fs.read_text_regular(paths, p, max_bytes=_MARKER_MAX))
    except (OSError, PathContainmentError, ValueError):
        return None
    return d if _valid(d, log) else None


def _write(paths, log: str, d: dict) -> bool:
    if not _valid(d, log):
        return False
    try:
        runtime_fs.ensure_dir(paths, paths.under(*_DIR))
        runtime_fs.write_marker(paths, _path(paths, log), json.dumps(d), 0o600)
        return True
    except (OSError, PathContainmentError, ValueError, validators.ValidationError):
        return False


def _unlink(paths, log: str) -> bool:
    try:
        runtime_fs.unlink(paths, _path(paths, log))
        return True
    except FileNotFoundError:
        return True
    except (OSError, PathContainmentError, ValueError, validators.ValidationError):
        return False


def _mutate(paths, log, attempt_id, fn) -> bool:
    """COMPARE-AND-WRITE under the per-log lock: re-read → require existing marker with matching
    attempt_id → write fn(d). No absent-create; atomic against concurrent requests."""
    with _locked(paths, log) as ok:
        if not ok:
            return False
        d = _read_raw(paths, log)
        if d is None or d.get("attempt_id") != attempt_id:
            return False
        return _write(paths, log, fn(d))


def reserve(paths, log, attempt_id, op, target, stack, source_keys) -> bool:
    """PARENT, pre-spawn: create the attempt (state=starting, startup_unverified, admitted=False). Under the
    per-log lock it REFUSES to replace an existing LIVE attempt (state ∈ starting/running/unsafe) — only a
    terminal done/failed result may be superseded by a new run (per-log single-flight)."""
    if op not in _OPS or not (isinstance(attempt_id, str) and _ATTEMPT_RE.match(attempt_id)):
        return False
    keys = sorted({k for k in (source_keys or []) if isinstance(k, str) and _KEY_RE.match(k)})[:_MAX_KEYS]
    d = {"op": op, "target": str(target)[:120], "stack": str(stack)[:120], "log": log,
         "attempt_id": attempt_id, "state": "starting", "startup_unverified": True, "admitted": False,
         "source_keys": keys, "started_at": _now(), "finished_at": "", "detail": ""}
    with _locked(paths, log) as ok:
        if not ok:
            return False
        existing = _read_raw(paths, log)
        if existing is not None and existing.get("state") in ("starting", "running", "unsafe"):
            return False                                   # a live/blocking attempt is never overwritten
        return _write(paths, log, d)


def mark_gate_passed(paths, log, attempt_id) -> bool:
    """CHILD, after the tracking gate: clear startup_unverified (state stays `starting`)."""
    return _mutate(paths, log, attempt_id, lambda d: {**d, "startup_unverified": False})


def mark_running(paths, log, attempt_id) -> bool:
    """CHILD, after acquiring source locks: the ADMITTED signal (state=running, admitted=True)."""
    return _mutate(paths, log, attempt_id,
                   lambda d: {**d, "startup_unverified": False, "state": "running", "admitted": True})


def _safe_ident(di) -> dict:
    return {k: di.get(k) for k in ("pid", "starttime", "pgid", "sid")
            if isinstance(di, dict) and isinstance(di.get(k), int)}


def terminalize(paths, log, attempt_id, state, detail="", driver_ident=None) -> bool:
    """Terminal write (done/failed/unsafe). `unsafe` stores only SAFE driver identity fields (never
    session_ident, never fed to session_ceased)."""
    if state not in _TERMINAL:
        return False

    def _f(d):
        nd = {**d, "state": state, "finished_at": _now(),
              "detail": str(detail)[:_MAX_DETAIL], "startup_unverified": False}
        nd.pop("driver_ident", None)
        if state == "unsafe":
            si = _safe_ident(driver_ident)
            if si:
                nd["driver_ident"] = si
        return nd
    return _mutate(paths, log, attempt_id, _f)


def recover(paths, log, attempt_id) -> bool:
    """Explicit-ack recovery of an `unsafe` attempt → non-blocking `failed`; drop driver identity."""
    with _locked(paths, log) as ok:
        if not ok:
            return False
        d = _read_raw(paths, log)
        if d is None or d.get("attempt_id") != attempt_id or d.get("state") != "unsafe":
            return False
        nd = {**d, "state": "failed",
              "detail": ("recovered — " + d.get("detail", ""))[:_MAX_DETAIL]}
        nd.pop("driver_ident", None)
        if not _TS_RE.match(nd.get("finished_at", "")):
            nd["finished_at"] = _now()
        return _write(paths, log, nd)


def remove(paths, log, attempt_id) -> bool:
    """Dismissal: unlink ONLY the matching attempt (durable). A stale attempt_id is refused."""
    with _locked(paths, log) as ok:
        if not ok:
            return False
        d = _read_raw(paths, log)
        if d is None or d.get("attempt_id") != attempt_id:
            return False
        return _unlink(paths, log)


def read_one(paths, log: str) -> dict | None:
    """GET-safe read of ONE validated marker (or None). Never mutates/raises."""
    return _read_raw(paths, log)


def read_results(paths) -> list:
    """GET-safe: all valid attempt markers as (log, dict). Skips symlink/oversized/malformed/mismatched;
    returns [] on an unsafe/missing dir. NEVER mutates, NEVER raises."""
    out = []
    try:
        entries = runtime_fs.scandir_nofollow(paths, paths.under(*_DIR))
    except (OSError, PathContainmentError, ValueError):
        return []
    for name, is_link in entries:
        if is_link or not name.endswith(".log.json"):
            continue
        rec = _read_raw(paths, name[:-5])          # strip ".json" → the "<...>.log" leaf
        if rec is not None:
            out.append((name[:-5], rec))
    return out


def prune_done(paths, now_epoch: int, expiry_s: int) -> int:
    """Housekeeping: remove ONLY `done` markers older than expiry (failed/unsafe retained). For each expired
    candidate from the lock-free snapshot, RE-READ under the per-log lock and unlink only if it is STILL the
    same attempt AND an expired `done` — so a `reserve()` that replaced it with a newer/live attempt between
    the snapshot and the unlink survives. Never raises."""
    removed = 0
    for log, snap in read_results(paths):
        if snap.get("state") != "done":
            continue
        with _locked(paths, log) as ok:
            if not ok:
                continue
            cur = _read_raw(paths, log)                     # re-read UNDER the lock
            if (cur is None or cur.get("attempt_id") != snap.get("attempt_id")
                    or cur.get("state") != "done"):
                continue                                    # replaced by a newer/live attempt → keep
            fin = parse_epoch(cur.get("finished_at", ""))
            if fin is not None and now_epoch - fin >= expiry_s and _unlink(paths, log):
                removed += 1
    return removed

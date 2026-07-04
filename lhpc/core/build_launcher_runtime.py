"""Shared, tested runtime for the detached build/test launcher.

The generated launcher (see `commands._BUILD_RUNNER`) is a THIN wrapper that only passes an
immutable spec dict here; ALL security-sensitive behavior lives in this module so it is unit
tested rather than embedded in a generated string:

* strict positive per-step timeout parsing (a malformed value fails safe, never unlimited);
* descriptor-safe (no-follow) source-transaction journal preflight;
* index-lock → journal-check → source-lock handoff, all via no-follow lock opens;
* bounded `pkg-config`;
* structured (`shell=False`) step spawning with output streamed to the inherited job log;
* process-tree termination via the shared `proctree` session-token helper;
* fail-closed blocking before any source access when a lock/journal leaf is unsafe.

`run()` raises `SystemExit(code)` on any blocking/failed condition (the thin launcher lets it
propagate), mirroring the exit codes the previous inline launcher used: 3 = lock/journal
blocked, 1 = pkg-config failure, first failing step's own return code otherwise.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time

from . import proctree

_PKGCONFIG_TIMEOUT = 30
_LOCK_POLL = 0.2


def _step_timeout() -> float:
    """Strict positive per-step timeout from the environment; a malformed value fails SAFE
    (SystemExit 3) rather than becoming unlimited."""
    try:
        t = float(os.environ.get("LHPC_BUILD_STEP_TIMEOUT_S", "1800"))
        if not (t > 0):
            raise ValueError
        return t
    except (TypeError, ValueError):
        sys.stderr.write("invalid LHPC_BUILD_STEP_TIMEOUT_S (must be a positive number)\n")
        raise SystemExit(3)


def _lock_tries() -> int:
    return max(1, int(float(os.environ.get("LHPC_BUILD_LOCK_WAIT_S", "10")) / _LOCK_POLL))


def _flock_bounded(fd: int, tries: int) -> bool:
    for _ in range(tries):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            time.sleep(_LOCK_POLL)
    return False


def _resolve_argv(tokens: list) -> list:
    """Resolve `{pkgconfig:NAME}` tokens via a BOUNDED pkg-config; fail closed on error."""
    argv: list = []
    for t in tokens:
        if t.startswith("{pkgconfig:") and t.endswith("}"):
            try:
                r = subprocess.run(["pkg-config", "--cflags", "--libs", t[11:-1]],
                                   capture_output=True, text=True, timeout=_PKGCONFIG_TIMEOUT)
            except subprocess.TimeoutExpired:
                sys.stderr.write("pkg-config timed out for %s\n" % t)
                raise SystemExit(1)
            if r.returncode != 0:
                sys.stderr.write("pkg-config failed for %s: %s\n" % (t, r.stderr))
                raise SystemExit(1)
            argv += r.stdout.split()
        else:
            argv.append(t)
    return argv


def _run_step(argv: list, cwd: str, env: dict, timeout: float) -> int:
    """Run one step in its OWN session; on timeout, terminate the whole tree via the shared
    proctree session-token helper. Output is inherited -> streamed to the job log, not held
    in memory."""
    # LIVE log streaming: PYTHONUNBUFFERED un-buffers python tools (pip, PlatformIO) —
    # the dominant chunkiness source. Deliberately NO stdbuf/LD_PRELOAD wrapping: this
    # launcher also runs HOST-TEST steps, and an inherited LD_PRELOAD alters the pipe
    # buffering of the programs UNDER TEST (the daemon suite's single-read capture then
    # races line-buffered output and fails under load). glibc tools keep their own
    # ~4 KB block flushes — still live via the fd redirect, just coarser.
    env = {**env, "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen(argv, cwd=cwd, env=env, shell=False, start_new_session=True)
    token = proctree.capture_session_token(p.pid)   # FULL ownership token captured at spawn
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result = proctree.terminate_session(token, os.getpid())
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if not result.ok:                # UNVERIFIED or INCOMPLETE -> surface, don't hide
            sys.stderr.write("WARNING: step termination %s (surviving processes possible): "
                             "%s\n" % (result.value, " ".join(argv)))
        sys.stderr.write("step timed out after %ss: %s\n" % (timeout, " ".join(argv)))
        return 124


def run(spec: dict) -> None:
    """Execute the build/test job described by `spec` (keys: `steps`, `cwd`, `runtime_root`,
    `lock_names`, `index_lock_name`). Lock/journal access is DESCRIPTOR-SAFE: `Paths` is
    rebuilt from `runtime_root` and every lock is opened via `runtime_fs.open_lock` (a full
    parent NO-FOLLOW walk) and the journal scanned via `runtime_fs.scandir_nofollow`, so a
    symlinked/replaced parent ANYWHERE in the lock/journal path fails closed BEFORE source
    access. Index-lock → journal-preflight → source-locks handoff; every acquired fd is
    released in `finally` (including partial-acquisition/failure paths). Raises SystemExit on
    any blocked/failed condition."""
    from pathlib import Path
    from .paths import Paths, PathContainmentError
    from . import runtime_fs
    steps = spec["steps"]
    cwd = spec["cwd"]
    paths = Paths(runtime_root=Path(spec["runtime_root"]))
    lock_names = sorted(spec.get("lock_names") or [])
    index_name = spec.get("index_lock_name") or ""
    step_timeout = _step_timeout()
    tries = _lock_tries()

    def _open(name):
        # Descriptor-safe: `open_lock` walks the parent NO-FOLLOW from the runtime root, so a
        # symlinked parent (state/, state/locks/) or lock leaf fails closed.
        return runtime_fs.open_lock(paths, paths.under("state", "locks", name))

    held = []            # file objects held for the whole job lifetime
    idx = None
    try:
        # Index-to-source handoff: hold the INDEX lock, verify NO unresolved journal, THEN
        # take the source lock(s); release the index lock only afterwards.
        if index_name:
            try:
                idx = _open(index_name)
            except (PathContainmentError, OSError) as e:
                sys.stderr.write("source-transaction index lock open failed (unsafe path?): %s\n" % e)
                raise SystemExit(3)
            if not _flock_bounded(idx.fileno(), tries):
                sys.stderr.write("source-transaction index busy — another source operation is "
                                 "in progress\n")
                raise SystemExit(3)
            try:
                entries = runtime_fs.scandir_nofollow(paths, paths.under("state", "source-txn"))
            except PathContainmentError as e:
                sys.stderr.write("blocked: unsafe source-transaction directory (%s)\n" % e)
                raise SystemExit(3)
            if any(n.endswith(".json") for n, _is_link in entries):
                sys.stderr.write("blocked: an unresolved source-transaction journal is present "
                                 "— resolve it before building/testing\n")
                raise SystemExit(3)

        # Hold the source-path lock(s) for the FULL job lifetime BEFORE touching the source.
        for name in lock_names:
            try:
                f = _open(name)
            except (PathContainmentError, OSError) as e:
                sys.stderr.write("source lock open failed (%s): %s\n" % (name, e))
                raise SystemExit(3)
            if not _flock_bounded(f.fileno(), tries):
                f.close()
                sys.stderr.write("could not acquire source lock %s — another source operation "
                                 "is in progress\n" % name)
                raise SystemExit(3)
            held.append(f)
        if idx is not None:              # source lock(s) held -> handoff complete
            fcntl.flock(idx.fileno(), fcntl.LOCK_UN)
            idx.close()
            idx = None

        for s in steps:
            argv = _resolve_argv(s["argv"])
            print("+ " + " ".join(argv), flush=True)
            rc = _run_step(argv, cwd, {**os.environ, **s["env"]}, step_timeout)
            if rc != 0:
                raise SystemExit(rc)
    finally:
        # Release EVERY acquired fd explicitly — including partial-acquisition failure paths.
        for f in held:
            try:
                f.close()
            except OSError:
                pass
        if idx is not None:
            try:
                idx.close()
            except OSError:
                pass

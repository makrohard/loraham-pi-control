"""The console's source-probe cache: a checkout's git answer, reused across page reads.

A status page runs `git status` and `git describe` for every installed source (the bulk of a page's time on
a small box). This keeps the last answer per (checkout, pin) with a KEY and the time it was probed, and gives it
back while the key is unchanged and the answer is at most `MAX_AGE_S` old; otherwise it probes as before.

The key: `.git/HEAD`'s bytes; inode, size and mtime of `.git/index`, `.git/packed-refs` and the ref file HEAD
names; and the mtime of every directory holding a tracked file (listed by `git ls-files` when the answer is
stored). A commit, checkout, reset, stage, tag or pack changes the first part; a file created, deleted or renamed
changes its directory. A hand edit of an existing file inside an unchanged directory changes neither: it shows
when the answer is `MAX_AGE_S` old, counted from the probe, never from a reuse.

Never cached: a checkout whose `.git` is not a plain directory (a symlink, a gitdir file, absent), and any
answer other than MATCH / DIRTY / DIFFERS (a timeout or a git error is retried at the next read). The service
gives the cache only to snapshots without `fresh` (never to a recheck under the operation locks), and every
mutating service entry clears it (`snapshot_memo.invalidates_snapshot`); a probe already running when it is cleared
does not store its answer (a generation counter). One per process (`SHARED`): the
console keeps it across requests; a CLI command starts with an empty one.
"""
from __future__ import annotations

import os
import stat
import threading
import time

from .model import SourceState
from .probes.source import _READ_ONLY, _TIMEOUT_S, probe_source

MAX_AGE_S = 30.0
_KEPT = (SourceState.MATCH, SourceState.DIRTY, SourceState.DIFFERS)
_HEAD_MAX = 4096


def _stamp(path: str):
    """(inode, size, mtime_ns) of `path` without following a symlink; None when absent."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def meta_key(abs_path: str):
    """The git-metadata part of the key, or None when `abs_path/.git` is not a plain directory or HEAD is
    unreadable (then nothing is cached for this checkout)."""
    gitdir = os.path.join(abs_path, ".git")
    try:
        if not stat.S_ISDIR(os.lstat(gitdir).st_mode):
            return None
        fd = os.open(os.path.join(gitdir, "HEAD"), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            head = os.read(fd, _HEAD_MAX)
        finally:
            os.close(fd)
        ref = head.decode("utf-8", "replace").strip()
        ref_file = (os.path.join(gitdir, *ref[len("ref: "):].split("/"))
                    if ref.startswith("ref: refs/") and ".." not in ref else None)
        return (head, _stamp(os.path.join(gitdir, "index")), _stamp(os.path.join(gitdir, "packed-refs")),
                _stamp(ref_file) if ref_file else None)
    except OSError:
        return None


def _tracked_dirs(system, abs_path: str):
    """The checkout's directories that hold a tracked file (the root included), or None."""
    r = system.runner.run(["git", "-C", abs_path, "ls-files", "-z"], timeout=_TIMEOUT_S, env=_READ_ONLY)
    if r.returncode != 0 or r.timed_out:
        return None
    rel = {os.path.dirname(p) for p in r.stdout.split("\0") if p}
    return tuple(sorted(os.path.join(abs_path, d) if d else abs_path for d in rel | {""}))


def _dir_key(dirs):
    out = []
    for d in dirs:
        try:
            out.append(os.lstat(d).st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


class SourceCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict = {}
        self._generation = 0            # bumped by every clear(): a probe begun before one is not stored
        self.clock = time.monotonic

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation += 1

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def get(self, abs_path: str, pin: str, meta):
        """The stored probe, or None. The key checks run outside the lock; the hit is returned only if no
        clear() came in between (the same generation, the same entry), so a mutation during a read is seen."""
        with self._lock:
            e, gen = self._entries.get((abs_path, pin)), self._generation
        if e is None or e[0] != meta or self.clock() - e[3] > MAX_AGE_S or _dir_key(e[1]) != e[2]:
            return None
        with self._lock:
            if self._generation != gen or self._entries.get((abs_path, pin)) is not e:
                return None
        return e[4]

    def put(self, abs_path: str, pin: str, meta, dirs, dkey, at: float, probe, generation: int) -> None:
        """Store `probe` unless the cache was cleared since `generation` was read (a mutation ran while the
        probe was in flight: its answer may predate the mutation)."""
        with self._lock:
            if generation == self._generation:
                self._entries[(abs_path, pin)] = (meta, dirs, dkey, at, probe)


def probe_cached(cache, system, spec, abs_path: str, git: bool = True, probe=probe_source):
    """`probe(system, spec, abs_path, git=…)` (the source probe), through `cache` when one is given (see the
    module docstring)."""
    if cache is None or not git:
        return probe(system, spec, abs_path, git=git)
    meta = meta_key(abs_path)
    if meta is None:
        return probe(system, spec, abs_path)
    pin = spec.pin_commit or ""
    hit = cache.get(abs_path, pin, meta)
    if hit is not None:
        return hit
    # The key and the generation are taken BEFORE the probe, so a change while it runs makes the next read
    # probe again, and a clear while it runs keeps its answer out of the cache.
    gen = cache.generation()
    at = cache.clock()
    dirs = _tracked_dirs(system, abs_path)
    dkey = _dir_key(dirs) if dirs is not None else None
    got = probe(system, spec, abs_path)
    if dirs is not None and got.state in _KEPT:
        cache.put(abs_path, pin, meta, dirs, dkey, at, got, gen)
    return got


SHARED = SourceCache()

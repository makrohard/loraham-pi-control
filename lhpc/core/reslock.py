"""Controller-owned operation locks for claimed resources (Workstream §12).

Mutating operations (build/update/uninstall, start/stop/restart, and per-resource
start/stop) must not overlap on the same claimed resource. This module provides a
runtime-owned exclusive lock per canonical resource key under `state/locks/`, using
`flock` — which the kernel releases automatically when the holding process dies, so a
crashed holder never leaves a permanently stuck lock (stale-lock recovery is intrinsic).

An owner record (operation, target, pid, time) is written alongside the lock purely for
diagnostics, so a conflict can name the holder. This is NOT a persistent supervisor: a
lock lives only for the duration of one operation.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from contextlib import contextmanager

from . import runtime_fs
from .paths import Paths

_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ResourceBusy(Exception):
    """A conflicting operation already holds the resource lock."""

    def __init__(self, key: str, holder: dict | None):
        self.key = key
        self.holder = holder or {}
        op = self.holder.get("operation", "?")
        tgt = self.holder.get("target", "?")
        pid = self.holder.get("pid", "?")
        super().__init__(f"resource '{key}' is busy: {op} on '{tgt}' (pid {pid})")


def canonical_key(resource_key: str) -> str:
    """Normalise a resource key to one stable, filename-safe lock id (no separators,
    no `..` traversal)."""
    k = _KEY_RE.sub("-", str(resource_key).strip().lower())
    k = k.replace("..", "-").strip("-.")
    return k or "resource"


def source_lock_key(source_path: str) -> str:
    """THE canonical lock key for a managed source checkout, derived from its path (not
    a component id or stack target). Every operation on a shared checkout (e.g. chat and
    igate both use `src/LoRaHAM_Daemon`) MUST contend on this one key."""
    return f"source.{source_path}"


def lock_file_path(paths: Paths, resource_key: str):
    """The on-disk flock file for a resource key — THE same file `operation_lock` uses,
    so an external process (e.g. a detached build launcher) can hold the identical lock."""
    return paths.under("state", "locks", canonical_key(resource_key) + ".lock")


def _owner_path(paths: Paths, key: str):
    return paths.under("state", "locks", key + ".owner")


def read_owner(paths: Paths, key: str) -> dict | None:
    try:
        return json.loads(runtime_fs.read_text(paths, _owner_path(paths, canonical_key(key))))
    except (OSError, ValueError):
        return None


# IN-PROCESS PUBLICATION LOCK, one per resource key.
#
# The flock and the `.owner` record cannot be made atomic against other PROCESSES — the
# kernel grants the flock, and only then can we write who we are. But every ambiguous
# report we have actually seen came from our OWN process: this service is shared across
# waitress threads, so two threads routinely contend for the same claim, and the second
# one could observe "flock held, owner not yet written" and be told it was busy by a
# holder it could not identify. The same window exists on RELEASE (owner already removed,
# flock not yet dropped).
#
# Serializing acquire+publish and unpublish+release per key makes that in-between state
# unobservable WITHIN this process, which is what lets the contender path treat an
# unidentifiable holder as genuinely external and fail fast instead of polling a timeout.
_PUBLISH_LOCKS: dict = {}
_PUBLISH_LOCKS_GUARD = threading.Lock()


def _publish_lock(key: str) -> threading.Lock:
    with _PUBLISH_LOCKS_GUARD:
        lk = _PUBLISH_LOCKS.get(key)
        if lk is None:
            lk = _PUBLISH_LOCKS[key] = threading.Lock()
        return lk


@contextmanager
def operation_lock(paths: Paths, resource_key: str, operation: str,
                   target: str = "", blocking: bool = False, stamp: float | None = None):
    """Hold an exclusive controller lock on `resource_key` for one operation.

    Raises `ResourceBusy` (naming the holder) if another operation holds it and
    `blocking` is False. The lock is released — and the owner record cleared — on exit,
    and the kernel releases the flock automatically if this process dies mid-operation.

    Acquisition and publication of the owner record are serialized per key within this
    process (see `_PUBLISH_LOCKS`), so no other thread here can see one without the other.
    """
    key = canonical_key(resource_key)
    lockfile = paths.under("state", "locks", key + ".lock")
    fh = runtime_fs.open_lock(paths, lockfile)
    pub = _publish_lock(key)
    owner = json.dumps({
        "resource": key, "operation": operation, "target": target,
        "pid": os.getpid(), "acquired": stamp if stamp is not None else time.time(),
    })
    try:
        if blocking:
            # A BLOCKING flock may wait for a holder that needs `pub` to release — so it is
            # never attempted while holding `pub`. The publish itself still is.
            fcntl.flock(fh, fcntl.LOCK_EX)
            with pub:
                runtime_fs.write_marker(paths, _owner_path(paths, key), owner)
        else:
            with pub:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ResourceBusy(key, read_owner(paths, key)) from exc
                runtime_fs.write_marker(paths, _owner_path(paths, key), owner)
        try:
            yield
        finally:
            # Symmetric: the owner record disappears and the flock drops as one step, so a
            # contender never sees a held lock with no owner on the way out either.
            with pub:
                try:
                    runtime_fs.unlink(paths, _owner_path(paths, key))
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()

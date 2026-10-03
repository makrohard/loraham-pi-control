"""The interruption-and-retry harness: every durable write of an operation, one failure at a time.

    from interrupts import FAILURES, durable_writes, run_interrupted, tree

Every recovery-bearing transaction writes its durable state through `lhpc.core.runtime_fs` (the
descriptor-anchored seam; callers use it as `runtime_fs.<fn>`). `durable_writes()` wraps each
writer of that seam for the duration of a `with` block and logs every OUTERMOST call (a writer
that delegates to another — `write_marker` → `atomic_write` → `atomic_write_bytes` — is one
point, not three). With `fail_at=k` and `exc` it raises `exc` INSTEAD of performing write k, so
the operation sees the disk refuse (ENOSPC, EIO) or the operator's Ctrl-C land exactly before
that write, with everything earlier on disk — the state a crash or a full disk leaves.

A writer whose contract reports an `OSError` instead of raising it (`OwnedMarker.rewrite`/`remove`
return `False`, `source_fs.remove_bound` a `(False, reason)`) gets the injected `OSError` as that
report, as the real failure would; a `KeyboardInterrupt` always propagates.

A test pins the operation's write log — the order of a transaction's durable writes IS its crash
contract — so a write added later fails the pin and must join the parametrisation; then, per
(write point, failure class), it runs the operation into the failure (`run_interrupted`), runs the
matching recovery path, checks the state it leaves (`tree()`), and retries the operation to the
end state of an uninterrupted run.
"""
from __future__ import annotations

import errno
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from lhpc.core import runtime_fs

# The writers of the seam. Reads, probes and lock opens are not durable writes.
WRITERS = ("atomic_write_bytes", "atomic_write", "write_marker", "write_launcher",
           "create_exclusive_bytes", "open_marker_excl", "rename_leaf", "link_leaf", "unlink",
           "unlink_link", "chmod", "replace_symlink", "publish_symlink", "ensure_dir", "mkdir")
MARKER_WRITERS = ("rewrite", "remove")

FAILURES = {
    "ENOSPC": lambda: OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)),
    "EIO": lambda: OSError(errno.EIO, os.strerror(errno.EIO)),
    "KeyboardInterrupt": KeyboardInterrupt,
}

_depth = threading.local()


def _label(name, args, root: Path) -> str:
    """The path a write targets, relative to the runtime root (a leaf name for a descriptor-relative
    call), with per-run nonces masked."""
    args = [a for a in args if isinstance(a, (str, Path))]
    if name == "mkdir":
        args = [Path(root, *args)]                  # runtime_fs.mkdir(paths, "state", "binary")
    if not args:
        return "?"
    try:
        rel = str(Path(args[0]).relative_to(root))
    except ValueError:
        rel = str(args[0])
    # nonces, pids, times, and the binary install's `tempfile` suffix
    return re.sub(r"[0-9a-f]{8,}|\d{3,}|(?<=lhpc-binary-)[a-z0-9_]{8}", "*", rel)


class _Module:
    """A module's `os`/`shutil` as that module sees it: the named writers replaced, every other
    attribute (`os.path`, the reads) the real one."""

    def __init__(self, real, writers):
        self._real = real
        self.__dict__.update(writers)

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextmanager
def durable_writes(root: Path, *, fail_at: int | None = None, exc=None, raw=()):
    """Log every outermost durable write under `root` as (writer, masked path); with `fail_at`,
    raise `exc()` in place of that write. Yields the log (a list, filled while the block runs).

    `raw` names the writes a transaction makes outside the seam: `(module, "os.replace")` swaps
    that module's own `os` reference for one whose `replace` is gated the same way (other modules
    keep the real one); `(owner, "name")` gates a module function or class method that performs
    a descriptor-relative write as one point. A third element, `reported`, is for a writer whose
    contract REPORTS an `OSError` instead of raising it: the injected `OSError` becomes the return
    value `reported(exc)`, as the real failure would."""
    log: list[tuple[str, str]] = []

    def gate(name, args, call, reported):
        if getattr(_depth, "n", 0):
            return call()
        k = len(log)
        log.append((name, _label(name, args, root)))
        if k == fail_at:
            err = exc()
            if reported is not None and isinstance(err, OSError):
                return reported(err)
            raise err
        _depth.n = 1
        try:
            return call()
        finally:
            _depth.n = 0

    def wrap(name, real, reported=None):
        def writer(*args, **kwargs):
            return gate(name, args, lambda: real(*args, **kwargs), reported)
        return writer

    def wrap_marker(name, real):
        def method(self, *args, **kwargs):
            # the marker's own path is not on the call; its leaf name is
            return gate(f"OwnedMarker.{name}", (Path(self.name),),
                        lambda: real(self, *args, **kwargs), lambda e: False)
        return method

    with pytest.MonkeyPatch.context() as mp:
        for name in WRITERS:
            mp.setattr(runtime_fs, name, wrap(name, getattr(runtime_fs, name)))
        for name in MARKER_WRITERS:             # an OSError is reported as False (runtime_fs.py)
            mp.setattr(runtime_fs.OwnedMarker, name,
                       wrap_marker(name, getattr(runtime_fs.OwnedMarker, name)))
        swapped: dict[tuple[object, str], dict] = {}
        for owner, dotted, *reported in raw:
            reported = reported[0] if reported else None
            if "." not in dotted:                   # the owner's own function or method
                mp.setattr(owner, dotted, wrap(f"{owner.__name__.rsplit('.', 1)[-1]}.{dotted}",
                                               getattr(owner, dotted), reported))
                continue
            ref, name = dotted.split(".")
            real = getattr(getattr(owner, ref), name)
            swapped.setdefault((owner, ref), {})[name] = wrap(dotted, real, reported)
        for (module, ref), writers in swapped.items():
            mp.setattr(module, ref, _Module(getattr(module, ref), writers))
        yield log


def run_interrupted(root: Path, operation, *, fail_at: int, exc, raw=()):
    """Run `operation()` with write `fail_at` replaced by `exc()`; returns (log, what it raised or
    returned). The injected failure must have been reached — a point the run never hits is a
    stale pin, not a pass."""
    with durable_writes(root, fail_at=fail_at, exc=exc, raw=raw) as log:
        try:
            outcome = operation()
        except BaseException as e:      # noqa: BLE001 — the injected class itself, KeyboardInterrupt included
            outcome = e
    assert len(log) > fail_at, f"write point {fail_at} not reached: {log}"
    return log, outcome


def tree(root: Path, *, skip=()) -> dict[str, bytes | str]:
    """Every entry under `root` (files by content, dirs and links by kind), minus the relative
    prefixes in `skip` — the state a recovery must return to."""
    out: dict[str, bytes | str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = Path(dirpath, n)
            rel = str(p.relative_to(root))
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            if p.is_symlink():
                out[rel] = "link:" + os.readlink(p)
            elif p.is_dir():
                out[rel] = "dir"
            else:
                out[rel] = p.read_bytes()
    return out

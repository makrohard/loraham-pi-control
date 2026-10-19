"""Browser shims installed BEFORE lhpc is imported. Pyodide lacks a few POSIX modules that
lhpc imports at module load; in a single browser tab their real behaviour is meaningless."""
from __future__ import annotations

import sys
import types
from concurrent.futures import Future


class _InlineExecutor:
    """A thread pool without threads: WASM cannot start one, so `submit` runs the call at once and
    returns its finished Future. The answers are the same; the calls run one after another."""

    def __init__(self, max_workers=None, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, /, *args, **kwargs):
        fut = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:   # as the real pool's work item does
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, **kwargs):
        pass


def install() -> None:
    # fcntl: file locking is a no-op in a single-tab, single-process WASM runtime.
    if "fcntl" not in sys.modules:
        m = types.ModuleType("fcntl")
        m.LOCK_EX, m.LOCK_SH, m.LOCK_UN, m.LOCK_NB = 2, 1, 8, 4
        m.flock = lambda *a, **k: None
        m.lockf = lambda *a, **k: None
        m.fcntl = lambda *a, **k: 0
        m.ioctl = lambda *a, **k: 0
        sys.modules["fcntl"] = m
    # threads: a snapshot probes its checkouts through a thread pool (lhpc.core.status, imported
    # here once fcntl is in place); in the browser that pool runs its calls inline.
    from lhpc.core import status
    status.ThreadPoolExecutor = _InlineExecutor

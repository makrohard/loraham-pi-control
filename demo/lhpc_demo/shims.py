"""Browser shims installed BEFORE lhpc is imported. Pyodide lacks a few POSIX modules that
lhpc imports at module load; in a single browser tab their real behaviour is meaningless."""
from __future__ import annotations

import sys
import types


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

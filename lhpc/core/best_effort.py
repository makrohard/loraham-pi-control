"""Best-effort side actions: a cleanup or diagnostic that runs beside a failure. An ordinary
Exception from it never replaces or hides the original error; a BaseException from the side action
or the logger propagates. One plain function, so no such site lets Ctrl-C skip the rest of its
unwind."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def stderr_line(line: str) -> None:
    sys.stderr.write(line + "\n")


def best_effort(fn: Callable[[], T], *, what: str,
                log: Callable[[str], object] = stderr_line) -> T | None:
    """Run the side action `fn` and return its result. An ordinary `Exception` from it is logged as
    the one line "<what>: <Class>: <msg>" (without ": <msg>" when its __str__ raises an ordinary
    Exception; a BaseException from it propagates) and None is returned; the exception text is
    flattened to one line: runs of whitespace, including newlines, become one space, and leading or
    trailing whitespace is dropped. An ordinary Exception from `log` is ignored (a closed stderr). A
    `BaseException` (KeyboardInterrupt, SystemExit) from `fn` or `log` propagates. Called inside an
    `except` block it leaves the handled exception untouched: a bare `raise` after it re-raises the
    original."""
    try:
        return fn()
    except Exception as exc:
        try:
            line = f"{what}: {type(exc).__name__}: {' '.join(str(exc).split())}"
        except Exception:   # __str__ raised an ordinary Exception (a BaseException propagates)
            line = f"{what}: {type(exc).__name__}"
        try:
            log(line)
        except Exception:                    # a closed stderr
            pass
        return None

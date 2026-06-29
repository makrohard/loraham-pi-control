"""Runtime helper imported by generated manual wrappers (Workstream C).

A generated wrapper must not blindly perform `os.makedirs`/`os.chmod`/`os.symlink`
on paths frozen when the wrapper was written — the filesystem may have changed since
(a leaf or parent may now be a symlink escaping the runtime root). This helper
reconstructs `Paths(runtime_root)` AT EXECUTION TIME and revalidates every mutable
destination with the same safe path API the controller uses, immediately before each
mutation. It fails closed: any containment/no-follow violation raises, so the wrapper
exits non-zero BEFORE exec rather than mutating an unsafe path.
"""

from __future__ import annotations

from pathlib import Path

from . import runtime_fs
from .paths import Paths


def apply_steps(paths: Paths, steps) -> None:
    """THE single execution-time-safe pre-step engine, shared by controller starts and
    generated wrappers (no shell). `steps` are normalized tuples (paths already
    substituted): ("mkdir", path, mode) | ("chmod", path, mode) | ("symlink", src, dst).

    Every mutation is DESCRIPTOR-ANCHORED (runtime_fs): each runtime parent is walked from
    the runtime-root fd with O_DIRECTORY|O_NOFOLLOW and the leaf is mutated relative to the
    held parent fd — never a pathname check-then-mutate. Policy (identical for both
    callers): a symlinked/swapped runtime parent, or a `mkdir`/`chmod` through a symlink
    leaf, or a `symlink` over a REAL directory, fails closed with PathContainmentError. So
    a controller start blocks and a generated wrapper exits non-zero BEFORE exec — the
    revalidation happens at execution time, inside the syscall walk, with no TOCTOU gap."""
    for step in steps:
        kind = step[0]
        if kind == "mkdir":
            path = Path(step[1])
            if step[2]:
                runtime_fs.chmod(paths, path, int(step[2], 8), create_dir=True)
            else:
                runtime_fs.ensure_dir(paths, path)
        elif kind == "chmod":
            runtime_fs.chmod(paths, Path(step[1]), int(step[2], 8))
        elif kind == "symlink":
            # step[1] = link TARGET (may point at a source/runtime read path); step[2] =
            # the runtime-owned destination leaf we create (never followed).
            runtime_fs.replace_symlink(paths, Path(step[2]), step[1])
        else:
            raise ValueError(f"unknown pre-step kind: {kind!r}")


def apply_pre_steps(runtime_root: str, steps) -> None:
    """Wrapper entry point: rebuild `Paths` at execution time and run the shared engine."""
    apply_steps(Paths(runtime_root=Path(runtime_root)), steps)

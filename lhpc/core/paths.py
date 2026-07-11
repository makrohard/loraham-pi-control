"""Centralized, testable path resolution.

The development checkout (this repo) is distinct from the runtime root where
managed stack sources are installed. Nothing here hard-codes a home directory;
the runtime root is `~/loraham-pi-control` by default and overridable via the
`LHPC_RUNTIME_ROOT` environment variable.

This module only resolves paths; `bootstrap` creates the runtime root. When it is
absent, source probes report components as not-installed rather than erroring.

CONTAINMENT: LHPC never reads or writes outside the runtime root. Exactly two
deliberate boundary crossings exist and are allowlisted:
  1. the `~` expansion of the runtime-root setting itself (below) — it DEFINES the
     root, so it is in-root by definition;
  2. CLIENT connects to the external LoRaHAM daemon's own /tmp IPC sockets
     (daemon_control/lifecycle) — the daemon creates and owns those; LHPC performs
     no file operation there.
Everything else — sources, builds, venvs, configs, secrets, logs, markers, the
socat PTY — lives under the runtime root; a configured adopt_search_root must lie
inside it or adoption is refused typed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_RUNTIME_ROOT = "LHPC_RUNTIME_ROOT"
_DEFAULT_RUNTIME_ROOT = "~/loraham-pi-control"


class PathContainmentError(ValueError):
    """A path would resolve outside its designated runtime root."""


@dataclass(frozen=True)
class Paths:
    runtime_root: Path

    @property
    def runtime_root_exists(self) -> bool:
        return self.runtime_root.is_dir()

    def _lexical_under(self, rel: str) -> Path:
        """Lexical containment only (reject absolute / `..`). Used for SOURCE dirs,
        which may legitimately be SYMLINKS to an external checkout (adopt-by-link)."""
        if os.path.isabs(rel):
            raise PathContainmentError(f"absolute path not allowed: {rel!r}")
        target = self.runtime_root / rel
        base = Path(os.path.normpath(str(self.runtime_root)))
        lex = Path(os.path.normpath(str(target)))
        if lex != base and base not in lex.parents:
            raise PathContainmentError(f"path escapes runtime root: {rel!r}")
        return target

    def under(self, *parts: str) -> Path:
        """Resolve a MUTABLE runtime path (logs/config/state/owned records),
        proven to stay inside the runtime root both lexically AND against symlink
        escapes — LHPC must never write through a symlink that leaves the root.
        (Use `resolve_source` for observe-only source dirs, which may be links.)"""
        rel = os.path.join(*parts) if parts else ""
        target = self._lexical_under(rel)
        base_real = Path(os.path.realpath(self.runtime_root))
        real = Path(os.path.realpath(target))
        if real != base_real and base_real not in real.parents:
            raise PathContainmentError(f"path escapes runtime root via symlink: {rel!r}")
        return target

    def contains(self, path: Path) -> bool:
        """True if `path` (an absolute runtime path) stays under the runtime root once
        its PARENT's symlinks are resolved — without following a leaf symlink."""
        base = Path(os.path.realpath(self.runtime_root))
        real = Path(os.path.realpath(path.parent)) / path.name
        return real == base or base in real.parents

    def safe_unlink(self, path: Path) -> None:
        """Delete a runtime-owned leaf safely: contained, and never through a symlink
        leaf OR a swapped parent. A missing file is a no-op; an escaping or symlinked
        target raises. Descriptor-anchored (AUDIT FS2): the parent is walked O_NOFOLLOW
        and the leaf unlinked relative to that fd, so a check-then-unlink TOCTOU where the
        parent dir is swapped to a symlink between validation and the syscall cannot
        redirect the delete outside the root."""
        from . import runtime_fs
        runtime_fs.unlink(self, path)

    def resolve_source(self, relative: str) -> Path:
        """Resolve a manifest `source.path` (runtime-root-relative) to absolute,
        with lexical containment. A source may be a symlink (adopt-by-link); LHPC
        only OBSERVES/reads it and never writes generated files into it."""
        return self._lexical_under(relative)


def resolve_paths(env: dict[str, str] | None = None) -> Paths:
    environ = env if env is not None else os.environ
    raw = environ.get(ENV_RUNTIME_ROOT, _DEFAULT_RUNTIME_ROOT)
    return Paths(runtime_root=Path(raw).expanduser())

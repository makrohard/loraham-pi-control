"""Centralized, testable path resolution.

The development checkout (this repo) is distinct from the runtime root where
managed stack sources are installed. Nothing here hard-codes a home directory;
the runtime root is `~/loraham-pi-control` by default and overridable via the
`LHPC_RUNTIME_ROOT` environment variable.

This module only resolves paths; `bootstrap` creates the runtime root. When it is
absent, source probes report components as not-installed rather than erroring.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_RUNTIME_ROOT = "LHPC_RUNTIME_ROOT"
_DEFAULT_RUNTIME_ROOT = "~/loraham-pi-control"


@dataclass(frozen=True)
class Paths:
    runtime_root: Path

    @property
    def runtime_root_exists(self) -> bool:
        return self.runtime_root.is_dir()

    def resolve_source(self, relative: str) -> Path:
        """Resolve a manifest `source.path` (runtime-root-relative) to absolute."""
        return self.runtime_root / relative


def resolve_paths(env: dict[str, str] | None = None) -> Paths:
    environ = env if env is not None else os.environ
    raw = environ.get(ENV_RUNTIME_ROOT, _DEFAULT_RUNTIME_ROOT)
    return Paths(runtime_root=Path(raw).expanduser())

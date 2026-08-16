"""The LHPC_SYSTEM_PROVIDER factory for the demo. lhpc's ControllerService calls
`build(paths)` and uses the returned .system/.manifest_path/.wrap_spawn. Set
LHPC_SYSTEM_PROVIDER=lhpc_demo.provider:build in the Pyodide bootstrap."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemProvider:
    system: object
    manifest_path: object
    wrap_spawn: object


def build(paths):
    from .system import build_demo_system
    return SystemProvider(system=build_demo_system(paths),
                          manifest_path=None, wrap_spawn=None)

"""The lhpc extension point. lhpc's `ControllerService`, when the caller injected no
`system`/`manifest_path`, reads `LHPC_SYSTEM_PROVIDER` (module:factory) and calls the
factory with its Paths. The devcontainer sets
`LHPC_SYSTEM_PROVIDER=lhpc_testlab.provider:build`, so every lhpc process (CLI, web,
detached helpers) picks up the lab defaults — with no testlab reference anywhere in lhpc.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import active


@dataclass(frozen=True)
class SystemProvider:
    system: object                 # probes System with the lab runner/fs, or None
    manifest_path: Path | None     # generated overlay, or None
    wrap_spawn: object             # wrap_spawn(real_spawn) -> guarded spawn, or None


def build(paths) -> SystemProvider | None:
    """Return the lab defaults, or None when the latch does NOT hold (lhpc then uses the real
    system, byte-for-byte — a production process is never affected).

    FAIL CLOSED once the latch IS engaged: any failure building the lab system PROPAGATES so
    lhpc's _load_system_provider refuses to construct rather than silently dropping to the real
    command runner while the operator believes the box is sandboxed. Swallowing the exception
    here (returning None) was exactly the test-lab-fails-open finding — the real system ran
    shutdown/nft/apt under a 'SIMULATED HARDWARE' banner."""
    if not active(paths):
        return None
    from .spawn import make_wrap_spawn
    from .system import build_lab_system
    overlay = paths.under("state", "testlab", "manifest.toml")
    return SystemProvider(system=build_lab_system(paths),
                          manifest_path=overlay if overlay.exists() else None,
                          wrap_spawn=make_wrap_spawn(paths))

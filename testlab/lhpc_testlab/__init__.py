"""LHPC test lab — a SEPARATE, downloadable package that drives the real LHPC
(web console, CLI, stack lifecycle) against deterministic fake hardware/OS backends.
It depends on `lhpc` but is NOT part of it: nothing here ships in the lhpc package or
the Pi image.

Activation is a TWO-KEY latch checked by `active()`: `LHPC_TESTLAB=1` in the environment
(per-process operator intent — a production unit never carries it) AND the
`state/testlab/enabled` marker (binds the ROOT as a lab root). `lhpc-testlab init` is the
only op allowed on the env key alone.

lhpc discovers this package through its ONE generic extension point: the
`LHPC_SYSTEM_PROVIDER` environment variable, which the devcontainer sets to
`lhpc_testlab.provider:build`. `provider.build(paths)` returns the SystemProvider lhpc
falls back to when the caller injected nothing — the LabSystem, the generated manifest
overlay, and the spawn guard. lhpc contains no reference to "testlab".
"""
from __future__ import annotations

import os
from pathlib import Path

from . import scenarios, supervisor  # noqa: F401  (re-exported for ops/provider)

ENV_KEY = "LHPC_TESTLAB"
BOOT_FILE_ENV = "LHPC_BOOT_ID_FILE"


def env_enabled() -> bool:
    return os.environ.get(ENV_KEY) == "1"


def marker_path(paths) -> Path:
    return paths.under("state", "testlab", "enabled")


def state_dir(paths) -> Path:
    return paths.under("state", "testlab")


def active(paths) -> bool:
    """The two-key latch. Env first — the common (production) case never stats."""
    if not env_enabled():
        return False
    try:
        return marker_path(paths).exists()
    except OSError:
        return False


def data_path(*parts) -> Path:
    """A path inside this package's bundled data (fake daemon, shims, sim yaml)."""
    return Path(__file__).parent / "data" / Path(*parts)

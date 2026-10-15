"""The `Installer` the install suites build (the `installer` fixture of conftest.py), importable,
so a test can build the very same one in a FRESH interpreter:

    python tests/install/installers.py kill-mid-staging <runtime root>

stages the `app` source and is SIGKILLed right after its copy into the candidate, before any
journal exists (a box losing power mid-clone). A fresh interpreter, not `os.fork()`: under
pytest-xdist the worker process has threads, and a forked child of a multi-threaded process can
deadlock on a lock another thread held at the fork.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealSystem


def app_component():
    """The plain managed-source component the install suites adopt: `app`, sourced from
    `src/app`, with a local fallback directory `app` under the adopt search root."""
    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app"))


def build_installer(root, comp=None, *, extra=(), search_root=None, system=None, values=None):
    root = Path(root)
    comp = comp or app_component()
    cfg = Config(values={"install": {"adopt_search_root": str(search_root or root / "local")},
                         **(values or {})})
    stacks = (Stack(id="s", name="s", main=comp.id, components=(comp, *extra)),)
    return Installer(Paths(runtime_root=root), stacks, cfg, system or RealSystem())


def _kill_mid_staging(root: Path) -> None:
    real_copy = Installer._copy_into_candidate

    def copy_then_die(local, cand):           # the one seam inside the staging
        real_copy(local, cand)
        os.kill(os.getpid(), signal.SIGKILL)
    Installer._copy_into_candidate = staticmethod(copy_then_die)
    build_installer(root).adopt_source(app_component(), source="dev")


if __name__ == "__main__":
    if sys.argv[1:2] != ["kill-mid-staging"] or len(sys.argv) != 3:
        sys.exit("usage: installers.py kill-mid-staging <runtime root>")
    _kill_mid_staging(Path(sys.argv[2]))
    sys.exit(1)                               # the staging returned: it was not killed

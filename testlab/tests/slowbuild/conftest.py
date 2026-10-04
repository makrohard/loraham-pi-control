"""Slow-target build lane (row C, docs/maintenance.md): every operation on the update path, run
under the production limits inside a CPU- and memory-throttled container, measured and checked
against the slow-target budget.

Opt-in (`LHPC_SLOW_BUILD=1`): it installs and builds every stack a release may move on a
deliberately slow box. The CI job `slow-build` runs it; a skip there is not a pass.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from lhpc_testlab.testing import LabServer, lab_env


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LHPC_SLOW_BUILD") == "1":
        return
    skip = pytest.mark.skip(reason="slow-build lane is opt-in: set LHPC_SLOW_BUILD=1")
    for item in items:
        if str(item.fspath).replace("\\", "/").find("tests/slowbuild") != -1:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    """A FRESH lab root, so `pinned` resolves to the candidate manifest pin."""
    root = Path(tmp_path_factory.mktemp("slowbuild") / "runtime")
    server = LabServer(root)
    server.init_and_reset()
    return server


@pytest.fixture(scope="session")
def env(lab):
    return lab_env(lab.root)


@pytest.fixture(scope="session")
def svc(lab):
    """An in-process ControllerService over the SAME lab root: reads the manifest and the
    graywolf upstream state. Every build and install goes through the real executable."""
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")
        mp.setenv("LHPC_TESTLAB", "1")
        yield ControllerService(paths=Paths(runtime_root=lab.root))

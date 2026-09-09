"""Release lane: every stack this release may repin is installed at the SELECTED commit,
built, started and verified on a fresh lab root — then its installed identity is proved
against the candidate manifest.

Opt-in (`LHPC_RELEASE_VERIFY=1`), because it installs and builds every stack. The lane exists
so an automated pin release has evidence that the pins it moves actually run; the box test
matrix stays the minor release's proof.

Case names are the contract: `test_release_<stack>[_<variant>]`. A release automation maps a
moved component to its stack through the manifest and requires that stack's case to have
PASSED — a skip is not proof.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from lhpc_testlab.testing import LabServer, lab_env, run_lhpc


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LHPC_RELEASE_VERIFY") == "1":
        return
    skip = pytest.mark.skip(reason="release lane is opt-in: set LHPC_RELEASE_VERIFY=1")
    for item in items:
        if str(item.fspath).replace("\\", "/").find("tests/release") != -1:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    """A FRESH lab root: no operator known-working records, so `pinned` resolves to the
    candidate manifest pin and nothing carries over from a previous run."""
    root = Path(tmp_path_factory.mktemp("release") / "runtime")
    server = LabServer(root)
    server.init_and_reset()
    yield server
    for sid in ("meshcom", "meshtastic", "reticulum", "meshcore", "graywolf", "kiss",
                "daemon"):
        run_lhpc(server.env, "stack", "stop", sid, "--yes", timeout=300)


@pytest.fixture(scope="session")
def env(lab):
    return lab_env(lab.root)


@pytest.fixture(scope="session")
def svc(lab):
    """An in-process ControllerService over the SAME lab root — used to read the manifest,
    render an interactive component's own start command and run the production identity
    verifiers. Never to mutate: every lifecycle step goes through the real executable."""
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService

    os.environ["LHPC_SYSTEM_PROVIDER"] = "lhpc_testlab.provider:build"
    os.environ["LHPC_TESTLAB"] = "1"
    return ControllerService(paths=Paths(runtime_root=lab.root))

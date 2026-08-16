"""Acceptance lane: REAL `lhpc` executable + REAL waitress server over a lab root.
Env-gated — without LHPC_ACCEPTANCE=1 every test here skips (the default unit lane and
existing CI stay byte-identical). Session-scoped server; per-test scenario reset through
the real executable."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.append(os.path.dirname(__file__))   # labproc/httpc live beside us; APPEND —
# prepending would shadow tests/conftest.py for the legacy `from conftest import …` uses
from httpc import Client
from labproc import LabServer, run_lab


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LHPC_ACCEPTANCE") == "1":
        return
    skip = pytest.mark.skip(reason="acceptance lane is opt-in: set LHPC_ACCEPTANCE=1")
    for item in items:
        if str(item.fspath).replace("\\", "/").find("tests/acceptance") != -1:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    root = tmp_path_factory.mktemp("labroot") / "runtime"
    server = LabServer(root)
    server.init_and_reset()
    server.start()
    yield server
    server.stop()


@pytest.fixture()
def client(lab):
    return Client(lab.base)


@pytest.fixture(autouse=True)
def _fresh_scenario(request, lab):
    """Deterministic baseline per test: healthy scenario via the REAL executable.
    (Full `testlab reset` per test would re-install — scenario reset is the cheap,
    sufficient baseline; tests that mutate installs say so and clean up.)"""
    if os.environ.get("LHPC_ACCEPTANCE") != "1":
        yield
        return
    run_lab(lab.env, "scenario", "healthy", check=True)
    yield

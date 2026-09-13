"""Acceptance lane: REAL `lhpc` executable + REAL waitress server over a lab root.
Env-gated — without LHPC_ACCEPTANCE=1 every test here skips (the default unit lane and
existing CI stay byte-identical). Session-scoped server; per-test scenario reset through
the real executable."""
from __future__ import annotations

import os

import pytest
from lhpc_testlab.http_client import Client
from lhpc_testlab.testing import LabServer, run_lab


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


@pytest.fixture(scope="session")
def app_rules(tmp_path_factory):
    """The real app's url_map, built ONCE in process purely to ENUMERATE routes — every request
    the sweeps make goes to the running server. One builder, so the GET and POST sweeps can never
    disagree about what the surface is. The scratch runtime root is pytest's, and LHPC_TESTLAB is
    withheld for the build only, so this in-process service never latches onto a lab root."""
    from lhpc.adapters.web.app import create_app
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    root = tmp_path_factory.mktemp("urlmap")
    (root / "config" / "stacks").mkdir(parents=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("LHPC_TESTLAB", raising=False)
        svc = ControllerService(system=FakeSystem(files={"/proc/uptime": "1 2\n"}).system,
                                paths=Paths(runtime_root=root))
        app = create_app(lambda: svc)
    return [r for r in app.url_map.iter_rules() if r.endpoint != "static"]


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

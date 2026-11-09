"""Test-lab suite fixtures: make ControllerService pick up the lab provider (the same env
the devcontainer sets) for the whole session."""

import re

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: slow test (builds/containers)")


@pytest.fixture(autouse=True)
def _lab_provider(monkeypatch):
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")


@pytest.fixture(autouse=True)
def _lab_fakes_stop_with_the_test(request):
    """W389: whatever lab root a test built under its tmp_path (the root itself or one or two levels down), the fakes
    its init/reset/reboot spawned (the fake gpsd, the APRS-IS sink, detached) stop when the test ends; a simulated
    reboot respawns the fake gpsd, so a test without a LabServer to stop left one behind on every run."""
    tmp = request.getfixturevalue("tmp_path") if "tmp_path" in request.fixturenames else None
    yield
    if tmp is None:
        return
    from lhpc_testlab.testing import stop_lab_fakes
    for marker in (*tmp.glob("state/testlab"), *tmp.glob("*/state/testlab"), *tmp.glob("*/*/state/testlab")):
        stop_lab_fakes(marker.parent.parent)


@pytest.fixture(scope="session")
def lab_banner():
    """The lab banner as an element, not a sentence: the one element the lab overlay injects
    into every page, identified by the class its own stylesheet addresses."""
    return re.compile(r'class="[^"]*\blab-banner\b')

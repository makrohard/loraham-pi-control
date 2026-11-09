"""Test-lab suite fixtures: make ControllerService pick up the lab provider (the same env
the devcontainer sets) for the whole session."""

import re

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: slow test (builds/containers)")
    config.addinivalue_line("markers", "lab_serial: the test resets a lab or runs a fake daemon: one such test at a time "
                                       "on the host (testlab/tests/conftest.py _lab_serial)")


@pytest.fixture(autouse=True)
def _lab_serial(request):
    """G1-LF1, DEC-W384x: the product reads ANY `loraham_daemon` process on the host as a lab root's running daemon (the
    run state matches the exec name), so a lab reset or a LabServer beside a fake-daemon test of another worker, run or
    lane aborts "could not stop: daemon". A `lab_serial` test holds the host's lab lock (testing._hold_lab_lock, the one
    a LabServer holds) for its run: such tests and labs take turns across xdist workers, runs and lanes on this host;
    every other test stays parallel. A pytest the holding test runs itself (an ancestor holds it) does not wait."""
    import os

    from lhpc_testlab import testing
    if request.node.get_closest_marker("lab_serial") is None or testing.serial_held_by_ancestor():
        yield                                          # not one of them, or a pytest run by the holder itself
        return
    held = testing._hold_lab_lock(f"test {request.node.nodeid}", wait_s=1800.0)   # waits out a lab's lifetime
    os.environ["LHPC_LAB_SERIAL_HELD"] = str(os.getpid())
    try:
        yield
    finally:
        os.environ.pop("LHPC_LAB_SERIAL_HELD", None)
        testing._release_lab_lock(held)


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

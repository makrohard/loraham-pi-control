"""Test-lab suite fixtures: make ControllerService pick up the lab provider (the same env
the devcontainer sets) for the whole session."""

import re

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: slow test (builds/containers)")
    config.addinivalue_line("markers", "lab_serial: the test resets a lab or runs a fake daemon: one such test at a time "
                                       "on the host (testlab/tests/conftest.py _lab_serial)")


LAB_SERIAL_LOCK = "/tmp/lhpc-testlab-serial.lock"


@pytest.fixture(autouse=True)
def _lab_serial(request):
    """G1-LF1, DEC-W384x: the product reads ANY `loraham_daemon` process on the host as a lab root's running daemon (the
    run state matches the exec name), so a lab reset beside a fake-daemon test of a parallel worker aborts "could not
    stop: daemon". A `lab_serial` test waits for this host-wide lock: such tests run one at a time, across xdist workers
    and other runs on this host, in any --dist mode; every other test stays parallel."""
    import os
    if request.node.get_closest_marker("lab_serial") is None or os.environ.get("LHPC_LAB_SERIAL_HELD"):
        yield                                          # not one of them, or a pytest run by the holder itself
        return
    import fcntl
    fd = os.open(LAB_SERIAL_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)                 # blocks while another lab_serial test runs
        os.environ["LHPC_LAB_SERIAL_HELD"] = str(os.getpid())
        yield
    finally:
        os.environ.pop("LHPC_LAB_SERIAL_HELD", None)
        os.close(fd)                                   # closing releases the lock


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

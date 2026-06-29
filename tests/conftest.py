"""Shared test fixtures."""

from __future__ import annotations

import subprocess

import pytest

from lhpc.core.services import ControllerService
from lhpc.core.lifecycle import Lifecycle

# Real-but-harmless spawn shim: ownership recording now requires a COMPLETE /proc
# identity, so tests that "start" something must spawn a real process (a detached
# `sleep`) rather than a fake pid. All spawned sleepers are reaped at session end.
_SPAWNED: list = []


def real_spawn(argv, log, cwd=None, env=None):
    """A `spawn` callable for Lifecycle that launches a real detached `sleep` (its own
    session, so it is an LHPC-ownable session leader) and returns its pid. The log path
    is created so callers that read it work."""
    try:
        open(str(log), "a").close()
    except OSError:
        pass
    p = subprocess.Popen(["sleep", "300"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _SPAWNED.append(p)
    return p.pid


@pytest.fixture(autouse=True)
def _reap_real_spawns():
    yield
    while _SPAWNED:
        p = _SPAWNED.pop()
        try:
            p.kill(); p.wait(timeout=2)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _no_daemon_verify_wait(monkeypatch):
    """The daemon-start CONF-socket verification waits seconds in production; the
    FakeSystem never simulates the daemon coming up, so disable the wait in tests
    (the start path is still exercised; it just reports success immediately).

    Also disable the post-launch identity-observation wait by default: most tests
    inject a fake spawn whose pid is not a real /proc process. Tests that exercise
    the real ownership/identity path set OBSERVE_TIMEOUT_S explicitly."""
    monkeypatch.setattr(ControllerService, "DAEMON_VERIFY_TIMEOUT_S", 0.0)
    monkeypatch.setattr(ControllerService, "ENDPOINT_VERIFY_TIMEOUT_S", 0.0)
    monkeypatch.setattr(Lifecycle, "OBSERVE_TIMEOUT_S", 0.0)

"""Shared test fixtures."""

from __future__ import annotations

import pytest

from lhpc.core.services import ControllerService


@pytest.fixture(autouse=True)
def _no_daemon_verify_wait(monkeypatch):
    """The daemon-start CONF-socket verification waits seconds in production; the
    FakeSystem never simulates the daemon coming up, so disable the wait in tests
    (the start path is still exercised; it just reports success immediately)."""
    monkeypatch.setattr(ControllerService, "DAEMON_VERIFY_TIMEOUT_S", 0.0)

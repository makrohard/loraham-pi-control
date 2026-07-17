"""Shared test fixtures."""

from __future__ import annotations

import os
import subprocess

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_session: requires a real POSIX session (sid>0) — the product's "
                   "procident.identity_complete refuses sid==0, so these tests fail in a sandbox "
                   "whose processes have session id 0 (run them under `setsid`).")
    config.addinivalue_line(
        "markers", "needs_nonroot: requires a non-root euid — a chmod-based permission fixture "
                   "does not bind for root.")
    config.addinivalue_line(
        "markers", "no_default_hardware: opt OUT of the test-baseline hardware setup so the test sees "
                   "the true fresh-install default (no radio hardware configured).")


def pytest_collection_modifyitems(config, items):
    # Skip (with a reason) in degenerate environments so the product's CORRECT strictness
    # (sid>0 identity, non-root perm fixtures) is not misread as a code failure. Never fires on a
    # normal desktop or the Raspberry Pi target (sid>0, non-root).
    no_session = os.getsid(0) == 0
    is_root = os.geteuid() == 0
    for it in items:
        if no_session and it.get_closest_marker("needs_session"):
            it.add_marker(pytest.mark.skip(
                reason="no real POSIX session (sid==0); run under `setsid` (identity_complete needs sid>0)"))
        if is_root and it.get_closest_marker("needs_nonroot"):
            it.add_marker(pytest.mark.skip(
                reason="running as root; the chmod permission fixture does not bind for root"))

from lhpc.core.services import ControllerService
from lhpc.core.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def _default_hardware(request, monkeypatch):
    """A fresh install has NO radio hardware configured (the daemon refuses to start), but nearly every
    test exercises a working box. So the test BASELINE defaults to the LoRaHAM dual-radio setup — i.e.
    a `[radio]`-less runtime resolves to 'loraham' — mirroring a deployed unit. Tests about the
    unconfigured state opt out with @pytest.mark.no_default_hardware (then the true 'unset' default
    applies). A test that writes its own `[radio].hardware` overrides this either way."""
    if request.node.get_closest_marker("no_default_hardware"):
        return
    from lhpc.core import config as _config
    monkeypatch.setattr(_config, "HW_DEFAULT", "loraham", raising=False)


def set_call(svc, callsign="DJ0CHE"):
    """Configure a valid operator callsign so a LICENSED stack (chat/igate/voice/meshcom) passes
    CALL-enforcement — the realistic precondition for starting one. Returns the service."""
    from lhpc.core.config import save_operator_config
    save_operator_config(svc._paths, callsign)
    svc._invalidate_config()
    return svc

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

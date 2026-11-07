"""G1-C14: a signal-0 probe that fails with anything but ESRCH (gone) or EPERM (alive, someone else's) reads as
"unknown", never as gone, so a process group whose leader cannot be probed is never taken as ours to signal through its
members (proctree.terminate_session) nor reported as ended (session_ceased)."""
import errno
import os

import pytest

from lhpc.core import procident, proctree


def _kill_raising(exc):
    def kill(pid, sig):
        raise exc
    return kill


@pytest.mark.parametrize("exc, want", [
    (ProcessLookupError(errno.ESRCH, "no such process"), "gone"),
    (PermissionError(errno.EPERM, "not permitted"), "denied"),
    (OSError(errno.EIO, "i/o error"), "unknown"),
    (OSError(errno.EINVAL, "invalid"), "unknown"),
], ids=["esrch", "eperm", "eio", "einval"])
def test_signal0_names_only_esrch_gone(monkeypatch, exc, want):
    monkeypatch.setattr(os, "kill", _kill_raising(exc))
    assert procident.signal0_state(4242) == want


@pytest.fixture
def unprobeable_leader(monkeypatch):
    """A process group whose leader is not captured and whose signal-0 probe fails with EIO; one member remains."""
    monkeypatch.setattr(os, "kill", _kill_raising(OSError(errno.EIO, "i/o error")))
    monkeypatch.setattr(proctree, "capture_session_token", lambda pid: None)
    monkeypatch.setattr(proctree, "session_members", lambda sid, exclude: [4343])
    signalled = []
    monkeypatch.setattr(proctree, "_signal_session_groups", lambda token, exclude, sig: signalled.append(sig))
    return proctree.SessionToken(4242, 1, 4242, 4242), signalled


def test_an_unprobeable_leader_is_never_signalled_through_its_members(unprobeable_leader):
    token, signalled = unprobeable_leader
    assert proctree.terminate_session(token, 0, term_grace=0, kill_grace=0) is proctree.Termination.UNVERIFIED
    assert signalled == []


def test_an_unprobeable_leader_never_reads_as_an_ended_session(unprobeable_leader, monkeypatch):
    token, _signalled = unprobeable_leader
    monkeypatch.setattr(proctree, "session_members", lambda sid, exclude: [])
    assert proctree.session_ceased(token, 0) is False

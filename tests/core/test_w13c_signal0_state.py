"""The class of the item-13 fuzz finding (rule 6): a pid read from a file or another process's record that the
kernel cannot hold must read "no such process", never raise out of a status read.

`procident.signal0_state` is the one signal-0 probe for such pids: "alive", "denied" (someone else's) or "gone";
OverflowError and ValueError (2**31 and above) are "gone". `pid_exists_signal0`, proctree's ownership check
(a SessionToken's pid) and the nginx master check (webserver, its pidfile) go through it.
"""
from __future__ import annotations

import os

import pytest

from lhpc.core import procident

HUGE = [2**31, 2**63, 10**30]


@pytest.mark.parametrize("pid", HUGE, ids=["2^31", "2^63", "10^30"])
def test_r1_a_pid_the_kernel_cannot_hold_is_gone(pid):
    """RED before for pid_exists_signal0: OverflowError."""
    assert procident.pid_exists_signal0(pid) is False
    assert procident.signal0_state(pid) == "gone"


@pytest.mark.parametrize("exc,want", [(OverflowError, "gone"), (ValueError, "gone"), (ProcessLookupError, "gone"),
                                      (PermissionError, "denied"), (OSError, "gone")],
                         ids=["overflow", "value", "lookup", "permission", "oserror"])
def test_r2_each_error_kind_of_the_probe(monkeypatch, exc, want):
    def kill(pid, sig):
        raise exc("x")
    monkeypatch.setattr(procident.os, "kill", kill)
    assert procident.signal0_state(4242) == want
    assert procident.pid_exists_signal0(4242) is (want != "gone")


@pytest.mark.parametrize("pid", [0, -1, True, "12", None, 1.5], ids=["0", "-1", "True", "str", "None", "float"])
def test_r3_control_a_pid_that_is_not_a_positive_int_is_never_signalled(monkeypatch, pid):
    def kill(pid, sig):
        raise AssertionError("signalled")
    monkeypatch.setattr(procident.os, "kill", kill)
    assert procident.signal0_state(pid) == "gone"


def test_r4_control_this_process_is_alive():
    assert procident.signal0_state(os.getpid()) == "alive" and procident.pid_exists_signal0(os.getpid())


@pytest.mark.parametrize("pid", HUGE, ids=["2^31", "2^63", "10^30"])
def test_r5_a_session_token_with_an_impossible_pid_is_ceased_not_an_exception(pid):
    """RED before: OverflowError out of the ownership check (a token rebuilt from a stored record)."""
    from lhpc.core import proctree
    token = proctree.SessionToken(pid=pid, starttime=1, sid=pid, pgid=pid)
    assert proctree._ownership_state(token, exclude_pid=os.getpid()) == "ceased"


@pytest.mark.parametrize("text", ["2147483648", "99999999999999999999"], ids=["2^31", "20-digits"])
def test_r6_an_nginx_pidfile_with_an_impossible_pid_reads_not_running(tmp_path, text):
    """RED before: OverflowError out of nginx_master_active."""
    from lhpc.core import webserver
    from lhpc.core.paths import Paths
    p = Paths(runtime_root=tmp_path)
    pidfile = p.under(*webserver.NGINX_PID)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(text + "\n")
    assert webserver.nginx_master_active(p) is False


def _denied(monkeypatch):
    def kill(pid, sig):
        raise PermissionError("not yours")
    monkeypatch.setattr(procident.os, "kill", kill)


def test_r7_control_a_leader_pid_that_exists_but_is_not_ours_is_unverified(monkeypatch):
    """Someone else's live process on the token's pid: recycled, never signalled (as before)."""
    from lhpc.core import proctree
    _denied(monkeypatch)
    monkeypatch.setattr(proctree, "capture_session_token", lambda pid: None)   # not our self-led leader
    token = proctree.SessionToken(pid=4242, starttime=1, sid=4242, pgid=4242)
    assert proctree._ownership_state(token, exclude_pid=os.getpid()) == "unverified"


def test_r8_control_an_nginx_pid_that_is_not_ours_reads_not_running(tmp_path, monkeypatch):
    """As before: only a live pid this process may signal counts as LHPC's nginx master."""
    from lhpc.core import webserver
    from lhpc.core.paths import Paths
    p = Paths(runtime_root=tmp_path)
    pidfile = p.under(*webserver.NGINX_PID)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text("4242\n")
    _denied(monkeypatch)
    assert webserver.nginx_master_active(p) is False

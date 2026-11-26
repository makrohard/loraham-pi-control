"""C1 (PLAN-C1-STOP-v2): after a verified stop's main process ended, the members of its OWN process group that are still
running get a grace, then SIGKILL through a pidfd, only from a fresh scan that holds an anchor (a member born before the
stop's SIGTERM, still the same process). Scans and kills are stubbed here, except where a real process proves the
pidfd step, the grace after a slow leader and a foreign session."""
import dataclasses
import os
import select
import signal
import subprocess
import sys

import pytest

from lhpc.core.config import Config
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import Component, ComponentKind, ProcessSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

N = 4000
T_BOUND = 1000
STACK = Stack(id="daemon", name="d", main="loraham-daemon")
COMP = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE, process=ProcessSpec(exec_name="python3"))
REC = {"pid": N, "pgid": N, "sid": N}


def _life(tmp_path):
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system, spawn=lambda argv, log: None)


def _m(pid, start):
    return {"pid": pid, "starttime": start}


class World:
    """Stubbed scans (one list per `_group_members` call), grace/wait answers, identities and kills."""

    def __init__(self, life, monkeypatch, scans, waits, idents=None, kill=True):
        self.scans, self.waits, self.killed, self.grace = list(scans), list(waits), [], []
        self.idents = idents if idents is not None else {}
        monkeypatch.setattr(life, "_group_members", lambda pg, sid: self.scans.pop(0))
        monkeypatch.setattr(life, "_await_group_ceased", self._wait)
        monkeypatch.setattr(life, "_stat_ident", lambda pid: self.idents.get(pid, (N, N, self._start(pid))))
        monkeypatch.setattr(life, "_pidfd_kill", lambda m, pg, sid: self._kill(m, kill))
        monkeypatch.setattr(life, "_boot_ticks", lambda: 2000)
        self.starts = {}

    def _start(self, pid):
        return self.starts.get(pid, 0)

    def _wait(self, pg, sid, seconds, sleep):
        self.grace.append(seconds)
        return self.waits.pop(0)

    def _kill(self, m, kill):
        if kill is None:
            return None
        self.killed.append(m["pid"])
        return True


def _world(life, monkeypatch, scans, waits, **kw):
    w = World(life, monkeypatch, scans, waits, **kw)
    for scan in scans:
        for m in scan or []:
            w.starts[m["pid"]] = m["starttime"]
    return w


def test_no_member_left_sends_nothing(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[]], [])
    assert life._end_group_survivors(REC, COMP, T_BOUND) == ("none", [], [], "")
    assert w.killed == [] and w.grace == []


def test_a_member_that_ends_within_the_grace_is_never_killed(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4001, 900)]], [True])
    assert life._end_group_survivors(REC, COMP, T_BOUND)[0] == "none"
    assert w.killed == [] and w.grace == [Lifecycle.GROUP_GRACE_S]


def test_the_components_stop_group_grace_is_the_wait_used(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4001, 900)]], [True])
    life._end_group_survivors(REC, dataclasses.replace(COMP, stop_group_grace=7.0), T_BOUND)
    assert w.grace == [7.0]


def test_survivors_of_the_grace_are_killed_from_a_fresh_scan_with_the_child_forked_in_the_grace(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4001, 900)], [_m(4001, 900), _m(4002, 1500)]], [False, True])
    assert life._end_group_survivors(REC, COMP, T_BOUND) == ("killed", [4001, 4002], [], "")
    assert w.grace == [Lifecycle.GROUP_GRACE_S, Lifecycle.GROUP_KILL_WAIT_S]


def test_no_member_born_before_the_sigterm_means_no_signal(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4002, 1500)], [_m(4002, 1500)]], [False])
    status, killed, left, why = life._end_group_survivors(REC, COMP, T_BOUND)
    assert (status, killed, left) == ("left", [], [4002]) and "predates" in why
    assert w.killed == []


def test_an_anchor_born_in_the_sigterms_tick_is_no_anchor(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4001, T_BOUND)], [_m(4001, T_BOUND)]], [False])
    assert life._end_group_survivors(REC, COMP, T_BOUND)[0] == "left" and w.killed == []


def test_an_anchor_that_is_no_longer_the_scanned_process_is_no_anchor(tmp_path, monkeypatch):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4001, 900)], [_m(4001, 900)]], [False], idents={4001: (N, N, 1999)})
    assert life._end_group_survivors(REC, COMP, T_BOUND)[0] == "left" and w.killed == []


def test_a_later_round_needs_its_own_anchor(tmp_path, monkeypatch):
    # round 1 kills the anchor; round 2 sees only a child born after round 1's bound (2000): never signalled
    life = _life(tmp_path)
    w = _world(life, monkeypatch, [[_m(4001, 900)], [_m(4001, 900)], [_m(4003, 2500)]], [False, False])
    status, killed, left, _ = life._end_group_survivors(REC, COMP, T_BOUND)
    assert (status, killed, left) == ("left", [4001], [4003]) and w.killed == [4001]


def test_no_pidfd_fails_closed(tmp_path, monkeypatch):
    life = _life(tmp_path)
    _world(life, monkeypatch, [[_m(4001, 900)], [_m(4001, 900)]], [False], kill=None)
    status, killed, left, why = life._end_group_survivors(REC, COMP, T_BOUND)
    assert (status, killed, left) == ("left", [], [4001]) and "pidfd" in why


@pytest.mark.parametrize("scans,waits", [([None], []), ([[_m(4001, 900)], None], [False])],
                         ids=["first_scan", "fresh_scan_after_the_grace"])
def test_an_unlistable_group_is_unverified(tmp_path, monkeypatch, scans, waits):
    life = _life(tmp_path)
    w = _world(life, monkeypatch, scans, waits)
    assert life._end_group_survivors(REC, COMP, T_BOUND)[0] == "unverified" and w.killed == []


def test_a_survivor_that_outlives_the_sigkill_rounds_is_left(tmp_path, monkeypatch):
    # D state: SIGKILL pending, the member stays in every scan; bounded by GROUP_KILL_ROUNDS
    life = _life(tmp_path)
    rounds = Lifecycle.GROUP_KILL_ROUNDS
    w = _world(life, monkeypatch, [[_m(4001, 900)]] * (rounds + 2), [False] * (rounds + 1))
    status, _killed, left, why = life._end_group_survivors(REC, COMP, T_BOUND)
    assert (status, left) == ("left", [4001]) and "after SIGKILL" in why and len(w.killed) == rounds


# ---- real processes -------------------------------------------------------------------------------------------------

def _session(argv):
    p = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p


def _gone(pid, bound=5.0):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        select.select([fd], [], [], bound)
    finally:
        os.close(fd)
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1][0] == "Z"
    except OSError:
        return True


@pytest.mark.wallclock("a real sleeping process is signalled through its pidfd")
def test_the_pidfd_kill_signals_only_the_scanned_process(tmp_path, monkeypatch):
    life = _life(tmp_path)
    p = _session(["sleep", "30"])
    try:
        pgid, sid, start = Lifecycle._stat_ident(p.pid)
        assert life._pidfd_kill(_m(p.pid, start + 1), pgid, sid) is False      # another starttime: not the scanned one
        assert p.poll() is None
        assert life._pidfd_kill(_m(p.pid, start), pgid, sid) is True
        assert p.wait(timeout=5) == -9
        assert life._pidfd_kill(_m(p.pid, start), pgid, sid) in (False, True)   # a zombie: never another process
    finally:
        p.kill()
        p.wait(timeout=5)


@pytest.mark.wallclock("a real sleeping process; its identity changes after the pidfd open")
def test_an_identity_that_changes_after_the_pidfd_open_is_never_signalled(tmp_path, monkeypatch):
    life = _life(tmp_path)
    p = _session(["sleep", "30"])
    try:
        pgid, sid, start = Lifecycle._stat_ident(p.pid)
        monkeypatch.setattr(life, "_stat_ident", lambda pid: (pgid, sid, start + 7))   # read AFTER the open
        assert life._pidfd_kill(_m(p.pid, start), pgid, sid) is False and p.poll() is None
    finally:
        p.kill()
        p.wait(timeout=5)


def test_no_pidfd_on_the_system_signals_nothing(tmp_path, monkeypatch):
    life = _life(tmp_path)

    def nosys(pid):
        raise OSError(38, "Function not implemented")
    monkeypatch.setattr(os, "pidfd_open", nosys)
    assert life._pidfd_kill(_m(1, 0), 1, 1) is None


def test_an_ended_member_is_no_kill(tmp_path):
    life = _life(tmp_path)
    p = subprocess.Popen(["true"])
    p.wait(timeout=5)
    assert life._pidfd_kill(_m(p.pid, 0), p.pid, p.pid) is False


SLOW = r"""
import os, signal, sys, time
def child_term(s, f):
    time.sleep(float(sys.argv[2])); os._exit(0)
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, child_term)
    while True: time.sleep(0.05)
def leader_term(s, f):
    time.sleep(float(sys.argv[1])); os._exit(0)
signal.signal(signal.SIGTERM, leader_term)
print(pid, flush=True)
while True: time.sleep(0.05)
"""

FORKS = r"""
import multiprocessing, os, signal, threading, time
signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))
def work():
    while True: sum(range(1000))
def spawn():
    ps = [multiprocessing.get_context("fork").Process(target=work, daemon=True) for _ in range(2)]
    for p in ps: p.start()
    print(" ".join(str(p.pid) for p in ps), flush=True)
threading.Thread(target=spawn).start()
while True: time.sleep(0.2)
"""


def _kill_by_pidfd(pid):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)


def _recorded(life, tmp_path, src, *args):
    script = tmp_path / "tree.py"
    script.write_text(src)
    p = _session([sys.executable, str(script), *args])
    line = p.stdout.readline().split()
    assert life.record_launch(STACK, COMP, p.pid, band="433")
    return p, [int(x) for x in line]


@pytest.mark.needs_session
@pytest.mark.wallclock("a real leader saves for 1 s on SIGTERM; its child ends 0.3 s after the leader")
def test_a_slow_saving_leaders_child_gets_the_grace_from_the_leaders_end(tmp_path, monkeypatch):
    life = _life(tmp_path)
    monkeypatch.setattr(Lifecycle, "GROUP_GRACE_S", 1.0)
    p, (child,) = _recorded(life, tmp_path, SLOW, "1.0", "1.3")     # the child ends 0.3 s after the leader
    try:
        res = life.stop(dataclasses.replace(COMP, stop_timeout=3.0))
        assert res.outcome.value == "stopped" and "SIGKILL" not in res.summary, res.summary
        assert _gone(child)
    finally:
        p.kill()
        p.wait(timeout=5)


@pytest.mark.needs_session
@pytest.mark.wallclock("real workers forked from a non-main thread outlive the SIGTERM; a foreign session runs the same")
def test_the_stop_kills_its_own_workers_never_a_foreign_session_with_the_same_argv(tmp_path, monkeypatch):
    life = _life(tmp_path)
    monkeypatch.setattr(Lifecycle, "GROUP_GRACE_S", 0.5)
    script = tmp_path / "tree.py"
    p, workers = _recorded(life, tmp_path, FORKS)
    foreign = _session([sys.executable, str(script)])
    fworkers = [int(x) for x in foreign.stdout.readline().split()]
    try:
        res = life.stop(COMP)
        assert res.outcome.value == "stopped", res.summary
        assert f"SIGKILLed {' '.join(map(str, workers))}" in res.summary, res.summary
        assert all(_gone(w) for w in workers)
        assert foreign.poll() is None and not any(_gone(w, 0.2) for w in fworkers)
        assert life.owned_records("loraham-daemon") == []
    finally:
        for w in workers + fworkers:
            _kill_by_pidfd(w)          # this test's own workers
        for q in (p, foreign):
            q.kill()
            q.wait(timeout=5)

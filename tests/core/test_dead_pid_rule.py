"""DEC-W384bm: ONE rule for "this pid is dead" (procident.pid_dead): state Z/X AND a thread list of the leader alone.
The leader of a thread group shows Z after pthread_exit while a worker thread runs on; every reader that judged a pid
by its stat state alone read such a process as dead. Each reader below is driven with a real process of that shape
(and a real unreaped zombie for the dead case). The post-runner script cannot import lhpc: its inlined copy is held
equal to procident.pid_dead here."""
import os
import re
import subprocess
import sys

import pytest

from lhpc.core import commands, procident, progress, proctree
from lhpc.core.lifecycle import Lifecycle

LEADER_Z = (
    "import ctypes, os, select, sys, threading\n"
    "def w():\n"
    "    while True:\n"
    "        st = open('/proc/self/stat').read()\n"
    "        if st[st.rindex(')') + 2] == 'Z':\n"
    "            break\n"
    "        select.select([], [], [], 0.01)\n"
    "    sys.stdout.write('leader-z\\n'); sys.stdout.flush()\n"
    "    select.select([], [], [], 60)\n"
    "threading.Thread(target=w).start()\n"
    "ctypes.CDLL(None).pthread_exit(None)\n")


@pytest.fixture
def leader_z():
    """A real detached process (start_new_session=True) whose leader is Z while a worker thread runs (pid == pgid == sid)."""
    p = subprocess.Popen([sys.executable, "-c", LEADER_Z], stdout=subprocess.PIPE, start_new_session=True)
    try:
        assert p.stdout.readline().strip() == b"leader-z"
        assert len(os.listdir(f"/proc/{p.pid}/task")) == 2
        yield p.pid
    finally:
        p.kill()
        p.wait(timeout=5)


@pytest.fixture
def zombie():
    """A real detached child (start_new_session=True) that exited and is NOT reaped (os.waitid WNOWAIT)."""
    z = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    os.waitid(os.P_PID, z.pid, os.WEXITED | os.WNOWAIT)
    try:
        assert os.listdir(f"/proc/{z.pid}/task") == [str(z.pid)]
        yield z.pid
    finally:
        z.wait()


def test_proc_alive_follows_the_rule(leader_z, zombie):
    assert procident.proc_alive(leader_z) is True                 # a live thread: alive
    assert procident.proc_alive(zombie) is False


def test_a_session_keeps_a_zombie_leader_with_a_live_thread(leader_z, zombie):
    me = os.getpid()
    assert proctree.session_members(leader_z, me) == [leader_z]
    assert proctree.session_member_details(leader_z, me) == [(leader_z, leader_z)]
    assert proctree.session_members(zombie, me) == []              # a true zombie: no member left


def test_the_progress_sampler_counts_a_zombie_leader_with_a_live_thread(leader_z):
    # direct: SessionSampler._members is what sample() reads (no public result names the members)
    members = progress.SessionSampler(leader_z, os.getpid(), lambda: 0)._members()
    assert [pid for pid, _start in members] == [leader_z]


def test_a_process_group_keeps_a_zombie_leader_with_a_live_thread(leader_z, zombie):
    # direct: Lifecycle._group_members is reached publicly only through a kill's group target
    assert [m["pid"] for m in Lifecycle._group_members(leader_z, leader_z)] == [leader_z]
    assert Lifecycle._group_members(zombie, zombie) == []


def _runner_pid_dead():
    src = re.search(r"^def _pid_dead\(pid, stat, tasks\):\n(?:    .*\n|\n)+?(?=\n\ndef )", commands._POST_RUNNER, re.M)
    assert src, "the post-runner's _pid_dead is gone"
    ns: dict = {}
    exec(src.group(0), ns)                                          # noqa: S102 - our own template text
    return ns["_pid_dead"]


@pytest.mark.parametrize("stat", ["7 (x) Z 1 7 7", "7 (x) X 1 7 7", "7 (x) x 1 7 7", "7 (x) S 1 7 7", "7 (a) b) Z 1 7 7",
                                  "7 (x) R 1", "garbage", "", "7 (x)", "7 (x) "])
@pytest.mark.parametrize("tasks", [None, [], ["7"], ["7", "8"], ["8"], ("7",)])
def test_the_post_runner_copy_equals_the_one_rule(stat, tasks):
    # the post-runner is stdlib-only (it cannot import lhpc): its _main_ok judges the main pid by an inlined copy
    assert _runner_pid_dead()(7, stat, tasks) is procident.pid_dead(7, stat, tasks)


def test_no_raw_state_check_outside_the_rule():
    # DEC-W384bm: the state set appears only in procident.pid_dead and the post-runner's held-equal copy
    root = os.path.join(os.path.dirname(procident.__file__))
    hits = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".py"):
            with open(os.path.join(root, name)) as fh:
                hits += [(name, n) for n, line in enumerate(fh, 1) if '("Z", "X", "x")' in line]
    assert [h[0] for h in hits] == ["commands.py", "procident.py"], hits

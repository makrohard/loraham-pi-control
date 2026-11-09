"""DEC-W384bm: ONE rule for "this pid is dead" (procident.pid_dead): state Z/X AND a thread list of the leader alone.
The leader of a thread group shows Z after pthread_exit while a worker thread runs on; every reader that judged a pid
by its stat state alone read such a process as dead. Each reader below is driven with a real process of that shape
(and a real unreaped zombie for the dead case). The post-runner's _main_ok is not a death reader but an AUTHORIZATION
check (may side effects still run?): it keeps its own strict refusal of any Z/X state (G1-DP1 B1, DEC-W384bq)."""
import os
import re
import select
import subprocess
import sys
import types

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
        if not select.select([p.stdout], [], [], 10)[0]:           # N3: bounded; the finally kills and reaps it
            pytest.fail("the leader never reached state Z within 10 s")
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


def _identity(pid):
    rest = open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()
    return {"main_pid": pid, "main_pgid": int(rest[2]), "main_sid": int(rest[3]), "main_starttime": int(rest[19])}


def _main_ok(binding, listdir=os.listdir):
    """The post-runner's own _main_ok (and any helper it calls) from the template, run against `binding`."""
    ns = {"BINDING": binding, "os": types.SimpleNamespace(**{**vars(os), "listdir": listdir})}
    for name in ("_pid_dead", "_main_ok"):
        m = re.search(rf"^def {name}\(.*\n(?:(?:    .*)?\n)+?(?=\S)", commands._POST_RUNNER, re.M)
        if m:
            exec(m.group(0), ns)                                    # noqa: S102 - our own template text
    return ns["_main_ok"]()


def _no_task_dir(path):
    if path.endswith("/task"):
        raise PermissionError(path)
    return os.listdir(path)


def test_the_post_runner_refuses_a_zombie_main_whose_task_list_cannot_be_read(zombie):
    # G1-DP1 B1: authorization fails toward "refuse": a ceased main (here a real unreaped zombie whose task list cannot
    # be read, as when another process reaps it between the reads) must never authorize the post steps
    assert _main_ok(_identity(zombie), listdir=_no_task_dir) is False
    assert _main_ok(_identity(zombie)) is False


def test_the_post_runner_authorizes_only_a_main_that_is_not_in_a_dead_state(leader_z):
    # strict by design: a Z leader refuses even with a live worker thread; a running main with its identity is ok
    assert _main_ok(_identity(leader_z)) is False
    live = subprocess.Popen([sys.executable, "-c", "import select; select.select([], [], [], 30)"], start_new_session=True)
    try:
        assert _main_ok(_identity(live.pid)) is True
    finally:
        live.kill()
        live.wait(timeout=5)


@pytest.mark.parametrize("stat", ["123 (daemon) ", "123 (daemon)", "123 (daemon) 7", "garbage"])
def test_proc_alive_reads_a_stat_without_a_state_field_as_not_alive(monkeypatch, stat):
    # G1-DP1 N2: a truncated or malformed stat is not evidence of life (proc_alive's own contract)
    real = procident.Path.read_text
    monkeypatch.setattr(procident.Path, "read_text", lambda self, *a, **k: stat if str(self) == "/proc/123/stat"
                        else real(self, *a, **k))
    assert procident.proc_alive(123) is False


def test_no_raw_state_check_outside_the_rule():
    # DEC-W384bq: a dead state is judged only by procident.pid_dead; the one other state test is the post-runner's
    # authorization refusal in _main_ok (pinned by name). Any quoted "Z"/"X" in code elsewhere fails here.
    root = os.path.dirname(procident.__file__)
    state = re.compile(r"""['"][ZX]['"]""")
    hits = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        func = ""
        with open(os.path.join(root, name)) as fh:
            for n, line in enumerate(fh, 1):
                m = re.match(r"(?:    )?def (\w+)\(", line)
                func = m.group(1) if m else func
                if state.search(line) and not line.lstrip().startswith("#"):
                    hits.append((name, func))
    assert sorted(set(hits)) == [("commands.py", "_main_ok"), ("procident.py", "pid_dead")], hits

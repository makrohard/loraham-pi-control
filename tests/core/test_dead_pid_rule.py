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


def _read_line(stream, want: bytes, timeout: float = 10.0) -> None:
    """Read `want` from the child's stdout within a bounded number of 0.1 s waits (G1-DP1-RE N1): select, then os.read
    what is there; a partial line, EOF or running out of waits fails the test (the fixture's teardown still kills and
    reaps the child). No clock is read: the bound is the count of select timeouts."""
    got = b""
    for _ in range(int(timeout * 10)):
        if not select.select([stream], [], [], 0.1)[0]:
            continue
        chunk = os.read(stream.fileno(), 64)
        if not chunk:
            pytest.fail(f"the child closed its output before {want!r} (got {got!r})")
        got += chunk
        if got.endswith(b"\n"):
            assert got.strip() == want, got
            return
    pytest.fail(f"the child did not write {want!r} within {timeout} s (got {got!r})")


def test_the_bounded_read_fails_on_a_partial_line():
    # G1-DP1-RE N1: a child that wrote a partial line and hangs (select says readable, readline would block for ever)
    # fails within the deadline; the old select()+readline() fixture hung here
    code = "import select, sys; sys.stdout.buffer.write(b'leader-'); sys.stdout.flush(); select.select([], [], [], 30)"
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    try:
        with pytest.raises(pytest.fail.Exception, match="did not write"):
            _read_line(p.stdout, b"leader-z", timeout=1.0)
    finally:
        p.kill()
        p.wait(timeout=5)


@pytest.fixture
def leader_z():
    """A real detached process (start_new_session=True) whose leader is Z while a worker thread runs (pid == pgid == sid)."""
    p = subprocess.Popen([sys.executable, "-c", LEADER_Z], stdout=subprocess.PIPE, start_new_session=True)
    try:
        _read_line(p.stdout, b"leader-z")                           # bounded (N1); the finally kills and reaps it
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
    # direct: SessionSampler._members is what sample() reads (no public result names the members). The live process
    # is never dropped as dead: it is a member, or, where the kernel refuses /proc/<pid>/io for a group whose leader is
    # a zombie (seen on the CI runner), the sample is _Unknown (sample() then reads it as progress), never "no member"
    try:
        members = progress.SessionSampler(leader_z, os.getpid(), lambda: 0)._members()
    except progress._Unknown:
        return
    assert [pid for pid, _start in members] == [leader_z]


def _fake_leader_z(root, pid=4242, sid=4242):
    """A fake /proc entry of a Z leader with a live worker thread (task list: the leader and one more)."""
    d = root / str(pid)
    (d / "task" / str(pid)).mkdir(parents=True)
    (d / "task" / str(pid + 1)).mkdir()
    (d / "stat").write_text(f"{pid} (x) Z 1 {pid} {sid} 0 -1 0 0 0 0 0 5 5 0 0 20 0 2 0 1000\n")
    (d / "io").write_text("rchar: 10\nwchar: 10\nsyscr: 1\n")
    return pid


def test_the_sampler_lists_a_zombie_leader_with_a_live_thread_when_its_io_is_readable(tmp_path):
    # DEC-W384bt, the deterministic half 1: io readable -> the live process is a member (the old rule: no member)
    pid = _fake_leader_z(tmp_path)
    members = progress.SessionSampler(pid, 1, lambda: 0, proc_root=str(tmp_path))._members()
    assert members == {(pid, 1000): (10, 20)}                      # cpu utime+stime 5+5, io rchar+wchar


def test_the_sampler_reads_an_unreadable_io_as_unknown_and_counts_it_as_progress(tmp_path, monkeypatch):
    # DEC-W384bt, half 2: io unreadable (as the CI runner's kernel does under a zombie leader) -> _Unknown, which
    # sample() counts as progress; against a baseline where nothing changed it would be False
    pid = _fake_leader_z(tmp_path)
    s = progress.SessionSampler(pid, 1, lambda: 0, proc_root=str(tmp_path))
    assert s.sample() is True                                       # the baseline (every member is new)
    assert s.sample() is False                                      # nothing changed: no progress
    real = progress._read_text
    monkeypatch.setattr(progress, "_read_text", lambda path: (_ for _ in ()).throw(PermissionError(path))
                        if path.endswith("/io") else real(path))
    with pytest.raises(progress._Unknown):
        s._members()
    assert s.sample() is True                                       # unknown is counted as progress, never a stall


def test_a_process_group_keeps_a_zombie_leader_with_a_live_thread(leader_z, zombie):
    # direct: Lifecycle._group_members is reached publicly only through a kill's group target
    assert [m["pid"] for m in Lifecycle._group_members(leader_z, leader_z)] == [leader_z]
    assert Lifecycle._group_members(zombie, zombie) == []


def _identity(pid):
    rest = open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()
    return {"main_pid": pid, "main_pgid": int(rest[2]), "main_sid": int(rest[3]), "main_starttime": int(rest[19])}


def _main_ok(binding):
    """The post-runner's own _main_ok, from the template, run against `binding`."""
    m = re.search(r"^def _main_ok\(.*\n(?:(?:    .*)?\n)+?(?=\S)", commands._POST_RUNNER, re.M)
    assert m, "the post-runner's _main_ok is gone"
    ns = {"BINDING": binding, "os": os}
    exec(m.group(0), ns)                                            # noqa: S102 - our own template text
    return ns["_main_ok"]()


def test_the_post_runner_refuses_a_zombie_main(zombie):
    # G1-DP1 B1: authorization fails toward "refuse": a ceased main (a real unreaped zombie, its identity matching)
    # never authorizes the post steps, whatever its task list (the check reads none)
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

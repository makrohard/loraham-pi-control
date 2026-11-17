"""A lab command that hangs is diagnosed, never only killed (`lhpc_testlab.testing.run_lab`): past its timeout
the child dumps every thread's Python stack (PYTHONFAULTHANDLER + SIGABRT), and the error keeps that dump and what
the child printed before. It runs without core dumps. A stand-in replaces the `lhpc-testlab` executable."""
from __future__ import annotations

import os
import resource
import select
import signal
import subprocess
import sys

import pytest
from lhpc_testlab import testing


def _stand_in(tmp_path, monkeypatch, body: str):
    exe = tmp_path / "lhpc-testlab"
    exe.write_text(f"#!{sys.executable}\n{body}")
    exe.chmod(0o755)
    monkeypatch.setattr(testing, "lab_bin", lambda: exe)
    monkeypatch.chdir(tmp_path)                      # a core file would land here


@pytest.mark.wallclock("a real child hangs past run_lab's timeout")
def test_a_hang_that_prints_nothing_names_the_frame_it_hangs_in(tmp_path, monkeypatch):
    _stand_in(tmp_path, monkeypatch, "import time\n\ndef wait_for_the_download():\n"
                                     "    time.sleep(60)\n\nwait_for_the_download()\n")
    with pytest.raises(RuntimeError) as hang:
        testing.run_lab({}, "reset", timeout=1)
    text = str(hang.value)
    assert text.split("\n", 1)[0] == "lhpc-testlab reset timed out after 1 s", text
    assert "in wait_for_the_download" in text, text   # the hanging frame, from the thread dump
    assert not list(tmp_path.glob("core*"))


@pytest.mark.wallclock("a real child hangs past run_lab's timeout")
def test_a_hang_after_output_keeps_that_output_and_runs_without_core_dumps(tmp_path, monkeypatch):
    _stand_in(tmp_path, monkeypatch, "import resource, time\n"
                                     "print('PARTIAL-OUT core-limit', resource.getrlimit(resource.RLIMIT_CORE)[0],"
                                     " flush=True)\ntime.sleep(60)\n")
    soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    resource.setrlimit(resource.RLIMIT_CORE, (hard, hard))   # what the child would inherit, were it not set
    try:
        with pytest.raises(RuntimeError) as hang:
            testing.run_lab({}, "init", timeout=1)
    finally:
        resource.setrlimit(resource.RLIMIT_CORE, (soft, hard))
    assert "stdout: PARTIAL-OUT core-limit 0\n" in str(hang.value), str(hang.value)
    assert not list(tmp_path.glob("core*"))


def _ended(pid: int, timeout: float = 5.0) -> bool:
    """True once `pid` has exited (reaped or a zombie of init's)."""
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        return bool(select.select([fd], [], [], timeout)[0])
    finally:
        os.close(fd)


@pytest.mark.wallclock("a real child hangs past run_lab's timeout; its children are real; waits bounded at 5 s")
def test_a_hang_ends_the_children_the_lab_started(tmp_path, monkeypatch):
    """G2-TESTSA B5: the timeout killed only the lab's own process; a child it had started (holding the output pipes,
    or not) ran on. Both end with the lab now, and the error still names the command and its output."""
    _stand_in(tmp_path, monkeypatch, "import subprocess, time\n"
              "a = subprocess.Popen(['sleep', '60'])\n"                                   # holds the pipes
              "b = subprocess.Popen(['sleep', '60'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
              f"open({str(tmp_path / 'kids')!r}, 'w').write(f'{{a.pid}} {{b.pid}}')\n"
              "print('PARTIAL-OUT', flush=True)\ntime.sleep(60)\n")
    with pytest.raises(RuntimeError) as hang:
        testing.run_lab({}, "reset", timeout=1)
    kids = [int(p) for p in (tmp_path / "kids").read_text().split()]
    try:
        assert str(hang.value).split("\n", 1)[0] == "lhpc-testlab reset timed out after 1 s"
        assert "stdout: PARTIAL-OUT\n" in str(hang.value), str(hang.value)
        assert [_ended(k) for k in kids] == [True, True], kids
    finally:
        for k in kids:
            try:
                os.kill(k, 9)
            except ProcessLookupError:
                pass


# A setsid child; an orphan: a setsid grandchild whose parent (sh) has ended; and G2-TESTSD B1's own input: an orphan
# in the caller's process group that holds the output pipes (`sh -c 'sleep 60 &'`, its sh ended before the timeout).
_STRAYS = ("import os\n"
           "r, w = os.pipe()\n"
           "subprocess.run(['sh', '-c', f'sleep 60 {w}>&- & echo $! >&{w}'], pass_fds=(w,))\n"   # wallclock: marked tests' text
           "os.close(w)\no = os.read(r, 64).decode().split()[0]\n"
           "c = subprocess.Popen(['setsid', 'sleep', '60'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
           "g = subprocess.run(['sh', '-c', 'setsid sleep 60 >/dev/null 2>&1 & echo $!'],\n"  # wallclock: marked tests' text
           "                   capture_output=True, text=True).stdout.split()[0]\n")


@pytest.mark.wallclock("a real child hangs past run_lab's timeout; its children are real; waits bounded at 5 s")
def test_a_hang_ends_what_the_lab_started_outside_its_group(tmp_path, monkeypatch):
    """G2 read N-d: the timeout listed only what the lab started that was still in its process group and still below
    it, so a setsid child and a grandchild whose parent had ended (init's by then) ran on. A keeper
    (a child subreaper) now holds both below the lab command, and they end with it."""
    _stand_in(tmp_path, monkeypatch, "import subprocess, time\n" + _STRAYS +
              f"open({str(tmp_path / 'kids')!r}, 'w').write(f'{{c.pid}} {{g}} {{o}}')\ntime.sleep(60)\n")
    with pytest.raises(RuntimeError, match=r"^lhpc-testlab reset timed out after 1 s\n"):
        testing.run_lab({}, "reset", timeout=1)
    kids = [int(p) for p in (tmp_path / "kids").read_text().split()]
    try:
        assert [_ended(k) for k in kids] == [True, True, True], kids
    finally:
        for k in kids:
            try:
                os.kill(k, 9)
            except ProcessLookupError:
                pass


@pytest.mark.wallclock("an outer timeout kills a real caller after 2 s; waits bounded at 5 s")
def test_an_outer_kill_of_the_caller_ends_the_lab_and_its_children(tmp_path):
    """G2-TESTSA B5 read R3: a lab command in a process group of its own outlived an outer kill of its caller's group (a
    lane's `timeout`, Ctrl-C): the lab and its children ran on after their pytest ended. They end with the caller, and so
    do a setsid child and an orphaned grandchild (N-d), which no group kill reaches."""
    exe = tmp_path / "lhpc-testlab"
    exe.write_text(f"#!{sys.executable}\nimport os, subprocess, time\n"
                   "a = subprocess.Popen(['sleep', '60'])\n" + _STRAYS +
                   f"open({str(tmp_path / 'kids')!r}, 'w').write(f'{{os.getpid()}} {{a.pid}} {{c.pid}} {{g}} {{o}}')\n"
                   "time.sleep(60)\n")
    exe.chmod(0o755)
    caller = ("import pathlib, sys\nfrom lhpc_testlab import testing\n"
              "testing.lab_bin = lambda: pathlib.Path(sys.argv[1])\ntesting.run_lab({}, 'reset', timeout=100)\n")
    outer = subprocess.run(["timeout", "-k", "2", "2", sys.executable, "-c", caller, str(exe)], timeout=30, check=False,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    kids = [int(p) for p in (tmp_path / "kids").read_text().split()]
    try:
        assert outer.returncode == 124, outer.stderr          # the outer timeout's kill ended the caller
        assert [_ended(k) for k in kids] == [True] * 5, kids
    finally:
        for k in kids:
            try:
                os.kill(k, 9)
            except ProcessLookupError:
                pass


def test_a_kill_reaches_the_listed_process_or_nothing():
    """G2 read N-e: a listed pid was signalled by its number, up to 5 s later; a pid the kernel had handed on since
    would have been hit. It is pinned now by a pidfd, and only while its start time is still the listed one."""
    from lhpc_testlab import reaper
    proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE)                 # blocks on its stdin until killed
    try:
        listed = [p for p in reaper.descendants(os.getpid()) if p[0] == proc.pid]
        assert len(listed) == 1, listed
        pid, start = listed[0]
        assert reaper.pin([(pid, start + 1)]) == []                       # the same number, another process
        reaper.kill(reaper.pin(listed))
        assert proc.wait(timeout=5) == -signal.SIGKILL
        assert reaper.pin(listed) == []                                   # ended and reaped: nothing to pin
    finally:
        proc.kill()
        proc.wait()


def test_a_command_that_ends_returns_its_status_and_output(tmp_path, monkeypatch):
    _stand_in(tmp_path, monkeypatch, "import sys\nprint('out ' + ' '.join(sys.argv[1:]))\n"
                                     "print('err', file=sys.stderr)\nsys.exit(3)\n")
    r = testing.run_lab({}, "status")
    assert (r.returncode, r.stdout, r.stderr) == (3, "out status\n", "err\n")
    with pytest.raises(RuntimeError, match=r"^lhpc-testlab status rc=3\nstdout: out status\n"):
        testing.run_lab({}, "status", check=True)

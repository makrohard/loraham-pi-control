"""A lab command that hangs is diagnosed, never only killed (`lhpc_testlab.testing.run_lab`): past its timeout
the child dumps every thread's Python stack (PYTHONFAULTHANDLER + SIGABRT), and the error keeps that dump and what
the child printed before. It runs without core dumps. A stand-in replaces the `lhpc-testlab` executable."""
from __future__ import annotations

import os
import re
import resource
import select
import signal
import subprocess
import sys
import tempfile
import time

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


@pytest.mark.wallclock("a real child hangs past run_lab's timeout")
def test_a_hang_in_the_kernel_names_where_the_lab_waits(tmp_path, monkeypatch):
    """SLOWBUILD-RESET-ABORT: a lab that had done its work hung in its exit, where no Python frame is left, so the
    stack dump named nothing. The error now also keeps where the lab waited in the kernel when its time ran out,
    read before the abort: its State, its wait channel and its kernel stack (or why /proc does not give it). That
    is the lab's, not that of a child it started."""
    _stand_in(tmp_path, monkeypatch, "import os, subprocess\n"
              "kid = subprocess.Popen(['sleep', '60'])\n"
              f"open({str(tmp_path / 'pids')!r}, 'w').write(f'{{os.getpid()}} {{kid.pid}}')\n"
              "r, w = os.pipe()\nos.read(r, 1)\n")                    # in the kernel: a pipe no one writes to
    with pytest.raises(RuntimeError) as hang:
        testing.run_lab({}, "reset", timeout=1)
    lab, kid = (tmp_path / "pids").read_text().split()
    text = str(hang.value)
    at = re.search(r"^lab at the timeout: pid (\d+) State: (.+); wchan: (.+); stack: (.+)$", text, re.MULTILINE)
    assert at, text
    assert at.group(1) == lab != kid, text
    assert at.group(2) == "S (sleeping)", text                         # waiting, not running
    assert "pipe" in at.group(3), text                                 # the kernel function it waits in
    assert text.index("lab at the timeout:") < text.index("stdout: "), text


@pytest.mark.wallclock("a real process stands in for a keeper with no descendant; killed at once")
def test_a_keeper_with_no_lab_left_says_so():
    """The lab already ended and only its keeper is left: the capture says that, it names no other process."""
    with subprocess.Popen(["sleep", "60"]) as alone:        # a process with no descendant
        try:
            assert testing._lab_at_timeout(alone.pid) == "lab at the timeout: no lab process left under the keeper"
        finally:
            alone.kill()


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
    """G2-TESTSA B5: the timeout killed only the lab's own process; a child it had started (holding the output,
    or not) ran on. Both end with the lab now, and the error still names the command and its output."""
    _stand_in(tmp_path, monkeypatch, "import subprocess, time\n"
              "a = subprocess.Popen(['sleep', '60'])\n"                                   # holds the output
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
# in the caller's process group that holds the output (`sh -c 'sleep 60 &'`, its sh ended before the timeout).
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


@pytest.mark.wallclock("a real child that ends at once, its background child holds its output; bounded at 5 s")
def test_a_command_that_ends_while_its_child_holds_its_output_returns_when_it_ends(tmp_path, monkeypatch):
    """G2-TESTSE F1: run_lab took the pipes' EOF for the command's end. A command that started a child holding
    stdout/stderr and exited (`sh -c 'sleep 60 &'`) was reported as a hang at the timeout, by which time its keeper had
    ended, the timeout found no descendant, and the child ran on. Its output goes to files now and the keeper's exit ends
    the command: run_lab returns its status and output then, and what it left running is left, as for any command
    that ends."""
    _stand_in(tmp_path, monkeypatch, "import subprocess\n"
              "kid = subprocess.Popen(['sleep', '60'])\n"                                 # inherits stdout/stderr
              f"open({str(tmp_path / 'kid')!r}, 'w').write(str(kid.pid))\n"
              "print('done', flush=True)\n")
    try:
        start = time.monotonic()                                                    # wallclock: against the bound
        r = testing.run_lab({}, "reset", timeout=5)
        assert time.monotonic() - start < 3, "run_lab waited for the child, not for the command"  # wallclock
        assert (r.returncode, r.stdout) == (0, "done\n")
    finally:
        try:
            os.kill(int((tmp_path / "kid").read_text()), signal.SIGKILL)
        except (ProcessLookupError, FileNotFoundError):
            pass


class _WritesMidRead:
    """A capture file whose other holder (a real child, on its stdout or stderr) writes once between the reader's
    first and second call on it: the boundary inside a readback."""

    def __init__(self, f, go: int, done: int):
        self._f, self._go, self._done, self._calls = f, go, done, 0

    def _tick(self):
        self._calls += 1
        if self._calls == 2:
            os.write(self._go, b".")
            os.read(self._done, 1)                       # the child's write has landed

    def fileno(self):
        self._tick()
        return self._f.fileno()

    def seek(self, *a):
        self._tick()
        return self._f.seek(*a)

    def read(self, *a):
        self._tick()
        return self._f.read(*a)


@pytest.mark.parametrize("stream", [1, 2], ids=["stdout", "stderr"])
def test_a_child_that_writes_during_the_readback_loses_no_output(stream):
    """G2-TESTSG B1: the readback rewound the file and read on; a child the command left running shares that offset,
    so its write in between landed over the start and moved the offset past it: the read came back empty. The file
    is read by position now, whatever its other holders do with the offset."""
    go_r, go_w = os.pipe()
    done_r, done_w = os.pipe()
    with tempfile.TemporaryFile() as f:
        child = subprocess.Popen(
            [sys.executable, "-c", f"import os\nos.write({stream}, b'HEAD\\n')\nos.write({done_w}, b'.')\n"
                                   f"os.read({go_r}, 1)\nos.write({stream}, b'TAIL\\n')\nos.write({done_w}, b'.')\n"],
            pass_fds=(go_r, done_w), **{"stdout" if stream == 1 else "stderr": f})
        try:
            os.read(done_r, 1)                                # HEAD is written
            got = testing._read_back(_WritesMidRead(f, go_w, done_r))
            assert child.wait(10) == 0
        finally:
            child.kill()
            for fd in (go_r, go_w, done_r, done_w):
                os.close(fd)
    assert got in (b"HEAD\n", b"HEAD\nTAIL\n"), got       # never the empty or overwritten start


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


# ---- G2-RELP4c B1: the keeper's kill repeats until no descendant is live -------------------------------------------------

def _forker(tag: str) -> str:
    """Lab-command text: a setsid worker forking a `sleep <tag>` every 1 ms (one forked after any single snapshot)."""
    loop = f"import subprocess, time\\nwhile True:\\n    subprocess.Popen([\"sleep\", \"{tag}\"])\\n    time.sleep(0.001)\\n"
    return f"import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', '{loop}'], start_new_session=True)\n"


def _tagged(tag: str) -> list[int]:
    """The live processes whose command line holds `tag` (the forker's sleeps and the worker itself)."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                cmd = fh.read()
            with open(f"/proc/{name}/stat") as fh:
                state = fh.read().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if tag.encode() in cmd and state != "Z":
            found.append(int(name))
    return found


def _sweep(tag: str) -> None:
    for pid in _tagged(tag):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.wallclock("a real forking worker past run_lab's timeout; three trials, each bounded at 5 s")
def test_a_timeout_leaves_no_descendant_forked_after_the_snapshot(tmp_path, monkeypatch):
    # red-before (13c5b1be): run_lab pinned one snapshot before the SIGABRT and the keeper exited at the command's end: a
    # worker forking after the snapshot left live sleeps (a22's repro: survivors in 7 of 20 trials at 200 ms)
    for trial in range(3):
        tag = f"31.{os.getpid()}{trial}"
        _stand_in(tmp_path, monkeypatch, _forker(tag) + "import time\ntime.sleep(60)\n")
        try:
            with pytest.raises(RuntimeError, match="timed out"):
                testing.run_lab({}, "reset", timeout=0.3)
            time.sleep(0.3)                               # a survivor would have forked on by now
            assert _tagged(tag) == [], f"trial {trial}"
        finally:
            _sweep(tag)


@pytest.mark.wallclock("a real forking worker under the keeper, SIGTERM after 0.3 s; three trials, each bounded at 10 s")
def test_an_outer_kill_of_the_keeper_leaves_no_descendant(tmp_path):
    # red-before (13c5b1be): the keeper's SIGTERM handler killed one snapshot and exited at once
    exe = tmp_path / "lab"
    for trial in range(3):
        tag = f"32.{os.getpid()}{trial}"
        exe.write_text(f"#!{sys.executable}\n" + _forker(tag) + "import time\ntime.sleep(60)\n")
        exe.chmod(0o755)
        keeper = subprocess.Popen([sys.executable, "-I", "-S", testing.reaper.__file__, str(exe)], stdin=subprocess.DEVNULL)
        try:
            time.sleep(0.3)
            keeper.terminate()
            assert keeper.wait(timeout=10) == 128 + signal.SIGTERM
            time.sleep(0.3)
            assert _tagged(tag) == [], f"trial {trial}"
        finally:
            keeper.kill()
            keeper.wait()
            _sweep(tag)


@pytest.mark.wallclock("a real forking lab that ignores SIGABRT: run_lab's 5 s wait, then its SIGTERM; bounded at 20 s")
def test_a_lab_that_ignores_the_abort_is_ended_by_the_keeper_after_5_s(tmp_path, monkeypatch):
    # the lab does not end on SIGABRT: after 5 s run_lab sends the keeper SIGTERM, and the keeper kills all it keeps
    tag = f"33.{os.getpid()}"
    _stand_in(tmp_path, monkeypatch, "import signal\nsignal.signal(signal.SIGABRT, signal.SIG_IGN)\n" + _forker(tag) +
              "import time\ntime.sleep(60)\n")
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="timed out"):
            testing.run_lab({}, "reset", timeout=0.3)
        assert time.monotonic() - t0 < 20
        time.sleep(0.3)                                   # a survivor would have forked on by now
        assert _tagged(tag) == []
    finally:
        _sweep(tag)

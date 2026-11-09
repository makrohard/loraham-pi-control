"""W389 (1): a lab's fakes stop with the lab. `lhpc-testlab init`/`reset` spawn the fake gpsd (and the APRS-IS sink)
detached, each the leader of its own process group; `LabServer.stop()` stopped only `lhpc web`, so every lab root left its fake gpsd
running (~24 MB each, about three per test run). Told by the root: a fake carries its lab's LHPC_RUNTIME_ROOT."""
from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest
from lhpc_testlab.testing import LabServer

pytestmark = pytest.mark.lab_serial         # resets labs (testlab/tests/conftest.py)


def _fakes_of(root: Path) -> list:
    """The live fake processes (`lhpc_testlab _gpsd`, the APRS-IS sink) whose lab root is `root`."""
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ")
            with open(f"/proc/{p}/environ", "rb") as fh:
                env = fh.read().split(b"\0")
        except OSError:
            continue
        if (b"lhpc_testlab _gpsd" in cmd or b"aprs_sink.py" in cmd) and f"LHPC_RUNTIME_ROOT={root}".encode() in env:
            out.append(int(p))
    return out


def test_a_fake_that_ignores_term_is_killed_with_its_whole_group(tmp_path):
    """The stop reaches the fake's whole process group (it leads a group of its own) and kills what ignores
    SIGTERM after the grace: a stand-in fake that ignores TERM, with a child in its group, both gone."""
    import subprocess
    import sys

    from lhpc_testlab import gpsd, ops

    from lhpc.core.paths import Paths
    prog = ("import signal, subprocess, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "c = subprocess.Popen([sys.executable, '-c', 'import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "signal.pause()']); print(c.pid, flush=True); signal.pause()")
    fake = subprocess.Popen([sys.executable, "-c", prog, "lhpc_testlab", "_gpsd"], stdout=subprocess.PIPE,
                            start_new_session=True, text=True, env=dict(os.environ, LHPC_RUNTIME_ROOT=str(tmp_path)))
    child = int(fake.stdout.readline())
    paths = Paths(runtime_root=tmp_path)
    try:
        pid_file = gpsd.pid_path(paths)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{fake.pid}\n")
        assert ops.stop_fakes(paths, grace_s=0.5) == [fake.pid]
        assert fake.wait(timeout=5) == -signal.SIGKILL               # it ignored TERM: killed after the grace
        state = ""
        if os.path.exists(f"/proc/{child}"):
            with open(f"/proc/{child}/stat") as fh:
                state = fh.read().split(") ")[1][0]
        assert state in ("", "Z", "X"), state                         # the child of its group is gone too
    finally:
        for pid in (fake.pid, child):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_lab_reset_refuses_while_a_foreign_daemon_serves_its_radio_sockets(tmp_path, monkeypatch):
    """W389 (2): the daemon's sockets are the host's (/run/loraham or /tmp, LORAHAM_SOCKET_DIR, the clients' paths
    too), so two lab roots on one host collide on loraconf-r1.sock. A reset (the lab's start) refuses while a live one
    exists that its own stopped stacks no longer explain, naming it; a stale socket file does not refuse."""
    import socket
    import tempfile

    from lhpc_testlab import ops
    from lhpc_testlab.testing import make_lab_root, stop_lab_fakes

    from lhpc.core.services import ControllerService
    sockdir = Path(tempfile.mkdtemp(prefix="lab-", dir="/tmp"))     # short: an AF_UNIX path has a limit
    monkeypatch.setattr(ops, "RADIO_SOCKET_DIRS", (str(sockdir),), raising=False)
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        live.bind(str(sockdir / "loraconf-r1.sock"))
        live.listen(1)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(sockdir / "loraconf-r2.sock"))                 # bound, never listening: refuses a connect
        stale.close()
        closed = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        closed.bind(str(sockdir / "loraconf-r3.sock"))
        closed.listen(1)
        os.chmod(sockdir / "loraconf-r3.sock", 0)                     # a connect is refused by permission: unprobed
        paths = make_lab_root(tmp_path, monkeypatch)
        svc = ControllerService(paths=paths)
        r = ops.reset(svc)
        assert not r.ok and str(sockdir / "loraconf-r1.sock") in r.summary, r.summary
        assert "loraconf-r2.sock" not in r.summary                   # a stale file is no foreign daemon
        unprobed = str(sockdir / "loraconf-r3.sock")
        if os.geteuid() != 0:                                         # root connects regardless: then it is live
            unprobed += " (unprobed: PermissionError)"
        assert unprobed in r.summary, r.summary                       # a socket it cannot rule out is named too
        # The foreign daemon reads as this root's running stack, which this root cannot stop: still named.
        from types import SimpleNamespace
        first = svc.stacks()[0].id
        monkeypatch.setattr(svc, "stack_running", lambda sid: sid == first)
        monkeypatch.setattr(svc, "stop", lambda *a, **k: SimpleNamespace(ok=False))
        r = ops.reset(svc)
        assert not r.ok and str(sockdir / "loraconf-r1.sock") in r.summary, r.summary
        assert f"could not stop: {first}" in r.summary and "this lab's own or another lab's" in r.summary, r.summary
        assert "another daemon serves" not in r.summary               # it may be this lab's own that did not stop
        assert f"stop failed: {first}" in "\n".join(r.details)
    finally:
        live.close()
        closed.close()
        stop_lab_fakes(tmp_path)
        for f in sockdir.iterdir():
            f.unlink()
        sockdir.rmdir()


def test_a_lab_test_that_reboots_leaves_no_fake_behind(tmp_path):
    """The suite's own teardown: a lab test that simulates a reboot (the boot respawns the fake gpsd) and builds no
    LabServer, run as the suite runs it, leaves no fake of its lab root."""
    import subprocess
    import sys
    bt = tmp_path / "bt"
    target = Path(__file__).with_name("test_testlab.py")
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider",
                            f"{target}::test_power_reboot_does_not_tombstone", f"--basetemp={bt}"],
                           capture_output=True, text=True, timeout=120, check=False)
        assert r.returncode == 0, r.stdout[-2000:]
        assert _fakes_of(bt / "test_power_reboot_does_not_tom0") == [], "the reboot's fake gpsd outlived the test"
    finally:
        for pid in _fakes_of(bt / "test_power_reboot_does_not_tom0"):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass


def _stand_in_fake(tmp_path, root, prog):
    """A stand-in fake gpsd (its argv carries the fake's), leader of its own group, its environment naming `root`;
    it prints a pid when ready. Returns (the Popen, the printed pid)."""
    import subprocess
    import sys
    fake = subprocess.Popen([sys.executable, "-c", prog, "lhpc_testlab", "_gpsd"], stdout=subprocess.PIPE,
                            start_new_session=True, text=True, env=dict(os.environ, LHPC_RUNTIME_ROOT=str(root)))
    return fake, int(fake.stdout.readline())


def _gone(pid: int) -> bool:
    """`pid` has exited (a zombie too), awaited on its pidfd for 5 s at most: a SIGKILL lands asynchronously."""
    import select
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        return bool(select.select([fd], [], [], 5.0)[0])
    finally:
        os.close(fd)


def test_a_member_that_ignores_term_dies_after_its_leader_exits(tmp_path):
    """G1-LF1 (1): the leader exits on TERM, a member of its group ignores TERM; the SIGKILL after the grace still
    reaches the group, so the member is gone (the stop no longer keys the KILL on the leader being alive)."""
    from lhpc_testlab import gpsd
    from lhpc_testlab.testing import stop_lab_fakes

    from lhpc.core.paths import Paths
    prog = ("import os, signal, subprocess, sys; "
            "c = subprocess.Popen([sys.executable, '-c', 'import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print(1, flush=True); signal.pause()'], stdout=subprocess.PIPE, text=True); c.stdout.readline(); "
            "signal.signal(signal.SIGTERM, lambda *a: os._exit(0)); print(c.pid, flush=True); signal.pause()")
    fake, member = _stand_in_fake(tmp_path, tmp_path, prog)
    paths = Paths(runtime_root=tmp_path)
    try:
        gpsd.pid_path(paths).parent.mkdir(parents=True, exist_ok=True)
        gpsd.pid_path(paths).write_text(f"{fake.pid}\n")
        assert stop_lab_fakes(tmp_path) == [fake.pid]
        assert fake.wait(timeout=5) == 0                              # the leader left on TERM
        assert _gone(member), "a TERM-ignoring member outlived its exited leader"
    finally:
        for pid in (fake.pid, member):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_stale_pid_file_never_reaches_another_roots_fake(tmp_path):
    """G1-LF1 (2): lab A's stale pid file names a pid that is now lab B's fake (the same argv): A's stop leaves it."""
    from lhpc_testlab import gpsd
    from lhpc_testlab.testing import stop_lab_fakes

    from lhpc.core.paths import Paths
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    fake, _ = _stand_in_fake(tmp_path, root_b, "import signal; print(0, flush=True); signal.pause()")
    paths_a = Paths(runtime_root=root_a)
    try:
        gpsd.pid_path(paths_a).parent.mkdir(parents=True, exist_ok=True)
        gpsd.pid_path(paths_a).write_text(f"{fake.pid}\n")
        assert stop_lab_fakes(root_a) == []
        assert fake.poll() is None, "lab A's stop reached lab B's fake"
    finally:
        fake.kill()
        fake.wait(timeout=5)


def test_a_second_lab_on_the_host_refuses_at_once_naming_the_holder(tmp_path, monkeypatch):
    """G1-LF1 (4), DEC-W384u: two lab roots, two processes. While lab A's LabServer holds the host's lab lock, lab B's
    LabServer refuses before any init or reset, naming A's pid and root; after A's stop the lock is free. No real reset
    in this lane: a reset reads any daemon on the host as its own, another checkout's live lab too."""
    import subprocess
    import sys

    from lhpc_testlab import testing
    lock = tmp_path / "lab.lock"
    monkeypatch.setattr(testing, "LAB_LOCK_PATH", str(lock), raising=False)
    root_a, root_b = tmp_path / "a" / "runtime", tmp_path / "b" / "runtime"
    second = ("import sys; from pathlib import Path; import lhpc_testlab.testing as t; t.LAB_LOCK_PATH = sys.argv[1]; "
              "t.LAB_LOCK_WAIT_S = 0; "
              "t.LabServer(Path(sys.argv[2])).init_and_reset() if sys.argv[3] == 'lab' else t._hold_lab_lock(sys.argv[2])")
    monkeypatch.setattr(testing, "run_lab", lambda *a, **k: None)   # lab A's init/reset are not the case: its lock is
    a = LabServer(root_a)
    try:
        a.init_and_reset()
        r = subprocess.run([sys.executable, "-c", second, str(lock), str(root_b), "lab"], capture_output=True,
                           text=True, timeout=60, check=False)
        assert r.returncode != 0 and f"pid {os.getpid()} root {root_a}" in r.stderr, r.stderr[-1500:]
        assert not (root_b / "state").exists()                       # refused before its init touched the root
    finally:
        a.stop()
    r = subprocess.run([sys.executable, "-c", second, str(lock), str(root_b), "lock"], capture_output=True, text=True,
                       timeout=60, check=False)
    assert r.returncode == 0, r.stderr[-1500:]                        # A's stop released it


def test_a_group_member_of_another_root_is_never_signalled(tmp_path):
    """DEC-W384y (2): no group signal. A member of the fake's group whose environment names ANOTHER root (as a
    recycled group id would hold) survives the stop; this root's leader is stopped."""
    from lhpc_testlab import gpsd
    from lhpc_testlab.testing import stop_lab_fakes

    from lhpc.core.paths import Paths
    other = tmp_path / "other"
    prog = ("import os, signal, subprocess, sys; "
            f"c = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'], env=dict(os.environ, LHPC_RUNTIME_ROOT={str(other)!r})); "
            "print(c.pid, flush=True); signal.pause()")
    fake, stranger = _stand_in_fake(tmp_path, tmp_path, prog)
    paths = Paths(runtime_root=tmp_path)
    try:
        gpsd.pid_path(paths).parent.mkdir(parents=True, exist_ok=True)
        gpsd.pid_path(paths).write_text(f"{fake.pid}\n")
        assert stop_lab_fakes(tmp_path) == [fake.pid]
        assert fake.wait(timeout=5) == -signal.SIGTERM
        os.kill(stranger, 0)                                          # still there: raises if it was signalled dead
        with open(f"/proc/{stranger}/stat") as fh:
            assert fh.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    finally:
        for pid in (fake.pid, stranger):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.mark.wallclock("the grace under test is real time: a member needs 0.3 s on TERM")
def test_every_member_gets_the_grace_not_only_the_leader(tmp_path):
    """DEC-W384y (the grace NOTE): the leader leaves on TERM at once; a member needs 0.3 s on TERM to finish (it
    writes a file, then exits). The KILL waits for it, so its file is written."""
    from lhpc_testlab import gpsd
    from lhpc_testlab.testing import stop_lab_fakes

    from lhpc.core.paths import Paths
    done = tmp_path / "member-finished"
    member = ("import os, pathlib, signal, sys, time\n"
              "def bye(*a):\n    time.sleep(0.3); pathlib.Path(sys.argv[1]).touch(); os._exit(0)\n"  # rule-1 exception: the grace under test
              "signal.signal(signal.SIGTERM, bye); print(1, flush=True); signal.pause()")
    prog = ("import os, signal, subprocess, sys; "
            f"c = subprocess.Popen([sys.executable, '-c', {member!r}, {str(done)!r}], stdout=subprocess.PIPE, text=True); "
            "c.stdout.readline(); signal.signal(signal.SIGTERM, lambda *a: os._exit(0)); print(c.pid, flush=True); "
            "signal.pause()")
    fake, mpid = _stand_in_fake(tmp_path, tmp_path, prog)
    paths = Paths(runtime_root=tmp_path)
    try:
        gpsd.pid_path(paths).parent.mkdir(parents=True, exist_ok=True)
        gpsd.pid_path(paths).write_text(f"{fake.pid}\n")
        assert stop_lab_fakes(tmp_path) == [fake.pid]
        assert _gone(mpid)
        assert done.exists(), "the member was killed before its grace"
    finally:
        for pid in (fake.pid, mpid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_lab_that_does_not_come_up_releases_the_host_lock(tmp_path, monkeypatch):
    """DEC-W384y: an init or reset that fails leaves the host's lab lock free (it was held until the process ended)."""
    import fcntl

    from lhpc_testlab import testing
    lock = tmp_path / "lab.lock"
    monkeypatch.setattr(testing, "LAB_LOCK_PATH", str(lock), raising=False)

    def failing(env, verb, check=False):
        raise RuntimeError(f"lhpc-testlab {verb} rc=1")
    monkeypatch.setattr(testing, "run_lab", failing)
    server = LabServer(tmp_path / "runtime")
    try:
        server.init_and_reset()
    except RuntimeError:
        pass
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                # BlockingIOError while it is still held
    finally:
        os.close(fd)
        server.stop()


def test_the_host_lock_never_follows_a_symlink(tmp_path, monkeypatch):
    """DEC-W384y: a symlink at the lock's path (another user's, in /tmp) is refused; its target is never written."""
    from lhpc_testlab import testing
    target = tmp_path / "someone-elses-file"
    target.write_text("keep\n")
    link = tmp_path / "lab.lock"
    link.symlink_to(target)
    monkeypatch.setattr(testing, "LAB_LOCK_PATH", str(link), raising=False)
    server = LabServer(tmp_path / "runtime")
    try:
        server.init_and_reset()
        raise AssertionError("the lab came up over a symlinked lock")
    except OSError:
        pass                                                          # ELOOP, before any init
    finally:
        server.stop()
    assert target.read_text() == "keep\n"
    assert not (tmp_path / "runtime" / "state").exists()


def test_the_turn_lock_is_skipped_only_for_a_pytest_its_holder_runs(tmp_path):
    """DEC-W384y (b): LHPC_LAB_SERIAL_HELD exempts a process only when it names an ancestor (the holding test's own
    pytest child); another live pid or junk does not."""
    import subprocess
    import sys
    probe = "from lhpc_testlab.testing import serial_held_by_ancestor as f; print(f())"
    other = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"])
    try:
        for held, want in ((str(os.getpid()), "True"), (str(other.pid), "False"), ("x", "False"), ("", "False")):
            r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=30, check=False,
                               env=dict(os.environ, LHPC_LAB_SERIAL_HELD=held))
            assert r.stdout.strip() == want, (held, r.stdout, r.stderr[-500:])
    finally:
        other.kill()
        other.wait(timeout=5)


@pytest.mark.wallclock("waits on another process: a fake-daemon test holding the lock in a child pytest")
def test_a_lab_waits_for_a_fake_daemon_test_of_another_process_then_takes_its_turn(tmp_path, monkeypatch):
    """DEC-W384x option (a), the browser-lane flake: a lab_serial test (one that runs a fake daemon) in ANOTHER process
    holds the host's lab lock through the real conftest fixture; a LabServer here waits for it and, past its wait,
    refuses naming that test; once the test ends, the LabServer takes its turn. (Before: the test held a lock of its
    own, so the lab came up beside the fake daemon and read it as its own.)"""
    import subprocess
    import sys
    import time

    from lhpc_testlab import testing
    lock, inside, release = tmp_path / "lab.lock", tmp_path / "inside", tmp_path / "release"
    (tmp_path / "test_holder.py").write_text(
        "import pathlib, time\nimport pytest\n\n\n@pytest.mark.lab_serial\ndef test_holder():\n"
        f"    pathlib.Path({str(inside)!r}).touch()\n"
        f"    while not pathlib.Path({str(release)!r}).exists():\n        time.sleep(0.05)\n")  # rule-1 exception: the holder waits for its release
    here = Path(__file__).resolve().parents[1]                        # testlab/tests: its conftest, as a plugin
    env = {k: v for k, v in os.environ.items() if k != "LHPC_LAB_SERIAL_HELD"}
    env.update(LHPC_TESTLAB_LOCK=str(lock), PYTHONPATH=f"{here}:{env.get('PYTHONPATH', '')}")
    child = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
                              "--rootdir", str(tmp_path), str(tmp_path / "test_holder.py")], env=env, cwd=tmp_path,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(300):                                          # bounded: up to 30 s for the child to hold it
            if inside.exists() or child.poll() is not None:
                break
            time.sleep(0.1)  # rule-1 exception: waits on another process (the child's hold)
        assert inside.exists(), child.communicate(timeout=30)[0][-1500:]
        monkeypatch.setattr(testing, "LAB_LOCK_PATH", str(lock), raising=False)
        monkeypatch.setattr(testing, "run_lab", lambda *a, **k: None)    # the lab's init/reset are not the case
        monkeypatch.setattr(testing, "LAB_LOCK_WAIT_S", 0.5, raising=False)
        lab = LabServer(tmp_path / "runtime")
        try:
            lab.init_and_reset()
            raise AssertionError("the lab came up while a fake-daemon test of another process held the host")
        except RuntimeError as exc:
            assert f"pid {child.pid} root test " in str(exc) and "test_holder" in str(exc), exc
        import threading
        threading.Timer(1.0, release.touch).start()                   # the test ends 1 s into the lab's wait
        monkeypatch.setattr(testing, "LAB_LOCK_WAIT_S", 30.0, raising=False)
        lab.init_and_reset()                                          # it waited, then took its turn: no refusal
        lab.stop()
        assert child.wait(timeout=30) == 0, child.communicate(timeout=5)[0][-1500:]
    finally:
        release.touch()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

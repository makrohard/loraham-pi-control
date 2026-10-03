"""B — the shared command runner bounds captured output in memory and terminates the whole
child process group (TERM→KILL) on timeout, so a runaway build/test cannot exhaust memory
or orphan sub-processes. Uses harmless local subprocesses (python3/sleep) — no RF/hardware."""

import time

import pytest

from lhpc.core.probes.backends import RealCommandRunner, _MAX_CAPTURE_BYTES


def test_large_output_is_bounded_in_memory():
    r = RealCommandRunner().run(
        ["python3", "-c", "import sys; sys.stdout.write('x' * (5 * 1024 * 1024))"], timeout=20)
    assert r.returncode == 0
    assert 0 < len(r.stdout) <= _MAX_CAPTURE_BYTES        # capped to the tail
    assert set(r.stdout) == {"x"}                          # the retained tail is real output


def test_stdout_stderr_kept_separate():
    r = RealCommandRunner().run(
        ["python3", "-c", "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"],
        timeout=10)
    assert r.stdout == "OUT" and r.stderr == "ERR"        # streams not merged (git parsing safe)


def test_timeout_kills_process_group_promptly():
    t0 = time.time()
    r = RealCommandRunner().run(["sleep", "30"], timeout=0.4)
    assert r.timed_out and r.returncode == 124
    assert time.time() - t0 < 5.0                          # killed, not waited out


def test_timeout_terminates_child_tree():
    # A parent that spawns a grandchild sleep in the SAME session; on timeout the whole
    # group is killed, so the grandchild does not outlive the run.
    prog = ("import subprocess, time, sys;"
            "p = subprocess.Popen(['sleep', '30']);"
            "sys.stdout.write(str(p.pid) + '\\n'); sys.stdout.flush();"
            "time.sleep(30)")
    r = RealCommandRunner().run(["python3", "-c", prog], timeout=0.6)
    assert r.timed_out
    child_pid = int(r.stdout.strip().splitlines()[0])
    time.sleep(0.3)
    # The grandchild was killed with the group. Use the PRODUCTION liveness predicate, which reads
    # /proc/<pid>/stat and treats a reaped-pending zombie (Z/X) as ceased — `kill -0` would call a
    # zombie "alive", so it fails on any non-reaping init (containers/some CI).
    from lhpc.core import procident
    assert not procident.proc_alive(child_pid)


def _dead_or_zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            st = fh.read()
        return st[st.rindex(")") + 2] in ("Z", "X", "x")
    except (OSError, ValueError):
        return True                                       # /proc gone -> dead


def test_timeout_kills_term_ignoring_child_that_outlives_parent():
    # The parent spawns a child that IGNORES SIGTERM and, on its own SIGTERM, the parent
    # exits immediately — so the leader dies but the child survives in the original session.
    # The runner must detect the surviving session member and SIGKILL the whole group.
    import sys
    prog = (
        "import signal, subprocess, sys, os, time\n"
        "child = subprocess.Popen([sys.executable, '-c',"
        " 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
        "sys.stdout.write(str(child.pid) + '\\n'); sys.stdout.flush()\n"
        "signal.signal(signal.SIGTERM, lambda *a: os._exit(0))\n"   # leader exits on TERM
        "time.sleep(60)\n"
    )
    r = RealCommandRunner().run([sys.executable, "-c", prog], timeout=0.6)
    assert r.timed_out
    child_pid = int(r.stdout.strip().splitlines()[0])
    for _ in range(60):                                   # bounded wait for the KILL to land
        if _dead_or_zombie(child_pid):
            break
        time.sleep(0.1)
    assert _dead_or_zombie(child_pid)                     # TERM-ignoring child was SIGKILLed


def test_proctree_valid_token_kills_term_ignoring_child():
    # #13: a VALID token still kills a TERM-ignoring child that outlives its parent.
    import subprocess, sys, os, time
    from lhpc.core import proctree
    p = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, signal, time\n"
         "subprocess.Popen(['sleep', '60'])\n"           # child in the same session
         "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"  # leader ignores TERM
         "time.sleep(60)\n"],
        start_new_session=True)
    token = proctree.capture_session_token(p.pid)         # FULL typed token at spawn
    assert token and token.sid == p.pid and token.pgid == p.pid
    for _ in range(50):
        if proctree.session_members(p.pid, os.getpid()):
            break
        time.sleep(0.05)
    res = proctree.terminate_session(token, os.getpid())  # TERM ignored -> escalates to KILL
    try:
        p.wait(timeout=2)
    except Exception:
        pass
    assert res == proctree.Termination.TERMINATED         # whole session cleared
    assert not proctree.session_members(p.pid, os.getpid())


def test_proctree_missing_or_incomplete_token_never_signals():
    # #11: no/incomplete token -> fail closed, UNVERIFIED, never signal.
    import subprocess, sys, os, dataclasses
    from lhpc.core import proctree
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         start_new_session=True)
    try:
        assert proctree.terminate_session(None, os.getpid()) is proctree.Termination.UNVERIFIED
        real = proctree.capture_session_token(p.pid)
        incomplete = dataclasses.replace(real, starttime=0)     # zeroed field -> not complete
        assert proctree.terminate_session(incomplete, os.getpid()) is proctree.Termination.UNVERIFIED
        assert p.poll() is None                            # NOT signalled
    finally:
        p.kill(); p.wait()


def _raw_token(pid):
    """A SessionToken for ANY pid, bypassing capture_session_token's session-leader
    invariant — these tests intentionally probe NON-leader pids to exercise
    terminate_session's fail-closed ownership checks."""
    from lhpc.core import proctree
    import os
    with open(f"/proc/{pid}/stat") as fh:
        data = fh.read()
    rest = data[data.rindex(")") + 2:].split()
    return proctree.SessionToken(pid=pid, starttime=int(rest[19]),
                                 sid=os.getsid(pid), pgid=os.getpgid(pid))


def test_proctree_wrong_token_does_not_signal():
    # #12: a live pid that is NOT a session leader + a WRONG start-time token -> neither the
    # token nor a session member matches -> nothing is signalled.
    import subprocess, sys, os, dataclasses
    from lhpc.core import proctree
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # no new session
    try:
        wrong = dataclasses.replace(_raw_token(p.pid), starttime=987654321)
        res = proctree.terminate_session(wrong, os.getpid(), term_grace=0.2, kill_grace=0.2)
        assert not res.ok and p.poll() is None             # NOT signalled (ownership not proven)
    finally:
        p.kill(); p.wait()


def test_proctree_absent_pid_no_throw():
    from lhpc.core import proctree
    tok = proctree.SessionToken(pid=999_999_999, starttime=123, sid=999_999_999, pgid=999_999_999)
    res = proctree.terminate_session(tok, 1)               # nothing owned, no throw
    assert res is proctree.Termination.ALREADY_CEASED and res.ok


def test_proctree_never_signals_controller_group():
    # §1 #6: a token that resolves to the CONTROLLER's own group is never signalled — a
    # stale/mismatched token must not authorize killing ourselves (if it did, pytest dies).
    import os, dataclasses
    from lhpc.core import proctree
    stale = dataclasses.replace(_raw_token(os.getpid()), starttime=987654321)  # stale
    res = proctree.terminate_session(stale, os.getpid(), term_grace=0.1, kill_grace=0.1)
    assert res is proctree.Termination.UNVERIFIED                     # no signal; we're alive


def test_command_result_may_still_be_running_flags():
    from lhpc.core.probes.backends import CommandResult
    assert CommandResult(124, "", "", timed_out=True, termination="incomplete").may_still_be_running
    assert CommandResult(124, "", "", timed_out=True, termination="unverified").may_still_be_running
    assert not CommandResult(124, "", "", timed_out=True, termination="terminated").may_still_be_running
    assert not CommandResult(0, "", "").may_still_be_running


def test_runner_surfaces_termination_status_on_timeout():
    # #11: a normal timeout is cleanly TERMINATED and the status is carried on the result.
    r = RealCommandRunner().run(["sleep", "30"], timeout=0.4)
    assert r.timed_out and r.returncode == 124
    assert r.termination == "terminated" and not r.may_still_be_running


def test_proctree_kills_setpgrp_descendant_in_session():
    # #12: a descendant that calls setpgrp() (new group, SAME session) and ignores TERM,
    # outliving its leader, is still reached — we signal EVERY group in the private session.
    import subprocess, sys, os, time
    from lhpc.core import proctree
    prog = (
        "import os, signal, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c',"
        " 'import os,signal,time; os.setpgrp();"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
        "sys.stdout.write(str(child.pid) + '\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.PIPE,
                         start_new_session=True)
    try:
        token = proctree.capture_session_token(p.pid)
        child_pid = int(p.stdout.readline())
        res = proctree.terminate_session(token, os.getpid())
        try:
            p.wait(timeout=3)
        except Exception:
            pass
        for _ in range(60):
            if _dead_or_zombie(child_pid):
                break
            time.sleep(0.1)
        assert _dead_or_zombie(child_pid)                    # setpgrp child was reached + killed
        assert res == proctree.Termination.TERMINATED
    finally:
        try:
            p.kill(); p.wait()
        except Exception:
            pass


def test_setsid_descendant_is_outside_proven_ownership():
    # §4: a descendant that calls setsid() LEAVES our private session and is OUTSIDE the
    # proven ownership set — session_members does not include it (documented behavior: we
    # neither see nor claim to have killed a session escapee; we never falsely report it).
    import subprocess, sys, os, time
    from lhpc.core import proctree
    prog = ("import os, subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import os,time; os.setsid(); time.sleep(30)'])\n"
            "sys.stdout.write(str(c.pid) + '\\n'); sys.stdout.flush()\n"
            "time.sleep(30)\n")
    p = subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.PIPE,
                         start_new_session=True)
    child = None
    try:
        child = int(p.stdout.readline())
        for _ in range(200):            # bounded wait for the grandchild's setsid() to land
            if os.getsid(child) != p.pid:   # p was spawned start_new_session -> sid == p.pid
                break
            time.sleep(0.02)
        members = proctree.session_members(p.pid, os.getpid())
        assert child not in members                             # setsid escapee not in our session
        assert p.pid in members                                 # the leader IS ours (so members is non-empty)
    finally:
        if child:
            try:
                os.kill(child, 9)
            except OSError:
                pass
        p.kill(); p.wait()


# --- F42: a build step ends on a stall, not on the clock ------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("controlled", [False, True], ids=["fast", "controlled"])
def test_sleeping_step_is_stalled(tmp_path, controlled):
    # A step whose whole session sleeps (no CPU, no I/O, no output after its first line) is ended
    # by the stall rule long before its ceiling, and no member survives.
    log = tmp_path / "step.log"
    kw = {"should_cancel": lambda: False} if controlled else {}
    t0 = time.time()
    with open(log, "wb" if controlled else "w") as fh:
        r = RealCommandRunner().run_streaming(["sh", "-c", "sleep 30 & echo $!; wait"],
                                              timeout=60, log_fh=fh, stall_s=1, sample_s=0.2, **kw)
    assert r.timed_out and r.returncode == 124 and r.stop_reason == "stalled"
    assert time.time() - t0 < 5.0
    time.sleep(0.3)
    assert _dead_or_zombie(int(log.read_text().split()[0]))    # the background sleep is gone too


@pytest.mark.slow
@pytest.mark.parametrize("controlled", [False, True], ids=["fast", "controlled"])
def test_a_busy_step_still_ends_at_its_ceiling(tmp_path, controlled):
    # preservation: the ceiling still applies to a step that is busy all the time.
    kw = {"should_cancel": lambda: False} if controlled else {}
    with open(tmp_path / "step.log", "wb" if controlled else "w") as fh:
        r = RealCommandRunner().run_streaming(["python3", "-c", "while True: pass"], timeout=1.5,
                                              log_fh=fh, stall_s=1, sample_s=0.2, **kw)
    assert r.timed_out and r.stop_reason == "budget"


@pytest.mark.slow
@pytest.mark.parametrize("controlled", [False, True], ids=["fast", "controlled"])
def test_a_step_cannot_outlive_its_ceiling_by_a_wait_slice(tmp_path, controlled):
    # Ceiling 1.5 s, sample 1 s: a 1.8 s step ends at the ceiling — each wait is capped by the time
    # REMAINING to the ceiling, not by the whole ceiling.
    kw = {"should_cancel": lambda: False} if controlled else {}
    with open(tmp_path / "step.log", "wb" if controlled else "w") as fh:
        r = RealCommandRunner().run_streaming(["sleep", "1.8"], timeout=1.5, log_fh=fh,
                                              sample_s=1.0, **kw)
    assert r.timed_out and r.returncode == 124 and r.stop_reason == "budget"


# A child whose descendant leaves the session (setsid) still holding stdout, prints that
# descendant's pid and exits at once. The escapee outlives the call by design; it is killed after.
_ESCAPEE = ["sh", "-c", "setsid sleep 8 & echo $!; echo hi"]


def _kill(pid_text):
    import os
    try:
        os.kill(int(pid_text.split()[0]), 9)
    except (ValueError, IndexError, OSError):
        pass


def test_run_returns_while_an_escaped_descendant_holds_stdout():
    # Closing the pipe used to wait for the drain thread's buffer lock, i.e. for the escapee to
    # exit: the bounded joins did not bound the call.
    t0 = time.time()
    r = RealCommandRunner().run(_ESCAPEE, timeout=20)
    try:
        assert time.time() - t0 < 6.0
        assert r.returncode == 0 and r.stdout.split()[1:] == ["hi"]
    finally:
        _kill(r.stdout)


def test_streaming_run_returns_and_flags_unverified_output(tmp_path):
    log = tmp_path / "job.log"
    t0 = time.time()
    with open(log, "wb") as fh:
        r = RealCommandRunner().run_streaming(_ESCAPEE, timeout=20, log_fh=fh,
                                              should_cancel=lambda: False)
    try:
        assert time.time() - t0 < 6.0
        assert r.returncode == 0 and r.output_unverified          # the pipe never reached EOF
        assert log.read_text().split()[1:] == ["hi"]
    finally:
        _kill(log.read_text())

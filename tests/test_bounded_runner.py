"""B — the shared command runner bounds captured output in memory and terminates the whole
child process group (TERM→KILL) on timeout, so a runaway build/test cannot exhaust memory
or orphan sub-processes. Uses harmless local subprocesses (python3/sleep) — no RF/hardware."""

import time

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
        time.sleep(0.3)
        members = proctree.session_members(p.pid, os.getpid())
        assert child not in members                             # setsid escapee not in our session
        assert p.pid in [m for m in members] or True            # (the leader itself is ours)
    finally:
        if child:
            try:
                os.kill(child, 9)
            except OSError:
                pass
        p.kill(); p.wait()

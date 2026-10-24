"""The `kill` verb, stage L3-K (A §2.12; PROOF T-K1–T-K4, T-K6, T-K7; Q-K1, Q-K2). Real processes
that ignore SIGTERM stand in for a stuck stack; /proc's argv for them comes from the fake procfs,
their identity from the real /proc. The 5 s grace runs on the injected clock (H3)."""
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lhpc.core import commands, jobs, kill, procident, reslock
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session
ADMISSION = ControllerService.ADMISSION_KEY
STUCK = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); signal.pause()"
REPO = Path(__file__).resolve().parents[2]


WATCHED: list = []      # the pidfds of the processes `procs` spawned, for the clock


class Clock:
    """The injected clock: counts the slept seconds and, per step, waits on the exit event of each
    spawned process (its pidfd), bounded, so a SIGKILLed one is seen gone without any sleep."""
    def __init__(self):
        self.t = 0.0

    def __call__(self, s):
        self.t += s
        live = [fd for fd in WATCHED if not select.select([fd], [], [], 0)[0]]   # exited: readable
        if live:
            select.select(live, [], [], 0.005)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(kill, "sleep", c)
    return c


@pytest.fixture
def procs():
    started = []

    def spawn(argv=None, code=STUCK, leader=True, **kw):
        p = subprocess.Popen(argv or [sys.executable, "-c", code], start_new_session=leader,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=REPO, **kw)
        assert p.stdout.readline().strip() in (b"up", b"held")      # its handler is installed
        started.append(p)
        WATCHED.append(os.pidfd_open(p.pid))
        return p
    yield spawn
    for p in started:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)
    while WATCHED:
        os.close(WATCHED.pop())


def _svc(tmp_path, table):
    return ControllerService(system=FakeSystem(cmdlines_data=table).system,
                             paths=Paths(runtime_root=tmp_path))


def _argv(svc, comp_id, band):
    """The component's real argv on `band` (what its start launches)."""
    comp = next(c for st in svc.stacks() for c in st.components if c.id == comp_id)
    src = str(svc._paths.resolve_source(comp.source.path)) if comp.source else str(svc._paths.runtime_root)
    return commands.expand_argv(comp.run_argv, comp, svc.stack_config(comp.id, band),
                                Config().operator, str(svc._paths.runtime_root), src, band)


def _gone(p):
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return True


def test_k1_a_process_ignoring_sigterm_is_gone_after_the_5s_grace(tmp_path, clock, procs):
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-kiss-tnc", "433")})
    res = kill.kill(svc, "kiss@433", apply=True)
    assert res.ok, res.summary
    assert _gone(p) and p.returncode == -signal.SIGKILL      # SIGTERM was ignored: SIGKILL ended it
    assert round(clock.t, 6) >= kill.GRACE_S      # only after the grace, on the clock


def test_k3_a_kill_of_one_instance_never_touches_the_other_nor_the_daemon(tmp_path, clock, procs):
    p433, p868, daemon = procs(), procs(), procs()
    svc = _svc(tmp_path, {})
    table = {p433.pid: _argv(svc, "loraham-kiss-tnc", "433"),
             p868.pid: _argv(svc, "loraham-kiss-tnc", "868"),
             daemon.pid: ["/x/loraham_daemon", "--radio", "433"]}
    assert table[p433.pid] != table[p868.pid]
    svc = _svc(tmp_path, table)
    assert kill.kill(svc, "kiss@433", apply=True).ok
    assert _gone(p433)
    assert p868.poll() is None and daemon.poll() is None     # the other band and the daemon live


def test_the_daemon_goes_per_band_by_its_radio_parse(tmp_path, clock, procs):
    d433, d868 = procs(), procs()
    table = {d433.pid: ["/x/loraham_daemon", "--radio", "433"],
             d868.pid: ["/x/loraham_daemon", "--radio=868"]}
    svc = _svc(tmp_path, table)
    sid = svc.stack_of(svc.DAEMON_ID)
    assert kill.kill(svc, f"{sid}@868", apply=True).ok
    assert _gone(d868) and d433.poll() is None


def test_k4_a_user_started_chat_is_killed(tmp_path, clock, procs):
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-chat", "433")})     # pasted CLI line
    plan = kill.kill(svc, "chat")
    assert plan.ok and plan.data["pids"] == [p.pid]                     # the confirm names it
    assert kill.kill(svc, "chat", apply=True).ok and _gone(p)


def test_records_are_scoped_like_the_matcher(tmp_path, clock, procs):
    # T-R6 two-record subcase (W88, P2-9 F2): a component kill takes only its own verified
    # record, a whole-stack kill all of them. No argv matches: only the records name them.
    bridge, qemu = procs(), procs()
    svc = _svc(tmp_path, {})
    stk = svc.stack("meshcom")
    life = svc._lifecycle()
    for p, cid in ((bridge, "meshcom-bridge"), (qemu, "meshcom-qemu")):
        life.record_launch(stk, stk.component(cid), p.pid, band="433")
    assert kill.kill(svc, "meshcom", "meshcom-bridge", apply=True).ok
    assert _gone(bridge) and qemu.poll() is None
    assert kill.kill(svc, "meshcom", apply=True).ok and _gone(qemu)
    assert life.owned_records("meshcom-qemu", None) == []                # its record is removed


HOLD = """
import signal, sys
from pathlib import Path
from lhpc.core import reslock
from lhpc.core.paths import Paths
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with reslock.operation_lock(Paths(runtime_root=Path(sys.argv[1])), "controller-task-admission",
                            sys.argv[2], sys.argv[3]):
    print("held", flush=True)
    signal.pause()
"""


def _holder(procs, tmp_path, op, target):
    return procs(argv=[sys.executable, "-c", HOLD, str(tmp_path), op, target])


def test_k2_a_web_start_holding_the_flock_is_killed(tmp_path, clock, procs, monkeypatch):
    svc = _svc(tmp_path, {})
    job = _holder(procs, tmp_path, "start", "kiss")
    assert jobs.write_job_marker(svc._paths, "start-kiss-1", job.pid, "kiss", "start",
                                 ident=procident.proc_identity(job.pid))
    life = svc._lifecycle()
    log = life.logs_dir() / "start-loraham-kiss-tnc-433.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("starting\n")
    seen = []
    real = kill._signal

    def during_sweep(svc_, targets):
        # While kill sweeps, it holds the admission flock: no start can begin.
        with pytest.raises(reslock.ResourceBusy), reslock.operation_lock(
                svc._paths, ADMISSION, "start", "kiss"):
            pass
        seen.append(True)
        return real(svc_, targets)
    monkeypatch.setattr(kill, "_signal", during_sweep)
    res = kill.kill(svc, "kiss@433", apply=True)
    assert res.ok, res.summary
    assert _gone(job) and job.returncode == -signal.SIGKILL and seen
    with reslock.operation_lock(svc._paths, ADMISSION, "start", "kiss"):
        pass                                                      # the flock is free
    assert not list(jobs.jobs_dir(svc._paths).glob("*.job"))      # the marker is cleared
    assert "killed by operator" in log.read_text()


def _lhpc_script(tmp_path):
    """A program named `lhpc` whose argv reads `lhpc stack start kiss`, holding the flock."""
    s = tmp_path / "bin" / "lhpc"
    s.parent.mkdir()
    # argv `lhpc stack start kiss <runtime root>`: the op and target are argv 2 and 3.
    s.write_text(f"#!{sys.executable}\n" + HOLD.replace("sys.argv[1]", "sys.argv[4]"))
    s.chmod(0o755)
    return s


def test_k7_a_cli_start_group_leader_is_ended(tmp_path, clock, procs):
    cli = procs(argv=[str(_lhpc_script(tmp_path)), "stack", "start", "kiss", str(tmp_path)])
    svc = _svc(tmp_path, {})
    res = kill.kill(svc, "kiss", apply=True)
    assert res.ok, res.summary
    assert _gone(cli)


def test_k7_a_cli_start_that_cannot_be_ended_is_refused_by_name_and_never_hangs(tmp_path, clock,
                                                                                  procs):
    cli = procs(argv=[str(_lhpc_script(tmp_path)), "stack", "start", "kiss", str(tmp_path)],
                leader=False)                        # in our process group: no group leader
    svc = _svc(tmp_path, {})
    t0 = time.monotonic()
    res = kill.kill(svc, "kiss", apply=True)
    assert not res.ok and f"(pid {cli.pid})" in res.summary and "could not be ended" in res.summary
    assert cli.poll() is None and time.monotonic() - t0 < 5     # never signalled, never waited


def test_q_k2_a_holder_of_another_operation_is_refused_by_name(tmp_path, clock, procs):
    h = _holder(procs, tmp_path, "build", "graywolf")
    res = kill.kill(_svc(tmp_path, {}), "kiss", apply=True)
    assert not res.ok and f"pid {h.pid} (" in res.summary and "build graywolf" in res.summary
    assert h.poll() is None


def test_q_k2_another_task_of_the_same_stack_is_refused_not_ended(tmp_path, clock, procs):
    # A web build of kiss holds the flock, verifiable by its marker: only a start is ended.
    svc = _svc(tmp_path, {})
    h = _holder(procs, tmp_path, "build", "kiss")
    assert jobs.write_job_marker(svc._paths, "build-kiss-1", h.pid, "kiss", "build",
                                 ident=procident.proc_identity(h.pid))
    res = kill.kill(svc, "kiss", apply=True)
    assert not res.ok and f"pid {h.pid} (" in res.summary and "build kiss" in res.summary
    assert h.poll() is None


RAW = """
import fcntl, signal, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
fh = open(sys.argv[1], "a")
fcntl.flock(fh, fcntl.LOCK_EX)
if len(sys.argv) > 2:
    open(sys.argv[2], "w").write(sys.argv[3])
print("held", flush=True)
signal.pause()
"""


@pytest.mark.parametrize("owner,why", [
    (None, "unverifiable holder (no readable owner record)"),
    ("{not json", "unverifiable holder (no readable owner record)"),
    (json.dumps({"pid": "12x", "operation": "start", "target": "kiss"}),
     "unverifiable holder (corrupt owner record)"),
], ids=["missing", "unparsable", "corrupt-pid"])
def test_q_k2_an_unverifiable_holder_is_refused(tmp_path, clock, procs, owner, why):
    svc = _svc(tmp_path, {})
    lock = reslock.lock_file_path(svc._paths, ADMISSION)
    lock.parent.mkdir(parents=True, exist_ok=True)
    extra = [] if owner is None else [str(lock.with_suffix(".owner")), owner]
    h = procs(argv=[sys.executable, "-c", RAW, str(lock), *extra])
    res = kill.kill(svc, "kiss", apply=True)
    assert not res.ok and why in res.summary, res.summary
    assert h.poll() is None                                       # nothing signalled


def test_q_k1_terminate_stays_term_only_by_default(tmp_path, procs):
    p = procs()
    life = _svc(tmp_path, {})._lifecycle()
    ident = procident.proc_identity(p.pid)
    assert life._terminate_unobserved(p.pid, ident, sleep=Clock()) is False
    assert p.poll() is None                                       # no SIGKILL without the opt-in
    assert life._terminate_unobserved(p.pid, ident, kill_after=5.0, sleep=Clock())
    assert _gone(p) and p.returncode == -signal.SIGKILL


def test_q_k1_the_kill_leg_re_checks_the_identity(tmp_path, procs, monkeypatch):
    p = procs()
    life = _svc(tmp_path, {})._lifecycle()
    ident = procident.proc_identity(p.pid)
    reads = iter([ident, {**ident, "starttime": ident["starttime"] + 1}])
    monkeypatch.setattr(life, "_proc_identity", lambda pid: next(reads))
    assert life._terminate_unobserved(p.pid, ident, kill_after=5.0, sleep=Clock()) is False
    assert p.poll() is None                     # another process by now: never SIGKILLed


@pytest.mark.parametrize("group", [False, True], ids=["pid", "group"])
def test_no_signal_reaches_a_process_whose_identity_changed_after_the_scan(tmp_path, clock, procs, monkeypatch,
                                                                          group):
    # C28 (gate 1 G1-12, F1): the pid was reused between the scan and the first SIGTERM, so the live process
    # is not the target the scan captured. Nothing may be sent to it, neither the SIGTERM nor the SIGKILL.
    # Asserted at the send point (G1-12-2, F2): every os.kill / os.killpg call is recorded, then passed through.
    # A live target beside it shows the sends are seen. Red-before at f4fb027b: the stale target got SIGTERM.
    code = "import signal; print('up', flush=True); signal.pause()"     # dies on SIGTERM
    stale_p, live_p = procs(code=code), procs(code=code)
    svc = _svc(tmp_path, {})

    def target(p, ident):
        return {"pid": p.pid, "component": "loraham-kiss-tnc", "group": group, "argv": "", "ident": ident,
                **({"pgid": p.pid} if group else {})}
    ident = procident.proc_identity(stale_p.pid)
    stale = target(stale_p, {**ident, "starttime": ident["starttime"] - 1})   # another process's start time
    live = target(live_p, procident.proc_identity(live_p.pid))
    sent = []
    real_kill, real_killpg = os.kill, os.killpg

    def rec_kill(pid, sig):
        sent.append((pid, sig))
        real_kill(pid, sig)

    def rec_killpg(pgid, sig):
        sent.append((pgid, sig))
        real_killpg(pgid, sig)
    monkeypatch.setattr(os, "kill", rec_kill)
    monkeypatch.setattr(os, "killpg", rec_killpg)
    assert kill._signal(svc, [stale, live]) == []
    assert (live_p.pid, signal.SIGTERM) in sent                     # the live target was signalled
    assert [pid for pid, _sig in sent if pid == stale_p.pid] == []   # the stale one never was
    live_p.wait(timeout=5)                                           # a cleanup bound, not the proof


def test_an_unreadable_identity_during_the_grace_keeps_the_target(tmp_path, clock, procs, monkeypatch):
    # C29 (a regression of C28, found by the second read of SA v4): one /proc read that fails during the grace is
    # not proof the process is gone. The target stays in the survivor list, gets its SIGKILL and ends.
    # Red-before at 2650a737: the read returning None once dropped the live target; _signal returned [] with the
    # SIGTERM-ignoring child still running.
    p = procs()                                                       # ignores SIGTERM
    svc = _svc(tmp_path, {})
    target = {"pid": p.pid, "component": "loraham-kiss-tnc", "group": False, "argv": "",
              "ident": procident.proc_identity(p.pid)}
    real, calls = procident.proc_identity, []

    def flaky(pid):
        calls.append(pid)
        return None if len(calls) == 2 else real(pid)                # the read after the SIGTERM fails once
    monkeypatch.setattr(procident, "proc_identity", flaky)
    assert kill._signal(svc, [target]) == []
    assert _gone(p) and p.returncode == -signal.SIGKILL


def test_k6_no_other_operation_calls_kill():
    pkg = REPO / "lhpc"
    call = re.compile(r"\b_?kill\.kill\(")
    callers = sorted(str(f.relative_to(REPO)) for f in pkg.rglob("*.py") if call.search(f.read_text()))
    assert callers == ["lhpc/adapters/cli/main.py", "lhpc/adapters/web/app.py"], callers

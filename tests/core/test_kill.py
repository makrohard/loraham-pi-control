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
def signals(monkeypatch):
    """Every os.kill / os.killpg call as (pid or pgid, signal), recorded, then passed through."""
    sent, real_kill, real_killpg = [], os.kill, os.killpg

    def rec(real):
        def call(pid, sig):
            sent.append((pid, sig))
            real(pid, sig)
        return call
    monkeypatch.setattr(os, "kill", rec(real_kill))
    monkeypatch.setattr(os, "killpg", rec(real_killpg))
    return sent


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
                                                                                  procs, signals):
    # G2-B2 F5: the stand-in ignores SIGTERM, so its being alive proves nothing; the send point and the
    # injected clock do. Red-before: a TERM sent to the holder before the refusal passed the earlier form.
    cli = procs(argv=[str(_lhpc_script(tmp_path)), "stack", "start", "kiss", str(tmp_path)],
                leader=False)                        # in our process group: no group leader
    svc = _svc(tmp_path, {})
    res = kill.kill(svc, "kiss", apply=True)
    assert not res.ok and f"(pid {cli.pid})" in res.summary and "could not be ended" in res.summary
    assert signals == [] and clock.t == 0            # never signalled, never waited


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


def _web_start(svc, procs, tmp_path):
    """A web start of kiss in flight: a real process holding the admission flock, with its job
    marker, and no process of the stack yet."""
    job = _holder(procs, tmp_path, "start", "kiss")
    assert jobs.write_job_marker(svc._paths, "start-kiss-1", job.pid, "kiss", "start",
                                 ident=procident.proc_identity(job.pid))
    return job


def test_the_plan_counts_an_in_flight_start_of_the_stack(tmp_path, clock, procs):
    # C27 (docs/cli.md: "A start of the same stack that is still running is ended first"): with no
    # process of the stack yet, the dry run still names the start, so the apply runs.
    # Red-before at e7d24171: "Kill kiss: 0 process(es)." with changes 0.
    svc = _svc(tmp_path, {})
    job = _web_start(svc, procs, tmp_path)
    plan = kill.kill(svc, "kiss")
    assert plan.ok and plan.data["changes"] == 1, plan.summary
    assert any(f"pid {job.pid}" in d and "in-flight start" in d for d in plan.details), plan.details


def test_the_cli_kill_ends_an_in_flight_start_with_nothing_else_running(tmp_path, clock, procs,
                                                                        capsys):
    # Red-before at e7d24171: `lhpc kill kiss --yes` printed "Nothing to do." and the start ran on.
    from lhpc.adapters.cli.main import _apply_flow
    svc = _svc(tmp_path, {})
    job = _web_start(svc, procs, tmp_path)
    rc = _apply_flow(lambda a: kill.kill(svc, "kiss", apply=a), yes=True)
    assert "Nothing to do." not in capsys.readouterr().out
    assert rc == 0 and _gone(job) and job.returncode == -signal.SIGKILL


def test_another_task_holding_the_flock_is_not_counted(tmp_path, clock, procs):
    # A holder that is not a start of this stack is no target: the plan does not claim to end it.
    svc = _svc(tmp_path, {})
    _holder(procs, tmp_path, "build", "graywolf")
    assert kill.kill(svc, "kiss").data["changes"] == 0


class _Ident:
    """kill's procident with scripted identity reads: `reads(pid)` gives the next of `seq`, then its last."""
    def __init__(self, seq):
        self.seq, self.identity_matches = list(seq), procident.identity_matches

    def proc_identity(self, pid):
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


def test_a_record_target_keeps_the_records_identity_not_a_later_read(tmp_path, clock, procs, signals,
                                                                      monkeypatch):
    # G2-B2 F1: the record was verified, then its pid was reused before the target's identity was read. The
    # target keeps the record's identity, so the replacement is never signalled. Red-before at dd086a20: the
    # target took the replacement's identity and the replacement's group got the SIGTERM.
    p = procs()                                             # the process now at the recorded pid
    svc = _svc(tmp_path, {})
    stk = svc.stack("meshcom")
    svc._lifecycle().record_launch(stk, stk.component("meshcom-bridge"), p.pid, band="433")
    now = procident.proc_identity(p.pid)
    monkeypatch.setattr(kill, "procident", _Ident([{**now, "starttime": now["starttime"] + 1}]))
    assert kill.kill(svc, "meshcom", "meshcom-bridge", apply=True).ok
    assert [s for s in signals if s[0] == p.pid] == []


def test_a_matcher_hit_whose_identity_changes_during_collection_is_no_target(tmp_path, procs, monkeypatch):
    # G2-B2 F1: a matcher hit is bound to one identity: read, then the argv re-read, then read again. Another
    # start time on the second read is another process, so it is not taken. Red-before at dd086a20: taken.
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-chat", "433")})
    now = procident.proc_identity(p.pid)
    monkeypatch.setattr(kill, "procident", _Ident([now, {**now, "starttime": now["starttime"] + 1}]))
    stk, band, comps, _err = kill._parse(svc, "chat", "")
    assert [t for t in kill._targets(svc, stk, band, comps)[0] if t["pid"] == p.pid] == []


def test_an_unreadable_identity_during_collection_blocks_an_empty_success(tmp_path, clock, procs, signals,
                                                                          monkeypatch):
    # G2-B2 F3: a matcher hit whose identity cannot be read is not dropped: the plan names it and is not ok, the
    # apply sends it nothing and does not report "no process of it is left". Red-before at 00c9d891: the plan
    # said 0 processes, ok, and the apply reported none left.
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-chat", "433")})
    monkeypatch.setattr(kill, "procident", _Ident([None]))
    plan = kill.kill(svc, "chat")
    assert not plan.ok and f"pid {p.pid}" in "\n".join(plan.details)
    res = kill.kill(svc, "chat", apply=True)
    assert not res.ok and "no process of it is left" not in res.summary and signals == []


def test_an_unresolved_instance_value_with_a_candidate_running_is_not_an_empty_plan(tmp_path, procs, monkeypatch):
    # G2-B2 F3: the component's instance value cannot be resolved while a process of its spec runs: the matcher
    # cannot scope it, so the plan reports the gap instead of 0 changes. Red-before at 00c9d891: 0 changes, ok.
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-chat", "433")})

    def broken(*_a, **_k):
        raise ValueError("no such parameter")
    monkeypatch.setattr(commands, "instance_value", broken)
    plan = kill.kill(svc, "chat@433")
    assert not plan.ok and "loraham-chat" in "\n".join(plan.details)


def test_an_exempt_component_under_another_band_is_refused_not_taken(tmp_path, procs):
    # G2-B2 F4: voice's argv names no band, so under @868 a running voice process proves nothing about 868:
    # without a verified 868 ownership record it is refused by name, not taken. Red-before at 63a8376c: the 433
    # process was planned for voice@868.
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-voice", "433")})
    plan = kill.kill(svc, "voice@868")
    assert not plan.ok and p.pid not in plan.data["pids"] and "cannot verify band 868" in "\n".join(plan.details)


def test_an_exempt_component_with_a_verified_record_of_the_band_is_taken(tmp_path, procs):
    # G2-B2 F4, the positive side: a verified ownership record of the requested band names it.
    p = procs()
    svc = _svc(tmp_path, {})
    svc = _svc(tmp_path, {p.pid: _argv(svc, "loraham-voice", "433")})
    stk = svc.stack("voice")
    svc._lifecycle().record_launch(stk, stk.component("loraham-voice"), p.pid, band="433")
    plan = kill.kill(svc, "voice@433", "loraham-voice")
    assert plan.ok and plan.data["pids"] == [p.pid]


def test_the_band_by_record_set_is_the_exemptions_owned_by_their_record():
    from lhpc.core.probes.process import NO_INSTANCE_ARG
    assert {k for k, why in NO_INSTANCE_ARG.items() if "ownership record" in why} == kill.BAND_BY_RECORD

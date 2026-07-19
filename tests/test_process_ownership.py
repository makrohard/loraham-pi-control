"""B — verified ownership: LHPC only signals a process whose full identity (pid,
start time, pgid, sid, executable, argv fingerprint) still matches an ownership
record AND that is an LHPC-owned session leader, not the controller's own group.
Uses controlled real subprocesses."""

import os
import signal
import subprocess
import time

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import Component, ComponentKind, ProcessSpec, Stack
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

STACK = Stack(id="daemon", name="d", main="loraham-daemon")
# ProcessSpec matches the real `sleep` we spawn (exact basename, no substring).
COMP = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                 process=ProcessSpec(exec_name="sleep"))


@pytest.fixture
def reaper():
    procs = []
    yield procs
    for p in procs:
        try:
            p.kill()          # single PID only — never killpg (could hit pytest's group)
            p.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _life(tmp_path, cmdlines=None):
    sys = FakeSystem(cmdlines_data=cmdlines or {}).system
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(), sys, spawn=lambda a, l: None)


def _leader(reaper):
    """A real detached process in its OWN session (as LHPC starts components).
    Wait until its /proc identity is fully observable (exe symlink ready), so a
    record taken now matches a later verify (avoids a startup race)."""
    p = subprocess.Popen(["sleep", "30"], start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reaper.append(p)
    for _ in range(50):
        ident = Lifecycle._proc_identity(p.pid)
        if ident and ident["exec"] == "sleep":
            break
        time.sleep(0.02)
    return p


@pytest.mark.needs_session
def test_owned_session_leader_is_verified_and_stopped(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    assert life.verify_owned(life.owned_records("loraham-daemon")[0])[0] is True
    res = life.stop(COMP)
    assert res.outcome.value == "stopped" and res.pid == p.pid
    assert life.owned_records("loraham-daemon") == []      # record cleared after cessation


def test_manual_matching_process_without_record_is_not_killed(tmp_path, reaper):
    p = _leader(reaper)
    # A process that matches the ProcessSpec is running, but LHPC has NO record.
    life = _life(tmp_path, cmdlines={p.pid: ["sleep", "30"]})
    res = life.stop(COMP)
    assert res.outcome.value == "manual_required" and not res.ok
    assert life._proc_alive(p.pid)                         # untouched


@pytest.mark.needs_session
def test_stale_record_after_process_exit_is_cleaned_not_blocked(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    p.terminate(); p.wait()                                # process exits -> record is stale
    res = life.stop(COMP)
    # The original process is PROVABLY gone: its stale record is DROPPED (never signalled),
    # and the stop converges to STOPPED instead of being blocked forever as "unverified".
    assert res.outcome.value == "stopped" and res.ok
    assert res.pid is None                                 # nothing was killed
    assert life.owned_records("loraham-daemon") == []      # stale record cleaned up


@pytest.mark.needs_session
def test_wrong_start_time_fails_verification(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    rec = life.owned_records("loraham-daemon")[0]
    rec["starttime"] = str(int(rec["starttime"]) + 999)    # tamper: pid reused scenario
    ok, why = life.verify_owned(rec)
    assert not ok and "starttime" in why


def test_controller_own_group_is_never_signalled(tmp_path, reaper):
    life = _life(tmp_path)
    # A child in the CONTROLLER's own session/group (no start_new_session).
    p = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reaper.append(p)
    life.record_launch(STACK, COMP, p.pid, band="433")
    rec = life.owned_records("loraham-daemon")
    # Either there is no record (record_launch may still capture it) — if captured,
    # verification must refuse it as the controller's own group / not a leader.
    if rec:
        ok, why = life.verify_owned(rec[0])
        assert not ok and ("own group" in why or "session leader" in why)
    assert life._proc_alive(p.pid)


@pytest.mark.needs_session
def test_per_band_records_are_independent(tmp_path, reaper):
    life = _life(tmp_path)
    a, b = _leader(reaper), _leader(reaper)
    life.record_launch(STACK, COMP, a.pid, band="433")
    life.record_launch(STACK, COMP, b.pid, band="868")
    assert len(life.owned_records("loraham-daemon")) == 2
    assert len(life.owned_records("loraham-daemon", band="433")) == 1   # band-scoped
    assert len(life.owned_records("loraham-daemon", band="868")) == 1   # the other band, independent


@pytest.mark.needs_session
def test_record_is_restrictive_permissions(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    f = life.owned_records("loraham-daemon")[0]["_path"]
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"


# --- §6.1/§6.2/§6.4 ownership retention + PID-reuse-safe cleanup --------------

@pytest.mark.needs_session
def test_ceased_process_with_lingering_endpoint_retains_record(tmp_path, reaper, monkeypatch):
    # Process ceases on SIGTERM, but a ready=true endpoint still answers (even after the
    # bounded grace poll) -> the stop is ENDPOINT_STILL_PRESENT and the record is retained.
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, comp, p.pid, band="433")
    # Genuinely-persistent endpoint (survives the grace) -> patch the grace-poll decision.
    monkeypatch.setattr(life, "_await_ready_endpoints_gone", lambda c: (False, ["127.0.0.1:9999"]))
    res = life.stop(comp)
    assert res.outcome.value == "endpoint_still_present" and not res.ok
    assert life.owned_records("loraham-daemon") != []      # evidence retained


def test_no_record_but_lingering_endpoint_is_not_already_stopped(tmp_path, monkeypatch):
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    life = _life(tmp_path)
    monkeypatch.setattr(life, "_await_ready_endpoints_gone", lambda c: (False, ["127.0.0.1:9999"]))
    res = life.stop(comp)
    assert res.outcome.value == "endpoint_still_present" and not res.ok


@pytest.mark.needs_session
def test_lingering_endpoint_that_closes_within_grace_converges_to_stopped(tmp_path, reaper, monkeypatch):
    # THE meshcom-qemu case: the tracked wrapper (run.sh) ceases immediately while its
    # backgrounded child (qemu) takes a beat to close its 12323 listener. The endpoint is
    # present on the first check(s) then gone -> the bounded grace poll must converge to a
    # clean STOPPED (record cleared), NOT a spurious ENDPOINT_STILL_PRESENT.
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    life = _life(tmp_path)
    monkeypatch.setattr(life, "STOP_POLL_S", 0.01)         # keep the test fast
    p = _leader(reaper)
    life.record_launch(STACK, comp, p.pid, band="433")
    calls = {"n": 0}

    def _fake_gone(c):
        calls["n"] += 1
        return (calls["n"] >= 3, [] if calls["n"] >= 3 else ["127.0.0.1:9999"])

    monkeypatch.setattr(life, "_ready_endpoints_gone", _fake_gone)
    res = life.stop(comp)
    assert res.outcome.value == "stopped" and res.ok
    assert calls["n"] >= 3                                  # it actually POLLED past the first check
    assert life.owned_records("loraham-daemon") == []      # record cleared on the clean stop


def test_endpoint_grace_is_bounded(tmp_path, reaper, monkeypatch):
    # A genuinely-stuck endpoint must NOT poll forever — bounded by STOP_ENDPOINT_GRACE_S.
    from lhpc.core.model import Component, ComponentKind, EndpointSpec
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    life = _life(tmp_path)
    monkeypatch.setattr(life, "STOP_POLL_S", 0.01)
    monkeypatch.setattr(life, "STOP_ENDPOINT_GRACE_S", 0.05)
    p = _leader(reaper)
    life.record_launch(STACK, comp, p.pid, band="433")
    calls = {"n": 0}

    def _always_present(c):
        calls["n"] += 1
        return (False, ["127.0.0.1:9999"])

    monkeypatch.setattr(life, "_ready_endpoints_gone", _always_present)
    res = life.stop(comp)
    assert res.outcome.value == "endpoint_still_present" and not res.ok
    assert 2 <= calls["n"] <= 12                            # polled a few times, then gave up


def test_terminate_unobserved_refuses_on_identity_mismatch(tmp_path, reaper):
    # A reused PID (different start time than captured at launch) must NOT be signalled.
    life = _life(tmp_path)
    p = _leader(reaper)
    fake_launch_ident = {"starttime": 1, "pgid": p.pid, "sid": p.pid, "exec": "sleep", "argv_fp": ""}
    signalled = life._terminate_unobserved(p.pid, fake_launch_ident)   # start times differ
    assert signalled is False
    assert life._proc_alive(p.pid)                         # untouched


@pytest.mark.needs_session
def test_terminate_unobserved_signals_matching_identity(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    ident = life._proc_identity(p.pid)                     # real current identity
    signalled = life._terminate_unobserved(p.pid, ident)
    assert signalled is True
    assert not life._proc_alive(p.pid)


# --- P0.2 ownership-record write is mandatory --------------------------------

def test_record_launch_symlink_leaf_rejected(tmp_path, reaper):
    import os
    life = _life(tmp_path)
    p = _leader(reaper)
    owned = life._owned_dir(); owned.mkdir(parents=True)
    outside = tmp_path / "evil.json"; outside.write_text("{}")
    os.symlink(outside, owned / f"loraham-daemon__433__{p.pid}.json")  # symlink leaf
    ok = life.record_launch(STACK, COMP, p.pid, band="433")
    assert ok is False                              # refused -> not owned
    assert outside.read_text() == "{}"              # link target untouched


def test_record_launch_write_failure_returns_false(tmp_path, reaper, monkeypatch):
    from lhpc.core import runtime_fs
    life = _life(tmp_path)
    p = _leader(reaper)
    monkeypatch.setattr(runtime_fs, "atomic_write",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert life.record_launch(STACK, COMP, p.pid, band="433") is False
    assert life.owned_records("loraham-daemon") == []   # nothing persisted


def test_start_fails_when_ownership_record_cannot_persist(tmp_path, monkeypatch):
    # A start whose ownership record can't be written must NOT report success — the
    # just-spawned session is cleaned up and the launch fails.
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core import runtime_fs
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.model import Component, ComponentKind, Stack
    cleaned = []
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=lambda argv, log, cwd=None, env=None: 4321)
    life.OBSERVE_TIMEOUT_S = 0.0
    monkeypatch.setattr(runtime_fs, "atomic_write",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(life, "_terminate_unobserved", lambda *a, **k: cleaned.append(a) or True)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))
    res = life.start(Stack(id="s", name="s", main="c"), comp)
    assert not res.ok                               # not reported started
    assert cleaned                                  # cleanup attempted


# --- P0.1 truthful cleanup + complete-identity ownership ---------------------

def test_incomplete_identity_blocks_ownership(tmp_path, reaper, monkeypatch):
    # An OBSERVED process whose /proc identity is incomplete must not be recorded.
    life = _life(tmp_path)
    p = _leader(reaper)
    monkeypatch.setattr(type(life), "_proc_identity",
                        staticmethod(lambda pid: {"pid": pid, "exec": "sleep"}))  # no starttime
    assert life.record_launch(STACK, COMP, p.pid, band="433") is False
    assert life.owned_records("loraham-daemon") == []


def test_terminate_unobserved_returns_false_when_still_alive(tmp_path, reaper, monkeypatch):
    # If the residual process does NOT cease, cleanup must report False (not a lie).
    life = _life(tmp_path)
    p = _leader(reaper)
    ident = life._proc_identity(p.pid)
    monkeypatch.setattr(type(life), "_proc_alive", staticmethod(lambda pid: True))  # never dies
    # patch killpg to a no-op so the real sleeper isn't actually signalled fast
    import os as _os
    monkeypatch.setattr(_os, "killpg", lambda *a: None)
    life.OBSERVE_POLL_S = 0.0
    assert life._terminate_unobserved(p.pid, ident) is False


def test_terminate_unobserved_true_when_gone(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    p.terminate(); p.wait()                        # already ceased
    assert life._terminate_unobserved(p.pid) is True


def test_record_launch_rejects_none_identity(tmp_path, reaper, monkeypatch):
    life = _life(tmp_path)
    p = _leader(reaper)
    monkeypatch.setattr(type(life), "_proc_identity", staticmethod(lambda pid: None))
    assert life.record_launch(STACK, COMP, p.pid, band="433") is False


def test_record_launch_rejects_empty_exec(tmp_path, reaper, monkeypatch):
    life = _life(tmp_path)
    p = _leader(reaper)
    monkeypatch.setattr(type(life), "_proc_identity", staticmethod(
        lambda pid: {"starttime": 1, "pgid": pid, "sid": pid, "exec": "", "argv_fp": "ab"}))
    assert life.record_launch(STACK, COMP, p.pid, band="433") is False


def test_proc_ceased_transient_read_failure_is_not_cessation(tmp_path, reaper, monkeypatch):
    # /proc/<pid> still present but stat read raises a NON-ENOENT error -> not ceased.
    life = _life(tmp_path)
    p = _leader(reaper)
    import lhpc.core.lifecycle as L
    real_read = L.Path.read_text
    def flaky(self, *a, **k):
        if "/stat" in str(self):
            raise PermissionError("transient")
        return real_read(self, *a, **k)
    monkeypatch.setattr(L.Path, "read_text", flaky)
    assert life._proc_ceased(p.pid) is False     # transient failure != ceased


def test_empty_cmdline_argv_rejected_despite_nonempty_digest(tmp_path, reaper, monkeypatch):
    import hashlib
    life = _life(tmp_path)
    p = _leader(reaper)
    # argv_fp is a valid SHA-256 of an EMPTY cmdline, but argv_len == 0 -> not ownable.
    monkeypatch.setattr(type(life), "_proc_identity", staticmethod(lambda pid: {
        "starttime": 1, "pgid": pid, "sid": pid, "exec": "sleep",
        "argv_fp": hashlib.sha256(b"").hexdigest(), "argv_len": 0}))
    assert life.record_launch(STACK, COMP, p.pid, band="433") is False


@pytest.mark.needs_session
def test_passed_identity_is_not_silently_resubstituted(tmp_path, reaper, monkeypatch):
    # When a complete identity is passed in, record_launch must use IT (not re-read a
    # weaker one). Make a re-read return None; the passed complete identity still records.
    life = _life(tmp_path)
    p = _leader(reaper)
    good = life._proc_identity(p.pid)
    assert life._identity_complete(good)
    monkeypatch.setattr(type(life), "_proc_identity", staticmethod(lambda pid: None))
    assert life.record_launch(STACK, COMP, p.pid, band="433", ident=good) is True


# --- P0 stop truth: transient /proc, confirmed reuse, removal failure --------

@pytest.mark.needs_session
def test_transient_proc_error_during_stop_retains_record(tmp_path, reaper, monkeypatch):
    from lhpc.core.outcomes import Outcome
    import lhpc.core.lifecycle as L
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    # Make every /proc/<pid>/stat read raise a NON-ENOENT (transient) error.
    real_read = L.Path.read_text
    def flaky(self, *a, **k):
        if f"/proc/{p.pid}/stat" in str(self):
            raise PermissionError("transient")
        return real_read(self, *a, **k)
    monkeypatch.setattr(L.Path, "read_text", flaky)
    res = life.stop(COMP)
    assert res.outcome == Outcome.UNVERIFIED
    assert life.owned_records("loraham-daemon")             # record RETAINED as evidence


@pytest.mark.needs_session
def test_confirmed_pid_reuse_proves_cessation_without_signalling(tmp_path, reaper):
    # A record whose starttime no longer matches the live pid -> the original ceased
    # (PID reused). verify_owned must refuse to signal; _original_ceased proves cessation.
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    rec = life.owned_records("loraham-daemon")[0]
    rec = dict(rec); rec["starttime"] = str(int(rec["starttime"]) + 999999)
    assert life.verify_owned(rec)[0] is False               # identity mismatch -> no signal
    assert life._original_ceased(rec) is True               # reuse proves original gone


@pytest.mark.needs_session
def test_record_removal_failure_is_typed_unverified(tmp_path, reaper, monkeypatch):
    from lhpc.core.outcomes import Outcome
    life = _life(tmp_path)
    p = _leader(reaper)
    life.record_launch(STACK, COMP, p.pid, band="433")
    p.terminate(); p.wait()                                  # process ceases cleanly
    monkeypatch.setattr(type(life), "_remove_record", lambda self, rec: False)
    res = life.stop(COMP)
    assert res.outcome == Outcome.UNVERIFIED                 # ceased but record not removed
    assert life.owned_records("loraham-daemon")             # evidence retained


# --- P0.1 no signal without a COMPLETE captured launch identity --------------

def test_terminate_refuses_without_complete_identity(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    assert life._terminate_unobserved(p.pid, None) is False        # no identity -> no signal
    assert life._proc_alive(p.pid)                                  # NOT signalled
    assert life._terminate_unobserved(p.pid, {"starttime": 1}) is False   # partial -> no signal
    assert life._proc_alive(p.pid)


def test_terminate_refuses_on_identity_mismatch(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    ident = life._capture_identity(p.pid)
    # A changed START TIME / pgid / sid means it is NOT our process -> never signal.
    for field, val in (("starttime", 999999999), ("pgid", 424242), ("sid", 424242)):
        bad = dict(ident); bad[field] = val
        assert life._terminate_unobserved(p.pid, bad) is False
        assert life._proc_alive(p.pid)


@pytest.mark.needs_session
def test_terminate_tolerates_exec_argv_change(tmp_path, reaper):
    # A legitimate later exec (e.g. env -> bash) changes exec/argv but NOT start time, so the
    # process is still safely cleanable (matches the accepted stable-identity ownership model).
    life = _life(tmp_path)
    p = _leader(reaper)
    ident = life._capture_identity(p.pid)
    bad = dict(ident); bad["exec"] = "env"; bad["argv_fp"] = "deadbeef"
    assert life._terminate_unobserved(p.pid, bad) is True          # exec change tolerated -> cleaned
    assert not life._proc_alive(p.pid)


@pytest.mark.needs_session
def test_terminate_signals_and_verifies_with_complete_identity(tmp_path, reaper):
    life = _life(tmp_path)
    p = _leader(reaper)
    ident = life._capture_identity(p.pid)
    assert life._identity_complete(ident)
    assert life._terminate_unobserved(p.pid, ident) is True        # complete -> signalled + ceased
    assert not life._proc_alive(p.pid)


# --- shebang-wrapper launches are owned (interpreter-prefix in /proc cmdline) -------

def test_argv_confirms_exact_suffix_and_mismatch():
    # Direct binary: observed argv equals intended exactly.
    direct = b"qemu-system-xtensa\x00-nographic\x00"
    assert Lifecycle._argv_confirms(direct, ["qemu-system-xtensa", "-nographic"])
    # Shebang script: kernel prepends the interpreter -> intended is a trailing SUFFIX.
    wrapped = b"/bin/sh\x00scripts/run.sh\x00--env\x00qemu-headless-extradio-gpsd\x00"
    assert Lifecycle._argv_confirms(
        wrapped, ["scripts/run.sh", "--env", "qemu-headless-extradio-gpsd"])
    # An unrelated process whose argv does not END with the intended tokens is rejected.
    assert not Lifecycle._argv_confirms(b"/bin/sh\x00other.sh\x00", ["scripts/run.sh"])
    # A prefix-only overlap (intended is not a contiguous suffix) is rejected.
    assert not Lifecycle._argv_confirms(b"scripts/run.sh\x00--env\x00x\x00", ["scripts/run.sh"])
    assert not Lifecycle._argv_confirms(b"sh\x00run.sh\x00", [])   # empty intended never confirms


@pytest.mark.needs_session
def test_start_records_ownership_for_shebang_wrapper(tmp_path, reaper):
    # A run command that is a shebang SCRIPT which forks a child and waits (exactly the
    # meshcom-qemu `run.sh` shape) must still be OWNED: the launched pid is the
    # interpreter, whose /proc cmdline is `<interp> <script> <args>` — intended argv as a
    # suffix. Regression: this previously timed out unowned -> UNVERIFIED.
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.model import Component, ComponentKind, Stack
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\nsleep 30 &\nwait\n")
    os.chmod(script, 0o755)
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system)        # real _real_spawn (real Popen)
    comp = Component(id="meshcom-qemu", name="q", kind=ComponentKind.SERVICE,
                     run_argv=(str(script),))
    res = life.start(Stack(id="meshcom", name="m", main="meshcom-qemu"), comp)
    try:
        assert res.ok                            # launch is OWNED, not UNVERIFIED
        recs = life.owned_records("meshcom-qemu")
        assert recs and recs[0]["exec"] in ("sh", "dash", "bash")   # the interpreter pid
    finally:
        for rec in life.owned_records("meshcom-qemu"):
            try:
                os.killpg(rec["pgid"], signal.SIGKILL)   # reap wrapper + its child
            except OSError:
                pass


# --- 3.1: observed identity changes between spawn and marker persistence -------

def test_reused_pid_between_spawn_and_persist_is_not_owned(tmp_path, reaper):
    # The launch identity is captured at spawn; if the SAME pid presents a different
    # start time when we go to record (i.e. it exited and was reused), ownership must
    # NOT be persisted — the start is reported unverified, not owned.
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.model import Component, ComponentKind, Stack
    p = _leader(reaper)                                   # a real detached 'sleep'
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=lambda argv, log, cwd=None, env=None: p.pid)
    life.OBSERVE_TIMEOUT_S = 1.0
    life.OBSERVE_POLL_S = 0.02
    real_capture = life._capture_identity
    calls = {"n": 0}
    def capture(pid):
        calls["n"] += 1
        ident = real_capture(pid)
        # First capture = the launch identity; a LATER capture reports a different
        # start time (simulating a PID reused by a new process before we persist).
        if ident and calls["n"] >= 2:
            ident = dict(ident); ident["starttime"] = int(ident["starttime"]) + 9999
        return ident
    life._capture_identity = capture
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("sleep", "30"))
    status = life._observe_and_record(Stack(id="s", name="s", main="c"), comp, p.pid, "", ["sleep", "30"])
    assert status == "unverified"                        # reused pid -> not owned
    assert life.owned_records("c") == []                 # nothing persisted


# --- 3.2: a spawned job that cannot be marker-tracked is terminated, not orphaned --

@pytest.mark.needs_session
def test_untracked_job_spawn_is_terminated_not_orphaned(tmp_path, reaper, monkeypatch):
    from lhpc.core.services import ControllerService
    from lhpc.core import runtime_fs
    from lhpc.core.probes.backends import FakeSystem
    p = _leader(reaper)                                   # a real detached session leader
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    life = svc._lifecycle()
    # Marker persistence fails after the process is already spawned.
    monkeypatch.setattr(runtime_fs, "write_marker",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    err = svc._track_or_terminate(life, "build-x", p.pid, "x", "build")
    assert err and "could not be persisted" in err and "terminated" in err
    for _ in range(50):
        if not life._proc_alive(p.pid):
            break
        time.sleep(0.05)
    assert not life._proc_alive(p.pid)                   # the untracked process was killed
    assert svc.active_jobs() == []                        # and never recorded as a job


@pytest.mark.needs_session
def test_stop_drops_reused_pid_record_without_signalling(tmp_path, reaper):
    # The reported bug's safety case: a record whose pid is ALIVE but is a REUSED pid
    # (recorded start time differs from the live process) is STALE. Stop must DROP it,
    # NEVER signal the unrelated live process, and converge to STOPPED — not loop forever
    # as "unverified — ownership retained".
    import json
    from pathlib import Path
    life = _life(tmp_path)
    p = _leader(reaper)                              # an unrelated live session leader
    life.record_launch(STACK, COMP, p.pid, band="433")
    rec_path = Path(life.owned_records("loraham-daemon")[0]["_path"])
    data = json.loads(rec_path.read_text())
    data["starttime"] = int(data["starttime"]) + 999999   # simulate pid reuse
    rec_path.write_text(json.dumps(data))
    res = life.stop(COMP)
    assert res.outcome.value == "stopped" and res.ok
    assert life._proc_alive(p.pid)                  # the unrelated live process was NOT killed
    assert life.owned_records("loraham-daemon") == []     # stale record dropped


# --- #3: job markers use FULL identity + descriptor-safe enumeration ----------

def _svc_rt(tmp_path):
    from lhpc.core.services import ControllerService
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _write_reused_pid_marker(svc, name):
    # A marker for OUR live pid but with a TAMPERED start time -> simulates PID reuse
    # (the recorded process is gone; this pid now belongs to us/an unrelated process).
    from lhpc.core import procident
    d = svc._jobs_dir(); d.mkdir(parents=True, exist_ok=True)
    ident = procident.proc_identity(os.getpid())
    body = (f'pid = {os.getpid()}\n'
            f'starttime = {int(ident["starttime"]) + 999999}\n'
            f'pgid = {ident["pgid"]}\nsid = {ident["sid"]}\n'
            f'exec = "{ident["exec"]}"\nargv_fp = "{ident["argv_fp"]}"\nlog = "{name}"\n')
    (d / f"{name}.job").write_text(body)


def test_log_running_rejects_reused_pid_marker(tmp_path):
    svc = _svc_rt(tmp_path)
    _write_reused_pid_marker(svc, "build-x")
    assert svc.log_running("x", job="build-x") is False    # identity mismatch, not bare pid


def test_active_jobs_rejects_reused_pid_marker(tmp_path):
    svc = _svc_rt(tmp_path)
    _write_reused_pid_marker(svc, "build-x")
    assert svc.active_jobs() == []                          # reused-pid marker not "active"


def test_active_jobs_symlinked_jobs_dir_fails_closed(tmp_path):
    svc = _svc_rt(tmp_path)
    svc._paths.under("state").mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil-jobs"; outside.mkdir()
    (outside / "x.job").write_text('pid = 1\n')
    os.symlink(outside, svc._jobs_dir())                   # jobs dir is a symlink out
    assert svc.active_jobs() == []                          # unsafe dir -> no trusted jobs


def test_prune_logs_never_follows_symlinked_log(tmp_path):
    svc = _svc_rt(tmp_path)
    logs = svc._paths.under("logs"); logs.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "victim.log"; outside.write_text("KEEP")
    os.symlink(outside, logs / "evil.log")                 # symlinked log leaf
    svc.prune_logs()
    assert outside.read_text() == "KEEP"                    # never pruned through the symlink
    assert (logs / "evil.log").is_symlink()                # the symlink itself is left as-is


# --- #3: ONE shared identity-completeness rule for ownership + job markers -----

def test_identity_complete_predicate():
    from lhpc.core import procident
    ok = {"starttime": 123, "pgid": 5, "sid": 5, "exec": "app", "argv_fp": "ab", "argv_len": 10}
    assert procident.identity_complete(ok)
    assert not procident.identity_complete(None)
    assert not procident.identity_complete({**ok, "starttime": -1})    # sentinel
    assert not procident.identity_complete({**ok, "pgid": 0})          # non-positive
    assert not procident.identity_complete({**ok, "argv_len": 0})      # empty argv
    assert not procident.identity_complete({**ok, "exec": ""})         # blank exec
    assert not procident.identity_complete({k: v for k, v in ok.items() if k != "argv_len"})


def test_lifecycle_and_jobs_share_completeness_rule():
    from lhpc.core import procident
    assert Lifecycle._identity_complete is procident.identity_complete


def test_write_job_marker_refuses_incomplete_identity(tmp_path):
    svc = _svc_rt(tmp_path)
    bad = {"starttime": -1, "pgid": 1, "sid": 1, "exec": "x", "argv_fp": "a", "argv_len": 5}
    assert svc._write_job_marker("build-x", 12345, "x", "build", ident=bad) is False
    assert svc.active_jobs() == []                       # nothing persisted


def test_write_job_marker_refuses_empty_argv(tmp_path):
    svc = _svc_rt(tmp_path)
    bad = {"starttime": 100, "pgid": 1, "sid": 1, "exec": "x", "argv_fp": "a", "argv_len": 0}
    assert svc._write_job_marker("build-x", 12345, "x", "build", ident=bad) is False


def test_incomplete_marker_never_reported_active(tmp_path):
    from lhpc.core import procident
    svc = _svc_rt(tmp_path)
    d = svc._jobs_dir(); d.mkdir(parents=True, exist_ok=True)
    ident = procident.proc_identity(os.getpid())         # a LIVE pid, but marker omits argv_len
    body = (f'pid = {os.getpid()}\nstarttime = {ident["starttime"]}\n'
            f'pgid = {ident["pgid"]}\nsid = {ident["sid"]}\n'
            f'exec = "{ident["exec"]}"\nargv_fp = "{ident["argv_fp"]}"\nlog = "build-x"\n')
    (d / "build-x.job").write_text(body)
    assert svc.active_jobs() == []                        # incomplete record -> not active
    assert svc.log_running("x", job="build-x") is False


def test_symlinked_marker_leaf_not_active_and_no_crash(tmp_path):
    svc = _svc_rt(tmp_path)
    d = svc._jobs_dir(); d.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil.job"; outside.write_text('pid = 1\n')
    os.symlink(outside, d / "build-x.job")               # symlinked marker leaf
    assert svc.active_jobs() == []                        # skipped, never followed, no crash
    assert (d / "build-x.job").is_symlink()              # evidence retained


@pytest.mark.needs_session
def test_identity_tolerates_exec_change_same_starttime():
    # A process that exec's after launch (e.g. `#!/usr/bin/env bash`: env -> bash) changes its
    # exec/argv but NOT its start time. Ownership must still match (start time is reuse-proof);
    # requiring exec/argv wrongly disowned MeshCom's run.sh and blocked its stop.
    import os
    from lhpc.core import procident
    live = procident.proc_identity(os.getpid())
    assert live is not None
    rec = dict(live); rec["exec"] = "env"; rec["argv_fp"] = "0" * 64; rec["argv_len"] = 1
    assert procident.identity_matches(rec, os.getpid()) is True          # exec changed, same proc
    reused = dict(live); reused["starttime"] = int(live["starttime"]) + 7
    assert procident.identity_matches(reused, os.getpid()) is False       # different start = reuse

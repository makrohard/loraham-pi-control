"""The detached web-job child lifecycle: tracking gate → mark_gate_passed → mark_running → terminal,
all attempt-id-guarded (parallels the HMAC driver gate)."""

import os

from lhpc.core import build_launcher_runtime, jobresult, procident, webjob_gate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_LOG = "build-meshcom-qemu.log"
_A = "a" * 32
_B = "b" * 32


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def _job_marker(svc, attempt=_A):
    ident = procident.proc_identity(os.getpid())
    assert svc._write_job_marker(_LOG, os.getpid(), "meshcom-qemu", "build",
                                 ident=ident, attempt_id=attempt)


def _spec(tmp_path, argv, attempt=_A):
    return {"steps": [{"argv": argv, "env_items": []}], "cwd": str(tmp_path),
            "runtime_root": str(tmp_path), "lock_names": [], "index_lock_name": "",
            "result_name": _LOG, "attempt_id": attempt, "op": "build",
            "target": "meshcom-qemu", "stack": "meshcom"}


def test_gate_matches_tracked_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = _svc(tmp_path)
    _job_marker(svc, _A)
    assert webjob_gate.verify_tracked(svc._paths, _LOG, "build", "meshcom-qemu", _A, os.getpid())
    assert not webjob_gate.verify_tracked(svc._paths, _LOG, "build", "meshcom-qemu", _B, os.getpid())  # wrong attempt
    assert not webjob_gate.verify_tracked(svc._paths, _LOG, "test", "meshcom-qemu", _A, os.getpid())   # wrong op


def test_web_launcher_success_records_done(tmp_path, monkeypatch):
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = _svc(tmp_path)
    _job_marker(svc)
    assert jobresult.reserve(svc._paths, _LOG, _A, "build", "meshcom-qemu", "meshcom", [])
    build_launcher_runtime.run(_spec(tmp_path, ["true"]))
    assert jobresult._read_raw(svc._paths, _LOG)["state"] == "done"


def test_web_launcher_step_failure_records_failed_with_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = _svc(tmp_path)
    _job_marker(svc)
    jobresult.reserve(svc._paths, _LOG, _A, "build", "meshcom-qemu", "meshcom", [])
    try:
        build_launcher_runtime.run(_spec(tmp_path, ["false"]))
    except SystemExit as e:
        assert e.code == 1
    d = jobresult._read_raw(svc._paths, _LOG)
    assert d["state"] == "failed" and d["detail"]


def test_web_launcher_gate_refusal_leaves_startup_unverified(tmp_path, monkeypatch):
    # No job marker → gate never passes → child exits WITHOUT mutation; the reservation keeps
    # startup_unverified (→ the projection derives unsafe).
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.2")
    svc = _svc(tmp_path)
    jobresult.reserve(svc._paths, _LOG, _A, "build", "meshcom-qemu", "meshcom", [])
    raised = False
    try:
        build_launcher_runtime.run(_spec(tmp_path, ["true"]))
    except SystemExit:
        raised = True
    assert raised
    d = jobresult._read_raw(svc._paths, _LOG)
    assert d["state"] == "starting" and d["startup_unverified"] is True


def test_web_launcher_superseded_attempt_runs_no_step(tmp_path, monkeypatch):
    # The reservation holds a NEWER attempt (B); this child (A) passes its own gate but mark_running(A)
    # fails → it must run no step and leave B's reservation intact.
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = _svc(tmp_path)
    _job_marker(svc, _A)                                   # job marker tracks attempt A
    jobresult.reserve(svc._paths, _LOG, _B, "build", "meshcom-qemu", "meshcom", [])   # result marker is attempt B
    # sentinel file a build step would create — must NOT appear
    sentinel = tmp_path / "ran.txt"
    try:
        build_launcher_runtime.run(_spec(tmp_path, ["touch", str(sentinel)], attempt=_A))
    except SystemExit:
        pass
    assert not sentinel.exists()                           # gate passed (A) but mark_running(A) failed → no step
    assert jobresult._read_raw(svc._paths, _LOG)["attempt_id"] == _B   # B's reservation untouched


# ---- P1 race/correctness fixes: liveness, unverified-timeout, handshake, spawn/install ------------


def test_is_live_attempt_requires_log_attempt_and_identity(tmp_path):
    svc = _svc(tmp_path)
    _job_marker(svc, _A)
    assert webjob_gate.is_live_attempt(svc._paths, _LOG, _A)
    assert not webjob_gate.is_live_attempt(svc._paths, _LOG, _B)          # wrong attempt
    assert not webjob_gate.is_live_attempt(svc._paths, "test-other.log", _A)   # no marker


def _timeout_env(monkeypatch):
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    monkeypatch.setenv("LHPC_BUILD_STEP_TIMEOUT_S", "0.3")


def test_unverified_timeout_step_records_unsafe(tmp_path, monkeypatch):
    from lhpc.core import proctree
    _timeout_env(monkeypatch)
    monkeypatch.setattr(proctree, "terminate_session", lambda *a, **k: proctree.Termination.UNVERIFIED)
    svc = _svc(tmp_path)
    _job_marker(svc)
    jobresult.reserve(svc._paths, _LOG, _A, "build", "meshcom-qemu", "meshcom", [])
    try:
        build_launcher_runtime.run(_spec(tmp_path, ["sleep", "5"]))
    except SystemExit:
        pass
    assert jobresult._read_raw(svc._paths, _LOG)["state"] == "unsafe"     # cessation UNPROVEN → unsafe


def test_proven_terminated_timeout_step_records_failed(tmp_path, monkeypatch):
    from lhpc.core import proctree
    _timeout_env(monkeypatch)
    monkeypatch.setattr(proctree, "terminate_session", lambda *a, **k: proctree.Termination.TERMINATED)
    svc = _svc(tmp_path)
    _job_marker(svc)
    jobresult.reserve(svc._paths, _LOG, _A, "build", "meshcom-qemu", "meshcom", [])
    try:
        build_launcher_runtime.run(_spec(tmp_path, ["sleep", "5"]))
    except SystemExit:
        pass
    assert jobresult._read_raw(svc._paths, _LOG)["state"] == "failed"     # proven stop → ordinary failed


def test_handshake_uses_attempt_and_admitted_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "0.2")
    svc = _svc(tmp_path)
    assert jobresult.reserve(svc._paths, _LOG, _A, "build", "meshcom-qemu", "meshcom", [])
    # a marker for ANOTHER attempt = superseded → blocked immediately (not pending)
    assert svc._web_admit_handshake(_LOG, _B) == ("blocked", "job attempt was superseded")
    # still starting for OUR attempt → pending after the (short) window
    assert svc._web_admit_handshake(_LOG, _A)[0] == "pending"
    # admitted → admitted
    jobresult.mark_gate_passed(svc._paths, _LOG, _A); jobresult.mark_running(svc._paths, _LOG, _A)
    assert svc._web_admit_handshake(_LOG, _A) == ("admitted", "")
    # ran then failed (admitted=True) → still admitted; a never-admitted terminal → blocked
    jobresult.terminalize(svc._paths, _LOG, _A, "failed", detail="step failed")
    assert svc._web_admit_handshake(_LOG, _A) == ("admitted", "")
    lg2 = "build-x.log"
    jobresult.reserve(svc._paths, lg2, _A, "build", "meshcom-qemu", "meshcom", [])
    jobresult.terminalize(svc._paths, lg2, _A, "failed", detail="blocked: lock busy")  # never admitted
    assert svc._web_admit_handshake(lg2, _A)[0] == "blocked"


def _fake_spawn(monkeypatch, pid):
    from lhpc.core.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle, "spawn_job",
                        lambda self, name, argv, cwd, env=None: (f"{name}.log", pid))


def test_spawn_web_job_proven_terminated_primary_blocks_no_secondaries(tmp_path, monkeypatch):
    import os
    svc = _svc(tmp_path)
    _fake_spawn(monkeypatch, os.getpid())
    monkeypatch.setattr(ControllerService, "_track_or_terminate",
                        lambda self, life, ln, pid, cid, op, attempt_id="":
                        f"{op} '{cid}' spawned but its job marker could not be persisted; "
                        "the process was terminated (not left orphaned).")
    log, admission, reason = svc.spawn_web_job("build", "meshcom")
    assert log is None and admission == "blocked" and "terminated" in reason
    # only the primary's (failed) marker exists — no secondary component job spawned
    assert len(jobresult.read_results(svc._paths)) <= 1


def test_spawn_web_job_orphan_primary_blocks(tmp_path, monkeypatch):
    import os
    svc = _svc(tmp_path)
    _fake_spawn(monkeypatch, os.getpid())
    monkeypatch.setattr(ControllerService, "_track_or_terminate",
                        lambda self, life, ln, pid, cid, op, attempt_id="":
                        f"{op} '{cid}' ... could NOT be confirmed stopped — ORPHAN RISK; check ps.")
    log, admission, reason = svc.spawn_web_job("build", "meshcom")
    assert log is None and admission == "blocked" and "Recover" in reason


def test_spawn_web_job_blocked_by_same_source_derived_unsafe(tmp_path, monkeypatch):
    from lhpc.core import reslock
    svc = _svc(tmp_path)
    items, _ = svc._resolve("meshcom")
    comp = next(c for _, c in items if c.source and (c.build_steps or c.test_argv))
    keys = sorted({reslock.source_lock_key(comp.source.path)})
    # a starting + startup_unverified marker whose child is GONE (no job marker) → projects unsafe
    d = {"op": "build", "target": comp.id, "stack": "meshcom", "log": "build-prev.log",
         "attempt_id": _A, "state": "starting", "startup_unverified": True, "admitted": False,
         "source_keys": keys, "started_at": "2026-01-01T00:00:00Z", "finished_at": "", "detail": ""}
    assert jobresult._write(svc._paths, "build-prev.log", d)
    log, admission, reason = svc.spawn_web_job("build", "meshcom")
    assert log is None and admission == "blocked" and "unsafe" in reason and "Recover" in reason


def test_install_on_admit_false_installs_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    called = []
    monkeypatch.setattr(ControllerService, "_adopt_dev_fallback",
                        lambda self, *a, **k: called.append(1))
    r = svc.install("meshcom", apply=True, source="pinned", on_admit=lambda: False)
    assert not r.ok and "superseded" in r.summary.lower() and called == []


def test_service_zero_change_install_calls_on_admit_once(tmp_path, monkeypatch):
    # Contract the adapter relies on: a zero-change apply still enters the guard and fires on_admit ONCE.
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_plan_source_groups", lambda self, items, source: ([], []))
    calls = []
    r = svc.install("daemon", apply=True, source="pinned", on_admit=lambda: (calls.append(1), True)[1])
    assert calls == [1] and r.ok


def test_web_install_noop_admits_not_blocked(tmp_path, monkeypatch):
    # A no-op web install (0 changes) must still admit (mark_running) and finish done — never a false "blocked".
    from lhpc.adapters.cli import main as cli_main
    from lhpc.core.service_base import ActionResult
    svc = _svc(tmp_path)
    aid, web = "c" * 32, "install-daemon.log"
    ident = procident.proc_identity(os.getpid())
    assert svc._write_job_marker(web, os.getpid(), "daemon", "install", ident=ident, attempt_id=aid)
    assert jobresult.reserve(svc._paths, web, aid, "install", "daemon", "daemon", [])
    admit = []

    def fake_install(self, stack_id=None, apply=False, source="pinned", auto_install_ctx=None, on_admit=None):
        if apply and on_admit:
            admit.append(on_admit())                       # a NO-OP install that STILL admits
        return ActionResult(True, "Nothing to do.", data={"changes": 0})
    monkeypatch.setattr(ControllerService, "install", fake_install)
    monkeypatch.setattr(cli_main, "_print_install_dep_gate", lambda svc, stack, check=False: False)
    monkeypatch.setattr(cli_main, "ControllerService", lambda: svc)
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.5")
    rc = cli_main.main(["install", "daemon", "--yes", "--source", "pinned",
                        "--web-result", web, "--attempt-id", aid])
    assert rc == 0 and admit == [True]
    d = jobresult._read_raw(svc._paths, web)
    assert d["state"] == "done" and d["admitted"] is True

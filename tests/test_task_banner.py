"""PART 4: the running-task indicator banner (install-all + HMAC apply), server-authoritative expiry."""

import calendar
import json
import time

from lhpc.core import runtime_fs
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def _write_hmac(svc, phase, **extra):
    m = {"run_id": "a" * 32, "sid": "meshcom", "action": "renew", "phase": phase,
         "finished": phase != "running", "steps": [], **extra}
    runtime_fs.atomic_write(svc._paths, svc._paths.under("state", "hmac_apply.json"),
                            json.dumps(m), 0o600)


def _utc(delta_s=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(calendar.timegm(time.gmtime()) + delta_s))


def test_running_hmac_task_is_yellow_with_href(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _write_hmac(svc, "running")
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)
    t = next(t for t in svc.running_tasks() if t["kind"] == "hmac")
    assert t["state"] == "running" and t["href"] == "/stacks/meshcom/hmac/renew" and t["run_id"] == "a" * 32


def test_terminal_task_included_only_within_expiry(tmp_path):
    svc = _svc(tmp_path)
    _write_hmac(svc, "done", finished_at=_utc(0))
    assert any(t["state"] == "done" for t in svc.running_tasks())
    _write_hmac(svc, "done", finished_at=_utc(-120))          # finished >60 s ago -> gone
    assert not any(t["kind"] == "hmac" for t in svc.running_tasks())


def test_unsafe_task_is_red_and_never_expires(tmp_path):
    svc = _svc(tmp_path)
    _write_hmac(svc, "unsafe", finished_at="2000-01-01T00:00:00Z", unsafe_scope="session-unverified",
                session_ident={"pid": 1, "starttime": 1, "sid": 1, "pgid": 1})
    t = next(t for t in svc.running_tasks() if t["kind"] == "hmac")
    assert t["state"] == "unsafe"                             # shown despite an ancient timestamp


def test_derived_interrupted_without_timestamp_is_excluded(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _write_hmac(svc, "running")                              # no finished_at
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)  # driver gone
    assert not any(t["kind"] == "hmac" for t in svc.running_tasks())   # derived-interrupted, never invented


def test_api_tasks_is_get_safe(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    c = create_app(lambda: svc).test_client()
    r = c.get("/api/tasks")
    assert r.status_code == 200 and "tasks" in r.get_json()


def test_banner_renders_on_dash_and_stacks(tmp_path, monkeypatch):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    _write_hmac(svc, "running")
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: True)
    c = create_app(lambda: svc).test_client()
    for path in ("/", "/stacks"):
        body = c.get(path).get_data(as_text=True)
        assert 'id="task-banner"' in body and "HMAC renew on meshcom" in body


# ---- detached web build/test/install job projection + red-stays + dismiss/recover ----------------

from lhpc.core import jobresult   # noqa: E402

_JLOG = "build-meshcom-qemu.log"
_JA = "a" * 32


def _job(svc, state, log=_JLOG, attempt=_JA, op="build", target="meshcom-qemu",
         startup_unverified=False, finished_at=None):
    """Write a job attempt marker directly in a chosen state (bypassing the child)."""
    d = {"op": op, "target": target, "stack": "meshcom", "log": log, "attempt_id": attempt,
         "state": state, "startup_unverified": startup_unverified, "source_keys": [],
         "started_at": _utc(-5), "finished_at": finished_at or "", "detail": ""}
    if state in ("done", "failed", "unsafe") and not d["finished_at"]:
        d["finished_at"] = _utc(0)
    assert jobresult._write(svc._paths, log, d)


def _only_job(svc):
    return next(t for t in svc.running_tasks() if t["kind"] == "job")


def _manifest_ok(monkeypatch):
    monkeypatch.setattr(ControllerService, "stack_of", lambda self, t: "meshcom" if t else "")


def _live(monkeypatch, alive):
    from lhpc.core import webjob_gate
    monkeypatch.setattr(webjob_gate, "is_live_attempt", lambda paths, log, aid: alive)


def test_job_running_live_is_yellow_with_log_href(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    _live(monkeypatch, True)
    _job(svc, "running")
    t = _only_job(svc)
    assert t["state"] == "running" and t["href"] == "/logs/meshcom-qemu?job=" + _JLOG


def test_job_flag_clear_gone_is_incomplete_red(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    _live(monkeypatch, False)
    _job(svc, "running", startup_unverified=False)     # flag cleared, child gone
    t = _only_job(svc)
    assert t["state"] == "failed" and "unexpectedly" in t["hint"]


def test_job_startup_unverified_live_vs_gone(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    _job(svc, "starting", startup_unverified=True)
    _live(monkeypatch, True)
    assert _only_job(svc)["state"] == "running"         # alive in gate = normal startup
    _live(monkeypatch, False)
    assert _only_job(svc)["state"] == "unsafe"          # gone before gate = orphan


def test_job_done_expires_failed_and_unsafe_stay(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    _job(svc, "done", finished_at=_utc(-120))            # done, aged out
    assert not any(t["kind"] == "job" for t in svc.running_tasks())
    _job(svc, "done", finished_at=_utc(0))
    t = _only_job(svc)
    assert t["state"] == "done" and t["hint"] == "Next: Test."
    _job(svc, "failed", finished_at=_utc(-9999))         # failed NEVER expires
    assert _only_job(svc)["state"] == "failed"
    _job(svc, "unsafe", finished_at=_utc(-9999))
    assert _only_job(svc)["state"] == "unsafe"


def test_job_hints_per_op(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    for op, hint in (("install", "Next: Build."), ("build", "Next: Test."), ("test", "Ready.")):
        _job(svc, "done", op=op, finished_at=_utc(0))
        assert _only_job(svc)["hint"] == hint
    _job(svc, "failed", op="build")
    assert _only_job(svc)["hint"] == "Maybe try to install known-working."


def test_hmac_failed_stays_until_dismissed(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)
    _write_hmac(svc, "failed", finished_at=_utc(-9999))   # old, but failed stays now
    assert any(t["kind"] == "hmac" and t["state"] == "failed" for t in svc.running_tasks())
    assert svc.task_dismiss("hmac", "a" * 32)
    assert not any(t["kind"] == "hmac" for t in svc.running_tasks())


def test_job_dismiss_and_recover(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    monkeypatch.setattr(ControllerService, "log_running", lambda self, *a, **k: False)
    # failed job -> ✕ dismiss removes the marker (attempt-checked)
    _job(svc, "failed")
    assert not svc.task_dismiss("job", _JLOG, "wrong")   # stale attempt refused
    assert svc.task_dismiss("job", _JLOG, _JA)
    assert jobresult.read_one(svc._paths, _JLOG) is None
    # unsafe job -> NOT dismissible, but Recover rewrites it to failed
    _job(svc, "unsafe")
    assert not svc.task_dismiss("job", _JLOG, _JA)        # unsafe never ✕-dismissible
    assert svc.task_recover("job", _JLOG, _JA)
    assert jobresult.read_one(svc._paths, _JLOG)["state"] == "failed"


def test_running_tasks_is_get_safe_never_cleans_jobs(tmp_path, monkeypatch):
    # The GET path must use active_jobs(cleanup=False)/log_running only — never the mutating active_jobs().
    svc = _svc(tmp_path)
    _manifest_ok(monkeypatch)
    called = []
    real = ControllerService.active_jobs

    def spy(self, cleanup=True):
        called.append(cleanup)
        return real(self, cleanup=cleanup)
    monkeypatch.setattr(ControllerService, "active_jobs", spy)
    svc.running_tasks()
    assert True not in called                             # never the mutating (cleanup=True) variant


def test_spawn_web_job_blocked_by_bulk_gate(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_bulk_gate", lambda self: "a bulk run is already in progress")
    log, admission, reason = svc.spawn_web_job("build", "meshcom")
    assert log is None and admission == "blocked" and "blocked" in reason and "bulk" in reason

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

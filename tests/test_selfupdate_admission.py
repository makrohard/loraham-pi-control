"""Item 7: the self-update gate must block on every relevant durable task state — including a job
marker whose safety cannot be PROVEN (unsafe jobs dir, symlinked/non-regular/oversized/malformed
marker), not only a live job. active_jobs(include_unsafe=True) surfaces those as blockers.
"""

from pathlib import Path

import os

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))


def _jobs_dir(svc):
    d = svc._jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_include_unsafe_surfaces_malformed_marker(tmp_path):
    svc = _svc(tmp_path)
    (_jobs_dir(svc) / "build-x.job").write_text("this is not valid toml [[[")
    # Default: a malformed marker is invisible (never trusted as a live job).
    assert svc.active_jobs(cleanup=False) == []
    # include_unsafe: it is surfaced as an unsafe blocker with a reason.
    unsafe = svc.active_jobs(include_unsafe=True)
    assert any(j.get("unsafe") and "malformed" in j["reason"] for j in unsafe)


def test_include_unsafe_surfaces_symlinked_marker(tmp_path):
    svc = _svc(tmp_path)
    d = _jobs_dir(svc)
    (tmp_path / "elsewhere").write_text("x")
    os.symlink(tmp_path / "elsewhere", d / "build-y.job")
    unsafe = svc.active_jobs(include_unsafe=True)
    assert any(j.get("unsafe") and "symlink" in j["reason"] for j in unsafe)


def test_include_unsafe_is_read_only(tmp_path):
    svc = _svc(tmp_path)
    bad = _jobs_dir(svc) / "build-z.job"
    bad.write_text("bad [[[")
    svc.active_jobs(include_unsafe=True)
    assert bad.exists()                          # gate never mutates, even a malformed marker


def test_self_update_apply_blocks_on_unsafe_job_marker(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    (_jobs_dir(svc) / "build-x.job").write_text("not toml [[[")
    r = svc.self_update_apply()
    assert not r.ok and r.data.get("blocked_by_jobs")


def test_self_update_blockers_are_centralized(tmp_path):
    # Item 4: trigger AND direct apply share ONE strict blocker scan. It blocks on an unprovable job.
    svc = _svc(tmp_path)
    assert svc._self_update_blockers() is None                       # clean
    d = svc._jobs_dir(); d.mkdir(parents=True, exist_ok=True)
    (d / "build-x.job").write_text("not toml [[[")                    # malformed marker
    blk = svc._self_update_blockers()
    assert blk and blk[1] == "jobs"
    # direct apply uses the same scan -> refuses.
    assert svc.self_update_apply().data.get("blocked_by_jobs")


def test_trigger_uses_strict_scan_and_admission(tmp_path, monkeypatch):
    # A malformed job marker blocks the WEB trigger too (strict include_unsafe scan under admission),
    # after the managed-integration preflight passes.
    svc = _svc(tmp_path)
    monkeypatch.setenv("INVOCATION_ID", "x")
    monkeypatch.setattr(ControllerService, "updater_integration", lambda self: {"status": "ok"})
    monkeypatch.setattr(ControllerService, "self_update_status",
                        lambda self: {"available": True, "identity": {"status": "ok"}})
    d = svc._jobs_dir(); d.mkdir(parents=True, exist_ok=True)
    (d / "build-x.job").write_text("bad [[[")
    r = svc.self_update_trigger()
    assert not r.ok and r.data.get("blocked_by_jobs")
    assert not (svc._paths.under("state", "selfupdate.request")).exists()   # no request queued


def test_helper_owns_inflight_pid_and_start_time(tmp_path):
    # Item 1: the managed helper may proceed with an in-flight request ONLY when the record's PID AND
    # /proc start time both match this process — a foreign/forged/PID-reused owner is refused.
    import os, json
    from lhpc.core import runtime_fs, updater_units
    from lhpc.core.service_base import _proc_start_time
    svc = _svc(tmp_path)
    inflight = svc._paths.under(*updater_units.INFLIGHT_REL)
    runtime_fs.write_marker(svc._paths, inflight,
                            json.dumps({"pid": os.getpid(), "start_time": _proc_start_time(os.getpid())}))
    assert svc._helper_owns_inflight() is True                      # exact owner
    runtime_fs.write_marker(svc._paths, inflight, json.dumps({"pid": os.getpid() + 999999, "start_time": "x"}))
    assert svc._helper_owns_inflight() is False                     # foreign PID
    runtime_fs.write_marker(svc._paths, inflight, json.dumps({"pid": os.getpid(), "start_time": "0"}))
    assert svc._helper_owns_inflight() is False                     # PID reuse (start time differs)
    runtime_fs.write_marker(svc._paths, inflight, "not json at all")
    assert svc._helper_owns_inflight() is False                     # malformed

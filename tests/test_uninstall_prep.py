"""Item 5/10: controller uninstall-prep runs UNDER the task-admission lock (a concurrent task-start
contends), proves quiescence from durable evidence, and stops the managed stacks (clients before the
shared daemon) with VERIFIED cessation — failing closed on jobs, auto-install/HMAC, UNKNOWN state,
snapshot errors, or a stop that does not cease. The stop tests mock the LOWER lifecycle boundary
(`Lifecycle.stop`), never the public `stop()` that carries the real admission behavior.
"""

import threading
from pathlib import Path

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import RunState
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    svc.bootstrap(apply=True)
    return svc


def _running_then_clean(svc, running_ids):
    """A build_snapshot side-effect: first call marks `running_ids` RUNNING, later calls are clean."""
    base = svc.build_snapshot(fresh=True)
    running = svc.build_snapshot(fresh=True)
    for ss in running.stacks:
        for cid, cs in ss.components.items():
            if cid in running_ids:
                cs.run_state = RunState.RUNNING
    calls = {"n": 0}
    def snap(self, *, fresh=False):
        calls["n"] += 1
        return running if calls["n"] == 1 else base
    return snap


def test_prep_quiescent_when_nothing_running(tmp_path):
    r = _svc(tmp_path).controller_uninstall_prep()
    assert r.ok and r.data.get("quiescent")


def test_prep_blocks_on_active_or_unsafe_job(tmp_path):
    svc = _svc(tmp_path)
    d = svc._jobs_dir(); d.mkdir(parents=True, exist_ok=True)
    (d / "build-x.job").write_text("not toml [[[")
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "jobs"


def test_prep_blocks_on_pending_self_update(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "classify_request", lambda self: "in_flight")
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "self_update"


def test_prep_blocks_on_unknown_state(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    snap = svc.build_snapshot(fresh=True)
    next(iter(next(iter(snap.stacks)).components.values())).run_state = RunState.UNKNOWN
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *, fresh=False: snap)
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "unknown"


def test_prep_snapshot_exception_fails_closed(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    def boom(self, *, fresh=False):
        raise OSError("proc scan failed")
    monkeypatch.setattr(ControllerService, "build_snapshot", boom)
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "snapshot"


def test_prep_stops_clients_before_daemon_via_real_public_stop(tmp_path, monkeypatch):
    # Real public stop() (with its real admission behavior) is exercised; only the LOWER lifecycle
    # boundary is mocked to simulate cessation. Order must be clients-before-the-shared-daemon.
    svc = _svc(tmp_path)
    daemon_sid = next(ss.stack.id for ss in svc.build_snapshot(fresh=True).stacks
                      if ss.stack.main == svc.DAEMON_ID)
    client_sid = next(ss.stack.id for ss in svc.build_snapshot(fresh=True).stacks
                      if ss.stack.main != svc.DAEMON_ID and ss.components)
    run_ids = {cid for ss in svc.build_snapshot(fresh=True).stacks if ss.stack.id in (daemon_sid, client_sid)
               for cid in ss.components}
    monkeypatch.setattr(ControllerService, "build_snapshot", _running_then_clean(svc, run_ids))
    order = []
    def _rec_stop(_life, comp, band="", _svc=svc, _order=order):
        _order.append(_svc.stack_of(comp.id) or comp.id)
        return CompResult(component=comp.id, stack=comp.id, action="stop",
                          outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", _rec_stop)
    r = svc.controller_uninstall_prep()
    assert r.ok and r.data.get("quiescent"), r.summary
    seen = [s for s in order if s in (client_sid, daemon_sid)]
    assert seen.index(client_sid) < seen.index(daemon_sid)   # clients stopped BEFORE the shared daemon


def test_prep_client_stop_failure_returns_before_daemon(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    daemon_sid = next(ss.stack.id for ss in svc.build_snapshot(fresh=True).stacks
                      if ss.stack.main == svc.DAEMON_ID)
    client_sid = next(ss.stack.id for ss in svc.build_snapshot(fresh=True).stacks
                      if ss.stack.main != svc.DAEMON_ID and ss.components)
    run_ids = {cid for ss in svc.build_snapshot(fresh=True).stacks if ss.stack.id in (daemon_sid, client_sid)
               for cid in ss.components}
    # Snapshot always shows running (the stop "fails" to cease).
    running = svc.build_snapshot(fresh=True)
    for ss in running.stacks:
        for cid, cs in ss.components.items():
            if cid in run_ids:
                cs.run_state = RunState.RUNNING
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self, *, fresh=False: running)
    stopped = []
    def fail_client(self, comp, band=""):
        sid = svc.stack_of(comp.id) or comp.id
        stopped.append(sid)
        out = Outcome.FAILED if sid == client_sid else Outcome.STOPPED
        return CompResult(component=comp.id, stack=comp.id, action="stop", outcome=out, summary=out.value)
    monkeypatch.setattr(Lifecycle, "stop", fail_client)
    r = svc.controller_uninstall_prep()
    assert not r.ok and r.data.get("prep_blocked") == "client_stop_failed"
    assert daemon_sid not in stopped                         # the daemon was NEVER stopped


def test_prep_contended_admission_returns_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "_SELF_LOCK_WAIT_S", 0.2)
    svc = _svc(tmp_path)
    other = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    held, release = threading.Event(), threading.Event()

    def hold():
        with other._admission_guard("build", "daemon"):
            held.set(); release.wait(3)
    t = threading.Thread(target=hold); t.start()
    try:
        assert held.wait(3)
        r = svc.controller_uninstall_prep()                  # admission contended
        assert not r.ok and r.data.get("prep_blocked") == "busy"
    finally:
        release.set(); t.join(3)


def test_uninstall_guard_live_owner_refused_and_owned_release(tmp_path):
    # Item 4: a guard owned by a LIVE process is not overwritten and blocks a second claim; release only
    # by the owning nonce.
    import os
    from lhpc.core import updater_units
    from lhpc.core.service_base import _proc_start_time
    svc = _svc(tmp_path)
    guard = Path(tmp_path) / updater_units.UNINSTALL_GUARD
    live_pid, live_start = str(os.getpid()), str(_proc_start_time(os.getpid()))
    assert svc.controller_uninstall_guard_claim(live_pid, "N1", live_start).ok    # LIVE owner
    assert guard.exists()
    r = svc.controller_uninstall_guard_claim("222", "N2", "43")
    assert not r.ok and r.data.get("guard_live")                                 # live -> refused (no overwrite)
    assert not svc.controller_uninstall_guard_release("WRONG").ok                # foreign nonce -> refused
    assert guard.exists()                                                        # retained
    assert svc.controller_uninstall_guard_release("N1").ok                       # owner releases
    assert not guard.exists()


def test_uninstall_guard_stale_owner_is_reclaimed(tmp_path):
    # Item 4/5: a guard whose recorded owner is PROVEN DEAD (an interrupted uninstall) is reclaimed —
    # the safe retry path, so a reload/stop failure can never permanently strand the deployment.
    svc = _svc(tmp_path)
    assert svc.controller_uninstall_guard_claim("1073741824", "OLD", "1").ok     # a pid that cannot exist
    r = svc.controller_uninstall_guard_claim("222", "NEW", "2")
    assert r.ok and r.data.get("reclaimed")                                      # stale -> reclaimed
    assert svc.controller_uninstall_guard_release("NEW").ok                      # now owned by NEW


def test_uninstall_guard_claim_refuses_symlink(tmp_path):
    import os
    from lhpc.core import updater_units
    svc = _svc(tmp_path)
    (Path(tmp_path) / "elsewhere").write_text("x")
    os.symlink(Path(tmp_path) / "elsewhere", Path(tmp_path) / updater_units.UNINSTALL_GUARD)
    assert not svc.controller_uninstall_guard_claim("1", "N", "1").ok        # never follows/overwrites
    assert (Path(tmp_path) / "elsewhere").read_text() == "x"                 # target untouched

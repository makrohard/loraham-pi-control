"""Item 1/2: task admission is a HELD interprocess lock (`controller-task-admission`), acquired as
lock-order #1 around a task's check→reserve/spawn. It refuses new task-starts while a controller
self-update or uninstall is pending, is reentrant per thread (nested start in an admitted restart),
and does NOT gate stop (needed to quiesce during uninstall). A second ControllerService on the same
root contends on the same flock."""


import threading
import pytest
from pathlib import Path
from lhpc.core import updater_units
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


# ===== merged from test_task_admission.py =====
def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    svc.bootstrap(apply=True)
    return svc


def _guard(tmp_path):
    (Path(tmp_path) / updater_units.UNINSTALL_GUARD).write_text('{"pid": 1, "nonce": "x"}')


def test_gate_clear_when_no_update_or_uninstall(tmp_path):
    svc = _svc(tmp_path)
    assert svc.uninstall_guard_blocks() is False
    assert svc._task_admission_blocked() is None


def test_absent_runtime_root_is_zero_mutation_pass(tmp_path):
    # No bootstrap: runtime root absent -> admission is skipped (no lockfile, no state dir created).
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path) / "nope"))
    import contextlib
    with contextlib.ExitStack() as stack:
        svc._admit(stack, "build", "daemon")           # no raise, no mutation
    assert not (Path(tmp_path) / "nope").exists()


def test_apply_task_starts_refused_during_uninstall(tmp_path):
    svc = _svc(tmp_path)
    # The stack must be INSTALLED: `build()` refuses an absent source before it reaches
    # admission (both are zero-mutation refusals, but this test is about the ADMISSION one).
    for c in svc.stack("daemon").components:
        if c.source:
            (tmp_path / c.source.path).mkdir(parents=True, exist_ok=True)
    _guard(tmp_path)
    assert svc.build("daemon", apply=True).data.get("admission_blocked")
    assert svc.install("daemon", apply=True).data.get("admission_blocked")
    assert svc.start("daemon", apply=True).data.get("admission_blocked")
    assert svc.auto_install(apply=True, emit=lambda *_: None).data.get("admission_blocked")
    _, admission, _ = svc.spawn_web_job("build", "daemon")
    assert admission == "blocked"
    assert svc.hmac_apply_start("meshcom", "enable").data.get("admission_blocked")


def test_dry_run_is_preserved_during_uninstall(tmp_path):
    svc = _svc(tmp_path)
    _guard(tmp_path)
    assert not svc.build("daemon", apply=False).data.get("admission_blocked")
    assert not svc.install("daemon", apply=False).data.get("admission_blocked")


def test_stop_is_allowed_during_uninstall(tmp_path):
    # Item 2: uninstall writes .lhpc-uninstalling FIRST, so an APPLIED stop must still run to quiesce.
    svc = _svc(tmp_path)
    _guard(tmp_path)
    res = svc.stop("daemon", apply=True)
    assert not res.data.get("admission_blocked")       # NOT an admission refusal


def test_restart_refuses_before_any_stop_when_pending(tmp_path):
    # Item 2: restart acquires admission at its OUTER boundary — so when update/uninstall is pending it
    # refuses BEFORE issuing any stop (a real applied restart, no monkeypatching the boundary).
    svc = _svc(tmp_path)
    stops = []
    orig_stop = ControllerService.stop
    ControllerService.stop = lambda self, *a, **k: stops.append(a) or orig_stop(self, *a, **k)
    try:
        _guard(tmp_path)
        r = svc.restart("daemon", apply=True)
        assert r.data.get("admission_blocked")         # refused
        assert stops == []                             # ... before any stop was attempted
    finally:
        ControllerService.stop = orig_stop


def test_second_service_contends_on_the_admission_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "_SELF_LOCK_WAIT_S", 0.2)   # keep the contention test fast
    a = _svc(tmp_path)
    b = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    held, release = threading.Event(), threading.Event()

    def hold_admission():
        with a._admission_guard("build", "daemon"):    # instance A holds the flock
            held.set()
            release.wait(3)

    t = threading.Thread(target=hold_admission)
    t.start()
    try:
        assert held.wait(3)
        # Instance B (same runtime root) must CONTEND — its build cannot acquire admission.
        res = b.build("daemon", apply=True)
        assert not res.ok
        assert "busy" in res.summary.lower() or "contend" in res.summary.lower() \
            or "in use" in res.summary.lower() or res.data.get("admission_blocked") is None
    finally:
        release.set()
        t.join(3)


def test_guard_absent_does_not_block(tmp_path):
    assert _svc(tmp_path).uninstall_guard_blocks() is False


def test_guard_present_of_any_kind_blocks(tmp_path):
    import os
    for maker in (
        lambda p: p.write_text("x"),                       # regular
        lambda p: p.mkdir(),                               # directory
        lambda p: os.mkfifo(p),                            # FIFO
    ):
        sub = Path(tmp_path) / f"r{id(maker)}"
        sub.mkdir()
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=sub))
        svc.bootstrap(apply=True)
        maker(sub / updater_units.UNINSTALL_GUARD)
        assert svc.uninstall_guard_blocks() is True


def test_guard_symlink_blocks(tmp_path):
    import os
    svc = _svc(tmp_path)
    (Path(tmp_path) / "elsewhere").write_text("x")
    os.symlink(Path(tmp_path) / "elsewhere", Path(tmp_path) / updater_units.UNINSTALL_GUARD)
    assert svc.uninstall_guard_blocks() is True             # a symlinked guard is present -> blocks


def test_guard_state_escape_is_unsafe_not_absent(tmp_path):
    # An escaping/uninspectable path is 'unsafe' (blocks), NEVER conflated with 'absent' — the exact
    # fail-open the old _marker_present had.
    from lhpc.core import runtime_fs
    paths = Paths(runtime_root=Path(tmp_path))
    assert runtime_fs.guard_state(paths, Path("/etc/hostname")) == "unsafe"
    assert runtime_fs.guard_state(paths, paths.under(updater_units.UNINSTALL_GUARD)) == "absent"


def test_admission_acquired_before_config_stable(tmp_path, monkeypatch):
    import contextlib
    svc = _svc(tmp_path)
    order = []
    real_admit = ControllerService._admit
    def spy_admit(self, stack, op, target=""):
        if op in ("start", "restart"):
            order.append("admit")
        return real_admit(self, stack, op, target)
    real_cfg = ControllerService._config_stable
    @contextlib.contextmanager
    def spy_cfg(self, exclusive=False):
        order.append("config")
        with real_cfg(self, exclusive):
            yield
    monkeypatch.setattr(ControllerService, "_admit", spy_admit)
    monkeypatch.setattr(ControllerService, "_config_stable", spy_cfg)
    svc.start("daemon", apply=True)                      # not installed -> refuses, but AFTER the guards
    assert order[:2] == ["admit", "config"], order       # admission is lock-order #1, BEFORE config


def test_start_refused_by_guard_does_not_clear_daemon_feed(tmp_path, monkeypatch):
    # Item 2: an applied start refused by a pending uninstall must NOT clear daemon-feed state (the
    # clear happens INSIDE admission, after it is granted).
    svc = _svc(tmp_path)
    _guard(tmp_path)
    cleared = []
    monkeypatch.setattr(ControllerService, "clear_daemon_feed", lambda self, b: cleared.append(b))
    r = svc.start("daemon", apply=True)
    assert r.data.get("admission_blocked")
    assert cleared == []                                 # feed untouched


def test_second_thread_start_and_config_do_not_invert(tmp_path):
    # Deterministic: one thread holds admission; a second thread's applied start must contend on
    # admission (its lock-order #1) and refuse typed — it can NEVER acquire config first and then wait
    # on admission (the old inversion), so no deadlock/hang.
    import threading
    svc = _svc(tmp_path)
    other = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    other._SELF_LOCK_WAIT_S = 0.2
    held, release = threading.Event(), threading.Event()

    def hold():
        with svc._admission_guard("holder"):
            held.set(); release.wait(3)
    t = threading.Thread(target=hold); t.start()
    try:
        assert held.wait(3)
        r = other.start("daemon", apply=True)            # completes (no deadlock), typed refusal
        assert not r.ok
    finally:
        release.set(); t.join(3)


# ===== merged from test_admission_holes.py =====
@pytest.fixture
def held_admission(tmp_path, monkeypatch):
    """Yields (svc_b) while a second service (svc_a) HOLDS admission on the same root."""
    monkeypatch.setattr(ControllerService, "_SELF_LOCK_WAIT_S", 0.2)   # fast contention
    a = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    a.bootstrap(apply=True)
    b = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    held, release = threading.Event(), threading.Event()

    def hold():
        with a._admission_guard("holder"):
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    assert held.wait(5)
    yield b
    release.set()
    t.join(5)


def _jobs(svc):
    d = svc._jobs_dir()
    return list(d.iterdir()) if d.exists() else []


def test_install_contends_typed(held_admission):
    r = held_admission.install("daemon", apply=True)
    assert not r.ok and (r.data.get("contended") or r.data.get("admission_blocked"))


def test_auto_install_driver_contends_typed(held_admission):
    r = held_admission.auto_install(apply=True, emit=lambda *_: None)
    assert not r.ok and (r.data.get("contended") or r.data.get("admission_blocked"))
    # zero reservation left behind
    assert not (held_admission._paths.under("state", "auto-install-start.json")).exists()


def test_auto_install_spawn_contends_typed(held_admission):
    log, error = held_admission.spawn_auto_install_job({"daemon": {"install": True}})
    assert log is None and error                # (log_name, error) tuple -> typed refusal, no spawn
    # zero reservation created under contention
    assert not (held_admission._paths.under("state", "auto-install-start.json")).exists()


def test_web_job_spawn_contends_typed(held_admission):
    log, admission, reason = held_admission.spawn_web_job("build", "daemon")
    assert log is None and admission == "blocked"


def test_hmac_cli_contends_typed(held_admission):
    msgs = []
    rc = held_admission.hmac_apply_cli("meshcom", "enable", emit=msgs.append)
    assert rc == 1


def test_self_update_apply_contends_typed(held_admission):
    r = held_admission.self_update_apply()
    assert not r.ok and (r.data.get("contended") or r.data.get("admission_blocked"))
    # no request/in-flight marker written
    assert not (held_admission._paths.under("state", "selfupdate.inflight")).exists()


def test_restart_hook_fires_before_the_stop_and_a_refusal_stops_nothing(tmp_path, monkeypatch):
    """0.2.9: the detached web restart's admission hook runs at the restart transaction's
    pre-mutation boundary — under admission + config + lifecycle locks, after preflight — and
    BEFORE `stop()`. A refusing (superseded) hook leaves the running stack untouched: no stop, no
    start. The hook is never handed to the nested start."""
    from conftest import set_call
    from lhpc.core.services import ActionResult, ControllerService as _CS
    svc = _svc(tmp_path)
    set_call(svc)
    events = []
    monkeypatch.setattr(_CS, "stop", lambda self, t, **kw: events.append(("stop", t)) or
                        ActionResult(False, "stop stub — abort here"))
    monkeypatch.setattr(_CS, "_start_impl", lambda self, t, **kw: events.append(("start", t)) or
                        ActionResult(True, "start stub"))

    def refusing():
        events.append(("hook", svc._held_counts().get(svc.ADMISSION_KEY, 0) > 0))
        return ActionResult(False, "superseded")
    r = svc.restart("igate", apply=True, _before_restart_locked=refusing)
    assert r.summary == "superseded"
    assert events == [("hook", True)]                       # under admission; nothing stopped/started

    def admitting():
        events.append(("hook", True))
        return None
    events.clear()
    r2 = svc.restart("igate", apply=True, _before_restart_locked=admitting)
    assert events[:2] == [("hook", True), ("stop", "igate")]  # hook BEFORE the stop
    assert not r2.ok and "stop was not verified" in r2.summary
    assert ("start", "igate") not in events                 # aborted after the (stubbed) failed stop

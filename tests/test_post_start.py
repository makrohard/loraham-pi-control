"""§7 — required vs optional post-start, and §5 typed start aggregation."""

from lhpc.core.lifecycle import Lifecycle
from conftest import real_spawn
from lhpc.core.services import ControllerService
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.model import Component, ComponentKind, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.probes.backends import FakeSystem


def _real_life(tmp_path):
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     RealSystem())


STK = Stack(id="s", name="s", main="c")


def test_required_post_start_failure_is_typed(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["false"], "required": True},))
    life = _real_life(tmp_path)
    assert life.has_required_post_start(comp)
    jr = life.run_required_post_start(STK, comp)
    assert not jr.ok and jr.returncode != 0


def test_required_post_start_success_is_typed(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"], "required": True},))
    life = _real_life(tmp_path)
    jr = life.run_required_post_start(STK, comp)
    assert jr.ok and jr.returncode == 0


def test_optional_post_start_not_required(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"]},))   # no required flag
    life = _real_life(tmp_path)
    assert life.has_required_post_start(comp) is False


def test_start_action_carries_typed_results(tmp_path):
    # A dependent of an uninstalled daemon -> BLOCKED, and ActionResult exposes the
    # typed CompResult objects; ok is derived from them.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("kiss", apply=True)
    assert not res.ok
    assert res.results and all(hasattr(r, "outcome") for r in res.results)
    assert any(r.outcome.value in ("blocked", "failed") for r in res.results)


# --- P0.1 integration: required post-start via ControllerService.start() ------

def _fake_life_factory(svc):
    return Lifecycle(svc._paths, svc.stacks(), svc.config(), svc._system,
                     spawn=real_spawn)


def _igate_svc(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    (tmp_path / "src" / "LoRaHAM_Daemon" / "loraham_igate").write_text("#bin")  # built
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock":
                                   b"STATUS RADIO=READY TXMODE=MANAGED\n"}).system
    return ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))


def test_start_required_post_start_failure_is_unverified(tmp_path, monkeypatch):
    # Drives ControllerService.start() and exercises the CALLER's handling of the typed
    # required-post-start result (the bug was the caller using a nonexistent Outcome.ok).
    svc = _igate_svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: (False, "required post-start failed (rc 7)"))
    res = svc.start("igate", apply=True)            # must NOT raise AttributeError
    assert not res.ok
    assert any("required post-start failed" in d for d in res.details)
    assert any(r.component == "loraham-igate" and r.outcome.value == "unverified"
               for r in res.results)


def test_start_required_post_start_success_verifies(tmp_path, monkeypatch):
    svc = _igate_svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: (True, "required post-start completed"))
    res = svc.start("igate", apply=True)
    assert any(r.component == "loraham-igate" and r.outcome.value == "verified"
               for r in res.results)
    assert any("required post-start completed" in d for d in res.details)


# --- P0.4 required post-start never raises past the typed boundary ------------

def _req_comp():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"], "required": True},))


def test_required_post_start_launcher_write_failure_is_typed(tmp_path, monkeypatch):
    from lhpc.core import runtime_fs
    from lhpc.core.jobs import JobState
    life = _real_life(tmp_path)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(runtime_fs, "write_launcher", boom)
    jr = life.run_required_post_start(STK, _req_comp())          # must NOT raise
    assert jr.state == JobState.FAILED and not jr.ok
    assert any("launcher write failed" in t for t in jr.tail)


def test_required_post_start_runner_spawn_failure_is_typed(tmp_path, monkeypatch):
    from lhpc.core import lifecycle as L
    from lhpc.core.jobs import JobState
    life = _real_life(tmp_path)
    def boom(*a, **k):
        raise OSError("no exec")
    monkeypatch.setattr(L, "run_job", boom)
    jr = life.run_required_post_start(STK, _req_comp())          # must NOT raise
    assert jr.state == JobState.FAILED and "runner failed to start" in " ".join(jr.tail)


def _opt_comp():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",), post_steps=({"kind": "exec", "argv": ["true"]},))


def test_optional_post_start_spawn_failure_is_typed_and_visible(tmp_path):
    # REAL Lifecycle: an injected spawn that raises OSError -> typed PostStartSchedule
    # (ok=False), surfaced by _run_post_start as a visible NON-gating detail.
    from lhpc.core.lifecycle import Lifecycle, PostStartSchedule
    from lhpc.core.services import ControllerService
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    def boom_spawn(*a, **k):
        raise OSError("cannot spawn")
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=boom_spawn)
    sched = life.spawn_post_start(STK, _opt_comp(), {}, "")
    assert isinstance(sched, PostStartSchedule) and sched.ok is False and sched.detail
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ok, summary = svc._run_post_start(life, STK, _opt_comp(), {}, "")
    assert ok is None and "could NOT be scheduled" in summary    # non-gating, visible


def test_optional_post_start_containment_failure_is_typed(tmp_path):
    # REAL Lifecycle: a logs/ parent swapped to a symlink -> PathContainmentError from
    # the anchored log setup becomes a typed (ok=False) schedule result, not an exception.
    import os
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    rt = tmp_path / "rt"; rt.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "logs")                  # logs/ -> outside the runtime root
    life = Lifecycle(Paths(runtime_root=rt), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=lambda *a, **k: 1)
    sched = life.spawn_post_start(STK, _opt_comp(), {}, "")
    assert sched.ok is False and sched.detail        # typed, not raised
    assert not any(outside.iterdir())                # nothing created outside the root


def test_optional_post_start_success_is_scheduled(tmp_path):
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=lambda *a, **k: 4321)
    sched = life.spawn_post_start(STK, _opt_comp(), {}, "")
    assert sched.ok is True

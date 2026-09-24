"""A stack restart brings back the OPTIONAL components that were running (F-R3).

`lhpc stack restart reticulum` on the reference box left a running MeshChat stopped
(reticulum-rnode-test-2026-09-24): the start leg raises the run order only. The restart now
captures the optional components that are up BEFORE its stop leg and starts them again by name.
"""
from lhpc.core.service_base import ActionResult
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _isolate(monkeypatch, svc, optional_up):
    """Pin the seam: the restart's preflights are proven elsewhere; here only the stop/start choreography
    and the optional re-raise are under test."""
    T = type(svc)
    for name in ("_gui_fallback_refusal", "_meshcore_mode_refusal", "_identity_refusal",
                 "_saved_launch_refusal"):
        monkeypatch.setattr(T, name, lambda self, *a, **k: None)
    monkeypatch.setattr(T, "_dep_band_block", lambda self, *a, **k: None)
    monkeypatch.setattr(T, "_run_order", lambda self, t: [])
    monkeypatch.setattr(T, "operation_band", lambda self, t, b: b or "868")
    # first call (before the stop leg) = what was up; second call (after the stack start) = what
    # the stack start brought back on its own — nothing, so every captured optional is re-raised
    seq = [list(optional_up), []]
    monkeypatch.setattr(T, "_running_optional_components",
                        lambda self, t: seq.pop(0) if seq else [])
    calls = []
    monkeypatch.setattr(T, "stop", lambda self, t, **k: (calls.append(("stop", t)),
                                                          ActionResult(True, "stopped"))[1])
    monkeypatch.setattr(T, "start", lambda self, t, **k: (calls.append(("start", t, k.get("band"))),
                                                           ActionResult(True, f"started {t}"))[1])
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def test_restart_reraises_optional_components_that_were_running(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    calls = _isolate(monkeypatch, svc, ["meshchat"])
    res = svc._restart_impl_inner("reticulum", apply=True)
    assert res.ok, res.summary
    assert calls == [("stop", "reticulum"), ("start", "reticulum", "868"), ("start", "meshchat", "868")]
    assert "meshchat" in res.summary
    assert any("[optional] meshchat: was running" in d for d in res.details)


def test_restart_without_running_optionals_starts_the_stack_only(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    calls = _isolate(monkeypatch, svc, [])
    res = svc._restart_impl_inner("reticulum", apply=True)
    assert res.ok
    assert [c[:2] for c in calls] == [("stop", "reticulum"), ("start", "reticulum")]
    assert "Optional" not in res.summary


def test_an_optional_component_that_does_not_come_back_fails_the_restart(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _isolate(monkeypatch, svc, ["meshchat"])
    T = type(svc)
    monkeypatch.setattr(T, "start", lambda self, t, **k: ActionResult(t == "reticulum", f"{t}"))
    res = svc._restart_impl_inner("reticulum", apply=True)
    assert not res.ok
    assert "did not come back: meshchat" in res.summary
    assert any("NOT back" in d for d in res.details)


def test_the_plan_lists_running_optional_components(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    T = type(svc)
    monkeypatch.setattr(T, "_gui_fallback_refusal", lambda self, *a, **k: None)
    monkeypatch.setattr(T, "_meshcore_mode_refusal", lambda self, *a, **k: None)
    monkeypatch.setattr(T, "operation_band", lambda self, t, b: b or "868")
    monkeypatch.setattr(T, "_running_optional_components", lambda self, t: ["meshchat"])
    monkeypatch.setattr(T, "_start_impl", lambda self, t, **k: ActionResult(True, "plan", details=["  [run] rns"]))
    monkeypatch.setattr(T, "_stop_impl", lambda self, t, **k: ActionResult(True, "plan", data={"dependents": [], "other_bands": []}))
    res = svc._restart_impl_inner("reticulum", apply=False)
    assert res.ok
    assert res.data["optional_restarted"] == ["meshchat"]
    assert any("[optional] meshchat: running — restarted with the stack" in d for d in res.details)


def test_stack_start_already_brought_it_back_means_no_second_start(tmp_path, monkeypatch):
    """An optional component the stack start raised itself (a GPS feed in the run order, or one that
    simply came back) is NOT started a second time and NOT reported as restarted."""
    svc = _svc(tmp_path)
    calls = _isolate(monkeypatch, svc, ["meshcore-webui"])
    T = type(svc)
    monkeypatch.setattr(T, "_running_optional_components",
                        lambda self, t: ["meshcore-webui"])       # up before AND up after
    res = svc._restart_impl_inner("meshcore", apply=True)
    assert res.ok
    assert [c[:2] for c in calls] == [("stop", "meshcore"), ("start", "meshcore")]
    assert "Optional" not in res.summary


def test_main_start_failure_does_not_blame_optionals(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _isolate(monkeypatch, svc, ["meshchat"])
    T = type(svc)
    monkeypatch.setattr(T, "start", lambda self, t, **k: ActionResult(False, "rns refused"))
    res = svc._restart_impl_inner("reticulum", apply=True)
    assert not res.ok
    assert "did not come back" not in res.summary and "rns refused" in res.summary


def test_the_real_helper_filters_planned_interactive_and_non_optional(tmp_path, monkeypatch):
    """The helper itself, against a snapshot of the real reticulum stack: MeshChat (optional
    service, not in the run order) is captured; nomadnet (optional but INTERACTIVE) and rns (the
    main, not optional) are not, even when the snapshot reports them running."""
    from types import SimpleNamespace as NS
    from lhpc.core.model import RunState
    svc = _svc(tmp_path)
    stack = svc.stack("reticulum")
    ids = {c.id for c in stack.components}
    assert {"rns", "meshchat", "nomadnet"} <= ids, ids
    running = NS(run_state=RunState.RUNNING)
    snap = NS(stacks=[NS(stack=stack, components={cid: running for cid in ids})])
    monkeypatch.setattr(type(svc), "build_snapshot", lambda self: snap)
    monkeypatch.setattr(type(svc), "_run_order", lambda self, t: [(stack, stack.component("rns"))])
    got = svc._running_optional_components("reticulum")
    assert "meshchat" in got
    assert "nomadnet" not in got and "rns" not in got
    assert all(stack.component(c).optional and not stack.component(c).interactive for c in got)

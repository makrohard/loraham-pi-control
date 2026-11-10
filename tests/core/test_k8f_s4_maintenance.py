"""K8f S4-4c (K8F-S4-DESIGN-v4, G1-K8F-S4-D3 B7): a maintenance restart after an update keeps each instance's key with
its result: a failed one is named with its remedy; a stack that runs with no instance told is not restarted by guess."""
from types import SimpleNamespace

import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def _svc(tmp_path, monkeypatch, keys, failing=()):
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "graywolf_upstream_state", lambda self, t: {"ahead": True, "latest": "v9.9.9"})
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    monkeypatch.setattr(svc._system.runner, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(instances, "running_keys", lambda _s, sid: list(keys) if sid == "graywolf" else [])
    calls = []
    monkeypatch.setattr(type(svc), "restart", lambda self, target, **k: calls.append(getattr(k.get("inst"), "rid", "")) or
                        ActionResult(getattr(k.get("inst"), "rid", "") not in failing, "x"))
    return svc, calls


def test_a_failed_instance_restart_is_named_with_its_remedy(tmp_path, monkeypatch):
    # red on 868fa6f3: "FAILED — restart manually" and `lhpc stack start graywolf…` (no instance named)
    svc, calls = _svc(tmp_path, monkeypatch, ["graywolf@r1", "graywolf@r2"], failing=("r2",))
    r = svc._graywolf_upstream_update_locked("graywolf", svc.stack("graywolf").main_component)
    assert calls == ["r1", "r2"] and not r.ok
    assert "  [restart] FAILED for graywolf@r2 — restart it by name" in r.details
    assert r.next_commands == ["lhpc stack restart graywolf@r2 --yes"]


@pytest.mark.parametrize("keys", [[]])
def test_a_running_stack_without_a_told_instance_is_not_restarted_by_guess(tmp_path, monkeypatch, keys):
    # red on 868fa6f3: the instance-less restart (the fallback instance) ran and could report ok
    svc, calls = _svc(tmp_path, monkeypatch, keys)
    r = svc._graywolf_upstream_update_locked("graywolf", svc.stack("graywolf").main_component)
    assert calls == [] and not r.ok
    assert "  [restart] not done: a running process no instance names — restart it by name" in r.details
    assert r.next_commands == ["lhpc status graywolf"]

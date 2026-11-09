"""K7: a maintenance restart restarts every running instance, each as itself (`restart(target, inst=...)`); before, the
bare restart was a band caller and reached one instance, the others kept the old tree."""
from types import SimpleNamespace

import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def test_k7_a_graywolf_upstream_update_restarts_every_running_instance(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    main = svc.stack("graywolf").main_component
    monkeypatch.setattr(type(svc), "graywolf_upstream_state", lambda self, t: {"ahead": True, "latest": "v9.9.9"})
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: True)
    monkeypatch.setattr(svc._system.runner, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(instances, "running_keys",
                        lambda _s, sid: ["graywolf@r1", "graywolf@r2"] if sid == "graywolf" else [])
    calls = []
    monkeypatch.setattr(type(svc), "restart", lambda self, target, **k: calls.append(
        (target, getattr(k.get("inst"), "rid", ""))) or ActionResult(True, "restarted"))
    r = svc._graywolf_upstream_update_locked("graywolf", main)
    assert r.ok, (r.summary, r.details)
    assert calls == [("graywolf", "r1"), ("graywolf", "r2")]

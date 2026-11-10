"""K8f S4-4a2 (K8F-S4-DESIGN-v4, G1-K8F-S4-D3 B5): a client's stop releases ITS radio's daemon (daemon@rN) when no
other running client is on that radio, never the band's daemon(s): two radios on 433 (r1, r2), kiss on both; the stop
of kiss@r1 releases daemon@r1 only, kiss@r2 keeps daemon@r2."""
import dataclasses

import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def test_a_client_stop_releases_its_own_radios_daemon_only(tmp_path, monkeypatch):
    # red on 91338805: the band's release stop(daemon, band="433") without an instance (both radios' daemons)
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))           # r1 + r2, both 433
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    k1, k2 = (instances.parse_target(svc, f"kiss@{r}", "start")[1] for r in ("r1", "r2"))
    monkeypatch.setattr(type(svc), "stop_dependents", lambda self, target, bands=None: ["kiss"])
    monkeypatch.setattr(instances, "running_insts", lambda s, sid: [k2] if sid == "kiss" else None)
    real_view = type(svc).daemon_view
    monkeypatch.setattr(type(svc), "daemon_view",
                        lambda self, band, rid="": dataclasses.replace(real_view(self, band, rid), reachable=True))
    real_stop, released = type(svc).stop, []

    def spy(self, target, *a, **k):
        if target == "daemon":
            released.append((k.get("band"), getattr(k.get("inst"), "rid", None)))
            return ActionResult(True, "released")
        return real_stop(self, target, *a, **k)
    monkeypatch.setattr(type(svc), "stop", spy)
    svc.stop("kiss", apply=True, inst=k1)
    assert released == [("433", "r1")], released


@pytest.mark.parametrize("other_rid, released", [("r2", [("433", "r1")]), ("r1", [])])
def test_another_stacks_client_keeps_only_its_own_radios_daemon(tmp_path, monkeypatch, other_rid, released):
    # graywolf runs on the same band: on r2 it does not need daemon@r1 (released); on r1 it does (kept).
    # red on 91338805 for r2: the band rule kept every daemon of the band while any client ran on it
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    k1 = instances.parse_target(svc, "kiss@r1", "start")[1]
    g = instances.parse_target(svc, f"graywolf@{other_rid}", "start")[1]
    monkeypatch.setattr(type(svc), "stop_dependents", lambda self, target, bands=None: ["kiss", "graywolf"])
    monkeypatch.setattr(instances, "running_insts", lambda s, sid: [g] if sid == "graywolf" else [])
    real_view = type(svc).daemon_view
    monkeypatch.setattr(type(svc), "daemon_view",
                        lambda self, band, rid="": dataclasses.replace(real_view(self, band, rid), reachable=True))
    real_stop, got = type(svc).stop, []

    def spy(self, target, *a, **k):
        if target == "daemon":
            got.append((k.get("band"), getattr(k.get("inst"), "rid", None)))
            return ActionResult(True, "released")
        return real_stop(self, target, *a, **k)
    monkeypatch.setattr(type(svc), "stop", spy)
    svc.stop("kiss", apply=True, inst=k1)
    assert got == released, got


def test_the_stop_preview_announces_the_same_release_as_the_apply(tmp_path, monkeypatch):
    # G1-K8F-S4-4A N1: the plan of kiss@r1's stop (graywolf on r2) says it also stops the daemon (daemon@r1), as the
    # apply does; red on 09b07002: the preview's own band rule saw graywolf on the band and announced nothing
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    k1, g2 = (instances.parse_target(svc, k, "start")[1] for k in ("kiss@r1", "graywolf@r2"))
    monkeypatch.setattr(type(svc), "stop_dependents", lambda self, target, bands=None: ["kiss", "graywolf"])
    monkeypatch.setattr(instances, "running_insts", lambda s, sid: [g2] if sid == "graywolf" else [])
    monkeypatch.setattr(type(svc), "stack_running", lambda self, sid: sid in ("kiss", "graywolf"))
    real_view = type(svc).daemon_view
    monkeypatch.setattr(type(svc), "daemon_view",
                        lambda self, band, rid="": dataclasses.replace(real_view(self, band, rid), reachable=True))
    plan = svc.stop("kiss", apply=False, inst=k1)
    assert "  [also stops] daemon 433 MHz: no other stack needs it" in plan.details, plan.details

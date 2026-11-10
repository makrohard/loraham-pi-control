"""K8f S4-4a2 (K8F-S4-DESIGN-v4, G1-K8F-S4-D3 B5): a client's stop releases ITS radio's daemon (daemon@rN) when no
other running client is on that radio, never the band's daemon(s): two radios on 433 (r1, r2), kiss on both; the stop
of kiss@r1 releases daemon@r1 only, kiss@r2 keeps daemon@r2."""
import dataclasses

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

"""K8d: an instance runs on its radio's band. kiss@r2 on an 868 radio planned and ran its start and restart on 433
(the operation band's primary fallback): the daemon ensure, the TNC's band and its config store were r1's band.
G1-K8D1: the band is the radio ROW's, resolved by the rid, whatever band the caller or the instance carries."""
import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core.commands import Instance
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


@pytest.fixture
def svc(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))          # r1 on 433, r2 on 868
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


@pytest.mark.parametrize("op", ["start", "restart"])
@pytest.mark.parametrize("inst,caller,band", [
    (Instance("r1", band="433"), "", "433"), (Instance("r2", band="868"), "", "868"),
    (Instance("r2"), "433", "868"),                         # no band on the instance, an ambient 433: r2's row
    (Instance("r2", band="433"), "", "868")])               # a band the row contradicts: the row's
def test_k8_an_instance_plans_on_its_radios_band(svc, op, inst, caller, band):
    res = getattr(svc, op)("kiss", band=caller, inst=inst)
    assert res.ok, res.summary
    assert res.details == [f"  [daemon] start/ensure --radio {band}, TXMODE=MANAGED",
                           f"  [start] loraham-kiss-tnc (band {band})"]


@pytest.mark.parametrize("inst", [Instance("r2"), Instance("r2", band="868")])
def test_k8_a_stop_of_kiss_at_r2_addresses_r2_alone_beside_an_ambient_433(svc, monkeypatch, inst):
    # G1-K8D1: the stop needs no band rule: it addresses the instance by its rid, token and endpoints (r1 on 433 runs)
    stops = []

    def stop(self, comp, **kw):
        stops.append((comp.id, kw.get("rid"), tuple(kw.get("token") or ()), tuple(e.address for e in kw.get("endpoints") or ())))
        return CompResult(component=comp.id, stack="", action="stop", outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", stop)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: [])
    monkeypatch.setattr(instances, "instance_runs", lambda s, c, i, band: i.rid in ("r1", "r2"))
    assert svc.stop("kiss", apply=True, band="433", inst=inst).ok
    assert stops == [("loraham-kiss-serial", "r2", (), ("state/r2/loraham_kiss",)),
                     ("loraham-kiss-tnc", "r2", ("--data-socket", "/tmp/loraf-r2.sock"), ("127.0.0.1:8134",))], stops


@pytest.mark.parametrize("op", ["start", "restart"])
def test_k8_the_daemons_own_instance_keeps_its_band_rule(svc, op):
    # the daemon's instance carries no band (radio_start.daemon_inst): K8d leaves its start and restart as they were
    # (its plan names no band; its radio comes from the rid), never r2's row band
    from lhpc.core import radio_start
    res = getattr(svc, op)("daemon", inst=radio_start.daemon_inst("r2"))
    assert res.ok and res.details == ["  [daemon] start/ensure --radio all bands"], res.details

"""K8f S4-4b1 (D2-CTX B5, DEC-W409p/q): the host-listener inventory is per instance. Each instance listens at its own
radio's port of its own base (its store, or the save's value for the saving instance only: base + 133 x (n - 1),
W348), and each radio r1..r64 without a saved instance holds the stack's template instance (the manifest bases), so
a save is refused exactly where two owners would share a port on some radio. Two radios on 433 (r1, r2)."""
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, rows=(radios_fixture.R1, radios_fixture.R2)):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _save(svc, target, values):
    return svc.save_config_bundle(target.partition("@")[0], values=values,
                                  inst=instances.parse_target(svc, target, "start")[1])


def test_a_base_one_step_from_another_instances_base_on_another_radio_saves(tmp_path):
    # kiss@r1 at base 7633 listens on 7633; meshcom@r1 at 7500 listens on 7500 (meshcom@r2 keeps its own 7133): no two
    # owners share a port. red on 617eed17: refused, "port 7500 is already kiss/loraham-kiss-tnc's listener" (the two
    # bases differ by 133, read as one base for every radio)
    svc = _svc(tmp_path)
    assert _save(svc, "kiss@r1", {"kiss_port": "7633"}).ok
    r = _save(svc, "meshcom@r1", {"port": "7500"})
    assert r.ok, r.details


def test_a_port_another_radios_instance_listens_on_is_refused_by_its_instance(tmp_path):
    # kiss@r2 at base 7367 listens on 7500 (r2: + 133); meshcom@r1 at 7500 would listen there too: refused, naming
    # kiss@r2. red on 617eed17: saved (the inventory held kiss's fallback base, r1's 8001, only)
    svc = _svc(tmp_path)
    assert _save(svc, "kiss@r2", {"kiss_port": "7367"}).ok
    r = _save(svc, "meshcom@r1", {"port": "7500"})
    assert not r.ok and r.details == ["meshcom@r1/meshcom-bridge: port 7500 is already kiss@r2/loraham-kiss-tnc's listener"], \
        r.details


def test_a_future_radios_listener_is_named_as_its_template(tmp_path):
    # only r1 on the list: graywolf on a radio added as r2 would start at its manifest base, 8080 + 133 = 8213, so
    # kiss@r1 on 8213 is refused and the owner says it is a future radio's template. red on 617eed17: named
    # "graywolf/graywolf"
    svc = _svc(tmp_path, (radios_fixture.R1,))
    r = _save(svc, "kiss@r1", {"kiss_port": "8213"})
    assert not r.ok and r.details == [
        "kiss@r1/loraham-kiss-tnc: port 8213 is already graywolf@r2 (template)/graywolf's listener"], r.details

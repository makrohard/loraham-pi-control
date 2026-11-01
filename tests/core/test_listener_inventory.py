"""L4: the host-listener inventory (A §2.4, F4; PROOF T-P1, T-P3). Every TCP port this LHPC would listen on has one
owner: a stack component's listener (at its configured port), the console, or a stack web page. A save that would put
two owners on one port, or a port outside 1024..65535, is refused at save and names both owners. The radio save of
T-P3 comes with radios at L5."""

import re

from lhpc.core import commands
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _manifest_bases():
    """(slotted bases, fixed ports) of the shipped manifest's TCP listeners, read from the manifest."""
    slotted, fixed = set(), set()
    for c in (c for st in load_manifest() for c in st.components):
        for e in (e for e in c.endpoints if e.kind == "tcp" and e.role == "listener" and not e.external):
            m = re.search(r"\{port:(\d+)\}", e.address_template)
            (slotted.add(int(m.group(1))) if m else fixed.add(int(e.address.rsplit(":", 1)[1])))
    return slotted, fixed


def test_t_p1_the_slotted_bases_are_disjoint_over_slots_0_to_7_and_from_the_fixed_ports():
    slotted, fixed = _manifest_bases()
    ports = [b + commands.PORT_STEP * k for b in slotted for k in commands.SLOTS]
    assert len(set(ports)) == len(ports) == 10 * 8
    assert not set(ports) & fixed


def test_t_p1_the_bases_are_the_manifests():
    # the ten slotted bases are exactly the bases the manifest's own listener templates name, the four fixed
    # ports exactly the TCP listeners without a template
    from lhpc.core import listeners
    slotted, fixed = _manifest_bases()
    assert slotted == set(listeners.SLOTTED_BASES) and fixed == set(listeners.FIXED_PORTS)


def test_t_p3_a_stack_port_param_meeting_another_stacks_listener_is_refused(tmp_path):
    svc = _svc(tmp_path)
    r = svc.save_config("kiss", {"kiss_port": "8080"})          # graywolf's web UI
    assert not r.ok and any("port 8080 is already graywolf/graywolf's listener" in d for d in r.details), r.details


def test_t_p3_a_stack_port_param_outside_the_range_is_refused(tmp_path):
    svc = _svc(tmp_path)
    r = svc.save_config("kiss", {"kiss_port": "80"})
    assert not r.ok and any("port 80 is outside 1024..65535" in d for d in r.details), r.details


def test_the_meshcom_bridge_port_0_is_refused(tmp_path):
    # A decision (B3): the manifest allows 0 for the bridge's control port, but the port must equal the
    # firmware's XR_PORT and 0 is no TCP port a client can reach, so it is refused like any port below 1024.
    r = _svc(tmp_path).save_config("meshcom", {"port": "0"})
    assert not r.ok and any("port 0 is outside 1024..65535" in d for d in r.details), r.details


def test_t_p3_a_stack_port_param_on_a_free_port_saves(tmp_path):
    assert _svc(tmp_path).save_config("kiss", {"kiss_port": "8070"}).ok


def test_t_p3_a_stack_port_param_meeting_the_console_is_refused(tmp_path):
    svc = _svc(tmp_path)
    console = svc.config().webserver.port
    r = svc.save_config("kiss", {"kiss_port": str(console)})
    assert not r.ok and any(f"port {console} is already the console's listener" in d for d in r.details), r.details


def test_t_p3_a_stack_web_port_meeting_a_stack_listener_is_refused(tmp_path):
    svc = _svc(tmp_path)
    r = svc.stack_web_configure("graywolf", port=8001)          # kiss's KISS port
    assert not r.ok and any("port 8001 is already kiss/loraham-kiss-tnc's listener" in d
                            for d in (r.summary, *r.details)), (r.summary, r.details)

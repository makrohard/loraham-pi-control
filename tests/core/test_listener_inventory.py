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


def test_t_p1_the_radio_bases_are_disjoint_over_r1_to_r64_and_from_the_fixed_and_page_ports():
    # DEC-W348 (PORTS-LIMIT-OPTIONS-v3): base + 133 x (n - 1) for r1..r64, the top port 26462.
    slotted, fixed = _manifest_bases()
    ports = [b + commands.PORT_STEP * k for b in slotted for k in range(commands.PORT_RADIOS)]
    assert len(set(ports)) == len(ports) == 10 * 64 and max(ports) == 26462
    assert not set(ports) & (fixed | set(range(8444, 8449)))


def test_t_p1_the_bases_are_the_manifests():
    # the ten slotted bases are exactly the bases the manifest's own listener templates name, the four fixed
    # ports exactly the TCP listeners without a template
    from lhpc.core import listeners
    slotted, fixed = _manifest_bases()
    assert slotted == set(listeners.INSTANCE_BASES) and fixed == set(listeners.FIXED_PORTS)


def test_t_p3_a_stack_port_param_meeting_another_stacks_listener_is_refused(tmp_path):
    svc = _svc(tmp_path)
    r = svc.save_config("kiss", {"kiss_port": "8080"})          # graywolf's web UI
    assert not r.ok and any("port 8080 is already graywolf/graywolf's listener" in d for d in r.details), r.details


def test_t_p3_a_port_one_radio_step_from_another_stacks_listener_is_refused(tmp_path):
    # DEC-W348: kiss on 8213 is kiss@r2's 8346 and graywolf's 8080 is graywolf@r2's 8213: the two meet on some radio.
    r = _svc(tmp_path).save_config("kiss", {"kiss_port": "8213"})               # 8080 + 133
    assert not r.ok and any("port 8213 is already graywolf/graywolf's listener" in d for d in r.details), r.details


def test_t_p3_a_port_whose_radio_step_reaches_a_fixed_port_is_refused(tmp_path):
    # A fixed port (meshchat's 8790, one node) meets a derived one when base + 133 x k reaches it: kiss@r2 would be 8790.
    from lhpc.core import listeners
    assert 8790 in listeners.FIXED_PORTS
    r = _svc(tmp_path).save_config("kiss", {"kiss_port": "8657"})               # 8790 - 133
    assert not r.ok and r.details == ["kiss/loraham-kiss-tnc: port 8657 is already reticulum/meshchat's listener"], r.details


def test_t_p3_a_base_whose_r64_port_leaves_the_range_is_refused_and_the_last_fitting_one_saves(tmp_path):
    # G1-B1 NOTE: 60000 is in range on r1 but r64's port would be 60000 + 133 x 63 = 68379; 57156 is the last base
    # whose r64 port (65535) fits.
    r = _svc(tmp_path).save_config("kiss", {"kiss_port": "60000"})
    assert not r.ok and r.details == ["kiss/loraham-kiss-tnc: port 60000 on r64 gives 68379, outside 1024..65535"], \
        r.details
    assert _svc(tmp_path).save_config("kiss", {"kiss_port": "57156"}).ok


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
    r = svc.stack_web_configure("graywolf@r1", port=8001)       # kiss's KISS port (K4b: the page is per instance)
    assert not r.ok and any("port 8001 is already kiss/loraham-kiss-tnc's listener" in d
                            for d in (r.summary, *r.details)), (r.summary, r.details)

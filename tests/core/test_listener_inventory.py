"""L4: the host-listener inventory (A §2.4, F4; PROOF T-P1, T-P3). Every TCP port this LHPC would listen on has one
owner: a stack component's listener (at its configured port), the console, or a stack web page. A save that would put
two owners on one port, or a port outside 1024..65535, is refused at save and names both owners. The radio save of
T-P3 comes with radios at L5. K8f S4-4b1 (DEC-W409p/q): the inventory is per instance, each at its own radio's port
(`<sid>@<rid>/<component>`), and each radio r1..r64 without a saved instance holds the stack's template instance."""

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
    assert not r.ok and any("port 8080 is already graywolf@r1/graywolf's listener" in d for d in r.details), r.details


def test_t_p3_a_port_one_radio_step_from_another_stacks_listener_is_refused(tmp_path):
    # DEC-W348: kiss@r1 on 8213 meets graywolf@r2, whose 8080 is 8213 on r2 (K8f S4-4b1: exact ports per instance).
    r = _svc(tmp_path).save_config("kiss", {"kiss_port": "8213"})               # 8080 + 133
    assert not r.ok and any("port 8213 is already graywolf@r2/graywolf's listener" in d for d in r.details), r.details


def test_t_p3_a_port_whose_radio_step_reaches_a_fixed_port_is_refused(tmp_path):
    # A fixed port (meshchat's 8790, one node) meets a derived one when the instance's own base + 133 x (n - 1) reaches
    # it. K8f S4-4b1 (DEC-W409q): a base is its own instance's, so kiss@r1 on 8657 saves (8657 on r1; a new kiss@r2
    # starts from the manifest base, 8134); kiss on r2 with base 8657 listens on 8790 and is refused. (Was: kiss@r1's
    # 8657 refused, as if it moved every radio's base.)
    import radios_fixture

    from lhpc.core import instances, listeners
    assert 8790 in listeners.FIXED_PORTS
    assert _svc(tmp_path).save_config("kiss", {"kiss_port": "8657"}).ok        # 8790 - 133, on r1
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))      # r2 on 433: kiss@r2
    svc = _svc(tmp_path)
    r = svc.save_config("kiss", {"kiss_port": "8657"}, inst=instances.parse_target(svc, "kiss@r2", "start")[1])
    assert not r.ok and r.details == ["kiss@r2/loraham-kiss-tnc: port 8790 is already reticulum/meshchat's listener"], \
        r.details


def test_t_p3_a_base_whose_own_radios_port_leaves_the_range_is_refused_and_the_last_fitting_one_saves(tmp_path):
    # K8f S4-4b1 (DEC-W409s): a saved base sets its own instance's port only, rid_port(base, its rid); on r2 65403 gives
    # 65403 + 133 = 65536 (refused by name) and 65402 gives 65535 (saves). (Was G1-B1 NOTE: 60000 refused because r64's
    # port would be 68379, as if r1's base moved every radio's.) The templates stay in range through r64: T-P1 above.
    import radios_fixture

    from lhpc.core import instances
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))      # r2 on 433: kiss@r2
    svc = _svc(tmp_path)
    r2 = instances.parse_target(svc, "kiss@r2", "start")[1]
    r = svc.save_config("kiss", {"kiss_port": "65403"}, inst=r2)
    assert not r.ok and r.details == ["kiss@r2/loraham-kiss-tnc: port 65403 on r2 gives 65536, outside 1024..65535"], \
        r.details
    assert svc.save_config("kiss", {"kiss_port": "65402"}, inst=r2).ok


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
    assert not r.ok and any("port 8001 is already kiss@r1/loraham-kiss-tnc's listener" in d
                            for d in (r.summary, *r.details)), (r.summary, r.details)

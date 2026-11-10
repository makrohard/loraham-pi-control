"""K8f S4-5 (DEC-W409x): the stack-wide MeshCore mode reads go per instance. Two radios on 868: r2 chat, r3 a
repeater. Status readiness takes each running instance's own-mode endpoints at its own addresses (DEC-W409ah); an
instance's own view, its stop verification and its Pages notes take its own mode, never the other radio's."""
import radios_fixture

from lhpc.core import instances, radios
from lhpc.core import meshcore_mode as mm
from lhpc.core.model import RunState
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def _node_argv(tmp_path, rid):
    return ["python", "-m", "meshcore_host", f"{tmp_path}/config/files/{rid}/meshcore.toml"]


def _box(tmp_path, listeners=(), running=()):
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    fake = FakeSystem(listeners=list(listeners), cmdlines_data={500 + n: _node_argv(tmp_path, r) for n, r in enumerate(running)})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3"}, inst=r3).ok
    assert svc.save_config_bundle("meshcore", values={"file_mode": "repeater"}, inst=r3).ok
    return svc, instances.parse_target(svc, "meshcore@r2", "start")[1], r3


def test_each_radios_pages_note_its_own_mode(tmp_path):
    # red on dc5d894a: r3's repeater dashboard read "Not served in the current mode (chat)" (r2's mode), and r3's
    # Web UI had no note although r3 runs no Companion
    svc, _r2, _r3 = _box(tmp_path)
    notes = {p.page_id: svc.page_mode_note(p) for p in svc.stack_web_pages("meshcore")}
    assert notes["meshcore-meshcore-node@r3"] == "", notes
    tail = " — change it with the Mode switch on the stack page, then restart the stack."
    assert notes["meshcore@r3"] == ("Not served in the current mode (repeater): this upstream runs in the chat and "
                                    "chat+repeater modes" + tail), notes
    assert notes["meshcore-meshcore-node@r2"] == ("Not served in the current mode (chat): this upstream runs in the "
                                                  "chat+repeater and repeater modes" + tail), notes
    assert notes["meshcore@r2"] == "", notes


def test_an_instances_stop_verifies_only_its_own_modes_endpoints(tmp_path):
    # red on dc5d894a: r3's stop judged r3's Companion port 5266 (absent in the repeater mode) as lingering, so a
    # foreign listener there made the stop ENDPOINT_STILL_PRESENT
    svc, _r2, r3 = _box(tmp_path, listeners=[Listener("ipv4", "127.0.0.1", 5266, 71)])
    res = svc.stop("meshcore", apply=True, inst=r3)
    node = [r for r in res.results if r.component == mm.NODE_ID]
    assert node and node[0].outcome in (Outcome.STOPPED, Outcome.ALREADY_STOPPED), (res.summary, res.details)


def test_an_instances_stop_still_verifies_its_own_modes_endpoint(tmp_path):
    # the other side (a pin, green before): a listener on r3's own dashboard port 8266 keeps the stop unverified
    svc, _r2, r3 = _box(tmp_path, listeners=[Listener("ipv4", "127.0.0.1", 8266, 72)])
    res = svc.stop("meshcore", apply=True, inst=r3)
    node = [r for r in res.results if r.component == mm.NODE_ID]
    assert node and node[0].outcome == Outcome.ENDPOINT_STILL_PRESENT, (res.summary, res.details)


def test_status_judges_each_instance_by_its_own_mode(tmp_path):
    # red on dc5d894a: r3's own view probed a Companion port (5266) it never opens and never its dashboard
    svc, r2, r3 = _box(tmp_path)
    cs = next(ss.components[mm.NODE_ID] for ss in svc.build_snapshot().stacks if ss.stack.id == "meshcore")
    assert [o.spec.address for o in instances.endpoint_statuses(svc, cs, r3)] == ["127.0.0.1:8266"]
    assert [o.spec.address for o in instances.endpoint_statuses(svc, cs, r2)] == ["127.0.0.1:5133"]


def _node(svc):
    return next(ss.components[mm.NODE_ID] for ss in svc.build_snapshot().stacks if ss.stack.id == "meshcore")


def test_two_running_instances_of_different_modes_are_ready_at_their_own_endpoints(tmp_path):
    # red on d2506c3a (G1-K8F-S4-5A B1): the modes were ORed and probed at the template's 5000/8000, so r2 chat + r3
    # repeater, both up at their own ports, read DEGRADED
    listeners = [Listener("ipv4", "127.0.0.1", 5133, 1), Listener("ipv4", "127.0.0.1", 8266, 2)]
    svc, _r2, _r3 = _box(tmp_path, listeners, running=("r2", "r3"))
    cs = _node(svc)
    assert [(o.spec.address, o.present) for o in cs.endpoints] == [("127.0.0.1:5133", True), ("127.0.0.1:8266", True)]
    assert cs.run_state == RunState.RUNNING


def test_stray_listeners_at_the_template_ports_never_make_the_node_ready(tmp_path):
    # red on d2506c3a (B1): listeners on 5000 and 8000 (no instance's) satisfied the template probe while r2's and r3's
    # own endpoints were down
    listeners = [Listener("ipv4", "127.0.0.1", 5000, 1), Listener("ipv4", "127.0.0.1", 8000, 2)]
    svc, _r2, _r3 = _box(tmp_path, listeners, running=("r2", "r3"))
    assert _node(svc).run_state == RunState.DEGRADED


def test_one_meshcore_radio_keeps_its_modes_endpoints(tmp_path):
    # the baseline box (r2 chat alone): a whole node stop checks chat's endpoint at r2's own port (the template's 5000
    # on d2506c3a, which r2 never opens; G1-K8F-S4-5A B1 fix)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    node = svc.stack("meshcore").component(mm.NODE_ID)
    assert [mm._port(e) for e in svc._lifecycle().expected_endpoints(node)] == [5133]   # r2's own Companion port


def test_a_saved_instance_that_does_not_run_does_not_hold_the_nodes_readiness(tmp_path):
    # status reads the RUNNING instances: r2 runs (chat, its 5133 up), r3 is saved but stopped -> the node is ready
    svc, _r2, _r3 = _box(tmp_path, [Listener("ipv4", "127.0.0.1", 5133, 1)], running=("r2",))
    assert _node(svc).run_state == RunState.RUNNING


def test_an_instances_stop_never_waits_for_another_instances_live_endpoint(tmp_path):
    # r2's Companion (5133) still serves while r3's stop is verified: r3's check is its own 8266 only
    svc, _r2, r3 = _box(tmp_path, listeners=[Listener("ipv4", "127.0.0.1", 5133, 73)])
    res = svc.stop("meshcore", apply=True, inst=r3)
    node = [r for r in res.results if r.component == mm.NODE_ID]
    assert node and node[0].outcome in (Outcome.STOPPED, Outcome.ALREADY_STOPPED), (res.summary, res.details)


def test_r1s_stop_in_chat_and_repeater_never_waits_for_r2s_live_companion(tmp_path):
    # red on fd735b4a (G1-K8F-S4-5A-RE N1): r1's own endpoints are the template's 5000/8000, so its stop was read as a
    # whole-node stop and waited for every saved instance's endpoints; r2's live Companion (5133) left r1's stop unverified
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "868", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "868", ce="ce1")))
    svc = ControllerService(system=FakeSystem(listeners=[Listener("ipv4", "127.0.0.1", 5133, 74)]).system,
                            paths=Paths(runtime_root=tmp_path))
    r1 = instances.parse_target(svc, "meshcore@r1", "start")[1]
    assert r1 is not None and r1.rid == "r1"
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT1"}, inst=r1).ok
    assert svc.save_config_bundle("meshcore", values={"file_mode": "chat+repeater"}, inst=r1).ok
    res = svc.stop("meshcore", apply=True, inst=r1)
    node = [r for r in res.results if r.component == mm.NODE_ID]
    assert node and node[0].outcome in (Outcome.STOPPED, Outcome.ALREADY_STOPPED), (res.summary, res.details)


def test_a_whole_node_stop_checks_every_saved_instances_own_endpoints(tmp_path):
    # the other path (no instance: the whole component): r2's live Companion keeps the whole node's stop unverified
    svc, _r2, _r3 = _box(tmp_path, listeners=[Listener("ipv4", "127.0.0.1", 5133, 75)])
    cr = svc._lifecycle().stop(svc.stack("meshcore").component(mm.NODE_ID))
    assert cr.outcome == Outcome.ENDPOINT_STILL_PRESENT, cr.summary

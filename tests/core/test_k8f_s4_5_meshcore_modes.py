"""K8f S4-5 (DEC-W409x): the stack-wide MeshCore mode reads go per instance. Two radios on 868: r2 chat, r3 a
repeater. Status (StatusProber) and a whole-component stop take the node endpoints of any instance's mode; an
instance's own view, its stop verification and its Pages notes take its own mode, never the other radio's."""
import radios_fixture

from lhpc.core import instances
from lhpc.core import meshcore_mode as mm
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def _box(tmp_path, listeners=()):
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    fake = FakeSystem(listeners=list(listeners))
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
    # red on dc5d894a: the snapshot kept only the node endpoints of one radio's mode (r2: chat), so r3's own view
    # probed a Companion port it never opens and never its dashboard
    svc, r2, r3 = _box(tmp_path)
    cs = next(ss.components[mm.NODE_ID] for ss in svc.build_snapshot().stacks if ss.stack.id == "meshcore")
    assert sorted(mm._port(o.spec) for o in cs.endpoints) == [5000, 8000]
    assert [o.spec.address for o in instances.endpoint_statuses(svc, cs, r3)] == ["127.0.0.1:8266"]
    assert [o.spec.address for o in instances.endpoint_statuses(svc, cs, r2)] == ["127.0.0.1:5133"]


def test_one_meshcore_radio_keeps_its_modes_endpoints(tmp_path):
    # the baseline box (r2 chat alone; a pin, green before): status and stop verification take chat's endpoints only
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    node = svc.stack("meshcore").component(mm.NODE_ID)
    assert [mm._port(e) for e in svc._lifecycle().expected_endpoints(node)] == [5000]

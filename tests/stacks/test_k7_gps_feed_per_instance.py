"""K7 (DEC-W384s): every instance has its own GPS feed. Its files (readiness marker, PTY or position socket, monitor
socket) live in state/<rid>/gps/<consumer>; before, every instance of a stack shared state/gps/<consumer> (meshcore@r1
and @r2 published one PTY; meshtastic@r2's feed overwrote r1's marker). MeshCom's feed connects to its own guest's
UART socket, run.sh's .run/<n>/ (n = N - 1; r1 = the single-guest .run/)."""
import json
import os
import time

import radios_fixture

from lhpc.core import gps
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import StatusProber

TWO_868 = (radios_fixture.R3, radios_fixture.R2_868)        # r3 and r2, both 868: two meshtastic instances


def _marker(root, rid, state="ready", age=0, pid=None):
    d = root / "state" / rid / "gps" / "meshtastic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "readiness.json").write_text(json.dumps({"state": state, "detail": "", "sentences": 7, "fixes": 1,
                                                  "updated": int(time.time()) - age, "pid": pid or os.getpid()}))  # wallclock: the record's timestamp, data the product ages against its clock


def _feed(svc):
    return next(c for c in svc.stack("meshtastic").components if c.id == "meshtastic-gps")


def test_k7_each_instances_feed_has_its_own_state_dir_and_endpoint(tmp_path):
    root = str(tmp_path)
    assert gps.bridge_state_dir(root, "meshtastic", "r2") == f"{root}/state/r2/gps/meshtastic"
    assert gps.bridge_endpoint_path(root, "meshtastic", "r2") == f"{root}/state/r2/gps/meshtastic/nmea0"
    assert gps.bridge_endpoint_path(root, "meshcore", "r3") == f"{root}/state/r3/gps/meshcore/position.sock"
    assert gps.bridge_endpoint_path(root, "meshcom", "r2") == f"{root}/src/meshcom-qemu-raspi/.run/1/gps-uart1.sock"
    assert gps.bridge_endpoint_path(root, "meshcom", "r1") == f"{root}/src/meshcom-qemu-raspi/.run/gps-uart1.sock"
    assert gps.bridge_state_dir(root, "meshtastic") == f"{root}/state/gps/meshtastic"          # no hardware list


def test_k7_an_instances_generated_config_reads_its_own_feed(tmp_path):
    radios_fixture.write(tmp_path, TWO_868)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=2947).ok
    assert svc._gps_config_values("meshtastic", Instance("r2", band="868"))["{gps_device}"] == \
        f"{tmp_path}/state/r2/gps/meshtastic/nmea0"
    assert svc._gps_config_values("meshtastic", Instance("r3", band="868"))["{gps_device}"] == \
        f"{tmp_path}/state/r3/gps/meshtastic/nmea0"


def test_k7_the_start_gate_reads_the_starting_instances_marker(tmp_path):
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r2", state="ready")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.gps_feed_state(_feed(svc), Instance("r2", band="868"))["state"] == "ready"
    assert svc.gps_feed_state(_feed(svc), Instance("r3", band="868")) == {}       # r3's feed has not published


def test_k7_the_status_names_the_instance_whose_feed_is_unhealthy(tmp_path):
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r2", state="ready")
    _marker(tmp_path, "r3", state="ready", age=3600)                       # r3's feed stopped refreshing
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))
    comp = _feed(ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
    assert prober._gps_feed_ready(comp) == (False, "r3: readiness marker is stale")
    _marker(tmp_path, "r3", state="connected")
    assert prober._gps_feed_ready(comp) == (True, "source live (7 sentences)")


def test_k7_one_instances_feed_alone_is_the_components_verdict(tmp_path):
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r3", state="connected")
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path))
    comp = _feed(ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
    assert prober._gps_feed_ready(comp) == (True, "source reachable, waiting for a fix")


def test_k7_the_start_admission_waits_for_the_starting_instances_feed(tmp_path):
    # meshtastic@r2's start is admitted on r2's live feed; a marker of no instance (the bare dir) is not it
    import dataclasses
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r2", state="ready")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = dataclasses.replace(_feed(svc), readiness_timeout=0.3)          # a short budget: the r3 case waits it out
    assert svc._gps_feed_admission(comp, Instance("r2", band="868")) == (True, "position source live (7 sentences)")
    assert svc._gps_feed_admission(comp, Instance("r3", band="868"))[0] is False


def test_g1_k7a_1_a_running_feed_without_a_marker_is_unhealthy(tmp_path):
    # meshtastic@r2's feed is live, meshtastic@r3's runs but never published: the component is not healthy, r3 named;
    # before, only existing markers counted and r3 was dropped (a healthy status for a feed delivering nothing)
    from lhpc.core import instances
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r2", state="ready")
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = _feed(plain)
    spec = comp.process
    table = {500 + n: [f"/x/{spec.exec_name}", *spec.all_args,
                       *instances._token(plain, comp, Instance(rid, band="868"), "868")]
             for n, rid in enumerate(("r2", "r3"))}
    prober = StatusProber(FakeSystem(cmdlines_data=table).system, Paths(runtime_root=tmp_path))
    assert prober._gps_feed_ready(comp) == (False, "r3: no readiness marker")
    _marker(tmp_path, "r3", state="connected")
    assert prober._gps_feed_ready(comp) == (True, "source live (7 sentences)")


def test_g1_k7a_4_a_left_over_marker_no_running_feed_names_does_not_count(tmp_path):
    # a pre-K7 state/gps/meshtastic marker, expired, beside meshtastic@r2's live feed: r2 alone decides (healthy)
    from lhpc.core import instances
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r2", state="ready")
    bare = tmp_path / "state" / "gps" / "meshtastic"
    bare.mkdir(parents=True)
    (bare / "readiness.json").write_text(json.dumps({"state": "ready", "updated": int(time.time()) - 3600, "pid": 1}))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = _feed(plain)
    table = {500: [f"/x/{comp.process.exec_name}", *comp.process.all_args,
                   *instances._token(plain, comp, Instance("r2", band="868"), "868")]}
    prober = StatusProber(FakeSystem(cmdlines_data=table).system, Paths(runtime_root=tmp_path))
    assert prober._gps_feed_ready(comp) == (True, "source live (7 sentences)")


def test_g1_k7a_1_the_snapshot_degrades_on_a_running_feed_without_a_marker(tmp_path):
    # the same through the public status path (build_snapshot): the feed component reads degraded, not running
    from lhpc.core import instances
    from lhpc.core.model import RunState
    radios_fixture.write(tmp_path, TWO_868)
    _marker(tmp_path, "r2", state="ready")
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = _feed(plain)
    table = {500 + n: [f"/x/{comp.process.exec_name}", *comp.process.all_args,
                       *instances._token(plain, comp, Instance(rid, band="868"), "868")]
             for n, rid in enumerate(("r2", "r3"))}
    svc = ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))
    st = svc.build_snapshot(fresh=True).stack("meshtastic").components["meshtastic-gps"]
    assert st.run_state is not RunState.RUNNING and st.evidence.get("gps.feed") == "r3: no readiness marker", \
        (st.run_state, st.evidence.get("gps.feed"))

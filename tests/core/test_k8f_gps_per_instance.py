"""K8f S2 (DEC-W384am): every GPS answer of a start is the STARTING instance's own `use_gps` switch, never another
radio's. The baseline box (tests/conftest.py) has meshtastic on r1 (433) and r2 (868); here r1 uses GPS and r2 does
not, with a live source (gpsd) that meshtastic reads through its bridge feed."""
import pytest

from lhpc.core import instances
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=2947).ok
    i1, i2 = (instances.parse_target(svc, f"meshtastic@{r}", "start")[1] for r in ("r1", "r2"))
    names = {"node_name": "TEST", "node_short": "T"}
    assert svc.save_config_bundle("meshtastic", values={**names, "use_gps": "on"}, inst=i1).ok
    assert svc.save_config_bundle("meshtastic", values={**names, "use_gps": "off"}, inst=i2).ok
    return svc, i1, i2


@pytest.mark.parametrize("rid, feed", [("r1", True), ("r2", False)])
def test_the_start_plan_runs_the_feed_by_the_starting_instances_own_switch(tmp_path, rid, feed):
    # red on a0c884c1: both plans read r2's switch (the bare-id fallback), so meshtastic@r1 planned without its feed
    svc, i1, i2 = _box(tmp_path)
    res = svc.start("meshtastic", inst={"r1": i1, "r2": i2}[rid], apply=False)
    assert res.ok, res.summary
    assert ("meshtastic-gps" in "\n".join(res.details or ())) is feed, res.details


@pytest.mark.parametrize("rid, feed", [("r1", True), ("r2", False)])
def test_the_rendered_config_reads_the_instances_own_feed_by_its_own_switch(tmp_path, rid, feed):
    # red on a0c884c1: r1's meshtasticd.yaml was rendered with no GPS device (r2's "off" read)
    svc, i1, i2 = _box(tmp_path)
    inst = {"r1": i1, "r2": i2}[rid]
    svc.write_config_files("meshtastic", inst.band, inst=inst)
    text = (tmp_path / "config" / "files" / rid / "meshtasticd.yaml").read_text()
    assert (f"{tmp_path}/state/{rid}/gps/meshtastic/nmea0" in text) is feed, text


@pytest.mark.parametrize("running, blocked", [(["meshtastic@r1"], ["meshtastic"]), (["meshtastic@r2"], [])])
def test_a_source_change_is_blocked_by_a_running_instance_that_uses_gps(tmp_path, monkeypatch, running, blocked):
    # red on a0c884c1 for r1: its live reader did not block (r2's "off" read), the source changed beneath it
    svc, _i1, _i2 = _box(tmp_path)
    monkeypatch.setattr(instances, "running_keys", lambda s, sid: running if sid == "meshtastic" else [])
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid == "meshtastic" else RunState.STOPPED
    svc.build_snapshot = lambda fresh=False: snap
    assert svc.gps_consumers_running() == blocked
    assert svc.set_gps(source="auto").ok is (not blocked)


@pytest.mark.parametrize("rid, refused", [("r1", True), ("r2", False)])
def test_a_receiver_gpsd_owns_refuses_only_the_instance_that_uses_gps(tmp_path, monkeypatch, rid, refused):
    # gps_block per instance; red on a0c884c1 for r1: r2's "off" read, so r1 started onto a receiver gpsd owns
    from lhpc.core import config as _cfg
    from lhpc.core import gps as _gps
    svc, i1, i2 = _box(tmp_path)
    _cfg.save_gps(svc._paths, source="nmea", device="/dev/null", nmea_baud="9600")
    svc._invalidate_config()
    monkeypatch.setattr(_gps, "gpsd_owns_device", lambda d, h, po, timeout=3.0: (True, "/dev/null (gpsd)"))
    res = svc.start("meshtastic", inst={"r1": i1, "r2": i2}[rid], apply=False)
    assert (not res.ok and "gpsd" in res.summary) is refused, res.summary


@pytest.mark.parametrize("rid, refused", [("r2", True), ("r3", False)])
def test_meshcores_position_decision_is_the_instances_own(tmp_path, rid, refused):
    # meshcore_position per instance (two 868 radios): an unusable source refuses only the instance that uses GPS;
    # red on a0c884c1: both read one fallback instance's switch
    import radios_fixture

    from lhpc.core import config as _cfg
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ins = {r: instances.parse_target(svc, f"meshcore@{r}", "start")[1] for r in ("r2", "r3")}
    assert svc.save_config_bundle("meshcore", values={"use_gps": "on", "file_node_name": "TEST"}, inst=ins["r2"]).ok
    assert svc.save_config_bundle("meshcore", values={"use_gps": "off", "file_node_name": "TEST"}, inst=ins["r3"]).ok
    _cfg.save_gps(svc._paths, source="nmea", device="/dev/does-not-exist", nmea_baud="9600")
    svc._invalidate_config()
    res = svc.start("meshcore", inst=ins[rid], apply=False)
    assert ("position source is not usable" in res.summary) is refused, res.summary


def _nmea_free(svc, monkeypatch):
    from lhpc.core import config as _cfg
    from lhpc.core import gps as _gps
    _cfg.save_gps(svc._paths, source="nmea", device="/dev/null", nmea_baud="9600")     # a receiver a start claims
    svc._invalidate_config()
    monkeypatch.setattr(_gps, "gpsd_owns_device", lambda d, h, po, timeout=3.0: (False, "free"))


def _running(svc, monkeypatch, keys: dict, comps: set):
    monkeypatch.setattr(instances, "running_keys", lambda s, sid: keys.get(sid, []))
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid in comps else RunState.STOPPED
    svc.build_snapshot = lambda fresh=False: snap


def _gps_blockers(svc, target, inst):
    return [b for b in svc.run_blockers(target, inst=inst) if b["resource"].startswith("gps.")]


@pytest.mark.parametrize("rid, blocked", [("r1", True), ("r2", False)])
def test_the_starting_instance_claims_the_receiver_by_its_own_switch(tmp_path, monkeypatch, rid, blocked):
    # run_blockers' own claim; red on a0c884c1 for r1: no claim (r2's "off" read), it would open a held receiver
    svc, i1, i2 = _box(tmp_path)
    _nmea_free(svc, monkeypatch)
    _running(svc, monkeypatch, {"meshcom": [s for s in instances.keys(svc, "meshcom")][:1]}, {"meshcom-qemu"})
    assert bool(_gps_blockers(svc, "meshtastic", {"r1": i1, "r2": i2}[rid])) is blocked


@pytest.mark.parametrize("holder, blocked", [("meshtastic@r1", True), ("meshtastic@r2", False)])
def test_a_running_holder_holds_the_receiver_by_its_own_instances_switch(tmp_path, monkeypatch, holder, blocked):
    # run_blockers' holder claim; red on a0c884c1 for r1: the live r1 reader held nothing (r2's "off" read)
    svc, _i1, _i2 = _box(tmp_path)
    _nmea_free(svc, monkeypatch)
    _running(svc, monkeypatch, {"meshtastic": [holder]}, {"meshtastic"})
    target = instances.parse_target(svc, f"meshcom@{instances.keys(svc, 'meshcom')[0].partition('@')[2]}", "start")[1]
    assert bool(_gps_blockers(svc, "meshcom", target)) is blocked


@pytest.mark.parametrize("keys, unclaimed", [(["meshtastic@r2"], True), (["meshtastic@r2", "meshtastic@r9"], False)])
def test_a_partly_told_running_set_never_hides_a_gps_reader(tmp_path, monkeypatch, keys, unclaimed):
    # G1-K8F-S2 B1: r2 (use_gps off) is told, but a meshtastic process no instance claims runs too, or a running key
    # does not resolve (r9 is no radio of the list): either may be r1 (use_gps on), so the source change is refused.
    # red on ec85c9f1: the told r2 alone answered "no GPS user" and the source changed beneath it
    svc, _i1, _i2 = _box(tmp_path)
    monkeypatch.setattr(instances, "running_keys", lambda s, sid: keys if sid == "meshtastic" else [])
    monkeypatch.setattr(instances, "unclaimed", lambda s, comp, *a: unclaimed and comp.id == "meshtastic")
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid == "meshtastic" else RunState.STOPPED
    svc.build_snapshot = lambda fresh=False: snap
    assert svc.gps_consumers_running() == ["meshtastic"]
    assert not svc.set_gps(source="auto").ok

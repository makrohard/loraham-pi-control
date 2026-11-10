"""K8f S3: the lock bundle of an operation is the instance's own, like its conflict set (run_blockers keys each claim
by `instances.claim_key(r, with_bases(.., inst))`): meshtastic@r2 locks r2's ports (4403/9443 + 133), never r1's,
and so do a stop_owners holder and a cascade's dependents. Seen through the unchanged public start/stop: the lock
keys are recorded and the operation refused busy before anything runs."""
import pytest

from lhpc.core import instances, reslock
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    seen, orig = [], svc._lifecycle_lock_keys

    def spy(*a, **k):
        seen.append(orig(*a, **k))
        raise reslock.ResourceBusy("probe", {"operation": "probe"})
    svc._lifecycle_lock_keys = spy
    return svc, seen


def _inst(svc, key):
    return instances.parse_target(svc, key, "start")[1]


def _ports(keys):
    return sorted(k for k in keys if k.startswith("claim.tcp.port."))


def _running(svc, monkeypatch, keys: dict, comps: set):
    monkeypatch.setattr(instances, "running_keys", lambda s, sid: keys.get(sid, []))
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid in comps else RunState.STOPPED
    svc.build_snapshot = lambda fresh=False: snap


@pytest.mark.parametrize("rid, ports", [("r1", ["claim.tcp.port.4403", "claim.tcp.port.9443"]),
                                        ("r2", ["claim.tcp.port.4536", "claim.tcp.port.9576"])])
def test_a_start_locks_its_own_instances_ports(tmp_path, rid, ports):
    # red on d97e3a48 for r2: it locked r1's 4403/9443 (the claim as declared), while run_blockers checks 4536/9576
    svc, seen = _box(tmp_path)
    inst = _inst(svc, f"meshtastic@{rid}")
    assert svc.save_config_bundle("meshtastic", values={"node_name": "T", "node_short": "T"}, inst=inst).ok
    res = svc.start("meshtastic", inst=inst, apply=True)
    assert not res.ok and "busy" in res.summary, res.summary
    assert _ports(seen[0]) == ports


def test_a_stop_owners_holder_is_locked_by_its_running_instances_keys(tmp_path, monkeypatch):
    # red on d97e3a48: the holder meshtastic@r2 was locked by r1's ports, the start's own kiss@r2 by r1's 8001
    svc, seen = _box(tmp_path)
    _running(svc, monkeypatch, {"meshtastic": ["meshtastic@r2"]}, {"meshtastic"})
    res = svc.start("kiss", inst=_inst(svc, "kiss@r2"), apply=True, stop_owners=True)
    assert not res.ok and "busy" in res.summary, res.summary
    assert _ports(seen[0]) == ["claim.tcp.port.4536", "claim.tcp.port.8134", "claim.tcp.port.9576"]


def test_a_cascades_stopped_dependents_are_locked_on_the_stopped_radios_band(tmp_path, monkeypatch):
    # none of kiss's instances runs: the stop of daemon@r2 (868) locks kiss@r2's 8134 (so it cannot start mid-cascade),
    # never kiss@r1's 8001 on 433; red on the base: the declared 8001 (and every band, the daemon's band unknown)
    svc, seen = _box(tmp_path)
    _running(svc, monkeypatch, {}, set())
    res = svc.stop("daemon", inst=_inst(svc, "daemon@r2"), apply=True)
    assert not res.ok and "busy" in res.summary, res.summary
    assert "claim.tcp.port.8134" in seen[0] and "claim.tcp.port.8001" not in seen[0], seen[0]


@pytest.mark.parametrize("rid, locked", [("r1", True), ("r2", False)])
def test_the_receiver_lock_is_the_starting_instances_by_its_own_switch(tmp_path, monkeypatch, rid, locked):
    # the GPS receiver key in the lock set follows the instance's use_gps (r1 on, r2 off), like its conflict set
    from lhpc.core import config as _cfg
    from lhpc.core import gps as _gps
    svc, seen = _box(tmp_path)
    _cfg.save_gps(svc._paths, source="nmea", device="/dev/null", nmea_baud="9600")
    svc._invalidate_config()
    monkeypatch.setattr(_gps, "gpsd_owns_device", lambda d, h, po, timeout=3.0: (False, "free"))
    for r, on in (("r1", "on"), ("r2", "off")):
        assert svc.save_config_bundle("meshtastic", values={"node_name": "T", "node_short": "T", "use_gps": on},
                                      inst=_inst(svc, f"meshtastic@{r}")).ok
    key = "claim." + svc._gps_device_claim("meshtastic", _inst(svc, "meshtastic@r1"))     # r1's (on) receiver claim
    res = svc.start("meshtastic", inst=_inst(svc, f"meshtastic@{rid}"), apply=True)
    assert not res.ok and "busy" in res.summary, res.summary
    assert key != "claim." and (key in seen[0]) is locked, seen[0]


def test_a_stop_locks_its_own_instances_ports(tmp_path):
    # red on ec85c9f1: stop meshtastic@r2 locked r1's 4403/9443
    svc, seen = _box(tmp_path)
    res = svc.stop("meshtastic", inst=_inst(svc, "meshtastic@r2"), apply=True)
    assert not res.ok and "busy" in res.summary, res.summary
    assert _ports(seen[0]) == ["claim.tcp.port.4536", "claim.tcp.port.9576"]


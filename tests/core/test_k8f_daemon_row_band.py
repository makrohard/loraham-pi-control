"""K8f: the daemon's own instance runs on its radio ROW's band like every instance (instances.radio_band): a start
of `daemon@r2` without a band plans radio r2 on 868, never "every band" (G4-web: the adapters pass no band)."""
from lhpc.core import instances
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_a_daemon_instance_start_plans_its_radio_rows_band_and_names_the_holder(tmp_path, monkeypatch):
    # red on 29b437d7: "start/ensure --radio none (433 owned by meshtastic, 868 owned by meshtastic — skipped)"
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(instances, "running_keys", lambda s, sid: ["meshtastic@r2"] if sid == "meshtastic" else [])
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid == "meshtastic" else RunState.STOPPED
    svc.build_snapshot = lambda fresh=False: snap
    res = svc.start("daemon", inst=instances.parse_target(svc, "daemon@r2", "start")[1], apply=False)
    text = "\n".join(res.details or ())
    assert res.ok and "start/ensure --radio 868 --id r2" in text, text
    assert "[conflict] loraham.radio.r2 is held by running stack 'meshtastic'" in text, text


def test_a_daemon_instance_whose_radio_left_the_list_is_refused_by_name(tmp_path):
    import radios_fixture

    from lhpc.core.commands import Instance
    radios_fixture.write(tmp_path)                                 # r1 433, r2 868; no r5
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    # red on 29b437d7: the start went on, planned for every band
    res = svc.start("daemon", inst=Instance("r5", band="868"), apply=False)
    assert (res.ok, res.summary) == (False, "Refusing to start: daemon@r5: r5 is not in the hardware list"), res.summary

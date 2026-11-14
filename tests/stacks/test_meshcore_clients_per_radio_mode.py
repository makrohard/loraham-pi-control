"""A22 (0.14.0 manual test): a MeshCore instance's start admits the ticked Companion clients (webui) by ITS OWN mode
(DEC-W52), never another radio's on the same band. Through run_action, the plan `lhpc stack start meshcore@rN`
prints."""
import radios_fixture

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = radios_fixture.RADIOS + (radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1"),)


def _start(tmp_path, modes):
    """{rid: whether the start plan of meshcore@rid starts the web UI}, with each radio's own `modes[rid]` saved and the
    web UI ticked (the stack's one global auto-start tick, K6)."""
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for rid, mode in modes.items():
        values = {"file_node_name": f"N{rid}", "file_mode": mode, "file_repeater_name": "Relay",
                  "autostart_meshcore-webui": "on"}
        assert svc.save_config_bundle("meshcore", values=values, inst=instances.copy_of(svc, "meshcore", rid)[0]).ok
    svc._invalidate_config()
    out = {}
    for rid in modes:
        plan = svc.run_action("start", "meshcore", apply=False, inst=instances.copy_of(svc, "meshcore", rid)[0])
        assert plan.ok, plan.summary
        started = [ln.split()[1] for ln in plan.details if ln.split()[:1] == ["[start]"]]
        assert "meshcore-node" in started, started                          # the anchor: a real start plan
        out[rid] = "meshcore-webui" in started
    return out


def test_a_chat_radio_starts_its_ticked_web_ui_beside_a_repeater_radio(tmp_path):
    # red-before (b3fc6e60): r4's plan has no meshcore-webui (judged by r2's repeater mode)
    got = _start(tmp_path, {"r2": "repeater", "r4": "chat"})
    assert got == {"r2": False, "r4": True}                                # repeater-only r2: no Companion, no client


def test_a_repeater_radio_beside_a_chat_radio_starts_no_client(tmp_path):
    got = _start(tmp_path, {"r2": "chat", "r4": "repeater"})
    assert got == {"r2": True, "r4": False}


def test_a_start_without_an_instance_keeps_todays_mode_read(tmp_path):
    # G1-B1-PLAN N11: no instance (a template caller) reads meshcore_mode() as on b3fc6e60 (service_params.py:1687)
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    calls = []
    real = svc.meshcore_mode
    svc.meshcore_mode = lambda inst=None: (calls.append(inst), real(inst))[1]
    svc._run_order("meshcore", None)
    assert calls and calls[-1] is None

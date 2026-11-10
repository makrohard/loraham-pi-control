"""K8f S4-1 (K8F-S4-DESIGN-v4, G1-K8F-S4-D1 B2/B3): a change of the global callsign marks exactly the live instances
that INHERIT it, each by its own key: chat@r1 with its own explicit call is left alone, chat@r2 (inheriting) is
marked; two inheriting instances on one band are both marked. Two radios on 433 (r1, r2), chat live on both."""
import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path, monkeypatch, explicit_r1: bool):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))           # r1 + r2, both 433
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_operator_identity("DL1AAA").ok
    if explicit_r1:
        assert svc.save_config_bundle("chat", values={"file_call": "DL9XYZ"},
                                      inst=instances.parse_target(svc, "chat@r1", "start")[1]).ok
    monkeypatch.setattr(instances, "instance_runs", lambda s, comp, inst, band: comp.id == s.stack("chat").main)
    monkeypatch.setattr(svc, "stack_running", lambda sid: sid == "chat")              # chat runs (both radios)
    return svc


@pytest.mark.parametrize("explicit_r1, marked", [(True, ["chat@r2"]), (False, ["chat@r1", "chat@r2"])])
def test_a_new_global_callsign_marks_each_inheriting_live_instance(tmp_path, monkeypatch, explicit_r1, marked):
    # red on a3dce2ea: one (stack, band) pair for both radios, judged by the fallback instance (r1): with r1's own
    # call nothing was marked (r2 missed); with both inheriting only the fallback's key chat@r1 was marked
    svc = _box(tmp_path, monkeypatch, explicit_r1)
    assert svc.set_operator_identity("DL2BBB").ok
    assert [k for k in ("chat@r1", "chat@r2") if svc.restart_required(k) is not None] == marked

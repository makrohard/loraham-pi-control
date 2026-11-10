"""K8f S4-1 (K8F-S4-DESIGN-v4, G1-K8F-S4-D1 B1 / D3 N4): the Settings view of `<stack>@<rid>` is that instance's in
every part: the flat values, each component's and config file's rows, the band and whether it runs. Two radios on
one band (r1, r2 on 433) with different saved values are the case that told them apart: the component rows and the
running state read one fallback instance (r1) while the flat values read r2."""
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2))           # r1 + r2, both 433
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ins = {r: instances.parse_target(svc, f"kiss@{r}", "start")[1] for r in ("r1", "r2")}
    assert svc.save_config_bundle("kiss", values={"tx_freq": "434.000"}, inst=ins["r1"]).ok
    assert svc.save_config_bundle("kiss", values={"tx_freq": "434.125"}, inst=ins["r2"]).ok
    return svc, ins


def _component_value(view, name):
    return next(c["values"][name] for c in view["components"] if name in c["values"])


def test_each_part_of_an_instances_view_is_its_own(tmp_path):
    # red on a3dce2ea: the flat values said r2's 434.125, the component row r1's 434.000
    svc, _ins = _box(tmp_path)
    for rid, freq in (("r1", "434.000"), ("r2", "434.125")):
        view = svc.config_view("kiss", rid=rid)
        assert (view["values"]["tx_freq"], _component_value(view, "tx_freq")) == (freq, freq), (rid, view["values"])


def test_an_instances_view_says_whether_that_instance_runs(tmp_path, monkeypatch):
    # D3 N4: kiss runs on r1 only; r2's view says it is not running. red on a3dce2ea: the stack-wide state (running)
    svc, _ins = _box(tmp_path)
    monkeypatch.setattr(instances, "instance_runs", lambda s, comp, inst, band: inst.rid == "r1")
    from lhpc.core.model import RunState
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if ss.stack.id == "kiss" else RunState.STOPPED
    svc.build_snapshot = lambda fresh=False: snap
    assert (svc.config_view("kiss", rid="r1")["running"], svc.config_view("kiss", rid="r2")["running"]) == (True, False)


def test_a_configured_box_without_a_radio_for_the_stack_has_no_template_candidate(tmp_path):
    # G1-K8F-S4-D3 B1: r1 + r2 on 433 and an 868-only stack (meshcore) with a default-equal value in its global file:
    # no migration candidate (the global file is the no-list template). red on a3dce2ea: ('meshcore', 'use_gps')
    from lhpc.core import config as _cfg
    svc, _ins = _box(tmp_path)
    _cfg.save_stack_config(svc._paths, "meshcore", {"use_gps": "on"})
    svc._invalidate_config()
    assert [c["key"] for c in svc._migration_candidates() if c["stack"] == "meshcore"] == []

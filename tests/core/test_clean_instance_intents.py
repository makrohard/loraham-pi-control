"""Item 16 batch 1 (from 7e): `lhpc clean <stack> --purge` removes the stack's operator stop intents, its own
(`state/stop-intent/<stack>.json`) and each instance's (`<stack>@<rid>.json`, 7e's key), also a removed instance's;
another stack's, and a stack whose id only starts the same, stay."""
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_a_clean_removes_the_stacks_own_and_its_instances_stop_intents(tmp_path):
    d = tmp_path / "state" / "stop-intent"
    d.mkdir(parents=True)
    for name in ("kiss", "kiss@r3", "kiss@r12", "chat@r3", "kissx@r3"):
        (d / f"{name}.json").write_text('{"stack": "x"}')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.clean("kiss", apply=True, purge=True)
    assert res.ok, res.details
    assert sorted(p.name for p in d.iterdir()) == ["chat@r3.json", "kissx@r3.json"]

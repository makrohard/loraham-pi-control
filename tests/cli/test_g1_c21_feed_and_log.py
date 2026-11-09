"""Gate-1 C-21 on item 16 batch 2, per radio on a band two radios share: note 1 a start clears the RF activity
window of the radio it runs on, never the other radio's; note 3 a named radio's start log is that radio's or none,
never another radio's."""
import radios_fixture
from radios_fixture import R1, R2

from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import Component, ComponentKind
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def test_a_start_clears_its_own_radios_window_only(box, tmp_path):
    run = box((R1, R2), ports=(8001,))
    floors = {rid: tmp_path / "state" / f"daemon-feed-floor-433-{rid}" for rid in ("r1", "r2")}
    for p in floors.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("123")
    rc, out, launches = run("start", "kiss@r1")                   # slot 0 of kiss on r1 (K3: named)
    assert rc == 0 and ("loraham-kiss-tnc", "433", "r1") in launches, (out, launches)
    assert floors["r1"].read_text() != "123"
    assert floors["r2"].read_text() == "123"


def test_a_named_radios_log_is_its_own_or_none(tmp_path):
    radios_fixture.write(tmp_path, (R1, R2))
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "start-c-433-r1.log").write_text("r1\n")
    assert life.start_log(comp, "433", "r2") is None
    assert life.start_log(comp, "433", "r1").name == "start-c-433-r1.log"

"""The stack-copy cleanup, C4: a listed copy refused over its identity names the copy's own `lhpc config
<stack>@<rid>` remedy (a `lhpc config <stack>` hint would save into slot 0's file and the copy's start would refuse
again). Slot 0's remedy stays `lhpc config <stack>`."""
import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TAIL = ("YOURCALL-10   # YOURCALL-10 = your callsign (+optional SSID); "
        "or once for every licensed stack: lhpc config operator --callsign YOURCALL")
ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_copys_identity_refusal_names_the_copys_own_config_command(tmp_path):
    svc = _svc(tmp_path)
    copy, why = instances.copy_of(svc, "chat", "r3")
    assert why == ""
    res = svc.start("chat", apply=False, inst=copy)
    assert res.ok is False and res.data.get("enforce_fields")
    assert res.next_commands == [f"lhpc config chat@r3 call {TAIL}"]


def test_slot0s_identity_refusal_names_the_stack(tmp_path):
    res = _svc(tmp_path).start("chat", apply=False)
    assert res.ok is False and res.data.get("enforce_fields")
    assert res.next_commands == [f"lhpc config chat call {TAIL}"]

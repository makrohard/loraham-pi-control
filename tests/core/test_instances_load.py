"""Item 7b: instances.load reads config/instances.toml fail-closed, and commands.stack_instances builds each listed
instance with the slots of the other stacks' instances on its radio (`{port_of:}`)."""
import pytest

from lhpc.core import commands, instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _paths(tmp_path, text=None):
    (tmp_path / "config").mkdir(exist_ok=True)
    if text is not None:
        (tmp_path / "config" / "instances.toml").write_text(text)
    return Paths(runtime_root=tmp_path)


def test_absent_and_valid(tmp_path):
    assert instances.load(_paths(tmp_path)).kind == instances.ABSENT
    state = instances.load(_paths(tmp_path, 'kiss = ["", "r3"]\n'))
    assert (state.kind, state.slots) == (instances.VALID, {"kiss": ("", "r3")})
    assert instances.listed(state, "kiss") == ((2, "r3"),) and instances.listed(state, "voice") == ()


@pytest.mark.parametrize("text,why", [
    ("kiss = [", "is not valid TOML"), ('kiss = "r3"', "kiss: a list of radio ids"),
    ("kiss = [3]", "kiss: a list of radio ids"), ('kiss = ["r0"]', "kiss: 'r0' is not r1..r99"),
    ('kiss = ["r3", "r3"]', "kiss: a radio holds one instance of a stack"), ('Kiss = ["r3"]', "'Kiss' is not a stack id"),
    ('kiss = ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8"]', "kiss: at most 7 instances beyond slot 0"),
])
def test_a_registry_that_does_not_read_back_is_invalid_with_its_reason(tmp_path, text, why):
    paths = _paths(tmp_path, text)
    state = instances.load(paths)
    assert state.kind == instances.INVALID and any(why in r for r in state.reasons), state.reasons
    assert instances.listed(state, "kiss") == ()


def test_an_unreadable_registry_is_invalid_not_absent(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "config" / "instances.toml").mkdir()               # a directory where the file belongs
    assert instances.load(paths).kind == instances.INVALID


def test_peer_slots_name_the_other_stacks_instances_on_the_same_radio(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path, 'kiss = ["", "r3"]\ngraywolf = ["r3"]\n'))
    gw = commands.stack_instances(svc.stack("graywolf"), svc._paths)[1]
    assert (gw.slot, gw.rid, gw.subst("{port_of:kiss:8001}")) == (1, "r3", "8021")   # kiss@r3 is slot 2


def test_without_paths_a_stack_has_slot_0_only(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=_paths(tmp_path, 'kiss = ["r3"]\n'))
    assert commands.stack_instances(svc.stack("kiss")) == (commands.SLOT0,)

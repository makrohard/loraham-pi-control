"""Item 7f, DEC-W60: a listed MeshCore instance's start is judged on its own saved settings, never the global file's: its
mode decides which identity it needs and which endpoints mean ready, and its own node and repeater names are the
ones checked. Through `lhpc stack start meshcore@r3`."""
from radios_fixture import R1, R2_868, R3

from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths

LISTED = 'meshcore = ["r3"]\n'


def _save(tmp_path, values, rid=""):
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore", values, inst=Instance(rid) if rid else None)


def _node_started(launches) -> bool:
    return any(comp == "meshcore-node" and rid == "r3" for comp, _band, rid in launches)


def test_an_instance_starts_on_its_own_node_name_and_mode(box, tmp_path):
    # the global file: a pure repeater without a chat node name; the instance: a chat node with its own name, ready on its
    # own Companion port alone
    run = box((R1, R2_868, R3), ports=(5266, 9054))
    _save(tmp_path, {"file_mode": "repeater", "file_repeater_name": "Relay 0"})
    _save(tmp_path, {"file_node_name": "Node R3"}, rid="r3")
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 0 and _node_started(launches), out


def test_the_global_files_node_name_does_not_let_an_instance_start(box, tmp_path):
    run = box((R1, R2_868, R3), ports=(5266, 9054))
    _save(tmp_path, {"file_node_name": "Node 0"})
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 1 and "'node_name' is required" in out and not _node_started(launches), out


def test_the_global_files_repeater_name_does_not_let_an_instance_repeater_start(box, tmp_path):
    run = box((R1, R2_868, R3), ports=(5266, 8266, 9054))
    _save(tmp_path, {"file_mode": "repeater", "file_repeater_name": "Relay 0"})
    _save(tmp_path, {"file_mode": "repeater"}, rid="r3")
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 1 and "needs the repeater's own node name" in out and not _node_started(launches), out

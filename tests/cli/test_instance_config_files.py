"""Item 7d part 3 (DEFECTS-0.14.0-v1 (1), C17): a listed instance's start writes ITS config file under its
{instance_dir} (config/files/<rid>/), with its own controller-owned paths; slot 0's file is not written by it. A
purge removes the listed instances' generated config files too, nothing beside them."""
import json

import radios_fixture
from radios_fixture import R1, R2_868, R3

from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core import openhop_store as ops
from lhpc.core.paths import Paths


def _node(tmp_path, name="Node3"):
    # the instance's own node name, config/stacks/meshcore@r3.toml (7f, DEC-W60: slot 0's is never lent)
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore", {"file_node_name": name}, inst=Instance(1, "r3"))


def test_a_meshcore_instance_start_writes_its_own_config_file(box, tmp_path, monkeypatch):
    run = box((R1, R2_868, R3), ports=(5266, 8266, 9054))
    monkeypatch.setattr(ops, "_helper", lambda *a, **k: json.dumps({}))
    _node(tmp_path)
    rc, out, launches = run("start", "meshcore@r3")
    assert ("meshcore-node", "", 1, "r3") in launches, (launches, out)
    mine = tmp_path / "config" / "files" / "r3" / "meshcore.toml"
    assert mine.is_file(), out
    text = mine.read_text()
    for path in ("state/r3/meshcore/companion.db", "state/r3/openhop", "logs/r3/rf-meshcore.log"):
        assert f"{tmp_path}/{path}" in text, (path, text)
    assert not (tmp_path / "config" / "files" / "meshcore.toml").exists()          # slot 0's: not this start's


def test_purge_removes_the_listed_instances_config_files_only(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    radios_fixture.write(tmp_path, (R2_868, R3))  # K4ac: meshcore@r3 (868 radio 2)
    for rel in ("config/files/meshcore.toml", "config/files/r3/meshcore.toml", "config/files/r4/meshcore.toml",
                "config/files/r3/lorachat.conf"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.clean("meshcore", apply=True, purge=True)
    assert res.ok, res.details
    assert not (tmp_path / "config/files/r3/meshcore.toml").exists()
    assert (tmp_path / "config/files/r4/meshcore.toml").exists() and (tmp_path / "config/files/r3/lorachat.conf").exists()

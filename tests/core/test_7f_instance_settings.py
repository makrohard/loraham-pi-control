"""Item 7f, DEC-W60: a listed instance `<stack>@<rid>` keeps its own settings in config/stacks/<sid>@<rid>.toml,
through the same load and update functions; slot 0 keeps <sid>[@<band>].toml, byte-identical. An instance without
its file runs on the manifest defaults: nothing of slot 0's is shared. Its generated config file (write_config_files
with the instance, 7d part 3) is written from its own values."""
from pathlib import Path

import radios_fixture

from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _sid, inst, band, why = instances.resolve(svc, "chat@r3")
    assert why == "" and band == "433"
    return svc, inst


def _conf(tmp_path, rel):
    text = (tmp_path / "config" / "files" / rel).read_text()
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_an_instances_own_value_reaches_its_generated_config_file(tmp_path):
    svc, inst = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "chat", {"file_call": "DL1ABC"}, inst=Instance("r3"))
    assert (tmp_path / "config" / "stacks" / "chat@r3.toml").is_file()
    [cw] = svc.write_config_files("chat", "433", inst=inst)
    assert cw.status == "written" and cw.path.endswith("/config/files/r3/lorachat.conf")
    assert _conf(tmp_path, "r3/lorachat.conf")["CALL"] == "DL1ABC"


def test_slot0s_saved_value_is_not_an_instances_and_the_instance_runs_on_the_defaults(tmp_path):
    svc, inst = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "chat", {"file_call": "DL2XYZ"})       # chat is fixed-band: chat.toml
    default = svc.file_config_values("chat", "433", inst)["call"]
    assert svc.file_config_values("chat", "433")["call"] == "DL2XYZ"            # slot 0's own
    assert default != "DL2XYZ"                                                    # the instance: no inheritance
    svc.write_config_files("chat", "433", inst=inst)
    assert _conf(tmp_path, "r3/lorachat.conf")["CALL"] != "DL2XYZ"                # its file: its default, not slot 0's


def test_an_instances_value_is_never_slot0s(tmp_path):
    svc, inst = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "chat", {"file_call": "DL1ABC"}, inst=Instance("r3"))
    assert svc.file_config_values("chat", "433", inst)["call"] == "DL1ABC"
    assert svc.file_config_values("chat", "433")["call"] != "DL1ABC"
    assert not (tmp_path / "config" / "stacks" / "chat.toml").exists()            # slot 0's file untouched


def test_a_radio_id_key_is_a_radio_id_only(tmp_path):
    import pytest
    from lhpc.core import validators
    paths = Paths(runtime_root=tmp_path)
    from types import SimpleNamespace                      # K6: the radio id keys the store (not the band)
    for bad in ("r0", "r100", "r1/../x", "R1", "x"):
        with pytest.raises(validators.ValidationError):
            cfgmod.load_stack_config(paths, "chat", inst=SimpleNamespace(rid=bad))


def test_a_listed_meshcore_instance_mints_its_own_identity_never_slot0s(tmp_path):
    # Two MeshCore nodes sharing one identity key are one node on the mesh: each instance's generated key is its own,
    # held at config/secrets/<rid>/; the band caller writes the band's first radio's (r2) and r4's is another.
    import hashlib
    import tomllib
    rows = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _sid, inst, band, why = instances.resolve(svc, "meshcore@r4")
    assert why == "" and band == "868"
    cfgmod.update_stack_config(svc._paths, "meshcore", {"file_mode": "chat+repeater", "file_repeater_name": "R4"},
                               inst=Instance("r4"))                     # its repeater's handle names its own chat key
    assert all(w.status == "written" for w in svc.write_config_files("meshcore", "868"))
    assert all(w.status == "written" for w in svc.write_config_files("meshcore", "868", inst=inst))
    files = tmp_path / "config" / "files"
    slot0 = tomllib.loads((files / "r2" / "meshcore.toml").read_text())["identity"]["key"]
    doc = tomllib.loads((files / "r4" / "meshcore.toml").read_text())
    own = doc["identity"]["key"]
    assert len(slot0) == len(own) == 64 and own != slot0
    assert doc["repeater"]["chat_node_id"] == hashlib.sha256(own.encode()).hexdigest()[:16]
    assert (tmp_path / "config" / "secrets" / "r4" / "meshcore_identity.key").read_text().strip() == own
    assert (tmp_path / "config" / "secrets" / "r4").stat().st_mode & 0o777 == 0o700     # as private as slot 0's
    assert (tmp_path / "config" / "secrets" / "r2" / "meshcore_identity.key").read_text().strip() == slot0
    assert not (tmp_path / "config" / "secrets" / "meshcore_identity.key").exists()


def test_a_listed_repeater_instance_fills_its_own_openhop_file_from_its_own_settings(tmp_path, monkeypatch):
    # The instance's mode and repeater name are its own (DEC-W52, DEC-W60): slot 0 in chat mode with another name
    # neither stops the instance's repeater step nor lends it a value, and slot 0's file and stored name stay as they were.
    import json
    from lhpc.core import openhop_store as ops

    def helper(_svc, verb, path, *keys, stdin=""):          # the openhop_file helper's contract, at the given path
        p = Path(path)
        conf = json.loads(p.read_text()) if p.exists() else {}
        if verb == "get":
            return json.dumps({k: conf.get(k) for k in keys})
        if verb == "set":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({**conf, **json.loads(stdin)}))
        return ""
    monkeypatch.setattr(ops, "_helper", helper)
    rows = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _sid, inst, _band, _why = instances.resolve(svc, "meshcore@r4")
    cfgmod.update_stack_config(svc._paths, "meshcore", {"file_repeater_name": "Slot Zero"})
    cfgmod.update_stack_config(svc._paths, "meshcore", {"file_mode": "repeater", "file_repeater_name": "Relay Four"},
                               inst=Instance("r4"))
    svc._invalidate_config()
    ops.start_ensure(svc, "meshcore", inst=inst)
    own = json.loads(ops.file_path(svc._paths, inst).read_text())
    assert own["repeater.node_name"] == "Relay Four" and len(own["repeater.identity_key"]) == 64
    assert not ops.file_path(svc._paths).exists()
    assert svc._stored_param_value("meshcore", "file", "meshcore-node", "repeater_name") == "Slot Zero"


def test_an_instances_openhop_gps_switch_is_its_own(tmp_path):
    # G1-C24 B2: r2 (the band's store) reports its position (use_gps on, the default); meshcore@r4 has it off in its own file, and
    # its generated repeater section says off
    import tomllib
    rows = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _sid, inst, _band, _why = instances.resolve(svc, "meshcore@r4")
    cfgmod.update_stack_config(svc._paths, "meshcore", {"file_mode": "chat+repeater", "file_repeater_name": "R4",
                                                        "use_gps": "off"}, inst=Instance("r4"))
    cfgmod.update_stack_config(svc._paths, "meshcore", {"file_mode": "chat+repeater", "file_repeater_name": "R2"})
    svc.write_config_files("meshcore", "868")
    svc.write_config_files("meshcore", "868", inst=inst)
    files = tmp_path / "config" / "files"
    assert tomllib.loads((files / "r2" / "meshcore.toml").read_text())["repeater"]["use_gps"] == "on"
    assert tomllib.loads((files / "r4" / "meshcore.toml").read_text())["repeater"]["use_gps"] == "off"

"""Item 7f, DEC-W60: a listed instance `<stack>@<rid>` keeps its own settings in config/stacks/<sid>@<rid>.toml,
through the same load and update functions; slot 0 keeps <sid>[@<band>].toml, byte-identical. An instance without
its file runs on the manifest defaults: nothing of slot 0's is shared. Its generated config file (write_config_files
with the instance, 7d part 3) is written from its own values."""
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('chat = ["r3"]\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _sid, inst, band, why = instances.resolve(svc, "chat@r3")
    assert why == "" and band == "433"
    return svc, inst


def _conf(tmp_path, rel):
    text = (tmp_path / "config" / "files" / rel).read_text()
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_an_instances_own_value_reaches_its_generated_config_file(tmp_path):
    svc, inst = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "chat", {"file_call": "DL1ABC"}, band="r3")
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
    cfgmod.update_stack_config(svc._paths, "chat", {"file_call": "DL1ABC"}, band="r3")
    assert svc.file_config_values("chat", "433", inst)["call"] == "DL1ABC"
    assert svc.file_config_values("chat", "433")["call"] != "DL1ABC"
    assert not (tmp_path / "config" / "stacks" / "chat.toml").exists()            # slot 0's file untouched


def test_a_radio_id_key_is_a_radio_id_only(tmp_path):
    import pytest
    from lhpc.core import validators
    paths = Paths(runtime_root=tmp_path)
    for bad in ("r0", "r100", "r1/../x", "R1", "x"):
        with pytest.raises(validators.ValidationError):
            cfgmod.load_stack_config(paths, "chat", bad)

"""K6 (K6-SCOPE-v2, G1-K4B 1): an instance's settings are its own file, config/stacks/<sid>@<rid>.toml, whatever
other radios the hardware list holds: adding, disabling or removing another radio never moves an existing
instance to another store."""
import dataclasses

import pytest
import radios_fixture

from lhpc.core import radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

R1, R3 = radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1")


def _svc(tmp_path, rows):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _port(svc, rid):
    return svc.stack_config("loraham-kiss-tnc", "433", Instance(rid, band="433")).get("kiss_port")


@pytest.mark.parametrize("change", ["remove-r1", "disable-r1", "add-r1"])
def test_another_radios_change_never_moves_an_instances_store(tmp_path, change):
    before = (R3,) if change == "add-r1" else (R1, R3)
    svc = _svc(tmp_path, before)
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8061"}, inst=Instance("r3", band="433")).ok
    assert (tmp_path / "config" / "stacks" / "kiss@r3.toml").is_file()
    assert _port(svc, "r3") == "8061"
    after = {"remove-r1": (R3,), "disable-r1": (dataclasses.replace(R1, enabled=False), R3), "add-r1": (R1, R3)}[change]
    svc = _svc(tmp_path, after)
    assert _port(svc, "r3") == "8061", change

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


def _graywolf_tnc(svc, inst):
    from lhpc.core import commands
    gw = svc.stack("graywolf").component("graywolf")
    step = next(st["argv"] for st in gw.post_steps if st.get("kind") == "exec")       # the provisioning: its TNC
    return commands.expand_argv([t for t in step if t in ("{param:tnc_host}", "{param:tnc_port}")], gw,
                                svc.stack_config("graywolf", inst.band, inst), svc.config().operator, "/rt", "/src",
                                inst.band, inst=inst)


def _kiss_port(svc, inst):
    from lhpc.core import commands
    kiss = svc.stack("kiss").component("loraham-kiss-tnc")
    argv = commands.expand_argv(kiss.run_argv, kiss, svc.stack_config("loraham-kiss-tnc", inst.band, inst),
                                svc.config().operator, "/rt", "/src", inst.band, inst=inst)
    return argv[argv.index("--kiss-port") + 1]


@pytest.mark.parametrize("saved,want", [(None, "8134"), ("9000", "9133")])
def test_k6s2_graywolfs_default_tnc_port_is_kiss_at_its_radios_effective_port(tmp_path, saved, want):
    # K6S-2 / G1-K7G (576c2f80): {port_of:kiss:8001} is the port kiss@r2 LISTENS on: its saved base + 133 (r2), else
    # the manifest's 8001 + 133.
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    r2 = Instance("r2", band="868")
    if saved:
        assert svc.save_config_bundle("kiss", values={"kiss_port": saved}, inst=r2).ok
    assert _kiss_port(svc, r2) == want
    assert _graywolf_tnc(svc, r2) == ["--tnc-host", "127.0.0.1", "--tnc-port", want]


def test_k6s2_an_explicit_graywolf_tnc_port_wins(tmp_path):
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    r2 = Instance("r2", band="868")
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000"}, inst=r2).ok
    assert svc.save_config_bundle("graywolf", values={"tnc_port": "9555"}, inst=r2).ok
    assert _graywolf_tnc(svc, r2)[-1] == "9555"

"""G2-0.14.0-COMB findings owned by core lane 1: 2 (a Settings radio for a stack without bands), 4 (a saved port value
remapped through the bases again) and NOTE 11 (Home probes an instance at its default ports after a saved port change).
2 and 4 are not reachable through the shipped manifest (measured on 01d3d09e: Graywolf declares its bands; no run order
holds two saved-port bases); their pins close the latent gaps. NOTE 11 is driven through Home's entry builder."""
import types

import radios_fixture

from lhpc.core import commands, instances, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

_BANDLESS = ('[[stack]]\nid = "s"\nname = "s"\nmain = "m"\n'
             '[[stack.component]]\nid = "m"\nname = "m"\nkind = "service"\nrun_argv = ["sleep", "60"]\n'
             'readiness = "process"\n')


def test_a_stack_without_bands_shows_and_carries_the_radio_it_shows(tmp_path):
    # finding 2, latent (red on 01d3d09e: radio "" for r2, the Save would have named no radio): Settings eligibility is
    # radios_of's, the one rule a start uses (a stack without bands runs on every enabled radio)
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    (tmp_path / "m.toml").write_text(_BANDLESS)
    svc = ControllerService(manifest_path=tmp_path / "m.toml", system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    r2 = instances.copy_of(svc, "s", "r2")[0]
    choice = instances.settings_choice(svc, "s", "", r2)
    assert choice["radio"] == "r2"
    assert [c["radio"] for c in choice["radio_choices"]] == ["r1", "r2"]
    assert instances.can_run_on(svc, svc.stack("s")) == ["r1", "r2"]


def test_a_saved_port_value_is_derived_for_its_radio_once(tmp_path):
    # finding 4, latent (red on 01d3d09e: 9133): a saved value is already the base (r1's) port; rN derives from it, never
    # through another base's mapping (8001 saved as 8080 while a second base 8080 is saved as 9000)
    inst = commands.Instance("r2", bases={8001: 8080, 8080: 9000})
    p = types.SimpleNamespace(name="kiss_port", default="{port:8001}", default_template="{port:8001}")
    assert commands._param_raw(p, {"kiss_port": "8080"}, inst) == "8213"
    assert inst.port(8001) == 8213                       # the endpoint of the same base: the same port


def test_home_judges_an_instance_at_its_saved_port(tmp_path):
    # NOTE 11 (red on 01d3d09e: "listener absent: 127.0.0.1:8134"): kiss@r2 saved at 9000 listens on 9133 (+ 133)
    rows = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"))
    radios_fixture.write(tmp_path, rows)
    fake = FakeSystem(cmdlines_data={4200: radios_fixture.kiss_on("r2")}, listeners=[Listener("ipv4", "127.0.0.1", 9133, 1)])
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000"}, inst=instances.copy_of(svc, "kiss", "r2")[0]).ok
    live = {c: cs for ss in svc.build_snapshot().stacks for c, cs in ss.components.items()}
    (entry,) = radio_start.running_instances(svc, "433", live)
    (tnc,) = [r for r in entry["components"] if r["id"] == "loraham-kiss-tnc"]
    assert (entry["id"], tnc["state"]) == ("kiss@r2", "running"), tnc

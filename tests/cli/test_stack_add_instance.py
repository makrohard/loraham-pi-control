"""Item 7c (DEC-W15): `lhpc stack add-instance <stack> <rid>` lists `<stack>@<rid>` at the next free slot of
config/instances.toml (append-only), and `remove-instance` keeps its slot reserved, so no other instance's ports
move. Only the T2.2 stacks may have one (kiss, meshcore, chat); a refusal writes nothing. The CLI runs on a
FakeSystem service over a temporary runtime root."""
import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "wave", "waveshare-sx1262", "433"),
        radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"),
        radios.Radio("r4", "off", "meshstick-1262", "868", usb_serial="MSTICK02", enabled=False))


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    radios_fixture.write(tmp_path, ROWS)
    assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID          # the fixture list itself holds
    real = ControllerService
    monkeypatch.setattr(cli, "ControllerService",
                        lambda *a, **k: real(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))

    def go(*argv):
        rc = cli.main(["stack", *argv])
        return rc, capsys.readouterr().out
    return go


def _listed(tmp_path):
    p = tmp_path / "config" / "instances.toml"
    return [ln for ln in p.read_text().splitlines() if not ln.startswith("#")] if p.exists() else None


def test_an_instance_is_listed_at_the_next_free_slot(run, tmp_path):
    rc, out = run("add-instance", "kiss", "r2")
    assert rc == 0 and "kiss@r2 added: slot 1 on 433 MHz" in out, out
    assert _listed(tmp_path) == ['kiss = ["r2"]']


def test_a_removed_instance_keeps_its_slot_reserved_and_a_new_one_appends(run, tmp_path):
    assert run("add-instance", "kiss", "r2")[0] == 0
    rc, out = run("remove-instance", "kiss", "r2")
    assert rc == 0 and "kiss@r2 removed: slot 1 stays reserved" in out, out
    assert _listed(tmp_path) == ['kiss = [""]']
    rc, out = run("add-instance", "kiss", "r2")
    assert rc == 0 and "kiss@r2 added: slot 2 on 433 MHz" in out, out
    assert _listed(tmp_path) == ['kiss = ["", "r2"]']


@pytest.mark.parametrize("argv,why", [
    (("voice", "r2"), "Cannot add an instance of voice: voice has one instance per box"),
    (("meshcom", "r2"), "Cannot add an instance of meshcom: meshcom has one instance per box in 0.14.0"),
    (("reticulum", "r2"), "Cannot add an instance of reticulum: Reticulum runs one rnsd per box"),
    (("graywolf", "r2"), "Cannot add an instance of graywolf: graywolf has one instance in 0.14.0"),
    (("nosuch", "r2"), "unknown stack 'nosuch'"),
    (("kiss", "r9"), "Cannot add kiss@r9: r9 is not an enabled radio of the hardware list"),
    (("kiss", "r4"), "Cannot add kiss@r4: r4 is disabled"),
    (("meshcore", "r2"), "Cannot add meshcore@r2: meshcore runs on 868 MHz, r2 is on 433 MHz"),
], ids=["voice", "meshcom", "reticulum", "graywolf", "unknown-stack", "unknown-rid", "disabled", "band"])
def test_a_refused_instance_names_why_and_writes_nothing(run, tmp_path, argv, why):
    rc, out = run("add-instance", *argv)
    assert rc != 0 and why in out, out
    assert _listed(tmp_path) is None


def test_any_enabled_radio_of_the_stacks_bands_may_hold_one(run, tmp_path):
    # Slot 0's radio is decided at its start (DEC-W16), so the band's first radio can be listed too.
    rc, out = run("add-instance", "kiss", "r3")                    # kiss switches bands: r3 is the first 868 radio
    assert rc == 0 and "kiss@r3 added: slot 1 on 868 MHz" in out, out


def test_a_listed_instance_is_not_listed_twice(run, tmp_path):
    assert run("add-instance", "kiss", "r2")[0] == 0
    rc, out = run("add-instance", "kiss", "r2")
    assert rc != 0 and "kiss@r2 exists already (slot 1)" in out, out
    assert _listed(tmp_path) == ['kiss = ["r2"]']


def test_a_registry_that_does_not_read_back_refuses_and_stays_as_it_is(run, tmp_path):
    p = tmp_path / "config" / "instances.toml"
    p.write_text('kiss = ["r0"]\n')
    rc, out = run("add-instance", "kiss", "r2")
    assert rc != 0 and "instances.toml does not read back: kiss: 'r0' is not r1..r99" in out, out
    assert p.read_text() == 'kiss = ["r0"]\n'


def test_eight_instances_is_the_limit(run, tmp_path):
    p = tmp_path / "config" / "instances.toml"
    p.write_text('kiss = ["r10", "r11", "r12", "r13", "r14", "r15", "r16"]\n')
    rc, out = run("add-instance", "kiss", "r2")
    assert rc != 0 and "Cannot add kiss@r2: kiss has 7 instances beyond its own" in out, out


def test_removing_an_instance_that_is_not_listed_is_refused(run, tmp_path):
    rc, out = run("remove-instance", "kiss", "r2")
    assert rc != 0 and "kiss@r2 is not a listed instance" in out, out
    assert _listed(tmp_path) is None


def test_a_removed_instance_names_the_state_it_keeps(run, tmp_path):
    # 7d (DEC-W51): a removal deletes nothing; purge takes only listed instances' folders, so the kept state and
    # secrets of meshcore@r3 are named (only what is on disk), never left there silently.
    assert run("add-instance", "meshcore", "r3")[0] == 0
    for rel in ("state/r3/meshcore/companion.db", "state/r3/openhop/repeater.db",
                "config/secrets/r3/meshcore_identity.key"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    rc, out = run("remove-instance", "meshcore", "r3")
    assert rc == 0, out
    kept = sorted(ln.split("[kept] ")[1].split(" ")[0] for ln in out.splitlines() if "[kept] " in ln)
    assert kept == ["config/secrets/r3/meshcore_identity.key", "state/r3/meshcore", "state/r3/openhop"], out
    assert (tmp_path / "state/r3/meshcore/companion.db").exists()

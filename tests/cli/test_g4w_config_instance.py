"""G4-web (DEC-W384cz/da): `lhpc config <stack>` without an instance never picks among several radios by band. With
several radios able to run the stack it is refused by name with the instances (W323); with exactly one it is that
instance (exact, not a fallback). Through the CLI's own entry; the saved files are read back."""
import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths

TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
ONE_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),)


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))         # the CLI's own service reads this root
    return tmp_path


def _stacks(tmp_path):
    d = tmp_path / "config" / "stacks"
    return {p.name: p.read_bytes() for p in d.glob("*.toml")} if d.exists() else {}


@pytest.mark.parametrize("argv", [["config", "kiss"], ["config", "kiss", "list"], ["config", "kiss", "rx_only", "on"],
                                  ["config", "kiss", "--reset", "--yes"]])
def test_a_bare_config_with_several_radios_is_refused_by_name_and_changes_nothing(box, capsys, argv):
    radios_fixture.write(box, TWO_433)
    before = _stacks(box)
    assert cli.main(argv) == 2
    out = capsys.readouterr().out
    assert "kiss can run on r1 or r3: use kiss@r1 or kiss@r3" in out and "lhpc config kiss@r3" in out
    assert _stacks(box) == before


def test_a_bare_config_with_one_radio_is_that_instance(box, capsys):
    radios_fixture.write(box, ONE_433)
    assert cli.main(["config", "kiss", "rx_only", "on"]) == 0
    paths = Paths(runtime_root=box)
    assert cfgmod.load_stack_config(paths, "kiss", inst=Instance("r1")).get("rx_only") == "on"
    assert cli.main(["config", "kiss"]) == 0 and "OK    kiss@r1 settings:" in capsys.readouterr().out

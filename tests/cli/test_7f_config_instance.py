"""Item 7f, DEC-W60 on the CLI: `lhpc config <stack>@<rid> [<param> [<value>]]` lists, shows, sets and resets the
listed instance's own settings in config/stacks/<stack>@<rid>.toml, never slot 0's; its daemon parameters are its
radio's, so that form is refused; a target that is no listed instance, or a --band other than its radio's, is
refused and writes nothing."""
import tomllib

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    radios_fixture.write(tmp_path, ROWS)
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_call": "DL2XYZ"})          # slot 0's
    real = ControllerService
    monkeypatch.setattr(cli, "ControllerService",
                        lambda *a, **k: real(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))

    def go(*argv):
        rc = cli.main(["config", *argv])
        return rc, capsys.readouterr().out
    return go


def _stored(tmp_path, name):
    p = tmp_path / "config" / "stacks" / name
    return tomllib.loads(p.read_text()) if p.exists() else {}


def test_a_set_writes_the_instances_own_file_and_a_show_reads_it(run, tmp_path):
    rc, out = run("chat@r3", "tx_freq", "433.900")
    assert rc == 0, out
    assert _stored(tmp_path, "chat@r3.toml").get("file_tx_freq") == "433.900"
    assert "file_tx_freq" not in _stored(tmp_path, "chat.toml")
    rc, out = run("chat@r3", "tx_freq")
    assert rc == 0 and "value   = 433.900" in out and "lhpc config chat@r3 tx_freq <value>" in out, out
    rc, out = run("chat", "tx_freq")
    assert "value   = 433.900" not in out                                        # slot 0's is not the instance's


def test_a_reset_clears_the_instances_own_file_alone(run, tmp_path):
    run("chat@r3", "tx_freq", "433.900")
    rc, out = run("chat@r3", "--reset", "--yes")
    assert rc == 0, out
    assert "file_tx_freq" not in _stored(tmp_path, "chat@r3.toml")
    assert _stored(tmp_path, "chat.toml").get("file_call") == "DL2XYZ"


@pytest.mark.parametrize("argv,rc", [(("chat@r9", "tx_freq", "433.900"), 1), (("chat@r3", "tx_freq", "433.900", "--band", "868"), 2),
                                     (("chat@r3", "--daemon-param", "SF=9"), 2)])
def test_a_target_that_is_no_listed_instance_or_a_daemon_param_is_refused_and_writes_nothing(run, tmp_path, argv, rc):
    before = sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir())
    got, out = run(*argv)
    assert got == rc and out[:3] == "ERR", out
    assert sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir()) == before

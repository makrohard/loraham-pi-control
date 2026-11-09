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
                                     (("chat", "--daemon-param", "SF=9"), 2)])    # K8: a bare stack names no instance
def test_a_target_that_is_no_listed_instance_or_a_daemon_param_is_refused_and_writes_nothing(run, tmp_path, argv, rc):
    before = sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir())
    got, out = run(*argv)
    assert got == rc and out[:3] == "ERR", out
    assert sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir()) == before


def _row_dp(tmp_path):
    return {r.id: dict(r.dp) for r in radios.load(Paths(runtime_root=tmp_path)).radios}


@pytest.mark.parametrize("target", ["chat@r3", "daemon@r3"])
def test_k8e_an_instances_daemon_params_are_its_radios_own_settings(run, tmp_path, target):
    # DEC-W53, DEC-W384ak: chat@r3 and daemon@r3 write the same store, r3's row of the hardware list; r1 (also on 433)
    # keeps its own. Before, daemon@r3 saved per band ("daemon (433)") and chat@r3 was refused while two radios shared 433
    rc, out = run(target, "--daemon-param", "SF=9")
    via = " (via chat@r3)" if target.startswith("chat") else ""
    assert (rc, out.splitlines()[0]) == (0, f"OK    saved the daemon settings of r3{via}"), out
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {"SF": "9"}}
    rc, out = run(target, "--reset-daemon")
    assert (rc, out.splitlines()[0]) == (0, f"OK    saved the daemon settings of r3{via}"), out
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {}}


@pytest.mark.parametrize("target", ["chat", "chat@r1"])
def test_g1_k8a_2_with_one_radio_able_to_run_it_the_daemon_params_save_as_before(run, tmp_path, target):
    # W290 (handler): one radio of chat's band, the bare form addresses it, as before K8; so does its instance
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios_fixture.R3))
    rc, out = run(target, "--daemon-param", "SF=9")
    assert rc == 0 and out.splitlines()[0] == "OK    saved the daemon settings of r1 (via chat@r1)", out
    assert _row_dp(tmp_path)["r1"] == {"SF": "9"}


def test_g1_k8a_re_b_a_disabled_radios_instance_never_reaches_the_daemon_params(run, tmp_path):
    # measured: the resolver refuses chat@r2 on a disabled r2 first, so the band's daemon parameters stay unchanged
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1", enabled=False)))
    before = sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir())
    rc, out = run("chat@r2", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (1, "ERR   chat@r2: r2 is disabled"), out
    assert sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir()) == before


@pytest.mark.parametrize("target", ["chat@r3", "daemon@r3"])
def test_k8e_an_instances_apply_reaches_its_radios_daemon(run, target):
    # --apply-daemon goes to r3's own daemon: while it is down, the refusal names it (before: the band's two radios)
    rc, out = run(target, "--apply-daemon")
    via = " (via chat@r3)" if target.startswith("chat") else ""
    assert (rc, out.splitlines()[0]) == (1, "ERR   the daemon of r3 is not running — start it first: lhpc stack start "
                                            f"daemon@r3{via}"), out

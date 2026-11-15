"""Item 7f, DEC-W60 on the CLI: `lhpc config <stack>@<rid> [<param> [<value>]]` lists, shows, sets and resets the
listed instance's own settings in config/stacks/<stack>@<rid>.toml, never the global <stack>.toml's; its daemon parameters are its own
set there too (DEC-W384az; daemon@<rid>'s are the radio's row); a target that is no listed instance, or a --band other than its radio's, is
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
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_call": "DL2XYZ"})          # the global chat.toml's
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
    assert "value   = 433.900" not in out                                        # the global file's is not the instance's


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


def test_k8e_daemon_at_rid_writes_its_radios_own_row(run, tmp_path):
    # DEC-W53: daemon@r3 writes r3's row of the hardware list; r1 (also on 433) keeps its own. Before, daemon@r3 saved
    # per band ("daemon (433)")
    rc, out = run("daemon@r3", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (0, "OK    saved the daemon settings of r3"), out
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {"SF": "9"}}
    rc, out = run("daemon@r3", "--reset-daemon")
    assert (rc, out.splitlines()[0]) == (0, "OK    saved the daemon settings of r3"), out
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {}}


def test_w384az_an_app_instances_daemon_params_are_its_own_set(run, tmp_path):
    # DEC-W384az (supersedes DEC-W384ak (a)): chat@r3 writes its own set in chat@r3.toml, never r3's row (red on
    # 8690b049: "saved the daemon settings of r3 (via chat@r3)" into the row); chat@r1 and the rows keep theirs
    rc, out = run("chat@r3", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (0, "OK    saved the daemon settings of chat@r3"), out
    assert _stored(tmp_path, "chat@r3.toml").get("dp_SF") == "9" and "dp_SF" not in _stored(tmp_path, "chat@r1.toml")
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {}}
    rc, out = run("chat@r3", "--reset-daemon")
    assert (rc, out.splitlines()[0]) == (0, "OK    saved the daemon settings of chat@r3"), out
    assert "dp_SF" not in _stored(tmp_path, "chat@r3.toml")


@pytest.mark.parametrize("target", ["chat", "chat@r1"])
def test_g1_k8a_2_with_one_radio_able_to_run_it_the_daemon_params_save_as_before(run, tmp_path, target):
    # W290 (handler): one radio of chat's band, the bare form addresses it, as before K8; so does its instance
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios_fixture.R3))
    rc, out = run(target, "--daemon-param", "SF=9")
    assert rc == 0 and out.splitlines()[0] == "OK    saved the daemon settings of chat@r1", out
    assert _stored(tmp_path, "chat@r1.toml").get("dp_SF") == "9" and _row_dp(tmp_path)["r1"] == {}


def test_g1_k8a_re_b_a_disabled_radios_instance_never_reaches_the_daemon_params(run, tmp_path):
    # measured: the resolver refuses chat@r2 on a disabled r2 first, so the band's daemon parameters stay unchanged
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1", enabled=False)))
    before = sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir())
    rc, out = run("chat@r2", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (1, "ERR   chat@r2: r2 is disabled"), out
    assert sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir()) == before


@pytest.mark.parametrize("target,why", [("chat@r3", "Apply live is only available while chat@r3 is running"),
                                        ("daemon@r3", "the daemon of r3 is not running — start it first: lhpc stack "
                                                      "start daemon@r3")])
def test_k8e_an_instances_apply_reaches_its_radios_daemon(run, target, why):
    # --apply-daemon goes to r3's own daemon: while it is down, the refusal names it (before: the band's two radios);
    # an app instance's only while it runs there
    rc, out = run(target, "--apply-daemon")
    assert (rc, out.splitlines()[0]) == (1, f"ERR   {why}"), out


@pytest.mark.parametrize("argv", [("daemon", "--daemon-param", "SF=9"), ("daemon", "--reset-daemon"),
                                  ("daemon", "--apply-daemon")])
def test_g1_k8e_1_the_bare_daemon_form_names_the_radios_when_several_run_it(run, tmp_path, argv):
    # r1 and r3 both on 433: the bare daemon form refuses by name, never the band's store (before: "saved daemon
    # params for daemon (433)")
    before = (tmp_path / "config" / "radios.toml").read_text()
    rc, out = run(*argv)
    assert (rc, out.splitlines()[0]) == (2, "ERR   daemon runs on r1, r3: name the instance, lhpc config "
                                            "daemon@<rid> --daemon-param …"), out
    assert (tmp_path / "config" / "radios.toml").read_text() == before
    assert not (tmp_path / "config" / "stacks" / "daemon.toml").exists()


def test_g1_k8e_1_the_bare_daemon_form_with_one_radio_writes_its_row(run, tmp_path):
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),))
    rc, out = run("daemon", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (0, "OK    saved the daemon settings of r1"), out
    assert _row_dp(tmp_path) == {"r1": {"SF": "9"}}


def test_g1_k8e_2_a_radio_the_resolver_refuses_is_refused_by_its_reason(run, tmp_path, monkeypatch):
    # the one able radio leaves the list between the count and the resolve: its reason, never the band's store
    from lhpc.core import instances
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),))
    monkeypatch.setattr(instances, "copy_of", lambda svc, sid, rid="", band="", run=None:
                        (None, f"{sid}@{rid}: {rid} is not in the hardware list"))
    rc, out = run("chat", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (1, "ERR   chat@r1: r1 is not in the hardware list"), out
    assert _row_dp(tmp_path) == {"r1": {}} and not (tmp_path / "config" / "stacks" / "chat.toml").read_text().count("dp_")


@pytest.mark.parametrize("target", ["graywolf@r1", "graywolf"])
def test_g1_k8e_a_stack_without_daemon_parameters_is_refused(run, tmp_path, target):
    rc, out = run(target, "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (1, "ERR   graywolf has no configurable daemon parameters"), out
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {}}


def test_w384az_an_old_band_value_is_read_nowhere_and_a_reset_returns_to_the_default(run, tmp_path):
    # DEC-W384az, W269 (CHANGELOG: move it by hand): a 0.13 per-band value (chat's dp_433_SF) is no store any more:
    # not r3's row, not chat@r3's; a reset of chat@r3's set returns to the default (was: to the old band value)
    from lhpc.core import daemon_params, radio_start
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"dp_433_SF": "7"})
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    default = daemon_params.default_value("chat", "433", "SF")
    assert default != "7" and radio_start.row_dp(svc, "r3") == {} and svc._daemon_param_applies("chat", "r3")["SF"] == default
    assert run("chat@r3", "--daemon-param", "SF=9")[0] == 0 and svc._daemon_param_applies("chat", "r3")["SF"] == "9"
    assert run("chat@r3", "--reset-daemon")[0] == 0 and svc._daemon_param_applies("chat", "r3")["SF"] == default


def test_g1_k8e_4_an_apply_reaches_r3s_running_daemon_only(tmp_path, monkeypatch, capsys):
    # r1 and r3 both on 433 and both running: daemon@r3's apply sends its SETs to r3's socket alone
    ready = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r3")}
    radios_fixture.write(tmp_path, ROWS)
    fake = FakeSystem(unix_replies=ready)
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: ControllerService(system=fake.system,
                                                                                    paths=Paths(runtime_root=tmp_path)))
    assert cli.main(["config", "daemon@r3", "--daemon-param", "SF=9"]) == 0
    fake.sent.clear()
    cli.main(["config", "daemon@r3", "--apply-daemon"])
    assert {p for p, _b in fake.sent} == {"/tmp/loraconf-r3.sock"} and (("/tmp/loraconf-r3.sock", b"SET SF=9\n") in fake.sent)

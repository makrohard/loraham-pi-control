"""Item 16 batch 2: every start that runs on a radio writes its start log under that radio,
`start-<comp>-<band>-<rid>.log` (radio_start.start_log_name), a band's only radio too; the readers use the same
name. Two radios of one band no longer share one log. A box upgraded from 0.13.x keeps its old `start-<comp>-<band>.log`
until the next start: the reader returns it without error, and the new radio's log once it exists."""
import radios_fixture

from lhpc.core import commands, radios
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import Component, ComponentKind, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"))
COMP = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))


def _life(tmp_path, monkeypatch, logs):
    def spawn(argv, log, cwd=None, env=None):
        logs.append(log.name)
        return 4321
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system,
                     spawn=spawn)
    life.OBSERVE_TIMEOUT_S = 0.0
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: True)
    return life


def test_two_radios_of_one_band_write_two_start_logs(tmp_path, monkeypatch):
    logs = []
    life = _life(tmp_path, monkeypatch, logs)
    for rid in ("r1", "r2"):
        assert life.start(Stack(id="s", name="s", main="c"), COMP, band="433", inst=commands.Instance(rid)).ok
    assert logs == ["start-c-433-r1.log", "start-c-433-r2.log"]


def test_a_start_without_a_radio_keeps_its_name(tmp_path, monkeypatch):
    logs = []
    life = _life(tmp_path, monkeypatch, logs)
    assert life.start(Stack(id="s", name="s", main="c"), COMP, band="433").ok
    assert life.start(Stack(id="s", name="s", main="c"), COMP).ok
    assert logs == ["start-c-433.log", "start-c.log"]


def test_the_reader_takes_the_radios_log_and_an_upgraded_boxs_old_log_until_then(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, TWO_433)
    life = _life(tmp_path, monkeypatch, [])
    d = tmp_path / "logs"
    d.mkdir()
    (d / "start-c-433.log").write_text("0.13.x\n")                     # the upgraded box's last start
    assert life.start_log(COMP, "433").name == "start-c-433.log"        # no error, the old one until a start
    (d / "start-c-433-r1.log").write_text("r1\n")
    (d / "start-c-433-r2.log").write_text("r2\n")
    assert life.start_log(COMP, "433").name == "start-c-433-r1.log"     # the band's first radio
    assert life.start_log(COMP, "433", "r2").name == "start-c-433-r2.log"


def test_each_radios_rf_feed_reads_its_own_daemons_log(tmp_path):
    radios_fixture.write(tmp_path, TWO_433)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    d = tmp_path / "logs"
    d.mkdir()
    (d / "start-loraham-daemon-433-r1.log").write_text("[TX] from r1\n")
    (d / "start-loraham-daemon-433-r2.log").write_text("[RX] at r2\n")
    assert svc.daemon_feed("433", rid="r2") == ["[RX] at r2"]
    assert svc.daemon_feed("433") == ["[TX] from r1"]                    # the band's first radio
    svc.clear_daemon_feed("433")                                         # each radio's window
    assert svc.daemon_feed("433", rid="r1") == [] and svc.daemon_feed("433", rid="r2") == []

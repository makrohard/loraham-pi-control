"""Item 7c2a (DEC-W15/W16/W31): `lhpc stack start <stack>@<rid>` starts a listed instance on its radio's band, and
slot 0 runs on the first enabled radio of its band not listed for the stack.

An instance is told apart by its process spec's instance token (kiss-tnc: `--data-socket` = its radio's socket) and
by its ownership records' rid, the matcher kill.py uses. The CLI runs on a FakeSystem service over a temporary
runtime root: the daemon's CONF socket answers READY, the launch is recorded (Lifecycle.start), and a component's
readiness port is a declared listener."""
import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, radio_start, radios
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

R1, R2 = radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "wave", "waveshare-sx1262", "433")
R3 = radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01")
READY = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r3")}


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    """box(rows, listed, procs, ports) -> run(*argv) -> (rc, out, launches)."""
    def make(rows, listed, procs=None, ports=()):
        radios_fixture.write(tmp_path, rows)
        assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID
        if listed:
            (tmp_path / "config" / "instances.toml").write_text(listed)
        for name, value in (("hardware_configured", lambda self: True), ("active_bands", lambda self: ["433", "868"]),
                            ("binary_behind", lambda self, c: ""), ("is_built", lambda self, c: True),
                            ("_source_present", lambda self, c: True), ("_verify_band_up", lambda self, b: True)):
            monkeypatch.setattr(ControllerService, name, value)
        monkeypatch.setattr(radio_start, "verify_identity", lambda svc, rid: "")
        launches = []

        def launch(self, stack, comp, params=None, band="", **kw):
            inst = kw.get("inst") or commands.SLOT0
            launches.append((comp.id, band, inst.slot, inst.rid))
            return StartLaunch(True, "")
        monkeypatch.setattr(Lifecycle, "start", launch)
        system = FakeSystem(unix_replies=READY, cmdlines_data=procs or {},
                            listeners=[Listener(family="ipv4", ip="127.0.0.1", port=p, inode=i)
                                       for i, p in enumerate(ports, 1)]).system
        real = ControllerService
        monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: real(system=system, paths=Paths(runtime_root=tmp_path)))

        def run(*argv):
            rc = cli.main(["stack", *argv, "--yes"])
            return rc, capsys.readouterr().out, launches
        return run
    return make


def _kiss_on(rid):                                     # a kiss-tnc process of radio `rid`, by its instance token
    return ["loraham-kiss-tnc", "--data-socket", f"/tmp/loraf-{rid}.sock"]


def test_a_listed_instance_starts_on_its_radio_as_its_slot(box):
    rc, out, launches = box((R1, R3), 'kiss = ["r3"]\n', ports=(8011,))("start", "kiss@r3")
    assert ("loraham-kiss-tnc", "868", 1, "r3") in launches, (launches, out)
    assert rc == 0, out


def test_an_instance_that_runs_is_already_healthy_and_nothing_launches(box):
    run = box((R1, R3), 'kiss = ["r3"]\n', procs={4100: _kiss_on("r3")}, ports=(8011,))
    rc, out, launches = run("start", "kiss@r3")
    assert rc == 0 and "already healthy" in out and launches == [], (out, launches)


def test_slot_0_is_not_healthy_because_another_instance_runs(box):
    # Only kiss@r3 runs; the plain start is slot 0 on r1 (433) and launches, never reads r3's process as its own.
    run = box((R1, R3), 'kiss = ["r3"]\n', procs={4100: _kiss_on("r3")}, ports=(8001,))
    rc, out, launches = run("start", "kiss", "--band", "433")
    assert ("loraham-kiss-tnc", "433", 0, "r1") in launches, (launches, out)


def test_slot_0_takes_the_first_unlisted_radio_and_names_its_choice(box):
    box((R1, R2, R3), 'kiss = ["r1"]\n')
    svc = cli.ControllerService()
    comp = next(c for c in svc.stack("kiss").components if c.id == "loraham-kiss-tnc")
    out = []
    inst, why = radio_start.client_instance(svc, comp, "433", None, out)
    assert (inst.slot, inst.rid, why) == (0, "r2", "")              # r1 holds kiss@r1, so slot 0 is r2
    assert out == ["  [radio] kiss starts on r2 (433 MHz); kiss@r1 is a separate instance"]


def test_slot_0_is_refused_when_every_radio_of_its_band_is_listed(box):
    box((R1, R2, R3), 'kiss = ["r1", "r2"]\n')
    svc = cli.ControllerService()
    comp = next(c for c in svc.stack("kiss").components if c.id == "loraham-kiss-tnc")
    assert radio_start.client_instance(svc, comp, "433") == (
        None, "no radio for its daemon sockets: every enabled radio of 433 MHz holds a listed instance of kiss (r1, r2): "
              "start one of them as kiss@<rid>")


@pytest.mark.parametrize("argv,why", [
    (("start", "kiss@r9"), "kiss@r9 is not a listed instance (lhpc stack add-instance kiss r9)"),
    (("stop", "kiss@r3"), "lhpc stack stop kiss@r3: an instance is started only, in this build"),
])
def test_the_cli_refuses_an_unlisted_or_not_yet_supported_instance(box, capsys, argv, why):
    box((R1, R3), 'kiss = ["r3"]\n')
    rc = cli.main(["stack", *argv, "--yes"])
    assert rc == 1 and why in capsys.readouterr().err


def test_the_plan_names_the_instance(box):
    rc, out, _launches = box((R1, R3), 'kiss = ["r3"]\n', ports=(8011,))("start", "kiss@r3")
    assert "Run plan for 'kiss@r3': 2 component(s) in order." in out, out


def test_an_instances_claims_are_its_own_and_slot_0_keeps_its_keys(box):
    # Item 7a: the start's conflict check keys the target's claims by its instance (slot 1: BASE + 10, the kiss-serial
    # key with its radio's suffix); slot 0's keys are the manifest's, unchanged.
    from lhpc.core import instances
    box((R1, R3), 'kiss = ["r3"]\n')
    stack = cli.ControllerService().stack("kiss")
    claims = [r for c in stack.components for r in c.resources if not r.key.startswith("loraham.")]
    assert [instances.claim_key(r, commands.Instance(1, "r3")) for r in claims] == ["tcp.port.8011",
                                                                                    "serial.loraham-kiss-r3"]
    assert [instances.claim_key(r, commands.SLOT0) for r in claims] == ["tcp.port.8001", "serial.loraham-kiss"]


def test_an_instance_start_leaves_slot_0s_band_marker_alone(box, tmp_path):
    run = box((R1, R3), 'kiss = ["r3"]\n', ports=(8011,))
    assert run("start", "kiss@r3")[0] == 0
    assert not (tmp_path / "state" / "running" / "kiss.band").exists() and cli.ControllerService().running_band("kiss", "") == ""


def test_an_instance_on_another_band_starts_while_slot_0_runs(box):
    # kiss is band-switchable: slot 0 running on 433 (r1) refuses a slot-0 start on 868, never kiss@r3's own start.
    run = box((R1, R3), 'kiss = ["r3"]\n', procs={4100: _kiss_on("r1")}, ports=(8001, 8011))
    svc = cli.ControllerService()
    assert svc._set_running_band("kiss", "433")
    assert svc.start("kiss", apply=False, band="868").ok is False              # slot 0: the one-band refusal
    rc, out, launches = run("start", "kiss@r3")
    assert ("loraham-kiss-tnc", "868", 1, "r3") in launches, (launches, out)

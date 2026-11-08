"""Item 7c2a (DEC-W15/W16/W31): `lhpc stack start <stack>@<rid>` starts a listed instance on its radio's band, and
slot 0 runs on the first enabled radio of its band not listed for the stack.

An instance is told apart by its process spec's instance token (kiss-tnc: `--data-socket` = its radio's socket) and
by its ownership records' rid, the matcher kill.py uses. The CLI runs on a FakeSystem service over a temporary
runtime root: the daemon's CONF socket answers READY, the launch is recorded (Lifecycle.start), and a component's
readiness port is a declared listener."""
import pytest
from radios_fixture import R1, R2, R3, kiss_on as _kiss_on

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, radio_start

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


def test_slot_0_that_runs_beside_a_listed_copy_is_already_healthy(box):
    # Slot 0 runs on r1 (its DEC-W16 radio) while kiss@r3 is listed: its start is the no-op, judged on slot 0's own
    # copy from copy_of (CLEANUP-DESIGN-v6: slot 0 is a real copy), never a re-derived radio.
    run = box((R1, R3), 'kiss = ["r3"]\n', procs={4100: _kiss_on("r1")}, ports=(8001,))
    rc, out, launches = run("start", "kiss", "--band", "433")
    assert rc == 0 and "already healthy" in out and launches == [], (out, launches)


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
    (("poststart", "kiss@r3"), "lhpc stack poststart kiss@r3: not for an instance in this build"),
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

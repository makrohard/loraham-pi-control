"""Gate 1 C7 (VERDICT-G1-C7, W267) on stack instances, through the public paths: the CLI on the FakeSystem box and
the start's run blockers. B1 add-instance on the radio slot 0 runs on; B2 an instance start keeps slot 0's stop
intent; B3 a disabled radio's instance can still be stopped and is not removed while it runs; B4 an instance is
"already healthy" only by slot 0's own health rule (readiness endpoints, DEGRADED kept); B5 another stack's
running instance holds its derived claim; the preview's next command names the instance."""
import dataclasses

from radios_fixture import R1, R3, kiss_on

from lhpc.adapters.cli import main as cli
from lhpc.core import manifest as manifest_mod
from lhpc.core.model import ResourceClaim, ResourceKind, ResourceMode, RunState

KISS = 'kiss = ["r3"]\n'


def test_b1_an_instance_is_not_added_on_the_radio_slot_0_runs_on(box, tmp_path, capsys):
    box((R1, R3), None, procs={4100: kiss_on("r1")})
    assert cli.ControllerService()._set_running_band("kiss", "433")
    rc = cli.main(["stack", "add-instance", "kiss", "r1"])
    out = capsys.readouterr().out
    assert rc == 1 and "Cannot add kiss@r1: kiss (slot 0) runs on r1 — stop it first" in out, out
    assert not (tmp_path / "config" / "instances.toml").exists()


def test_b2_an_instance_start_keeps_slot_0s_stop_intent(box, tmp_path):
    run = box((R1, R3), KISS, ports=(8011,))
    svc = cli.ControllerService()
    svc._write_stop_intent(["kiss"])                                    # the operator stopped slot 0
    intent = svc._stop_intent_path("kiss")
    assert intent.is_file()
    rc, out, launches = run("start", "kiss@r3")
    assert ("loraham-kiss-tnc", "868", 1, "r3") in launches, (launches, out)
    assert intent.is_file()                                             # slot 0's intent survives


def _disable_r3(tmp_path):
    import radios_fixture
    radios_fixture.write(tmp_path, (R1, dataclasses.replace(R3, enabled=False)))


def test_b3_a_disabled_radios_instance_is_stopped(box, tmp_path, capsys):
    box((R1, R3), KISS, procs={4200: kiss_on("r3")})
    _disable_r3(tmp_path)
    cli.main(["stack", "stop", "kiss@r3", "--yes"])
    got = capsys.readouterr()
    assert "is disabled" not in got.err and "Stop plan for 'kiss'" in got.out, (got.out, got.err)


def test_b3_a_disabled_radios_running_instance_is_not_removed(box, tmp_path, capsys):
    box((R1, R3), KISS, procs={4200: kiss_on("r3")})
    _disable_r3(tmp_path)
    rc = cli.main(["stack", "remove-instance", "kiss", "r3"])
    assert rc == 1 and "it is running — stop it first" in capsys.readouterr().out
    assert '"r3"' in (tmp_path / "config" / "instances.toml").read_text()


def test_b4_a_running_instance_without_its_readiness_port_is_not_already_healthy(box):
    # kiss@r3's process runs and r3's daemon is READY, but 127.0.0.1:8011 does not listen: the start runs.
    run = box((R1, R3), KISS, procs={4200: kiss_on("r3")})
    rc, out, launches = run("start", "kiss@r3")
    assert "already healthy" not in out, out


def test_b4_slot_0s_degraded_state_is_kept(box):
    from lhpc.core import instances
    box((R1, R3), KISS, procs={4100: kiss_on("r1")})
    svc = cli.ControllerService()
    comp = svc.stack("kiss").component("loraham-kiss-tnc")
    assert instances.start_state(svc, "kiss", comp, None, "433", RunState.DEGRADED) == RunState.DEGRADED


def test_b5_another_stacks_running_instance_holds_its_derived_claim(box, monkeypatch):
    # kiss@r3 runs and holds tcp.port.8011 (slot 1); a chat start that would claim tcp.port.8011 is blocked by it.
    real = manifest_mod.load_manifest

    def load(path=None):
        out = []
        for s in real(path):
            if s.id == "chat":
                comps = [dataclasses.replace(c, resources=(*c.resources, ResourceClaim(
                    kind=ResourceKind.TCP_PORT, key="tcp.port.8011", mode=ResourceMode.EXCLUSIVE)))
                    if c.id == s.main else c for c in s.components]
                s = dataclasses.replace(s, components=tuple(comps))
            out.append(s)
        return tuple(out)
    monkeypatch.setattr(manifest_mod, "load_manifest", load)
    box((R1, R3), KISS, procs={4200: kiss_on("r3")})
    svc = cli.ControllerService()
    blockers = svc.run_blockers("chat", "433")
    assert any(b["resource"].endswith("8011") and b["holder_stack"] == "kiss" for b in blockers), blockers


def test_the_instance_preview_names_the_instance(box, capsys):
    box((R1, R3), KISS)
    cli.main(["stack", "start", "kiss@r3"])                             # no --yes: the plan only
    out = capsys.readouterr().out
    assert "lhpc stack start kiss@r3 --yes" in out and "lhpc stack start kiss@r1 --band" not in out, out


def test_c17_a_running_instance_holds_the_secrets_backup_back(box, tmp_path):
    # VERDICT-G1-C17: a backup is refused while a stack with a state root runs, a listed instance included.
    box((R1, R3), 'meshcore = ["r3"]\n',
        procs={4300: ["python", "-m", "meshcore_host", f"{tmp_path}/config/files/r3/meshcore.toml"]})
    svc = cli.ControllerService()
    assert "meshcore" in svc._secrets_running(), svc._secrets_running()

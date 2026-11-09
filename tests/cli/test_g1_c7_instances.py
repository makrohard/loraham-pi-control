"""Gate 1 C7 (VERDICT-G1-C7, W267) on stack instances, through the public paths: the CLI on the FakeSystem box and
the start's run blockers. B2 an instance start keeps slot 0's stop intent; B3 a disabled radio's instance can still
be stopped (K4ac: B1 and B3's removal went with add-instance/remove-instance, DEC-W344); B4 an instance is
"already healthy" only by slot 0's own health rule (readiness endpoints, DEGRADED kept); B5 another stack's
running instance holds its derived claim; the preview's next command names the instance."""
import dataclasses

from radios_fixture import R1, R2_868, R3, kiss_on

from lhpc.adapters.cli import main as cli
from lhpc.core import manifest as manifest_mod
from lhpc.core.model import ResourceClaim, ResourceKind, ResourceMode, RunState

def test_b2_an_instance_start_keeps_slot_0s_stop_intent(box, tmp_path):
    run = box((R1, R2_868, R3), ports=(8267,))
    svc = cli.ControllerService()
    svc._write_stop_intent(["kiss"])                                    # the operator stopped slot 0
    intent = svc._stop_intent_path("kiss")
    assert intent.is_file()
    rc, out, launches = run("start", "kiss@r3")
    assert ("loraham-kiss-tnc", "868", "r3") in launches, (launches, out)
    assert intent.is_file()                                             # slot 0's intent survives


def _disable_r3(tmp_path):
    import radios_fixture
    radios_fixture.write(tmp_path, (R1, dataclasses.replace(R3, enabled=False)))


def test_b3_a_disabled_radios_instance_is_stopped(box, tmp_path, capsys):
    box((R1, R2_868, R3), procs={4200: kiss_on("r3")})
    _disable_r3(tmp_path)
    cli.main(["stack", "stop", "kiss@r3", "--yes"])
    got = capsys.readouterr()
    assert "is disabled" not in got.err and "Stop plan for 'kiss'" in got.out, (got.out, got.err)


def test_b4_a_running_instance_without_its_readiness_port_is_not_already_healthy(box):
    # kiss@r3's process runs and r3's daemon is READY, but 127.0.0.1:8267 does not listen: the start runs.
    run = box((R1, R2_868, R3), procs={4200: kiss_on("r3")})
    rc, out, launches = run("start", "kiss@r3")
    assert "already healthy" not in out, out


def test_b4_slot_0s_degraded_state_is_kept(box):
    from lhpc.core import instances
    box((R1, R2_868, R3), procs={4100: kiss_on("r1")})
    svc = cli.ControllerService()
    comp = svc.stack("kiss").component("loraham-kiss-tnc")
    assert instances.start_state(svc, "kiss", comp, None, "433", RunState.DEGRADED) == RunState.DEGRADED


def test_b5_another_stacks_running_instance_holds_its_derived_claim(box, monkeypatch):
    # kiss@r3 runs and holds tcp.port.8267 (r3, DEC-W348); a chat start that would claim it is blocked by it.
    real = manifest_mod.load_manifest

    def load(path=None):
        out = []
        for s in real(path):
            if s.id == "chat":
                comps = [dataclasses.replace(c, resources=(*c.resources, ResourceClaim(
                    kind=ResourceKind.TCP_PORT, key="tcp.port.8267", mode=ResourceMode.EXCLUSIVE)))
                    if c.id == s.main else c for c in s.components]
                s = dataclasses.replace(s, components=tuple(comps))
            out.append(s)
        return tuple(out)
    monkeypatch.setattr(manifest_mod, "load_manifest", load)
    box((R1, R2_868, R3), procs={4200: kiss_on("r3")})
    svc = cli.ControllerService()
    blockers = svc.run_blockers("chat", "433")
    assert any(b["resource"].endswith("8267") and b["holder_stack"] == "kiss" for b in blockers), blockers


def test_the_instance_preview_names_the_instance(box, capsys):
    box((R1, R2_868, R3))
    cli.main(["stack", "start", "kiss@r3"])                             # no --yes: the plan only
    out = capsys.readouterr().out
    assert "lhpc stack start kiss@r3 --yes" in out and "lhpc stack start kiss@r1 --band" not in out, out


def test_c17_a_running_instance_holds_the_secrets_backup_back(box, tmp_path):
    # VERDICT-G1-C17: a backup is refused while a stack with a state root runs, a listed instance included.
    box((R1, R2_868, R3),
        procs={4300: ["python", "-m", "meshcore_host", f"{tmp_path}/config/files/r3/meshcore.toml"]})
    svc = cli.ControllerService()
    assert "meshcore" in svc._secrets_running(), svc._secrets_running()

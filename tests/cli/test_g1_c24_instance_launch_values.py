"""Gate-1 C-24 B1 on 7f: the saved-launch-value check of a listed instance's start and restart judges the
instance's own saved settings (config/stacks/<stack>@<rid>.toml), never slot 0's: an invalid value of its own
refuses before anything launches or stops; slot 0's valid value does not let it through."""
from radios_fixture import R1, R3

from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths


def _box(box, tmp_path):
    run = box((R1, R3), 'kiss = ["r3"]\n', ports=(8001, 8002))
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "kiss", {"kiss_port": "not-a-port"}, band="r3")
    return run


def test_an_instances_invalid_saved_value_refuses_its_start(box, tmp_path):
    rc, out, launches = _box(box, tmp_path)("start", "kiss@r3")
    assert rc == 1 and "invalid saved configuration" in out and launches == [], out


def test_an_instances_invalid_saved_value_refuses_its_restart(box, tmp_path):
    rc, out, launches = _box(box, tmp_path)("restart", "kiss@r3")
    assert rc == 1 and "Cannot restart 'kiss': invalid saved configuration" in out and launches == [], out

def test_an_instance_that_inherits_the_callsign_launches_with_the_global_not_slot0s_call(box, tmp_path, monkeypatch):
    # the launch overlay of an inherited licensed identity is judged on the instance's own (blank) call: chat@r3
    # launches as the operator's callsign, never with slot 0's own call
    from lhpc.adapters.cli import main as cli
    import radios_fixture
    from radios_fixture import R2
    ready = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r2")}
    monkeypatch.setattr(radios_fixture, "INSTANCE_READY", ready)
    run = box((R1, R2), 'chat = ["r2"]\n', ports=())
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_call": "DL2XYZ"})          # slot 0's own
    assert cli.main(["config", "operator", "--callsign", "DL9OP"]) == 0
    rc, out, _launches = run("start", "chat@r2")
    conf = (tmp_path / "config" / "files" / "r2" / "lorachat.conf").read_text()
    assert "CALL=DL9OP" in conf and "DL2XYZ" not in conf, (out, conf)


def test_an_instances_invalid_saved_value_refuses_its_restart_plan(box, tmp_path, capsys):
    from lhpc.adapters.cli import main as cli
    _box(box, tmp_path)
    rc = cli.main(["stack", "restart", "kiss@r3"])                           # the plan: no --yes
    out = capsys.readouterr().out
    assert rc == 1 and "Cannot restart 'kiss': invalid saved configuration" in out, out

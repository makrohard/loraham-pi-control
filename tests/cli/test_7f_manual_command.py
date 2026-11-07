"""Item 7f: an interactive component's run command, the line the operator copies into a terminal, is a listed
instance's own: chat@r2's start plan names r2's daemon sockets, never slot 0's radio r1's."""
import radios_fixture
from radios_fixture import R1, R2

READY = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r2")}


def test_an_instances_start_plan_names_its_own_radios_sockets(box, monkeypatch, capsys):
    from lhpc.adapters.cli import main as cli
    monkeypatch.setattr(radios_fixture, "INSTANCE_READY", READY)
    box((R1, R2), 'chat = ["r2"]\n', ports=())
    assert cli.main(["config", "operator", "--callsign", "DL9OP"]) == 0
    capsys.readouterr()
    cli.main(["stack", "start", "chat@r2"])                                  # the plan: no --yes
    out = capsys.readouterr().out
    assert "--data-socket /tmp/lora-r2.sock --conf-socket /tmp/loraconf-r2.sock" in out, out
    assert "lora-r1.sock" not in out, out

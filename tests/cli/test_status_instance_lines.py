"""STATUS-DEAD-SOCKET: `lhpc status daemon` names each daemon instance with its own state (box H 11-15: daemon@r3 had
ended, its socket refused, and the output still read "running" for the one daemon row). The decision is owned by
tests/core/test_status_instance_liveness.py; this is the command's exit status and its lines."""
import radios_fixture
from radios_fixture import NO_PID, launch_record, live_records

from lhpc.adapters.cli import main as cli
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_status_names_the_ended_and_the_live_daemon_instance(tmp_path, monkeypatch, capsys):
    # red-before (59d00cab): rc 0, no `instance daemon@r3` line (the daemon row read "running")
    radios_fixture.write(tmp_path, radios_fixture.BOX_H)
    paths = Paths(runtime_root=tmp_path)
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", NO_PID + 7)       # its process is gone
    launch_record(tmp_path, "daemon", "loraham-daemon", "r4", "868", NO_PID + 8)       # it runs
    live_records(monkeypatch, [NO_PID + 8])
    system = FakeSystem(cmdlines_data={NO_PID + 8: ["loraham_daemon", "--radio", "868"]},
                        sockets={"/tmp/loraconf-r3.sock", "/tmp/loraconf-r4.sock"},
                        unix_replies={"/tmp/loraconf-r4.sock": b"STATUS RADIO=READY ID=r4 BOUND=- TXMODE=MANAGED\n"},
                        unix_errors={"/tmp/loraconf-r3.sock": "[Errno 111] Connection refused"}).system
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: ControllerService(system=system, paths=paths))
    assert cli.main(["status", "daemon"]) == 0
    lines = {ln.split()[1]: ln.split(": ", 1)[1] for ln in capsys.readouterr().out.splitlines()
             if ln.split()[:1] == ["instance"]}
    assert lines["daemon@r4:"] == "running"
    state, _, detail = lines["daemon@r3:"].partition(" — ")
    assert state == "not running" and "lhpc stack start daemon@r3" in detail

"""STATUS-DEAD-SOCKET: the console's stack card names each instance with its own state, as `lhpc status` does (the
decision: tests/core/test_status_instance_liveness.py). Box H 11-15: daemon@r3 had ended, its socket refused, and the
daemon's card read "running" from daemon@r4 alone."""
import radios_fixture
from radios_fixture import NO_PID, launch_record, live_records
from htmlq import parse

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_the_daemon_card_names_the_ended_and_the_live_instance(web, tmp_path, monkeypatch):
    # red-before (59d00cab): the card has no instance list (only "Run state: running")
    radios_fixture.write(tmp_path, radios_fixture.BOX_H)
    paths = Paths(runtime_root=tmp_path)
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", NO_PID + 7)       # its process is gone
    launch_record(tmp_path, "daemon", "loraham-daemon", "r4", "868", NO_PID + 8)       # it runs
    live_records(monkeypatch, [NO_PID + 8])
    system = FakeSystem(cmdlines_data={NO_PID + 8: ["loraham_daemon", "--radio", "868"]},
                        sockets={"/tmp/loraconf-r3.sock", "/tmp/loraconf-r4.sock"},
                        unix_replies={"/tmp/loraconf-r4.sock": b"STATUS RADIO=READY ID=r4 BOUND=- TXMODE=MANAGED\n"},
                        unix_errors={"/tmp/loraconf-r3.sock": "[Errno 111] Connection refused"}).system
    client = web(service_factory=lambda: ControllerService(system=system, paths=paths))
    r = client.get("/stacks/daemon/body")
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    panel = doc.by_id("stack-info-daemon--instances")
    assert panel is not None, "no instance list on the daemon card"
    states = {li["data-instance"]: doc.within(li).find("span", class_="badge")[0]["data-instance-state"]
              for li in doc.within(panel).find("li")}
    assert states == {"daemon@r3": "not running", "daemon@r4": "running"}

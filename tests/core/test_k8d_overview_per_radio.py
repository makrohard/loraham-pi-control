"""K8d item 3, DEC-W384bu/bv: radio_overview() is one entry per enabled radio of the hardware list (every entry has its
rid), each with its own daemon view, its own running/startable stacks and conflicts; a live daemon on a band without an
enabled radio is service.stray_daemons()'s, its card kept (W290)."""
import radios_fixture

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
READY = b"STATUS RADIO=READY ID=%s BOUND=- TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


def _svc(tmp_path, rows=TWO_433, replies=None, cmdlines=None):
    radios_fixture.write(tmp_path, rows)
    fake = FakeSystem(unix_replies=replies or {}, cmdlines_data=cmdlines or {})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def test_one_entry_per_enabled_radio_each_with_its_own_daemon_view(tmp_path):
    # r1 and r3 on 433, only r1's daemon up: two entries, r1 ready and r3 offline, each read on its own socket.
    # red on 8289f771: one 433 entry whose daemon dict is the band's (no socket for two radios: unreachable)
    svc = _svc(tmp_path, replies={"/tmp/loraconf-r1.sock": READY % b"r1"})
    got = [(e["rid"], e["name"], e["band"], e["daemon"]["ready"], e["daemon"]["state_label"]) for e in svc.radio_overview()]
    assert got == [("r1", "a", "433", True, "usable"), ("r3", "b", "433", False, "offline")]
    assert all(e["rid"] for e in svc.radio_overview()) and svc.stray_daemons() == []


def test_a_stack_running_on_r1_is_startable_on_r3_of_the_same_band(tmp_path, monkeypatch):
    # kiss@r1 runs (its process, told as r1's): r1's card lists it running, r3's card offers it (one app stack per
    # RADIO, W286/W322). red on 8289f771: the band's one entry listed kiss running and offered it nowhere on 433
    svc = _svc(tmp_path, replies={"/tmp/loraconf-r1.sock": READY % b"r1"},
               cmdlines={4242: ["loraham-kiss-tnc", "--config", "X"]})
    monkeypatch.setattr(instances, "instance_processes",
                        lambda s, table: [("kiss", "r1", "loraham-kiss-tnc", 4242)])
    entries = {e["rid"]: e for e in svc.radio_overview()}
    assert [(x["sid"], x["rid"]) for x in entries["r1"]["running"]] == [("kiss", "r1")], entries["r1"]["running"]
    assert "kiss" in [x["id"] for x in entries["r3"]["startable"]], entries["r3"]["startable"]
    assert "kiss" not in [x.get("sid", x["id"]) for x in entries["r3"]["running"]]


def test_a_stray_band_daemon_is_listed_by_stray_daemons_only(tmp_path, monkeypatch):
    # the list has r1 on 433 alone; a daemon still answers for 868 (no enabled radio there): no radio_overview entry
    # without a rid, one stray_daemons() row for 868 (its card and Stop stay, W290). The 868 view is faked through a
    # radio-named socket (conftest's band-socket guard: no band socket beside a hardware list).
    # red on 8289f771: the stray row was a radio_overview entry without a rid (stray_daemons did not exist)
    from lhpc.core import daemon_control
    rows = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),)
    svc = _svc(tmp_path, rows, replies={"/tmp/loraconf-r1.sock": READY % b"r1", "/tmp/loraconf-r9.sock": READY % b"r9"})
    real = svc.daemon_view
    monkeypatch.setattr(svc, "daemon_view", lambda band, rid="": daemon_control.read_view(svc._system, "r9")
                        if band == "868" and not rid else real(band, rid))
    assert [e["rid"] for e in svc.radio_overview()] == ["r1"]
    strays = svc.stray_daemons()
    assert [(x["band"], x["daemon"]["reachable"]) for x in strays] == [("868", True)]
    assert set(strays[0]) == {"band", "daemon", "shared_with", "rflogs"}

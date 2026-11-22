"""F33 (his word): the RX-TX Monitor's parameter line shows SYNC after SF (server markup; the browser tests fill it)."""
import radios_fixture
from htmlq import parse

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

READY = b"STATUS ID=r1 BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


def test_the_monitor_line_has_a_sync_group_after_sf(tmp_path, web, monkeypatch):
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": READY})
    doc = parse(web(service_factory=lambda: ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)))
                .get("/").get_data(as_text=True))
    [line] = doc.within(doc.by_id("monitor-r1")).find("p", class_="rparams")
    keys = [k.text for k in doc.within(line).find("span", class_="rpk")]
    assert keys == ["FREQ", "PWR", "BW", "SF", "SYNC"]
    assert doc.by_id("rd-sync-r1").text.strip() == "-"                                # until the poll fills it

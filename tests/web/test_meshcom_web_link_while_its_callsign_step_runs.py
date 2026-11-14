"""B2a's acceptance condition (his Q-B2-5 condition): while MeshCom's required callsign step still waits for the
firmware's console (12323 not answering), its web UI is usable, and Home shows the web link as soon as the web listener
is up. A required step runs synchronously, so there is no detached runner and no "booting" badge: the console's
listener is simply absent, and the link follows the web listener alone."""
import radios_fixture
from htmlq import parse

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

LEGACY_QEMU = ["qemu-system-xtensa", "-nographic"]


def test_home_shows_meshcoms_web_link_while_its_console_is_still_deaf(tmp_path, web):
    radios_fixture.write(tmp_path)                                    # r1 433, r2 868
    fake = FakeSystem(cmdlines_data={2000: LEGACY_QEMU},             # MeshCom's node runs, unclaimed: the band card
                      listeners=[Listener("ipv4", "127.0.0.1", 18083, 18083)])   # its web up, console 12323 deaf
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    r = web(service_factory=lambda: svc).get("/", headers={"Host": "localhost:8443"})
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    cards = [doc.within(d) for d in doc.find("div", class_="runstack")
             if doc.within(d).find("p", **{"data-stack": "meshcom"})]
    assert cards, "no MeshCom card on Home"
    for card in cards:
        assert [a["href"] for a in card.find("a", class_="iface-web")] == ["http://127.0.0.1:18083"]

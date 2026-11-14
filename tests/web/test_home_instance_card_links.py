"""A23 web part: an instance's component row on its radio's card links what the 0.13.x card linked (config, logs, the
web UI per viewer: local, LAN, client network; proxied or direct), each for the instance's own radio. Through the
rendered Home page, each assertion scoped to one instance's `data-stack="meshcom@<rid>"` block, so an orphan entry can
never satisfy it. Two instances run: meshcom@r1 and meshcom@r3, each with its own ports."""
import radios_fixture
from htmlq import parse

from lhpc.core import commands, config, instances, radios
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

ROWS = radios_fixture.RADIOS + (radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),)
WEB = {"r1": 18083, "r3": 18349}
CONSOLE = {"r1": 12323, "r3": 12589}
LOCAL = {"X-LHPC-Peer": "loopback", "Host": "127.0.0.1:8443"}
LAN = {"X-LHPC-Peer": "remote", "Host": "198.51.100.7"}
AP = {"X-LHPC-Peer": "remote", "Host": "10.42.0.1"}
SETTINGS = "/stacks?open=meshcom&cfg=meshcom&radio={}#stack-settings-meshcom"


def _svc(tmp_path, exposed=(), extra=()):
    """meshcom@r1 + meshcom@r3 running; a port in `exposed` listens on 0.0.0.0, the others on 127.0.0.1; `extra`:
    more listeners (a stackweb proxy's port, as (ip, port))."""
    radios_fixture.write(tmp_path, ROWS)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    comp = plain.stack("meshcom").component("meshcom-qemu")
    table = {}
    for n, rid in enumerate(("r1", "r3")):
        opt, val = instances._token(plain, comp, commands.Instance(rid, band="433"), "433")
        table[1000 + n] = [comp.process.exec_name, opt, val]
    ports = [p for r in ("r1", "r3") for p in (WEB[r], CONSOLE[r])]
    listeners = [Listener("ipv4", "0.0.0.0" if p in exposed else "127.0.0.1", p, p) for p in ports]
    listeners += [Listener("ipv4", ip, port, port) for ip, port in extra]
    return ControllerService(system=FakeSystem(cmdlines_data=table, listeners=listeners).system, paths=paths)


def _remote(tmp_path):
    config.save_webserver_config(Paths(runtime_root=tmp_path), remote_exposed=True)   # an IP-literal Host passes


def _block(web, svc, headers, rid):
    r = web(service_factory=lambda: svc).get("/", headers=headers)
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    (block,) = [doc.within(d) for d in doc.find("div", class_="runstack")
                if doc.within(d).find("p", **{"data-stack": f"meshcom@{rid}"})]
    return block


def _hrefs(block, cls):
    return [a["href"] for a in block.find("a", class_=cls)]


def test_a_local_viewer_gets_each_instances_own_web_ui(tmp_path, web):
    svc = _svc(tmp_path)
    assert _hrefs(_block(web, svc, LOCAL, "r3"), "iface-web") == ["http://127.0.0.1:18349"]
    assert _hrefs(_block(web, svc, LOCAL, "r1"), "iface-web") == ["http://127.0.0.1:18083"]


def test_config_and_logs_name_the_instances_radio(tmp_path, web):
    block = _block(web, _svc(tmp_path), LOCAL, "r3")
    assert _hrefs(block, "logslink") == ["/stacks?cfg=meshcom&radio=r3#stack-settings-meshcom",
                                         "/logs/meshcom-qemu?radio=r3"]


def test_a_lan_viewer_gets_an_exposed_instance_at_the_host_it_reached(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path, exposed=(WEB["r3"],))
    assert _hrefs(_block(web, svc, LAN, "r3"), "iface-web") == ["http://198.51.100.7:18349/"]
    r1 = _block(web, svc, LAN, "r1")                                  # r1's listener stays on loopback
    assert _hrefs(r1, "iface-web") == [] and _hrefs(r1, "iface-local") == [SETTINGS.format("r1")]


def test_a_client_network_viewer_gets_the_access_point_address(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path, exposed=(WEB["r3"],))
    assert _hrefs(_block(web, svc, AP, "r3"), "iface-web") == ["http://10.42.0.1:18349/"]


def test_a_loopback_instance_seen_remotely_links_its_own_settings(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path)
    block = _block(web, svc, LAN, "r3")
    (local,) = block.find("a", class_="iface-local")
    assert local["href"] == SETTINGS.format("r3") and "local only" in local.text
    page = web(service_factory=lambda: svc).get(local["href"], headers=LAN)
    assert page.status_code == 200
    assert parse(page.get_data(as_text=True)).present("stack-settings-meshcom")


def _proxy(svc, page, port):
    r = svc.stack_web_configure(page, mode="local", port=port, scheme="http", access_mode="no-auth")
    assert r.ok, (r.summary, r.details)


def test_the_instances_own_proxied_page_decides_its_link(tmp_path, web):
    for listen, want_web, want_local in (
            (("0.0.0.0", 8470), ["http://127.0.0.1:8470/"], []),            # exposed, a local viewer
            (("127.0.0.1", 8470), ["http://127.0.0.1:8470/"], []),          # loopback, a local viewer
            (None, [], [SETTINGS.format("r3")])):                           # absent: Apply
        svc = _svc(tmp_path, extra=[listen] if listen else [])
        _proxy(svc, "meshcom@r3", 8470)
        block = _block(web, svc, LOCAL, "r3")
        assert (_hrefs(block, "iface-web"), _hrefs(block, "iface-local")) == (want_web, want_local), listen
        assert _hrefs(_block(web, svc, LOCAL, "r1"), "iface-web") == ["http://127.0.0.1:18083"]   # r1: direct


def test_a_proxied_instance_seen_remotely(tmp_path, web):
    _remote(tmp_path)
    for listen, want_web, label in ((("0.0.0.0", 8470), ["http://198.51.100.7:8470/"], None),
                                    (("127.0.0.1", 8470), [], "local only"),
                                    (None, [], "proxy not active — Apply")):
        svc = _svc(tmp_path, extra=[listen] if listen else [])
        _proxy(svc, "meshcom@r3", 8470)
        block = _block(web, svc, LAN, "r3")
        assert _hrefs(block, "iface-web") == want_web, listen
        if label:
            (local,) = block.find("a", class_="iface-local")
            assert local["href"] == SETTINGS.format("r3") and label in local.text, listen


def test_another_instances_proxied_page_does_not_decide_this_ones(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path, extra=[("0.0.0.0", 8471)])
    _proxy(svc, "meshcom@r1", 8471)                                   # r1 proxied and exposed, r3 not proxied
    assert _hrefs(_block(web, svc, LAN, "r1"), "iface-web") == ["http://198.51.100.7:8471/"]
    assert _hrefs(_block(web, svc, LAN, "r3"), "iface-local") == [SETTINGS.format("r3")]


def test_a_booting_instance_shows_the_badge_and_its_links(tmp_path, web, monkeypatch):
    # the plan's NOTE D: r3's post-start runner still works, its web listener is up -> "booting" + its links
    real = Lifecycle.owned_records

    def owned_records(self, comp_id, band=None, role="", rid=None):
        return [{"pid": 3000, "rid": "r3"}] if role == "post" and comp_id == "meshcom-qemu" else real(
            self, comp_id, band, role, rid)
    monkeypatch.setattr(Lifecycle, "owned_records", owned_records)
    monkeypatch.setattr(Lifecycle, "_original_ceased", lambda self, rec: False)
    block = _block(web, _svc(tmp_path), LOCAL, "r3")
    assert block.find("span", class_="badge-booting")
    assert _hrefs(block, "iface-web") == ["http://127.0.0.1:18349"] and len(_hrefs(block, "logslink")) == 2

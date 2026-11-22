"""F23 (his decision: keep the LAN address, add a yellow "blocked by firewall" pill): a client address the APPLIED firewall
drops for every source shows the pill next to it, for a remote viewer only. The rule: the applied rows with proto tcp and
that port are non-empty and every one is deny-default, not selected and has no allow list. No applied firewall = no pill.
Through the rendered Home page; the applied firewall is the one read `_fw_applied_candidate` (its own tests own it)."""
import pytest
import radios_fixture
from htmlq import parse
from seams import entry_args, exe

from lhpc.core import config, instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

LOCAL = {"X-LHPC-Peer": "loopback", "Host": "127.0.0.1:8443"}
LAN = {"X-LHPC-Peer": "remote", "Host": "198.51.100.7"}
API, WEB = 4403, 9443                                        # meshtastic@r1's API and web ports


def _row(port=API, deny=True, selected=False, cidrs=(), proto="tcp", family="ipv4"):
    return {"id": f"meshtastic:{port}", "proto": proto, "family": family, "addr": "0.0.0.0", "port": port,
            "allow_cidrs": list(cidrs), "selected": selected, "deny_default": deny}


def _svc(tmp_path, monkeypatch, rows):
    """meshtastic@r1 running, its API on 0.0.0.0 (exposed); `rows`: the applied firewall's endpoint rows (None: none)."""
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    paths = Paths(runtime_root=tmp_path)
    config.save_webserver_config(paths, remote_exposed=True)                   # an IP-literal Host passes
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    comp = plain.stack("meshtastic").component("meshtastic")
    opt, val = instances._token(plain, comp, instances.parse_target(plain, "meshtastic@r1", "start")[1], "433")
    argv = [exe(comp.process, comp.process.exec_name), *entry_args(comp.process),
            *[a for a in comp.process.all_args if a != opt], opt, val]
    monkeypatch.setattr(ControllerService, "_fw_applied_candidate",
                        lambda self: None if rows is None else {"endpoints": rows})
    return ControllerService(system=FakeSystem(cmdlines_data={900: argv}, listeners=[
        Listener("ipv4", "0.0.0.0", API, 1), Listener("ipv4", "127.0.0.1", WEB, 2)]).system, paths=paths)


def _pills(web, svc, headers=LAN):
    r = web(service_factory=lambda: svc).get("/", headers=headers)
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    return doc, doc.find("a", class_="fwblocked") + doc.find("span", class_="fwblocked")


def test_a_blocked_api_address_keeps_the_lan_address_and_gets_the_pill(tmp_path, web, monkeypatch):
    # red before F23: the card offered the address with no sign that the firewall drops it
    doc, pills = _pills(web, _svc(tmp_path, monkeypatch, [_row(), _row(family="ipv6")]))
    assert len(pills) == 1 and pills[0].text.strip() == "blocked by firewall" and pills[0].has_class("pill-warn")
    assert f"tcp: 198.51.100.7:{API}" in doc.text                                # the address stays (his word)
    assert pills[0]["href"].startswith("/stacks/controller/menu/firewall-row--configure")


@pytest.mark.parametrize("rows", [
    [_row(selected=True)],                                   # direct access ticked
    [_row(), _row(family="ipv6", selected=True)],            # one of the matching rows allows it
    [_row(cidrs=("192.168.0.0/16",))],                       # an allow list
    [_row(deny=False)],                                      # not deny-default
    [_row(proto="udp")],                                     # no tcp row for the port
    [_row(port=API + 1)],                                    # another port
    [_row(port="x")],                                        # a non-numeric port never matches
    [],                                                      # no rows
    None,                                                    # no applied firewall: no guessing
], ids=["selected", "one-of-two-selected", "allow-list", "not-deny", "udp-only", "other-port", "non-numeric", "empty", "none"])
def test_no_pill_unless_every_tcp_row_of_the_port_blocks_it(tmp_path, web, monkeypatch, rows):
    _doc, pills = _pills(web, _svc(tmp_path, monkeypatch, rows))
    assert pills == []


def test_a_local_viewer_gets_no_pill(tmp_path, web, monkeypatch):
    _doc, pills = _pills(web, _svc(tmp_path, monkeypatch, [_row()]), LOCAL)
    assert pills == []


def test_a_web_interface_never_gets_the_pill(tmp_path, web, monkeypatch):
    # only the plain-address branch carries it: the web GUI's port blocked too, still no pill on its link
    _doc, pills = _pills(web, _svc(tmp_path, monkeypatch, [_row(), _row(port=WEB)]))
    assert len(pills) == 1


def test_the_pill_links_the_firewall_page_only_when_it_exists(tmp_path, web, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, [_row()])
    _doc, [pill] = _pills(web, svc)
    c = web(service_factory=lambda: svc)
    assert c.get(pill["href"]).status_code == 200                               # a live link
    monkeypatch.setattr(ControllerService, "firewall_settings_view", lambda self, st=None: (_ for _ in ()).throw(RuntimeError()))
    _doc, [pill] = _pills(web, svc)
    assert pill.tag == "span" and not pill.has_attr("href")                      # no firewall levels: no dead link


def test_the_signature_poll_never_reads_the_firewall(tmp_path, web, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, [_row()])
    seen = []
    monkeypatch.setattr(ControllerService, "_fw_applied_candidate", lambda self: seen.append(1) or {"endpoints": [_row()]})
    assert web(service_factory=lambda: svc).get("/api/dash-signature").status_code == 200
    assert seen == []


@pytest.mark.parametrize("port, row_port", [("²", "²"), ("8001", "٨٠٠١")])
def test_a_non_ascii_digit_port_is_no_port_and_never_breaks_the_check(tmp_path, monkeypatch, port, row_port):
    # str.isdigit takes "²" and Arabic-Indic digits; int() refuses the first: the check says "not blocked", never raises
    # (G2-FINDSTRAING N2)
    from lhpc.core import fw_card
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "_fw_applied_candidate", lambda self: {"endpoints": [_row(port=row_port)]})
    monkeypatch.setattr(ControllerService, "_request_memo", lambda self, key, fn: fn(), raising=False)
    assert fw_card.port_blocked(svc, port) is False

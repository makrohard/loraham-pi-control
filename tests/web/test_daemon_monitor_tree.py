"""DAEMON-MONITOR-TREE (0.14.0, maintainer's word 11-18): Apps › LoRaHAM daemon › Settings › Monitor · rN holds Get
STATUS, Get STATS, View Socket and RX/TX View, each its own submenu level "like the logs": a closed panel with Reload
and "page ›", its own page opening it. The button row and its ✕ windows are gone; every view stays (the browser lane
pins what each asks: testlab/tests/browser/test_daemon_monitor_tree.py)."""

from __future__ import annotations

import re

from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")
_VIEWS = [("status", "Get STATUS"), ("stats", "Get STATS"), ("socket", "View Socket"), ("rxtx", "RX/TX View")]


def _client(web, tmp_path, live=("r1", "r2")):
    """A two-radio box whose daemon answers on the radios in `live`."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    assert radio_start.setup_radios(p, "uputronics")[0]
    cfgmod.save_hardware_setup(p, "uputronics")
    svc = ControllerService(system=FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in live}).system, paths=p)
    return web(service_factory=lambda: svc), svc


def _html(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


def _text(el):
    return re.sub(r"\s+", " ", el.text).strip()


def _mid(rid, kind):
    return f"stack-settings-daemon--{rid}-{kind}"


def test_the_tree_lists_the_four_views_under_each_radios_monitor_in_order(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for rid in ("r1", "r2"):                                  # the tree shows the levels of the radio it is opened on
        doc = parse(_html(c, f"/stacks/menus?radio={rid}"))
        links = [(m.group(1), _text(a)) for a in doc.within(doc.find("div", class_="card")[0]).find("a")
                 if (m := re.fullmatch(r"/stacks/daemon/menu/(stack-settings-daemon--r\d-[a-z]+)(?:\?.*)?", a["href"]))]
        mine = [(mid, label) for mid, label in links if mid.startswith(f"stack-settings-daemon--{rid}-")]
        assert mine[0] == (_mid(rid, "monitor"), f"Monitor · {rid} page ›"), mine      # a branch now: its own page link
        assert mine[1:5] == [(_mid(rid, k), f"{name} · {rid}") for k, name in _VIEWS], mine


def test_each_views_page_opens_its_one_panel_under_its_monitor(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for kind, name in _VIEWS:
        doc = parse(_html(c, f"/stacks/daemon/menu/{_mid('r1', kind)}?radio=r1"))
        panels = doc.find("details", class_="daemonview")
        assert [(p["id"], p["data-rid"], p["data-kind"]) for p in panels] == [(_mid("r1", kind), "r1", kind)], kind
        assert "open" in panels[0].attrs, kind                                 # its page opens it (the JS asks at load)
        assert _text(doc.within(panels[0]).find("summary")[0]) == f"{name} · r1"
        assert doc.within(panels[0]).find("button", class_="daemonview-reload")
        assert doc.within(panels[0]).find("pre", class_="stream22")
        crumbs = _text(doc.find("nav", class_="crumbs")[0])
        assert crumbs.endswith(f"› Settings › Monitor · r1 › {name} · r1"), crumbs


def test_the_stacks_body_holds_the_views_closed_and_no_button_row(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    body = _html(c, "/stacks/daemon/body?radio=r1")
    doc = parse(body)
    panels = doc.find("details", class_="daemonview")
    assert sorted((p["data-rid"], p["data-kind"]) for p in panels) == sorted(("r1", k) for k, _n in _VIEWS)   # r1's view
    assert not [p for p in panels if "open" in p.attrs]                          # closed: nothing asks
    monitor = doc.by_id(_mid("r1", "monitor"))
    inside = [p["id"] for p in doc.within(monitor).find("details", class_="daemonview")]
    assert inside == [_mid("r1", k) for k, _n in _VIEWS]                         # under its Monitor, in order
    for gone in ("socketbtn", "txbtn", "liveclose", "socketclose", "txclose"):
        assert f'class="{gone}' not in body and f" {gone}" not in body, gone
    assert not re.search(r'id="(liveout|socketout|txout)-', body)
    assert 'class="livebtn" data-rid' not in body


def test_an_offline_radio_has_no_monitor_and_no_views(tmp_path, web):
    c, _svc = _client(web, tmp_path, live=("r1",))
    body = _html(c, "/stacks/daemon/body?radio=r1")
    assert f'id="{_mid("r1", "status")}"' in body
    assert "--r2-monitor" not in body and not re.search(r'data-rid="r2" data-kind', body)
    assert c.get(f"/stacks/daemon/menu/{_mid('r2', 'status')}").status_code == 404

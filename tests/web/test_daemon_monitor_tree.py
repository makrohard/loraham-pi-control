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
        panels = doc.find("div", class_="daemonview")       # UI-REORG c4: always open, no own show/hide (was details)
        assert [(p["id"], p["data-rid"], p["data-kind"]) for p in panels] == [(_mid("r1", kind), "r1", kind)], kind
        assert panels[0]["data-up"] == "1" and not doc.find("details", class_="daemonview"), kind
        head = doc.within(panels[0]).find("h4", class_="daemonview-head")[0]
        assert _text(head).startswith(f"{name} · r1 ")                         # c4: its own page carries the badge
        assert [b["data-rid"] for b in doc.within(head).find("span", class_="dbadge")] == ["r1"]
        assert doc.within(panels[0]).find("button", class_="daemonview-reload")
        assert doc.within(panels[0]).find("pre", class_="stream22")
        crumbs = _text(doc.find("nav", class_="crumbs")[0])
        assert crumbs.endswith(f"› Settings › Monitor · r1 › {name} · r1"), crumbs


def test_the_stacks_body_holds_the_views_closed_and_no_button_row(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    body = _html(c, "/stacks/daemon/body?radio=r1")
    doc = parse(body)
    panels = doc.find("div", class_="daemonview")       # UI-REORG c4: always open views (was closed details)
    assert sorted((p["data-rid"], p["data-kind"]) for p in panels) == sorted(("r1", k) for k, _n in _VIEWS)   # r1's view
    monitor = doc.by_id(_mid("r1", "monitor"))
    assert "open" not in monitor.attrs                                           # c4: no hard `open`: nothing asks
    inside = [p["id"] for p in doc.within(monitor).find("div", class_="daemonview")]
    assert inside == [_mid("r1", k) for k, _n in _VIEWS]                         # under its Monitor, in order
    for gone in ("socketbtn", "txbtn", "liveclose", "socketclose", "txclose"):
        assert f'class="{gone}' not in body and f" {gone}" not in body, gone
    assert not re.search(r'id="(liveout|socketout|txout)-', body)
    assert 'class="livebtn" data-rid' not in body


def test_an_offline_radio_has_its_monitor_paused(tmp_path, web):
    # UI-REORG c4 (his OPEN-M3 = b): the Monitor is always present; daemon not running: "Daemon not running", Reload
    # off (was: no Monitor, its view pages 404). The other radio's blocks are not rendered (the switch shows one).
    c, _svc = _client(web, tmp_path, live=("r1",))
    body = _html(c, "/stacks/daemon/body?radio=r1")
    assert f'id="{_mid("r1", "status")}"' in body
    assert "--r2-monitor" not in body and not re.search(r'data-rid="r2" data-kind', body)
    doc = parse(_html(c, "/stacks/daemon/body?radio=r2"))
    monitor = doc.by_id(_mid("r2", "monitor"))
    assert monitor["data-up"] == "0" and "Daemon not running" in _text(monitor)
    assert all(b.has_attr("disabled") for b in doc.within(monitor).find("button", class_="daemonview-reload"))
    page = parse(_html(c, f"/stacks/daemon/menu/{_mid('r2', 'status')}"))      # its page: the id's radio, paused
    [view] = page.find("div", class_="daemonview")
    assert view["data-up"] == "0" and page.within(view).find("button", class_="daemonview-reload")[0].has_attr("disabled")

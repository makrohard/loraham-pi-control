"""DAEMON-MONITOR-TREE (0.14.0, maintainer's word 11-18) + F7 (his Q1 B, Q2 A, Q3, Q8 A, Q9 B, Q10 A): Apps ›
LoRaHAM daemon › Settings › Monitor (ONE, on the Settings radio) holds STATUS, STATS, SOCKET and RX/TX View, tree
nodes showing their view inline (Reload, "Daemon not running" each: F8) with no page of their own. Only the Monitor
has a page: radio pins + view pins (Webserver-logs tabs), then ONE view (no Reload, no "every N s" line: F6). The
button row and its ✕ windows are gone; every view stays (the browser lane pins what each asks:
testlab/tests/browser/test_daemon_monitor_tree.py)."""

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
_VIEWS = [("status", "STATUS"), ("stats", "STATS"), ("socket", "SOCKET"), ("rxtx", "RX/TX View")]
_MON = "stack-settings-daemon--monitor"


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


def _mid(kind):
    return f"{_MON}-{kind}"


def _monitor_page(c, query):
    return parse(_html(c, f"/stacks/daemon/menu/{_MON}?{query}"))


def test_the_tree_lists_one_monitor_with_its_four_views_without_pages(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for rid in ("r1", "r2"):                                  # the tree shows the levels of the radio it is opened on
        doc = parse(_html(c, f"/stacks/menus?radio={rid}"))
        card = doc.within(doc.find("div", class_="card")[0])
        hrefs = [a["href"] for a in card.find("a")]
        assert [h for h in hrefs if "/stacks/daemon/" in h and "-monitor" in h] == \
            [f"/stacks/daemon/menu/{_MON}?radio={rid}"], hrefs
        assert not [h for h in hrefs if re.search(r"--r\d-(monitor|status|stats|socket|rxtx)", h)], hrefs
        [mon] = [d for d in card.find("details") if _text(doc.within(d).find("summary")[0]) == "Monitor"]
        assert [_text(li) for li in doc.within(mon).find("li")] == [n for _k, n in _VIEWS]   # Q2 A, no link (Q3)
        assert len(doc.within(mon).find("a")) == 1                                # only "Monitor page ›"


def test_the_monitor_page_shows_the_pins_and_one_view_of_the_chosen_kind_and_radio(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for rid, band, other in (("r1", "433", ("r2", "868")), ("r2", "868", ("r1", "433"))):
        for kind, name in _VIEWS:
            doc = _monitor_page(c, f"radio={rid}&view={kind}")
            panels = doc.find("div", class_="daemonview")
            assert [(p["id"], p["data-rid"], p["data-kind"]) for p in panels] == [(_mid(kind), rid, kind)], kind
            assert panels[0]["data-up"] == "1" and doc.find("div", class_="menupage")[0]["data-fullpage"] == "1"
            head = doc.within(panels[0]).find("h4", class_="daemonview-head")[0]
            assert _text(head) == f"{name} · {rid} daemon READY"                 # Q8 A heading, the page's badge
            assert [b["data-rid"] for b in doc.within(head).find("span", class_="dbadge")] == [rid]
            assert not doc.find("button", class_="daemonview-reload")           # F6: no Reload on the page
            assert doc.within(panels[0]).find("pre", class_="stream22")
            assert _text(doc.find("nav", class_="crumbs")[0]).endswith("› Settings › Monitor")
            assert not doc.find("nav", class_="rswitch")                         # the pins replace the switch
            [radios, views] = doc.find("span", class_="tabs")
            pins = {_text(a): (a["href"], a.has_class("mono")) for a in doc.within(radios).find("a")}
            assert pins == {f"{rid} · {band}": (f"/stacks/daemon/menu/{_MON}?radio={rid}&view={kind}", True),
                            f"{other[0]} · {other[1]}": (f"/stacks/daemon/menu/{_MON}?radio={other[0]}&view={kind}",
                                                         False)}, pins       # each keeps the shown view
            vp = [(_text(a), a["href"], a.has_class("mono")) for a in doc.within(views).find("a")]
            assert vp == [("RX TX" if k == "rxtx" else k.upper(), f"/stacks/daemon/menu/{_MON}?radio={rid}&view={k}",
                           k == kind) for k, _n in _VIEWS], vp                # each keeps the shown radio


def test_without_a_view_the_monitor_page_shows_status_on_the_first_radio(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for query in ("", "view=bogus"):                                         # Q10 A: STATUS; no ?radio=: the first
        [view] = _monitor_page(c, query).find("div", class_="daemonview")
        assert (view["data-kind"], view["data-rid"]) == ("status", "r1"), query


def test_the_stacks_body_holds_the_views_and_no_button_row(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    body = _html(c, "/stacks/daemon/body?radio=r1")
    doc = parse(body)
    panels = doc.find("div", class_="daemonview")       # UI-REORG c4: always open views (was closed details)
    assert sorted((p["data-rid"], p["data-kind"]) for p in panels) == sorted(("r1", k) for k, _n in _VIEWS)   # r1's view
    monitor = doc.by_id(_MON)
    assert "open" not in monitor.attrs and monitor["data-rid"] == "r1"           # c4: no hard `open`: nothing asks
    views = doc.within(monitor).find("div", class_="daemonview")
    assert [p["id"] for p in views] == [_mid(k) for k, _n in _VIEWS]             # under its Monitor, in order
    for p in views:                                    # each with its Reload and its own (hidden) down line (F8)
        assert doc.within(p).find("button", class_="daemonview-reload")
        assert [x.has_attr("hidden") for x in doc.within(p).find("p", class_="dstate-down")] == [True]
    assert len(doc.within(monitor).find("p", class_="dstate-down")) == 4         # F8: no Monitor-level line
    assert not [a for a in doc.within(monitor).find("a") if "--monitor-" in a["href"]]   # no "page ›" (Q3)
    for gone in ("socketbtn", "txbtn", "liveclose", "socketclose", "txclose"):
        assert f'class="{gone}' not in body and f" {gone}" not in body, gone
    assert not re.search(r'id="(liveout|socketout|txout)-', body)
    assert 'class="livebtn" data-rid' not in body


def test_an_offline_radio_has_its_monitor_paused(tmp_path, web):
    # UI-REORG c4 (OPEN-M3 = b): the Monitor is always present; daemon not running: each view says "Daemon not
    # running" (F8), Reload off. The other radio's views are not rendered (the switch shows one).
    c, _svc = _client(web, tmp_path, live=("r1",))
    body = _html(c, "/stacks/daemon/body?radio=r1")
    assert f'id="{_mid("status")}"' in body and not re.search(r'data-rid="r2" data-kind', body)
    doc = parse(_html(c, "/stacks/daemon/body?radio=r2"))
    monitor = doc.by_id(_MON)
    assert monitor["data-rid"] == "r2" and monitor["data-up"] == "0"
    downs = doc.within(monitor).find("p", class_="dstate-down")
    assert len(downs) == 4 and not any(d.has_attr("hidden") for d in downs)
    assert all(b.has_attr("disabled") for b in doc.within(monitor).find("button", class_="daemonview-reload"))
    page = _monitor_page(c, "radio=r2")                                          # its page: that radio, paused
    [view] = page.find("div", class_="daemonview")
    assert view["data-up"] == "0" and not page.find("button", class_="daemonview-reload")
    assert page.text.count("Daemon not running") == 1                            # once, in its one view (F8)


def test_the_views_and_the_old_per_radio_monitor_pages_are_not_found(tmp_path, web):
    # F7 (Q9 B, G1 B1): the views have no page (their new ids are page-less tree nodes); the old per-radio Monitor
    # and view pages are gone
    c, _svc = _client(web, tmp_path)
    for kind, _n in _VIEWS:
        assert c.get(f"/stacks/daemon/menu/{_mid(kind)}?radio=r1").status_code == 404, kind
    for rid in ("r1", "r2"):
        for kind in ("monitor", *(k for k, _n in _VIEWS)):
            assert c.get(f"/stacks/daemon/menu/stack-settings-daemon--{rid}-{kind}").status_code == 404, (rid, kind)

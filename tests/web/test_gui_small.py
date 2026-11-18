"""GUI-SMALL (0.14.0, maintainer's word 11-18): (1) a web proxy's "Monitor · rN" page is its "Info · rN" page, for
every web proxy, everywhere it shows (level id `--info`); (2) the Auto-install and Dependency Overview pages, which
the tree does not list, carry the crumb line of the other pages: "All menus · Home › Apps › <the page>"."""

from __future__ import annotations

import re

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")


def _client(web, tmp_path, radios=False):
    """A fake box; `radios`: two radios with a live daemon on both (the stacks' web proxy pages exist only then)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    fs = FakeSystem()
    if radios:
        assert radio_start.setup_radios(p, "uputronics")[0]
        cfgmod.save_hardware_setup(p, "uputronics")
        fs = FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in ("r1", "r2")})
    svc = ControllerService(system=fs.system, paths=p)
    return web(service_factory=lambda: svc), svc


def _html(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


def _text(el):
    return re.sub(r"\s+", " ", el.text).strip()


# ---- (2) the crumb line on the two pages outside the tree ------------------------------------------------------

@pytest.mark.parametrize("path,here", [("/auto-install", "Auto-install"), ("/dependencies", "Dependency Overview")])
@pytest.mark.parametrize("radio", ["", "r1"])
def test_the_pages_outside_the_tree_show_all_menus_home_apps_and_their_name(tmp_path, web, path, here, radio):
    c, _svc = _client(web, tmp_path, radios=bool(radio))
    html = _html(c, path + (f"?radio={radio}" if radio else ""))
    doc = parse(html)
    line = doc.find("div", class_="crumbline")
    assert len(line) == 1, path
    # the first thing of the page's content: before the cards (and before an ack banner)
    assert html.index('class="crumbline"') < html.index('class="card"')
    links = doc.within(line[0]).find("a")
    assert [(_text(a), a["href"]) for a in links] == [
        ("All menus", "/stacks/menus" + (f"?radio={radio}" if radio else "")), ("Home", "/"), ("Apps", "/stacks")]
    # "All menus" stands before the crumbs' <nav> (as on every other page), the page's name is the last crumb
    nav = doc.within(line[0]).find("nav", class_="crumbs")
    assert len(nav) == 1 and nav[0]["aria-label"] == "You are here"
    assert "All menus" not in _text(nav[0])
    assert _text(nav[0]) == f"Home › Apps › {here}"
    assert [_text(s) for s in doc.within(nav[0]).find("span", class_="here")] == [here]


# ---- (1) a web proxy's Info page (was "Monitor") ----------------------------------------------------------------

def _tree_levels(c):
    """(owner, level id, label) of every web proxy level the tree page lists (the public path)."""
    doc = parse(_html(c, "/stacks/menus"))
    out = []
    for a in doc.within(doc.find("div", class_="card")[0]).find("a"):
        m = re.fullmatch(r"/stacks/([^/]+)/menu/(stack-webserver-[^?]+)(?:\?.*)?", a["href"])
        if m:
            out.append((m.group(1), m.group(2), _text(a)))
    return out


def test_every_web_proxy_has_an_info_page_and_no_monitor_page(tmp_path, web):
    """The proxies are the tree's own: every `stack-webserver-<page>` level the tree page lists (any stack, any
    radio) has an Info child, "Info · rN", and no Monitor child; its block's section, its page's crumbs, its
    Stacks-page body say Info; the old `--monitor` page is gone (404)."""
    c, _svc = _client(web, tmp_path, radios=True)
    levels = _tree_levels(c)
    pages = [(o, mid) for o, mid, _ in levels if re.fullmatch(r"stack-webserver-[^@]+@r\d", mid)]
    labels = {(o, mid): label for o, mid, label in levels}
    assert len({o for o, _ in pages}) > 1 and {mid[-2:] for _, mid in pages} == {"r1", "r2"}, pages
    assert not [lv for lv in levels if "monitor" in lv[1] or "Monitor" in lv[2]], levels
    for owner, mid in pages:
        rid = mid[-2:]
        assert labels.get((owner, mid + "--info")) == f"Info · {rid}", (owner, mid)
        page = parse(_html(c, f"/stacks/{owner}/menu/{mid}--info?radio={rid}"))
        nav = page.find("nav", class_="crumbs")[0]
        assert _text(nav).endswith(f" › Info · {rid}") and "Monitor" not in _text(nav), (owner, mid)
        block = page.by_id(f"{mid}--info")
        assert block is not None and _text(page.within(block).find("summary")[0]) == "Info", (owner, mid)
        body = _html(c, f"/stacks/{owner}/body?radio={rid}")
        assert f'id="{mid}--info"' in body and f"{mid}--monitor" not in body, (owner, mid)
        assert c.get(f"/stacks/{owner}/menu/{mid}--monitor?radio={rid}").status_code == 404, (owner, mid)


def test_the_graywolf_info_page_names_its_path_like_his_word(tmp_path, web):
    """Apps › Graywolf APRS › Web Proxies › Proxy · Graywolf UI · r1 › Info · r1 (the crumbs and the page's heading)."""
    c, _svc = _client(web, tmp_path, radios=True)
    doc = parse(_html(c, "/stacks/graywolf/menu/stack-webserver-graywolf@r1--info?radio=r1"))
    nav = doc.find("nav", class_="crumbs")[0]
    assert _text(nav) == "Home › Apps › Graywolf APRS › Web Proxies › Proxy · Graywolf UI · r1 › Info · r1"
    assert [_text(h) for h in doc.within(nav).find("h2", class_="here")] == ["Info · r1"]

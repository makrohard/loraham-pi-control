"""Dash plan c13 NAV FIRST + 13a/13c/13d (G1 B6 matrix, the nav-first rows): in <main> the first element is the page's
.crumbline, then its optional second line (p.pagebar), then any notice (the disk notice forced on). Home: "All menus ·
Home" (Home the h2.here, no link), no second line. Apps: "All menus · Home › Apps", no second line. All menus: one line
"Home › Apps › All menus", no second line. Tree pages: their crumbs + "‹ show in Stacks". Hardware Add / a radio: the
hardware crumbs + "‹ The radios". Pages outside the tree (Remove radio, Dependency Overview, Auto-install, a job log;
13b) and the pages with no place (confirmations, error, self-update progress, HMAC apply: "All menus · Home › Apps ›
<title>") get "‹ Back" (data-back, href Home). Every page except Home links Home in its crumbline. Rendered GETs."""
import pytest
from htmlq import parse

from lhpc.core.services import ControllerService


@pytest.fixture
def client(web, monkeypatch):
    monkeypatch.setattr(ControllerService, "disk_health", lambda self: [
        {"path": "/", "total_b": 8 << 30, "free_b": 1 << 30, "free_inodes": 0, "total_inodes": 0, "level": "low", "reason": "bytes"}])
    return web()


def _main(c, path):
    r = c.get(path, headers={"Host": "127.0.0.1"})
    assert r.status_code == 200, (path, r.status_code)
    doc = parse(r.get_data(as_text=True))
    main = [m for m in doc.find("main") if m.has_class("wrap")][0]
    return doc, main


def _top(doc, main):
    """The direct children of <main>, in order, as (tag, classes, element)."""
    out, end = [], main.index
    for e in doc._els[main.index + 1: main.end + 1]:
        if e.index > end:
            out.append(e)
            end = e.end
    return out


@pytest.mark.parametrize("path, crumbs, second", [
    ("/", ["All menus", "Home"], None),
    ("/stacks", ["All menus", "Home", "Apps"], None),
    ("/stacks/menus", ["Home", "Apps", "All menus"], None),
    ("/hardware", None, "‹ show in Stacks"),
    ("/stacks/chat/menu/stack-settings-chat?radio=r1", None, "‹ show in Stacks"),
    ("/webserver/logs", None, "‹ show in Stacks"),
    ("/hardware/add", None, "‹ The radios"),
    ("/hardware/radio/r1", None, "‹ The radios"),
    ("/hardware/remove/r1", None, "‹ Back"),
    ("/dependencies", ["All menus", "Home", "Apps"], "‹ Back"),
    ("/auto-install", ["All menus", "Home", "Apps"], "‹ Back"),
    ("/logs/loraham-daemon?job=start-loraham-daemon-433-r1.log", None, "‹ Back"),
])
def test_the_nav_lines_come_first(client, path, crumbs, second):
    doc, main = _main(client, path)
    top = _top(doc, main)
    assert top[0].tag == "div" and top[0].has_class("crumbline"), (path, [(e.tag, e.classes) for e in top[:3]])
    line = doc.within(top[0])
    words = [e.text.strip() for e in sorted(line.find("a") + line.find("h2"), key=lambda e: e.index) if e.text.strip()]
    if crumbs:
        assert words == crumbs, (path, words)
    if path != "/":
        assert line.find("a", href="/"), path                                     # a Home link
    else:
        assert not line.find("a", href="/") and line.find("h2", class_="here")[0].text == "Home"
    nxt = top[1]
    if second:
        assert nxt.tag == "p" and nxt.has_class("pagebar") and doc.within(nxt).text.strip() == second, (path, nxt.classes)
        a = doc.within(nxt).find("a")[0]
        assert a.has_attr("data-back") == (second == "‹ Back") and (second != "‹ Back" or a["href"] == "/"), path
        nxt = top[2]
    else:
        assert not (nxt.tag == "p" and nxt.has_class("pagebar")), path
    backs = [a for a in doc.find("a") if a.has_attr("data-back")]                 # never both lines on one page
    assert len(backs) == (second == "‹ Back"), (path, len(backs))
    notice = doc.by_id("disk-notice")
    assert notice is not None and notice.index >= nxt.index, path                 # every notice after the nav lines


def test_a_page_with_no_place_gets_crumbs_with_its_title_and_back(client):
    r = client.get("/no-such-page", headers={"Host": "127.0.0.1"})
    assert r.status_code == 404
    doc = parse(r.get_data(as_text=True))
    main = [m for m in doc.find("main") if m.has_class("wrap")][0]
    top = _top(doc, main)
    line = doc.within(top[0])
    assert top[0].has_class("crumbline") and line.find("a", href="/") and line.find("a", href="/stacks")
    assert line.find("span", class_="here")[0].text == "Error 404"
    assert top[1].has_class("pagebar") and doc.within(top[1]).find("a")[0].has_attr("data-back")
    assert doc.by_id("disk-notice").index > top[1].index


def test_exactly_the_pages_with_no_place_use_the_default_nav():
    import pathlib

    import lhpc.adapters.web as webpkg
    tdir = pathlib.Path(webpkg.__file__).parent / "templates"
    default = {p.name for p in tdir.glob("*.html") if '{% extends "base.html" %}' in p.read_text() and "block nav" not in p.read_text()}
    assert default == {"confirm.html", "confirm_radio.html", "kill_confirm.html", "power_confirm.html", "network_confirm.html",
                       "webserver_replace_confirm.html", "auto_install_confirm.html", "error.html", "updating.html", "hmac_apply.html"}

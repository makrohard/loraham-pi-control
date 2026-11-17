"""WEBSERVER-RENAME (plan v2): LHPC's own webserver entries and every stack's proxy entry carry their new names,
the same in the menu tree and in the panel headings, and every menu path the docs write names entries that exist."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import radios_fixture
import repo_paths
from htmlq import parse

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_RADIOS = (radios_fixture.R1, radios_fixture.R2, radios_fixture.R3)      # r1, r2 on 433, r3 on 868


def _client(web, tmp_path):
    radios_fixture.write(tmp_path, _RADIOS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return web(service_factory=lambda: svc), svc


class _Tree(HTMLParser):
    """The menu tree page's levels: (owner, level id) -> its path below Apps (the summaries above it, then its own
    label). A level with levels below is a <details>; its own link reads "<label> page ›"."""

    def __init__(self):
        super().__init__()
        self.uls, self.path, self.levels, self._summary, self._a = [], [], {}, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "ul":
            self.uls.append("navtree" in (dict(attrs).get("class") or ""))
        elif any(self.uls) and tag == "summary":
            self._summary = ""
        elif any(self.uls) and tag == "a":
            self._a = [dict(attrs).get("href", ""), ""]

    def handle_data(self, data):
        if self._summary is not None:
            self._summary += data
        elif self._a is not None:
            self._a[1] += data

    def handle_endtag(self, tag):
        if tag == "ul" and self.uls:
            self.uls.pop()
        elif tag == "summary" and self._summary is not None:
            self.path.append(" ".join(self._summary.split()))
            self._summary = None
        elif tag == "details" and any(self.uls):
            self.path.pop()
        elif tag == "a" and self._a is not None:
            (href, text), self._a = self._a, None
            m = re.fullmatch(r"/stacks/([^/]+)/menu/([^?]+)", href)
            if m:
                text = " ".join(text.split())
                own = () if text.endswith(" page ›") else (text,)
                self.levels[(m.group(1), m.group(2))] = tuple(self.path) + own


def _levels(c) -> dict:
    """(owner, level id) -> the level's path below Apps, read from the one tree page (the public path)."""
    tree = _Tree()
    tree.feed(c.get("/stacks/menus").get_data(as_text=True))
    return tree.levels


def test_lhpcs_own_webserver_entries_carry_their_new_names(tmp_path, web):
    # "Webserver (HTTPS / mTLS)" is "Webserver and mTLS proxy"; its children "LHPC Webserver" (was LHPC WebGUI),
    # "Stacks Web Proxies" (was Stacks WebGUIs, not moved), "Monitor", "Certificates". red on c968c785: the old names
    c, _svc = _client(web, tmp_path)
    ctl = {mid: path for (owner, mid), path in _levels(c).items() if owner == "controller"}
    top = ctl["webserver-row"]
    assert top[1:] == ("Webserver and mTLS proxy",), top
    kids = ("webserver-row--lhpc-webgui", "ws-stackwebs", "webserver-row--monitor", "webserver-row--certificates")
    assert [ctl[k] for k in kids] == [top + (name,) for name in
                                      ("LHPC Webserver", "Stacks Web Proxies", "Monitor", "Certificates")]


_HEADINGS = {"graywolf@r1": "Proxy · Graywolf UI · r1", "meshtastic@r2": "Proxy · Meshtastic UI · r2",
             "meshcom@r1": "Proxy · MeshCom UI · r1", "meshcom@r2": "Proxy · MeshCom UI · r2",
             "meshcore@r3": "Proxy · MeshCore UI · r3", "meshcore-meshcore-node@r3": "Proxy · openHop repeater · r3",
             "reticulum@r1": "Proxy · Reticulum UI"}


def test_every_stack_proxy_entry_names_its_web_ui_and_radio_in_the_tree_and_its_heading(tmp_path, web):
    # Each page's entry is "Proxy · <its web UI> · rN" (no radio part for reticulum, whose one page serves whichever
    # radio it runs on); its panel heading reads the same, so two radios' pages no longer look alike. red on
    # c968c785: "<page> · <stack>@rN"
    c, svc = _client(web, tmp_path)
    levels = _levels(c)
    got = {}
    for stk in svc.stacks():
        for page in svc.stack_web_pages(stk.id):
            label = levels[(stk.id, f"stack-webserver-{page.page_id}")][-1]
            rid = page.page_id.partition("@")[2]
            doc = parse(c.get(f"/stacks?open={stk.id}&radio={rid}").get_data(as_text=True))
            summary = " ".join(doc.within(doc.by_id(f"stack-webserver-{page.page_id}")).find("summary")[0].text.split())
            assert summary == label or summary.startswith(label + " "), (page.page_id, label, summary)
            got[page.page_id] = label
    assert {k: got.get(k) for k in _HEADINGS} == _HEADINGS, got


def test_every_shipped_web_interface_has_its_proxy_name(tmp_path):
    # the short name is the manifest's, one per web interface (data, not code)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    unnamed = [(stk.id, comp.id) for stk in svc.stacks() for comp in stk.components for ep in comp.endpoints
               if getattr(ep, "client", False) and ep.scheme in ("http", "https") and not ep.proxy_name]
    assert unnamed == []


_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")


def _doc_files():
    return [repo_paths.REPO / "README.md", repo_paths.REPO / "README.de.md",
            *sorted((repo_paths.REPO / "docs").glob("*.md"))]


def test_every_menu_path_the_docs_write_names_entries_that_exist(tmp_path, web):
    # A bold path "Apps → LoRaHAM Pi Control → A → B …", "… → A" or "Webserver… → A" (README EN + DE, docs/*.md)
    # starts with entries that exist, each below the one before (Apps: from the controller row; "…" or a bare entry:
    # anywhere in the tree); what follows the entries is the panel's action (a button). A renamed entry the docs
    # still name fails here.
    c, _svc = _client(web, tmp_path)
    paths = set(_levels(c).values())
    bad = []
    for f in _doc_files():
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for span in _BOLD.findall(line):
                parts = [x.strip() for x in span.split("→")]
                if len(parts) < 2:
                    continue
                if parts[0] == "Apps":
                    parts, anchored = parts[1:], True
                elif parts[0] == "…":
                    parts, anchored = parts[1:], False
                elif parts[0].startswith("Webserver"):
                    anchored = False
                else:
                    continue                                                  # not a menu path
                k = max((i for i in range(1, len(parts) + 1)
                         if any((p[:i] if anchored else p[-i:]) == tuple(parts[:i]) for p in paths)), default=0)
                if k < (2 if anchored else 1):                                 # Apps: the row and one entry at least
                    bad.append(f"{f.relative_to(repo_paths.REPO)}:{n}: {span}")
    assert bad == []

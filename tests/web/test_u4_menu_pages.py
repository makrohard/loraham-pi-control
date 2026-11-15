"""U4 (W423, PLAN-UI-v4 K3): every Stacks submenu level can open on its own page ("page ›", at the
very right after "logs"), with the breadcrumb and the SAME partial as the expanded view; a tree page
lists every level. A form sent from a level's page returns there."""

from __future__ import annotations

import re

import pytest
from htmlq import parse
from lhpc.adapters.web import submenus
from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")
_OPEN = re.compile(r"\sopen(?=[\s>]|$)")


def _client(web, tmp_path, radios=False):
    """A fake box; `radios`: a two-radio hardware list with a live daemon on both radios (the
    per-radio Settings levels and the stacks' Webserver pages exist only then)."""
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


def _block(html, mid):
    """The <details id="mid"> element of `html`, as rendered (its balanced markup)."""
    m = re.search(r'<details\b[^>]*\bid="' + re.escape(mid) + r'"[^>]*>', html)
    assert m, f"no <details id={mid!r}>"
    depth = 0
    for t in re.finditer(r"<details\b|</details>", html[m.start():]):
        depth += -1 if t.group(0) == "</details>" else 1
        if depth == 0:
            return html[m.start(): m.start() + t.end()]
    raise AssertionError("unbalanced <details>")


def _tag(block):
    return block.split(">", 1)[0]


def _comparable(block):
    """A level's markup without what its own page changes: ` open` on its tag, the forms' `return`,
    and a radio switch's link (it stays on the page; that rule is tested on its own)."""
    block = re.sub(r'<input type="hidden" name="return" value="[^"]*">', "", block)
    block = re.sub(r'href="/stacks(?:/[^/"]+/menu/[^"?]+)?\?radio=(r\d+)[^"]*"', r'href="SWITCH \1"', block)
    head, rest = block.split(">", 1)
    return _OPEN.sub("", head, count=1) + ">" + rest


def _owners(svc):
    return ["controller"] + [s.id for s in svc.stacks()]


def _expanded(c, owner):
    """The owner's levels as the Stacks page shows them: the controller row on /stacks, a stack's
    body as its row loads it (no query: nothing forced open)."""
    return _block(_html(c, "/stacks"), "controller-row") if owner == "controller" else _html(c, f"/stacks/{owner}/body")


# U5 (rule 21): a level whose page is an existing route; its "page ›" and its tree entry link that route
_OWN_PAGES = {"/hardware": ("daemon", "stack-hardware-daemon")}


def _href(owner, mid):
    """The link a level's "page ›" and tree entry carry."""
    return next((url for url, level in _OWN_PAGES.items() if level == (owner, mid)), f"/stacks/{owner}/menu/{mid}")


def _tree(c):
    """owner -> its levels, read through the tree page's links (the public path)."""
    out = {}
    doc = parse(_html(c, "/stacks/menus"))
    for a in doc.within(doc.find("div", class_="card")[0]).find("a"):
        m = re.fullmatch(r"/stacks/([^/]+)/menu/([^?]+)", a["href"])
        owner, mid = (m.group(1), m.group(2)) if m else _OWN_PAGES.get(a["href"], (None, None))
        if owner:
            out.setdefault(owner, set()).add(mid)
    return out


def _menus(c, owner):
    return _tree(c).get(owner, set())


def _slot(doc, block):
    """The result banners U1's slot renders inside the level `block` (E3-1: the block holding the form)."""
    return doc.within(doc.by_id(block)).find("div", class_="panel-result")


def _crumbs(doc):
    return doc.find("nav", class_="crumbs")[0]


# ---- red on the base (085034cf): no "page ›", no page for a nested level ------------------------

def test_every_row_and_submenu_has_a_page_link_right_of_logs(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks?open=meshcore"))
    hrefs = [a["href"] for a in doc.find("a", class_="pagelink")]
    assert "/stacks/controller/menu/controller-row" in hrefs
    assert "/stacks/meshcore/menu/stackrow-meshcore" in hrefs
    assert "/stacks/controller/menu/firewall-apply--other-commands" in hrefs
    # each top row's overlay: "logs" first, then "page ›" at the very right
    for owner, row in (("controller", "controller-row"), ("meshcore", "stackrow-meshcore")):
        overlay = [d for d in doc.find("div", class_="row-actions")
                   if any(a["href"] == f"/stacks/{owner}/menu/{row}" for a in doc.within(d).find("a"))]
        assert len(overlay) == 1, row
        links = doc.within(overlay[0]).find("a")
        assert links[-1].text == "page ›" and links[-2].has_class("logslink"), row


def test_firewall_other_commands_opens_on_its_own_page(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks/controller/menu/firewall-apply--other-commands"))
    assert _crumbs(doc).text == "Home › Apps › LoRaHAM Pi Control › Firewall › Apply & commands › Other commands"
    assert doc.by_id("fw-check-cmd") is not None and doc.by_id("fw-reset-cmd") is not None


# ---- every level: one descriptor, one link, one page, the same block -----------------------------

@pytest.mark.parametrize("radios, paged", [(False, None), (True, ("daemon", "meshcore"))],
                         ids=["no-radios", "two-radios"])
def test_each_level_has_one_link_and_its_page_shows_the_same_block(tmp_path, web, radios, paged):
    """Every owner: its rendered levels are its descriptors, each has one "page ›" link, and each
    page shows the level's own block. With radios, the pages of the owners with radio levels (the
    daemon's per-radio Settings, MeshCore's per-radio Webserver pages; the other stacks' are the
    same partials)."""
    c, svc = _client(web, tmp_path, radios)
    tree = _tree(c)
    for owner in _owners(svc):
        shown = _expanded(c, owner)
        ids = re.findall(r'<details\b[^>]*\bid="([^"]+)"', shown)
        assert not re.search(r"<details(?![^>]*\bid=)[^>]*>", shown), f"{owner}: a level without an id"
        menus = tree[owner]
        top = "controller-row" if owner == "controller" else f"stackrow-{owner}"
        assert set(ids) | {top} == menus, owner                         # descriptors == rendered levels
        links = [a["href"] for a in parse(shown).find("a", class_="pagelink")]
        assert sorted(links) == sorted(_href(owner, m) for m in menus - {top}), owner
        for mid in (menus - {top}) if paged is None or owner in paged else ():
            if _href(owner, mid) != f"/stacks/{owner}/menu/{mid}":
                continue                     # its page is that route (test_a_level_with_an_own_page_has_one_page)
            page = _block(_html(c, f"/stacks/{owner}/menu/{mid}"), mid)
            assert _OPEN.search(_tag(page)), (owner, mid)
            assert _comparable(page) == _comparable(_block(shown, mid)), (owner, mid)


def test_a_level_with_an_own_page_has_one_page(tmp_path, web):
    # U5 (rule 21): the daemon row's Hardware submenu has /hardware as its page: its "page ›" and its tree entry
    # link it, and its level URL answers 302 there (one block, one page).
    c, _svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks?open=daemon"))
    wrap = [d for d in doc.find("div", class_="stackrow-wrap") if doc.within(d).present("stack-hardware-daemon")]
    assert [a["href"] for a in doc.within(wrap[-1]).find("a", class_="pagelink")] == ["/hardware"]   # innermost
    tree = parse(_html(c, "/stacks/menus"))
    assert [a["href"] for a in tree.find("a") if a.text == "Hardware"] == ["/hardware"]
    assert "/stacks/daemon/menu/stack-install-daemon" in [a["href"] for a in tree.find("a")]    # anchor: a sibling
    r = c.get("/stacks/daemon/menu/stack-hardware-daemon")
    assert r.status_code == 302 and r.headers["Location"].removeprefix("http://localhost") == "/hardware"
    assert c.get("/stacks/meshcore/menu/stack-hardware-daemon").status_code == 404          # still the owner's only


def test_a_stack_row_page_renders_its_body_inline_and_opened(tmp_path, web):
    c, svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks/kiss/menu/stackrow-kiss"))
    assert _OPEN.search(_tag(_block(_html(c, "/stacks/kiss/menu/stackrow-kiss"), "stackrow-kiss")))
    assert not doc.find("div", class_="lazy-body") and doc.by_id("stack-install-kiss") is not None
    assert _crumbs(doc).text == "Home › Apps › " + svc.stack("kiss").name


def test_the_page_bar_leads_back_to_the_stacks_row_its_logs_and_the_tree(tmp_path, web):
    c, svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks/meshcore/menu/stack-install-meshcore--dependencies"))
    bar = [a["href"] for a in doc.within(doc.find("p", class_="pagebar")[0]).find("a")]
    assert bar == ["/stacks?open=meshcore#stack-install-meshcore--dependencies",
                   "/logs/" + svc.stack("meshcore").main, "/stacks/menus"]       # the row's own logs link
    crumbs = [a["href"] for a in doc.within(_crumbs(doc)).find("a")]
    assert crumbs[2:] == ["/stacks/meshcore/menu/stackrow-meshcore", "/stacks/meshcore/menu/stack-install-meshcore"]


# ---- the owner is part of the page's identity ----------------------------------------------------

def test_the_same_level_under_two_stacks_shows_each_its_own_stack(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for sid in ("meshcore", "meshcom"):
        assert "comp-loraham-daemon" in _menus(c, sid), sid
        doc = parse(_html(c, f"/stacks/{sid}/menu/comp-loraham-daemon"))
        assert doc.within(_crumbs(doc)).find("a")[2]["href"] == f"/stacks/{sid}/menu/stackrow-{sid}"
        assert doc.within(doc.find("p", class_="pagebar")[0]).find("a")[0]["href"] == f"/stacks?open={sid}#comp-loraham-daemon"


@pytest.mark.parametrize("path", [
    "/stacks/nope/menu/stackrow-nope",                        # unknown owner
    "/stacks/meshcore/menu/no-such-level",                    # unknown id
    "/stacks/meshtastic/menu/comp-meshcore-webui",            # another stack's component
    "/stacks/controller/menu/stackrow-meshcore",              # a stack's row under the controller
    "/stacks/meshcore/menu/firewall-row",                     # a controller level under a stack
])
def test_an_unknown_or_mismatched_pair_is_404(tmp_path, web, path):
    c, _svc = _client(web, tmp_path)
    assert c.get(path).status_code == 404


def test_a_radio_switch_on_a_page_stays_on_that_page(tmp_path, web):
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/daemon/menu/stack-settings-daemon"
    doc = parse(_html(c, page + "?radio=r2"))
    switch = sorted(a["href"] for a in doc.find("a") if a["href"].startswith(page + "?radio="))
    assert switch == [page + "?radio=r1", page + "?radio=r2"]
    assert doc.within(_crumbs(doc)).find("a")[2]["href"] == "/stacks/daemon/menu/stackrow-daemon?radio=r2"


# ---- a form sent from a page returns there --------------------------------------------------------

def test_a_form_on_a_page_carries_its_return_and_the_stacks_page_none(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    page = parse(_html(c, "/stacks/controller/menu/operator-callsign"))
    assert [i["value"] for i in page.find("input", name="return")] == ["/stacks/controller/menu/operator-callsign"]
    assert not parse(_html(c, "/stacks?open=meshcore")).find("input", name="return")


def _post(c, csrf, path, ret, src, **form):
    """POST `form` to `path` with the CSRF token of page `src` and `return=ret`."""
    return c.post(path, data={"_csrf": csrf(c, src), "return": ret, **form})


def test_a_post_from_a_page_returns_to_it_with_its_anchor(tmp_path, web, csrf):
    c, _svc = _client(web, tmp_path)
    page = "/stacks/controller/menu/operator-callsign"
    r = _post(c, csrf, "/operator/callsign", page, page, callsign="G0ABC")
    assert r.status_code in (302, 303) and r.headers["Location"] == page + "#operator-callsign"
    assert parse(_html(c, page)).find("input", name="callsign")[0]["value"] == "G0ABC"


def test_a_nested_component_form_posts_from_its_page_and_returns_there(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    page = "/stacks/meshcore/menu/comp-meshcore-webui"
    doc = parse(_html(c, page))
    form = [f for f in doc.find("form") if f["action"].startswith("/source-check/")][0]
    fields = {i["name"]: i["value"] for i in doc.within(form).find("input")}
    assert fields["return"] == page and fields["_csrf"]
    r = c.post(form["action"], data=fields)
    assert r.status_code in (302, 303) and r.headers["Location"].split("#")[0] == page


def test_a_refused_post_from_a_webserver_page_returns_there_with_its_message(tmp_path, web, csrf):
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/meshcore/menu/stack-webserver-meshcore@r2?radio=r2"
    r = _post(c, csrf, "/stacks/meshcore/webserver", page, page, page="meshcore@r2", mode="public",
              port="80", scheme="https", access_mode="no-auth", cidrs="", confirm_phrase="")
    assert r.status_code in (302, 303) and r.headers["Location"].split("#")[0] == page
    doc = parse(_html(c, page))
    assert _slot(doc, "stack-webserver-meshcore@r2--settings"), "the refusal shows at its form's block on that page"


@pytest.mark.parametrize("ret", [
    "https://example.org/stacks/controller/menu/operator-callsign",   # another host
    "//example.org/stacks/controller/menu/operator-callsign",         # scheme-relative
    "/stacks/controller/menu/operator-callsign#x",                    # a fragment
    "/stacks/controller/menu/no-such-level",                          # not a level
    "/stacks/meshtastic/menu/comp-meshcore-webui",                    # a mismatched pair
    "/stacks/controller/menu/operator-callsign?open=x",               # another query
    "/hardware",                                                      # not a level page
    "/stacks/controller/menu/operator-callsign\\x",                   # a backslash
])
def test_an_external_or_invalid_return_keeps_the_views_own_redirect(tmp_path, web, csrf, ret):
    c, _svc = _client(web, tmp_path)
    r = _post(c, csrf, "/operator/callsign", ret, "/stacks", callsign="G0ABC")
    assert r.headers["Location"] == "/stacks#operator-callsign"


def test_a_return_is_honoured_only_on_a_redirect_to_the_stacks_page(tmp_path, web, csrf):
    c, _svc = _client(web, tmp_path)
    r = c.post("/interactive/meshcore/dismiss",
               data={"_csrf": csrf(c), "return": "/stacks/controller/menu/operator-callsign"})
    assert r.headers["Location"] == "/"                                # the view's own redirect stays


# ---- what stays ------------------------------------------------------------------------------------

@pytest.mark.parametrize("query, mid", [
    ("open=meshcore", "stackrow-meshcore"), ("open=meshcore&cfg=meshcore", "stack-settings-meshcore"),
    ("open=meshcore&dp=meshcore", "stack-daemon-params-meshcore"), ("fw=apply", "firewall-apply"),
    ("open=firewall", "firewall-row"), ("open=meshcore&inst=meshcore", "stack-install-meshcore"),
])
def test_deep_links_still_open_their_level(tmp_path, web, query, mid):
    c, _svc = _client(web, tmp_path)
    assert _OPEN.search(_tag(_block(_html(c, "/stacks?" + query), mid))), query


def test_the_tree_page_lists_every_level_nested_without_script(tmp_path, web):
    c, svc = _client(web, tmp_path)
    body = _html(c, "/stacks/menus")
    assert _crumbs(parse(body)).text == "Home › Apps › All menus"
    tree = _tree(c)
    assert "controller-row" in tree["controller"]
    for sid in [s.id for s in svc.stacks()]:
        assert {f"stackrow-{sid}", f"stack-install-{sid}", f"stack-settings-{sid}"} <= tree[sid], sid
    tree = body[body.index("<h2>All menus</h2>"):]
    assert "<script" not in tree.split("</main>")[0] and "<details>" in tree
    assert parse(_html(c, "/stacks")).find("a", href="/stacks/menus")             # "All menus (tree) ›"


def test_descriptor_chain_and_children_follow_the_parents():
    menus = [submenus.Submenu("a", None, "A", "x"), submenus.Submenu("b", "a", "B", "x"),
             submenus.Submenu("c", "b", "C", "x"), submenus.Submenu("d", "a", "D", "x")]
    assert [m.id for m in submenus.chain(menus, "c")] == ["a", "b", "c"]
    assert submenus.chain(menus, "zz") == []
    assert [m.id for m in submenus.children(menus)["a"]] == ["b", "d"]


# ---- G2-B4 (DEC Option A): only the action's own owner, radio and branch ---------------------------

def test_a_refused_save_with_another_owners_return_falls_back_to_stacks(tmp_path, web, csrf):
    """G2-B4's example: a refused MeshCore Webserver save carrying a MeshTastic page as `return`."""
    c, _svc = _client(web, tmp_path, radios=True)
    src = "/stacks/meshcore/menu/stack-webserver-meshcore@r2?radio=r2"
    r = _post(c, csrf, "/stacks/meshcore/webserver", "/stacks/meshtastic/menu/stack-settings-meshtastic", src,
              page="meshcore@r2", mode="public", port="80", scheme="https", access_mode="no-auth",
              cidrs="", confirm_phrase="")
    # the K4 fallback: the Stacks page with the action's stack open and its radio (the view names neither)
    assert r.headers["Location"] == "/stacks?open=meshcore&radio=r2#stack-webserver-meshcore@r2"
    doc = parse(_html(c, r.headers["Location"].split("#")[0]))
    assert _slot(doc, "stack-webserver-meshcore@r2--settings")
    # seen without JS: the panel around the Settings block holding the result renders open (its sibling does not)
    assert doc.by_id("stack-webserver-meshcore@r2").has_attr("open")
    assert not doc.by_id("stack-webserver-meshcore-meshcore-node@r2").has_attr("open")


def _fields(doc, form):
    """The fields a browser sends for `form` as rendered (checked boxes, each select's first option)."""
    out = {i["name"]: i.attrs.get("value", "") for i in doc.within(form).find("input")
           if i.attrs.get("name") and (i.attrs.get("type") not in ("checkbox", "radio") or "checked" in i.attrs)}
    out.update({s["name"]: (doc.within(s).find("option") or [None])[0].attrs.get("value", "")
                for s in doc.within(form).find("select") if s.attrs.get("name") and doc.within(s).find("option")})
    return out


_NAV = {"open", "cfg", "dp", "inst", "fw", "comp", "wsg", "radio"}


def _query(url):
    q = url.split("#")[0].partition("?")[2]
    return dict(kv.partition("=")[::2] for kv in q.split("&") if kv)


def urlsplit_path(url):
    return url.split("?")[0].split("#")[0]


def _radio_set(c, csrf, ret, rid):
    return _post(c, csrf, f"/radio/{rid}/set", ret, "/stacks/daemon/menu/stack-settings-daemon?radio=" + rid,
                 key="TXMODE", value="MANAGED", confirmed="yes")


def test_a_live_setting_returns_to_its_radios_page_and_another_radio_falls_back(tmp_path, web, csrf):
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/daemon/menu/stack-settings-daemon--r2-live?radio=r2"
    assert parse(_html(c, page)).find("form", class_="setform")         # the control is on that page
    assert _radio_set(c, csrf, page, "r2").headers["Location"] == page + "#stack-settings-daemon--r2-live"
    other = "/stacks/daemon/menu/stack-settings-daemon--r1-live?radio=r1"
    assert _radio_set(c, csrf, other, "r2").headers["Location"] == "/stacks?cfg=daemon&radio=r2#stack-settings-daemon"


def test_a_sibling_level_returns_to_the_nearest_ancestor_that_holds_the_form(tmp_path, web, csrf):
    """E3-1: the Settings reset sent with Install (a sibling) as `return` comes back to the
    stack's row page, the nearest level above Install that holds the reset form."""
    c, _svc = _client(web, tmp_path)
    own, sibling = "/stacks/meshcore/menu/stack-settings-meshcore", "/stacks/meshcore/menu/stack-install-meshcore"
    doc = parse(_html(c, own))
    form = [f for f in doc.find("form") if f["action"] == "/stacks/meshcore/config/reset"][0]
    fields = _fields(doc, form)
    radio = f"?radio={fields['radio']}" if fields.get("radio") else ""     # the radio the form saves (named back)
    r = c.post(form["action"], data=fields)
    assert r.headers["Location"] == own + radio + "#stack-settings-meshcore"
    r = c.post(form["action"], data={**fields, "return": sibling})
    assert r.headers["Location"] == "/stacks/meshcore/menu/stackrow-meshcore" + radio + "#stackrow-meshcore"


@pytest.mark.parametrize("page, action, form", [
    ("/stacks/controller/menu/firewall-row--configure", "/firewall/configure", {"recommended": "yes"}),
    ("/stacks/controller/menu/firewall-row--configure", "/firewall/refresh", {}),
    ("/stacks/controller/menu/webserver-row--certificates", "/webserver/init", {}),
])
def test_a_level_below_its_actions_anchor_returns_to_itself(tmp_path, web, csrf, page, action, form):
    """Firewall › Configure and Webserver › Certificates: their views anchor the whole panel
    (#firewall-row, #webserver-row), which contains the page; the POST comes back to the page."""
    c, _svc = _client(web, tmp_path)
    r = _post(c, csrf, action, page, page, **form)
    assert r.status_code in (302, 303) and r.headers["Location"].split("#")[0] == page


# ---- G2-N3: every control of the named levels, through the forms their pages render ---------------

_MATRIX = [
    (False, "/stacks/controller/menu/webserver-row--certificates"),       # issue / revoke / renew / init
    (False, "/stacks/controller/menu/webserver-row--lhpc-webgui"),        # Apply / Reset
    (False, "/stacks/controller/menu/webserver-row--monitor"),            # Verify
    (False, "/stacks/controller/menu/firewall-row--configure"),           # Save / recommended / Refresh
    (False, "/stacks/controller/menu/ws-stackwebs"),                      # Apply to all
    (False, "/stacks/controller/menu/operator-callsign"),
    (False, "/stacks/meshcore/menu/comp-meshcore-webui"),                 # a nested component's buttons
    (True, "/stacks/meshcore/menu/stack-webserver-meshcore@r2--settings?radio=r2"),   # per-page proxy Apply
    (True, "/stacks/daemon/menu/stack-settings-daemon?radio=r1"),
]


@pytest.mark.parametrize("radios, page", _MATRIX, ids=[p.split("/menu/")[1] for _r, p in _MATRIX])
def test_every_form_on_a_level_page_returns_to_it(tmp_path, web, radios, page):
    """Each POST form of the page, sent with the fields the page rendered (CSRF and `return`
    included): a redirect to the Stacks page comes back to this page, on success and on refusal; a
    confirm page it renders carries the `return` on; a log or job page is not the Stacks page."""
    c, _svc = _client(web, tmp_path, radios)
    doc = parse(_html(c, page))
    forms = [f for f in doc.find("form") if (f.attrs.get("method") or "").lower() == "post"]
    assert forms, page
    for f in forms:
        fields = _fields(doc, f)
        assert fields.get("return") == page and fields.get("_csrf"), (page, f["action"])
        r = c.post(f["action"], data=fields)
        if r.status_code in (302, 303) and set(_query(r.headers["Location"])) - _NAV:
            assert urlsplit_path(r.headers["Location"]) == "/stacks", (page, f["action"])   # one-time data: Stacks
        elif r.status_code in (302, 303) and urlsplit_path(r.headers["Location"]) in ("/stacks", page.split("?")[0]):
            back = r.headers["Location"].split("#")[0]
            path, query = urlsplit_path(back), _query(back)
            assert path == page.split("?")[0], (page, f["action"], back)
            assert query == _query(page) or (not _query(page) and query in ({}, {"radio": "r1"}, {"radio": "r2"})), \
                (page, f["action"], back)                            # a page without a radio gets the action's
        elif r.status_code == 200 and "_csrf" in r.get_data(as_text=True):
            assert f'name="return" value="{page.replace("&", "&amp;")}"' in r.get_data(as_text=True), (page, f["action"])
        else:
            assert r.status_code in (200, 302, 303), (page, f["action"], r.status_code)


def test_a_certificate_issue_keeps_the_stacks_page_with_its_one_time_passphrase(tmp_path, web, csrf):
    """The issue's redirect carries `pw=` (the passphrase the Stacks page shows once): it is kept,
    and the passphrase shows there; the .p12 fetch shows on the Certificates page for a local viewer."""
    c, _svc = _client(web, tmp_path)
    page = "/stacks/controller/menu/webserver-row--certificates"
    assert _post(c, csrf, "/webserver/init", page, page).headers["Location"] == page + "#webserver-row--certificates"
    r = _post(c, csrf, "/webserver/cert", page, page, op="issue", label="u4test")
    assert urlsplit_path(r.headers["Location"]) == "/stacks" and "pw" in _query(r.headers["Location"])
    assert parse(_html(c, r.headers["Location"].split("#")[0])).by_id("one-time-passphrase") is not None
    body = _html(c, page)
    assert "u4test" in body and ".p12" in body


def test_a_successful_revoke_returns_to_certificates_though_its_form_is_gone(tmp_path, web, csrf):
    """G1 round 1 B2: the Revoke form exists only while the certificate is active; after the revoke
    the POST still comes back to the Certificates page (its form is matched on the page as it was before the
    action)."""
    c, _svc = _client(web, tmp_path)
    page = "/stacks/controller/menu/webserver-row--certificates"
    _post(c, csrf, "/webserver/init", page, page)
    _post(c, csrf, "/webserver/cert", page, page, op="issue", label="u4rev")
    doc = parse(_html(c, page))
    form = [f for f in doc.find("form") if doc.within(f).find("input", name="label")
            and doc.within(f).find("input", name="label")[0]["value"] == "u4rev"
            and doc.within(f).find("input", name="op")[0]["value"] == "revoke"][0]
    r = c.post(form["action"], data={**_fields(doc, form), "confirm_phrase": "u4rev"})
    assert r.headers["Location"] == page + "#webserver-row--certificates"
    after = parse(_html(c, page))
    assert not [f for f in after.find("form") if after.within(f).find("input", name="label")
                and after.within(f).find("input", name="label")[0]["value"] == "u4rev"], "still revocable"
    assert after.find("p", class_="flash"), "the result shows on the page"



def test_a_shared_components_action_is_its_own_stacks_and_falls_back(tmp_path, web):
    """`comp-loraham-daemon` is a level of MeshCore too, but its source check is the daemon's
    action (its redirect opens the daemon's Install): from MeshCore's page it falls back."""
    c, _svc = _client(web, tmp_path)
    page = "/stacks/meshcore/menu/comp-loraham-daemon"
    doc = parse(_html(c, page))
    form = [f for f in doc.find("form") if f["action"] == "/source-check/loraham-daemon"][0]
    r = c.post(form["action"], data=_fields(doc, form))
    assert r.headers["Location"] == "/stacks?open=daemon&inst=daemon#stack-install-daemon"


# ---- E3-1: the block that holds the form, or its nearest ancestor --------------------------------

_REFUSED_PROXY = {"page": "meshcore@r2", "mode": "public", "port": "80", "scheme": "https",
                  "access_mode": "no-auth", "cidrs": "", "confirm_phrase": ""}


def test_a_refused_apply_from_the_standalone_settings_child_returns_there(tmp_path, web, csrf):
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/meshcore/menu/stack-webserver-meshcore@r2--settings?radio=r2"
    r = _post(c, csrf, "/stacks/meshcore/webserver", page, page, **_REFUSED_PROXY)
    assert r.headers["Location"] == page + "#stack-webserver-meshcore@r2--settings"
    assert _slot(parse(_html(c, page)), "stack-webserver-meshcore@r2--settings"), "the refusal shows in that block"


def test_a_return_to_the_monitor_child_goes_to_the_panel_that_holds_the_settings(tmp_path, web, csrf):
    """The Monitor child has no form of the Settings Apply: its nearest ancestor holding it, the
    instance's Webserver panel, is the page returned to."""
    c, _svc = _client(web, tmp_path, radios=True)
    monitor = "/stacks/meshcore/menu/stack-webserver-meshcore@r2--monitor?radio=r2"
    r = _post(c, csrf, "/stacks/meshcore/webserver", monitor, "/stacks/meshcore/menu/stack-webserver-meshcore@r2?radio=r2",
              **_REFUSED_PROXY)
    assert r.headers["Location"] == ("/stacks/meshcore/menu/stack-webserver-meshcore@r2?radio=r2"
                                     "#stack-webserver-meshcore@r2")


def test_an_instance_action_returns_only_to_its_own_radios_page(tmp_path, web):
    """The radio a person picks in a Run/Stop selector is not a hidden field: the action's radio
    (its redirect's `radio`) decides. Picked r2 on the r1 page -> the Stacks fallback; on the r2
    page -> back to that page."""
    c, _svc = _client(web, tmp_path, radios=True)
    for rid, back_here in (("r1", False), ("r2", True)):
        page = f"/stacks/graywolf/menu/stackrow-graywolf?radio={rid}"
        doc = parse(_html(c, page))
        form = [f for f in doc.find("form") if f["action"] == "/action"
                and any(o.attrs.get("value", "").endswith("@r2") for o in doc.within(f).find("option"))
                and doc.within(f).find("input", name="op")[0]["value"] == "stop"][0]
        fields = {**_fields(doc, form), "target": [o["value"] for o in doc.within(form).find("option")
                                                    if o["value"].endswith("@r2")][0]}
        confirm = parse(c.post("/action", data=fields).get_data(as_text=True))     # Stop asks first
        step = [f for f in confirm.find("form") if f["action"] == "/action"][0]
        assert _fields(confirm, step)["return"] == page                        # the confirm page carries it on
        loc = c.post("/action", data=_fields(confirm, step)).headers["Location"]
        assert loc == (page + "#stackrow-graywolf" if back_here else "/stacks?open=graywolf&radio=r2#stackrow-graywolf"), (rid, loc)



def test_a_malformed_return_never_breaks_a_post(tmp_path, web, csrf):
    """Gate 1 (G1-UI-U5REST NOTE 1): urlsplit refuses `//[` (ValueError). A POST whose confirm page renders the
    `return` on answers that page without it (it names no page), and a redirecting POST keeps its view's target."""
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/graywolf/menu/stackrow-graywolf?radio=r1"
    doc = parse(_html(c, page))
    form = [f for f in doc.find("form") if f["action"] == "/action"
            and doc.within(f).find("input", name="op")[0]["value"] == "stop"][0]
    r = c.post("/action", data={**_fields(doc, form), "return": "//["})              # Stop asks first
    assert r.status_code == 200, r.status_code
    confirm = parse(r.get_data(as_text=True))
    step = [f for f in confirm.find("form") if f["action"] == "/action"][0]
    assert "return" not in _fields(confirm, step) and _fields(confirm, step).get("_csrf")
    r = _post(c, csrf, "/operator/callsign", "//[", page, callsign="G0ABC")
    assert r.status_code in (302, 303) and urlsplit_path(r.headers["Location"]) == "/stacks", r.status_code


def test_a_page_link_keeps_the_radio_the_view_shows_and_edits_that_radio(tmp_path, web):
    """G1 round 1 B1: the daemon Settings shown for r2 on the Stacks page; its "page ›" opens the
    r2 page, whose live setting posts to r2 and returns to that page."""
    c, _svc = _client(web, tmp_path, radios=True)
    doc = parse(_html(c, "/stacks?open=daemon&cfg=daemon&radio=r2"))
    link = [a["href"] for a in doc.find("a", class_="pagelink")
            if a["href"].split("?")[0] == "/stacks/daemon/menu/stack-settings-daemon"]
    assert link == ["/stacks/daemon/menu/stack-settings-daemon?radio=r2"]
    page = parse(_html(c, link[0]))
    live = [f for f in page.find("form", class_="setform")]
    assert live and {f["action"] for f in live} == {"/radio/r2/set"}, "the page shows r2's live settings"
    form = live[0]
    confirm = parse(c.post(form["action"], data=_fields(page, form)).get_data(as_text=True))
    step = [f for f in confirm.find("form") if f["action"] == "/radio/r2/set"][0]
    r = c.post(step["action"], data=_fields(confirm, step))
    assert r.headers["Location"] == link[0] + "#stack-settings-daemon"



def test_a_radio_less_page_returns_with_the_radio_the_action_ran_on(tmp_path, web):
    """ERRATA-UI-v5-1 (3): a page without a radio showed its default instance; an instance action
    picked there comes back to that page with the action's radio named."""
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/graywolf/menu/stackrow-graywolf"
    doc = parse(_html(c, page))
    form = [f for f in doc.find("form") if f["action"] == "/action" and doc.within(f).find("option")
            and doc.within(f).find("input", name="op")[0]["value"] == "stop"][0]
    confirm = parse(c.post("/action", data={**_fields(doc, form), "target": "graywolf@r2"}).get_data(as_text=True))
    step = [f for f in confirm.find("form") if f["action"] == "/action"][0]
    assert c.post("/action", data=_fields(confirm, step)).headers["Location"] == page + "?radio=r2#stackrow-graywolf"


def test_a_sibling_components_form_returns_to_the_level_holding_both(tmp_path, web):
    """The posted form's hidden fields tell sibling levels apart: libch341's Update sent with the
    radiolib page as `return` comes back to Dependencies (holding both), not to radiolib's page."""
    c, _svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks/daemon/menu/comp-libch341"))
    form = [f for f in doc.find("form") if f["action"] == "/action" and doc.within(f).find("input", name="op")
            and doc.within(f).find("input", name="op")[0]["value"] == "update"][0]
    sent = {**_fields(doc, form), "return": "/stacks/daemon/menu/comp-radiolib"}
    r = c.post("/action", data=sent)
    if r.status_code == 200:                                                   # through its confirm page
        confirm = parse(r.get_data(as_text=True))
        step = [f for f in confirm.find("form") if f["action"] == "/action"][0]
        r = c.post("/action", data=_fields(confirm, step))
    assert r.headers["Location"] == ("/stacks/daemon/menu/stack-install-daemon--dependencies"
                                     "#stack-install-daemon--dependencies")



def test_a_flash_pending_before_a_post_still_shows_after_it(tmp_path, web, csrf):
    """The pre-action render (which runs the sent level's partials, result slots included) reads no
    flash: one still pending before the POST shows on the page after it."""
    c, _svc = _client(web, tmp_path, radios=True)
    page = "/stacks/meshcore/menu/stack-webserver-meshcore@r2--settings?radio=r2"
    token = csrf(c, page)
    with c.session_transaction() as sess:
        sess["_flashes"] = [*sess.get("_flashes", []), ("warn", "a pending note")]
    r = c.post("/stacks/meshcore/webserver", data={"_csrf": token, "return": page, **_REFUSED_PROXY})
    assert r.headers["Location"] == page + "#stack-webserver-meshcore@r2--settings"
    doc = parse(_html(c, page))
    assert [p.text.strip() for p in doc.find("p", class_="flash")] == ["a pending note"]
    assert _slot(doc, "stack-webserver-meshcore@r2--settings")                   # and the refusal at its block


def test_the_tree_and_its_links_keep_the_pages_radio(tmp_path, web):
    c, _svc = _client(web, tmp_path, radios=True)
    page = parse(_html(c, "/stacks/daemon/menu/stack-settings-daemon?radio=r2"))
    tree_link = [a["href"] for a in page.within(page.find("p", class_="pagebar")[0]).find("a")][-1]
    assert tree_link == "/stacks/menus?radio=r2"
    links = [a["href"] for a in parse(_html(c, tree_link)).find("a") if "/menu/" in a["href"]]
    assert links and all(_query(h) == {"radio": "r2"} for h in links)
    assert "/stacks/daemon/menu/stack-settings-daemon--r2-live?radio=r2" in links     # that radio's levels


def test_a_fallback_opens_the_stack_the_views_redirect_does_not_name(tmp_path, web, csrf):
    """K4 / ERRATA-UI-v5-1: with no radio for MeshCore (a 433 MHz list) the Webserver save is refused to
    `/stacks#stackrow-meshcore` (no `open`); sent from a level page that does not hold that form,
    the fallback opens the stack, so its row shows."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    assert radio_start.setup_radios(p, "waveshare-433")[0]                     # one 433 MHz radio: MeshCore has none
    cfgmod.save_hardware_setup(p, "waveshare-433")
    svc = ControllerService(system=FakeSystem().system, paths=p)
    c = web(service_factory=lambda: svc)
    page = "/stacks/meshcore/menu/stackrow-meshcore"
    r = _post(c, csrf, "/stacks/meshcore/webserver", page, page, page="meshcore", mode="local", port="8446",
              scheme="https", access_mode="no-auth", cidrs="", confirm_phrase="")
    assert r.headers["Location"] == "/stacks?open=meshcore#stackrow-meshcore"

"""UI-REORG c9 (plan v7 s8, OPEN-22): an action result shows under the node that holds its button (page_actions'
slot, outside the <details>), not at the top; the acted level comes from the Apps page's `_level` field (checked by
the server against the owner's levels) or from a level page's form; unknown, absent or not rendered -> the top, as
before. Box-wide notices stay on top. Through the public pages."""
from __future__ import annotations

import pytest
from htmlq import parse

_VERIFY = "/webserver/verify"


def _results(c, path="/stacks"):
    doc = parse(c.get(path).get_data(as_text=True))
    return doc, [b for b in doc.find("div", class_="panel-result")], doc.find("p", class_="flash")


def _slot(doc, node_id):
    """UI-REORG c9: the result banners IN the node's own slot: directly after the .row-actions that directly
    follows the node (page_actions: </details><div class="row-actions">…</div><banner>), or, with no such overlay (a
    level's own page), directly after the node. A banner under an ancestor's slot does not count."""
    node = doc.by_id(node_id)
    overlay = [e for e in doc.find("div", class_="row-actions") if e.index == node.end + 1]
    anchor = overlay[0] if overlay else node
    return [b for b in doc.find("div", class_="panel-result") if b.index == anchor.end + 1]


@pytest.mark.parametrize("level", ["controller/webserver-row"])
def test_verify_from_the_apps_page_shows_under_its_node_and_nothing_on_top(web, csrf, level):
    # red before c9: Verify's result was a top flash (and stacks_state.js scrolled there)
    c = web()
    r = c.post(_VERIFY, data={"_csrf": csrf(c), "_level": level})
    assert r.status_code == 302
    doc, banners, top = _results(c)
    assert banners and not top, (banners, top)
    assert _slot(doc, level.split("/")[1]) == banners                        # in that node's slot, under it


@pytest.mark.parametrize("level", ["controller/no-such-level", "kiss/webserver-row--monitor", "controller", ""])
def test_an_unknown_level_shows_on_top(web, csrf, level):
    c = web()
    c.post(_VERIFY, data={"_csrf": csrf(c), **({"_level": level} if level else {})})   # no field: no JS
    _doc, banners, top = _results(c)
    assert top and not banners


def test_a_result_whose_level_is_not_rendered_shows_on_top(web, csrf):
    # a level inside a lazy row body (not rendered inline): no taker -> the top
    c = web()
    c.post(_VERIFY, data={"_csrf": csrf(c), "_level": "kiss/stack-settings-kiss"})
    _doc, banners, top = _results(c)
    assert top and not banners


def _inside_monitor(doc, banners):
    """F16: the Monitor's result banner INSIDE the Monitor node, after its Verify control, the node open."""
    node = doc.by_id("webserver-row--monitor")
    [form] = [f for f in doc.find("form") if f["action"] == _VERIFY]
    assert node.has_attr("open"), "a result opens the Monitor"
    assert len(banners) == 1, banners                                      # shown once, not again in its slot
    assert node.index < banners[0].index <= node.end and banners[0].index > form.end


def test_verify_from_the_apps_page_shows_inside_the_monitor_and_nothing_on_top(web, csrf):
    # red before F16: the Monitor's result sat in page_actions' slot, outside the node (between Monitor and Certificates)
    c = web()
    r = c.post(_VERIFY, data={"_csrf": csrf(c), "_level": "controller/webserver-row--monitor"})
    assert r.status_code == 302
    doc, banners, top = _results(c)
    assert banners and not top, (banners, top)
    _inside_monitor(doc, banners)


def test_a_result_from_a_level_page_shows_under_that_level(web, csrf):
    c = web()
    page = "/stacks/controller/menu/webserver-row--certificates"
    doc = parse(c.get(page).get_data(as_text=True))
    [form] = [f for f in doc.find("form") if f["action"] == "/webserver/init"]
    data = {i["name"]: i["value"] for i in doc.within(form).find("input") if i["type"] == "hidden"}
    r = c.post("/webserver/init", data=data)
    assert r.headers["Location"].split("#")[0] == page
    doc, banners, top = _results(c, page)
    assert banners and not top
    assert _slot(doc, "webserver-row--certificates") == banners           # directly under that level


def test_a_result_from_the_monitor_page_shows_inside_the_monitor(web, csrf):
    c = web()
    page = "/stacks/controller/menu/webserver-row--monitor"
    doc = parse(c.get(page).get_data(as_text=True))
    [form] = [f for f in doc.find("form") if f["action"] == _VERIFY]
    data = {i["name"]: i["value"] for i in doc.within(form).find("input") if i["type"] == "hidden"}
    r = c.post(_VERIFY, data=data)
    assert r.headers["Location"].split("#")[0] == page
    doc, banners, top = _results(c, page)
    assert banners and not top
    _inside_monitor(doc, banners)


def test_an_addressed_result_keeps_its_address(web, csrf):
    # K4: a bulk-proxy refusal names ws-stackwebs itself; a _level does not re-address it
    c = web()
    c.post("/webserver/stacks", data={"_csrf": csrf(c), "mode": "lan", "scheme": "https",
                                      "access_mode": "local-open-remote-auth", "_level": "controller/webserver-row"})
    doc, banners, top = _results(c)
    assert not top
    assert banners and _slot(doc, "ws-stackwebs") == banners


# ---- F27: Save configuration / Reset to defaults show their result INSIDE the Settings node, under their button row ----

def _under_cfgacts(doc, sid):
    """The result banners right after the Settings node's button row (div.cfgacts), inside details#stack-settings-<sid>."""
    node = doc.by_id(f"stack-settings-{sid}")
    [row] = [r for r in doc.within(node).find("div", class_="cfgacts")]
    return node, [b for b in doc.find("div", class_="panel-result") if b.index == row.end + 1]


def _form_fields(c, page, action):
    """The hidden fields of the form posting to `action`, as `page` renders it (CSRF, radio, return)."""
    doc = parse(c.get(page).get_data(as_text=True))
    [form] = [f for f in doc.find("form") if f["action"] == action]
    return {i["name"]: i["value"] for i in doc.within(form).find("input") if i["type"] == "hidden"}


@pytest.mark.parametrize("sid", ["kiss", "meshcore"])
@pytest.mark.parametrize("button", ["config", "config/reset"])
def test_save_and_reset_show_their_result_inside_settings_under_their_row(web, csrf, sid, button):
    # red before F27: the result sat in the Settings level's slot, after the node (or on top without JS's _level)
    c = web()
    action = f"/stacks/{sid}/{button}"
    data = {**_form_fields(c, f"/stacks?cfg={sid}", action), "_level": f"{sid}/stack-settings-{sid}"}
    r = c.post(action, data=data)
    assert r.status_code == 302, r.status_code
    doc, banners, top = _results(c, r.headers["Location"].split("#")[0])
    node, under = _under_cfgacts(doc, sid)
    assert banners and not top, (banners, top)
    assert under == banners and node.index < under[0].index <= node.end           # once, inside, under the row


def test_save_from_the_settings_page_shows_under_its_row(web, csrf):
    c = web()
    page = "/stacks/kiss/menu/stack-settings-kiss"
    r = c.post("/stacks/kiss/config", data=_form_fields(c, page, "/stacks/kiss/config"))
    back = r.headers["Location"].split("#")[0]
    assert back.split("?")[0] == page                                           # back on the level page (menu return)
    doc, banners, top = _results(c, back)
    _node, under = _under_cfgacts(doc, "kiss")
    assert banners and not top and under == banners                             # under the row, not below the page body


def test_another_settings_row_keeps_its_place(web, csrf):
    # F27 touches Save/Reset only: an RF log save (same Settings node, the level's own address) is not moved under them
    c = web()
    c.post("/stacks/graywolf/rflog", data={"_csrf": csrf(c), "value": "on", "_level": "graywolf/stack-settings-graywolf"})
    doc, banners, _top = _results(c, "/stacks?cfg=graywolf")
    _node, under = _under_cfgacts(doc, "graywolf")
    assert banners and not under, banners


def test_a_save_result_whose_settings_are_not_on_the_page_shows_on_top(web, csrf):
    c = web()
    r = c.post("/stacks/kiss/config", data=_form_fields(c, "/stacks?cfg=kiss", "/stacks/kiss/config"))
    assert r.status_code == 302
    _doc, banners, top = _results(c, "/")                                         # Home: no Settings node to take it
    assert top and not banners

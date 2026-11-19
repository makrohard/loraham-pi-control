"""UI-REORG c9 (plan v7 s8, his OPEN-22): an action result shows under the node that holds its button (page_actions'
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
    """UI-REORG c9 (a25 B1): the result banners IN the node's own slot: directly after the .row-actions that directly
    follows the node (page_actions: </details><div class="row-actions">…</div><banner>), or, with no such overlay (a
    level's own page), directly after the node. A banner under an ancestor's slot does not count."""
    node = doc.by_id(node_id)
    overlay = [e for e in doc.find("div", class_="row-actions") if e.index == node.end + 1]
    anchor = overlay[0] if overlay else node
    return [b for b in doc.find("div", class_="panel-result") if b.index == anchor.end + 1]


@pytest.mark.parametrize("level", ["controller/webserver-row--monitor", "controller/webserver-row"])
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


def test_a_result_from_a_level_page_shows_under_that_level(web, csrf):
    c = web()
    page = "/stacks/controller/menu/webserver-row--monitor"
    doc = parse(c.get(page).get_data(as_text=True))
    [form] = [f for f in doc.find("form") if f["action"] == _VERIFY]
    data = {i["name"]: i["value"] for i in doc.within(form).find("input") if i["type"] == "hidden"}
    r = c.post(_VERIFY, data=data)
    assert r.headers["Location"].split("#")[0] == page
    doc, banners, top = _results(c, page)
    assert banners and not top
    assert _slot(doc, "webserver-row--monitor") == banners                # directly under that level


def test_an_addressed_result_keeps_its_address(web, csrf):
    # K4: a bulk-proxy refusal names ws-stackwebs itself; a _level does not re-address it
    c = web()
    c.post("/webserver/stacks", data={"_csrf": csrf(c), "mode": "lan", "scheme": "https",
                                      "access_mode": "local-open-remote-auth", "_level": "controller/webserver-row"})
    doc, banners, top = _results(c)
    assert not top
    assert banners and _slot(doc, "ws-stackwebs") == banners

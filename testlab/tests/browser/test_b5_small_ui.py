"""B5 small UI fixes in a real browser over the lab server: what the rendered page looks like and does (computed
styles, boxes, the request a click sends), where a template read cannot tell."""
from __future__ import annotations


def _style(page, selector, *props):
    return page.eval_on_selector(selector, "(el, ps) => { const s = getComputedStyle(el); return ps.map(p => s[p]); }",
                                 list(props))


def test_add_hardware_is_a_button_link_that_opens_add_hardware(page):
    # A10: the link has a button's frame (border, padding, hover background) and still opens Add Hardware.
    page.goto(page.lab_base + "/hardware", wait_until="load")
    border, pad, margin, bg = _style(page, "#hw-add", "borderTopWidth", "paddingLeft", "marginLeft", "backgroundColor")
    assert border == "1px" and float(pad.rstrip("px")) > 0 and margin == "0px"
    page.hover("#hw-add")
    assert _style(page, "#hw-add", "backgroundColor")[0] != bg
    page.click("#hw-add")
    page.wait_for_url("**/hardware/add")

"""The Hardware pages in a real browser: what they look like and do (computed styles, boxes, the request a click
sends), where a template read cannot tell. The radio detail page runs on the `flask_page` fixture (a USB radio with
two ports to choose, which the lab cannot set up)."""
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



def test_re_bind_on_the_action_row_still_sends_its_form(flask_page, monkeypatch):
    # A19, G1 BLOCKING 1: the moved Re-bind submits ITS form: the chosen port, the radio and the token reach /hardware/bind.
    from urllib.parse import parse_qs
    page, posts = flask_page()
    page.check("#hw-rebind input[name=port][value='1-2']")
    page.click(".acts button:has-text('Re-bind')")
    page.wait_for_load_state("load")
    assert [p for p, _ in posts] == ["/hardware/bind"]
    form = parse_qs(posts[0][1])
    assert form["rid"] == ["r3"] and form["port"] == ["1-2"] and form["_csrf"][0]
    page.context.close()


def test_without_ports_to_choose_there_is_no_re_bind(flask_page, monkeypatch):
    page, _posts = flask_page(choices=False)
    assert page.locator("text=Re-bind").count() == 0 and page.locator(".acts button:has-text('Detect')").count() == 1
    page.context.close()


def test_the_actions_share_one_row_that_wraps_on_a_phone(flask_page, monkeypatch):
    page, _posts = flask_page()
    boxes = page.eval_on_selector_all(".acts > *", "els => els.map(e => { const b = e.getBoundingClientRect(); "
                                      "return [b.top, b.height, getComputedStyle(e).marginLeft]; })")
    assert len(boxes) == 3 and all(m == "0px" for _t, _h, m in boxes)              # Re-bind, Detect form, Remove
    centres = {round(t + h / 2) for t, h, _m in boxes}
    assert max(centres) - min(centres) <= 2                                         # one row at 1280 px
    page.set_viewport_size({"width": 360, "height": 800})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 360                                                             # wraps, no sideways scroll
    page.context.close()


def _ratio(rgb_a, rgb_b):
    def lum(rgb):
        out = []
        for v in [int(x) / 255 for x in rgb.replace("rgba(", "").replace("rgb(", "").rstrip(")").split(",")[:3]]:
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    a, b = sorted((lum(rgb_a), lum(rgb_b)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_remove_is_red_and_legible_in_both_themes(flask_page, monkeypatch):
    # A19 (his "remove botton danger color"), G1 NOTE 4: red text and border; the hover is tinted; >= 4.5:1 each.
    page, _posts = flask_page()
    for theme in ("light", "dark"):
        page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
        page.mouse.move(0, 0)
        bad = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bad').trim()")
        colour, border, bg = _style(page, "a.button.danger", "color", "borderTopColor", "backgroundColor")
        surface = page.evaluate("getComputedStyle(document.querySelector('.card')).backgroundColor")
        assert colour == border and bad, (theme, colour, border)
        assert _ratio(colour, surface) >= 4.5, (theme, colour, surface)
        page.hover("a.button.danger")
        colour_h, bg_h = _style(page, "a.button.danger", "color", "backgroundColor")
        assert bg_h != bg and _ratio(colour_h, bg_h) >= 4.5, (theme, colour_h, bg_h)
    page.context.close()


def test_a_blocked_remove_looks_disabled(flask_page, monkeypatch):
    # G1-RE NOTE B: the disabled Remove is told from an enabled control: no pointer, faded, no hover change.
    page, _posts = flask_page(activity=True)
    sel = "button.danger[disabled]"
    cursor, opacity, bg = _style(page, sel, "cursor", "opacity", "backgroundColor")
    assert cursor == "not-allowed" and float(opacity) < 1
    page.hover(sel, force=True)
    assert _style(page, sel, "backgroundColor")[0] == bg
    page.context.close()

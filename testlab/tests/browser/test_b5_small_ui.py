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


# --- A19 (#14): the radio detail page, served by the Flask app itself (a USB radio with two ports to choose) ---------

def _flask_page(browser, tmp_path, monkeypatch, *, choices=True, activity=False):
    """A real Chromium page over the console app (no lab): every request of the page is answered by a Flask test
    client on a runtime with a USB radio r3. Returns (page, posts): `posts` collects each POST the page sends."""
    from urllib.parse import urlsplit

    from lhpc.adapters.web.app import create_app
    from lhpc.core import radio_start, radios, usb_flow
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.usb_access import Probe
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r3", "mstick", "meshstick-1262", "868", usb_serial="MSTICK01"))))
    items = (usb_flow.Choice("1-1.3", "MSTICK01", True, "port 1-1.3: serial MSTICK01", ""),
             usb_flow.Choice("1-2", "MSTICK02", True, "port 1-2: serial MSTICK02", "")) if choices else ()
    monkeypatch.setattr(usb_flow, "usb_choices", lambda found, m: usb_flow.Choices(Probe.PRESENT, "", items))
    if activity:
        real = radio_start.radio_detail
        monkeypatch.setattr(radio_start, "radio_detail", lambda svc, rid: {**real(svc, rid), "activity": ["daemon"],
                                                                           "stop_first": "stop its daemon"})
    client = create_app(service_factory=lambda: ControllerService(system=FakeSystem().system,
                                                                  paths=Paths(runtime_root=tmp_path))).test_client()
    posts = []

    def handle(route, request):
        u = urlsplit(request.url)
        path = u.path + ("?" + u.query if u.query else "")
        if request.method == "POST":
            posts.append((u.path, request.post_data or ""))
            route.fulfill(status=200, body="posted")              # captured, not run
            return
        resp = client.get(path)
        route.fulfill(status=resp.status_code, body=resp.get_data(),
                      headers={k: v for k, v in resp.headers.items() if k.lower() != "content-length"})
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.route("http://127.0.0.1:9/**", handle)
    page = context.new_page()
    page.goto("http://127.0.0.1:9/hardware/radio/r3", wait_until="load")
    return page, posts


def test_re_bind_on_the_action_row_still_sends_its_form(browser, tmp_path, monkeypatch):
    # A19, G1 BLOCKING 1: the moved Re-bind submits ITS form: the chosen port, the radio and the token reach /hardware/bind.
    from urllib.parse import parse_qs
    page, posts = _flask_page(browser, tmp_path, monkeypatch)
    page.check("#hw-rebind input[name=port][value='1-2']")
    page.click(".acts button:has-text('Re-bind')")
    page.wait_for_load_state("load")
    assert [p for p, _ in posts] == ["/hardware/bind"]
    form = parse_qs(posts[0][1])
    assert form["rid"] == ["r3"] and form["port"] == ["1-2"] and form["_csrf"][0]
    page.context.close()


def test_without_ports_to_choose_there_is_no_re_bind(browser, tmp_path, monkeypatch):
    page, _posts = _flask_page(browser, tmp_path, monkeypatch, choices=False)
    assert page.locator("text=Re-bind").count() == 0 and page.locator(".acts button:has-text('Detect')").count() == 1
    page.context.close()


def test_the_actions_share_one_row_that_wraps_on_a_phone(browser, tmp_path, monkeypatch):
    page, _posts = _flask_page(browser, tmp_path, monkeypatch)
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


def test_remove_is_red_and_legible_in_both_themes(browser, tmp_path, monkeypatch):
    # A19 (his "remove botton danger color"), G1 NOTE 4: red text and border; the hover is tinted; >= 4.5:1 each.
    page, _posts = _flask_page(browser, tmp_path, monkeypatch)
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


def test_a_blocked_remove_looks_disabled(browser, tmp_path, monkeypatch):
    # G1-RE NOTE B: the disabled Remove is told from an enabled control: no pointer, faded, no hover change.
    page, _posts = _flask_page(browser, tmp_path, monkeypatch, activity=True)
    sel = "button.danger[disabled]"
    cursor, opacity, bg = _style(page, sel, "cursor", "opacity", "backgroundColor")
    assert cursor == "not-allowed" and float(opacity) < 1
    page.hover(sel, force=True)
    assert _style(page, sel, "backgroundColor")[0] == bg
    page.context.close()

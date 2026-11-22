"""The Hardware page's radio list table (#hw-list) is today's 950 px on a
big screen and fits the width on a phone (no sideways scroll); at 900 px (no phone rule) it is no wider than its container.
Real browser."""
_FIT = """i => { const e = document.getElementById(i), p = e.parentElement, cs = getComputedStyle(p);
  return [Math.round(e.getBoundingClientRect().width), p.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)]; }"""


def test_the_hardware_list_is_capped(flask_page):
    page, _posts = flask_page(path="/hardware")
    width = "() => Math.round(document.getElementById('hw-list').getBoundingClientRect().width)"
    page.set_viewport_size({"width": 1920, "height": 1000})
    assert page.evaluate(width) == 950
    page.set_viewport_size({"width": 900, "height": 1000})
    w, box = page.evaluate(_FIT, "hw-list")
    assert w <= box + 1, (w, box)
    page.set_viewport_size({"width": 360, "height": 900})
    assert page.evaluate(width) < 360 and page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
    page.context.close()

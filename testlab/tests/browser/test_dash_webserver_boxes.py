"""In a real browser: the Webserver entries' boxes have the component box look, .45rem apart, the
lines inside have no separator border; phone inset below 480 px; no sideways scroll at 360."""
_STYLE = """() => { const cs = e => getComputedStyle(e), root = parseFloat(cs(document.documentElement).fontSize);
  const g = [...document.querySelectorAll('#wsbox .wsgroup')];
  return {root, gap: cs(document.querySelector('#wsbox > .body')).rowGap,
          boxes: g.map(e => [cs(e).borderTopWidth, cs(e).borderTopLeftRadius, cs(e).paddingTop, cs(e).paddingLeft]),
          lines: [...document.querySelectorAll('#wsbox .wsgroup .wsline')].map(l => cs(l).borderBottomWidth),
          scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def _px(rem, root):
    return f"{round(rem * root, 2):g}px"


def test_the_webserver_boxes(flask_page):
    page, _posts = flask_page(path="/")
    page.locator("#wsbox > summary").click()
    page.set_viewport_size({"width": 1600, "height": 1000})
    s = page.evaluate(_STYLE)
    assert len(s["boxes"]) >= 2 and s["gap"] == _px(.45, s["root"]) and set(s["lines"]) == {"0px"}, s
    assert all(b == ["1px", "6px", _px(.45, s["root"]), _px(.6, s["root"])] for b in s["boxes"]), s
    page.set_viewport_size({"width": 360, "height": 900})
    s = page.evaluate(_STYLE)
    assert all(b[2:] == [_px(.3, s["root"]), _px(.35, s["root"])] for b in s["boxes"]) and s["scroll"] <= 0, s
    page.context.close()

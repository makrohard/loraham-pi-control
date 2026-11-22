"""In a real browser: the System card's boxes have the radio-card component box look (1px border,
6px radius, tinted fill, .45rem .6rem), the gauge table's last row has no line, phones get the Apps inset (.3rem .35rem)
and nothing scrolls sideways at 360 px."""
_STYLE = """() => { const cs = e => getComputedStyle(e), root = parseFloat(cs(document.documentElement).fontSize);
  const g = [...document.querySelectorAll('#sysbox .sysgroup')];
  const rows = [...document.querySelectorAll('#sysbox .systab tr')].filter(r => r.offsetParent !== null);
  return {n: g.length, root, boxes: g.map(e => [cs(e).borderTopWidth, cs(e).borderTopLeftRadius, cs(e).backgroundColor,
          cs(e).paddingTop, cs(e).paddingLeft]), lastLine: cs(rows[rows.length - 1]).borderBottomWidth,
          scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def _px(rem, root):
    return f"{round(rem * root, 2):g}px"


def test_the_system_boxes_look_and_phone_inset(flask_page):
    page, _posts = flask_page(path="/")
    page.locator("#sysbox > summary").click()
    page.set_viewport_size({"width": 1600, "height": 1000})
    s = page.evaluate(_STYLE)
    assert s["n"] >= 2 and s["lastLine"] == "0px", s
    for b in s["boxes"]:
        assert b[:2] == ["1px", "6px"] and b[2] not in ("rgba(0, 0, 0, 0)", "transparent") and b[3:] == [_px(.45, s["root"]), _px(.6, s["root"])], s
    page.set_viewport_size({"width": 360, "height": 900})
    s = page.evaluate(_STYLE)
    assert all(b[3:] == [_px(.3, s["root"]), _px(.35, s["root"])] for b in s["boxes"]) and s["scroll"] <= 0, s
    page.context.close()

"""Dash plan c12 S8b in a real browser: at 360 and 390 px every System row label fits its column (scrollWidth <=
clientWidth), MEM included."""


def test_every_system_row_label_fits_on_a_phone(flask_page):
    page, _posts = flask_page(path="/")
    page.locator("#sysbox > summary").click()
    for width in (360, 390):
        page.set_viewport_size({"width": width, "height": 900})
        over = page.evaluate("""() => [...document.querySelectorAll('#sysbox .systab th')].filter(t => t.offsetParent !== null)
          .filter(t => t.scrollWidth > t.clientWidth).map(t => t.textContent.trim())""")
        assert over == [], (width, over)
    page.context.close()

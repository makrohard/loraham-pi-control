"""A radio's Live radio settings grid is one column below 880 px and from 880 px
as many 26rem columns as fit the wide page (at least 3 at 1920); nothing scrolls sideways at 360. Real browser."""
_M = """() => { const g = document.querySelector('.settingsgrid');
  return {cols: getComputedStyle(g).gridTemplateColumns.split(' ').filter(Boolean).length,
          scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def test_the_live_settings_fill_the_wide_page(flask_page):
    page, _posts = flask_page(path="/stacks/daemon/menu/stack-settings-daemon--r3-live?radio=r3")
    page.wait_for_function("() => document.querySelector('.settingsgrid')")
    got = {}
    for width in (360, 760, 1920):
        page.set_viewport_size({"width": width, "height": 1000})
        got[width] = page.evaluate(_M)
    assert got[360]["cols"] == got[760]["cols"] == 1 and got[1920]["cols"] >= 3 and got[360]["scroll"] <= 0, got
    page.context.close()

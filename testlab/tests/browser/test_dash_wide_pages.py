"""In a real browser: main is wide on Home (well past 1024 px at 2560) and 1024 px on a confirmation page
(Remove radio); the header's Home / Apps buttons sit at the same x on both; at 360 px no page scrolls sideways."""
_GEO = """() => { const m = document.querySelector('main.wrap').getBoundingClientRect();
  const x = s => Math.round(document.querySelector(s).getBoundingClientRect().left);
  return {main: Math.round(m.width), header: Math.round(document.querySelector('header .header-wrap').getBoundingClientRect().width), home: x('header .home'), apps: x('header .apps-link'),
          scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def test_wide_main_fixed_header_and_a_narrow_confirmation(flask_page):
    page, _posts = flask_page(path="/")
    base = "/".join(page.url.split("/")[:3])
    geo = {}
    for width in (2560, 360):
        page.set_viewport_size({"width": width, "height": 1000})
        for path in ("/", "/hardware/remove/r3"):
            page.goto(base + path, wait_until="load")
            geo[width, path] = page.evaluate(_GEO)
    home, conf = geo[2560, "/"], geo[2560, "/hardware/remove/r3"]
    assert home["main"] > 2000 and conf["main"] == 1024, geo
    assert (home["home"], home["apps"]) == (conf["home"], conf["apps"]) and home["header"] == conf["header"] == 1024, geo
    assert all(g["scroll"] <= 0 for (w, _p), g in geo.items() if w == 360), geo
    page.context.close()

"""Home's radio cards are left-aligned 484 px columns (repeat(auto-fill, min(484px, 100%)),
justify-content start): two radios side by side from the grid's left edge, one radio one 484 px column at the left
(no narrowed page), and on a phone one column that shrinks to the width. Computed geometry in a real browser."""
from lhpc.core import radios

_GEO = """() => { const g = document.querySelector('.radiogrid').getBoundingClientRect();
  return {grid: [g.left, g.width], cards: [...document.querySelectorAll('.radio-col')].map(c => {
    const r = c.getBoundingClientRect(); return [Math.round(r.left - g.left), Math.round(r.width), Math.round(r.top)]; }),
    narrow: document.body.classList.contains('dash-narrow'), scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def test_two_radios_are_two_left_aligned_484px_columns(flask_page):
    page, _posts = flask_page(path="/")
    page.set_viewport_size({"width": 1600, "height": 900})
    g = page.evaluate(_GEO)
    assert [c[:2] for c in g["cards"]] == [[0, 484], [500, 484]] and g["cards"][0][2] == g["cards"][1][2], g
    page.set_viewport_size({"width": 360, "height": 900})
    g = page.evaluate(_GEO)
    assert [c[:2] for c in g["cards"]] == [[0, round(g["grid"][1])]] * 2 and g["grid"][1] < 484 and g["scroll"] <= 0, g
    page.context.close()


def test_one_radio_is_one_484px_column_on_the_wide_page(flask_page, tmp_path):
    page, _posts = flask_page(path="/")
    # Leave the page before r3 leaves the hardware list: its column polls /api/daemon/r3 every 3 s, and a poll
    # landing between the rewrite and a reload is a 404 for a radio that no longer exists (a console error).
    url = page.url
    page.goto("about:blank")
    (tmp_path / "config" / "radios.toml").write_text(radios.render((radios.Radio("r1", "low", "loraham", "433"),)))
    page.goto(url, wait_until="load")
    page.set_viewport_size({"width": 1600, "height": 900})
    g = page.evaluate(_GEO)
    assert [c[:2] for c in g["cards"]] == [[0, 484]] and not g["narrow"] and g["grid"][1] > 900, g
    page.context.close()

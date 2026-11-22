"""PROXY-AFFECT-LIST (0.14.0, maintainer's word 11-18), in a real browser: the Stacks Web Proxies' "Will affect" rows
stand in 2-3 columns on a large screen and in one on a phone, each row inside its column (a long one wraps), the page
without sideways scroll — on both pages that show the panel (the column count follows the panel's width)."""
from __future__ import annotations

import pytest

_LIVE = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r3")}
_MEASURE = """() => {
  const panel = document.getElementById('ws-stackwebs');
  for (let d = panel; d; d = d.parentElement) if (d.tagName === 'DETAILS') d.open = true;
  const ul = panel.querySelector('ul.affectlist'), box = ul.getBoundingClientRect();
  const lis = [...ul.querySelectorAll('li')].map(li => li.getBoundingClientRect());
  return {n: lis.length, cols: new Set(lis.map(r => Math.round(r.left))).size,
          inside: lis.every(r => r.left >= box.left - 1 && r.right <= box.right + 1),
          scrollW: document.documentElement.scrollWidth}; }"""


@pytest.mark.parametrize("path", ["/stacks?open=webserver", "/stacks/controller/menu/ws-stackwebs"])
def test_the_rows_stand_in_columns_on_a_large_screen_and_in_one_on_a_phone(flask_page, path):
    page, _posts = flask_page(path=path, unix_replies=_LIVE)
    page.set_viewport_size({"width": 1280, "height": 900})
    big = page.evaluate(_MEASURE)
    # the page is wide (no 1024 px main), so 1280 px fits a 4th auto-fill column (was <= 3 at the cap)
    assert big["n"] >= 3 and 2 <= big["cols"] <= 4 and big["inside"], (path, big)
    page.set_viewport_size({"width": 390, "height": 900})
    small = page.evaluate(_MEASURE)
    assert small["cols"] == 1 and small["inside"] and small["scrollW"] <= 390, (path, small)


@pytest.mark.parametrize("width", [220, 300])          # a phone at 200 % zoom has ~300 px; 220: well below one column
@pytest.mark.parametrize("path", ["/stacks?open=webserver", "/stacks/controller/menu/ws-stackwebs"])
def test_the_list_fits_a_box_narrower_than_one_column(flask_page, path, width):
    # G2-RELUI B3: below 16rem of room the one column shrinks to the box, never wider than it
    page, _posts = flask_page(path=path, unix_replies=_LIVE)
    page.set_viewport_size({"width": width, "height": 900})
    m = page.evaluate(_MEASURE)
    box = page.evaluate("() => { const u = document.querySelector('#ws-stackwebs ul.affectlist');"
                        " return {ul: u.getBoundingClientRect().width, parent: u.parentElement.getBoundingClientRect().width}; }")
    assert m["cols"] == 1 and m["inside"] and box["ul"] <= box["parent"] + 1, (path, m, box)
    assert box["ul"] < 256 and m["scrollW"] <= width, box                     # narrower than 16rem; no sideways scroll

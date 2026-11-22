"""Dash plan c12 S4 (his "let columns grow"): on the wide Apps page no stack-list name is cut off (each .col-name shows
its whole text) at 1280, 1920 and 2560 px, and the status column still lines up across the rows. Real browser."""
_M = """() => { const rows = [...document.querySelectorAll('.stacklist .stackrow > summary')].filter(s => s.offsetParent !== null);
  const names = rows.map(s => s.querySelector('.col-name')).filter(Boolean);
  return {n: names.length, cut: names.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent.trim()),
          status: [...new Set(rows.map(s => s.querySelector('.col-status')).filter(Boolean).map(e => Math.round(e.getBoundingClientRect().left)))],
          scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def test_no_name_is_cut_and_the_columns_line_up(flask_page):
    page, _posts = flask_page(path="/stacks")
    for width in (1280, 1920, 2560):
        page.set_viewport_size({"width": width, "height": 1000})
        m = page.evaluate(_M)
        assert m["n"] >= 5 and m["cut"] == [] and len(m["status"]) == 1 and m["scroll"] <= 0, (width, m)
    page.context.close()

"""U4 (W423) in a real browser: "page ›" sits at the very right of each level's header, after
"logs", clear of the header's own text, at phone and desktop width; it opens the level's page in
the same tab; a level page and the tree work with JS off and fit a phone. Layout facts only: no
screenshots, no pixel constants beyond "right of" and "does not overlap"."""
from __future__ import annotations

import pytest

# The "page ›" link of a level, its "logs" link (when the level has one) and the header text it
# must not cover: the overlay sits on the summary of the <details> just before it.
_GEOMETRY = """(mid) => {
  const box = (el) => { const r = el.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; };
  const link = [...document.querySelectorAll('a.pagelink')].find(a => a.getAttribute('href').endsWith('/menu/' + mid));
  if (!link) return null;
  const actions = link.closest('.row-actions');
  const level = document.getElementById(mid);
  const summary = level.querySelector(':scope > summary');
  const range = document.createRange();
  range.selectNodeContents(summary);
  const text = [...range.getClientRects()].filter(r => r.width > 0).map(r => ({l: r.left, r: r.right, t: r.top, b: r.bottom}));
  const logs = actions.querySelector('a.logslink');
  return {link: box(link), logs: logs ? box(logs) : null, text: text,
          last: actions.lastElementChild === link, summary: box(summary)};
}"""

# Each level is shown through its own anchor: the Stacks page opens it with its ancestors.
_LEVELS = [("/stacks?open=meshcore", "stackrow-meshcore"), ("/stacks?open=meshcore", "controller-row"),
           ("/stacks?open=meshcore#stack-install-meshcore", "stack-install-meshcore"),
           ("/stacks#firewall-apply", "firewall-apply"), ("/stacks#webserver-row--monitor", "webserver-row--monitor"),
           ("/stacks#firewall-apply--other-commands", "firewall-apply--other-commands")]


def _overlaps(a, b):
    return a["l"] < b["r"] and b["l"] < a["r"] and a["t"] < b["b"] and b["t"] < a["b"]


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("path, mid", _LEVELS, ids=[m for _p, m in _LEVELS])
def test_page_link_is_rightmost_after_logs_and_clear_of_the_heading(page, width, path, mid):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(page.lab_base + path, wait_until="networkidle")
    page.wait_for_function("(m) => { const s = document.getElementById(m).querySelector(':scope > summary');"
                           " return s.getClientRects().length > 0; }", arg=mid, timeout=15000)
    g = page.evaluate(_GEOMETRY, mid)
    assert g is not None, f"no page link for {mid}"
    for part in ("link", "summary"):                                     # measured on screen, not hidden
        assert g[part]["r"] > g[part]["l"] and g[part]["b"] > g[part]["t"], f"{mid}@{width}: {part} not shown"
    assert g["last"], f"{mid}: 'page ›' is not the overlay's last link"
    if g["logs"] is not None:
        assert g["link"]["l"] >= g["logs"]["r"], f"{mid}@{width}: 'page ›' is not right of 'logs'"
    assert g["link"]["r"] <= g["summary"]["r"] + 1, f"{mid}@{width}: 'page ›' sticks out of its header"
    covered = [t for t in g["text"] if _overlaps(g["link"], t)]
    assert not covered, f"{mid}@{width}: 'page ›' covers the header text {covered}"


def test_page_link_opens_the_level_in_the_same_tab(page):
    page.goto(page.lab_base + "/stacks#firewall-apply--other-commands", wait_until="networkidle")
    page.locator('a.pagelink[href="/stacks/controller/menu/firewall-apply--other-commands"]').wait_for(state="visible")
    before = len(page.context.pages)
    page.locator('a.pagelink[href="/stacks/controller/menu/firewall-apply--other-commands"]').click()
    page.wait_for_url("**/stacks/controller/menu/firewall-apply--other-commands")
    assert len(page.context.pages) == before, "the page opened in another tab"
    assert page.evaluate("() => document.getElementById('firewall-apply--other-commands').open")
    assert page.locator("nav.crumbs").inner_text().endswith("Other commands")


@pytest.mark.parametrize("path", ["/stacks/menus", "/stacks/meshcore/menu/stack-install-meshcore",
                                  "/stacks/controller/menu/webserver-row--certificates"])
def test_level_pages_and_the_tree_work_without_js_and_fit_a_phone(browser, lab, path):
    context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
    try:
        pg = context.new_page()
        pg.goto(lab.base + path, wait_until="load")
        overflow = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, f"{path}: {overflow}px of horizontal overflow at 390px"
        if path == "/stacks/menus":
            pg.locator("ul.navtree summary").first.click()               # native <details>, no JS
            assert pg.locator("ul.navtree a").first.is_visible()
        else:
            mid = path.rsplit("/", 1)[1]
            assert pg.evaluate("(m) => document.getElementById(m).open", mid), f"{mid} is not open"
            assert pg.locator("nav.crumbs .here").is_visible()
    finally:
        context.close()

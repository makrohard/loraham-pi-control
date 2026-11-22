"""Dash plan c12 S12 (his word): on the Auto-install page the selection table (#ai-select) and the run's task table
(#ai-tasks) are at most 950 px on a big screen and fill the width on a phone; other .tasklist tables (HMAC) are not
capped. Real browser."""
_W = """ids => ids.map(i => { const e = document.getElementById(i);
  return [Math.round(e.getBoundingClientRect().width), getComputedStyle(e).width]; })"""


def test_the_two_auto_install_tables_are_capped(flask_page):
    page, _posts = flask_page(path="/auto-install")
    ids = page.evaluate("() => [...document.querySelectorAll('table.tasklist')].map(t => t.id)")
    assert "ai-select" in ids, ids
    page.set_viewport_size({"width": 2560, "height": 1000})
    if "ai-tasks" not in ids:                  # the run's table renders only with a run: the same element, by its id
        page.evaluate("""() => { const t = document.createElement('table'); t.className = 'tasklist'; t.id = 'ai-tasks';
          t.innerHTML = '<tr><td>x</td></tr>'; document.querySelector('main.wrap').appendChild(t); }""")
    wide = page.evaluate(_W, ["ai-select", "ai-tasks"])
    assert wide[0][0] == wide[1][0] == 950, wide
    page.set_viewport_size({"width": 360, "height": 900})
    narrow = page.evaluate(_W, ["ai-select"])
    assert narrow[0][0] < 360 and page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0, narrow
    page.context.close()


def test_the_task_table_rule_is_by_id_not_tasklist(flask_page):
    page, _posts = flask_page(path="/auto-install")
    page.set_viewport_size({"width": 2560, "height": 1000})
    w = page.evaluate("""() => { const t = document.createElement('table'); t.className = 'tasklist'; t.innerHTML = '<tr><td>x</td></tr>';
      document.querySelector('main.wrap').appendChild(t); return Math.round(t.getBoundingClientRect().width); }""")
    assert w > 1500, w                                                     # an other tasklist (hmac_apply.html) stays wide
    page.context.close()

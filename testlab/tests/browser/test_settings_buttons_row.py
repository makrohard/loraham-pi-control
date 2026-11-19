"""#76 (0.14.0, 11-19), in a real browser: a stack's Settings buttons "Save configuration" and "Reset
to defaults" stand side by side on a large screen and wrap without sideways scroll on a narrow one; Enter in a config
field still saves (Save, outside the form by `form=`, stays its default button): one POST, to the config route."""
from __future__ import annotations

PAGE = "/stacks/chat/menu/stack-settings-chat?radio=r1"
_POS = """() => { const save = document.querySelector('button[form="cfg-form-chat"]');
  const reset = [...document.querySelectorAll('.cfgacts form.act button')].find(b => b.textContent.trim() === 'Reset to defaults');
  const a = save.getBoundingClientRect(), b = reset.getBoundingClientRect();
  return {saveTop: Math.round(a.top), resetTop: Math.round(b.top), saveRight: a.right, resetLeft: b.left,
          scrollW: document.documentElement.scrollWidth}; }"""


def test_save_and_reset_share_a_line_and_wrap_on_a_narrow_screen(flask_page):
    page, _posts = flask_page(path=PAGE)
    page.set_viewport_size({"width": 1280, "height": 900})
    big = page.evaluate(_POS)
    assert big["saveTop"] == big["resetTop"] and big["saveRight"] < big["resetLeft"], big     # one row, Save first
    page.set_viewport_size({"width": 300, "height": 900})
    small = page.evaluate(_POS)
    assert small["scrollW"] <= 300, small


def test_enter_in_a_config_field_saves_once(flask_page):
    page, posts = flask_page(path=PAGE)
    field = page.locator('#cfg-form-chat input[name="f_call"]')
    field.fill("DL9TST")
    with page.expect_response(lambda r: r.request.method == "POST"):
        field.press("Enter")
    assert [p for p, _body in posts] == ["/stacks/chat/config"], posts
    assert "f_call=DL9TST" in posts[0][1]


def test_the_old_repositories_url_with_its_old_fragment_lands_on_install_repositories(flask_page):
    # UI-REORG c3 (s2): the old daemon Settings › Repositories URL, with its old in-page anchor, forwards to the new level
    page, _posts = flask_page(path="/stacks/daemon/menu/stack-settings-daemon--repository?radio=r1"
                                   "#stack-settings-daemon--repository")
    page.wait_for_url("**/stack-install-daemon--repositories?radio=r1#stack-install-daemon--repositories")
    assert page.locator("#stack-install-daemon--repositories input[name^=remote_]").first.is_visible()

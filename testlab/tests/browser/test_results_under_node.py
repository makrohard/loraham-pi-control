"""UI-REORG c9 (plan v7 s8), in a real browser: a form sent from the Apps page carries the acted LEVEL (`_level`,
stacks_state.js: the nearest ancestor <details> that is a level, with its row's owner), so the server can show the
result under that node; a form in an inner non-level panel names its nearest level."""
from __future__ import annotations

from urllib.parse import parse_qs


def test_verify_now_sends_its_level(flask_page):
    page, posts = flask_page(path="/stacks")
    page.evaluate("""() => document.querySelector('form[action="/webserver/verify"] button[type=submit]').click()""")
    page.wait_for_function("() => document.body.textContent.includes('posted')", timeout=15000)
    [(path, body)] = posts
    assert path == "/webserver/verify" and parse_qs(body)["_level"] == ["controller/webserver-row--monitor"], body

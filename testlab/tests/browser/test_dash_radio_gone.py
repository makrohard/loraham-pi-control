"""DASH-RADIO-GONE: a radio that leaves the hardware list while a page shows it. Its daemon API answers 404
(app.py `_api_radio`); the page's badges of that radio then read "removed — reload the page", its poller stops (no
request after a hidden -> visible toggle or the 15 s error interval), its daemon blocks pause (data-up 0), and a closed
and reopened block's pill still reads "removed". Any other failure (a 500) keeps the slow retry. Real Chromium over the
app (flask_page: r1 433 and the USB radio r3 868); the waits are pumped (page.wait_for_*), never slept."""
from __future__ import annotations

from urllib.parse import urlsplit

from lhpc_testlab.testing import unexpected_console_errors

from lhpc.core import radios

RID = "r3"
GONE = "removed — reload the page"
TREE = f"/stacks/daemon/menu/stack-settings-daemon?radio={RID}"     # Monitor + Live · r3, no dashboard reload
LIVE = f"#stack-settings-daemon--{RID}-live"
PILL = LIVE + " > summary .dbadge"
_READY = {"/tmp/loraconf-r3.sock": b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"}
_R3_404 = (("status of 404", r"/api/daemon/r3(/.*)?"),)              # the 404s this test provokes, nothing else


def _drop_r3(tmp_path):
    """r3 leaves the list; r2 (868) joins, so the bands stay the same and Home does not reload itself (its signature
    names bands: service_lifecycle_ops.dash_signature `M:`)."""
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))))


def _is_r3_poll(url: str) -> bool:
    return urlsplit(url).path == "/api/daemon/" + RID


def _r3_requests(page) -> list:
    seen = []
    page.on("request", lambda req: seen.append(req.url) if _is_r3_poll(req.url) else None)
    return seen


def _toggle_hidden(page):
    """The tab hidden, then visible again (visibilitychange each time): a poller that still runs asks at once."""
    page.evaluate("""() => { for (const h of [true, false]) {
        Object.defineProperty(document, "hidden", {configurable: true, get: () => h});
        document.dispatchEvent(new Event("visibilitychange")); } }""")


def _open_live(page):
    page.click(LIVE + " > summary")
    page.wait_for_function("s => document.querySelector(s).open", arg=LIVE, timeout=10000)


def _pill(page) -> list:
    return page.evaluate("s => { const p = document.querySelector(s); return [p.textContent, p.className, p.hidden]; }",
                         PILL)


def test_a_removed_radios_home_badge_says_so_and_the_others_do_not(flask_page, tmp_path):
    page, _posts = flask_page(path="/", allowed_errors=_R3_404)
    page.wait_for_function("() => document.getElementById('rd-badge-r3').textContent.trim() !== ''", timeout=10000)
    # r1's badge is snapshot after r1's OWN poll painted it (the cache entry and the paint are one synchronous pass),
    # not when r3's server-rendered badge is there: the two radios poll on their own (tests/README C14)
    page.wait_for_function("() => window.lhpcDaemonState('r1') !== null", timeout=10000)
    r1 = page.evaluate("() => document.getElementById('rd-badge-r1').textContent")
    _drop_r3(tmp_path)
    page.wait_for_function("t => document.getElementById('rd-badge-r3').textContent === t", arg=GONE, timeout=10000)
    assert page.evaluate("() => document.getElementById('rd-badge-r3').className") == "badge badge-stopped"
    assert page.evaluate("() => document.getElementById('rd-badge-r1').textContent") == r1 != GONE
    page.context.close()


def test_a_removed_radio_is_not_polled_again(flask_page, tmp_path):
    page, _posts = flask_page(path=TREE, unix_replies=_READY, allowed_errors=_R3_404)
    _open_live(page)
    page.wait_for_function("s => document.querySelector(s).getAttribute('data-up') === '1'", arg=LIVE, timeout=10000)
    with page.expect_response(lambda r: _is_r3_poll(r.url) and r.status == 404, timeout=10000):
        _drop_r3(tmp_path)                                   # synced on the first 404, not on the page's answer to it
    # the radio's daemon views ask the same path (daemoncfg.js): none is shown, so every request counted is the poller's
    assert page.evaluate("r => [...document.querySelectorAll('.daemonview')].filter(v => v.getAttribute('data-rid') === r"
                         " && window.lhpcShown(v)).map(v => v.id)", RID) == []
    seen = _r3_requests(page)
    _toggle_hidden(page)                                     # a poller that still runs asks at once here ...
    page.wait_for_timeout(16000)                             # ... or after the 15 s error interval
    assert seen == []
    page.context.close()


def test_a_server_error_is_not_removed_and_keeps_polling(flask_page):
    page, _posts = flask_page(path=TREE, unix_replies=_READY,
                              allowed_errors=(("status of 500", r"/api/daemon/r3"),))
    _open_live(page)
    page.route("**/api/daemon/r3", lambda route: route.fulfill(status=500, body="boom"))   # before the fixture's route
    with page.expect_response(lambda r: _is_r3_poll(r.url) and r.status == 500, timeout=10000):
        pass                                                 # the next poll fails
    with page.expect_request(lambda r: _is_r3_poll(r.url), timeout=10000):
        _toggle_hidden(page)                                 # still polled: asked at once
    assert _pill(page)[0] != GONE
    page.context.close()


def test_a_removed_radios_pill_still_says_so_after_its_block_is_closed_and_opened(flask_page, tmp_path):
    page, _posts = flask_page(path=TREE, unix_replies=_READY, allowed_errors=_R3_404)
    _open_live(page)
    page.wait_for_function("s => !document.querySelector(s).hidden", arg=PILL, timeout=10000)   # present and shown
    page.wait_for_function("s => document.querySelector(s).getAttribute('data-up') === '1'", arg=LIVE, timeout=10000)
    _drop_r3(tmp_path)
    page.wait_for_function("([s, t]) => document.querySelector(s).textContent === t", arg=[PILL, GONE], timeout=10000)
    assert page.evaluate("s => document.querySelector(s).getAttribute('data-up')", LIVE) == "0"   # its block paused
    page.click(LIVE + " > summary")                          # closed: its pill is hidden
    page.wait_for_function("s => document.querySelector(s).hidden", arg=PILL, timeout=10000)
    _open_live(page)                                         # opened again: "removed", shown
    page.wait_for_function("s => !document.querySelector(s).hidden", arg=PILL, timeout=10000)
    assert _pill(page) == [GONE, "badge badge-stopped dbadge", False]
    page.context.close()


def test_only_an_expected_error_at_its_own_url_is_allowed():
    errors = [("Failed to load resource: the server responded with a status of 404 (Not Found)",
               "http://127.0.0.1:9/api/daemon/r3"),
              ("Failed to load resource: the server responded with a status of 404 (Not Found)",
               "http://127.0.0.1:9/static/missing.js"),
              ("TypeError: x is undefined", "")]
    assert unexpected_console_errors(errors, _R3_404) == [
        "Failed to load resource: the server responded with a status of 404 (Not Found)"
        " @ http://127.0.0.1:9/static/missing.js", "TypeError: x is undefined"]
    assert len(unexpected_console_errors(errors, ())) == 3

"""dash.js's per-band daemon polling, in a real browser: no request while the page is hidden, never
a second request for a band while one is still out, and a slow retry after a failed one.

The daemon API is answered by the test (counted at the route), and time is Playwright's clock,
advanced by the test and never slept through. What is asserted is the number of requests in a
window, not how the script arms its timers.
"""
from __future__ import annotations

import json
import re

BANDS = ("433", "868")
DAEMON = re.compile(r".*/api/daemon/(433|868)$")
LIVE = {"reachable": True, "channel": {}, "status": {}, "stats": {}, "feed": []}


def _open(page, answer, hidden=False):
    """Open the dashboard with `answer(route, band)` serving the daemon API; return the per-band
    request counts and a `settled()` that returns once every request issued before it has
    reached the handler. With `hidden`, the page is hidden from its first script on (a tab
    opened in the background)."""
    seen = dict.fromkeys(BANDS, 0)

    def handler(route):
        band = DAEMON.match(route.request.url).group(1)
        seen[band] += 1
        answer(route, band)

    page.clock.install()
    page.route(DAEMON, handler)
    # "nothing structural changed": the signature poll must not reload the page under the test
    page.route("**/api/dash-signature", lambda route: route.fulfill(
        status=200, content_type="application/json", body="null"))
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    if hidden:
        page.add_init_script(
            "Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});"
            "Object.defineProperty(document, 'visibilityState', {configurable: true,"
            " get: () => 'hidden'});")
    page.goto(page.lab_base + "/", wait_until="load")

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    settled()
    assert seen == dict.fromkeys(BANDS, 0 if hidden else 1), seen
    return seen, settled


def _live(route, band):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(dict(LIVE, band=band)))


def _wait_rendered(page):
    """dash.js has rendered a daemon answer for every band (the server renders other text)."""
    page.wait_for_function(
        "bands => bands.every(b => document.getElementById('rd-badge-' + b).textContent"
        " === 'daemon live')", arg=list(BANDS), timeout=15000)


def _set_hidden(page, hidden):
    page.evaluate(
        "h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});"
        " Object.defineProperty(document, 'visibilityState', {configurable: true,"
        "   get: () => h ? 'hidden' : 'visible'});"
        " document.dispatchEvent(new Event('visibilitychange')); }", hidden)


def test_a_visible_page_polls_each_band_every_3_s(page):
    seen, settled = _open(page, _live)
    _wait_rendered(page)
    page.clock.run_for(2500)
    settled()
    assert seen == dict.fromkeys(BANDS, 1), seen
    page.clock.run_for(1000)
    settled()
    assert seen == dict.fromkeys(BANDS, 2), seen


def test_a_hidden_page_makes_no_daemon_request_and_refreshes_once_on_return(page):
    seen, settled = _open(page, _live)
    _wait_rendered(page)
    _set_hidden(page, True)
    page.clock.run_for(30000)
    settled()
    assert seen == dict.fromkeys(BANDS, 1), seen
    _set_hidden(page, False)
    settled()
    assert seen == dict.fromkeys(BANDS, 2), seen   # one immediate refresh, no waiting for a tick


def test_a_page_loaded_hidden_makes_no_daemon_request_until_it_is_shown(page):
    seen, settled = _open(page, _live, hidden=True)          # no request at load (asserted there)
    page.clock.run_for(30000)
    settled()
    assert seen == dict.fromkeys(BANDS, 0), seen
    _set_hidden(page, False)
    settled()
    assert seen == dict.fromkeys(BANDS, 1), seen             # exactly one refresh per band
    _wait_rendered(page)


def test_a_slow_answer_is_never_overlapped_by_a_second_request(page):
    held = []
    seen, settled = _open(page, lambda route, band: held.append((route, band)))
    page.clock.run_for(10000)                    # three ticks' worth while the first is still out
    settled()
    assert seen == dict.fromkeys(BANDS, 1), seen
    for route, band in held:
        _live(route, band)
    _wait_rendered(page)
    page.clock.run_for(3000)                     # answered: the normal cadence resumes
    settled()
    assert seen == dict.fromkeys(BANDS, 2), seen


def test_a_failed_poll_is_retried_after_the_slow_interval_not_the_normal_one(page):
    """The failure is an unreadable body. An HTTP or network error takes the same rejection path,
    but Chromium logs either as a console error, which this lane's page fixture fails on."""
    seen, settled = _open(page, lambda route, band: route.fulfill(
        status=200, content_type="application/json", body="not json"))
    page.clock.run_for(3500)
    settled()
    assert seen == dict.fromkeys(BANDS, 1), seen
    page.clock.run_for(11000)                    # 14.5 s after the failure: still no retry
    settled()
    assert seen == dict.fromkeys(BANDS, 1), seen
    page.clock.run_for(1000)                     # 15.5 s after the failure: exactly one retry
    settled()
    assert seen == dict.fromkeys(BANDS, 2), seen

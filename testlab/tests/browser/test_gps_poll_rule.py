"""UI-REORG c11 (plan v7 s13 P2, L85): the GPS Monitor asks /api/gps only while it and EVERY <details> around it are
open (the controller row included) and the tab is visible (shown.js). In a real browser; /api/gps answered and counted
by the test; time is Playwright's clock, advanced and never slept."""
from __future__ import annotations

import json

_PAGE = "/stacks?open=gps"                       # the controller row, the GPS panel and its Monitor open
_GPS = {"source": "gpsd", "state": "3d", "label": "3D fix", "lat": 48.1, "lon": 11.6}


def _serve(page):
    seen = {"n": 0}

    def handler(route):
        seen["n"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_GPS))

    page.clock.install()
    page.route("**/api/gps", handler)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    page.reload()

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    page.wait_for_function("() => document.getElementById('gps-mon-state').textContent === '3D fix'", timeout=15000)
    settled()
    return seen, settled


def test_a_closed_controller_row_or_a_hidden_tab_stops_the_gps_poll_and_showing_again_asks_once(flask_page):
    page, _posts = flask_page(path=_PAGE)
    seen, settled = _serve(page)
    page.clock.run_for(3500)
    settled()
    n = seen["n"]
    assert n >= 2                                                    # open: it polls (every 3 s)
    page.click("#controller-row > summary")                           # an outer <details> closed, the pane still open
    page.wait_for_function("() => !document.getElementById('controller-row').open", timeout=15000)
    settled()
    n = seen["n"]
    page.clock.run_for(9000)
    settled()
    assert seen["n"] == n                                            # no request with the row closed
    with page.expect_request("**/api/gps", timeout=15000):            # a link to the pane reopens only the row
        page.evaluate("() => { location.hash = '#gps-monitor'; }")    # (its panes stayed open): one request at once
    settled()
    assert seen["n"] == n + 1
    page.evaluate("h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});"
                  " document.dispatchEvent(new Event('visibilitychange')); }", True)       # a background tab
    settled()
    n = seen["n"]
    page.clock.run_for(9000)
    settled()
    assert seen["n"] == n                                            # no request while hidden
    with page.expect_request("**/api/gps", timeout=15000):
        page.evaluate("h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});"
                      " document.dispatchEvent(new Event('visibilitychange')); }", False)   # in front: at once
    settled()
    assert seen["n"] == n + 1

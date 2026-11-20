"""dash.js's per-radio daemon polling on Home's radio cards (W285/W286), in a real browser: no request
while the page is hidden, never a second request for a radio while one is still out, and a slow retry
after a failed one.

The daemon API is answered by the test (counted at the route), and time is Playwright's clock,
advanced by the test and never slept through. What is asserted is the number of requests in a
window, not how the script arms its timers.
"""
from __future__ import annotations

import json
import re

RIDS = {"r1": "433", "r2": "868"}                       # the lab's radios: one card each
DAEMON = re.compile(r".*/api/daemon/(r[0-9]+)$")
LIVE = {"reachable": True, "ready": True, "radio_state": "READY", "label": "daemon READY", "channel": {}, "status": {}, "stats": {}, "feed": []}


def _open(page, answer, hidden=False, path="/"):
    """Open the dashboard (or `path`) with `answer(route, band)` serving the daemon API; return the per-radio
    request counts and a `settled()` that returns once every request issued before it has
    reached the handler. With `hidden`, the page is hidden from its first script on (a tab
    opened in the background)."""
    seen = dict.fromkeys(RIDS, 0)

    def handler(route):
        rid, = DAEMON.match(route.request.url).groups()   # each card asks its own radio
        seen[rid] += 1
        answer(route, RIDS[rid])

    # Paused before the page loads: installed alone, the clock runs with real time, and the real time of the load and
    # the render wait counted into the test's windows (a 3 s poll inside "2.5 s": testlab RED on cons/f14-r1)
    page.clock.install()
    page.clock.pause_at(page.evaluate("() => Date.now()") / 1000 + 1)   # Python Playwright: a number is in SECONDS
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
    page.goto(page.lab_base + path, wait_until="load")

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    settled()
    assert seen == dict.fromkeys(RIDS, 0 if hidden else 1), seen
    return seen, settled


def _live(route, band):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(dict(LIVE, band=band)))


def _wait_rendered(page):
    """dash.js has rendered a daemon answer on every radio's card (the server renders other text)."""
    page.wait_for_function(
        "rids => rids.every(r => document.getElementById('rd-badge-' + r).textContent"
        " === 'daemon READY')", arg=list(RIDS), timeout=15000)


def _set_hidden(page, hidden):
    page.evaluate(
        "h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});"
        " Object.defineProperty(document, 'visibilityState', {configurable: true,"
        "   get: () => h ? 'hidden' : 'visible'});"
        " document.dispatchEvent(new Event('visibilitychange')); }", hidden)


def test_a_visible_page_polls_each_radio_every_3_s(page):
    seen, settled = _open(page, _live)
    _wait_rendered(page)
    page.clock.run_for(2500)
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen
    page.clock.run_for(1000)
    settled()
    assert seen == dict.fromkeys(RIDS, 2), seen


def test_a_hidden_page_makes_no_daemon_request_and_refreshes_once_on_return(page):
    seen, settled = _open(page, _live)
    _wait_rendered(page)
    _set_hidden(page, True)
    page.clock.run_for(30000)
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen
    _set_hidden(page, False)
    settled()
    assert seen == dict.fromkeys(RIDS, 2), seen   # one immediate refresh, no waiting for a tick


def test_a_page_loaded_hidden_makes_no_daemon_request_until_it_is_shown(page):
    seen, settled = _open(page, _live, hidden=True)          # no request at load (asserted there)
    page.clock.run_for(30000)
    settled()
    assert seen == dict.fromkeys(RIDS, 0), seen
    _set_hidden(page, False)
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen             # exactly one refresh per radio
    _wait_rendered(page)


def test_a_slow_answer_is_never_overlapped_by_a_second_request(page):
    held = []
    seen, settled = _open(page, lambda route, band: held.append((route, band)))
    page.clock.run_for(10000)                    # three ticks' worth while the first is still out
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen
    for route, band in held:
        _live(route, band)
    _wait_rendered(page)
    page.clock.run_for(3000)                     # answered: the normal cadence resumes
    settled()
    assert seen == dict.fromkeys(RIDS, 2), seen


def test_a_failed_poll_is_retried_after_the_slow_interval_not_the_normal_one(page):
    """The failure is an unreadable body. An HTTP or network error takes the same rejection path,
    but Chromium logs either as a console error, which this lane's page fixture fails on."""
    seen, settled = _open(page, lambda route, band: route.fulfill(
        status=200, content_type="application/json", body="not json"))
    page.clock.run_for(3500)
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen
    page.clock.run_for(11000)                    # 14.5 s after the failure: still no retry
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen
    page.clock.run_for(1000)                     # 15.5 s after the failure: exactly one retry
    settled()
    assert seen == dict.fromkeys(RIDS, 2), seen


def test_a_reachable_daemon_whose_radio_failed_reads_its_state_not_ready(page):
    """G1-C28 N1 / UI-REORG s9: the badge shows the server's label (its ONE readiness -> label mapping), not reachability."""
    failed = dict(LIVE, ready=False, radio_state="FAILED", label="daemon (FAILED)")
    _open(page, lambda route, band: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(dict(failed, band=band))))
    page.wait_for_function(
        "rids => rids.every(r => document.getElementById('rd-badge-' + r).textContent === 'daemon (FAILED)')",
        arg=list(RIDS), timeout=15000)
    assert all(page.get_attribute(f"#rd-badge-{rid}", "class") == "badge badge-degraded" for rid in RIDS)


# --- A32: the Live RSSI value stays on one line on a phone (Home served by the console app, r3 up, the value from
#     dash.js's own poll) ---------------------------------------------------------------------------------------

_READY = b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"
_POLL = {"reachable": True, "ready": True, "label": "daemon READY", "channel": {"LIVERSSI": "-115.00"}, "status": {"RADIO": "READY"}}

_CELL = """rid => { const td = document.querySelector('#monitor-' + rid + ' .monrssi td');
  const sp = td.querySelector('span'), lh = parseFloat(getComputedStyle(sp).lineHeight) || 20;
  const unit = [...td.childNodes].find(n => n.nodeType === 3 && n.textContent.trim() === 'dBm');
  const r = document.createRange(); r.selectNodeContents(unit);
  return {value: sp.textContent, spanH: sp.getBoundingClientRect().height, lineH: lh,
          unitLines: [...r.getClientRects()].filter(q => q.width).length,
          meterW: td.querySelector('meter').getBoundingClientRect().width,
          scrollW: document.documentElement.scrollWidth}; }"""


def test_the_live_rssi_value_stays_on_one_line_on_a_phone(flask_page):
    page, _posts = flask_page(path="/", unix_replies={"/tmp/loraconf-r3.sock": _READY}, api={"/api/daemon/r3": _POLL})
    page.wait_for_function("(document.getElementById('rd-rssiv-r3') || {}).textContent === '-115.00'")
    for width in (390, 360):
        page.set_viewport_size({"width": width, "height": 900})
        c = page.evaluate(_CELL, "r3")
        assert c["value"] == "-115.00" and c["spanH"] < 1.5 * c["lineH"] and c["unitLines"] == 1, (width, c)
        assert c["meterW"] >= 48 and c["scrollW"] <= width, (width, c)
    page.set_viewport_size({"width": 1280, "height": 900})
    assert round(page.evaluate(_CELL, "r3")["meterW"]) == 192                     # the desktop meter is unchanged
    page.context.close()


# --- HWLIST-BADGE-LIVE: the hardware list's daemon badges follow the same poll (/hardware, and a Stacks body that
#     arrives on first expand) -------------------------------------------------------------------------------------

def _answer_live(page):
    page.route(DAEMON, lambda route: _live(route, RIDS[DAEMON.match(route.request.url).group(1)]))


def _wait_list_rendered(page):
    page.wait_for_function(
        # a lazily loaded body has no badge yet: a throwing predicate is not retried, so a missing badge reads ""
        "rids => rids.every(r => (document.getElementById('hw-badge-' + r) || {}).textContent"
        " === 'daemon READY')",
        arg=list(RIDS), timeout=15000)


def test_the_hardware_lists_badges_follow_the_daemon_live(page):
    _answer_live(page)
    page.goto(page.lab_base + "/hardware", wait_until="load")
    _wait_list_rendered(page)
    assert all(page.get_attribute(f"#hw-badge-{rid}", "aria-live") == "polite" for rid in RIDS)
    assert all(page.get_attribute(f"#hw-badge-{rid}", "class") == "badge badge-running" for rid in RIDS)


def test_a_daemon_body_loaded_on_first_expand_polls_its_list_badges(page):
    _answer_live(page)
    page.goto(page.lab_base + "/stacks", wait_until="load")
    assert page.locator("[id^='hw-badge-']").count() == 0          # the daemon's body is not loaded yet
    page.click("#stackrow-daemon > summary")
    page.wait_for_selector("#stack-hardware-daemon > summary")
    page.click("#stack-hardware-daemon > summary")                 # c11 (s13 P3): its badges poll once shown
    _wait_list_rendered(page)


def test_an_unchanged_answer_does_not_rewrite_a_list_badge(page):
    """aria-live="polite": a badge's text is rewritten only when it changes, so a screen reader announces changes
    only. Its class is set on every answer, which marks each answer as rendered."""
    seen, settled = _open(page, _live, path="/hardware")
    _wait_list_rendered(page)
    page.evaluate(
        "rids => { window.__writes = {text: 0, cls: 0}; rids.forEach(r => new MutationObserver(ms => ms.forEach("
        "m => window.__writes[m.type === 'attributes' ? 'cls' : 'text']++)).observe("
        "document.getElementById('hw-badge-' + r), {childList: true, characterData: true, subtree: true,"
        " attributes: true, attributeFilter: ['class']})); }", list(RIDS))
    page.clock.run_for(3500)
    settled()
    page.clock.run_for(3000)
    settled()
    assert seen == dict.fromkeys(RIDS, 3), seen                       # two more polls, the same answer
    page.wait_for_function("n => window.__writes.cls >= n", arg=2 * len(RIDS), timeout=15000)
    assert page.evaluate("() => window.__writes.text") == 0


def test_a_radio_scanned_twice_is_still_polled_once(page):
    """One poller per radio, however many badges show it: a second scan for list badges (a later Stacks body) adds
    no request, so a radio still never has a second request out."""
    seen, settled = _open(page, _live, path="/hardware")
    _wait_list_rendered(page)
    page.evaluate("() => document.dispatchEvent(new CustomEvent('lhpc:bodyloaded',"
                  " {detail: {root: document.getElementById('hw-badge-r1').closest('table')}}))")
    settled()
    assert seen == dict.fromkeys(RIDS, 1), seen
    page.clock.run_for(3500)
    settled()
    assert seen == dict.fromkeys(RIDS, 2), seen


def test_a_list_badge_follows_a_later_change_of_its_daemon(page):
    """The feature itself: after the first poll, a daemon that goes offline and comes back changes its list badge on
    the next poll each time (the server rendered the page once)."""
    up = [True]
    seen, settled = _open(page, lambda route, band: _live(route, band) if up[0] else route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"reachable": False, "ready": False, "label": "daemon offline"})),
        path="/hardware")
    _wait_list_rendered(page)
    for n, (state, text, cls) in enumerate(((False, "daemon offline", "stopped"), (True, "daemon READY",
                                                                                   "running")), 2):
        up[0] = state
        page.clock.run_for(3500)
        settled()
        assert seen == dict.fromkeys(RIDS, n), seen                     # the next poll, at the normal cadence
        page.wait_for_function(
            "([rids, t, c]) => rids.every(r => { const b = document.getElementById('hw-badge-' + r);"
            " return b.textContent === t && b.className === 'badge badge-' + c; })",
            arg=[list(RIDS), text, cls], timeout=15000)

"""UI-REORG c6 (plan v7 s5), in a real browser: a static full log page refreshes every 3 s from its preview source
with its history depth, so a log of more than 30 lines still shows all of them after a poll. Time is Playwright's
clock (installed, then the page loaded again), never slept. Requests are counted at their route; a __settled__ round
trip before each count proves every request issued before it reached the handler (a request event alone could arrive
after the count: a25 c17 N2)."""
from __future__ import annotations

import re


def _count(page, pattern):
    seen = []
    now = page.evaluate("() => Date.now()") / 1000      # the real time, in SECONDS (Python Playwright's unit)
    page.clock.install(time=now - 60)    # a minute back: a busy runner's stall cannot put the pause below in the past
    page.clock.pause_at(now + 1)          # paused: only the test moves the page's time
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    page.route(re.compile(pattern), lambda route: (seen.append(route.request.url), route.fallback()))
    page.reload()
    return seen


def _settle(page):
    assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204


def test_a_controller_log_of_50_lines_shows_50_after_each_3_s_poll(flask_page, tmp_path):
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs" / "lhpc-web.log").write_text("".join(f"line {i}\n" for i in range(50)))
    page, _posts = flask_page(path="/controller/logs")
    seen = _count(page, r"/api/logpreview/controller-web\?lines=300$")
    page.wait_for_function("() => document.getElementById('logbox').textContent.split('\\n').length === 50",
                           timeout=15000)
    _settle(page)
    n = len(seen)
    page.clock.run_for(3000)
    _settle(page)
    assert len(seen) == n + 1, seen                                       # every 3 s (was 5 s)
    assert page.evaluate("() => document.getElementById('logbox').textContent.split('\\n').length") == 50


def test_the_rf_log_page_polls_every_3_s(flask_page, tmp_path):
    page, _posts = flask_page(path="/logs/loraham-daemon?job=r1/rf-daemon.log&radio=r1")
    assert page.locator("#rfview").count() == 1                           # the RF log's own content (rflog.js)
    seen = _count(page, r"/api/rflog/loraham-daemon\?")
    _settle(page)
    n = len(seen)
    page.clock.run_for(3000)
    _settle(page)
    assert len(seen) == n + 1, seen                                       # every 3 s (was 5 s)

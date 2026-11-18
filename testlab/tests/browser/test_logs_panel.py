"""LOGS-IN-TREE (plan v4 N2-N4, test 4), in a real browser: a log entry's panel on the Apps page asks for nothing while
it is closed, exactly once when it is opened, once per Reload and never on its own (no timer: none after 15 s); it
shows the LAST 30 lines (an RF log: the last 30 records, raw) as text, and its status line says what happened.

The log APIs are answered by the test (counted at the route); time is Playwright's clock, advanced and never slept."""
from __future__ import annotations

import json
import re

LOGS = re.compile(r".*/api/(logs|rflog|logpreview)/.*")


def _serve(page, answer):
    """Route the log APIs to `answer(route)`; return the requests seen and a `settled()` that returns once every
    request issued before it has reached the handler."""
    seen = []

    def handler(route):
        seen.append(route.request.url)
        answer(route)

    page.clock.install()
    page.route(LOGS, handler)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    return seen, settled


def _json(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _status(page, panel, text):
    page.wait_for_function("([p, t]) => document.querySelector(p + ' .logpanel-status').textContent === t",
                           arg=[panel, text], timeout=15000)


def test_a_log_panel_asks_once_on_open_once_per_reload_and_never_by_itself(page):
    seen, settled = _serve(page, lambda route: _json(route, {"source": "controller-web",
                                                            "lines": [f"line {i}" for i in range(40)]}))
    page.goto(page.lab_base + "/stacks/controller/menu/controller-row", wait_until="load")
    page.clock.run_for(15000)
    settled()
    assert seen == []                                                     # closed: nothing
    page.click("#controller-logs > summary")
    _status(page, "#controller-logs", "updated")
    settled()
    assert [u.rsplit("/", 1)[1] for u in seen] == ["controller-web"]       # opened: exactly one
    assert page.text_content("#controller-logs .logpanel-text").split("\n") == [f"line {i}" for i in range(10, 40)]
    page.evaluate("() => document.querySelector('#controller-logs .logpanel-status').textContent = ''")
    page.click("#controller-logs .logpanel-reload")
    _status(page, "#controller-logs", "updated")
    settled()
    assert len(seen) == 2                                                   # one per Reload
    assert page.evaluate("() => document.activeElement.classList.contains('logpanel-reload')")   # focus stays
    page.click("#controller-logs > summary")                                # closed and opened again: no request
    page.click("#controller-logs > summary")
    page.clock.run_for(15000)
    settled()
    assert len(seen) == 2                                                   # and none on its own
    page.select_option("#controller-logs .logpanel-src", label="boot-restore")    # a source change: one request
    settled()
    assert seen[-1].endswith("/api/logpreview/controller-boot-restore") and len(seen) == 3


def test_an_rf_log_panel_shows_the_last_30_records_raw(page):
    seen, settled = _serve(page, lambda route: _json(route, {"records": [{"raw": f"rec {i}", "decoded": "x"}
                                                                         for i in range(35)]}))
    page.goto(page.lab_base + "/stacks/meshcom/menu/logs-meshcom", wait_until="load")
    page.click("#rflog-meshcom > summary")
    _status(page, "#rflog-meshcom", "updated")
    settled()
    assert [u.split("/api/", 1)[1] for u in seen] == ["rflog/meshcom-bridge?job=rf-meshcom.log"]
    assert page.text_content("#rflog-meshcom .logpanel-text").split("\n") == [f"rec {i}" for i in range(5, 35)]


def test_an_empty_log_and_a_failed_fetch_say_so(page):
    # the failure is an unreadable body: an HTTP or network error takes the same path, but Chromium logs either as a
    # console error, which this lane's page fixture fails on (as test_dash_polling's failed poll)
    answers = [lambda route: _json(route, {"lines": []}),
               lambda route: route.fulfill(status=200, content_type="application/json", body="not json")]
    _seen, _settled = _serve(page, lambda route: answers.pop(0)(route=route))
    page.goto(page.lab_base + "/stacks/meshcom/menu/logs-meshcom", wait_until="load")
    page.click("#log-meshcom-bridge > summary")
    _status(page, "#log-meshcom-bridge", "no entries yet")
    page.click("#log-meshcom-bridge .logpanel-reload")
    _status(page, "#log-meshcom-bridge", "could not load the log (reload or open the page)")
    assert page.text_content("#log-meshcom-bridge .logpanel-text") == ""

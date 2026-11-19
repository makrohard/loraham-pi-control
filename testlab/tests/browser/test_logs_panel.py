"""LOGS-IN-TREE (plan v4 N2-N4, test 4), in a real browser: a log entry's panel on the Apps page asks for nothing while
it is closed, exactly once each time it is opened, once per Reload and never on its own (no timer: none after 15 s); it
shows the LAST 30 lines (an RF log: the last 30 records, raw) as text, and its status line says what happened; a late
reply to an earlier request (another source picked meanwhile) is dropped.

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
    lines = [f"line {i}" for i in range(39)] + ['<b id="lp-mark">line 39</b>']      # markup stays text
    seen, settled = _serve(page, lambda route: _json(route, {"source": "controller-web", "lines": lines}))
    page.goto(page.lab_base + "/stacks/controller/menu/controller-row", wait_until="load")
    page.clock.run_for(15000)
    settled()
    assert seen == []                                                     # closed: nothing
    page.click("#controller-logs > summary")
    _status(page, "#controller-logs", "updated")
    settled()
    assert [u.rsplit("/", 1)[1] for u in seen] == ["controller-web"]       # opened: exactly one
    assert page.text_content("#controller-logs .logpanel-text").split("\n") == lines[10:]
    assert page.evaluate("() => document.getElementById('lp-mark') === null")
    page.evaluate("() => document.querySelector('#controller-logs .logpanel-status').textContent = ''")
    page.click("#controller-logs .logpanel-reload")
    _status(page, "#controller-logs", "updated")
    settled()
    assert len(seen) == 2                                                   # one per Reload
    assert page.evaluate("() => document.activeElement.classList.contains('logpanel-reload')")   # focus stays
    page.click("#controller-logs > summary")                                # closed: no request
    with page.expect_request(re.compile(r".*/api/logpreview/controller-web$")):   # opened again: a fresh one
        page.click("#controller-logs > summary")
    page.clock.run_for(15000)
    settled()
    assert len(seen) == 3                                                   # exactly one, and none on its own
    page.select_option("#controller-logs .logpanel-src", label="boot-restore")    # a source change: one request
    settled()
    assert seen[-1].endswith("/api/logpreview/controller-boot-restore") and len(seen) == 4


def test_a_late_reply_to_the_previous_source_is_dropped(page):
    held = []

    def answer(route):
        if route.request.url.endswith("/controller-web"):
            held.append(route)                                              # the first source's reply comes late
        else:
            _json(route, {"source": "controller-boot-restore", "lines": ["BOOT-RESTORE"]})
    # counts the JSON bodies read, so the test knows when the late reply has reached the panel's handler
    page.add_init_script("""(() => { const j = Response.prototype.json; window.lpJson = 0;
        Response.prototype.json = function () { return j.call(this).then(v => { window.lpJson += 1; return v; }); };
    })()""")
    _seen, settled = _serve(page, answer)
    page.goto(page.lab_base + "/stacks/controller/menu/controller-row", wait_until="load")
    with page.expect_request(re.compile(r".*/api/logpreview/controller-web$")):   # toggle is a queued task: wait
        page.click("#controller-logs > summary")
    settled()
    assert len(held) == 1
    page.select_option("#controller-logs .logpanel-src", label="boot-restore")
    _status(page, "#controller-logs", "updated")
    page.evaluate("() => document.querySelector('#controller-logs .logpanel-status').textContent = ''")
    held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"source": "controller-web",
                                                                                  "lines": ["WEB"]}))
    page.wait_for_function("() => window.lpJson === 2", timeout=15000)
    settled()                                                               # a task later: its handler has run
    assert page.text_content("#controller-logs .logpanel-text") == "BOOT-RESTORE"
    assert page.text_content("#controller-logs .logpanel-status") == ""
    assert page.evaluate("() => document.querySelector('#controller-logs .logpanel-src').selectedOptions[0].text") \
        == "boot-restore"


def test_an_rf_log_panel_shows_the_last_30_records_raw(page):
    seen, settled = _serve(page, lambda route: _json(route, {"records": [{"raw": f"rec {i}", "decoded": "x"}
                                                                         for i in range(35)]}))
    page.goto(page.lab_base + "/stacks/meshcom/menu/logs-meshcom--per-radio", wait_until="load")   # c7: its group
    page.click("#rflog-meshcom > summary")
    _status(page, "#rflog-meshcom", "updated")
    settled()
    assert [u.split("/api/", 1)[1] for u in seen] == ["rflog/meshcom-bridge?job=r1/rf-meshcom.log"]   # #75: r1's own file
    assert page.text_content("#rflog-meshcom .logpanel-text").split("\n") == [f"rec {i}" for i in range(5, 35)]


def test_an_empty_log_and_a_failed_fetch_say_so(page):
    # the failure is an unreadable body: an HTTP or network error takes the same path, but Chromium logs either as a
    # console error, which this lane's page fixture fails on (as test_dash_polling's failed poll)
    answers = [lambda route: _json(route, {"lines": []}),
               lambda route: route.fulfill(status=200, content_type="application/json", body="not json")]
    _seen, _settled = _serve(page, lambda route: answers.pop(0)(route=route))
    page.goto(page.lab_base + "/stacks/meshcom/menu/logs-meshcom--per-radio", wait_until="load")   # c7: its group
    page.click("#log-meshcom-bridge > summary")
    _status(page, "#log-meshcom-bridge", "no entries yet")
    page.click("#log-meshcom-bridge .logpanel-reload")
    _status(page, "#log-meshcom-bridge", "could not load the log (reload or open the page)")
    assert page.text_content("#log-meshcom-bridge .logpanel-text") == ""


def test_a_radio_switch_click_on_the_logs_page_lands_with_the_switch_shown(page):
    # F9 B1: on the Logs level's page the switch sits in the closed Per radio group; a click on one of its radios
    # lands on a page where Per radio (with the switch just used) is open, not hidden in a closed group.
    page.goto(page.lab_base + "/stacks/meshcom/menu/logs-meshcom", wait_until="load")
    page.click("#logs-meshcom--per-radio > summary")
    links = page.locator("#logs-meshcom--per-radio nav.rswitch a")
    assert links.count() >= 1
    links.last.click()
    page.wait_for_load_state("load")
    assert page.evaluate("() => document.getElementById('logs-meshcom--per-radio').open")
    assert page.locator("#logs-meshcom--per-radio nav.rswitch").is_visible()

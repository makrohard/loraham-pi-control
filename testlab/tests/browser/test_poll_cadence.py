"""The console's slow polls, in a real browser: the dashboard asks whether anything structural
changed every 10 s (every 2 s while an interactive start is pending), and a log page asks for new
lines every 3 s (UI-REORG c6; was 5 s).

Each poll is counted at its route; time is Playwright's clock, advanced by the test and never
slept through. What is asserted is the number of requests in a window.
"""
from __future__ import annotations

import pytest


def _settle(page):
    """Every request the page issued before this call has reached its route handler."""
    assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204


def _count(page, pattern, answer=None):
    seen = {"n": 0}

    def handler(route):
        seen["n"] += 1
        if answer is None:
            route.continue_()
        else:
            answer(route)
    page.route(pattern, handler)
    return seen


def _paused_clock(page):
    """Playwright's clock, paused before the page loads: installed alone it runs with real time, and the real time a
    load and a settle take counts into the test's windows (a 10 s / 3 s poll fired inside "9.9 s" / "2.9 s" on a
    busy run: train A testlab 44525282762)."""
    page.clock.install()
    page.clock.pause_at(page.evaluate("() => Date.now()") / 1000 + 1)   # Python Playwright: a number is in SECONDS


def _open_dashboard(page, pending=False):
    _paused_clock(page)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    # "nothing structural changed": the answer never reloads the page under the test
    sig = _count(page, "**/api/dash-signature", lambda route: route.fulfill(
        status=200, content_type="application/json", body="null"))
    page.route("**/api/daemon/*", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"reachable": false}'))
    if pending:
        # The page marks a pending interactive start on the grid it renders; set it before any
        # script runs, as the server does then.
        page.add_init_script(
            "new MutationObserver(() => { const g = document.querySelector('.radiogrid');"
            " if (g) g.setAttribute('data-pending-interactive', '1'); })"
            ".observe(document, {childList: true, subtree: true});")
    page.goto(page.lab_base + "/", wait_until="load")
    _settle(page)
    assert sig["n"] == 0
    return sig


def test_the_dashboard_checks_for_structural_changes_every_10_s(page):
    sig = _open_dashboard(page)
    page.clock.run_for(9900)
    _settle(page)
    assert sig["n"] == 0
    page.clock.run_for(200)
    _settle(page)
    assert sig["n"] == 1
    page.clock.run_for(10000)
    _settle(page)
    assert sig["n"] == 2


def test_while_a_start_is_pending_the_dashboard_checks_every_2_s(page):
    sig = _open_dashboard(page, pending=True)
    page.clock.run_for(2100)
    _settle(page)
    assert sig["n"] == 1
    page.clock.run_for(2000)
    _settle(page)
    assert sig["n"] == 2


def _open_log(page, path, pattern):
    _paused_clock(page)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    seen = _count(page, pattern)
    page.goto(page.lab_base + path, wait_until="load")
    _settle(page)
    assert seen["n"] == 1                          # the first read at load
    return seen


def test_a_process_log_asks_for_new_lines_every_3_s(page):
    seen = _open_log(page, "/logs/loraham-kiss-tnc", "**/api/logs/**")
    page.clock.run_for(2900)
    _settle(page)
    assert seen["n"] == 1
    page.clock.run_for(200)
    _settle(page)
    assert seen["n"] == 2


def test_an_rf_log_asks_for_new_records_every_3_s(page, seeded_rf_logs):
    seen = _open_log(page, "/logs/loraham-kiss-tnc?job=rf-kiss.log", "**/api/rflog/**")
    page.clock.run_for(2900)
    _settle(page)
    assert seen["n"] == 1
    page.clock.run_for(200)
    _settle(page)
    assert seen["n"] == 2


_HIDE = ("h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});"
         " document.dispatchEvent(new Event('visibilitychange')); }")


@pytest.mark.parametrize("path, pattern", [("/logs/loraham-kiss-tnc", "**/api/logs/**"),
                                           ("/logs/loraham-kiss-tnc?job=rf-kiss.log", "**/api/rflog/**")])
def test_a_log_page_asks_nothing_while_its_tab_is_hidden_and_once_at_once_when_shown(page, seeded_rf_logs,
                                                                                    path, pattern):
    # G2-UXM B1 (plan s13): a page-level poller stops while the tab is hidden; shown, it resumes its 3 s cadence
    # from the request it makes at once (a25 c17 B1: never a shorter gap). Hidden at +1 s, shown at +10 s: off the
    # load's 3 s grid, so an interval kept from the load would tick 2 s after the show (a25 c17 r2 B1)
    seen = _open_log(page, path, pattern)
    page.clock.run_for(1000)
    page.evaluate(_HIDE, True)                                          # a background tab
    page.clock.run_for(9000)
    _settle(page)
    assert seen["n"] == 1                                               # nothing while hidden
    page.evaluate(_HIDE, False)                                         # in front again: one request at once
    _settle(page)
    assert seen["n"] == 2
    page.clock.run_for(2900)                                            # then the 3 s cadence from that request
    _settle(page)
    assert seen["n"] == 2
    page.clock.run_for(200)
    _settle(page)
    assert seen["n"] == 3

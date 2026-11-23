"""The dashboard's signature reload (dash.js) over the real lab server: a structural change reloads the page ONCE,
and a reload that is still loading is never restarted by a later poll. On a box whose dashboard takes longer to
render than the poll interval, a restarted reload never finishes: each one is aborted by the next and the page
stays stale for good.

A slow `GET /` is a held route, so "still loading" is a state, never a race. While a reload is held the page answers
no evaluate, so that test runs on the page's real time and counts the requests the page sends: the polls go on
(their answers are what must not reload again). The others pause the page's clock, so only the test moves the poll."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

POLL_MS = 10_000                  # dash.js's poll interval with nothing pending (the lab dashboard has nothing pending)


def _open(page, sig_answer, *, paused: bool):
    """The dashboard with the signature answered by `sig_answer(rendered sig)`; returns (signature polls, reload
    requests, settled). `paused`: the page's clock is paused before the load, so no poll fires by itself."""
    sigs: list = []
    navs: list = []
    rendered = {"sig": ""}               # read after the load: a route handler never calls back into the page

    def answer(route):
        sigs.append(route)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"sig": sig_answer(rendered["sig"])}))

    if paused:
        now = page.evaluate("() => Date.now()") / 1000
        page.clock.install(time=now - 60)
        page.clock.pause_at(now + 1)
    page.route("**/api/dash-signature", answer)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    page.goto(page.lab_base + "/", wait_until="networkidle")
    assert page.get_attribute("[data-dash-sig]", "data-pending-interactive") == "0"
    rendered["sig"] = page.get_attribute("[data-dash-sig]", "data-dash-sig")
    assert rendered["sig"]
    # from now on every request for the dashboard document is counted and held (a slow `GET /`)
    page.on("request", lambda r: navs.append(r) if r.is_navigation_request() else None)
    page.route(lambda url: urlsplit(url).path == "/", lambda route: None)

    def settled():
        # a round trip from the page: every request issued before it has reached its handler
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    return sigs, navs, settled


def test_a_reload_still_loading_is_never_restarted_by_a_later_poll(page):
    sigs, navs, _settled = _open(page, lambda rendered: rendered + "-changed", paused=False)
    with page.expect_request(lambda r: r.is_navigation_request(), timeout=3 * POLL_MS):
        pass                                           # the first poll sees a new signature -> one reload, held
    for _ in range(3):                                 # three more polls while that reload still loads
        with page.expect_request("**/api/dash-signature", timeout=3 * POLL_MS):
            pass
    # the two answers before the last poll were handled a poll period ago; none started a second reload
    # (the held reload is never answered: the fixture's context close at teardown drops it)
    assert len(navs) == 1, f"{len(navs)} reloads for one change ({len(sigs)} polls)"


def test_an_unchanged_signature_keeps_polling_and_never_reloads(page):
    sigs, navs, settled = _open(page, lambda rendered: rendered, paused=True)
    for n in (1, 2, 3):
        page.clock.run_for(POLL_MS)
        settled()
        assert len(sigs) == n, f"poll {n}: {len(sigs)} signature polls"
    assert navs == [], "an unchanged signature reloaded the page"


def test_a_reload_deferred_by_text_entry_happens_on_the_next_poll(page):
    sigs, navs, settled = _open(page, lambda rendered: rendered + "-changed", paused=True)
    page.evaluate("() => { const i = document.createElement('input'); i.id = 'probe-entry';"
                  " document.body.appendChild(i); i.focus(); }")            # the viewer is typing
    page.clock.run_for(POLL_MS)
    settled()
    assert (len(sigs), len(navs)) == (1, 0), f"{len(sigs)} polls, {len(navs)} reloads while typing"
    page.evaluate("() => document.getElementById('probe-entry').blur()")
    with page.expect_request(lambda r: r.is_navigation_request()):
        page.clock.run_for(POLL_MS)                    # typing ended: the next poll reloads
    assert len(sigs) == 2, f"{len(sigs)} polls"

"""The dashboard's system box, in a real browser with controlled `/api/system` responses.

These are the three behaviours that are genuinely easy to get wrong in the client state
machine, and each one is asserted through what the operator sees, not through the script's
source:

  * an omitted `net` sample must not inflate the rate (the counters did not advance, only
    the timestamp did — dividing a two-interval delta by one interval doubled it);
  * the source-dependent rows must follow the CURRENT sample, so a source that disappears
    takes its row with it instead of leaving a stale value on screen;
  * the clock advances from a LOCAL timer between polls, so animating a digit does not cost
    a second `/api/system` request per second on a Zero 2 W.

The responses are served by intercepting the route in the browser, so the test controls the
exact sample sequence while the REAL page and the REAL system.js do the work.
"""
from __future__ import annotations

import json

# A minimal but REAL `/api/system` payload: the keys are the ones the endpoint actually
# emits (rx_bytes/tx_bytes, *_kb, free_b/total_b), so these tests break if that machine
# contract changes — which is exactly what they should be sensitive to.
BASE = {
    "cpu": {"cores": 2, "percore": [[10, 0, 5, 100, 0, 0, 0, 0], [10, 0, 5, 100, 0, 0, 0, 0]]},
    "mem": {"total_kb": 512 * 1024, "available_kb": 256 * 1024},
    "disk": {"root": {"free_b": 4 * 1024 ** 3, "total_b": 8 * 1024 ** 3}},
    "load": [0.1, 0.1, 0.1],
    "uptime_s": 100,
    "info": {},
}


def _sample(ts, *, net=None, swap=False, power=False, time_row=False):
    """One `/api/system` payload. The OMITTED keys are the point of these tests."""
    d = json.loads(json.dumps(BASE))
    d["ts"] = ts
    if net is not None:
        d["net"] = {"rx_bytes": net, "tx_bytes": 0}
    if swap:
        d["mem"]["swap_total_kb"] = 1024
        d["mem"]["swap_free_kb"] = 512
    if power:
        d["power"] = {"source": "hwmon-alarm", "undervolt_alarm": False}
    if time_row:
        # `epoch` AND `utc` are what anchor the local 1 Hz clock (system.js:297) — without
        # both, the row shows the sent string and never ticks.
        d["time"] = {"local": "2026-01-01 13:00:00", "utc": "2026-01-01 12:00:00",
                     "tz": "CET", "label": "synced", "epoch": 1767268800.0,
                     "daemons": ["chrony"]}
    return d


def _open_box(page):
    """The system box polls only while its <details> is open — open it and wait for the
    first sample to land, so each test starts from a known state."""
    page.goto(page.lab_base + "/", wait_until="networkidle")
    box = page.locator("#sysbox")
    box.wait_for(state="attached", timeout=15000)
    if not box.evaluate("e => e.open"):
        box.locator("summary").first.click()
    page.wait_for_function("() => document.getElementById('sysbox').open", timeout=15000)


def _serve(page, samples, *, then_silent=False):
    """Answer `/api/system` from `samples`, in order.

    After the list is exhausted the last sample repeats, or — with `then_silent` — the request is
    left HANGING, which is how a test observes what the page does with no new data. Hanging, not
    aborted: an aborted request is a console error, and every test here fails on those.
    """
    state = {"i": 0}

    def handler(route):
        i = state["i"]
        state["i"] += 1
        if i >= len(samples):
            if then_silent:
                # Never answered. Playwright prints one asyncio CancelledError at context
                # close for a route still pending — a teardown artifact, not a failure.
                return
            i = len(samples) - 1
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(samples[i]))

    page.route("**/api/system*", handler)
    return state


def test_an_omitted_net_sample_does_not_inflate_the_rate(page):
    # 4000 B over four seconds is 1.0 kB/s. The bug divided the whole delta by ONE
    # interval because the omitted middle sample still advanced the timestamp.
    _serve(page, [_sample(0, net=0), _sample(2), _sample(4, net=4000)])
    _open_box(page)
    page.wait_for_function(
        "() => { const e = document.getElementById('sys-net-val');"
        " return e && /kB\\/s/.test(e.textContent); }", timeout=20000)
    text = page.locator("#sys-net-val").inner_text()
    assert "2.0 kB/s" not in text, f"the omitted sample doubled the rate: {text!r}"


def test_an_optional_row_disappears_with_its_source(page):
    # Sample 1 proves swap and power; sample 2 omits both. The rows must follow the
    # current sample, never keep the previous value on screen.
    _serve(page, [_sample(0, net=0, swap=True, power=True), _sample(1, net=0)])
    _open_box(page)
    page.wait_for_function(
        "() => { const r = document.getElementById('sys-swap-row');"
        " return r && !r.hidden; }", timeout=15000)
    page.wait_for_function(
        "() => { const r = document.getElementById('sys-swap-row');"
        " return r && r.hidden; }", timeout=15000)
    assert page.locator("#sys-power-row").is_hidden()


def test_the_clock_advances_without_a_new_request(page):
    # One response, then no more: the displayed time must still move, from the local
    # timer anchored on that response.
    # One response, then the endpoint goes silent: each poll RE-anchors the clock, so only a
    # sample-free interval can show that the seconds move from the local timer.
    _serve(page, [_sample(0, net=0, time_row=True)], then_silent=True)
    _open_box(page)
    page.wait_for_function(
        "() => { const e = document.getElementById('sys-time-val');"
        " return e && e.textContent.trim().length > 0; }", timeout=15000)
    first = page.locator("#sys-time-val").inner_text()
    page.wait_for_function(
        "(prev) => { const e = document.getElementById('sys-time-val');"
        " return e && e.textContent !== prev; }", arg=first, timeout=15000)
    assert page.locator("#sys-time-val").inner_text() != first

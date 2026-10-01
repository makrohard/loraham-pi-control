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

import pytest

# A minimal but REAL `/api/system` payload: the keys are the ones the endpoint actually
# emits (rx_bytes/tx_bytes, *_kb, free_b/total_b), so these tests break if that machine
# contract changes — which is exactly what they should be sensitive to.
BASE = {
    "cpu": {"cores": 2, "percore": [[10, 0, 5, 100, 0, 0, 0, 0], [10, 0, 5, 100, 0, 0, 0, 0]]},
    "mem": {"total_kb": 512 * 1024, "available_kb": 256 * 1024},
    "disk": {"root": {"free_b": 4 * 1024 ** 3, "total_b": 8 * 1024 ** 3, "free_inodes": 90000,
                      "total_inodes": 100000, "level": "ok", "reason": ""}},
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
        # `epoch` AND `utc` are what anchor the local 1 Hz clock — without both, the row
        # shows the sent string and never ticks.
        d["time"] = {"local": "2026-01-01 13:00:00", "utc": "2026-01-01 12:00:00",
                     "tz": "CET", "label": "NTP", "epoch": 1767268800.0,
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


@pytest.mark.parametrize("level, reason, cls, inode_text", [
    ("low", "bytes", "sys-warn", False),
    ("critical", "bytes", "sys-crit", False),
    ("critical", "inodes", "sys-crit", True),
    ("ok", "", "", False),
])
def test_the_disk_row_follows_the_servers_level(page, level, reason, cls, inode_text):
    # The colour is the server's classifier's `level`; system.js holds no threshold. The disk is
    # half used in every sample, so the old used-% rule would have given NO class at all.
    s = _sample(0, net=0)
    s["disk"]["root"].update({"level": level, "reason": reason, "free_inodes": 4000})
    _serve(page, [s])
    _open_box(page)
    page.wait_for_function(
        "() => { const e = document.getElementById('sys-disk-val');"
        " return e && e.textContent.trim() && e.textContent.trim() !== '…'; }", timeout=15000)
    row_cls = page.locator("#sys-disk-bar").evaluate("e => e.closest('tr').className")
    for c in ("sys-warn", "sys-crit"):
        assert (c in row_cls) == (c == cls), (level, row_cls)
    text = page.locator("#sys-disk-val").inner_text()
    assert ("% inodes free" in text) == inode_text, text


def test_an_omitted_net_sample_does_not_inflate_the_rate(page):
    # 4000 B over four seconds is 1.0 kB/s. The bug divided the whole delta by ONE
    # interval because the omitted middle sample still advanced the timestamp.
    _serve(page, [_sample(0, net=0), _sample(2), _sample(4, net=4000)])
    _open_box(page)
    page.wait_for_function(
        "() => { const e = document.getElementById('sys-net-val');"
        " return e && /kB\\/s/.test(e.textContent); }", timeout=20000)
    text = page.locator("#sys-net-val").inner_text()
    assert "1.0 kB/s" in text, f"expected 1.0 kB/s; the omitted sample doubled the rate: {text!r}"


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


# --- GPS row: the GPS Monitor's /api/gps, riding on the box's own poll ------------------------
# Payloads carry the keys the row reads from `service.gps_monitor()` (state, label, lat, lon).

def _gps(state, label, lat=None, lon=None):
    return {"source": "gpsd", "state": state, "label": label, "lat": lat, "lon": lon}


def _serve_gps(page, payload):
    """Answer `/api/gps` with `payload` and count the requests."""
    seen = {"n": 0}

    def handler(route):
        seen["n"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/gps", handler)
    return seen


def _gps_row(page):
    """The GNSS row as the operator reads it: (pill text, pill colour class, coordinates)."""
    cls = [c for c in (page.locator("#sys-gps-state").get_attribute("class") or "").split()
           if c.startswith("pill-")]
    return (page.locator("#sys-gps-state").text_content(), cls[0] if cls else "plain",
            " ".join(page.locator("#sys-gps-val").text_content().split()))


def _wait_gps_state(page, text):
    page.wait_for_function(
        "(t) => { const e = document.getElementById('sys-gps-state'); return e && e.textContent === t; }",
        arg=text, timeout=15000)


def test_gps_row_shows_the_fix_with_the_monitors_precision(page):
    _serve(page, [_sample(0, net=0)])
    _serve_gps(page, _gps("3d", "3D fix", 48.123456789, -9.87654321))
    _open_box(page)
    _wait_gps_state(page, "3D fix")
    assert _gps_row(page) == ("3D fix", "pill-ok", "48.123457 -9.876543")


def test_gps_row_without_a_fix_shows_the_state_and_no_coordinates(page):
    _serve(page, [_sample(0, net=0)])
    _serve_gps(page, _gps("no-fix", "no fix"))
    _open_box(page)
    _wait_gps_state(page, "no fix")
    assert _gps_row(page) == ("no fix", "pill-bad", "—")


def test_gps_row_without_a_receiver_shows_no_position_source(page):
    _serve(page, [_sample(0, net=0)])
    _serve_gps(page, {"source": "off", "state": "off", "label": "no position source",
                      "lat": None, "lon": None})
    _open_box(page)
    _wait_gps_state(page, "off")
    assert _gps_row(page) == ("off", "plain", "—")
    assert page.locator("#sys-gps-state").get_attribute("title") == "no position source"


@pytest.mark.parametrize("state, label, word, lat, cls", [
    ("2d", "2D fix", "2D fix", 48.4, "pill-warn"),
    ("fix", "fix (dimension unknown)", "fix", 48.4, "pill-warn"),
    ("stale", "stale — no navigation data for a while", "stale", None, "pill-bad"),
    ("gpsd-no-data", "gpsd device present, no position data yet", "no data", None, "pill-bad"),
    ("fixed", "fixed position (configured)", "fixed", 48.4, "plain"),
    ("auto-off", "auto: no gpsd on this box — no position", "no GPS", None, "plain"),
])
def test_gnss_pill_colour_for_every_state_class(page, state, label, word, lat, cls):
    # The approved mapping: green 3D (above), yellow 2D / unknown dimension, red a receiver
    # without navigation, plain for no GPS and for states that are not a receiver's fix.
    _serve(page, [_sample(0, net=0)])
    _serve_gps(page, _gps(state, label, lat, None if lat is None else 11.6))
    _open_box(page)
    _wait_gps_state(page, word)
    assert _gps_row(page)[1] == cls
    assert page.locator("#sys-gps-state").get_attribute("title") == label   # full label as tooltip


def test_gps_row_escapes_what_the_server_sends(page):
    # textContent, never innerHTML: a label carrying markup renders as text.
    _serve(page, [_sample(0, net=0)])
    _serve_gps(page, _gps("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"))
    _open_box(page)
    _wait_gps_state(page, "<img src=x onerror=alert(1)>")
    assert page.locator("#sys-gps img").count() == 0


def _settled(page):
    """A network round trip from the page, answered by the test: every request the page issued
    before it has reached the route handlers by the time it returns."""
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
    assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204


def test_a_closed_box_makes_no_request_and_an_open_one_asks_gps_every_second_poll(page):
    # The page's timers run on Playwright's clock, which the test advances: the poll cadence is
    # proven over simulated seconds, not by sleeping through real ones.
    page.clock.install()
    # Each answer carries its own uptime, so the footer shows which sample the page has applied.
    sysn = _serve(page, [dict(_sample(k, net=0), uptime_s=60 * (k + 1)) for k in range(4)])
    gps = _serve_gps(page, _gps("3d", "3D fix", 1.0, 2.0))
    page.goto(page.lab_base + "/", wait_until="networkidle")
    # Closed (the default): nothing at all is fetched, neither /api/system nor /api/gps.
    assert not page.locator("#sysbox").evaluate("e => e.open")
    page.clock.run_for(5000)                          # 2.5 poll intervals
    _settled(page)
    assert (sysn["i"], gps["n"]) == (0, 0)
    assert _gps_row(page) == ("…", "plain", "")
    # Open: /api/gps rides on the poll — the first tick, then every second one.
    page.locator("#sysbox summary").first.click()
    _wait_gps_state(page, "3D fix")
    for k in range(4):
        if k:
            page.clock.run_for(2000)                  # one poll interval
        page.wait_for_function(
            "(t) => { const e = document.getElementById('sys-info');"
            " return e && e.textContent.includes(t); }", arg=f"0h {k + 1}m", timeout=15000)
    polls, asks = sysn["i"], gps["n"]
    assert polls == 4 and 1 <= asks <= (polls + 1) // 2, (polls, asks)
    # Closed again: both stop.
    page.locator("#sysbox summary").first.click()
    page.wait_for_function("() => !document.getElementById('sysbox').open", timeout=5000)
    _settled(page)
    frozen = (sysn["i"], gps["n"])
    page.clock.run_for(5000)
    _settled(page)
    assert (sysn["i"], gps["n"]) == frozen



# --- Network row: the Wi-Fi pill's colour bands on integers, exactly as approved --------------
# The lab box is a Wi-Fi client ("LabNet" on wlan0), so the server renders the Wi-Fi pill; the
# test drives the REAL system.js with controlled `wifi` samples.

def test_network_row_wifi_pill_bands_and_alignment(page):
    samples = []
    for i, dbm in enumerate((-67, -68, -75, -76, None)):
        s = _sample(i, net=0, time_row=True)
        if dbm is not None:
            s["wifi"] = {"wlan0": dbm}
        samples.append(s)
    _serve(page, samples)
    _open_box(page)
    assert page.locator("#sys-wifi-pill").get_attribute("data-dev") == "wlan0"
    for text, cls in (("-67 dBm", "pill-ok"), ("-68 dBm", "pill-warn"),
                      ("-75 dBm", "pill-warn"), ("-76 dBm", "pill-bad")):
        page.wait_for_function(
            "(t) => { const p = document.getElementById('sys-wifi-pill'); return p && p.textContent === t; }",
            arg=text, timeout=15000)
        classes = page.locator("#sys-wifi-pill").get_attribute("class").split()
        assert [c for c in classes if c.startswith("pill-")] == [cls], (text, classes)
    # Time, Network (and GNSS) share one layout: pill column and text column line up.
    geo = page.evaluate("""() => { const x = (id) => document.getElementById(id).getBoundingClientRect().left;
        return {tp: x('sys-time-pill'), np: x('sys-wifi-pill'), gp: x('sys-gps-state'),
                tt: x('sys-time-val'), nt: x('sys-link-val'), gt: x('sys-gps-val')}; }""")
    assert max(geo["tp"], geo["np"], geo["gp"]) - min(geo["tp"], geo["np"], geo["gp"]) <= 1, geo
    assert max(geo["tt"], geo["nt"], geo["gt"]) - min(geo["tt"], geo["nt"], geo["gt"]) <= 1, geo
    # a sample without `wifi` (the link went away) returns the pill to plain "Wi-Fi"
    page.wait_for_function(
        "() => { const p = document.getElementById('sys-wifi-pill'); return p.textContent === 'Wi-Fi' && !/pill-(ok|warn|bad)/.test(p.className); }",
        timeout=15000)

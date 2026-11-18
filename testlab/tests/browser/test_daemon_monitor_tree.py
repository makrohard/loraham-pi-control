"""DAEMON-MONITOR-TREE (0.14.0, maintainer's word 11-18), in a real browser: a radio's four live daemon views under its
Monitor ask only while their panel is open: Get STATUS / Get STATS one read per open and per Reload; View Socket a
poll each second, RX/TX View one every 3 s, both stopped by closing; a view's own page asks from load; a reply to a
run that was closed meanwhile is dropped. The daemon APIs are answered by the test (counted at the route); time is
Playwright's clock, advanced and never slept."""
from __future__ import annotations

import json
import re

RID = "r3"                                           # flask_page's USB radio (its daemon answers: unix_replies)
_READY = {"/tmp/loraconf-r3.sock": b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"}
MONITOR = f"/stacks/daemon/menu/stack-settings-daemon--{RID}-monitor?radio={RID}"
_REPLY = {"": {"reachable": True, "status": {"RADIO": "READY"}, "stats": {"RX": "7"}},
          "/socket": {"reachable": True, "line": "STATUS RADIO=READY"},
          "/feed": {"feed": ["RX one", "TX two"]}}
API = re.compile(r".*/api/daemon/" + RID + r"(/socket|/feed)?$")


def _serve(page, held=None):
    """Answer the daemon APIs, counting them by path ("", "/socket", "/feed"); `held`: a list that keeps the socket
    replies instead (answered later by the test). Returns (seen, settled)."""
    seen = []

    def handler(route):
        tail = API.match(route.request.url).group(1) or ""
        seen.append(tail)
        if held is not None and tail == "/socket":
            held.append(route)
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[tail]))

    page.clock.install()
    page.route(API, handler)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    return seen, settled


def _sel(kind):
    return f"#stack-settings-daemon--{RID}-{kind}"


def _close(page, kind):
    """Close the view's panel and wait for its toggle (a queued task) to have stopped it, before the clock moves."""
    page.click(_sel(kind) + " > summary")
    page.wait_for_function("s => !document.querySelector(s).open && !document.querySelector(s).dvRunning",
                           arg=_sel(kind), timeout=15000)


def _text(page, kind):
    return page.text_content(_sel(kind) + " .daemonview-text")


def test_status_and_stats_ask_once_per_open_and_per_reload_and_never_while_closed(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled = _serve(page)
    page.clock.run_for(15000)
    settled()
    assert seen == []                                                          # all closed: nothing
    for kind, want in (("status", "RADIO=READY"), ("stats", "RX=7")):
        before = len(seen)
        page.click(_sel(kind) + " > summary")
        page.wait_for_function("([s, w]) => document.querySelector(s + ' .daemonview-text').textContent.includes(w)",
                               arg=[_sel(kind), want], timeout=15000)
        assert _text(page, kind).startswith(f"{kind.upper()} — {RID} (live)")
        page.click(_sel(kind) + " .daemonview-reload")
        settled()
        assert seen[before:] == ["", ""]                                     # one per open, one per Reload
        _close(page, kind)
        page.clock.run_for(15000)
        settled()
        assert len(seen) == before + 2                                       # no timer


def test_the_socket_view_polls_each_second_only_while_open(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled = _serve(page)
    page.click(_sel("socket") + " > summary")
    page.wait_for_function("s => document.querySelector(s + ' .daemonview-text').textContent.includes('RADIO=READY')",
                           arg=_sel("socket"), timeout=15000)
    page.clock.run_for(3000)
    settled()
    assert seen == ["/socket"] * 4                                           # at once, then each second
    assert len(_text(page, "socket").split("\n")) == 4                       # the rolling window grows to 22
    page.click(_sel("socket") + " .daemonview-reload")                       # afresh: the window starts over
    settled()
    assert len(seen) == 5
    page.wait_for_function("s => document.querySelector(s + ' .daemonview-text').textContent.split('\\n').length === 1",
                           arg=_sel("socket"), timeout=15000)
    _close(page, "socket")                                                    # closed: the poll stops
    page.clock.run_for(5000)
    settled()
    assert len(seen) == 5


def test_the_rxtx_view_polls_every_3_s_only_while_open(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled = _serve(page)
    page.click(_sel("rxtx") + " > summary")
    page.wait_for_function("s => document.querySelector(s + ' .daemonview-text').textContent === 'RX one\\nTX two'",
                           arg=_sel("rxtx"), timeout=15000)
    page.clock.run_for(6000)
    settled()
    assert seen == ["/feed"] * 3
    _close(page, "rxtx")
    page.clock.run_for(9000)
    settled()
    assert len(seen) == 3


def test_a_views_own_page_asks_from_load(flask_page):
    page, _posts = flask_page(path=f"/stacks/daemon/menu/stack-settings-daemon--{RID}-status?radio={RID}",
                              unix_replies=_READY, api={f"/api/daemon/{RID}": _REPLY[""]})
    page.wait_for_function("s => document.querySelector(s + ' .daemonview-text').textContent.includes('RADIO=READY')",
                           arg=_sel("status"), timeout=15000)
    assert page.evaluate("s => document.querySelector(s).open", _sel("status"))


def test_a_reply_to_a_closed_run_is_dropped(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    held = []
    _seen, settled = _serve(page, held=held)
    with page.expect_request(API):                                            # toggle is a queued task: wait
        page.click(_sel("socket") + " > summary")
    settled()
    assert len(held) == 1
    before = _text(page, "socket")
    _close(page, "socket")                                                    # closed before the reply
    held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"reachable": True, "line": "LATE"}))
    settled()
    page.evaluate("() => new Promise(r => setTimeout(r, 0))")
    assert _text(page, "socket") == before and "LATE" not in _text(page, "socket")

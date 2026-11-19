"""DAEMON-MONITOR-TREE (0.14.0, maintainer's word 11-18) + UI-REORG c4 (plan v7 s3/s4), in a real browser: a radio's
four live daemon views under its Monitor are always open and ask only while SHOWN (every <details> around them open):
Get STATUS / Get STATS one read per show and per Reload; View Socket a poll each second, RX/TX View one every 3 s, both
stopped by hiding; a view's own page asks from load; a reply to a run that was hidden meanwhile is dropped; one request
out per view, and a show while one is out asks once more when it settles. Daemon down: paused ("Daemon not running",
Reload off) until the badge's poll (dash.js, the same /api/daemon/<rid>) sees it up; Live greys and re-enables the
same way; a block inserted later takes the cached state at once. The daemon APIs are answered by the test (counted at
the route; the badge poll is told apart by its Accept header); time is Playwright's clock, advanced and never slept."""
from __future__ import annotations

import json
import re

RID = "r3"                                           # flask_page's USB radio (its daemon answers: unix_replies)
_READY = {"/tmp/loraconf-r3.sock": b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"}
MONITOR = f"/stacks/daemon/menu/stack-settings-daemon--{RID}-monitor?radio={RID}"
LIVE = f"/stacks/daemon/menu/stack-settings-daemon--{RID}-live?radio={RID}"
_REPLY = {"": {"reachable": True, "status": {"RADIO": "READY"}, "stats": {"RX": "7"}},
          "/socket": {"reachable": True, "line": "STATUS RADIO=READY"},
          "/feed": {"feed": ["RX one", "TX two"]}}
API = re.compile(r".*/api/daemon/" + RID + r"(/socket|/feed)?$")
_MON = f"#stack-settings-daemon--{RID}-monitor"
_KINDS = ("status", "stats", "socket", "rxtx")


def _serve(page, state=None):
    """Answer the daemon APIs, counting the views' requests by path ("", "/socket", "/feed"); `state` {"up": bool}: the
    badge poll's (dash.js) answer, read at each poll (default up). The clock and the route are installed, then the page
    is loaded again (its views start under them). Returns (seen, settled, held): `held` {"path": p, "routes": []} keeps
    the replies of path p once the test sets it (answered later by the test)."""
    seen, held = [], {"path": None, "routes": []}
    state = state if state is not None else {"up": True}

    def handler(route):
        tail = API.match(route.request.url).group(1) or ""
        if tail == "" and route.request.headers.get("accept") != "application/json":       # dash.js's badge poll
            up = state["up"]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"rid": RID, "reachable": up, "ready": up, "radio_state": "READY" if up else "",
                 "status": {"RADIO": "READY", "TXMODE": "DIRECT", "CADWAIT": "1234"} if up else {},
                 "channel": {}, "stats": {}, "feed": []}))
            return
        seen.append(tail)
        if tail == held["path"]:
            held["routes"].append(route)
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[tail]))

    page.clock.install()
    page.route(API, handler)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    page.reload()
    return seen, settled, held


def _sel(kind):
    return f"#stack-settings-daemon--{RID}-{kind}"


def _ticks(page, kind, n, settled):
    """Advance the clock by `n` poll periods one at a time, each time until the poll's reply is in (a view has one
    request out at a time: a period that passes while it is out sends none)."""
    ms = {"socket": 1000, "rxtx": 3000}[kind]
    for _ in range(n):
        page.clock.run_for(ms)
        settled()
        page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel(kind), timeout=15000)


def _running(page, want):
    """Wait until every view of the page runs (`want` True) or none does."""
    page.wait_for_function("w => [...document.querySelectorAll('.daemonview')].every(v => !!v.dvRunning === w)",
                           arg=want, timeout=15000)


def _toggle_monitor(page, want_open):
    """Open or close the Monitor around the views and wait for its toggle (a queued task) to have reached them."""
    page.click(_MON + " > summary")
    page.wait_for_function("([m, o]) => document.querySelector(m).open === o", arg=[_MON, want_open], timeout=15000)
    _running(page, want_open)


def _text(page, kind):
    return page.text_content(_sel(kind) + " .daemonview-text")


def _wait_text(page, kind, want):
    page.wait_for_function("([s, w]) => document.querySelector(s + ' .daemonview-text').textContent.includes(w)",
                           arg=[_sel(kind), want], timeout=15000)


def _loaded(page, settled, seen):
    """The Monitor page at load: every shown view asked once; let those settle, then count afresh."""
    _running(page, True)
    for kind in _KINDS:
        page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel(kind), timeout=15000)
    settled()
    assert sorted(seen) == ["", "", "/feed", "/socket"], seen
    seen.clear()


def _paused(page):
    """Every view paused, Reload off, "Daemon not running" shown."""
    _running(page, False)
    assert page.evaluate("() => [...document.querySelectorAll('.daemonview-reload')].every(b => b.disabled)")
    assert page.locator(".dstate-down").first.is_visible()


def test_status_and_stats_ask_once_per_show_and_per_reload_and_never_while_hidden(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    _toggle_monitor(page, False)                                             # hidden: nothing
    page.clock.run_for(15000)
    settled()
    assert seen == []
    _toggle_monitor(page, True)                                              # shown again: one read each
    for kind, want in (("status", "RADIO=READY"), ("stats", "RX=7")):
        _wait_text(page, kind, want)
        assert _text(page, kind).split("\n")[0] == f"{kind.upper()} — {RID} (live)"
    settled()
    assert seen.count("") == 2
    page.click(_sel("status") + " .daemonview-reload")
    page.clock.run_for(15000)
    settled()
    assert seen.count("") == 3                                               # one per Reload, no timer


def test_the_socket_view_polls_each_second_only_while_shown(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    _ticks(page, "socket", 3, settled)
    assert seen.count("/socket") == 3                                        # each second
    page.click(_sel("socket") + " .daemonview-reload")                       # afresh: the window starts over
    page.wait_for_function("s => document.querySelector(s + ' .daemonview-text').textContent.split('\\n').length === 1",
                           arg=_sel("socket"), timeout=15000)
    _toggle_monitor(page, False)                                             # hidden: the poll stops
    settled()
    n = seen.count("/socket")
    page.clock.run_for(5000)
    settled()
    assert seen.count("/socket") == n


def test_a_second_toggle_while_shown_starts_no_second_run(flask_page):
    # the accordion or a restore may announce an open Monitor again: still one poll per view
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    page.evaluate("m => document.querySelector(m).dispatchEvent(new Event('toggle'))", _MON)
    _ticks(page, "socket", 3, settled)
    assert seen.count("/socket") == 3


def test_a_scripted_close_stops_the_socket_poll(flask_page):
    # the accordion or a restore closes a panel by setting open=false, no click
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    page.evaluate("m => { document.querySelector(m).open = false; }", _MON)
    _running(page, False)
    settled()
    n = len(seen)
    page.clock.run_for(5000)
    settled()
    assert len(seen) == n, seen


def test_the_rxtx_view_polls_every_3_s_only_while_shown(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    _ticks(page, "rxtx", 2, settled)
    assert seen.count("/feed") == 2
    _wait_text(page, "rxtx", "RX one\nTX two")
    _toggle_monitor(page, False)
    page.clock.run_for(9000)
    settled()
    assert seen.count("/feed") == 2


def test_a_views_own_page_asks_from_load(flask_page):
    page, _posts = flask_page(path=f"/stacks/daemon/menu/stack-settings-daemon--{RID}-status?radio={RID}",
                              unix_replies=_READY, api={f"/api/daemon/{RID}": _REPLY[""]})
    _wait_text(page, "status", "RADIO=READY")
    assert page.locator(_sel("status") + " .dbadge").count() == 1           # c4: its own page carries the badge


def test_a_reply_to_a_hidden_run_is_dropped(flask_page):
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, hold = _serve(page)
    _loaded(page, settled, seen)
    hold["path"], held = "/socket", hold["routes"]
    _toggle_monitor(page, False)
    _toggle_monitor(page, True)
    settled()
    assert len(held) == 1
    before = _text(page, "socket")
    _toggle_monitor(page, False)                                              # hidden before the reply
    held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"reachable": True, "line": "LATE"}))
    settled()
    page.evaluate("() => new Promise(r => setTimeout(r, 0))")
    assert _text(page, "socket") == before and "LATE" not in _text(page, "socket")


def test_a_slow_reply_is_never_overtaken_one_request_at_a_time(flask_page):
    # G2-RELUI B2: while a poll is out no next one is sent, so an older reply cannot land after a newer one
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, hold = _serve(page)
    _loaded(page, settled, seen)
    hold["path"], held = "/feed", hold["routes"]
    page.clock.run_for(3000)
    settled()
    assert seen.count("/feed") == 1 and len(held) == 1, seen
    page.clock.run_for(9000)                                                  # three ticks while it is out
    settled()
    assert seen.count("/feed") == 1, seen
    held.pop().fulfill(status=200, content_type="application/json", body=json.dumps({"feed": ["OLD"]}))
    _wait_text(page, "rxtx", "OLD")
    page.clock.run_for(3000)                                                  # the next tick asks again
    settled()
    assert seen.count("/feed") == 2, seen


def test_a_hide_and_show_during_a_slow_status_asks_once_more_when_it_settles(flask_page):
    # UI-REORG s4: never two requests out per view, AND fresh STATUS content after the old reply settles
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    seen, settled, hold = _serve(page)
    _loaded(page, settled, seen)
    hold["path"], held = "", hold["routes"]
    page.click(_sel("status") + " .daemonview-reload")                       # STATUS asks: held
    settled()
    assert len(held) == 1
    _toggle_monitor(page, False)
    _toggle_monitor(page, True)                                               # shown while it is out
    settled()
    assert len(held) == 2, len(held)                                          # STATS asks; STATUS waits (pending)
    assert _text(page, "status") == "loading…"
    held.pop(0).fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[""]))   # the old one
    settled()
    assert len(held) == 2, len(held)                                          # exactly one fresh STATUS request
    for r in held:
        r.fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[""]))
    _wait_text(page, "status", "RADIO=READY")
    settled()
    assert seen.count("") == 3                                                # Reload, STATS, the one fresh STATUS


def test_a_monitor_page_without_the_hardware_list_resumes_when_the_daemon_starts(flask_page):
    # UI-REORG s4: the Monitor page's own badge poll (dash.js) is the daemon-state source: down -> paused, up -> the
    # views run, down again -> paused
    page, _posts = flask_page(path=MONITOR)                                   # no daemon answers: stopped
    state = {"up": False}
    seen, settled, _held = _serve(page, state=state)
    assert page.locator("#hw-badge-" + RID).count() == 0
    _paused(page)
    page.clock.run_for(9000)
    settled()
    assert seen == []
    state["up"] = True
    page.clock.run_for(3000)                                                  # the next badge poll: up
    _running(page, True)
    _wait_text(page, "status", "RADIO=READY")
    assert not page.locator(".dstate-down").first.is_visible()
    assert page.evaluate("() => [...document.querySelectorAll('.daemonview-reload')].every(b => !b.disabled)")
    state["up"] = False
    page.clock.run_for(3000)
    _paused(page)
    settled()
    n = len(seen)
    page.clock.run_for(9000)
    settled()
    assert len(seen) == n


def test_live_settings_grey_while_the_daemon_is_down_and_re_enable_with_its_current_values(flask_page):
    page, _posts = flask_page(path=LIVE)
    state = {"up": False}
    _seen, _settled, _held = _serve(page, state=state)
    live = _sel("live")
    assert page.evaluate("l => [...document.querySelectorAll(l + ' .setform select, ' + l + ' .setform button')]"
                         ".every(e => e.disabled)", live)
    assert page.locator(live + " .dstate-down").is_visible()
    state["up"] = True
    page.clock.run_for(3000)
    page.wait_for_function("l => !document.querySelector(l + ' .setform button').disabled", arg=live, timeout=15000)
    assert page.locator(live + ' select[aria-label="TXMODE"]').input_value() == "DIRECT"   # that answer's values
    assert not page.locator(live + " .dstate-down").is_visible()
    state["up"] = False
    page.clock.run_for(3000)
    page.wait_for_function("l => document.querySelector(l + ' .setform button').disabled", arg=live, timeout=15000)


_INSERT = """([m, up]) => { const old = document.querySelector(m), host = old.parentElement;
  const html = old.outerHTML.replace(/data-up="[01]"/, 'data-up="' + up + '"'); old.remove();
  const box = document.createElement('div'); box.innerHTML = html; host.appendChild(box);
  document.dispatchEvent(new CustomEvent('lhpc:bodyloaded', {detail: {root: box}})); }"""


def test_a_monitor_inserted_later_takes_the_cached_state_at_once(flask_page):
    # UI-REORG s4: a lazily inserted Monitor reads the badge poll's cached state (fresher than one poll) at start
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    state = {"up": True}
    _seen, settled, _held = _serve(page, state=state)
    page.clock.run_for(3000)                                                  # a badge poll: up (fresh cache)
    settled()
    page.evaluate(_INSERT, [_MON, "0"])                                       # rendered as stopped
    _running(page, True)                                                      # up at once: no clock advance
    state["up"] = False                                                       # later transitions still work
    page.clock.run_for(3000)
    _paused(page)


def test_a_stopped_monitor_inserted_later_pauses_at_once(flask_page):
    page, _posts = flask_page(path=MONITOR)
    state = {"up": False}
    _seen, settled, _held = _serve(page, state=state)
    page.clock.run_for(3000)
    settled()
    page.evaluate(_INSERT, [_MON, "1"])                                       # rendered as running
    _paused(page)
    state["up"] = True
    page.clock.run_for(3000)
    _running(page, True)

"""DAEMON-MONITOR-TREE (0.14.0, 11-18) + UI-REORG c4 (plan v7 s3/s4), in a real browser: a radio's
live daemon views (STATUS RADIO STATS SOCKET RX TX; RADIO = daemon 1.5.0, its own tests at the end) under its Monitor, each its own closed node (F14), ask only while SHOWN (it and every <details>
around it open; opening one node polls that view only):
in the tree (the Settings page) Get STATUS / Get STATS one read per show and per Reload; View Socket a poll each
second, RX/TX View one every 3 s, both stopped by hiding; the Monitor's page (F7) shows ONE view, which asks from load
and repeats every 3 s (F6: no Reload, no "every N s" line); a reply to a run that was hidden meanwhile is dropped; one request
out per view, and a show while one is out asks once more when it settles. Daemon down: paused ("Daemon not running",
Reload off) until the badge's poll (dash.js, the same /api/daemon/<rid>) sees it up; Live greys and re-enables the
same way; a block inserted later takes the cached state at once. The daemon APIs are answered by the test (counted at
the route; the badge poll is told apart by its Accept header); time is Playwright's clock, advanced and never slept."""
from __future__ import annotations

import json
import re

import pytest

RID = "r3"                                           # flask_page's USB radio (its daemon answers: unix_replies)
_READY = {"/tmp/loraconf-r3.sock": b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"}
MONITOR = f"/stacks/daemon/menu/stack-settings-daemon--monitor?radio={RID}"   # F7: the one Monitor page (STATUS): 3 s
TREE = f"/stacks/daemon/menu/stack-settings-daemon?radio={RID}"           # the tree's modes (its Monitor opened here)
LIVE = f"/stacks/daemon/menu/stack-settings-daemon--{RID}-live?radio={RID}"
_REPLY = {"": {"reachable": True, "status": {"RADIO": "READY"}, "stats": {"RX": "7"}},
          "/socket": {"reachable": True, "line": "STATUS RADIO=READY"},
          "/feed": {"feed": ["RX one", "TX two"]},
          "?radio=1": {"reachable": True, "status": {"RADIO": "READY"}, "stats": {"RX": "7"}, "radio": {}}}
API = re.compile(r".*/api/daemon/" + RID + r"(/socket|/feed|\?radio=1)?$")          # ?radio=1: the RADIO view only
_MON = "#stack-settings-daemon--monitor"
_KINDS = ("status", "radio", "stats", "socket", "rxtx")


def _badge_answer(route, up):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"rid": RID, "reachable": up, "ready": up, "radio_state": "READY" if up else "",
         "label": "daemon READY" if up else "daemon offline",
         "status": {"RADIO": "READY", "TXMODE": "DIRECT", "CADWAIT": "1234"} if up else {},
         "channel": {}, "stats": {}, "feed": []}))


def _serve(page, state=None):
    """Answer the daemon APIs, counting the views' requests by path ("", "?radio=1", "/socket", "/feed"); `state` {"up": bool}: the
    badge poll's (dash.js) answer, read at each poll (default up). The clock and the route are installed, then the page
    is loaded again (its views start under them). Returns (seen, settled, held): `held` {"path": p, "routes": []} keeps
    the replies of path p once the test sets it (answered later by the test)."""
    seen, held = [], {"path": None, "routes": []}
    state = state if state is not None else {"up": True}

    def handler(route):
        tail = API.match(route.request.url).group(1) or ""
        if tail == "" and route.request.headers.get("accept") != "application/json":       # dash.js's badge poll
            state["polls"] = state.get("polls", 0) + 1                                    # counted for c11
            if state.get("hold") is not None:                                             # answered by the test
                state["hold"].append(route)
                return
            _badge_answer(route, state["up"])
            return
        seen.append(tail)
        if tail == held["path"]:
            held["routes"].append(route)
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[tail]))

    # Paused before the page loads again: installed alone, the clock runs with real time, and the real time of the
    # clicks and waits counts into the exact counts (a 1 s SOCKET poll inside _loaded: reader probe on 60a20fae)
    now = page.evaluate("() => Date.now()") / 1000      # the real time, in SECONDS (Python Playwright's unit)
    page.clock.install(time=now - 60)    # a minute back: a busy runner's stall cannot put the pause below in the past
    page.clock.pause_at(now + 1)          # paused: only the test moves the page's time
    page.route(API, handler)
    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))

    def settled():
        assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
    page.reload()
    return seen, settled, held


def _sel(kind):
    """A view of the one Monitor (F7: radio-less), or the radio's Live block (still per radio)."""
    return f"#stack-settings-daemon--{RID}-live" if kind == "live" else f"#stack-settings-daemon--monitor-{kind}"


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


def _wait_text(page, kind, want, exact=False):
    page.wait_for_function("([s, w, x]) => { const t = document.querySelector(s + ' .daemonview-text').textContent;"
                           " return x ? t === w : t.includes(w); }", arg=[_sel(kind), want, exact], timeout=15000)


def _open_monitor_and_views(page):
    """F14: open the Monitor, then each view's own node (on the Settings page no accordion closes the others)."""
    page.click(_MON + " > summary")
    page.wait_for_function("m => document.querySelector(m).open", arg=_MON, timeout=15000)
    for kind in _KINDS:
        page.click(_sel(kind) + " > summary")
    _running(page, True)


def _loaded(page, settled, seen):
    """The Settings page (TREE) with its Monitor and the five view nodes opened: every view asked once; let those
    settle, then count afresh."""
    _open_monitor_and_views(page)
    for kind in _KINDS:
        page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel(kind), timeout=15000)
    settled()
    assert sorted(seen) == ["", "", "/feed", "/socket", "?radio=1"], seen    # only RADIO sends ?radio=1
    seen.clear()


def _paused(page):
    """Every view paused, Reload off, "Daemon not running" shown."""
    _running(page, False)
    assert page.evaluate("() => [...document.querySelectorAll('.daemonview-reload')].every(b => b.disabled)")
    assert page.locator(".dstate-down").first.is_visible()


def test_status_and_stats_ask_once_per_show_and_per_reload_and_never_while_hidden(flask_page):
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    _toggle_monitor(page, False)                                             # hidden: nothing
    page.clock.run_for(15000)
    settled()
    assert seen == []
    _toggle_monitor(page, True)                                              # shown again: one read each
    for kind, want in (("status", "RADIO=READY"), ("radio", "needs daemon 1.5.0"), ("stats", "RX=7")):
        _wait_text(page, kind, want)
        assert _text(page, kind).split("\n")[0] == f"{kind.upper()} — {RID} (live)"
    settled()
    assert seen.count("") == 2 and seen.count("?radio=1") == 1, seen
    for kind in ("status", "radio", "stats"):
        p = "?radio=1" if kind == "radio" else ""
        before = seen.count(p)
        page.click(_sel(kind) + " .daemonview-reload")
        page.clock.run_for(15000)
        settled()
        assert seen.count(p) == before + 1, kind                             # one per Reload, no timer


def test_the_socket_view_polls_each_second_only_while_shown(flask_page):
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    _ticks(page, "socket", 3, settled)
    assert seen.count("/socket") == 3                                        # each second
    assert len(_text(page, "socket").split("\n")) == 4                      # the rolling window grows to 22
    page.click(_sel("socket") + " .daemonview-reload")                       # afresh: the window starts over
    settled()
    assert seen.count("/socket") == 4                                        # the Reload's request
    page.wait_for_function("s => document.querySelector(s + ' .daemonview-text').textContent.split('\\n').length === 1",
                           arg=_sel("socket"), timeout=15000)
    _toggle_monitor(page, False)                                             # hidden: the poll stops
    settled()
    n = seen.count("/socket")
    page.clock.run_for(5000)
    settled()
    assert seen.count("/socket") == n
    _toggle_monitor(page, True)                                              # shown again: one run, its cadence
    page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel("socket"), timeout=15000)
    _ticks(page, "socket", 2, settled)
    assert seen.count("/socket") == n + 3                                    # at once, then each second


def test_opening_one_view_node_polls_that_view_only_and_closing_it_stops_it(flask_page):
    # F14: the Monitor opened, its five views are closed nodes: nothing asks; opening SOCKET's node runs SOCKET only;
    # closing that node (a click, then a scripted open=false after a reopen) stops it
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    page.click(_MON + " > summary")
    page.wait_for_function("m => document.querySelector(m).open", arg=_MON, timeout=15000)
    assert page.evaluate("() => [...document.querySelectorAll('.daemonview')].map(v => [v.tagName, v.open])") == \
        [["DETAILS", False]] * len(_KINDS)
    page.clock.run_for(5000)
    settled()
    assert seen == [] and not page.evaluate("() => [...document.querySelectorAll('.daemonview')].some(v => v.dvRunning)")
    running = "() => [...document.querySelectorAll('.daemonview')].filter(v => v.dvRunning).map(v => v.dataset.kind)"
    for close in ("click", "script"):
        page.click(_sel("socket") + " > summary")
        page.wait_for_function("s => document.querySelector(s).dvRunning", arg=_sel("socket"), timeout=15000)
        assert page.evaluate(running) == ["socket"]
        _ticks(page, "socket", 2, settled)
        assert seen and set(seen) == {"/socket"}, seen
        if close == "click":
            page.click(_sel("socket") + " > summary")
        else:
            page.evaluate("s => { document.querySelector(s).open = false; }", _sel("socket"))
        page.wait_for_function("s => !document.querySelector(s).dvRunning", arg=_sel("socket"), timeout=15000)
        settled()
        seen.clear()
        page.clock.run_for(5000)
        settled()
        assert seen == [] and page.evaluate(running) == [], close


def test_a_second_toggle_while_shown_starts_no_second_run(flask_page):
    # the accordion or a restore may announce an open Monitor again: still one poll per view
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    page.evaluate("m => document.querySelector(m).dispatchEvent(new Event('toggle'))", _MON)
    _ticks(page, "socket", 3, settled)
    assert seen.count("/socket") == 3


def test_a_scripted_close_stops_the_socket_poll(flask_page):
    # the accordion or a restore closes a panel by setting open=false, no click
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
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
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    _loaded(page, settled, seen)
    _ticks(page, "rxtx", 2, settled)
    assert seen.count("/feed") == 2
    _wait_text(page, "rxtx", "RX one\nTX two", exact=True)
    _toggle_monitor(page, False)
    page.clock.run_for(9000)
    settled()
    assert seen.count("/feed") == 2


def test_the_monitor_pages_view_asks_from_load(flask_page):
    page, _posts = flask_page(path=MONITOR + "&view=status", unix_replies=_READY,
                              api={f"/api/daemon/{RID}": _REPLY[""]})
    _wait_text(page, "status", "RADIO=READY")
    assert page.locator(".daemonview").count() == 1                         # F7: one view
    assert page.locator(_sel("status") + " .dbadge").count() == 0           # F15: no pin after the view heading
    page.wait_for_function("s => !document.querySelector(s + ' > summary .dbadge').hidden", arg=_MON, timeout=15000)
    assert page.locator(".dbadge").count() == 1 and page.locator(".pagehead").count() == 0   # F15: the node's pin only
    tops = page.evaluate("() => [...document.querySelectorAll('.monitor-pins .tabs')]"
                         ".map(t => t.getBoundingClientRect().top)")
    assert len(tops) == 2 and tops[1] > tops[0], tops                       # F15b: radio pins, then view pins below


def test_a_reply_to_a_hidden_run_is_dropped(flask_page):
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
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
    page.evaluate("() => new Promise(r => { const c = new MessageChannel(); c.port1.onmessage = () => r();"
                  " c.port2.postMessage(0); })")                           # one task later (no timer: clock paused)
    assert _text(page, "socket") == before and "LATE" not in _text(page, "socket")


def test_a_slow_reply_is_never_overtaken_one_request_at_a_time(flask_page):
    # G2-RELUI B2: while a poll is out no next one is sent, so an older reply cannot land after a newer one
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
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
    _wait_text(page, "rxtx", "OLD", exact=True)
    page.clock.run_for(3000)                                                  # the next tick asks again
    settled()
    assert seen.count("/feed") == 2, seen


def test_a_hide_and_show_during_a_slow_status_asks_once_more_when_it_settles(flask_page):
    # UI-REORG s4: never two requests out per view, AND fresh STATUS content after the old reply settles
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, hold = _serve(page)
    _loaded(page, settled, seen)
    hold["path"], held = "", hold["routes"]
    page.click(_sel("status") + " .daemonview-reload")                       # STATUS asks: held
    settled()
    assert len(held) == 1
    _toggle_monitor(page, False)
    _toggle_monitor(page, True)                                               # shown while it is out
    settled()
    assert len(held) == 2, len(held)                                          # STATS asks (RADIO's ?radio=1 answered); STATUS waits
    assert _text(page, "status") == "loading…"
    held.pop(0).fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[""]))   # the old one
    settled()
    assert len(held) == 2, len(held)                                          # exactly one fresh STATUS request
    for r in held:
        r.fulfill(status=200, content_type="application/json", body=json.dumps(_REPLY[""]))
    _wait_text(page, "status", "RADIO=READY")
    settled()
    assert seen.count("") == 3 and seen.count("?radio=1") == 1, seen          # Reload, STATS, the one fresh STATUS; RADIO


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


def test_the_monitor_pages_one_view_repeats_every_3_s_status_and_stats_included(flask_page):
    # UI-REORG c5 (s4) + F7/F6: the Monitor's page works like a log page: its ONE view (each kind in turn) repeats
    # every 3 s; no Reload and no "every N s" line (STATUS / STATS: "updated hh:mm:ss" only, a25 v4 N1)
    path = {"status": "", "radio": "?radio=1", "stats": "", "socket": "/socket", "rxtx": "/feed"}
    for kind in _KINDS:
        page, _posts = flask_page(path=MONITOR + "&view=" + kind, unix_replies=_READY)
        seen, settled, _held = _serve(page)
        _running(page, True)
        page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel(kind), timeout=15000)
        settled()
        seen.clear()
        for _ in range(2):
            page.clock.run_for(3000)
            settled()
            page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel(kind), timeout=15000)
        assert seen == [path[kind]] * 2, (kind, seen)                       # 3 s, STATUS/STATS included
        assert page.locator(".daemonview").count() == 1 and page.locator(".daemonview-reload").count() == 0
        status = page.text_content(_sel(kind) + " .daemonview-status")
        assert (re.fullmatch(r"updated \d\d:\d\d:\d\d", status) if kind in ("status", "radio", "stats")
                else status == ""), (kind, status)
        assert page.locator(".dbadge").count() == 1 and page.locator(".pagehead").count() == 0   # F15: the node's pin
        page.context.close()


def test_the_monitor_page_without_the_hardware_list_follows_the_daemon_stopped_started_stopped(flask_page):
    # F7 (G1 N1): the Monitor page's shown view pauses / resumes with the daemon and flips its own down line
    page, _posts = flask_page(path=MONITOR + "&view=status")
    state = {"up": False}
    seen, settled, _held = _serve(page, state=state)
    _paused(page)
    page.clock.run_for(9000)
    settled()
    assert seen == []                                                         # polling off
    state["up"] = True
    page.clock.run_for(3000)
    _running(page, True)
    _wait_text(page, "status", "RADIO=READY")
    assert not page.locator(_sel("status") + " .dstate-down").is_visible()  # its down line: gone
    settled()
    n = seen.count("")
    page.clock.run_for(3000)
    settled()
    assert seen.count("") == n + 1                                            # polling on: every 3 s
    state["up"] = False
    page.clock.run_for(3000)
    _paused(page)
    assert page.locator(_sel("status") + " .dstate-down").is_visible()      # ... and back


# --- UI-REORG c11 (plan v7 s13, L85): the in-tree poll rule — a poller asks only while its element and every
#     <details> around it are open AND the tab is visible (shown.js) ---------------------------------------------

def _set_hidden(page, hidden):
    page.evaluate(
        "h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});"
        " Object.defineProperty(document, 'visibilityState', {configurable: true,"
        "   get: () => h ? 'hidden' : 'visible'});"
        " document.dispatchEvent(new Event('visibilitychange')); }", hidden)


def _is_badge_poll(request):
    return request.url.endswith("/api/daemon/" + RID) and request.headers.get("accept") != "application/json"


def test_a_hidden_tab_stops_the_views_and_visible_again_asks_each_view_once(flask_page):
    """s13 P1: the Socket (1 s) and RX/TX (3 s) views stop in a background tab, the radio's badge poll too; back in
    front, each view asks once at once (STATUS/STATS one read: a new show)."""
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    state = {"up": True}
    seen, settled, _held = _serve(page, state)
    _loaded(page, settled, seen)
    _set_hidden(page, True)
    _running(page, False)
    settled()
    seen.clear()
    polls = state["polls"]
    page.clock.run_for(9000)
    settled()
    assert (seen, state["polls"]) == ([], polls)
    _set_hidden(page, False)
    _running(page, True)
    for kind in _KINDS:
        page.wait_for_function("s => !document.querySelector(s).dvBusy", arg=_sel(kind), timeout=15000)
    settled()
    assert sorted(seen) == ["", "", "/feed", "/socket", "?radio=1"], seen    # only RADIO sends ?radio=1
    assert state["polls"] == polls + 1                                       # the badge poll: one at once


def _pill(page, block):
    return page.evaluate("b => { const p = document.querySelector(b + ' > summary .dbadge');"
                         " return [p.textContent, p.className]; }", block)


def _hidden(page, block):
    """F11: a header pin without a live answer (its block closed) is hidden (was: a neutral "–")."""
    return page.evaluate("b => document.querySelector(b + ' > summary .dbadge').hidden", block)


def test_a_closed_blocks_pill_is_hidden_and_its_radio_polls_only_while_a_pill_is_shown(flask_page):
    """s13 P3: one radio, two pills (the summaries of Monitor · r3 and Live · r3). A closed block never polls and its
    pill is hidden (F11; was a neutral "–"), never a stale state (the daemon going down meanwhile included); one block
    shown -> its radio polls, the closed one's pill stays hidden; opened -> one request at once (also a quick
    close/reopen); a later body with a shown pill -> one request at once. One poll, one cache per radio."""
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    state = {"up": True}
    seen, settled, _held = _serve(page, state)
    live = _sel("live")
    assert _hidden(page, live)                                               # closed at load: hidden from the start
    _loaded(page, settled, seen)                                             # paused since _serve: "at once"

    def toggle(want):
        page.click(live + " > summary")
        page.wait_for_function("([s, w]) => document.querySelector(s).open === w", arg=[live, want], timeout=15000)
        page.evaluate("() => new Promise(r => { const c = new MessageChannel(); c.port1.onmessage = () => r();"
                      " c.port2.postMessage(0); })")                         # one task later: its toggle was handled
    def answered():                                                          # the last answer is in (cached)
        page.wait_for_function(f"() => window.lhpcDaemonState('{RID}') !== null", timeout=15000)
    settled()
    answered()
    polls = state["polls"]
    page.clock.run_for(3000)
    settled()
    assert state["polls"] == polls + 1                                       # Monitor shown: its radio polls
    answered()
    assert _hidden(page, live)                                               # Live closed: hidden, not READY
    toggle(True)                                                             # Live opened within the poll: the
    assert _pill(page, live) == ["daemon READY", "badge badge-running dbadge"] and not _hidden(page, live)  # fresh
                                                                             # answer at once,
    assert state["polls"] == polls + 1                                       # no second request
    toggle(False)
    _toggle_monitor(page, False)                                             # both closed
    settled()
    assert _hidden(page, _MON)
    polls = state["polls"]
    state["up"] = False                                                      # the daemon goes down
    page.clock.run_for(9000)
    settled()
    assert state["polls"] == polls                                           # no request ...
    assert _hidden(page, _MON)                                               # ... and never a stale "daemon READY"
    with page.expect_request(_is_badge_poll, timeout=15000):
        toggle(True)                                                         # Live opened: at once
    settled()
    assert state["polls"] == polls + 1
    page.wait_for_function("l => document.querySelector(l + ' > summary .dbadge').textContent === 'daemon offline'",
                           arg=live, timeout=15000)                          # its answer
    assert not _hidden(page, live)
    toggle(False)                                                            # its timer goes ...
    assert _hidden(page, live)
    with page.expect_request(_is_badge_poll, timeout=15000):
        toggle(True)                                                         # ... so a quick reopen asks at once
    settled()
    assert state["polls"] == polls + 2
    answered()
    toggle(False)
    with page.expect_request(_is_badge_poll, timeout=15000):                 # a body inserted later with a shown
        page.evaluate("rid => { const p = document.createElement('p');"   # pill of the radio: one request at once
                      " p.innerHTML = '<span class=\"badge dbadge\" data-rid=\"' + rid + '\">daemon READY</span>';"
                      " document.querySelector('.menupage').appendChild(p);"
                      " document.dispatchEvent(new CustomEvent('lhpc:bodyloaded', {detail: {root: p}})); }", RID)
    settled()
    assert state["polls"] == polls + 3


def test_a_full_pages_pill_keeps_an_answer_that_lands_in_a_hidden_tab(flask_page):
    """A badge answer that lands while the tab is hidden paints the full page's pill with its state, not hidden (only
    a closed block's pill is hidden, F11); back in front after the cache went stale, the pill still reads that state while
    the next request is out."""
    page, _posts = flask_page(path=MONITOR, unix_replies=_READY)
    state = {"up": True}
    _seen, settled, _held = _serve(page, state)
    pill, ready = _MON + " > summary .dbadge", ["daemon READY", "badge badge-running dbadge"]   # F15: its one pin

    def read():
        return page.evaluate("p => { const b = document.querySelector(p); return [b.textContent, b.className]; }", pill)
    page.wait_for_function("p => document.querySelector(p).textContent === 'daemon READY'", arg=pill, timeout=15000)
    state["hold"] = []
    page.clock.run_for(3000)                                                 # the next poll, held out
    settled()
    assert len(state["hold"]) == 1
    _set_hidden(page, True)                                                  # the tab goes to the background
    _badge_answer(state["hold"].pop(), True)                                 # ... and the answer lands there
    page.wait_for_function(f"() => window.lhpcDaemonState('{RID}') !== null", timeout=15000)
    assert read() == ready
    page.clock.run_for(4000)                                                 # the cache goes stale
    _set_hidden(page, False)                                                 # in front: a request goes out
    settled()
    assert len(state["hold"]) == 1
    assert read() == ready                                                   # the pill kept the answer



def test_a_header_pin_shows_only_a_live_answer_while_its_node_is_open(flask_page):
    """F11 (+ N2): with nothing shown the cache goes stale; opening Live sends a request and its pin stays hidden while
    the answer is out (no load-time state), then shows that answer; closing hides it on the toggle itself (the clock
    is paused: no poll can do it)."""
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    state = {"up": True}
    seen, settled, _held = _serve(page, state)
    live = _sel("live")
    _loaded(page, settled, seen)
    _toggle_monitor(page, False)
    settled()
    assert _hidden(page, _MON) and _hidden(page, live)
    page.clock.run_for(4000)                                                 # the cache goes stale
    settled()
    state["hold"] = []

    def toggle(want):
        page.click(live + " > summary")
        page.wait_for_function("([s, w]) => document.querySelector(s).open === w", arg=[live, want], timeout=15000)
        page.evaluate("() => new Promise(r => { const c = new MessageChannel(); c.port1.onmessage = () => r();"
                      " c.port2.postMessage(0); })")                         # one task later: its toggle was handled
    toggle(True)
    settled()
    assert len(state["hold"]) == 1 and _hidden(page, live)                   # the request is out: still hidden
    _badge_answer(state["hold"].pop(), False)
    page.wait_for_function("l => !document.querySelector(l + ' > summary .dbadge').hidden", arg=live, timeout=15000)
    assert _pill(page, live) == ["daemon offline", "badge badge-stopped dbadge"]
    toggle(False)
    assert _hidden(page, live) and state["hold"] == []


# ---- RADIO (daemon 1.5.0 `GET RADIO`, plan v17 §13 A6.1): the RADIO view reads /api/daemon/<rid>'s `radio` ----------

_RADIO = {"RADIO": "READY", "MODE": "LORA", "FREQ": "433.175", "SF": "12", "BW": "125.0", "CR": "5",
          "PREAMBLE": "8", "SYNC": "0x12", "CRC": "1", "LDRO": "1", "POWER": "17"}
_ST15 = {"RADIO": "READY", "RADIOERR": "retry"}                       # a 1.5.0 daemon's STATUS carries RADIOERR=


@pytest.mark.parametrize("status,radio,want,absent", [
    (_ST15, _RADIO, "FREQ=433.175", None),
    ({"RADIO": "READY"}, {}, "RADIO not reported by this daemon (needs daemon 1.5.0)", None),
    (_ST15, {}, "RADIO not readable", "needs daemon 1.5.0"),                        # 1.5.0, but no complete reply
    (_ST15, dict(_RADIO, RADIO="FAILED"), "RADIO=FAILED", "RADIO=READY"),          # its health, never STATUS's
    (_ST15, dict(_RADIO, RADIO="UNPLUGGED"), "RADIO=UNPLUGGED", "RADIO=READY"),
], ids=["1.5.0", "1.4.2", "1.5.0-unreadable", "radio-failed", "radio-unplugged"])
def test_the_radio_view_shows_its_get_radio_reply_and_says_why_there_is_none(flask_page, monkeypatch, status, radio,
                                                                             want, absent):
    monkeypatch.setitem(_REPLY, "?radio=1", {"reachable": True, "status": status, "stats": {}, "radio": radio})
    page, _posts = flask_page(path=TREE, unix_replies=_READY)
    seen, settled, _held = _serve(page)
    page.click(_MON + " > summary")
    page.wait_for_function("m => document.querySelector(m).open", arg=_MON, timeout=15000)
    page.click(_sel("radio") + " > summary")                                  # only the RADIO node shown
    _wait_text(page, "radio", want)
    text = _text(page, "radio")
    assert text.split("\n")[0] == f"RADIO — {RID} (live)", text
    assert absent is None or absent not in text, text
    settled()
    assert seen == ["?radio=1"], seen                                         # one read per show, and only it asks RADIO

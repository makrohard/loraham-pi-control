"""The Monitor's RX / TX pill also shows TX when this radio's TXOK+TXERR grew since its previous poll.
The first valid sample is the baseline (TX only on STATUS TX=1); a lower total is a reset (new baseline, no TX);
missing or incomplete STATS never count as 0 and drop the comparison until the next valid sample; STATUS TX=1 alone
shows TX. Each radio has its own baseline. Two radios (r1, r3) in a real browser, their polls answered by the test;
each step is told apart by STATS RX (the step number), painted in the same pass as the pill. A TX lasts one poll, so
each pill is read in the pass that paints its step, not when the other radio catches up."""
_SOCKS = {"/tmp/loraconf-r1.sock": b"STATUS RADIO=READY ID=r1 BOUND=- TXMODE=MANAGED\n",
          "/tmp/loraconf-r3.sock": b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"}


def _reply(step, tx, ok, err="0"):
    stats = {"RX": str(step), "UPTIME": "1"}
    if ok is not None:
        stats["TXOK"] = ok
    if err is not None:
        stats["TXERR"] = err
    return {"reachable": True, "ready": True, "label": "daemon READY", "channel": {}, "feed": [],
            "status": {"RADIO": "READY", "TX": tx}, "stats": stats}


# Records each radio's pill in the pass that paints a new RX value (the pill is set before RX in that pass).
_WATCH = """() => { window.lhpcPills = {};
  ['r1', 'r3'].forEach(r => { const rx = document.getElementById('rd-rx-' + r), seen = window.lhpcPills[r] = {};
    new MutationObserver(() => { if (!(rx.textContent in seen))
      seen[rx.textContent] = document.getElementById('rd-txpill-' + r).textContent; })
      .observe(rx, {childList: true, characterData: true, subtree: true}); }); }"""
_PILLS = """step => ['r1', 'r3'].every(r => step in window.lhpcPills[r])
  ? ['r1', 'r3'].map(r => window.lhpcPills[r][step]) : null"""


def _open(flask_page):
    api = {"/api/daemon/r1": _reply(0, "0", "100"), "/api/daemon/r3": _reply(0, "0", "5")}
    page, _posts = flask_page(path="/", unix_replies=_SOCKS, api=api)
    page.evaluate(_WATCH)
    return page, api


def test_the_pill_counts_sent_frames_per_radio(flask_page):
    page, api = _open(flask_page)
    r3 = [  # (STATUS TX, TXOK, TXERR) -> r3's pill
        (("0", "5", "0"), "RX"),       # 1 first valid sample: the baseline (step 0 was the load's; same total)
        (("0", "6", "0"), "TX"),       # 2 grew: a frame went out since the previous poll
        (("0", "6", "0"), "RX"),       # 3 no growth
        (("0", "6", "1"), "TX"),       # 4 TXERR counts too
        (("0", "2", "0"), "RX"),       # 5 lower total: a reset, new baseline, no pulse
        (("0", "3", "0"), "TX"),       # 6 growth from the reset's baseline
        (("0", None, "0"), "RX"),      # 7 incomplete STATS: never 0, no comparison
        (("0", "50", "0"), "RX"),      # 8 the next valid sample is a new baseline, not growth
        (("1", "50", "0"), "TX"),      # 9 STATUS TX=1 alone
        (("0", "x", "0"), "RX"),       # 10 non-numeric: no comparison
        (("0", "51", "0"), "RX"),      # 11 new baseline again
    ]
    for step, ((tx, ok, err), want) in enumerate(r3, start=1):
        api["/api/daemon/r1"] = _reply(step, "0", "100")                 # r1 never grows: its own baseline
        api["/api/daemon/r3"] = _reply(step, tx, ok, err)
        got = page.wait_for_function(_PILLS, arg=str(step)).json_value()
        assert got == ["RX", want], (step, got)
    page.context.close()


def test_a_pulse_is_read_even_when_the_other_radio_paints_a_poll_later(flask_page):
    # r3 grows while r1 still answers the previous step: r3's next poll of the same sample ends the pulse before r1
    # shows the step. The pill painted with the growth is still the step's.
    page, api = _open(flask_page)
    for step, ok in ((1, "5"), (2, "2")):
        api["/api/daemon/r1"] = _reply(step, "0", "100")
        api["/api/daemon/r3"] = _reply(step, "0", ok)
        assert page.wait_for_function(_PILLS, arg=str(step)).json_value() == ["RX", "RX"], step
    api["/api/daemon/r3"] = _reply(3, "0", "3")                          # grew; r1 is held at step 2
    page.wait_for_function("() => window.lhpcPills.r3['3'] === 'TX'")
    page.wait_for_function("() => document.getElementById('rd-txpill-r3').textContent === 'RX'")   # r3 polled again
    api["/api/daemon/r1"] = _reply(3, "0", "100")
    got = page.wait_for_function(_PILLS, arg="3").json_value()
    page.context.close()
    assert got == ["RX", "TX"], got

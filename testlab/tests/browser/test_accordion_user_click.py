"""UI-REORG c14 (the GPS poll test's race): the /stacks accordions act only on the toggle of the <details> whose
<summary> the user just clicked (a mouse click or Enter/Space on a focused summary). A scripted open (a #link's
hashchange, a load restore) fires the same async toggle but leaves the panels as opened: the old guard flag, cleared
by setTimeout(0), raced those toggles. The init script runs setTimeout(fn, 0) as a microtask, so the timer always
runs before the toggles (the losing order of the race, every time). A click that toggles nothing (cancelled, or on
a control inside the summary) leaves no mark for a later scripted open."""
from __future__ import annotations

_ZERO_TIMER_FIRST = """(() => { const st = window.setTimeout;
  window.setTimeout = (f, ms, ...a) => ms ? st(f, ms, ...a) : (queueMicrotask(() => f(...a)), 0); })();"""
_SHOWN = "() => window.lhpcShown(document.getElementById('gps-monitor'))"


def _open(flask_page):
    page, _posts = flask_page(path="/stacks?open=gps")             # the controller row, GPS panel + Monitor open
    page.add_init_script(_ZERO_TIMER_FIRST)
    page.reload()
    page.wait_for_function(_SHOWN, timeout=15000)
    return page


def _frames(page):
    """Two animation frames: every toggle task queued before has run."""
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def _is_open(page, did):
    return page.evaluate("d => document.getElementById(d).open", did)


def _wait_open(page, did, want):
    page.wait_for_function("([d, w]) => document.getElementById(d).open === w", arg=[did, want], timeout=15000)


def test_a_link_to_a_nested_panel_leaves_it_open_when_the_zero_timer_runs_first(flask_page):
    page = _open(flask_page)
    page.evaluate("() => { window.__closed = new Promise(r => document.getElementById('controller-row')"
                  ".addEventListener('toggle', r, {once: true})); }")
    page.click("#controller-row > summary")                         # a user close: its toggle consumes the click
    page.evaluate("() => window.__closed")                           # (the async toggle has run: one user action)
    page.evaluate("() => { location.hash = '#gps-monitor'; }")      # the hashchange opens it and its ancestors
    page.wait_for_function(_SHOWN, timeout=15000)
    _frames(page)
    assert page.evaluate(_SHOWN)                                     # still shown: no accordion undid it


def test_a_user_click_or_key_still_drives_both_accordions_and_a_click_that_toggles_nothing_leaves_no_mark(flask_page):
    page = _open(flask_page)                                        # F1: Install is gps-row's sibling (was Update)
    page.click("#controller-install > summary")                      # sub accordion, mouse: the sibling closes
    _wait_open(page, "controller-install", True)
    _wait_open(page, "gps-row", False)
    page.focus("#gps-row > summary")
    page.keyboard.press("Enter")                                     # sub accordion, keyboard
    _wait_open(page, "gps-row", True)
    _wait_open(page, "controller-install", False)
    page.evaluate("() => { document.getElementById('controller-install').open = true; }")   # a scripted sub open
    _wait_open(page, "controller-install", True)
    _frames(page)
    assert _is_open(page, "gps-row")                                 # its sibling stays open
    page.evaluate("() => { document.getElementById('controller-install').open = false; }")
    page.click("#stackrow-kiss > summary")                          # main accordion, mouse
    _wait_open(page, "stackrow-kiss", True)
    _wait_open(page, "controller-row", False)
    page.focus("#controller-row > summary")
    page.keyboard.press("Space")                                     # main accordion, keyboard: own panels reset
    _wait_open(page, "controller-row", True)
    _wait_open(page, "stackrow-kiss", False)
    _wait_open(page, "gps-row", False)
    # A cancelled summary click toggles nothing; a later scripted open of that row is no user open.
    page.evaluate("() => document.querySelector('#stackrow-kiss > summary')"
                  ".addEventListener('click', e => e.preventDefault(), {once: true})")
    page.click("#stackrow-kiss > summary")
    _frames(page)
    assert not _is_open(page, "stackrow-kiss")
    page.evaluate("() => { document.getElementById('stackrow-kiss').open = true; }")
    _wait_open(page, "stackrow-kiss", True)
    _frames(page)
    assert _is_open(page, "controller-row")                          # the accordion left it be
    page.evaluate("() => { document.getElementById('stackrow-kiss').open = false; }")
    # The same for a click on a control inside the summary (the summary does not toggle).
    page.evaluate("() => { const b = document.createElement('button'); b.type = 'button'; b.id = 'in-summary';"
                  " b.textContent = 'x'; document.querySelector('#stackrow-kiss > summary').appendChild(b); }")
    page.click("#in-summary")
    _frames(page)
    assert not _is_open(page, "stackrow-kiss")
    page.evaluate("() => { document.getElementById('stackrow-kiss').open = true; }")
    _wait_open(page, "stackrow-kiss", True)
    _frames(page)
    assert _is_open(page, "controller-row")


def test_the_update_link_opens_install_and_shows_update(page):
    # F1: the controller's Update moved under Install; a link to #controller-update (the Update row link, the
    # self-update redirects) opens its ancestors, Install included (stacks_state.js), and shows Update.
    page.goto(page.lab_base + "/stacks#controller-update", wait_until="load")
    page.wait_for_function("() => document.getElementById('controller-update').open || "
                           "document.getElementById('controller-install').open", timeout=10000)
    assert page.evaluate("() => ['controller-row', 'controller-install'].every(i => document.getElementById(i).open)")
    assert page.locator("#controller-update > summary").is_visible()

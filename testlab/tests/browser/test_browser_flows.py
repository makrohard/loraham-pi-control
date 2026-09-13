"""Real-browser flows over the real lab server: the pages' JS executes, forms submit with
CSRF through the browser, the accordion loads its lazy body, and an action travels form ->
route -> service -> visible state.

Every wait here is for an OBSERVABLE condition. A fixed sleep would pass on a page that
never finished and fail on a slow runner, which is the opposite of what a browser test is
for.
"""
from __future__ import annotations

import pytest


def test_the_dashboard_renders_in_the_lab(page):
    page.goto(page.lab_base + "/", wait_until="networkidle")
    assert "LoRaHAM" in page.title()
    assert page.locator(".lab-banner").count() == 1, "the lab banner marks a simulated box"


def test_opening_a_stack_loads_its_lazy_body(page):
    """The accordion fetches GET /stacks/<sid>/body and inserts it — the body must actually
    arrive and carry the stack's controls, not merely be requested."""
    page.goto(page.lab_base + "/stacks", wait_until="networkidle")
    row = page.locator("details").filter(has_text="LoRaHAM daemon").first
    row.locator("summary").first.click()
    # stacklazy.js REPLACES the `.lazy-body` placeholder with the fetched body: the
    # placeholder disappearing is the observable proof the fetch arrived and was inserted.
    page.wait_for_function(
        "() => { const d = [...document.querySelectorAll('details')]"
        ".find(e => e.textContent.includes('LoRaHAM daemon'));"
        " return d && d.open && !d.querySelector(':scope > .lazy-body')"
        " && d.querySelector('form'); }", timeout=15000)


def test_a_lifecycle_action_travels_form_to_route_to_state(page, lab):
    """One representative web action, end to end in the browser: the form posts with its
    CSRF token, the route reaches the service, and the resulting state is observable."""
    page.goto(page.lab_base + "/testlab", wait_until="networkidle")
    scenario = lab.root / "state" / "testlab" / "scenario.json"
    page.check('input[name="name"][value="degraded"]')
    page.click('button:has-text("Switch scenario")')
    page.wait_for_load_state("networkidle")
    assert '"degraded"' in scenario.read_text(), "the browser action did not reach the service"
    page.check('input[name="name"][value="healthy"]')
    page.click('button:has-text("Switch scenario")')
    page.wait_for_load_state("networkidle")
    assert '"healthy"' in scenario.read_text()


def test_the_gps_panel_opens_with_its_controls(page):
    page.goto(page.lab_base + "/stacks?open=gps", wait_until="networkidle")
    gps = page.locator("#gps-row")
    assert gps.count() == 1, "the GPS panel is part of the Stacks page; its absence is a regression, not a build variant"
    # `?open=gps` is the deep link the console itself uses: the panel arrives OPEN, so the
    # assertion is that it carries its controls, not that a click can open it.
    page.wait_for_function(
        "() => { const r = document.getElementById('gps-row');"
        " return r && r.open && r.querySelector('form'); }", timeout=15000)
    assert gps.locator("select, input").count() >= 1, "the GPS panel exposes no controls"


@pytest.mark.parametrize("path", ["/", "/stacks", "/dependencies"])
def test_a_phone_viewport_has_no_horizontal_overflow(page, path):
    """One smartphone smoke test: the page must not scroll sideways and its primary
    navigation must stay reachable. No screenshots, no pixel geometry."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(page.lab_base + path, wait_until="networkidle")
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{path}: {overflow}px of horizontal overflow at 390px"
    assert page.locator("a.apps-link").is_visible(), "the Apps link must stay reachable"
    assert page.locator("a.home").is_visible(), "the Home link must stay reachable"


def test_the_auto_install_channel_rules_hold_in_both_directions(page):
    """auto_install.js owns three operator rules on the Auto-install page. All three are asserted
    here against the real rendered page, because they are pure client-side state:

      * BINARY disables and UNCHECKS a row's Tests and TX boxes — a prebuilt artifact has no test
        tree, and the server refuses the combination outright, so offering the tick would invite a
        run that is rejected;
      * BINARY -> SOURCE gives them back, for the stacks that declare a test tree;
      * the "All" box then ticks every box it may legitimately touch and never a disabled one.

    The binary half runs only where a row actually offers that channel: `binary_available()` is
    declaration plus platform, so an aarch64 lab publishes artifacts and an x86-64 one does not.
    That witness therefore comes from CI, and no option is ever faked into the DOM here — a page
    the product does not render proves nothing.
    """
    page.goto(page.lab_base + "/auto-install", wait_until="networkidle")
    page.wait_for_selector("tr[data-stack] input.ai-tests", timeout=15000)
    rows = page.locator("tr[data-stack]")

    # A row that offers `binary` AND declares a test tree: only there is the transition
    # OBSERVABLE. A binary row with no test tree has its boxes disabled already, so asserting
    # they are disabled would prove nothing about the channel. The TESTS box is the one that
    # actually moves here; the TX box is checked for the same rule, but no stack currently both
    # publishes an artifact and is TX-capable, so that half is a state check rather than a
    # witnessed transition.
    binary_testable = rows.evaluate_all(
        "rs => rs.filter(r => r.dataset.testable === '1')"
        "       .filter(r => [...r.querySelector('select.ai-version').options]"
        "                    .some(o => o.value === 'binary')).map(r => r.dataset.stack)")
    if binary_testable:
        sid = binary_testable[0]
        row = page.locator(f'tr[data-stack="{sid}"]')
        row.locator("select.ai-version").select_option("dev")
        row.locator("input.ai-tests").check()          # tick it, so we see it TAKEN AWAY
        page.wait_for_function(
            "sid => { const t = document.querySelector("
            "  `tr[data-stack='${sid}'] input.ai-tests`); return t.checked && !t.disabled; }",
            arg=sid, timeout=10000)
        row.locator("select.ai-version").select_option("binary")
        page.wait_for_function(
            "sid => { const r = document.querySelector(`tr[data-stack='${sid}']`);"
            " const t = r.querySelector('input.ai-tests'), x = r.querySelector('input.ai-tx');"
            " return t.disabled && !t.checked && x.disabled && !x.checked; }",
            arg=sid, timeout=10000)
        row.locator("select.ai-version").select_option("dev")     # ...and back again
        page.wait_for_function(
            "sid => { const r = document.querySelector(`tr[data-stack='${sid}']`);"
            " return r.querySelector('input.ai-tests').disabled === (r.dataset.testable !== '1'); }",
            arg=sid, timeout=10000)

    # Every row onto a source channel, then the "All" rule over the whole table.
    page.locator("#ai-all-version").select_option("dev")
    page.wait_for_function(
        "() => [...document.querySelectorAll('tr[data-stack] select.ai-version')]"
        ".every(s => s.value === 'dev' || ![...s.options].some(o => o.value === 'dev'))",
        timeout=10000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('tr[data-stack]')].every(r =>"
        " r.querySelector('input.ai-tests').disabled === (r.dataset.testable !== '1'))",
        timeout=10000)

    split = rows.evaluate_all(
        "rs => ({on: rs.filter(r => r.dataset.testable === '1').map(r => r.dataset.stack),"
        "        off: rs.filter(r => r.dataset.testable !== '1').map(r => r.dataset.stack)})")
    assert split["on"] and split["off"], f"fixture must have both kinds: {split}"

    page.locator("#ai-all-tests").check()
    page.wait_for_function(
        "sids => sids.every(s => document.querySelector("
        "  `tr[data-stack='${s}'] input.ai-tests`).checked)", arg=split["on"], timeout=10000)
    ticked_anyway = rows.evaluate_all(
        "rs => rs.filter(r => r.querySelector('input.ai-tests').disabled)"
        "       .filter(r => r.querySelector('input.ai-tests').checked).map(r => r.dataset.stack)")
    assert not ticked_anyway, f"All ticked a disabled host-test box: {ticked_anyway}"


def test_a_start_marks_its_stack_and_reloads_the_page_once_when_it_finishes(page):
    """taskbanner.js is not decoration: while a start job runs it marks that stack, and when the
    job turns terminal it reloads the page EXACTLY once so the server-rendered rows become
    current. Both halves are asserted here, against a controlled `/api/tasks` feed — the same
    interception the system-box tests use, so no job has to be really started.

    `/stacks` deliberately, not the dashboard: there `dash.js` owns the reload (the banner steps
    aside when `.radiogrid` is present), so the dashboard would prove the opposite.
    """
    import json

    task = {"kind": "job", "op": "start", "state": "running", "stack": "kiss",
            "run_id": "r1", "attempt_id": "a1", "label": "start kiss", "admitted": True}
    feed = {"tasks": [task]}

    page.route("**/api/tasks*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(feed)))

    navigations = []
    page.on("framenavigated", lambda f: navigations.append(f.url) if f is page.main_frame else None)

    page.goto(page.lab_base + "/stacks", wait_until="networkidle")
    assert page.locator(".radiogrid").count() == 0, "this page must not be the reload owner"
    page.wait_for_selector('.badge-starting[data-starting-for="kiss"]', timeout=15000)
    assert len(navigations) == 1, navigations

    feed["tasks"] = [dict(task, state="done")]        # the job finishes -> exactly one reload
    page.wait_for_function(
        "() => (sessionStorage.getItem('lhpc_reloaded_starts') || '').includes('a1')",
        timeout=15000)
    page.wait_for_function("() => document.readyState === 'complete'", timeout=15000)
    assert len(navigations) == 2, f"expected exactly one reload, saw {navigations}"

    # The remembered attempt is what stops a second reload: the feed still lists the finished
    # job, and the banner must leave the page alone from here.
    page.wait_for_function(
        "() => document.querySelectorAll('.badge-starting').length === 0", timeout=15000)
    assert len(navigations) == 2, f"reloaded more than once: {navigations}"


def test_the_rf_log_viewer_sorts_filters_toggles_columns_and_decrypts(page, lab):
    """rflog.js is proven only here: the records table over a seeded two-segment log, sort by
    rssi, a filter, a column off, a row appended and picked up by the poll, and the Decrypt
    toggle — which switches the poll to the decoded API (answered by a controlled feed, so no
    stack venv is needed) and renders the decoded column through textContent, never as HTML."""
    import json

    logs = lab.root / "logs"
    logs.mkdir(exist_ok=True)
    older = '2026-09-12T16:00:00.000Z RX rssi=-90.00 snr=2.00 len=3 hex=aabbcc ascii="..."'
    newer = '2026-09-12T16:01:00.000Z TX rssi=- snr=- len=3 outcome=ok tnc2="G0ABC>APRS:hello" hex=112233 ascii="..."'
    (logs / "rf-kiss.log.1").write_text(older + "\n")
    (logs / "rf-kiss.log").write_text(newer + "\n")

    page.goto(page.lab_base + "/logs/loraham-kiss-tnc?job=rf-kiss.log", wait_until="networkidle")
    page.wait_for_function("() => document.querySelectorAll('#rfview tbody tr').length === 2", timeout=15000)
    assert page.locator("#rf-decrypt").count() == 0                     # plaintext stack: no toggle
    rows = page.locator("#rfview tbody tr")
    assert "16:00:00" in rows.nth(0).inner_text() and "16:01:00" in rows.nth(1).inner_text()

    page.locator("#rfview th[data-col=rssi] button").click()            # sort: nulls last
    page.locator("#rfview th[data-col=rssi] button").click()            # reversed
    assert page.locator("#rfview th[data-col=rssi]").get_attribute("aria-sort") == "descending"
    page.fill("#rf-filter", "G0ABC")
    page.wait_for_function("() => document.querySelectorAll('#rfview tbody tr').length === 1", timeout=5000)
    assert "hello" in rows.nth(0).inner_text()
    page.fill("#rf-filter", "")
    page.locator("#rf-cols input[value=hex]").uncheck()
    assert page.locator("#rfview tbody tr").nth(0).locator("td").nth(7).is_hidden()

    with open(logs / "rf-kiss.log", "a") as f:                            # the poll picks it up
        f.write('2026-09-12T16:02:00.000Z RX rssi=-70.00 snr=8.00 len=1 hex=ff ascii="."\n')
    page.wait_for_function("() => document.querySelectorAll('#rfview tbody tr').length === 3", timeout=15000)

    page.locator("#rf-raw").click()                                       # the raw view is the file
    assert page.locator("#logbox").is_visible() and older in page.locator("#logbox").inner_text()
    page.locator("#rf-raw").click()

    # An encrypted stack: the toggle sits on its own row and, on, the page reads the decoded API.
    (logs / "rf-meshtastic.log").write_text(
        '{"timestamp":1789228997,"rssi":-67,"snr":11.25,"from":1,"to":4294967295,"size":4,"bytes":"01020304"}\n')
    decoded_hits = []

    def decoded(route):
        decoded_hits.append(route.request.url)
        rec = json.loads(page.evaluate("() => fetch('/api/rflog/meshtastic?job=rf-meshtastic.log').then(r => r.text())"))["records"][0]
        body = {"target": "meshtastic", "job": "rf-meshtastic.log", "path": "x", "running": False, "error": "",
                "records": [dict(rec, status="ok", kind="text", peer="!00000001", decoded="<b>hi from the radio</b>")]}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    page.route("**/api/rflog/meshtastic/decoded*", decoded)
    page.goto(page.lab_base + "/logs/meshtastic?job=rf-meshtastic.log", wait_until="networkidle")
    page.wait_for_function("() => document.querySelectorAll('#rfview tbody tr').length === 1", timeout=15000)
    toggle = page.locator("#rf-decrypt")
    assert toggle.get_attribute("aria-pressed") == "false" and not decoded_hits
    box = toggle.bounding_box()
    nav = page.locator("nav.rfswitch").bounding_box()
    assert box["y"] >= nav["y"] + nav["height"], "Decrypt must sit on its own row below the switcher"
    toggle.click()
    page.wait_for_function("() => document.querySelector('#rfview tbody tr td:last-child').textContent.includes('hi from the radio')", timeout=15000)
    assert toggle.get_attribute("aria-pressed") == "true" and decoded_hits
    assert page.locator("#rfview tbody tr td b").count() == 0            # text, never markup
    toggle.click()
    page.wait_for_function("() => document.querySelector('#rf-decrypt').getAttribute('aria-pressed') === 'false'", timeout=5000)
    assert not page.locator("#rfview tbody tr td:last-child").inner_text().strip()

    # The race: a decoded response still in flight when Decrypt goes OFF must never land.
    page.unroute("**/api/rflog/meshtastic/decoded*")
    held = []

    def hold(route):                                                      # hold, do not answer (playwright
        held.append(route)                                                # needs a plain function here)
    page.route("**/api/rflog/meshtastic/decoded*", hold)
    with page.expect_request("**/api/rflog/meshtastic/decoded*", timeout=5000):
        toggle.click()                                                    # ON: a decoded fetch starts and hangs
    page.wait_for_function("() => document.querySelector('#rf-decrypt').getAttribute('aria-pressed') === 'true'", timeout=5000)
    assert held, "the decoded request must be in flight"
    toggle.click()                                                        # OFF while it is in flight
    page.wait_for_function("() => document.querySelector('#rf-decrypt').getAttribute('aria-pressed') === 'false'", timeout=5000)
    rec = json.loads(page.evaluate("() => fetch('/api/rflog/meshtastic?job=rf-meshtastic.log').then(r => r.text())"))["records"][0]
    late = {"target": "meshtastic", "job": "rf-meshtastic.log", "path": "x", "running": False, "error": "",
            "records": [dict(rec, status="ok", kind="text", peer="!00000001", decoded="LATE PLAINTEXT")]}
    # Hold every PLAIN poll from here on: a good plain response landing after the stale decoded
    # one would repair the very state under test and turn a real overwrite into a false green.
    plain_held = []

    def hold_plain(route):
        plain_held.append(route)
    is_plain = lambda u: "/api/rflog/meshtastic?" in u and "/decoded" not in u  # noqa: E731
    page.route(is_plain, hold_plain)
    with page.expect_request(lambda r: is_plain(r.url), timeout=10000):       # the page is now blocked on plain
        pass
    with page.expect_response(lambda r: "/api/rflog/meshtastic/decoded" in r.url, timeout=5000):
        for route in held:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(late))
    # The stale decoded answer has been delivered. The page's OWN next poll (issued by its
    # 2 s interval, and held like the rest) is the observable that its handler has long since
    # run — there is no plain data in flight that could have cleaned up after it.
    with page.expect_request(lambda r: is_plain(r.url), timeout=10000):
        pass
    assert "LATE PLAINTEXT" not in page.locator("#rfview").inner_text()
    for route in plain_held:                                              # let the page carry on
        route.continue_()
    page.unroute(is_plain, hold_plain)
    # And the reveal is never remembered: a reload starts with Decrypt off.
    page.reload(wait_until="networkidle")
    assert page.locator("#rf-decrypt").get_attribute("aria-pressed") == "false"

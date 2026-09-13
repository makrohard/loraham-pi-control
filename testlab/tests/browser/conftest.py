"""Browser lane: headless Chromium (Playwright) over the same real lab server the
acceptance lane uses. Doubly gated — LHPC_BROWSER=1 AND a usable playwright+chromium —
so the default lane and boxes without the browser skip with a reason, never fail."""
from __future__ import annotations

import os

import pytest
from lhpc_testlab.testing import LabServer


def _browser_ready() -> str:
    if os.environ.get("LHPC_BROWSER") != "1":
        return "browser lane is opt-in: set LHPC_BROWSER=1"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not installed (pip install -e .[browser])"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()          # emulated/partial chromium dies HERE,
            page = ctx.new_page()                # not at launch — exercise the full path
            page.goto("about:blank")
            ctx.close()
            browser.close()
    except Exception as exc:
        return f"chromium not usable ({exc}) — run `playwright install --with-deps chromium`"
    return ""


def pytest_collection_modifyitems(config, items):
    reason = _browser_ready()
    if not reason:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "tests/browser" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    root = tmp_path_factory.mktemp("browserlab") / "runtime"
    server = LabServer(root)
    server.init_and_reset()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture()
def page(browser, lab):
    """A fresh page per test that FAILS the test on any JS console error."""
    context = browser.new_context()
    pg = context.new_page()
    errors: list[str] = []
    pg.on("console", lambda msg: errors.append(msg.text)
          if msg.type == "error" else None)
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    pg.lab_base = lab.base
    yield pg
    context.close()
    assert not errors, f"JS console errors: {errors}"


@pytest.fixture()
def seeded_rf_logs(lab):
    """The RF logs rflog.js is proved over, written fresh for each test: a two-segment
    plaintext kiss log (an older rotated segment and the live one) and a one-record meshtastic
    log, the stack whose payloads are encrypted. `meshtastic_record` is that record exactly as
    the plain API serves it — fetched here, in Python, so a route handler that answers the
    decoded API never has to call back into the page for it."""
    import json
    import urllib.request
    from types import SimpleNamespace

    logs = lab.root / "logs"
    logs.mkdir(exist_ok=True)
    older = '2026-09-12T16:00:00.000Z RX rssi=-90.00 snr=2.00 len=3 hex=aabbcc ascii="..."'
    newer = ('2026-09-12T16:01:00.000Z TX rssi=- snr=- len=3 outcome=ok '
             'tnc2="G0ABC>APRS:hello" hex=112233 ascii="..."')
    (logs / "rf-kiss.log.1").write_text(older + "\n")
    (logs / "rf-kiss.log").write_text(newer + "\n")
    (logs / "rf-meshtastic.log").write_text(
        '{"timestamp":1789228997,"rssi":-67,"snr":11.25,"from":1,"to":4294967295,'
        '"size":4,"bytes":"01020304"}\n')
    with urllib.request.urlopen(lab.base + "/api/rflog/meshtastic?job=rf-meshtastic.log",
                                timeout=10) as r:
        record = json.loads(r.read())["records"][0]
    return SimpleNamespace(dir=logs, older=older, newer=newer, meshtastic_record=record)

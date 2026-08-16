"""Browser lane: headless Chromium (Playwright) over the same real lab server the
acceptance lane uses. Doubly gated — LHPC_BROWSER=1 AND a usable playwright+chromium —
so the default lane and boxes without the browser skip with a reason, never fail."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "acceptance"))
from labproc import LabServer


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

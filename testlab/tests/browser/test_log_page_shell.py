"""UI-REORG c6 (plan v7 s5), in a real browser: a static full log page refreshes every 3 s from its preview source
with its history depth, so a log of more than 30 lines still shows all of them after a poll. Time is Playwright's
clock (installed, then the page loaded again), never slept."""
from __future__ import annotations

import re


def _count(page, pattern):
    seen = []
    page.clock.install()
    page.on("request", lambda r: seen.append(r.url) if re.search(pattern, r.url) else None)
    page.reload()
    return seen


def test_a_controller_log_of_50_lines_shows_50_after_each_3_s_poll(flask_page, tmp_path):
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs" / "lhpc-web.log").write_text("".join(f"line {i}\n" for i in range(50)))
    page, _posts = flask_page(path="/controller/logs")
    seen = _count(page, r"/api/logpreview/controller-web\?lines=300$")
    page.wait_for_function("() => document.getElementById('logbox').textContent.split('\\n').length === 50",
                           timeout=15000)
    n = len(seen)
    page.clock.run_for(3000)
    page.evaluate("() => new Promise(r => setTimeout(r, 0))")
    assert len(seen) == n + 1, seen                                       # every 3 s (was 5 s)
    assert page.evaluate("() => document.getElementById('logbox').textContent.split('\\n').length") == 50


def test_the_rf_log_page_polls_every_3_s(flask_page, tmp_path):
    page, _posts = flask_page(path="/logs/loraham-daemon?job=r1/rf-daemon.log&radio=r1")
    assert page.locator("#rfview").count() == 1                           # the RF log's own content (rflog.js)
    seen = _count(page, r"/api/rflog/loraham-daemon\?")
    page.evaluate("() => new Promise(r => setTimeout(r, 0))")
    n = len(seen)
    page.clock.run_for(3000)
    page.evaluate("() => new Promise(r => setTimeout(r, 0))")
    assert len(seen) == n + 1, seen                                       # every 3 s (was 5 s)

"""The auto-install page's live poller keeps the Tests cells empty for a run in which no stack had its tests on
(Q-B2-3 B): the page says so in one line, and a poll of /api/auto-install, whose rows still carry their per-row
"skipped (tests disabled)" state, does not write it back into the cells."""
from __future__ import annotations

import time

from lhpc.core import auto_install as ai
from lhpc.core.paths import Paths


def test_a_poll_leaves_the_tests_cells_of_a_run_without_tests_empty(page, lab):
    paths = Paths(runtime_root=lab.root)
    m = ai.new_marker("c" * 32, "install", "pinned", False, False, [{"id": "daemon"}, {"id": "kiss"}])
    m.update(state="completed", finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    for r in m["stacks"]:
        r.update(status="success", tests={"ran": False, "ok": None, "detail": "skipped (tests disabled)"})
    assert ai.write_marker(paths, m)
    try:
        with page.expect_response(lambda r: "/api/auto-install" in r.url, timeout=15000):
            page.goto(page.lab_base + "/auto-install")
        page.wait_for_timeout(300)                       # the poll's render ran (same task as the response)
        assert page.locator("#ai-tests-off").count() == 1
        cells = page.locator("#ai-tasks tr[data-stack] td:nth-child(3)").all_text_contents()
        assert cells == ["", ""], cells
    finally:
        paths.under(*ai.MARKER).unlink()

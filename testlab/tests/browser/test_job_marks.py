"""UI-REORG c12 (plan v7 s14, OPEN-L86 = a): every task of a stack marks that stack's main node from the task
banner's one /api/tasks poll -- "⏳ <op>" in the row's summary (open or closed) while running, "<op> failed" until
dismissed, gone when done; two tasks, two marks; a Home card tagged "<sid>@<rid>" matches by its stack part; a
running job's body line in the (lazily loaded) body follows the same poll; an item without a stack stays in the
banner only. The feed is answered by the test; time is Playwright's clock, advanced and never slept."""
from __future__ import annotations

import json

_INSTALL = {"kind": "job", "run_id": "install-kiss.log", "attempt_id": "a" * 32, "label": "install kiss",
            "op": "install", "stack": "kiss", "target": "loraham-kiss-tnc", "log": "install-kiss.log",
            "state": "running", "href": "/logs/loraham-kiss-tnc?job=install-kiss.log"}
_BUILD = dict(_INSTALL, run_id="build-kiss.log", attempt_id="b" * 32, label="build kiss", op="build",
              log="build-kiss.log", href="/logs/loraham-kiss-tnc?job=build-kiss.log")
_HMAC = {"kind": "hmac", "run_id": "c" * 32, "label": "HMAC enable on meshcom", "state": "unsafe",
         "href": "/stacks/meshcom/hmac/enable", "hint": "unreadable marker"}       # no stack: banner only
_ROW = "#stackrow-kiss > summary .col-status"


def _open(flask_page, feed):
    page, _posts = flask_page(path="/stacks")
    page.clock.install()
    page.route("**/api/tasks", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"tasks": feed["tasks"]})))
    page.route("**/api/tasks/dismiss", lambda route: (feed["tasks"].remove(feed["dismiss"]), route.fulfill(
        status=200, content_type="application/json", body="{}")))
    page.reload()
    page.clock.pause_at(page.evaluate("() => Date.now()") + 1)           # polls only when the test advances time
    return page


def _marks(page, host):
    return page.evaluate("h => [...document.querySelectorAll(h + ' > [data-task]')].map(m => [m.textContent,"
                         " m.className])", host)


def _wait_marks(page, host, want):
    page.wait_for_function("([h, w]) => JSON.stringify([...document.querySelectorAll(h + ' > [data-task]')]"
                           ".map(m => m.textContent)) === JSON.stringify(w)", arg=[host, want], timeout=15000)


def test_a_stacks_tasks_mark_its_row_and_cards_and_follow_the_one_poll(flask_page):
    feed = {"tasks": [_INSTALL, _HMAC]}
    page = _open(flask_page, feed)
    assert not page.locator("#stackrow-kiss").get_attribute("open")                  # the row closed
    _wait_marks(page, _ROW, ["⏳ install"])
    assert page.locator("#task-banner > [data-key]").count() == 2                     # both in the banner,
    assert page.locator(".badge[data-task]").count() == 1                             # the HMAC item no mark
    page.evaluate("() => { const p = document.createElement('p'); p.setAttribute('data-stack', 'kiss@r3');"
                  " document.querySelector('.stacklist').appendChild(p); }")         # a Home card's host
    feed["tasks"] = [_INSTALL, _BUILD]
    page.clock.run_for(2000)                                                          # the next (fast) poll
    _wait_marks(page, _ROW, ["⏳ install", "⏳ build"])                                # two tasks, two marks
    _wait_marks(page, "[data-stack='kiss@r3']", ["⏳ install", "⏳ build"])          # matched by its stack part
    page.click("#stackrow-kiss > summary")                                            # the lazy body arrives
    _wait_marks(page, ".jobruns[data-jobs-for='kiss']", [
        "⏳ install running (loraham-kiss-tnc) — open log", "⏳ build running (loraham-kiss-tnc) — open log"])
    assert page.get_attribute(".jobruns [data-task='job-build-kiss.log'] a", "href") == \
        "/logs/loraham-kiss-tnc?job=build-kiss.log"
    feed["tasks"] = [dict(_INSTALL, state="failed"), _BUILD]
    page.clock.run_for(2000)
    _wait_marks(page, _ROW, ["install failed", "⏳ build"])                           # running -> failed: its text
    assert _marks(page, _ROW)[0][1] == "badge badge-failed"
    _wait_marks(page, ".jobruns[data-jobs-for='kiss']", ["⏳ build running (loraham-kiss-tnc) — open log"])
    feed["dismiss"] = feed["tasks"][0]
    page.click("#task-banner > [data-key='job-install-kiss.log'] .ti-close")        # dismissed: gone at once
    _wait_marks(page, _ROW, ["⏳ build"])                                             # (one tick, no clock advance)
    feed["tasks"] = [dict(_BUILD, state="done")]
    page.clock.run_for(2000)
    _wait_marks(page, _ROW, [])                                                       # done: gone
    _wait_marks(page, ".jobruns[data-jobs-for='kiss']", [])


def test_a_server_rendered_job_line_meets_its_feed_item_once_and_a_line_without_one_stays(flask_page, monkeypatch):
    """a25 c12 N1 + B2: a web job's server-rendered body line and the same feed item make ONE line (key
    "job-<log>"); a running HMAC apply has a job marker but no feed job item (no attempt id), so its line carries
    no data-task and the poll leaves it. B1: the row shows the live mark, no static "⏳ N job" pill."""
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "active_jobs", lambda self, *a, **k: [
        {"log": "build-kiss.log", "target": "loraham-kiss-tnc", "op": "build", "stack": "kiss",
         "attempt_id": "b" * 32},
        {"log": "hmac-apply-meshcom.log", "target": "meshcom", "op": "hmac-apply", "stack": "meshcom",
         "attempt_id": ""}])
    page = _open(flask_page, {"tasks": [_BUILD]})
    _wait_marks(page, _ROW, ["⏳ build"])
    assert page.locator(".col-extra .pill", has_text="job").count() == 0              # B1: the marks replace it
    page.click("#stackrow-kiss > summary")
    page.click("#stackrow-meshcom > summary")
    page.wait_for_selector(".jobruns[data-jobs-for='meshcom'] > p", state="attached")       # in the DOM
    page.clock.run_for(2000)                                                          # a poll after both bodies
    _wait_marks(page, ".jobruns[data-jobs-for='kiss']", ["⏳ build running (loraham-kiss-tnc) — open log"])
    lines = page.locator(".jobruns[data-jobs-for='meshcom'] > p")
    assert lines.all_text_contents() == ["⏳ hmac-apply running (meshcom) — open log"]
    assert lines.first.get_attribute("data-task") is None

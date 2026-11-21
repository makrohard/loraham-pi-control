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
    page.clock.pause_at((page.evaluate("() => Date.now()") + 1) / 1000)   # polls only when the test advances time (SECONDS)
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


def test_a_lazy_bodys_job_line_newer_than_the_last_reply_stays_until_a_reply_sent_after_it(flask_page, monkeypatch):
    """a22 G2-UXF B1: the body's server-rendered line of a job the last reply did not list yet (started elsewhere
    after the page loaded: the row was lazy) is not pruned by that old snapshot; a later reply decides."""
    from lhpc.core.services import ControllerService
    started = {"on": False}
    monkeypatch.setattr(ControllerService, "active_jobs", lambda self, *a, **k: [
        {"log": "build-kiss.log", "target": "loraham-kiss-tnc", "op": "build", "stack": "kiss",
         "attempt_id": "b" * 32}] if started["on"] else [])
    feed = {"tasks": []}
    page = _open(flask_page, feed)                                      # the last reply: no task, the row lazy
    started["on"] = True                                                # the job starts elsewhere
    page.click("#stackrow-kiss > summary")                              # the lazy body arrives with its line
    line = ".jobruns[data-jobs-for='kiss'] > [data-task='job-build-kiss.log']"
    page.wait_for_selector(line, state="attached", timeout=15000)      # (lhpc:bodyloaded ran in the insert's task)
    assert page.locator(line).count() == 1                              # kept: the old snapshot does not prune
    feed["tasks"] = [_BUILD]
    page.clock.run_for(15000)                                           # the next (slow) poll lists it: one line
    _wait_marks(page, ".jobruns[data-jobs-for='kiss']", ["⏳ build running (loraham-kiss-tnc) — open log"])
    feed["tasks"] = []
    page.clock.run_for(2000)                                            # a later reply without it: gone
    _wait_marks(page, ".jobruns[data-jobs-for='kiss']", [])


def _flowing(flask_page, feed, hold):
    """/stacks with the clock flowing (a 0 ms timer runs by itself); the GETs numbered in `hold` are held, not
    answered: returns (page, gets, held) -- held gets (route, the feed's body as it was when sent)."""
    gets, held = {"n": 0}, []

    def answer(route):
        gets["n"] += 1
        body = json.dumps({"tasks": feed["tasks"]})
        if gets["n"] in hold:
            held.append((route, body))
        else:
            route.fulfill(status=200, content_type="application/json", body=body)
    page, _posts = flask_page(path="/stacks")
    page.clock.install()
    page.route("**/api/tasks", answer)
    page.route("**/api/tasks/dismiss", lambda route: (feed["tasks"].remove(feed["dismiss"]), route.fulfill(
        status=200, content_type="application/json", body="{}")))
    with page.expect_response(lambda r: r.url.endswith("/api/tasks")):
        page.reload()                                                   # reply 1
    _frames(page)                                                       # (its next poll is scheduled)
    return page, gets, held


def _hold_next(page, held):
    with page.expect_request(lambda r: r.url.endswith("/api/tasks")):
        page.clock.run_for(15000)                                       # the next (slow) poll goes out, held
    page.evaluate("() => 1")                                            # (its route handler has run)
    assert len(held) >= 1


def _frames(page):
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def test_a_reply_to_a_get_sent_before_a_lazy_body_arrived_leaves_its_job_line(flask_page, monkeypatch):
    """a25 fold read B1 (the G2 B1 clause): only a reply to a GET sent AFTER the body was inserted prunes its line."""
    from lhpc.core.services import ControllerService
    started = {"on": False}
    monkeypatch.setattr(ControllerService, "active_jobs", lambda self, *a, **k: [
        {"log": "build-kiss.log", "target": "loraham-kiss-tnc", "op": "build", "stack": "kiss",
         "attempt_id": "b" * 32}] if started["on"] else [])
    feed = {"tasks": []}
    page, gets, held = _flowing(flask_page, feed, hold={2})
    _hold_next(page, held)                                              # GET 2 out, before the job and the body
    started["on"] = True
    page.click("#stackrow-kiss > summary")                              # the lazy body arrives with its line
    line = ".jobruns[data-jobs-for='kiss'] > [data-task='job-build-kiss.log']"
    page.wait_for_selector(line, state="attached", timeout=15000)
    route, body = held[0]
    with page.expect_response(lambda r: r.url.endswith("/api/tasks")):
        route.fulfill(status=200, content_type="application/json", body=body)   # the older reply: no job
    _frames(page)
    assert page.locator(line).count() == 1                              # kept
    with page.expect_response(lambda r: r.url.endswith("/api/tasks") and gets["n"] == 3):
        page.clock.run_for(15000)                                       # a GET sent after the body: decides
    _frames(page)
    assert page.locator(line).count() == 0


def test_a_dismiss_while_a_poll_is_out_drops_that_older_reply_and_polls_once_more(flask_page):
    """a22 G2-UXF B2: the reply of a GET sent before the dismiss would re-create the item and its mark; it is dropped
    and one fresh poll follows at once. The clock flows here (that poll is a 0 ms timer)."""
    failed = dict(_INSTALL, state="failed")
    feed = {"tasks": [failed], "dismiss": failed}
    page, gets, held = _flowing(flask_page, feed, hold={2, 3})
    _wait_marks(page, _ROW, ["install failed"])                         # reply 1
    _hold_next(page, held)
    page.click("#task-banner > [data-key='job-install-kiss.log'] .ti-close")
    _wait_marks(page, _ROW, [])                                         # dismissed: its mark at once
    route, body = held[0]
    with page.expect_request(lambda r: r.url.endswith("/api/tasks"), timeout=5000):   # at once, not a 15 s poll
        route.fulfill(status=200, content_type="application/json", body=body)   # the older reply lands: GET 3 out
    page.evaluate("() => 1")
    _frames(page)
    assert len(held) == 2                                               # GET 3 held: the older reply alone
    assert _marks(page, _ROW) == []                                     # never re-created it
    assert page.locator("#task-banner > [data-key='job-install-kiss.log']").count() == 0
    route, body = held[1]
    with page.expect_response(lambda r: r.url.endswith("/api/tasks")):
        route.fulfill(status=200, content_type="application/json", body=body)   # the fresh reply: nothing left
    _frames(page)
    assert _marks(page, _ROW) == [] and gets["n"] == 3


def test_a_failed_poll_after_a_dismiss_does_not_drop_the_next_good_reply(flask_page):
    """a25 fold read N1: the held GET fails; the next reply (a new task) renders, no extra poll at once."""
    failed = dict(_INSTALL, state="failed")
    feed = {"tasks": [failed], "dismiss": failed}
    page, gets, held = _flowing(flask_page, feed, hold={2})
    _wait_marks(page, _ROW, ["install failed"])
    _hold_next(page, held)
    page.click("#task-banner > [data-key='job-install-kiss.log'] .ti-close")
    _wait_marks(page, _ROW, [])
    held[0][0].fulfill(status=200, content_type="application/json", body="not json")   # that GET fails (catch)
    _frames(page)
    feed["tasks"] = [_BUILD]
    page.clock.run_for(15000)                                           # the next (slow) poll: its reply renders
    _wait_marks(page, _ROW, ["⏳ build"])
    _frames(page)
    assert gets["n"] == 3                                               # it was not dropped for a refetch


def test_a_dismiss_with_no_poll_out_polls_once_at_once(flask_page):
    """a22 G2-UXH B5: no GET in flight at the dismiss: one poll at once (no clock advance), so a job started
    elsewhere meanwhile shows its mark."""
    failed = dict(_INSTALL, state="failed")
    feed = {"tasks": [failed], "dismiss": failed}
    page = _open(flask_page, feed)
    _wait_marks(page, _ROW, ["install failed"])
    feed["tasks"].append(_BUILD)                                        # started elsewhere; no poll yet
    page.click("#task-banner > [data-key='job-install-kiss.log'] .ti-close")
    _wait_marks(page, _ROW, ["⏳ build"])


def test_a_real_home_card_of_the_stack_is_marked_and_another_stacks_card_is_not(flask_page, tmp_path):
    """a22 G2-UXF B4 + N1: on Home (served by the console app, meshcom@r1 and meshcore@r3 running) the meshcom@r1
    card carries the job's and the readable HMAC marker's marks, which update and disappear with the feed (a25 B2), the
    meshcore@r3 card none; the /stacks row too."""
    from lhpc.core import commands, instances, radios
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r3", "mstick", "meshstick-1262", "868",
                                                                  usb_serial="MSTICK01"))))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = plain.stack("meshcom").component("meshcom-qemu")
    opt, val = instances._token(plain, comp, commands.Instance("r1", band="433"), "433")
    table = {1000: [comp.process.exec_name, opt, val]}
    inst = instances.parse_target(plain, "meshcore@r3", "start")[1]
    comp = plain.stack("meshcore").component("meshcore-node")
    opt, val = instances._token(plain, comp, inst, "868")
    table[1001] = [comp.process.exec_path or comp.process.exec_name,
                   *(("-m", comp.process.entry) if comp.process.entry else ()),
                   *[a for a in comp.process.all_args if a != opt], opt, val]
    job = dict(_INSTALL, run_id="web-install-meshcom.log", label="install meshcom", stack="meshcom",
               target="meshcom", log="web-install-meshcom.log")              # (a start's end reloads the page)
    hmac = {"kind": "hmac", "run_id": "d" * 32, "label": "HMAC enable on meshcom", "state": "running",
            "href": "/stacks/meshcom/hmac/enable", "stack": "meshcom", "op": "HMAC enable"}
    feed = {"tasks": [job, hmac]}
    page, _posts = flask_page(path="/", cmdlines=table)
    page.clock.install()
    page.route("**/api/tasks", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"tasks": feed["tasks"]})))
    page.reload()
    page.clock.pause_at((page.evaluate("() => Date.now()") + 1) / 1000)   # polls only when the test advances time (SECONDS)
    card = 'p[data-stack="meshcom@r1"]'
    assert page.locator('p[data-stack="meshcore@r3"]').count() == 1      # the other stack's real card
    _wait_marks(page, card, ["⏳ install", "⏳ HMAC enable"])
    assert _marks(page, 'p[data-stack="meshcore@r3"]') == []
    feed["tasks"] = [dict(job, state="failed"), hmac]
    page.clock.run_for(2000)
    _wait_marks(page, card, ["install failed", "⏳ HMAC enable"])        # it updates
    feed["tasks"] = []
    page.clock.run_for(2000)
    _wait_marks(page, card, [])                                         # and disappears
    feed["tasks"] = [job, hmac]
    page.goto(page.url.rsplit("/", 1)[0] + "/stacks")
    _wait_marks(page, "#stackrow-meshcom > summary .col-status", ["⏳ install", "⏳ HMAC enable"])

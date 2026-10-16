# Gate 1: efficiency-a3 (the decided slower polls: dashboard 10 s, log pages 5 s), v1

**Request.** Judge the code commit below against the plan's A3 (quoted). That means: correctness; whether each
red-before test can fail for the defect named; whether behaviour changes only where the plan says (the three intervals
the maintainer decided; everything else unchanged); the CHANGELOG line; and the report's claims against the diff. The
plan is GREEN (gate 1, round 4). The diff is against the quick-win packets' tip (`564737d2`).

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's A3 (plan v4 §4, verbatim)

## 4. A3 — slower polls (decided intervals)
| page | today | after | what the operator sees |
|---|---|---|---|
| dashboard signature (`dash.js:160`) | 4 s; 2 s while an interactive start is pending | 10 s; 2 s while pending (unchanged) | a run-state change made elsewhere (CLI, another tab, boot restore, a crash) reloads the dashboard within 10 s instead of 4 s; a change made on the page itself navigates or reloads at once, as today |
| process log (`logs.js:50`) and RF log (`rflog.js:140`) | 2 s | 5 s | new lines appear within 5 s instead of 2 s; Clear and the switches act at once |
Unchanged: the dashboard's live monitor tick (3 s, `dash.js:73`), the system box (2 s), the daemon socket view (1 s,
3 s), GPS, the task banner, auto-install, HMAC apply. **Tests** (testlab browser lane, Playwright clock as in
`test_system_box.py`): with the clock installed, no signature fetch before 10 s and one at 10 s (pending: at 2 s);
a log page fetches at 5 s, not at 2 s. Docs: the operations page row that names the refresh times, if one does
(grep at code time); CHANGELOG one line ("The dashboard checks for changes every 10 s and log pages every 5 s").

## The commit message (`git log 564737d2..ef4cb067`, full)

### ef4cb067 efficiency: the dashboard and the log pages poll less often

efficiency: the dashboard and the log pages poll less often

The dashboard asked for its structural signature every 4 s and a log
page for new lines every 2 s, each a status read on the box. The
maintainer decided (2026-10-16) on 10 s for the dashboard (2 s while an
interactive start is pending, unchanged) and 5 s for the process log and
the RF log. A change made on the page itself still reloads at once; a
change made elsewhere shows within 10 s, new log lines within 5 s. The
other polls (the live monitor, the system box, the daemon view, GPS) are
unchanged.

Red before: test_the_dashboard_checks_for_structural_changes_every_10_s
(2 checks by 9.9 s), test_a_process_log_asks_for_new_lines_every_5_s and
test_an_rf_log_asks_for_new_records_every_5_s (3 reads by 4.9 s);
test_while_a_start_is_pending_the_dashboard_checks_every_2_s is the
unchanged-case guard. Browser lane, Playwright clock.


## The code report (verbatim; committed as `code-review/code-report-efficiency-a3.md`)

# Code report: efficiency-a3 (the decided slower polls: dashboard 10 s, log pages 5 s)

Branch `cons/efficiency-a3-r1`, stacked on `cons/efficiency-p2-r1` (`564737d2`, P1 v2 + P2 v2) on `origin/dev` =
`d77e34f4`. Plan: `PLAN-efficiency` v4 §4 (gate 1 GREEN, round 4). One commit.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| ef4cb067 | efficiency: the dashboard and the log pages poll less often | static/dash.js, static/logs.js, static/rflog.js, testlab/tests/browser/test_poll_cadence.py, CHANGELOG.md | testlab browser lane, Playwright clock, requests counted at their route: `test_the_dashboard_checks_for_structural_changes_every_10_s` (0 signature requests by 9.9 s, 1 by 10.1 s, 2 by 20.1 s); `test_while_a_start_is_pending_the_dashboard_checks_every_2_s` (1 by 2.1 s, 2 by 4.1 s); `test_a_process_log_asks_for_new_lines_every_5_s` and `test_an_rf_log_asks_for_new_records_every_5_s` (1 read at load, still 1 at 4.9 s, 2 at 5.1 s) | yes, with the three JS lines reverted (`git apply -R`): `3 failed, 1 passed` — 2 signature requests by 9.9 s; 3 log reads by 4.9 s (both pages); the pending test passes (the unchanged case, a guard) |

**How the lane was run here.** `PYTHONPATH=<worktree>:<worktree>/testlab LHPC_BROWSER=1 python -m pytest -q
-p no:cacheprovider testlab/tests/browser` — both paths, because the venv's editable installs resolve `lhpc` and
`lhpc_testlab` from another checkout otherwise (checked: `lhpc_testlab.__file__` and `lhpc.__file__` both under the
worktree). An earlier attempt hit "Reset aborted — could not stop: meshcom" while a concurrent real MeshCom stack ran
on this PC (another item's runner gate); after it ended the lane ran clean.

**Green.** Browser lane, whole: `50 passed, 1 skipped in 79.39s`. The new file alone: `4 passed`. Testlab unit lane
(`python -m pytest -q testlab/tests`, both paths): `145 passed, 137 skipped` (the opt-in lanes skip). `tests/repo
tests/web`: `1644 passed, 1 skipped`. `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select
F,E9` → `All checks passed!`. CI: testlab workflow run 38830638331 on ef4cb067, conclusion `success`; its `testlab` job ran the unit lane (`152 passed`), the acceptance lane (`52 passed`) and the browser lane (`51 passed`, which includes `test_poll_cadence.py`), in that order (`gh run view --job 116748371397 --log`).

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  564737d2..ef4cb067
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 26 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        4 new tests; layers testlab/tests/browser=testlab; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- Three numbers changed (`dash.js:160` 4000 → 10000; `logs.js:50` and `rflog.js:140` 2000 → 5000); no new code path.
  The pending interval (2000) and every other timer (`dash.js:73` monitor 3000, the system box, the daemon view,
  GPS, the task banner, auto-install, HMAC) are untouched.
- Lines: the three JS files keep their lengths (205, 52, 142); the new test file is 100 lines.

## Deviations from the plan

- None. The plan's "docs: the operations page row that names the refresh times, if one does": none does (`grep` of
  `docs/` for the intervals and "poll"/"refresh" near the dashboard or log pages finds no such sentence), so only the
  CHANGELOG line states it.

## The 6-point block

1. **Contracts.** The dashboard's signature poll fires every 10 000 ms while visible (2 000 ms when the grid carries
   `data-pending-interactive="1"` at load), skips while `document.hidden`, and reloads only when the signature changed
   (unchanged code around the number). A log page reads once at load and then every 5 000 ms (`logs.js:50`,
   `rflog.js:140`); Clear, the switches and Decrypt act at once, as before.
2. **Invariants and their tests.** The four tests above, one per interval; the pending one is the guard that the
   unchanged case stays 2 s. The existing browser tests that depend on these pages (the RF-log viewer's poll picks up
   an appended row; the dashboard daemon polling) stay green in the full lane run.
3. **Failure classes.** Fakes: the signature route answers `null` ("nothing structural"), as the existing
   `test_dash_polling.py` does; the log routes are counted and continued to the lab's real server. Time is
   Playwright's clock, never slept through. EIO/EACCES, Ctrl-C, stacked conflicts: not reachable by three constants.
   CLI/web/job/boot: the CLI never polls; nothing server-side changed.
4. **Test rules.** A real browser (rule 6), observable requests, no `wait_for_timeout`, no hand-built DOM; the pending
   state is set the way the server sets it (the grid attribute before any script runs).
5. **Directories and ruff.** As under Green.
6. **Adversarial self-review.** Found: (a) the first lane run passed against another checkout's code (the editable
   install) — rerun with both paths, and the red was taken with the lines reverted in the same setup; (b) the
   testlab unit lane failed 39 times with only `lhpc` on the path (stale `lhpc_testlab`) — clean with both paths.
   Not changed: `dash.js:73`'s monitor tick (3 s) is a live field, not part of the decision.

## Numbers, measured

| figure | command | output |
|---|---|---|
| requests by 9.9 s at the old 4 s | the dashboard test, JS reverted | `assert 2 == 0` |
| log reads by 4.9 s at the old 2 s | the two log tests, JS reverted | `assert 3 == 1` (each) |
| browser lane | as above | `50 passed, 1 skipped` |

## The diff (`git diff 564737d2 ef4cb067`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 0f34cadc..c3696c39 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -24,6 +24,8 @@ Fixed
   index in the stack's source checkout.
 - The dashboard's and the log pages' background refresh no longer runs git for every installed source; what they
   show is unchanged.
+- The dashboard checks for changes made elsewhere every 10 s (was 4 s; still 2 s while a start is pending), and log
+  pages fetch new lines every 5 s (was 2 s), which lowers the console's load on a small box.
 
 ## 0.12.9
 
diff --git a/lhpc/adapters/web/static/dash.js b/lhpc/adapters/web/static/dash.js
index 7bac5367..b02642dc 100644
--- a/lhpc/adapters/web/static/dash.js
+++ b/lhpc/adapters/web/static/dash.js
@@ -157,7 +157,7 @@
           }
         })
         .catch(function () { /* transient */ });
-    }, pending ? 2000 : 4000);
+    }, pending ? 2000 : 10000);
   }
 
   // "Scan now": ONE real CAD measurement, on explicit click only.
diff --git a/lhpc/adapters/web/static/logs.js b/lhpc/adapters/web/static/logs.js
index 11a67e1d..3107b463 100644
--- a/lhpc/adapters/web/static/logs.js
+++ b/lhpc/adapters/web/static/logs.js
@@ -47,6 +47,6 @@
   }
 
   box.scrollTop = box.scrollHeight;     // start at the newest line
-  setInterval(poll, 2000);
+  setInterval(poll, 5000);
   poll();
 })();
diff --git a/lhpc/adapters/web/static/rflog.js b/lhpc/adapters/web/static/rflog.js
index 2dec02d3..94dca760 100644
--- a/lhpc/adapters/web/static/rflog.js
+++ b/lhpc/adapters/web/static/rflog.js
@@ -137,6 +137,6 @@
   });
 
   render();
-  setInterval(poll, 2000);
+  setInterval(poll, 5000);
   poll();
 })();
diff --git a/testlab/tests/browser/test_poll_cadence.py b/testlab/tests/browser/test_poll_cadence.py
new file mode 100644
index 00000000..324ba95a
--- /dev/null
+++ b/testlab/tests/browser/test_poll_cadence.py
@@ -0,0 +1,100 @@
+"""The console's slow polls, in a real browser: the dashboard asks whether anything structural
+changed every 10 s (every 2 s while an interactive start is pending), and a log page asks for new
+lines every 5 s.
+
+Each poll is counted at its route; time is Playwright's clock, advanced by the test and never
+slept through. What is asserted is the number of requests in a window.
+"""
+from __future__ import annotations
+
+
+def _settle(page):
+    """Every request the page issued before this call has reached its route handler."""
+    assert page.evaluate("() => fetch('/__settled__').then(r => r.status)") == 204
+
+
+def _count(page, pattern, answer=None):
+    seen = {"n": 0}
+
+    def handler(route):
+        seen["n"] += 1
+        if answer is None:
+            route.continue_()
+        else:
+            answer(route)
+    page.route(pattern, handler)
+    return seen
+
+
+def _open_dashboard(page, pending=False):
+    page.clock.install()
+    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
+    # "nothing structural changed": the answer never reloads the page under the test
+    sig = _count(page, "**/api/dash-signature", lambda route: route.fulfill(
+        status=200, content_type="application/json", body="null"))
+    page.route("**/api/daemon/*", lambda route: route.fulfill(
+        status=200, content_type="application/json", body='{"reachable": false}'))
+    if pending:
+        # The page marks a pending interactive start on the grid it renders; set it before any
+        # script runs, as the server does then.
+        page.add_init_script(
+            "new MutationObserver(() => { const g = document.querySelector('.radiogrid');"
+            " if (g) g.setAttribute('data-pending-interactive', '1'); })"
+            ".observe(document, {childList: true, subtree: true});")
+    page.goto(page.lab_base + "/", wait_until="load")
+    _settle(page)
+    assert sig["n"] == 0
+    return sig
+
+
+def test_the_dashboard_checks_for_structural_changes_every_10_s(page):
+    sig = _open_dashboard(page)
+    page.clock.run_for(9900)
+    _settle(page)
+    assert sig["n"] == 0
+    page.clock.run_for(200)
+    _settle(page)
+    assert sig["n"] == 1
+    page.clock.run_for(10000)
+    _settle(page)
+    assert sig["n"] == 2
+
+
+def test_while_a_start_is_pending_the_dashboard_checks_every_2_s(page):
+    sig = _open_dashboard(page, pending=True)
+    page.clock.run_for(2100)
+    _settle(page)
+    assert sig["n"] == 1
+    page.clock.run_for(2000)
+    _settle(page)
+    assert sig["n"] == 2
+
+
+def _open_log(page, path, pattern):
+    page.clock.install()
+    page.route("**/__settled__", lambda route: route.fulfill(status=204, body=""))
+    seen = _count(page, pattern)
+    page.goto(page.lab_base + path, wait_until="load")
+    _settle(page)
+    assert seen["n"] == 1                          # the first read at load
+    return seen
+
+
+def test_a_process_log_asks_for_new_lines_every_5_s(page):
+    seen = _open_log(page, "/logs/loraham-kiss-tnc", "**/api/logs/**")
+    page.clock.run_for(4900)
+    _settle(page)
+    assert seen["n"] == 1
+    page.clock.run_for(200)
+    _settle(page)
+    assert seen["n"] == 2
+
+
+def test_an_rf_log_asks_for_new_records_every_5_s(page, seeded_rf_logs):
+    seen = _open_log(page, "/logs/loraham-kiss-tnc?job=rf-kiss.log", "**/api/rflog/**")
+    page.clock.run_for(4900)
+    _settle(page)
+    assert seen["n"] == 1
+    page.clock.run_for(200)
+    _settle(page)
+    assert seen["n"] == 2
```

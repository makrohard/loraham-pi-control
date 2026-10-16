# Code report: efficiency-a3 (the decided slower polls: dashboard 10 s, log pages 5 s)

Branch `cons/efficiency-a3-r1`, stacked on `cons/efficiency-p2-r1` (`564737d2`, P1 v2 + P2 v2) on `origin/dev` =
`d77e34f4`. Plan: `PLAN-efficiency` v4 §4 (gate 1 GREEN, round 4). One commit.

**For the record (after gate 1 round 8 GREEN):** the second reader's note, adopted by the handler: the CHANGELOG bullet moved from "Fixed" to a new "Changed" section and lost the clause "which lowers the console's load on a small box" (not measured here). Code commit `ef4cb067` → `597d1f02`, that line only; the gate-1 file below this report is kept as audited.

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

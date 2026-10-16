# Code report: testrun-p1 v2 (fixed waits → polls with a 5 s deadline)

## Changes v1 → v2 (round 7 verdict, RED, 1 finding; accepted)

| finding | change |
|---|---|
| `for _ in range(50)` + `sleep(0.1)` bounds iterations, not time | each of the four sites sets `deadline = time.monotonic() + 5.0` and polls `while <predicate does not hold> and time.monotonic() < deadline: time.sleep(0.1)`, then asserts the predicate; the commit message, the table below and the negative run are redone on the new code |

Branch `cons/testrun-p1-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-testrun v3 §3 P1 (gate 1 GREEN WITH NOTES).
Tests only; no production file.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| b6a531cd | tests/core/test_bounded_runner.py, tests/core/test_structured_exec.py, tests/stacks/test_stack_params.py | core and stacks layers, the same 4 tests (5 cases): the asserted predicate is unchanged (`procident.proc_alive`, `_dead_or_zombie`, `life._proc_alive`, `svc._component_booting(...) is False`) | none: a test-suite change, no behaviour. Negative run instead (below) |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        0 new tests; layers tests/core=core tests/stacks=stacks; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 4 startswith 0 network 0   WARN
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 1)
```

The WARN, answered: `sleep 4` counts the four added `time.sleep(0.1)` lines. Each is the step of a poll loop
(`deadline = time.monotonic() + 5.0`; `while <predicate does not hold> and time.monotonic() < deadline:
time.sleep(0.1)`) followed by the same assert, so a wait ends as soon as the predicate holds and the assert
runs once 5 s of monotonic time have passed. They replace four `time.sleep(0.3)` lines.

## What changed

| site (on d77e34f4) | before | after |
|---|---|---|
| tests/core/test_bounded_runner.py:44 `test_timeout_terminates_child_tree` | `time.sleep(0.3)`, then `assert not procident.proc_alive(child_pid)` | poll while `procident.proc_alive(child_pid)`, 5 s monotonic deadline, same assert |
| tests/core/test_bounded_runner.py:267 `test_sleeping_step_is_stalled` (2 cases) | `time.sleep(0.3)`, then `assert _dead_or_zombie(<pid from the log>)` | the pid read once into `bg`; poll while `not _dead_or_zombie(bg)`, 5 s deadline, same assert |
| tests/core/test_structured_exec.py:140 `test_identity_mismatch_leaves_no_record_and_terminates` | `time.sleep(0.3)`, then `assert not life._proc_alive(p.pid)` | poll while `life._proc_alive(p.pid)`, 5 s deadline, same assert |
| tests/stacks/test_stack_params.py:200 `test_component_booting_tracks_live_post_runner` | `time.sleep(0.3)`, then `assert svc._component_booting("meshcom-qemu") is False` | poll while `svc._component_booting("meshcom-qemu") is not False`, 5 s deadline, same assert |

A local loop per site, no shared helper (plan: a local loop per file). `stacks/test_manifest_model.py:364` (1.5 s, proves an absence) is kept, as
planned.

## Negative run (the poll can fail)

Temporary copies of the three files with only the polled predicate replaced by a lambda that never holds
(`(lambda _p: True)(child_pid)`, `(lambda _p: False)(bg)`, `(lambda _p: True)(p.pid)`,
`(lambda _c: True)("meshcom-qemu")` in the loop condition and its assert only), run with `--durations=0`, then
deleted (`git status --short` after: only the three edited files):

| case | result | duration |
|---|---|---|
| test_timeout_terminates_child_tree | FAILED (assert on the predicate) | 5.67 s |
| test_sleeping_step_is_stalled[fast] | FAILED | 6.43 s |
| test_sleeping_step_is_stalled[controlled] | FAILED | 6.41 s |
| test_identity_mismatch_leaves_no_record_and_terminates | FAILED | 6.11 s |
| test_component_booting_tracks_live_post_runner | FAILED (`assert True is False`) | 5.08 s |

(Each duration is the test's whole call: its setup run before the poll, plus the 5 s deadline.)

In v1 a first try of the last case also replaced the test's earlier `is False` check (no runner yet) and failed
after 0.03 s, before the loop; since then only the loop's two lines are replaced (above).

## The 6-point block

1. **Contracts:** none; the four tests assert the same predicates.
2. **Invariants:** none new; each test still fails when the process does not end (the negative run).
3. **Failure classes:** a slow runner where the kill or the reap takes over 0.3 s now passes once it lands; a
   process that never ends still fails, after 5 s of monotonic time instead of 0.3 s, however slow each poll
   iteration is.
4. **Test rules:** rule 10 (each loop's assert can fail: shown); no sleep before an assert at the same level
   remains in these files (the plan's scan class "fixed wait then assert": 4 → 1, the kept absence check).
5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/stacks
   tests/repo` → `4090 passed, 1 skipped in 71.62s`; `ruff check lhpc testlab` and `ruff check tests --select F,E9`
   → `All checks passed!`.
6. **Adversarial self-review:**
   - (a) The first local run started under plain `setsid`, which forked and returned; its basetemp was removed
     while it ran. It was stopped by PID and rerun with `setsid -w`; only the second run's figure is used.
   - (b) The first commit message used a word the checker flags (WARN `words 1`); reworded before the push.
   - (c) The stalled-step pid is now read from the log once, before the loop, instead of inside the assert;
     the log is complete then (the run returned).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | d77e34f4 |
| diff | `git diff --stat origin/dev b6a531cd` | 3 files changed, 14 insertions(+), 5 deletions(-) |
| fixed waits before/after | `python3 -I testrun-scan-v3.py`, class "timing: sleep, fixed wait then assert" | 4 / 1 |
| focused cases | `pytest … -k "terminates_child_tree or sleeping_step_is_stalled or identity_mismatch_leaves_no_record or component_booting_tracks"` | 5 passed |
| directories | the command in 5 | 4090 passed, 1 skipped |

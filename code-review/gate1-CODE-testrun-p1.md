# Gate 1: testrun-p1 v2 (fixed waits → polls with a 5 s deadline)

**Request.** Round 8: the correction of v1 (your round 7: RED, 1 finding: an iteration bound is not a deadline); change table at the top of the report. Judge: does each of the four sites now poll the same predicate it asserted before until a real 5 s deadline on `time.monotonic()` passes, and nothing else changed; can each loop's assert still fail (the negative run, redone on the new code); is any claim in the report stronger than the diff; is the WARN answered truthfully.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## The plan, §3 P1 and §4 (PLAN-testrun v3)

> | P1 fixed waits | `core/test_bounded_runner.py`, `core/test_structured_exec.py`, `stacks/test_stack_params.py` | the 4 waits → poll the same predicate to a 5 s deadline (a local loop) | each passes; with the predicate stubbed never to hold, the poll fails at its deadline |
> ## 4. Per packet (brief v2.1)
> Test-suite changes, not fixes of behaviour: no parent-red; each states "test-suite change" and shows its negative
> run. Before the push: `tools/check_houserules.py origin/dev..HEAD`, the touched test directories + `tests/repo`,
> `ruff check lhpc testlab`, `ruff check tests --select F,E9`. Report + gate-1 packet per packet with the scan
> before/after; lands on dev only on the handler's line.

## Commit (full message, `git log -1 --format=%B`)

### b6a531cd testrun-p1: a wait for a process to end polls to a deadline instead of sleeping a fixed time

```text
testrun-p1: a wait for a process to end polls to a deadline instead of sleeping a fixed time

Four checks slept a fixed 0.3 s and then asserted that a process had
ended (tests/README.md, the test-run plan's "fixed wait then assert"):
test_bounded_runner.py's grandchild after a timeout and the background
sleep of a stalled step, test_structured_exec.py's terminated child,
and test_stack_params.py's finished post-start runner. On a loaded runner
0.3 s can be too short. Each now polls the same predicate every 0.1 s
until it holds or a 5 s deadline on time.monotonic() passes, then asserts
it.

Test-suite change, no behaviour: no parent-red. Negative run instead:
with each polled predicate stubbed never to hold, each of the 5 cases
fails at the deadline (5 s), not before.

```

## The report

> # Code report: testrun-p1 v2 (fixed waits → polls with a 5 s deadline)
>
> ## Changes v1 → v2 (round 7 verdict, RED, 1 finding; accepted)
>
> | finding | change |
> |---|---|
> | `for _ in range(50)` + `sleep(0.1)` bounds iterations, not time | each of the four sites sets `deadline = time.monotonic() + 5.0` and polls `while <predicate does not hold> and time.monotonic() < deadline: time.sleep(0.1)`, then asserts the predicate; the commit message, the table below and the negative run are redone on the new code |
>
> Branch `cons/testrun-p1-r1` on `origin/dev` = `d77e34f4`. Plan: PLAN-testrun v3 §3 P1 (gate 1 GREEN WITH NOTES).
> Tests only; no production file.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | b6a531cd | tests/core/test_bounded_runner.py, tests/core/test_structured_exec.py, tests/stacks/test_stack_params.py | core and stacks layers, the same 4 tests (5 cases): the asserted predicate is unchanged (`procident.proc_alive`, `_dead_or_zombie`, `life._proc_alive`, `svc._component_booting(...) is False`) | none: a test-suite change, no behaviour. Negative run instead (below) |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        0 new tests; layers tests/core=core tests/stacks=stacks; red-before 0/0   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 4 startswith 0 network 0   WARN
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 1)
> ```
>
> The WARN, answered: `sleep 4` counts the four added `time.sleep(0.1)` lines. Each is the step of a poll loop
> (`deadline = time.monotonic() + 5.0`; `while <predicate does not hold> and time.monotonic() < deadline:
> time.sleep(0.1)`) followed by the same assert, so a wait ends as soon as the predicate holds and the assert
> runs once 5 s of monotonic time have passed. They replace four `time.sleep(0.3)` lines.
>
> ## What changed
>
> | site (on d77e34f4) | before | after |
> |---|---|---|
> | tests/core/test_bounded_runner.py:44 `test_timeout_terminates_child_tree` | `time.sleep(0.3)`, then `assert not procident.proc_alive(child_pid)` | poll while `procident.proc_alive(child_pid)`, 5 s monotonic deadline, same assert |
> | tests/core/test_bounded_runner.py:267 `test_sleeping_step_is_stalled` (2 cases) | `time.sleep(0.3)`, then `assert _dead_or_zombie(<pid from the log>)` | the pid read once into `bg`; poll while `not _dead_or_zombie(bg)`, 5 s deadline, same assert |
> | tests/core/test_structured_exec.py:140 `test_identity_mismatch_leaves_no_record_and_terminates` | `time.sleep(0.3)`, then `assert not life._proc_alive(p.pid)` | poll while `life._proc_alive(p.pid)`, 5 s deadline, same assert |
> | tests/stacks/test_stack_params.py:200 `test_component_booting_tracks_live_post_runner` | `time.sleep(0.3)`, then `assert svc._component_booting("meshcom-qemu") is False` | poll while `svc._component_booting("meshcom-qemu") is not False`, 5 s deadline, same assert |
>
> A local loop per site, no shared helper (plan: a local loop per file). `stacks/test_manifest_model.py:364` (1.5 s, proves an absence) is kept, as
> planned.
>
> ## Negative run (the poll can fail)
>
> Temporary copies of the three files with only the polled predicate replaced by a lambda that never holds
> (`(lambda _p: True)(child_pid)`, `(lambda _p: False)(bg)`, `(lambda _p: True)(p.pid)`,
> `(lambda _c: True)("meshcom-qemu")` in the loop condition and its assert only), run with `--durations=0`, then
> deleted (`git status --short` after: only the three edited files):
>
> | case | result | duration |
> |---|---|---|
> | test_timeout_terminates_child_tree | FAILED (assert on the predicate) | 5.67 s |
> | test_sleeping_step_is_stalled[fast] | FAILED | 6.43 s |
> | test_sleeping_step_is_stalled[controlled] | FAILED | 6.41 s |
> | test_identity_mismatch_leaves_no_record_and_terminates | FAILED | 6.11 s |
> | test_component_booting_tracks_live_post_runner | FAILED (`assert True is False`) | 5.08 s |
>
> (Each duration is the test's whole call: its setup run before the poll, plus the 5 s deadline.)
>
> In v1 a first try of the last case also replaced the test's earlier `is False` check (no runner yet) and failed
> after 0.03 s, before the loop; since then only the loop's two lines are replaced (above).
>
> ## The 6-point block
>
> 1. **Contracts:** none; the four tests assert the same predicates.
> 2. **Invariants:** none new; each test still fails when the process does not end (the negative run).
> 3. **Failure classes:** a slow runner where the kill or the reap takes over 0.3 s now passes once it lands; a
>    process that never ends still fails, after 5 s of monotonic time instead of 0.3 s, however slow each poll
>    iteration is.
> 4. **Test rules:** rule 10 (each loop's assert can fail: shown); no sleep before an assert at the same level
>    remains in these files (the plan's scan class "fixed wait then assert": 4 → 1, the kept absence check).
> 5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/stacks
>    tests/repo` → `4090 passed, 1 skipped in 71.62s`; `ruff check lhpc testlab` and `ruff check tests --select F,E9`
>    → `All checks passed!`.
> 6. **Adversarial self-review:**
>    - (a) The first local run started under plain `setsid`, which forked and returned; its basetemp was removed
>      while it ran. It was stopped by PID and rerun with `setsid -w`; only the second run's figure is used.
>    - (b) The first commit message used a word the checker flags (WARN `words 1`); reworded before the push.
>    - (c) The stalled-step pid is now read from the log once, before the loop, instead of inside the assert;
>      the log is complete then (the run returned).
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | d77e34f4 |
> | diff | `git diff --stat origin/dev b6a531cd` | 3 files changed, 14 insertions(+), 5 deletions(-) |
> | fixed waits before/after | `python3 -I testrun-scan-v3.py`, class "timing: sleep, fixed wait then assert" | 4 / 1 |
> | focused cases | `pytest … -k "terminates_child_tree or sleeping_step_is_stalled or identity_mismatch_leaves_no_record or component_booting_tracks"` | 5 passed |
> | directories | the command in 5 | 4090 passed, 1 skipped |

## The full diff (`git diff d77e34f4c775d85ba0822b373ec4243651be5cc9 b6a531cd00ceeff43b1c7bf422411bf7473b885e`)

```diff
diff --git a/tests/core/test_bounded_runner.py b/tests/core/test_bounded_runner.py
index e6271430..a7328566 100644
--- a/tests/core/test_bounded_runner.py
+++ b/tests/core/test_bounded_runner.py
@@ -41,11 +41,13 @@ def test_timeout_terminates_child_tree():
     r = RealCommandRunner().run(["python3", "-c", prog], timeout=0.6)
     assert r.timed_out
     child_pid = int(r.stdout.strip().splitlines()[0])
-    time.sleep(0.3)
     # The grandchild was killed with the group. Use the PRODUCTION liveness predicate, which reads
     # /proc/<pid>/stat and treats a reaped-pending zombie (Z/X) as ceased — `kill -0` would call a
     # zombie "alive", so it fails on any non-reaping init (containers/some CI).
     from lhpc.core import procident
+    deadline = time.monotonic() + 5.0                  # a 5 s deadline, polled every 0.1 s
+    while procident.proc_alive(child_pid) and time.monotonic() < deadline:
+        time.sleep(0.1)
     assert not procident.proc_alive(child_pid)
 
 
@@ -264,8 +266,11 @@ def test_sleeping_step_is_stalled(tmp_path, controlled):
                                               timeout=60, log_fh=fh, stall_s=1, sample_s=0.2, **kw)
     assert r.timed_out and r.returncode == 124 and r.stop_reason == "stalled"
     assert time.time() - t0 < 5.0
-    time.sleep(0.3)
-    assert _dead_or_zombie(int(log.read_text().split()[0]))    # the background sleep is gone too
+    bg = int(log.read_text().split()[0])
+    deadline = time.monotonic() + 5.0                  # a 5 s deadline, polled every 0.1 s
+    while not _dead_or_zombie(bg) and time.monotonic() < deadline:
+        time.sleep(0.1)
+    assert _dead_or_zombie(bg)                            # the background sleep is gone too
 
 
 @pytest.mark.slow
diff --git a/tests/core/test_structured_exec.py b/tests/core/test_structured_exec.py
index 06e3c035..5af6adf4 100644
--- a/tests/core/test_structured_exec.py
+++ b/tests/core/test_structured_exec.py
@@ -137,7 +137,9 @@ def test_identity_mismatch_leaves_no_record_and_terminates(tmp_path, reaper):
     assert ok != "ok"                                          # not owned
     assert ok in ("ceased", "unverified")                      # typed cleanup status
     assert life.owned_records("racer") == []
-    time.sleep(0.3)
+    deadline = time.monotonic() + 5.0                  # a 5 s deadline, polled every 0.1 s
+    while life._proc_alive(p.pid) and time.monotonic() < deadline:
+        time.sleep(0.1)
     assert not life._proc_alive(p.pid)                          # session terminated
 
 
diff --git a/tests/stacks/test_stack_params.py b/tests/stacks/test_stack_params.py
index fb4319b8..aa05db59 100644
--- a/tests/stacks/test_stack_params.py
+++ b/tests/stacks/test_stack_params.py
@@ -197,7 +197,9 @@ def test_component_booting_tracks_live_post_runner(tmp_path):
         assert svc._component_booting("meshcom-qemu") is True          # runner alive -> booting
     finally:
         p.terminate(); p.wait()
-    time.sleep(0.3)
+    deadline = time.monotonic() + 5.0                  # a 5 s deadline, polled every 0.1 s
+    while svc._component_booting("meshcom-qemu") is not False and time.monotonic() < deadline:
+        time.sleep(0.1)
     assert svc._component_booting("meshcom-qemu") is False             # runner gone -> ready
 
 
```

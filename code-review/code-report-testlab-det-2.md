# Code report: testlab-det-2 (packet P2 = plan item M2 + decision c4, step 1)

Branch `cons/testlab-det-2-r1`, rebased on `origin/dev` = `f874040d` (P1, P3a, the house rules and field-fixes-p1 landed). Plan: `plans/PLAN-testlab-determinism.md` v3 (gate 1 GREEN, round 4) with the maintainer's decisions (DECISIONS-testlab-determinism.md): item M2, and decision c4 (remove the NomadNet retry and find the cause; step one is instrumentation, because the saved logs do not show the cause). M4 and S2 follow in their own packet: M4's hook in slow-build's `_judged` touches a file P1 changed.

## Commits

| commit | subject | files | tests | red-before |
|---|---|---|---|---|
| 2ea5b5b0 | testlab: a readiness window marks the stack only when the component is gone | testlab/lhpc_testlab/release.py (`readiness_failed`, the `stack_regression` docstring example), testlab/tests/release/test_release_verify.py (21 sites), testlab/tests/unit/test_release_attribution.py, docs/testlab.md, CHANGELOG.md | 3 cases, 9 parametrizations (unit layer; rule 10: whether the message contains a line of the lane's own marker grammar, and the state token it read): `running`/`degraded`/`unknown` → unmarked; `stopped`/`failed` → marked FIRST; status rc 1, rc 2 with "stopped" text, component not listed, `TimeoutExpired` → unmarked, state `unreadable` | yes, against eb9673a4: all 9 fail `AttributeError: module 'lhpc_testlab.release' has no attribute 'readiness_failed'`. This red is the new helper's absence; the parent's behaviour is that every one of the 21 sites marks unconditionally (e.g. test_release_verify.py:113 on eb9673a4: `f"{stack_regression('kiss', 'readiness')}\n"` in the message of `assert wait_tcp(KISS_TCP, 120)`) |
| aa5410dd | testlab: an interactive start that times out is diagnosed, never retried | release.py (`pty_readiness` runs once, `on_timeout`; `reticulum_peers_snapshot`; `import warnings` removed), testlab/tests/release/conftest.py (`PYTHONFAULTHANDLER=1`), test_release_verify.py (the NomadNet case; `record_property` and `record=` removed from 4 callers), test_release_attribution.py, docs/testlab.md, CHANGELOG.md | `test_a_start_that_times_out_is_not_retried_and_carries_its_stack`, `test_the_timeout_diagnosis_runs_while_the_program_still_hangs`, `test_the_reticulum_snapshot_aborts_only_reticulum_processes_and_returns_their_stacks` (unit layer; real processes and a real pty); the retry's 4 tests removed (1 replaced, 3 obsolete) | yes, against eb9673a4. The not-retried case fails `DID NOT RAISE AssertionError` (the parent retries and the second start draws: the behaviour itself). The diagnosis and snapshot cases fail on the missing API (`unexpected keyword argument 'on_timeout'`; `cannot import name 'reticulum_peers_snapshot'`) |

Commands: red-before = the committed test module copied beside the parent's lane in a worktree of eb9673a4, `PYTHONPATH=<parent>/testlab:<parent> python -m pytest -q -p no:cacheprovider <copy> -k <case>` (the copy removed afterwards; `git status --short` empty). Green on 1078189e: `PYTHONPATH=<worktree>/testlab:<worktree> python -m pytest -q -p no:cacheprovider -n 8 testlab/tests/unit tests/repo` → `668 passed, 8 skipped`; `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`. Green on the head aa5410dd: the same command → `721 passed, 8 skipped` (dev's newer tests included); ruff clean; the snapshot case 3/3 in a row.

## v2: the second read's 3 points (all accepted)

1. **Lab safety:** `reticulum_peers_snapshot` SIGABRTed the process group named by an owned-record FILENAME, unchecked, one record per component. A stale record after pid reuse could abort an unrelated group. Now each record's JSON is read, and its process is signalled only when `proc_identity(pid)` (lhpc/core/procident.py) has the recorded starttime, pgid and sid and sid == pid, the rule of `Lifecycle.verify_owned`. A skipped record is named in the output; records are not merged per component. Test `test_the_reticulum_snapshot_aborts_only_recorded_reticulum_processes`: a stale `lxmd` record (its starttime off by one) is left alone, `rns` is aborted, another stack's process is untouched. Red-before against the first version of the c4 commit (e96413fc): `assert -6 is None`, the stale record's process was SIGABRTed. Green 3/3.
2. The 6-point block's stale sentence about `time.sleep(0.5)` is deleted (the handshake replaced it in v1's amend).
3. Line numbers are re-quoted from the head (aa5410dd), as below.

The lane runs above ran on 2bbdb442 / e96413fc. The head differs from e96413fc by the snapshot's identity check, its test, and the rebase onto f874040d (CHANGELOG context). Under the landing rule of 2026-10-16 the batch run on dev is the lane proof.

## HOUSE RULES KEPT (dev's tools/check_houserules.py, on origin/dev..aa5410dd)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +2 bullets (limit 2, each ≤ 3 lines; longest 2), top section 16 lines   OK
docs         1 files, +9 lines; fact "readiness_failed" -> docs/testlab.md; fact "reticulum_peers_snapshot" -> docs/testlab.md   OK
tests        10 new tests; layers testlab/tests/release=testlab testlab/tests/unit=testlab; red-before 2/2   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 1 startswith 1 network 0   WARN
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 1)
```

The patterns WARN (two tokens), answered:
- **patterns `sleep 1`:** the token is inside the child command STRING `"import time; print('ready', flush=True); time.sleep(60)"`, the process being aborted, not a wait in the test. The test's own timing wait was replaced by a `ready` handshake (that is the e96413fc amend).
- **patterns `startswith 1`:** `assert msg.startswith("STACK-REGRESSION stack=kiss phase=readiness\n")` compares the WHOLE first line, and "the marker is the first line" is the grammar's contract (rule 2: exact where exactness is the contract).
- (attribution: `words 0`. The snapshot's identity check is worded with `pgid`/`sid`, not the POSIX word for a process group's owner, which the house rule keeps out of committed files.)

## The cause analysis behind c4 (step 1)

- **Three failures:** 37291605525 (10-05), 37395375156 and 37401912454 (10-06). NomadNet drew only `[Notice] Could not load config file, creating default configuration file...` and the Kitty graphics query, then nothing for 90 s while alive. The Reticulum node logged "ready" about 5 s before NomadNet's first line in all three, so slow interface bring-up is refuted. Zero recurrences in the 10 release-verify runs since the retry landed.
- **Where it can block (code at the pins, NomadNet b2f852c9 / RNS e40191b3):**
  - The Kitty query has a 0.3 s non-blocking deadline.
  - The UI then builds every sub-display before the first draw. `InterfaceDisplay.__init__` calls `rns.get_interface_stats()`, an RPC with no timeout on the client (RNS Reticulum.py:1494–1499).
  - The shared instance's `rpc_loop` serves one client at a time, also with no timeout (Reticulum.py:1365 ff.).
  - lxmd, MeshChat and Sideband are RPC clients of the same instance at that moment.
- **What this packet adds so the next occurrence names the cause:** NomadNet's own stack (exists), the shared instance's sockets, and every Reticulum process's stack, taken before anything is stopped. The fix (c4 step 2) follows the measurement.

## Lane proof

| run | sha | workflow | jobs → conclusion |
|---|---|---|---|
| 38811543591 | 1078189e (pre-rebase) | testlab.yml `release_verify=true` | testlab success · release-verify success · slow-build success |
| 38811546360 | 1078189e | ci.yml | all 8 jobs success |
| 38816936648 | 2bbdb442 | testlab.yml `release_verify=true` | testlab success · release-verify success · slow-build CANCELLED by the re-dispatch below (~37 min in) |
| 38816939679 | 2bbdb442 | ci.yml | all 8 jobs success |
| 38821447859 | e96413fc (the tree before v2) | testlab.yml `release_verify=true` | testlab success · release-verify success · slow-build running at packet time (extra evidence under the landing rule of 2026-10-16) |
| 38821450711 | e96413fc | ci.yml | running at packet time |

The NomadNet case passed in release-verify, so the diagnosis path did not run on the lane. It is proven in the unit layer with real processes: a hung Python program on a real pty, and an aborted owned process group whose faulthandler dump is returned.

`git diff 2bbdb442 e96413fc` = testlab/tests/unit/test_release_attribution.py only (+6/−4: the `ready` handshake replaces `time.sleep(0.5)`).

## Simplicity guardrails

- **No new mechanism, class or module.**
  - Two plain functions in the existing lane module: `readiness_failed`, called from 21 sites in place of a literal, and `reticulum_peers_snapshot`, called only by NomadNet's case.
  - One keyword parameter: `on_timeout`, a callable returning text, which replaces the retry's `record`.
  - `pty_readiness` loses its retry loop: one attempt, no `first`/`warnings`/`record`.
- **Lines** (`git show <rev>:<f> | wc -l`, dev 254bbae3 → head):
  - release.py 662 → 734;
  - test_release_verify.py 621 → 625;
  - test_release_attribution.py 775 → 836;
  - release/conftest.py 108 → 113.
- **Dependencies:**
  - `readiness_failed`: {`run_lhpc`, `stack_regression`, `_log_tails`, `re`, `Path`}.
  - `reticulum_peers_snapshot`: {`/proc/net`, `state/owned` file names, `os.killpg`, `time.sleep`, the run logs}.
  - `pty_readiness`: −`warnings`.
  - Nothing in `lhpc/` changes: no `service_*` file, so the module budgets are untouched.

## The 6-point block

1. **Contracts.**
   - `readiness_failed(env, stack, component, what) -> str`. It is the assert message: the marker line first only for `stopped`/`failed`, then `what (LHPC reports <component>: <state>)` and the run-log tails. `state` is `unreadable` when `lhpc status` raised `TimeoutExpired`, returned rc ≠ 0, or did not list the component.
   - `pty_readiness(command, env, expect, *, ready_timeout, hold, stack, logs, on_timeout)` keeps everything else. A timeout appends `on_timeout()`'s text before the program's SIGABRT dump.
   - `reticulum_peers_snapshot(env, drain=3.0) -> str`:
     - reads `/proc/net/{unix,tcp,tcp6}` rows containing `rns/` or ports 0x9234/0x9235;
     - SIGABRTs the process group of each `state/owned/<comp>__<band>__<pid>__<nonce>.json` whose comp is rns, lxmd, meshchat or sideband;
     - returns each such run log from its last `Fatal Python error` line, up to 12000 characters.
   - The release lane's `env` carries `PYTHONFAULTHANDLER=1`.
2. **Invariants + tests (docs/architecture.md safety model, finding 139).**
   - A flake never freezes a stack: a running component, an unreadable status and a pure timeout are all unmarked (the 9 + 3 cases above).
   - The evidence-based markers are unchanged: an exited or traceback-drawing program (`test_a_readiness_failure_with_evidence_of_breaking_is_marked`, `…never_retried`, `…closed_before_it_was_reaped_is_an_exit`), the plugin-manager count (:214 on the head), rnstatus without LoRa (:329), and `meshtastic --info` failing (:400).
   - No product code changes.
3. **Failure classes.**
   - The stubbed `run_lhpc` has the real signature `(env, *args, timeout=…, check=…)`.
   - The snapshot ignores `ProcessLookupError`/`PermissionError` and an unreadable `/proc` table (named in the output).
   - The NomadNet case's stop after a snapshot is `require=False`, so a stop of already-aborted processes cannot replace the case's own failure.
   - KeyboardInterrupt: the `finally` still stops the stack.
4. **Test rules.**
   - Red-before per test, above and in the commit messages.
   - Rule 10: assertions read the marker grammar, the state token, the start counter, the process exit and the dump's first line, never a sentence exactly.
   - No network. Real processes are reaped in `finally`.
5. **Whole directories and grep.**
   - Run: testlab/tests/unit + tests/repo (counts above), and ruff (both).
   - `grep -rn "record=\|readiness-retry\|record_property" testlab` → none left in the lane.
   - `grep -n "stack_regression(" testlab/tests/release/test_release_verify.py` → 5 on the head: :101 the start helper, :214, :316 install, :329, :400.
6. **Adversarial self-review.**
   - (a) Scope beyond the plan's 12 sites: the 9 `alive()` sites also marked on a failed status call, and they go through the same helper. This is a deviation in scope, not in rule.
   - (b) The snapshot aborts the node: the case is failing anyway, and the stop is relaxed only on that path.
   - (c) `/proc/net/unix` is per network namespace; in the lab container it is the lab's own.
   - (d) Removing the retry means the next NomadNet hang reds release-verify unmarked: that is the decision's stated cost. The bot's long re-run (B1) covers only `EXTERNAL-UNAVAILABLE`.
   - (e) The docs paragraph is rewritten in the same commit, so no sentence about the retry remains (`grep -n -i "retr" docs/testlab.md` → one line, :145, "Such a timeout is never retried").

## Deviations

- M2 covers 21 sites, not the plan's 12: the `alive()`-based readiness asserts follow the same rule.
- P2 is M2 + c4 step 1 only. M4 and S2 come next as their own packet (M4's slow-build hook touches test_slow_build.py, which P1 changed).
- CHANGELOG under `## Unreleased`.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log --oneline -1 origin/dev` | `254bbae3` |
| diff | `git diff --stat origin/dev <head>` | 6 files changed, 322 insertions(+), 197 deletions(-) |
| rebase only moved CHANGELOG context | `diff <(git diff eb9673a4 1078189e -- . ':!CHANGELOG.md') <(git diff origin/dev <head> -- . ':!CHANGELOG.md')` | no difference |
| readiness sites | `grep -c "readiness_failed(" testlab/tests/release/test_release_verify.py` | 21 |
| unit test functions | `grep -c "^def test_" test_release_attribution.py` (dev → head) | 40 → 42 |

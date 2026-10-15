# Gate 1: testlab-det-2 (M2 + c4 step 1), v2 (second read, 3 points)

**Request.** Judge the two code commits below against plan v4 §2 P2: M2 (a wait-based readiness failure is marked `STACK-REGRESSION` only when LHPC's status reports the component stopped or failed; any other state, or an unreadable status, is unmarked) and decision c4 step 1 (the 0.12.5 NomadNet start retry removed; a readiness timeout is diagnosed before the abort: the shared instance's sockets and every Reticulum process's stack). Judge correctness, whether each red-before fails through the behaviour (or is labelled as an API absence), whether the attribution can still freeze a stack on a flake, the scope deviation (21 sites, not the plan's 12), the checker's two WARNs as answered, and the report's claims against the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)

### 2ea5b5b0 testlab: a readiness window marks the stack only when the component is gone

```text
testlab: a readiness window marks the stack only when the component is gone

Twenty-one asserts of the release lane (test_release_verify.py) put `STACK-REGRESSION
stack=<s> phase=readiness` first on any failed wait: twelve after `wait_tcp`/`wait_http`/
`wait_for` returned False, which they do only when their deadline passed (release.py:350-389),
and nine after `alive()` read anything but "running", including a status call that failed. A
slow runner, or a status that could not be read, could therefore freeze a moved stack: the
finding-139 class the interactive check (`pty_readiness`) was already fixed for.

`readiness_failed(env, stack, component, what)` builds those messages. It reads LHPC's own
status once, only when the check failed, and marks the stack only when the component is
reported `stopped` or `failed` (its process is gone). Any other state, and a status call that
failed, timed out or did not list the component, is UNMARKED. The message names the state it
read and ends with the component's run-log tail. The windows, the checks and what passes are
unchanged; only the attribution of a failure changes. Three evidence-based markers stay as they
were: the plugin-manager count (:215), rnstatus answering without a LoRa interface (:330),
`lhpc meshtastic --info` failing (:389).

Red-before: the new cases in testlab/tests/unit/test_release_attribution.py (9) fail on the
parent eb9673a4 with "AttributeError: module 'lhpc_testlab.release' has no attribute
'readiness_failed'" (the helper is new; the parent's sites mark unconditionally, e.g.
test_release_verify.py:113 `f"{stack_regression('kiss', 'readiness')}\n"`). Green after:
testlab/tests/unit 147 passed, 7 skipped; tests/repo 524 passed, 1 skipped; ruff clean.

```

### aa5410dd testlab: an interactive start that times out is diagnosed, never retried

```text
testlab: an interactive start that times out is diagnosed, never retried

The 0.12.5 retry (finding 146) started an interactive program a second time after a pure
timeout and passed on the second draw: a test body re-run because it failed, which hid the
cause of NomadNet's first-start hang (three release-verify failures on 2026-10-05/06: nothing
drawn after the Kitty graphics query, the Reticulum node ready about 5 s before; no occurrence
in the ten release-verify runs since). The maintainer's decision c4: remove the retry and fix
the cause; step one is to make the cause visible.

`pty_readiness` runs once. A timeout first calls `on_timeout()` while everything still hangs,
then SIGABRTs the program (faulthandler dump, as before) and fails UNMARKED unless the program
showed it broke (unchanged). `reticulum_peers_snapshot` is NomadNet's `on_timeout`: the shared
instance's sockets from /proc/net/{unix,tcp,tcp6}, then SIGABRT to the process group of every
Reticulum process LHPC owns (rns, lxmd, meshchat, sideband; from state/owned) and their dumps
from their run logs. A record is signalled only when its live process is still the recorded one
(start time, pgid, sid, and sid == pid: the rule of `Lifecycle.verify_owned`); a
stale record whose pid was reused is skipped and named. The release lane's `env` sets PYTHONFAULTHANDLER=1 so those processes dump.
Its stop after a snapshot does not require them (they were aborted on purpose). The retry's
`record=`/`readiness-retry` plumbing and its four tests go; the callers lose `record_property`.

Working hypothesis the dump will confirm or refute: NomadNet blocks in
`rns.get_interface_stats()` (built into its interface display before the first draw), an RPC
with no timeout, while the shared instance's single-threaded RPC loop serves another client.

Red-before (testlab/tests/unit/test_release_attribution.py, against the parent eb9673a4):
test_a_start_that_times_out_is_not_retried_and_carries_its_stack fails "DID NOT RAISE
AssertionError" (the parent retries and the second start draws); the diagnosis and snapshot
cases fail on the missing API ("unexpected keyword argument 'on_timeout'"; "cannot import name
'reticulum_peers_snapshot'"). Green after: testlab/tests/unit + tests/repo 668 passed, 8 skipped;
ruff clean.

Red-before (the stale-record rule): test_the_reticulum_snapshot_aborts_only_recorded_reticulum_processes
run against this commit's first version (e96e2bb5): `assert -6 is None`, the stale record's
process was SIGABRTed. Green after, 3/3.

```

## The report

> # Code report: testlab-det-2 (packet P2 = plan item M2 + decision c4, step 1)
>
> Branch `cons/testlab-det-2-r1`, rebased on `origin/dev` = `f874040d` (P1, P3a, the house rules and field-fixes-p1 landed). Plan: `plans/PLAN-testlab-determinism.md` v3 (gate 1 GREEN, round 4) with the maintainer's decisions (DECISIONS-testlab-determinism.md): item M2, and decision c4 (remove the NomadNet retry and find the cause; step one is instrumentation, because the saved logs do not show the cause). M4 and S2 follow in their own packet: M4's hook in slow-build's `_judged` touches a file P1 changed.
>
> ## Commits
>
> | commit | subject | files | tests | red-before |
> |---|---|---|---|---|
> | 2ea5b5b0 | testlab: a readiness window marks the stack only when the component is gone | testlab/lhpc_testlab/release.py (`readiness_failed`, the `stack_regression` docstring example), testlab/tests/release/test_release_verify.py (21 sites), testlab/tests/unit/test_release_attribution.py, docs/testlab.md, CHANGELOG.md | 3 cases, 9 parametrizations (unit layer; rule 10: whether the message contains a line of the lane's own marker grammar, and the state token it read): `running`/`degraded`/`unknown` → unmarked; `stopped`/`failed` → marked FIRST; status rc 1, rc 2 with "stopped" text, component not listed, `TimeoutExpired` → unmarked, state `unreadable` | yes, against eb9673a4: all 9 fail `AttributeError: module 'lhpc_testlab.release' has no attribute 'readiness_failed'`. This red is the new helper's absence; the parent's behaviour is that every one of the 21 sites marks unconditionally (e.g. test_release_verify.py:113 on eb9673a4: `f"{stack_regression('kiss', 'readiness')}\n"` in the message of `assert wait_tcp(KISS_TCP, 120)`) |
> | aa5410dd | testlab: an interactive start that times out is diagnosed, never retried | release.py (`pty_readiness` runs once, `on_timeout`; `reticulum_peers_snapshot`; `import warnings` removed), testlab/tests/release/conftest.py (`PYTHONFAULTHANDLER=1`), test_release_verify.py (the NomadNet case; `record_property` and `record=` removed from 4 callers), test_release_attribution.py, docs/testlab.md, CHANGELOG.md | `test_a_start_that_times_out_is_not_retried_and_carries_its_stack`, `test_the_timeout_diagnosis_runs_while_the_program_still_hangs`, `test_the_reticulum_snapshot_aborts_only_reticulum_processes_and_returns_their_stacks` (unit layer; real processes and a real pty); the retry's 4 tests removed (1 replaced, 3 obsolete) | yes, against eb9673a4. The not-retried case fails `DID NOT RAISE AssertionError` (the parent retries and the second start draws: the behaviour itself). The diagnosis and snapshot cases fail on the missing API (`unexpected keyword argument 'on_timeout'`; `cannot import name 'reticulum_peers_snapshot'`) |
>
> Commands: red-before = the committed test module copied beside the parent's lane in a worktree of eb9673a4, `PYTHONPATH=<parent>/testlab:<parent> python -m pytest -q -p no:cacheprovider <copy> -k <case>` (the copy removed afterwards; `git status --short` empty). Green on 1078189e: `PYTHONPATH=<worktree>/testlab:<worktree> python -m pytest -q -p no:cacheprovider -n 8 testlab/tests/unit tests/repo` → `668 passed, 8 skipped`; `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`. Green on the head aa5410dd: the same command → `721 passed, 8 skipped` (dev's newer tests included); ruff clean; the snapshot case 3/3 in a row.
>
> ## v2: the second read's 3 points (all accepted)
>
> 1. **Lab safety:** `reticulum_peers_snapshot` SIGABRTed the process group named by an owned-record FILENAME, unchecked, one record per component. A stale record after pid reuse could abort an unrelated group. Now each record's JSON is read, and its process is signalled only when `proc_identity(pid)` (lhpc/core/procident.py) has the recorded starttime, pgid and sid and sid == pid, the rule of `Lifecycle.verify_owned`. A skipped record is named in the output; records are not merged per component. Test `test_the_reticulum_snapshot_aborts_only_recorded_reticulum_processes`: a stale `lxmd` record (its starttime off by one) is left alone, `rns` is aborted, another stack's process is untouched. Red-before against the first version of the c4 commit (e96413fc): `assert -6 is None`, the stale record's process was SIGABRTed. Green 3/3.
> 2. The 6-point block's stale sentence about `time.sleep(0.5)` is deleted (the handshake replaced it in v1's amend).
> 3. Line numbers are re-quoted from the head (aa5410dd), as below.
>
> The lane runs above ran on 2bbdb442 / e96413fc. The head differs from e96413fc by the snapshot's identity check, its test, and the rebase onto f874040d (CHANGELOG context). Under the landing rule of 2026-10-16 the batch run on dev is the lane proof.
>
> ## HOUSE RULES KEPT (dev's tools/check_houserules.py, on origin/dev..aa5410dd)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +2 bullets (limit 2, each ≤ 3 lines; longest 2), top section 16 lines   OK
> docs         1 files, +9 lines; fact "readiness_failed" -> docs/testlab.md; fact "reticulum_peers_snapshot" -> docs/testlab.md   OK
> tests        10 new tests; layers testlab/tests/release=testlab testlab/tests/unit=testlab; red-before 2/2   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 1 startswith 1 network 0   WARN
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 1)
> ```
>
> The patterns WARN (two tokens), answered:
> - **patterns `sleep 1`:** the token is inside the child command STRING `"import time; print('ready', flush=True); time.sleep(60)"`, the process being aborted, not a wait in the test. The test's own timing wait was replaced by a `ready` handshake (that is the e96413fc amend).
> - **patterns `startswith 1`:** `assert msg.startswith("STACK-REGRESSION stack=kiss phase=readiness\n")` compares the WHOLE first line, and "the marker is the first line" is the grammar's contract (rule 2: exact where exactness is the contract).
> - (attribution: `words 0`. The snapshot's identity check is worded with `pgid`/`sid`, not the POSIX word for a process group's owner, which the house rule keeps out of committed files.)
>
> ## The cause analysis behind c4 (step 1)
>
> - **Three failures:** 37291605525 (10-05), 37395375156 and 37401912454 (10-06). NomadNet drew only `[Notice] Could not load config file, creating default configuration file...` and the Kitty graphics query, then nothing for 90 s while alive. The Reticulum node logged "ready" about 5 s before NomadNet's first line in all three, so slow interface bring-up is refuted. Zero recurrences in the 10 release-verify runs since the retry landed.
> - **Where it can block (code at the pins, NomadNet b2f852c9 / RNS e40191b3):**
>   - The Kitty query has a 0.3 s non-blocking deadline.
>   - The UI then builds every sub-display before the first draw. `InterfaceDisplay.__init__` calls `rns.get_interface_stats()`, an RPC with no timeout on the client (RNS Reticulum.py:1494–1499).
>   - The shared instance's `rpc_loop` serves one client at a time, also with no timeout (Reticulum.py:1365 ff.).
>   - lxmd, MeshChat and Sideband are RPC clients of the same instance at that moment.
> - **What this packet adds so the next occurrence names the cause:** NomadNet's own stack (exists), the shared instance's sockets, and every Reticulum process's stack, taken before anything is stopped. The fix (c4 step 2) follows the measurement.
>
> ## Lane proof
>
> | run | sha | workflow | jobs → conclusion |
> |---|---|---|---|
> | 38811543591 | 1078189e (pre-rebase) | testlab.yml `release_verify=true` | testlab success · release-verify success · slow-build success |
> | 38811546360 | 1078189e | ci.yml | all 8 jobs success |
> | 38816936648 | 2bbdb442 | testlab.yml `release_verify=true` | testlab success · release-verify success · slow-build CANCELLED by the re-dispatch below (~37 min in) |
> | 38816939679 | 2bbdb442 | ci.yml | all 8 jobs success |
> | 38821447859 | e96413fc (the tree before v2) | testlab.yml `release_verify=true` | testlab success · release-verify success · slow-build running at packet time (extra evidence under the landing rule of 2026-10-16) |
> | 38821450711 | e96413fc | ci.yml | running at packet time |
>
> The NomadNet case passed in release-verify, so the diagnosis path did not run on the lane. It is proven in the unit layer with real processes: a hung Python program on a real pty, and an aborted owned process group whose faulthandler dump is returned.
>
> `git diff 2bbdb442 e96413fc` = testlab/tests/unit/test_release_attribution.py only (+6/−4: the `ready` handshake replaces `time.sleep(0.5)`).
>
> ## Simplicity guardrails
>
> - **No new mechanism, class or module.**
>   - Two plain functions in the existing lane module: `readiness_failed`, called from 21 sites in place of a literal, and `reticulum_peers_snapshot`, called only by NomadNet's case.
>   - One keyword parameter: `on_timeout`, a callable returning text, which replaces the retry's `record`.
>   - `pty_readiness` loses its retry loop: one attempt, no `first`/`warnings`/`record`.
> - **Lines** (`git show <rev>:<f> | wc -l`, dev 254bbae3 → head):
>   - release.py 662 → 734;
>   - test_release_verify.py 621 → 625;
>   - test_release_attribution.py 775 → 836;
>   - release/conftest.py 108 → 113.
> - **Dependencies:**
>   - `readiness_failed`: {`run_lhpc`, `stack_regression`, `_log_tails`, `re`, `Path`}.
>   - `reticulum_peers_snapshot`: {`/proc/net`, `state/owned` file names, `os.killpg`, `time.sleep`, the run logs}.
>   - `pty_readiness`: −`warnings`.
>   - Nothing in `lhpc/` changes: no `service_*` file, so the module budgets are untouched.
>
> ## The 6-point block
>
> 1. **Contracts.**
>    - `readiness_failed(env, stack, component, what) -> str`. It is the assert message: the marker line first only for `stopped`/`failed`, then `what (LHPC reports <component>: <state>)` and the run-log tails. `state` is `unreadable` when `lhpc status` raised `TimeoutExpired`, returned rc ≠ 0, or did not list the component.
>    - `pty_readiness(command, env, expect, *, ready_timeout, hold, stack, logs, on_timeout)` keeps everything else. A timeout appends `on_timeout()`'s text before the program's SIGABRT dump.
>    - `reticulum_peers_snapshot(env, drain=3.0) -> str`:
>      - reads `/proc/net/{unix,tcp,tcp6}` rows containing `rns/` or ports 0x9234/0x9235;
>      - SIGABRTs the process group of each `state/owned/<comp>__<band>__<pid>__<nonce>.json` whose comp is rns, lxmd, meshchat or sideband;
>      - returns each such run log from its last `Fatal Python error` line, up to 12000 characters.
>    - The release lane's `env` carries `PYTHONFAULTHANDLER=1`.
> 2. **Invariants + tests (docs/architecture.md safety model, finding 139).**
>    - A flake never freezes a stack: a running component, an unreadable status and a pure timeout are all unmarked (the 9 + 3 cases above).
>    - The evidence-based markers are unchanged: an exited or traceback-drawing program (`test_a_readiness_failure_with_evidence_of_breaking_is_marked`, `…never_retried`, `…closed_before_it_was_reaped_is_an_exit`), the plugin-manager count (:214 on the head), rnstatus without LoRa (:329), and `meshtastic --info` failing (:400).
>    - No product code changes.
> 3. **Failure classes.**
>    - The stubbed `run_lhpc` has the real signature `(env, *args, timeout=…, check=…)`.
>    - The snapshot ignores `ProcessLookupError`/`PermissionError` and an unreadable `/proc` table (named in the output).
>    - The NomadNet case's stop after a snapshot is `require=False`, so a stop of already-aborted processes cannot replace the case's own failure.
>    - KeyboardInterrupt: the `finally` still stops the stack.
> 4. **Test rules.**
>    - Red-before per test, above and in the commit messages.
>    - Rule 10: assertions read the marker grammar, the state token, the start counter, the process exit and the dump's first line, never a sentence exactly.
>    - No network. Real processes are reaped in `finally`.
> 5. **Whole directories and grep.**
>    - Run: testlab/tests/unit + tests/repo (counts above), and ruff (both).
>    - `grep -rn "record=\|readiness-retry\|record_property" testlab` → none left in the lane.
>    - `grep -n "stack_regression(" testlab/tests/release/test_release_verify.py` → 5 on the head: :101 the start helper, :214, :316 install, :329, :400.
> 6. **Adversarial self-review.**
>    - (a) Scope beyond the plan's 12 sites: the 9 `alive()` sites also marked on a failed status call, and they go through the same helper. This is a deviation in scope, not in rule.
>    - (b) The snapshot aborts the node: the case is failing anyway, and the stop is relaxed only on that path.
>    - (c) `/proc/net/unix` is per network namespace; in the lab container it is the lab's own.
>    - (d) Removing the retry means the next NomadNet hang reds release-verify unmarked: that is the decision's stated cost. The bot's long re-run (B1) covers only `EXTERNAL-UNAVAILABLE`.
>    - (e) The docs paragraph is rewritten in the same commit, so no sentence about the retry remains (`grep -n -i "retr" docs/testlab.md` → one line, :145, "Such a timeout is never retried").
>
> ## Deviations
>
> - M2 covers 21 sites, not the plan's 12: the `alive()`-based readiness asserts follow the same rule.
> - P2 is M2 + c4 step 1 only. M4 and S2 come next as their own packet (M4's slow-build hook touches test_slow_build.py, which P1 changed).
> - CHANGELOG under `## Unreleased`.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log --oneline -1 origin/dev` | `254bbae3` |
> | diff | `git diff --stat origin/dev <head>` | 6 files changed, 322 insertions(+), 197 deletions(-) |
> | rebase only moved CHANGELOG context | `diff <(git diff eb9673a4 1078189e -- . ':!CHANGELOG.md') <(git diff origin/dev <head> -- . ':!CHANGELOG.md')` | no difference |
> | readiness sites | `grep -c "readiness_failed(" testlab/tests/release/test_release_verify.py` | 21 |
> | unit test functions | `grep -c "^def test_" test_release_attribution.py` (dev → head) | 40 → 42 |

## The plan (v4, for reference)

````markdown
# PLAN testlab-determinism (v4)

Base: `origin/dev` = `254bbae3` (P1 landed). Scope: `testlab/`, `.github/workflows/`, the Graywolf fetch script (P5), one product function (S1, landed). The bot's re-runs are NOT in this plan: they belong to the bot-stability item, on the contract `CONTRACT-external-unavailable.md`.

Rules: tests/README.md rules 1–13; no waiver, no lane exclusion, no weakened assertion; red-before for every fix ("new invariant" with a negative test otherwise); a flake never freezes a stack.

## 0. Changes

**v4** (the maintainer's DECISIONS a–c4, binding, and the W1b verdict, Item A, 6 findings, all accepted):

| finding | change |
|---|---|
| W1b-1 transfer-time separation missing (decision a) | new P5 (§2): every timed transfer is acquired before the lane and recorded as its own number; the budget is judged on the local work only; a test proves a slower transfer leaves the verdict unchanged |
| W1b-2 retry policy against c1 | §1: one backoff (at once, then 1, 2, 5, 10 min, then 10 min steps to a 30-min total) for every TRANSFER, in the acquisition steps and around the lane's unavoidable class-B transfers; never around an assertion or a test body. Bot re-runs: not this plan (B1 removed) |
| W1b-3 c4 missing from P2 | §2 P2 = M2 + c4 step 1 (retry removed, instrumentation); c4 step 2 (the cause fix with a reproducer) is a later packet, triggered by the first recorded hang |
| W1b-4 same-step attribution hole | M4: EXTERNAL only when the decisive step's TERMINAL error line is a transport line; a recovered transport line earlier in the same step does not count; twin test |
| W1b-5 S1 proof surface | P4 adds the test that the summary and the kept status carry `curl rc <n>` and curl's message as one line of ≤ 200 chars; red on the parent |
| W1b-6 CHANGELOG destination | `## Unreleased`, one or two lines per packet |

v3 and v2 change tables: see the frozen copies PLAN-testlab-determinism-v2.md / -v3.md.

## 1. The rule (one place: docs/testlab.md, "Running the verification lanes")

> Every external access by a testlab job is in exactly one class.
> - **(A) Acquisition, before the lane.** The container image, the lab's Python packages, Chromium, the upstream git sources (mirrors), and in slow-build every artifact a timed operation transfers. A workflow step of its own does it, with the transfer backoff, and names what failed.
> - **(B) Under test, inside the lane.** The published binaries, the pinned release downloads and the build steps' packages that a stack installs in testlab and release-verify. They run as the product runs them, including the product's own retries. Their transfer is wrapped in the transfer backoff by the lane helper that starts it; the lane never repeats an assertion or a test body. A transfer still failing after the backoff is reported as the first line `EXTERNAL-UNAVAILABLE what=<token>` (the contract), never `STACK-REGRESSION`.
> - **(C) Everything else is forbidden.**
> - **Attribution.** `STACK-REGRESSION` needs typed evidence of the component itself: a wait-based readiness check → its process is gone; an interactive program → it exited or drew a traceback/exception line; a build → its decisive step is one the recipe declares `attributable`. Anything else, an unrecognised failure included, is UNMARKED.
> - **Transfer backoff:** retry at once, then after 1, 2, 5 and 10 min, then every 10 min, until 30 min have passed; each wait and the reason logged.

## 2. Packets and items

**P1 (LANDED, 254bbae3):** M1 slow-build fetches the pinned Graywolf; S1 the update check keeps curl's reason.

**P2 (in review, cons/testlab-det-2-r1):**
- **M2:** 21 readiness asserts go through `readiness_failed`, marked only on `stopped`/`failed`.
- **c4 step 1:**
  - the 0.12.5 retry is removed;
  - a timeout runs `on_timeout()` before the abort;
  - NomadNet's case records the shared instance's sockets and every Reticulum process's stack (`reticulum_peers_snapshot`, `PYTHONFAULTHANDLER=1`).

**P3a (in review, cons/testlab-det-3a-r1):**
- CI's test job runs `-n auto --dist loadfile`;
- only `cons/*` cancels superseded runs;
- `slow-build needs: testlab`.

**P3 M3, the mirror:**
- an acquisition step in testlab and release-verify clones the 14 lane remotes as mirrors with the transfer backoff; `GIT_CONFIG_GLOBAL` → `insteadOf`;
- a `--network none` step proves the list complete;
- `docker build` gets the backoff; Xvfb waits for its socket.
- Red-before: the gitconfig text test (new script).
- Lanes: testlab, release-verify.

**P4: M4 + S2 + the transfer wrapper + the S1 test.**
- **M4, the decisive-step rule** (`external_unavailable(output)`, release.py):
  - install: the failing action's own `[failed]`/`ERR` line(s) only;
  - build: the step log named by `[failed] build <comp> (rc N, log <path>)` (build_regression.py:29):
    - an `attributable` step → STACK-REGRESSION, unchanged;
    - otherwise the step's TERMINAL error line is decisive: the last line matching `^(ERROR|error|fatal):|^curl: \(\d+\)|download failed`. EXTERNAL only if that line is a transport line (`download failed`, `Could not resolve host`, `Failed to connect`, `ConnectionError`, `ReadTimeoutError`, `HTTP error 5\d\d`, `curl: \((6|7|28|35|56)\)`); an earlier, recovered transport line in the same step does not count;
  - slow-build `_judged`: the timed command's terminal error line, by the same rule.
- **M4 tests:**
  - install `download failed` ⇒ EXTERNAL (red on the parent);
  - attributable step + transport text elsewhere ⇒ STACK-REGRESSION (guard);
  - a non-attributable pip step whose terminal line is `ReadTimeoutError` ⇒ EXTERNAL (red);
  - SAME-STEP TWIN: a `ConnectionError` retried earlier in the step, the terminal line `ResolutionImpossible` ⇒ UNMARKED (guard);
  - unknown ⇒ neither line.
- **Transfer wrapper:** the lane helpers that run a class-B transfer (`install_build`'s install, reset's binary install, slow-build's fetch steps) repeat ONLY an attempt that ended EXTERNAL, with the backoff. Any other failure is reported at once. Tests: a fake install failing EXTERNAL twice then passing ⇒ passes, both waits logged (red on the parent); a fake failing non-externally once ⇒ fails at once (guard).
- **S2:** reset fails on a failed binary download, with the EXTERNAL line.
- **S1 test:** `tests/stacks/test_graywolf_upstream.py` asserts that the summary and the kept `error` contain `curl rc 22` and curl's message, flattened to one line of ≤ 200 chars. Red on the parent (the summary says only "network/API error").
- Lanes: testlab, release-verify, slow-build.

**P5 (decision a), transfer separation in slow-build.**
- The timed operations that transfer (L7a clone, L6 deb fetch, L5 CLI venv, L4 self-update pip sync) get their artifacts in an acquisition step of the slow-build job:
  - git mirrors (P3's script);
  - the pinned Graywolf `.deb` + `checksums.txt`;
  - `pip download` wheelhouses for the CLI venv and lhpc's own requirements, inside the lab image.
- Each transfer's wall time is written to the evidence as `op = "transfer"` with the operation it serves; it is recorded, never judged.
- The lane then times the operation against the local copies:
  - clone through `insteadOf`;
  - the fetch with `GRAYWOLF_BASE_URL=file://…` (one line in graywolf-fetch.sh: `BASE_URL="${GRAYWOLF_BASE_URL:-https://github.com/…}"`; curl reads `file://`);
  - pip with `PIP_FIND_LINKS` + `PIP_NO_INDEX=1`, which the product's spawned steps inherit.
- The budget (`slow_target.compare`) judges only those local timings.
- Tests:
  - (a) a `transfer` entry is never judged, however large: new invariant, negative test;
  - (b) the fetch script uses `GRAYWOLF_BASE_URL` when set (red on the parent: it ignores it);
  - (c) slowing the acquisition (a stub transfer + 60 s) leaves the budget verdict unchanged.
- Lane: slow-build.

**c4 step 2 (after the first recorded hang):** the fix follows the measured cause. If it is the RPC hold, the smallest real fix is a socket timeout on the shared instance's RPC listener in our `loraham-rns-node`. The reproducer: a peer that holds the RPC connection while NomadNet starts, red before the fix.

## 3. The 27 inventory rows

The v3 table stands, with these changes:
- rows 1, 2 and 4: acquisition with the transfer backoff;
- rows 3, 5 and 6: class B with the transfer wrapper;
- row 9: P5;
- row 18: the retry is removed (P2).

## 4. Landing order

P2 → P3a → P3 → P4 → P5. Each: branch `cons/testlab-det-<n>-r<round>` on the then-current dev; checker block; gate-1 packet ≤ 90 KB; second reader; auditor; land on GREEN + lanes on the exact sha. Then one full testlab + CI run on dev and the injections: `--network none` (P3), binaries unreachable (P4: an EXTERNAL red, no marker), a slow transfer (P5: verdict unchanged). CHANGELOG under `## Unreleased`.

## 5. Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log --oneline -1 origin/dev` | `254bbae3` |
| typed build line | `grep -n TYPED_BUILD_FAILURE lhpc/core/build_regression.py` | :29 |
| the fetch URL | `grep -n BASE_URL lhpc/data/scripts/graywolf-fetch.sh` | :29, one hard-coded https URL |
| product timing lines | `grep -n "\[git\] \|\[venv\]\|pip sync" lhpc/core/install.py lhpc/core/service_binary_ops.py lhpc/core/service_selfupdate.py` | install.py:2582, service_binary_ops.py:767, service_selfupdate.py:1180 |
| history | `gh run list --workflow testlab.yml --limit 100` + per-job tally | 21 failed jobs, 5 flakes (v3 §6 table) |
````

## The full diff (`git diff origin/dev aa5410dd3bec2e1a6201c75a6202a889a7a34df3`)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ff949d4e..4b54a493 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -12,6 +12,10 @@ Fixed
 - Graywolf's update check now says why GitHub could not be reached: curl's exit code and the end of its message,
   in the result and in the status it keeps.
 - CI runs the test suite in parallel processes, and the slow-build check starts only after the testlab job passed.
+- The release check no longer blames a stack when one of its services was still starting after its wait: a
+  readiness timeout names the stack only when LHPC reports the component stopped or failed.
+- The release check no longer starts an interactive program a second time after a timeout: a hung start fails
+  (unmarked), and NomadNet's records the stacks of every Reticulum process and the shared instance's sockets.
 
 ## 0.12.9
 
diff --git a/docs/testlab.md b/docs/testlab.md
index 3e6fd20c..c724258f 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -136,12 +136,17 @@ subprocesses import `lhpc` from the editable install, i.e. whichever checkout wa
   - Only a failure at a genuine per-stack step carries the JUnit marker
     `STACK-REGRESSION stack=<id> phase=<install|build|start|readiness>` (grammar and unmarked
     sites: `lhpc_testlab.release.stack_regression`); a build only at a step the manifest marks
-    `attributable = true` (a compile, a patch, a check over fetched code), never at a fetch. An
+    `attributable = true` (a compile, a patch, a check over fetched code), never at a fetch. A
+    service whose port or page did not answer within its window is marked only when LHPC's status
+    reports the component stopped or failed; one still running, or a status that cannot be read,
+    is unmarked and carries the component's run-log tail (`readiness_failed`). An
     interactive program's readiness is marked only when it exited or drew a traceback or an
     exception line; a timeout without that evidence is unmarked and carries the tails of its logs.
-    Such a timeout also captures the program's stack (SIGABRT with Python's faulthandler) and gets
-    ONE retry, since a flaky third-party UI start is not the stack's regression: the first start's
-    stack and log tails are kept as a warning in the JUnit, and only a second start that draws passes.
+    Such a timeout is never retried (a test body is not re-run because it failed): it captures the
+    program's stack (SIGABRT with Python's faulthandler) and fails. NomadNet's case also records,
+    before anything is stopped, the shared instance's sockets and every Reticulum process's stack
+    (`reticulum_peers_snapshot`; the release lane runs with `PYTHONFAULTHANDLER=1`), because its
+    first start has hung on a loaded runner for a cause not yet measured.
     A regression that only hangs is therefore unmarked: the release stops at its proof, nothing is
     released and nothing frozen (a deliberate trade). An automation may freeze a stack only on a
     marked failure.
diff --git a/testlab/lhpc_testlab/release.py b/testlab/lhpc_testlab/release.py
index 7ce3770c..9a423b9f 100644
--- a/testlab/lhpc_testlab/release.py
+++ b/testlab/lhpc_testlab/release.py
@@ -8,6 +8,7 @@ from __future__ import annotations
 
 import contextlib
 import errno
+import json
 import os
 import pathlib
 import re
@@ -17,7 +18,6 @@ import subprocess
 import time
 import urllib.error
 import urllib.request
-import warnings
 from pathlib import Path
 
 from lhpc.core import build_regression as br
@@ -45,9 +45,12 @@ def stack_regression(stack: str, phase: str) -> str:
 
     It goes FIRST in a pytest assertion message, on a line of its own::
 
-        assert wait_tcp(KISS_TCP, 120), (
-            f"{stack_regression('kiss', 'readiness')}\n"
-            "kiss is not serving KISS/TCP on 8001")
+        assert rnstatus_lists_lora, (
+            f"{stack_regression('reticulum', 'readiness')}\n"
+            "rnstatus lists no LoRa interface")
+
+    A wait-based readiness check (a port or page that never answered) does not write it
+    itself: `readiness_failed` adds it only when LHPC reports the component gone.
 
     **Grammar — a separate program parses this, so do not reformat it:**
 
@@ -415,7 +418,7 @@ def _log_tails(logs, size: int = 1500) -> str:
 
 def pty_readiness(command: str, env: dict, expect: str, *, ready_timeout: float = 60.0,
                   hold: float = 5.0, stack: str | None = None,
-                  logs=(), record=None) -> bytes:
+                  logs=(), on_timeout=None) -> bytes:
     # `logs` tails go in unfiltered: name lab-identity logs only
     """Run an INTERACTIVE component the way an operator does — on a real terminal — and prove
     it: it must draw its OWN screen, still be running afterwards, and exit CLEANLY when asked.
@@ -438,24 +441,19 @@ def pty_readiness(command: str, env: dict, expect: str, *, ready_timeout: float
 
     `stack` names the stack this component belongs to, and marks a READINESS failure with
     `stack_regression` when the program shows it is broken: it exited on its own, or drew a
-    traceback or an exception line (`_DREW_AN_ERROR`). A timeout without that evidence — still running, drew nothing or
-    not what was expected — stays unmarked: see `stack_regression`. `stack` is validated before
-    the program is started, so a typo fails immediately instead of an hour in. `logs` are files
-    whose tails every readiness failure appends (the program's own log, its daemon's). The
-    SIGTERM escalation below stays unmarked on purpose.
-
-    A readiness TIMEOUT captures where the program is stuck before anything else happens: it
-    runs with `PYTHONFAULTHANDLER=1`, and its process group is sent SIGABRT, whose thread dump is
-    drained from the terminal for ~3 s and appended. Whether it is broken, and whom that blames,
-    is still decided from what it drew BEFORE the abort.
-
-    ONE retry: a first start that times out with no evidence of breaking (still running,
-    nothing broken drawn) is stopped and started once more, the same command. Only a second
-    start that draws passes. Every retry, with the first start's dump and log tails, is
-    reported when it starts, as a warning and through `record(name, value)` — pytest's
-    `record_property` — into the JUnit, whether the second start then passes or fails. A start that hangs twice still fails; one that exits or draws an error is
-    never retried. A retried start's failure is never marked: it ran after an abort, against
-    whatever the first start left behind. The program runs without core dumps.
+    traceback or an exception line (`_DREW_AN_ERROR`). A timeout without that evidence — still
+    running, drew nothing or not what was expected — stays unmarked: see `stack_regression`.
+    `stack` is validated before the program is started, so a typo fails immediately instead of
+    an hour in. `logs` are files whose tails every readiness failure appends (the program's own
+    log, its daemon's). The SIGTERM escalation below stays unmarked on purpose.
+
+    A readiness TIMEOUT is diagnosed, never retried: a test body is not re-run because it
+    failed. First `on_timeout()`, when given, runs while everything is still as it hung, and
+    its text is appended (what the program waits on may be another process). Then the program,
+    which runs with `PYTHONFAULTHANDLER=1`, gets SIGABRT on its process group, and the thread
+    dump it prints is drained from the terminal for ~3 s and appended. Whether it is broken,
+    and whom that blames, is decided from what it drew BEFORE either. The program runs
+    without core dumps.
     """
     mark = f"{stack_regression(stack, 'readiness')}\n" if stack else ""
     env = dict(env)
@@ -464,49 +462,97 @@ def pty_readiness(command: str, env: dict, expect: str, *, ready_timeout: float
         env["TERM"] = "xterm-256color"
     env["LINES"], env["COLUMNS"] = "40", "120"
     env["PYTHONFAULTHANDLER"] = "1"            # SIGABRT then dumps every thread's stack
-    first = ""
-    for attempt in (1, 2):
-        proc, master = _pty_launch(command, env)
-        if attempt == 2:
-            # Reported once the retry is really running — after the first start was stopped
-            # and the second launched — and before it is judged, so a retry that fails is too.
-            note = f"{command!r} was started a second time{first}"
-            warnings.warn(note, stacklevel=2)
-            if record:
-                record("readiness-retry", note)
+    proc, master = _pty_launch(command, env)
+    try:
+        drawn, ended = _pty_read(master, proc, expect, ready_timeout)
+        if ended:
+            with contextlib.suppress(subprocess.TimeoutExpired):
+                proc.wait(timeout=2)                # for its exit status in the message
+        tail = drawn[-600:].decode("utf-8", "replace")
+        # ENDED, not `poll()`: the terminal closes before the exit can be reaped, and a
+        # program read as still running there would be judged as a pure timeout.
+        broken = ended or proc.poll() is not None or bool(_DREW_AN_ERROR.search(drawn))
+        matched = _matches(drawn, expect)
+        dump = ""
+        if not (matched or broken):
+            dump = (on_timeout() if on_timeout else "") + _pty_abort(proc, master)
+        why = mark if broken else ""
+        assert drawn, (f"{why}{command!r} drew nothing on its terminal within "
+                       f"{ready_timeout} s{dump}{_log_tails(logs)}")
+        assert matched, (
+            f"{why}{command!r} did not draw anything matching {expect!r} within "
+            f"{ready_timeout} s. It drew: {tail!r}{dump}{_log_tails(logs)}")
+        time.sleep(hold)
+        assert proc.poll() is None, (
+            f"{mark}{command!r} exited on its own (rc={proc.returncode}) — not a running "
+            f"app. It drew: {tail!r}{_log_tails(logs)}")
+    finally:
+        killed = _pty_stop(proc, master)
+    assert not killed, (f"{command!r} ignored SIGTERM and had to be killed — it does not "
+                        f"exit cleanly")
+    return drawn
+
+
+# The Reticulum processes LHPC starts that talk to the shared instance's RPC.
+_RNS_PEERS = ("rns", "lxmd", "meshchat", "sideband")
+
+
+def reticulum_peers_snapshot(env: dict, drain: float = 3.0) -> str:
+    """A Reticulum client's readiness timeout, diagnosed while everything still hangs: what is
+    connected to the shared instance (`/proc/net/unix`, `/proc/net/tcp*`: the RPC and
+    local-interface sockets), then where every Reticulum process LHPC started stands — each
+    process group gets SIGABRT, and with `PYTHONFAULTHANDLER=1` (set for the release lane)
+    every thread's stack lands in its own run log, whose tail is returned. A record is acted on
+    only when its live process is still the one recorded (start time, pgid, sid).
+
+    Diagnosis only: it decides nothing and marks nothing. The processes it aborts are gone
+    afterwards, so the caller's stop must not require them."""
+    root = Path(env["LHPC_RUNTIME_ROOT"])
+    out = ["\n--- sockets of the Reticulum shared instance at the timeout ---"]
+    for table in ("unix", "tcp", "tcp6"):
+        try:
+            rows = Path("/proc/net", table).read_text().splitlines()
+        except OSError as exc:
+            rows = [f"(/proc/net/{table}: {type(exc).__name__})"]
+        # unix: the abstract `@rns/<instance>/…` names; tcp: ports 37428/37429 (0x9234/0x9235)
+        out += [r for r in rows if "rns/" in r or ":9234 " in r or ":9235 " in r
+                or r.startswith("(")]
+    from lhpc.core.procident import proc_identity
+    aborted, skipped = [], []
+    for path in sorted((root / "state" / "owned").glob("*__*__*__*.json")):
+        comp = path.name.split("__", 1)[0]
+        if comp not in _RNS_PEERS:
+            continue
+        try:
+            rec = json.loads(path.read_text())
+            pid = int(rec["pid"])
+        except (OSError, ValueError, KeyError, TypeError):
+            skipped.append(f"{comp} (unreadable record)")
+            continue
+        # The product's ownership rule (`Lifecycle.verify_owned`): the live process has the
+        # recorded start time, pgid and sid, and its sid is its own pid. A stale record
+        # whose pid was reused is skipped, never signalled.
+        live = proc_identity(pid)
+        if (live is None or live.get("sid") != pid
+                or any(str(rec.get(k)) != str(live.get(k)) for k in ("starttime", "pgid", "sid"))):
+            skipped.append(f"{comp} (not the recorded process)")
+            continue
+        with contextlib.suppress(ProcessLookupError, PermissionError):
+            os.killpg(pid, signal.SIGABRT)
+            aborted.append(comp)
+    time.sleep(drain)
+    out.append(f"--- aborted for their stacks: {', '.join(aborted) or 'none'}"
+               + (f"; skipped: {', '.join(skipped)}" if skipped else "") + " ---")
+    for log in sorted(p for comp in set(aborted) for p in (root / "logs").glob(f"start-{comp}*.log")):
         try:
-            drawn, ended = _pty_read(master, proc, expect, ready_timeout)
-            if ended:
-                with contextlib.suppress(subprocess.TimeoutExpired):
-                    proc.wait(timeout=2)            # for its exit status in the message
-            tail = drawn[-600:].decode("utf-8", "replace")
-            # ENDED, not `poll()`: the terminal closes before the exit can be reaped, and a
-            # program read as still running there would be retried as a pure timeout.
-            broken = ended or proc.poll() is not None or bool(_DREW_AN_ERROR.search(drawn))
-            matched = _matches(drawn, expect)
-            dump = "" if (matched or broken) else _pty_abort(proc, master)
-            if attempt == 1 and not matched and not broken:
-                first = (f"\n--- first start: timed out after {ready_timeout} s, retried once. "
-                         f"It drew: {tail!r}{dump}{_log_tails(logs)}\n--- end of first start")
-                continue                            # `finally` stops it; then the retry
-            # A timeout without evidence blames no stack; neither does a retried start, which
-            # runs against whatever the aborted first start left (a held connection, a lock).
-            why = mark if broken and not first else ""
-            assert drawn, (f"{why}{command!r} drew nothing on its terminal within "
-                           f"{ready_timeout} s{dump}{_log_tails(logs)}{first}")
-            assert matched, (
-                f"{why}{command!r} did not draw anything matching {expect!r} within "
-                f"{ready_timeout} s. It drew: {tail!r}{dump}{_log_tails(logs)}{first}")
-            time.sleep(hold)
-            assert proc.poll() is None, (
-                f"{mark if not first else ''}{command!r} exited on its own (rc={proc.returncode}) — not a running "
-                f"app. It drew: {tail!r}{_log_tails(logs)}{first}")
-        finally:
-            killed = _pty_stop(proc, master)
-        assert not killed, (f"{command!r} ignored SIGTERM and had to be killed — it does not "
-                            f"exit cleanly")
-        return drawn
-    raise AssertionError("unreachable")          # the second start returns or fails
+            text = log.read_text(encoding="utf-8", errors="replace")
+        except OSError as exc:
+            text = f"(not readable: {type(exc).__name__})"
+        # From the dump's first line: it is followed by a C-level backtrace that a plain tail
+        # would keep instead of the Python stacks.
+        at = text.rfind("Fatal Python error")
+        out.append(f"--- {log} ---\n" + (text[at:at + 12000] if at >= 0 else text[-1500:]))
+    return "\n".join(out)
 
 
 def _pty_launch(command: str, env: dict):
@@ -650,6 +696,32 @@ def alive(env: dict, component: str) -> bool:
     return bool(m) and m.group(1) == "running"
 
 
+# What LHPC's status calls a component that is GONE: the only readiness evidence a wait-based
+# check has against the stack (`RunState`, docs/architecture.md "Probes and status").
+_GONE = ("stopped", "failed")
+
+
+def readiness_failed(env: dict, stack: str, component: str, what: str) -> str:
+    """The message for a wait-based readiness check that did not pass: marked
+    `stack_regression(stack, 'readiness')` ONLY when LHPC's own status reports `component`
+    stopped or failed — its process is gone. A component still running while the window passed
+    (a slow runner looks exactly like that), any other state, and a status call that failed,
+    timed out or did not list the component are no evidence against the stack: UNMARKED. Either
+    way the message names the state it read and ends with the component's run-log tail.
+
+    Called only when the check failed (an assert message is built only then), so a passing
+    case pays nothing."""
+    try:
+        r = run_lhpc(env, "status", timeout=120)
+        m = re.search(rf"^\s+{re.escape(component)}\s+([a-z-]+)", r.stdout, re.MULTILINE)
+        state = m.group(1) if (r.returncode == 0 and m) else "unreadable"
+    except subprocess.TimeoutExpired:
+        state = "unreadable"
+    mark = f"{stack_regression(stack, 'readiness')}\n" if state in _GONE else ""
+    logs = sorted(Path(env["LHPC_RUNTIME_ROOT"], "logs").glob(f"start-{component}*.log"))
+    return f"{mark}{what} (LHPC reports {component}: {state}){_log_tails(logs)}"
+
+
 def gui_startable(svc, stack_id: str, comp_id: str) -> bool:
     """LHPC's OWN predicate for 'can this GUI component run here'. Never re-derived: the
     build skip, the start gate and the image's composition check all read this one."""
diff --git a/testlab/tests/release/conftest.py b/testlab/tests/release/conftest.py
index 1d88ecd0..17aa7865 100644
--- a/testlab/tests/release/conftest.py
+++ b/testlab/tests/release/conftest.py
@@ -86,7 +86,12 @@ def lane_display(request):
 
 @pytest.fixture(scope="session")
 def env(lab):
-    return lab_env(lab.root)
+    env = lab_env(lab.root)
+    # Inherited by every Python program the lane starts through LHPC: a fatal signal, and the
+    # SIGABRT of a readiness timeout's diagnosis (`reticulum_peers_snapshot`), then prints every
+    # thread's stack into that program's own run log. It changes nothing else.
+    env["PYTHONFAULTHANDLER"] = "1"
+    return env
 
 
 @pytest.fixture(scope="session")
diff --git a/testlab/tests/release/test_release_verify.py b/testlab/tests/release/test_release_verify.py
index e591683a..12ac89d7 100644
--- a/testlab/tests/release/test_release_verify.py
+++ b/testlab/tests/release/test_release_verify.py
@@ -25,8 +25,10 @@ from lhpc_testlab.release import (
     plugin_manager_marker,
     plugin_manager_pids,
     pty_readiness,
+    readiness_failed,
     require_prerequisite,
     required_release_cases,
+    reticulum_peers_snapshot,
     stack_regression,
     start_component,
     stop,
@@ -110,11 +112,10 @@ def test_release_kiss(env, svc):
     """The KISS TNC over the fake daemon: its own TCP listener is the evidence."""
     install_build(env, svc, "kiss")
     _start(env, "kiss")
-    assert wait_tcp(KISS_TCP, 120), (f"{stack_regression('kiss', 'readiness')}\n"
-                                     "kiss is not serving KISS/TCP on 8001")
-    assert alive(env, "loraham-kiss-tnc"), (
-        f"{stack_regression('kiss', 'readiness')}\n"
-        "LHPC does not report loraham-kiss-tnc running")
+    assert wait_tcp(KISS_TCP, 120), readiness_failed(
+        env, "kiss", "loraham-kiss-tnc", "kiss is not serving KISS/TCP on 8001")
+    assert alive(env, "loraham-kiss-tnc"), readiness_failed(
+        env, "kiss", "loraham-kiss-tnc", "LHPC does not report loraham-kiss-tnc running")
 
 
 def test_release_graywolf(env, svc):
@@ -124,14 +125,13 @@ def test_release_graywolf(env, svc):
     _start(env, "graywolf", timeout=900)
     assert wait_http(f"http://127.0.0.1:{GRAYWOLF_UI}/", 120,
                      accept=(200, 401, 403)) in (200, 401, 403), \
-        (f"{stack_regression('graywolf', 'readiness')}\n"
-         "graywolf web UI never answered on 8080")
-    assert alive(env, "graywolf"), (f"{stack_regression('graywolf', 'readiness')}\n"
-                                    "LHPC does not report graywolf running")
+        readiness_failed(env, "graywolf", "graywolf", "graywolf web UI never answered on 8080")
+    assert alive(env, "graywolf"), readiness_failed(
+        env, "graywolf", "graywolf", "LHPC does not report graywolf running")
     stop(env, "graywolf")
 
 
-def test_release_chat(env, svc, record_property):
+def test_release_chat(env, svc):
     """Chat is interactive: the controller presents a command instead of starting it, so the
     proof is that command running on a real terminal against the fake daemon."""
     stop(env, "kiss", require=False)
@@ -140,8 +140,7 @@ def test_release_chat(env, svc, record_property):
     # — a broken fixture must never freeze chat.
     _start(env, "daemon", timeout=300, attribute=False)
     comp = next(c for c in svc.stack("chat").components if c.id == "loraham-chat")
-    pty_readiness(svc.manual_start_command(comp), env, CHAT_DRAWS, stack="chat",
-                  record=record_property)
+    pty_readiness(svc.manual_start_command(comp), env, CHAT_DRAWS, stack="chat")
 
 
 def test_release_voice_gtk(env, svc):
@@ -163,13 +162,12 @@ def test_release_voice_gtk(env, svc):
         "LHPC's GUI predicate says the Voice GTK app cannot run here, but this lane owns a "
         "display: the capability it requires is missing, which is a release defect")
     _start(env, "voice", timeout=300)
-    assert alive(env, "loraham-voice"), (
-        f"{stack_regression('voice', 'readiness')}\n"
-        "the GTK variant is startable here but is not running")
+    assert alive(env, "loraham-voice"), readiness_failed(
+        env, "voice", "loraham-voice", "the GTK variant is startable here but is not running")
     stop(env, "voice")
 
 
-def test_release_voice_terminal(env, svc, headless_box, record_property):
+def test_release_voice_terminal(env, svc, headless_box):
     """The terminal variant on a box with NO graphical session — the box LHPC ships it for.
 
     Nothing here is faked into place. With the display gone, LHPC's own start typed-SKIPS the
@@ -191,8 +189,7 @@ def test_release_voice_terminal(env, svc, headless_box, record_property):
     _start(env, "voice", timeout=300)
     cli = next(c for c in svc.stack("voice").components if c.id == "loraham-voice-cli")
     try:
-        pty_readiness(svc.manual_start_command(cli), env, VOICE_CLI_DRAWS, stack="voice",
-                      record=record_property)
+        pty_readiness(svc.manual_start_command(cli), env, VOICE_CLI_DRAWS, stack="voice")
     finally:
         stop(env, "voice")
         stop(env, "daemon")
@@ -240,17 +237,16 @@ def test_release_meshcore_chat(env, svc):
     install_build(env, svc, "meshcore", timeout=2400)
     _meshcore_mode(env, "chat")
     _start(env, "meshcore", timeout=900)
-    assert wait_tcp(MESHCORE_COMPANION, 180), (
-        f"{stack_regression('meshcore', 'readiness')}\n"
-        "MeshCore companion never opened TCP 5000")
-    assert alive(env, "meshcore-node"), (f"{stack_regression('meshcore', 'readiness')}\n"
-                                         "LHPC does not report meshcore-node running")
+    assert wait_tcp(MESHCORE_COMPANION, 180), readiness_failed(
+        env, "meshcore", "meshcore-node", "MeshCore companion never opened TCP 5000")
+    assert alive(env, "meshcore-node"), readiness_failed(
+        env, "meshcore", "meshcore-node", "LHPC does not report meshcore-node running")
     start_component(env, "meshcore-webui", stack="meshcore")
     assert wait_http(f"http://127.0.0.1:{MESHCORE_WEBUI}/", 180,
                      accept=(200, 401, 403)) in (200, 401, 403), \
-        (f"{stack_regression('meshcore', 'readiness')}\nMeshCore Web UI never answered")
-    assert alive(env, "meshcore-webui"), (f"{stack_regression('meshcore', 'readiness')}\n"
-                                          "LHPC does not report meshcore-webui running")
+        readiness_failed(env, "meshcore", "meshcore-webui", "MeshCore Web UI never answered")
+    assert alive(env, "meshcore-webui"), readiness_failed(
+        env, "meshcore", "meshcore-webui", "LHPC does not report meshcore-webui running")
     stop(env, "meshcore")
 
 
@@ -260,10 +256,9 @@ def test_release_meshcore_chat_repeater(env):
     _start(env, "meshcore", timeout=900)
     assert wait_http(f"http://127.0.0.1:{REPEATER_DASHBOARD}/", 240,
                      accept=(200, 401, 403)) in (200, 401, 403), \
-        (f"{stack_regression('meshcore', 'readiness')}\nrepeater dashboard silent")
-    assert wait_tcp(MESHCORE_COMPANION, 180), (
-        f"{stack_regression('meshcore', 'readiness')}\n"
-        "hosted companion never opened TCP 5000")
+        readiness_failed(env, "meshcore", "meshcore-node", "repeater dashboard silent")
+    assert wait_tcp(MESHCORE_COMPANION, 180), readiness_failed(
+        env, "meshcore", "meshcore-node", "hosted companion never opened TCP 5000")
     _assert_plugin_manager_running(env)
     stop(env, "meshcore")
     _assert_plugin_manager_gone(env)
@@ -279,7 +274,7 @@ def test_release_meshcore_repeater(env):
     _start(env, "meshcore", timeout=900)
     assert wait_http(f"http://127.0.0.1:{REPEATER_DASHBOARD}/", 240,
                      accept=(200, 401, 403)) in (200, 401, 403), \
-        (f"{stack_regression('meshcore', 'readiness')}\nrepeater dashboard silent")
+        readiness_failed(env, "meshcore", "meshcore-node", "repeater dashboard silent")
     # Unmarked: mode wiring between LHPC's config and the upstream repeater, not one of the
     # four phases — a freeze would be the wrong remedy.
     assert not wait_tcp(MESHCORE_COMPANION, 20), \
@@ -290,17 +285,16 @@ def test_release_meshcore_repeater(env):
     _meshcore_mode(env, "chat")
 
 
-def test_release_meshcore_cli(env, svc, record_property):
+def test_release_meshcore_cli(env, svc):
     """The MeshCore CLI is an interactive component: proved on a terminal against the running
     companion, not by `--version`."""
     _start(env, "meshcore", timeout=900)
-    assert wait_tcp(MESHCORE_COMPANION, 180), (
-        f"{stack_regression('meshcore', 'readiness')}\n"
-        "MeshCore companion never opened TCP 5000")
+    assert wait_tcp(MESHCORE_COMPANION, 180), readiness_failed(
+        env, "meshcore", "meshcore-node", "MeshCore companion never opened TCP 5000")
     comp = next(c for c in svc.stack("meshcore").components if c.id == "meshcore-cli")
     try:
         pty_readiness(svc.manual_start_command(comp), env, MESHCLI_DRAWS, ready_timeout=90,
-                      stack="meshcore", record=record_property)
+                      stack="meshcore")
     finally:
         stop(env, "meshcore")
 
@@ -312,8 +306,8 @@ def test_release_reticulum(env, svc):
     """Reticulum's own client tool must list the LoRa interface this stack installed."""
     install_build(env, svc, "reticulum", timeout=3600)
     _start(env, "reticulum", timeout=900)
-    assert alive(env, "rns"), (f"{stack_regression('reticulum', 'readiness')}\n"
-                               "LHPC does not report rns running")
+    assert alive(env, "rns"), readiness_failed(
+        env, "reticulum", "rns", "LHPC does not report rns running")
     root = Path(env["LHPC_RUNTIME_ROOT"])
     # The venv lives in the node's own checkout (its `bin` and `build_marker` are declared
     # relative to it), so the tool's path follows the manifest's source path.
@@ -330,14 +324,13 @@ def test_release_reticulum(env, svc):
         out = r.stdout + r.stderr
         return "Interface" in out
 
-    assert wait_for(_rnstatus_lists_an_interface, 120), (f"{stack_regression('reticulum', 'readiness')}\n"
-                                f"rnstatus listed no interface:\n{out[-1500:]}")
+    assert wait_for(_rnstatus_lists_an_interface, 120), readiness_failed(
+        env, "reticulum", "rns", f"rnstatus listed no interface:\n{out[-1500:]}")
     assert "lora" in out.lower(), (f"{stack_regression('reticulum', 'readiness')}\n"
                                    f"rnstatus lists no LoRa interface:\n{out[-1500:]}")
     # Optional components of this stack: not started by the stack, so started by name.
     start_component(env, "lxmd", stack="reticulum")
-    assert alive(env, "lxmd"), (f"{stack_regression('reticulum', 'readiness')}\n"
-                                "lxmd did not come up")
+    assert alive(env, "lxmd"), readiness_failed(env, "reticulum", "lxmd", "lxmd did not come up")
     # MeshChat is a release deliverable too, and a stack start deliberately leaves it stopped —
     # so without starting it by name the lane shipped a browser client it never once ran. It
     # launches through the rns-client guard, which refuses unless the node above is really up,
@@ -347,9 +340,9 @@ def test_release_reticulum(env, svc):
     # 200, not "any answer": the bundled UI requires no authentication of its own — the proxy is
     # where auth lives — so a 401/403 here would mean something other than MeshChat replied.
     assert wait_http(f"http://127.0.0.1:{MESHCHAT_UI}/", 180, accept=(200,)) == 200, \
-        (f"{stack_regression('reticulum', 'readiness')}\nMeshChat did not serve its UI")
-    assert alive(env, "meshchat"), (f"{stack_regression('reticulum', 'readiness')}\n"
-                                    "LHPC does not report meshchat running")
+        readiness_failed(env, "reticulum", "meshchat", "MeshChat did not serve its UI")
+    assert alive(env, "meshchat"), readiness_failed(
+        env, "reticulum", "meshchat", "LHPC does not report meshchat running")
     # Sideband is a release deliverable of this stack, and the lane runs against a real X
     # display (the workflow starts Xvfb before it). So LHPC's GUI predicate dropping it here
     # means the GUI capability it needs is MISSING, not that this box is legitimately headless
@@ -359,14 +352,24 @@ def test_release_reticulum(env, svc):
         "LHPC's GUI predicate says Sideband cannot run here, but this lane has a display: the "
         "capability it requires is missing, which is a release defect")
     start_component(env, "sideband", stack="reticulum")
-    assert alive(env, "sideband"), (f"{stack_regression('reticulum', 'readiness')}\n"
-                                    "sideband is startable here but did not come up")
+    assert alive(env, "sideband"), readiness_failed(
+        env, "reticulum", "sideband", "sideband is startable here but did not come up")
 
 
-def test_release_reticulum_nomadnet(env, svc, record_property):
-    """NomadNet is interactive: proved on a terminal against the running Reticulum."""
+def test_release_reticulum_nomadnet(env, svc):
+    """NomadNet is interactive: proved on a terminal against the running Reticulum.
+
+    Its first start has hung on a loaded runner (nothing drawn, the process alive), never
+    reproduced elsewhere. A timeout is not retried: it fails unmarked, and before anything is
+    stopped `reticulum_peers_snapshot` records who holds the shared instance's sockets and every
+    Reticulum process's stack, so the next occurrence names its cause."""
     require_prerequisite(env, "reticulum", left_by="test_release_reticulum")
     comp = next(c for c in svc.stack("reticulum").components if c.id == "nomadnet")
+    aborted = []
+
+    def _peers():
+        aborted.append(True)
+        return reticulum_peers_snapshot(env)
     try:
         root = Path(env["LHPC_RUNTIME_ROOT"])
         # NomadNet's own log, and the start logs (those that exist) of the Reticulum node it
@@ -375,9 +378,11 @@ def test_release_reticulum_nomadnet(env, svc, record_property):
                 *sorted(p for name in ("rns", "lxmd", "meshchat", "sideband")
                         for p in (root / "logs").glob(f"start-{name}-*.log")))
         pty_readiness(svc.manual_start_command(comp), env, NOMADNET_DRAWS, ready_timeout=90,
-                      stack="reticulum", logs=logs, record=record_property)
+                      stack="reticulum", logs=logs, on_timeout=_peers)
     finally:
-        stop(env, "reticulum")
+        # After a snapshot its processes were aborted on purpose: the stop then only tidies,
+        # and must not replace the case's own failure with its own.
+        stop(env, "reticulum", require=not aborted)
 
 
 # --------------------------------------------------------------------------- binary stacks
@@ -389,9 +394,8 @@ def test_release_meshtastic(env, svc):
     install_build(env, svc, "meshtastic", timeout=2400)
     _artifact_intact(svc, "meshtastic")
     _start(env, "meshtastic", timeout=900)
-    assert wait_tcp(MESHTASTIC_API, 180), (
-        f"{stack_regression('meshtastic', 'readiness')}\n"
-        "meshtasticd never opened its API port 4403")
+    assert wait_tcp(MESHTASTIC_API, 180), readiness_failed(
+        env, "meshtastic", "meshtastic", "meshtasticd never opened its API port 4403")
     info = run_lhpc(env, "meshtastic", "--info", timeout=300)
     assert info.returncode == 0, (f"{stack_regression('meshtastic', 'readiness')}\n"
                                   f"the node did not answer its own CLI (rc {info.returncode}): "
@@ -444,8 +448,8 @@ def test_release_meshcom(env, svc):
     _artifact_intact(svc, "meshcom")
     _start(env, "meshcom", timeout=1200)
     assert wait_http(f"http://127.0.0.1:{MESHCOM_UI}/", 900, accept=(200,)) == 200, \
-        (f"{stack_regression('meshcom', 'readiness')}\n"
-         "MeshCom web UI never reached 200 — the firmware did not finish booting")
+        readiness_failed(env, "meshcom", "meshcom-qemu",
+                         "MeshCom web UI never reached 200 — the firmware did not finish booting")
     stop(env, "meshcom")
 
 
diff --git a/testlab/tests/unit/test_release_attribution.py b/testlab/tests/unit/test_release_attribution.py
index 3308d52a..9d6caab3 100644
--- a/testlab/tests/unit/test_release_attribution.py
+++ b/testlab/tests/unit/test_release_attribution.py
@@ -575,17 +575,75 @@ def test_the_log_names_the_rule_expects_are_the_ones_the_builder_writes(tmp_path
         f"silently disables every automatic freeze")
 
 
+# --- a wait-based readiness check: marked only when LHPC reports the component gone ------------
+
+_STATUS = """OK    Status collected (read-only; no network, no changes).
+[kiss] LoRaHAM KISS TNC  ({stack})
+  loraham-kiss-tnc         {state:<14} [service] band 433  tx disabled  src ok
+  loraham-kiss-serial      stopped        [service] band 433  tx disabled  src ok
+"""
+
+
+def _readiness_message(monkeypatch, tmp_path, status):
+    """`readiness_failed` for kiss with `lhpc status` answering `status` (a CompletedProcess,
+    or an exception to raise). The lane's executable is stubbed: the case is the reading of
+    its answer, not the process table."""
+    import lhpc_testlab.release as rel
+
+    def run(env, *args, timeout=300.0, check=False):
+        if isinstance(status, BaseException):
+            raise status
+        return status
+    monkeypatch.setattr(rel, "run_lhpc", run)
+    (tmp_path / "logs").mkdir(exist_ok=True)
+    (tmp_path / "logs" / "start-loraham-kiss-tnc-433.log").write_text("KISS-RUN-LOG-LAST\n")
+    return rel.readiness_failed({"LHPC_RUNTIME_ROOT": str(tmp_path)}, "kiss",
+                                "loraham-kiss-tnc", "kiss is not serving KISS/TCP on 8001")
+
+
+def _done(stdout, rc=0):
+    return subprocess.CompletedProcess(["lhpc", "status"], rc, stdout, "")
+
+
+@pytest.mark.parametrize("state", ["running", "degraded", "unknown"])
+def test_a_window_that_passed_while_the_component_runs_blames_no_stack(monkeypatch, tmp_path,
+                                                                      state):
+    msg = _readiness_message(monkeypatch, tmp_path,
+                             _done(_STATUS.format(stack=state, state=state)))
+    assert not re.search(r"STACK-REGRESSION stack=", msg)
+    assert f"loraham-kiss-tnc: {state}" in msg and "KISS-RUN-LOG-LAST" in msg
+
+
+@pytest.mark.parametrize("state", ["stopped", "failed"])
+def test_a_component_lhpc_reports_gone_is_marked(monkeypatch, tmp_path, state):
+    msg = _readiness_message(monkeypatch, tmp_path,
+                             _done(_STATUS.format(stack=state, state=state)))
+    assert msg.startswith("STACK-REGRESSION stack=kiss phase=readiness\n")
+    assert "KISS-RUN-LOG-LAST" in msg
+
+
+@pytest.mark.parametrize("status", [
+    _done("ERR   status failed\n", rc=1),                                # the call failed
+    _done(_STATUS.format(stack="stopped", state="stopped"), rc=2),     # failed, whatever it printed
+    _done("OK    Status collected.\n"),                                 # the component not listed
+    subprocess.TimeoutExpired(["lhpc", "status"], 120),               # the call timed out
+], ids=["rc1", "rc2-with-stopped-text", "not-listed", "timeout"])
+def test_a_status_that_cannot_be_read_blames_no_stack(monkeypatch, tmp_path, status):
+    msg = _readiness_message(monkeypatch, tmp_path, status)
+    assert not re.search(r"STACK-REGRESSION stack=", msg)
+    assert "loraham-kiss-tnc: unreadable" in msg
+
+
 # --- an interactive program's readiness: only evidence of breaking blames the stack -------------
 
-def _readiness_failure(command, tmp_path, *, expect="NEVER-DRAWN", logs=(), record=None):
+def _readiness_failure(command, tmp_path, *, expect="NEVER-DRAWN", logs=(), on_timeout=None):
     import os
-    import warnings
 
     from lhpc_testlab.release import pty_readiness
-    with pytest.raises(AssertionError) as exc, warnings.catch_warnings():
-        warnings.simplefilter("ignore")             # a retry warns; its cases check `record`
+    extra = {"on_timeout": on_timeout} if on_timeout else {}
+    with pytest.raises(AssertionError) as exc:
         pty_readiness(command, dict(os.environ), expect, ready_timeout=1.5, hold=0.2,
-                      stack="reticulum", logs=logs, record=record)
+                      stack="reticulum", logs=logs, **extra)
     return str(exc.value)
 
 
@@ -618,7 +676,7 @@ def test_a_readiness_failure_carries_the_tails_of_the_named_logs(tmp_path):
     assert f"--- {daemon} (tail) ---" in msg and "not readable" in msg
 
 
-# --- a readiness timeout: its stack is captured, and a first one is retried once ----------------
+# --- a readiness timeout: diagnosed (its stack, and what it waits on), never retried -------------
 
 def _counted(tmp_path, first, then):
     """A shell command that appends one line per start to a counter file and runs `first` on
@@ -633,32 +691,76 @@ def _hang_in_python():
     return f"exec {sys.executable} -c 'import time; time.sleep(60)'"
 
 
-def test_a_first_start_that_times_out_is_retried_once_and_reported(tmp_path):
+def test_a_start_that_times_out_is_not_retried_and_carries_its_stack(tmp_path):
+    """A test body is never re-run because it failed: a hung first start fails, unmarked, with
+    the thread dump of where it hung — even when a second start would have drawn."""
+    command, counter = _counted(tmp_path, _hang_in_python(), "printf 'READY'; sleep 30")
+    msg = _readiness_failure(command, tmp_path, expect="READY")
+    assert counter.read_text().count("start") == 1
+    assert "STACK-REGRESSION" not in msg
+    assert "Fatal Python error" in msg and "time.sleep" in msg
+
+
+def test_the_timeout_diagnosis_runs_while_the_program_still_hangs(tmp_path):
+    """`on_timeout` runs BEFORE the abort: what a hung program waits on (another process, a
+    socket) is only visible while it still waits."""
     import os
 
-    from lhpc_testlab.release import pty_readiness
-    command, counter = _counted(tmp_path, _hang_in_python(), "printf 'READY'; sleep 30")
-    recorded = []
-    with pytest.warns(UserWarning) as warned:
-        drawn = pty_readiness(command, dict(os.environ), "READY", ready_timeout=1.5, hold=0.2,
-                              stack="reticulum", record=lambda k, v: recorded.append((k, v)))
-    assert b"READY" in drawn
-    assert counter.read_text().count("start") == 2
-    [(name, note)] = recorded
-    assert name == "readiness-retry" and "retried once" in note
-    assert "Fatal Python error" in note and "time.sleep" in note   # the first start's stack
-    assert any("retried once" in str(w.message) for w in warned)
-
-
-def test_a_start_that_times_out_twice_fails_unmarked_with_both_stacks(tmp_path):
-    command, counter = _counted(tmp_path, _hang_in_python(), _hang_in_python())
-    recorded = []
-    msg = _readiness_failure(command, tmp_path, record=lambda k, v: recorded.append((k, v)))
-    assert counter.read_text().count("start") == 2
-    [(name, note)] = recorded                 # the failed retry is in the JUnit too
-    assert name == "readiness-retry" and "Fatal Python error" in note
-    assert "STACK-REGRESSION" not in msg      # a dump is drawn after the abort; it blames nobody
-    assert msg.count("Fatal Python error") == 2 and "first start" in msg
+    pidfile = tmp_path / "pid"
+    seen = []
+
+    def diagnose():
+        pid = int(pidfile.read_text())
+        os.kill(pid, 0)                            # raises if it were already gone
+        seen.append(pid)
+        return "\n--- DIAGNOSIS-TEXT ---"
+    msg = _readiness_failure(f"echo $$ > {pidfile}; {_hang_in_python()}", tmp_path,
+                             on_timeout=diagnose)
+    assert len(seen) == 1
+    assert msg.index("DIAGNOSIS-TEXT") < msg.index("Fatal Python error")
+    assert "STACK-REGRESSION" not in msg
+
+
+def test_the_reticulum_snapshot_aborts_only_recorded_reticulum_processes(tmp_path):
+    """The snapshot SIGABRTs the process group of every Reticulum process LHPC owns, and returns
+    their faulthandler dumps from their run logs. A process of another stack, and a record whose
+    pid now belongs to a different process (a stale record after pid reuse), are left alone."""
+    import json
+    import os
+
+    from lhpc_testlab.release import reticulum_peers_snapshot
+
+    from lhpc.core.procident import proc_identity
+    (tmp_path / "logs").mkdir()
+    owned = tmp_path / "state" / "owned"
+    owned.mkdir(parents=True)
+    env = dict(os.environ, PYTHONFAULTHANDLER="1")
+    procs = {}
+    for comp, band in (("rns", "868"), ("loraham-kiss-tnc", "433"), ("lxmd", "868")):
+        # It says `ready` once its interpreter (and with it faulthandler) is up; the dump goes to
+        # its run log on stderr, as an LHPC-started component's does.
+        with open(tmp_path / "logs" / f"start-{comp}-{band}.log", "ab") as log:
+            procs[comp] = subprocess.Popen(
+                [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
+                stdout=subprocess.PIPE, stderr=log, env=env, start_new_session=True, text=True)
+        rec = {"pid": procs[comp].pid, **proc_identity(procs[comp].pid)}
+        if comp == "lxmd":                          # stale: the pid's process started later
+            rec["starttime"] = int(rec["starttime"]) - 1
+        (owned / f"{comp}__{band}__{procs[comp].pid}__n1.json").write_text(json.dumps(rec))
+    try:
+        for p in procs.values():
+            assert p.stdout.readline() == "ready\n"
+        out = reticulum_peers_snapshot({"LHPC_RUNTIME_ROOT": str(tmp_path)}, drain=2.0)
+        assert procs["rns"].wait(timeout=10) != 0          # aborted
+        assert procs["loraham-kiss-tnc"].poll() is None    # another stack: untouched
+        assert procs["lxmd"].poll() is None                # a stale record: untouched
+        assert "aborted for their stacks: rns; skipped: lxmd (not the recorded process) ---" in out
+        assert "Fatal Python error: Aborted" in out and 'File "<string>", line 1' in out
+    finally:
+        for p in procs.values():
+            if p.poll() is None:
+                p.kill()
+                p.wait(timeout=10)
 
 
 @pytest.mark.parametrize("first", [
@@ -672,22 +774,6 @@ def test_a_start_that_shows_it_is_broken_is_never_retried(tmp_path, first):
     assert "STACK-REGRESSION stack=reticulum phase=readiness" in msg
 
 
-@pytest.mark.parametrize("then", [
-    "exit 3",                                                           # exited
-    "printf 'Traceback (most recent call last):\\n'; sleep 30",         # drew a traceback
-])
-def test_a_retried_start_that_breaks_blames_no_stack(tmp_path, then):
-    """The retry runs against what the aborted first start left behind, so its breakage may be
-    the harness's own doing: it fails, unmarked."""
-    command, counter = _counted(tmp_path, _hang_in_python(), then)
-    recorded = []
-    msg = _readiness_failure(command, tmp_path, expect="READY",
-                             record=lambda k, v: recorded.append((k, v)))
-    assert counter.read_text().count("start") == 2
-    assert [k for k, _v in recorded] == ["readiness-retry"]
-    assert "STACK-REGRESSION" not in msg and "first start" in msg
-
-
 def test_the_program_runs_without_core_dumps(tmp_path):
     import os
 
@@ -698,31 +784,6 @@ def test_the_program_runs_without_core_dumps(tmp_path):
     assert limit.read_text().strip() == "0"
 
 
-@pytest.mark.parametrize("fails", ["_pty_stop", "_pty_launch"])
-def test_a_retry_that_never_started_is_not_reported(tmp_path, monkeypatch, fails):
-    """The first start's cleanup, or the second launch, raises: no retry ran, so none is
-    reported, and the error is what the case fails on."""
-    import os
-
-    from lhpc_testlab import release
-    real, calls = getattr(release, fails), []
-
-    def once_then_raise(*a, **kw):
-        calls.append(1)
-        if (fails == "_pty_stop" and len(calls) == 1) or (fails == "_pty_launch"
-                                                          and len(calls) == 2):
-            if fails == "_pty_stop":
-                real(*a, **kw)                    # leave nothing running
-            raise OSError("injected")
-        return real(*a, **kw)
-    monkeypatch.setattr(release, fails, once_then_raise)
-    recorded = []
-    with pytest.raises(OSError, match="injected"):
-        release.pty_readiness(_hang_in_python(), dict(os.environ), "READY", ready_timeout=1.5,
-                              hold=0.2, record=lambda k, v: recorded.append((k, v)))
-    assert recorded == []
-
-
 def test_a_start_whose_terminal_closed_before_it_was_reaped_is_an_exit(tmp_path, monkeypatch):
     """The race CI hit: the program's terminal closes BEFORE its exit can be reaped, so
     `poll()` still says "running" right after an exit. That is an exit — marked, never retried
```

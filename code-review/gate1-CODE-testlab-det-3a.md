# Gate 1: testlab-det-3a (CI speed), v2 (round 7 note)

**Request.** Judge the one commit below against plan v4 §2 P3a (CI speed): parallel test processes in CI (`-n auto --dist loadfile`, pytest-xdist), cancellation of superseded runs ONLY on `cons/*` branches (was: every ref, main and dev included), and `slow-build needs: testlab`. Judge whether the measured before/after figures support the report's claims, whether the docs and CHANGELOG lines say exactly what the diff does, and whether the reported risk (fork under xdist, fixed in a later packet) is stated honestly. It is a workflow and dependency change: no red-before applies; the proof is the branch's own GitHub runs.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)

### add7076c ci: parallel test processes; cancel only superseded topic-branch runs; slow-build after testlab

```text
ci: parallel test processes; cancel only superseded topic-branch runs; slow-build after testlab

CI's `test` job ran the whole suite in one process: about 26-39 min per Python version on
GitHub (CI run 38810819791, job wall time incl. setup: 3.12 26 min, 3.13 38 min, 3.11 39 min).
Locally the same suite runs parallel: `-n 8` over tests/stacks, tests/repo and testlab/tests/unit
took 42.5 s today; the full suite with `-n 12 --dist loadfile` took 63 s (2026-09-04). It now runs `-n auto --dist loadfile` (pytest-xdist joins the dev
extras; `loadfile` keeps one module in one process, so module-level fixtures behave as before;
pytest-cov combines the workers' coverage).

Both workflows cancelled a superseded run for the same ref on EVERY ref, main and dev included,
so a quick second push could leave a commit there without a complete result. Now only a topic
branch (`refs/heads/cons/*`) cancels its superseded run; main, dev, release, bot and pull-request
runs always complete.

`slow-build` (~45 min on an arm runner) `needs: testlab`: a candidate the `testlab` job
rejects no longer holds an arm runner for it (release-verify is not a dependency). Cost on a green run: slow-build starts after testlab (~6 min).

No test can be red-before for a workflow change; the proof is this branch's own CI and testlab
runs (durations in the code report). tests/repo: 524 passed, 1 skipped.

```

## The report

> # Code report: testlab-det-3a (packet P3a: CI speed)
>
> Branch `cons/testlab-det-3a-r1` on `origin/dev` = `254bbae3`. One commit, add7076c (rebased onto dev a8e0e5ce; the measuring runs ran on 75dfd78f, whose tree differs only in the two wording lines below). Plan v4 §2 P3a. Workflow and dependency change only: no product or test code changes.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | add7076c | .github/workflows/ci.yml, .github/workflows/testlab.yml, pyproject.toml (dev extras + `pytest-xdist`), docs/maintenance.md, CHANGELOG.md | none new | not applicable: a workflow change cannot be red-before in a test. The proof is this branch's own runs (below), compared with the last serial run |
>
> Round 7 note (GREEN WITH NOTES): `slow-build` depends on the `testlab` JOB only, not on release-verify. The workflow comment, the CHANGELOG line and the commit message now say "the `testlab` job" instead of "the lab lanes".
>
> ## HOUSE RULES KEPT (tools/check_houserules.py from cons/houserules-1-r1)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 1), top section 10 lines   OK
> docs         1 files, +5 lines; fact "-n auto --dist loadfile" -> docs/maintenance.md; fact "only after `testlab` passed" -> docs/maintenance.md   OK
> tests        0 new tests; layers -; red-before 0/0   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      N/A (budget table absent)   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## Proof (GitHub runs; each figure from `gh run view <id> --json jobs` or the job log)
>
> | | before: CI 38810819791 (serial, b379911f) | after: CI 38819553965 (`-n auto --dist loadfile`, 75dfd78f) |
> |---|---|---|
> | test (3.11), job wall time | 21:33:50 → 22:12:42 = 38 min 52 s | 22:49:52 → 23:00:32 = 10 min 40 s |
> | test (3.12) | 21:33:50 → 22:00:13 = 26 min 23 s | 22:49:42 → 23:00:18 = 10 min 36 s |
> | test (3.13) | 21:33:50 → 22:11:51 = 38 min 1 s | 22:49:22 → 23:00:54 = 11 min 32 s |
> | 3.13 pytest line | `7611 passed in 2240.04s (0:37:20)` | `7611 passed, 1 warning in 650.29s (0:10:50)` |
> | 3.13 coverage TOTAL (stmts / miss / branch / part / %) | 35391 / 3900 / 13408 / 1529 / 88% | 35391 / 3891 / 13408 / 1528 / 88% |
>
> - **Same suite, same coverage base.** Equal test count (7611) and equal statement and branch counts. The parallel run covers 9 statements more, a difference of process lifetimes, not of the suite. The `1 warning` is new in the parallel run and is a real, small risk: `tests/install/test_source.py:387` (`test_recovery_removes_a_clone_killed_before_its_journal`) calls `os.fork()`, and under xdist the worker process has an execnet thread, so Python 3.12+ warns `This process … is multi-threaded, use of fork() may lead to deadlocks in the child`. The child then runs `adopt_source` (git, file I/O) before it is SIGKILLed; a lock held by the other thread at fork time could hang it. Not observed (1 run, passed), not fixed here. The fix goes into packet P4 (the review order): the child runs in a fresh interpreter, built by a small importable helper shared with the `installer` fixture (tests/install/conftest.py:60), instead of `os.fork()`. As a one-off script it would duplicate the fixture in about 25 lines.
> - **Single runs.** Each figure comes from one run per side, at different times and under different runner load, so the ratio is indicative, not a benchmark.
> - **slow-build `needs: testlab`.** testlab run 38819556953: `testlab` ended 22:56:13 and `slow-build` started 22:56:17. Slow-build result: still running when this packet was sent (landing rule of 2026-10-16: gate 1 + local checks; the run is extra evidence).
> - **Cancellation.**
>   - Before, both workflows cancelled on every ref.
>   - After, observed live: re-dispatching on this `cons/*` branch cancelled the superseded runs 38819502242 (CI) and 38819505873 (testlab), both `completed cancelled`, about 20 s after the new runs queued.
>   - main/dev/release/bot/PR runs are no longer cancelled. That follows from the expression `startsWith(github.ref, 'refs/heads/cons/')`; it is not observed here, because no such push was made.
>
> ## The 6-point block
>
> 1. **Contracts.**
>    - `ci.yml` test job: `python -m pytest -q -rs -n auto --dist loadfile --cov=…` (ci.yml:91). `loadfile` keeps one module in one worker.
>    - The concurrency of both workflows: `cancel-in-progress: ${{ startsWith(github.ref, 'refs/heads/cons/') }}` (ci.yml:19, testlab.yml:30).
>    - testlab.yml `slow-build`: `needs: testlab`.
>    - pyproject `[project.optional-dependencies] dev` + `pytest-xdist`, unpinned like the rest (docs/maintenance.md, Policy).
> 2. **Invariants.**
>    - Every test still runs: equal count, measured above.
>    - The coverage artifact and job summary are produced (steps `Coverage summary`, `Upload coverage.xml` = success in all three jobs).
>    - A commit on main/dev always gets complete results; before this change a second push could cancel them.
> 3. **Failure classes.**
>    - The bot dispatches the testlab workflow on its `pins/*` branches, which no longer cancel: one dispatch per branch, unaffected.
>    - A failed `testlab` job now skips `slow-build`, so the run is red and the bot reads the whole-run conclusion as today.
>    - `-n auto` uses the runner's cores (`auto` = CPU count).
> 4. **Test rules:** no test changed.
> 5. **Directories:** `pytest -q -n 8 tests/repo` → `524 passed, 1 skipped`. Ruff is not affected, because no Python file changed.
> 6. **Adversarial self-review.**
>    - The first commit message quoted "about 1 min locally" without a measurement. It was corrected, before the measuring runs, to the measured local figures (42.5 s for three directories today; 63 s for the full suite on 2026-09-04). The amend is why the runs are on 75dfd78f.
>    - Nothing else found.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | diff | `git diff --stat origin/dev 75dfd78f` | 5 files (ci.yml, testlab.yml, pyproject.toml, maintenance.md, CHANGELOG.md) |
> | job times | `gh run view <run> --json jobs --jq '.jobs[]|…startedAt, completedAt'` | as tabled |
> | test counts, coverage | `gh run view <run> --log --job <3.13 job>`, the pytest and TOTAL lines | as tabled |
> | cancellation | `gh run view 38819502242 --json status,conclusion` | `completed cancelled` |

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

## The full diff (`git diff origin/dev add7076c30169d65f50a8901c17c56b0e080fe0c`)

```diff
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index bd534173..c4fa9780 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -12,10 +12,11 @@ on:
 permissions:
   contents: read
 
-# Cancel superseded runs for the same ref so only the latest matters.
+# A newer push to a topic branch (`cons/*`) cancels its superseded run. Never on main, dev, a
+# release or bot branch, or a pull request: every commit there keeps its own complete result.
 concurrency:
   group: ci-${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  cancel-in-progress: ${{ startsWith(github.ref, 'refs/heads/cons/') }}
 
 jobs:
   test:
@@ -87,7 +88,7 @@ jobs:
         # nobody executes and reports ~0% however many tests pass. `-m` puts the working directory
         # first, so the imported and the measured tree are the same one. The install above still
         # proves the package builds and installs.
-        run: python -m pytest -q -rs --cov=lhpc --cov-branch --cov-report=term:skip-covered --cov-report=xml:coverage.xml
+        run: python -m pytest -q -rs -n auto --dist loadfile --cov=lhpc --cov-branch --cov-report=term:skip-covered --cov-report=xml:coverage.xml
 
       - name: Coverage summary
         if: ${{ !cancelled() }}
diff --git a/.github/workflows/testlab.yml b/.github/workflows/testlab.yml
index 09a95dca..d842123f 100644
--- a/.github/workflows/testlab.yml
+++ b/.github/workflows/testlab.yml
@@ -24,9 +24,10 @@ on:
 permissions:
   contents: read
 
+# As in ci.yml: only a newer push to a topic branch (`cons/*`) cancels its superseded run.
 concurrency:
   group: testlab-${{ github.ref }}
-  cancel-in-progress: true
+  cancel-in-progress: ${{ startsWith(github.ref, 'refs/heads/cons/') }}
 
 jobs:
   testlab:
@@ -215,6 +216,9 @@ jobs:
     # on ANY part (cpu, io, mem) of the fixed calibration workload: then tighten that part's
     # throttle below; a throttle only ever gets tighter.
     if: ${{ (github.event_name == 'push' && github.ref == 'refs/heads/main') || inputs.release_verify }}
+    # After the `testlab` job passes (not release-verify): a candidate it rejects does not hold an arm
+    # runner for the ~45 min of this job.
+    needs: testlab
     runs-on: ubuntu-24.04-arm
     # Kept well below the release bot's testlab bound (280 min, counted from the bot's dispatch),
     # leaving >= 40 min for queueing and setup, so a hung slow-build job is cut before the bot gives
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 9e41cefe..000c87f1 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -9,6 +9,7 @@ Fixed
   still comes from GitHub).
 - Graywolf's update check now says why GitHub could not be reached: curl's exit code and the end of its message,
   in the result and in the status it keeps.
+- CI runs the test suite in parallel processes, and the slow-build check starts only after the testlab job passed.
 
 ## 0.12.9
 
diff --git a/docs/maintenance.md b/docs/maintenance.md
index 20217611..eac5a8e5 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -23,7 +23,8 @@ What CI enforces, branches and releases, the pin-bump recipe, and the gotchas on
     is the committed `lhpc deps --script` snapshot for the pre-clone moment; the suite fails when it
     differs from the generator (`tests/install/test_bootstrap_deps.py`).
   - `ruff check lhpc testlab` (the frozen ruleset) and `ruff check tests --select F,E9`
-  - `python -m pytest -q -rs --cov=lhpc --cov-branch`: the whole suite, no `-m` lane; branch
+  - `python -m pytest -q -rs -n auto --dist loadfile --cov=lhpc --cov-branch`: the whole suite in
+    parallel processes, no `-m` lane; branch
     coverage in the log, a `coverage.xml` artifact per Python version and the total in the job
     summary
   - `bandit -q -r lhpc -lll` (high severity) and `pip-audit . --strict`, also after a test failure
@@ -44,8 +45,9 @@ What CI enforces, branches and releases, the pin-bump recipe, and the gotchas on
 `.github/workflows/testlab.yml`, on the same events: the `testlab` job runs the lab lanes on an
 aarch64 runner; `release-verify` installs, builds, starts and identity-proves every stack a pin
 release may move, on pushes to `main` and on dispatch with `release_verify=true` only
-([testlab.md](testlab.md#running-the-verification-lanes)); `slow-build`, on the same triggers,
-is the slow-target build row below.
+([testlab.md](testlab.md#running-the-verification-lanes)); `slow-build`, on the same triggers
+and only after `testlab` passed, is the slow-target build row below. A newer push to a `cons/*`
+branch cancels that branch's superseded runs of both workflows; nothing else is cancelled.
 
 No external project's own test suite runs in CI, apart from meshcom-qemu-raspi's
 `tests/test-fetch-qemu.sh` in `pin-validation`.
diff --git a/pyproject.toml b/pyproject.toml
index dd4b69b7..960b76db 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -39,6 +39,9 @@ dev = [
     # Coverage plugin. CI MEASURES and publishes line and branch coverage; there is no
     # threshold and no gate — a drop is judged in review.
     "pytest-cov",
+    # Parallel test processes (`-n auto --dist loadfile`, CI's test job): one module stays in one
+    # process, so module-level fixtures behave as in a serial run.
+    "pytest-xdist",
     # In-process Zstandard (genuine .zst) for the binary-channel archive tests, so they no longer
     # shell out to build fixtures. Production extraction still uses the host `zstd`.
     "zstandard",
```

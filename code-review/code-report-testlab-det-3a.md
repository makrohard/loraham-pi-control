# Code report: testlab-det-3a (packet P3a: CI speed)

Branch `cons/testlab-det-3a-r1` on `origin/dev` = `254bbae3`. One commit, add7076c (rebased onto dev a8e0e5ce; the measuring runs ran on 75dfd78f, whose tree differs only in the two wording lines below). Plan v4 §2 P3a. Workflow and dependency change only: no product or test code changes.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| add7076c | .github/workflows/ci.yml, .github/workflows/testlab.yml, pyproject.toml (dev extras + `pytest-xdist`), docs/maintenance.md, CHANGELOG.md | none new | not applicable: a workflow change cannot be red-before in a test. The proof is this branch's own runs (below), compared with the last serial run |

Round 7 note (GREEN WITH NOTES): `slow-build` depends on the `testlab` JOB only, not on release-verify. The workflow comment, the CHANGELOG line and the commit message now say "the `testlab` job" instead of "the lab lanes".

## HOUSE RULES KEPT (tools/check_houserules.py from cons/houserules-1-r1)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 1), top section 10 lines   OK
docs         1 files, +5 lines; fact "-n auto --dist loadfile" -> docs/maintenance.md; fact "only after `testlab` passed" -> docs/maintenance.md   OK
tests        0 new tests; layers -; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      N/A (budget table absent)   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Proof (GitHub runs; each figure from `gh run view <id> --json jobs` or the job log)

| | before: CI 38810819791 (serial, b379911f) | after: CI 38819553965 (`-n auto --dist loadfile`, 75dfd78f) |
|---|---|---|
| test (3.11), job wall time | 21:33:50 → 22:12:42 = 38 min 52 s | 22:49:52 → 23:00:32 = 10 min 40 s |
| test (3.12) | 21:33:50 → 22:00:13 = 26 min 23 s | 22:49:42 → 23:00:18 = 10 min 36 s |
| test (3.13) | 21:33:50 → 22:11:51 = 38 min 1 s | 22:49:22 → 23:00:54 = 11 min 32 s |
| 3.13 pytest line | `7611 passed in 2240.04s (0:37:20)` | `7611 passed, 1 warning in 650.29s (0:10:50)` |
| 3.13 coverage TOTAL (stmts / miss / branch / part / %) | 35391 / 3900 / 13408 / 1529 / 88% | 35391 / 3891 / 13408 / 1528 / 88% |

- **Same suite, same coverage base.** Equal test count (7611) and equal statement and branch counts. The parallel run covers 9 statements more, a difference of process lifetimes, not of the suite. The `1 warning` is new in the parallel run and is a real, small risk: `tests/install/test_source.py:387` (`test_recovery_removes_a_clone_killed_before_its_journal`) calls `os.fork()`, and under xdist the worker process has an execnet thread, so Python 3.12+ warns `This process … is multi-threaded, use of fork() may lead to deadlocks in the child`. The child then runs `adopt_source` (git, file I/O) before it is SIGKILLed; a lock held by the other thread at fork time could hang it. Not observed (1 run, passed), not fixed here. The fix goes into packet P4 (the review order): the child runs in a fresh interpreter, built by a small importable helper shared with the `installer` fixture (tests/install/conftest.py:60), instead of `os.fork()`. As a one-off script it would duplicate the fixture in about 25 lines.
- **Single runs.** Each figure comes from one run per side, at different times and under different runner load, so the ratio is indicative, not a benchmark.
- **slow-build `needs: testlab`.** testlab run 38819556953: `testlab` ended 22:56:13 and `slow-build` started 22:56:17. Slow-build result: still running when this packet was sent (landing rule of 2026-10-16: gate 1 + local checks; the run is extra evidence).
- **Cancellation.**
  - Before, both workflows cancelled on every ref.
  - After, observed live: re-dispatching on this `cons/*` branch cancelled the superseded runs 38819502242 (CI) and 38819505873 (testlab), both `completed cancelled`, about 20 s after the new runs queued.
  - main/dev/release/bot/PR runs are no longer cancelled. That follows from the expression `startsWith(github.ref, 'refs/heads/cons/')`; it is not observed here, because no such push was made.

## The 6-point block

1. **Contracts.**
   - `ci.yml` test job: `python -m pytest -q -rs -n auto --dist loadfile --cov=…` (ci.yml:91). `loadfile` keeps one module in one worker.
   - The concurrency of both workflows: `cancel-in-progress: ${{ startsWith(github.ref, 'refs/heads/cons/') }}` (ci.yml:19, testlab.yml:30).
   - testlab.yml `slow-build`: `needs: testlab`.
   - pyproject `[project.optional-dependencies] dev` + `pytest-xdist`, unpinned like the rest (docs/maintenance.md, Policy).
2. **Invariants.**
   - Every test still runs: equal count, measured above.
   - The coverage artifact and job summary are produced (steps `Coverage summary`, `Upload coverage.xml` = success in all three jobs).
   - A commit on main/dev always gets complete results; before this change a second push could cancel them.
3. **Failure classes.**
   - The bot dispatches the testlab workflow on its `pins/*` branches, which no longer cancel: one dispatch per branch, unaffected.
   - A failed `testlab` job now skips `slow-build`, so the run is red and the bot reads the whole-run conclusion as today.
   - `-n auto` uses the runner's cores (`auto` = CPU count).
4. **Test rules:** no test changed.
5. **Directories:** `pytest -q -n 8 tests/repo` → `524 passed, 1 skipped`. Ruff is not affected, because no Python file changed.
6. **Adversarial self-review.**
   - The first commit message quoted "about 1 min locally" without a measurement. It was corrected, before the measuring runs, to the measured local figures (42.5 s for three directories today; 63 s for the full suite on 2026-09-04). The amend is why the runs are on 75dfd78f.
   - Nothing else found.

## Numbers, measured

| figure | command | output |
|---|---|---|
| diff | `git diff --stat origin/dev 75dfd78f` | 5 files (ci.yml, testlab.yml, pyproject.toml, maintenance.md, CHANGELOG.md) |
| job times | `gh run view <run> --json jobs --jq '.jobs[]|…startedAt, completedAt'` | as tabled |
| test counts, coverage | `gh run view <run> --log --job <3.13 job>`, the pytest and TOTAL lines | as tabled |
| cancellation | `gh run view 38819502242 --json status,conclusion` | `completed cancelled` |

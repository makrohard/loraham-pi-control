# Code report: efficiency-qw8 (the RF-log badge runs no git)

Branch `cons/efficiency-qw8-r1` on `origin/dev` = `771d1a37`. Order: the handler's QW8 (2026-10-16, found during A1's
second read): `rflog_running` gets the treatment `log_running` got in QW7; same packet shape; UX unchanged; one
function. One commit.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| f3dec5fb | efficiency: the RF-log badge runs no git | core/service_lifecycle_ops.py (one line), tests/core/test_snapshot_memo.py | core: `test_the_rf_log_badge_runs_no_git[running, stopped]` (writer `meshcore-node`, job `rf-meshcore.log`, a FakeSystem checkout at `src/openhop-core`: the badge after a fresh request equals the full snapshot's answer and `is running`; 0 git calls; precondition: the full read ran git) | yes, on 771d1a37: 2 failed — 2 git calls per poll |

**Green:** `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/web tests/stacks tests/repo
tests/cli tests/golden` → `5395 passed, 1 skipped in 72.42s`; `tests/core/test_snapshot_memo.py` → `84 passed`.
`ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` → `All checks passed!`.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  771d1a37..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 27 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        1 new tests; layers tests/core=core; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_lifecycle_ops.py 6315->6315 lines (budget 6315), 73->73 public (budget 73); family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The 6-point block

1. **Contract.** `rflog_running(job)` (`service_lifecycle_ops.py:5882`): for a stack writer it now reads
   `build_snapshot(sources=False)` (`:5893`), which returns the request's full snapshot when one is cached, else one
   without git that is never cached (QW7). The daemon branch (`daemon_view(band).reachable`) is unchanged.
2. **Invariant and its test.** The badge equals the full snapshot's answer and runs no git: the new test, running and
   stopped. That a run state is the same with and without git for every source state is QW7's
   `test_a_snapshot_without_git_has_the_same_run_states` (7 states × running/stopped), unchanged and green.
3. **Failure classes.** Same as QW7: the light snapshot is uncached, so a later full read in the same request builds
   its own; `fresh=True` rechecks are untouched; the CLI path (`lhpc rflog`) reads no badge.
4. **Test rules.** Through the public service call; FakeSystem; call counts and a typed bool.
5. **Directories and ruff.** As under Green.
6. **Self-review.** One line changed; the line count of `service_lifecycle_ops.py` is unchanged (6315). No CHANGELOG
   line: QW7's bullet ("the log pages' background refresh no longer runs git for every installed source") now also
   holds for the RF-log page.

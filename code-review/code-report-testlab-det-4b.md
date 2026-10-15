# Code report: testlab-det-4b (packet P4b: the fork fix and the S1 surface test)

Branch `cons/testlab-det-4b-r1` on `origin/dev` = `78c9fac7`. Two test-only commits; no product code, no CHANGELOG line (tests change nothing an operator sees). Plan v4 §2 P4: the S1 test (W1b-5), plus the fork-under-xdist risk that P3a's report named and the review order put here.

## Commits

| commit | files | tests | red-before |
|---|---|---|---|
| 0e87103b | tests/install/installers.py (new: the `installer` fixture's builder + the child script), tests/install/conftest.py (the fixture calls it), tests/install/test_source.py | `test_recovery_removes_a_clone_killed_before_its_journal` (install layer): the child runs `installers.py kill-mid-staging <root>` in a fresh interpreter, `returncode == -SIGKILL`, the recovery assertions unchanged | NONE: the defect is a possible deadlock of a forked child in a multi-threaded xdist worker, signalled by CI's DeprecationWarning (run 38819553965, test (3.13)), not producible on demand. Locally the old test passes even under `-n 4 -W error::DeprecationWarning`. Stated in the commit message |
| 2ecef9d6 | tests/stacks/test_graywolf_upstream.py | `test_a_failed_check_keeps_curls_exit_code_and_message` (stacks layer): for the summary AND the kept `error`: `curl rc 22: `, curl's last line kept as one line, no newline, ≤ 200 chars after the marker | yes, against the tree before S1 (eb9673a4): `assert ('curl rc 22: ' in "upstream check failed for 'graywolf': network/API error")` |

## HOUSE RULES KEPT (dev's tools/check_houserules.py)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 16 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        1 new tests; layers tests/install=install tests/stacks=stacks; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The 6-point block

1. **Contracts.** `installers.build_installer(root, comp=None, *, extra, search_root, system, values)`: the old fixture body, unchanged in behaviour; the fixture keeps its signature and default root (`tmp_path/"rt"`). `installers.py kill-mid-staging <root>`: exit by SIGKILL after the copy into the candidate, exit 1 if the staging returned.
2. **Invariants.** The recovery assertions of the killed-staging case are unchanged (one staged candidate holding `file.txt`, no journal, recovery removes it, a fresh adopt is `done`). The S1 behaviour is unchanged; only its test is stronger.
3. **Failure classes.** The child imports lhpc from this checkout (`PYTHONPATH` = the repo), not from whatever is installed (the stale-editable trap). A child that fails to start shows its stderr tail in the assertion. 174 `installer(` uses in tests/install still go through the fixture (`grep -rn "installer(" tests/install/*.py | wc -l`).
4. **Test rules.** No private patching in the test any more (the monkeypatch moved into the child script, where it IS the scenario); the S1 case asserts the kept fields, not a sentence.
5. **Directories:** `pytest -q -n 8 tests/install tests/stacks tests/repo` → `3481 passed, 1 skipped`; `ruff check lhpc testlab`, `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review.** The fork fix has no red-before, said plainly rather than dressed up; the conftest lost 7 now-unused imports (ruff F401).

## Numbers, measured

| figure | command | output |
|---|---|---|
| diff | `git diff --shortstat origin/dev 2ecef9d6` | 4 files changed, 76 insertions(+), 35 deletions(-) |
| lines | `git show origin/dev:<f> \| wc -l`; `wc -l <f>` | conftest.py 223 → 210; test_source.py 5369 → 5363; test_graywolf_upstream.py 402 → 407; installers.py 55 (new) |
| tests | the pytest command above | 3481 passed, 1 skipped |

# Code report: testrun-p7 (the two undocumented autouse fixtures say what they isolate)

Branch `cons/testrun-p7-r1` on `origin/dev` = `1803e92c`. Plan: PLAN-testrun v3 §3 P7 (gate 1 GREEN WITH NOTES).
Docstrings and one comment; no test behaviour changes.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| 9107acea | tests/conftest.py, tests/core/test_clock_policy.py | none changed | none: no behaviour |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        0 new tests; layers tests/core=core; red-before 0/0   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## What changed

| where | change | checked against |
|---|---|---|
| tests/conftest.py `_reap_real_spawns` (autouse) | docstring: after every test, each detached `sleep` `real_spawn` started is killed and reaped | its body: `yield`, then `while _SPAWNED: p.kill(); p.wait(timeout=2)`; a function-scoped autouse fixture runs around each test |
| tests/conftest.py, the comment above `_SPAWNED` | "reaped at" the end of the run → "reaped after its test (`_reap_real_spawns`)" | the same fixture: function scope, not session scope |
| tests/core/test_clock_policy.py `_probe_must_not_be_read` (autouse) | docstring: `service_system.read_kernel_time_state` raises in every test of the module, so a case passes only on the kernel state it hands the policy | its body: `monkeypatch.setattr(service_system, "read_kernel_time_state", boom)`, `boom` raises AssertionError |

Scan (`testrun-scan-v3.py`), class "r8 autouse fixture without a docstring": 2 → 0.

## The 6-point block

1. **Contracts:** none. 2. **Invariants:** none new. 3. **Failure classes:** none (no executable line changed).
4. **Test rules:** rule 8 ("autouse fixtures isolate the host, and say so").
5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/repo
   tests/golden` on 1803e92c → `2883 passed, 1 skipped in 72.02s`; both ruff runs → `All checks passed!`.
6. **Adversarial self-review:** the first commit message quoted the old comment and used a word the checker flags;
   reworded before the push (words 0). The branch was rebased from d77e34f4 onto 1803e92c (the P8 landing) before the
   directories ran; the two files do not overlap P8's.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | 1803e92c |
| diff | `git diff --stat origin/dev 9107acea` | 2 files changed, 5 insertions(+), 1 deletion(-) |
| undocumented autouse fixtures before/after | `python3 -I testrun-scan-v3.py` | 2 / 0 |

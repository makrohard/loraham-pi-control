# Gate 1: testrun-p7 (the two undocumented autouse fixtures say what they isolate)

**Request.** Judge the one commit below against PLAN-testrun v3 §3 P7 (excerpt below). Judge: is each docstring true against the fixture's body shown in the diff context; is the corrected comment true; is anything else changed.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff -U12` output verbatim (wider context so the fixture bodies are visible).

## The plan, §3 P7 (PLAN-testrun v3)

> | P7 autouse docstrings | `tests/conftest.py`, `core/test_clock_policy.py` | one docstring each: what the fixture isolates (rule 8) | the scan's "without a docstring" 2 → 0 |

## Commit (full message, `git log -1 --format=%B`)

### 9107acea testrun-p7: the two undocumented autouse fixtures say what they isolate

```text
testrun-p7: the two undocumented autouse fixtures say what they isolate

tests/README.md rule 8: autouse fixtures isolate the host, and say so.
tests/conftest.py's _reap_real_spawns and tests/core/test_clock_policy.py's
_probe_must_not_be_read had no docstring; each now names what it isolates.
The comment above conftest.py's spawn shim said the sleepers are reaped
once, at the end of the run; the autouse fixture reaps them after each
test, and the comment now says so.

Docstrings and one comment only: no test behaviour changes, no red-before.

```

## The report

> # Code report: testrun-p7 (the two undocumented autouse fixtures say what they isolate)
>
> Branch `cons/testrun-p7-r1` on `origin/dev` = `1803e92c`. Plan: PLAN-testrun v3 §3 P7 (gate 1 GREEN WITH NOTES).
> Docstrings and one comment; no test behaviour changes.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 9107acea | tests/conftest.py, tests/core/test_clock_policy.py | none changed | none: no behaviour |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        0 new tests; layers tests/core=core; red-before 0/0   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## What changed
>
> | where | change | checked against |
> |---|---|---|
> | tests/conftest.py `_reap_real_spawns` (autouse) | docstring: after every test, each detached `sleep` `real_spawn` started is killed and reaped | its body: `yield`, then `while _SPAWNED: p.kill(); p.wait(timeout=2)`; a function-scoped autouse fixture runs around each test |
> | tests/conftest.py, the comment above `_SPAWNED` | "reaped at" the end of the run → "reaped after its test (`_reap_real_spawns`)" | the same fixture: function scope, not session scope |
> | tests/core/test_clock_policy.py `_probe_must_not_be_read` (autouse) | docstring: `service_system.read_kernel_time_state` raises in every test of the module, so a case passes only on the kernel state it hands the policy | its body: `monkeypatch.setattr(service_system, "read_kernel_time_state", boom)`, `boom` raises AssertionError |
>
> Scan (`testrun-scan-v3.py`), class "r8 autouse fixture without a docstring": 2 → 0.
>
> ## The 6-point block
>
> 1. **Contracts:** none. 2. **Invariants:** none new. 3. **Failure classes:** none (no executable line changed).
> 4. **Test rules:** rule 8 ("autouse fixtures isolate the host, and say so").
> 5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/repo
>    tests/golden` on 1803e92c → `2883 passed, 1 skipped in 72.02s`; both ruff runs → `All checks passed!`.
> 6. **Adversarial self-review:** the first commit message quoted the old comment and used a word the checker flags;
>    reworded before the push (words 0). The branch was rebased from d77e34f4 onto 1803e92c (the P8 landing) before the
>    directories ran; the two files do not overlap P8's.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | 1803e92c |
> | diff | `git diff --stat origin/dev 9107acea` | 2 files changed, 5 insertions(+), 1 deletion(-) |
> | undocumented autouse fixtures before/after | `python3 -I testrun-scan-v3.py` | 2 / 0 |

## The full diff (`git diff -U12 1803e92cea6ad68a111c3e65ed6f94ad0d87b1d6 9107acea808cb151c3396be0570589f09c42bfe7`)

```diff
diff --git a/tests/conftest.py b/tests/conftest.py
index 6d0da7ea..ddb330bf 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -397,25 +397,25 @@ def set_call():
 
 
 def _set_call(svc, callsign="XX0XXA"):
     """Configure a valid operator callsign so a LICENSED stack (chat/graywolf/voice/meshcom) passes
     CALL-enforcement — the realistic precondition for starting one. Returns the service."""
     from lhpc.core.config import save_operator_config
     save_operator_config(svc._paths, callsign)
     svc._invalidate_config()
     return svc
 
 # Real-but-harmless spawn shim: ownership recording now requires a COMPLETE /proc
 # identity, so tests that "start" something must spawn a real process (a detached
-# `sleep`) rather than a fake pid. All spawned sleepers are reaped at session end.
+# `sleep`) rather than a fake pid. Every spawned sleeper is reaped after its test (`_reap_real_spawns`).
 _SPAWNED: list = []
 
 
 @pytest.fixture
 def real_spawn():
     """The real-spawn shim as a FIXTURE, so a test in any directory gets it without importing
     another test module (see tests/README.md: a test module never imports a test module)."""
     return _real_spawn
 
 
 def _real_spawn(argv, log, cwd=None, env=None):
     """A `spawn` callable for Lifecycle that launches a real detached `sleep` (its own
@@ -424,24 +424,26 @@ def _real_spawn(argv, log, cwd=None, env=None):
     try:
         open(str(log), "a").close()
     except OSError:
         pass
     p = subprocess.Popen(["sleep", "300"], start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
     _SPAWNED.append(p)
     return p.pid
 
 
 @pytest.fixture(autouse=True)
 def _reap_real_spawns():
+    """Isolates the host from the real processes `real_spawn` starts: after every test, each
+    detached `sleep` it spawned is killed and reaped, so none outlives the test that made it."""
     yield
     while _SPAWNED:
         p = _SPAWNED.pop()
         try:
             p.kill(); p.wait(timeout=2)
         except Exception:
             pass
 
 
 @pytest.fixture(autouse=True)
 def _no_daemon_verify_wait(monkeypatch):
     """The daemon-start CONF-socket verification waits seconds in production; the
diff --git a/tests/core/test_clock_policy.py b/tests/core/test_clock_policy.py
index 9c12f9d6..b4608530 100644
--- a/tests/core/test_clock_policy.py
+++ b/tests/core/test_clock_policy.py
@@ -8,24 +8,26 @@ import subprocess
 import sys
 
 import pytest
 
 from lhpc.core import clock, service_system
 
 _SYNCED = {"synced": True, "maxerror_us": 1000}
 _FLOOR = 1_735_689_600.0                              # 2025-01-01T00:00:00Z
 
 
 @pytest.fixture(autouse=True)
 def _probe_must_not_be_read(monkeypatch):
+    """Isolates the policy from the host clock: `service_system.read_kernel_time_state` raises in
+    every test here, so a case passes only on the kernel state it hands the policy itself."""
     def boom():
         raise AssertionError("the policy read the kernel itself")
     monkeypatch.setattr(service_system, "read_kernel_time_state", boom)
 
 
 @pytest.mark.parametrize("kernel, now, expected", [
     (_SYNCED, _FLOOR, (True, "")),
     ({"synced": True, "maxerror_us": 1_000_000}, _FLOOR + 1, (True, "")),
     (None, _FLOOR + 1,
      (False, "kernel time state unavailable — cannot tell whether the clock is synchronised")),
     ({"synced": False, "maxerror_us": 1000}, _FLOOR + 1,
      (False, "the clock is not synchronised (no time source has set it yet)")),
```

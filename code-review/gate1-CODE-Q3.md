# Gate 1 — CODE review request: Q3 (the best-effort helper), round 2

Please judge round 2 of Q3: does each code commit below reach the completion criterion (per adopted
site: an ordinary error in the side action leaves the main outcome and the original error unchanged
with one log line; Ctrl-C in the main action still runs the cleanup and propagates), does the
round-2 fix close every round-1 finding (the notice is one stderr line for any exception text; the
`_note_staged` claim matches the code), is behaviour otherwise unchanged, do the tests prove what
they claim (red before, green after), and does every sentence of the plan, the CHANGELOG and the
report below match the diff. Is anything simpler possible without trading safety?

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Answer form:

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| e2af0c4 (plan) | | |
| 3961f2a | | |
| 1877198 | | |
| cda4802 | | |
| af75627 | | |

Final line: GREEN / GREEN WITH NOTES / RED

## Round 1 RED → round 2

Gate 1 round 1: RED — five findings with one root cause, plus one claim.

**Root cause.** `best_effort` built its notice as `f"{what}: {Class}: {exc}"` without flattening the
exception text, so a message containing `\n` or `\r` (an OSError with a path, subprocess output, a
multi-line ValueError) broke the "one line on stderr" contract stated in the plan, the helper, both
adopting commits and the CHANGELOG. Fix, amended into 3961f2a (was 17212ff): the notice is built with
`' '.join(str(exc).split())` — every run of whitespace, newlines included, becomes one space, and
leading or trailing whitespace is dropped; nothing is capped. The docstring states that rule. Test
`test_a_multi_line_message_is_flattened_to_one_stderr_line` (`tests/core/test_best_effort.py`): an
exception with message `"first\nsecond\r\n  third\r"` through the default `stderr_line` gives
exactly the stderr text `"x: ValueError: first second third\n"`. Red before (the amended test file
over the unamended helper): `1 failed, 8 passed` — stderr held `first`, `second`, `  third` on
three lines (the raw message). Green after: `9 passed`. The callers' tests raise single-line messages and needed no
change.

**Claim.** The round-1 report said `_note_staged` "never raises an ordinary Exception"; its False
path calls `stderr_line` outside `best_effort`, so an error from a closed stderr there propagates.
Chosen: make the claim exact, no code move — the simplest code: moving that one write into
`best_effort` would mean a second `best_effort` around a plain write whose own failure notice goes to
the same broken stderr, and the base wrote this notice outside its `try` the same way. The
6-point block below now says exactly that; the plan (S3 change item) says it too. cda4802 (was
a6854ad) is unchanged.

**Wording.** Plan (amended into e2af0c4, was b6af7e9): the flatten rule; the `log` rule made exact
(an ordinary Exception from `log` is ignored, a BaseException propagates — it said "a failing `log`
is swallowed"); the helper test list names the multi-line case; the S3 False-path notice. CHANGELOG
(amended into af75627, was 00c43bb): one added sentence, "In these lines an error message that
spans several lines is joined into one." The code report: the commit ids, the helper's line count
(36 → 38) and production net (+47 → +49), the test count (8 → 9 cases), the flatten rule in the
contract, the `_note_staged` sentence.

**Commits.** `git commit --fixup` + `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash 042716f`, then
one report commit on top. `git patch-id --stable` before → after: changed only for the three amended
commits (plan 4b4ce80 → 162f673, helper 2a0f257 → 12efd56, CHANGELOG dce76af → 5179039); unchanged
for 1877198 (c2b7369), cda4802 (b9a9fad), and the three earlier report commits (8a073d9, 6f7e52d,
7d14602).

**Runs (foreground, `--basetemp=$HOME/pt-lhpc-q3`, removed after).**
`tests/core/test_best_effort.py tests/install/test_binary_channel.py tests/install/test_source.py`:
`393 passed, 1 skipped`. `tests/install`: `92 failed, 1127 passed, 4 skipped` — all 92 in
`tests/install/test_bootstrap_deps.py`, the uid-0 refusal described in point 5 of the 6-point block below, as in round 1.
(`zstd` was missing in this container at first, which failed
`test_doctor_is_quiet_for_a_healthy_binary_install` on the untouched base too; installed, as in
round 1.) `tests/core`: `2054 passed, 6 skipped`. Collected count: 2060 now, 2059 on the round-1
head; the round-1 figure `2052 passed` was one short of that head's 2053 (its cause is not
re-verifiable here). `tests/repo`:
`1 failed, 409 passed, 5 skipped` — the same base-red `test_changelog_leads_with_the_current_version`.
`ruff check lhpc testlab`: All checks passed. `ruff check tests --select F,E9`: All checks passed.

**Adversarial self-review (round 2).** Found: (a) `" ".join(str(exc).split())` also trims leading and
trailing whitespace — the round-1 finding's wording ("runs of whitespace become one space") alone would be
inexact, so docstring, plan and report state the trim; (b) the plan said "a failing `log` is
swallowed", untrue for a BaseException from `log` — fixed; (c) the code report's commit ids, line counts
and case count went stale with the amend — fixed; (d) the round-1 `tests/core` count — explained
above. Accepted: `what` is not flattened — it is caller text, built by the three callers from fixed
words, a stack id that `binary_available` has already accepted, and the staging record's own file name; an
exception whose `str()` raises still gives `"<what>: <Class>"` (the flattening sits inside the same
`try`).

## The code report

### Code report (round 1 body, ids and figures updated)

Base `origin/integration/1.0` @ 042716f. Branch `claude/focused-dijkstra-ivqkwl` (round 1); round 2 on
`claude/friendly-shannon-fh0bca`.
Python 3.11, pytest 9.1, ruff from `.[dev]` in a scratch venv; `zstd` installed in the container.

### Commits

| sha | subject | files | tests (red-before vs parent) |
|---|---|---|---|
| e2af0c4 | Q3: plan — the best-effort helper | plans/PLAN-Q3.md | — |
| 3961f2a | Q3: best_effort — one way to run a side action beside a failure | lhpc/core/best_effort.py (new, 38 lines), tests/core/test_best_effort.py | 9 cases; RED yes (`ModuleNotFoundError: lhpc.core.best_effort` at the parent) |
| 1877198 | Q3: binary install — the traceback and the interrupt unwind run best-effort | lhpc/core/service_binary_ops.py, tests/install/test_binary_channel.py | `test_a_failing_traceback_print_is_one_line_and_changes_no_outcome`, `test_a_failing_unwind_after_ctrl_c_is_one_line_and_the_interrupt_propagates`; RED yes: `2 failed` (no line; every outcome assertion before it held) |
| cda4802 | Q3: the staging record rewrite runs best-effort | lhpc/core/install.py, tests/install/test_source.py | `test_a_failing_staging_record_rewrite_never_stops_the_install[unexpected]` RED yes (the RuntimeError stops the adoption); `[oserror]`/`[containment]` RED (they now pin the standard line's tail); `[lost]` green both; `test_ctrl_c_in_the_staging_record_rewrite_propagates_and_the_record_is_cleared` green before by design (the narrow catch never swallowed Ctrl-C — it guards against widening the helper). Parent result: `3 failed, 2 passed` |
| af75627 | Q3: CHANGELOG — best-effort side actions | CHANGELOG.md | — |

Red-before commands (each with the parent's version of the one production file checked out over the
final tests, then restored):
`python -m pytest -q -p no:cacheprovider tests/core/test_best_effort.py` (module moved aside) →
collection error; `… tests/install/test_binary_channel.py::test_a_failing_traceback_print_is_one_line_and_changes_no_outcome
…::test_a_failing_unwind_after_ctrl_c_is_one_line_and_the_interrupt_propagates` → `2 failed`;
`… tests/install/test_source.py::test_a_failing_staging_record_rewrite_never_stops_the_install
…::test_ctrl_c_in_the_staging_record_rewrite_propagates_and_the_record_is_cleared` → `3 failed, 2 passed`.

### Sites (keep / replace) — as in the plan

Replaced: S1 `service_binary_ops.py` traceback print (silent `except Exception: pass` → one line);
S2 the interrupt unwind's `binary_recover()` (silent → one line; `except BaseException:` boundary and
bare `raise` kept); S3 `install.py::_note_staged` (narrow `(OSError, PathContainmentError)` → any
ordinary Exception; a `False` return keeps its existing line). Kept, with reasons in the plan: the
binary message fallback, the main unwind boundary, the venv `rmtree; raise`, every
`service_maintenance.py` `except Exception` (fail-closed decisions/refusals, task-panel reads), both
`config.py` sites. Outside FILES: listed in the plan, not adopted.

### Simplicity guardrails

- One plain function plus a one-line `stderr_line`; no class, no registry, no result type, no
  context-manager form (no site needs it).
- Line counts: `service_binary_ops.py` 988 → 986; `install.py` 2323 → 2317 (`_note_staged` body
  13 → 5 lines); new `best_effort.py` 38. Production net: +49 / −19 (+30, the helper; the sites
  themselves shrink by 8). Net code grows by the helper only — the price of one tested contract
  replacing three hand-written variants; adopting the 30+ listed sites outside FILES is where it pays.
- Dependencies: `service_binary_ops.py` imports +`best_effort`; self.* used unchanged
  (`binary_recover`). `install.py` imports −`sys`, +`best_effort, stderr_line`; self.* unchanged
  (`_staged_clone_payload`). The helper imports only `sys`, `collections.abc`, `typing`.

### The 6-point block

1. CONTRACTS. `best_effort(fn, *, what, log=stderr_line) -> fn() | None` (`lhpc/core/best_effort.py:18`):
   catches `Exception` only; returns None then; logs exactly one line `"<what>: <Class>: <msg>"`
   (`"<what>: <Class>"` if `__str__` raises; the exception text flattened to one line: runs of
   whitespace, including newlines, become one space, leading or trailing whitespace is dropped); an ordinary Exception from `log` is ignored; a BaseException (from `log` or `fn`) propagates;
   the caller's handled exception is untouched (bare `raise` re-raises it). `_note_staged`
   (`install.py:1295`): returns None; the rewrite (payload included) runs inside `best_effort`, so no
   ordinary Exception from it escapes; the False-path notice is a plain `stderr_line` write outside
   `best_effort` (as the base wrote it outside its `try`), so an error from stderr there propagates; `OwnedMarker.rewrite`
   (`runtime_fs.py:310`) returns bool — `False` keeps the existing line. `binary_install`
   (`service_binary_ops.py:402-450`): the typed `ActionResult` on Exception (data `binary_failed`,
   `offer_source`, `rolled_back`, `unexpected`) and propagation of the same BaseException object are
   unchanged. No lock order, persisted format or public signature touched.
2. INVARIANTS + TESTS. Binary install transaction (a failed install never costs the working one;
   Ctrl-C unwinds now): `test_a_failing_diagnostic_never_skips_the_unwind`,
   `test_ctrl_c_inside_the_transaction_unwinds_now`, the two new tests. Source transactions (a crash
   during staging leaves nothing nothing names; an unproven candidate is never removed):
   `test_a_staging_record_never_removes_an_unproven_candidate`, `test_a_staging_record_defers_to_its_journal`,
   the extended rewrite test and the new Ctrl-C test. Config as a transaction, uninstall protection:
   not touched; `tests/core/test_config.py`, `tests/core/test_uninstall_prep.py` green.
3. FAILURE CLASSES. Fakes: `traceback.print_exc`, `binary_recover`, `rewrite` replaced with
   functions of the real call shape (`binary_recover(self)`, `rewrite(text)`); `rewrite` fakes raise
   EIO OSError, `PathContainmentError`, RuntimeError, or return False. KeyboardInterrupt through
   cleanup: S2 test (unwind raises during Ctrl-C → same interrupt object, one line, journal left for
   the next command which restores), S3 Ctrl-C test (propagates, record cleared). CLI / web /
   detached job / boot restore: all reach these through the same `binary_install` / `adopt_source`;
   no adapter change. Stacked conflicts: `install.py` is NOT in the brief's FILES list (only in its
   ADOPT list) — touched lines 1295-1305 and two import lines only; if Q1 edits `install.py`
   recovery the conflict is textual at most; commit cda4802 can be dropped independently.
   `config.py` untouched (no shared line with Q1/Q2).
4. TEST RULES. Each new test red before against its parent except the deliberate Ctrl-C guard (stated);
   log lines asserted by count, site name and the exact `: <Class>: <msg>` tail (not startswith-only;
   the helper's own test pins the whole line, its contract); no network; no unchecked returns.
5. WHOLE DIRECTORIES (foreground, `--basetemp=$HOME/pt-lhpc-q3`, removed after):
   `tests/core`: `2052 passed, 6 skipped in 303.41s` (round 1; see "Round 1 RED → round 2" above).
   `tests/install`: `92 failed, 1127 passed, 4 skipped in 126.51s` — all 92 in
   `tests/install/test_bootstrap_deps.py`, identical on the untouched base (`92 failed, 35 passed,
   1 skipped`): the container runs as uid 0 and `bootstrap-deps.sh` refuses ("no non-root operator").
   Environmental, unrelated to this diff.
   `tests/repo`: `1 failed, 409 passed, 5 skipped` — `test_changelog_leads_with_the_current_version`,
   red on the base too (0.11.12 heading vs `version.py` 0.11.11); stays red with `## 0.12.0` until the
   release commit bumps the version.
   After the final rebase (round 1): the touched modules + config + uninstall prep: `770 passed, 1 skipped`.
   `ruff check lhpc testlab`: All checks passed. `ruff check tests --select F,E9`: All checks passed.
   No signature changed; grep of tests/ + testlab/ for `_note_staged`, `binary_install(`,
   `best_effort` → only tests/install and tests/core modules, all run above.
6. ADVERSARIAL SELF-REVIEW. Found and fixed: (a) an error whose `__str__` raises made the helper log
   nothing — now `"<what>: <Class>"`, tested; (b) ruff UP035 (`collections.abc.Callable`) and an
   unused `sys` in `install.py`; (c) test assertions loosened from whole-sentence equality to
   count + site + tail (house rule 2); (d) plan line refs corrected (725, 442-450). Accepted:
   S3 widens the catch to any Exception — by design (the record is crash evidence only; with no inode
   recorded, recovery removes a candidate only while it is empty). S3's exception line now reads
   `… — install continues: OSError: …` instead of `… (msg) — install continues`. The docs name no
   sentence this makes untrue (architecture.md lists "not every module"); the CHANGELOG line
   describes exactly the three operator-visible changes.

### Deviations

- `install.py` touched although not in FILES (named in ADOPT). Own commit, disjoint lines.
- No config.py or uninstall-prep change: the "diagnostics-before-unwind" line and the uninstall-prep
  diagnostics the brief names do not exist at this base; their `except Exception` sites are
  decisions, not side actions (plan table).
- No context-manager form (no site needs it).

## Full amended diff

`git diff --abbrev=7 042716f HEAD -- . ':!code-review'` — every change of the branch except the
review packet itself (this file and the code report, whose text is above). Commits, oldest first:
e2af0c4 plan, 3961f2a helper, 1877198 binary install, cda4802 staging record, af75627 CHANGELOG.

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 2d217a3..eb91240 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,13 @@
 # Changelog
 
+## 0.12.0
+
+- A source update no longer stops when recording its staged copy for crash recovery fails with an unexpected
+  error; it says so in one line and continues, as it already did for a disk error. A binary install that cannot
+  print the details of an unexpected error, or cannot undo itself after Ctrl-C, now says so in one line instead of
+  staying silent (after Ctrl-C the next command finishes the undo, as before). In these lines an error message that
+  spans several lines is joined into one.
+
 ## 0.11.12
 
 - Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
diff --git a/lhpc/core/best_effort.py b/lhpc/core/best_effort.py
new file mode 100644
index 0000000..d60115c
--- /dev/null
+++ b/lhpc/core/best_effort.py
@@ -0,0 +1,38 @@
+"""Best-effort side actions: a cleanup or diagnostic that runs beside a failure must never replace
+it. One plain function, so every such site neither hides the original error nor lets Ctrl-C skip
+the rest of its unwind."""
+
+from __future__ import annotations
+
+import sys
+from collections.abc import Callable
+from typing import TypeVar
+
+T = TypeVar("T")
+
+
+def stderr_line(line: str) -> None:
+    sys.stderr.write(line + "\n")
+
+
+def best_effort(fn: Callable[[], T], *, what: str,
+                log: Callable[[str], object] = stderr_line) -> T | None:
+    """Run the side action `fn` and return its result. An ordinary `Exception` from it is logged
+    as the one line "<what>: <Class>: <msg>" (without ": <msg>" when its __str__ raises) and None
+    is returned; the exception text is flattened to one line: runs of whitespace, including
+    newlines, become one space, and leading or trailing whitespace is dropped. An ordinary
+    Exception from `log` is ignored (a closed stderr). A `BaseException` (KeyboardInterrupt,
+    SystemExit) from `fn` or `log` propagates. Called inside an `except` block it leaves the
+    handled exception untouched: a bare `raise` after it re-raises the original."""
+    try:
+        return fn()
+    except Exception as exc:
+        try:
+            line = f"{what}: {type(exc).__name__}: {' '.join(str(exc).split())}"
+        except Exception:                    # an error whose __str__ raises
+            line = f"{what}: {type(exc).__name__}"
+        try:
+            log(line)
+        except Exception:                    # a closed stderr
+            pass
+        return None
diff --git a/lhpc/core/install.py b/lhpc/core/install.py
index cbac94c..53c77bd 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -18,7 +18,6 @@ import errno
 import os
 import re
 import shutil
-import sys
 import time
 from contextlib import contextmanager
 from dataclasses import dataclass, field
@@ -26,6 +25,7 @@ from pathlib import Path
 
 from . import provenance
 from .assets import asset_text
+from .best_effort import best_effort, stderr_line
 from .config import Config
 from .model import Component, Stack
 from .paths import PathContainmentError, Paths
@@ -1298,16 +1298,10 @@ class Installer:
         one stderr line and the staging goes on; recovery then finds no inode recorded."""
         if rec is None:
             return
-        try:
-            ok = rec.rewrite(self._staged_clone_payload(dest, staging,
-                                                        [handle.st_dev, handle.st_ino]))
-        except (OSError, PathContainmentError) as exc:
-            ok, why = False, f" ({exc})"
-        else:
-            why = ""
-        if not ok:
-            sys.stderr.write(f"staging record {rec.name} could not record the candidate{why} — "
-                             "install continues\n")
+        what = f"staging record {rec.name} could not record the candidate — install continues"
+        if best_effort(lambda: rec.rewrite(self._staged_clone_payload(
+                dest, staging, [handle.st_dev, handle.st_ino])), what=what) is False:
+            stderr_line(what)
 
     def _recover_staged_clone(self, jf: Path) -> str:
         """Resolve ONE pre-clone record ("" = nothing to report). Its writer held the source-path
diff --git a/lhpc/core/service_binary_ops.py b/lhpc/core/service_binary_ops.py
index eeae6ad..d2c3c40 100644
--- a/lhpc/core/service_binary_ops.py
+++ b/lhpc/core/service_binary_ops.py
@@ -25,6 +25,7 @@ import traceback
 from . import binary_install as bi
 from . import binary_receipt as brx
 from . import reslock, runtime_fs, source_registry
+from .best_effort import best_effort
 from .paths import PathContainmentError
 from .service_base import ActionResult, AdmissionRefused, SourceTxnBlocked
 from .snapshot_memo import invalidates_snapshot
@@ -418,10 +419,8 @@ class BinaryOpsMixin:
                 if not isinstance(exc, (bi.BinaryInstallError, OSError, tarfile.TarError,
                                         PathContainmentError)):
                     _name = type(exc).__name__
-                    try:
-                        traceback.print_exc()
-                    except Exception:
-                        pass
+                    best_effort(traceback.print_exc,
+                                what=f"binary install of '{stack_id}': printing the traceback failed")
                     _unexpected = {"unexpected": _name}
                     _orig, exc = exc, bi.BinaryInstallError(f"unexpected {_name}")
                     exc.__cause__ = _orig
@@ -443,10 +442,9 @@ class BinaryOpsMixin:
                 # Ctrl-C / SystemExit inside the transaction: unwind NOW (a half-switched install
                 # must not wait for the next command), then let the original propagate unchanged.
                 # A failing unwind leaves the journal open for that next command to recover.
-                try:
-                    self.binary_recover()
-                except Exception:
-                    pass
+                best_effort(self.binary_recover,
+                            what=f"binary install of '{stack_id}': the unwind after the interrupt "
+                                 "failed — the next command recovers it")
                 raise
             finally:
                 shutil.rmtree(tmpdir, ignore_errors=True)
diff --git a/plans/PLAN-Q3.md b/plans/PLAN-Q3.md
new file mode 100644
index 0000000..9f22117
--- /dev/null
+++ b/plans/PLAN-Q3.md
@@ -0,0 +1,91 @@
+# PLAN-Q3 — the best-effort helper
+
+Base: `origin/integration/1.0` @ 042716f. One helper, adopted where a side action (cleanup or
+diagnostic) sits beside a failure today; every other `except Exception` in FILES is listed with keep.
+
+## The helper — `lhpc/core/best_effort.py` (new, plain function)
+
+`best_effort(fn, *, what, log=stderr_line) -> fn() | None`: runs `fn()`; an ordinary `Exception`
+from it is logged as the one line `"<what>: <Class>: <msg>"` through `log` and `None` is returned
+(no `: <msg>` when its `__str__` raises; the exception text is flattened to one line: runs of
+whitespace, including newlines, become one space, and leading or trailing whitespace is dropped);
+a `BaseException` (KeyboardInterrupt/SystemExit) from `fn` propagates. From `log` (a side action
+too: a closed stderr) an ordinary Exception is ignored; a BaseException propagates. Called inside
+an `except` block it leaves the handled exception untouched, so a bare `raise` after it re-raises
+the original.
+No context-manager form: no adopted site needs one (guardrail: no abstraction without a user).
+Test: `tests/core/test_best_effort.py` (result, the exact line, a message with `\n` and `\r\n`
+flattened to one stderr line, BaseException passes, a raising log, a bare `raise` after it
+re-raises the original).
+
+## Sites in FILES
+
+| # | site today | verdict | why |
+|---|---|---|---|
+| S1 | `service_binary_ops.py:420-424` `traceback.print_exc()` in `try/except Exception: pass` after the unwind | REPLACE | diagnostic side action; silent today when it fails |
+| S2 | `service_binary_ops.py:442-450` `except BaseException:` → `binary_recover()` in `try/except Exception: pass`, then `raise` | REPLACE (boundary kept) | cleanup side action; the BaseException boundary and the bare `raise` stay |
+| — | `service_binary_ops.py:427-433` message built with `except Exception` fallback | KEEP | a value with a fallback (a raising `__str__`), not a side action; both arms produce the error |
+| — | `service_binary_ops.py:402` `except Exception as exc` (main unwind boundary) | KEEP | it IS the surrounding operation's contract |
+| — | `service_binary_ops.py:725` `except BaseException: rmtree(ignore_errors=True); raise` | KEEP | already never raises an Exception; nothing to log |
+| S3 | `install.py:1295-1309` `_note_staged`: `rec.rewrite(...)` in `except (OSError, PathContainmentError)` + a False return, one stderr line each | REPLACE | B6 best-effort record; today any OTHER Exception (TypeError, ValueError from the payload) stops the install — the CR3-3b corr2 class |
+| — | `service_maintenance.py` uninstall prep `:2088 :2105 :2123 :2133` | KEEP | fail-CLOSED decisions (an exception → refusal / `hmac_bad=True`), not side actions; no diagnostic exists there at this base |
+| — | `service_maintenance.py :363 :656 :1157 :1188 :1224 :1252 :1320 :1328 :1338 :1505` | KEEP | reads whose exception is a decision value (None / refusal / False); adding a log line would change the task panel's output |
+| — | `service_maintenance.py :2175 :2219 :2228 :2239` uninstall guard | KEEP | typed refusals |
+| — | `config.py:1864` | KEEP | turns a raising recovery into the typed `ConfigRecoveryRequired` (Q1's recovery result) |
+| — | `config.py:1956` rollback boundary | KEEP / NOT TOUCHED | there is no diagnostic line before the unwind at this base (the "RETRO-FU" line does not exist); the rollback lines are Q1's — not shared, so no STOP needed, but nothing to adopt |
+
+## Sites found by grep OUTSIDE FILES (not adopted — other batches' or untouched files)
+
+`except Exception: pass` around a side action: `service_webserver.py:181 :925`, `jobs.py:119`,
+`service_selfupdate.py:1168 :1313`, `probes/backends.py:336`, `build_launcher_runtime.py:150`,
+`services.py:679 :977`, `lifecycle.py:1366`, `service_lifecycle_ops.py:1858 :3865 :5592`,
+`service_firewall.py:914 :938 :952 :967`, `service_network.py:666 :1059 :1065 :1085`,
+`service_hmac.py:253`, `adapters/web/app.py` (13 sites). Recommendation: a later batch adopts the
+helper per owning module; this one stays file-disjoint.
+
+## Changes and tests (one commit each)
+
+1. `Q3: best_effort — one way to run a side action beside a failure` — the helper + its test.
+2. `Q3: binary install — the traceback and the interrupt unwind run best-effort` (S1, S2).
+   Tests (`tests/install/test_binary_channel.py`):
+   (a) S1: `traceback.print_exc` raises RuntimeError → same typed failure (`rolled_back`,
+   `unexpected == "KeyError"`), journal closed, exactly one line
+   `binary install of 'meshcom': printing the traceback failed: RuntimeError: …`. RED before (no line).
+   (b) S2: KeyboardInterrupt after publish and `binary_recover` raises OSError → the SAME
+   KeyboardInterrupt object propagates, recovery was attempted, exactly one line. RED before (no
+   line). The plain Ctrl-C unwind stays proven by `test_ctrl_c_inside_the_transaction_unwinds_now`.
+3. `Q3: the staging record rewrite runs best-effort` (S3). The rewrite runs inside `best_effort`;
+   a `False` return keeps its plain `stderr_line` notice, written outside `best_effort` as the
+   base wrote it outside its `try`, so a raising stderr there still propagates.
+   Tests (`tests/install/test_source.py`):
+   (a) `rewrite` raises RuntimeError → adoption `done`, one line per rewrite naming
+   `RuntimeError` and "install continues". RED before (the RuntimeError stops the adoption).
+   (b) KeyboardInterrupt from `rewrite` → it propagates (never swallowed as best-effort) and the
+   staging record is removed by its context manager. Green before too (the narrow catch did not
+   swallow it) — kept as the guard against widening the helper to BaseException.
+   The existing `test_a_failing_staging_record_rewrite_never_stops_the_install` keeps passing.
+4. CHANGELOG `## 0.12.0` line; docs: no sentence becomes untrue (architecture.md lists modules
+   "not every module is listed"; maintenance.md names no such pattern).
+
+## Risks and how they are ruled out
+
+- A bare `raise` after the helper re-raising the inner error instead of the original: Python 3
+  restores the handled exception when the inner `except` ends; proven by the helper test and S2's
+  identity assertion (`is` the injected interrupt).
+- `traceback.print_exc()` called from inside the helper printing nothing: it reads
+  `sys.exc_info()`, still the outer exception while no inner one is being handled.
+- S3 widening (any Exception no longer stops a staging): the record is evidence for crash
+  recovery only; without an inode recorded, recovery removes the candidate only while empty
+  (`_recover_staged_clone`), so a lost rewrite can never cost data — the documented contract.
+- Log line wording changes: S3's exception arm now reads `…install continues: OSError: …` instead
+  of `…(msg) — install continues`; no test or doc pins the old wording beyond "install continues".
+
+## Open questions (with recommendation)
+
+1. `install.py` is not in the brief's FILES list but `_note_staged` is in its ADOPT list.
+   Recommendation: adopt it in its own commit touching only `_note_staged` (lines 1295-1309); Q1
+   may own `install.py` recovery — disjoint lines, so at worst a textual stack conflict the
+   integrator can resolve by taking both; or drop commit 3.
+2. `tests/repo/test_version_consistent.py::test_changelog_leads_with_the_current_version` is red on
+   the base (CHANGELOG leads 0.11.12, `version.py` says 0.11.11); a `## 0.12.0` heading keeps it red
+   until the release commit bumps the version. Recommendation: leave it to the release commit.
diff --git a/tests/core/test_best_effort.py b/tests/core/test_best_effort.py
new file mode 100644
index 0000000..26ab3f6
--- /dev/null
+++ b/tests/core/test_best_effort.py
@@ -0,0 +1,77 @@
+"""best_effort: a side action beside a failure never replaces it (the helper's own contract; each
+adopting site proves its outcome in its own module)."""
+
+import pytest
+
+from lhpc.core.best_effort import best_effort
+
+
+def test_returns_the_side_actions_result_and_logs_nothing():
+    lines = []
+    assert best_effort(lambda: 7, what="x", log=lines.append) == 7
+    assert lines == []
+
+
+def test_an_ordinary_error_is_one_line_and_none():
+    lines = []
+
+    def boom():
+        raise OSError(5, "I/O error")
+    assert best_effort(boom, what="cleanup of 'a'", log=lines.append) is None
+    assert lines == ["cleanup of 'a': OSError: [Errno 5] I/O error"]
+
+
+
+def test_a_multi_line_message_is_flattened_to_one_stderr_line(capsys):
+    def boom():
+        raise ValueError("first\nsecond\r\n  third\r")
+    assert best_effort(boom, what="x") is None
+    assert capsys.readouterr().err == "x: ValueError: first second third\n"
+
+@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
+def test_a_base_exception_from_the_side_action_propagates(exc):
+    lines = []
+
+    def interrupted():
+        raise exc
+    with pytest.raises(type(exc)) as got:
+        best_effort(interrupted, what="x", log=lines.append)
+    assert got.value is exc and lines == []
+
+
+def test_an_unprintable_error_is_still_one_line():
+    lines = []
+
+    class Unprintable(Exception):
+        def __str__(self):
+            raise RuntimeError("hostile __str__")
+
+    def boom():
+        raise Unprintable()
+    assert best_effort(boom, what="x", log=lines.append) is None
+    assert lines == ["x: Unprintable"]
+
+
+def test_a_failing_log_is_ignored():
+    def boom():
+        raise RuntimeError("side")
+
+    def closed(line):
+        raise ValueError("I/O operation on closed file")
+    assert best_effort(boom, what="x", log=closed) is None
+
+
+@pytest.mark.parametrize("original", [ValueError("main"), KeyboardInterrupt()])
+def test_a_bare_raise_after_it_reraises_the_original(original):
+    lines = []
+
+    def boom():
+        raise RuntimeError("side")
+    with pytest.raises(type(original)) as got:
+        try:
+            raise original
+        except BaseException:
+            best_effort(boom, what="unwind", log=lines.append)
+            raise
+    assert got.value is original
+    assert lines == ["unwind: RuntimeError: side"]
diff --git a/tests/install/test_binary_channel.py b/tests/install/test_binary_channel.py
index 06fdba1..f113545 100644
--- a/tests/install/test_binary_channel.py
+++ b/tests/install/test_binary_channel.py
@@ -1167,6 +1167,98 @@ def test_ctrl_c_inside_the_transaction_unwinds_now(tmp_path, monkeypatch, stub_p
                                      svc._hmac_component("meshcom").id, "password_file") == before
 
 
+def _fail_past_publish(svc, tmp_path, monkeypatch, stub_pipeline, receipt_step):
+    """Stage meshcom's proof and probe files, publish them, and let `receipt_step` replace the
+    receipt builder (the first step past publish). Returns the files this run creates."""
+    import os
+
+    from lhpc.core import binary_install as bi
+    from lhpc.core.install import Installer
+    monkeypatch.setattr(Installer, "adopt_source",
+                        lambda self, comp, **k: type("A", (), {"status": "done", "detail": ""})())
+    stub_pipeline(svc, download=lambda entry, path: None)
+    spec = svc.binary_spec("meshcom")
+    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})
+
+    def stage(tar, stage_dir, roots):
+        for rel in files:
+            os.makedirs(os.path.dirname(os.path.join(stage_dir, rel)), exist_ok=True)
+            with open(os.path.join(stage_dir, rel), "w") as fh:
+                fh.write("staged")
+        return files
+    monkeypatch.setattr(bi, "validate_and_extract", stage)
+    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
+    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])
+    monkeypatch.setattr(bi, "build_receipt", receipt_step)
+    new = [f for f in files if not (tmp_path / f).exists()]
+    assert new
+    return new
+
+
+def test_a_failing_traceback_print_is_one_line_and_changes_no_outcome(tmp_path, monkeypatch,
+                                                                      stub_pipeline, capsys):
+    """The traceback of an unexpected error is a diagnostic beside the failure: when printing it
+    fails, one line says so and the typed, rolled-back failure is returned unchanged."""
+    import traceback
+
+    from lhpc.core import binary_install as bi
+    svc = _svc(tmp_path, monkeypatch=monkeypatch)
+
+    def broken(*a, **k):
+        raise KeyError("components")
+
+    def print_exc(*a, **k):
+        raise RuntimeError("no terminal")
+    new = _fail_past_publish(svc, tmp_path, monkeypatch, stub_pipeline, broken)
+    monkeypatch.setattr(traceback, "print_exc", print_exc)
+    res = svc.binary_install("meshcom", apply=True)
+    assert not res.ok and res.data.get("binary_failed") and res.data.get("rolled_back")
+    assert res.data["unexpected"] == "KeyError"
+    assert bi.read_journal(svc._paths)[1] == "absent"
+    assert not any((tmp_path / f).exists() for f in new)
+    lines = capsys.readouterr().err.splitlines()
+    assert len(lines) == 1 and "binary install of 'meshcom'" in lines[0], lines
+    assert lines[0].endswith(": RuntimeError: no terminal"), lines
+
+
+def test_a_failing_unwind_after_ctrl_c_is_one_line_and_the_interrupt_propagates(
+        tmp_path, monkeypatch, stub_pipeline, capsys):
+    """Ctrl-C past publish runs the unwind; when the unwind itself raises, one line says so and
+    the operator's interrupt — the same object — propagates; the journal stays open for the next
+    command, which restores the previous install."""
+    from lhpc.core import binary_install as bi
+    svc = _svc(tmp_path, monkeypatch=monkeypatch)
+    receipt_before = svc.binary_receipt_state("meshcom")[0]
+    ctrl_c = KeyboardInterrupt()
+    interrupted = []
+    real_recover = ControllerService.binary_recover
+
+    def interrupt(*a, **k):
+        interrupted.append(True)
+        raise ctrl_c
+
+    def recover(self):
+        if interrupted:                       # only the unwind after the Ctrl-C fails
+            raise OSError(5, "I/O error")
+        return real_recover(self)
+    new = _fail_past_publish(svc, tmp_path, monkeypatch, stub_pipeline, interrupt)
+    monkeypatch.setattr(ControllerService, "binary_recover", recover)
+    with pytest.raises(KeyboardInterrupt) as got:
+        svc.binary_install("meshcom", apply=True)
+    assert got.value is ctrl_c
+    lines = capsys.readouterr().err.splitlines()
+    assert len(lines) == 1 and "binary install of 'meshcom'" in lines[0], lines
+    assert lines[0].endswith(": OSError: [Errno 5] I/O error"), lines
+    assert bi.read_journal(svc._paths)[1] != "absent"          # left for the next command
+    monkeypatch.setattr(ControllerService, "binary_recover", real_recover)
+    ok, why = svc.binary_recover()
+    assert ok, why
+    assert bi.read_journal(svc._paths)[1] == "absent"
+    assert not any((tmp_path / f).exists() for f in new)
+    svc.invalidate_snapshot()
+    assert svc.binary_receipt_state("meshcom")[0] == receipt_before
+
+
 def test_committed_transaction_keeps_open_auth(tmp_path, monkeypatch):
     """Past the commit point the NEW install is the truth: recovery must NOT put the password
     back (the installed firmware has none)."""
diff --git a/tests/install/test_source.py b/tests/install/test_source.py
index 7892f12..5f5ea56 100644
--- a/tests/install/test_source.py
+++ b/tests/install/test_source.py
@@ -442,11 +442,12 @@ def test_a_staging_record_never_removes_an_unproven_candidate(tmp_path, installe
     assert (staging / "part").read_text() == "keep" and rec.exists()
 
 
-@pytest.mark.parametrize("failure", ["oserror", "containment", "lost"])
+@pytest.mark.parametrize("failure", ["oserror", "containment", "unexpected", "lost"])
 def test_a_failing_staging_record_rewrite_never_stops_the_install(tmp_path, make_repo, installer,
                                                                   monkeypatch, capsys, failure):
     # The record is best-effort, like the clone log: when giving it the candidate's [dev, ino]
-    # fails, one line says so and the staging goes on (recovery then sees no recorded inode).
+    # fails — any ordinary error, not only a filesystem one — one line says so and the staging
+    # goes on (recovery then sees no recorded inode).
     from lhpc.core import runtime_fs
     make_repo(tmp_path / "rt" / "local" / "app")
     comp = _comp()
@@ -462,6 +463,8 @@ def test_a_failing_staging_record_rewrite_never_stops_the_install(tmp_path, make
             raise OSError(errno.EIO, "I/O error")
         if failure == "containment":
             raise PathContainmentError("swapped")
+        if failure == "unexpected":
+            raise RuntimeError("payload")
         return False
 
     def open_marker(paths, path, text, *a, **kw):   # only the staging record fails, not the journal
@@ -475,9 +478,39 @@ def test_a_failing_staging_record_rewrite_never_stops_the_install(tmp_path, make
     # One line per failed rewrite (this path creates the candidate twice: reset, then the copy).
     assert calls and len(lines) == len(calls), (calls, lines)
     assert all("install continues" in ln for ln in lines), lines
+    tail = {"oserror": ": OSError: [Errno 5] I/O error", "containment": ": PathContainmentError: swapped",
+            "unexpected": ": RuntimeError: payload", "lost": "install continues"}[failure]
+    assert all(ln.endswith(tail) for ln in lines), lines
     assert list(inst.paths.under("state", "source-txn").iterdir()) == []
 
 
+def test_ctrl_c_in_the_staging_record_rewrite_propagates_and_the_record_is_cleared(
+        tmp_path, make_repo, installer, monkeypatch):
+    # Best-effort covers ordinary errors only: Ctrl-C while the record is rewritten stops the
+    # staging (the same interrupt propagates) and the record's own cleanup still runs.
+    from lhpc.core import runtime_fs
+    make_repo(tmp_path / "rt" / "local" / "app")
+    comp = _comp()
+    inst = installer(comp)
+    real_open = runtime_fs.open_marker_excl
+    ctrl_c = KeyboardInterrupt()
+
+    def interrupted_rewrite(text):
+        raise ctrl_c
+
+    def open_marker(paths, path, text, *a, **kw):
+        marker = real_open(paths, path, text, *a, **kw)
+        if path.name.endswith(".staging"):
+            marker.rewrite = interrupted_rewrite
+        return marker
+    monkeypatch.setattr(runtime_fs, "open_marker_excl", open_marker)
+    with pytest.raises(KeyboardInterrupt) as got:
+        inst.adopt_source(comp, source="dev")
+    assert got.value is ctrl_c
+    assert not [p for p in inst.paths.under("state", "source-txn").iterdir()
+                if p.name.endswith(".staging")]
+
+
 def test_a_staging_record_defers_to_its_journal(tmp_path, installer):
     # A crash after the journal was written leaves both: the journal owns the candidate (with its
     # full identity proof), so the record is cleared and never removes the candidate itself.
```

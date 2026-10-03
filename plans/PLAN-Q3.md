# PLAN-Q3 — the best-effort helper

Base: `origin/integration/0.12.0` @ 042716f. One helper, adopted where a side action (cleanup or
diagnostic) sits beside a failure today; every other `except Exception` in FILES is listed with keep.

## The helper — `lhpc/core/best_effort.py` (new, plain function)

`best_effort(fn, *, what, log=stderr_line) -> fn() | None`: runs `fn()`; an ordinary `Exception`
from it is logged as the one line `"<what>: <Class>: <msg>"` through `log` and `None` is returned
(no `: <msg>` when its `__str__` raises an ordinary Exception (a BaseException propagates); the exception text is flattened to one line: runs of
whitespace, including newlines, become one space, and leading or trailing whitespace is dropped);
a `BaseException` (KeyboardInterrupt/SystemExit) from `fn` propagates. From `log` (a side action
too: a closed stderr) an ordinary Exception is ignored; a BaseException propagates. Called inside
an `except` block it leaves the handled exception untouched, so a bare `raise` after it re-raises
the original.
No context-manager form: no adopted site needs one (guardrail: no abstraction without a user).
Test: `tests/core/test_best_effort.py` (result, the exact line, a message with `\n` and `\r\n`
flattened to one stderr line, BaseException passes, a raising log, a bare `raise` after it
re-raises the original).

## Sites in FILES

| # | site today | verdict | why |
|---|---|---|---|
| S1 | `service_binary_ops.py:420-424` `traceback.print_exc()` in `try/except Exception: pass` after the unwind | REPLACE | diagnostic side action; silent today when it fails |
| S2 | `service_binary_ops.py:442-450` `except BaseException:` → `binary_recover()` in `try/except Exception: pass`, then `raise` | REPLACE (boundary kept) | cleanup side action; the BaseException boundary and the bare `raise` stay |
| — | `service_binary_ops.py:427-433` message built with `except Exception` fallback | KEEP | a value with a fallback (a raising `__str__`), not a side action; both arms produce the error |
| — | `service_binary_ops.py:402` `except Exception as exc` (main unwind boundary) | KEEP | it IS the surrounding operation's contract |
| — | `service_binary_ops.py:725` `except BaseException: rmtree(ignore_errors=True); raise` | KEEP | already never raises an Exception; nothing to log |
| S3 | `install.py:1295-1310` `_note_staged`: `rec.rewrite(...)` in `except (OSError, PathContainmentError)` + a False return, one stderr line each | REPLACE | B6 best-effort record; today any OTHER Exception (TypeError, ValueError from the payload) stops the install — the CR3-3b corr2 class |
| — | `service_maintenance.py` uninstall prep `:2088 :2105 :2123 :2133` | KEEP | fail-CLOSED decisions (an exception → refusal / `hmac_bad=True`), not side actions; no diagnostic exists there at this base |
| — | `service_maintenance.py :363 :656 :1157 :1188 :1224 :1252 :1320 :1328 :1338 :1505` | KEEP | reads whose exception is a decision value (None / refusal / False); adding a log line would change the task panel's output |
| — | `service_maintenance.py :2175 :2219 :2228 :2239` uninstall guard | KEEP | typed refusals |
| — | `config.py:1864` | KEEP | turns a raising recovery into the typed `ConfigRecoveryRequired` (Q1's recovery result) |
| — | `config.py:1956` rollback boundary | KEEP / NOT TOUCHED | there is no diagnostic line before the unwind at this base (the "RETRO-FU" line does not exist); the rollback lines are Q1's — not shared, so no STOP needed, but nothing to adopt |

## Sites found by grep OUTSIDE FILES (not adopted — other batches' or untouched files)

`except Exception: pass` around a side action: `service_webserver.py:181 :925`, `jobs.py:119`,
`service_selfupdate.py:1168 :1313`, `probes/backends.py:336`, `build_launcher_runtime.py:150`,
`services.py:679 :977`, `lifecycle.py:1366`, `service_lifecycle_ops.py:1858 :3865 :5592`,
`service_firewall.py:914 :938 :952 :967`, `service_network.py:666 :1059 :1065 :1085`,
`service_hmac.py:253`, `adapters/web/app.py` (13 sites). Recommendation: a later batch adopts the
helper per owning module; this one stays file-disjoint.

## Changes and tests (one commit each)

1. `Q3: best_effort — one way to run a side action beside a failure` — the helper + its test.
2. `Q3: binary install — the traceback and the interrupt unwind run best-effort` (S1, S2).
   Tests (`tests/install/test_binary_channel.py`):
   (a) S1: `traceback.print_exc` raises RuntimeError → same typed failure (`rolled_back`,
   `unexpected == "KeyError"`), journal closed, exactly one line
   `binary install of 'meshcom': printing the traceback failed: RuntimeError: …`. RED before (no line).
   (b) S2: KeyboardInterrupt after publish and `binary_recover` raises OSError → the SAME
   KeyboardInterrupt object propagates, recovery was attempted, exactly one line. RED before (no
   line). The plain Ctrl-C unwind stays proven by `test_ctrl_c_inside_the_transaction_unwinds_now`.
3. `Q3: the staging record rewrite runs best-effort` (S3). The rewrite runs inside `best_effort`;
   a `False` return keeps its plain `stderr_line` notice, written outside `best_effort` as the
   base wrote it outside its `try`, so a raising stderr there still propagates.
   Tests (`tests/install/test_source.py`):
   (a) `rewrite` raises RuntimeError → adoption `done`, one line per rewrite naming
   `RuntimeError` and "install continues". RED before (the RuntimeError stops the adoption).
   (b) KeyboardInterrupt from `rewrite` → it propagates (never swallowed as best-effort) and the
   staging record is removed by its context manager. Green before too (the narrow catch did not
   swallow it) — kept as the guard against widening the helper to BaseException.
   The existing `test_a_failing_staging_record_rewrite_never_stops_the_install` keeps passing.
4. CHANGELOG `## 0.12.0` line; docs: no sentence becomes untrue (architecture.md lists modules
   "not every module is listed"; maintenance.md names no such pattern).

## Risks and how they are ruled out

- A bare `raise` after the helper re-raising the inner error instead of the original: Python 3
  restores the handled exception when the inner `except` ends; proven by the helper test and S2's
  identity assertion (`is` the injected interrupt).
- `traceback.print_exc()` called from inside the helper printing nothing: it reads
  `sys.exc_info()`, still the outer exception while no inner one is being handled.
- S3 widening (any Exception no longer stops a staging): the record is evidence for crash
  recovery only; without an inode recorded, recovery removes the candidate only while empty
  (`_recover_staged_clone`), so a lost rewrite can never cost data — the documented contract.
- Log line wording changes: S3's exception arm now reads `…install continues: OSError: …` instead
  of `…(msg) — install continues`; no test or doc pins the old wording beyond "install continues".

## Open questions (with recommendation)

1. `install.py` is not in the brief's FILES list but `_note_staged` is in its ADOPT list.
   Recommendation: adopt it in its own commit touching only `_note_staged` (lines 1295-1310); Q1
   may own `install.py` recovery — disjoint lines, so at worst a textual stack conflict the
   integrator can resolve by taking both; or drop commit 3.
2. `tests/repo/test_version_consistent.py::test_changelog_leads_with_the_current_version` is red on
   the base (CHANGELOG leads 0.11.12, `version.py` says 0.11.11); a `## 0.12.0` heading keeps it red
   until the release commit bumps the version. Recommendation: leave it to the release commit.

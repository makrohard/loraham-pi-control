# Code report — Q3: the best-effort helper

Base `origin/integration/1.0` @ 042716f. Branch `claude/focused-dijkstra-ivqkwl` (round 1); round 2 on
`claude/friendly-shannon-fh0bca`.
Python 3.11, pytest 9.1, ruff from `.[dev]` in a scratch venv; `zstd` installed in the container.

## Commits

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

## Sites (keep / replace) — as in the plan

Replaced: S1 `service_binary_ops.py` traceback print (silent `except Exception: pass` → one line);
S2 the interrupt unwind's `binary_recover()` (silent → one line; `except BaseException:` boundary and
bare `raise` kept); S3 `install.py::_note_staged` (narrow `(OSError, PathContainmentError)` → any
ordinary Exception; a `False` return keeps its existing line). Kept, with reasons in the plan: the
binary message fallback, the main unwind boundary, the venv `rmtree; raise`, every
`service_maintenance.py` `except Exception` (fail-closed decisions/refusals, task-panel reads), both
`config.py` sites. Outside FILES: listed in the plan, not adopted.

## Simplicity guardrails

- One plain function plus a one-line `stderr_line`; no class, no registry, no result type, no
  context-manager form (no site needs it).
- Line counts: `service_binary_ops.py` 988 → 986; `install.py` 2323 → 2317 (`_note_staged` body
  13 → 5 lines); new `best_effort.py` 38. Production net: +49 / −19 (+30, the helper; the sites
  themselves shrink by 8). Net code grows by the helper only — the price of one tested contract
  replacing three hand-written variants; adopting the 30+ listed sites outside FILES is where it pays.
- Dependencies: `service_binary_ops.py` imports +`best_effort`; self.* used unchanged
  (`binary_recover`). `install.py` imports −`sys`, +`best_effort, stderr_line`; self.* unchanged
  (`_staged_clone_payload`). The helper imports only `sys`, `collections.abc`, `typing`.

## The 6-point block

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
   `tests/core`: `2052 passed, 6 skipped in 303.41s` (round 1; see Correction 1).
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

## Deviations

- `install.py` touched although not in FILES (named in ADOPT). Own commit, disjoint lines.
- No config.py or uninstall-prep change: the "diagnostics-before-unwind" line and the uninstall-prep
  diagnostics the brief names do not exist at this base; their `except Exception` sites are
  decisions, not side actions (plan table).
- No context-manager form (no site needs it).

## Correction 1

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
6-point block above now says exactly that; the plan (S3 change item) says it too. cda4802 (was
a6854ad) is unchanged.

**Wording.** Plan (amended into e2af0c4, was b6af7e9): the flatten rule; the `log` rule made exact
(an ordinary Exception from `log` is ignored, a BaseException propagates — it said "a failing `log`
is swallowed"); the helper test list names the multi-line case; the S3 False-path notice. CHANGELOG
(amended into af75627, was 00c43bb): one added sentence, "In these lines an error message that
spans several lines is joined into one." This report: the commit ids, the helper's line count
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
`tests/install/test_bootstrap_deps.py`, the uid-0 refusal described in point 5, as in round 1.
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
swallowed", untrue for a BaseException from `log` — fixed; (c) this report's commit ids, line counts
and case count went stale with the amend — fixed; (d) the round-1 `tests/core` count — explained
above. Accepted: `what` is not flattened — it is caller text, built by the three callers from fixed
words, a stack id that `binary_available` has already accepted, and the staging record's own file name; an
exception whose `str()` raises still gives `"<what>: <Class>"` (the flattening sits inside the same
`try`).

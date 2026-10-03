# Gate 1 — code review request, batch Q1 (typed outcomes)

Please judge the plan and each code commit below: does the batch reach its completion criterion
(changing a diagnostic message cannot change admission, recovery requirements or success
classification), are behaviour and operator-facing messages otherwise unchanged, are the tests
red-before / green-after and decision-bearing, and is the change the simplest one that does it?

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Answer form:

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| … | … | … |

Final line: GREEN / GREEN WITH NOTES / RED

## The plan (plans/PLAN-Q1.md)

# PLAN-Q1 — typed outcomes for three machine decisions

Goal: no admission, recovery or success decision reads a human sentence. Each site gets one
`str` enum plus the existing message as a separate value; messages and files stay byte-identical.
Completion test per site: reword the message, the decision does not move (red on the parent).

## Q1-1 — job tracking: tracked / terminated / termination_unverified

Today: `ServiceLifecycleOps._track_or_terminate` (lhpc/core/service_lifecycle_ops.py:3975)
returns `""` (tracked) or a sentence; the sentence contains `ORPHAN RISK` when the stop is
unproven. Four callers decide on that wording:
- detached build/test/install: `_settle_track` (service_lifecycle_ops.py:3580-3592) → "orphan";
- detached start/restart: `spawn_start_job` (service_lifecycle_ops.py:3786-3795);
- HMAC apply: service_hmac.py:497-509 (unsafe marker vs ordinary failed);
- auto-install spawn: service_auto_install.py:696-704 (`settle_unproven` vs `settle_gone`).

Change: `class TrackOutcome(str, Enum)` in lhpc/core/jobs.py (beside `JobState`):
TRACKED / TERMINATED / TERMINATION_UNVERIFIED. `_track_or_terminate` returns
`(TrackOutcome, message)`; the message strings are unchanged (TRACKED → ""). Every caller
unpacks and branches on the enum; the message is only shown/persisted, as today.
`_settle_track` returns the enum instead of its private ""/"orphan"/"terminated" codes.

Tests (tests/web/test_webjob.py, tests/web/test_hmac.py, tests/install/test_auto_install.py):
a wrapper keeps the real outcome and replaces the message with a neutral sentence; the
detached job, the detached start, HMAC and auto-install must still land in the blocking
unsafe/orphan-risk state. Red before: on the parent the reworded unverified result is read as
"terminated" (ordinary failed). Existing fakes switch to the tuple shape.

Risk: a missed caller would treat the tuple as truthy (always "error"). Ruled out by grepping
every `_track_or_terminate` use in lhpc/ tests/ testlab/ and running those modules.

## Q1-2 — interactive start: "a usable command was presented"

Today: the start aggregation `blocks()` (service_lifecycle_ops.py:1496-1502) accepts a non-main
interactive MANUAL_REQUIRED only if `r.summary.startswith("interactive —")`.

Change: the `record` helper (service_lifecycle_ops.py:1102) already receives the copy-paste
`command` separately; it adds the component id to a local `presented` set when a command was
given, and `blocks()` checks `r.component in presented`. No other line of `_start_impl_inner`
moves. Simpler than a new `CompResult` field (no change to outcomes.py, its consumers or
equality), and the fact lives where the command is printed.

Test (tests/core/test_post_start.py or the start-aggregation owner): a non-main interactive
sidecar whose summary is reworded (CompResult factory prepends text) still leaves the start ok;
a non-main interactive MANUAL_REQUIRED without a presented command still blocks. Red before:
reworded summary flips ok to False.

Risk: `manual_start_command` returns "(no run command)" rather than "" when it has no argv, so
today that shape is presented and non-blocking — the flag keeps exactly that (any non-empty
command), no behaviour change.

## Q1-3 — config recovery: unnecessary / recovered / blocked

Today: `recover_config_transaction` (lhpc/core/config.py:1792) returns None (no journal), a
note (recovered) or "" (blocked). Callers: `_finish_pending_journal` (config.py:1862, raises
`ConfigRecoveryRequired` on falsy), `set_operator` in service_params.py:1602 (`== ""`).

Change: `class ConfigRecovery(str, Enum)` in config.py: UNNECESSARY / RECOVERED / BLOCKED;
the function returns `(ConfigRecovery, note)`. `_finish_pending_journal` raises unless
RECOVERED (today None after a positive lstat also raised — kept); its own `str | None` return,
`recover_config_journal_at_startup` and the CLI note are unchanged. `ConfigRecoveryRequired`
and `ConfigLockBusy` are raised exactly where and as today. service_params.py:1602 compares the
enum (one line; it is a caller of the changed signature).

Test (tests/core/test_config.py): a wrapper keeps the outcome but blanks the note; a pending
journal is still recovered and the writer proceeds; existing `== ""` assertions become
`[0] is BLOCKED`. Red before: blank note on a recovered journal raised ConfigRecoveryRequired.

## Not in scope (named, not changed)

`_recover_cmd_hint` (service_auto_install.py:42) and the adapters (web app.py:551, CLI
main.py:1220) read `ORPHAN RISK` in the persisted auto-install *recovery reason* to choose the
`--confirm-orphan` hint. That is the auto-install record contract, not the tracking result, and
two of the three sites are adapter files outside this batch. Recommendation: a later batch
gives the orphan-risk record a typed flag the gate returns.

## Docs / CHANGELOG

No doc sentence names these strings or return shapes (grep docs/); architecture.md "Truthful
outcomes" already states ok derives from typed outcomes. CHANGELOG `## 0.12.0`: one line in
operator terms.

## Open questions

None blocking. `service_params.py` is touched for one line (signature caller) — not on any
other batch's do-not-touch list.

## Commits

```
e0bdcc3 Q1: plan — typed outcomes for job tracking, interactive start and config recovery
8a0da1c Q1: job tracking returns a typed outcome; callers stop reading 'ORPHAN RISK'
f841f86 Q1: an interactive sidecar's start is accepted when its command was presented, not by its wording
b84fb18 Q1: config recovery returns a typed verdict (unnecessary / recovered / blocked)
75eb281 Q1: CHANGELOG — decisions no longer depend on message wording
```

## The report (code-review/code-report-Q1.md)

## Code report — batch Q1 (typed outcomes)

Base: `origin/integration/0.12.0` (042716f). Branch: `cons/Q1`. Next version heading: `## 0.12.0`.

### Commits

| # | sha | subject | files |
|---|---|---|---|
| 0 | e0bdcc3 | Q1: plan | plans/PLAN-Q1.md |
| 1 | 8a0da1c | job tracking returns a typed outcome | lhpc/core/jobs.py, service_lifecycle_ops.py, service_hmac.py, service_auto_install.py; tests/web/test_webjob.py, tests/web/test_hmac.py, tests/install/test_auto_install.py, tests/core/test_process_ownership.py |
| 2 | f841f86 | interactive sidecar accepted when its command was presented | service_lifecycle_ops.py; tests/stacks/test_voice_stack.py |
| 3 | b84fb18 | config recovery returns a typed verdict | lhpc/core/config.py, service_params.py; tests/core/test_config.py |
| 4 | 75eb281 | CHANGELOG | CHANGELOG.md |

### Tests per commit (red-before against the commit's parent)

Venv: `python -m venv $SCRATCH/venv && pip install -e .[dev]`. Red-before ran in a scratch
worktree of the parent commit with the new test files copied in.

**Commit 1** (parent e0bdcc3). New/changed decision tests:
- `tests/web/test_webjob.py::test_spawn_web_job_orphan_primary_blocks[as-shipped|reworded]` (now also asserts the reservation is `unsafe`)
- `tests/web/test_webjob.py::test_spawn_start_job_orphan_and_terminated_are_typed[as-shipped|reworded]`
- `tests/web/test_hmac.py::test_unverified_tracking_stays_blocking_unsafe_whatever_its_message_says`
- `tests/install/test_auto_install.py::test_unverified_tracking_is_orphan_risk_whatever_its_message_says`
Red before: yes. On the parent `_track_or_terminate` returns a bare string, so the fakes were
converted to that shape for the red run (`(TrackOutcome.TERMINATION_UNVERIFIED, msg)` → `msg`;
the transform script is mechanical). Command:
`python -m pytest -q -p no:cacheprovider tests/web/test_webjob.py tests/web/test_hmac.py tests/install/test_auto_install.py -k "whatever_its_message or orphan_primary or orphan_and_terminated"`
→ parent: `4 failed, 2 passed` (the 4 reworded cases fail: `assert 'failed' == 'unsafe'`; the 2
as-shipped cases pass); this commit: `6 passed`.
Existing fakes in those modules and test_process_ownership.py moved to the tuple shape (no
assertion weakened; `test_untracked_job_spawn_is_terminated_not_orphaned` now asserts `TrackOutcome.TERMINATED`).

**Commit 2** (parent 8a0da1c). `tests/stacks/test_voice_stack.py::test_lite_voice_start_ok_does_not_depend_on_the_sidecar_wording`
— every start `CompResult` summary is prefixed "reworded: " via the module's `CompResult` name.
Red before: yes — parent: `1 failed` (`ActionResult(ok=False, summary="Run for 'voice': manual
start required for loraham-voice-cli …")`); this commit: passed.
Command: `python -m pytest -q -p no:cacheprovider tests/stacks/test_voice_stack.py -k wording`.

**Commit 3** (parent f841f86). `tests/core/test_config.py::test_a_recovered_journal_admits_the_writer_whatever_its_note_says[|reworded]`
— the real recovery runs, a wrapper keeps the verdict and replaces the note with "" or "reworded".
Red before: yes for `[]` (parent raised `ConfigRecoveryRequired` on a recovered journal whose note
is empty); `[reworded]` passes on the parent too (a non-empty note was already accepted) and is
kept as the guard for the other direction. Parent-shaped wrapper `lambda p: (real(p) and note)`.
Command: `python -m pytest -q -p no:cacheprovider tests/core/test_config.py -k whatever_its_note`
→ parent: `1 failed, 1 passed`; this commit: `2 passed`. Five `== ""` assertions became
`[0] is ConfigRecovery.BLOCKED` (same cases, typed).

### Line counts and dependencies (production)

| file | before | after |
|---|---|---|
| lhpc/core/jobs.py | 338 | 347 (+ enum) |
| lhpc/core/config.py | 2151 | 2161 (+ enum, tuple returns) |
| lhpc/core/service_lifecycle_ops.py | 6047 | 6053 |
| lhpc/core/service_hmac.py | 1016 | 1016 |
| lhpc/core/service_auto_install.py | 1870 | 1870 |
| lhpc/core/service_params.py | 3527 | 3527 |

Net +25 production lines (`git diff --numstat 042716fe 75eb281f -- lhpc`: +77 / −52 over 6 files):
two enums with docstrings (17) and wrapped tuple returns. No
extraction, so the "no net growth" rule for extractions does not apply; nothing smaller reaches
the criterion (the alternative, a field on `CompResult`, would also touch outcomes.py and every
equality/consumer of it). Dependencies: no new import anywhere except `enum.Enum` in config.py
(stdlib). service_hmac/auto_install/lifecycle_ops already imported `jobs`; service_params already
imported `config`. No new `self.*` method used by any mixin.

### Simplicity guardrails

- One enum + one message per site, returned as a plain 2-tuple; no wrapper class, no hierarchy,
  no generic result type, no registry.
- Interactive flag: a local `set` of component ids filled by the existing `record` helper — no
  new type; `_start_impl_inner`'s flow is unchanged (2 lines in the helper, 1 in `blocks()`).
- `_settle_track`'s private ""/"orphan"/"terminated" codes are removed in favour of the enum (one
  vocabulary instead of two).

Note on shas: the red-before runs for commits 2 and 3 were made against their parents before the
return annotation `-> tuple[jobs.TrackOutcome, str]` was folded into commit 1 (self-review item 1
below); the trees differ only by that annotation (`from __future__ import annotations`, no runtime
effect), so the red/green results stand for the final shas.

### The 6-point block

**1. Contracts touched (owner file:line)**
- `LifecycleOpsMixin._track_or_terminate(life, log_name, pid, cid, op, attempt_id="", ident=None)
  -> tuple[jobs.TrackOutcome, str]` (lhpc/core/service_lifecycle_ops.py:3977). TRACKED ⇔ the `.job`
  marker was written, message ""; TERMINATED ⇔ marker not written and `_terminate_unobserved`
  proved cessation; TERMINATION_UNVERIFIED ⇔ marker not written and cessation unproven. Messages
  byte-identical to before. Enum owner: lhpc/core/jobs.py:38.
- `_settle_track(log, aid, pid, tracked) -> TrackOutcome` (service_lifecycle_ops.py:3583):
  UNVERIFIED → attempt terminalized `unsafe` (detail text unchanged, driver_ident captured);
  TERMINATED → `failed` with the message[:200]; TRACKED → nothing written. Same persisted
  jobresult states and details as before.
- Callers keep their observable results: spawn_web_job / spawn_start_job return the same
  `(None, "blocked", reason)` strings; HMAC apply returns `ActionResult(False, err, …)` with the
  unchanged message and writes the same `unsafe`/`failed` marker; auto-install reaches
  `settle_unproven`/`settle_gone` exactly as before (service_auto_install.py:696-704,
  service_hmac.py:497-509).
- `config.recover_config_transaction(paths) -> tuple[ConfigRecovery, str]` (config.py:1800);
  `ConfigRecovery` (config.py:1793): UNNECESSARY = no journal (lexists false); RECOVERED = every
  pre-image restored and the journal unlinked, note "recovered a pending config transaction (N
  file(s))"; BLOCKED = journal kept (unlocatable, unreadable, wrong schema, duplicate / unknown
  target, restore or unlink failed), note "". Fail-closed rules unchanged.
- `_finish_pending_journal(paths) -> str | None` (config.py:1854): unchanged contract — None only
  on ENOENT/ENOTDIR of the lstat; otherwise returns the note only for RECOVERED and raises
  `ConfigRecoveryRequired` (reason "recovery-required", same text) for BLOCKED or UNNECESSARY-
  after-a-positive-lstat (as before: None after a positive lstat also raised); `ConfigLockBusy`
  re-raised untouched; any other exception wrapped as `ConfigRecoveryRequired` with `__cause__`.
  Lock order unchanged: `_config_flock` then journal finish, inside `config_lock` (config.py:76).
- `recover_config_journal_at_startup` and the CLI note (adapters/cli/main.py:1027): unchanged.
- `ParamsConfigMixin.set_operator_identity` (service_params.py:1602): refuses on BLOCKED, same
  message; one line changed.
- Start aggregation `blocks(r)` (service_lifecycle_ops.py:1495-1510): a non-main interactive
  MANUAL_REQUIRED is non-blocking iff its component is in `presented` — filled by `record()`
  (service_lifecycle_ops.py:1102) whenever a copy-paste `command` is passed. `manual_start_command`
  never returns "" ("(no run command)" at worst), so the accepted set is exactly the set whose
  summary began "interactive —" before.

**2. Invariants (docs/architecture.md) and the test that holds each**
- Truthful outcomes (`ok` derives from typed outcomes): tests/stacks/test_voice_stack.py (whole
  module, incl. the new wording test, the blocked-pre-steps and desktop cases); tests/core/test_post_start.py (in the tests/core run).
- Detached web jobs (untrackable child terminated, or the attempt marked unsafe when its stop is
  unproven): tests/web/test_webjob.py orphan/terminated cases (both wordings) +
  tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned (real
  session leader, real marker failure → TERMINATED, process gone).
- HMAC orphan-risk blocking: tests/web/test_hmac.py orphan / fallback / terminated cases + new
  reworded case.
- Auto-install orphan-risk record requiring confirmed recovery: tests/install/test_auto_install.py
  orphan-risk cases + new reworded case (real child process).
- Config as a transaction (journal finished under the lock before any writer, or the lock is
  refused): tests/core/test_config.py — all recovery/blocking cases, the EIO/EACCES lstat case, the
  raising-recovery case, and the new note-mutation case.

**3. Known failure classes**
- Fakes with the real signature: every fake of `_track_or_terminate` now returns the real tuple
  shape; the HMAC/web fakes keep the real positional signature. Not autospec'd (they are
  replacement functions on the class, as before). The new config wrapper calls the real function.
- EIO/EACCES/ENOTDIR probes: the lstat classification in `_finish_pending_journal` is untouched;
  `test_a_journal_path_that_cannot_be_examined_refuses_the_writer[EIO|EACCES]` green.
- KeyboardInterrupt through cleanup: no try/except added or changed; tuple unpacking happens
  where the string was assigned, inside the same `settle_on_raise` / try blocks as before.
- Same decision across CLI / web / detached job / boot-restore: the start aggregation is the one
  `start()` path all four use; config recovery is the one `config_lock`; job tracking is the one
  `_track_or_terminate` used by the web spawners, HMAC and auto-install.
- Stacked-only conflicts: none of install.py, service_binary_ops.py, runtime_fs.py,
  service_system.py, pki.py, web static touched. Outside the named FILES I touched one line of
  service_params.py (a caller of the changed signature) — on no other batch's list I was given.

**4. Test rules**
Red-before shown per new test above (the `[reworded]` config case is a non-flipping guard, said
so). No new startswith-only assertion on a decision string (the voice test's `startswith("reworded: ")`
only proves the mutation took effect; the decision asserted is `res.ok`). No unchecked error
return; no network.

**5. Whole test directories** (foreground unless noted; `python -m pytest -q -p no:cacheprovider`)
- `tests/core tests/repo tests/cli/test_cli.py` → `1 failed, 2548 passed, 11 skipped in 361.49s`.
  The failure is `tests/repo/test_version_consistent.py::test_changelog_leads_with_the_current_version`
  (`('0.12.0', '0.11.11')`); it fails identically on the base (`('0.11.12', '0.11.11')`): the base's
  CHANGELOG already leads with an unreleased heading; the release commit fixes the number.
- `tests/stacks` → `1248 passed in 129.01s`.
- `tests/web` → `1010 passed in 270.14s`.
- `tests/install` → `97 failed, 1107 passed, 16 skipped in 135.83s`; the identical 97 node ids
  fail on the base (`diff` of the sorted FAILED lists: identical). Cause: this container runs as
  uid 0 (92 × test_bootstrap_deps "no non-root operator…", plus permission checks that never fail for root in
  test_binary_install / test_binary_channel / test_install).
- After the fixup: the six changed modules → `451 passed, 1 skipped`.
- `ruff check lhpc testlab` → All checks passed; `ruff check tests --select F,E9` → All checks passed.
- grep tests/ + testlab/ for `_track_or_terminate`, `recover_config_transaction`: only modules in
  the directories above; testlab uses neither.

**6. Adversarial self-review**
1. `_track_or_terminate` had lost its return annotation → added `-> tuple[jobs.TrackOutcome, str]`
   (folded into commit 1).
2. Checked every `results.append` / `CompResult(` in `_start_impl_inner`: only `record()` can
   produce a MANUAL_REQUIRED for a non-main interactive component, so `presented` covers exactly the
   old accepted set.
3. Checked `_finish_pending_journal`'s old falsy test also caught None after a positive lstat;
   kept by `is not RECOVERED` (not `is BLOCKED`).
4. The voice wording test patches the module's `CompResult` name — a seam for the mutation, not
   a pin on statement order; stated here.
Docs: no sentence in docs/ names these strings or return shapes; architecture.md's "Truthful
outcomes" and "Detached web jobs" stay true. This report and the plan describe exactly the diff.

### Deviations

- The first directory run (`tests/core tests/repo tests/cli/test_cli.py`) was moved to the
  background by the tool's 2-minute default timeout; I waited for it to finish before running
  anything else; the later runs used an explicit 10-minute foreground limit.
- Plan said the interactive test goes in test_post_start.py "or the start-aggregation owner": it
  went to tests/stacks/test_voice_stack.py, the owner of the only non-main interactive sidecar.
- Not changed (named in the plan): `_recover_cmd_hint` (service_auto_install.py:42) and the web/CLI
  adapters still read "ORPHAN RISK" in the persisted auto-install *recovery reason* to pick the
  `--confirm-orphan` hint. That is the record contract, not the tracking result; recommended for a
  later batch.

## Full diff of the code commits (plan and review files excluded)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 2d217a3..592a376 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,12 @@
 # Changelog
 
+## 0.12.0
+
+- Whether a job, an HMAC apply or an auto-install whose stop could not be proven is treated as unsafe, whether
+  a start with a terminal-only part (the Voice terminal variant) counts as successful, and whether a half-finished
+  configuration save blocks the next one are now decided from a recorded fact, never from the wording of the
+  message you see. The messages themselves are unchanged.
+
 ## 0.11.12
 
 - Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
diff --git a/lhpc/core/config.py b/lhpc/core/config.py
index f737f27..c878d7c 100644
--- a/lhpc/core/config.py
+++ b/lhpc/core/config.py
@@ -24,6 +24,7 @@ import re
 import tomllib
 from contextlib import contextmanager
 from dataclasses import dataclass, field, replace
+from enum import Enum
 from pathlib import Path
 
 from .assets import asset_path
@@ -1789,10 +1790,18 @@ def _resolve_journal_target(paths: Paths, rec) -> Path:
     return p
 
 
-def recover_config_transaction(paths: Paths) -> str | None:
-    """Recover a pending config journal. Returns a message if it restored cleanly,
-    None if there was NO journal, or "" if recovery is required but could not complete
-    (journal retained — caller must block). A journal that EXISTS but is malformed,
+class ConfigRecovery(str, Enum):
+    """The verdict of `recover_config_transaction`; callers decide on it, never on the note."""
+    UNNECESSARY = "unnecessary"     # no journal
+    RECOVERED = "recovered"         # the journal was rolled back and removed
+    BLOCKED = "blocked"             # a journal is pending and could not be finished — kept
+
+
+def recover_config_transaction(paths: Paths) -> tuple[ConfigRecovery, str]:
+    """Recover a pending config journal. Returns `(ConfigRecovery, note)`: RECOVERED with a note
+    if it restored cleanly, UNNECESSARY if there was NO journal, or BLOCKED if recovery is
+    required but could not complete (journal retained — caller must block); the note is "" for
+    the latter two. A journal that EXISTS but is malformed,
     unreadable, wrong-schema, duplicate, or names a non-allowlisted target is NEVER
     treated as absent — it blocks (fail-closed)."""
     from . import runtime_fs
@@ -1802,30 +1811,30 @@ def recover_config_transaction(paths: Paths) -> str | None:
         # The journal's OWN location escapes the runtime root (e.g. a journal symlink
         # whose target leaves the root): a pending journal that cannot be safely located
         # is recovery-required, never absent and never an uncaught containment exception.
-        return ""
+        return ConfigRecovery.BLOCKED, ""
     # Presence is decided WITHOUT following the leaf: ANY directory entry at the journal
     # path -- a regular file, OR a symlink (including a dangling or escaping one) -- is a
     # pending journal that must be recovered/blocked. `Path.exists()` follows the link and
     # would report a dangling-symlink journal as absent; `os.path.lexists` does not.
     if not os.path.lexists(jp):
-        return None
+        return ConfigRecovery.UNNECESSARY, ""
     try:
         journal = runtime_fs.loads_json(runtime_fs.read_text(paths, jp))   # no-follow read
     except (OSError, ValueError, PathContainmentError):
-        return ""                       # exists but unreadable/symlinked/malformed -> BLOCK
+        return ConfigRecovery.BLOCKED, ""  # exists but unreadable/symlinked/malformed -> BLOCK
     if (not isinstance(journal, dict) or journal.get("version") != _JOURNAL_VERSION
             or not isinstance(journal.get("targets"), list) or not journal["targets"]):
-        return ""                       # wrong schema -> BLOCK
+        return ConfigRecovery.BLOCKED, ""  # wrong schema -> BLOCK
     resolved, seen = [], set()
     try:
         for rec in journal["targets"]:
             p = _resolve_journal_target(paths, rec)
             if str(p) in seen:
-                return ""               # duplicate target -> BLOCK
+                return ConfigRecovery.BLOCKED, ""  # duplicate target -> BLOCK
             seen.add(str(p))
             resolved.append((p, rec))
     except ConfigError:
-        return ""                       # unknown/escaping/symlink target -> BLOCK
+        return ConfigRecovery.BLOCKED, ""  # unknown/escaping/symlink target -> BLOCK
     for p, rec in resolved:
         try:
             if rec.get("existed"):
@@ -1833,12 +1842,13 @@ def recover_config_transaction(paths: Paths) -> str | None:
             else:
                 runtime_fs.unlink(paths, p)           # descriptor-anchored, no-follow
         except (OSError, PathContainmentError):
-            return ""                   # recovery FAILED -> keep journal, BLOCK
+            return ConfigRecovery.BLOCKED, ""  # recovery FAILED -> keep journal, BLOCK
     try:
         runtime_fs.unlink(paths, jp)
     except (OSError, PathContainmentError):
-        return ""                       # journal could not be removed -> recovery-required
-    return f"recovered a pending config transaction ({len(resolved)} file(s))"
+        return ConfigRecovery.BLOCKED, ""  # journal could not be removed -> recovery-required
+    return (ConfigRecovery.RECOVERED,
+            f"recovered a pending config transaction ({len(resolved)} file(s))")
 
 
 def _finish_pending_journal(paths: Paths) -> str | None:
@@ -1858,12 +1868,12 @@ def _finish_pending_journal(paths: Paths) -> str | None:
     except OSError as exc:              # EACCES/EIO/...: presence unknown -> never "absent"
         raise ConfigRecoveryRequired(f"{refusal} (journal unreadable: {exc})") from exc
     try:
-        note = recover_config_transaction(paths)
+        outcome, note = recover_config_transaction(paths)
     except ConfigLockBusy:
         raise                           # already the typed refusal (a busy lock / recovery-required)
     except Exception as exc:            # a recovery that RAISES could not finish it either
         raise ConfigRecoveryRequired(f"{refusal} ({exc})") from exc
-    if not note:
+    if outcome is not ConfigRecovery.RECOVERED:     # the lstat saw a journal: it must be finished
         raise ConfigRecoveryRequired(refusal)
     return note
 
diff --git a/lhpc/core/jobs.py b/lhpc/core/jobs.py
index c79244b..5111522 100644
--- a/lhpc/core/jobs.py
+++ b/lhpc/core/jobs.py
@@ -35,6 +35,15 @@ class JobState(str, Enum):
     TIMEOUT = "timeout"
 
 
+class TrackOutcome(str, Enum):
+    """What publishing a spawned job's tracking marker achieved. Callers decide on this, never on
+    the accompanying message: TERMINATION_UNVERIFIED (no marker, stop unproven) is a blocking
+    unsafe state; TERMINATED (no marker, stop proven) is an ordinary failure."""
+    TRACKED = "tracked"
+    TERMINATED = "terminated"
+    TERMINATION_UNVERIFIED = "termination_unverified"
+
+
 @dataclass
 class JobResult:
     name: str
diff --git a/lhpc/core/service_auto_install.py b/lhpc/core/service_auto_install.py
index 1e17f49..92e3001 100644
--- a/lhpc/core/service_auto_install.py
+++ b/lhpc/core/service_auto_install.py
@@ -693,11 +693,11 @@ class AutoInstallOpsMixin:
                              and ai_mod.bind_reservation(self._paths, run_id, pid,
                                                            child_ident, "spawned"))
                     if bound:
-                        err = self._track_or_terminate(life, ln, pid, "all",
-                                                       self.AUTO_INSTALL_OP)
-                        if not err:
+                        tracked, err = self._track_or_terminate(life, ln, pid, "all",
+                                                                self.AUTO_INSTALL_OP)
+                        if tracked is jobs.TrackOutcome.TRACKED:
                             return ln, None
-                        if "ORPHAN RISK" in err:
+                        if tracked is jobs.TrackOutcome.TERMINATION_UNVERIFIED:
                             return None, settle_unproven(
                                 pid, child_ident,
                                 "job tracking failed and cessation is unproven")
diff --git a/lhpc/core/service_hmac.py b/lhpc/core/service_hmac.py
index 55babf3..0349468 100644
--- a/lhpc/core/service_hmac.py
+++ b/lhpc/core/service_hmac.py
@@ -494,12 +494,12 @@ class HmacOpsMixin:
                 # Capture the driver identity at the SAME instant `_track_or_terminate` does (reuse-proof).
                 from . import procident
                 ident = procident.proc_identity(pid)
-                err = self._track_or_terminate(life, ln, pid, stack_id, "hmac-apply")
-                if err:
+                tracked, err = self._track_or_terminate(life, ln, pid, stack_id, "hmac-apply")
+                if tracked is not jobs.TrackOutcome.TRACKED:
                     # Mirror the auto-install spawn path (service_auto_install.py): an UNPROVEN-cessation tracking failure
-                    # ("ORPHAN RISK") means the driver MIGHT still be building — a BLOCKING unsafe state, never
+                    # (TERMINATION_UNVERIFIED) means the driver MIGHT still be building — a BLOCKING unsafe state, never
                     # an ordinary retryable `failed`. A proven-terminated failure stays ordinary `failed`.
-                    if "ORPHAN RISK" in err:
+                    if tracked is jobs.TrackOutcome.TERMINATION_UNVERIFIED:
                         self._hmac_mark_unsafe_orphan(
                             marker, ident,
                             "the apply driver could not be identity-tracked and its stop is UNPROVEN — it "
diff --git a/lhpc/core/service_lifecycle_ops.py b/lhpc/core/service_lifecycle_ops.py
index 20cb162..cbded87 100644
--- a/lhpc/core/service_lifecycle_ops.py
+++ b/lhpc/core/service_lifecycle_ops.py
@@ -1099,9 +1099,12 @@ class LifecycleOpsMixin:
         daemon_ok = True                # gate dependents on verified daemon readiness
         daemon_gate = ""                # a refusal of THIS stack's daemon config (not a daemon failure)
 
+        presented: set[str] = set()      # components whose copy-paste start command was shown
         def record(comp, stack, outcome, summary, command="", note=""):
             # A copy-paste `command` goes on a line of its own, and the `note` on the next one:
             # a note appended to the command made the pasted line a shell syntax error (F-C2).
+            if command:
+                presented.add(comp.id)
             results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                                       outcome=outcome,
                                       summary=f"{summary} {command}" if command else summary))
@@ -1495,7 +1498,7 @@ class LifecycleOpsMixin:
                 return False          # optional: a manual/headless skip is an accepted outcome
             if r.outcome == Outcome.MANUAL_REQUIRED \
                     and r.component in nonmain_interactive_ids \
-                    and (r.summary or "").startswith("interactive —"):
+                    and r.component in presented:
                 # interactive sidecar whose command WAS presented: that IS the outcome.
                 # Other MANUAL_REQUIRED shapes (no marker/command presented) still block
                 # like any failure.
@@ -3577,20 +3580,19 @@ class LifecycleOpsMixin:
             runtime_fs.ensure_dir(self._paths, post_dir)
             index_lock = str(reslock.lock_file_path(self._paths, self._installer()._index_key()))
 
-            def _settle_track(log, aid, pid, terr) -> str:
-                """Turn a `_track_or_terminate` outcome into a terminal reservation + a TYPED code the orchestrator
-                trusts: "" tracked-ok; "orphan" (ORPHAN RISK ⇒ blocking `unsafe`); "terminated" (proven-terminated
-                ⇒ ordinary `failed`). Never inferred later from free text."""
-                if not terr:
-                    return ""
-                if "ORPHAN RISK" in terr:
+            def _settle_track(log, aid, pid, tracked):
+                """Turn a `_track_or_terminate` result into a terminal reservation and return its
+                `TrackOutcome`: TERMINATION_UNVERIFIED ⇒ blocking `unsafe`; TERMINATED ⇒ ordinary
+                `failed`; TRACKED ⇒ nothing to settle."""
+                outcome, terr = tracked
+                if outcome is jobs.TrackOutcome.TERMINATION_UNVERIFIED:
                     jobresult.terminalize(self._paths, log, aid, "unsafe",
                                           detail="the job could not be identity-tracked and its stop is "
                                                  "UNPROVEN — inspect processes (ps) then Recover",
                                           driver_ident=procident.proc_identity(pid))
-                    return "orphan"
-                jobresult.terminalize(self._paths, log, aid, "failed", detail=terr[:200])
-                return "terminated"
+                elif outcome is jobs.TrackOutcome.TERMINATED:
+                    jobresult.terminalize(self._paths, log, aid, "failed", detail=terr[:200])
+                return outcome
 
             def _spawn_install():
                 name = f"install-{target}"
@@ -3664,8 +3666,8 @@ class LifecycleOpsMixin:
                 `.job` tracking marker. The child's `verify_tracked` gate passes only once the marker
                 exists, so by the time it takes task admission itself the parent no longer holds the
                 flock (the parent held admission across the handshake, so the child saw
-                it as an external holder). Returns (log, aid, outcome) — outcome "" | "orphan" |
-                "terminated", or the spawn error when log is None."""
+                it as an external holder). Returns (log, aid, outcome) — outcome a `TrackOutcome`,
+                or the spawn error when log is None."""
                 log, aid, spawned = spawn_fn()
                 if log is None:
                     adm_stack.close()
@@ -3700,10 +3702,10 @@ class LifecycleOpsMixin:
             if plog is None:                        # reserve/spawn/render failed
                 self.prune_logs()
                 return None, "blocked", f"blocked — {pout}"
-            if pout == "orphan":                    # PROVEN: tracking failed, cessation UNPROVEN → blocking unsafe
+            if pout is jobs.TrackOutcome.TERMINATION_UNVERIFIED:   # cessation UNPROVEN → blocking unsafe
                 self.prune_logs()
                 return None, "blocked", "blocked — the job could not be tracked; Recover it first"
-            if pout == "terminated":                # PROVEN: driver terminated before it ran → ordinary failed
+            if pout is jobs.TrackOutcome.TERMINATED:   # PROVEN: driver terminated before it ran → ordinary failed
                 self.prune_logs()
                 return None, "blocked", "blocked — the job process was terminated before it ran"
             admission, reason = self._web_admit_handshake(plog, paid)
@@ -3783,10 +3785,10 @@ class LifecycleOpsMixin:
                     return None, "blocked", f"could not start the {op} of '{target}'"
             ident = procident.proc_identity(pid)                   # capture FIRST
             _adm.close()                                           # release admission
-            terr = self._track_or_terminate(life, ln, pid, target, op, attempt_id=aid,
-                                            ident=ident)           # then publish
-            if terr:
-                if "ORPHAN RISK" in terr:
+            tracked, terr = self._track_or_terminate(life, ln, pid, target, op, attempt_id=aid,
+                                                     ident=ident)  # then publish
+            if tracked is not jobs.TrackOutcome.TRACKED:
+                if tracked is jobs.TrackOutcome.TERMINATION_UNVERIFIED:
                     jobresult.terminalize(self._paths, log, aid, "unsafe",
                                           detail="the job could not be identity-tracked and its "
                                                  "stop is UNPROVEN — inspect processes (ps) then "
@@ -3973,11 +3975,13 @@ class LifecycleOpsMixin:
         return frozenset(names)
 
     def _track_or_terminate(self, life, log_name: str, pid: int, cid: str, op: str,
-                            attempt_id: str = "", ident: dict | None = None) -> str:
+                            attempt_id: str = "",
+                            ident: dict | None = None) -> tuple[jobs.TrackOutcome, str]:
         """Persist a job marker; if it cannot be persisted, terminate the (identity-
-        verified) spawned session so it never leaks as an untracked orphan. Returns ""
-        on success, else a visible error describing the outcome (the literal 'ORPHAN RISK'
-        marks the unproven-cessation case). `ident` is the identity the caller captured
+        verified) spawned session so it never leaks as an untracked orphan. Returns
+        `(jobs.TrackOutcome, message)`: TRACKED with "", else TERMINATED or
+        TERMINATION_UNVERIFIED with a visible error for the operator — callers decide on the
+        outcome, never on the message. `ident` is the identity the caller captured
         IMMEDIATELY after the spawn (the web spawners capture it while they still hold task
         admission, release admission, then publish through here — so the child's own admission
         never sees the parent as an external holder)."""
@@ -3986,13 +3990,15 @@ class LifecycleOpsMixin:
         if ident is None:
             ident = procident.proc_identity(pid)
         if jobs.write_job_marker(self._paths, log_name, pid, cid, op, ident=ident, attempt_id=attempt_id):
-            return ""
+            return jobs.TrackOutcome.TRACKED, ""
         killed = life._terminate_unobserved(pid, ident)
         if killed:
-            return (f"{op} '{cid}' spawned but its job marker could not be persisted; "
-                    "the process was terminated (not left orphaned).")
-        return (f"{op} '{cid}' spawned but its job marker could not be persisted AND the "
-                "process could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")
+            return jobs.TrackOutcome.TERMINATED, (
+                f"{op} '{cid}' spawned but its job marker could not be persisted; "
+                "the process was terminated (not left orphaned).")
+        return jobs.TrackOutcome.TERMINATION_UNVERIFIED, (
+            f"{op} '{cid}' spawned but its job marker could not be persisted AND the "
+            "process could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")
 
     def active_jobs(self, cleanup: bool = True, *, include_unsafe: bool = False) -> list[dict]:
         """Build/test jobs whose ORIGINAL process is still alive (identity-verified).
diff --git a/lhpc/core/service_params.py b/lhpc/core/service_params.py
index 0de81fa..0fec3b1 100644
--- a/lhpc/core/service_params.py
+++ b/lhpc/core/service_params.py
@@ -1599,7 +1599,7 @@ class ParamsConfigMixin:
                 # transaction may have left local.toml partially written — reading/patching
                 # it before recovery would resurrect rolled-back data or drop restored keys.
                 # Everything below reads the RECOVERED state.
-                if _config.recover_config_transaction(self._paths) == "":
+                if _config.recover_config_transaction(self._paths)[0] is _config.ConfigRecovery.BLOCKED:
                     return ActionResult(False, "a pending configuration transaction could "
                                         "not be recovered — nothing was changed "
                                         "(journal retained; see lhpc doctor)")
diff --git a/tests/core/test_config.py b/tests/core/test_config.py
index 910af88..68f4ffb 100644
--- a/tests/core/test_config.py
+++ b/tests/core/test_config.py
@@ -40,7 +40,7 @@ def test_deeply_nested_config_journal_blocks(tmp_path):
     paths = _paths(tmp_path)
     (tmp_path / "state").mkdir()
     cfgmod._txn_journal(paths).write_text("[" * 3000)
-    assert cfgmod.recover_config_transaction(paths) == ""             # BLOCK, no exception
+    assert cfgmod.recover_config_transaction(paths)[0] is cfgmod.ConfigRecovery.BLOCKED             # BLOCK, no exception
 
 
 def test_non_utf8_pre_image_is_a_typed_refusal(tmp_path):
@@ -660,7 +660,7 @@ def test_rollback_failure_retains_journal_and_blocks_later(tmp_path, monkeypatch
 
 
 def test_symlinked_config_txn_journal_blocks_recovery(tmp_path):
-    # A symlinked transaction journal must not be read/followed -> recovery BLOCKS ("").
+    # A symlinked transaction journal must not be read/followed -> recovery BLOCKS.
     import os
     from lhpc.core import config as cfgmod
     from lhpc.core.paths import Paths
@@ -669,7 +669,7 @@ def test_symlinked_config_txn_journal_blocks_recovery(tmp_path):
     outside = tmp_path / "evil.json"
     outside.write_text('{"version": 1, "targets": [{"kind": "local", "rel": "x", "pre": "P", "existed": true, "mode": 420}]}')
     os.symlink(outside, cfgmod._txn_journal(paths))     # symlinked journal
-    assert cfgmod.recover_config_transaction(paths) == ""   # blocked, never followed
+    assert cfgmod.recover_config_transaction(paths)[0] is cfgmod.ConfigRecovery.BLOCKED   # blocked, never followed
 
 
 def test_dangling_internal_journal_symlink_blocks_not_absent(tmp_path):
@@ -684,7 +684,7 @@ def test_dangling_internal_journal_symlink_blocks_not_absent(tmp_path):
     # target stays inside the root (so _txn_journal/under does not raise) but does NOT exist
     os.symlink(tmp_path / "state" / "ghost.json", cfgmod._txn_journal(paths))
     assert not (tmp_path / "state" / "ghost.json").exists()          # genuinely dangling
-    assert cfgmod.recover_config_transaction(paths) == ""            # BLOCK, not None
+    assert cfgmod.recover_config_transaction(paths)[0] is cfgmod.ConfigRecovery.BLOCKED            # BLOCK, not None
 
     # save_config_bundle must refuse while that journal entry is present.
     svc = _svc_config_bundle(tmp_path)
@@ -709,7 +709,7 @@ def test_malformed_journal_pre_or_mode_blocks_not_raises(tmp_path, bad):
     second = {"kind": "stack", "rel": "config/stacks/x.toml", "pre": "S", "existed": True,
               "mode": 0o644, **bad}
     cfgmod._txn_journal(paths).write_text(json.dumps({"version": 1, "targets": [good, second]}))
-    assert cfgmod.recover_config_transaction(paths) == ""            # BLOCK, no exception
+    assert cfgmod.recover_config_transaction(paths)[0] is cfgmod.ConfigRecovery.BLOCKED            # BLOCK, no exception
     assert (tmp_path / "config" / "local.toml").read_text() == "CURRENT"   # nothing restored
     assert cfgmod._txn_journal(paths).exists()                       # journal retained
 
@@ -727,7 +727,7 @@ def test_external_journal_symlink_blocks_not_raises(tmp_path):
     outside.write_text('{"version": 1, "targets": [{"kind": "local", "rel": "config/local.toml", "pre": "P", "existed": true, "mode": 420}]}')
     os.symlink(outside, tmp_path / "state" / "config-txn.json")     # escaping journal symlink
     try:
-        assert cfgmod.recover_config_transaction(paths) == ""       # BLOCK, no exception
+        assert cfgmod.recover_config_transaction(paths)[0] is cfgmod.ConfigRecovery.BLOCKED       # BLOCK, no exception
         svc = _svc_config_bundle(tmp_path)
         r = svc.save_config_bundle("daemon", values={"radio": "868"})
         assert not r.ok and any("recovery-required" in d for d in r.details)
@@ -1824,6 +1824,26 @@ def test_a_recovery_that_raises_refuses_the_writer_with_the_typed_refusal(tmp_pa
     assert journal.exists() and local.read_text() == "# untouched\n"
 
 
+@pytest.mark.safety("config-transaction")
+@pytest.mark.parametrize("note", ["", "reworded"])
+def test_a_recovered_journal_admits_the_writer_whatever_its_note_says(tmp_path, monkeypatch, note):
+    """The writer is admitted on the typed RECOVERED verdict, not on the note: the same recovery
+    with an empty or reworded note still restores the pre-image and lets the save proceed."""
+    paths = _paths(tmp_path)
+    local = tmp_path / "config" / "local.toml"
+    local.parent.mkdir(parents=True, exist_ok=True)
+    local.write_text("[remotes\n")                                   # half-written
+    journal = _write_journal(tmp_path, {"version": 1, "targets": [
+        {"kind": "local", "rel": "config/local.toml", "pre": '[remotes]\nx = "y"\n',
+         "existed": True, "mode": 0o600}]})
+    real = cfgmod.recover_config_transaction
+    monkeypatch.setattr(cfgmod, "recover_config_transaction", lambda p: (real(p)[0], note))
+    cfgmod.save_hardware_setup(paths, "loraham")
+    saved = tomllib.loads(local.read_text())
+    assert not journal.exists()
+    assert saved["remotes"] == {"x": "y"} and saved["radio"]["hardware"] == "loraham"
+
+
 @pytest.mark.safety("config-transaction")
 @pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
 def test_a_journal_path_that_cannot_be_examined_refuses_the_writer(tmp_path, monkeypatch, err):
diff --git a/tests/core/test_process_ownership.py b/tests/core/test_process_ownership.py
index 32e7fd2..99c7114 100644
--- a/tests/core/test_process_ownership.py
+++ b/tests/core/test_process_ownership.py
@@ -538,7 +538,7 @@ def test_reused_pid_between_spawn_and_persist_is_not_owned(tmp_path, reaper):
 @pytest.mark.needs_session
 def test_untracked_job_spawn_is_terminated_not_orphaned(tmp_path, reaper, monkeypatch):
     from lhpc.core.services import ControllerService
-    from lhpc.core import runtime_fs
+    from lhpc.core import jobs, runtime_fs
     from lhpc.core.probes.backends import FakeSystem
     p = _leader(reaper)                                   # a real detached session leader
     svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
@@ -546,8 +546,8 @@ def test_untracked_job_spawn_is_terminated_not_orphaned(tmp_path, reaper, monkey
     # Marker persistence fails after the process is already spawned.
     monkeypatch.setattr(runtime_fs, "write_marker",
                         lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
-    err = svc._track_or_terminate(life, "build-x", p.pid, "x", "build")
-    assert err and "could not be persisted" in err and "terminated" in err
+    outcome, err = svc._track_or_terminate(life, "build-x", p.pid, "x", "build")
+    assert outcome is jobs.TrackOutcome.TERMINATED and "could not be persisted" in err
     for _ in range(50):
         if not life._proc_alive(p.pid):
             break
diff --git a/tests/install/test_auto_install.py b/tests/install/test_auto_install.py
index fed8149..1cc0546 100644
--- a/tests/install/test_auto_install.py
+++ b/tests/install/test_auto_install.py
@@ -623,7 +623,8 @@ def _spawnable(svc, monkeypatch, spawn_ok=True, track_ok=True):
             return getattr(real_life, name)
     monkeypatch.setattr(svc, "_lifecycle", lambda: FakeLife())
     monkeypatch.setattr(svc, "_track_or_terminate",
-                        lambda life, ln, pid, cid, op: "" if track_ok else "track failed")
+                        lambda life, ln, pid, cid, op: ((jobs.TrackOutcome.TRACKED, "") if track_ok
+                                                        else (jobs.TrackOutcome.TERMINATED, "track failed")))
     return calls
 
 
@@ -794,7 +795,7 @@ def test_child_death_before_claim_is_ackable_while_spawner_lives(tmp_path, monke
         def __getattr__(self, name):     # only spawn_job is faked; everything else stays REAL
             return getattr(real_life, name)
     monkeypatch.setattr(svc, "_lifecycle", lambda: FakeLife())
-    monkeypatch.setattr(svc, "_track_or_terminate", lambda *a, **k: "")
+    monkeypatch.setattr(svc, "_track_or_terminate", lambda *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     ln, err = svc.spawn_auto_install_job(_sel(svc))
     assert ln and err is None
     st, res = ai_mod.read_reservation(svc._paths)
@@ -1193,6 +1194,27 @@ def test_orphan_risk_phase_requires_confirmed_ack(tmp_path, monkeypatch):
     assert svc._auto_install_gate() == ""                                # later launch possible
 
 
+@pytest.mark.needs_session
+def test_unverified_tracking_is_orphan_risk_whatever_its_message_says(tmp_path, monkeypatch):
+    # The orphan-risk decision follows the typed tracking outcome: an unverified stop whose
+    # message is reworded (no 'ORPHAN RISK' in it) still leaves the confirmed-recovery record.
+    svc = _svc(tmp_path)
+    kids = []
+    _real_child_spawn(svc, monkeypatch, kids)
+    monkeypatch.setattr(svc, "_track_or_terminate",
+                        lambda *a, **k: (jobs.TrackOutcome.TERMINATION_UNVERIFIED,
+                                         "reworded: the stop was not proven"))
+    ln, err = svc.spawn_auto_install_job(_sel(svc))
+    monkeypatch.undo()
+    assert ln is None
+    st, res = ai_mod.read_reservation(svc._paths)
+    assert st == "valid" and res["phase"] == "orphan-risk" and res["pid"] == kids[0].pid
+    assert not svc.auto_install_ack().ok                                 # plain ack refused
+    kids[0].terminate()
+    kids[0].wait(timeout=5)
+    assert svc.auto_install_ack(confirm_orphan=True).ok
+
+
 @pytest.mark.needs_session
 def test_rebind_write_failure_yields_orphan_risk(tmp_path, monkeypatch):
     # bind persistence fails AND cessation is unproven (SIGTERM-ignoring child):
diff --git a/tests/stacks/test_voice_stack.py b/tests/stacks/test_voice_stack.py
index 447ba84..3118fcc 100644
--- a/tests/stacks/test_voice_stack.py
+++ b/tests/stacks/test_voice_stack.py
@@ -85,6 +85,22 @@ def test_lite_voice_start_seeds_config_despite_display_skip(tmp_path, monkeypatc
     assert "run it yourself in a terminal:" in (cli.summary or "")
 
 
+def test_lite_voice_start_ok_does_not_depend_on_the_sidecar_wording(tmp_path, monkeypatch, set_call, real_spawn):
+    # Success follows the fact that the copy-paste command was presented, not the sentence
+    # around it: the same start with every result summary reworded is still OK.
+    from lhpc.core import service_lifecycle_ops
+    real = service_lifecycle_ops.CompResult
+    monkeypatch.setattr(service_lifecycle_ops, "CompResult",
+                        lambda **kw: real(**{**kw, "summary": "reworded: " + kw.get("summary", "")}))
+    svc = _voice_svc(real_spawn, tmp_path, monkeypatch, desktop=False)
+    _config_written(monkeypatch, svc)
+    set_call(svc)
+    res = svc.start("voice", apply=True)
+    cli = next(r for r in res.results if r.component == "loraham-voice-cli")
+    assert cli.outcome == Outcome.MANUAL_REQUIRED and cli.summary.startswith("reworded: ")
+    assert res.ok is True, outcomes(res)
+
+
 def test_voice_cli_command_is_printed_on_a_line_of_its_own(tmp_path, monkeypatch, set_call, real_spawn):
     # F-C2: the note was appended to the command, so pasting the printed line gave
     # "syntax error near unexpected token `('". The command line must parse as printed.
diff --git a/tests/web/test_hmac.py b/tests/web/test_hmac.py
index b8653fc..94041b5 100644
--- a/tests/web/test_hmac.py
+++ b/tests/web/test_hmac.py
@@ -340,9 +340,11 @@ def test_interrupted_between_steps_stays_retryable(tmp_path, monkeypatch):
 
 # ---- unverified driver startup: the detached-driver tracking gate + orphan-risk blocking -----------
 
-_ORPHAN_ERR = ("hmac-apply 'meshcom' spawned but its job marker could not be persisted AND the process "
+_ORPHAN_ERR = (jobs.TrackOutcome.TERMINATION_UNVERIFIED,
+               "hmac-apply 'meshcom' spawned but its job marker could not be persisted AND the process "
                "could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")
-_TERMINATED_ERR = ("hmac-apply 'meshcom' spawned but its job marker could not be persisted; the process "
+_TERMINATED_ERR = (jobs.TrackOutcome.TERMINATED,
+                   "hmac-apply 'meshcom' spawned but its job marker could not be persisted; the process "
                    "was terminated (not left orphaned).")
 
 
@@ -387,6 +389,20 @@ def test_orphan_risk_startup_is_blocking_unsafe_with_driver_ident(tmp_path, monk
     assert not svc.hmac_apply_start("meshcom", "renew").ok
 
 
+def test_unverified_tracking_stays_blocking_unsafe_whatever_its_message_says(tmp_path, monkeypatch):
+    """The unsafe decision follows the typed tracking outcome: the same unverified stop with a
+    reworded message (no 'ORPHAN RISK' in it) still blocks as unsafe, never an ordinary failure."""
+    import os
+    svc = _svc(tmp_path)
+    _fake_spawn(monkeypatch, os.getpid())
+    monkeypatch.setattr(ControllerService, "_track_or_terminate",
+                        lambda self, life, ln, pid, cid, op: (_ORPHAN_ERR[0], "reworded: stop not proven"))
+    r = svc.hmac_apply_start("meshcom", "renew")
+    assert not r.ok and r.summary == "reworded: stop not proven"
+    assert svc.hmac_apply_status()["phase"] == "unsafe"
+    assert not svc.hmac_apply_start("meshcom", "renew").ok      # blocking, not retryable
+
+
 def test_confirmed_terminated_startup_is_ordinary_failed(tmp_path, monkeypatch):
     import os
     svc = _svc(tmp_path)
@@ -552,7 +568,8 @@ def test_apply_start_spawns_and_records_the_run(tmp_path, monkeypatch):
             assert argv[:5] == [__import__("sys").executable, "-u", "-m", "lhpc", "_hmac-apply"]
             return name + ".log", 4242
     monkeypatch.setattr(type(svc), "_lifecycle", lambda self: _Life())
-    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
+    monkeypatch.setattr(type(svc), "_track_or_terminate",
+                        lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     r = svc.hmac_apply_start("meshcom", "enable")
     assert r.ok and svc.hmac_apply_status()["run_id"] == r.data["run_id"]
 
@@ -574,7 +591,8 @@ def test_hmac_disable_start_proceeds_with_confirm(tmp_path, monkeypatch):
     monkeypatch.setattr(type(svc), "_lifecycle",
                         lambda self: type("L", (), {"spawn_job":
                             lambda self, name, argv, cwd, env=None: (name + ".log", 4242)})())
-    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
+    monkeypatch.setattr(type(svc), "_track_or_terminate",
+                        lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     r = svc.hmac_apply_start("meshcom", "disable", confirm=True)
     assert r.ok and svc.hmac_apply_status()["run_id"] == r.data["run_id"]
 
@@ -584,7 +602,8 @@ def test_hmac_enable_and_renew_start_are_not_gated(tmp_path, monkeypatch):
     monkeypatch.setattr(type(svc), "_lifecycle",
                         lambda self: type("L", (), {"spawn_job":
                             lambda self, name, argv, cwd, env=None: (name + ".log", 4242)})())
-    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
+    monkeypatch.setattr(type(svc), "_track_or_terminate",
+                        lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     assert svc.hmac_apply_start("meshcom", "enable").ok        # confirm defaults False, still starts
     svc.hmac_apply_recover("meshcom", svc.hmac_apply_status()["run_id"])
     assert svc.hmac_apply_start("meshcom", "renew").ok
@@ -631,7 +650,8 @@ def test_hmac_disable_web_starts_with_the_correct_phrase(tmp_path, monkeypatch,
     monkeypatch.setattr(type(svc), "_lifecycle",
                         lambda self: type("L", (), {"spawn_job":
                             lambda self, name, argv, cwd, env=None: (name + ".log", 4242)})())
-    monkeypatch.setattr(type(svc), "_track_or_terminate", lambda self, *a, **k: "")
+    monkeypatch.setattr(type(svc), "_track_or_terminate",
+                        lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     client.post("/stacks/meshcom/hmac/disable/apply",
                 data={"_csrf": tok, "confirm_phrase": svc.HMAC_DISABLE_CONFIRM}, follow_redirects=True)
     assert svc.hmac_apply_status() and svc.hmac_apply_status()["action"] == "disable"
diff --git a/tests/web/test_webjob.py b/tests/web/test_webjob.py
index 059c94d..7bede7a 100644
--- a/tests/web/test_webjob.py
+++ b/tests/web/test_webjob.py
@@ -195,8 +195,9 @@ def test_spawn_web_job_proven_terminated_primary_blocks_no_secondaries(tmp_path,
     _fake_spawn(monkeypatch, os.getpid())
     monkeypatch.setattr(ControllerService, "_track_or_terminate",
                         lambda self, life, ln, pid, cid, op, attempt_id="", **k:
-                        f"{op} '{cid}' spawned but its job marker could not be persisted; "
-                        "the process was terminated (not left orphaned).")
+                        (jobs.TrackOutcome.TERMINATED,
+                         f"{op} '{cid}' spawned but its job marker could not be persisted; "
+                         "the process was terminated (not left orphaned)."))
     log, admission, reason = svc.spawn_web_job("build", "meshcom")
     assert log is None and admission == "blocked" and "terminated" in reason
     # only the primary's (failed) marker exists — no secondary component job spawned
@@ -217,8 +218,9 @@ def test_spawn_web_job_launcher_carries_the_manifest_timeout(tmp_path, monkeypat
     _fake_spawn(monkeypatch, os.getpid())
     monkeypatch.setattr(ControllerService, "_track_or_terminate",
                         lambda self, life, ln, pid, cid, op, attempt_id="", **k:
-                        f"{op} '{cid}' spawned but its job marker could not be persisted; "
-                        "the process was terminated (not left orphaned).")
+                        (jobs.TrackOutcome.TERMINATED,
+                         f"{op} '{cid}' spawned but its job marker could not be persisted; "
+                         "the process was terminated (not left orphaned)."))
     seen = {}
     real = commands.render_build_launcher
     def _rec(*a, **kw):
@@ -229,15 +231,24 @@ def test_spawn_web_job_launcher_carries_the_manifest_timeout(tmp_path, monkeypat
     assert seen[primary] == expected
 
 
-def test_spawn_web_job_orphan_primary_blocks(tmp_path, monkeypatch):
+# The unsafe decision follows the typed tracking outcome, whatever its message says.
+_UNVERIFIED_MESSAGES = pytest.mark.parametrize("message", [
+    "... could NOT be confirmed stopped — ORPHAN RISK; check ps.",
+    "reworded: the stop was not proven",
+], ids=["as-shipped", "reworded"])
+
+
+@_UNVERIFIED_MESSAGES
+def test_spawn_web_job_orphan_primary_blocks(tmp_path, monkeypatch, message):
     import os
     svc = _svc(tmp_path)
     _fake_spawn(monkeypatch, os.getpid())
     monkeypatch.setattr(ControllerService, "_track_or_terminate",
                         lambda self, life, ln, pid, cid, op, attempt_id="", **k:
-                        f"{op} '{cid}' ... could NOT be confirmed stopped — ORPHAN RISK; check ps.")
+                        (jobs.TrackOutcome.TERMINATION_UNVERIFIED, message))
     log, admission, reason = svc.spawn_web_job("build", "meshcom")
     assert log is None and admission == "blocked" and "Recover" in reason
+    assert [rec["state"] for _log, rec in jobresult.read_results(svc._paths)] == ["unsafe"]
 
 
 def test_spawn_web_job_launcher_write_failure_is_typed_and_settles_the_attempt(tmp_path, monkeypatch):
@@ -487,7 +498,7 @@ def test_spawn_start_job_captures_then_releases_admission_before_publishing(tmp_
         seen["free"] = _second_service_can_take_admission(svc)
         seen["ident"] = ident
         seen["op"], seen["cid"], seen["ln"] = op, cid, ln
-        return ""
+        return jobs.TrackOutcome.TRACKED, ""
     monkeypatch.setattr(ControllerService, "_track_or_terminate", publish)
     monkeypatch.setattr(ControllerService, "_web_admit_handshake", lambda self, log, aid: ("admitted", ""))
     log, admission, reason = svc.spawn_start_job("start", "kiss", band="433", stop_owners=True)
@@ -509,7 +520,7 @@ def test_spawn_web_job_captures_then_releases_admission_before_publishing(tmp_pa
 
     def publish(self, life, ln, pid, cid, op, attempt_id="", ident=None):
         seen.append((op, cid, _second_service_can_take_admission(svc), bool(ident)))
-        return ""
+        return jobs.TrackOutcome.TRACKED, ""
     monkeypatch.setattr(ControllerService, "_track_or_terminate", publish)
     monkeypatch.setattr(ControllerService, "_web_admit_handshake", lambda self, log, aid: ("admitted", ""))
     log, admission, _ = svc.spawn_web_job("build", "meshcom")
@@ -532,7 +543,8 @@ def test_spawn_web_job_secondary_raise_releases_its_admission(tmp_path, monkeypa
             raise RuntimeError("secondary spawn failed")
         return f"{name}.log", os.getpid()
     monkeypatch.setattr(Lifecycle, "spawn_job", spawn)
-    monkeypatch.setattr(ControllerService, "_track_or_terminate", lambda self, *a, **k: "")
+    monkeypatch.setattr(ControllerService, "_track_or_terminate",
+                        lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     monkeypatch.setattr(ControllerService, "_web_admit_handshake", lambda self, log, aid: ("admitted", ""))
     with pytest.raises(RuntimeError):
         svc.spawn_web_job("build", "meshcom")
@@ -561,24 +573,26 @@ def test_spawn_start_job_second_start_is_a_typed_already_in_progress(tmp_path, m
     spawned = []
     _fake_spawn(monkeypatch, os.getpid())
     monkeypatch.setattr(ControllerService, "_track_or_terminate",
-                        lambda self, *a, **k: spawned.append(1) or "")
+                        lambda self, *a, **k: spawned.append(1) or (jobs.TrackOutcome.TRACKED, ""))
     log, admission, reason = svc.spawn_start_job("start", "kiss")
     assert log is None and admission == "blocked" and "already in progress" in reason
     assert spawned == []
     assert svc.spawn_start_job("stop", "kiss")[1] == "blocked"        # not a detached op
 
 
-def test_spawn_start_job_orphan_and_terminated_are_typed(tmp_path, monkeypatch):
+@_UNVERIFIED_MESSAGES
+def test_spawn_start_job_orphan_and_terminated_are_typed(tmp_path, monkeypatch, message):
     svc = _svc(tmp_path)
     _fake_spawn(monkeypatch, os.getpid())
     monkeypatch.setattr(ControllerService, "_track_or_terminate",
-                        lambda self, *a, **k: "... ORPHAN RISK; check ps.")
+                        lambda self, *a, **k: (jobs.TrackOutcome.TERMINATION_UNVERIFIED, message))
     log, admission, reason = svc.spawn_start_job("start", "kiss")
     assert log is None and admission == "blocked" and "Recover" in reason
     assert jobresult.read_one(svc._paths, _SLOG)["state"] == "unsafe"
     svc2 = _svc(tmp_path / "b")
     monkeypatch.setattr(ControllerService, "_track_or_terminate",
-                        lambda self, *a, **k: "spawned but ... was terminated (not left orphaned).")
+                        lambda self, *a, **k: (jobs.TrackOutcome.TERMINATED,
+                                               "spawned but ... was terminated (not left orphaned)."))
     log, admission, reason = svc2.spawn_start_job("restart", "kiss")
     assert log is None and admission == "blocked" and "terminated" in reason
     assert jobresult.read_one(svc2._paths, "web-restart-kiss.log")["state"] == "failed"
@@ -591,7 +605,8 @@ def test_spawn_start_job_transports_the_restart_cascade_consent(tmp_path, monkey
     argvs = []
     monkeypatch.setattr(Lifecycle, "spawn_job",
                         lambda self, name, argv, cwd, env=None: argvs.append(argv) or (f"{name}.log", os.getpid()))
-    monkeypatch.setattr(ControllerService, "_track_or_terminate", lambda self, *a, **k: "")
+    monkeypatch.setattr(ControllerService, "_track_or_terminate",
+                        lambda self, *a, **k: (jobs.TrackOutcome.TRACKED, ""))
     monkeypatch.setattr(ControllerService, "_web_admit_handshake", lambda self, log, aid: ("admitted", ""))
     assert svc.spawn_start_job("restart", "kiss", cascade=True)[1] == "admitted"
     assert "--cascade" in argvs[-1] and "--restart" in argvs[-1]
@@ -603,7 +618,7 @@ def test_spawn_start_job_refuses_an_unknown_target_before_spawning(tmp_path, mon
     svc = _svc(tmp_path)
     spawned = []
     _fake_spawn(monkeypatch, os.getpid())
-    monkeypatch.setattr(ControllerService, "_track_or_terminate", lambda self, *a, **k: spawned.append(1) or "")
+    monkeypatch.setattr(ControllerService, "_track_or_terminate", lambda self, *a, **k: spawned.append(1) or (jobs.TrackOutcome.TRACKED, ""))
     log, admission, reason = svc.spawn_start_job("start", "bogus")
     assert log is None and admission == "blocked" and "unknown stack or component" in reason
     assert spawned == [] and jobresult.read_one(svc._paths, "web-start-bogus.log") is None
```

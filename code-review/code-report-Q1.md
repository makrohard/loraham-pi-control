# Code report — batch Q1 (typed outcomes)

Base: `origin/integration/0.12.0` (042716f). Branch: `cons/Q1`. Next version heading: `## 0.12.0`.

## Commits

| # | sha | subject | files |
|---|---|---|---|
| 0 | e0bdcc3 | Q1: plan | plans/PLAN-Q1.md |
| 1 | 8a0da1c | job tracking returns a typed outcome | lhpc/core/jobs.py, service_lifecycle_ops.py, service_hmac.py, service_auto_install.py; tests/web/test_webjob.py, tests/web/test_hmac.py, tests/install/test_auto_install.py, tests/core/test_process_ownership.py |
| 2 | f841f86 | interactive sidecar accepted when its command was presented | service_lifecycle_ops.py; tests/stacks/test_voice_stack.py |
| 3 | b84fb18 | config recovery returns a typed verdict | lhpc/core/config.py, service_params.py; tests/core/test_config.py |
| 4 | 75eb281 | CHANGELOG | CHANGELOG.md |

## Tests per commit (red-before against the commit's parent)

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

## Line counts and dependencies (production)

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

## Simplicity guardrails

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

## The 6-point block

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

## Deviations

- The first directory run (`tests/core tests/repo tests/cli/test_cli.py`) was moved to the
  background by the tool's 2-minute default timeout; I waited for it to finish before running
  anything else; the later runs used an explicit 10-minute foreground limit.
- Plan said the interactive test goes in test_post_start.py "or the start-aggregation owner": it
  went to tests/stacks/test_voice_stack.py, the owner of the only non-main interactive sidecar.
- Not changed (named in the plan): `_recover_cmd_hint` (service_auto_install.py:42) and the web/CLI
  adapters still read "ORPHAN RISK" in the persisted auto-install *recovery reason* to pick the
  `--confirm-orphan` hint. That is the record contract, not the tracking result; recommended for a
  later batch.

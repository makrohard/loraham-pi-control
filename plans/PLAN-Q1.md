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

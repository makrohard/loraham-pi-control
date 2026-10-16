# Gate 1: doc-pointers (owners named by file and symbol, checked by a repo test) — v3

**Request.** Judge the one commit below against the maintainer's decisions (excerpts below) and tests/README.md rule 1 as this commit amends it. Judge: is the new second exception narrow and worded as decided, and does the test stay inside it (it reads names, asserts existence only); is the rewrite of the 100 pointers purely mechanical; do the red cases hold; is any claim stronger than the evidence; is anything changed beyond the doc, the rule and the one test. v3 answers your round-4 RED (the policy point): the v2 → v3 diff is shown first.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diffs are `git diff` output verbatim.

## v2 → v3 (`git diff 91a62ac1 HEAD`)

```diff
diff --git a/tests/README.md b/tests/README.md
index 7b2e481f..4c49ad64 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -68,7 +68,9 @@ through `interrupts.py`: its module pins the operation's durable writes and fail
 1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
    to assert its contents, no pinning statement order or helper names. Exception: a whole-module
    negative invariant ("this file spawns nothing") no driven path can prove, saying so in its
-   docstring and with a behavioural twin.
+   docstring and with a behavioural twin. Second exception: a documentation-integrity test may
+   assert that a symbol named by `docs/` is defined in its file; it asserts existence, never
+   behaviour or source text.
 2. **Exact where exactness is the contract.** Systemd units, nginx config, firewall rules, generated
    argv, config rendering, persisted schemas, receipts, permissions and canonical paths are compared
    byte for byte. Human sentences are not: assert the typed field, the state, or the command token
diff --git a/tests/repo/test_doc_owner_pointers.py b/tests/repo/test_doc_owner_pointers.py
index 6c154afd..1763f275 100644
--- a/tests/repo/test_doc_owner_pointers.py
+++ b/tests/repo/test_doc_owner_pointers.py
@@ -2,8 +2,8 @@
 `, `symbol`` in the same file). Each named symbol must still be defined in its file, so a rename
 or a removal fails here instead of leaving the doc pointing at nothing.
 
-A repository invariant, not behaviour: it reads the production files to find the names, which
-rule 1 otherwise forbids; nothing it asserts is about what the code does.
+A documentation-integrity test (tests/README.md, rule 1's second exception): it reads the
+production files only to find each name's definition, never behaviour or source text.
 """
 
 import re
```

## The decisions (the maintainer's choices, as relayed to me)

On the doc-pointer note:

> docs/architecture.md keeps `path` + symbol name, drops the line numbers, and a small tests/repo test asserts each
> named symbol exists in its file (and fails on a rename/removal). One commit, docs + one test, no other change; no
> CHANGELOG line per the tooling rule.

After round 4, option (a) of two ((b): drop the existence test, keep the no-line-number check):

> amend rule 1 with a second narrow exception ("a documentation-integrity test may assert that a symbol named by
> docs/ is defined in its file; it asserts existence, never behaviour or source text")

## tests/README.md rule 1 before this commit (verbatim)

> 1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
>    to assert its contents, no pinning statement order or helper names. Exception: a whole-module
>    negative invariant ("this file spawns nothing") no driven path can prove, saying so in its
>    docstring and with a behavioural twin.

## Commit (full message, `git log -1 --format=%B`)

### 25c7d657 doc-pointers: owners are named by file and symbol, and a repo test checks they exist

```text
doc-pointers: owners are named by file and symbol, and a repo test checks they exist

The invariant table in docs/architecture.md pointed at owners as `path:N` `name` (100 pointers);
the line numbers drifted with every edit above a cited function. They are dropped: an owner is
`path` `name`, continued by `, `name`` in the same file. tests/repo/test_doc_owner_pointers.py
fails when a named symbol is no longer defined in its file (a rename or a removal), and when a
numbered pointer comes back, since that form would escape the check.

tests/README.md rule 1 gets a second, narrow exception for such a documentation-integrity
test: it asserts that a symbol named by docs/ is defined in its file, never behaviour or
source text.

Red before: on the parent's doc both tests fail (no `path` `name` pointer is found; 37 table
rows carry `path:N`). Docs, the rule and one test: no CHANGELOG line.

```

## The report

> # Code report: doc-pointers (owners named by file and symbol, checked by a repo test) — v3
>
> Branch `cons/doc-pointers-r1` on the 0.12.10 release commit `f6b7940e` (itself on `origin/dev` = `0a7fb61d`; it lands
> after the release, 0.12.11 content). Decision: the maintainer's choice on the doc-pointer note: docs/architecture.md
> keeps `path` + symbol name and drops the line numbers; a small tests/repo test asserts each named symbol exists in its
> file and fails on a rename or a removal. After gate 1 round 4 (rule 1), the maintainer chose to add a second, narrow
> exception to tests/README.md rule 1 for this kind of test. Docs, the rule and one test, no CHANGELOG line (tooling).
>
> **v3 against v2 (packet sha256 d3236118…, commit 91a62ac1):** gate 1 round 4 was RED on one policy point: rule 1
> forbids a test that reads production `.py` files, and the docstring's "repository invariant, not behaviour" is not
> its exception. tests/README.md rule 1 gets a second exception, verbatim: "Second exception: a
> documentation-integrity test may assert that a symbol named by `docs/` is defined in its file; it asserts existence,
> never behaviour or source text." The docstring points to it (`git diff 91a62ac1 25c7d657 --stat` →
> `2 files changed, 5 insertions(+), 3 deletions(-)`); the commit message names the rule change. The test's code is unchanged.
>
> **v2 against v1 (packet sha256 50fedac5…, commit 73d80fb6):** the second read found that the definition pattern's
> assignment branch matched indented keyword-argument lines, so renaming `def build` in
> `lhpc/core/service_lifecycle_ops.py` still passed (`build=False),` at line 6264). The pattern is now anchored: `def`/
> `class` (optionally `async`) at line start after indentation, assignments at column 0. One line of the test changed
> (`git diff 73d80fb6 91a62ac1 --stat` → `1 file changed, 1 insertion(+), 1 deletion(-)`); the two new red cases, the
> 6-point items 3 and 6 and the test description below are updated. Nothing else changed.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 25c7d657 | docs/architecture.md (38 lines), tests/README.md (rule 1, +3/−1), tests/repo/test_doc_owner_pointers.py (new, 45 lines) | repo layer: `test_every_named_owner_is_defined_in_its_file`, `test_owner_pointers_carry_no_line_numbers` | both fail on the parent's doc (below) |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py f6b7940e..HEAD --fact "the owning function's file and name")
>
> ```
> HOUSE RULES KEPT  f6b7940e..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 31 lines   OK
> docs         1 files, +38 lines; fact "the owning function's file and name" -> docs/architecture.md   OK
> tests        2 new tests; layers tests/repo=repo; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> The checker's docs row reads `docs/` and `README.md` only; the tests/README.md rule-1 line is shown in the diff.
>
> ## The rewrite is mechanical
>
> The edit was made by a script: `` `(lhpc|testlab)/….py:N` ` `` → `` `(lhpc|testlab)/….py` ` `` (62 replacements) and
> `` , `:N` ` `` → `` , ` `` (38 replacements), plus one sentence of the table's intro. Proof: the same two substitutions
> applied to `git show f6b7940e:docs/architecture.md` give a file of 403 lines that differs from the commit's
> `docs/architecture.md` (403 lines) in exactly one line, the intro:
>
> ```text
> - the row says where it is enforced (`file:line` of the owning function), which locks it takes, what
> + the row says where it is enforced (the owning function's file and name), which locks it takes, what
> ```
>
> All 38 continuations followed ", " before the edit (`grep -oE '.{2}`:[0-9]+`'` → 38 × `, `). No other doc carries a
> `path:N` pointer: `grep -rlE '\.py:[0-9]+`' --include=*.md .` lists only frozen review records under `code-review/`.
> The second read checked that every one of the 100 old `path:N` pointers hit the definition line of its named symbol
> at f6b7940e (100/100), so each name kept is the right symbol.
>
> ## The test
>
> `_POINTER` reads `` `path` `name` `` followed by any `` , `name` `` in each table row; each name must match
> `^(\s*(async\s+)?(def|class)\s+{name}\b|{name}\s*[:=])` (multiline) in its file: a `def`/`class` at line start after
> indentation, or an assignment at column 0 (both assignment owners, `SNAPSHOT_NEUTRAL` and `SNAPSHOT_NEUTRAL_WRITERS`,
> are at column 0). The test reads 100 pointers on 25c7d657 (`list(_pointers())` → 100), and each matches exactly one
> line of its file (the per-pointer count of matching lines → `{1: 100}`). The second test fails on any table row with
> `` .py:N` `` or `` `:N` ``: such a pointer would not match `_POINTER` and would go unchecked (the re-added case below
> shows it: the first test still passes). The docstring points to rule 1's second exception,
> which it names.
>
> ## Red cases (on a clean detached checkout of 25c7d657; each edit reverted, `git status --short` empty after)
>
> | case | edit | result on 25c7d657 | on 73d80fb6 (v1 pattern) |
> |---|---|---|---|
> | green | none | `2 passed` | `2 passed` |
> | doc rename | `operation_lock` → `operation_lockX` in the doc | `names owners not defined there: lhpc/core/reslock.py `operation_lockX`` — 1 failed, 1 passed | same |
> | missing file | `testlab/lhpc_testlab/ops.py` → `opsX.py` in the doc | `… testlab/lhpc_testlab/opsX.py `power`` — 1 failed, 1 passed | same |
> | source rename | `def _retire_body(` → `def _retire_bodyX(` in `lhpc/core/service_binary_ops.py` | `… lhpc/core/service_binary_ops.py `_retire_body`` — 1 failed, 1 passed | same |
> | source rename, name also a kwarg | `    def build(` → `    def build_x(` in `lhpc/core/service_lifecycle_ops.py` | `… lhpc/core/service_lifecycle_ops.py `build`` — 1 failed, 1 passed | `2 passed` (the v1 gap) |
> | source rename, old name in a comment | `def operation_lock(` → `# replaces def operation_lock` + `def operation_lock_x(` in `lhpc/core/reslock.py` | `… lhpc/core/reslock.py `operation_lock`` — 1 failed, 1 passed | `2 passed` (the v1 gap) |
> | re-added `:N` | `` `lhpc/core/assets.py` `` → `` `lhpc/core/assets.py:20` `` | `a `path:N` pointer drifts and escapes the check above: ['| install / recovery | a wheel installed into a fresh venv r']` — 1 failed, 1 passed | same |
> | parent doc | `git show f6b7940e:docs/architecture.md` | `assert []` and the numbered-rows message — 2 failed | same |
>
> A public name (`asset_path`) could not serve as the source-rename case: the rename breaks `tests/conftest.py`'s import
> before collection. The private `_retire_body` is only called inside its module.
>
> ## The 6-point block
>
> 1. **Contracts:** none (docs and a repo test). 2. **Invariants:** every owner the invariant table names is defined in
>    the file it names.
> 3. **Failure classes:** a rename, a removal or a move to another file of a named owner now fails tests/repo, also
>    when the old name survives as a keyword argument, a comment or any other indented non-definition line (each of
>    the 100 names matches exactly one line today); an edit above an owner no longer touches the doc.
> 4. **Test rules:** rule 1, with its new second exception (this test is its only user); rule 13 not touched.
> 5. **Directories:** `python -m pytest tests/repo -q -n 12 -p no:cacheprovider` → `620 passed, 1 skipped` (616 before:
>    the 2 new tests and 2 `test_suite_hygiene.py` parametrizations over the new file, by a `--co` diff against
>    f6b7940e); `ruff check tests/repo/test_doc_owner_pointers.py` → clean.
> 6. **Adversarial self-review:**
>    - v1's pattern (`\b(def|class)\s+{name}\b|^\s*{name}\s*[:=]`) let a rename pass when the old name stayed on an
>      indented `name=` line or after `def` in a comment; true for `build` today. Found by the second read, fixed in v2.
>    - The existence check is file-wide: an owner moved to another class in the same file still passes, and so does
>      a column-0 assignment of the same name. That the named function still enforces the row's invariant stays a
>      reading check, as before.
>    - Only the grammar `` `path` `name`(, `name`)* `` is read. A continuation written another way (`` and `b` ``) is
>      not seen; today all 38 use ", " (measured above).
>    - The numbered check guards `` `path:N` `` and `` `:N` `` in table rows only; `path:N-M`, a `.js:N` pointer and a
>      pointer in prose would escape it (the second read measured each at `2 passed`). None exists today.
>    - The numbered-rows message names a row by its first 60 characters, not the pointer.
>    - Slip: after the first red run, a `git checkout docs/architecture.md` meant to undo the red edit also reverted
>      the uncommitted rewrite. It was reapplied by the same script (62/38 again) before the commit; the mechanical
>      proof above was run on the committed file.
>    - v2's docstring claimed "a repository invariant, not behaviour", the wording of `test_module_budgets.py`, but
>      rule 1 had no such exception (gate 1 round 4). v3 names the exception the rule now has. `test_module_budgets.py`
>      keeps its own sentence; it is backed by rule 13, not by rule 1.
>    - Amends: the handler first got `eb660169`; `f3078165` added the rule-1 sentence to the docstring; `73d80fb6` added
>      the red-before paragraph to the commit message (v1 packet); `91a62ac1` is the anchored pattern (v2);
>      `25c7d657` adds the rule-1 exception (this v3).
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h HEAD~1` | f6b7940e |
> | pointers before | the doc-pointer resolver on f6b7940e | 100 ok, 0 stale |
> | pointers read by the test | `list(_pointers())` on 25c7d657 | 100 |
> | matching lines per pointer | the test's pattern, `re.findall` per pointer | `{1: 100}` |
> | replacements | the rewrite script | 62 + 38 |
> | numbered rows before / after | the second test's pattern on the parent / commit | 37 / 0 |
> | tests/repo | `pytest tests/repo -q -n 12` | 620 passed, 1 skipped |

## The full diff (`git diff f6b7940e8a2cb8679c390da1e8534739792da242 25c7d6570403b0da9fddb5bdd371e76a6e2dede1`)

```diff
diff --git a/docs/architecture.md b/docs/architecture.md
index f1a13f0f..532a3221 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -323,7 +323,7 @@ leaves on disk and which test proves it: the [invariant table](#invariant-table)
 ### Invariant table
 
 One row per safety invariant: the statement is in the bullet or page the second column links to;
-the row says where it is enforced (`file:line` of the owning function), which locks it takes, what
+the row says where it is enforced (the owning function's file and name), which locks it takes, what
 it keeps on disk and how a crash is recovered, and the tests that prove it. The order in which the
 locks are taken is stated once, in [Locking](#safety-model); the table does not repeat it. A
 proving test runs at least one owner function of its row, or names a table the row lists as its
@@ -334,44 +334,44 @@ the runtime root unless absolute.
 
 | area | invariant | owner | locks | durable state · recovery | proving tests |
 |---|---|---|---|---|---|
-| admission / locks | task admission: no new task while an uninstall, a self-update or a reboot/shutdown is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2589` `_admit`; `lhpc/core/service_selfupdate.py:1280` `_task_admission_blocked` | `controller-task-admission` | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
-| admission / locks | named non-blocking locks; a contended operation refuses naming the holder, after waiting up to 5 s for a holder in the same controller process ([Locking](#safety-model)) | `lhpc/core/reslock.py:106` `operation_lock`; `lhpc/core/services.py:2652` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
-| admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py:2639` `_admission_guard`, `:219` `_config_stable`, `:361` `_source_operation_guard`, `:2716` `_lifecycle_guard` | the order is stated once, in [Locking](#safety-model) | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
-| admission / locks | a build holds every source it consumes, transitively, for its whole run; the receipt's revisions are read under those locks ([Locking](#safety-model)) | `lhpc/core/build_plan.py:36` `lock_sources`, `:96` `consumed_lines`; `lhpc/core/service_lifecycle_ops.py:3383` `build`, `:3730` `spawn_web_job`; `lhpc/core/build_launcher_runtime.py:206` `run` | the CLI: admission, index, every source exclusive; a detached build: index, its own source exclusive, each dependency's shared | the completion marker (the receipt) and its build-inputs sidecar, written only after the last step | `tests/core/test_build_plan.py::test_a_provider_source_cannot_move_during_a_dependent_build`, `::test_the_receipt_records_the_revision_read_under_the_locks`, `::test_both_paths_lock_and_record_the_same`, `::test_parallel_jobs_share_a_dependency` |
-| config transactions | a save validates everything first, is journalled and rolls back; a crashed save is finished before any writer runs ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1905` `apply_config_transaction`, `:1858` `_finish_pending_journal`, `:1885` `recover_config_journal_at_startup` | config lock `config/.lock` (`lhpc/core/config.py:77` `config_lock`) | `state/config-txn.json`; `lhpc/core/config.py:1800` `recover_config_transaction` rolls back, or blocks and keeps the journal | `tests/core/test_config.py::test_pending_journal_is_recovered_before_next_save`, `::test_rollback_failure_retains_journal_and_blocks_later`, `::test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`; `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` |
-| config transactions | a malformed `local.toml` is kept; only an absent per-stack file means defaults ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1320` `_write_local_tables`, `:2023` `load_stack_config` | config lock (writes only) | `config/local.toml`, `config/stacks/<id>[@band].toml` | `tests/core/test_config.py::test_local_unsupported_structures_block_and_preserve`, `::test_malformed_stack_config_raises_and_is_preserved`, `::test_absent_stack_config_is_defaults`, `::test_web_returns_409_on_malformed_config`; gap: a write over a `local.toml` that is not valid TOML |
-| install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py:532` `_stage_and_activate`, `:1454` `_staged_clone_record`, `:2280` `_activate_held`, `:1381` `_pending_journals` | admission, `source-txn-index`, source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py:1404` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`, `::test_an_empty_directory_that_is_not_the_recorded_inode_is_kept_and_named`, `::test_an_unrecorded_candidate_is_retained_and_named`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
-| install / recovery | locally added files are carried, never collateral ([Locally added files](#safety-model)) | `lhpc/core/source_fs.py:553` `carry_extras`, `:703` `extras_preserved`; `lhpc/core/install.py:2280` `_activate_held` | as source transactions | `.prev` and its journal are kept while a carry is unproven; the journal says `carrying` while the carry writes, and what it wrote is fsynced before the activation; a copy a stop caught inside the carry no longer matches its full recorded identity and is kept and named, never removed on dev+ino; a prior another process can still write into is kept (`prior-in-use`) until the next recovery | `tests/install/test_source.py::test_an_added_file_colliding_with_the_new_upstream_refuses`, `::test_a_carry_failure_restores_the_prior_and_refuses`, `::test_an_addition_made_after_the_carry_retains_the_archived_prior`, `::test_a_write_through_a_descriptor_opened_before_the_update_is_never_lost`, `::test_a_write_by_a_process_inside_the_prior_without_a_descriptor_is_never_lost`, `::test_a_writer_whose_proc_entries_cannot_be_read_is_still_a_holder`, `::test_the_carry_makes_what_it_wrote_durable`; `tests/install/test_source_interruption.py::test_a_power_loss_after_the_carry_wrote_is_recovered_and_retried`, `::test_a_directory_on_a_recycled_inode_in_carrying_is_never_removed` |
-| install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1267` `stop`, `:1129` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2524` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
-| install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:74` `_walk_parent`, `:156` `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
-| install / recovery | a path that cannot be examined is never absent: only ENOENT/ENOTDIR read as absent, any other error refuses or keeps; a `.git` symlink that does not resolve is present ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:740` `probe_exists`, `:756` `probe_stat`; `lhpc/core/firewall_helper.py:1066` `_read_json_state` (the stdlib-only helper's inline rule) | none | the receipt, journal or record is kept while its path cannot be examined | `tests/core/test_existence_probe.py::test_any_other_error_is_unknown_with_the_error`, `::test_an_unexaminable_path_is_never_absent`, `::test_a_git_symlink_that_does_not_resolve_is_never_clean`; `tests/host/test_firewall.py::test_a_journal_that_cannot_be_examined_is_not_absent` |
-| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2397` `uninstall` | admission, config stability (shared), every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
-| install / recovery | boot restore replays saved configuration through the gated start, honours stop notes ([operations](operations.md#not-a-supervisor)) | `lhpc/core/service_boot_restore.py:290` `boot_restore_run`, `:131` `_write_stop_intent` | admission and the normal start locks | `state/boot-restore.json`, `state/stop-intent/<stack>.json`; `lhpc/core/service_boot_restore.py:268` `_boot_journal_recovery` consumes an item left *attempting*, never retries it | `tests/core/test_boot_restore.py::test_crash_after_attempting_never_retries_but_pending_survives`, `::test_boot_restore_run_honors_stop_intent_end_to_end`, `::test_driver_admission_refusal_consumes_nothing` |
-| install / recovery | a wheel installed into a fresh venv runs ([Packaging](#safety-model)) | `lhpc/core/assets.py:20` `asset_path` | none | — | `tests/repo/test_packaging.py::test_data_assets_resolve`; gap: no test installs the wheel into a fresh venv |
-| binary channel | a failed install, or one interrupted before its commit, is rolled back to the previous one; one interrupted after its commit stays installed and recovery only completes its cleanup (backups, journal) ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:97` `binary_install`, `:600` `binary_recover`; `lhpc/core/binary_install.py:556` `rollback_files` | admission, the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `::test_an_open_binary_transaction_is_recovered_before_already_installed_is_said`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything`, `::test_committed_journal_leaves_the_receipt_authoritative` |
-| binary channel | the receipt is absent, valid, superseded or unsafe; unreadable is never absent ([operations](operations.md#install-channels)) | `lhpc/core/binary_receipt.py:272` `receipt_state` | none (read) | `state/binary/<stack>.json` | `tests/install/test_binary_channel.py::test_malformed_receipt_is_unsafe_never_absent`, `::test_superseded_when_txn_id_differs`, `::test_absent_when_no_receipt` |
-| binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:915` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
-| PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2519` `run_server`, `:326` `_trusted_host`, `:259` `_csrf_ok`, `:310` `_set_headers`; `lhpc/core/config.py:1999` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
-| PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py:741` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
-| PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:26` `invalidates_snapshot`; `lhpc/core/services.py:883` `invalidate_snapshot`, `:597` `build_snapshot`; the tables `lhpc/core/snapshot_memo.py:48` `SNAPSHOT_NEUTRAL`, `:291` `SNAPSHOT_NEUTRAL_WRITERS` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
-| PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3730` `spawn_web_job`, `:4238` `active_jobs`, `:4078` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
-| PKI / web | no shell: a validated value is always one argv token ([No shell](#safety-model)) | `lhpc/core/commands.py:75` `expand_argv` | none | — | `tests/core/test_structured_exec.py::test_hostile_value_stays_one_token`, `::test_started_process_argv_is_not_a_shell` |
-| PKI / web | typed validation; an `@file:` secret that is missing, empty, a symlink, not a regular file or over 64 KiB blocks the launch; a `pkg-config` failure aborts a build ([Typed validation](#safety-model)) | `lhpc/core/validators.py:434` `validate_param`; `lhpc/core/commands.py:130` `build_env`, `:241` `build_step_argv`; `lhpc/core/runtime_fs.py:515` `read_secret_text` | none | — | `tests/core/test_validators.py::test_start_rejects_malicious_runparam`; `tests/core/test_structured_exec.py::test_at_file_secret_missing_blocks`, `::test_at_file_secret_empty_blocks`, `::test_a_secret_that_is_not_a_bounded_regular_file_is_a_command_error` (a symlink; a 1 MiB file), `::test_a_fifo_secret_is_refused_without_blocking` (not a regular file); `tests/stacks/test_daemon_bounds.py::test_build_step_argv_pkgconfig_failure_fails_closed`; gap: a secret the service user cannot read (no read permission); the 64 KiB bound itself (no test reads a file just over it) |
-| PKI / web | an unverified clock never dates a certificate ([operations](operations.md#clock)) | `lhpc/core/clock.py:27` `verdict`, `:79` `clock_refusal` | the caller's | the original certificate stays active on a refusal | `tests/core/test_clock_gate.py::test_every_mutating_path_refuses_an_unverified_clock`, `::test_a_refused_reissue_leaves_the_original_certificate_active` |
-| firewall | the helper's apply is journalled and verified; a crash is finished forward or rolled back ([firewall](firewall.md)) | `lhpc/core/firewall_helper.py:1327` `op_apply`, `:1237` `op_check`, `:1129` `recover` | root flock `/etc/lhpc/.firewall.lock` | `/etc/lhpc/firewall.{journal,meta,snapshot,transition}.json`, receipt `/run/lhpc-firewall/check.json`; a corrupt journal fails closed | `tests/host/test_firewall.py::test_recovery_finishes_promoted_apply_forward`, `::test_recovery_rolls_back_unpromoted_apply`, `::test_corrupt_journal_fails_closed`, `::test_apply_happy_path_writes_snapshot_and_verified_receipt` |
-| firewall | remote exposure only with this boot's verified receipt, else loopback-only; a stale helper is named before the reboot ([firewall](firewall.md)) | `lhpc/core/service_firewall.py:609` `firewall_gate_activation`, `:781` `firewall_boot_gate`, `:705` `firewall_reapply_notice` | the caller's | the boot receipt above | `tests/host/test_firewall.py::test_gate_refuses_even_already_exposed_when_unverified`, `::test_boot_gate_falls_back_to_loopback_when_unverified`, `::test_boot_gate_fails_closed_when_fallback_cannot_stage`, `::test_reapply_notice_only_for_a_stale_helper` |
-| self-update | an unsafe controller identity blocks apply ([Controller identity](#controller-identity--self-update)) | `lhpc/core/services.py:496` `controller_identity_live` | none (read) | the cached verdict in the self-update envelope | `tests/core/test_controller.py::test_identity_ok`, `::test_missing_checkout_is_not_applicable_not_unsafe`, `::test_self_update_apply_blocked_by_unsafe_identity` |
-| self-update | a running console never has its source changed underneath it ([Controller identity](#controller-identity--self-update)) | `lhpc/core/selfupdate.py:100` `controller_runtime_lock`, `:65` `update_lock` | admission, controller-runtime (exclusive; the console holds it shared), self-update lock | `state/locks/` | `tests/core/test_controller.py::test_apply_refused_while_web_shared_lock_held`, `::test_web_shared_fails_closed_while_apply_exclusive_held` |
-| self-update | one-click update runs only on the canonical, unoverridden units; boot restore also on the next release's units (`compatible`, named with the repair) ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py:623` `verify`, `:667` `integration`; `lhpc/core/service_boot_restore.py:44` `_web_integration_proven` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit`, `::test_the_next_releases_units_read_compatible_for_any_root`; `tests/golden/test_golden_boot_restore.py::test_restores_on_the_next_releases_web_unit_and_says_so` |
-| self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py:962` `self_update_trigger`, `:1089` `_self_update_run_service_locked`, `:1348` `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
-| self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py:651` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
-| radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
+| admission / locks | task admission: no new task while an uninstall, a self-update or a reboot/shutdown is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py` `_admit`; `lhpc/core/service_selfupdate.py` `_task_admission_blocked` | `controller-task-admission` | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
+| admission / locks | named non-blocking locks; a contended operation refuses naming the holder, after waiting up to 5 s for a holder in the same controller process ([Locking](#safety-model)) | `lhpc/core/reslock.py` `operation_lock`; `lhpc/core/services.py` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
+| admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py` `_admission_guard`, `_config_stable`, `_source_operation_guard`, `_lifecycle_guard` | the order is stated once, in [Locking](#safety-model) | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
+| admission / locks | a build holds every source it consumes, transitively, for its whole run; the receipt's revisions are read under those locks ([Locking](#safety-model)) | `lhpc/core/build_plan.py` `lock_sources`, `consumed_lines`; `lhpc/core/service_lifecycle_ops.py` `build`, `spawn_web_job`; `lhpc/core/build_launcher_runtime.py` `run` | the CLI: admission, index, every source exclusive; a detached build: index, its own source exclusive, each dependency's shared | the completion marker (the receipt) and its build-inputs sidecar, written only after the last step | `tests/core/test_build_plan.py::test_a_provider_source_cannot_move_during_a_dependent_build`, `::test_the_receipt_records_the_revision_read_under_the_locks`, `::test_both_paths_lock_and_record_the_same`, `::test_parallel_jobs_share_a_dependency` |
+| config transactions | a save validates everything first, is journalled and rolls back; a crashed save is finished before any writer runs ([Config as a transaction](#safety-model)) | `lhpc/core/config.py` `apply_config_transaction`, `_finish_pending_journal`, `recover_config_journal_at_startup` | config lock `config/.lock` (`lhpc/core/config.py` `config_lock`) | `state/config-txn.json`; `lhpc/core/config.py` `recover_config_transaction` rolls back, or blocks and keeps the journal | `tests/core/test_config.py::test_pending_journal_is_recovered_before_next_save`, `::test_rollback_failure_retains_journal_and_blocks_later`, `::test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`; `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` |
+| config transactions | a malformed `local.toml` is kept; only an absent per-stack file means defaults ([Config as a transaction](#safety-model)) | `lhpc/core/config.py` `_write_local_tables`, `load_stack_config` | config lock (writes only) | `config/local.toml`, `config/stacks/<id>[@band].toml` | `tests/core/test_config.py::test_local_unsupported_structures_block_and_preserve`, `::test_malformed_stack_config_raises_and_is_preserved`, `::test_absent_stack_config_is_defaults`, `::test_web_returns_409_on_malformed_config`; gap: a write over a `local.toml` that is not valid TOML |
+| install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py` `_stage_and_activate`, `_staged_clone_record`, `_activate_held`, `_pending_journals` | admission, `source-txn-index`, source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`, `::test_an_empty_directory_that_is_not_the_recorded_inode_is_kept_and_named`, `::test_an_unrecorded_candidate_is_retained_and_named`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
+| install / recovery | locally added files are carried, never collateral ([Locally added files](#safety-model)) | `lhpc/core/source_fs.py` `carry_extras`, `extras_preserved`; `lhpc/core/install.py` `_activate_held` | as source transactions | `.prev` and its journal are kept while a carry is unproven; the journal says `carrying` while the carry writes, and what it wrote is fsynced before the activation; a copy a stop caught inside the carry no longer matches its full recorded identity and is kept and named, never removed on dev+ino; a prior another process can still write into is kept (`prior-in-use`) until the next recovery | `tests/install/test_source.py::test_an_added_file_colliding_with_the_new_upstream_refuses`, `::test_a_carry_failure_restores_the_prior_and_refuses`, `::test_an_addition_made_after_the_carry_retains_the_archived_prior`, `::test_a_write_through_a_descriptor_opened_before_the_update_is_never_lost`, `::test_a_write_by_a_process_inside_the_prior_without_a_descriptor_is_never_lost`, `::test_a_writer_whose_proc_entries_cannot_be_read_is_still_a_holder`, `::test_the_carry_makes_what_it_wrote_durable`; `tests/install/test_source_interruption.py::test_a_power_loss_after_the_carry_wrote_is_recovered_and_retried`, `::test_a_directory_on_a_recycled_inode_in_carrying_is_never_removed` |
+| install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py` `stop`, `verify_owned`; `lhpc/core/procident.py` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
+| install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py` `_walk_parent`, `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
+| install / recovery | a path that cannot be examined is never absent: only ENOENT/ENOTDIR read as absent, any other error refuses or keeps; a `.git` symlink that does not resolve is present ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py` `probe_exists`, `probe_stat`; `lhpc/core/firewall_helper.py` `_read_json_state` (the stdlib-only helper's inline rule) | none | the receipt, journal or record is kept while its path cannot be examined | `tests/core/test_existence_probe.py::test_any_other_error_is_unknown_with_the_error`, `::test_an_unexaminable_path_is_never_absent`, `::test_a_git_symlink_that_does_not_resolve_is_never_clean`; `tests/host/test_firewall.py::test_a_journal_that_cannot_be_examined_is_not_absent` |
+| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py` `uninstall` | admission, config stability (shared), every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
+| install / recovery | boot restore replays saved configuration through the gated start, honours stop notes ([operations](operations.md#not-a-supervisor)) | `lhpc/core/service_boot_restore.py` `boot_restore_run`, `_write_stop_intent` | admission and the normal start locks | `state/boot-restore.json`, `state/stop-intent/<stack>.json`; `lhpc/core/service_boot_restore.py` `_boot_journal_recovery` consumes an item left *attempting*, never retries it | `tests/core/test_boot_restore.py::test_crash_after_attempting_never_retries_but_pending_survives`, `::test_boot_restore_run_honors_stop_intent_end_to_end`, `::test_driver_admission_refusal_consumes_nothing` |
+| install / recovery | a wheel installed into a fresh venv runs ([Packaging](#safety-model)) | `lhpc/core/assets.py` `asset_path` | none | — | `tests/repo/test_packaging.py::test_data_assets_resolve`; gap: no test installs the wheel into a fresh venv |
+| binary channel | a failed install, or one interrupted before its commit, is rolled back to the previous one; one interrupted after its commit stays installed and recovery only completes its cleanup (backups, journal) ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py` `binary_install`, `binary_recover`; `lhpc/core/binary_install.py` `rollback_files` | admission, the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `::test_an_open_binary_transaction_is_recovered_before_already_installed_is_said`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything`, `::test_committed_journal_leaves_the_receipt_authoritative` |
+| binary channel | the receipt is absent, valid, superseded or unsafe; unreadable is never absent ([operations](operations.md#install-channels)) | `lhpc/core/binary_receipt.py` `receipt_state` | none (read) | `state/binary/<stack>.json` | `tests/install/test_binary_channel.py::test_malformed_receipt_is_unsafe_never_absent`, `::test_superseded_when_txn_id_differs`, `::test_absent_when_no_receipt` |
+| binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
+| PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py` `run_server`, `_trusted_host`, `_csrf_ok`, `_set_headers`; `lhpc/core/config.py` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
+| PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
+| PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py` `invalidates_snapshot`; `lhpc/core/services.py` `invalidate_snapshot`, `build_snapshot`; the tables `lhpc/core/snapshot_memo.py` `SNAPSHOT_NEUTRAL`, `SNAPSHOT_NEUTRAL_WRITERS` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
+| PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py` `spawn_web_job`, `active_jobs`, `prune_logs`; `lhpc/core/webjob_gate.py` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
+| PKI / web | no shell: a validated value is always one argv token ([No shell](#safety-model)) | `lhpc/core/commands.py` `expand_argv` | none | — | `tests/core/test_structured_exec.py::test_hostile_value_stays_one_token`, `::test_started_process_argv_is_not_a_shell` |
+| PKI / web | typed validation; an `@file:` secret that is missing, empty, a symlink, not a regular file or over 64 KiB blocks the launch; a `pkg-config` failure aborts a build ([Typed validation](#safety-model)) | `lhpc/core/validators.py` `validate_param`; `lhpc/core/commands.py` `build_env`, `build_step_argv`; `lhpc/core/runtime_fs.py` `read_secret_text` | none | — | `tests/core/test_validators.py::test_start_rejects_malicious_runparam`; `tests/core/test_structured_exec.py::test_at_file_secret_missing_blocks`, `::test_at_file_secret_empty_blocks`, `::test_a_secret_that_is_not_a_bounded_regular_file_is_a_command_error` (a symlink; a 1 MiB file), `::test_a_fifo_secret_is_refused_without_blocking` (not a regular file); `tests/stacks/test_daemon_bounds.py::test_build_step_argv_pkgconfig_failure_fails_closed`; gap: a secret the service user cannot read (no read permission); the 64 KiB bound itself (no test reads a file just over it) |
+| PKI / web | an unverified clock never dates a certificate ([operations](operations.md#clock)) | `lhpc/core/clock.py` `verdict`, `clock_refusal` | the caller's | the original certificate stays active on a refusal | `tests/core/test_clock_gate.py::test_every_mutating_path_refuses_an_unverified_clock`, `::test_a_refused_reissue_leaves_the_original_certificate_active` |
+| firewall | the helper's apply is journalled and verified; a crash is finished forward or rolled back ([firewall](firewall.md)) | `lhpc/core/firewall_helper.py` `op_apply`, `op_check`, `recover` | root flock `/etc/lhpc/.firewall.lock` | `/etc/lhpc/firewall.{journal,meta,snapshot,transition}.json`, receipt `/run/lhpc-firewall/check.json`; a corrupt journal fails closed | `tests/host/test_firewall.py::test_recovery_finishes_promoted_apply_forward`, `::test_recovery_rolls_back_unpromoted_apply`, `::test_corrupt_journal_fails_closed`, `::test_apply_happy_path_writes_snapshot_and_verified_receipt` |
+| firewall | remote exposure only with this boot's verified receipt, else loopback-only; a stale helper is named before the reboot ([firewall](firewall.md)) | `lhpc/core/service_firewall.py` `firewall_gate_activation`, `firewall_boot_gate`, `firewall_reapply_notice` | the caller's | the boot receipt above | `tests/host/test_firewall.py::test_gate_refuses_even_already_exposed_when_unverified`, `::test_boot_gate_falls_back_to_loopback_when_unverified`, `::test_boot_gate_fails_closed_when_fallback_cannot_stage`, `::test_reapply_notice_only_for_a_stale_helper` |
+| self-update | an unsafe controller identity blocks apply ([Controller identity](#controller-identity--self-update)) | `lhpc/core/services.py` `controller_identity_live` | none (read) | the cached verdict in the self-update envelope | `tests/core/test_controller.py::test_identity_ok`, `::test_missing_checkout_is_not_applicable_not_unsafe`, `::test_self_update_apply_blocked_by_unsafe_identity` |
+| self-update | a running console never has its source changed underneath it ([Controller identity](#controller-identity--self-update)) | `lhpc/core/selfupdate.py` `controller_runtime_lock`, `update_lock` | admission, controller-runtime (exclusive; the console holds it shared), self-update lock | `state/locks/` | `tests/core/test_controller.py::test_apply_refused_while_web_shared_lock_held`, `::test_web_shared_fails_closed_while_apply_exclusive_held` |
+| self-update | one-click update runs only on the canonical, unoverridden units; boot restore also on the next release's units (`compatible`, named with the repair) ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py` `verify`, `integration`; `lhpc/core/service_boot_restore.py` `_web_integration_proven` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit`, `::test_the_next_releases_units_read_compatible_for_any_root`; `tests/golden/test_golden_boot_restore.py::test_restores_on_the_next_releases_web_unit_and_says_so` |
+| self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py` `self_update_trigger`, `_self_update_run_service_locked`, `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
+| self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
+| radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
 | radio / band ownership | the SPI bus is shared through the daemon's `spi0.lock` by the daemon and the Reticulum node; meshtasticd takes no part and is admitted beside the daemon only on the opposite band ([Radios](#radios-bands-and-resource-claims)) | the daemon, not LHPC; LHPC models it as the `spi.bus.0` / `spi.bus.0.unlocked` claims | the daemon's flock `state/loraham/spi0.lock` | — | `tests/core/test_resources_conflicts.py::test_cooperative_peers_do_not_conflict`, `::test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi`; gap: the lock itself (daemon side) |
-| radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py:179` `parse_conf_reply`, `:519` `apply_set`; `lhpc/core/probes/backends.py:797` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_bounds.py::test_oversized_response_rejected`, `::test_too_many_tokens_rejected`; `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
-| radio / band ownership | truthful outcomes: a start fails unless every required component is verified ready, and a failed start stops again what it launched or names what stayed up; a stop counts only when the process ceased and its endpoints are gone; an update with a failed part reports failure; the CLI exit status follows the result ([Truthful outcomes](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:900` `_start_coordinator`, `:1690` `_roll_back_start`, `:2621` `_stop_impl`; `lhpc/core/start_plan.py:77` `verify_start`; `lhpc/core/outcomes.py:71` `applied_ok`; `lhpc/core/service_maintenance.py:1781` `update`; `lhpc/adapters/cli/main.py:254` `_render` | the start, stop and update locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`, `::test_dependent_not_started_when_daemon_unready`; `tests/core/test_process_ownership.py::test_a_failed_start_leaves_nothing_of_the_stack_running`, `::test_a_failed_start_stops_the_daemon_only_if_it_launched_it`, `::test_a_failed_start_names_what_it_could_not_stop`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers`; `tests/core/test_known_working.py::test_candidate_clear_failure_is_truthful_incomplete`; `tests/cli/test_cli.py::test_status_unknown_stack_exits_one`; gap: a daemon start's check of each band's CONF socket separately (only a daemon that is not ready at all is driven); the web flash agreeing with the CLI exit status (no test drives one result through both) |
-| radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py:2812` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
-| lab | a simulated reboot kills only owned groups and runs the real boot restore ([testlab](testlab.md)) | `testlab/lhpc_testlab/ops.py:286` `power` | admission (waited for) | the lab root's boot identity | `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`; `testlab/tests/acceptance/test_power_network.py::test_simulated_reboot_advances_boot_identity_and_recovers` |
-| lab | the lab's controls never signal a reused PID ([testlab](testlab.md)) | `testlab/lhpc_testlab/supervisor.py:118` `pid_alive`, `:132` `_nginx_master` | none | — | `testlab/tests/unit/test_testlab.py::test_nginx_ctl_never_signals_a_reused_pid`, `::test_a_reused_pid_naming_the_config_is_not_the_lab_nginx`, `::test_the_lab_nginx_master_is_verified_and_signalled`, `::test_check_does_not_take_a_reused_pid_for_the_fake_gpsd` |
+| radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py` `parse_conf_reply`, `apply_set`; `lhpc/core/probes/backends.py` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_bounds.py::test_oversized_response_rejected`, `::test_too_many_tokens_rejected`; `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
+| radio / band ownership | truthful outcomes: a start fails unless every required component is verified ready, and a failed start stops again what it launched or names what stayed up; a stop counts only when the process ceased and its endpoints are gone; an update with a failed part reports failure; the CLI exit status follows the result ([Truthful outcomes](#safety-model)) | `lhpc/core/service_lifecycle_ops.py` `_start_coordinator`, `_roll_back_start`, `_stop_impl`; `lhpc/core/start_plan.py` `verify_start`; `lhpc/core/outcomes.py` `applied_ok`; `lhpc/core/service_maintenance.py` `update`; `lhpc/adapters/cli/main.py` `_render` | the start, stop and update locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`, `::test_dependent_not_started_when_daemon_unready`; `tests/core/test_process_ownership.py::test_a_failed_start_leaves_nothing_of_the_stack_running`, `::test_a_failed_start_stops_the_daemon_only_if_it_launched_it`, `::test_a_failed_start_names_what_it_could_not_stop`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers`; `tests/core/test_known_working.py::test_candidate_clear_failure_is_truthful_incomplete`; `tests/cli/test_cli.py::test_status_unknown_stack_exits_one`; gap: a daemon start's check of each band's CONF socket separately (only a daemon that is not ready at all is driven); the web flash agreeing with the CLI exit status (no test drives one result through both) |
+| radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
+| lab | a simulated reboot kills only owned groups and runs the real boot restore ([testlab](testlab.md)) | `testlab/lhpc_testlab/ops.py` `power` | admission (waited for) | the lab root's boot identity | `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`; `testlab/tests/acceptance/test_power_network.py::test_simulated_reboot_advances_boot_identity_and_recovers` |
+| lab | the lab's controls never signal a reused PID ([testlab](testlab.md)) | `testlab/lhpc_testlab/supervisor.py` `pid_alive`, `_nginx_master` | none | — | `testlab/tests/unit/test_testlab.py::test_nginx_ctl_never_signals_a_reused_pid`, `::test_a_reused_pid_naming_the_config_is_not_the_lab_nginx`, `::test_the_lab_nginx_master_is_verified_and_signalled`, `::test_check_does_not_take_a_reused_pid_for_the_fake_gpsd` |
 
 Open gaps: [backlog.md](backlog.md).
 
diff --git a/tests/README.md b/tests/README.md
index 7b2e481f..4c49ad64 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -68,7 +68,9 @@ through `interrupts.py`: its module pins the operation's durable writes and fail
 1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
    to assert its contents, no pinning statement order or helper names. Exception: a whole-module
    negative invariant ("this file spawns nothing") no driven path can prove, saying so in its
-   docstring and with a behavioural twin.
+   docstring and with a behavioural twin. Second exception: a documentation-integrity test may
+   assert that a symbol named by `docs/` is defined in its file; it asserts existence, never
+   behaviour or source text.
 2. **Exact where exactness is the contract.** Systemd units, nginx config, firewall rules, generated
    argv, config rendering, persisted schemas, receipts, permissions and canonical paths are compared
    byte for byte. Human sentences are not: assert the typed field, the state, or the command token
diff --git a/tests/repo/test_doc_owner_pointers.py b/tests/repo/test_doc_owner_pointers.py
new file mode 100644
index 00000000..1763f275
--- /dev/null
+++ b/tests/repo/test_doc_owner_pointers.py
@@ -0,0 +1,45 @@
+"""The invariant table in docs/architecture.md names each owner as `path` `symbol` (continued by
+`, `symbol`` in the same file). Each named symbol must still be defined in its file, so a rename
+or a removal fails here instead of leaving the doc pointing at nothing.
+
+A documentation-integrity test (tests/README.md, rule 1's second exception): it reads the
+production files only to find each name's definition, never behaviour or source text.
+"""
+
+import re
+
+import repo_paths
+
+ROOT = repo_paths.REPO
+
+_POINTER = re.compile(r"`((?:lhpc|testlab)/[\w/]+\.py)` `(\w+)`((?:, `\w+`)*)")
+
+
+def _table_rows():
+    text = (ROOT / "docs" / "architecture.md").read_text()
+    return [row for row in text.splitlines() if row.startswith("|")]
+
+
+def _pointers():
+    for row in _table_rows():
+        for m in _POINTER.finditer(row):
+            yield m.group(1), m.group(2)
+            for name in re.findall(r"`(\w+)`", m.group(3)):
+                yield m.group(1), name
+
+
+def test_every_named_owner_is_defined_in_its_file():
+    pointers = list(_pointers())
+    assert pointers
+    missing = []
+    for path, name in pointers:
+        src = ROOT / path
+        defined = rf"^(\s*(async\s+)?(def|class)\s+{name}\b|{name}\s*[:=])"
+        if not (src.is_file() and re.search(defined, src.read_text(), re.MULTILINE)):
+            missing.append(f"{path} `{name}`")
+    assert not missing, f"docs/architecture.md names owners not defined there: {', '.join(missing)}"
+
+
+def test_owner_pointers_carry_no_line_numbers():
+    numbered = [row[:60] for row in _table_rows() if re.search(r"\.py:\d+`|`:\d+`", row)]
+    assert not numbered, f"a `path:N` pointer drifts and escapes the check above: {numbered}"
```

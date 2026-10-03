# Code report B6 — CR7-13b, CR3-3b, CR10-1

Base `main` e5187f70 (v0.11.10). Plan: `plans/PLAN-B6.md` (commit c78dd5a). Every test command below ran with
`python -m pytest -q -p no:cacheprovider`. Red-before runs used a git worktree at the parent commit with only the
new test module copied in, and `PYTHONPATH=<worktree>:<worktree>/testlab`, so the parent's `lhpc` and
`lhpc_testlab` were imported (checked: `lhpc_testlab.ops.__file__` resolved into the worktree).

## Commits

| sha | subject |
|---|---|
| c78dd5a | plans: PLAN-B6 (CR7-13b, CR3-3b, CR10-1) |
| f3a73ce | CR7-13b: an @file: secret is read no-follow, regular-file-only and bounded |
| 8121d5a | CR3-3b: recovery removes a clone a crash interrupted before its journal |
| 320869f | CR10-1: the lab's simulated reboot kills the stacks and runs the real boot restore |

### f3a73ce — CR7-13b
- Files: `lhpc/core/runtime_fs.py` (+16: `read_secret_text`), `lhpc/core/commands.py` (both `@file` reads,
  docstring, import), `tests/core/test_structured_exec.py` (+40), `docs/architecture.md`, `CHANGELOG.md`.
- Tests:
  - `tests/core/test_structured_exec.py::test_a_secret_that_is_not_a_bounded_regular_file_is_a_command_error`
    `[symlink|oversize × @file:|@file?:]` — red-before **yes**.
  - `tests/core/test_structured_exec.py::test_a_fifo_secret_is_refused_without_blocking[@file:|@file?:]` —
    red-before **yes** (the call was still blocked after 5 s; the test then releases it).
  - Preservation: `test_at_file_secret_missing_blocks`, `..._empty_blocks`, `..._present_is_read`, the build
    launcher's `test_build_launcher_at_file_missing_blocks`; on the B4 stack also B4's
    `test_non_utf8_secret_file_is_a_command_error` (green).
- Red-before: at c78dd5a, `pytest tests/core/test_structured_exec.py -k "bounded_regular or fifo_secret"` →
  `6 failed, 37 deselected`.
- At f3a73ce: `pytest tests/core/test_structured_exec.py tests/core/test_runtime_fs.py tests/repo` →
  `524 passed, 5 skipped`; `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` →
  `All checks passed!`.

### 8121d5a — CR3-3b
- Files: `lhpc/core/install.py` (+117/−3), `tests/install/test_source.py` (+88), `docs/architecture.md`,
  `CHANGELOG.md`.
- Tests (all in `tests/install/test_source.py`):
  - `test_recovery_removes_a_clone_killed_before_its_journal` — a forked child adopts `app` and SIGKILLs itself
    inside the staging (copy stub: copy, then die); red-before **yes** (`assert not staged[0].exists()` fails:
    the partial tree survives recovery).
  - `test_a_staging_record_of_a_live_staging_is_left_alone` — source lock held → record and candidate kept;
    released → both gone. Red-before yes (new mechanism; the parent has no record helper → AttributeError).
  - `test_a_staging_record_never_removes_an_unproven_candidate[other-inode|unrecorded]` — red-before yes (same
    reason); pins that an unproven leaf is never removed.
  - `test_a_staging_record_defers_to_its_journal` — record + `planned` journal whose candidate ident is stale:
    record cleared, candidate kept (B4's CR3-3a rule wins). Red-before yes (same reason); also pins the
    records-before-journals order (with the order reversed the record would remove the candidate).
- Red-before: at f3a73ce, `pytest tests/install/test_source.py -k "clone_killed or staging_record"` →
  `5 failed, 228 deselected`, the first with `AssertionError: assert not True` on the candidate's existence.
- At 8121d5a: `pytest tests/install/test_source.py tests/repo` → `623 passed, 6 skipped`; both ruff runs →
  `All checks passed!`.

### 320869f — CR10-1
- Files: `testlab/lhpc_testlab/ops.py` (`power`, new `_wait_for_admission`, `_kill_owned_groups`),
  `testlab/lhpc_testlab/spawn.py` (docstring), `testlab/tests/unit/test_testlab.py` (+52),
  `testlab/tests/acceptance/test_chain_roundtrip.py`, `docs/testlab.md`, `CHANGELOG.md`.
- Tests:
  - `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`
    `[free|held-by-the-console]` — a real `sleep` in its own session with a v1 ownership record for `kiss`;
    asserts death by SIGKILL and `state/boot-restore.json` with the new boot id and the consumed `kiss` item.
    Red-before **yes** for both (`sleep` still alive after 10 s).
  - Preservation: `test_power_reboot_does_not_tombstone` (green before and after).
  - `testlab/tests/acceptance/test_chain_roundtrip.py::test_simulated_reboot_restores_running_stacks` — now also
    asserts the journal (new boot id, `kiss` succeeded). **Not run here** (slow lab lane, needs the lab image and
    live clones); see Deviations.
- Red-before: at 8121d5a, `pytest testlab/tests/unit/test_testlab.py -k kills_owned` → `2 failed, 33 deselected`.
- At 320869f: `pytest testlab/tests/unit tests/repo` → `445 passed, 5 skipped`; both ruff runs →
  `All checks passed!`.

### Stacked on B4
In a worktree at `origin/claude/busy-heisenberg-vb7z2i` (4091e62), cherry-picking f3a73ce, 8121d5a, 320869f: one
conflict, as planned, in `lhpc/core/commands.py` (`@file:` branch: B6's read line is adjacent to B4's `except`
line). Resolution: B6's `first = runtime_fs.read_secret_text(path).splitlines()` + B4's
`except (OSError, ValueError) as exc:` line. The other two applied cleanly. Then
`pytest tests/core/test_structured_exec.py tests/core/test_runtime_fs.py tests/core/test_config.py
tests/install/test_source.py testlab/tests/unit tests/repo` → `965 passed, 6 skipped`; both ruff runs →
`All checks passed!`.

## Self-audit proof

### CR7-13b
- (a) An `@file:`/`@file?:` secret is used only if it is a regular, non-symlink file of ≤ 64 KiB of UTF-8 text;
  otherwise the launch/build gets a `CommandError`; nothing can block the read.
- (b) `runtime_fs.read_secret_text`: `os.open(... O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)` (symlink → ELOOP; FIFO
  open does not wait), `_require_regular_fd` (fstat, non-regular → OSError), `_read_fd_bounded(fd, 64 KiB)`
  (oversize → OSError), decode failure → OSError. `build_env` already maps OSError to `CommandError` and
  `FileNotFoundError` (optional form) to `""`.
- (c) Could break: every stack's build/launch env. The only manifest secret is
  `@file?:{runtime}/config/secrets/xr_pw`, written by LHPC's HMAC actions as a regular file — unaffected
  (`test_at_file_secret_present_is_read`, the HMAC suites untouched). The web-job launcher imports the same
  `build_env` (`build_launcher_runtime.py:283`). A dangling-symlink optional secret was `""` and is now a
  `CommandError`; that is the defect (a symlink is never followed), named in the plan.
- (d) Re-read `_require_regular_fd`: it closes the fd before raising, so `read_secret_text` must not close it
  again — it does not (the `try/finally` starts after the check). Found nothing to change.

### CR3-3b
- (a) A staging killed before its journal leaves a record naming its candidate; the next recovery removes that
  candidate if — and only if — the stager is provably dead (source lock acquired), no journal owns the
  candidate, and the leaf is the recorded inode (or, with none recorded, an empty directory).
- (b) Record: `_staged_clone_record` (exclusive create before the candidate exists, removed on exit of the
  transaction's `with`), `_note_staged` after both `create_candidate` calls. Recovery: `_recover_scan` handles
  `*.staging` before `*.json`; `_recover_staged_clone` validates (managed destination, controller candidate name,
  record name, ident shape), takes `reslock.operation_lock(source lock)` (busy → return ""), journal present →
  clear the record only, else `source_fs.remove_bound(..., [dev, ino])` / `os.rmdir`, record cleared only after.
- (c) Could break: (1) blocking — a `.staging` leaf never matches `*.json`, which is what `_pending_journals`,
  `build_launcher_runtime.py:233` and `auto_install_welcome` test; a symlinked entry already blocked before.
  (2) B4's CR3-1/2/3 — journal recovery is unchanged; a record with a journal is cleared without touching the
  candidate, before journal recovery runs (`test_a_staging_record_defers_to_its_journal`, green on the B4 stack).
  (3) Self-update — the controller checkout is not in `_managed_source_dests` and self-update does not call
  `_stage_and_activate`. (4) A live clone — the stager holds the source lock throughout (`adopt_source` and the
  services' guard), so recovery is `ResourceBusy` and touches nothing
  (`test_a_staging_record_of_a_live_staging_is_left_alone`). (5) A record that cannot be written — the staging
  proceeds as before (no new refusal). (6) `test_shared_remote.py:155` asserts an empty `source-txn` after a
  refused install: the refusal happens before staging, and records are removed on exit anyway.
- (d) Re-read the `with A as txn, B as clone_rec` exit order: B (record) exits before A (transaction), so the
  record is gone before the candidate handles close; and the record is created before the journal preflight,
  so a refused preflight creates and removes it with no other effect. Found nothing to change.

### CR10-1
- (a) The lab's Reboot kills every owned process group its record still proves, keeps the records, advances the
  boot id and runs the production `boot_restore_run`, whose journal records what came back.
- (b) `_kill_owned_groups`: `owned_inventory` → `verify_owned` (boot id, start time, pgid, sid, not the
  controller's group) → `os.killpg(pgid, SIGKILL)` → `_wait_ceased`. Then `advance_boot`, `_respawn_gpsd`,
  `svc._web_integration_proven = lambda: (True, "")`, `_wait_for_admission`, `svc.boot_restore_run()`.
- (c) Could break: (1) a reused pid — never signalled (`verify_owned` before the kill). (2) The lab's other reboot
  rows: `test_power_reboot_does_not_tombstone` (green), `test_power_network.py` asserts "simulated reboot" and
  "host untouched" in the event log — both kept in the new log line. (3) The web-triggered reboot: the console
  request still holds its power admission when the helper starts; the driver would refuse (see Observations), so
  the lab waits for admission, bounded 30 s (`[held-by-the-console]` case, which was red with the first version
  of this commit: no journal). (4) poweroff — unchanged code path.
- (d) Re-read the `spawn.py` docstring: it still said the helper "stops owned stacks" — fixed in this commit.

## Deviations
1. CR3-3b is +117/−3 lines in `install.py`, not ~60: the validation and the four recovery outcomes, each
   commented. Its tests are named and split differently from the plan's sketch: the plan's
   `test_a_staged_clone_whose_source_lock_is_held_is_left_alone` is
   `test_a_staging_record_of_a_live_staging_is_left_alone`, and two further pinning tests were added
   (`..._never_removes_an_unproven_candidate`, `..._defers_to_its_journal`).
2. CR10-1 adds `_wait_for_admission` and the `[held-by-the-console]` case, not in the plan: found while testing
   (Observations 1). It also edits the `spawn.py` docstring (a sentence became untrue). It is ~60 lines, not ~35.
3. The acceptance test `test_simulated_reboot_restores_running_stacks` was changed but not run (slow lab lane,
   lab image + live remotes). Residual risk: if `verify_owned` cannot prove the lab's kiss process on a runner
   (the test's comment says status reads can be unreliable under nested qemu), it is not killed, the restore
   start meets port 8001 in use and the new `succeeded` assertion fails — which would show the reboot is not
   faithful there, rather than pass vacuously as before.
4. History: the plan commit was amended once (a home path written as `$HOME`) by an autosquashed fixup before
   the push; nothing had been pushed.

## Observations (not changed)
1. Production `boot_restore_run` reports a busy task-admission flock as "driver failure (ResourceBusy: …)",
   not as the `admission_blocked` refusal it has for gate refusals. Nothing is consumed either way; on a box the
   unit runs after boot, so it does not arise there. Left for the maintainer.

## Summary lines
- f3a73ce: `524 passed, 5 skipped`; 8121d5a: `623 passed, 6 skipped`; 320869f: `445 passed, 5 skipped`;
  stacked on B4: `965 passed, 6 skipped`. Every `ruff check lhpc testlab` and `ruff check tests --select F,E9`:
  `All checks passed!`.

## Adversarial self-review
Re-read the whole diff as a reviewer paid per finding. Found and fixed:
1. CR10-1: a web-triggered reboot could kill the stacks and then have the restore refused by the triggering
   request's admission — fixed by `_wait_for_admission` + test case.
2. CR10-1: `spawn.py` docstring described the old behaviour — fixed.
3. CR3-3b: the first test version asserted the message before the candidate, so red-before showed a message
   mismatch, not the defect — reordered.
4. CR3-3b: the `OSError` branch's message said "source parent unsafe" although it also covers a lock file that
   cannot be opened — reworded.
5. Packet hygiene: two home paths written with `~` — now `$HOME`; the acceptance assertions moved next to the
   boot-id check.

Checked and left: `_require_regular_fd` leaks the fd if `fstat` itself fails (shared with `read_bytes`;
unchanged); the fork in `test_recovery_removes_a_clone_killed_before_its_journal` is safe because the suite
turns only unhandled thread exceptions into errors and starts no threads in that module.

## Correction 1

New SHAs after the amend (autosquashed fixup into the CR3-3b commit; nothing else edited):

| before | after | patch-id (`git patch-id --stable`) |
|---|---|---|
| c78dd5a plan | c78dd5a (same commit) | 55e1beec… unchanged |
| f3a73ce CR7-13b | f3a73ce (same commit) | 013f91ff… unchanged |
| 8121d5a CR3-3b | **19c3527** | 5618f823… → b1c59924… (changed: the fix) |
| 320869f CR10-1 | fe8ae50 | 2c7e46f0… unchanged |
| fe72dd6 report | (this commit) | 787ec160… before this section |

`git range-diff e5187f70 <old head> <new head>`: `1 = 1`, `2 = 2`, `3 ! 3`, `4 = 4`, `5 = 5`. The only tree
difference between the old and new CR10-1 commits is `tests/install/test_binary_install.py` (+19/−5). The SHAs in
the sections above are the pre-amend ones; read 8121d5a as 19c3527 and 320869f as fe8ae50.

### The defect
The maintainer's local full suite failed 3 tests in `tests/install/test_binary_install.py` (on this branch's own
head too): `test_a_stale_clone_moves_to_the_manifest_pin_not_an_older_known_working`,
`test_a_missing_clone_is_adopted_at_the_manifest_pin_not_an_older_known_working`,
`test_an_ordinary_pinned_update_still_resolves_known_working`, all with
`TypeError: _record_adoption_target.<locals>._stage() got an unexpected keyword argument 'clone_rec'`. CR3-3b gave
`Installer._stage_candidate` a new keyword `clone_rec=None` and passes it from `_stage_and_activate`; the test fake
`_record_adoption_target` (since 3aed16fe, v0.11.10) replaces `_stage_candidate` with the old signature.

### Why the session missed it
It ran `tests/install/test_source.py` (where CR3-3b's own tests live) and `tests/repo`, and stopped there. It did
not grep `tests/` and `testlab/` for other callers or fakes of the function whose signature it changed, so the
monkeypatched fake in another module of the same directory was never run.

### Fix (in 19c3527)
- `_record_adoption_target._stage` now takes `expected_pin="", clone_rec=None` — the real signature, explicitly,
  not `**kw`, so the next signature change fails here again (docstring says so).
- It takes an optional `recs` list and records `(dest, staging, clone_rec, the record leaf's text at staging
  time)`. `test_a_missing_clone_is_adopted_at_the_manifest_pin_not_an_older_known_working` asserts that the
  staging got the record the real path holds for it: `clone_rec` is not None and names
  `_staged_clone_path(dest, staging)`; the leaf's payload is `{"state": "staging", "source_rel", "candidate_rel",
  "ident": None}` (the fake runs before `_note_staged`); the leaf is gone after the adoption returns. (The leaf is
  read from disk, not through `clone_rec.read()`: the marker's retained fd is write-only — the first attempt
  failed with EBADF.)
- Red-before: at the old head, `pytest tests/install/test_binary_install.py` → `3 failed, 109 passed` (the three
  TypeErrors). After: `112 passed`.
- Mutation check: with `_stage_and_activate` changed to pass `clone_rec=None`, the strengthened test fails
  (`1 failed`); reverted.

### Grep
`git show 8121d5aa -- lhpc` changes one existing signature: `Installer._stage_candidate` (adds `clone_rec`). The
other functions it touches are new (`_staged_clone_path`, `_staged_clone_payload`, `_staged_clone_record`,
`_note_staged`, `_recover_staged_clone`) or keep their signature with new behaviour (`_stage_and_activate`'s
`with`, `recover_source_activations` → `_recover_scan`). Searched `tests/` and `testlab/` (`*.py`) for:
- `_stage_candidate` → only `tests/install/test_binary_install.py` (the fake; one real caller in `lhpc/core/install.py`).
- `_staged_clone`, `_note_staged`, `_recover_staged_clone` → `tests/install/test_source.py`,
  `tests/install/test_binary_install.py` (the new assertion).
- `recover_source_activations`, `_recover_one` → `tests/install/test_source.py` only.
- `open_marker_excl`, `OwnedMarker` (used by the new record) → `tests/core/test_runtime_fs.py` (no fake of
  either; direct unit tests).
- `create_candidate`, `ManagedSourceTransaction` (wrapped by the changed `with`) → no test or testlab hits.
- testlab: no hit for any of these names.

### Results (run in this session; venv with `pip install -e .[dev]`)
| command | result |
|---|---|
| `pytest tests/install/test_binary_install.py` | `112 passed` |
| `pytest tests/install/test_source.py` | `232 passed, 1 skipped` |
| `pytest tests/core/test_runtime_fs.py` | `90 passed` |
| `pytest tests/install` | `89 failed, 1057 passed, 3 skipped` — see below |
| `pytest tests/repo` | `391 passed, 5 skipped` |
| `ruff check lhpc testlab` | `All checks passed!` |
| `ruff check tests --select F,E9` | `All checks passed!` |
| stacked on B4 (4091e62 + f3a73ce, 19c3527, fe8ae50; same one planned conflict, same resolution): `pytest tests/install/test_binary_install.py tests/install/test_source.py tests/core/test_structured_exec.py tests/core/test_runtime_fs.py tests/core/test_config.py testlab/tests/unit tests/repo` | `1077 passed, 6 skipped` |

The 89 `tests/install` failures are all in `tests/install/test_bootstrap_deps.py` and come from this container:
it runs as root with no non-root user, so `bootstrap-deps.sh` exits 2 with "no non-root operator for group
grants / power controls". The same command at `main` e5187f70 (worktree, its own `lhpc` on `PYTHONPATH`) gives
`89 failed, 1052 passed, 3 skipped` with the identical failure set (`diff` of the sorted FAILED lists empty);
the +5 passed are B6's new tests. A first run also failed 3 archive tests in `test_binary_install.py` with
`FileNotFoundError: 'zstd'` (no host `zstd`); after installing `zstd` they pass, which is the run above. No full
suite was run.

### Adversarial self-review of the correction
- The name check (`rec.name == _staged_clone_path(...).name`) uses the same helper as the code; on its own it
  would be near-tautological. The payload check against the leaf on disk and the mutation run are what prove the
  pass-through. Kept both.
- `payload` would be `None` if the leaf were missing; the preceding `rec is not None` assert fails first with a
  clear message. Left.
- The fake now reaches `self._staged_clone_path`, a private helper; if it is renamed, this test fails loudly —
  acceptable for a fake of a private method.
- Commit identity and messages re-checked: no attribution lines (`git log --format='%an %cn%n%B'
  e5187f70..HEAD`).

## Correction 2

New SHAs after the amend (autosquashed fixup into the CR3-3b commit; the report commit is amended with this
section; nothing else edited):

| before | after | patch-id (`git patch-id --stable`) |
|---|---|---|
| c78dd5a plan | c78dd5a (same commit) | 55e1beec… unchanged |
| f3a73ce CR7-13b | f3a73ce (same commit) | 013f91ff… unchanged |
| 19c3527 CR3-3b | **40cfd98** | b1c59924… → dd5e9f1b… (changed: the fix) |
| fe8ae50 CR10-1 | 7a47ed3 | 2c7e46f0… unchanged |
| 3701256 report | (this commit) | 6981362b… before this section |

`git range-diff e5187f70..<old head> e5187f70..<new head>` before this section: `1 = 1`, `2 = 2`, `3 ! 3`,
`4 = 4`, `5 = 5`. `git diff 19c3527 40cfd98` and `git diff fe8ae50 7a47ed3` are the same two files
(`lhpc/core/install.py` +15/−3, `tests/install/test_source.py` +36): the fix is the only tree change.

### The finding
`_note_staged()` called `rec.rewrite(...)` right after `create_candidate()` with no containment. The plan
(PLAN-B6 §2, risk (c)) says "a record write failure: the clone proceeds as today (best-effort, like the clone
log)". Today `OwnedMarker.rewrite` turns its own write errors (`ftruncate`/`write`/`fsync`) into `False`, so
the escape needs an exception outside that inner `try` or a later change to `rewrite`. Either way, the call
site did not enforce the plan's rule. An `OSError` raised there propagated out of `adopt_source`. A
`PathContainmentError` became "managed source parent is unsafe" (`failed`). A `False` return was ignored without
a word.

### Fix (in 40cfd98)
`_note_staged` now wraps the rewrite in `try/except (OSError, PathContainmentError)`. On an exception, or a
`False` return, it writes one line to stderr:
`staging record <leaf> could not record the candidate[ (<error>)] — install continues`. Then it returns, and the
staging goes on. The record has no error type of its own (`OwnedMarker` raises `OSError` only), so those two
types are the whole set. The record stays as written before the candidate existed (`ident: null`) and is still
removed when the staging ends. After a crash, the recovery rule handles it as designed: with no inode recorded,
the candidate is removed only while it is an empty directory, otherwise kept.

The logging path: `install.py` has no logger. stderr is what the core's job code already uses for one-line
diagnostics (`build_launcher_runtime.py`), and it lands in the job's log. The adoption clone log
(`logs/adopt-<comp>.log`) was not used: it is opened after the first `_note_staged` call, does not exist without
a remote, and is already closed at the second call.

### Test
`tests/install/test_source.py::test_a_failing_staging_record_rewrite_never_stops_the_install`
`[oserror|containment|lost]`. It wraps `runtime_fs.open_marker_excl` so that only the `.staging` record's marker
gets a failing `rewrite` (raises `OSError(EIO)`, raises `PathContainmentError`, or returns `False`). The
transaction journal, also an `OwnedMarker`, is left alone. The test asserts that the dev adoption's status is
`done`, that the stderr has exactly one "staging record … install continues" line per failed rewrite, and that
`state/source-txn` is empty afterwards. This path creates the candidate twice (no remote → reset → local copy),
so it calls `_note_staged` twice. The test counts the calls rather than assuming one.
- Red-before: with `lhpc/core/install.py` at 19c3527 (`git stash` of the production change only), `pytest
  tests/install/test_source.py -k failing_staging_record` → `3 failed`: `oserror` with `OSError: [Errno 5]`
  escaping, `containment` with `assert 'failed' == 'done'`, and `lost` with no log line (that case already
  continued and only lacked the line). After: `3 passed`.
- A first version patched `OwnedMarker.rewrite` on the class. That also failed the journal's state rewrites, so
  the adoption failed for a reason unrelated to the record. It was narrowed to the `.staging` marker before the
  red/green runs above.

### Grep
No signature changed (`_note_staged(self, rec, dest, staging, handle) -> None` as before). `_note_staged`,
`_staged_clone_record` → no hit in `tests/` or `testlab/` before the new test. `open_marker_excl` (wrapped by the
new test) → `tests/core/test_runtime_fs.py` (direct unit tests, no fake), and now the new test.

### Results (run in this session; venv with `pip install -e .[dev]`, host `zstd` installed)
| command | result |
|---|---|
| `pytest tests/install/test_source.py` | `235 passed, 1 skipped` |
| `pytest tests/install/test_binary_install.py` | `112 passed` |
| `pytest tests/install` | `89 failed, 1060 passed, 3 skipped` — the same 89 `test_bootstrap_deps.py` failures as in Correction 1 (container runs as root, no non-root operator); no other failure |
| `pytest tests/repo` | `391 passed, 5 skipped` |
| `ruff check lhpc testlab` | `All checks passed!` |
| `ruff check tests --select F,E9` | `All checks passed!` |

Before `zstd` was installed, `tests/install` gave `93 failed`. The same command on the unmodified previous head
(worktree importing its own `lhpc`) gave the identical sorted FAILED list (`diff` empty): 89 bootstrap, 3 archive
tests (`FileNotFoundError: 'zstd'`), 1 `test_binary_channel.py` doctor test. No full suite was run.

### Adversarial self-review of the correction
- Found and fixed: the class-wide `rewrite` patch (above) would have exercised the journal, not the record.
- Found and fixed: the first assertion expected exactly one line, but this path makes two record writes. It now
  checks one line per failed rewrite.
- Behaviour change beyond the finding: a `False` return now produces a line where it was silent. Kept, because
  the finding asks for the failure to be logged and a `False` return is that failure.
- Checked and left (observation): if `rewrite` fails after its `ftruncate`, the record leaf can be empty or
  partial. A crash in that staging then leaves a record that recovery reports as "invalid (retained)", and the
  candidate is kept, since nothing proves it. That is the fail-closed side and does not block anything (not
  `*.json`). This was already true before this correction.
- The log line names the record leaf and the error text only: no home path is added beyond what `OSError`
  carries for a path it was given (`rewrite` works on file descriptors, so its errors carry none).
- Mutation: without the `try/except` (the 19c3527 production code), all three cases fail (the red-before run).
- Commit identity and messages re-checked: no attribution lines (`git log --format='%an %cn%n%B'
  e5187f70..HEAD`).

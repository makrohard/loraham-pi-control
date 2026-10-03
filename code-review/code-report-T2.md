# Code report T2 — interruption-and-retry at every durable write

Branch `cons/S2` on `origin/integration/0.12.0` (f257831). Tests only: `git diff
f257831..HEAD -- lhpc testlab | wc -l` → `0`.

## Commits

| sha | subject | files | red-before |
|---|---|---|---|
| ef5f7ad | T2: plan | plans/PLAN-T2.md | — |
| 65e095d | T2: the interruption harness | tests/interrupts.py, tests/README.md, CHANGELOG.md | helper; proven through the modules below |
| 5ac5305 | T2: Settings save | tests/core/test_config_interruption.py | yes (mutation) |
| 98291f0 | T2: boot restore | tests/core/test_boot_restore_interruption.py | yes (mutation) |
| 8b12452 | T2: binary re-install | tests/install/test_binary_install_interruption.py | yes (mutation) |
| b938c4b | T2: source adoption/update | tests/install/test_source_interruption.py | yes (mutation) |
| 374c99a | T2: self-update | tests/install/test_selfupdate_interruption.py | yes (mutation) |
| 3cadc86 | T2: firewall apply | tests/host/test_firewall_interruption.py | yes (mutation) |

Red-before. The modules pin what the shipped recovery does, so they pass on the parent. Proof
that they can fail: one throwaway mutation per recovery path, then `python -m pytest -q
-p no:cacheprovider <module>`, then `git checkout -- <file>` (`git status --short lhpc` is empty
afterwards):

| recovery path mutated | result |
|---|---|
| config.py `recover_config_transaction` drops the journal without restoring | 2 failed, 23 passed |
| service_boot_restore.py `_boot_journal_recovery` prunes nothing | 3 failed, 19 passed |
| binary_install.py `rollback_files` returns success untouched | 6 failed, 64 passed |
| install.py `_finish_or_rollback` only removes the journal | 17 failed, 97 passed, 26 xfailed |
| service_selfupdate.py never promotes a `prepared` record at to_head | 2 failed, 28 passed, 4 xfailed |
| firewall_helper.py `_recover_apply` only removes the journal | 6 failed, 19 passed |

Green: the six modules → `286 passed, 30 xfailed in 42.07s`.

## Known defects (strict xfail — a fix flips them to XPASS, which is red until the mark goes)

- **KD-S1** (source, 20 cases). `Installer._staged_clone_record` (install.py:1276-1293) removes
  the staging record in `finally` on ANY exit. A Ctrl-C during staging (identity note :1302, local
  copy :811/:834), a candidate reset that reports failure (`remove_bound` :711) or a journal that
  cannot be created (:1125) leaves `src/.app.candidate-*` with no record, so
  `recover_source_activations` never names it. The retry succeeds, but the candidate (a full
  clone) stays for good. With journal creation failing, the result also says "left a retained
  journal" although none was written.
- **KD-S2** (source update, 1 case). `carry_extras` (:607) adds the operator's file to the
  candidate after the journal recorded the candidate's `[dev, ino, ctime]` (:1125). A crash
  between the carry and the activation rename (:2033) leaves `src/app` ABSENT (archived to
  `.app.prev`). Recovery answers "staged candidate is not the recorded one … (everything
  retained)", so the stack has no active source until someone fixes it by hand. This contradicts
  architecture.md "A failed activation never destroys the active source" for a crash. The
  in-process failure is fine.
- **KD-S3** (source update, 3 cases). The quarantined prior's removal (:1592) failing or being
  interrupted after activation leaves `src/..app.prev.quarantine-*`. A failure is reported as
  "recovery-required"; recovery says "active source intact" and closes the journal but keeps the
  directory. `uninstall`/`clean` then refuse (service_maintenance.py:1943) until it is removed
  by hand.
- **KD-S4** (source adoption, 2 cases). `require_atomic_rename` (source_fs.py:168-182) cleans
  its `.lhpc-atomic-probe-*` directory only on `OSError`; a Ctrl-C leaves it in `src/`.
- **KD-U1** (self-update, 4 cases). An anchor `refs/lhpc/selfupdate/<txid>` survives in two
  cases. First, a Ctrl-C between `create_anchor` (service_selfupdate.py:662) and the journal
  write (:665): only an `OSError`-reported failure deletes it. Second, `delete_anchor`
  (selfupdate.py:520) fails: it ignores the result. Nothing removes an anchor that no journal
  names. The effect is a stray ref and blob in the operator's checkout; behaviour is unaffected.

Config, boot restore, binary install and firewall apply: no defect found.

## Finding 76 — the adoption/update pin compared the copy order of the local tree

`test_the_operation_writes_exactly_the_pinned_points[adopt|update]` compared the write log with
the pinned list in order, including the copy of the local tree (`shutil.copytree` of `.git`,
`shutil.copy2` of `file.txt`). `copytree` visits a directory's entries in the filesystem's order,
which differs between tmpfs and ext4/btrfs, so the test failed where pytest's tmp dir is tmpfs.
Fix, in the source module only (`b938c4b`): the comparison sorts that one run of copy writes; every
other write is still compared in order. The interruption cases are unaffected: the known-defect
marks of the two copy points carry the same reason (KD-S1), so either order gives the same
outcome. Red-before: the old test on a tmpfs tmp dir → `2 failed`; with the fix → `2 passed` on
tmpfs and on disk (btrfs).

## Numbers, measured

| figure | command | output |
|---|---|---|
| per module | `python -m pytest -q -p no:cacheprovider <module>` (PYTHONPATH = the tree) | config 25 passed; boot restore 22 passed; binary 70 passed; source 114 passed, 26 xfailed; self-update 30 passed, 4 xfailed; firewall 25 passed |
| all six | the same over the six modules | `286 passed, 30 xfailed` |
| known defects | `pytest -rx` over the source and self-update modules, reasons counted | KD-S1 20, KD-S2 1, KD-S3 3, KD-S4 2, KD-U1 4 (= 30) |
| pinned points | the length of each module's pinned list (`ast` over the module, `RAW` excluded) | config 4, boot restore 7, binary 23, source adopt 22 / update 24, self-update 11, firewall 8 |
| failure kinds per point | `FAILURES` in `tests/interrupts.py` | 3 (ENOSPC, EIO, KeyboardInterrupt) |
| module lines | `wc -l` | config 82, boot restore 103, binary 123, source 192, self-update 121, firewall 151; `tests/interrupts.py` 170 |
| production untouched | `git diff f257831..HEAD -- lhpc testlab \| wc -l` | `0` |
| finding 76 red-before | `pytest --basetemp=<tmpfs dir> tests/install/test_source_interruption.py -k pinned_points` with the old test | `2 failed`; with the fix `2 passed` (tmpfs and btrfs) |

## The 6-point block

1. **CONTRACTS** (read, none changed). Write order of each transaction (the pinned lists; owners in
   PLAN-T2 with file:line). `OwnedMarker.rewrite/remove` report OSError as False
   (runtime_fs.py:310/326). `remove_bound` reports `(False, why)` (source_fs.py:210).
   `carry_extras` reports a reason string (:546). `create_anchor` reports False
   (selfupdate.py:472). `delete_anchor` and `_durable_unlink` never raise (selfupdate.py:520,
   firewall_helper.py:1006). `recover_config_transaction` returns None/""/message
   (config.py:1792). `binary_recover` returns `(ok, msg)` (service_binary_ops.py:555).
   `op_check` returns `EXIT_*` (firewall_helper.py:1156). The harness honours each reporting
   contract (`reported=`).
2. **INVARIANTS + TESTS.** Config as a transaction → config module (both recovery paths). Source
   transactions and locally added files never collateral → source module (`notes.txt` checked in
   every update case); KD-S1/S2/S3 are where the claim does not hold today. Boot restore replays
   saved configuration once → boot module (`started == [("kiss","433")]` in total). Binary crash
   journal → binary module (v1 or v2 whole with its own receipt). Firewall fail-closed /
   journalled apply → firewall module (live == canonical, `verified`). Path containment is
   untouched: the harness calls the real writers.
3. **KNOWN FAILURE CLASSES.** Fakes: the only replaced production callables are the seam writers,
   wrapped with `*args, **kwargs` pass-through to the real function. The boot module's `start`
   stub has the REAL `start` signature. The binary module reuses `stub_pipeline` (real
   signatures). EIO: injected at every point. KeyboardInterrupt through cleanup: every point
   (this is what found KD-S1/S2/S4/U1). Same decision across CLI/web/job/boot: recovery entries
   are the shared service/helper functions the CLI, web, units and timer all call (startup hook
   cli/main.py:1027; `config_lock`; `binary_recover`; `recover_source_activations`;
   `self_update_apply`; helper `recover()`). Stacked conflicts: T2 adds new files in tests/ only
   and one sentence in tests/README.md; T5 (the same branch) touches tests/repo, tests/host (unit
   pins), .github and docs/maintenance.md — no shared file except CHANGELOG.md (separate lines).
4. **TEST RULES.** Red-before per module (table). Decision strings compared whole: write lists
   and journal states by equality, never `startswith`. Every return checked (`.ok`, `.status`,
   `EXIT_OK`, `binary_recover()[0]`). No network: local git repos, the stubbed binary pipeline,
   a fake nft.
5. **WHOLE TEST DIRECTORIES** (foreground, `python -m pytest -q -p no:cacheprovider <dir>`):
   - `tests/core` → `2092 passed, 6 skipped`. This ran before the last fixup, which only dropped
     an unneeded callsign line from the config module; that module re-ran green (25 passed).
   - `tests/install` → `96 failed, 1321 passed, 16 skipped, 30 xfailed`.
   - `tests/host tests/repo` → `3 failed, 1096 passed, 5 skipped`.

   Every failure also fails on the base `origin/integration/0.12.0` (f257831) in this container (the same files on the
   base: `99 failed`). It runs as root, and `bootstrap-deps.sh` refuses root ("no non-root
   operator for group grants"). The failures are `tests/install/test_bootstrap_deps.py` (92),
   `test_binary_install.py` (3), `test_binary_channel.py` (1), and in host `test_firewall.py::
   test_receipt_reader_rejects_nonroot_symlink_and_unsafe`, `test_network_controls.py::
   test_rule_helpers_and_single_install_site` and `test_power_controls.py::
   test_dependency_entry_probe_and_bootstrap_exclusion`. None is in a file this batch touches.
   `ruff check lhpc testlab` → All checks passed; `ruff check tests --select F,E9` → All checks
   passed. No signature changed (no grep needed).
6. **ADVERSARIAL SELF-REVIEW.** Found and fixed:
   - the harness first raised from `OwnedMarker`/`remove_bound` where the real writer reports →
     added `reported=`;
   - the first source run counted `carry_extras` with nothing to carry → added the operator file;
   - the atomic-rename probe cache made the adoption pins order-dependent → reset per test;
   - the `tempfile` mask also masked "registry" → narrowed;
   - the docstring named a non-existent `points()`;
   - `pytest.mark.safety` lacked its id.

   Remaining notes for the reviewer:
   - The pins name writer functions and order. Rule 1 forbids pinning helper names; rule 2 makes
     exactness the contract for persisted state. The write order IS the crash contract, and a
     change that moves a write must re-derive the cases.
   - The self-update module routes only `merge` through a gated name.

   The docs (one README sentence, one CHANGELOG entry) and this report describe exactly the diff.

## Simplicity guardrails

No production extraction (tests only), so no before/after for `lhpc/`. New code: the harness
(170 lines; one context manager, one runner, one tree reader; its one class `_Module` is the
`os`/`shutil` stand-in). It imports `lhpc.core.runtime_fs`, `pytest` and the standard library.
Six modules of 83–181 lines. No registry, no result type.

## Deviations

- The source transaction is driven through the local-fallback adoption (no network). The
  `git clone` path's writes are the same journal/record/rename points plus git's own files.
- The self-update module covers the anchor/migration journal. The helper's request/inflight claim
  and `--repair-integration` are not among the six named transactions.
- The firewall receipt lives under `/etc/lhpc` and `/run/lhpc-firewall`, written by the root
  helper's own writer, so it is gated there, not in `runtime_fs`.

# PLAN T2 — interruption-and-retry at every durable write (tests only)

Base `integration/0.12.0` (0.12.0 heading). Files: `tests/` only; `lhpc/` and `testlab/` untouched.

## The harness (one helper, no framework)

`tests/interrupts.py` (beside `seams.py`): `durable_writes(root, fail_at=, exc=, raw=)` wraps the
writers of the seam every transaction already uses — `runtime_fs.<fn>` attributes (callers do
`from . import runtime_fs`; no `from runtime_fs import fn` exists) and `OwnedMarker.rewrite/remove`
— and logs each OUTERMOST call as (writer, path under the root, nonces masked). With `fail_at=k`
it raises `ENOSPC`/`EIO`/`KeyboardInterrupt` INSTEAD of write k (state = everything before k on
disk). Writes a transaction makes outside `runtime_fs` are gated by `raw=`: a module's own
`os`/`shutil` reference (`binary_install`, `source_fs`, `install`, `firewall_helper`) or one
named function (`source_fs.remove_bound`, `selfupdate.create_anchor`, …). A writer whose contract
REPORTS an OSError (`OwnedMarker` → False, `remove_bound` → `(False, why)`, `carry_extras` → str,
`_durable_unlink`/`delete_anchor` → None) gets the injected OSError as that report. Plus
`run_interrupted()` and `tree()`. Each module pins its operation's write list (a new write fails
the pin first) and parametrises over (write point × {ENOSPC, EIO, KeyboardInterrupt}); every case
proves (a) the left state is recognised by the recovery path, (b) recovery leaves it clean,
(c) the retry succeeds to the bytes of an uninterrupted run.

## The six transactions and their write points (file:line)

1. **Config journal + pre-images** — `ControllerService.save_config_bundle` (service_params.py:1055)
   → config.py `_apply_config_transaction_locked`: journal with pre-images :1930, `local.toml` and
   `stacks/daemon.toml` :1955, journal unlink :1975. Recovery: startup
   `recover_config_journal_at_startup` (config.py:1871, cli/main.py:1027) and the next writer's
   `config_lock` → `_finish_pending_journal` (:1844). Module `tests/core/test_config_interruption.py`.
2. **Boot-restore markers** — `boot_restore_run` (service_boot_restore.py:280): admission owner
   (reslock.py:134), plan :357, claim `attempting` :501, evidence prune (lifecycle.py:1078), settle
   :541, close :381, owner release (reslock.py:149); all journal writes via boot_restore.py:163.
   Recovery: the next run (`_boot_journal_recovery` :258). `tests/core/test_boot_restore_interruption.py`.
3. **Binary install journal/receipts** — `binary_install` re-install over v1 (service_binary_ops.py:88):
   journal open/publishing/committed (binary_install.py:384 via :439/:532/:551), backup
   `os.makedirs`+`os.replace` (:535-537), publish (:539-541), receipt (binary_receipt.py:213),
   backup `rmtree` (:610), journal clear (:427), lock owners. Recovery: in-process unwind
   (service_binary_ops.py:416/:447) and `binary_recover` (:555, run first by the next install).
   `tests/install/test_binary_install_interruption.py`.
4. **Source adoption journal + `staging` record (B6)** — `Installer.adopt_source` (install.py:332),
   first adoption AND update over an operator-added file: `ensure_dir` :394, atomic-rename probe
   (source_fs.py:168-176), staging record :1284 + its identity :1302, candidate `os.mkdir`
   (source_fs.py:379), clone-failure reset `remove_bound` :711, local copy :811/:834, journal
   :1125, prior archive :1966, journal states :1991/:2048, `carry_extras` :607, activation :2033,
   ownership record (source_registry.py:78), prior quarantine+removal :1573/:1592, journal and
   record removal :2139/:1292. Recovery: `recover_source_activations` (:1217; the next writer, :371).
   `tests/install/test_source_interruption.py`.
5. **Self-update anchor/migration journal** — `self_update_apply` (service_selfupdate.py:318) with a
   changed default the saved config still holds: admission owner, `create_anchor` :662
   (selfupdate.py:472), journal prepared :665 (selfupdate.py:583), the checkout advance
   (selfupdate.py:832), status cache (:404), promoted journal, migration (config.py:2119 via
   services.py:2756), journal clear (selfupdate.py:594), `delete_anchor` :694, firewall post-update
   marker (service_firewall.py:950). Recovery: the next apply (`classify_journal` selfupdate.py:600,
   reconcile service_selfupdate.py:612-638). `tests/install/test_selfupdate_interruption.py`.
6. **Firewall receipt** — root helper `op_apply` (firewall_helper.py:1246), re-apply of a
   transitional candidate: journal begin :1352, staging snapshot :1353, journal staged :1354,
   promote `os.replace` :1380, transition :1383, journal unlink :1392, receipt :784, log :760. Its
   own writer (it imports nothing from lhpc). Recovery: `op_check`/`op_load` → `recover()` (:1048).
   `tests/host/test_firewall_interruption.py`.

`--repair-integration` (unit files, `updater_units.write_set`) is not one of the six; out of scope.

## docs/architecture.md recovery claims → cases

- Source transactions ("recovery removes a clone a crash interrupted", "a failed activation never
  destroys the active source", journalled at every step) → module 4 (KD-S1/S2 below are the gaps).
- Locally added files are never collateral → module 4, update op (`notes.txt` asserted every case).
- Config as a transaction (mid-write rollback; finished before ANY writer; eagerly at start) →
  module 1, `recovery` ∈ {startup, next-writer}.
- Boot restore replays only saved configuration (once) → module 2 (`started == [kiss]` in total).
- Binary crash journal → module 3; firewall journal ("interruption finished or rolled back") → 6.

## Red-before

The modules characterise current recovery, so they are green on the parent; the red-before is a
throwaway mutation of each recovery path (config recovery drops the journal unrestored; boot
recovery skips the prune; `rollback_files` no-op; `_finish_or_rollback` drops the journal;
self-update never promotes `prepared`; helper `_recover_apply` drops the journal) — each turns
cases of its module red; `git checkout` restores `lhpc/` (recorded in the report).

## Risks and how they are ruled out

- Unfaithful injection (raising where the real writer reports) → the `reported` contract above;
  checked against each writer's source.
- Points silently skipped → `run_interrupted` asserts the injected point was reached; the pin test.
- Nondeterministic labels (pids, nonces, tempfile) → masked; the atomic-rename probe cache is reset.
- No network: local git repos only; the binary pipeline stub (`stub_pipeline`); firewall fake nft.

## Real defects found → strict xfail "known defect" (not fixed here)

KD-S1 orphaned candidate (staging record dropped on exception exit), KD-S2 carried file breaks the
candidate's recorded ctime → crash before activation leaves NO active source, KD-S3 quarantined
prior left after the journal closes, KD-S4 atomic-rename probe dir left on Ctrl-C, KD-U1 stray
self-update anchor. Details in the report. Open question: none — recommendation: the handler
turns each KD into a fix item; the strict xfail flips to XPASS (red) when fixed.

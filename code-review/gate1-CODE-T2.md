# Gate 1 — code review request, batch T2

Please judge the plan and each code commit of batch T2 below: does each commit do what the
plan says, are its tests able to fail for the right reason (red-before), are the recorded known
defects real and precisely described, and does anything in the diff change production behaviour
(it must not: tests only)? Answer per commit in this form, then one final line:

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Final line: GREEN / GREEN WITH NOTES / RED

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

Note: IP literals of test data are shown as `<ip-…>` placeholders in this file only.

This round: one test defect fixed (finding 76, the copy order of the local tree in the
source module's pin; see the code report) and a "Numbers, measured" table added. All other
commits keep their patch-ids.

## The plan

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


## The commits

```
ef5f7ad T2: plan — the interruption-and-retry harness over six transactions
65e095d T2: the interruption harness — fail each durable write in turn
5ac5305 T2: a Settings save interrupted at every write recovers and retries
98291f0 T2: a boot restore interrupted at every write is finished by the next run
8b12452 T2: a binary re-install interrupted at every write is unwound and retried
b938c4b T2: a source adoption or update interrupted at every write; four known defects
374c99a T2: a self-update interrupted at every write is finished by the next apply
3cadc86 T2: a firewall apply interrupted at every write is recovered by the next check
```

## The code report

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

## The full diff of the code commits

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index f8f7148..a2a5aa3 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,6 +2,12 @@
 
 ## 0.12.0
 
+- Every step that writes a journal, receipt or marker the next run recovers from (settings saves, boot restore,
+  binary installs, source installs and updates, self-update, the firewall apply) is now tested against a full
+  disk, an I/O error and Ctrl-C at that exact step: recovery must leave a clean state and the same command must
+  then succeed. Five places where an interrupted source install or self-update leaves a leftover behind are
+  recorded for fixing.
+
 - The dashboard's radio columns ask the daemon for updates only while the page is shown: nothing while the tab
   is in the background (one refresh when you come back), never a second request while the first is still
   unanswered, and after a failed update the next try waits 15 s instead of 3 s.
diff --git a/tests/README.md b/tests/README.md
index 0d44446..db39c68 100644
--- a/tests/README.md
+++ b/tests/README.md
@@ -45,6 +45,10 @@ in a file named after when or how a defect was found:
   deployment scripts.
 - **`repo/`** — invariants of the repository itself: packaging, versions, README drift, suite hygiene.
 
+A transaction whose state another run must recover (a journal, receipt or marker) is also driven
+through `interrupts.py`: its module pins the operation's durable writes and fails each one in turn
+(disk full, I/O error, Ctrl-C), then proves recovery and a retry (`core/test_config_interruption.py`).
+
 ## The rules
 
 1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
diff --git a/tests/core/test_boot_restore_interruption.py b/tests/core/test_boot_restore_interruption.py
new file mode 100644
index 0000000..a3e3d11
--- /dev/null
+++ b/tests/core/test_boot_restore_interruption.py
@@ -0,0 +1,103 @@
+"""A boot restore interrupted at every durable write is recovered by the next run, which starts
+the saved stack exactly once in total.
+
+The driver (`service_boot_restore.boot_restore_run`) takes the controller admission marker,
+journals its plan in `state/boot-restore.json`, claims an item `attempting` before the start,
+prunes the consumed ownership evidence, settles the item and closes the journal. Each case fails
+ONE of those writes (disk full, I/O error, Ctrl-C) and proves: the journal left behind is one the
+next run reads (never `unsafe`), that run finishes the cleanup — evidence pruned, no admission
+marker left, journal closed — and across both runs the stack was started exactly once: an item
+claimed before the interruption is never started again, one still pending is started by the next
+run.
+"""
+from __future__ import annotations
+
+import json
+
+import pytest
+
+from interrupts import FAILURES, durable_writes, run_interrupted, tree
+from lhpc.core import boot_restore as br
+from lhpc.core import service_boot_restore as sbr
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.service_base import ActionResult
+from lhpc.core.services import ControllerService
+
+pytestmark = [pytest.mark.needs_session, pytest.mark.safety("boot-restore-once")]
+
+LAUNCH = "loraham-kiss-tnc__433__999999__" + "ab" * 16
+# The run's durable writes, in order (service_boot_restore.py `boot_restore_run`).
+POINTS = [
+    ("write_marker", "state/locks/controller-task-admission.owner"),   # admission
+    ("atomic_write", "state/boot-restore.json"),                        # the plan
+    ("atomic_write", "state/boot-restore.json"),                        # claim: attempting
+    ("unlink", "state/owned/loraham-kiss-tnc__*__*__*.json"),           # prune the evidence
+    ("atomic_write", "state/boot-restore.json"),                        # settle the item
+    ("atomic_write", "state/boot-restore.json"),                        # close the run
+    ("unlink", "state/locks/controller-task-admission.owner"),         # release admission
+]
+
+
+@pytest.fixture
+def boot(tmp_path, monkeypatch):
+    """A driver in a new boot with one stack (kiss on 433) the previous boot had running; the
+    start is stubbed with the REAL signature and records only a start that passed its claim."""
+    started = []
+
+    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
+              _before_start_locked=None, _operator=True, position=None, position_note=""):
+        if _before_start_locked is not None:
+            refusal = _before_start_locked()
+            if refusal is not None:
+                return refusal
+        started.append((target, band))
+        return ActionResult(True, "started")
+
+    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
+    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
+    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))
+    monkeypatch.setattr(ControllerService, "start", start)
+    rec = {"launch_id": LAUNCH, "stack": "kiss", "component": "loraham-kiss-tnc", "band": "433",
+           "pid": 999999, "role": "", "launched_at": 1000, "version": 1,
+           "requested_target": "kiss", "start_scope": "stack", "boot_id": "OLDBOOT",
+           "starttime": "123", "pgid": 999999, "sid": 999999}
+    (tmp_path / "state" / "owned").mkdir(parents=True)
+    (tmp_path / "state" / "owned" / f"{LAUNCH}.json").write_text(json.dumps(rec))
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    return svc, started
+
+
+def test_the_run_writes_exactly_the_pinned_points(tmp_path, boot):
+    svc, started = boot
+    with durable_writes(tmp_path) as log:
+        assert svc.boot_restore_run().ok
+    assert log == POINTS and started == [("kiss", "433")]
+
+
+@pytest.mark.parametrize("failure", sorted(FAILURES))
+@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
+def test_an_interrupted_run_is_finished_by_the_next(tmp_path, boot, point, failure):
+    svc, started = boot
+    _, outcome = run_interrupted(tmp_path, svc.boot_restore_run, fail_at=point,
+                                 exc=FAILURES[failure])
+    # (a) what is left is readable by the next run — never a journal that blocks restoration —
+    # and evidence a claimed item left behind is on the next run's cleanup list. A run whose
+    # only failure is that prune reports the restore it did (the prune is recorded, not lost).
+    journal, state = br.load_journal(svc._paths)
+    assert state in ("absent", "valid")
+    evidence_left = (tmp_path / "state" / "owned" / f"{LAUNCH}.json").exists()
+    claimed = bool(journal) and any(i["state"] != "pending" for i in journal["items"])
+    if evidence_left and claimed:
+        assert br.unpruned_consumed(journal), "consumed evidence the next run would not prune"
+    if POINTS[point][0] != "unlink":
+        assert not getattr(outcome, "ok", False), "an interrupted run must not report success"
+
+    # (b)+(c) the next run recovers and completes
+    assert svc.boot_restore_run().ok
+    journal, state = br.load_journal(svc._paths)
+    assert state == "valid" and journal["state"] in ("done", "no-plan")
+    assert all(i["state"] not in ("pending", "attempting") for i in journal["items"])
+    assert set(tree(tmp_path)) == {"state", "state/owned", "state/locks", "state/boot-restore.json",
+                                   "state/locks/controller-task-admission.lock"}
+    assert started == [("kiss", "433")], "the saved stack is started exactly once in total"
diff --git a/tests/core/test_config_interruption.py b/tests/core/test_config_interruption.py
new file mode 100644
index 0000000..4b2411a
--- /dev/null
+++ b/tests/core/test_config_interruption.py
@@ -0,0 +1,82 @@
+"""A Settings save interrupted at every durable write is recovered and can be retried.
+
+The config transaction (`config.apply_config_transaction`) journals the pre-images in
+`state/config-txn.json`, replaces each target, then removes the journal. Each case fails ONE of
+those writes (disk full, I/O error, Ctrl-C) and proves: what is left is recognised by the
+recovery path — the eager one every `lhpc` process runs at start, or the next writer's
+`config_lock` — recovery returns the files to their pre-save bytes with no journal left, and
+the same save then succeeds to the bytes an uninterrupted save writes.
+"""
+from __future__ import annotations
+
+import pytest
+
+from interrupts import FAILURES, durable_writes, run_interrupted, tree
+from lhpc.core import config as cfgmod
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.safety("config-transaction")
+
+# The save's durable writes, in order (config.py `_apply_config_transaction_locked`).
+POINTS = [
+    ("atomic_write", "state/config-txn.json"),       # the journal with every pre-image
+    ("atomic_write", "config/local.toml"),
+    ("atomic_write", "config/stacks/daemon.toml"),
+    ("unlink", "state/config-txn.json"),             # commit
+]
+SKIP = ("config/.lock", "state/locks")               # lock files, not transaction state
+
+
+def _svc(root):
+    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
+
+
+def _seeded(root):
+    svc = _svc(root)
+    assert svc.save_config_bundle("daemon", values={"radio": "868"},
+                                  remotes={"loraham-daemon": "", "radiolib": ""}).ok
+    return svc
+
+
+def _save(svc):
+    return svc.save_config_bundle("daemon", values={"radio": "433"},
+                                  remotes={"loraham-daemon": "https://example.invalid/d.git",
+                                           "radiolib": ""})
+
+
+def test_the_save_writes_exactly_the_pinned_points(tmp_path):
+    """The parametrisation below covers every write: a write added to the save fails here first."""
+    svc = _seeded(tmp_path)
+    with durable_writes(tmp_path) as log:
+        assert _save(svc).ok
+    assert log == POINTS
+
+
+@pytest.mark.parametrize("recovery", ["startup", "next-writer"])
+@pytest.mark.parametrize("failure", sorted(FAILURES))
+@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
+def test_an_interrupted_save_recovers_and_retries(tmp_path, point, failure, recovery):
+    done_root = tmp_path / "uninterrupted"
+    done = _seeded(done_root)
+    assert _save(done).ok
+    want_after = tree(done_root, skip=SKIP)
+
+    root = tmp_path / "interrupted"
+    svc = _seeded(root)
+    before = tree(root, skip=SKIP)
+    _, outcome = run_interrupted(root, lambda: _save(svc), fail_at=point, exc=FAILURES[failure])
+    assert not getattr(outcome, "ok", False), "an interrupted save must not report success"
+
+    paths = Paths(runtime_root=root)
+    journal = (root / "state" / "config-txn.json").exists()
+    if recovery == "startup":
+        note = cfgmod.recover_config_journal_at_startup(paths)
+        # (a) a journal left behind is recognised and finished; without one there is nothing to do
+        assert bool(note) == journal
+        # (b) the pre-save bytes, no journal, no stray temp leaf
+        assert tree(root, skip=SKIP) == before
+    # (c) the retry (for "next-writer" its config_lock finishes the journal first) succeeds
+    assert _save(svc).ok
+    assert tree(root, skip=SKIP) == want_after
diff --git a/tests/host/test_firewall_interruption.py b/tests/host/test_firewall_interruption.py
new file mode 100644
index 0000000..4658097
--- /dev/null
+++ b/tests/host/test_firewall_interruption.py
@@ -0,0 +1,151 @@
+"""A firewall apply interrupted at every durable write is recovered and can be retried.
+
+The root helper's apply (`firewall_helper.op_apply`) journals `firewall.journal.json` (begin),
+writes the new snapshot to a staging name, journals `snapshot-staged`, loads and verifies the
+live table, promotes the staging file over the canonical snapshot, records or drops the
+transition file, removes the journal and writes the receipt `check.json` and its log line. The helper writes with
+its own `atomic_write`, `_durable_unlink` and `os.replace` (it imports nothing from lhpc), which
+the harness gates. Each case fails ONE write (disk full, I/O error, Ctrl-C) while a changed
+candidate replaces an applied one, and proves: the next check (`op_check`, the timer's and the
+boot loader's entry, which run `recover()` first) leaves no journal or staging file and a live
+table that is the canonical snapshot — old or new, never a mix — with a `verified` receipt, and
+the same apply then succeeds.
+"""
+from __future__ import annotations
+
+import json
+
+import pytest
+
+from interrupts import FAILURES, durable_writes, run_interrupted, tree
+from lhpc.core import firewall as fw
+from lhpc.core import firewall_helper as fh
+
+pytestmark = pytest.mark.safety("firewall-fail-closed")
+
+RAW = [(fh, "atomic_write"), (fh, "_durable_unlink", lambda e: None), (fh, "os.replace")]
+# The re-apply's durable writes, in order (firewall_helper.py `op_apply`).
+POINTS = [
+    ("firewall_helper.atomic_write", "etc/firewall.journal.json"),               # begin
+    ("firewall_helper.atomic_write", "etc/firewall.snapshot.json.staging-*"),
+    ("firewall_helper.atomic_write", "etc/firewall.journal.json"),               # snapshot-staged
+    ("os.replace", "etc/firewall.snapshot.json.staging-*"),                      # promote
+    ("firewall_helper.atomic_write", "etc/firewall.transition.json"),
+    ("firewall_helper._durable_unlink", "etc/firewall.journal.json"),
+    ("firewall_helper.atomic_write", "run/check.json"),                          # the receipt
+    ("firewall_helper.atomic_write", "run/firewall.log"),                        # its log line
+]
+
+
+def _candidate(allow, ingress=()):
+    ep = {"id": "kiss.tnc.tcp-8001", "proto": "tcp", "family": "ipv4", "addr": "<ip-a>",
+          "port": 8001, "allow_cidrs": [allow], "selected": True, "deny_default": False,
+          "auth": "none", "band": ""}
+    return {"schema": fw.CANDIDATE_SCHEMA, "mode": "secure-default", "endpoints": [ep],
+            "proxy_ingress": [{"proto": "tcp", "family": "ipv4", "addr": "<ip-a>", "port": port,
+                               "allow_cidrs": [allow]} for port in ingress],
+            "ssh_ports": [], "ap": {"enabled": False, "interface": "",
+                                                          "cidr": ""}, "extra_allow": []}
+
+
+# v2 narrows the endpoint and drops v1's proxy ingress, so the apply is transitional (the ingress
+# stays allowed until the cleanup apply) and writes the transition record too.
+V1 = _candidate("<ip-b>", ingress=(8443,))
+V2 = _candidate("<ip-c>")
+
+
+class _Nft:
+    """The helper's `Sys` seam over a simulated kernel table: `nft -f` loads a text, `nft -j list
+    table` lists exactly the model that text was rendered from (handles as the kernel adds them),
+    so the helper's real live verification decides."""
+
+    def __init__(self, models):
+        self.models = models            # rendered nft text -> the model it came from
+        self.loaded = None
+
+    def run(self, argv, timeout=30.0, stdin_text=None):
+        if argv[:3] == ["nft", "-c", "-f"]:
+            return 0, "", ""
+        if argv[:2] == ["nft", "-f"]:
+            self.loaded = stdin_text
+            return 0, "", ""
+        if argv[:4] == ["nft", "-j", "list", "tables"]:
+            tables = [{"table": {"family": "inet", "name": "lhpc"}}] if self.loaded else []
+            return 0, json.dumps({"nftables": tables}), ""
+        if argv[:4] == ["nft", "-j", "list", "table"]:
+            if self.loaded is None:
+                return 1, "", "Error: No such file or directory"
+            listing = fh.expected_listing(self.models[self.loaded])
+            for i, entry in enumerate(listing):
+                for body in entry.values():
+                    body["handle"] = i + 1
+            return 0, json.dumps({"nftables": [{"metainfo": {"version": "1.1.3"}}, *listing]}), ""
+        if argv[:2] == ["nft", "destroy"]:
+            self.loaded = None
+            return 0, "", ""
+        if argv[0] == "sshd":
+            return 0, "port 22\n", ""
+        return 0, "", ""
+
+    def boot_id(self):
+        return "boot-1"
+
+    def boottime(self):
+        return 1234.5
+
+    def walltime(self):
+        return 1_784_900_000.0
+
+
+@pytest.fixture
+def box(tmp_path, monkeypatch):
+    """V1 applied and verified; returns (nft, apply-V2, etc, run)."""
+    models = {}
+    real_render = fh.render_nft_text
+
+    def render(model):                  # spy: remember which model each loaded text came from
+        text = real_render(model)
+        models[text] = model
+        return text
+    monkeypatch.setattr(fh, "render_nft_text", render)
+    etc, run = tmp_path / "etc", tmp_path / "run"
+    nft = _Nft(models)
+    for name, cand in (("v1.json", V1), ("v2.json", V2)):
+        (tmp_path / name).write_text(json.dumps(cand))
+    assert fh.op_apply(nft, str(tmp_path / "v1.json"), etc_dir=str(etc), run_dir=str(run)) == fh.EXIT_OK
+    return nft, lambda: fh.op_apply(nft, str(tmp_path / "v2.json"), etc_dir=str(etc),
+                                    run_dir=str(run)), etc, run
+
+
+def _settled(nft, etc, run):
+    """No journal, staging or temp file; the live table is the canonical snapshot; the receipt
+    says verified. Returns the snapshot's intent hash."""
+    names = set(tree(etc)) | {f"run/{n}" for n in tree(run)}
+    assert "firewall.journal.json" not in names
+    assert not [n for n in names if ".staging-" in n or ".tmp" in n]
+    snap = json.loads((etc / "firewall.snapshot.json").read_text())
+    assert nft.loaded == snap["nft_text"]
+    assert json.loads((run / "check.json").read_text())["verdict"] == "verified"
+    return snap["intent_hash"]
+
+
+def test_the_reapply_writes_exactly_the_pinned_points(tmp_path, box):
+    nft, apply, _etc, _run = box
+    with durable_writes(tmp_path, raw=RAW) as log:
+        assert apply() == fh.EXIT_OK
+    assert log == POINTS
+
+
+@pytest.mark.parametrize("failure", sorted(FAILURES))
+@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
+def test_an_interrupted_apply_recovers_and_retries(tmp_path, box, point, failure):
+    nft, apply, etc, run = box
+    v1, v2 = fw.intent_hash(V1), fw.intent_hash(V2)
+    run_interrupted(tmp_path, apply, fail_at=point, exc=FAILURES[failure], raw=RAW)
+
+    # (a)+(b) the next check recovers: old or new ruleset whole, live == canonical, verified
+    assert fh.op_check(nft, etc_dir=str(etc), run_dir=str(run)) == fh.EXIT_OK
+    assert _settled(nft, etc, run) in (v1, v2)
+    # (c) the retry applies v2
+    assert apply() == fh.EXIT_OK
+    assert _settled(nft, etc, run) == v2
diff --git a/tests/install/test_binary_install_interruption.py b/tests/install/test_binary_install_interruption.py
new file mode 100644
index 0000000..1e02b30
--- /dev/null
+++ b/tests/install/test_binary_install_interruption.py
@@ -0,0 +1,123 @@
+"""A binary re-install interrupted at every durable write is unwound, recognised and retried.
+
+The binary transaction (`binary_install.py`, driven by `service_binary_ops.binary_install`) opens
+`state/binary/install.journal.json` before it changes anything, moves the live file into
+`state/binary/.backup-<txn>/`, publishes the staged one, writes the receipt, commits the journal,
+drops the backups and clears the journal. The journal and receipt go through `runtime_fs`; the
+moves use the module's own `os`/`shutil`, which the harness gates the same way. Each case fails
+ONE write (disk full, I/O error, Ctrl-C) during a re-install over a working v1 and proves: no
+journal recovery cannot read is left (`binary_recover`, which the next install runs first,
+finishes it), the box holds v1 or v2 whole with its own receipt — never a mix, a backup or a
+staging directory — and the same install then succeeds to the bytes an uninterrupted one writes.
+"""
+from __future__ import annotations
+
+import json
+import os
+
+import pytest
+
+from interrupts import FAILURES, durable_writes, run_interrupted, tree
+from lhpc.core import binary_install as bi
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.safety("binary-transaction")
+
+BIN = "src/loraham-daemon/loraham_daemon/loraham_daemon"
+RECEIPT = "state/binary/daemon.json"
+RAW = [(bi, "os.replace"), (bi, "os.makedirs"), (bi, "os.unlink"), (bi, "shutil.rmtree")]
+LOCKS = ("state/locks",)
+# The re-install's durable writes, in order (service_binary_ops.py `_binary_install_locked`).
+POINTS = [
+    ("write_marker", "state/locks/controller-task-admission.owner"),
+    ("write_marker", "state/locks/source-txn-index.owner"),
+    ("write_marker", "state/locks/source.src-radiolib.owner"),
+    ("write_marker", "state/locks/source.src-loraham-daemon.owner"),
+    ("unlink", "state/locks/source-txn-index.owner"),
+    ("mkdir", "state"),
+    ("mkdir", "state/binary"),
+    ("write_marker", "state/binary/install.journal.json"),            # open: prepared
+    ("mkdir", "state/binary"),
+    ("write_marker", "state/binary/install.journal.json"),            # publishing
+    ("os.makedirs", "state/binary/.backup-*"),
+    ("os.replace", BIN),                                              # live -> backup
+    ("os.makedirs", "src/loraham-daemon/loraham_daemon"),
+    ("os.replace", "state/lhpc-binary-*/stage/" + BIN),               # staged -> live
+    ("mkdir", "state/binary"),
+    ("write_marker", RECEIPT),
+    ("mkdir", "state/binary"),
+    ("write_marker", "state/binary/install.journal.json"),            # committed
+    ("shutil.rmtree", "state/binary/.backup-*"),
+    ("unlink", "state/binary/install.journal.json"),                  # cleared
+    ("unlink", "state/locks/source.src-loraham-daemon.owner"),
+    ("unlink", "state/locks/source.src-radiolib.owner"),
+    ("unlink", "state/locks/controller-task-admission.owner"),
+]
+
+
+@pytest.fixture
+def box(monkeypatch, stub_pipeline):
+    """`box(root) -> (svc, install)`: the daemon installed from a binary once (v1); `install()`
+    re-installs it with the bytes the next staged artifact carries (v2)."""
+    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
+    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
+    version = ["v1"]
+
+    def stage(tar, stage_dir, roots):
+        os.makedirs(os.path.dirname(os.path.join(stage_dir, BIN)), exist_ok=True)
+        with open(os.path.join(stage_dir, BIN), "w") as fh:
+            fh.write(version[0])
+        return [BIN]
+    monkeypatch.setattr(bi, "validate_and_extract", stage)
+
+    def make(root):
+        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
+        stub_pipeline(svc, download=lambda entry, path: None)
+        assert svc.binary_install("daemon", apply=True).ok
+        version[0] = "v2"
+        return svc, lambda: svc.binary_install("daemon", apply=True)
+    return make
+
+
+def _receipt(root):
+    """The receipt minus its install time."""
+    data = json.loads((root / RECEIPT).read_text())
+    data.pop("installed_at")
+    return data
+
+
+def test_the_reinstall_writes_exactly_the_pinned_points(tmp_path, box):
+    svc, install = box(tmp_path)
+    with durable_writes(tmp_path, raw=RAW) as log:
+        assert install().ok
+    assert log == POINTS
+
+
+@pytest.mark.parametrize("failure", sorted(FAILURES))
+@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
+def test_an_interrupted_reinstall_recovers_and_retries(tmp_path, box, point, failure):
+    done_root = tmp_path / "uninterrupted"
+    _svc, done = box(done_root)
+    v1_receipt = _receipt(done_root)
+    assert done().ok
+    v2_receipt = _receipt(done_root)
+    want_after = tree(done_root, skip=LOCKS + (RECEIPT,))
+
+    root = tmp_path / "interrupted"
+    svc, install = box(root)
+    run_interrupted(root, install, fail_at=point, exc=FAILURES[failure], raw=RAW)
+
+    # (a) never a journal recovery cannot read; recovery (the next install's first step) finishes it
+    assert bi.read_journal(svc._paths)[1] in ("absent", "valid")
+    assert svc.binary_recover()[0]
+    # (b) v1 or v2 whole, each with its own receipt; no journal, backup or staging directory left
+    state = tree(root, skip=LOCKS + (RECEIPT,))
+    assert not [p for p in state if p.startswith(("state/binary/.backup-", "state/lhpc-binary-"))
+                or p == "state/binary/install.journal.json"]
+    assert (state[BIN], _receipt(root)) in ((b"v1", v1_receipt), (b"v2", v2_receipt))
+    assert svc.binary_receipt_state("daemon")[0] == "valid"
+    # (c) the retry lands where an uninterrupted install does
+    assert install().ok
+    assert tree(root, skip=LOCKS + (RECEIPT,)) == want_after and _receipt(root) == v2_receipt
diff --git a/tests/install/test_selfupdate_interruption.py b/tests/install/test_selfupdate_interruption.py
new file mode 100644
index 0000000..dc7247d
--- /dev/null
+++ b/tests/install/test_selfupdate_interruption.py
@@ -0,0 +1,121 @@
+"""A self-update interrupted at every durable write is recovered by the next apply.
+
+The self-update transaction (`service_selfupdate._self_update_locked`, `selfupdate.py`) takes the
+controller admission marker, binds the migration intent to a git anchor
+(`refs/lhpc/selfupdate/<txid>`) and the journal `state/selfupdate-migrate.json` BEFORE the
+checkout moves, advances the checkout, refreshes the status cache, promotes the journal, migrates
+the config value that still equals the old default, clears the journal, deletes the anchor and
+marks the firewall post-update check. The journal, cache and config go through `runtime_fs`;
+the anchor writes and the advance are git calls, gated as one point each. Each case fails ONE
+write (disk full, I/O error, Ctrl-C) during an update whose new manifest changes a default the
+saved config still holds, and proves: what is left is never a journal the gate blocks on
+(`classify_journal`), and the next apply — the recovery path — ends at the new commit with the
+value migrated, no journal and no anchor.
+"""
+from __future__ import annotations
+
+import types
+
+import pytest
+
+import gitrepo
+from interrupts import FAILURES, durable_writes, run_interrupted
+from lhpc.core import config as cfgmod
+from lhpc.core import selfupdate
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import RealSystem
+from lhpc.core.services import ControllerService
+
+pytestmark = pytest.mark.safety("selfupdate-transaction")
+
+MANIFEST = ('[[stack]]\nid="s"\nname="S"\nmain="c"\n'
+            '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\n'
+            'readiness="process"\n'
+            '  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{}"\n')
+# The checkout's advance (`merge --ff-only`) is a git call among many reads: routed through its
+# own name so the harness gates it, and only it, as one point.
+GIT = types.SimpleNamespace(__name__="git", advance=None)
+RAW = [(selfupdate, "create_anchor", lambda e: False),   # reports False, never raises
+       (selfupdate, "delete_anchor", lambda e: None),    # best effort, never raises
+       (GIT, "advance")]
+# The apply's durable writes, in order.
+POINTS = [
+    ("write_marker", "state/locks/controller-task-admission.owner"),
+    ("selfupdate.create_anchor", "*"),                       # the git anchor of the intent
+    ("write_marker", "state/selfupdate-migrate.json"),       # prepared
+    ("git.advance", "merge"),                                # the checkout moves
+    ("write_marker", "state/selfupdate.json"),               # status cache
+    ("write_marker", "state/selfupdate-migrate.json"),       # completed
+    ("atomic_write", "config/stacks/s.toml"),                # the migration
+    ("unlink", "state/selfupdate-migrate.json"),
+    ("selfupdate.delete_anchor", "*"),
+    ("write_marker", "state/firewall-postupdate.pending"),
+    ("unlink", "state/locks/controller-task-admission.owner"),
+]
+
+
+@pytest.fixture
+def box(tmp_path, monkeypatch):
+    """A self-hosted checkout at a manifest whose `ropt` defaults to OLD, the saved config holding
+    OLD, and an upstream commit changing that default to NEW. Returns (service factory, work)."""
+    _origin, work, up = gitrepo.repos(tmp_path)
+    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
+    (work / "manifest.toml").write_text(MANIFEST.format("OLD"))
+    gitrepo.git(work, "add", "manifest.toml")
+    gitrepo.git(work, "commit", "-qm", "manifest")
+    gitrepo.git(work, "push", "-q", "origin", "main")
+    gitrepo.git(up, "fetch", "-q", "origin")
+    gitrepo.git(up, "reset", "-q", "--hard", "origin/main")
+    (up / "manifest.toml").write_text(MANIFEST.format("NEW"))
+    gitrepo.git(up, "commit", "-qam", "new default")
+    gitrepo.git(up, "push", "-q", "origin", "HEAD:main")
+
+    real_git = selfupdate._git
+    monkeypatch.setattr(GIT, "advance", real_git)
+    monkeypatch.setattr(selfupdate, "_git", lambda system, root, args, timeout: (
+        GIT.advance if args[0] == "merge" else real_git)(system, root, args, timeout))
+    rt = tmp_path / "rt"
+    (rt / "config" / "stacks").mkdir(parents=True)
+
+    def svc():          # a fresh service: what the next process sees
+        return ControllerService(manifest_path=work / "manifest.toml", system=RealSystem(),
+                                 paths=Paths(runtime_root=rt))
+    cfgmod.save_stack_config(svc()._paths, "s", {"ropt": "OLD"})
+    return svc, work, rt
+
+
+def test_the_apply_writes_exactly_the_pinned_points(box):
+    svc, _work, rt = box
+    with durable_writes(rt, raw=RAW) as log:
+        assert svc().self_update_apply().ok
+    assert [(w, "merge" if w == "git.advance" else p) for w, p in log] == POINTS
+
+
+# Known recovery defect (code-review/code-report-T2.md), a strict xfail: a fix turns it into an
+# XPASS, which fails until the mark is removed.
+KD_U1 = ("KD-U1: an anchor whose journal was never written (Ctrl-C between the two) or whose "
+         "delete failed (`delete_anchor` ignores it) stays under refs/lhpc/selfupdate/; no "
+         "recovery path removes an anchor no journal names")
+KNOWN = {(2, "KeyboardInterrupt"), (8, "EIO"), (8, "ENOSPC"), (8, "KeyboardInterrupt")}
+CASES = [pytest.param(k, f, id=f"{w}:{p}-{f}",
+                      marks=[pytest.mark.xfail(strict=True, reason=KD_U1)] if (k, f) in KNOWN else [])
+         for k, (w, p) in enumerate(POINTS) for f in sorted(FAILURES)]
+
+
+@pytest.mark.parametrize(("point", "failure"), CASES)
+def test_an_interrupted_apply_is_finished_by_the_next(box, point, failure):
+    svc, work, rt = box
+    first = svc()
+    run_interrupted(rt, first.self_update_apply, fail_at=point, exc=FAILURES[failure], raw=RAW)
+
+    # (a) never a journal the gate blocks on
+    assert selfupdate.classify_journal(first._paths, first._system)[0] != "blocked"
+    # (b)+(c) the next apply recovers and finishes: new commit, value migrated, no journal/anchor
+    nxt = svc()
+    assert nxt.self_update_apply().ok
+    assert gitrepo.git(work, "rev-parse", "HEAD") == gitrepo.git(work, "rev-parse", "origin/main")
+    assert "ropt" not in cfgmod.load_stack_config(nxt._paths, "s")
+    assert svc().stack_config("s")["ropt"] == "NEW"              # follows the new default
+    assert selfupdate.read_migration_journal(nxt._paths) == (None, False)
+    assert gitrepo.git(work, "for-each-ref", "refs/lhpc") == ""
+    assert not (rt / "state" / "locks" / "controller-task-admission.owner").exists()
diff --git a/tests/install/test_source_interruption.py b/tests/install/test_source_interruption.py
new file mode 100644
index 0000000..4fca0d5
--- /dev/null
+++ b/tests/install/test_source_interruption.py
@@ -0,0 +1,192 @@
+"""A source adoption or update interrupted at every durable write is recovered and can be retried.
+
+The source transaction (`install.py` `adopt_source`, `source_fs.py`) records its candidate in a
+`state/source-txn/*.staging` record before the clone exists, stages the new tree in a
+`.<name>.candidate-*` sibling, journals the activation (`state/source-txn/<stem>.json`), archives
+the active source to `.<name>.prev`, renames the candidate into place, writes the ownership
+record, removes the archived prior and closes journal and staging record. The journal and records
+go through `runtime_fs`; the tree moves are descriptor-relative calls in `source_fs` (and the
+local-fallback copy `shutil` in `install.py`), which the harness gates the same way. Each case
+fails ONE write (disk full, I/O error, Ctrl-C) during a first adoption and during an update
+v1 → v2 over an operator's own added file, and proves: recovery (`recover_source_activations`,
+which the next writer runs first) resolves what is left, the box then holds no source or one
+whole version with a record naming that commit and the added file intact — no candidate,
+archived prior, journal or staging record — and the same operation succeeds.
+"""
+from __future__ import annotations
+
+import pytest
+
+from interrupts import FAILURES, durable_writes, run_interrupted
+from lhpc.core import install as installmod
+from lhpc.core import source_fs, source_registry
+from lhpc.core.model import Component, ComponentKind, SourceSpec
+
+pytestmark = pytest.mark.safety("source-transaction")
+
+COMP = Component(id="app", name="app", kind=ComponentKind.SERVICE,
+                 source=SourceSpec(path="src/app", local_dir="app"))
+RAW = [(source_fs, "os.mkdir"), (source_fs, "os.rename"), (source_fs, "os.rmdir"),
+       (source_fs, "os.unlink"), (source_fs, "_rename_noreplace_at"),
+       (source_fs, "remove_bound", lambda e: (False, str(e))),     # reports, never raises
+       (source_fs, "carry_extras", lambda e: f"notes.txt: could not be written ({e})"), (installmod, "shutil.copytree"), (installmod, "shutil.copy2"),
+       (installmod, "shutil.rmtree")]
+CANDIDATE = ".app.candidate-*-*"
+STAGING = "app-*.app.candidate-*-*.staging"
+# A first adoption's durable writes, in order (install.py `adopt_source` → `_activate_held`).
+ADOPT = [
+    ("write_marker", "state/locks/source-txn-index.owner"),
+    ("write_marker", "state/locks/source.src-app.owner"),
+    ("ensure_dir", "src"),
+    ("os.mkdir", ".lhpc-atomic-probe-*-*"),                       # the filesystem's renameat2
+    ("source_fs._rename_noreplace_at", ".lhpc-atomic-probe-*-*"),  # probe, once per device
+    ("os.rmdir", ".lhpc-atomic-probe-*-*-b"),
+    ("open_marker_excl", "state/source-txn/" + STAGING),
+    ("os.mkdir", CANDIDATE),
+    ("OwnedMarker.rewrite", STAGING),
+    ("source_fs.remove_bound", CANDIDATE),
+    ("os.mkdir", CANDIDATE),
+    ("OwnedMarker.rewrite", STAGING),
+    ("shutil.copytree", "local/app/.git"),
+    ("shutil.copy2", "local/app/file.txt"),
+    ("open_marker_excl", "state/source-txn/app-*.json"),
+    ("source_fs._rename_noreplace_at", CANDIDATE),                # candidate -> active
+    ("OwnedMarker.rewrite", "app-*.json"),                         # activated
+    ("write_marker", "state/source-registry/app-*.json"),
+    ("OwnedMarker.remove", "app-*.json"),
+    ("OwnedMarker.remove", STAGING),
+    ("unlink", "state/locks/source.src-app.owner"),
+    ("unlink", "state/locks/source-txn-index.owner"),
+]
+# The update's durable writes, in order: the same, with the prior archived and removed.
+UPDATE = [
+    ("write_marker", "state/locks/source-txn-index.owner"),
+    ("write_marker", "state/locks/source.src-app.owner"),
+    ("ensure_dir", "src"),
+    ("open_marker_excl", "state/source-txn/" + STAGING),           # the staging record
+    ("os.mkdir", CANDIDATE),
+    ("OwnedMarker.rewrite", STAGING),                              # its identity
+    ("source_fs.remove_bound", CANDIDATE),               # no remote: clone fails,
+    ("os.mkdir", CANDIDATE),                                       # the local fallback restages
+    ("OwnedMarker.rewrite", STAGING),
+    ("shutil.copytree", "local/app/.git"),
+    ("shutil.copy2", "local/app/file.txt"),
+    ("open_marker_excl", "state/source-txn/app-*.json"),           # the activation journal
+    ("source_fs._rename_noreplace_at", "app"),           # active -> .app.prev
+    ("OwnedMarker.rewrite", "app-*.json"),                         # prior-archived
+    ("source_fs.carry_extras", "?"),
+    ("source_fs._rename_noreplace_at", CANDIDATE),       # candidate -> active
+    ("OwnedMarker.rewrite", "app-*.json"),                         # activated
+    ("write_marker", "state/source-registry/app-*.json"),          # the ownership record
+    ("source_fs._rename_noreplace_at", ".app.prev"),     # prior -> quarantine
+    ("source_fs.remove_bound", "..app.prev.quarantine-*-*"),
+    ("OwnedMarker.remove", "app-*.json"),
+    ("OwnedMarker.remove", STAGING),
+    ("unlink", "state/locks/source.src-app.owner"),
+    ("unlink", "state/locks/source-txn-index.owner"),
+]
+
+
+@pytest.fixture
+def box(git, make_repo, installer, monkeypatch):
+    """`box(root, op) -> (inst, run, versions)`: the local fallback at v1; for "update", `app`
+    adopted at v1, the operator's `notes.txt` added to it and the local advanced to v2. `run()`
+    is the operator's adoption or update; `versions` maps each commit to its `file.txt`."""
+    monkeypatch.setattr(source_fs, "_ATOMIC_OK_DEVS", set())     # the probe runs once per test
+
+    def make(root, op):
+        local = root / "local" / "app"
+        versions = {make_repo(local, {"file.txt": "v1\n"}): b"v1\n"}
+        inst = installer(COMP, root=root)
+        if op == "adopt":
+            return inst, lambda: inst.adopt_source(COMP, source="dev"), versions
+        assert inst.adopt_source(COMP, source="dev").status == "done"
+        (root / "src" / "app" / "notes.txt").write_text("mine\n")     # the operator's own file
+        (local / "file.txt").write_text("v2\n")
+        git(local, "commit", "-qam", "v2")
+        versions[git(local, "rev-parse", "HEAD")] = b"v2\n"
+        return inst, lambda: inst.adopt_source(COMP, force=True, source="dev"), versions
+    return make
+
+
+def _copy_run_sorted(writes):
+    """The writes in order, except the copy of the local tree: `copytree` visits its entries in
+    the filesystem's directory order (tmpfs and ext4 differ), so that run is compared sorted."""
+    copies = ("shutil.copytree", "shutil.copy2")
+    i = next((n for n, w in enumerate(writes) if w[0] in copies), len(writes))
+    j = i
+    while j < len(writes) and writes[j][0] in copies:
+        j += 1
+    return writes[:i] + sorted(writes[i:j]) + writes[j:]
+
+
+@pytest.mark.parametrize("op", ["adopt", "update"])
+def test_the_operation_writes_exactly_the_pinned_points(tmp_path, box, op):
+    inst, run, _ = box(tmp_path / "rt", op)
+    with durable_writes(inst.paths.runtime_root, raw=RAW) as log:
+        assert run().status == "done"
+    assert _copy_run_sorted(log) == _copy_run_sorted({"adopt": ADOPT, "update": UPDATE}[op])
+
+
+# Known recovery defects (code-review/code-report-T2.md), each a strict xfail: a fix turns it
+# into an XPASS, which fails until the mark is removed.
+KD_S1 = ("KD-S1: leaving the staging block by an exception or a failed cleanup removes the "
+         "staging record (install.py `_staged_clone_record` finally) while the candidate stays; "
+         "no recovery path names it again")
+KD_S2 = ("KD-S2: carrying a local file changes the candidate's recorded ctime; a crash between "
+         "the carry and the activation leaves no active source and recovery refuses")
+KD_S3 = ("KD-S3: a failed removal of the quarantined prior leaves `..app.prev.quarantine-*` after "
+         "the journal is closed; no recovery removes it and uninstall/clean refuse until it is "
+         "removed by hand")
+KD_S4 = ("KD-S4: a Ctrl-C inside the atomic-rename probe leaves `src/.lhpc-atomic-probe-*`; "
+         "source_fs.py `require_atomic_rename` removes it only on an OSError")
+ALL = tuple(sorted(FAILURES))
+KNOWN = {
+    "update": {**{(k, f): KD_S1 for k in (5, 8, 9, 10) for f in ("KeyboardInterrupt",)},
+               **{(k, f): KD_S1 for k in (6, 11) for f in ALL},
+               (15, "KeyboardInterrupt"): KD_S2,
+               **{(19, f): KD_S3 for f in ALL}},
+    "adopt": {**{(k, "KeyboardInterrupt"): KD_S4 for k in (4, 5)},
+              **{(k, "KeyboardInterrupt"): KD_S1 for k in (8, 11, 12, 13)},
+              **{(k, f): KD_S1 for k in (9, 14) for f in ALL}},
+}
+CASES = [pytest.param(op, k, f, id=f"{op}-{w}:{p}-{f}",
+                      marks=[pytest.mark.xfail(strict=True, reason=KNOWN[op][k, f])]
+                      if (k, f) in KNOWN[op] else [])
+         for op, points in (("adopt", ADOPT), ("update", UPDATE))
+         for k, (w, p) in enumerate(points) for f in ALL]
+
+
+@pytest.mark.parametrize(("op", "point", "failure"), CASES)
+def test_an_interrupted_operation_recovers_and_retries(tmp_path, box, git, op, point, failure):
+    root = tmp_path / "rt"
+    inst, run, versions = box(root, op)
+    run_interrupted(root, run, fail_at=point, exc=FAILURES[failure], raw=RAW)
+
+    # (a) recovery resolves what is left: nothing blocks the next source mutation
+    inst.recover_source_activations()
+    assert not inst._pending_journals()
+    # (b) no source (a first adoption undone) or one whole version with its record; no sibling,
+    # journal or staging record; the operator's added file intact
+    txn = root / "state" / "source-txn"
+    assert not txn.exists() or list(txn.iterdir()) == []
+    held = _assert_one_whole_source(root, inst, git, versions, op, allow_absent=(op == "adopt"))
+    # (c) the retry lands on the newest version (an adoption that already completed is skipped)
+    assert run().status == ("skipped" if op == "adopt" and held else "done")
+    newest = _assert_one_whole_source(root, inst, git, versions, op, allow_absent=False)
+    assert newest == max(versions.values())
+
+
+def _assert_one_whole_source(root, inst, git, versions, op, *, allow_absent):
+    """The source tree is `app` alone (or nothing, when allowed); returns its `file.txt`."""
+    names = sorted(p.name for p in (root / "src").iterdir()) if (root / "src").exists() else []
+    if allow_absent and names == []:
+        assert source_registry.read_record(inst.paths, "src/app") is None
+        return None
+    assert names == ["app"]
+    head = git(root / "src" / "app", "rev-parse", "HEAD")
+    assert (root / "src" / "app" / "file.txt").read_bytes() == versions[head]
+    assert source_registry.read_record(inst.paths, "src/app").resolved_commit == head
+    if op == "update":
+        assert (root / "src" / "app" / "notes.txt").read_text() == "mine\n"   # never collateral
+    return versions[head]
diff --git a/tests/interrupts.py b/tests/interrupts.py
new file mode 100644
index 0000000..f6822e7
--- /dev/null
+++ b/tests/interrupts.py
@@ -0,0 +1,170 @@
+"""The interruption-and-retry harness: every durable write of an operation, one failure at a time.
+
+    from interrupts import FAILURES, durable_writes, run_interrupted, tree
+
+Every recovery-bearing transaction writes its durable state through `lhpc.core.runtime_fs` (the
+descriptor-anchored seam; callers use it as `runtime_fs.<fn>`). `durable_writes()` wraps each
+writer of that seam for the duration of a `with` block and logs every OUTERMOST call (a writer
+that delegates to another — `write_marker` → `atomic_write` → `atomic_write_bytes` — is one
+point, not three). With `fail_at=k` and `exc` it raises `exc` INSTEAD of performing write k, so
+the operation sees the disk refuse (ENOSPC, EIO) or the operator's Ctrl-C land exactly before
+that write, with everything earlier on disk — the state a crash or a full disk leaves.
+
+A writer whose contract reports an `OSError` instead of raising it (`OwnedMarker.rewrite`/`remove`
+return `False`, `source_fs.remove_bound` a `(False, reason)`) gets the injected `OSError` as that
+report, as the real failure would; a `KeyboardInterrupt` always propagates.
+
+A test pins the operation's write log — the order of a transaction's durable writes IS its crash
+contract — so a write added later fails the pin and must join the parametrisation; then, per
+(write point, failure class), it runs the operation into the failure (`run_interrupted`), runs the
+matching recovery path, checks the state it leaves (`tree()`), and retries the operation to the
+end state of an uninterrupted run.
+"""
+from __future__ import annotations
+
+import errno
+import os
+import re
+import threading
+from contextlib import contextmanager
+from pathlib import Path
+
+import pytest
+
+from lhpc.core import runtime_fs
+
+# The writers of the seam. Reads, probes and lock opens are not durable writes.
+WRITERS = ("atomic_write_bytes", "atomic_write", "write_marker", "write_launcher",
+           "create_exclusive_bytes", "open_marker_excl", "rename_leaf", "link_leaf", "unlink",
+           "unlink_link", "chmod", "replace_symlink", "publish_symlink", "ensure_dir", "mkdir")
+MARKER_WRITERS = ("rewrite", "remove")
+
+FAILURES = {
+    "ENOSPC": lambda: OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)),
+    "EIO": lambda: OSError(errno.EIO, os.strerror(errno.EIO)),
+    "KeyboardInterrupt": KeyboardInterrupt,
+}
+
+_depth = threading.local()
+
+
+def _label(name, args, root: Path) -> str:
+    """The path a write targets, relative to the runtime root (a leaf name for a descriptor-relative
+    call), with per-run nonces masked."""
+    args = [a for a in args if isinstance(a, (str, Path))]
+    if name == "mkdir":
+        args = [Path(root, *args)]                  # runtime_fs.mkdir(paths, "state", "binary")
+    if not args:
+        return "?"
+    try:
+        rel = str(Path(args[0]).relative_to(root))
+    except ValueError:
+        rel = str(args[0])
+    # nonces, pids, times, and the binary install's `tempfile` suffix
+    return re.sub(r"[0-9a-f]{8,}|\d{3,}|(?<=lhpc-binary-)[a-z0-9_]{8}", "*", rel)
+
+
+class _Module:
+    """A module's `os`/`shutil` as that module sees it: the named writers replaced, every other
+    attribute (`os.path`, the reads) the real one."""
+
+    def __init__(self, real, writers):
+        self._real = real
+        self.__dict__.update(writers)
+
+    def __getattr__(self, name):
+        return getattr(self._real, name)
+
+
+@contextmanager
+def durable_writes(root: Path, *, fail_at: int | None = None, exc=None, raw=()):
+    """Log every outermost durable write under `root` as (writer, masked path); with `fail_at`,
+    raise `exc()` in place of that write. Yields the log (a list, filled while the block runs).
+
+    `raw` names the writes a transaction makes outside the seam: `(module, "os.replace")` swaps
+    that module's own `os` reference for one whose `replace` is gated the same way (other modules
+    keep the real one); `(owner, "name")` gates a module function or class method that performs
+    a descriptor-relative write as one point. A third element, `reported`, is for a writer whose
+    contract REPORTS an `OSError` instead of raising it: the injected `OSError` becomes the return
+    value `reported(exc)`, as the real failure would."""
+    log: list[tuple[str, str]] = []
+
+    def gate(name, args, call, reported):
+        if getattr(_depth, "n", 0):
+            return call()
+        k = len(log)
+        log.append((name, _label(name, args, root)))
+        if k == fail_at:
+            err = exc()
+            if reported is not None and isinstance(err, OSError):
+                return reported(err)
+            raise err
+        _depth.n = 1
+        try:
+            return call()
+        finally:
+            _depth.n = 0
+
+    def wrap(name, real, reported=None):
+        def writer(*args, **kwargs):
+            return gate(name, args, lambda: real(*args, **kwargs), reported)
+        return writer
+
+    def wrap_marker(name, real):
+        def method(self, *args, **kwargs):
+            # the marker's own path is not on the call; its leaf name is
+            return gate(f"OwnedMarker.{name}", (Path(self.name),),
+                        lambda: real(self, *args, **kwargs), lambda e: False)
+        return method
+
+    with pytest.MonkeyPatch.context() as mp:
+        for name in WRITERS:
+            mp.setattr(runtime_fs, name, wrap(name, getattr(runtime_fs, name)))
+        for name in MARKER_WRITERS:             # an OSError is reported as False (runtime_fs.py)
+            mp.setattr(runtime_fs.OwnedMarker, name,
+                       wrap_marker(name, getattr(runtime_fs.OwnedMarker, name)))
+        swapped: dict[tuple[object, str], dict] = {}
+        for owner, dotted, *reported in raw:
+            reported = reported[0] if reported else None
+            if "." not in dotted:                   # the owner's own function or method
+                mp.setattr(owner, dotted, wrap(f"{owner.__name__.rsplit('.', 1)[-1]}.{dotted}",
+                                               getattr(owner, dotted), reported))
+                continue
+            ref, name = dotted.split(".")
+            real = getattr(getattr(owner, ref), name)
+            swapped.setdefault((owner, ref), {})[name] = wrap(dotted, real, reported)
+        for (module, ref), writers in swapped.items():
+            mp.setattr(module, ref, _Module(getattr(module, ref), writers))
+        yield log
+
+
+def run_interrupted(root: Path, operation, *, fail_at: int, exc, raw=()):
+    """Run `operation()` with write `fail_at` replaced by `exc()`; returns (log, what it raised or
+    returned). The injected failure must have been reached — a point the run never hits is a
+    stale pin, not a pass."""
+    with durable_writes(root, fail_at=fail_at, exc=exc, raw=raw) as log:
+        try:
+            outcome = operation()
+        except BaseException as e:      # noqa: BLE001 — the injected class itself, KeyboardInterrupt included
+            outcome = e
+    assert len(log) > fail_at, f"write point {fail_at} not reached: {log}"
+    return log, outcome
+
+
+def tree(root: Path, *, skip=()) -> dict[str, bytes | str]:
+    """Every entry under `root` (files by content, dirs and links by kind), minus the relative
+    prefixes in `skip` — the state a recovery must return to."""
+    out: dict[str, bytes | str] = {}
+    for dirpath, dirnames, filenames in os.walk(root):
+        for n in dirnames + filenames:
+            p = Path(dirpath, n)
+            rel = str(p.relative_to(root))
+            if any(rel == s or rel.startswith(s + "/") for s in skip):
+                continue
+            if p.is_symlink():
+                out[rel] = "link:" + os.readlink(p)
+            elif p.is_dir():
+                out[rel] = "dir"
+            else:
+                out[rel] = p.read_bytes()
+    return out
```

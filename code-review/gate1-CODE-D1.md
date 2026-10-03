# Gate 1 — CODE review request: D1

Please judge batch D1 of the architecture consolidation of loraham-pi-control: the plan (below) and its two commits — the plan commit and the docs commit that adds the invariant table to docs/architecture.md (owner file:line, locks in order, durable state and recovery, proving tests, one row per safety invariant) and trims the Safety-model prose so each fact is stated once. Judge: is every row truthful as the report says it was verified; is any guarantee lost or stated twice; are the gaps honest; does the report match the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Answer form

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| `b2a82ba` D1: plan — the invariant table | | |
| `b6655e2` D1: the invariant table — owner, locks, durable state and proving tests per safety invariant | | |

Final line: GREEN / GREEN WITH NOTES / RED

## The plan (plans/PLAN-D1.md)

````markdown
# PLAN-D1 — the invariant table in docs/architecture.md

Goal: one place where a reviewer finds, for every safety claim, its owner (`file:line` of the
enforcing function), its locks in order, its durable state with the recovery rule, and the tests
that prove it. Docs only: `docs/architecture.md`. No code, no tests.

## Today

- `docs/architecture.md` § Safety model (lines 184-279 on the base) states 16 guarantees as
  bullets. Some bullets end with a module and a test file (`core/install.py`;
  `tests/install/test_source.py`). Others name neither. None says which lock or which journal.
  So the "where" facts are scattered and partly missing.
- The firewall, self-update, boot-restore, binary-channel and radio guarantees are stated on other
  pages: firewall.md, deployment.md, operations.md and § Radios. Nothing maps them to code.

## Change

1. Add `### Invariant table` at the end of § Safety model, sorted by area: admission/locks,
   config transactions, install/recovery, binary channel, PKI/web, firewall, self-update,
   radio/band ownership, lab. Columns: area · invariant · owner · locks (in order) ·
   durable state · recovery · proving tests.
2. One place per fact.
   - The invariant column is one short phrase that links to the bullet or page where the
     guarantee is stated.
   - The bullets lose their trailing module and test references; those now live only in the
     table.
   - The opening sentence of § Safety model points to the table.
   - § Locking gains the one lock-order sentence. It was stated nowhere in the docs before; the
     code states it at `lhpc/core/services.py:2505`.
3. Where no test proves a claim, the tests cell says `gap: <what>` and the report lists it for the
   handler.

## Sources and verification (per row)

- **Owner:** `grep -n "def <name>(" <file>` on the base tree. The row cites the `def` line.
- **Locks:** read where the lock is taken. Examples: the `with (...)` of `update`/`uninstall`;
  `_admit`; `config_lock`; `firewall_helper.LOCK_PATH`; `selfupdate._LOCK`/`_CTRL_RUNTIME_LOCK`.
- **Durable state:** grep the path constant. Examples: `JOURNAL_REL`, `_journal_path`,
  `_txn_journal`, `REQUEST_REL`, `_MIGRATE_MARKER`.
- **Proving tests:** `grep -n "^def <test>(" <file>`. Each test must exist.
- **Mechanical check:** a script parses the table and checks two things. Every
  `` `path:line` `name` `` must land on `def name(` at that line. Every `` `tests/…::name` ``
  must exist. It also checks every link anchor. Its source and output go into the report.

## Gaps expected (from code and tests only)

- A write over a `local.toml` that is not valid TOML. Only the unsupported-structure case is
  tested.
- Uninstall keeping secrets and profiles. Config is tested by `test_recovery.py`.
- A wheel installed into a fresh venv. The prose cites `tests/repo/test_packaging.py`, but no
  test there installs a wheel.
- The daemon's `spi0.lock`. The daemon owns it, not LHPC; LHPC only tests the claim model.

## Risk and how it is ruled out

- **A stale line number.** The checker runs on the D1 commit. U1 (same branch, after D1) edits
  text in five owner files and shifts their lines. U1 therefore re-runs the checker and corrects
  the shifted numbers in its own commit. This is the one place U1 touches `docs/architecture.md`;
  it is named in U1's report.
- **Two statements of one fact.** Every trimmed bullet keeps its guarantee. The table adds no
  guarantee wording of its own beyond the short label and the lock order. The lock order is
  stated once, in the bullet.
- **A row claiming a test that proves something else.** The tests were chosen by name and read
  where the name was ambiguous. Partial proofs are marked `gap:`.

## Open questions

- Several rows' tests do not carry `@pytest.mark.safety`: admission, reslock, snapshot memo, boot
  restore, binary channel, self-update, path containment and web jobs. So `-m safety` is narrower
  than the table. Recommendation: a later batch marks them. That changes `tests/`, which is out of
  this batch.
````

## Commit list

- `b2a82ba` D1: plan — the invariant table
- `b6655e2` D1: the invariant table — owner, locks, durable state and proving tests per safety invariant
- (this file and the code report are added by the report commit that follows)

## The code report (code-review/code-report-D1.md)

`````markdown
# Code report — D1 (the invariant table)

Base: `integration/0.12.0` at `e38803b`. Docs only. No file under `lhpc/`, `tests/` or
`testlab/` changed.

## Commits

| sha | subject | files | tests |
|---|---|---|---|
| `b2a82ba` | D1: plan — the invariant table | `plans/PLAN-D1.md` | — |
| `b6655e2` | D1: the invariant table — owner, locks, durable state and proving tests per safety invariant | `docs/architecture.md` | none added (docs). Red-before: not applicable. The table checker below is the gate for this commit. |

## What changed

- New `### Invariant table` at the end of § Safety model. It has 35 rows in 9 areas, sorted in
  the order the brief gives: admission/locks, config transactions, install/recovery, binary
  channel, PKI/web, firewall, self-update, radio/band ownership, lab.
- Each row gives:
  - the invariant (a short label plus a link to where it is stated);
  - the owner (`file:line` of the `def`);
  - the locks in order;
  - the durable state and its recovery rule;
  - the proving tests (`module::name`).
- The 16 Safety-model bullets keep their guarantees. Their trailing module and test references
  were removed, because the table is now the one place for them. Two changes are not removals:
  - The § Safety model lead sentence now points to the table.
  - The *Locking* bullet gains the lock order, which no doc stated before. Its source is
    `lhpc/core/services.py:2505`. The update and uninstall `with` blocks
    (`lhpc/core/service_maintenance.py:1841` and `:2359` on the base) show the same order.
- Reworded lines in the prose: "Path containment" (`core/runtime_fs.py` → "Every runtime write"),
  "Typed validation", "Daemon sockets", "Web" and "Packaging". They lost their code paths and
  kept their meaning. Visible with `git diff --color-moved=dimmed-zebra e38803b b6655e2 -- docs/architecture.md`.

## How every row was verified

1. Owner, mechanically. A script reads the table. For every ```path:line` `name``` it asserts
   that line `line` of `path` contains `def name(`. For every ```tests/…::name``` it asserts
   `^def name(` exists in that file. It also checks every link anchor. Output on `b6655e2`:
   `rows=35 owners=77 tests=105 bad=0`. Source:

```python
"""Verify every owner `path:line` `name` and every `tests/...::name` in the invariant table."""
import re, sys, pathlib
doc = pathlib.Path("docs/architecture.md").read_text().split("### Invariant table", 1)[1].split("\n## ", 1)[0]
rows = [l for l in doc.splitlines() if l.startswith("| ") and not l.startswith("| area") ]
bad = owners = tests = 0
for row in rows:
    cells = [c.strip() for c in row.strip("|").split("|")]
    if len(cells) != 6:
        print("BADROW", row[:80]); bad += 1; continue
    for cell in (cells[2], cells[3], cells[4]):
        path = None
        for m in re.finditer(r"`([\w/.]+\.py)?:(\d+)` `(\w+)`", cell):
            path = m.group(1) or path
            n, name = int(m.group(2)), m.group(3)
            line = pathlib.Path(path).read_text().splitlines()[n - 1]
            owners += 1
            if not re.search(rf"def {name}\(", line):
                print("OWNER MISMATCH", path, n, name, "->", line.strip()); bad += 1
    path = None
    for m in re.finditer(r"`((?:testlab/)?tests/[\w/]+\.py)?::(\w+)`", cells[5]):
        path = m.group(1) or path
        tests += 1
        if not re.search(rf"^def {m.group(2)}\(", pathlib.Path(path).read_text(), re.M):
            print("TEST MISSING", path, m.group(2)); bad += 1
    for m in re.finditer(r"\]\(([\w.-]+\.md)?(#[\w-]+)?\)", cells[1]):
        f = pathlib.Path("docs") / (m.group(1) or "architecture.md")
        if not f.exists():
            print("LINK FILE", f); bad += 1; continue
        if m.group(2):
            slugs = {re.sub(r"[^\w\- ]", "", h.lower()).strip().replace(" ", "-")
                     for h in re.findall(r"^#+ (.+)$", f.read_text(), re.M)}
            if m.group(2)[1:] not in slugs:
                print("ANCHOR", f, m.group(2)); bad += 1
print(f"rows={len(rows)} owners={owners} tests={tests} bad={bad}")
sys.exit(1 if bad else 0)
```

2. Locks and durable state, by reading the code where the lock is taken or the path is built.
   Commands per row:
   - admission: `grep -n "def _admit" lhpc/core/services.py`, key at `:2507`;
   - reslock: `grep -n '"locks"' lhpc/core/reslock.py` → `:66`, `:70`;
   - config: `grep -n '".lock"' lhpc/core/config.py` → `:107`; `config-txn.json` → `:1746`;
   - source transactions: `lhpc/core/install.py:1043` (`source-txn` dir), `:1045-1059`
     (journal name), `:1202` (`source-txn-index`), `:1269` (`.staging` record);
   - uninstall order: the `with` at `lhpc/core/service_maintenance.py:2359` (base);
   - binary: `lhpc/core/binary_install.py:49` (`JOURNAL_REL`); order admission → covered paths
     in `binary_install` (base `:192-202`);
   - boot restore: `lhpc/core/boot_restore.py:27`;
   - jobs: `lhpc/core/jobresult.py:27`, `lhpc/core/jobs.py:262`;
   - firewall: `lhpc/core/firewall_helper.py:633-634`, `:975-976`;
   - self-update: `lhpc/core/selfupdate.py:36-38`, `lhpc/core/updater_units.py:48-49`;
   - owned records: `lhpc/core/lifecycle.py:883-884`.
3. Statements: each row's invariant label links to the bullet or page that states it. The checker
   checks the anchors.

## Gaps for the handler (no proving test)

1. A write over a `local.toml` that is not valid TOML keeps the bytes. Only the
   unsupported-structure case is tested (`test_local_unsupported_structures_block_and_preserve`).
2. Uninstall keeps secrets and profiles. Config is proven by
   `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`.
3. "A wheel installed into a fresh venv runs". The base prose cited
   `tests/repo/test_packaging.py`, but no test there builds or installs a wheel
   (`grep -rn "wheel" tests` → no hit).
4. The daemon's `spi0.lock`. The daemon enforces it, not LHPC; only LHPC's claim model is tested.

Also noted: "No GET runs a network command" has no single enforcing function; the row says so.
Many proving tests carry no `safety` marker (see the plan's open question).

## The 6-point block

1. **Contracts.** None touched; documentation only.
2. **Invariants and tests.** No invariant changes. The table states which test proves each one.
   All 105 cited tests exist (checker).
3. **Known failure classes.**
   - Fakes, probes and interrupts: not applicable, no code.
   - Same decision across entry paths: not applicable.
   - Stacked conflicts: `docs/architecture.md` is also changed by `cons/Q2` (`git diff --stat
     e38803b...origin/cons/Q2 -- docs/architecture.md` → 8 lines). Q2's hunks sit in the
     Safety-model bullets this batch trims, so a textual conflict on stacking is likely. The
     resolution is mechanical: keep Q2's sentence and drop the module and test references.
   - U1 (this branch) shifts owner lines in five files. U1 re-runs the checker and corrects them
     in its own commit.
4. **Test rules.** No test added.
5. **Whole test directories.** No test directory touched.
   - `python -m pytest -q -p no:cacheprovider tests/repo` → `410 passed, 5 skipped` (on
     `b6655e2`).
   - `ruff check lhpc testlab` → `All checks passed!`.
   - `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review.**
   - Found and fixed: the plan said 19 bullets; the measured count is 16 (`grep -c '^- \*\*'` on
     the base section). Fixed before this report.
   - Found: the "Packaging" bullet's test reference pointed to a test that does not prove the
     claim. It is now a listed gap, not a silent removal.
   - The docs and this report describe exactly the diff.

## Simplicity guardrails

- No code; no abstraction.
- The prose shrinks: 36 lines removed, 74 added, of which the table is 47. Net +38 lines
  (`git diff --numstat e38803b b6655e2 -- docs/architecture.md` → `74 36`).
- Dependencies: not applicable.

## Precision checklist

1. Absolute sentences:
   - "every row's claims verified" means the mechanical checks above plus the reads listed. Lock
     claims were read, not machine-checked.
   - "one place per fact" is limited to the facts moved here.
2. One-line texts: not applicable.
3. Fault injectors: not applicable.
4. Moved lines: shown with `--color-moved=dimmed-zebra`. The reworded bullets are named above.
5. Helpers swallowing exceptions: not applicable.
6. Every run cited above is on `b6655e2`, which is on this branch.

## Deviations

- `plans/PLAN-Q1.md` is not on `integration/0.12.0`. It is on `cons/Q1`, and was read there.
- `docs/README.md`: no change. The index lists pages, not sections.

## Numbers, measured

| figure | command | output |
|---|---|---|
| architecture.md lines before / after | `git show e38803b:docs/architecture.md \| wc -l`; `wc -l < docs/architecture.md` | 305 / 343 |
| +/- | `git diff --numstat e38803b b6655e2 -- docs/architecture.md` | 74 / 36 |
| rows, owners, tests | checker | 35, 77, 105, bad=0 |
| Safety-model bullets on the base | `git show e38803b:docs/architecture.md \| sed -n '/^## Safety model/,/^## Controller/p' \| grep -c '^- \*\*'` | 16 |
| gap markers | `grep -c "gap:" docs/architecture.md` | 4 |
| plan length | `wc -l plans/PLAN-D1.md` | 71 |
| tests/repo | `python -m pytest -q -p no:cacheprovider tests/repo` | 410 passed, 5 skipped |
`````

## Full diff of the commits under review

```diff
diff --git a/docs/architecture.md b/docs/architecture.md
index 144c64a..71849b4 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -183,82 +183,75 @@ and confirms a key the daemon reports back by reading back `GET STATUS`. Only wh
 
 ## Safety model
 
-The guarantees the controller gives, each with where it is implemented and proven.
+The guarantees the controller gives. Where each one is enforced, which locks it takes, what it
+leaves on disk and which test proves it: the [invariant table](#invariant-table).
 
 - **No shell.** Every launch, build, test and web job is structured argv with `shell=False`.
   `core/commands.py` expands the manifest's argv token templates so a validated user value is
   always its own token — it cannot merge with an option, change the executable, cwd or env, or
   become shell syntax. Interactive components get their copy-paste command from the same spec.
-  `tests/core/test_structured_exec.py` (rendered-runner AST check + spawn-argv capture).
-- **Typed validation.** Every value is validated by type (`core/validators.py`) before
-  persistence and before execution. `@file:` secrets fail closed — a missing, unreadable or empty
-  secret, or one that is a symlink, not a regular file or over 64 KiB, blocks the launch
-  (`core/commands.py`); a `pkg-config` failure aborts a build
-  (`core/build_launcher_runtime.py`).
+- **Typed validation.** Every value is validated by type before persistence and before
+  execution. `@file:` secrets fail closed — a missing, unreadable or empty secret, or one that is
+  a symlink, not a regular file or over 64 KiB, blocks the launch; a `pkg-config` failure aborts a
+  build.
 - **Identity-verified stopping.** Each launch records full process identity under a unique id
   (`state/owned/<comp>__<band>__<pid>__<nonce>.json`: pid, start time, pgid, sid, executable, argv
   fingerprint). `Lifecycle.stop` re-reads `/proc` and signals only an LHPC-owned session leader
   whose identity still matches; any mismatch means no signal and a `manual_required` verdict with
   the exact PID. It waits for verified cessation before clearing the record (no auto-SIGKILL).
-  `core/lifecycle.py`; `tests/core/test_process_ownership.py`.
-- **Path containment.** `core/runtime_fs.py` opens the runtime root and walks each parent with
+- **Path containment.** Every runtime write opens the runtime root and walks each parent with
   `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write; atomic
   writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
-  and caught at every boundary. `tests/core/test_runtime_fs.py`.
+  and caught at every boundary.
 - **Source transactions.** An update clones a candidate beside the destination (recorded before
   the clone starts, so recovery removes a clone a crash interrupted), archives the
   prior source to a transaction-owned `.prev`, activates by atomic no-clobber rename, writes the
   ownership record, then removes the `.prev` — journalled at every step. A failed activation never
   destroys the active source; an unresolved or malformed journal blocks all source mutation until
-  an operator resolves it. `core/install.py`, `core/source_fs.py`;
-  `tests/install/test_staged_update.py`, `tests/install/test_source.py`.
+  an operator resolves it.
 - **Locally added files are never collateral.** An update carries the operator's and the stack's
   own added files into the new source inside the activation and destroys the archived prior only
   after each is proven present there; a path the new upstream also ships is a refusal, never a
-  merge. Operator rule: [provenance](provenance.md#ownership-records). `core/install.py`,
-  `core/source_fs.py`; `tests/install/test_source.py`.
+  merge. Operator rule: [provenance](provenance.md#ownership-records).
 - **Locking.** Start, stop, restart, build, update, uninstall and clean take named non-blocking
-  locks; a contended operation refuses immediately, naming the holder. `core/reslock.py`.
+  locks; a contended operation refuses immediately, naming the holder. Lock order: task
+  admission, then configuration stability, then the source-transaction index and the source
+  paths (or a stack's lifecycle bundle), then the self-update lock.
 - **Config as a transaction.** A Settings save or reset validates the whole submission before any write;
   files are journalled and atomically replaced, and a mid-write failure rolls back; a journal a
   crashed process left behind is finished under the config lock before ANY writer runs (the
   non-transactional hardware, GPS, operator and remote saves included), or the lock is refused;
   each `lhpc` process also finishes it eagerly at start. A malformed `local.toml` is preserved, never overwritten; a present-but-malformed
   per-stack file is a typed error (CLI: clean failure, web: 409, no echo of the bad value) — only
-  an *absent* file means "use defaults". `tests/core/test_config.py`, `tests/stacks/test_stack_params.py`.
+  an *absent* file means "use defaults".
 - **Truthful outcomes.** Every component yields one typed `Outcome`; `ActionResult.ok` derives
   entirely from those. `start` fails unless every required component verified ready (a daemon
   start verifies each band's CONF socket); a stop counts as verified only when the process ceased
   AND every ready endpoint disappeared, and markers clear only then; `update` reports nonzero on
-  partial failure; CLI exit status and web flash agree. `tests/core/test_post_start.py`.
+  partial failure; CLI exit status and web flash agree.
 - **Daemon sockets.** One bounded CONF parser for every read (a reply reaching the 4 KiB read cap,
   or over-tokenized, is rejected); a TX-mode change is read back, and an unconfirmed change blocks
-  dependents (`core/daemon_control.py`). A compatibility `/tmp` socket is peer-checked with
-  `SO_PEERCRED` after `connect()` and before any payload — the peer UID must equal the
-  controller's; protected `/run/loraham` sockets keep their dedicated-UID model and are exempt
-  (`core/probes/backends.py`; `tests/web/test_socket_peercred.py`).
-- **Web.** Loopback bind only (`run_server` refuses a non-loopback host); every serving mode
-  rejects an empty, malformed or unrelated `Host` with 400 before any session or CSRF work
-  (`tests/web/test_trusted_host.py`); mutations are POST + CSRF token + explicit confirm; every
+  dependents. A compatibility `/tmp` socket is peer-checked with `SO_PEERCRED` after `connect()`
+  and before any payload — the peer UID must equal the controller's; protected `/run/loraham`
+  sockets keep their dedicated-UID model and are exempt.
+- **Web.** Loopback bind only; every serving mode rejects an empty, malformed or unrelated `Host`
+  with 400 before any session or CSRF work; mutations are POST + CSRF token + explicit confirm; every
   response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: no-referrer` and a `Content-Security-Policy` locked to `'self'` (with
-  `base-uri`/`frame-ancestors` `'none'`); per-stack config paths stay inside `config/stacks/`
-  (`tests/web/test_web.py::test_config_path_cannot_escape_via_band_or_id`). No GET route runs a
-  network or git-remote command (`tests/web/test_web.py::test_get_routes_make_no_network_calls`).
-  Network exposure is the nginx front end ([serving model](deployment.md#serving-model)).
+  `base-uri`/`frame-ancestors` `'none'`); per-stack config paths stay inside `config/stacks/`.
+  No GET route runs a network or git-remote command. Network exposure is the nginx front end ([serving model](deployment.md#serving-model)).
 - **Evidence once per request.** A page render reads each piece of evidence once (status
   snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git state
   per distinct checkout) through a thread-local request memo dropped at every request start and
   around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`. Every
   public `ControllerService` entry that writes anything is `@invalidates_snapshot`; the rest are
-  listed with a reason in `snapshot_memo.SNAPSHOT_NEUTRAL`
-  (`tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`). The
+  listed with a reason in `snapshot_memo.SNAPSHOT_NEUTRAL`. The
   neutral writers whose writes no snapshot input observes (lock files, the native RF-log roll,
   the GPS receiver's tty mode) name them in `SNAPSHOT_NEUTRAL_WRITERS`. Every neutral entry is
   driven under a write trace on a live runtime root: a "read-only" one that writes fails, a
-  writer must write exactly its listed paths (`test_every_traced_write_is_classified`); a trace
-  of a fresh snapshot on that state proves none is read (`test_no_neutral_writer_path_is_a_snapshot_input`).
+  writer must write exactly its listed paths; a trace of a fresh snapshot on that state proves
+  none is read.
 - **Detached web jobs** (install, build, test, start, restart). The console reserves an attempt
   marker (`state/jobresults/<log>.json`), spawns the child under task admission, captures its
   process identity, releases its own admission, and only then publishes the `.job` tracking
@@ -268,13 +261,58 @@ The guarantees the controller gives, each with where it is implemented and prove
   PID-reuse-resistant; `prune_logs()` never deletes the log of an active job.
 - **Uninstall protection.** Uninstall refuses while a target runs, never removes a source still
   referenced by another component (`loraham-kiss-tnc` and `loraham-kiss-serial` share
-  `src/loraham-kiss-tnc`), and never deletes config, secrets or profiles
-  (`tests/core/test_uninstall_safety.py`). `uninstall.sh`:
+  `src/loraham-kiss-tnc`), and never deletes config, secrets or profiles. `uninstall.sh`:
   [operations](operations.md#backup--restore).
 - **Boot restore replays only saved configuration** through the normal gated start path
   ([operations](operations.md#not-a-supervisor)).
-- **Packaging.** Tracked assets live in `lhpc/data/` and load via `importlib.resources`
-  (`core/assets.py`); a wheel installed into a fresh venv runs (`tests/repo/test_packaging.py`).
+- **Packaging.** Tracked assets live in `lhpc/data/` and load via `importlib.resources`; a wheel
+  installed into a fresh venv runs.
+
+### Invariant table
+
+One row per safety invariant: the statement is in the bullet or page the first column names; the
+row says where it is enforced (`file:line` of the owning function), the locks it takes in order,
+what it keeps on disk and how a crash is recovered, and the tests that prove it. *Gap* marks a
+claim with no proving test. Paths in the durable-state column are relative to the runtime root
+unless absolute.
+
+| area | invariant | owner | locks (in order) | durable state · recovery | proving tests |
+|---|---|---|---|---|---|
+| admission / locks | task admission: no new task while an uninstall or a self-update is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2509` `_admit`; `lhpc/core/service_selfupdate.py:1110` `_task_admission_blocked` | `controller-task-admission`, always first | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
+| admission / locks | named non-blocking locks; a contended operation refuses naming the holder ([Locking](#safety-model)) | `lhpc/core/reslock.py:106` `operation_lock`; `lhpc/core/services.py:2572` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
+| admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py:2559` `_admission_guard`, `:218` `_config_stable`, `:360` `_source_operation_guard`, `:2636` `_lifecycle_guard` | admission → config stability → `source-txn-index` → source paths (sorted) or the `lifecycle.<stack>` bundle → self-update lock | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
+| config transactions | a save validates everything first, is journalled and rolls back; a crashed save is finished before any writer runs ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1901` `apply_config_transaction`, `:1854` `_finish_pending_journal`, `:1881` `recover_config_journal_at_startup` | config lock `config/.lock` (`lhpc/core/config.py:77` `config_lock`) | `state/config-txn.json`; `lhpc/core/config.py:1800` `recover_config_transaction` rolls back, or blocks and keeps the journal | `tests/core/test_config.py::test_pending_journal_is_recovered_before_next_save`, `::test_rollback_failure_retains_journal_and_blocks_later`, `::test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`; `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` |
+| config transactions | a malformed `local.toml` is kept; only an absent per-stack file means defaults ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1320` `_write_local_tables`, `:2019` `load_stack_config` | config lock (writes only) | `config/local.toml`, `config/stacks/<id>[@band].toml` | `tests/core/test_config.py::test_local_unsupported_structures_block_and_preserve`, `::test_malformed_stack_config_raises_and_is_preserved`, `::test_absent_stack_config_is_defaults`, `::test_web_returns_409_on_malformed_config`; gap: a write over a `local.toml` that is not valid TOML |
+| install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py:512` `_stage_and_activate`, `:1277` `_staged_clone_record`, `:1920` `_activate_held`, `:1204` `_pending_journals` | admission → `source-txn-index` → source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py:1227` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
+| install / recovery | locally added files are carried, never collateral ([Locally added files](#safety-model)) | `lhpc/core/source_fs.py:546` `carry_extras`, `:688` `extras_preserved` | as source transactions | `.prev` and its journal are kept while a carry is unproven | `tests/install/test_source.py::test_an_added_file_colliding_with_the_new_upstream_refuses`, `::test_a_carry_failure_restores_the_prior_and_refuses`, `::test_an_addition_made_after_the_carry_retains_the_archived_prior` |
+| install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1255` `stop`, `:1117` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2444` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
+| install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:71` `_walk_parent`, `:153` `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
+| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2280` `uninstall` | admission → config stability (shared) → every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
+| install / recovery | boot restore replays saved configuration through the gated start, honours stop notes ([operations](operations.md#not-a-supervisor)) | `lhpc/core/service_boot_restore.py:280` `boot_restore_run`, `:121` `_write_stop_intent` | admission, then the normal start locks | `state/boot-restore.json`, `state/stop-intent/<stack>.json`; `lhpc/core/service_boot_restore.py:258` `_boot_journal_recovery` consumes an item left *attempting*, never retries it | `tests/core/test_boot_restore.py::test_crash_after_attempting_never_retries_but_pending_survives`, `::test_boot_restore_run_honors_stop_intent_end_to_end`, `::test_driver_admission_refusal_consumes_nothing` |
+| install / recovery | a wheel installed into a fresh venv runs ([Packaging](#safety-model)) | `lhpc/core/assets.py:20` `asset_path` | none | — | `tests/repo/test_packaging.py::test_data_assets_resolve`, `::test_manifest_loads_from_package_data`; gap: no test installs the wheel into a fresh venv |
+| binary channel | a failed or interrupted binary install restores the previous one ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:88` `binary_install`, `:555` `binary_recover` | admission → the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything` |
+| binary channel | the receipt is absent, valid, superseded or unsafe; unreadable is never absent ([operations](operations.md#install-channels)) | `lhpc/core/binary_receipt.py:272` `receipt_state` | none (read) | `state/binary/<stack>.json` | `tests/install/test_binary_channel.py::test_malformed_receipt_is_unsafe_never_absent`, `::test_superseded_when_txn_id_differs`, `::test_absent_when_no_receipt` |
+| binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:855` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
+| PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2492` `run_server`, `:311` `_trusted_host`, `:244` `_csrf_ok`, `:295` `_set_headers`; `lhpc/core/config.py:1995` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
+| PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py:690` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
+| PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:24` `invalidates_snapshot`; `lhpc/core/services.py:880` `invalidate_snapshot` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
+| PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3500` `spawn_web_job`, `:4003` `active_jobs`, `:3843` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
+| PKI / web | no shell; typed validation; `@file:` secrets fail closed ([No shell](#safety-model), [Typed validation](#safety-model)) | `lhpc/core/commands.py:75` `expand_argv`, `:130` `build_env`; `lhpc/core/validators.py:434` `validate_param` | none | — | `tests/core/test_structured_exec.py::test_hostile_value_stays_one_token`, `::test_started_process_argv_is_not_a_shell`, `::test_at_file_secret_missing_blocks`; `tests/core/test_validators.py::test_start_rejects_malicious_runparam` |
+| PKI / web | an unverified clock never dates a certificate ([operations](operations.md#clock)) | `lhpc/core/clock.py:27` `verdict`, `:79` `clock_refusal` | the caller's | the original certificate stays active on a refusal | `tests/core/test_clock_gate.py::test_every_mutating_path_refuses_an_unverified_clock`, `::test_a_refused_reissue_leaves_the_original_certificate_active` |
+| firewall | the helper's apply is journalled and verified; a crash is finished forward or rolled back ([firewall](firewall.md)) | `lhpc/core/firewall_helper.py:1246` `op_apply`, `:1156` `op_check`, `:1048` `recover` | root flock `/etc/lhpc/.firewall.lock` | `/etc/lhpc/firewall.{journal,meta,snapshot,transition}.json`, receipt `/run/lhpc-firewall/check.json`; a corrupt journal fails closed | `tests/host/test_firewall.py::test_recovery_finishes_promoted_apply_forward`, `::test_recovery_rolls_back_unpromoted_apply`, `::test_corrupt_journal_fails_closed`, `::test_apply_happy_path_writes_snapshot_and_verified_receipt` |
+| firewall | remote exposure only with this boot's verified receipt, else loopback-only; a stale helper is named before the reboot ([firewall](firewall.md)) | `lhpc/core/service_firewall.py:609` `firewall_gate_activation`, `:781` `firewall_boot_gate`, `:705` `firewall_reapply_notice` | the caller's | the boot receipt above | `tests/host/test_firewall.py::test_gate_refuses_even_already_exposed_when_unverified`, `::test_boot_gate_falls_back_to_loopback_when_unverified`, `::test_boot_gate_fails_closed_when_fallback_cannot_stage`, `::test_reapply_notice_only_for_a_stale_helper` |
+| self-update | an unsafe controller identity blocks apply ([Controller identity](#controller-identity--self-update)) | `lhpc/core/services.py:495` `controller_identity_live` | none (read) | the cached verdict in the self-update envelope | `tests/core/test_controller.py::test_identity_ok`, `::test_missing_checkout_is_not_applicable_not_unsafe`, `::test_self_update_apply_blocked_by_unsafe_identity` |
+| self-update | a running console never has its source changed underneath it ([Controller identity](#controller-identity--self-update)) | `lhpc/core/selfupdate.py:100` `controller_runtime_lock`, `:65` `update_lock` | admission → controller-runtime (exclusive; the console holds it shared) → self-update lock | `state/locks/` | `tests/core/test_controller.py::test_apply_refused_while_web_shared_lock_held`, `::test_web_shared_fails_closed_while_apply_exclusive_held`; `tests/core/test_task_admission.py::test_self_update_apply_contends_typed` |
+| self-update | one-click update and boot restore run only on the canonical, unoverridden units ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py:555` `verify`, `:578` `integration` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit` |
+| self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py:840` `self_update_trigger`, `:942` `_self_update_run_service_locked`, `:1178` `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
+| self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py:600` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
+| radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
+| radio / band ownership | the SPI bus is shared only through the daemon's `spi0.lock` ([Radios](#radios-bands-and-resource-claims)) | the daemon, not LHPC; LHPC models it as the `spi.bus.0` / `spi.bus.0.unlocked` claims | the daemon's flock `state/loraham/spi0.lock` | — | `tests/core/test_resources_conflicts.py::test_cooperative_peers_do_not_conflict`, `::test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi`; gap: the lock itself (daemon side) |
+| radio / band ownership | bounded CONF replies, TX-mode read-back, `/tmp` socket peer check ([Daemon sockets](#safety-model)) | `lhpc/core/daemon_control.py:179` `parse_conf_reply`, `:519` `apply_set`; `lhpc/core/probes/backends.py:767` `_authenticate_tmp_peer` | none | — | `tests/stacks/test_daemon_control.py::test_a_set_without_an_ok_is_never_reported_sent`; `tests/core/test_post_start.py::test_tx_mode_fails_when_readback_mismatches`; `tests/web/test_socket_peercred.py::test_foreign_uid_tmp_peer_is_refused` |
+| radio / band ownership | truthful start and stop outcomes ([Truthful outcomes](#safety-model)) | `lhpc/core/outcomes.py:71` `applied_ok` | the start/stop locks | owned records and markers cleared only on verified stop | `tests/core/test_outcomes.py::test_applied_ok_requires_all_verified`; `tests/core/test_post_start.py::test_start_required_post_start_failure_is_unverified`; `tests/core/test_stop_propagation.py::test_stop_unverified_keeps_markers` |
+| radio / band ownership | no start without a resolvable identity ([Identity](#identity-and-callsigns)) | `lhpc/core/service_params.py:2960` `enforce_identity` | the start locks (re-judged under them) | — | `tests/core/test_identity.py::test_start_refuses_identity_before_boot_hook_and_feed_clear`, `::test_licensed_with_neither_value_is_refused`; `tests/web/test_web.py::test_identity_refusal_sends_the_operator_to_the_settings_row` |
+| lab | a simulated reboot kills only owned groups and runs the real boot restore ([testlab](testlab.md)) | `testlab/lhpc_testlab/ops.py:283` `power` | admission (waited for) | the lab root's boot identity | `testlab/tests/unit/test_testlab.py::test_simulated_reboot_kills_owned_groups_and_runs_boot_restore`; `testlab/tests/acceptance/test_power_network.py::test_simulated_reboot_advances_boot_identity_and_recovers` |
+| lab | the lab's controls never signal a reused PID ([testlab](testlab.md)) | `testlab/lhpc_testlab/supervisor.py:117` `pid_alive` | none | — | `testlab/tests/unit/test_testlab.py::test_nginx_ctl_never_signals_a_reused_pid`, `::test_check_does_not_take_a_reused_pid_for_the_fake_gpsd` |
 
 Open gaps: [backlog.md](backlog.md).
 
```

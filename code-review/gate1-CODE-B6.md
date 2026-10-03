# Gate 1 — CODE review request, batch B6, corrections 1 and 2 (the amended CR3-3b commit only)

Please review ONE commit of batch B6 of loraham-pi-control: 40cfd98 "CR3-3b: recovery removes a clone a crash
interrupted before its journal". It replaces 8121d5a, the version the earlier gate judged, after two correction
rounds. Correction 1 was never reviewed on its own, so judge both corrections in this one packet.
Correction 1 is a test-only change. The test fake `_record_adoption_target` in
`tests/install/test_binary_install.py` now takes the real signature `expected_pin="", clone_rec=None` (no
`**kw`) and records the staging record. A strengthened assertion checks that the real path passes the record
through. It fixes three `TypeError` failures that came from CR3-3b's new `clone_rec` keyword.
Correction 2 answers a gate-1 finding on 19c3527. `_note_staged()` called `rec.rewrite(...)` after
`create_candidate()` with no error containment, against the plan's rule "a record write failure: the clone
proceeds as today". The call is now contained (`OSError`/`PathContainmentError`, or a `False` return → one
stderr line, the install continues), and a new parametrized test covers it.
Judge:
1. Correction 1: does the fake mirror the real signature explicitly, so a future signature change is still
   caught? Do its new assertions prove the record is passed through without being tautological?
2. Correction 2: is the containment complete for the failure modes of `OwnedMarker.rewrite`? Is the chosen
   logging path (stderr; `install.py` has no logger) acceptable? Does the install really continue, leaving the
   record to the recovery rule as designed?
3. Is the new test a faithful red-before/green-after proof? It injects the failure only into the `.staging`
   marker, not into the journal, and counts one line per failed rewrite.
4. Is the author's grep complete? Do the listed test runs cover it?
5. Do the claims hold on the evidence given: the other commits are unchanged (same patch-ids), and the 89
   `tests/install` failures are environmental?

Below: both of the author's correction sections from the report, the delta of each correction, and the full
amended commit (two context lines).

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Answer form

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| 40cfd98 CR3-3b (amended twice) | | |

Final line: GREEN / GREEN WITH NOTES / RED

---

## The author's report sections (`code-review/code-report-B6.md`, "Correction 1" and "Correction 2", full text)

### Correction 1

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

#### The defect
The maintainer's local full suite failed 3 tests in `tests/install/test_binary_install.py` (on this branch's own
head too): `test_a_stale_clone_moves_to_the_manifest_pin_not_an_older_known_working`,
`test_a_missing_clone_is_adopted_at_the_manifest_pin_not_an_older_known_working`,
`test_an_ordinary_pinned_update_still_resolves_known_working`, all with
`TypeError: _record_adoption_target.<locals>._stage() got an unexpected keyword argument 'clone_rec'`. CR3-3b gave
`Installer._stage_candidate` a new keyword `clone_rec=None` and passes it from `_stage_and_activate`; the test fake
`_record_adoption_target` (since 3aed16fe, v0.11.10) replaces `_stage_candidate` with the old signature.

#### Why the session missed it
It ran `tests/install/test_source.py` (where CR3-3b's own tests live) and `tests/repo`, and stopped there. It did
not grep `tests/` and `testlab/` for other callers or fakes of the function whose signature it changed, so the
monkeypatched fake in another module of the same directory was never run.

#### Fix (in 19c3527)
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

#### Grep
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

#### Results (run in this session; venv with `pip install -e .[dev]`)
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

#### Adversarial self-review of the correction
- The name check (`rec.name == _staged_clone_path(...).name`) uses the same helper as the code; on its own it
  would be near-tautological. The payload check against the leaf on disk and the mutation run are what prove the
  pass-through. Kept both.
- `payload` would be `None` if the leaf were missing; the preceding `rec is not None` assert fails first with a
  clear message. Left.
- The fake now reaches `self._staged_clone_path`, a private helper; if it is renamed, this test fails loudly —
  acceptable for a fake of a private method.
- Commit identity and messages re-checked: no attribution lines (`git log --format='%an %cn%n%B'
  e5187f70..HEAD`).

### Correction 2

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

#### The finding
`_note_staged()` called `rec.rewrite(...)` right after `create_candidate()` with no containment. The plan
(PLAN-B6 §2, risk (c)) says "a record write failure: the clone proceeds as today (best-effort, like the clone
log)". Today `OwnedMarker.rewrite` turns its own write errors (`ftruncate`/`write`/`fsync`) into `False`, so
the escape needs an exception outside that inner `try` or a later change to `rewrite`. Either way, the call
site did not enforce the plan's rule. An `OSError` raised there propagated out of `adopt_source`. A
`PathContainmentError` became "managed source parent is unsafe" (`failed`). A `False` return was ignored without
a word.

#### Fix (in 40cfd98)
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

#### Test
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

#### Grep
No signature changed (`_note_staged(self, rec, dest, staging, handle) -> None` as before). `_note_staged`,
`_staged_clone_record` → no hit in `tests/` or `testlab/` before the new test. `open_marker_excl` (wrapped by the
new test) → `tests/core/test_runtime_fs.py` (direct unit tests, no fake), and now the new test.

#### Results (run in this session; venv with `pip install -e .[dev]`, host `zstd` installed)
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

#### Adversarial self-review of the correction
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

## Delta of correction 1 (`git diff -U2 320869f fe8ae50`: reviewed branch head vs. head after correction 1, before the report)

```diff
diff --git a/tests/install/test_binary_install.py b/tests/install/test_binary_install.py
index b7d62d8..a2434e3 100644
--- a/tests/install/test_binary_install.py
+++ b/tests/install/test_binary_install.py
@@ -1571,11 +1571,17 @@ def _older_known_working(svc, commit="7" * 40):
 
 
-def _record_adoption_target(monkeypatch, svc, seen):
+def _record_adoption_target(monkeypatch, svc, seen, recs=None):
     """The REAL adoption runs up to candidate staging, which records the commit it was told to
-    reach (`expected_pin`, after all selector resolution) and stops there."""
+    reach (`expected_pin`, after all selector resolution) and stops there. `recs`, when given,
+    receives `(dest, staging, clone_rec, the record's payload at staging time)`. The signature
+    mirrors the real `_stage_candidate` (no `**kw`), so a change to it still fails here."""
     inst_cls = type(svc._installer())
 
-    def _stage(self, txn, comp, source, dest, staging, spec, local, action, expected_pin=""):
+    def _stage(self, txn, comp, source, dest, staging, spec, local, action, expected_pin="",
+               clone_rec=None):
         seen.append((comp.id, expected_pin))
+        if recs is not None:
+            leaf = self._staged_clone_path(dest, staging)
+            recs.append((dest, staging, clone_rec, leaf.read_text() if leaf.exists() else None))
         action.status, action.detail = "failed", "stopped by the test at staging"
         return None, None
@@ -1610,9 +1616,17 @@ def test_a_missing_clone_is_adopted_at_the_manifest_pin_not_an_older_known_worki
     _older_known_working(svc)
     assert not svc._paths.resolve_source(comp.source.path).exists()
-    seen = []
-    _record_adoption_target(monkeypatch, svc, seen)
+    seen, recs = [], []
+    _record_adoption_target(monkeypatch, svc, seen, recs)
     stub_pipeline(svc, download=lambda e, d: pytest.fail("the staging stop comes first"))
     svc.binary_install("meshcom", apply=True)
     assert seen == [("meshcom-qemu", comp.source.pin_commit)], seen
+    # The staging gets the pre-clone record the real path holds for it (CR3-3b): the
+    # `<journal stem><candidate>.staging` leaf, written before the candidate exists.
+    [(dest, staging, rec, payload)] = recs
+    inst = svc._installer()
+    assert rec is not None and rec.name == inst._staged_clone_path(dest, staging).name
+    assert json.loads(payload) == {"state": "staging", "source_rel": inst._source_rel(dest),
+                                   "candidate_rel": inst._source_rel(staging), "ident": None}
+    assert not inst._staged_clone_path(dest, staging).exists()      # removed when staging ends
 
 
```

## Delta of correction 2 (`git diff -U2 19c3527 40cfd98`)

```diff
diff --git a/lhpc/core/install.py b/lhpc/core/install.py
index 9858629..c5f1111 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -19,4 +19,5 @@ import os
 import re
 import shutil
+import sys
 import time
 from contextlib import contextmanager
@@ -1293,7 +1294,18 @@ class Installer:
     def _note_staged(self, rec, dest: Path, staging: Path, handle) -> None:
         """Record the candidate's [dev, ino] right after its creation, before anything is written
-        into it. (No ctime: the clone itself changes the directory's.) Best-effort."""
-        if rec is not None:
-            rec.rewrite(self._staged_clone_payload(dest, staging, [handle.st_dev, handle.st_ino]))
+        into it. (No ctime: the clone itself changes the directory's.) Best-effort: a failure is
+        one stderr line and the staging goes on; recovery then finds no inode recorded."""
+        if rec is None:
+            return
+        try:
+            ok = rec.rewrite(self._staged_clone_payload(dest, staging,
+                                                        [handle.st_dev, handle.st_ino]))
+        except (OSError, PathContainmentError) as exc:
+            ok, why = False, f" ({exc})"
+        else:
+            why = ""
+        if not ok:
+            sys.stderr.write(f"staging record {rec.name} could not record the candidate{why} — "
+                             "install continues\n")
 
     def _recover_staged_clone(self, jf: Path) -> str:
diff --git a/tests/install/test_source.py b/tests/install/test_source.py
index fb8a9ed..9bfebfb 100644
--- a/tests/install/test_source.py
+++ b/tests/install/test_source.py
@@ -443,4 +443,40 @@ def test_a_staging_record_never_removes_an_unproven_candidate(tmp_path, installe
 
 
+@pytest.mark.parametrize("failure", ["oserror", "containment", "lost"])
+def test_a_failing_staging_record_rewrite_never_stops_the_install(tmp_path, make_repo, installer,
+                                                                  monkeypatch, capsys, failure):
+    # The record is best-effort, like the clone log: when giving it the candidate's [dev, ino]
+    # fails, one line says so and the staging goes on (recovery then sees no recorded inode).
+    from lhpc.core import runtime_fs
+    make_repo(tmp_path / "rt" / "local" / "app")
+    comp = _comp()
+    inst = installer(comp)
+
+    real_open = runtime_fs.open_marker_excl
+
+    calls = []
+
+    def failing_rewrite(text):
+        calls.append(text)
+        if failure == "oserror":
+            raise OSError(errno.EIO, "I/O error")
+        if failure == "containment":
+            raise PathContainmentError("swapped")
+        return False
+
+    def open_marker(paths, path, text, *a, **kw):   # only the staging record fails, not the journal
+        marker = real_open(paths, path, text, *a, **kw)
+        if path.name.endswith(".staging"):
+            marker.rewrite = failing_rewrite
+        return marker
+    monkeypatch.setattr(runtime_fs, "open_marker_excl", open_marker)
+    assert inst.adopt_source(comp, source="dev").status == "done"
+    lines = [ln for ln in capsys.readouterr().err.splitlines() if "staging record" in ln]
+    # One line per failed rewrite (this path creates the candidate twice: reset, then the copy).
+    assert calls and len(lines) == len(calls), (calls, lines)
+    assert all("install continues" in ln for ln in lines), lines
+    assert list(inst.paths.under("state", "source-txn").iterdir()) == []
+
+
 def test_a_staging_record_defers_to_its_journal(tmp_path, installer):
     # A crash after the journal was written leaves both: the journal owns the candidate (with its
```

## The amended commit in full (`git show -U2 40cfd98`)

```diff
commit 40cfd98
Author: makrohard

CR3-3b: recovery removes a clone a crash interrupted before its journal

The candidate is cloned (up to 15 minutes) before the transaction's first
journal exists, so a power cut or kill in that window left a whole tree that
nothing named, and the source rule "without identity evidence nothing is
deleted" kept it on the SD card for good.

Each staging now holds a record, state/source-txn/<journal stem><candidate
name>.staging, written before the candidate exists and given the candidate's
[dev, ino] right after its creation; it is removed when the staging ends. It
is not a .json journal, so nothing that blocks on a pending journal sees a
clone in progress. Recovery handles records before journals: under the
source-path lock (held by the stager for the whole clone, so holding it
proves the stager dead; busy means alive and nothing is touched), a record
whose source has a journal is cleared and the journal keeps the candidate;
otherwise the named candidate is removed on its recorded [dev, ino] (with
none recorded yet, only while it is an empty directory) and the record is
cleared once the candidate is gone. An unprovable candidate and its record
are kept.


diff --git a/CHANGELOG.md b/CHANGELOG.md
index b529ba4..ee0b098 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -4,4 +4,6 @@
   64 KiB, not a symlink; anything else stops the start or build with a named error instead of being followed, read
   whole, or hanging on a pipe.
+- An install or update interrupted by a power cut or crash while it was still downloading no longer leaves the
+  partial download beside the source for good; the next source operation removes it.
 
 ## 0.11.10
diff --git a/docs/architecture.md b/docs/architecture.md
index 6a093c3..4166a94 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -205,5 +205,6 @@ The guarantees the controller gives, each with where it is implemented and prove
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
   and caught at every boundary. `tests/core/test_runtime_fs.py`.
-- **Source transactions.** An update clones a candidate beside the destination, archives the
+- **Source transactions.** An update clones a candidate beside the destination (recorded before
+  the clone starts, so recovery removes a clone a crash interrupted), archives the
   prior source to a transaction-owned `.prev`, activates by atomic no-clobber rename, writes the
   ownership record, then removes the `.prev` — journalled at every step. A failed activation never
diff --git a/lhpc/core/install.py b/lhpc/core/install.py
index 2a49e48..c5f1111 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -19,5 +19,7 @@ import os
 import re
 import shutil
+import sys
 import time
+from contextlib import contextmanager
 from dataclasses import dataclass, field
 from pathlib import Path
@@ -540,5 +542,6 @@ class Installer:
             expected, kw_label = "", ""
         try:
-            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
+            with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn, \
+                    self._staged_clone_record(dest, staging) as clone_rec:
                 # (1) Journal preflight: only an ABSENT journal may begin a new transaction;
                 # any existing journal must be resolved by recovery first (never overwritten).
@@ -550,5 +553,6 @@ class Installer:
                 # (2-3) Exclusive candidate creation + staging, all through the held FD.
                 desc, handle = self._stage_candidate(txn, comp, source, dest, staging, spec,
-                                                     local, action, expected_pin=expected)
+                                                     local, action, expected_pin=expected,
+                                                     clone_rec=clone_rec)
                 if desc is None:
                     return action          # `_stage_candidate` recorded the typed failure
@@ -712,5 +716,5 @@ class Installer:
 
     def _stage_candidate(self, txn, comp, source: str, dest: Path, staging: Path, spec,
-                         local: Path | None, action, expected_pin: str = ""):
+                         local: Path | None, action, expected_pin: str = "", clone_rec=None):
         """Stage the candidate through the held transaction. Returns `(desc, handle)` — a
         description plus the `CandidateHandle` (a retained FD on the candidate dir). On failure
@@ -723,4 +727,5 @@ class Installer:
         remote = self.config.remotes.get(comp.id) or spec.remote
         handle = txn.create_candidate(staging.name)
+        self._note_staged(clone_rec, dest, staging, handle)
         # Adoption is the auto-install's FIRST long phase and git is silent off-TTY — give the
         # clone a tail-able `logs/adopt-<comp>.log` whose first content says what is happening
@@ -758,4 +763,5 @@ class Installer:
             return None, None
         handle = txn.create_candidate(staging.name)
+        self._note_staged(clone_rec, dest, staging, handle)
 
         def _unavailable(why: str) -> str:
@@ -1236,4 +1242,11 @@ class Installer:
             return [f"recovery-required: source-txn dir is symlinked/unsafe ({exc}) — retained"]
         out: list[str] = []
+        # Pre-clone records FIRST: a record whose source still has a journal is cleared before that
+        # journal's recovery can remove the journal (the journal, not the record, owns the candidate).
+        for name, is_link in entries:
+            if not is_link and name.endswith(".staging"):
+                msg = self._recover_staged_clone(d / name)
+                if msg:
+                    out.append(msg)
         for name, is_link in entries:
             if is_link:
@@ -1245,4 +1258,117 @@ class Installer:
         return out
 
+    # -- the pre-clone record --
+    #
+    # A candidate is staged (cloned or copied, up to `_CLONE_TIMEOUT_S`) BEFORE its journal exists,
+    # so a crash in that window left a tree nothing names — and without identity evidence nothing is
+    # deleted. A `<journal stem><candidate name>.staging` leaf names it for that window. It is not
+    # `*.json`, so nothing that blocks on a pending journal ever sees a clone in progress.
+
+    def _staged_clone_path(self, dest: Path, staging: Path) -> Path:
+        return self._txn_dir() / f"{self._journal_path(dest).stem}{staging.name}.staging"
+
+    def _staged_clone_payload(self, dest: Path, staging: Path, ident) -> str:
+        import json
+        return json.dumps({"state": "staging", "source_rel": self._source_rel(dest),
+                           "candidate_rel": self._source_rel(staging), "ident": ident})
+
+    @contextmanager
+    def _staged_clone_record(self, dest: Path, staging: Path):
+        """Hold the record of one staging, written before the candidate exists; yields its
+        `OwnedMarker`, or None when it cannot be written (best-effort, like the clone log: the
+        staging then runs as it did before records existed). Removed on exit — by then a journal
+        owns the candidate, or the candidate is gone or kept as evidence, as before."""
+        from . import runtime_fs
+        try:
+            rec = runtime_fs.open_marker_excl(self.paths, self._staged_clone_path(dest, staging),
+                                              self._staged_clone_payload(dest, staging, None))
+        except (OSError, PathContainmentError):
+            rec = None
+        try:
+            yield rec
+        finally:
+            if rec is not None:
+                rec.remove()
+                rec.close()
+
+    def _note_staged(self, rec, dest: Path, staging: Path, handle) -> None:
+        """Record the candidate's [dev, ino] right after its creation, before anything is written
+        into it. (No ctime: the clone itself changes the directory's.) Best-effort: a failure is
+        one stderr line and the staging goes on; recovery then finds no inode recorded."""
+        if rec is None:
+            return
+        try:
+            ok = rec.rewrite(self._staged_clone_payload(dest, staging,
+                                                        [handle.st_dev, handle.st_ino]))
+        except (OSError, PathContainmentError) as exc:
+            ok, why = False, f" ({exc})"
+        else:
+            why = ""
+        if not ok:
+            sys.stderr.write(f"staging record {rec.name} could not record the candidate{why} — "
+                             "install continues\n")
+
+    def _recover_staged_clone(self, jf: Path) -> str:
+        """Resolve ONE pre-clone record ("" = nothing to report). Its writer held the source-path
+        lock for the whole staging, and a flock dies with its process, so holding that lock here
+        proves the staging dead; a busy lock leaves everything alone. A source with a journal: the
+        journal owns the candidate, so only the record is cleared. Otherwise the named candidate is
+        removed on its recorded [dev, ino] — with none recorded yet, only while it is still an empty
+        directory — and the record is cleared once the candidate is gone. A candidate that cannot be
+        proven is kept, and so is its record."""
+        import json
+
+        from . import reslock, runtime_fs, source_fs
+        try:
+            marker = runtime_fs.open_existing_marker(self.paths, jf)
+        except (OSError, PathContainmentError):
+            return f"staging record {jf.name} unreadable/unsafe (retained)"
+        try:
+            try:
+                j = json.loads(marker.read())
+                dest = self._resolve_rel(j["source_rel"])
+                staging = self._resolve_rel(j["candidate_rel"])
+                ident = j["ident"]
+                if j.get("state") != "staging" or not (ident is None or (
+                        isinstance(ident, list) and len(ident) == 2 and all(
+                            isinstance(x, int) and not isinstance(x, bool) for x in ident))):
+                    raise ValueError("bad state/ident")
+            except (OSError, ValueError, KeyError, TypeError):
+                return f"staging record {jf.name} invalid (retained)"
+            if (str(dest) not in self._managed_source_dests()
+                    or not self._is_candidate_name(dest, staging)
+                    or jf.name != self._staged_clone_path(dest, staging).name):
+                return f"staging record {jf.name} names no managed candidate (retained)"
+            try:
+                with reslock.operation_lock(self.paths,
+                                            self._source_lock_key(self._source_rel(dest)),
+                                            "recover", dest.name):
+                    if source_fs.leaf_kind(self.paths, self._journal_path(dest)) != "absent":
+                        kind = "staging record cleared: its journal owns the candidate"
+                    else:
+                        with source_fs.ManagedSourceTransaction(self.paths, dest.parent) as txn:
+                            if txn.leaf_kind(staging.name) != "absent":
+                                if ident is not None:
+                                    ok, _why = source_fs.remove_bound(txn.fd, staging.name, ident)
+                                else:
+                                    try:
+                                        os.rmdir(staging.name, dir_fd=txn.fd)
+                                        ok = True
+                                    except OSError:
+                                        ok = False
+                                if not ok:
+                                    return (f"interrupted clone {staging.name} kept: not provably "
+                                            "the one its record names (record retained)")
+                                txn.fsync()
+                        kind = "removed an interrupted clone"
+                    return (f"recovered {dest.name}: {kind}" if marker.remove()
+                            else f"staging record {jf.name} could not be removed (retained)")
+            except reslock.ResourceBusy:
+                return ""                               # the staging is alive: left alone
+            except (OSError, PathContainmentError) as exc:
+                return f"staging record {jf.name} not resolvable now ({exc}) (retained)"
+        finally:
+            marker.close()
+
     def _recover_one(self, jf: Path) -> str:
         """Resolve ONE journal under an OWNED marker handle: open the existing regular journal
diff --git a/tests/install/test_binary_install.py b/tests/install/test_binary_install.py
index b7d62d8..a2434e3 100644
--- a/tests/install/test_binary_install.py
+++ b/tests/install/test_binary_install.py
@@ -1571,11 +1571,17 @@ def _older_known_working(svc, commit="7" * 40):
 
 
-def _record_adoption_target(monkeypatch, svc, seen):
+def _record_adoption_target(monkeypatch, svc, seen, recs=None):
     """The REAL adoption runs up to candidate staging, which records the commit it was told to
-    reach (`expected_pin`, after all selector resolution) and stops there."""
+    reach (`expected_pin`, after all selector resolution) and stops there. `recs`, when given,
+    receives `(dest, staging, clone_rec, the record's payload at staging time)`. The signature
+    mirrors the real `_stage_candidate` (no `**kw`), so a change to it still fails here."""
     inst_cls = type(svc._installer())
 
-    def _stage(self, txn, comp, source, dest, staging, spec, local, action, expected_pin=""):
+    def _stage(self, txn, comp, source, dest, staging, spec, local, action, expected_pin="",
+               clone_rec=None):
         seen.append((comp.id, expected_pin))
+        if recs is not None:
+            leaf = self._staged_clone_path(dest, staging)
+            recs.append((dest, staging, clone_rec, leaf.read_text() if leaf.exists() else None))
         action.status, action.detail = "failed", "stopped by the test at staging"
         return None, None
@@ -1610,9 +1616,17 @@ def test_a_missing_clone_is_adopted_at_the_manifest_pin_not_an_older_known_worki
     _older_known_working(svc)
     assert not svc._paths.resolve_source(comp.source.path).exists()
-    seen = []
-    _record_adoption_target(monkeypatch, svc, seen)
+    seen, recs = [], []
+    _record_adoption_target(monkeypatch, svc, seen, recs)
     stub_pipeline(svc, download=lambda e, d: pytest.fail("the staging stop comes first"))
     svc.binary_install("meshcom", apply=True)
     assert seen == [("meshcom-qemu", comp.source.pin_commit)], seen
+    # The staging gets the pre-clone record the real path holds for it (CR3-3b): the
+    # `<journal stem><candidate>.staging` leaf, written before the candidate exists.
+    [(dest, staging, rec, payload)] = recs
+    inst = svc._installer()
+    assert rec is not None and rec.name == inst._staged_clone_path(dest, staging).name
+    assert json.loads(payload) == {"state": "staging", "source_rel": inst._source_rel(dest),
+                                   "candidate_rel": inst._source_rel(staging), "ident": None}
+    assert not inst._staged_clone_path(dest, staging).exists()      # removed when staging ends
 
 
diff --git a/tests/install/test_source.py b/tests/install/test_source.py
index 348456c..9bfebfb 100644
--- a/tests/install/test_source.py
+++ b/tests/install/test_source.py
@@ -372,4 +372,128 @@ def test_parent_swap_after_fd_cannot_redirect_clone_outside(tmp_path, git, make_
 
 
+def test_recovery_removes_a_clone_killed_before_its_journal(tmp_path, make_repo, installer, monkeypatch):
+    # Power lost while the candidate was still being staged (a clone may take 15 minutes), before
+    # any journal exists: nothing named the partial tree, so it stayed beside the source for good.
+    import signal
+    make_repo(tmp_path / "rt" / "local" / "app")
+    comp = _comp()
+    inst = installer(comp)
+    real_copy = Installer._copy_into_candidate
+
+    def copy_then_die(local, cand):           # stubs the copy: the one seam inside the staging
+        real_copy(local, cand)
+        os.kill(os.getpid(), signal.SIGKILL)
+    monkeypatch.setattr(Installer, "_copy_into_candidate", staticmethod(copy_then_die))
+    pid = os.fork()
+    if pid == 0:                              # the box: dies mid-staging, no cleanup runs
+        try:
+            inst.adopt_source(comp, source="dev")
+        finally:
+            os._exit(1)
+    _, status = os.waitpid(pid, 0)
+    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
+    staged = list(inst.paths.under("src").glob(".app.candidate-*"))
+    assert len(staged) == 1 and (staged[0] / "file.txt").is_file()
+    assert not inst._pending_journals()
+    monkeypatch.undo()
+    msgs = inst.recover_source_activations()
+    assert not staged[0].exists()
+    assert any("removed an interrupted clone" in m for m in msgs), msgs
+    assert list(inst.paths.under("state", "source-txn").iterdir()) == []
+    assert inst.adopt_source(comp, source="dev").status == "done"
+
+
+def _staging_record(inst, staging, ident):
+    dest = inst.paths.under("src", "app")
+    rec = inst._staged_clone_path(dest, staging)
+    rec.parent.mkdir(parents=True, exist_ok=True)
+    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
+    rec.write_text(json.dumps({"state": "staging", "source_rel": rel(dest),
+                               "candidate_rel": rel(staging), "ident": ident}))
+    return rec
+
+
+def test_a_staging_record_of_a_live_staging_is_left_alone(tmp_path, installer):
+    # The staging process holds the source lock for the whole clone: while it is held, the clone
+    # is alive and neither it nor its record is touched. Once the lock is free, it is removed.
+    from lhpc.core import reslock
+    inst = installer(search_root=tmp_path / "rt")
+    staging = inst.paths.under("src", ".app.candidate-1-2")
+    staging.mkdir(parents=True); (staging / "part").write_text("cloning")
+    rec = _staging_record(inst, staging, _ident_of(staging, ctime=False))
+    with reslock.operation_lock(inst.paths, inst._source_lock_key("src/app"), "update", "x"):
+        inst.recover_source_activations()
+    assert staging.exists() and rec.exists()
+    inst.recover_source_activations()
+    assert not staging.exists() and not rec.exists()
+
+
+@pytest.mark.parametrize("ident", ["other-inode", "unrecorded"])
+def test_a_staging_record_never_removes_an_unproven_candidate(tmp_path, installer, ident):
+    # A leaf that is not the recorded inode, or a non-empty one before any inode was recorded,
+    # is kept, and so is its record.
+    inst = installer(search_root=tmp_path / "rt")
+    staging = inst.paths.under("src", ".app.candidate-1-2")
+    staging.mkdir(parents=True); (staging / "part").write_text("keep")
+    st = os.stat(staging)
+    rec = _staging_record(inst, staging, [st.st_dev, st.st_ino + 1] if ident == "other-inode"
+                          else None)
+    inst.recover_source_activations()
+    assert (staging / "part").read_text() == "keep" and rec.exists()
+
+
+@pytest.mark.parametrize("failure", ["oserror", "containment", "lost"])
+def test_a_failing_staging_record_rewrite_never_stops_the_install(tmp_path, make_repo, installer,
+                                                                  monkeypatch, capsys, failure):
+    # The record is best-effort, like the clone log: when giving it the candidate's [dev, ino]
+    # fails, one line says so and the staging goes on (recovery then sees no recorded inode).
+    from lhpc.core import runtime_fs
+    make_repo(tmp_path / "rt" / "local" / "app")
+    comp = _comp()
+    inst = installer(comp)
+
+    real_open = runtime_fs.open_marker_excl
+
+    calls = []
+
+    def failing_rewrite(text):
+        calls.append(text)
+        if failure == "oserror":
+            raise OSError(errno.EIO, "I/O error")
+        if failure == "containment":
+            raise PathContainmentError("swapped")
+        return False
+
+    def open_marker(paths, path, text, *a, **kw):   # only the staging record fails, not the journal
+        marker = real_open(paths, path, text, *a, **kw)
+        if path.name.endswith(".staging"):
+            marker.rewrite = failing_rewrite
+        return marker
+    monkeypatch.setattr(runtime_fs, "open_marker_excl", open_marker)
+    assert inst.adopt_source(comp, source="dev").status == "done"
+    lines = [ln for ln in capsys.readouterr().err.splitlines() if "staging record" in ln]
+    # One line per failed rewrite (this path creates the candidate twice: reset, then the copy).
+    assert calls and len(lines) == len(calls), (calls, lines)
+    assert all("install continues" in ln for ln in lines), lines
+    assert list(inst.paths.under("state", "source-txn").iterdir()) == []
+
+
+def test_a_staging_record_defers_to_its_journal(tmp_path, installer):
+    # A crash after the journal was written leaves both: the journal owns the candidate (with its
+    # full identity proof), so the record is cleared and never removes the candidate itself.
+    inst = installer(search_root=tmp_path / "rt")
+    src = inst.paths.under("src"); src.mkdir(parents=True)
+    dest = src / "app"; dest.mkdir(); (dest / "marker").write_text("LIVE")
+    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "part").write_text("NEW")
+    rec = _staging_record(inst, staging, _ident_of(staging, ctime=False))
+    _journal(inst, dest, src / ".app.prev", staging, "planned")
+    j = json.loads(inst._journal_path(dest).read_text())
+    j["idents"]["candidate"][2] -= 1                 # not provable by the journal: must stay
+    inst._journal_path(dest).write_text(json.dumps(j))
+    inst.recover_source_activations()
+    assert not rec.exists()
+    assert (staging / "part").read_text() == "NEW" and (dest / "marker").read_text() == "LIVE"
+
+
 def test_transaction_renames_survive_parent_swap(tmp_path):
     # A parent-path swap AFTER opening the transaction cannot redirect later renames —
```

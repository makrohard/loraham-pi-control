# Gate 1 — code review request, batch Q2 (round 5)

Please judge the plan and the three code commits below for batch Q2, round 5. The batch adds one fail-closed existence probe (`probe_exists`, plus `probe_stat` where the kind of the path decides) and makes the listed safety decisions use it, so that a disk or permission error (EIO, EACCES) is never read as "the file is absent" or "not a directory". Round 4 was RED on two text items, its code commits OK; the section right below says what changed in round 5. The round 3 → round 4 section after it (the rebase onto `integration/0.12.0` and its two conflict resolutions) and the older sections are history with their own shas. Check each commit for: correctness; whether any safety path in the diff still reads an OSError, or a `.git` that does not resolve, as absence; whether behaviour changes anywhere beyond what the plan, the CHANGELOG and the docs state; whether the tests are red before the change and green after it; whether the plan, the CHANGELOG and the docs match the diff exactly.

Answer form, one row per commit, then a final line:

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| d390195 plan | | |
| b9f7c8b | | |
| a2ce1af | | |
| ffa36ec | | |

Final line: GREEN / GREEN WITH NOTES / RED

This file is your whole input: you have no repository access; use no connector, tool or web lookup.


## Round 4 RED (two text items) → round 5

Round 4 findings, both text: (1) the plan's file summary said `docs/architecture.md` "(one
sentence)", while the diff adds three; (2) the architecture sentence added by the firewall commit
cited `tests/core/test_existence_probe.py` for the firewall helper's inline rule, whose regression
test is `tests/host/test_firewall.py`.

Fixes, as fixups into their own commits: the plan's summary names the three sentences; the docs
clause now cites `tests/core/test_existence_probe.py` for the controller's sites and
`tests/host/test_firewall.py` for the firewall helper. Then the branch is rebased onto
`integration/0.12.0` c1d8bbd, which adds the separate Q1 fix (the locked transaction body now stops
on a BLOCKED journal) that round 4's config resolution assumed. The rebase had no conflict. The
helper and controller commits are patch-identical to round 4; the firewall commit's `lhpc/` and
`tests/` change is too, only its docs clause differs.

### The plan's change (round 4 `4026fc4` → round 5 `d390195`)

```diff
diff --git a/plans/PLAN-Q2.md b/plans/PLAN-Q2.md
index d6423e9..ab69855 100644
--- a/plans/PLAN-Q2.md
+++ b/plans/PLAN-Q2.md
@@ -1,9 +1,12 @@
 # PLAN-Q2 — `probe_exists()`: one fail-closed existence probe for every safety path
 
-Base: `integration/0.12.0` (ac1eadd; rounds 1–3 were based on 042716f). Files: `lhpc/core/runtime_fs.py` (helper), `install.py`,
+Base: `integration/0.12.0` (c1d8bbd from round 5, ac1eadd in round 4; rounds 1–3 042716f). Files: `lhpc/core/runtime_fs.py` (helper), `install.py`,
 `service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (the journal
 probe and its two outcomes, in Q1's result type), `firewall_helper.py`, their tests, CHANGELOG,
-`docs/architecture.md` (one sentence).
+`docs/architecture.md` (the runtime-filesystem guarantee gains three sentences: a `.git`
+symlink that does not resolve is present, never clean and never handed to git; the presence
+decisions that use `probe_exists`/`probe_stat`, by site; and only ENOENT/ENOTDIR read as absent,
+with its tests, the firewall helper's inline rule and its test included).
 
 ## The helper (runtime_fs.py, beside the other no-follow leaf helpers)
 
```

### The docs change in the firewall commit (round 4 `25a58c9` → round 5 `ffa36ec`)

```diff
diff --git a/docs/architecture.md b/docs/architecture.md
index 01cb624..1850a6e 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -213,8 +213,9 @@ The guarantees the controller gives, each with where it is implemented and prove
   retire's check of each receipt file and owned folder, the binary->source switch's listing of a
   tree, a checkout's dirty/carry inventory (`.git`), the boot-restore markers and journal, and
   the config journal recovery. In each, only
-  ENOENT/ENOTDIR read as absent, any other error refuses or keeps; the stdlib-only firewall
-  helper applies the same rule inline (`tests/core/test_existence_probe.py`).
+  ENOENT/ENOTDIR read as absent, any other error refuses or keeps
+  (`tests/core/test_existence_probe.py`); the stdlib-only firewall helper applies the same rule
+  inline to its journal (`tests/host/test_firewall.py`).
 - **Source transactions.** An update clones a candidate beside the destination (recorded before
   the clone starts, so recovery removes a clone a crash interrupted), archives the
   prior source to a transaction-owned `.prev`, activates by atomic no-clobber rename, writes the
```

### Numbers, measured

| figure | command | output |
|---|---|---|
| `lhpc/` size | `git diff --numstat c1d8bbd ffa36ec -- lhpc` summed | +115 −25 (net +90) |
| line counts | `git show <rev>:lhpc/core/<file>.py \| wc -l`, c1d8bbd → ffa36ec | runtime_fs 958→992, service_binary_ops 986→998, install 2317→2343, config 2161→2165, boot_restore 338→340, service_boot_restore 645→651, firewall_helper 1479→1485 |
| helper commit | `git show <c> \| git patch-id --stable`, `46214db` / `b9f7c8b` | `eb805740a5f8` / `eb805740a5f8` |
| controller commit | same, `8b35345` / `a2ce1af` | `34242f6fa248` / `34242f6fa248` |
| firewall commit, code | `git show <c> -- lhpc tests \| git patch-id --stable`, `25a58c9` / `ffa36ec` | `43c566118ea6` / `43c566118ea6` |
| what differs under the code, round 4 → 5 | `git diff --stat 25a58c9 ffa36ec -- lhpc tests` | 2 files changed, 27 insertions(+), 1 deletion(-) (the Q1 fix: `config.py`, `tests/core/test_config.py`) |
| ordered run | `PYTHONPATH=<tree> pytest -n 12 --dist loadfile tests/core tests/install tests/host/test_firewall.py tests/repo` (Python 3.14 venv) | `3937 passed, 1 skipped` |
| lint | `ruff check lhpc testlab`; `ruff check tests --select F,E9` | All checks passed (both) |

The ordered run sets `PYTHONPATH` to the tree: without it, the three
`test_build_launcher_runtime.py::test_rendered_launcher_nonfinite_spec_timeout_fails_safe` cases run
their launcher subprocess against an older installed copy of the package and fail (round 4's
"3 failed" line); with it, they pass.

## Round 3 → round 4: rebased onto integration/0.12.0 ac1eadd; two conflict resolutions

Round 3 (`2e934ea`, `65540b0`, `66810ed`, `a8db6d9`) was based on 042716f. Q1 (typed config-recovery
result) and Q3 (`best_effort`) were merged into `integration/0.12.0` since, and both touch lines that
round 3 changes. Round 4 rebases the three code commits onto ac1eadd. What differs from round 3:

- `46214db` (helper): patch-identical to `65540b0`.
- `25a58c9` (firewall): its `lhpc/` and `tests/` change is patch-identical to `a8db6d9`; its CHANGELOG
  line is unchanged and only lands under the 0.12.0 heading the base already has.
- `8b35345` (controller): two resolutions, below; the CHANGELOG entry likewise lands under the base's
  0.12.0 heading (the round-3 commit added that heading itself).
- `4026fc4` (plan): the base line and three lines made stale by Q1 are updated (diff below). The plan's line numbers
  stay those of 042716f.

**Resolution 1, `config.py` `recover_config_transaction`.** Q1 replaced the `str | None` result with
`tuple[ConfigRecovery, str]`. Round 3 returned `None` ("no journal") for an absent journal and `""`
(recovery required) for one that cannot be examined. In Q1's type: absent → `(UNNECESSARY, "")`;
cannot be examined → `(BLOCKED, "")`, the outcome Q1 already gives a journal that cannot be read or
finished. `config_lock` refuses on BLOCKED; so does the identity save in `service_params.py`.
The locked transaction body (`_apply_config_transaction_locked`) still compares the result with
`""` on the base, a Q1 leftover that never matches the tuple. It is fixed separately ("Q1: a blocked
config journal stops the locked transaction body", now merged as c1d8bbd, the base of round 5).

**Resolution 2, `install.py` imports.** Q3 removed `import sys`; round 3 added `import stat` beside
it. Only `import stat` is kept.

**The test.** `_config_journal_blocks` in `tests/core/test_existence_probe.py` now asserts
`(cfgmod.ConfigRecovery.BLOCKED, "")`. Red-before: with ac1eadd's `lhpc/core/config.py` checked out,
`test_an_unexaminable_path_is_never_absent[config-journal-EIO]` and `[config-journal-EACCES]` fail
(`2 failed`: the probe-less code returns UNNECESSARY); with `8b35345`'s: `2 passed`.

### Range-diff of the controller commit (round 3 `66810ed` → round 4 `8b35345`)

```diff
1:  66810edc ! 1:  8b35345a Q2: the controller's safety paths never read an unexaminable path as absent
    @@ Commit message
     
      ## CHANGELOG.md ##
     @@
    - # Changelog
    - 
    -+## 0.12.0
    -+
    +   print the details of an unexpected error, or cannot undo itself after Ctrl-C, now says so in one line instead of
    +   staying silent (after Ctrl-C the next command finishes the undo, as before). In these lines an error message that
    +   spans several lines is joined into one.
     +- A disk or permission error on a file LHPC must not lose no longer reads as "the file is gone": retiring a
     +  binary install keeps its record while a file or folder cannot be checked, a switch to the source channel does
     +  not take over a folder it cannot fully read, an update or uninstall treats a checkout whose `.git` cannot be
    @@ CHANGELOG.md
     +  present and the tree is not reported clean: every local-change check (update, uninstall, binary install, switch
     +  to the source channel, auto-install update) reads it as changed. Before, the `.git` was ignored and the tree
     +  read as clean.
    -+
    - ## 0.11.12
      
      - Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
    +   before it ships: an update that would stall or run into a time limit on a slow box turns the release check
     
      ## docs/architecture.md ##
     @@ docs/architecture.md: The guarantees the controller gives, each with where it is implemented and prove
    @@ lhpc/core/boot_restore.py: def load_journal(paths: Paths) -> tuple[dict | None,
              return None, f"unsafe:unreadable ({exc})"
     
      ## lhpc/core/config.py ##
    -@@ lhpc/core/config.py: def recover_config_transaction(paths: Paths) -> str | None:
    +@@ lhpc/core/config.py: def recover_config_transaction(paths: Paths) -> tuple[ConfigRecovery, str]:
          # Presence is decided WITHOUT following the leaf: ANY directory entry at the journal
          # path -- a regular file, OR a symlink (including a dangling or escaping one) -- is a
          # pending journal that must be recovered/blocked. `Path.exists()` follows the link and
     -    # would report a dangling-symlink journal as absent; `os.path.lexists` does not.
     -    if not os.path.lexists(jp):
    --        return None
    -+    # would report a dangling-symlink journal as absent; `probe_exists` does not, nor EIO.
    ++    # would report a dangling-symlink journal as absent; `probe_exists` does not, nor EIO. An
    ++    # unexaminable journal is never absent: it blocks, like any journal that cannot be finished.
     +    state = runtime_fs.probe_exists(jp)[0]
    ++    if state == "absent":
    +         return ConfigRecovery.UNNECESSARY, ""
     +    if state != "present":
    -+        return None if state == "absent" else ""
    ++        return ConfigRecovery.BLOCKED, ""
          try:
              journal = runtime_fs.loads_json(runtime_fs.read_text(paths, jp))   # no-follow read
          except (OSError, ValueError, PathContainmentError):
    @@ lhpc/core/install.py: import errno
      import re
      import shutil
     +import stat
    - import sys
      import time
      from contextlib import contextmanager
    + from dataclasses import dataclass, field
     @@ lhpc/core/install.py: class Plan:
              return all(a.status != "failed" for a in self.actions)
      
    @@ tests/core/test_existence_probe.py: def test_any_other_error_is_unknown_with_the
     +    jp.parent.mkdir(parents=True, exist_ok=True)
     +    jp.write_text("{}")
     +    fail(jp)
    -+    assert cfgmod.recover_config_transaction(paths) == ""
    ++    assert cfgmod.recover_config_transaction(paths) == (cfgmod.ConfigRecovery.BLOCKED, "")
     +    restore()
     +    assert jp.read_text() == "{}"
     +
```

### The plan's change (round 3 → round 4)

The base line was renamed to the integration branch and three lines made stale by Q1 were
updated; the plan as it stands in round 5 is quoted in full below.

### Numbers, measured

| figure | command | output |
|---|---|---|
| `lhpc/` size | `git diff --numstat ac1eadd 25a58c9 -- lhpc` summed | +115 −25 (net +90) |
| line counts | `git show <rev>:lhpc/core/<file>.py \| wc -l`, ac1eadd → 25a58c9 | runtime_fs 958→992, service_binary_ops 986→998, install 2317→2343, config 2161→2165, boot_restore 338→340, service_boot_restore 645→651, firewall_helper 1479→1485 |
| helper commit | `git show <c> \| git patch-id --stable`, `65540b0` / `46214db` | `eb805740a5f8` / `eb805740a5f8` |
| controller commit | same, `66810ed` / `8b35345` | `87bcffc77f34` / `34242f6fa248` (the resolutions) |
| firewall commit, code | `git show <c> -- lhpc tests \| git patch-id --stable`, `a8db6d9` / `25a58c9` | `43c566118ea6` / `43c566118ea6` |
| red-before of the re-pointed test | `pytest tests/core/test_existence_probe.py -k config` with ac1eadd's `config.py` / HEAD's | `2 failed` / `2 passed` |
| ordered run | `pytest -n 12 --dist loadfile tests/core tests/install tests/host/test_firewall.py tests/repo` (Python 3.14 venv) | `3 failed, 3931 passed, 1 skipped` |
| those 3 on the base | `pytest tests/core/test_build_launcher_runtime.py` on ac1eadd | `3 failed, 48 passed`: the same three `test_rendered_launcher_nonfinite_spec_timeout_fails_safe[inf,-inf,nan]`, not touched by Q2 |
| lint | `ruff check lhpc testlab`; `ruff check tests --select F,E9` | All checks passed (both) |

## Round 2 RED → round 3

Round 2 finding (plan `97e5057`, code `3f5b102`): the plan claimed "no behaviour change on healthy
disks", but `probe_exists` does not follow symlinks, so a DANGLING `.git` symlink became "present"
where the old `Path.exists()` said absent; `dirty_report`/`extra_files` then went to git and reported
dirty / `None` instead of clean / `()`, and no test pinned it.

Verified against the repository first, then decided. In short (details, the reproduction table on
both trees, the call-site list and the red-before evidence are in "Correction 2" at the top of the
report below):

- Reproduced: base `042716f` — dangling or looping `.git` → `DirtyReport()` / `()`, no git call.
  Round 2 `62083e7` — handed to git: dirty / `None` when git fails, but CLEAN / `()` when git answers
  for a clean enclosing repository, which real git does (git 2.43.0: a dangling `sub/.git` inside a
  repository `outer/` → `git -C sub status` lists `outer`'s view, rc 0).
- Decision: fail closed and never ask git. A `.git` that is there but leads nowhere does not prove the
  tree clean (the batch's rule: not proven absent → not absent), and git's answer would depend on the
  enclosing directory. New `install._git_marker_anomaly`: lstat via `probe_stat`; a symlink is
  followed (`probe_stat(follow=True)`): absent → `".git is a dangling symlink"`, unknown (ELOOP, EIO,
  EACCES) → `".git is a symlink that cannot be followed (why)"`. `dirty_report` returns
  `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))`, `extra_files` returns `None`, no git
  call. No `.git`, a `.git` directory, a gitfile and a resolving `.git` symlink behave as before.
- Text: the plan's risk section now states the behaviour change exactly and names every call site
  whose result changes (12 call lines in 9 functions); a new CHANGELOG bullet and a docs sentence say
  "treated as present and the tree is not reported clean; before, it was ignored". No "no behaviour
  change" sentence is left in the plan, CHANGELOG or docs.
- Tests: `test_a_git_symlink_that_does_not_resolve_is_never_clean[dangling,loop]` (exact values, no
  git call) — red on BOTH the base and round 2 (`2 failed, 1 passed`), green now;
  `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading` passes on all three trees.
- Patch-ids: `65540b0` = `d788bbe` and `a8db6d9` = `7bcccee` (`eb805740a5f8`, `839c1f1a76f8`); the
  plan and `66810ed` changed. Tree before and after the autosquash rebase identical.

### The round-3 change itself (round-2 head 62083e7 → round-3 code, without code-review/)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 5ba074d..1c8ccbf 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -13,6 +13,10 @@ reads *Update required* after the update; run `sudo bash <runtime root>/config/f
   not take over a folder it cannot fully read, an update or uninstall treats a checkout whose `.git` cannot be
   checked as changed, and boot restore and the config journal treat an unreadable record as one that needs
   attention instead of an absent one.
+- A source checkout whose `.git` is a symlink that does not resolve (dangling, or a loop) is now treated as
+  present and the tree is not reported clean: every local-change check (update, uninstall, binary install, switch
+  to the source channel, auto-install update) reads it as changed. Before, the `.git` was ignored and the tree
+  read as clean.
 
 ## 0.11.12
 
diff --git a/docs/architecture.md b/docs/architecture.md
index be4bdb6..075951a 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -204,7 +204,10 @@ The guarantees the controller gives, each with where it is implemented and prove
   `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write; atomic
   writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
-  and caught at every boundary. `tests/core/test_runtime_fs.py`. These presence decisions use
+  and caught at every boundary. `tests/core/test_runtime_fs.py`. A checkout's `.git` that is a
+  symlink which does not resolve (dangling, a loop) is treated as present and the tree is not
+  reported clean (before 0.12.0 it was ignored); it is never handed to git, which would walk up
+  to an enclosing repository. These presence decisions use
   `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory, symlink) decides: binary
   retire's check of each receipt file and owned folder, the binary->source switch's listing of a
   tree, a checkout's dirty/carry inventory (`.git`), the boot-restore markers and journal, and
diff --git a/lhpc/core/install.py b/lhpc/core/install.py
index 62805fa..41ee5a5 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -18,6 +18,7 @@ import errno
 import os
 import re
 import shutil
+import stat
 import sys
 import time
 from contextlib import contextmanager
@@ -160,6 +161,25 @@ class Plan:
         return all(a.status != "failed" for a in self.actions)
 
 
+def _git_marker_anomaly(dest: Path) -> str | None:
+    """The `.git` of a checkout for the dirty/carry inventory: None = absent (not a checkout),
+    "" = present and reachable, else the anomaly. A `.git` that cannot be examined, or a symlink
+    that does not resolve (dangling, a loop), is never "absent" and never handed to git: git
+    would walk up to an enclosing repository and report on that one."""
+    from . import runtime_fs
+    state, why, st = runtime_fs.probe_stat(dest / ".git")
+    if state == "absent":
+        return None
+    if state == "present" and stat.S_ISLNK(st.st_mode):
+        state, why, _st = runtime_fs.probe_stat(dest / ".git", follow=True)
+        if state == "absent":
+            return ".git is a dangling symlink"
+        if state == "unknown":
+            return f".git is a symlink that cannot be followed ({why})"
+        return ""
+    return f"cannot examine .git ({why})" if state == "unknown" else ""
+
+
 class Installer:
     def __init__(self, paths: Paths, stacks: tuple[Stack, ...], config: Config,
                  system: System) -> None:
@@ -886,12 +906,11 @@ class Installer:
         not a git checkout reports clean here (ownership verification handles unknown trees).
         A FAILED git status reports the failure as a tracked entry — fail toward dirty, never
         silently clean."""
-        from . import runtime_fs
-        state, why = runtime_fs.probe_exists(dest / ".git")
-        if state == "unknown":
-            return DirtyReport(tracked=(f"(cannot examine .git ({why}) — treating as dirty)",))
-        if state == "absent":
+        anomaly = _git_marker_anomaly(dest)
+        if anomaly is None:
             return DirtyReport()
+        if anomaly:
+            return DirtyReport(tracked=(f"({anomaly} — treating as dirty)",))
         # NUL-SAFE, ENTRY-EXACT status: `-z` terminates every path with NUL (no quoting, so
         # newline/quote-containing names parse exactly), and `--untracked-files=all`
         # enumerates every INDIVIDUAL untracked file — git never collapses a directory, so
@@ -950,10 +969,11 @@ class Installer:
 
         Regenerable artifacts are filtered by the SAME predicate `dirty_report` uses, so
         `build/`, `.run/` and a component's declared `bin` stay disposable in both."""
-        from . import runtime_fs
-        state, _why = runtime_fs.probe_exists(dest / ".git")
-        if state != "present":
-            return () if state == "absent" else None
+        anomaly = _git_marker_anomaly(dest)
+        if anomaly is None:
+            return ()
+        if anomaly:
+            return None
         r = self.system.runner.run(["git", "-C", str(dest), "ls-files", "-z", "--others"], 10.0)
         if r.returncode != 0:
             return None
diff --git a/plans/PLAN-Q2.md b/plans/PLAN-Q2.md
index 1b6189a..aa8174d 100644
--- a/plans/PLAN-Q2.md
+++ b/plans/PLAN-Q2.md
@@ -26,8 +26,8 @@ REPLACE (safety: an OSError read as absence deletes, overwrites or proceeds):
 
 | site today | decision on a false "absent" | with `unknown` |
 |---|---|---|
-| install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …") |
-| install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses) |
+| install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …"); a `.git` symlink that does not resolve: dirty (named, see below) |
+| install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses); same for an unresolvable `.git` symlink |
 | service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | `probe_stat(follow=True)`: unknown raises → `_artifact_only_dir` False; is-dir from its `st` |
 | service_binary_ops.py:930 retire `still_there` exists | receipt dropped while files remain (unowned) | still present → INCOMPLETE, receipt kept |
 | service_binary_ops.py:946 owned dir isdir + islink | dir skipped, receipt dropped (unowned venv) | `probe_stat` (lstat: link?) then `probe_stat(follow=True)` (dir?): unknown in either → INCOMPLETE, receipt kept |
@@ -76,14 +76,48 @@ public seam (`dirty_report`, `extra_files`, `binary_retire(force=True)`,
 test_boot_restore.py already does for private views) and asserts the refusal/keep state AND that
 the file is still there. The two kind decisions (`_rel_files_under`, the owned-dir retirement)
 get one more test each with an injector that fails `os.stat` ONLY (lstat answers), so the
-`isdir` after a present probe is caught too. Firewall helper: one case in
+`isdir` after a present probe is caught too. The dangling-`.git` change: `test_a_git_symlink_that_does_not_resolve_is_never_clean`
+(dangling, loop; exact `DirtyReport` and `None` asserted, no git call, against a fake git that
+answers like a clean enclosing repository) and `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading`
+(no `.git` → `DirtyReport()` / `()` with no git call; a resolving `.git` symlink → git consulted). Firewall helper: one case in
 `tests/host/test_firewall.py` (`recover()` is False and the journal stays). Red-before: run the
 new tests against commit 2's tree.
 
 ## Risks and how they are ruled out
 
-- Behaviour change on healthy disks: none — ENOENT/ENOTDIR keep "absent"; a symlink at an
-  owned-dir path is still left alone (never `rmtree`d through), as before.
+- Behaviour change without any I/O error, deliberate: a dangling `.git` symlink is treated
+  as present and the tree is not reported clean; before, it was ignored (`Path.exists()` follows
+  the link, so the old code read "not a checkout": `DirtyReport()` / `()`). Reproduced on the
+  base and on round 2's head (report, Correction 2). Why fail-closed: the inventory's question
+  is "may this tree be replaced or removed without losing the operator's work?", and a `.git`
+  that is there but leads nowhere does not answer it; it is the batch's own rule (not proven
+  absent → not absent). It is also not handed to git: given a `.git` it cannot use, git walks up
+  to the first enclosing checkout and reports on THAT one (round 2's head did exactly this:
+  dirty when no enclosing repository exists, but CLEAN / `()` under a clean enclosing one). So
+  `install._git_marker_anomaly` reads `.git` with `probe_stat` (lstat); a symlink is followed
+  (`probe_stat(follow=True)`): absent → ".git is a dangling symlink", unknown (ELOOP for a loop,
+  EIO, EACCES) → ".git is a symlink that cannot be followed (why)"; both make `dirty_report`
+  return `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))` and `extra_files` `None`,
+  with no git call. Same for a `.git` symlink loop (old: `Path.exists()` reads ELOOP as False).
+  A `.git` symlink that resolves goes to git exactly as before. Call sites whose result changes
+  for such a `.git` (all now see "changed" / "no inventory"): install.py `_adopt_locked` (update
+  refused), `_stage_and_activate` (final dirty check blocks the archive; the carry refuses),
+  `_prev_dirty_scan` (archived prior retained), `_prev_extras` (`None`);
+  service_binary_ops.py `binary_install` (refused: local changes), `_stale_clean_clones` (not
+  listed as a clean clone), `switch_source_plan` (refusal); service_maintenance.py
+  `_remove_source_leaf` (uninstall refuses, both checks; Clean skips the check, unchanged);
+  service_auto_install.py `_reconcile_group` (blocked). Where an identity check
+  (`verify_identity`) runs first, it may already refuse; the dirty result changes either way.
+- Other sites without an I/O error: ENOENT/ENOTDIR keep "absent"; the boot-restore, config and
+  firewall sites used `lexists` (lstat) before, so a dangling symlink reads the same; retire's
+  `still_there` moved from `os.path.exists` (follows) to lstat — a dangling symlink at a receipt
+  path is "still present" now, but the unlink loop just before it unlinks any symlink
+  (`islink`), so the result differs only if one remains after that loop (an unlink that failed
+  is already INCOMPLETE). A symlink at an owned-dir path is still left alone (never `rmtree`d
+  through), as before. `_rel_files_under` follows like `os.path.isdir` did; a base it cannot
+  stat other than ENOENT/ENOTDIR (ELOOP included) now raises instead of listing `[]`, so a
+  symlink loop at a switch target is no longer judged the artifact's own leftover (a second change without an
+  I/O error, covered by the CHANGELOG's "does not take over a folder it cannot fully read").
 - Firewall helper bytes change → boxes with the firewall read *Update required* until the
   operator re-applies (F36 makes the product say so). CHANGELOG carries the upgrade note.
 - Injector over-reach: it raises only for one exact path string, no `dir_fd` call is affected.
diff --git a/tests/core/test_existence_probe.py b/tests/core/test_existence_probe.py
index 5df3b73..ccd06e2 100644
--- a/tests/core/test_existence_probe.py
+++ b/tests/core/test_existence_probe.py
@@ -284,3 +284,53 @@ def test_retire_keeps_an_owned_dir_whose_stat_fails(tmp_path, monkeypatch, binar
                            f"([Errno {err}] {os.strerror(err)}: '{venv}')"]
     assert brx.receipt_path(svc._paths, "daemon").exists()     # the dir stays owned
     assert (venv / "bin" / "python3").read_bytes() == b"x"
+
+
+# ---- a `.git` that is a symlink: resolved or not --------------------------------------------
+#
+# The fake answers `git status`/`ls-files` as a CLEAN enclosing repository would: real git,
+# given a `.git` it cannot use, walks up to the first enclosing checkout and reports on that.
+
+def _git_answers_clean(dest):
+    from lhpc.core.probes.backends import CommandResult
+    ok = CommandResult(returncode=0, stdout="", stderr="")
+    return FakeSystem(commands={
+        ("git", "-C", str(dest), "status", "--porcelain", "-z", "--untracked-files=all"): ok,
+        ("git", "-C", str(dest), "ls-files", "-z", "--others"): ok})
+
+
+@pytest.mark.safety("existence-fail-closed")
+@pytest.mark.parametrize("kind", ["dangling", "loop"])
+def test_a_git_symlink_that_does_not_resolve_is_never_clean(tmp_path, kind):
+    from lhpc.core.install import DirtyReport
+    dest = tmp_path / "src" / "tree"
+    dest.mkdir(parents=True)
+    (dest / "added.txt").write_text("operator file")
+    (dest / ".git").symlink_to(tmp_path / "nowhere" if kind == "dangling" else dest / ".git")
+    fake = _git_answers_clean(dest)
+    inst = Installer(Paths(runtime_root=tmp_path), (), Config(values={}), fake.system)
+    anomaly = {"dangling": ".git is a dangling symlink",
+               "loop": (f".git is a symlink that cannot be followed ([Errno {errno.ELOOP}] "
+                        f"{os.strerror(errno.ELOOP)}: '{dest / '.git'}')")}[kind]
+    assert inst.dirty_report(dest, "src/tree") == DirtyReport(tracked=(f"({anomaly} — treating as dirty)",))
+    assert inst.extra_files(dest, "src/tree") is None
+    assert fake.calls == []                                   # never handed to git
+    assert (dest / ".git").is_symlink() and (dest / "added.txt").read_text() == "operator file"
+
+
+@pytest.mark.safety("existence-fail-closed")
+def test_no_git_and_a_git_symlink_that_resolves_keep_their_reading(tmp_path):
+    from lhpc.core.install import DirtyReport
+    dest = tmp_path / "src" / "tree"
+    dest.mkdir(parents=True)
+    (dest / "added.txt").write_text("operator file")
+    fake = _git_answers_clean(dest)
+    inst = Installer(Paths(runtime_root=tmp_path), (), Config(values={}), fake.system)
+    assert inst.dirty_report(dest, "src/tree") == DirtyReport()           # not a checkout: clean
+    assert inst.extra_files(dest, "src/tree") == ()
+    assert fake.calls == []
+    (tmp_path / "gitdir").mkdir()
+    (dest / ".git").symlink_to(tmp_path / "gitdir")
+    assert inst.dirty_report(dest, "src/tree") == DirtyReport()           # git consulted
+    assert inst.extra_files(dest, "src/tree") == ()
+    assert [c[3] for c in fake.calls] == ["status", "ls-files"]
```

## Round 1 RED → round 2 (history; the shas in this section are the round-2 shas)

Round 1 findings:

1. Plan `6753747` claimed two bootstrap `Path.exists()` edits (install.py:263/268) that the code
   commits do not contain.
2. Commit `075519e` kept `os.path.isdir()` after `probe_exists()`: an unexaminable path (stat
   fails, lstat fine) read as "not a dir" — `_rel_files_under` returned `[]` so `_artifact_only_dir`
   was `all([]) == True`, and the owned-dir retirement skipped the dir and dropped its receipt. The
   test's injector failed lstat and stat together, so it never reached these branches.

What changed, the red-before evidence, the per-commit patch-ids and the site list are in
"Correction 1" at the top of the report below. In short: the plan now states exactly what the diff
does (bootstrap moved to LEAVE with the reason); `runtime_fs.probe_stat` returns the stat result and
both kind decisions read it (`unknown` → raise / INCOMPLETE, receipt kept); two new stat-only tests
(one per site, × EIO/EACCES) were red before (`DID NOT RAISE OSError`; `ok=True`, receipt dropped)
and are green; the docs sentence now names exactly the sites in the diff; the CHANGELOG is unchanged.
`d788bbe` keeps its patch-id; `7bcccee` changed only in docs context lines (its `+`/`-` lines and
other files are identical).

## Commits

- d390195 Q2: plan
- b9f7c8b Q2: probe_exists — the fail-closed existence probe
- a2ce1af Q2: the controller's safety paths never read an unexaminable path as absent
- ffa36ec Q2: the firewall helper reads an unexaminable journal as present

### Commit messages

```text
d390195 Q2: plan

b9f7c8b Q2: probe_exists — the fail-closed existence probe

Only ENOENT, or ENOTDIR on a parent, proves a path absent; any other error
is "unknown" with the error text. The receipt-leaf check uses it.

a2ce1af Q2: the controller's safety paths never read an unexaminable path as absent

Binary retire (leftover files, owned folders), the binary->source switch's
artifact-only judgement, the dirty/carry inventory of a checkout, the
boot-restore markers and journal, and the config journal recovery decide
presence with probe_exists: an EIO/EACCES refuses or keeps. Where the kind
decides (the switch's tree listing, the owned-folder removal), probe_stat
hands back the stat result the decision reads, so a failing stat is never
"not a directory".

Behaviour change without an I/O error: a checkout whose .git is a symlink
that does not resolve (dangling, a loop) is treated as present and the tree
is not reported clean; before, Path.exists() followed the link and the
.git was ignored. It is never handed to git, which would walk up to an
enclosing repository and report on that one.

ffa36ec Q2: the firewall helper reads an unexaminable journal as present

_read_json_state reported a state file it could neither open nor lstat
(EIO, EACCES) as absent, so recover() reported nothing to finish. Only
ENOENT/ENOTDIR are absent now. The helper is stdlib-only, so the rule is
inline; the changed bytes make boxes report Update required.
```

## The plan (plans/PLAN-Q2.md)

# PLAN-Q2 — `probe_exists()`: one fail-closed existence probe for every safety path

Base: `integration/0.12.0` (c1d8bbd from round 5, ac1eadd in round 4; rounds 1–3 042716f). Files: `lhpc/core/runtime_fs.py` (helper), `install.py`,
`service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (the journal
probe and its two outcomes, in Q1's result type), `firewall_helper.py`, their tests, CHANGELOG,
`docs/architecture.md` (the runtime-filesystem guarantee gains three sentences: a `.git`
symlink that does not resolve is present, never clean and never handed to git; the presence
decisions that use `probe_exists`/`probe_stat`, by site; and only ENOENT/ENOTDIR read as absent,
with its tests, the firewall helper's inline rule and its test included).

## The helper (runtime_fs.py, beside the other no-follow leaf helpers)

`probe_exists(path) -> (state, why)`, `state` a `Literal["present", "absent", "unknown"]`, `why`
the error text ("" unless unknown). `os.lstat` (no-follow: a dangling or escaping symlink is
present); `absent` ONLY on `FileNotFoundError`/`NotADirectoryError`; any other `OSError` (EIO,
EACCES, ELOOP, ENAMETOOLONG) or a `ValueError` (NUL byte — `os.path.lexists` reads that as
absent too) is `unknown`. Plain function, one Literal + one message field; no class, no enum
type. Guardrail check: it REMOVES duplication — `_receipt_leaf_present` (service_binary_ops.py:842)
becomes a one-line use of it.

`probe_stat(path, *, follow=False) -> (state, why, st)` (added in commit 3, beside it): the same
rule plus the stat result it read (`st` None unless present); `follow=True` uses `os.stat` (a
dangling link is absent). A decision that follows the probe takes the kind (directory, symlink)
from `st`, never from a second `os.path.isdir`/`islink`, which reads an error as "no".

## Inventory — `grep -nE 'lexists\(|\.exists\(|isfile\(|isdir\('` over the FILES

REPLACE (safety: an OSError read as absence deletes, overwrites or proceeds):

| site today | decision on a false "absent" | with `unknown` |
|---|---|---|
| install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …"); a `.git` symlink that does not resolve: dirty (named, see below) |
| install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses); same for an unresolvable `.git` symlink |
| service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | `probe_stat(follow=True)`: unknown raises → `_artifact_only_dir` False; is-dir from its `st` |
| service_binary_ops.py:930 retire `still_there` exists | receipt dropped while files remain (unowned) | still present → INCOMPLETE, receipt kept |
| service_binary_ops.py:946 owned dir isdir + islink | dir skipped, receipt dropped (unowned venv) | `probe_stat` (lstat: link?) then `probe_stat(follow=True)` (dir?): unknown in either → INCOMPLETE, receipt kept |
| service_binary_ops.py:842 `_receipt_leaf_present` (already lstat) | — | same behaviour, uses the helper |
| service_boot_restore.py:75 running-band marker lexists | `absent` evidence | `unsafe` |
| service_boot_restore.py:86 last-start candidate lexists | `absent` evidence | `unsafe` |
| boot_restore.py:141 `load_journal` lexists | `absent` → restore runs | `unsafe:unreadable (why)` |
| config.py:1810 `recover_config_transaction` lexists | `UNNECESSARY` = "no journal" | `BLOCKED` (round 4, Q1's type; before Q1 `""` = recovery required) |
| firewall_helper.py:999 `_read_json_state` lexists | journal `absent` → `recover()` True → apply proceeds | `present-invalid` (inline: stdlib-only module) |

LEAVE (display/convenience, or an error already refuses/keeps):
install.py:263/268 bootstrap `local.toml`/`secrets.toml` `not dest.exists()` — dropped after
drafting because `Path.exists()` RAISES on EIO/EACCES on every supported Python (3.11–3.13), so
`apply_bootstrap` already fails the action and no red test is possible; install.py:228/232
plan status text; :291 doclink (`os.symlink` itself never clobbers); :2237-2260
post-clone sanity (`False` = clone failed). service_binary_ops.py:622 (an unstat-able entry is
listed as a file → not owned → not artifact-only: fails closed); :662 staging rmtree (error =
keep); :705 build cwd; :719 CLI missing → error; :922 unlink guard (error = keep, then :930
reports it). config.py:1001 web-session key persistence (error = ephemeral key, nothing written);
config.py:1877 eager startup recovery (absent and unknown both leave it to `config_lock`, which
already refuses on EIO — no observable difference, no red test possible). selfupdate.py:136
(error = no repo root = self-update unavailable); service_selfupdate.py:40 log tail; :96/:257
dependency rows; :1096 helper exe text; :1295 `.git` missing → refuses. firewall_helper.py:1390
transition unlink (error = keep). service_firewall.py:344 log link, :412 units-enabled row.
Out of the grep but scanned (`is_dir`/`is_file`/`is_symlink` in FILES): none decides a delete or
an overwrite on absence (binary_ops:247/532 hand absence to the locked adoption, which proves it
with `leaf_kind`).

## Commits

1. `Q2: plan`. 2. `Q2: probe_exists — the fail-closed existence probe` (helper, its unit test,
`_receipt_leaf_present` on it). 3. `Q2: the controller's safety paths never read an
unexaminable path as absent` (the REPLACE rows except `_receipt_leaf_present` and the firewall
helper — 9 sites — plus `probe_stat`, one parametrised test, and one stat-only test per kind
decision). 4. `Q2: the firewall helper reads an unexaminable journal as present` (inline
lstat; CHANGELOG upgrade note). 5. report.

## Tests

`tests/core/test_existence_probe.py`: the helper (present / absent / dangling symlink present /
ENOTDIR absent / EIO+EACCES unknown with errno text), and ONE parametrised test over the
replaced sites × {EIO, EACCES}: an injector makes `os.lstat` AND `os.stat` raise for exactly the
site's path (so the `exists`/`isfile` sites are red before too), drives the site through its
public seam (`dirty_report`, `extra_files`, `binary_retire(force=True)`,
`switch_source_plan`, `load_journal`, `recover_config_transaction`; `_boot_marker_view` as
test_boot_restore.py already does for private views) and asserts the refusal/keep state AND that
the file is still there. The two kind decisions (`_rel_files_under`, the owned-dir retirement)
get one more test each with an injector that fails `os.stat` ONLY (lstat answers), so the
`isdir` after a present probe is caught too. The dangling-`.git` change: `test_a_git_symlink_that_does_not_resolve_is_never_clean`
(dangling, loop; exact `DirtyReport` and `None` asserted, no git call, against a fake git that
answers like a clean enclosing repository) and `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading`
(no `.git` → `DirtyReport()` / `()` with no git call; a resolving `.git` symlink → git consulted). Firewall helper: one case in
`tests/host/test_firewall.py` (`recover()` is False and the journal stays). Red-before: run the
new tests against commit 2's tree.

## Risks and how they are ruled out

- Behaviour change without any I/O error, deliberate: a dangling `.git` symlink is treated
  as present and the tree is not reported clean; before, it was ignored (`Path.exists()` follows
  the link, so the old code read "not a checkout": `DirtyReport()` / `()`). Reproduced on the
  base and on round 2's head (report, Correction 2). Why fail-closed: the inventory's question
  is "may this tree be replaced or removed without losing the operator's work?", and a `.git`
  that is there but leads nowhere does not answer it; it is the batch's own rule (not proven
  absent → not absent). It is also not handed to git: given a `.git` it cannot use, git walks up
  to the first enclosing checkout and reports on THAT one (round 2's head did exactly this:
  dirty when no enclosing repository exists, but CLEAN / `()` under a clean enclosing one). So
  `install._git_marker_anomaly` reads `.git` with `probe_stat` (lstat); a symlink is followed
  (`probe_stat(follow=True)`): absent → ".git is a dangling symlink", unknown (ELOOP for a loop,
  EIO, EACCES) → ".git is a symlink that cannot be followed (why)"; both make `dirty_report`
  return `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))` and `extra_files` `None`,
  with no git call. Same for a `.git` symlink loop (old: `Path.exists()` reads ELOOP as False).
  A `.git` symlink that resolves goes to git exactly as before. Call sites whose result changes
  for such a `.git` (all now see "changed" / "no inventory"): install.py `_adopt_locked` (update
  refused), `_stage_and_activate` (final dirty check blocks the archive; the carry refuses),
  `_prev_dirty_scan` (archived prior retained), `_prev_extras` (`None`);
  service_binary_ops.py `binary_install` (refused: local changes), `_stale_clean_clones` (not
  listed as a clean clone), `switch_source_plan` (refusal); service_maintenance.py
  `_remove_source_leaf` (uninstall refuses, both checks; Clean skips the check, unchanged);
  service_auto_install.py `_reconcile_group` (blocked). Where an identity check
  (`verify_identity`) runs first, it may already refuse; the dirty result changes either way.
- Other sites without an I/O error: ENOENT/ENOTDIR keep "absent"; the boot-restore, config and
  firewall sites used `lexists` (lstat) before, so a dangling symlink reads the same; retire's
  `still_there` moved from `os.path.exists` (follows) to lstat — a dangling symlink at a receipt
  path is "still present" now, but the unlink loop just before it unlinks any symlink
  (`islink`), so the result differs only if one remains after that loop (an unlink that failed
  is already INCOMPLETE). A symlink at an owned-dir path is still left alone (never `rmtree`d
  through), as before. `_rel_files_under` follows like `os.path.isdir` did; a base it cannot
  stat other than ENOENT/ENOTDIR (ELOOP included) now raises instead of listing `[]`, so a
  symlink loop at a switch target is no longer judged the artifact's own leftover (a second change without an
  I/O error, covered by the CHANGELOG's "does not take over a folder it cannot fully read").
- Firewall helper bytes change → boxes with the firewall read *Update required* until the
  operator re-applies (F36 makes the product say so). CHANGELOG carries the upgrade note.
- Injector over-reach: it raises only for one exact path string, no `dir_fd` call is affected.
- Q1 conflict: resolved in round 4 on Q1's `ConfigRecovery` — an absent journal is `UNNECESSARY`,
  an unexaminable one `BLOCKED`. Line numbers in this plan are those of 042716f.

## Open questions (with recommendation)

1. Firewall helper change forces an operator re-apply on upgrade. Recommendation: ship it (the
   journal is the one firewall state whose false "absent" lets an apply run over an interrupted
   one); it is its own commit, so it can be held back to a release that changes the helper anyway.
2. `os.walk` error swallowing in `_rel_files_under` is the same class one line below the isdir;
   fixed in the same site (onerror raises). Recommendation: keep.

## The report (code-review/code-report-Q2.md)

# Code report — Q2: `probe_exists()`, one fail-closed existence probe

## Correction 2 (gate 1 round 2 RED → round 3)

Commits after the correction (round-2 sha in brackets; Correction 1 below uses the round-2 shas, the
body below it the round-1 shas):

| round 3 | round 2 | patch-id (`git patch-id --stable`, first 12) | change |
|---|---|---|---|
| `2e934ea` | `97e5057` | `73302f83c118` → `af34aa7da31a` | plan: the behaviour change, why, every call site |
| `65540b0` | `d788bbe` | `eb805740a5f8` = `eb805740a5f8` | none |
| `66810ed` | `3f5b102` | `3e9ebcd6c2dd` → `87bcffc77f34` | code, 2 tests, CHANGELOG bullet, docs sentence, commit message |
| `a8db6d9` | `7bcccee` | `839c1f1a76f8` = `839c1f1a76f8` | none |
| report | `62083e7` | — | this section, the gate-1 file |

Made with `git commit --fixup 97e5057`, `--fixup 3f5b102`, `--fixup=reword:3f5b102` and
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash 042716f`; the tree before and after the rebase is
identical (`HEAD^{tree}` compared). The new CHANGELOG bullet and docs sentence sit outside `7bcccee`'s
context lines, so its patch-id is unchanged this time.

### The finding

The plan said "Behaviour change on healthy disks: none". Untrue: `probe_exists` is `os.lstat`, the old
`dirty_report`/`extra_files` check was `(dest / ".git").exists()`, which follows the link. A dangling
`.git` symlink was "not a checkout" before (`DirtyReport()` / `()`), and "present" on round 2, which
then handed the tree to git. No test pinned it.

### Reproduction on both trees (and the fix)

`Installer(...).dirty_report(d, ...)` / `.extra_files(d, ...)` on a tree with `added.txt` and a `.git`
symlink, run from a worktree of each commit with `PYTHONPATH` set to it (Python 3.11). The fake
runner answers either nothing (git fails, as with no enclosing repository) or `rc 0`, empty output for
`git status`/`ls-files` (what git prints for a CLEAN enclosing repository):

| tree | `.git` | git answer | `dirty_report` | `extra_files` | git calls |
|---|---|---|---|---|---|
| base 042716f | dangling | fails | `DirtyReport()` (clean) | `()` | 0 |
| base 042716f | dangling | clean enclosing | `DirtyReport()` (clean) | `()` | 0 |
| base 042716f | loop | either | `DirtyReport()` (clean) | `()` | 0 |
| round 2 62083e7 | dangling | fails | `tracked=('(git status failed — treating as dirty)',)` | `None` | 2 |
| round 2 62083e7 | dangling | clean enclosing | `DirtyReport()` (**clean**) | `()` | 2 |
| round 2 62083e7 | loop | same two rows as dangling | | | 2 |
| round 3 | dangling | either | `tracked=('(.git is a dangling symlink — treating as dirty)',)` | `None` | 0 |
| round 3 | loop | either | `tracked=("(.git is a symlink that cannot be followed ([Errno 40] Too many levels of symbolic links: '<d>/.git') — treating as dirty)",)` | `None` | 0 |

That the clean-enclosing row is real git behaviour, not just the fake (git 2.43.0, in `$SCRATCH`):
an `outer/` repository with `sub/.git` → a dangling link: `git -C sub status --porcelain` prints
`?? sub/a` with rc 0 (git walked up to `outer`); `git -C sub ls-files --others` prints `a`; the same
`.git` with no enclosing repository: `fatal: not a git repository`, rc 128. So round 2 was not only
"dirty instead of clean": its answer depended on whatever repository encloses the tree, and could
be clean.

`Path.exists()` for the old reading, measured on 3.11.15, 3.12.3 and 3.13.14: dangling → False,
loop → False, an injected EIO on `os.stat` → raises `OSError` (all three versions).

### Decision: fail closed, and never ask git

The inventory answers "may this tree be replaced or removed without losing the operator's work?". A
`.git` that is there but leads nowhere does not answer that, and the batch's rule is "not proven
absent → not absent". Keeping the old "absent" reading would make a tree with a broken `.git` read
as a clean non-checkout, so every caller below would replace or remove it unasked. Handing it to git
(round 2) makes the answer depend on the enclosing directory. So:

- `install._git_marker_anomaly(dest)` (install.py:164): `probe_stat(.git)` (lstat). Absent → `None`.
  `unknown` → `"cannot examine .git (why)"` (round 2's text, unchanged). A symlink → `probe_stat(...,
  follow=True)`: absent → `".git is a dangling symlink"`; `unknown` (ELOOP for a loop, EIO, EACCES on
  the target) → `".git is a symlink that cannot be followed (why)"`; present → `""`. Anything else
  present (a directory, a gitfile) → `""`.
- `dirty_report` (install.py:901): `None` → `DirtyReport()`; an anomaly →
  `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))`; `""` → git, as before.
- `extra_files` (install.py:958): `None` → `()`; an anomaly → `None`; `""` → git, as before.

Behaviour change, exactly: a `.git` symlink that does not resolve (dangling, or a loop) is treated as
present and the tree is not reported clean; before, it was ignored (clean, `()`). A `.git` symlink
whose target cannot be stat'ed for another reason (EACCES, EIO) was an uncaught `OSError` from
`Path.exists()` before; now it is the named anomaly. A `.git` symlink that resolves, a `.git`
directory and a gitfile go to git as before. Plan, CHANGELOG (new bullet) and the docs sentence state
it; no "no behaviour change" sentence is left in the plan, the CHANGELOG or the docs (the gate file
quotes the old one only as the finding and as a removed diff line).

Call sites whose result changes for such a `.git` (HEAD line numbers):

| site | function | result now |
|---|---|---|
| install.py:475 | `_adopt_locked` (update) | `blocks_update()` → action failed, the anomaly listed under "modified" |
| install.py:607 | `_stage_and_activate` final dirty check | blocks the archive |
| install.py:622 | `_stage_and_activate` `_carry` | `None` → refuses ("could not be inventoried") |
| install.py:1514 / 1519 | `_prev_dirty_scan` | dirty → the archived prior is retained |
| install.py:1552 | `_prev_extras` | `None` |
| service_binary_ops.py:274 | `binary_install` (pinned checkout) | refused: local changes |
| service_binary_ops.py:539 | `_stale_clean_clones` | skipped (not a clean clone) |
| service_binary_ops.py:792 | `switch_source_plan` | refusal |
| service_maintenance.py:1961 / 1968 | `_remove_source_leaf`, uninstall (`allow_dirty=False`) | refused; Clean (`allow_dirty=True`) skips the check, unchanged |
| service_auto_install.py:1698 | `_reconcile_group` | `blocked` |

At the sites where `verify_identity` runs first (binary_ops 274/539/792, maintenance, auto-install)
that check may refuse before; the dirty result changes either way.

Other sites of the batch, without any I/O error (also in the plan's risk section): the boot-restore,
config and firewall sites used `lexists` (lstat) before, so a dangling symlink reads the same.
Retire's `still_there` moved from `os.path.exists` (follows) to lstat: a dangling symlink at a
receipt path now reads "still present", but the unlink loop right before it unlinks every symlink
(`islink`), so the result differs only if one remains after that loop (a failed unlink is already
INCOMPLETE). `_rel_files_under` follows like `os.path.isdir` did; a base it cannot stat for a reason
other than ENOENT/ENOTDIR (ELOOP included) now raises instead of listing `[]`. The owned-dir check
leaves a symlink alone as before.

### Tests (tests/core/test_existence_probe.py)

- `test_a_git_symlink_that_does_not_resolve_is_never_clean[dangling,loop]`: fake git answers like a
  clean enclosing repository; asserts `dirty_report == DirtyReport(tracked=("(.git is a dangling
  symlink — treating as dirty)",))` (loop: the exact ELOOP text with the path), `extra_files is None`,
  no git call, the link and `added.txt` still there.
- `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading`: no `.git` → `DirtyReport()` and
  `()` with no git call; a `.git` symlink to a directory → git consulted (`status`, `ls-files`) and its
  answer returned (`DirtyReport()`, `()`).

Red-before (the round-3 test file copied into a worktree of each commit, imports checked to come
from the worktree): base 042716f and round 2 62083e7 both `2 failed, 1 passed` — the two
`never_clean` cases fail on the `dirty_report` assertion (`DirtyReport(tracked=(), untracked=())` on
both: on the base the `.git` was ignored, on round 2 the fake enclosing repository answered clean);
the keep-their-reading test passes on both, as it must. Green on round 3: `32 passed` (whole file).

Healthy trees keep their results; existing tests that prove it, unchanged and green:
`tests/install/test_source.py::test_dirty_report_names_edits_and_additions_but_not_artifacts`,
`::test_dirty_carveout_is_exact_leaf_only`, `::test_dirty_report_ignores_the_shipped_patch_only`,
`::test_an_ignored_file_survives_an_update` (all with a real `.git` directory), and the round-2
`test_an_unexaminable_path_is_never_absent[dirty-report-*, extra-files-*]` (lstat `unknown`, same
text). No existing test pinned the no-`.git` reading; the new keep-their-reading test does (it
passes on the base too).

### Runs (foreground, HEAD, Python 3.11 venv with `.[dev]`)

- `ruff check lhpc testlab`: All checks passed.
- `tests/core tests/install tests/host/test_firewall.py tests/repo`: `103 failed, 3778 passed, 22 skipped`.
  The failure set (by test id) is identical to the same run on round 2's head `62083e7` in a
  worktree (`103 failed, 3775 passed, 22 skipped`): 92 `test_bootstrap_deps.py`,
  `test_firewall.py::test_receipt_reader_rejects_nonroot_symlink_and_unsafe`,
  `test_version_consistent.py::test_changelog_leads_with_the_current_version`, 3
  `test_binary_install.py`, 1 `test_binary_channel.py`, and five that were not in round 2's
  report list (2 `tests/core/test_disk_warning.py`, `test_binary_pin_override.py`,
  `test_dependency_overview.py`, `test_deps.py`) — they fail identically on `62083e7` in this
  container, so they are environmental here, not this change; not investigated further. +3 passed = the
  3 new test cases. Two earlier runs of the same HEAD tree reported `3773 passed, 27 skipped`
  (same 103 failures); split runs and the final full run gave 22 skips (all root-chmod, missing
  `zstd`, and the slow-target "no row A yet" skip). The 5-skip difference was not explained.

### Adversarial self-review of the correction

Found and fixed before the push:
- My first `extra_files` edit inverted the branches (`absent` went to git, a reachable `.git`
  returned `()`); `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading` caught it (a
  `ls-files` call with no `.git`). Fixed before the commit.
- The first CHANGELOG draft claimed "an update keeps an archived copy" and "does not take its file
  inventory"; the update is refused at `_adopt_locked` first, so the archive path is not reached in
  the plain case. Both clauses dropped; the bullet lists only the local-change checks.
- The first caller line list was taken before my edit shifted install.py by 20 lines; recomputed on
  HEAD.
- The commit message of `3f5b102` did not mention the behaviour change; reworded
  (`--fixup=reword:`) with a paragraph on it.
- The plan's first draft called the dangling-`.git` change "the ONE behaviour change without an I/O
  error", while its next bullet names a second (`_rel_files_under` on a symlink-loop base now raises
  instead of listing `[]`). "ONE" dropped; that bullet now says it is a second such change.

Noted, not changed:
- A `.git` DIRECTORY that is not a valid repository (empty, corrupt) is handed to git as before, and
  git walks up to an enclosing repository the same way (checked: an empty `emp/.git` inside `outer/`
  → `git -C emp status` prints `?? emp/`, rc 0). Pre-existing on the base, not changed by Q2; a fix
  needs a different mechanism (compare `git rev-parse --show-toplevel` with `dest`, or set
  `GIT_CEILING_DIRECTORIES`). Proposed as a follow-up, not widened into this batch.
- `_stage_and_activate`'s carry message says "(git failed)" also when the inventory is `None` for an
  unexaminable or dangling `.git`; reached only if the tree changed after the first dirty check, which
  already names the anomaly.

## Correction 1 (gate 1 round 1 RED → round 2)

Commits after the correction (the round-1 sha in brackets; the body below this section is the round-1
report and uses the round-1 shas):

| round 2 | round 1 | patch-id (`git patch-id --stable`, first 12) | change |
|---|---|---|---|
| `97e5057` | `6753747` | `83f2f16ea3f8` → `73302f83c118` | finding 1 (plan text) |
| `d788bbe` | `c6cdaac` | `eb805740a5f8` = `eb805740a5f8` | none |
| `3f5b102` | `075519e` | `2b4aa9c35721` → `3e9ebcd6c2dd` | finding 2 (code, test, docs sentence, message) |
| `7bcccee` | `f8badb9` | `97f09f822b50` → `839c1f1a76f8` | none in its own lines — see below |
| report | `38e2289` | — | this section, the gate-1 file |

`7bcccee`'s patch-id moved although none of its `+`/`-` lines did: `git patch-id` hashes context
lines, and its `docs/architecture.md` hunk has the sentence `3f5b102` rewords as context. Proof, per
file (`git show <sha> -- <file> | git patch-id --stable`): `CHANGELOG.md` `185d76a45cbb` both,
`lhpc/core/firewall_helper.py` `efd9ddc419e6` both, `tests/host/test_firewall.py` `54eb884c74c0`
both; `docs/architecture.md` `3ccec2cc40ee` → `287a42648b96`, and
`diff <(git show f8badb9 -- docs | grep '^[-+][^-+]') <(git show 7bcccee -- docs | grep '^[-+][^-+]')`
is empty. The docs claim cannot be narrowed without touching that context: every line of
`075519e`'s sentence is inside `f8badb9`'s hunk. The final tree before and after the autosquash
rebase is identical (`git rev-parse HEAD^{tree}` compared).

### Finding 1 — the plan described two edits that are not in the diff

`plans/PLAN-Q2.md` listed bootstrap `local.toml`/`secrets.toml` (install.py:263/268) under REPLACE,
named `apply_bootstrap` as a test seam, and claimed a dangling symlink there is now kept. The code
commits never changed them. Fixed: the two rows move to LEAVE with one sentence why (`Path.exists()`
raises on EIO/EACCES on every supported Python, so `apply_bootstrap` already fails the action and no
red test is possible); the `apply_bootstrap` seam and the bootstrap risk line are gone; the commit
list counts the 9 controller sites; the plan names `probe_stat` and the stat-only tests.

### Finding 2 — `os.path.isdir()` after `probe_exists()` read a stat error as "not a directory"

Two sites took the kind from a second, error-swallowing call after a `present` probe:

- `_rel_files_under` (service_binary_ops.py): lstat fine + stat failing → `isdir` False → `[]` →
  `_artifact_only_dir` → `all([])` → True: a tree it could not examine was judged the artifact's own
  leftover.
- owned-dir retirement (service_binary_ops.py): `isdir` False → the dir skipped → receipt dropped,
  the folder left unowned.

The round-1 injector failed `os.lstat` and `os.stat` together, so the probe already said `unknown`
and neither branch was reached.

Change (`3f5b102`):

- `runtime_fs.probe_stat(path, *, follow=False) -> (state, why, st)` (runtime_fs.py:752): the same
  ENOENT/ENOTDIR-only rule as `probe_exists`, plus the stat result it read (`st` None unless present);
  `follow=True` uses `os.stat`, so a dangling link is absent and an unreachable target is `unknown`.
  `probe_exists` itself is unchanged (`d788bbe` keeps its patch-id).
- `_rel_files_under` (service_binary_ops.py:618): one `probe_stat(base, follow=True)` — the question
  `os.walk(base)` answers. `unknown` raises `OSError` (its caller returns False → the switch
  refuses); is-dir comes from `stat.S_ISDIR(st.st_mode)`.
- owned-dir retirement (service_binary_ops.py:953/955): `probe_stat(d)` (lstat: a symlink is never
  `rmtree`d through, as before), then for a non-link `probe_stat(d, follow=True)` (is it a dir?);
  `unknown` from either raises into the existing INCOMPLETE result, receipt kept. A race that swaps
  in a symlink between the two probes reaches `shutil.rmtree`, which refuses a symlink → INCOMPLETE.
- Tests (tests/core/test_existence_probe.py), injector `_stat_failing` fails `os.stat` ONLY for one
  exact path (no `dir_fd` call affected):
  - `test_probe_stat_returns_the_kind_and_follows_on_request` (link/dir kinds, dangling → absent,
    stat-only EIO → `unknown` while lstat still `present`);
  - `test_a_tree_whose_stat_fails_is_not_listed_as_empty[EIO,EACCES]`: `_rel_files_under` raises
    `OSError` with the errno text; `switch_source_plan` refuses the path; the file stays;
  - `test_retire_keeps_an_owned_dir_whose_stat_fails[EIO,EACCES]`: `binary_retire` → not ok,
    summary "… stays owned", details exactly `could not remove build/tools/meshtastic-cli ([Errno N] …)`,
    receipt still there, venv file intact.

Red-before (the new test file against `075519e`'s `lhpc/`:
`git stash -- lhpc && pytest tests/core/test_existence_probe.py; git stash pop`): `5 failed, 24 passed`.

- `test_probe_stat_…`: `AttributeError: module 'lhpc.core.runtime_fs' has no attribute 'probe_stat'`.
- `test_a_tree_whose_stat_fails_…[EIO]`, `[EACCES]`: `Failed: DID NOT RAISE OSError` (the listing was `[]`).
- `test_retire_keeps_an_owned_dir_…[EIO]`, `[EACCES]`: `ActionResult(ok=True, summary="Retired the binary
  install of 'daemon' (1 file(s) removed).")` — the receipt was dropped and the folder left behind.

Green: `29 passed`.

### Every site in the diff and how it fails closed (HEAD line numbers)

The presence decision comes from the probe's own result at each of these; `unknown` refuses or keeps:

| site | probe | `unknown` → |
|---|---|---|
| lhpc/core/service_binary_ops.py:618 `_rel_files_under` | `probe_stat(follow=True)`, kind from `st` | raises `OSError`; `_artifact_only_dir` (:746) → False; the switch refuses (:785 →) |
| lhpc/core/service_binary_ops.py:857 `_receipt_leaf_present`, used by retire `still_there` (:935) | `probe_exists` | present → INCOMPLETE, receipt kept |
| lhpc/core/service_binary_ops.py:953/955 owned-dir retirement | `probe_stat` then `probe_stat(follow=True)`, kind from `st` | INCOMPLETE, receipt kept |
| lhpc/core/install.py:890 `dirty_report` | `probe_exists` | dirty ("cannot examine .git …") |
| lhpc/core/install.py:954 `extra_files` | `probe_exists` | `None` (caller refuses) |
| lhpc/core/service_boot_restore.py:75 running-band marker | `probe_exists` | `unsafe` |
| lhpc/core/service_boot_restore.py:89 last-start candidate | `probe_exists` | `unsafe` |
| lhpc/core/boot_restore.py:140 `load_journal` | `probe_exists` | `unsafe:unreadable (…)` |
| lhpc/core/config.py:1810 `recover_config_transaction` | `probe_exists` | `""` (recovery required) |
| lhpc/core/firewall_helper.py:1000 `_read_json_state` | inline `os.lstat` | `present-invalid` |

Two error-swallowing `os.path` calls remain next to these sites, outside the probe, and both err to
the safe side: service_binary_ops.py:630 `os.path.isdir(full)` inside the walk (an entry it cannot
stat is listed as a file → not owned → not artifact-only), and :928 the retire unlink guard
`os.path.isfile/islink` (an error → not unlinked → the :935 probe sees it present → receipt kept).
Two more `os.path.isdir` calls in the same file decide no deletion and no retirement: :670 in
`_sweep_binary_staging` (an error → the `rmtree` is skipped and the staging folder stays — the safe
side, not a safety decision), and :713 in `_binary_provision` (the build steps' working directory:
an error leaves it at the runtime root, so a step that needs the source fails and the provision is
rolled back — neither a delete nor a retire decision).

Text: the CHANGELOG bullet already names exactly these behaviours (retire, switch, `.git`
inventory, boot restore and config journal, firewall) and never says "uniformly"; it is unchanged.
The docs/architecture.md sentence claimed "an existence check that decides a delete, a retirement or
'nothing to recover' goes through `probe_exists`" — wider than the diff (bootstrap still uses
`Path.exists()`, the unlink guard `isfile`). It now names the sites above and `probe_stat`.

Guardrail: `lhpc/` vs the round-1 head +27 −6 (runtime_fs 976→992, service_binary_ops 995→1000);
vs the base +94 −27. One new plain function, no new module dependency (`service_binary_ops` imports
stdlib `stat`).

### Runs (foreground, HEAD, Python 3.11)

- `tests/core`: `2074 passed, 6 skipped`.
- `tests/install tests/host/test_firewall.py tests/repo`: `98 failed, 1701 passed, 21 skipped`. The
  failure set is identical, test for test, to the same run on the round-1 head `38e2289` (a
  worktree, imports checked to come from it): 92 `test_bootstrap_deps.py` (root container, no
  non-root operator), `test_firewall.py::test_receipt_reader_rejects_nonroot_symlink_and_unsafe`
  (root), `test_version_consistent.py::test_changelog_leads_with_the_current_version` (0.12.0
  heading vs `version.py` 0.11.11, as before), and four with no `zstd` binary in this container
  (`test_binary_install.py::test_extract_rejects_hostile_archive[3 ids]`,
  `test_binary_channel.py::test_doctor_is_quiet_for_a_healthy_binary_install`).
- `ruff check lhpc testlab`: All checks passed. `ruff check tests --select F,E9`: All checks passed.

### Adversarial self-review of the correction

Found and fixed before the push:
- The first draft of the plan's risk line said an unreadable-target symlink at an owned-dir path now
  keeps the receipt. The code checks link-ness first and leaves a symlink alone, as before. Reworded.
- The retirement test first asserted `receipt_state == "valid"`; after the files are unlinked the
  receipt reads `unsafe`, so it now checks the receipt leaf is still there, as the round-1 test does.
- The new test used `os.path.stat` (a private import of `posixpath`); it imports `stat`.
- The commit message of `075519e` said only `probe_exists`; reworded (`--fixup=reword:`) to name
  `probe_stat`.

Noted, not changed:
- A symlink at an owned-dir path is still skipped and the receipt dropped (pre-existing; nothing to
  `rmtree` through a link).
- `probe_stat` has no return annotation (`probe_exists` has one); the docstring states the tuple.

Base (rounds 1–3) 042716f. Branch commits: `6753747` plan, `c6cdaac`, `075519e`, `f8badb9`
code, then this report. Interpreter: Python 3.11 (the new tests also pass on 3.13).

## Commits

| sha | subject | files | tests (red-before → green) |
|---|---|---|---|
| c6cdaac | Q2: probe_exists — the fail-closed existence probe | `lhpc/core/runtime_fs.py`, `lhpc/core/service_binary_ops.py` (`_receipt_leaf_present` on the helper), `tests/core/test_existence_probe.py` | helper tests, 6 cases. RED against 6753747's `lhpc/`: `6 failed` (no `probe_exists`). GREEN: `6 passed`. `_receipt_leaf_present` keeps its behaviour: `tests/install/test_binary_channel.py` 133 passed (incl. `test_retire_refuses_a_file_whose_lstat_fails`). |
| 075519e | Q2: the controller's safety paths never read an unexaminable path as absent | `install.py`, `service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (one probe), test, CHANGELOG, `docs/architecture.md` | `test_an_unexaminable_path_is_never_absent`, 9 sites × {EIO, EACCES}. RED against c6cdaac's `lhpc/`: `18 failed, 6 deselected`. GREEN: `18 passed`. |
| f8badb9 | Q2: the firewall helper reads an unexaminable journal as present | `lhpc/core/firewall_helper.py`, `tests/host/test_firewall.py`, CHANGELOG (upgrade note), `docs/architecture.md` | `test_a_journal_that_cannot_be_examined_is_not_absent[EIO,EACCES]`. RED against 075519e's `lhpc/`: `2 failed`. GREEN: `2 passed`. |

How red-before was run: `git checkout <parent> -- lhpc && python -m pytest -q -p no:cacheprovider <test> ; git checkout HEAD -- lhpc`.

Red-before, by site. Two of the nine controller sites were not silent before. `Path.exists()` on 3.11–3.13
*raises* on EIO/EACCES, so `dirty_report`/`extra_files` let an uncaught `OSError` escape. Now they return the
typed refusal: dirty, or `None`. The other seven read the error as absence: retire dropped the receipt, the
switch took over the tree, boot restore and the config journal reported "absent".

## Deviations from the plan

1. Bootstrap `local.toml`/`secrets.toml` (install.py:263/268) are NOT changed. On every supported Python,
   `Path.exists()` raises on EIO/EACCES, so `apply_bootstrap` already records `failed`. No red test is possible.
   The change would have added code with no effect.
2. The plan named `config.py:1877` as "leave" and kept it. The commit list has 3 code commits, as planned.

## Line counts and dependencies (guardrail)

Measured on round 5 (base `integration/0.12.0` c1d8bbd, code head ffa36ec): `lhpc/` +115 −25 (net +90).
runtime_fs 958→992, service_binary_ops 986→998, install 2317→2343, config 2161→2165, boot_restore 338→340,
service_boot_restore 645→651, firewall_helper 1479→1485. The code grows because each site now has an explicit `unknown`
branch. That is the smallest form that meets the completion criterion.

Dependencies: no new imports between modules. `install.py` imports `runtime_fs` locally, as it already does in
other places. `boot_restore.py` already imported `runtime_fs` and loses a local `import os`. `service_*` gain no
new `self.*` method. The firewall helper stays stdlib-only, with the rule inline.

Design: one plain function and a `Literal` with a message field. No class, no enum type, no registry. Why
`guard_state` is not reused: it needs the path inside the runtime root, and it reads a missing parent as
`unsafe`. A fresh box has no `state/running/`, so boot restore would turn unsafe. Changing `guard_state` would
change the uninstall guard.

## The 6-point block

1. **Contracts.**
   - `runtime_fs.probe_exists(path) -> (state, why)` (runtime_fs.py:735). `state` is one of
     `present|absent|unknown`; `why` is `""` unless the state is `unknown`. It never raises.
   - `Installer.dirty_report` (install.py:881): an unexaminable `.git` now gives a truthy report with
     `blocks_update()` true. The new tracked entry `"(cannot examine .git (…) — treating as dirty)"` follows
     the same pattern as the existing `"(git status failed — treating as dirty)"`.
   - `Installer.extra_files` (install.py:939): `None` means "could not take the inventory" (existing contract:
     the caller refuses).
   - `_rel_files_under` (service_binary_ops.py:609) may now raise `OSError`. Its only caller,
     `_artifact_only_dir` (:745), turns that into `False`.
   - Retire (service_binary_ops.py:934/:950) returns the existing INCOMPLETE `ActionResult` and keeps the
     receipt.
   - `_boot_marker_view` (service_boot_restore.py:70): `unsafe` uses the existing tri-state.
   - `boot_restore.load_journal` (boot_restore.py:134): `"unsafe:unreadable (<err>)"`, an existing state form.
   - `config.recover_config_transaction` (config.py:1792): `""` means recovery-required (existing contract).
   - `firewall_helper._read_json_state` (:991): `present-invalid` (existing).
   - No lock order, persisted format or exception type changed.
2. **Invariants + tests.**
   - *Path containment / fail closed*: `test_existence_probe.py` (helper and sites).
   - *Locally added files are never collateral*: `extra-files`, `dirty-report` and `switch-artifact-only` cases.
   - *Config as a transaction*: the `config-journal` case, plus the existing
     `test_a_journal_path_that_cannot_be_examined_refuses_the_writer`.
   - *Boot restore replays only saved configuration*: the `boot-*` cases.
   - *Binary receipt ownership*: the `retire-*` cases.
   - *Firewall fail-closed*: the new `test_firewall.py` case.
   - All of `tests/core` passes.
3. **Known failure classes.**
   - Fakes: the injector wraps the real `os.lstat`/`os.stat` with the same call signature. It raises only for
     one exact path string, and only when no `dir_fd` is passed.
   - Probes: EIO and EACCES at every site; ELOOP and NUL in the helper; ENOTDIR on a parent is absent.
   - KeyboardInterrupt: the probe catches only `OSError`/`ValueError`. The `os.walk` `onerror` re-raises the
     walk's `OSError`, never a `BaseException`.
   - Same decision across CLI / web / detached job / boot-restore: every site is in the core function that all
     of them call. The adapters are untouched.
   - Stacked conflicts: no file on Q1's list (jobs/hmac/autoinstall), Q4's list (service_system/pki) or Q5's
     list (web static). `config.py` changes only in the probe and its one comment line inside
     `recover_config_transaction`.
4. **Test rules.**
   - Every new test is red before and green after (table above).
   - Decision strings are compared exactly: `load_journal` against the full `unsafe:unreadable (…)` string,
     refusals by their path field.
   - No return value goes unchecked.
   - No network.
5. **Whole directories**, foreground, on HEAD f8badb9:
   - `tests/core`: `2069 passed, 6 skipped`.
   - `tests/install tests/repo tests/host`: `96 failed, 2183 passed, 9 skipped`. Every failure also fails on
     the base 042716f:
     - 92 in `tests/install/test_bootstrap_deps.py`: the container runs as root and the script refuses without
       a non-root operator.
     - `test_firewall.py::test_receipt_reader_rejects_nonroot_symlink_and_unsafe`,
       `test_network_controls.py::test_rule_helpers_and_single_install_site` and
       `test_power_controls.py::test_dependency_entry_probe_and_bootstrap_exclusion`: root environment.
     - `tests/repo/test_version_consistent.py::test_changelog_leads_with_the_current_version`: `version.py` says
       0.11.11 under a 0.11.12 heading already on the base. The release commit fixes it.
   - `ruff check lhpc testlab`: All checks passed. `ruff check tests --select F,E9`: All checks passed.
   - No function signature changed. A grep of tests/ and testlab/ for `_rel_files_under`, `_artifact_only_dir`,
     `_receipt_leaf_present` and `probe_exists` finds only the new test.
6. **Adversarial self-review.** Found and fixed:
   - A stale "`os.path.lexists` does not" comment in config.py, reworded.
   - The bootstrap edit had no effect, so it was reverted (deviation 1).
   - The test's `monkeypatch.undo()` also undid the case's `binary_target` patch. The injector now has its own
     `MonkeyPatch`.

   Noted, not changed:
   - Retire's `still_there` now treats a path `under()` cannot resolve as still present. Before, the uncaught
     `PathContainmentError` escaped, so this is the safer side.
   - ~~`_rel_files_under` keeps `os.path.isdir(base)` after a `present` probe.~~ Wrong — gate 1 round 1
     finding 2; fixed in Correction 1.
   - The firewall helper's bytes change, so boxes read *Update required* (upgrade note in the CHANGELOG). The
     change is its own commit, so it can be held back.

   The docs (one sentence in architecture.md) and this report describe exactly the diff.

## Full diff of the code commits (c1d8bbd..ffa36ec, without code-review/)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 9977b2e..bb2263d 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -17,6 +17,21 @@
   print the details of an unexpected error, or cannot undo itself after Ctrl-C, now says so in one line instead of
   staying silent (after Ctrl-C the next command finishes the undo, as before). In these lines an error message that
   spans several lines is joined into one.
+Upgrade note — boxes with the firewall installed: this release changes the firewall helper, so the dashboard
+reads *Update required* after the update; run `sudo bash <runtime root>/config/files/firewall/firewall-apply.sh`
+(and `lhpc webserver apply` if remote access is configured) before a reboot.
+
+- The firewall no longer treats an interrupted change it cannot read (a disk or permission error) as "nothing to
+  finish": it refuses to apply or check until the state is readable again.
+- A disk or permission error on a file LHPC must not lose no longer reads as "the file is gone": retiring a
+  binary install keeps its record while a file or folder cannot be checked, a switch to the source channel does
+  not take over a folder it cannot fully read, an update or uninstall treats a checkout whose `.git` cannot be
+  checked as changed, and boot restore and the config journal treat an unreadable record as one that needs
+  attention instead of an absent one.
+- A source checkout whose `.git` is a symlink that does not resolve (dangling, or a loop) is now treated as
+  present and the tree is not reported clean: every local-change check (update, uninstall, binary install, switch
+  to the source channel, auto-install update) reads it as changed. Before, the `.git` was ignored and the tree
+  read as clean.
 
 - Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
   before it ships: an update that would stall or run into a time limit on a slow box turns the release check
diff --git a/docs/architecture.md b/docs/architecture.md
index 144c64a..1850a6e 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -205,7 +205,17 @@ The guarantees the controller gives, each with where it is implemented and prove
   `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write; atomic
   writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
-  and caught at every boundary. `tests/core/test_runtime_fs.py`.
+  and caught at every boundary. `tests/core/test_runtime_fs.py`. A checkout's `.git` that is a
+  symlink which does not resolve (dangling, a loop) is treated as present and the tree is not
+  reported clean (before 0.12.0 it was ignored); it is never handed to git, which would walk up
+  to an enclosing repository. These presence decisions use
+  `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory, symlink) decides: binary
+  retire's check of each receipt file and owned folder, the binary->source switch's listing of a
+  tree, a checkout's dirty/carry inventory (`.git`), the boot-restore markers and journal, and
+  the config journal recovery. In each, only
+  ENOENT/ENOTDIR read as absent, any other error refuses or keeps
+  (`tests/core/test_existence_probe.py`); the stdlib-only firewall helper applies the same rule
+  inline to its journal (`tests/host/test_firewall.py`).
 - **Source transactions.** An update clones a candidate beside the destination (recorded before
   the clone starts, so recovery removes a clone a crash interrupted), archives the
   prior source to a transaction-owned `.prev`, activates by atomic no-clobber rename, writes the
diff --git a/lhpc/core/boot_restore.py b/lhpc/core/boot_restore.py
index 37cd6b1..4fee885 100644
--- a/lhpc/core/boot_restore.py
+++ b/lhpc/core/boot_restore.py
@@ -137,9 +137,11 @@ def load_journal(paths: Paths) -> tuple[dict | None, str]:
     AND gate-based evidence retirement (it cannot know what was previously consumed)."""
     p = journal_path(paths)
     try:
-        import os
-        if not os.path.lexists(p):
+        state, why = runtime_fs.probe_exists(p)
+        if state == "absent":
             return None, "absent"
+        if state == "unknown":
+            return None, f"unsafe:unreadable ({why})"
         raw = runtime_fs.read_text(paths, p, max_bytes=JOURNAL_MAX_BYTES)
     except (OSError, PathContainmentError) as exc:
         return None, f"unsafe:unreadable ({exc})"
diff --git a/lhpc/core/config.py b/lhpc/core/config.py
index 3da7efd..332d076 100644
--- a/lhpc/core/config.py
+++ b/lhpc/core/config.py
@@ -1815,9 +1815,13 @@ def recover_config_transaction(paths: Paths) -> tuple[ConfigRecovery, str]:
     # Presence is decided WITHOUT following the leaf: ANY directory entry at the journal
     # path -- a regular file, OR a symlink (including a dangling or escaping one) -- is a
     # pending journal that must be recovered/blocked. `Path.exists()` follows the link and
-    # would report a dangling-symlink journal as absent; `os.path.lexists` does not.
-    if not os.path.lexists(jp):
+    # would report a dangling-symlink journal as absent; `probe_exists` does not, nor EIO. An
+    # unexaminable journal is never absent: it blocks, like any journal that cannot be finished.
+    state = runtime_fs.probe_exists(jp)[0]
+    if state == "absent":
         return ConfigRecovery.UNNECESSARY, ""
+    if state != "present":
+        return ConfigRecovery.BLOCKED, ""
     try:
         journal = runtime_fs.loads_json(runtime_fs.read_text(paths, jp))   # no-follow read
     except (OSError, ValueError, PathContainmentError):
diff --git a/lhpc/core/firewall_helper.py b/lhpc/core/firewall_helper.py
index 5a91969..8b0b4ea 100644
--- a/lhpc/core/firewall_helper.py
+++ b/lhpc/core/firewall_helper.py
@@ -996,7 +996,13 @@ def _read_json_state(path):
     callers report unverifiable."""
     text, err = read_bounded(path, MAX_CANDIDATE_BYTES)
     if err:
-        return ("absent", None) if not os.path.lexists(path) else ("present-invalid", None)
+        try:
+            os.lstat(path)
+        except (FileNotFoundError, NotADirectoryError):
+            return ("absent", None)      # ONLY ENOENT/ENOTDIR: EIO/EACCES are not "absent"
+        except (OSError, ValueError):
+            pass
+        return ("present-invalid", None)
     try:
         return ("valid", json.loads(text))
     except ValueError:
diff --git a/lhpc/core/install.py b/lhpc/core/install.py
index 53c77bd..f4983fb 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -18,6 +18,7 @@ import errno
 import os
 import re
 import shutil
+import stat
 import time
 from contextlib import contextmanager
 from dataclasses import dataclass, field
@@ -160,6 +161,25 @@ class Plan:
         return all(a.status != "failed" for a in self.actions)
 
 
+def _git_marker_anomaly(dest: Path) -> str | None:
+    """The `.git` of a checkout for the dirty/carry inventory: None = absent (not a checkout),
+    "" = present and reachable, else the anomaly. A `.git` that cannot be examined, or a symlink
+    that does not resolve (dangling, a loop), is never "absent" and never handed to git: git
+    would walk up to an enclosing repository and report on that one."""
+    from . import runtime_fs
+    state, why, st = runtime_fs.probe_stat(dest / ".git")
+    if state == "absent":
+        return None
+    if state == "present" and stat.S_ISLNK(st.st_mode):
+        state, why, _st = runtime_fs.probe_stat(dest / ".git", follow=True)
+        if state == "absent":
+            return ".git is a dangling symlink"
+        if state == "unknown":
+            return f".git is a symlink that cannot be followed ({why})"
+        return ""
+    return f"cannot examine .git ({why})" if state == "unknown" else ""
+
+
 class Installer:
     def __init__(self, paths: Paths, stacks: tuple[Stack, ...], config: Config,
                  system: System) -> None:
@@ -886,8 +906,11 @@ class Installer:
         not a git checkout reports clean here (ownership verification handles unknown trees).
         A FAILED git status reports the failure as a tracked entry — fail toward dirty, never
         silently clean."""
-        if not (dest / ".git").exists():
+        anomaly = _git_marker_anomaly(dest)
+        if anomaly is None:
             return DirtyReport()
+        if anomaly:
+            return DirtyReport(tracked=(f"({anomaly} — treating as dirty)",))
         # NUL-SAFE, ENTRY-EXACT status: `-z` terminates every path with NUL (no quoting, so
         # newline/quote-containing names parse exactly), and `--untracked-files=all`
         # enumerates every INDIVIDUAL untracked file — git never collapses a directory, so
@@ -946,8 +969,11 @@ class Installer:
 
         Regenerable artifacts are filtered by the SAME predicate `dirty_report` uses, so
         `build/`, `.run/` and a component's declared `bin` stay disposable in both."""
-        if not (dest / ".git").exists():
+        anomaly = _git_marker_anomaly(dest)
+        if anomaly is None:
             return ()
+        if anomaly:
+            return None
         r = self.system.runner.run(["git", "-C", str(dest), "ls-files", "-z", "--others"], 10.0)
         if r.returncode != 0:
             return None
diff --git a/lhpc/core/runtime_fs.py b/lhpc/core/runtime_fs.py
index 56cc01f..39e9ee2 100644
--- a/lhpc/core/runtime_fs.py
+++ b/lhpc/core/runtime_fs.py
@@ -28,6 +28,7 @@ import os
 import stat as _stat
 from contextlib import contextmanager
 from pathlib import Path
+from typing import Literal
 
 from .paths import PathContainmentError, Paths
 
@@ -45,6 +46,8 @@ __all__ = [
     "open_log_append",
     "open_log_truncate",
     "open_marker_excl",
+    "probe_exists",
+    "probe_stat",
     "read_bytes",
     "read_text",
     "rename_leaf",
@@ -730,6 +733,37 @@ def guard_state(paths: Paths, path: Path) -> str:
         return "unsafe"                  # escaped/swapped/unreadable parent — cannot prove absent
 
 
+def probe_exists(path) -> tuple[Literal["present", "absent", "unknown"], str]:
+    """THE existence probe for a safety decision (delete, overwrite, retire, "nothing to
+    recover"): `(state, why)`. No-follow `os.lstat`, so a dangling or escaping symlink is
+    present. Only ENOENT, or ENOTDIR on a parent, is "absent"; any other error (EIO, EACCES,
+    ELOOP, a NUL byte) is "unknown" with the error in `why` — not proven absent, so the caller
+    refuses or keeps. `os.path.lexists`/`exists`/`isfile` read every such error as absence.
+    Unlike `guard_state` it takes any path and reads a missing parent as absent."""
+    try:
+        os.lstat(path)
+    except (FileNotFoundError, NotADirectoryError):
+        return "absent", ""
+    except (OSError, ValueError) as exc:
+        return "unknown", str(exc)
+    return "present", ""
+
+
+def probe_stat(path, *, follow: bool = False):
+    """`probe_exists` plus the stat result it read: `(state, why, st)`, `st` None unless
+    present. A decision that follows the probe (is it a directory? a symlink?) takes the kind
+    from `st`, never from a second `os.path.isdir`/`islink`, which would read an error as "no".
+    `follow=True` stats the target (what `os.path.isdir` asked): a dangling link is absent,
+    a target that cannot be stat'ed is unknown."""
+    try:
+        st = os.stat(path) if follow else os.lstat(path)
+    except (FileNotFoundError, NotADirectoryError):
+        return "absent", "", None
+    except (OSError, ValueError) as exc:
+        return "unknown", str(exc), None
+    return "present", "", st
+
+
 def publish_symlink(paths: Paths, path: Path, target: str) -> None:
     """Atomically publish a SYMLINK leaf inside the runtime root, descriptor-anchored.
 
diff --git a/lhpc/core/service_binary_ops.py b/lhpc/core/service_binary_ops.py
index d2c3c40..7640930 100644
--- a/lhpc/core/service_binary_ops.py
+++ b/lhpc/core/service_binary_ops.py
@@ -16,6 +16,7 @@ from __future__ import annotations
 import os
 import secrets
 import shutil
+import stat
 import sys
 import tarfile
 import tempfile
@@ -609,12 +610,19 @@ class BinaryOpsMixin:
 
         Symlinks COUNT: a virtualenv is half symlinks (`bin/python3`), and owning only the
         regular files left them behind on removal — enough for `python3 -m venv` to treat the
-        environment as existing, skip ensurepip, and fail the next step."""
+        environment as existing, skip ensurepip, and fail the next step. Raises `OSError` when
+        the tree cannot be fully listed: an unread directory is not an empty one."""
         base = self._paths.under(*rel_dir.split("/"))
-        if not os.path.isdir(base):
+        state, why, st = runtime_fs.probe_stat(base, follow=True)   # what os.walk(base) lists
+        if state == "unknown":
+            raise OSError(why)
+        if state == "absent" or not stat.S_ISDIR(st.st_mode):
             return []
         out = []
-        for root, _dirs, names in os.walk(base):
+
+        def _unlistable(exc):
+            raise exc
+        for root, _dirs, names in os.walk(base, onerror=_unlistable):
             for n in names:
                 full = os.path.join(root, n)
                 if not os.path.isdir(full):          # regular file OR symlink
@@ -740,7 +748,10 @@ class BinaryOpsMixin:
         artifact published into it — it is not a checkout and not a foreign tree. Setting the
         artifact aside empties it (the retirement prunes it), so the ordinary adoption path
         clones there. Judging it as an unprovable checkout would refuse every first switch."""
-        return all(rel in owned for rel in self._rel_files_under(rel_dir))
+        try:
+            return all(rel in owned for rel in self._rel_files_under(rel_dir))
+        except OSError:
+            return False                   # not fully listable: not proven the artifact's own
 
     def switch_source_plan(self, groups, owned_files=()) -> tuple:
         """PRE-FLIGHT for a binary -> source switch: `(paths_to_replace, refusals)`.
@@ -839,16 +850,11 @@ class BinaryOpsMixin:
 
     def _receipt_leaf_present(self, rel: str) -> bool:
         """Anything at a receipt path, readable or not, counts as present; so does a path that
-        cannot even be resolved inside the runtime root (never "gone" on uncertainty). Only a
-        missing leaf or parent (ENOENT, ENOTDIR) is absent: `os.path.lexists` would also read
-        EIO or EACCES as absent."""
+        cannot even be resolved inside the runtime root (never "gone" on uncertainty)."""
         try:
-            os.lstat(self._paths.under(*rel.split("/")))
-        except (FileNotFoundError, NotADirectoryError):
-            return False
-        except (OSError, ValueError, PathContainmentError):
+            return runtime_fs.probe_exists(self._paths.under(*rel.split("/")))[0] != "absent"
+        except (ValueError, PathContainmentError):
             return True
-        return True
 
     def _retire_body(self, stack_id: str, state: str, rec, why: str, *, force: bool,
                      locked: bool, txn: str) -> ActionResult:
@@ -924,8 +930,7 @@ class BinaryOpsMixin:
                 failed.append(f"{rel} ({exc})")
         # PROVE removal before dropping the receipt: a swallowed unlink failure would leave
         # binary files behind with no ownership record at all.
-        still_there = [rel for rel in rec.files
-                       if os.path.exists(self._paths.under(*rel.split("/")))]
+        still_there = [rel for rel in rec.files if self._receipt_leaf_present(rel)]
         if still_there or failed:
             return ActionResult(
                 False,
@@ -941,8 +946,15 @@ class BinaryOpsMixin:
         for rel_dir in getattr(rec, "owned_dirs", ()):
             try:
                 d = self._paths.under(*rel_dir.split("/"))
-                if os.path.isdir(d) and not os.path.islink(d):
-                    shutil.rmtree(d)
+                # The kind comes from the probes' own stat results: an error is never "not a
+                # dir" (skip, receipt dropped) — not proven gone, the receipt keeps owning it.
+                state, why, lst = runtime_fs.probe_stat(d)
+                if state == "present" and not stat.S_ISLNK(lst.st_mode):
+                    state, why, st = runtime_fs.probe_stat(d, follow=True)
+                    if state == "present" and stat.S_ISDIR(st.st_mode):
+                        shutil.rmtree(d)
+                if state == "unknown":
+                    raise OSError(why)
             except (OSError, PathContainmentError, ValueError) as exc:
                 return ActionResult(
                     False, f"Retirement of '{stack_id}' is INCOMPLETE — the receipt was kept "
diff --git a/lhpc/core/service_boot_restore.py b/lhpc/core/service_boot_restore.py
index b960bba..609424d 100644
--- a/lhpc/core/service_boot_restore.py
+++ b/lhpc/core/service_boot_restore.py
@@ -72,7 +72,10 @@ class BootRestoreOpsMixin:
         rb_state, rb = "absent", ""
         marker = self._paths.under("state", "running", f"{stack_id}.band")
         try:
-            if os.path.lexists(marker):
+            state = runtime_fs.probe_exists(marker)[0]
+            if state == "unknown":              # cannot prove it absent: evidence unreadable
+                rb_state = "unsafe"
+            elif state == "present":
                 raw = runtime_fs.read_text(self._paths, marker, max_bytes=64).strip()
                 if raw in ALLOWED_BANDS:
                     rb_state, rb = "valid", raw
@@ -83,7 +86,10 @@ class BootRestoreOpsMixin:
         ls_state, ls_band, ls_at = "absent", "", 0.0
         cpath = known_working.candidate_path(self._paths, stack_id)
         try:
-            if os.path.lexists(cpath):
+            state = runtime_fs.probe_exists(cpath)[0]
+            if state == "unknown":              # cannot prove it absent: evidence unreadable
+                ls_state = "unsafe"
+            elif state == "present":
                 cand = known_working.read_candidate(self._paths, stack_id)
                 started = cand.get("started_at") if cand else None
                 if (cand is None or not isinstance(started, (int, float))
diff --git a/plans/PLAN-Q2.md b/plans/PLAN-Q2.md
new file mode 100644
index 0000000..ab69855
--- /dev/null
+++ b/plans/PLAN-Q2.md
@@ -0,0 +1,136 @@
+# PLAN-Q2 — `probe_exists()`: one fail-closed existence probe for every safety path
+
+Base: `integration/0.12.0` (c1d8bbd from round 5, ac1eadd in round 4; rounds 1–3 042716f). Files: `lhpc/core/runtime_fs.py` (helper), `install.py`,
+`service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (the journal
+probe and its two outcomes, in Q1's result type), `firewall_helper.py`, their tests, CHANGELOG,
+`docs/architecture.md` (the runtime-filesystem guarantee gains three sentences: a `.git`
+symlink that does not resolve is present, never clean and never handed to git; the presence
+decisions that use `probe_exists`/`probe_stat`, by site; and only ENOENT/ENOTDIR read as absent,
+with its tests, the firewall helper's inline rule and its test included).
+
+## The helper (runtime_fs.py, beside the other no-follow leaf helpers)
+
+`probe_exists(path) -> (state, why)`, `state` a `Literal["present", "absent", "unknown"]`, `why`
+the error text ("" unless unknown). `os.lstat` (no-follow: a dangling or escaping symlink is
+present); `absent` ONLY on `FileNotFoundError`/`NotADirectoryError`; any other `OSError` (EIO,
+EACCES, ELOOP, ENAMETOOLONG) or a `ValueError` (NUL byte — `os.path.lexists` reads that as
+absent too) is `unknown`. Plain function, one Literal + one message field; no class, no enum
+type. Guardrail check: it REMOVES duplication — `_receipt_leaf_present` (service_binary_ops.py:842)
+becomes a one-line use of it.
+
+`probe_stat(path, *, follow=False) -> (state, why, st)` (added in commit 3, beside it): the same
+rule plus the stat result it read (`st` None unless present); `follow=True` uses `os.stat` (a
+dangling link is absent). A decision that follows the probe takes the kind (directory, symlink)
+from `st`, never from a second `os.path.isdir`/`islink`, which reads an error as "no".
+
+## Inventory — `grep -nE 'lexists\(|\.exists\(|isfile\(|isdir\('` over the FILES
+
+REPLACE (safety: an OSError read as absence deletes, overwrites or proceeds):
+
+| site today | decision on a false "absent" | with `unknown` |
+|---|---|---|
+| install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …"); a `.git` symlink that does not resolve: dirty (named, see below) |
+| install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses); same for an unresolvable `.git` symlink |
+| service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | `probe_stat(follow=True)`: unknown raises → `_artifact_only_dir` False; is-dir from its `st` |
+| service_binary_ops.py:930 retire `still_there` exists | receipt dropped while files remain (unowned) | still present → INCOMPLETE, receipt kept |
+| service_binary_ops.py:946 owned dir isdir + islink | dir skipped, receipt dropped (unowned venv) | `probe_stat` (lstat: link?) then `probe_stat(follow=True)` (dir?): unknown in either → INCOMPLETE, receipt kept |
+| service_binary_ops.py:842 `_receipt_leaf_present` (already lstat) | — | same behaviour, uses the helper |
+| service_boot_restore.py:75 running-band marker lexists | `absent` evidence | `unsafe` |
+| service_boot_restore.py:86 last-start candidate lexists | `absent` evidence | `unsafe` |
+| boot_restore.py:141 `load_journal` lexists | `absent` → restore runs | `unsafe:unreadable (why)` |
+| config.py:1810 `recover_config_transaction` lexists | `UNNECESSARY` = "no journal" | `BLOCKED` (round 4, Q1's type; before Q1 `""` = recovery required) |
+| firewall_helper.py:999 `_read_json_state` lexists | journal `absent` → `recover()` True → apply proceeds | `present-invalid` (inline: stdlib-only module) |
+
+LEAVE (display/convenience, or an error already refuses/keeps):
+install.py:263/268 bootstrap `local.toml`/`secrets.toml` `not dest.exists()` — dropped after
+drafting because `Path.exists()` RAISES on EIO/EACCES on every supported Python (3.11–3.13), so
+`apply_bootstrap` already fails the action and no red test is possible; install.py:228/232
+plan status text; :291 doclink (`os.symlink` itself never clobbers); :2237-2260
+post-clone sanity (`False` = clone failed). service_binary_ops.py:622 (an unstat-able entry is
+listed as a file → not owned → not artifact-only: fails closed); :662 staging rmtree (error =
+keep); :705 build cwd; :719 CLI missing → error; :922 unlink guard (error = keep, then :930
+reports it). config.py:1001 web-session key persistence (error = ephemeral key, nothing written);
+config.py:1877 eager startup recovery (absent and unknown both leave it to `config_lock`, which
+already refuses on EIO — no observable difference, no red test possible). selfupdate.py:136
+(error = no repo root = self-update unavailable); service_selfupdate.py:40 log tail; :96/:257
+dependency rows; :1096 helper exe text; :1295 `.git` missing → refuses. firewall_helper.py:1390
+transition unlink (error = keep). service_firewall.py:344 log link, :412 units-enabled row.
+Out of the grep but scanned (`is_dir`/`is_file`/`is_symlink` in FILES): none decides a delete or
+an overwrite on absence (binary_ops:247/532 hand absence to the locked adoption, which proves it
+with `leaf_kind`).
+
+## Commits
+
+1. `Q2: plan`. 2. `Q2: probe_exists — the fail-closed existence probe` (helper, its unit test,
+`_receipt_leaf_present` on it). 3. `Q2: the controller's safety paths never read an
+unexaminable path as absent` (the REPLACE rows except `_receipt_leaf_present` and the firewall
+helper — 9 sites — plus `probe_stat`, one parametrised test, and one stat-only test per kind
+decision). 4. `Q2: the firewall helper reads an unexaminable journal as present` (inline
+lstat; CHANGELOG upgrade note). 5. report.
+
+## Tests
+
+`tests/core/test_existence_probe.py`: the helper (present / absent / dangling symlink present /
+ENOTDIR absent / EIO+EACCES unknown with errno text), and ONE parametrised test over the
+replaced sites × {EIO, EACCES}: an injector makes `os.lstat` AND `os.stat` raise for exactly the
+site's path (so the `exists`/`isfile` sites are red before too), drives the site through its
+public seam (`dirty_report`, `extra_files`, `binary_retire(force=True)`,
+`switch_source_plan`, `load_journal`, `recover_config_transaction`; `_boot_marker_view` as
+test_boot_restore.py already does for private views) and asserts the refusal/keep state AND that
+the file is still there. The two kind decisions (`_rel_files_under`, the owned-dir retirement)
+get one more test each with an injector that fails `os.stat` ONLY (lstat answers), so the
+`isdir` after a present probe is caught too. The dangling-`.git` change: `test_a_git_symlink_that_does_not_resolve_is_never_clean`
+(dangling, loop; exact `DirtyReport` and `None` asserted, no git call, against a fake git that
+answers like a clean enclosing repository) and `test_no_git_and_a_git_symlink_that_resolves_keep_their_reading`
+(no `.git` → `DirtyReport()` / `()` with no git call; a resolving `.git` symlink → git consulted). Firewall helper: one case in
+`tests/host/test_firewall.py` (`recover()` is False and the journal stays). Red-before: run the
+new tests against commit 2's tree.
+
+## Risks and how they are ruled out
+
+- Behaviour change without any I/O error, deliberate: a dangling `.git` symlink is treated
+  as present and the tree is not reported clean; before, it was ignored (`Path.exists()` follows
+  the link, so the old code read "not a checkout": `DirtyReport()` / `()`). Reproduced on the
+  base and on round 2's head (report, Correction 2). Why fail-closed: the inventory's question
+  is "may this tree be replaced or removed without losing the operator's work?", and a `.git`
+  that is there but leads nowhere does not answer it; it is the batch's own rule (not proven
+  absent → not absent). It is also not handed to git: given a `.git` it cannot use, git walks up
+  to the first enclosing checkout and reports on THAT one (round 2's head did exactly this:
+  dirty when no enclosing repository exists, but CLEAN / `()` under a clean enclosing one). So
+  `install._git_marker_anomaly` reads `.git` with `probe_stat` (lstat); a symlink is followed
+  (`probe_stat(follow=True)`): absent → ".git is a dangling symlink", unknown (ELOOP for a loop,
+  EIO, EACCES) → ".git is a symlink that cannot be followed (why)"; both make `dirty_report`
+  return `DirtyReport(tracked=("(<anomaly> — treating as dirty)",))` and `extra_files` `None`,
+  with no git call. Same for a `.git` symlink loop (old: `Path.exists()` reads ELOOP as False).
+  A `.git` symlink that resolves goes to git exactly as before. Call sites whose result changes
+  for such a `.git` (all now see "changed" / "no inventory"): install.py `_adopt_locked` (update
+  refused), `_stage_and_activate` (final dirty check blocks the archive; the carry refuses),
+  `_prev_dirty_scan` (archived prior retained), `_prev_extras` (`None`);
+  service_binary_ops.py `binary_install` (refused: local changes), `_stale_clean_clones` (not
+  listed as a clean clone), `switch_source_plan` (refusal); service_maintenance.py
+  `_remove_source_leaf` (uninstall refuses, both checks; Clean skips the check, unchanged);
+  service_auto_install.py `_reconcile_group` (blocked). Where an identity check
+  (`verify_identity`) runs first, it may already refuse; the dirty result changes either way.
+- Other sites without an I/O error: ENOENT/ENOTDIR keep "absent"; the boot-restore, config and
+  firewall sites used `lexists` (lstat) before, so a dangling symlink reads the same; retire's
+  `still_there` moved from `os.path.exists` (follows) to lstat — a dangling symlink at a receipt
+  path is "still present" now, but the unlink loop just before it unlinks any symlink
+  (`islink`), so the result differs only if one remains after that loop (an unlink that failed
+  is already INCOMPLETE). A symlink at an owned-dir path is still left alone (never `rmtree`d
+  through), as before. `_rel_files_under` follows like `os.path.isdir` did; a base it cannot
+  stat other than ENOENT/ENOTDIR (ELOOP included) now raises instead of listing `[]`, so a
+  symlink loop at a switch target is no longer judged the artifact's own leftover (a second change without an
+  I/O error, covered by the CHANGELOG's "does not take over a folder it cannot fully read").
+- Firewall helper bytes change → boxes with the firewall read *Update required* until the
+  operator re-applies (F36 makes the product say so). CHANGELOG carries the upgrade note.
+- Injector over-reach: it raises only for one exact path string, no `dir_fd` call is affected.
+- Q1 conflict: resolved in round 4 on Q1's `ConfigRecovery` — an absent journal is `UNNECESSARY`,
+  an unexaminable one `BLOCKED`. Line numbers in this plan are those of 042716f.
+
+## Open questions (with recommendation)
+
+1. Firewall helper change forces an operator re-apply on upgrade. Recommendation: ship it (the
+   journal is the one firewall state whose false "absent" lets an apply run over an interrupted
+   one); it is its own commit, so it can be held back to a release that changes the helper anyway.
+2. `os.walk` error swallowing in `_rel_files_under` is the same class one line below the isdir;
+   fixed in the same site (onerror raises). Recommendation: keep.
diff --git a/tests/core/test_existence_probe.py b/tests/core/test_existence_probe.py
new file mode 100644
index 0000000..9995d12
--- /dev/null
+++ b/tests/core/test_existence_probe.py
@@ -0,0 +1,336 @@
+"""The fail-closed existence probe: only ENOENT/ENOTDIR prove a path absent.
+
+`os.path.lexists`/`exists`/`isfile` return False on ANY OSError, so an unreadable leaf read as
+"gone" and a retire/clean/recover path proceeded. `runtime_fs.probe_exists` is the one probe a
+safety decision uses; an error it cannot read past is "unknown", never "absent"."""
+
+import errno
+import json
+import os
+import stat
+
+import pytest
+
+from lhpc.core import binary_receipt as brx, boot_restore, config as cfgmod, known_working, runtime_fs
+from lhpc.core.config import Config
+from lhpc.core.install import Installer
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import FakeSystem
+from lhpc.core.services import ControllerService
+
+
+def _failing(monkeypatch, path, err):
+    """Make `os.lstat` AND `os.stat` raise `err` for exactly `path` (by string), so a site that
+    used `exists`/`isfile` (stat) is caught as well as one that used `lexists` (lstat)."""
+    target = os.fspath(path)
+    for name in ("lstat", "stat"):
+        real = getattr(os, name)
+
+        def fake(p, *a, _real=real, **kw):
+            if "dir_fd" not in kw and isinstance(p, (str, os.PathLike)) and os.fspath(p) == target:
+                raise OSError(err, os.strerror(err), target)
+            return _real(p, *a, **kw)
+        monkeypatch.setattr(os, name, fake)
+
+
+@pytest.mark.safety("existence-fail-closed")
+def test_present_absent_and_a_missing_parent(tmp_path):
+    leaf = tmp_path / "leaf"
+    leaf.write_text("x")
+    assert runtime_fs.probe_exists(leaf) == ("present", "")
+    assert runtime_fs.probe_exists(tmp_path / "gone") == ("absent", "")
+    assert runtime_fs.probe_exists(tmp_path / "gone" / "deeper") == ("absent", "")   # ENOENT parent
+    assert runtime_fs.probe_exists(leaf / "below-a-file") == ("absent", "")           # ENOTDIR parent
+
+
+@pytest.mark.safety("existence-fail-closed")
+def test_a_dangling_symlink_is_present(tmp_path):
+    link = tmp_path / "link"
+    link.symlink_to(tmp_path / "nowhere")
+    assert runtime_fs.probe_exists(link) == ("present", "")
+
+
+@pytest.mark.safety("existence-fail-closed")
+@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES, errno.ELOOP], ids=["EIO", "EACCES", "ELOOP"])
+def test_any_other_error_is_unknown_with_the_error(tmp_path, monkeypatch, err):
+    leaf = tmp_path / "leaf"
+    leaf.write_text("x")
+    _failing(monkeypatch, leaf, err)
+    state, why = runtime_fs.probe_exists(leaf)
+    assert state == "unknown"
+    assert os.strerror(err) in why
+
+
+@pytest.mark.safety("existence-fail-closed")
+def test_an_unprobeable_path_is_unknown(tmp_path):
+    state, why = runtime_fs.probe_exists(str(tmp_path) + "/a\0b")
+    assert state == "unknown" and why
+
+
+# ---- every safety site: an unexaminable path is never read as absent ------------------------
+#
+# Each case lays down the state the site must protect, makes `os.lstat`/`os.stat` fail for
+# exactly that path, drives the site through its seam and asserts the refusal/keep AND that the
+# protected file is still there.
+
+def _svc(tmp_path, monkeypatch):
+    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
+    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+
+
+def _installer(tmp_path):
+    return Installer(Paths(runtime_root=tmp_path), (), Config(values={}), FakeSystem().system)
+
+
+def _git_tree(tmp_path):
+    dest = tmp_path / "src" / "tree"
+    (dest / ".git").mkdir(parents=True)
+    (dest / "added.txt").write_text("operator file")
+    return dest
+
+
+def _dirty_report_is_dirty(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    dest = _git_tree(tmp_path)
+    fail(dest / ".git")
+    report = _installer(tmp_path).dirty_report(dest, "src/tree")
+    assert report.blocks_update() and bool(report)
+
+
+def _extra_files_cannot_be_taken(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    dest = _git_tree(tmp_path)
+    fail(dest / ".git")
+    assert _installer(tmp_path).extra_files(dest, "src/tree") is None
+
+
+def _switch_does_not_take_an_unlisted_tree(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    svc = _svc(tmp_path, monkeypatch)
+    comp = next(c for st in svc.stacks() for c in st.components if c.id == "loraham-daemon")
+    dest = tmp_path / comp.source.path
+    dest.mkdir(parents=True)
+    (dest / "artifact.bin").write_bytes(b"ELF")
+    owned = (f"{comp.source.path}/artifact.bin",)
+    assert svc.switch_source_plan([(comp.source.path, comp, "pinned", ("", ""))], owned) == (set(), [])
+    fail(dest)
+    _replace, refusals = svc.switch_source_plan([(comp.source.path, comp, "pinned", ("", ""))], owned)
+    assert [r.split(":", 1)[0] for r in refusals] == [comp.source.path]
+
+
+def _retire_keeps_the_receipt_for_a_file(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    svc = _svc(tmp_path, monkeypatch)
+    rec = binary_receipt(svc)
+    victim = tmp_path / rec.files[0]
+    fail(victim)
+    res = svc.binary_retire("daemon", force=True)
+    assert not res.ok and f"  still present: {rec.files[0]}" in res.details
+    restore()
+    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid" and victim.read_bytes() == b"ELF"
+
+
+def _retire_keeps_the_receipt_for_an_owned_dir(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    import dataclasses
+    svc = _svc(tmp_path, monkeypatch)
+    rec = binary_receipt(svc)
+    venv = tmp_path / "build" / "tools" / "meshtastic-cli"
+    (venv / "bin").mkdir(parents=True)
+    (venv / "bin" / "python3").write_bytes(b"x")
+    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, owned_dirs=("build/tools/meshtastic-cli",)))
+    svc.invalidate_snapshot()
+    fail(venv)
+    res = svc.binary_retire("daemon")
+    assert not res.ok and "build/tools/meshtastic-cli stays owned" in res.summary
+    restore()
+    assert brx.receipt_path(svc._paths, "daemon").exists()     # the dir stays owned
+    assert (venv / "bin" / "python3").read_bytes() == b"x"
+
+
+def _boot_marker_unsafe(which):
+    def case(tmp_path, monkeypatch, fail, restore, binary_receipt):
+        svc = _svc(tmp_path, monkeypatch)
+        if which == "running-band":
+            leaf = tmp_path / "state" / "running" / "kiss.band"
+            leaf.parent.mkdir(parents=True)
+            leaf.write_text("433")
+        else:
+            leaf = known_working.candidate_path(svc._paths, "kiss")
+            leaf.parent.mkdir(parents=True)
+            leaf.write_text(json.dumps({"band": "433", "started_at": 1.0}))
+        fail(leaf)
+        view = svc._boot_marker_view("kiss")
+        state = view.running_band_state if which == "running-band" else view.last_start_state
+        assert state == "unsafe"
+    return case
+
+
+def _boot_journal_unsafe(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    paths = Paths(runtime_root=tmp_path)
+    jp = boot_restore.journal_path(paths)
+    jp.parent.mkdir(parents=True, exist_ok=True)
+    jp.write_text("{}")
+    err = fail(jp)
+    assert boot_restore.load_journal(paths) == (None, f"unsafe:unreadable ({err})")
+
+
+def _config_journal_blocks(tmp_path, monkeypatch, fail, restore, binary_receipt):
+    paths = Paths(runtime_root=tmp_path)
+    jp = cfgmod._txn_journal(paths)
+    jp.parent.mkdir(parents=True, exist_ok=True)
+    jp.write_text("{}")
+    fail(jp)
+    assert cfgmod.recover_config_transaction(paths) == (cfgmod.ConfigRecovery.BLOCKED, "")
+    restore()
+    assert jp.read_text() == "{}"
+
+
+SITES = {
+    "dirty-report": _dirty_report_is_dirty,
+    "extra-files": _extra_files_cannot_be_taken,
+    "switch-artifact-only": _switch_does_not_take_an_unlisted_tree,
+    "retire-still-present": _retire_keeps_the_receipt_for_a_file,
+    "retire-owned-dir": _retire_keeps_the_receipt_for_an_owned_dir,
+    "boot-running-band": _boot_marker_unsafe("running-band"),
+    "boot-last-start": _boot_marker_unsafe("last-start"),
+    "boot-journal": _boot_journal_unsafe,
+    "config-journal": _config_journal_blocks,
+}
+
+
+@pytest.mark.safety("existence-fail-closed")
+@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
+@pytest.mark.parametrize("site", sorted(SITES))
+def test_an_unexaminable_path_is_never_absent(tmp_path, monkeypatch, binary_receipt, site, err):
+    injected = pytest.MonkeyPatch()        # undone on its own: the case's other patches stay
+
+    def fail(path):
+        _failing(injected, path, err)
+        return OSError(err, os.strerror(err), os.fspath(path))
+    try:
+        SITES[site](tmp_path, monkeypatch, fail, injected.undo, binary_receipt)
+    finally:
+        injected.undo()
+
+
+# ---- the kind decision after the probe: lstat answers, stat fails ---------------------------
+#
+# `os.path.isdir` after a `present` probe read a stat error as "not a directory": the listing
+# came back `[]` (so `all([])` judged the tree artifact-only) and retirement skipped the owned
+# dir and dropped its receipt. The kind now comes from the probe's own stat result.
+
+def _stat_failing(monkeypatch, path, err):
+    """Make ONLY `os.stat` raise `err` for exactly `path`: `os.lstat` still answers."""
+    target, real = os.fspath(path), os.stat
+
+    def fake(p, *a, **kw):
+        if "dir_fd" not in kw and isinstance(p, (str, os.PathLike)) and os.fspath(p) == target:
+            raise OSError(err, os.strerror(err), target)
+        return real(p, *a, **kw)
+    monkeypatch.setattr(os, "stat", fake)
+
+
+@pytest.mark.safety("existence-fail-closed")
+def test_probe_stat_returns_the_kind_and_follows_on_request(tmp_path, monkeypatch):
+    d = tmp_path / "d"
+    d.mkdir()
+    link = tmp_path / "link"
+    link.symlink_to(d)
+    state, why, st = runtime_fs.probe_stat(link)
+    assert (state, why) == ("present", "") and stat.S_ISLNK(st.st_mode)
+    state, why, st = runtime_fs.probe_stat(link, follow=True)
+    assert (state, why) == ("present", "") and stat.S_ISDIR(st.st_mode)
+    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
+    assert runtime_fs.probe_stat(tmp_path / "dangling", follow=True) == ("absent", "", None)
+    _stat_failing(monkeypatch, d, errno.EIO)
+    state, why, st = runtime_fs.probe_stat(d, follow=True)
+    assert state == "unknown" and os.strerror(errno.EIO) in why and st is None
+    assert runtime_fs.probe_stat(d)[0] == "present"
+
+
+@pytest.mark.safety("existence-fail-closed")
+@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
+def test_a_tree_whose_stat_fails_is_not_listed_as_empty(tmp_path, monkeypatch, err):
+    svc = _svc(tmp_path, monkeypatch)
+    comp = next(c for st in svc.stacks() for c in st.components if c.id == "loraham-daemon")
+    dest = tmp_path / comp.source.path
+    dest.mkdir(parents=True)
+    (dest / "artifact.bin").write_bytes(b"ELF")
+    owned = (f"{comp.source.path}/artifact.bin",)
+    _stat_failing(monkeypatch, dest, err)
+    assert runtime_fs.probe_exists(dest) == ("present", "")       # lstat answers
+    with pytest.raises(OSError, match=os.strerror(err)):
+        svc._rel_files_under(comp.source.path)
+    _replace, refusals = svc.switch_source_plan([(comp.source.path, comp, "pinned", ("", ""))], owned)
+    assert [r.split(":", 1)[0] for r in refusals] == [comp.source.path]
+    assert (dest / "artifact.bin").read_bytes() == b"ELF"
+
+
+@pytest.mark.safety("existence-fail-closed")
+@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
+def test_retire_keeps_an_owned_dir_whose_stat_fails(tmp_path, monkeypatch, binary_receipt, err):
+    import dataclasses
+    svc = _svc(tmp_path, monkeypatch)
+    rec = binary_receipt(svc)
+    venv = tmp_path / "build" / "tools" / "meshtastic-cli"
+    (venv / "bin").mkdir(parents=True)
+    (venv / "bin" / "python3").write_bytes(b"x")
+    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, owned_dirs=("build/tools/meshtastic-cli",)))
+    svc.invalidate_snapshot()
+    injected = pytest.MonkeyPatch()
+    try:
+        _stat_failing(injected, venv, err)
+        res = svc.binary_retire("daemon")
+    finally:
+        injected.undo()
+    assert not res.ok and "build/tools/meshtastic-cli stays owned" in res.summary
+    assert res.details == [f"  could not remove build/tools/meshtastic-cli "
+                           f"([Errno {err}] {os.strerror(err)}: '{venv}')"]
+    assert brx.receipt_path(svc._paths, "daemon").exists()     # the dir stays owned
+    assert (venv / "bin" / "python3").read_bytes() == b"x"
+
+
+# ---- a `.git` that is a symlink: resolved or not --------------------------------------------
+#
+# The fake answers `git status`/`ls-files` as a CLEAN enclosing repository would: real git,
+# given a `.git` it cannot use, walks up to the first enclosing checkout and reports on that.
+
+def _git_answers_clean(dest):
+    from lhpc.core.probes.backends import CommandResult
+    ok = CommandResult(returncode=0, stdout="", stderr="")
+    return FakeSystem(commands={
+        ("git", "-C", str(dest), "status", "--porcelain", "-z", "--untracked-files=all"): ok,
+        ("git", "-C", str(dest), "ls-files", "-z", "--others"): ok})
+
+
+@pytest.mark.safety("existence-fail-closed")
+@pytest.mark.parametrize("kind", ["dangling", "loop"])
+def test_a_git_symlink_that_does_not_resolve_is_never_clean(tmp_path, kind):
+    from lhpc.core.install import DirtyReport
+    dest = tmp_path / "src" / "tree"
+    dest.mkdir(parents=True)
+    (dest / "added.txt").write_text("operator file")
+    (dest / ".git").symlink_to(tmp_path / "nowhere" if kind == "dangling" else dest / ".git")
+    fake = _git_answers_clean(dest)
+    inst = Installer(Paths(runtime_root=tmp_path), (), Config(values={}), fake.system)
+    anomaly = {"dangling": ".git is a dangling symlink",
+               "loop": (f".git is a symlink that cannot be followed ([Errno {errno.ELOOP}] "
+                        f"{os.strerror(errno.ELOOP)}: '{dest / '.git'}')")}[kind]
+    assert inst.dirty_report(dest, "src/tree") == DirtyReport(tracked=(f"({anomaly} — treating as dirty)",))
+    assert inst.extra_files(dest, "src/tree") is None
+    assert fake.calls == []                                   # never handed to git
+    assert (dest / ".git").is_symlink() and (dest / "added.txt").read_text() == "operator file"
+
+
+@pytest.mark.safety("existence-fail-closed")
+def test_no_git_and_a_git_symlink_that_resolves_keep_their_reading(tmp_path):
+    from lhpc.core.install import DirtyReport
+    dest = tmp_path / "src" / "tree"
+    dest.mkdir(parents=True)
+    (dest / "added.txt").write_text("operator file")
+    fake = _git_answers_clean(dest)
+    inst = Installer(Paths(runtime_root=tmp_path), (), Config(values={}), fake.system)
+    assert inst.dirty_report(dest, "src/tree") == DirtyReport()           # not a checkout: clean
+    assert inst.extra_files(dest, "src/tree") == ()
+    assert fake.calls == []
+    (tmp_path / "gitdir").mkdir()
+    (dest / ".git").symlink_to(tmp_path / "gitdir")
+    assert inst.dirty_report(dest, "src/tree") == DirtyReport()           # git consulted
+    assert inst.extra_files(dest, "src/tree") == ()
+    assert [c[3] for c in fake.calls] == ["status", "ls-files"]
diff --git a/tests/host/test_firewall.py b/tests/host/test_firewall.py
index 4345d4b..73507e2 100644
--- a/tests/host/test_firewall.py
+++ b/tests/host/test_firewall.py
@@ -9,6 +9,8 @@ controller can compare saved intent against a receipt's `intent_hash` without am
 from __future__ import annotations
 
 import copy
+import errno
+import os
 
 import pytest
 
@@ -695,6 +697,31 @@ def test_corrupt_journal_fails_closed(tmp_path):
     assert fh.recover(sysx, p) is True
 
 
+@pytest.mark.safety("firewall-fail-closed")
+@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES], ids=["EIO", "EACCES"])
+def test_a_journal_that_cannot_be_examined_is_not_absent(tmp_path, monkeypatch, err):
+    # Only ENOENT/ENOTDIR prove "no interrupted op": a journal the helper can neither open nor
+    # lstat (EIO, EACCES) fails recovery closed and stays where it is.
+    from lhpc.core import firewall_helper as fh
+    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
+    _seed_meta(etc)
+    p = fh._paths(etc, run)
+    fh.atomic_write(p["journal"], '{"op": "apply", "phase": "begin"}', 0o600)
+    real_open, real_lstat = os.open, os.lstat
+
+    def failing(real):
+        def fake(path, *a, **kw):
+            if path == p["journal"]:
+                raise OSError(err, os.strerror(err), path)
+            return real(path, *a, **kw)
+        return fake
+    monkeypatch.setattr(os, "open", failing(real_open))
+    monkeypatch.setattr(os, "lstat", failing(real_lstat))
+    assert fh.recover(_FakeSys(), p) is False
+    monkeypatch.undo()
+    assert os.path.exists(p["journal"])
+
+
 def test_reset_removes_only_when_owned(tmp_path):
     import json as _json
     from lhpc.core import firewall_helper as fh
```

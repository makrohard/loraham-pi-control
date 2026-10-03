# Gate 1 — code review request, batch Q2 (round 2)

Please judge the plan and the three code commits below for batch Q2, round 2. The batch adds one fail-closed existence probe (`probe_exists`, plus `probe_stat` where the kind of the path decides) and makes the listed safety decisions use it, so that a disk or permission error (EIO, EACCES) is never read as "the file is absent" or "not a directory". Round 1 was RED with two findings; the section right below says what changed. Check each commit for: correctness; whether any safety path in the diff still reads an OSError as absence or as "not a directory"; whether behaviour changes anywhere outside that; whether the tests are red before the change and green after it; whether the plan, the CHANGELOG and the docs match the diff exactly.

Answer form, one row per commit, then a final line:

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| 97e5057 plan | | |
| d788bbe | | |
| 3f5b102 | | |
| 7bcccee | | |

Final line: GREEN / GREEN WITH NOTES / RED

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Round 1 RED → round 2

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

### The round-2 change itself (round-1 head 38e2289 → round-2 code, without code-review/)

```diff
diff --git a/docs/architecture.md b/docs/architecture.md
index d50c443..be4bdb6 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -204,8 +204,11 @@ The guarantees the controller gives, each with where it is implemented and prove
   `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write; atomic
   writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
-  and caught at every boundary. `tests/core/test_runtime_fs.py`. An existence check that decides
-  a delete, a retirement or "nothing to recover" goes through `runtime_fs.probe_exists`: only
+  and caught at every boundary. `tests/core/test_runtime_fs.py`. These presence decisions use
+  `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory, symlink) decides: binary
+  retire's check of each receipt file and owned folder, the binary->source switch's listing of a
+  tree, a checkout's dirty/carry inventory (`.git`), the boot-restore markers and journal, and
+  the config journal recovery. In each, only
   ENOENT/ENOTDIR read as absent, any other error refuses or keeps; the stdlib-only firewall
   helper applies the same rule inline (`tests/core/test_existence_probe.py`).
 - **Source transactions.** An update clones a candidate beside the destination (recorded before
diff --git a/lhpc/core/runtime_fs.py b/lhpc/core/runtime_fs.py
index ac1b5b3..39e9ee2 100644
--- a/lhpc/core/runtime_fs.py
+++ b/lhpc/core/runtime_fs.py
@@ -47,6 +47,7 @@ __all__ = [
     "open_log_truncate",
     "open_marker_excl",
     "probe_exists",
+    "probe_stat",
     "read_bytes",
     "read_text",
     "rename_leaf",
@@ -748,6 +749,21 @@ def probe_exists(path) -> tuple[Literal["present", "absent", "unknown"], str]:
     return "present", ""
 
 
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
index 7f7b53c..c2e01ee 100644
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
@@ -614,10 +615,10 @@ class BinaryOpsMixin:
         environment as existing, skip ensurepip, and fail the next step. Raises `OSError` when
         the tree cannot be fully listed: an unread directory is not an empty one."""
         base = self._paths.under(*rel_dir.split("/"))
-        state, why = runtime_fs.probe_exists(base)
+        state, why, st = runtime_fs.probe_stat(base, follow=True)   # what os.walk(base) lists
         if state == "unknown":
             raise OSError(why)
-        if state == "absent" or not os.path.isdir(base):
+        if state == "absent" or not stat.S_ISDIR(st.st_mode):
             return []
         out = []
 
@@ -947,11 +948,15 @@ class BinaryOpsMixin:
         for rel_dir in getattr(rec, "owned_dirs", ()):
             try:
                 d = self._paths.under(*rel_dir.split("/"))
-                state, why = runtime_fs.probe_exists(d)
-                if state == "unknown":      # not proven gone: the receipt must keep owning it
+                # The kind comes from the probes' own stat results: an error is never "not a
+                # dir" (skip, receipt dropped) — not proven gone, the receipt keeps owning it.
+                state, why, lst = runtime_fs.probe_stat(d)
+                if state == "present" and not stat.S_ISLNK(lst.st_mode):
+                    state, why, st = runtime_fs.probe_stat(d, follow=True)
+                    if state == "present" and stat.S_ISDIR(st.st_mode):
+                        shutil.rmtree(d)
+                if state == "unknown":
                     raise OSError(why)
-                if os.path.isdir(d) and not os.path.islink(d):
-                    shutil.rmtree(d)
             except (OSError, PathContainmentError, ValueError) as exc:
                 return ActionResult(
                     False, f"Retirement of '{stack_id}' is INCOMPLETE — the receipt was kept "
diff --git a/plans/PLAN-Q2.md b/plans/PLAN-Q2.md
index 7e4c3a6..1b6189a 100644
--- a/plans/PLAN-Q2.md
+++ b/plans/PLAN-Q2.md
@@ -15,19 +15,22 @@ absent too) is `unknown`. Plain function, one Literal + one message field; no cl
 type. Guardrail check: it REMOVES duplication — `_receipt_leaf_present` (service_binary_ops.py:842)
 becomes a one-line use of it.
 
+`probe_stat(path, *, follow=False) -> (state, why, st)` (added in commit 3, beside it): the same
+rule plus the stat result it read (`st` None unless present); `follow=True` uses `os.stat` (a
+dangling link is absent). A decision that follows the probe takes the kind (directory, symlink)
+from `st`, never from a second `os.path.isdir`/`islink`, which reads an error as "no".
+
 ## Inventory — `grep -nE 'lexists\(|\.exists\(|isfile\(|isdir\('` over the FILES
 
 REPLACE (safety: an OSError read as absence deletes, overwrites or proceeds):
 
 | site today | decision on a false "absent" | with `unknown` |
 |---|---|---|
-| install.py:263 `local.toml` `not dest.exists()` | bootstrap OVERWRITES the operator config | action `failed`, why |
-| install.py:268 `secrets.toml` same | overwrites the secrets | action `failed`, why |
 | install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …") |
 | install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses) |
-| service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | raises → `_artifact_only_dir` False |
+| service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | `probe_stat(follow=True)`: unknown raises → `_artifact_only_dir` False; is-dir from its `st` |
 | service_binary_ops.py:930 retire `still_there` exists | receipt dropped while files remain (unowned) | still present → INCOMPLETE, receipt kept |
-| service_binary_ops.py:946 owned dir isdir | dir skipped, receipt dropped (unowned venv) | INCOMPLETE, receipt kept |
+| service_binary_ops.py:946 owned dir isdir + islink | dir skipped, receipt dropped (unowned venv) | `probe_stat` (lstat: link?) then `probe_stat(follow=True)` (dir?): unknown in either → INCOMPLETE, receipt kept |
 | service_binary_ops.py:842 `_receipt_leaf_present` (already lstat) | — | same behaviour, uses the helper |
 | service_boot_restore.py:75 running-band marker lexists | `absent` evidence | `unsafe` |
 | service_boot_restore.py:86 last-start candidate lexists | `absent` evidence | `unsafe` |
@@ -36,7 +39,10 @@ REPLACE (safety: an OSError read as absence deletes, overwrites or proceeds):
 | firewall_helper.py:999 `_read_json_state` lexists | journal `absent` → `recover()` True → apply proceeds | `present-invalid` (inline: stdlib-only module) |
 
 LEAVE (display/convenience, or an error already refuses/keeps):
-install.py:228/232 plan status text; :291 doclink (`os.symlink` itself never clobbers); :2237-2260
+install.py:263/268 bootstrap `local.toml`/`secrets.toml` `not dest.exists()` — dropped after
+drafting because `Path.exists()` RAISES on EIO/EACCES on every supported Python (3.11–3.13), so
+`apply_bootstrap` already fails the action and no red test is possible; install.py:228/232
+plan status text; :291 doclink (`os.symlink` itself never clobbers); :2237-2260
 post-clone sanity (`False` = clone failed). service_binary_ops.py:622 (an unstat-able entry is
 listed as a file → not owned → not artifact-only: fails closed); :662 staging rmtree (error =
 keep); :705 build cwd; :719 CLI missing → error; :922 unlink guard (error = keep, then :930
@@ -53,9 +59,11 @@ with `leaf_kind`).
 ## Commits
 
 1. `Q2: plan`. 2. `Q2: probe_exists — the fail-closed existence probe` (helper, its unit test,
-`_receipt_leaf_present` on it). 3. `Q2: the controller's safety paths never read an unreadable
-path as absent` (the 11 controller sites + one parametrised test). 4. `Q2: the firewall helper
-reads an unexaminable journal as present` (inline lstat; CHANGELOG upgrade note). 5. report.
+`_receipt_leaf_present` on it). 3. `Q2: the controller's safety paths never read an
+unexaminable path as absent` (the REPLACE rows except `_receipt_leaf_present` and the firewall
+helper — 9 sites — plus `probe_stat`, one parametrised test, and one stat-only test per kind
+decision). 4. `Q2: the firewall helper reads an unexaminable journal as present` (inline
+lstat; CHANGELOG upgrade note). 5. report.
 
 ## Tests
 
@@ -63,16 +71,19 @@ reads an unexaminable journal as present` (inline lstat; CHANGELOG upgrade note)
 ENOTDIR absent / EIO+EACCES unknown with errno text), and ONE parametrised test over the
 replaced sites × {EIO, EACCES}: an injector makes `os.lstat` AND `os.stat` raise for exactly the
 site's path (so the `exists`/`isfile` sites are red before too), drives the site through its
-public seam (`apply_bootstrap`, `dirty_report`, `extra_files`, `binary_retire(force=True)`,
+public seam (`dirty_report`, `extra_files`, `binary_retire(force=True)`,
 `switch_source_plan`, `load_journal`, `recover_config_transaction`; `_boot_marker_view` as
 test_boot_restore.py already does for private views) and asserts the refusal/keep state AND that
-the file is still there. Firewall helper: one case in `tests/host/test_firewall.py` (`recover()`
-is False and the journal stays). Red-before: run the new tests against commit 2's tree.
+the file is still there. The two kind decisions (`_rel_files_under`, the owned-dir retirement)
+get one more test each with an injector that fails `os.stat` ONLY (lstat answers), so the
+`isdir` after a present probe is caught too. Firewall helper: one case in
+`tests/host/test_firewall.py` (`recover()` is False and the journal stays). Red-before: run the
+new tests against commit 2's tree.
 
 ## Risks and how they are ruled out
 
-- Behaviour change on healthy disks: none — ENOENT/ENOTDIR keep "absent"; only a dangling
-  symlink at local.toml/secrets.toml is now kept (was replaced) — the safer reading; noted.
+- Behaviour change on healthy disks: none — ENOENT/ENOTDIR keep "absent"; a symlink at an
+  owned-dir path is still left alone (never `rmtree`d through), as before.
 - Firewall helper bytes change → boxes with the firewall read *Update required* until the
   operator re-applies (F36 makes the product say so). CHANGELOG carries the upgrade note.
 - Injector over-reach: it raises only for one exact path string, no `dir_fd` call is affected.
diff --git a/tests/core/test_existence_probe.py b/tests/core/test_existence_probe.py
index 2db04ef..5df3b73 100644
--- a/tests/core/test_existence_probe.py
+++ b/tests/core/test_existence_probe.py
@@ -7,6 +7,7 @@ safety decision uses; an error it cannot read past is "unknown", never "absent".
 import errno
 import json
 import os
+import stat
 
 import pytest
 
@@ -206,3 +207,80 @@ def test_an_unexaminable_path_is_never_absent(tmp_path, monkeypatch, binary_rece
         SITES[site](tmp_path, monkeypatch, fail, injected.undo, binary_receipt)
     finally:
         injected.undo()
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
```

## Commits

- 97e5057 Q2: plan
- d788bbe Q2: probe_exists — the fail-closed existence probe
- 3f5b102 Q2: the controller's safety paths never read an unexaminable path as absent
- 7bcccee Q2: the firewall helper reads an unexaminable journal as present

## The plan (plans/PLAN-Q2.md)

# PLAN-Q2 — `probe_exists()`: one fail-closed existence probe for every safety path

Base: `integration/1.0` (042716f). Files: `lhpc/core/runtime_fs.py` (helper), `install.py`,
`service_binary_ops.py`, `service_boot_restore.py`, `boot_restore.py`, `config.py` (two probe
lines only — Q1 owns the recovery result type), `firewall_helper.py`, their tests, CHANGELOG,
`docs/architecture.md` (one sentence).

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
| install.py:889 `dirty_report` `.git` exists | reports CLEAN → uninstall/clean/update proceed | dirty ("cannot examine …") |
| install.py:949 `extra_files` `.git` exists | `()` → update archives the prior with no carry | `None` (caller refuses) |
| service_binary_ops.py:616 `_rel_files_under` isdir (+ `os.walk` swallowing errors) | `[]` → `_artifact_only_dir` True → foreign dir treated as the artifact's leftover | `probe_stat(follow=True)`: unknown raises → `_artifact_only_dir` False; is-dir from its `st` |
| service_binary_ops.py:930 retire `still_there` exists | receipt dropped while files remain (unowned) | still present → INCOMPLETE, receipt kept |
| service_binary_ops.py:946 owned dir isdir + islink | dir skipped, receipt dropped (unowned venv) | `probe_stat` (lstat: link?) then `probe_stat(follow=True)` (dir?): unknown in either → INCOMPLETE, receipt kept |
| service_binary_ops.py:842 `_receipt_leaf_present` (already lstat) | — | same behaviour, uses the helper |
| service_boot_restore.py:75 running-band marker lexists | `absent` evidence | `unsafe` |
| service_boot_restore.py:86 last-start candidate lexists | `absent` evidence | `unsafe` |
| boot_restore.py:141 `load_journal` lexists | `absent` → restore runs | `unsafe:unreadable (why)` |
| config.py:1810 `recover_config_transaction` lexists | `None` = "no journal" | `""` = recovery required |
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
`isdir` after a present probe is caught too. Firewall helper: one case in
`tests/host/test_firewall.py` (`recover()` is False and the journal stays). Red-before: run the
new tests against commit 2's tree.

## Risks and how they are ruled out

- Behaviour change on healthy disks: none — ENOENT/ENOTDIR keep "absent"; a symlink at an
  owned-dir path is still left alone (never `rmtree`d through), as before.
- Firewall helper bytes change → boxes with the firewall read *Update required* until the
  operator re-applies (F36 makes the product say so). CHANGELOG carries the upgrade note.
- Injector over-reach: it raises only for one exact path string, no `dir_fd` call is affected.
- Q1 conflict: config.py touched on exactly one line in `recover_config_transaction`.

## Open questions (with recommendation)

1. Firewall helper change forces an operator re-apply on upgrade. Recommendation: ship it (the
   journal is the one firewall state whose false "absent" lets an apply run over an interrupted
   one); it is its own commit, so it can be held back to a release that changes the helper anyway.
2. `os.walk` error swallowing in `_rel_files_under` is the same class one line below the isdir;
   fixed in the same site (onerror raises). Recommendation: keep.

## The report (code-review/code-report-Q2.md)

# Code report — Q2: `probe_exists()`, one fail-closed existence probe

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

Base `integration/1.0` (042716f). Branch commits: `6753747` plan, `c6cdaac`, `075519e`, `f8badb9`
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

`lhpc/`: +71 −25 (net +46). runtime_fs 958→976 (helper +16 with docstring), service_binary_ops 988→995
(`_receipt_leaf_present` 5 lines shorter), install 2323→2329, service_boot_restore 645→651, boot_restore 338→340,
config 2151→2152, firewall_helper 1479→1485. The code grows because each site now has an explicit `unknown`
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

## Full diff of the code commits (97e5057..7bcccee)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 2d217a3..5ba074d 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,19 @@
 # Changelog
 
+## 0.12.0
+
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
+
 ## 0.11.12
 
 - Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
diff --git a/docs/architecture.md b/docs/architecture.md
index 622a35e..be4bdb6 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -204,7 +204,13 @@ The guarantees the controller gives, each with where it is implemented and prove
   `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in mid-operation cannot redirect a write; atomic
   writes fsync and `os.replace`; config, owned-record, journal and log leaves are opened
   `O_NOFOLLOW`; absolute and `..` paths are rejected. Failures are typed (`PathContainmentError`)
-  and caught at every boundary. `tests/core/test_runtime_fs.py`.
+  and caught at every boundary. `tests/core/test_runtime_fs.py`. These presence decisions use
+  `runtime_fs.probe_exists`, or `probe_stat` where the kind (directory, symlink) decides: binary
+  retire's check of each receipt file and owned folder, the binary->source switch's listing of a
+  tree, a checkout's dirty/carry inventory (`.git`), the boot-restore markers and journal, and
+  the config journal recovery. In each, only
+  ENOENT/ENOTDIR read as absent, any other error refuses or keeps; the stdlib-only firewall
+  helper applies the same rule inline (`tests/core/test_existence_probe.py`).
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
index f737f27..8aa288b 100644
--- a/lhpc/core/config.py
+++ b/lhpc/core/config.py
@@ -1806,9 +1806,10 @@ def recover_config_transaction(paths: Paths) -> str | None:
     # Presence is decided WITHOUT following the leaf: ANY directory entry at the journal
     # path -- a regular file, OR a symlink (including a dangling or escaping one) -- is a
     # pending journal that must be recovered/blocked. `Path.exists()` follows the link and
-    # would report a dangling-symlink journal as absent; `os.path.lexists` does not.
-    if not os.path.lexists(jp):
-        return None
+    # would report a dangling-symlink journal as absent; `probe_exists` does not, nor EIO.
+    state = runtime_fs.probe_exists(jp)[0]
+    if state != "present":
+        return None if state == "absent" else ""
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
index cbac94c..62805fa 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -886,7 +886,11 @@ class Installer:
         not a git checkout reports clean here (ownership verification handles unknown trees).
         A FAILED git status reports the failure as a tracked entry — fail toward dirty, never
         silently clean."""
-        if not (dest / ".git").exists():
+        from . import runtime_fs
+        state, why = runtime_fs.probe_exists(dest / ".git")
+        if state == "unknown":
+            return DirtyReport(tracked=(f"(cannot examine .git ({why}) — treating as dirty)",))
+        if state == "absent":
             return DirtyReport()
         # NUL-SAFE, ENTRY-EXACT status: `-z` terminates every path with NUL (no quoting, so
         # newline/quote-containing names parse exactly), and `--untracked-files=all`
@@ -946,8 +950,10 @@ class Installer:
 
         Regenerable artifacts are filtered by the SAME predicate `dirty_report` uses, so
         `build/`, `.run/` and a component's declared `bin` stay disposable in both."""
-        if not (dest / ".git").exists():
-            return ()
+        from . import runtime_fs
+        state, _why = runtime_fs.probe_exists(dest / ".git")
+        if state != "present":
+            return () if state == "absent" else None
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
index eeae6ad..c2e01ee 100644
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
@@ -611,12 +612,19 @@ class BinaryOpsMixin:
 
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
@@ -742,7 +750,10 @@ class BinaryOpsMixin:
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
@@ -841,16 +852,11 @@ class BinaryOpsMixin:
 
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
@@ -926,8 +932,7 @@ class BinaryOpsMixin:
                 failed.append(f"{rel} ({exc})")
         # PROVE removal before dropping the receipt: a swallowed unlink failure would leave
         # binary files behind with no ownership record at all.
-        still_there = [rel for rel in rec.files
-                       if os.path.exists(self._paths.under(*rel.split("/")))]
+        still_there = [rel for rel in rec.files if self._receipt_leaf_present(rel)]
         if still_there or failed:
             return ActionResult(
                 False,
@@ -943,8 +948,15 @@ class BinaryOpsMixin:
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
diff --git a/tests/core/test_existence_probe.py b/tests/core/test_existence_probe.py
new file mode 100644
index 0000000..5df3b73
--- /dev/null
+++ b/tests/core/test_existence_probe.py
@@ -0,0 +1,286 @@
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
+    assert cfgmod.recover_config_transaction(paths) == ""
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

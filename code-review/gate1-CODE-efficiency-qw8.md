# Gate 1: efficiency-qw8 (the RF-log badge runs no git), v1

**Request.** Judge the code commit below: the handler's order QW8 ("`rflog_running` gets the same treatment as
`log_running` after QW7 — no full snapshot with git per poll; UX unchanged; one function") and QW7's plan text
(quoted). That means: correctness; whether the badge can differ from today's in any state; whether the red-before
test can fail for the defect named; and the report's claims against the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## QW7 in the plan (plan v2 §3 P3, verbatim)

**P3 — QW7** — auditor OK with note; to be coded with the exhaustive equal-output test of v1 (7 source states ×
signature and `log_running`), zero git calls per poll.

## The commit message (`git log 771d1a37..f3dec5fb`, full)

### f3dec5fb efficiency: the RF-log badge runs no git

efficiency: the RF-log badge runs no git

The RF-log page polls its writer's run state every 5 s; rflog_running
built a full status snapshot for it, with git status + describe for
every installed source, as log_running did before the polled reads
stopped running git. It now uses build_snapshot(sources=False) like
log_running: a run state reads the source only as missing / not a
repository / binary, which the filesystem checks decide before git.
The CHANGELOG line for the polled reads ("the log pages' background
refresh no longer runs git") now also holds for the RF-log page.
service_lifecycle_ops.py: same line count.

Red before: test_the_rf_log_badge_runs_no_git[running/stopped] (2 git
calls per poll); the badge equals the full snapshot's answer. The run
states with and without git over all seven source states:
test_a_snapshot_without_git_has_the_same_run_states (QW7).


## The code report (verbatim; committed as `code-review/code-report-efficiency-qw8.md`)

# Code report: efficiency-qw8 (the RF-log badge runs no git)

Branch `cons/efficiency-qw8-r1` on `origin/dev` = `771d1a37`. Order: the handler's QW8 (2026-10-16, found during A1's
second read): `rflog_running` gets the treatment `log_running` got in QW7; same packet shape; UX unchanged; one
function. One commit.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| f3dec5fb | efficiency: the RF-log badge runs no git | core/service_lifecycle_ops.py (one line), tests/core/test_snapshot_memo.py | core: `test_the_rf_log_badge_runs_no_git[running, stopped]` (writer `meshcore-node`, job `rf-meshcore.log`, a FakeSystem checkout at `src/openhop-core`: the badge after a fresh request equals the full snapshot's answer and `is running`; 0 git calls; precondition: the full read ran git) | yes, on 771d1a37: 2 failed — 2 git calls per poll |

**Green:** `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/web tests/stacks tests/repo
tests/cli tests/golden` → `5395 passed, 1 skipped in 72.42s`; `tests/core/test_snapshot_memo.py` → `84 passed`.
`ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` → `All checks passed!`.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  771d1a37..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 27 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        1 new tests; layers tests/core=core; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_lifecycle_ops.py 6315->6315 lines (budget 6315), 73->73 public (budget 73); family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The 6-point block

1. **Contract.** `rflog_running(job)` (`service_lifecycle_ops.py:5882`): for a stack writer it now reads
   `build_snapshot(sources=False)` (`:5893`), which returns the request's full snapshot when one is cached, else one
   without git that is never cached (QW7). The daemon branch (`daemon_view(band).reachable`) is unchanged.
2. **Invariant and its test.** The badge equals the full snapshot's answer and runs no git: the new test, running and
   stopped. That a run state is the same with and without git for every source state is QW7's
   `test_a_snapshot_without_git_has_the_same_run_states` (7 states × running/stopped), unchanged and green.
3. **Failure classes.** Same as QW7: the light snapshot is uncached, so a later full read in the same request builds
   its own; `fresh=True` rechecks are untouched; the CLI path (`lhpc rflog`) reads no badge.
4. **Test rules.** Through the public service call; FakeSystem; call counts and a typed bool.
5. **Directories and ruff.** As under Green.
6. **Self-review.** One line changed; the line count of `service_lifecycle_ops.py` is unchanged (6315). No CHANGELOG
   line: QW7's bullet ("the log pages' background refresh no longer runs git for every installed source") now also
   holds for the RF-log page.

## The diff (`git diff 771d1a37 f3dec5fb`, verbatim)

```diff
diff --git a/lhpc/core/service_lifecycle_ops.py b/lhpc/core/service_lifecycle_ops.py
index 95f920c8..6424c08d 100644
--- a/lhpc/core/service_lifecycle_ops.py
+++ b/lhpc/core/service_lifecycle_ops.py
@@ -5890,7 +5890,7 @@ class LifecycleOpsMixin:
                 return bool(self.daemon_view(band).reachable)
             except Exception:
                 return False
-        for ss in self.build_snapshot().stacks:
+        for ss in self.build_snapshot(sources=False).stacks:     # a run state: no git per poll
             st = ss.components.get(e.writer)
             if st is not None:
                 return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
diff --git a/tests/core/test_snapshot_memo.py b/tests/core/test_snapshot_memo.py
index e9094deb..39c29787 100644
--- a/tests/core/test_snapshot_memo.py
+++ b/tests/core/test_snapshot_memo.py
@@ -992,3 +992,24 @@ def test_the_dashboard_signature_and_a_log_running_flag_run_no_git(tmp_path):
     svc.invalidate_snapshot()
     assert svc.log_running("meshcore") == full_run
     assert [c for c in calls[before:] if c[:1] == ["git"]] == []
+
+
+@pytest.mark.parametrize("running", [True, False])
+def test_the_rf_log_badge_runs_no_git(tmp_path, running):
+    # The RF-log page polls its writer's run state (every 5 s); like the process-log badge it needs run
+    # states only, so it builds no snapshot with git. The badge equals the full snapshot's answer.
+    src = str(tmp_path / "src" / "openhop-core")
+    cmds = {("git", "-C", src, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
+            CR(0, f"# branch.oid {_PIN}\n# branch.head main\n", ""),
+            ("git", "-C", src, "describe", "--tags", "--always"): CR(0, "v1\n", "")}
+    fake = FakeSystem(paths={src, src + "/.git"}, commands=cmds)
+    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
+    node = next(c for s in svc.stacks() for c in s.components if c.id == "meshcore-node")
+    if running:
+        fake.cmdlines_data = {77: [f"/opt/bin/{node.process.exec_name}", *node.process.all_args]}
+    full = (svc.build_snapshot(), svc.rflog_running("rf-meshcore.log"))[1]    # the request's full snapshot
+    assert any(c[:1] == ["git"] for c in fake.calls)                         # precondition: git ran
+    svc.invalidate_snapshot()
+    before = len(fake.calls)
+    assert svc.rflog_running("rf-meshcore.log") is full is running
+    assert [c for c in fake.calls[before:] if c[:1] == ["git"]] == []
```

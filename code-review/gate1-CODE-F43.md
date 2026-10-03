# Gate 1 — code review request, F43, Correction 4

**Request.** Judge ONLY one commit, 2e15657 "F43: every build step's log ends with its longest
quiet period". It re-implements an earlier commit (fa22a22) on top of F42's stall machinery
(`lhpc/core/progress.py`: `Watch`, `SessionSampler`, `build_limits`). Judge four things:
(1) the longest quiet period comes from F42's `progress.Watch` alone — no second sampler, no
second stall rule, and the stall verdict, `stall_s`, `sample_s` and the ceiling behave exactly as
before; (2) the `[progress] longest quiet <n> s` line is printed at the end of every build step by
the same runner paths F42 uses — `run_job` over `run_streaming` (CLI `lhpc build`, auto-install)
and the web launcher's `_run_step`, whose `(rc, reason, unverified)` return is unchanged — and by
no Test step; (3) the value is right: the largest `now - last progress` at a sample without
progress, or the quiet tail at the end; (4) the tests prove (1)–(3) and the slow-build lane's
reader still parses the line. The nine other commits of the branch are unchanged cherry-picks and
are not under review.

Answer in the form `| commit | verdict (OK / FINDING) | what |`, then give one final line: GREEN /
GREEN WITH NOTES / RED. A finding names the line in the diff and what goes wrong.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Context from the base (unchanged code the commit builds on)

`progress.Watch` on the base, as the commit finds it:

```python
class Watch:
    """`check()` -> None, "stalled" or "budget". Samples only when `sample_s` has elapsed; the stall
    decision is taken right after a sample. With `sampler=None` (or `stall_s=None`) only the ceiling
    applies."""

    def __init__(self, stall_s, ceiling_s: float, sample_s: float = SAMPLE_S,
                 clock=time.monotonic, sampler=None):
        self.stall_s, self.ceiling_s, self._sample_s = stall_s, ceiling_s, sample_s
        self._clock, self._sampler = clock, sampler
        self._start = self._last_sample = self._last_progress = clock()

    def check(self) -> str | None:
        now = self._clock()
        if now - self._start >= self.ceiling_s:
            return "budget"
        if self.stall_s is None or self._sampler is None or now - self._last_sample < self._sample_s:
            return None
        self._last_sample = now
        if self._sampler.sample():
            self._last_progress = now
            return None
        return "stalled" if now - self._last_progress >= self.stall_s else None
```

A sampler's `sample()` returns True for progress and also when the sample could not be taken
(unknown counts as alive). `lifecycle.build` is the only caller that passes `stall_s` to `run_job`
(every build step, CLI and auto-install); host tests pass none. The web launcher calls
`_run_step(..., stall_s, sample_s)` with `stall_s` set for a Build and None for a Test. The
slow-build lane reads the line with
`re.compile(r"^\[progress\] longest quiet (\d+(?:\.\d+)?) s\s*$", re.MULTILINE)` from the build
logs and records the largest value as `quiet_s`.

## Test evidence

Red before (the commit's `lhpc/` changes reverted, its tests kept): the new core test module 12
failed, 1 passed (the Test-run negative case); the lane module's new test failed. Causes: no
`Watch.longest_quiet`, no `progress.quiet_line`, no `CommandResult.longest_quiet_s`. After the
commit: 13 passed and 27 passed; the F42 modules (`test_progress.py`, `test_jobs.py`,
`test_build_launcher_runtime.py`, `test_bounded_runner.py`, `test_build_timeout.py`) pass unchanged.

## The commit

```diff
commit 2e15657

F43: every build step's log ends with its longest quiet period

Plan §5 change 4, on F42's stall machinery. The L1 quantity of the slow-target budget is the
largest `now - last progress` over a step: the difference F42's progress.Watch already compares
with stall_s at every sample. Watch.longest_quiet() reports it: the largest such value at a
sample without progress, or the quiet tail up to now (without a sampler: the whole runtime).
progress.quiet_line() renders `[progress] longest quiet <n> s`. No second sampler and no change
to the stall decision, stall_s or the ceiling.

The line is printed by the runner paths F42 uses. run_streaming (fast and controlled path)
returns the Watch's value as CommandResult.longest_quiet_s; run_job writes the line at the end
of the log of every step it runs with stall_s (every lifecycle build step: CLI `lhpc build` and
auto-install), whatever the outcome, log only, never the tail. The web launcher's _run_step
prints it after every Build step, stopped or not, and none after a Test step; its
(rc, reason, unverified) return is unchanged.

tests/core/test_longest_quiet.py: the value from a Watch driven by a fake clock and sampler
(quiet gap, quiet tail, continuous progress, no sampler, stall verdicts unchanged), the line in
the lane's own pattern, the job log on success, failure and timeout, none without stall_s,
both run_streaming paths, the web launcher for Build, a stopped Build step and Test.
testlab/tests/unit/test_slow_build_lane.py: the lane's build reader records quiet_s from a log
written by run_job. Against the previous commit 12 of the 13 new core tests and the lane test fail.


diff --git a/lhpc/core/build_launcher_runtime.py b/lhpc/core/build_launcher_runtime.py
index 706ea3d..49ed150 100644
--- a/lhpc/core/build_launcher_runtime.py
+++ b/lhpc/core/build_launcher_runtime.py
@@ -155,7 +155,9 @@ def _run_step(argv: list, cwd: str, env: dict, timeout: float, stall_s: float |
     watch = progress.Watch(stall_s, timeout, sample_s, sampler=sampler)
     while True:
         try:
-            return p.wait(timeout=min(1.0, sample_s, watch.remaining())), "", False   # never past the ceiling
+            rc = p.wait(timeout=min(1.0, sample_s, watch.remaining()))   # never past the ceiling
+            _print_quiet(watch, stall_s)
+            return rc, "", False
         except subprocess.TimeoutExpired:
             reason = watch.check()
             if reason:
@@ -170,9 +172,17 @@ def _run_step(argv: list, cwd: str, env: dict, timeout: float, stall_s: float |
                          "{}\n".format(result.value, " ".join(argv)))
     sys.stderr.write("step timed out {}: {}\n".format(_stop_words(reason, stall_s, timeout),
                                                       " ".join(argv)))
+    _print_quiet(watch, stall_s)
     return 124, reason, (not result.ok)
 
 
+def _print_quiet(watch, stall_s) -> None:
+    """A Build step's output ends with its longest quiet period (`Watch.longest_quiet`); a Test step
+    (no stall rule) prints none."""
+    if stall_s is not None:
+        print(progress.quiet_line(watch.longest_quiet()), flush=True)
+
+
 def _stop_words(reason: str, stall_s, timeout: float) -> str:
     """Which limit ended a step, with the effective values: a Build names the stall or the guard,
     a Test (no stall rule) keeps "after Ns"."""
diff --git a/lhpc/core/jobs.py b/lhpc/core/jobs.py
index 5d27d80..c79244b 100644
--- a/lhpc/core/jobs.py
+++ b/lhpc/core/jobs.py
@@ -80,7 +80,9 @@ def run_job(
     likewise typed — never a silently-successful job with a missing log.
 
     `stall_s` (build steps only) adds the stall rule to the `timeout` ceiling (`progress`); it is
-    handed to the runner only when given, so host tests and other callers keep a plain timeout."""
+    handed to the runner only when given, so host tests and other callers keep a plain timeout.
+    A build step's log then ends with its `[progress] longest quiet <n> s` line (the runner's
+    `longest_quiet_s`), whatever the outcome; the tail does not carry it."""
     from . import runtime_fs
     from .paths import PathContainmentError
     # A job name is controller-derived, but guard the leaf so a planted symlinked log
@@ -170,6 +172,15 @@ def run_job(
             except OSError:
                 pass
             output = (output + "\n" + marker) if output else marker
+        quiet = getattr(result, "longest_quiet_s", None)
+        if stall_s is not None and quiet is not None:
+            # Best-effort, like the announce line: a missing line makes the step no slow-target
+            # evidence, never a passing one.
+            try:
+                log_fh.write("\n" + progress.quiet_line(quiet) + "\n")
+                log_fh.flush()
+            except OSError:
+                pass
     finally:
         try:
             log_fh.close()
diff --git a/lhpc/core/probes/backends.py b/lhpc/core/probes/backends.py
index d9f00be..578d152 100644
--- a/lhpc/core/probes/backends.py
+++ b/lhpc/core/probes/backends.py
@@ -46,6 +46,8 @@ class CommandResult:
     # Why a timed-out run was stopped: "stalled" (no activity for stall_s) or "budget" (the ceiling);
     # "" when it was not timed out. `timed_out` stays True for both.
     stop_reason: str = ""
+    # The step's `progress.Watch.longest_quiet()` at its end (run_streaming only); None elsewhere.
+    longest_quiet_s: float | None = None
 
     @property
     def may_still_be_running(self) -> bool:
@@ -400,7 +402,8 @@ class RealCommandRunner:
             timed_out = bool(reason)
             rc = 124 if timed_out else (proc.returncode if proc.returncode is not None else -1)
             return CommandResult(returncode=rc, stdout="", stderr="", timed_out=timed_out,
-                                 termination=termination, stop_reason=reason)
+                                 termination=termination, stop_reason=reason,
+                                 longest_quiet_s=watch.longest_quiet())
         return self._run_controlled(argv, timeout, log_fh, cwd, env, redactor, should_cancel,
                                     low_priority, stall_s, sample_s)
 
@@ -511,7 +514,8 @@ class RealCommandRunner:
         return CommandResult(returncode=rc, stdout="", stderr="", timed_out=timed_out,
                              cancelled=cancelled, termination=termination,
                              output_unverified=output_unverified, session_ident=ident,
-                             log_write_failed=write_failed[0], stop_reason=reason)
+                             log_write_failed=write_failed[0], stop_reason=reason,
+                             longest_quiet_s=watch.longest_quiet())
 
 
 class RealProcFs:
diff --git a/lhpc/core/progress.py b/lhpc/core/progress.py
index 282a005..427513f 100644
--- a/lhpc/core/progress.py
+++ b/lhpc/core/progress.py
@@ -153,6 +153,7 @@ class Watch:
         self.stall_s, self.ceiling_s, self._sample_s = stall_s, ceiling_s, sample_s
         self._clock, self._sampler = clock, sampler
         self._start = self._last_sample = self._last_progress = clock()
+        self._quiet = 0.0
 
     def check(self) -> str | None:
         now = self._clock()
@@ -164,9 +165,23 @@ class Watch:
         if self._sampler.sample():
             self._last_progress = now
             return None
+        self._quiet = max(self._quiet, now - self._last_progress)
         return "stalled" if now - self._last_progress >= self.stall_s else None
 
+    def longest_quiet(self) -> float:
+        """The longest quiet period so far: the largest `now - last progress` the stall rule compared
+        at a sample without progress, or the quiet tail up to now. Its margin to `stall_s` is the
+        slow-target budget's L1 quantity, at the sampling resolution; without a sampler nothing is
+        observed after the start, so it is the whole runtime."""
+        return max(self._quiet, self._clock() - self._last_progress)
+
     def remaining(self) -> float:
         """Seconds left to the ceiling (never negative): a runner caps each wait slice with it, so a
         step can never outlive its ceiling by a wait slice."""
         return max(0.0, self._start + self.ceiling_s - self._clock())
+
+
+def quiet_line(seconds: float) -> str:
+    """The last line of a build step's log (`Watch.longest_quiet`); the slow-build lane and row A
+    read it."""
+    return f"[progress] longest quiet {seconds:.1f} s"
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index c89dac5..30df735 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -13,6 +13,10 @@ from pathlib import Path
 
 import pytest
 
+from lhpc.core.jobs import run_job
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import CommandResult
+
 _LANE = Path(__file__).resolve().parents[1] / "slowbuild" / "test_slow_build.py"
 _spec = importlib.util.spec_from_file_location("slow_build_lane", _LANE)
 lane = importlib.util.module_from_spec(_spec)
@@ -94,6 +98,21 @@ def test_a_clean_step_passes_and_returns_both_streams():
     assert "[progress] longest quiet 12.0 s" in out and "0 failed" in out
 
 
+def test_the_lane_records_the_quiet_line_a_build_step_log_ends_with(step, tmp_path, monkeypatch):
+    # The log as `run_job` writes it for a build step whose Watch saw a 41.5 s quiet period.
+    class _Streaming:
+        def run_streaming(self, argv, timeout, log_fh, **kw):
+            log_fh.write("compiling\n")
+            return CommandResult(returncode=0, stdout="", stderr="", longest_quiet_s=41.5)
+
+    run_job(_Streaming(), name="build-meshcore-cli", argv=["make"], cwd=None,
+            logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path), stall_s=600.0)
+    seen = []
+    monkeypatch.setattr(lane, "_record", lambda *a, **k: seen.append((a, k)))
+    lane._build({"LHPC_RUNTIME_ROOT": str(tmp_path)}, "meshcore-cli")
+    assert seen and seen[0][1] == {"quiet_s": 41.5}
+
+
 # ---- the budget case's waivers (bootstrap; L4 on the introducing release) -------------------
 
 L4 = "no row C evidence for lhpc-selfupdate selfupdate-pip: the slow-build run did not measure it"
diff --git a/tests/core/test_longest_quiet.py b/tests/core/test_longest_quiet.py
new file mode 100644
index 0000000..e6d0940
--- /dev/null
+++ b/tests/core/test_longest_quiet.py
@@ -0,0 +1,171 @@
+"""Every build step's log ends with its longest quiet period (plans/PLAN-F43.md §5 change 4): the
+L1 quantity the slow-target budget holds against the stall limit, read by the slow-build lane and
+by row A from `[progress] longest quiet <n> s`.
+
+The quantity comes from F42's `progress.Watch` — the stall rule's own `now - last progress` — and
+is printed by the runner paths F42 uses: `run_job` over `run_streaming` (CLI `lhpc build`,
+auto-install) and the web launcher's `_run_step`. These tests drive the Watch with a fake clock
+and sampler, as tests/core/test_progress.py does."""
+from __future__ import annotations
+
+import re
+import sys
+from pathlib import Path
+
+from lhpc.core import build_launcher_runtime as blr
+from lhpc.core import progress
+from lhpc.core.jobs import run_job
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import CommandResult, RealCommandRunner
+
+# The slow-build lane's own pattern (testlab/tests/slowbuild/test_slow_build.py `_QUIET`).
+LANE_QUIET = re.compile(r"^\[progress\] longest quiet (\d+(?:\.\d+)?) s\s*$", re.MULTILINE)
+
+
+class _Clock:
+    def __init__(self):
+        self.t = 0.0
+
+    def __call__(self):
+        return self.t
+
+
+class _Seq:
+    """A sampler answering a fixed sequence of progress / no-progress samples."""
+
+    def __init__(self, answers):
+        self._answers = iter(answers)
+
+    def sample(self):
+        return next(self._answers)
+
+
+def _drive(samples, end, stall_s=600):
+    """A Watch sampled every 15 s with `samples` (progress True/False), read at `end`."""
+    clock = _Clock()
+    w = progress.Watch(stall_s=stall_s, ceiling_s=10 ** 9, sample_s=15, clock=clock,
+                       sampler=_Seq(samples))
+    verdicts = []
+    for i in range(len(samples)):
+        clock.t = 15.0 * (i + 1)
+        verdicts.append(w.check())
+    clock.t = end
+    return w, verdicts
+
+
+# ---- the quantity, from the Watch's own samples --------------------------------------------
+
+def test_longest_quiet_is_the_largest_now_minus_last_progress_at_a_quiet_sample():
+    # progress at 15, quiet at 30 and 45, progress at 60, quiet at 75; read at 80
+    w, _ = _drive([True, False, False, True, False], end=80.0)
+    assert w.longest_quiet() == 30.0                     # 45 - 15: the stall rule's own difference
+
+
+def test_the_quiet_tail_before_the_end_counts():
+    w, _ = _drive([True], end=100.0)
+    assert w.longest_quiet() == 85.0
+
+
+def test_continuous_progress_reads_at_most_one_sample_interval():
+    w, _ = _drive([True, True, True], end=52.0)
+    assert w.longest_quiet() == 7.0
+
+
+def test_a_step_without_a_sampler_reads_its_whole_runtime():
+    clock = _Clock()
+    clock.t = 10.0
+    w = progress.Watch(stall_s=None, ceiling_s=10 ** 9, clock=clock)
+    clock.t = 250.5
+    assert w.check() is None and w.longest_quiet() == 240.5
+
+
+def test_the_accessor_leaves_the_stall_rule_unchanged():
+    # quiet from the start: stalled at the first sample with now - last progress >= stall_s
+    w, verdicts = _drive([False] * 4, end=60.0, stall_s=60)
+    assert verdicts == [None, None, None, "stalled"]
+    assert w.longest_quiet() == 60.0
+
+
+def test_the_line_is_what_the_lane_reads():
+    m = LANE_QUIET.search(progress.quiet_line(240.46))
+    assert m and m.group(0) == "[progress] longest quiet 240.5 s"
+
+
+# ---- CLI path: run_job over run_streaming ends a build step's log with it ------------------
+
+class _Streaming:
+    """A streaming runner whose step ended with a known longest quiet period."""
+
+    def __init__(self, rc=0, timed_out=False, quiet=37.0):
+        self.rc, self.timed_out, self.quiet = rc, timed_out, quiet
+
+    def run_streaming(self, argv, timeout, log_fh, **kw):
+        log_fh.write("compiling\n")
+        return CommandResult(returncode=self.rc, stdout="", stderr="", timed_out=self.timed_out,
+                             stop_reason="stalled" if self.timed_out else "",
+                             longest_quiet_s=self.quiet)
+
+
+def _job(tmp_path, runner, name="build-x", **kw):
+    return run_job(runner, name=name, argv=["make"], cwd=None, logs_dir=tmp_path / "logs",
+                   paths=Paths(runtime_root=tmp_path), **kw)
+
+
+def test_a_build_step_log_ends_with_its_longest_quiet_line(tmp_path):
+    for runner in (_Streaming(), _Streaming(rc=2), _Streaming(rc=124, timed_out=True)):
+        res = _job(tmp_path, runner, stall_s=600.0)
+        lines = Path(res.log_path).read_text().rstrip("\n").splitlines()
+        assert lines[-1] == "[progress] longest quiet 37.0 s", lines
+        assert not any(LANE_QUIET.search(t) for t in res.tail)      # log only, never the tail
+
+
+def test_a_job_without_the_stall_rule_writes_no_line(tmp_path):
+    res = _job(tmp_path, _Streaming())                  # a host test: no stall_s
+    assert not LANE_QUIET.search(Path(res.log_path).read_text())
+
+
+def test_run_streaming_reports_its_watch(tmp_path, monkeypatch):
+    # Both runner paths (fast fd redirect and the controlled pipe) hand back the Watch's value.
+    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 12.5)
+    for kw in ({}, {"should_cancel": lambda: False}):
+        with open(tmp_path / "out.log", "w") as fh:
+            res = RealCommandRunner().run_streaming([sys.executable, "-c", "pass"], timeout=30,
+                                                   log_fh=fh, stall_s=60, **kw)
+        assert res.returncode == 0 and res.longest_quiet_s == 12.5, kw
+
+
+def test_the_cli_build_log_carries_the_watch_value(tmp_path, monkeypatch):
+    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 12.5)
+    res = _job(tmp_path, RealCommandRunner(), stall_s=60.0)
+    assert Path(res.log_path).read_text().rstrip("\n").splitlines()[-1] == \
+        "[progress] longest quiet 12.5 s"
+
+
+# ---- web path: the detached build launcher prints it after every Build step ----------------
+
+def _spec(tmp_path, op):
+    Paths(runtime_root=tmp_path / "rt").under("state", "locks").mkdir(parents=True)
+    return {"steps": [{"argv": ["true"], "env": {}}, {"argv": ["true"], "env": {}}],
+            "cwd": str(tmp_path), "runtime_root": str(tmp_path / "rt"), "lock_names": [],
+            "index_lock_name": "", "op": op}
+
+
+def test_the_web_launcher_prints_it_after_every_build_step(tmp_path, capfd, monkeypatch):
+    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 12.5)
+    blr.run(_spec(tmp_path, "build"))
+    out = capfd.readouterr().out
+    assert LANE_QUIET.findall(out) == ["12.5", "12.5"]
+    assert out.rstrip("\n").splitlines()[-1] == "[progress] longest quiet 12.5 s"
+
+
+def test_the_web_launcher_prints_it_for_a_stopped_build_step(tmp_path, capfd, monkeypatch):
+    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 3.0)
+    rc, reason, unverified = blr._run_step([sys.executable, "-c", "import time; time.sleep(30)"],
+                                           str(tmp_path), {}, 0.5, stall_s=600.0, sample_s=0.1)
+    assert (rc, reason) == (124, "budget")
+    assert LANE_QUIET.findall(capfd.readouterr().out) == ["3.0"]
+
+
+def test_the_web_launcher_prints_none_for_a_test_run(tmp_path, capfd):
+    blr.run(_spec(tmp_path, "test"))
+    assert not LANE_QUIET.search(capfd.readouterr().out)
```

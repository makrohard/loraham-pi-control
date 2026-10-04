"""Every build step's log ends with its longest quiet period (docs/maintenance.md): the
L1 quantity the slow-target budget holds against the stall limit, read by the slow-build lane and
by row A from `[progress] longest quiet <n> s`.

The quantity comes from F42's `progress.Watch` — the stall rule's own `now - last progress` — and
is printed by the runner paths F42 uses: `run_job` over `run_streaming` (CLI `lhpc build`,
auto-install) and the web launcher's `_run_step`. These tests drive the Watch with a fake clock
and sampler, as tests/core/test_progress.py does."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from lhpc.core import build_launcher_runtime as blr
from lhpc.core import progress
from lhpc.core.jobs import run_job
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, RealCommandRunner

# The slow-build lane's own pattern (testlab/tests/slowbuild/test_slow_build.py `_QUIET`).
LANE_QUIET = re.compile(r"^\[progress\] longest quiet (\d+(?:\.\d+)?) s\s*$", re.MULTILINE)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Seq:
    """A sampler answering a fixed sequence of progress / no-progress samples."""

    def __init__(self, answers):
        self._answers = iter(answers)

    def sample(self):
        return next(self._answers)


def _drive(samples, end, stall_s=600):
    """A Watch sampled every 15 s with `samples` (progress True/False), read at `end`."""
    clock = _Clock()
    w = progress.Watch(stall_s=stall_s, ceiling_s=10 ** 9, sample_s=15, clock=clock,
                       sampler=_Seq(samples))
    verdicts = []
    for i in range(len(samples)):
        clock.t = 15.0 * (i + 1)
        verdicts.append(w.check())
    clock.t = end
    return w, verdicts


# ---- the quantity, from the Watch's own samples --------------------------------------------

def test_longest_quiet_is_the_largest_now_minus_last_progress_at_a_quiet_sample():
    # progress at 15, quiet at 30 and 45, progress at 60, quiet at 75; read at 80
    w, _ = _drive([True, False, False, True, False], end=80.0)
    assert w.longest_quiet() == 30.0                     # 45 - 15: the stall rule's own difference


def test_the_quiet_tail_before_the_end_counts():
    w, _ = _drive([True], end=100.0)
    assert w.longest_quiet() == 85.0


def test_continuous_progress_reads_at_most_one_sample_interval():
    w, _ = _drive([True, True, True], end=52.0)
    assert w.longest_quiet() == 7.0


def test_a_step_without_a_sampler_reads_its_whole_runtime():
    clock = _Clock()
    clock.t = 10.0
    w = progress.Watch(stall_s=None, ceiling_s=10 ** 9, clock=clock)
    clock.t = 250.5
    assert w.check() is None and w.longest_quiet() == 240.5


def test_the_accessor_leaves_the_stall_rule_unchanged():
    # quiet from the start: stalled at the first sample with now - last progress >= stall_s
    w, verdicts = _drive([False] * 4, end=60.0, stall_s=60)
    assert verdicts == [None, None, None, "stalled"]
    assert w.longest_quiet() == 60.0


def test_the_line_is_what_the_lane_reads():
    m = LANE_QUIET.search(progress.quiet_line(240.46))
    assert m and m.group(0) == "[progress] longest quiet 240.5 s"


# ---- CLI path: run_job over run_streaming ends a build step's log with it ------------------

class _Streaming:
    """A streaming runner whose step ended with a known longest quiet period."""

    def __init__(self, rc=0, timed_out=False, quiet=37.0):
        self.rc, self.timed_out, self.quiet = rc, timed_out, quiet

    def run_streaming(self, argv, timeout, log_fh, **kw):
        log_fh.write("compiling\n")
        return CommandResult(returncode=self.rc, stdout="", stderr="", timed_out=self.timed_out,
                             stop_reason="stalled" if self.timed_out else "",
                             longest_quiet_s=self.quiet)


def _job(tmp_path, runner, name="build-x", **kw):
    return run_job(runner, name=name, argv=["make"], cwd=None, logs_dir=tmp_path / "logs",
                   paths=Paths(runtime_root=tmp_path), **kw)


def test_a_build_step_log_ends_with_its_longest_quiet_line(tmp_path):
    for runner in (_Streaming(), _Streaming(rc=2), _Streaming(rc=124, timed_out=True)):
        res = _job(tmp_path, runner, stall_s=600.0)
        lines = Path(res.log_path).read_text().rstrip("\n").splitlines()
        assert lines[-1] == "[progress] longest quiet 37.0 s", lines
        assert not any(LANE_QUIET.search(t) for t in res.tail)      # log only, never the tail


def test_a_job_without_the_stall_rule_writes_no_line(tmp_path):
    res = _job(tmp_path, _Streaming())                  # a host test: no stall_s
    assert not LANE_QUIET.search(Path(res.log_path).read_text())


def test_run_streaming_reports_its_watch(tmp_path, monkeypatch):
    # Both runner paths (fast fd redirect and the controlled pipe) hand back the Watch's value.
    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 12.5)
    for kw in ({}, {"should_cancel": lambda: False}):
        with open(tmp_path / "out.log", "w") as fh:
            res = RealCommandRunner().run_streaming([sys.executable, "-c", "pass"], timeout=30,
                                                   log_fh=fh, stall_s=60, **kw)
        assert res.returncode == 0 and res.longest_quiet_s == 12.5, kw


def test_the_cli_build_log_carries_the_watch_value(tmp_path, monkeypatch):
    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 12.5)
    res = _job(tmp_path, RealCommandRunner(), stall_s=60.0)
    assert Path(res.log_path).read_text().rstrip("\n").splitlines()[-1] == \
        "[progress] longest quiet 12.5 s"


# ---- web path: the detached build launcher prints it after every Build step ----------------

def _spec(tmp_path, op):
    Paths(runtime_root=tmp_path / "rt").under("state", "locks").mkdir(parents=True)
    return {"steps": [{"argv": ["true"], "env": {}}, {"argv": ["true"], "env": {}}],
            "cwd": str(tmp_path), "runtime_root": str(tmp_path / "rt"), "lock_names": [],
            "index_lock_name": "", "op": op}


def test_the_web_launcher_prints_it_after_every_build_step(tmp_path, capfd, monkeypatch):
    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 12.5)
    blr.run(_spec(tmp_path, "build"))
    out = capfd.readouterr().out
    assert LANE_QUIET.findall(out) == ["12.5", "12.5"]
    assert out.rstrip("\n").splitlines()[-1] == "[progress] longest quiet 12.5 s"


def test_the_web_launcher_prints_it_for_a_stopped_build_step(tmp_path, capfd, monkeypatch):
    monkeypatch.setattr(progress.Watch, "longest_quiet", lambda self: 3.0)
    rc, reason, unverified = blr._run_step([sys.executable, "-c", "import time; time.sleep(30)"],
                                           str(tmp_path), {}, 0.5, stall_s=600.0, sample_s=0.1)
    assert (rc, reason) == (124, "budget")
    assert LANE_QUIET.findall(capfd.readouterr().out) == ["3.0"]


def test_the_web_launcher_prints_none_for_a_test_run(tmp_path, capfd):
    blr.run(_spec(tmp_path, "test"))
    assert not LANE_QUIET.search(capfd.readouterr().out)

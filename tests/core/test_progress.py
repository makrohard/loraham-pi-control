"""A build step is ended when it shows no activity, not for being slow (F42).

`progress` decides, from cumulative `/proc` counters of the step's session plus the log size,
whether a sample is progress; `Watch` turns that into "stalled" / "budget"; `build_limits` is the
one precedence rule every build path uses. These tests drive a fake `/proc` and a fake clock."""

from __future__ import annotations

import math

import pytest

from lhpc.core import progress

SID = 4242


def _stat(pid, cpu=(0, 0, 0, 0), start=1000, sid=SID, state="R"):
    # fields after "(comm) ": state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt
    # utime stime cutime cstime … starttime (index 19).
    rest = [state, "1", str(sid), str(sid), "0", "-1", "0", "0", "0", "0", "0",
            *(str(c) for c in cpu), "20", "0", "1", "0", str(start)]
    return f"{pid} (cc1 (x)) " + " ".join(rest) + "\n"


class _Proc:
    """A fake /proc: `set(pid, cpu=…, io=…, start=…)`; `drop(pid)` removes it."""

    def __init__(self, root):
        self.root = root
        self.size = 0

    def set(self, pid, cpu=(0, 0, 0, 0), io=(0, 0), start=1000, sid=SID, state="R"):
        d = self.root / str(pid)
        d.mkdir(exist_ok=True)
        (d / "stat").write_text(_stat(pid, cpu, start, sid, state))
        (d / "io").write_text(f"rchar: {io[0]}\nwchar: {io[1]}\nsyscr: 1\n")

    def drop(self, pid):
        for f in (self.root / str(pid)).iterdir():
            f.unlink()
        (self.root / str(pid)).rmdir()

    def sampler(self):
        return progress.SessionSampler(SID, exclude_pid=1, out_size=lambda: self.size,
                                       proc_root=str(self.root))


@pytest.fixture
def proc(tmp_path):
    p = _Proc(tmp_path)
    p.set(100, cpu=(50, 5, 0, 0), io=(4096, 512))
    p.set(200, sid=7)                       # another session: never counted
    return p


def _baseline(proc):
    s = proc.sampler()
    assert s.sample() is True               # first sample: no baseline yet
    assert s.sample() is False              # nothing changed
    return s


@pytest.mark.parametrize("change", ["cpu_tick", "child_cpu_tick", "io_byte", "log_byte",
                                    "new_member"])
def test_any_increase_is_progress(proc, change):
    s = _baseline(proc)
    if change == "cpu_tick":
        proc.set(100, cpu=(51, 5, 0, 0), io=(4096, 512))
    elif change == "child_cpu_tick":
        proc.set(100, cpu=(50, 5, 0, 1), io=(4096, 512))
    elif change == "io_byte":
        proc.set(100, cpu=(50, 5, 0, 0), io=(4096, 513))
    elif change == "log_byte":
        proc.size += 1
    else:
        proc.set(101, start=1200)
    assert s.sample() is True
    assert s.sample() is False              # the new state is the next baseline


def test_other_sessions_and_zombies_are_not_activity(proc):
    s = _baseline(proc)
    proc.set(200, cpu=(9, 9, 9, 9), io=(1, 1), sid=7)       # another session works hard
    proc.set(102, cpu=(3, 0, 0, 0), state="Z")              # a zombie member
    assert s.sample() is False


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_tiny_steady_progress_never_stalls(proc):
    # The reviewer's case: a contended compile gaining 0.20 s CPU per 15 s sample (here: 1 tick per
    # sample) is progress at EVERY sample — nothing below a threshold is thrown away.
    clock = _Clock()
    w = progress.Watch(stall_s=600, ceiling_s=10 ** 9, sample_s=15, clock=clock,
                       sampler=proc.sampler())
    ticks = 50
    while clock.t < 10 * 600:
        clock.t += 15
        ticks += 1
        proc.set(100, cpu=(ticks, 5, 0, 0), io=(4096, 512))
        assert w.check() is None, clock.t


class _Fixed:
    def __init__(self, value):
        self.value = value

    def sample(self):
        return self.value


def test_idle_session_stalls_after_stall_s_not_before():
    clock = _Clock()
    w = progress.Watch(stall_s=600, ceiling_s=10 ** 9, sample_s=15, clock=clock,
                       sampler=_Fixed(False))
    for t in range(15, 600, 15):
        clock.t = t
        assert w.check() is None
    clock.t = 599.9
    assert w.check() is None
    clock.t = 600
    assert w.check() == "stalled"


def test_a_real_idle_session_stalls(proc):
    # The real sampler on an unchanging session: the first sample is "progress" (no baseline), so
    # the stall comes one sample interval after stall_s — biased toward alive, never early.
    clock = _Clock()
    w = progress.Watch(stall_s=60, ceiling_s=10 ** 9, sample_s=15, clock=clock,
                       sampler=proc.sampler())
    seen = []
    for t in range(15, 120, 15):
        clock.t = t
        seen.append((t, w.check()))
    assert seen == [(15, None), (30, None), (45, None), (60, None), (75, "stalled"),
                    (90, "stalled"), (105, "stalled")]


def _listdir_fails(monkeypatch, proc):
    import os
    real = os.listdir
    monkeypatch.setattr(progress.os, "listdir",
                        lambda p: (_ for _ in ()).throw(PermissionError("no /proc"))
                        if p == str(proc.root) else real(p))


def _stat_enoent_after_listing(monkeypatch, proc):
    real = progress._read_text
    def read(path):
        if path.endswith("/100/stat"):
            raise FileNotFoundError(path)  # member 100 exited between listdir and its stat read
        return real(path)
    monkeypatch.setattr(progress, "_read_text", read)


def _io_eacces(monkeypatch, proc):
    real = progress._read_text
    def read(path):
        if path.endswith("/io"):
            raise PermissionError(path)
        return real(path)
    monkeypatch.setattr(progress, "_read_text", read)


def _unparsable_stat(monkeypatch, proc):
    (proc.root / "100" / "stat").write_text("100 (cc1) R garbage\n")


@pytest.mark.parametrize("break_it", [_listdir_fails, _stat_enoent_after_listing, _io_eacces,
                                      _unparsable_stat])
def test_unknown_counts_as_progress(proc, monkeypatch, break_it):
    s = _baseline(proc)
    break_it(monkeypatch, proc)
    assert s.sample() is True               # unknown is never a stall, and never raises


def test_unreadable_log_size_counts_as_progress(proc):
    s = _baseline(proc)
    s._out_size = lambda: (_ for _ in ()).throw(OSError("fstat failed"))
    assert s.sample() is True


def test_a_vanished_non_member_is_not_unknown(proc, monkeypatch):
    # Processes of OTHER sessions come and go all the time; one exiting between listdir and its
    # stat read must not count as activity of this step.
    s = _baseline(proc)
    real = progress._read_text
    def read(path):
        if path.endswith("/200/stat"):
            raise FileNotFoundError(path)
        return real(path)
    monkeypatch.setattr(progress, "_read_text", read)
    assert s.sample() is False


def test_reused_pid_is_a_new_member(proc):
    s = _baseline(proc)
    proc.set(100, cpu=(1, 0, 0, 0), io=(0, 0), start=5000)   # same pid, new process, lower counters
    assert s.sample() is True               # a new (pid, starttime): progress, no negative delta
    assert s.sample() is False


def test_reaped_child_shows_in_parent_cutime(proc):
    proc.set(101, cpu=(30, 2, 0, 0), io=(10, 10), start=1100)
    s = _baseline(proc)
    proc.drop(101)                                           # the child exits and is reaped …
    proc.set(100, cpu=(50, 5, 30, 2), io=(4096, 512))        # … its CPU lands in the parent's cutime
    assert s.sample() is True


def test_an_exited_member_alone_is_not_progress(proc):
    proc.set(101, cpu=(30, 2, 0, 0), io=(10, 10), start=1100)
    s = _baseline(proc)
    proc.drop(101)                                           # gone, parent's counters unchanged
    assert s.sample() is False


def test_guard_ends_a_busy_step():
    clock = _Clock()
    w = progress.Watch(stall_s=600, ceiling_s=3600, sample_s=15, clock=clock,
                       sampler=_Fixed(True))
    for t in range(15, 3600, 15):
        clock.t = t
        assert w.check() is None
    clock.t = 3600
    assert w.check() == "budget"


def test_without_a_sampler_only_the_ceiling_applies():
    clock = _Clock()
    w = progress.Watch(stall_s=None, ceiling_s=900, clock=clock)
    clock.t = 899
    assert w.check() is None
    clock.t = 900
    assert w.check() == "budget"


def test_remaining_counts_down_to_the_ceiling_and_never_below_zero():
    clock = _Clock()
    w = progress.Watch(stall_s=None, ceiling_s=1.5, clock=clock)
    assert w.remaining() == 1.5
    clock.t = 1.0
    assert w.remaining() == 0.5
    clock.t = 2.0
    assert w.remaining() == 0.0


@pytest.mark.parametrize("manifest, env, explicit, expected", [
    (1800.0, {}, None, (600.0, 86400.0)),
    (0.0, {}, None, (600.0, 86400.0)),                       # 0 = the manifest declares none
    (None, {}, None, (600.0, 86400.0)),
    (90000.0, {}, None, (600.0, 90000.0)),                   # only a value above the guard counts
    ("1800.0", {}, None, (600.0, 86400.0)),                  # the launcher spec carries a string
    (1800.0, {"LHPC_BUILD_STEP_TIMEOUT_S": "2"}, None, (600.0, 2.0)),   # env used AS IS
    (1800.0, {"LHPC_BUILD_STALL_S": "60"}, None, (60.0, 86400.0)),
    (1800.0, {"LHPC_BUILD_STEP_TIMEOUT_S": "2"}, 5, (600.0, 5.0)),      # explicit beats env
])
def test_build_limits_precedence(manifest, env, explicit, expected):
    assert progress.build_limits(manifest, env, explicit) == expected


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf", "x"])
@pytest.mark.parametrize("var", ["LHPC_BUILD_STALL_S", "LHPC_BUILD_STEP_TIMEOUT_S"])
def test_build_limits_rejects(var, bad):
    with pytest.raises(ValueError):
        progress.build_limits(1800.0, {var: bad})


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf, "x"])
def test_build_limits_rejects_a_bad_explicit_value(bad):
    with pytest.raises(ValueError):
        progress.build_limits(1800.0, {}, bad)


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf, "x", []])
def test_build_limits_rejects_a_bad_manifest_value(bad):
    with pytest.raises(ValueError):
        progress.build_limits(bad, {})


@pytest.mark.parametrize("manifest, env, explicit", [
    ("nan", {"LHPC_BUILD_STEP_TIMEOUT_S": "2"}, None),     # manifest masked by the env
    ("x", {}, 5),                                         # manifest masked by the explicit value
    (1800.0, {"LHPC_BUILD_STEP_TIMEOUT_S": "nan"}, 5),    # env masked by the explicit value
    (1800.0, {"LHPC_BUILD_STEP_TIMEOUT_S": "0"}, 5),
])
def test_build_limits_validates_a_masked_value(manifest, env, explicit):
    # Every supplied value is validated, not only the one the precedence picks.
    with pytest.raises(ValueError):
        progress.build_limits(manifest, env, explicit)


@pytest.mark.parametrize("seconds, text", [(86400, "24 h"), (600, "10 min"), (60, "1 min"),
                                           (2, "2 s"), (0.3, "0.3 s"), (90000, "25 h"),
                                           (5400, "90 min")])
def test_span_wording(seconds, text):
    assert progress.span(seconds) == text

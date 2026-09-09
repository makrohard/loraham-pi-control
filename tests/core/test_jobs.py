"""Job markers and launcher/log housekeeping: what `active_jobs()` will trust as a live job,
and what `prune_logs()` is allowed to delete.

The marker scan reads attacker-shaped filesystem state (a FIFO, a directory, a symlink, an
oversized file), so every case here is about staying non-blocking, bounded and fail-closed.
"""
from __future__ import annotations

import os

import pytest

from lhpc.core import jobs
from lhpc.core.jobs import JobState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService



def _svc(tmp_path):
    return ControllerService(system=FakeSystem(cmdlines_data={}).system,
                             paths=Paths(runtime_root=tmp_path))


def test_job_execution_runs_the_command_itself_and_never_a_stdbuf_wrapper(tmp_path):
    """LIVE FINDING: stdbuf's LD_PRELOAD propagated into the programs UNDER TEST — `run_job` also
    runs HOST TESTS, and the daemon suite's single-read pipe capture then raced line-buffered
    output and failed under load. So the child must be the command itself, and unbuffering must
    come from PYTHONUNBUFFERED, which only python honours and which does not propagate a preload.

    Asserted from what the runner is actually HANDED, not from the module's source text: a
    comment naming stdbuf is harmless, and a wrapper built at runtime would still be caught.
    """
    seen = {}

    class _Runner:
        def run_streaming(self, argv, timeout, log_fh, cwd=None, env=None, **kw):
            seen["argv"], seen["env"] = list(argv), dict(env or {})
            log_fh.write("out\n")
            return CommandResult(0, "", "")

        def run(self, argv, timeout=None, **kw):            # pragma: no cover - not taken here
            raise AssertionError("run_job must use the streaming path when it is available")

    logs = tmp_path / "logs"; logs.mkdir()
    res = jobs.run_job(_Runner(), name="t", argv=["true", "--flag"], cwd=None,
                       logs_dir=logs, paths=Paths(runtime_root=tmp_path))
    assert res.state is JobState.SUCCEEDED, res
    assert seen["argv"] == ["true", "--flag"], "the child must be the command, unwrapped"
    assert not any("stdbuf" in a for a in seen["argv"])
    assert seen["env"].get("PYTHONUNBUFFERED") == "1"
    assert "LD_PRELOAD" not in seen["env"]


def test_write_job_marker_records_only_a_complete_identity_and_blanks_a_bad_attempt(tmp_path):
    import os
    from lhpc.core import jobs, procident
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path); (tmp_path / "state" / "jobs").mkdir(parents=True)
    ident = procident.proc_identity(os.getpid())
    assert jobs.write_job_marker(paths, "build-x", os.getpid(), "daemon", "build", ident=ident,
                                 attempt_id="not-hex!") is True
    body = (tmp_path / "state" / "jobs" / "build-x.job").read_text()
    assert 'attempt_id = ""' in body and f"pid = {os.getpid()}" in body and 'log = "build-x"' in body
    assert jobs.write_job_marker(paths, "build-y", os.getpid(), "daemon", "build",
                                 ident={**ident, "starttime": -1}) is False        # incomplete identity
    assert not (tmp_path / "state" / "jobs" / "build-y.job").exists()


def test_prune_ephemeral_launchers_fails_closed_on_a_symlinked_subdir(tmp_path):
    import os
    from lhpc.core import jobs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path); (tmp_path / "state").mkdir()
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "s.py").write_text("SENTINEL")
    os.symlink(outside, tmp_path / "state" / "jobs")
    assert jobs.prune_ephemeral_launchers(paths, 0) == 0
    assert (outside / "s.py").read_text() == "SENTINEL"


def test_ephemeral_launcher_scripts_are_pruned_to_the_retention_budget(tmp_path):
    # Transient launcher scripts under state/jobs and state/post must be pruned
    # (they were created every build/start and never removed).
    svc = _svc(tmp_path)
    for sub in ("jobs", "post"):
        d = tmp_path / "state" / sub; d.mkdir(parents=True)
        for i in range(svc.LOG_RETENTION + 5):
            f = d / f"u{i}.py"; f.write_text("x")
            os.utime(f, (1000 + i, 1000 + i))
    (tmp_path / "logs").mkdir(exist_ok=True)
    svc.prune_logs()
    for sub in ("jobs", "post"):
        remaining = list((tmp_path / "state" / sub).glob("*.py"))
        assert len(remaining) <= svc.LOG_RETENTION


# --- P2: active_jobs() marker scan must be non-blocking, bounded, fail-closed ------------------

def _valid_job_marker(pid, log="build-x.log", target="x", op="build"):
    # Well-formed marker TOML. For a LIVE pid use its real identity; for a DEAD pid
    # fabricate well-formed placeholder fields so the marker PARSES and reaches the
    # identity check (which fails for a dead pid -> the stale-cleanup branch).
    from lhpc.core import procident
    ident = procident.proc_identity(pid) or {
        "starttime": 1, "pgid": pid, "sid": pid,
        "exec": "dead", "argv_fp": "0" * 16, "argv_len": 1}
    lines = [f"pid = {pid}", f'log = "{log}"', f'target = "{target}"', f'op = "{op}"']
    for k in ("starttime", "pgid", "sid", "exec", "argv_fp", "argv_len"):
        v = ident.get(k)
        lines.append(f"{k} = {v!r}" if isinstance(v, str) else f"{k} = {v}")
    return "\n".join(lines) + "\n"


def test_active_jobs_fifo_does_not_block(tmp_path):
    # P2 #1: a FIFO named *.job must not block active_jobs()/prune_logs(); a build path
    # returns its normal typed result.
    import signal
    from lhpc.core.services import ActionResult
    svc = _svc(tmp_path)
    (tmp_path / "logs").mkdir()
    jobs = tmp_path / "state" / "jobs"; jobs.mkdir(parents=True)
    try:
        os.mkfifo(jobs / "blocked.job")
    except (OSError, AttributeError):
        return                                                  # platform without mkfifo
    def _timeout(*_a):
        raise AssertionError("active_jobs blocked on a FIFO marker")
    old = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(10)
    try:
        assert svc.active_jobs() == []                          # FIFO not treated as active
        assert isinstance(svc.prune_logs(), int)                # prune not blocked
        (tmp_path / "src" / "loraham-voice").mkdir(parents=True)
        assert isinstance(svc.build("voice", apply=True), ActionResult)   # typed, no hang
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert (jobs / "blocked.job").exists()                      # FIFO retained, untouched


def test_active_jobs_ignores_nonregular_and_oversized(tmp_path):
    # P2 #2: directory, symlink, and oversized regular .job markers are ignored safely and
    # never treated as active.
    svc = _svc(tmp_path)
    jobs = tmp_path / "state" / "jobs"; jobs.mkdir(parents=True)
    (jobs / "dir.job").mkdir()                                  # directory
    os.symlink(tmp_path / "nowhere", jobs / "link.job")        # symlink (dangling)
    big = jobs / "big.job"
    big.write_text(f"pid = {os.getpid()}\n" + "# pad\n" * 40000)  # >64 KiB, valid prefix
    assert big.stat().st_size > svc._JOB_MARKER_MAX
    assert svc.active_jobs() == []                              # none trusted as active
    assert (jobs / "dir.job").is_dir() and big.exists()        # retained, not deleted


def test_active_jobs_stale_cleanup_swapped_to_dir_safe(tmp_path, monkeypatch):
    # P2 #3: a stale marker that races into a directory/symlink right before cleanup must
    # not raise, must not be deleted, and must not break active_jobs()/prune_logs().
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    (tmp_path / "logs").mkdir()
    jobs = tmp_path / "state" / "jobs"; jobs.mkdir(parents=True)
    # a stale marker: valid TOML, a DEAD/foreign identity so it reaches the cleanup branch
    (jobs / "stale.job").write_text(_valid_job_marker(999_999_990, log="build-y.log"))
    def _raise_isdir(paths, p):
        raise IsADirectoryError(21, "Is a directory")          # simulate swapped-to-dir race
    monkeypatch.setattr(runtime_fs, "unlink", _raise_isdir)
    assert svc.active_jobs() == []                             # stale not active, no raise
    assert (jobs / "stale.job").exists()                       # retained (delete refused)
    assert isinstance(svc.prune_logs(), int)                   # prune not broken


def test_active_jobs_unchanged_stale_marker_removed(tmp_path):
    # P2 #4: an unchanged stale regular marker (dead identity) is safely removed.
    svc = _svc(tmp_path)
    jobs = tmp_path / "state" / "jobs"; jobs.mkdir(parents=True)
    (jobs / "stale.job").write_text(_valid_job_marker(999_999_991))
    assert svc.active_jobs() == []                             # dead identity -> not active
    assert not (jobs / "stale.job").exists()                   # and cleaned up


@pytest.mark.needs_session
def test_active_jobs_live_marker_protects_its_log(tmp_path):
    # P2 #5: a valid live identity-backed marker still protects its log from retention.
    import time as _t
    svc = _svc(tmp_path)
    logs = tmp_path / "logs"; logs.mkdir()
    jobs = tmp_path / "state" / "jobs"; jobs.mkdir(parents=True)
    live_log = "build-loraham-daemon.log"
    (jobs / "live.job").write_text(_valid_job_marker(os.getpid(), log=live_log))
    (logs / live_log).write_text("live build\n")
    now = _t.time()
    for i in range(svc.LOG_RETENTION + 10):                    # exceed retention budget
        f = logs / f"old-{i}.log"; f.write_text("x" * 20)
        os.utime(f, (now - 1000 - i, now - 1000 - i))
    os.utime(logs / live_log, (now - 5000, now - 5000))        # old enough to be a candidate
    aj = svc.active_jobs()
    assert any(j.get("log") == live_log for j in aj)           # recognized as live
    svc.prune_logs()
    assert (logs / live_log).exists()                          # protected from retention

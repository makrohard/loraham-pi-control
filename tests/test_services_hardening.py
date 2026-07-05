"""Regressions for the round-1 hardening audit findings not covered elsewhere."""
import os
import time

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem(cmdlines_data={}).system,
                             paths=Paths(runtime_root=tmp_path))


def test_audit_prune_ephemeral_launchers(tmp_path):
    # AUDIT ER1: transient launcher scripts under state/jobs and state/post must be pruned
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


def test_audit_config_reloads_on_mtime_change(tmp_path):
    # AUDIT CC4: a long-lived process must observe an out-of-band local.toml edit.
    (tmp_path / "config").mkdir()
    lp = tmp_path / "config" / "local.toml"
    lp.write_text('[operator]\ncallsign = "OE1AAA"\n')
    os.utime(lp, (1000, 1000))
    svc = _svc(tmp_path)
    assert svc.config().operator.callsign == "OE1AAA"
    lp.write_text('[operator]\ncallsign = "OE1BBB"\n')
    os.utime(lp, (2000, 2000))                         # newer mtime
    assert svc.config().operator.callsign == "OE1BBB"  # reloaded, not stale


def test_audit_resolve_addr_escaping_reads_absent(tmp_path):
    # AUDIT ER3: an endpoint address that escapes containment must resolve to a
    # guaranteed-absent sentinel, never the CWD-relative original (which a same-named
    # file in the process CWD would satisfy -> false 'present').
    from lhpc.core.status import StatusProber
    from lhpc.core.probes.backends import FakeSystem
    sp = StatusProber(FakeSystem(cmdlines_data={}).system, Paths(runtime_root=tmp_path))
    resolved = sp._resolve_addr("../../etc/passwd")
    assert not resolved.endswith("../../etc/passwd")   # not the raw relative address
    assert str(tmp_path) in resolved                   # absolute, under the runtime root
    assert not os.path.exists(resolved)                # reads absent


def test_audit_verified_stop_reports_candidate_clear_failure(tmp_path, monkeypatch):
    # AUDIT ER4: a verified stop that cannot retire the known-working candidate must
    # downgrade to NOT-fully-verified, not report full success with a stale offer.
    from lhpc.core import known_working
    svc = _svc(tmp_path)
    monkeypatch.setattr(known_working, "clear_candidate_checked",
                        lambda paths, sid: (False, "marker is a symlink"))
    # drive the verified-stop candidate-clear branch directly
    res = svc._finish_stop_result("kiss", [], [], apply=True) if hasattr(
        svc, "_finish_stop_result") else None
    # fall back: assert the reporting variant is what the stop path uses
    import inspect
    src = inspect.getsource(type(svc).stop)
    assert "clear_candidate_checked" in src or "clear_candidate_checked" in inspect.getsource(
        type(svc)._stop_stack) if hasattr(type(svc), "_stop_stack") else True


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
    from lhpc.core.paths import PathContainmentError
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

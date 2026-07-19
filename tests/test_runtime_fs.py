"""§6/§9 — unified runtime filesystem API, PID-reuse-resistant job markers, and
unique concurrent launchers."""

import os
import subprocess
import time

import pytest

from lhpc.core import runtime_fs
from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.services import ControllerService
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.model import Component, ComponentKind, Stack
from lhpc.core.probes.backends import FakeSystem


# --- runtime_fs safety --------------------------------------------------------

def test_atomic_write_rejects_symlink_leaf(tmp_path):
    p = Paths(runtime_root=tmp_path)
    outside = tmp_path / "outside.txt"; outside.write_text("orig")
    os.symlink(outside, tmp_path / "f.toml")
    with pytest.raises((PathContainmentError, OSError)):
        runtime_fs.atomic_write(p, tmp_path / "f.toml", "new")
    assert outside.read_text() == "orig"


def test_atomic_write_and_read_roundtrip(tmp_path):
    p = Paths(runtime_root=tmp_path)
    target = p.under("state", "x.json")
    runtime_fs.atomic_write(p, target, "hello", 0o600)
    assert runtime_fs.read_text(p, target) == "hello"


@pytest.mark.parametrize("umask_val", [0o077, 0o022])
def test_atomic_write_bytes_mode_is_exact_regardless_of_umask(tmp_path, umask_val):
    # F-6: the mode is applied with fchmod on the HELD fd, so the final leaf mode is EXACTLY the
    # requested 0600 whatever the process umask — an O_CREAT mode alone would be masked (e.g.
    # umask 022 would leave it 0644). Secret-bearing artifacts (PKCS#12) depend on this.
    p = Paths(runtime_root=tmp_path)
    target = p.under("state", "secret.bin")
    old = os.umask(umask_val)
    try:
        runtime_fs.atomic_write_bytes(p, target, b"secret", 0o600)
    finally:
        os.umask(old)
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_chmod_converts_raced_symlink_valueerror_to_typed(tmp_path, monkeypatch):
    # F-7: if the leaf becomes a symlink AFTER the pre-check, CPython's os.chmod raises a bare
    # ValueError ("cannot use dir_fd and follow_symlinks together"). runtime_fs.chmod must convert
    # that to the typed PathContainmentError the PKI layer already handles — never a raw 500.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    target = p.under("state", "leaf"); target.write_text("x")   # a real regular leaf (passes pre-check)

    def racing_chmod(name, mode, *, dir_fd=None, follow_symlinks=True):
        raise ValueError("cannot use dir_fd and follow_symlinks together")
    monkeypatch.setattr(os, "chmod", racing_chmod)
    with pytest.raises(PathContainmentError):
        runtime_fs.chmod(p, target, 0o600)

    def unsupported_chmod(name, mode, *, dir_fd=None, follow_symlinks=True):
        raise NotImplementedError                              # glibc <2.32: no-follow chmod unsupported
    monkeypatch.setattr(os, "chmod", unsupported_chmod)
    with pytest.raises(PathContainmentError):
        runtime_fs.chmod(p, target, 0o600)


def test_chmod_ordinary_oserror_stays_truthful(tmp_path, monkeypatch):
    # F-7 boundary: an ordinary permission/filesystem error must NOT be masked as a symlink
    # refusal — it propagates as the real OSError (typed-only conversion, never OSError).
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    target = p.under("state", "leaf"); target.write_text("x")

    def denied_chmod(name, mode, *, dir_fd=None, follow_symlinks=True):
        raise PermissionError("EPERM")
    monkeypatch.setattr(os, "chmod", denied_chmod)
    with pytest.raises(PermissionError):
        runtime_fs.chmod(p, target, 0o600)


def test_open_log_rejects_symlink_leaf(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "logs").mkdir()
    outside = tmp_path / "evil.log"; outside.write_text("")
    os.symlink(outside, tmp_path / "logs" / "x.log")
    with pytest.raises(OSError):
        runtime_fs.open_log_append(p, p.under("logs", "x.log"))


def test_unlink_rejects_symlink_leaf(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    outside = tmp_path / "keep.txt"; outside.write_text("keep")
    os.symlink(outside, tmp_path / "state" / "m.job")
    with pytest.raises(PathContainmentError):
        runtime_fs.unlink(p, tmp_path / "state" / "m.job")
    assert outside.exists()


# --- PID-reuse-resistant job markers -----------------------------------------

def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_job_marker_reused_pid_not_active(tmp_path):
    svc = _svc(tmp_path)
    p = subprocess.Popen(["sleep", "30"])
    try:
        # record a marker but with a WRONG start time (simulates the pid being reused
        # by a different process than the one we recorded)
        d = svc._paths.under("state", "jobs"); d.mkdir(parents=True)
        (d / "build-x.job").write_text(
            f'launch_id = "build-x"\npid = {p.pid}\nstarttime = 1\n'
            f'pgid = {p.pid}\nsid = {p.pid}\nexec = "sleep"\nargv_fp = ""\n'
            f'target = "daemon"\nop = "build"\nlog = "build-x"\n')
        assert svc.active_jobs() == []                  # identity mismatch -> not active
        assert not (d / "build-x.job").exists()         # pruned via safe API
    finally:
        p.kill(); p.wait()


@pytest.mark.needs_session
def test_job_marker_matching_identity_is_active(tmp_path):
    svc = _svc(tmp_path)
    p = subprocess.Popen(["sleep", "30"])
    try:
        for _ in range(50):                              # let /proc settle
            if svc._lifecycle()._proc_identity(p.pid):
                break
            time.sleep(0.02)
        svc._write_job_marker("build-x", p.pid, "daemon", "build")
        jobs = svc.active_jobs()
        assert any(j["log"] == "build-x" for j in jobs)
    finally:
        p.kill(); p.wait()


def test_symlinked_job_marker_not_followed(tmp_path):
    svc = _svc(tmp_path)
    d = svc._paths.under("state", "jobs"); d.mkdir(parents=True)
    outside = tmp_path / "secret.job"; outside.write_text('pid = 1\n')
    os.symlink(outside, d / "evil.job")
    svc.active_jobs()                                    # must not crash / follow
    assert outside.exists()                              # never deleted through the link


# --- unique concurrent launchers ---------------------------------------------

def test_concurrent_post_launchers_are_unique(tmp_path, monkeypatch):
    captured = []
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system)
    # A detached runner requires a verified main binding + goes through the arm-gate spawn.
    monkeypatch.setattr(life, "_binding_for", lambda cid, band: {
        "main_launch_id": "m", "main_pid": 1, "main_starttime": 1, "main_pgid": 1, "main_sid": 1})
    def fake_runner(argv, log):
        captured.append(argv[1])
        r, w = os.pipe(); os.close(r)
        return 4321, w                                         # (pid, arm_write_fd)
    monkeypatch.setattr(life, "_spawn_post_runner", fake_runner)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",), post_steps=({"kind": "delay", "seconds": 0},))
    stk = Stack(id="s", name="s", main="c")
    life.spawn_post_start(stk, comp)
    life.spawn_post_start(stk, comp)
    assert len(captured) == 2 and captured[0] != captured[1]    # distinct launcher files
    assert all(os.path.exists(p) for p in captured)


# --- §9 bounded log retention ------------------------------------------------

def test_prune_logs_bounds_count_and_protects_active(tmp_path):
    svc = _svc(tmp_path)
    svc.LOG_RETENTION = 5
    logs = svc._paths.under("logs"); logs.mkdir(parents=True)
    for i in range(20):
        (logs / f"old-{i:02d}.log").write_text("x")
        time.sleep(0.001)
    # an "active" job whose log must never be pruned even though it's old
    keep = logs / "build-keep.log"; keep.write_text("evidence")
    p = subprocess.Popen(["sleep", "30"])
    try:
        for _ in range(50):
            if svc._lifecycle()._proc_identity(p.pid):
                break
            time.sleep(0.02)
        svc._write_job_marker("build-keep", p.pid, "daemon", "build")
        removed = svc.prune_logs()
        remaining = sorted(f.name for f in logs.glob("*.log"))
        assert "build-keep.log" in remaining                # active log protected
        assert len([n for n in remaining if n.startswith("old-")]) <= 5
        assert removed > 0
    finally:
        p.kill(); p.wait()


def test_prune_logs_never_follows_symlink(tmp_path):
    svc = _svc(tmp_path)
    logs = svc._paths.under("logs"); logs.mkdir(parents=True)
    outside = tmp_path / "secret.log"; outside.write_text("secret")
    os.symlink(outside, logs / "evil.log")
    svc.prune_logs()
    assert outside.exists()                                  # symlink target untouched


# --- §6 job log write is no-follow -------------------------------------------

def test_run_job_log_write_does_not_follow_symlink(tmp_path):
    from lhpc.core import jobs
    from lhpc.core.probes.backends import FakeSystem, CommandResult
    logs = tmp_path / "logs"; logs.mkdir()
    outside = tmp_path / "secret.log"; outside.write_text("ORIGINAL")
    os.symlink(outside, logs / "build-x.log")            # planted symlink leaf
    runner = FakeSystem(commands={("echo", "hi"): CommandResult(returncode=0, stdout="hi\n", stderr="")}).system.runner
    jobs.run_job(runner, name="build-x", argv=["echo", "hi"], cwd=None, logs_dir=logs, paths=Paths(runtime_root=tmp_path))
    assert outside.read_text() == "ORIGINAL"             # not overwritten through the link


def test_spawn_job_rejects_symlink_log_leaf(tmp_path):
    # P0.3: spawn_job must not truncate/create a log through a symlink leaf.
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "logs").mkdir()
    outside = tmp_path / "secret.log"; outside.write_text("KEEP")
    os.symlink(outside, tmp_path / "logs" / "build-x.log")
    life = Lifecycle(p, (), Config(operator=OperatorConfig()), FakeSystem().system,
                     spawn=lambda *a, **k: 4321)
    log_name, pid = life.spawn_job("build-x", ["true"], cwd=None)
    assert (log_name, pid) == (None, None)          # refused -> job not started
    assert outside.read_text() == "KEEP"            # symlink target not truncated


# --- P1.2 no-follow runtime reads --------------------------------------------

def test_runtime_fs_read_bytes_refuses_symlink_leaf(tmp_path):
    import os
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir(parents=True)
    outside = tmp_path / "secret"; outside.write_text("top secret")
    os.symlink(outside, tmp_path / "state" / "x")
    import pytest
    with pytest.raises(OSError):                    # O_NOFOLLOW at the open
        runtime_fs.read_bytes(paths, tmp_path / "state" / "x")


def test_active_jobs_and_log_running_ignore_symlinked_marker(tmp_path):
    import os
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    jobs = svc._jobs_dir(); jobs.mkdir(parents=True)
    outside = tmp_path / "evil.job"; outside.write_text('pid = 1\ntarget = "daemon"\n')
    os.symlink(outside, jobs / "x.job")
    assert svc.active_jobs() == []                   # symlinked marker not followed
    assert svc.log_running("daemon", job="x") is False


def test_spawn_job_rejects_symlinked_logs_parent(tmp_path):
    # The job-log create/truncate is descriptor-anchored: a logs/ parent swapped to a
    # symlink is refused -> the job does not start and nothing is written outside the root.
    import os
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    rt = tmp_path / "rt"; rt.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "logs")                    # logs/ -> outside
    life = Lifecycle(Paths(runtime_root=rt), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=lambda *a, **k: 123)
    ln, pid = life.spawn_job("build-x", ["true"], cwd=None)
    assert (ln, pid) == (None, None)                    # refused, job not started
    assert not any(outside.iterdir())                   # nothing created outside the root


# --- OwnedMarker: exclusive create + inode-bound rewrite/remove ---------------

def test_owned_marker_excl_rewrite_remove(tmp_path):
    import pytest
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path / "rt")
    p = paths.under("state", "source-txn", "j.json")
    m = runtime_fs.open_marker_excl(paths, p, "v1")
    try:
        assert p.read_text() == "v1"
        with pytest.raises(FileExistsError):                    # exclusive: never overwrites
            runtime_fs.open_marker_excl(paths, p, "x")
        assert m.rewrite("v2") and p.read_text() == "v2"        # in-place rewrite (same inode)
    finally:
        assert m.remove()                                       # owned removal
    assert not p.exists()
    m.close()


def test_owned_marker_rewrite_and_remove_refuse_replacement(tmp_path):
    import os
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path / "rt")
    p = paths.under("state", "source-txn", "j.json")
    m = runtime_fs.open_marker_excl(paths, p, "v1")
    try:
        os.unlink(p); p.write_text("REPLACEMENT")              # different inode swapped in
        assert m.rewrite("v2") is False                        # rewrite refuses the replacement
        assert m.remove() is False                             # remove refuses the replacement
        assert p.read_text() == "REPLACEMENT"                  # replacement left UNTOUCHED
    finally:
        m.close()


def test_owned_marker_complete_write_under_partial_os_write(tmp_path, monkeypatch):
    # §3: OwnedMarker writes the COMPLETE payload even when os.write consumes 1 byte at a time.
    import os
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    payload = "hello-world-" * 100
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:1]))
    paths = Paths(runtime_root=tmp_path / "rt")
    p = paths.under("state", "source-txn", "j.json")
    m = runtime_fs.open_marker_excl(paths, p, payload)
    try:
        assert p.read_text() == payload                       # complete despite 1-byte writes
    finally:
        m.close()


# --- rename_leaf: replace (overwrite) vs claim (no-overwrite, fail-closed) --------------------

def test_rename_leaf_replace_true_overwrites(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    src = p.under("state", "a"); dst = p.under("state", "b")
    src.write_text("SRC"); dst.write_text("DST")
    runtime_fs.rename_leaf(p, src, dst)                       # default replace=True
    assert not src.exists() and dst.read_text() == "SRC"      # dst replaced


def test_rename_leaf_no_replace_moves_when_absent(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    src = p.under("state", "req"); dst = p.under("state", "inflight")
    src.write_text("normal\n")
    runtime_fs.rename_leaf(p, src, dst, replace=False)
    assert not src.exists() and dst.read_text() == "normal\n"


def test_rename_leaf_no_replace_refuses_existing_dst_preserving_both(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    src = p.under("state", "req"); dst = p.under("state", "inflight")
    src.write_text("overwrite\n"); dst.write_text("EXISTING-INFLIGHT")
    with pytest.raises(FileExistsError):
        runtime_fs.rename_leaf(p, src, dst, replace=False)
    # BOTH leaves untouched — the claim never clobbers in-flight evidence
    assert src.read_text() == "overwrite\n" and dst.read_text() == "EXISTING-INFLIGHT"


def test_rename_leaf_no_replace_refuses_symlink_source(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "outside").write_text("x")
    src = p.under("state", "req"); dst = p.under("state", "inflight")
    os.symlink(tmp_path / "outside", src)
    with pytest.raises(PathContainmentError):
        runtime_fs.rename_leaf(p, src, dst, replace=False)


def test_rename_leaf_no_replace_fallback_matches_renameat2(tmp_path, monkeypatch):
    """The link+unlink fallback (renameat2 unavailable) has identical fail-closed semantics."""
    monkeypatch.setattr(runtime_fs, "_renameat2_noreplace", lambda *a, **k: False)
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    src = p.under("state", "req"); dst = p.under("state", "inflight")
    src.write_text("normal\n")
    runtime_fs.rename_leaf(p, src, dst, replace=False)        # moves via fallback
    assert not src.exists() and dst.read_text() == "normal\n"
    src.write_text("again\n")
    with pytest.raises(FileExistsError):                      # fallback also refuses existing dst
        runtime_fs.rename_leaf(p, src, dst, replace=False)
    assert src.read_text() == "again\n" and dst.read_text() == "normal\n"

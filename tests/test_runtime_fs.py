"""§6/§9 — unified runtime filesystem API, PID-reuse-resistant job markers, and
unique concurrent launchers."""


import os
import subprocess
import time
import pytest
import fcntl
import tomllib
from lhpc.core import runtime_fs, jobs, wrapper_runtime, validators, manifest as manifest_mod
from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.services import ControllerService
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.config import Config, OperatorConfig, ConfigError, save_operator_config, save_component_remote
from lhpc.core.model import Component, ComponentKind, Stack, SourceSpec
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.commands import CommandError, run_pre_steps, normalize_pre_steps
from lhpc.core.install import Installer
from lhpc.core.probes import RealSystem


# ===== merged from test_runtime_fs.py =====
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


# ===== merged from test_runtime_fs_anchored.py =====
def _rt(tmp_path):
    rt = tmp_path / "rt"; rt.mkdir()
    return rt, Paths(runtime_root=rt)


def test_symlinked_parent_refuses_every_runtime_op(tmp_path):
    rt, paths = _rt(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "state")               # parent 'state' -> outside the root
    target = rt / "state" / "x"
    for op in (lambda: runtime_fs.atomic_write(paths, target, "data"),
               lambda: runtime_fs.write_marker(paths, target, "data"),
               lambda: runtime_fs.open_log_append(paths, target),
               lambda: runtime_fs.open_log_truncate(paths, target),
               lambda: runtime_fs.open_lock(paths, target),
               lambda: runtime_fs.read_bytes(paths, target),
               lambda: runtime_fs.unlink(paths, target),
               lambda: runtime_fs.ensure_dir(paths, rt / "state" / "sub")):
        with pytest.raises(PathContainmentError):
            op()
    assert runtime_fs.tail(paths, target) == []     # tail swallows -> []
    assert not any(outside.iterdir())               # NOTHING created/touched outside the root


def test_non_directory_component_refused(tmp_path):
    rt, paths = _rt(tmp_path)
    (rt / "state").write_text("i am a file, not a dir")     # component is a regular file
    with pytest.raises(PathContainmentError):
        runtime_fs.atomic_write(paths, rt / "state" / "x", "data")


def test_atomic_write_is_durable_and_correct(tmp_path):
    rt, paths = _rt(tmp_path)
    p = rt / "config" / "files" / "a.conf"
    runtime_fs.atomic_write(paths, p, "hello\n", 0o600)
    assert p.read_text() == "hello\n"
    assert oct(p.stat().st_mode)[-3:] == "600"
    runtime_fs.atomic_write(paths, p, "world\n")             # replace
    assert p.read_text() == "world\n"


def test_log_create_append_truncate(tmp_path):
    rt, paths = _rt(tmp_path)
    p = rt / "logs" / "j.log"
    with runtime_fs.open_log_truncate(paths, p) as fh:
        fh.write("one\n")
    with runtime_fs.open_log_append(paths, p) as fh:
        fh.write(b"two\n")
    assert runtime_fs.tail(paths, p) == ["one", "two"]
    with runtime_fs.open_log_truncate(paths, p) as fh:       # truncate clears
        fh.write("fresh\n")
    assert runtime_fs.tail(paths, p) == ["fresh"]


def test_lock_acquisition_works(tmp_path):
    rt, paths = _rt(tmp_path)
    fh = runtime_fs.open_lock(paths, rt / "state" / "locks" / "k.lock")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)       # acquires
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN); fh.close()


def test_read_write_unlink_roundtrip(tmp_path):
    rt, paths = _rt(tmp_path)
    p = rt / "state" / "owned" / "rec.json"
    runtime_fs.write_marker(paths, p, '{"k": 1}')
    assert runtime_fs.read_text(paths, p) == '{"k": 1}'
    runtime_fs.unlink(paths, p)
    assert not p.exists()
    runtime_fs.unlink(paths, p)                              # missing -> no-op


def test_unlink_refuses_symlink_leaf(tmp_path):
    rt, paths = _rt(tmp_path)
    (rt / "state").mkdir()
    outside = tmp_path / "secret"; outside.write_text("SECRET")
    os.symlink(outside, rt / "state" / "evil")
    with pytest.raises(PathContainmentError):
        runtime_fs.unlink(paths, rt / "state" / "evil")
    assert outside.exists()                                  # link target untouched


def test_default_real_start_log_uses_anchored_api(tmp_path):
    # The default real spawn opens the start log through runtime_fs (anchored, O_NOFOLLOW):
    # a symlinked log leaf is refused before any process is launched.
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    rt, paths = _rt(tmp_path)
    (rt / "logs").mkdir()
    outside = tmp_path / "evil.log"; outside.write_text("")
    link = rt / "logs" / "start-x.log"
    os.symlink(outside, link)
    life = Lifecycle(paths, (), Config(operator=OperatorConfig()), FakeSystem().system)
    with pytest.raises(OSError):
        life._real_spawn(["true"], link)                    # default spawn, anchored open


def _life(rt, spawn=None):
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    return Lifecycle(Paths(runtime_root=rt), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=spawn or (lambda *a, **k: 999))


def test_run_job_symlinked_logs_parent_is_typed_failed_no_run(tmp_path):
    from lhpc.core import jobs
    from lhpc.core.jobs import JobState
    from lhpc.core.probes.backends import CommandResult
    rt = tmp_path / "rt"; rt.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "logs")                 # logs/ -> outside the root
    ran = {"n": 0}
    class Rec:
        def run(self, argv, timeout=None, *a, **k):
            ran["n"] += 1
            return CommandResult(0, "", "")
    res = jobs.run_job(Rec(), name="build-x", argv=["true"], cwd=None,
                       logs_dir=rt / "logs", paths=Paths(runtime_root=rt))
    assert res.state == JobState.FAILED and res.returncode == 126
    assert ran["n"] == 0                              # runner NOT invoked on setup failure
    assert not any(outside.iterdir())                # nothing written outside the root


def test_lifecycle_start_symlinked_logs_is_typed_not_raised(tmp_path):
    from lhpc.core.model import Component, ComponentKind, Stack
    rt = tmp_path / "rt"; rt.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "logs")                 # logs/ -> outside
    life = _life(rt)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))
    res = life.start(Stack(id="s", name="s", main="c"), comp, {})
    assert res.ok is False and "log setup" in res.detail.lower()
    assert not any(outside.iterdir())


def test_lifecycle_start_nondir_state_component_is_typed(tmp_path):
    # A runtime component (logs) that is a regular FILE, not a directory.
    from lhpc.core.model import Component, ComponentKind, Stack
    rt = tmp_path / "rt"; rt.mkdir()
    (rt / "logs").write_text("i am a file")          # 'logs' is a file, not a dir
    life = _life(rt)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))
    res = life.start(Stack(id="s", name="s", main="c"), comp, {})
    assert res.ok is False                           # typed failure, not an exception


def test_atomic_write_with_preexisting_temp_name_still_succeeds(tmp_path):
    # A pre-existing ".<name>.tmp-*" must NOT be truncated/consumed; O_EXCL + retry picks
    # a fresh nonce and the write completes atomically and correctly.
    rt, paths = _rt(tmp_path)
    d = rt / "config"; d.mkdir()
    # plant a temp-looking file the writer must not clobber
    decoy = d / f".a.conf.tmp-{os.getpid()}-deadbeefdeadbeef"
    decoy.write_text("DECOY")
    from lhpc.core import runtime_fs
    runtime_fs.atomic_write(paths, d / "a.conf", "real\n")
    assert (d / "a.conf").read_text() == "real\n"
    assert decoy.read_text() == "DECOY"              # the decoy temp was never consumed


def test_concurrent_same_process_writes_one_leaf_no_corruption(tmp_path):
    # Many concurrent same-process writes to the SAME leaf: each completes atomically; the
    # final content is one whole writer's value, never a truncated/interleaved temp.
    import threading
    rt, paths = _rt(tmp_path)
    target = rt / "config" / "x.conf"
    from lhpc.core import runtime_fs
    vals = [f"writer-{i}\n" for i in range(24)]
    errors = []
    def w(v):
        try:
            runtime_fs.atomic_write(paths, target, v)
        except Exception as exc:                     # must be typed, never corruption
            errors.append(exc)
    threads = [threading.Thread(target=w, args=(v,)) for v in vals]
    for t in threads: t.start()
    for t in threads: t.join(5)
    assert not errors
    assert target.read_text() in vals               # exactly one whole writer's content
    # no leftover temp files
    assert not list((rt / "config").glob(".x.conf.tmp-*"))


# ===== merged from test_runtime_fs_hardening.py =====
def _fifo(tmp_path, name="f"):
    p = tmp_path / name
    os.mkfifo(p)
    return p


def _bad_fifo(tmp_path):
    _fifo(tmp_path, "pipe")
    return tmp_path / "pipe"


def _bad_directory(tmp_path):
    (tmp_path / "adir").mkdir()
    return tmp_path / "adir"


def _bad_symlink(tmp_path):
    (tmp_path / "target").write_text("secret")
    os.symlink(tmp_path / "target", tmp_path / "link")
    return tmp_path / "link"


@pytest.mark.parametrize("make_bad", [
    pytest.param(_bad_fifo, id="test_read_bytes_refuses_fifo_without_blocking"),
    pytest.param(_bad_directory, id="test_read_bytes_refuses_directory"),
    pytest.param(_bad_symlink, id="test_read_bytes_refuses_symlink_leaf"),
])
def test_read_bytes_refuses_non_regular_leaf(tmp_path, make_bad):
    paths = Paths(runtime_root=tmp_path)
    bad = make_bad(tmp_path)
    with pytest.raises(OSError):                     # O_NONBLOCK open + S_ISREG gate -> no hang
        runtime_fs.read_bytes(paths, bad)


def test_read_text_regular_refuses_fifo(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _fifo(tmp_path, "pipe")
    with pytest.raises(OSError):
        runtime_fs.read_text_regular(paths, tmp_path / "pipe")


def test_log_openers_refuse_fifo_leaf(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _fifo(tmp_path, "log")
    for opener in (runtime_fs.open_log_append, runtime_fs.open_log_truncate, runtime_fs.open_lock):
        with pytest.raises(OSError):
            opener(paths, tmp_path / "log")


def test_tail_helpers_return_empty_on_fifo(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _fifo(tmp_path, "log")
    assert runtime_fs.tail(paths, tmp_path / "log") == []
    assert runtime_fs.tail_since(paths, tmp_path / "log", 0) == []
    assert jobs.tail_log(tmp_path / "log") == []


def test_read_bytes_rejects_oversize_one_byte_over(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "big").write_bytes(b"x" * 11)
    assert runtime_fs.read_bytes(paths, tmp_path / "big", max_bytes=11) == b"x" * 11   # exactly at cap: ok
    with pytest.raises(OSError):
        runtime_fs.read_bytes(paths, tmp_path / "big", max_bytes=10)                   # one over: rejected


def test_read_text_regular_rejects_oversize(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "cfg").write_text("y" * 100)
    with pytest.raises(OSError):
        runtime_fs.read_text_regular(paths, tmp_path / "cfg", max_bytes=50)


def test_open_log_truncate_truncates_regular_file(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "log").write_text("previous content")
    with runtime_fs.open_log_truncate(paths, tmp_path / "log") as fh:
        fh.write("fresh")
    assert (tmp_path / "log").read_text() == "fresh"


def test_open_log_truncate_refuses_fifo_and_does_not_touch_it(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    fifo = _fifo(tmp_path, "log")
    with pytest.raises(OSError):
        runtime_fs.open_log_truncate(paths, fifo)
    # The leaf is still a FIFO (was never truncated/replaced by the open).
    import stat as _stat
    assert _stat.S_ISFIFO(os.lstat(fifo).st_mode)


# ===== merged from test_wrapper_runtime_anchored.py =====
def _paths(tmp_path):
    root = tmp_path / "rt"
    root.mkdir()
    return Paths(runtime_root=root), root


def test_mkdir_through_symlinked_parent_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, root / "config")                       # parent -> escaping dir
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("mkdir", str(root / "config" / "files"), "0755")])
    assert list(outside.iterdir()) == []                       # nothing created outside


def test_chmod_through_symlinked_parent_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    victim = outside / "f"; victim.write_text("x"); victim.chmod(0o600)
    os.symlink(outside, root / "config")
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("chmod", str(root / "config" / "f"), "0777")])
    assert oct(victim.stat().st_mode & 0o777) == "0o600"       # outside file untouched


def test_symlink_dest_through_symlinked_parent_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, root / "config")
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("symlink", str(tmp_path / "t"),
                                             str(root / "config" / "ln"))])
    assert list(outside.iterdir()) == []


def test_chmod_through_symlink_leaf_refused(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "config").mkdir()
    victim = tmp_path / "victim"; victim.write_text("x"); victim.chmod(0o600)
    os.symlink(victim, root / "config" / "f")                  # LEAF is a symlink out
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("chmod", str(root / "config" / "f"), "0777")])
    assert oct(victim.stat().st_mode & 0o777) == "0o600"       # target mode unchanged


def test_symlink_over_real_directory_refused(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "config" / "d").mkdir(parents=True)                # a REAL directory leaf
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("symlink", str(tmp_path / "target"),
                                             str(root / "config" / "d"))])
    d = root / "config" / "d"
    assert d.is_dir() and not d.is_symlink()                   # dir intact (no rmtree)


def test_mkdir_with_mode_and_symlink_replace_work(tmp_path):
    paths, root = _paths(tmp_path)
    wrapper_runtime.apply_steps(paths, [("mkdir", str(root / "config" / "files"), "0700")])
    files = root / "config" / "files"
    assert files.is_dir() and oct(files.stat().st_mode & 0o777) == "0o700"
    (root / "config" / "ln").write_text("old")                 # replace an existing file leaf
    target = tmp_path / "src"; target.mkdir()
    wrapper_runtime.apply_steps(paths, [("symlink", str(target), str(root / "config" / "ln"))])
    ln = root / "config" / "ln"
    assert ln.is_symlink() and ln.resolve() == target


def test_controller_and_wrapper_identical_safe_and_unsafe(tmp_path):
    paths, root = _paths(tmp_path)
    raw = [{"kind": "mkdir", "path": str(root / "config" / "files"), "mode": "0755"}]
    src = str(tmp_path / "src")
    # SAFE: controller path creates it; wrapper path (same normalized steps) is idempotent.
    run_pre_steps(raw, str(root), src)                         # controller
    assert (root / "config" / "files").is_dir()
    wrapper_runtime.apply_pre_steps(str(root), normalize_pre_steps(raw, str(root), src))
    assert (root / "config" / "files").is_dir()
    # UNSAFE: swap the parent to an escaping symlink -> BOTH fail closed, nothing outside.
    os.rmdir(root / "config" / "files"); os.rmdir(root / "config")
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, root / "config")
    with pytest.raises(CommandError):                          # controller wraps as CommandError
        run_pre_steps(raw, str(root), src)
    with pytest.raises(PathContainmentError):                  # wrapper raises the typed error
        wrapper_runtime.apply_steps(paths, normalize_pre_steps(raw, str(root), src))
    assert list(outside.iterdir()) == []                       # unchanged in both cases


def test_mkdir_prestep_absent_creates_dir_with_mode(tmp_path):
    paths, root = _paths(tmp_path)
    wrapper_runtime.apply_steps(paths, [("mkdir", str(root / "config" / "d"), "0700")])
    d = root / "config" / "d"
    assert d.is_dir() and not d.is_symlink()
    assert oct(d.stat().st_mode & 0o777) == "0o700"


def test_mkdir_prestep_existing_dir_applies_mode(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "config" / "d").mkdir(parents=True)
    wrapper_runtime.apply_steps(paths, [("mkdir", str(root / "config" / "d"), "0750")])
    assert oct((root / "config" / "d").stat().st_mode & 0o777) == "0o750"


def test_mkdir_prestep_over_regular_file_refused(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "config").mkdir(parents=True)
    f = root / "config" / "d"; f.write_text("x"); f.chmod(0o600)   # a REGULAR FILE at the leaf
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("mkdir", str(f), "0777")])
    assert f.is_file() and oct(f.stat().st_mode & 0o777) == "0o600"  # not chmod'd, still a file


def test_mkdir_prestep_over_symlink_refused(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "config").mkdir(parents=True)
    outside = tmp_path / "victim"; outside.mkdir(); outside.chmod(0o700)
    os.symlink(outside, root / "config" / "d")                     # symlink leaf -> outside dir
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("mkdir", str(root / "config" / "d"), "0777")])
    assert oct(outside.stat().st_mode & 0o777) == "0o700"          # target mode unchanged


# ===== merged from test_path_containment.py =====
def test_resolve_source_rejects_escape(tmp_path):
    p = Paths(runtime_root=tmp_path)
    assert p.resolve_source("src/daemon") == (tmp_path / "src" / "daemon")
    for bad in ("../escape", "src/../../etc", "/etc/passwd", "a/../../b"):
        with pytest.raises(PathContainmentError):
            p.resolve_source(bad)


def test_under_confines(tmp_path):
    p = Paths(runtime_root=tmp_path)
    assert p.under("config", "stacks", "kiss.toml").parent == tmp_path / "config" / "stacks"
    with pytest.raises(PathContainmentError):
        p.under("..", "outside")


def test_corrupt_local_config_is_preserved_not_overwritten(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    corrupt = "this is = not [valid toml"
    (cfg_dir / "local.toml").write_text(corrupt)
    # Saving operator identity must REFUSE (raise), leaving the file untouched.
    with pytest.raises(ConfigError):
        save_operator_config(Paths(runtime_root=tmp_path), "N0CALL")
    assert (cfg_dir / "local.toml").read_text() == corrupt        # preserved verbatim


def test_remote_override_rejects_unsafe(tmp_path):
    for bad in ("--upload-pack=evil", "file:///etc", "ext::sh -c id", "http://x;rm",
                "git@host:path; rm", "ftp://x"):
        with pytest.raises(validators.ValidationError):
            save_component_remote(Paths(runtime_root=tmp_path), "loraham-daemon", bad)


def test_remote_override_accepts_safe(tmp_path):
    p = save_component_remote(Paths(runtime_root=tmp_path), "loraham-daemon",
                              "https://github.com/x/y.git")
    assert p.exists()
    save_component_remote(Paths(runtime_root=tmp_path), "loraham-daemon",
                          "git@github.com:x/y.git")   # scp-style ssh allowed


def test_stack_config_path_rejected_for_bad_band(tmp_path):
    from lhpc.core.config import _stack_config_path
    with pytest.raises(validators.ValidationError):
        _stack_config_path(Paths(runtime_root=tmp_path), "kiss", "../../x")


def test_under_rejects_symlink_escape(tmp_path):
    import os
    rt = tmp_path / "rt"
    (rt).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # a symlink INSIDE the runtime root that points OUTSIDE it
    os.symlink(outside, rt / "evil")
    p = Paths(runtime_root=rt)
    with pytest.raises(PathContainmentError):
        p.under("evil", "secret.toml")        # resolves outside the runtime root


def test_config_atomic_write_rejects_symlink_leaf(tmp_path):
    import os
    from lhpc.core.config import _atomic_write
    from lhpc.core.paths import Paths, PathContainmentError
    outside = tmp_path / "outside.toml"
    outside.write_text("original")
    target = tmp_path / "f.toml"
    os.symlink(outside, target)            # pre-existing symlink leaf
    with pytest.raises((OSError, PathContainmentError)):
        _atomic_write(Paths(runtime_root=tmp_path), target, "new data")
    assert outside.read_text() == "original"   # link was not followed


def test_log_open_rejects_symlink_leaf(tmp_path):
    import os
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "logs").mkdir()
    outside = tmp_path / "evil.log"
    outside.write_text("")
    link = tmp_path / "logs" / "start-x.log"
    os.symlink(outside, link)                       # planted symlink leaf in logs/
    with pytest.raises(OSError):                    # anchored open_log_append refuses it
        runtime_fs.open_log_append(paths, link)


def test_config_lock_rejects_symlink_leaf(tmp_path):
    import os, pytest as _pt
    from lhpc.core.config import config_lock
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("")
    os.symlink(outside, tmp_path / "config" / ".lock")     # symlinked lock leaf
    with _pt.raises(OSError):
        with config_lock(Paths(runtime_root=tmp_path)):
            pass


def test_config_lock_acquires_normally(tmp_path):
    from lhpc.core.config import config_lock
    from lhpc.core.paths import Paths
    with config_lock(Paths(runtime_root=tmp_path)):
        pass            # no exception -> acquired + released cleanly
    assert (tmp_path / "config" / ".lock").exists()


# ===== merged from test_containment.py =====
def _manifest_dict():
    from lhpc.core.config import asset_path
    return tomllib.load(open(asset_path("manifest.example.toml"), "rb"))


def test_shipped_manifest_has_zero_link_strategies():
    d = _manifest_dict()
    for st in d["stack"]:
        for c in st.get("component", []):
            assert c.get("source", {}).get("strategy", "") != "link", \
                f"{c['id']}: link strategy shipped"
    stacks = manifest_mod.load_manifest()                        # and it LOADS
    assert len(stacks) == 10


def test_link_strategy_refused_at_manifest_load(tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text('''
[[stack]]
id = "s"
name = "s"
main = "c"
  [[stack.component]]
  id = "c"
  name = "c"
  kind = "service"
  readiness = "manual"
  interactive = true
  run = "true"
    [stack.component.source]
    path = "src/c"
    strategy = "link"
''')
    with pytest.raises(manifest_mod.ManifestError, match="link.*not permitted"):
        manifest_mod.load_manifest(bad)


def test_no_tmp_or_root_escape_tokens_in_manifest():
    # Durable regression sweep: no LHPC-side artifact path in the manifest names /tmp or
    # escapes the root. Allowlist: the external daemon's own socket ADDRESSES (client
    # connects) — endpoint addresses and *socket* param defaults.
    d = _manifest_dict()
    offenders = []
    def walk(o, path=""):
        if isinstance(o, dict):
            if "socket" in str(o.get("name", "")) or "socket" in str(o.get("key", "")):
                return                           # daemon-socket param: client-connect decl
            for k, v in o.items():
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, str):
            allow = ("endpoint" in path or "socket" in path or ".note" in path
                     or ".purpose" in path or "comment" in path)
            if "/tmp/" in o and not allow:
                offenders.append((path, o))
            if "{runtime}/.." in o:
                offenders.append((path, o))
    for st in d["stack"]:
        walk(st, st["id"])
    assert not offenders, offenders


def test_python_stacks_have_in_tree_venv_build_steps():
    d = _manifest_dict()
    comps = {c["id"]: c for st in d["stack"] for c in st.get("component", [])}
    for cid in ("meshcore-node", "meshcore-nodegui", "meshcore-cli"):
        steps = comps[cid].get("build_steps", [])
        # meshcore-node prepends an lhpc-shipped patch step to the pinned upstream
        # checkout; the in-tree venv step must still exist for every python stack.
        assert steps and any(s["argv"][:3] == ["python3", "-m", "venv"] for s in steps), cid
        if cid == "meshcore-node":
            # system-site venv (OS-shipped GPIO bindings; never compiles lgpio/swig)
            venv_step = next(s for s in steps if s["argv"][:3] == ["python3", "-m", "venv"])
            assert "--system-site-packages" in venv_step["argv"]
            assert not any("rpi-lgpio" in a for s in steps for a in s["argv"])
        else:
            assert steps[1]["argv"][0] == ".venv/bin/pip", cid
    # meshcom-qemu is self-sufficient from a FRESH clone: the MANAGED tools (a PlatformIO venv + the
    # source-built headless qemu, both INSIDE the runtime root) are provisioned first, then the workspace
    # setup scripts run before build.sh (live finding: linked trees carried a pre-built .work/).
    q_steps = [st["argv"][0] for st in comps["meshcom-qemu"]["build_steps"]]
    assert q_steps == ["python3", "{runtime}/build/tools/platformio/.venv/bin/pip", "scripts/build-qemu.sh",
                       "scripts/setup.sh", "scripts/apply-overlay.sh",
                       "scripts/prepare-openeth.sh", "scripts/build.sh"]
    # meshcom secret is in-root and fail-closed
    q = comps["meshcom-qemu"]["build_steps"][-1]["env"]["XR_PASSWORD"]
    assert q == "@file?:{runtime}/config/secrets/xr_pw"          # OPTIONAL secret (legacy
    # `$(cat … 2>/dev/null)` semantics: absent -> HMAC disabled, never a blocked build)
    # meshcore-node's config BASE is an lhpc-SHIPPED asset (package data), never a path inside
    # the pinned upstream openhop checkout — the checkout stays a pristine upstream tree plus
    # a reviewable patch. It must resolve through the `{asset}` mechanism, and must NOT be an
    # un-seeded generated-output path (the old meshcore-pi-base.toml bug).
    base = comps["meshcore-node"]["config_file"]["base"]
    assert base == "{asset}/bases/meshcore.toml", base
    assert not base.startswith("{runtime}/config/files/"), base   # never the generated-output dir


def test_meshcom_secret_env_resolves_in_root(tmp_path):
    from lhpc.core import commands
    stacks = manifest_mod.load_manifest()
    comp = next(c for s in stacks if s.id == "meshcom"
                for c in s.components if c.id == "meshcom-qemu")
    step = comp.build_steps[-1]                     # the build.sh step carries env
    sec = tmp_path / "config" / "secrets"
    sec.mkdir(parents=True)
    (sec / "xr_pw").write_text("hunter2\n")
    env = commands.build_env(list(step.get("env", {}).items()), str(tmp_path),
                             str(tmp_path / "src"), "")
    assert env["XR_PASSWORD"] == "hunter2"
    (sec / "xr_pw").unlink()
    env2 = commands.build_env(list(step.get("env", {}).items()), str(tmp_path),
                              str(tmp_path / "src"), "")
    assert env2["XR_PASSWORD"] == ""             # optional: absent -> disabled, build runs


def _inst(tmp_path, search=""):
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app"))
    values = {"install": {"adopt_search_root": search}} if search else {}
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    inst = Installer(Paths(runtime_root=tmp_path / "rt"), stacks,
                     Config(values=values), RealSystem())
    (tmp_path / "rt").mkdir(exist_ok=True)
    return inst, comp


def test_default_no_fallback_clone_failure_is_typed(tmp_path):
    # Default (blank) adopt_search_root: no fallback dir exists AT ALL — a clone failure
    # is a typed selector refusal, never an outside-root read.
    inst, comp = _inst(tmp_path)                                 # no remote, no fallback
    a = inst.adopt_source(comp, source="dev")
    assert a.status == "failed"
    assert "active source untouched" in a.detail or "unavailable" in a.detail
    assert not (tmp_path / "rt" / "src" / "app").exists()


def test_outside_root_search_root_refused(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "app").mkdir()
    inst, comp = _inst(tmp_path, search=str(outside))
    a = inst.adopt_source(comp, source="dev")
    assert a.status == "failed" and "escapes the runtime root" in a.detail
    assert not (tmp_path / "rt" / "src" / "app").exists()        # zero mutation


def test_in_root_search_root_works(tmp_path):
    import subprocess, os
    local = tmp_path / "rt" / "checkouts" / "app"
    local.mkdir(parents=True)
    (local / "f").write_text("x")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "c")):
        subprocess.run(("git", "-C", str(local)) + args, env=env, check=True,
                       capture_output=True)
    inst, comp = _inst(tmp_path, search=str(tmp_path / "rt" / "checkouts"))
    a = inst.adopt_source(comp, source="dev")
    assert a.status == "done", a.detail
    assert (tmp_path / "rt" / "src" / "app" / "f").exists()


def test_radiolib_builds_in_root_and_daemon_pins_it():
    # LIVE FINDING: the daemon's build.sh silently fell back to the EXTERNAL
    # ~/src/RadioLib (prebuilt) because the managed in-root clone was never built.
    # The library now has its own in-root build_steps and the daemon's step pins
    # RADIOLIB_DIR to the managed clone.
    import tomllib
    from lhpc.core import manifest as mf
    with mf.default_manifest_path().open("rb") as fh:
        raw = tomllib.load(fh)
    comps = {c["id"]: c for st in raw["stack"] for c in st["component"]}
    rl = comps["radiolib"]
    assert [s["argv"][0] for s in rl["build_steps"]] == ["cmake", "cmake"]
    dm = comps["loraham-daemon"]
    env = dm["build_steps"][0]["env"]
    assert env["RADIOLIB_DIR"] == "{runtime}/src/RadioLib"       # in-root, never ~/src


def test_stack_build_includes_buildable_libraries_dep_first(tmp_path):
    # Stack build plans must include non-runnable buildable sources (libraries) and
    # order build_requires providers FIRST (fresh root: libRadioLib.a before build.sh).
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    # The stack must be INSTALLED: `build()` refuses a component whose source is absent
    # rather than handing Popen a nonexistent cwd, so ORDERING is only meaningful here.
    for c in svc.stack("daemon").components:
        if c.source:
            (tmp_path / c.source.path).mkdir(parents=True, exist_ok=True)
    plan = svc.build("daemon", apply=False)
    assert plan.ok
    order = [d.split()[1].rstrip(":") for d in plan.details if d.strip().startswith("[build]")]
    assert "radiolib" in order and "loraham-daemon" in order
    assert order.index("radiolib") < order.index("loraham-daemon")


def test_build_launcher_never_bakes_secret_plaintext():
    # AUDIT S1: build-step @file secrets were resolved at RENDER time and baked cleartext
    # into the on-disk launcher .py (which is never pruned). The launcher must carry the
    # UNRESOLVED token and resolve on-host at exec time.
    import tempfile
    from lhpc.core import commands
    secret = tempfile.NamedTemporaryFile("w", delete=False, suffix="-xrpw")
    secret.write("TOPSECRETpw\n")
    secret.close()
    steps = [{"argv": ["scripts/build.sh"],
              "env": {"XR_PASSWORD": f"@file:{secret.name}", "XR_HOST": "10.0.2.2"}}]
    script = commands.render_build_launcher(steps, "/rt", "/rt/src/x")
    assert "TOPSECRETpw" not in script                    # secret NOT baked
    assert "@file:" in script                             # token carried instead
    assert "10.0.2.2" in script                           # non-secret literal fine


def test_open_source_parent_refuses_intermediate_symlink(tmp_path):
    # AUDIT FS1: opening the resolved source root in one os.open guarded only its final
    # component; an intermediate `src` symlink escaped the root. The walk now starts at
    # the runtime root and refuses a swapped intermediate at the syscall.
    import os
    from lhpc.core.paths import Paths, PathContainmentError
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.model import Component, ComponentKind, SourceSpec
    rt = tmp_path / "rt"
    (rt / "src" / "app").mkdir(parents=True)
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system, paths=Paths(runtime_root=rt))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))
    # happy path writes inside the tree
    svc._write_source_config(comp, "conf/x.toml", "ok=1\n")
    assert (rt / "src" / "app" / "conf" / "x.toml").read_text() == "ok=1\n"
    # swap `src` for a symlink escaping the root -> refused at the walk
    outside = tmp_path / "evil"; outside.mkdir()
    import shutil
    shutil.rmtree(rt / "src")
    os.symlink(outside, rt / "src")
    try:
        svc._write_source_config(comp, "conf/y.toml", "pwned=1\n")
        assert False, "escape not refused"
    except (PathContainmentError, OSError):
        pass
    assert not (outside / "app" / "conf" / "y.toml").exists()   # nothing written outside


def test_norm_survives_hostile_daemon_value():
    # AUDIT IN1: int(float("1e400")) raised uncaught OverflowError, crashing a mutating
    # action on a garbled daemon reply.
    from lhpc.core import daemon_control as dc
    for v in ("1e400", "inf", "-inf", "9" * 400):
        assert isinstance(dc._norm(v), str)               # no crash
    assert dc._norm("433.0") == "433" and dc._norm("LORA") == "LORA"


def test_source_config_works_through_symlinked_runtime_root(tmp_path):
    # RE-AUDIT F1: the FS1 walk over-applied O_NOFOLLOW to the runtime ROOT, breaking the
    # documented symlinked-root setup (writes went via atomic_write and worked, but reads
    # via _open_source_parent raised ELOOP — asymmetric). The root is the trusted anchor
    # and may be a symlink; only components UNDER it are O_NOFOLLOW.
    import os
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.model import Component, ComponentKind, SourceSpec
    real = tmp_path / "real"; (real / "src" / "app").mkdir(parents=True)
    link = tmp_path / "link"; os.symlink(real, link)
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=link))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))
    svc._write_source_config(comp, "conf/x.toml", "ok=1\n")
    assert svc._read_source_base(comp, "conf/x.toml").strip() == "ok=1"   # read works too


def test_open_marker_excl_no_fd_leak_when_dup_fails(tmp_path, monkeypatch):
    # RE-AUDIT F2: hoisting os.dup(parent_fd) before the try leaked file_fd if os.dup
    # raised under fd exhaustion. The dup is now guarded; file_fd is always closed.
    import os
    from lhpc.core import runtime_fs
    closed = []
    real_close = os.close
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    real_dup = os.dup
    def boom(fd):
        raise OSError(24, "EMFILE")               # simulate fd exhaustion at dup
    monkeypatch.setattr(os, "dup", boom)
    try:
        runtime_fs.open_marker_excl(Paths_(tmp_path), tmp_path / "m.marker", "x")
        assert False, "should have raised"
    except OSError:
        pass
    monkeypatch.setattr(os, "dup", real_dup)
    assert closed, "file_fd was not closed on the dup-failure path"


def Paths_(rt):
    from lhpc.core.paths import Paths
    return Paths(runtime_root=rt)

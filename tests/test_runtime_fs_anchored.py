"""P1 — runtime_fs descriptor-anchored traversal: a runtime parent swapped to a symlink
(or a non-directory component) is refused at the syscall for every runtime-owned op, and
no file/dir is created/modified/read/deleted outside the runtime root."""

import os
import fcntl
import pytest

from lhpc.core import runtime_fs
from lhpc.core.paths import Paths, PathContainmentError


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


# --- P0.2 containment errors become typed outcomes, not crashes --------------

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


# --- P1.3 collision-safe atomic temp names -----------------------------------

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

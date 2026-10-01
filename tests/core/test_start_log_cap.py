"""M19: the start-log cap. A start log is opened O_APPEND and held by its component for the whole
run; `runtime_fs.cap_start_log` tries to cut it once it is over its trigger (checked at a start and at each console
pass, so not a hard maximum) by keeping the tail in `<name>.prev.log` and truncating in place. Fake payloads only."""
import os
from pathlib import Path

import pytest

from lhpc.core import runtime_fs
from lhpc.core.paths import PathContainmentError, Paths

LINE = b"2026-09-28 10:00:00 INFO filler line for the start-log cap tests\n"


def _logs(tmp_path) -> Path:
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    return d


def _big(tmp_path, name="start-x.log", n=200, mode=0o640) -> Path:
    p = _logs(tmp_path) / name
    p.write_bytes(b"".join(b"%05d " % i + LINE for i in range(n)))
    os.chmod(p, mode)
    return p


def test_absent_and_below_are_untouched(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    assert runtime_fs.cap_start_log(paths, _logs(tmp_path) / "start-none.log") == "absent"
    p = _big(tmp_path, n=3)
    before, ino = p.read_bytes(), p.stat().st_ino
    assert runtime_fs.cap_start_log(paths, p, max_bytes=len(before)) == "below"
    assert p.read_bytes() == before and p.stat().st_ino == ino
    assert not (p.parent / "start-x.prev.log").exists()


def test_cap_keeps_the_tail_from_a_line_start_and_truncates(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    p = _big(tmp_path, n=200, mode=0o640)
    whole, ino = p.read_bytes(), p.stat().st_ino
    keep = 1000
    assert runtime_fs.cap_start_log(paths, p, max_bytes=5000, keep_bytes=keep) == "capped"
    prev = (p.parent / runtime_fs.prev_log_name(p.name)).read_bytes()
    assert whole.endswith(prev) and 0 < len(prev) <= keep
    assert prev.startswith(b"0") and whole[len(whole) - len(prev) - 1:][:1] == b"\n"   # a line start
    assert p.read_bytes() == b"" and p.stat().st_ino == ino                            # in place
    assert (p.parent / "start-x.prev.log").stat().st_mode & 0o777 == 0o640              # mode kept


def test_a_live_o_append_writer_goes_on_at_the_new_end_without_a_hole(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    p = _big(tmp_path, n=200)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND)             # the component's inherited descriptor
    try:
        assert runtime_fs.cap_start_log(paths, p, max_bytes=5000, keep_bytes=1000) == "capped"
        os.write(fd, b"after the cap\n")
    finally:
        os.close(fd)
    assert p.read_bytes() == b"after the cap\n"            # size == bytes written after: no hole


def test_no_truncate_when_the_prev_write_fails(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    p = _big(tmp_path, n=200)
    before = p.read_bytes()

    def fail(*a, **k):
        raise OSError("no space left on device")
    monkeypatch.setattr(runtime_fs, "atomic_write_bytes", fail)
    with pytest.raises(OSError):
        runtime_fs.cap_start_log(paths, p, max_bytes=5000, keep_bytes=1000)
    assert p.read_bytes() == before


def test_a_symlinked_or_non_regular_leaf_is_refused_unchanged(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    target = tmp_path / "elsewhere.log"
    target.write_bytes(LINE * 200)
    (_logs(tmp_path) / "start-l.log").symlink_to(target)
    with pytest.raises((OSError, PathContainmentError)):
        runtime_fs.cap_start_log(paths, tmp_path / "logs" / "start-l.log", max_bytes=10)
    assert target.read_bytes() == LINE * 200
    os.mkdir(tmp_path / "logs" / "start-d.log")
    with pytest.raises((OSError, PathContainmentError)):
        runtime_fs.cap_start_log(paths, tmp_path / "logs" / "start-d.log", max_bytes=10)


def test_a_held_lock_means_busy_and_nothing_done(tmp_path):
    import fcntl
    paths = Paths(runtime_root=tmp_path)
    p = _big(tmp_path, n=200)
    before = p.read_bytes()
    with runtime_fs.open_lock(paths, paths.under(*runtime_fs.START_LOG_CAP_LOCK)) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        # a second open file description of the same lock file: flock conflicts across them
        assert runtime_fs.cap_start_log(paths, p, max_bytes=10) == "busy"
    assert p.read_bytes() == before


def test_the_read_is_bounded_by_keep_bytes(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    p = _big(tmp_path, n=2000)
    sizes = []
    real = os.pread

    def spy(fd, n, off):
        sizes.append(n)
        return real(fd, n, off)
    monkeypatch.setattr(runtime_fs.os, "pread", spy)
    runtime_fs.cap_start_log(paths, p, max_bytes=5000, keep_bytes=777)
    assert sizes == [777]


def test_cap_start_logs_takes_start_logs_only_and_never_raises(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _big(tmp_path, "start-a.log", n=2)
    _big(tmp_path, "start-a.prev.log", n=2)
    _big(tmp_path, "rf-kiss.log", n=2)
    _big(tmp_path, "build-x-1.log", n=2)
    got = svc.cap_start_logs()
    assert got == {"start-a.log": "below"}
    monkeypatch.setattr(runtime_fs, "cap_start_log",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert svc.cap_start_logs() == {"start-a.log": "error: OSError"}


def test_lhpc_logs_never_resolves_to_the_prev_half(tmp_path):
    # Found in review: right after a cap the banded .prev.log is NEWER than the emptied live log.
    from lhpc.core.config import Config
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind
    from lhpc.core.probes.backends import FakeSystem
    live = _big(tmp_path, "start-loraham-daemon-433.log", n=2)
    prev = _big(tmp_path, "start-loraham-daemon-433.prev.log", n=2)
    os.utime(live, (1_000_000_000, 1_000_000_000))
    os.utime(prev, (1_000_000_100, 1_000_000_100))                  # the .prev.log is the NEWER one
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system)
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE, run_argv=("true",))
    assert life.start_log(comp) == live


def test_a_start_caps_the_log(tmp_path, monkeypatch):
    from lhpc.core.config import Config
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    from lhpc.core.probes.backends import FakeSystem
    last = b"the last line before the start"
    p = _big(tmp_path, "start-meshcore-node.log", n=300)
    with open(p, "ab") as f:
        f.write(last + b"\n")
    real = runtime_fs.cap_start_log
    monkeypatch.setattr(runtime_fs, "cap_start_log",
                        lambda paths, path, **k: real(paths, path, max_bytes=5000, keep_bytes=1000))
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system,
                     spawn=lambda argv, log, cwd=None, env=None: None)
    comp = Component(id="meshcore-node", name="n", kind=ComponentKind.SERVICE, run_argv=("true",))
    life.start(Stack(id="meshcore", name="m", main="meshcore-node"), comp)
    prev = (p.parent / "start-meshcore-node.prev.log").read_bytes()
    assert prev.endswith(last + b"\n") and len(prev) <= 1000       # the kept tail ends with the last line
    assert last not in p.read_bytes()                                # the live log restarted


def test_a_failing_cap_never_blocks_the_start(tmp_path, monkeypatch):
    from lhpc.core.config import Config
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    from lhpc.core.probes.backends import FakeSystem
    _big(tmp_path, "start-meshcore-node.log", n=2)
    monkeypatch.setattr(runtime_fs, "cap_start_log",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    spawned = []
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system,
                     spawn=lambda argv, log, cwd=None, env=None: spawned.append(1) or None)
    comp = Component(id="meshcore-node", name="n", kind=ComponentKind.SERVICE, run_argv=("true",))
    life.start(Stack(id="meshcore", name="m", main="meshcore-node"), comp)
    assert spawned == [1]
    assert b"start-log cap skipped" in (tmp_path / "logs" / "start-meshcore-node.log").read_bytes()


def test_band_less_lhpc_logs_never_resolves_to_the_prev_half(tmp_path):
    # The same class as start_log(): `lhpc logs <comp>` without a band takes the newest job log,
    # and `start-<id>-<band>.prev.log` matches its "start-<id>-" rule.
    from lhpc.core.config import Config
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind
    from lhpc.core.probes.backends import FakeSystem
    live = _big(tmp_path, "start-loraham-daemon-433.log", n=2)
    prev = _big(tmp_path, "start-loraham-daemon-433.prev.log", n=2)
    os.utime(live, (1_000_000_000, 1_000_000_000))
    os.utime(prev, (1_000_000_100, 1_000_000_100))                  # the .prev.log is the NEWER one
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system)
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE, run_argv=("true",))
    path, _tail = life.logs(comp)
    assert path == str(live)

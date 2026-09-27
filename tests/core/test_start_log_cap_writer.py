"""Adversarial tests for `runtime_fs.cap_start_log` (M19, frozen interface evidence/m19-interface.md).

Written against the interface, not the implementation: a real second process appending with O_APPEND
while the cap runs, the kept tail's line start, refusals that must leave the log untouched, and the
memory bound. Small max/keep values keep every case fast.
"""
from __future__ import annotations

import errno
import fcntl
import os
import stat
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from lhpc.core import runtime_fs
from lhpc.core.paths import PathContainmentError, Paths

REFUSED = (OSError, PathContainmentError)
MAX = 4096
KEEP = 1024


@pytest.fixture
def paths(tmp_path) -> Paths:
    (tmp_path / "logs").mkdir()
    return Paths(runtime_root=tmp_path)


def _log(paths: Paths, name: str = "start-demo.log") -> Path:
    return paths.runtime_root / "logs" / name


def _prev(log: Path) -> Path:
    return log.with_name(runtime_fs.prev_log_name(log.name))


def _lines(n: int, width: int = 16, start: int = 0) -> bytes:
    return b"".join(f"line-{i:0{width - 6}d}\n".encode() for i in range(start, start + n))


def _expected_tail(data: bytes, keep: int) -> bytes:
    """The interface's rule: the last `keep` bytes, minus everything up to and including the first
    newline; empty when the window holds no newline."""
    window = data[-keep:]
    cut = window.find(b"\n")
    return b"" if cut < 0 else window[cut + 1:]


def _no_temp_left(directory: Path, name: str) -> bool:
    return not [p for p in directory.iterdir() if p.name.startswith(f".{name}.tmp-")]


# --- the plain contract ----------------------------------------------------------------------------

def test_prev_log_name():
    assert runtime_fs.prev_log_name("start-x.log") == "start-x.prev.log"
    assert runtime_fs.prev_log_name("start-loraham-daemon-433.log") == "start-loraham-daemon-433.prev.log"


def test_absent(paths):
    assert runtime_fs.cap_start_log(paths, _log(paths), max_bytes=MAX, keep_bytes=KEEP) == "absent"
    assert not _prev(_log(paths)).exists()


def test_below_and_at_the_cap_untouched(paths):
    log = _log(paths)
    data = _lines(MAX // 16)                       # exactly MAX bytes: "size <= max_bytes" is below
    assert len(data) == MAX
    log.write_bytes(data)
    before = os.stat(log)
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "below"
    after = os.stat(log)
    assert log.read_bytes() == data
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert not _prev(log).exists()


def test_one_byte_over_is_capped(paths):
    log = _log(paths)
    data = _lines(MAX // 16) + b"x"               # MAX + 1, the last line unterminated
    log.write_bytes(data)
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    assert log.stat().st_size == 0
    assert _prev(log).read_bytes() == _expected_tail(data, KEEP)


def test_tail_starts_at_a_line_start_and_is_a_suffix(paths):
    log = _log(paths)
    data = b"".join(f"row {i} {'y' * (i % 37)}\n".encode() for i in range(400))   # ragged lines
    assert len(data) > MAX
    log.write_bytes(data)
    inode = log.stat().st_ino
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    tail = _prev(log).read_bytes()
    assert tail == _expected_tail(data, KEEP)
    assert data.endswith(tail) and len(tail) <= KEEP
    assert tail == b"" or data[len(data) - len(tail) - 1:len(data) - len(tail)] == b"\n"   # a line start
    assert log.stat().st_ino == inode and log.stat().st_size == 0      # truncated IN PLACE, same inode


def test_window_starting_exactly_at_a_line_start_still_drops_its_first_line(paths):
    """The frozen rule drops everything up to and including the first newline of the window, even when
    the window happens to begin on a line boundary (no look-behind)."""
    log = _log(paths)
    data = _lines(512)                            # 16-byte lines; KEEP=1024 = 64 whole lines
    log.write_bytes(data)
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    assert _prev(log).read_bytes() == data[-KEEP + 16:]


def test_window_without_newline_keeps_an_empty_tail(paths):
    log = _log(paths)
    data = b"\n" + b"z" * (MAX + 50)              # the only newline lies outside the last KEEP bytes
    log.write_bytes(data)
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    assert log.stat().st_size == 0
    prev = _prev(log)
    assert (not prev.exists()) or prev.read_bytes() == b""


def test_mode_bits_carried_and_older_prev_replaced(paths):
    log = _log(paths)
    data = _lines(600)
    log.write_bytes(data)
    os.chmod(log, 0o640)
    _prev(log).write_bytes(b"old prev\n")
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    assert _prev(log).read_bytes() == _expected_tail(data, KEEP)
    assert stat.S_IMODE(_prev(log).stat().st_mode) == 0o640
    assert _no_temp_left(log.parent, _prev(log).name)


def test_second_call_right_after_is_below(paths):
    log = _log(paths)
    log.write_bytes(_lines(600))
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    prev = _prev(log).read_bytes()
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "below"
    assert _prev(log).read_bytes() == prev


# --- a real second process appending with O_APPEND -----------------------------------------------

_WRITER = r"""
import os, sys, time
n, pause = int(sys.argv[1]), float(sys.argv[2])
for i in range(n):
    os.write(1, b"w-%08d\n" % i)      # ONE write(2) per line, on the inherited O_APPEND stdout
    if pause:
        time.sleep(pause)
"""
_LINE = 11                                  # len(b"w-00000000\n")


def _seq(data: bytes) -> list[int]:
    assert data.endswith(b"\n"), "a writer line was split"
    out = []
    for raw in data.split(b"\n")[:-1]:
        assert raw.startswith(b"w-") and len(raw) == _LINE - 1, f"not a whole writer line: {raw[:40]!r}"
        out.append(int(raw[2:]))
    return out


def _spawn_writer(paths: Paths, log: Path, n: int, pause: float) -> subprocess.Popen:
    """Attach the writer exactly as the start path does: stdout = runtime_fs.open_log_append(...)."""
    fh = runtime_fs.open_log_append(paths, log)
    try:
        return subprocess.Popen([sys.executable, "-c", _WRITER, str(n), str(pause)], stdout=fh,
                                stderr=subprocess.DEVNULL)
    finally:
        fh.close()                          # the child keeps its own copy of the descriptor


def test_start_log_descriptor_is_append_mode(paths):
    """The no-hole guarantee rests on the child's descriptor being O_APPEND: an in-place truncate of a
    non-append writer leaves a sparse hole of NULs up to its old offset."""
    fh = runtime_fs.open_log_append(paths, _log(paths))
    try:
        assert fcntl.fcntl(fh.fileno(), fcntl.F_GETFL) & os.O_APPEND
    finally:
        fh.close()


def test_concurrent_append_writer_no_hole_and_only_the_window_lost(paths):
    log = _log(paths)
    total = 6000
    proc = _spawn_writer(paths, log, total, 0.0002)
    caps = 0
    deadline = time.monotonic() + 60
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            if log.stat().st_size > MAX:
                r = runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP)
                assert r in ("capped", "below")
                if r == "capped":
                    caps += 1
                    tail = _prev(log).read_bytes()
                    kept = _seq(tail) if tail else []
                    assert kept == list(range(kept[0], kept[0] + len(kept))) if kept else True
            time.sleep(0.001)
    finally:
        proc.wait(timeout=60)
    assert proc.returncode == 0
    assert caps >= 2, "the writer outran the test; the cap never ran while it was writing"

    data = log.read_bytes()
    assert b"\0" not in data, "sparse hole: a writer continued at its old offset after the truncate"
    assert os.stat(log).st_size == len(data)
    live = _seq(data)
    assert live == list(range(live[0], total)), "the live log must hold every line written after the last cap"

    tail = _prev(log).read_bytes()
    kept = _seq(tail) if tail else []
    if kept:
        assert kept == list(range(kept[0], kept[-1] + 1))
        assert kept[-1] < live[0], "the kept tail and the live log overlap or are out of order"
    # The lines between the last kept line and the first live line were written between the cap's read
    # and its truncate: lost by design. Nothing bounds how many (the capper can be descheduled).
    lost = live[0] - (kept[-1] + 1 if kept else live[0])
    assert lost >= 0


def test_writer_keeps_appending_after_the_cap(paths):
    """Afterwards the log grows from offset 0: its size equals exactly the bytes written after the cap."""
    log = _log(paths)
    log.write_bytes(_lines(600))                               # over the cap before the writer starts
    fh = runtime_fs.open_log_append(paths, log)
    try:
        assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
        for i in range(10):
            os.write(fh.fileno(), b"after-%02d\n" % i)        # the pre-cap descriptor, still O_APPEND
    finally:
        fh.close()
    data = log.read_bytes()
    assert data == b"".join(b"after-%02d\n" % i for i in range(10))
    assert log.stat().st_size == len(data)


# --- refusals: raise, and the log is UNCHANGED -----------------------------------------------------

def _snapshot(p: Path):
    st = os.stat(p, follow_symlinks=False)
    body = p.read_bytes() if stat.S_ISREG(st.st_mode) else None
    return st.st_ino, st.st_size, st.st_mtime_ns, stat.S_IFMT(st.st_mode), body


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write permission")
def test_unwritable_directory_raises_and_leaves_the_log(paths):
    log = _log(paths)
    log.write_bytes(_lines(600))
    old_prev = _prev(log)
    old_prev.write_bytes(b"older prev\n")
    before = _snapshot(log)
    os.chmod(log.parent, 0o500)
    try:
        with pytest.raises(REFUSED):
            runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP)
    finally:
        os.chmod(log.parent, 0o700)
    assert _snapshot(log) == before
    assert old_prev.read_bytes() == b"older prev\n"
    assert _no_temp_left(log.parent, old_prev.name)


def test_disk_full_on_prev_write_raises_and_leaves_the_log(paths, monkeypatch):
    """ENOSPC at the durable write of the .prev.log (simulated at fsync, which every durable write
    needs). The truncate must not happen, the older .prev.log must survive, and no temp is left."""
    log = _log(paths)
    data = _lines(600)
    log.write_bytes(data)
    old_prev = _prev(log)
    old_prev.write_bytes(b"older prev\n")
    before = _snapshot(log)
    real_fsync = os.fsync

    def full(fd):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(OSError):
        runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP)
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert _snapshot(log) == before
    assert old_prev.read_bytes() == b"older prev\n"
    assert _no_temp_left(log.parent, old_prev.name)


def test_symlinked_leaf_untouched(paths, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "victim.log"
    outside.write_bytes(_lines(600))
    victim_before = _snapshot(outside)
    log = _log(paths)
    log.symlink_to(outside)
    with pytest.raises(REFUSED):
        runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP)
    assert log.is_symlink() and os.readlink(log) == str(outside)
    assert _snapshot(outside) == victim_before
    assert not _prev(log).exists()


def test_symlinked_parent_untouched(paths, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    victim = elsewhere / "start-demo.log"
    victim.write_bytes(_lines(600))
    victim_before = _snapshot(victim)
    logs = paths.runtime_root / "logs"
    logs.rmdir()
    logs.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(REFUSED):
        runtime_fs.cap_start_log(paths, logs / "start-demo.log", max_bytes=MAX, keep_bytes=KEEP)
    assert _snapshot(victim) == victim_before
    assert not (elsewhere / "start-demo.prev.log").exists()


def test_fifo_leaf_refused_without_blocking(paths):
    log = _log(paths)
    os.mkfifo(log)
    box: dict = {}

    def run():
        try:
            box["r"] = runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP)
        except BaseException as exc:             # recorded for the assertion below
            box["e"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "cap_start_log blocked on a FIFO leaf (open without O_NONBLOCK?)"
    assert isinstance(box.get("e"), REFUSED), f"a FIFO leaf must be refused, got {box!r}"
    assert stat.S_ISFIFO(os.stat(log).st_mode)
    assert not _prev(log).exists()


def test_directory_leaf_refused(paths):
    log = _log(paths)
    log.mkdir()
    with pytest.raises(REFUSED):
        runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP)
    assert log.is_dir()


def test_busy_lock_does_nothing(paths):
    log = _log(paths)
    data = _lines(600)
    log.write_bytes(data)
    lock = paths.runtime_root.joinpath(*runtime_fs.START_LOG_CAP_LOCK)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f, fcntl.LOCK_EX);"
         " print('held', flush=True); time.sleep(30)", str(lock)],
        stdout=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == b"held"
        assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "busy"
        assert log.read_bytes() == data
        assert not _prev(log).exists()
    finally:
        holder.kill()
        holder.wait()


# --- the memory bound ------------------------------------------------------------------------------

def test_memory_bound_never_reads_more_than_keep(paths):
    """A 64 MiB log (sparse, so the test stays cheap) with keep=4 KiB: the peak of Python allocations
    during the cap stays near keep_bytes, far below the file size."""
    log = _log(paths)
    size = 64 * 1024 * 1024
    keep = 4096
    with open(log, "wb") as fh:
        fh.truncate(size - 200)
        fh.seek(size - 200)
        fh.write(_lines(12)[-200:])
    tracemalloc.start()
    try:
        assert runtime_fs.cap_start_log(paths, log, max_bytes=1024 * 1024, keep_bytes=keep) == "capped"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 * keep + 256 * 1024, f"peak {peak} B: the cap read far more than keep_bytes"
    assert log.stat().st_size == 0
    assert len(_prev(log).read_bytes()) <= keep


def test_memory_bound_read_calls(paths, monkeypatch):
    """No single read on the log's descriptor asks for more than keep_bytes."""
    log = _log(paths)
    log.write_bytes(_lines(8000))
    asked: list[int] = []
    real_read, real_pread = os.read, getattr(os, "pread", None)

    def rd(fd, n):
        asked.append(n)
        return real_read(fd, n)

    monkeypatch.setattr(os, "read", rd)
    if real_pread is not None:
        def prd(fd, n, off):
            asked.append(n)
            return real_pread(fd, n, off)
        monkeypatch.setattr(os, "pread", prd)
    assert runtime_fs.cap_start_log(paths, log, max_bytes=MAX, keep_bytes=KEEP) == "capped"
    assert asked, "the cap read nothing through os.read/os.pread (does it read through a file object?)"
    assert max(asked) <= KEEP
    assert sum(asked) <= 2 * KEEP

"""Runtime filesystem reads/writes must be safe against FIFOs, devices, symlinks, directories, and
oversized files — never blocking, never following a symlink, never returning truncated data as valid.

These cover the item-3 hardening: O_NONBLOCK + fstat(S_ISREG) gating on the ordinary readers, log
append/truncate/lock openers, and the tail helpers; a bounded read that REJECTS oversize (reads one
byte past the cap); and truncate validating the held descriptor BEFORE ftruncate (no O_TRUNC-on-open).
"""

import os

import pytest

from lhpc.core import jobs, runtime_fs
from lhpc.core.paths import Paths


def _fifo(tmp_path, name="f"):
    p = tmp_path / name
    os.mkfifo(p)
    return p


# --- FIFO / non-regular leaves are refused (and never block) --------------------------------------

def test_read_bytes_refuses_fifo_without_blocking(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _fifo(tmp_path, "pipe")
    with pytest.raises(OSError):                     # O_NONBLOCK open + S_ISREG gate -> no hang
        runtime_fs.read_bytes(paths, tmp_path / "pipe")


def test_read_text_regular_refuses_fifo(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _fifo(tmp_path, "pipe")
    with pytest.raises(OSError):
        runtime_fs.read_text_regular(paths, tmp_path / "pipe")


def test_read_bytes_refuses_directory(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "adir").mkdir()
    with pytest.raises(OSError):
        runtime_fs.read_bytes(paths, tmp_path / "adir")


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


# --- oversize is REJECTED, not silently truncated -------------------------------------------------

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


# --- truncate validates the fd BEFORE truncating (no O_TRUNC-on-open) ------------------------------

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


# --- symlink leaf still refused (O_NOFOLLOW) ------------------------------------------------------

def test_read_bytes_refuses_symlink_leaf(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "target").write_text("secret")
    os.symlink(tmp_path / "target", tmp_path / "link")
    with pytest.raises(OSError):
        runtime_fs.read_bytes(paths, tmp_path / "link")

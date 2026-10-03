"""The fail-closed existence probe: only ENOENT/ENOTDIR prove a path absent.

`os.path.lexists`/`exists`/`isfile` return False on ANY OSError, so an unreadable leaf read as
"gone" and a retire/clean/recover path proceeded. `runtime_fs.probe_exists` is the one probe a
safety decision uses; an error it cannot read past is "unknown", never "absent"."""

import errno
import os

import pytest

from lhpc.core import runtime_fs


def _failing(monkeypatch, path, err):
    """Make `os.lstat` AND `os.stat` raise `err` for exactly `path` (by string), so a site that
    used `exists`/`isfile` (stat) is caught as well as one that used `lexists` (lstat)."""
    target = os.fspath(path)
    for name in ("lstat", "stat"):
        real = getattr(os, name)

        def fake(p, *a, _real=real, **kw):
            if "dir_fd" not in kw and isinstance(p, (str, os.PathLike)) and os.fspath(p) == target:
                raise OSError(err, os.strerror(err), target)
            return _real(p, *a, **kw)
        monkeypatch.setattr(os, name, fake)


@pytest.mark.safety("existence-fail-closed")
def test_present_absent_and_a_missing_parent(tmp_path):
    leaf = tmp_path / "leaf"
    leaf.write_text("x")
    assert runtime_fs.probe_exists(leaf) == ("present", "")
    assert runtime_fs.probe_exists(tmp_path / "gone") == ("absent", "")
    assert runtime_fs.probe_exists(tmp_path / "gone" / "deeper") == ("absent", "")   # ENOENT parent
    assert runtime_fs.probe_exists(leaf / "below-a-file") == ("absent", "")           # ENOTDIR parent


@pytest.mark.safety("existence-fail-closed")
def test_a_dangling_symlink_is_present(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    assert runtime_fs.probe_exists(link) == ("present", "")


@pytest.mark.safety("existence-fail-closed")
@pytest.mark.parametrize("err", [errno.EIO, errno.EACCES, errno.ELOOP], ids=["EIO", "EACCES", "ELOOP"])
def test_any_other_error_is_unknown_with_the_error(tmp_path, monkeypatch, err):
    leaf = tmp_path / "leaf"
    leaf.write_text("x")
    _failing(monkeypatch, leaf, err)
    state, why = runtime_fs.probe_exists(leaf)
    assert state == "unknown"
    assert os.strerror(err) in why


@pytest.mark.safety("existence-fail-closed")
def test_an_unprobeable_path_is_unknown(tmp_path):
    state, why = runtime_fs.probe_exists(str(tmp_path) + "/a\0b")
    assert state == "unknown" and why

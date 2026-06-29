"""§1 — runtime pre-steps are DESCRIPTOR-ANCHORED (no pathname check-then-mutate).

A symlinked/swapped runtime PARENT or a symlink LEAF fails closed with a typed error; no
outside target is ever mutated; and the controller and generated-wrapper paths (both the
shared `apply_steps` engine) behave identically."""

import os

import pytest

from lhpc.core import wrapper_runtime
from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.commands import CommandError, run_pre_steps, normalize_pre_steps


def _paths(tmp_path):
    root = tmp_path / "rt"
    root.mkdir()
    return Paths(runtime_root=root), root


# --- symlinked runtime PARENT blocks every pre-step kind ---------------------

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


# --- symlink LEAF cannot redirect a mutation outside the root ----------------

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


# --- the safe cases still work ----------------------------------------------

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


# --- controller and generated-wrapper paths are identical -------------------

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


# --- §3: a `mkdir` pre-step must GUARANTEE a directory leaf --------------------

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

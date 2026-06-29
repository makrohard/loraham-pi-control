"""§1.3 — source_fs.rmtree_at: descriptor-anchored recursive removal that never follows a
symlink out of a managed source tree and never escapes a swapped source parent."""

import os

import pytest

from lhpc.core import source_fs
from lhpc.core.paths import Paths, PathContainmentError


def _paths(tmp_path):
    root = tmp_path / "rt"
    root.mkdir()
    return Paths(runtime_root=root), root


def test_rmtree_removes_a_normal_tree(tmp_path):
    paths, root = _paths(tmp_path)
    t = root / "src" / "app"
    (t / "sub").mkdir(parents=True)
    (t / "f").write_text("x"); (t / "sub" / "g").write_text("y")
    source_fs.rmtree_at(paths, t)
    assert not t.exists()


def test_rmtree_missing_leaf_is_noop(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    source_fs.rmtree_at(paths, root / "src" / "gone")           # no error


def test_rmtree_unlinks_symlink_leaf_without_following(tmp_path):
    # A LINKED external source: uninstall/discard removes only the runtime symlink leaf.
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "external"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    link = root / "src" / "app"; os.symlink(outside, link)
    source_fs.rmtree_at(paths, link)
    assert not link.is_symlink() and not link.exists()          # symlink leaf gone
    assert (outside / "keep").read_text() == "KEEP"             # external target UNTOUCHED


def test_rmtree_does_not_follow_symlink_inside_tree(tmp_path):
    paths, root = _paths(tmp_path)
    t = root / "src" / "app"; t.mkdir(parents=True)
    outside = tmp_path / "victim"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    os.symlink(outside, t / "danger")                           # symlink INSIDE the tree
    source_fs.rmtree_at(paths, t)
    assert not t.exists()                                        # tree removed
    assert (outside / "keep").read_text() == "KEEP"             # symlink target UNTOUCHED


def test_rmtree_swapped_source_parent_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    os.symlink(outside, root / "src")                           # source PARENT is a symlink
    with pytest.raises(PathContainmentError):
        source_fs.rmtree_at(paths, root / "src" / "app")
    assert (outside / "keep").read_text() == "KEEP"             # nothing outside touched


def test_rmtree_refuses_special_leaf(tmp_path):
    paths, root = _paths(tmp_path)
    t = root / "src" / "app"; t.mkdir(parents=True)
    os.mkfifo(t / "pipe")                                       # a FIFO -> fail closed
    with pytest.raises(PathContainmentError):
        source_fs.rmtree_at(paths, t)
    assert (t / "pipe").exists()                                # evidence retained


# --- §1.2/§1.4: rename_child, leaf_kind, pinned_parent (descriptor-anchored) ---

def test_leaf_kind_classifies_no_follow(tmp_path):
    paths, root = _paths(tmp_path)
    d = root / "src"; d.mkdir(parents=True)
    (d / "f").write_text("x"); (d / "sub").mkdir(); os.symlink(d / "f", d / "ln")
    assert source_fs.leaf_kind(paths, d / "f") == "file"
    assert source_fs.leaf_kind(paths, d / "sub") == "dir"
    assert source_fs.leaf_kind(paths, d / "ln") == "symlink"    # not followed
    assert source_fs.leaf_kind(paths, d / "gone") == "absent"


def test_rename_child_renames_siblings(tmp_path):
    paths, root = _paths(tmp_path)
    d = root / "src"; d.mkdir(parents=True); (d / "app").mkdir(); (d / "app" / "m").write_text("v")
    source_fs.rename_child(paths, d, "app", ".app.prev")
    assert not (d / "app").exists() and (d / ".app.prev" / "m").read_text() == "v"


def test_rename_child_swapped_parent_blocks(tmp_path):
    import shutil
    paths, root = _paths(tmp_path)
    (root / "src" / "app").mkdir(parents=True)
    outside = tmp_path / "out"; outside.mkdir()
    shutil.rmtree(root / "src"); os.symlink(outside, root / "src")   # parent swapped to symlink
    with pytest.raises(PathContainmentError):
        source_fs.rename_child(paths, root / "src", "app", ".app.prev")
    assert list(outside.iterdir()) == []


def test_pinned_parent_writes_into_held_inode(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    with source_fs.pinned_parent(paths, root / "src") as pin:
        with open(f"{pin}/probe", "w") as fh:
            fh.write("x")
    assert (root / "src" / "probe").read_text() == "x"


def test_pinned_parent_swapped_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "out"; outside.mkdir()
    os.symlink(outside, root / "src")                               # parent is a symlink
    with pytest.raises(PathContainmentError):
        with source_fs.pinned_parent(paths, root / "src"):
            pass


# --- §2: controller-pinned Git path (REAL git, not a mocked runner) -----------

def _git(args, cwd=None):
    import subprocess
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                               "HOME": "/tmp", "PATH": os.environ.get("PATH", "")})


def _make_repo(path):
    path.mkdir(parents=True)
    _git(["init", "-q"], cwd=path)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
          "-m", "init"], cwd=path)
    (path / "MARK").write_text("payload")
    _git(["add", "-A"], cwd=path)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add"], cwd=path)


def test_real_git_clone_through_controller_pinned_path(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    upstream = tmp_path / "upstream"; _make_repo(upstream)
    with source_fs.pinned_parent(paths, root / "src") as pin:
        cand = f"{pin}/.app.candidate-x"
        r = _git(["clone", "-q", f"file://{upstream}", cand])
        assert r.returncode == 0, r.stderr
        # Git verification/check-out through the SAME controller-pinned path
        assert _git(["-C", cand, "rev-parse", "HEAD"]).returncode == 0
    # The candidate landed in the intended HELD source parent (real path)
    assert (root / "src" / ".app.candidate-x" / "MARK").read_text() == "payload"


def test_parent_swap_after_fd_cannot_redirect_clone_outside(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    upstream = tmp_path / "upstream"; _make_repo(upstream)
    outside = tmp_path / "outside"; outside.mkdir()
    moved = tmp_path / "moved-src"
    with source_fs.pinned_parent(paths, root / "src") as pin:
        # AFTER acquiring the held fd, move the real parent aside and point its path at
        # `outside` — the held fd still refers to the ORIGINAL inode (now at `moved`).
        os.rename(root / "src", moved)
        os.symlink(outside, root / "src")
        cand = f"{pin}/.app.candidate-x"
        assert _git(["clone", "-q", f"file://{upstream}", cand]).returncode == 0
    assert list(outside.iterdir()) == []                     # NOT redirected through the swap
    assert (moved / ".app.candidate-x" / "MARK").read_text() == "payload"   # landed in held inode


# --- §2: exclusive candidate creation (no pre-seeded destination) --------------

def test_create_candidate_makes_fresh_empty_dir(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    source_fs.create_candidate_dir(paths, root / "src", ".app.candidate-1-2")
    cand = root / "src" / ".app.candidate-1-2"
    assert cand.is_dir() and not any(cand.iterdir())        # fresh, empty


def test_create_candidate_refuses_preexisting_symlink(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "evil"; outside.mkdir(); (outside / "x").write_text("V")
    os.symlink(outside, root / "src" / ".app.candidate-1-2")   # pre-seeded symlink
    with pytest.raises(PathContainmentError):
        source_fs.create_candidate_dir(paths, root / "src", ".app.candidate-1-2")
    assert (outside / "x").read_text() == "V"               # never followed/written


def test_create_candidate_refuses_preexisting_file(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    (root / "src" / ".app.candidate-1-2").write_text("seed")  # pre-seeded regular file
    with pytest.raises(PathContainmentError):
        source_fs.create_candidate_dir(paths, root / "src", ".app.candidate-1-2")


def test_create_candidate_refuses_preexisting_dir(tmp_path):
    paths, root = _paths(tmp_path)
    (root / "src").mkdir(parents=True)
    (root / "src" / ".app.candidate-1-2").mkdir()            # pre-seeded dir (not fresh)
    with pytest.raises(PathContainmentError):
        source_fs.create_candidate_dir(paths, root / "src", ".app.candidate-1-2")


def test_create_candidate_swapped_parent_blocks(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "out"; outside.mkdir()
    os.symlink(outside, root / "src")                       # source parent is a symlink
    with pytest.raises(PathContainmentError):
        source_fs.create_candidate_dir(paths, root / "src", ".app.candidate-1-2")
    assert list(outside.iterdir()) == []


# --- §1: ManagedSourceTransaction — ONE held FD across the whole transaction ---

def test_transaction_renames_survive_parent_swap(tmp_path):
    # #1: a parent-path swap AFTER opening the transaction cannot redirect later renames —
    # they keep hitting the ORIGINAL held inode, never the swapped-in path.
    paths, root = _paths(tmp_path)
    src = root / "src"; src.mkdir(parents=True)
    (src / "app").mkdir(); (src / "app" / "m").write_text("v")
    outside = tmp_path / "outside"; outside.mkdir()
    moved = tmp_path / "moved"
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        os.rename(src, moved); os.symlink(outside, src)      # swap parent path -> outside
        assert txn.leaf_kind("app") == "dir"                 # held fd still sees the original
        txn.rename("app", ".app.prev")                       # rename #1 (archive)
        txn.create_candidate(".app.candidate")               # exclusive create in held inode
        txn.rename(".app.candidate", "app")                  # rename #2 (activate)
    assert (moved / "app").is_dir()                          # activated within held inode
    assert (moved / ".app.prev" / "m").read_text() == "v"   # prior archived in held inode
    assert list(outside.iterdir()) == []                     # swapped path NEVER touched


def test_transaction_swapped_parent_blocks_at_enter(tmp_path):
    paths, root = _paths(tmp_path)
    outside = tmp_path / "out"; outside.mkdir()
    os.symlink(outside, root / "src")                        # parent is a symlink at open time
    with pytest.raises(PathContainmentError):
        with source_fs.ManagedSourceTransaction(paths, root / "src"):
            pass


def test_transaction_rmtree_and_pinned_path(tmp_path):
    paths, root = _paths(tmp_path)
    src = root / "src"; src.mkdir(parents=True)
    (src / "cand").mkdir(); (src / "cand" / "f").write_text("x")
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        assert txn.pinned_path() == f"/proc/{os.getpid()}/fd/{txn.fd}"   # controller-pinned
        txn.rmtree("cand")
        txn.fsync()
    assert not (src / "cand").exists()


# --- §1: CandidateHandle — leaf swap after creation is detected; no outside write ---

def _new_txn_candidate(tmp_path):
    paths, root = _paths(tmp_path)
    src = root / "src"; src.mkdir(parents=True)
    return paths, root, src


def test_candidate_handle_pinned_path_writes_into_held_inode(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        with open(f"{h.pinned_path()}/f", "w") as fh:
            fh.write("x")
    assert (src / ".app.candidate" / "f").read_text() == "x"    # landed in the candidate inode


def test_candidate_verify_detects_symlink_swap(tmp_path):
    import shutil
    paths, root, src = _new_txn_candidate(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        shutil.rmtree(src / ".app.candidate")                  # remove the leaf entry
        os.symlink(outside, src / ".app.candidate")            # swap for a symlink to outside
        assert txn.verify_candidate(h) is False                # swap detected (not our inode)
        # the FD-pinned path STILL refers to the original (now-unlinked) inode — a write via
        # it either fails or lands in the held inode, NEVER through the swapped-in symlink.
        try:
            with open(f"{h.pinned_path()}/g", "w") as fh:
                fh.write("y")
        except OSError:
            pass
    assert not (outside / "g").exists()                        # never redirected outside
    assert (outside / "keep").read_text() == "KEEP"


def test_candidate_verify_detects_file_swap(tmp_path):
    import shutil
    paths, root, src = _new_txn_candidate(tmp_path)
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        shutil.rmtree(src / ".app.candidate")
        (src / ".app.candidate").write_text("evil")            # swap for a regular file
        assert txn.verify_candidate(h) is False


def test_candidate_verify_detects_replacement_directory(tmp_path):
    import shutil
    paths, root, src = _new_txn_candidate(tmp_path)
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_candidate(".app.candidate")
        shutil.rmtree(src / ".app.candidate")
        (src / ".app.candidate").mkdir()                       # different-inode replacement dir
        assert txn.verify_candidate(h) is False                # inode differs -> refused


# --- §1: LinkHandle — link-leaf substitution detection ------------------------

def test_link_handle_detects_symlink_retarget(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    d1 = tmp_path / "d1"; d1.mkdir(); d2 = tmp_path / "d2"; d2.mkdir()
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_link(d1, ".app.candidate")
        assert txn.verify_link(h, ".app.candidate")             # the captured symlink
        os.unlink(src / ".app.candidate"); os.symlink(d2, src / ".app.candidate")  # retargeted
        assert txn.verify_link(h, ".app.candidate") is False    # dev/ino + readlink differ


def test_link_handle_detects_file_replacement(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    d1 = tmp_path / "d1"; d1.mkdir()
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_link(d1, ".app.candidate")
        os.unlink(src / ".app.candidate"); (src / ".app.candidate").write_text("evil")
        assert txn.verify_link(h, ".app.candidate") is False    # not a symlink anymore


def test_link_handle_detects_dangling_target(tmp_path):
    paths, root, src = _new_txn_candidate(tmp_path)
    tgt = tmp_path / "gone"                                     # does not exist -> dangling
    with source_fs.ManagedSourceTransaction(paths, src) as txn:
        h = txn.create_link(tgt, ".app.candidate")
        assert txn.verify_link(h, ".app.candidate") is False    # target not a directory

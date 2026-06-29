"""Descriptor-anchored mutation authority for MANAGED runtime source trees.

Separate from `runtime_fs` on purpose: managed sources include *linked external* checkouts
that are observe-only, so their mutation must never follow a symlink into an external target
or recurse outside the held source parent. This module reuses `runtime_fs`'s validated
parent-walk primitive but owns the source-specific operations (recursive removal today;
candidate/rename/activation are added incrementally).

The core guarantee: every mutation walks the source parent from the runtime-root fd with
`O_DIRECTORY|O_NOFOLLOW` and operates relative to the held parent fd — a swapped/symlinked
source parent fails closed with `PathContainmentError`, and a recursive removal can never
escape the held parent inode or follow a symlink out of the tree.
"""

from __future__ import annotations

import contextlib
import os
import stat as _stat
from pathlib import Path

from . import runtime_fs
from .paths import Paths, PathContainmentError

__all__ = ["rmtree_at", "rename_child", "leaf_kind", "pinned_parent",
           "create_candidate_dir", "ManagedSourceTransaction", "CandidateHandle", "LinkHandle", "PathContainmentError"]


class CandidateHandle:
    """A RETAINED no-follow FD on a freshly-created candidate directory, plus its immutable
    device/inode identity. Git/copy/provenance use its FD-pinned path (`/proc/<pid>/fd/<fd>`),
    NEVER the mutable candidate leaf name — so a post-creation swap of the leaf (to a symlink,
    file, or replacement directory) cannot redirect a write outside. Activation re-verifies
    the leaf still resolves to THIS device/inode (a real directory) before renaming it."""

    def __init__(self, name: str, fd: int, st_dev: int, st_ino: int):
        self.name = name
        self.fd = fd
        self.st_dev = st_dev
        self.st_ino = st_ino

    def pinned_path(self) -> str:
        return f"/proc/{os.getpid()}/fd/{self.fd}"

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


class LinkHandle:
    """Identity of a link-strategy staging leaf: its no-follow device/inode, the exact stored
    link target string (`readlink`), and the validated local target path. Activation proves
    the leaf is still THIS symlink (unswapped) before promoting it; there is no fd to retain
    (a symlink cannot hold a directory fd). Provenance evaluates only `local_target`."""

    def __init__(self, name: str, st_dev: int, st_ino: int, target: str, local_target: str):
        self.name = name
        self.st_dev = st_dev
        self.st_ino = st_ino
        self.target = target
        self.local_target = local_target

    def close(self) -> None:      # no retained fd — symmetry with CandidateHandle
        pass


class ManagedSourceTransaction:
    """ONE held source-parent FD for a whole activation transaction.

    Opened once via a no-follow walk from the runtime-root fd; every leaf inspection,
    sibling rename, recursive removal, and candidate creation then operates RELATIVE TO THAT
    HELD FD. A parent-path swap after the first operation therefore cannot redirect a later
    rename/rollback/cleanup into a DIFFERENT directory inode — they all keep hitting the
    original held inode. Use as a context manager; a symlinked/non-directory/escaping source
    parent fails closed with PathContainmentError at `__enter__`. Any `CandidateHandle` FDs
    opened during the transaction are closed on exit."""

    def __init__(self, paths: Paths, parent: Path):
        self._paths = paths
        self._parent = parent
        self._walk = None
        self.fd = -1
        self._handles: list[CandidateHandle] = []

    def __enter__(self) -> "ManagedSourceTransaction":
        # `_walk_parent` opens `parent` no-follow and yields its fd (the ".txn" leaf need not
        # exist — we only use the parent fd, held for the whole `with`).
        self._walk = runtime_fs._walk_parent(self._paths, self._parent / ".txn", create=False)
        self.fd, _leaf = self._walk.__enter__()
        return self

    def __exit__(self, et, ev, tb):
        for h in self._handles:              # close every retained candidate FD on any return
            h.close()
        return self._walk.__exit__(et, ev, tb)

    def leaf_kind(self, name: str) -> str:
        """No-follow classification of child `name` relative to the held fd."""
        try:
            st = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent"
        m = st.st_mode
        if _stat.S_ISLNK(m):
            return "symlink"
        if _stat.S_ISDIR(m):
            return "dir"
        if _stat.S_ISREG(m):
            return "file"
        return "special"

    def rename(self, old_name: str, new_name: str) -> None:
        """Rename sibling `old_name` -> `new_name`, both relative to the held fd."""
        os.rename(old_name, new_name, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def rmtree(self, name: str) -> None:
        """Descriptor-anchored recursive removal of child `name` relative to the held fd
        (no-follow recurse; special leaf fails closed; symlink leaf unlinked, not followed)."""
        _rmtree_fd(self.fd, name)

    def create_candidate(self, name: str) -> CandidateHandle:
        """Exclusively create empty candidate directory `name` relative to the held fd (any
        pre-existing leaf fails closed), then OPEN and RETAIN a no-follow FD on it. Returns a
        `CandidateHandle` (fd + device/inode identity) whose FD-pinned path Git/copy must use;
        the fd is closed when the transaction exits."""
        try:
            os.mkdir(name, 0o700, dir_fd=self.fd)
        except FileExistsError as exc:
            raise PathContainmentError(f"candidate {name!r} already exists") from exc
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            st = os.fstat(fd)
            if not _stat.S_ISDIR(st.st_mode) or os.listdir(fd):
                raise PathContainmentError(f"candidate {name!r} is not a fresh empty directory")
        except BaseException:
            os.close(fd)
            raise
        handle = CandidateHandle(name, fd, st.st_dev, st.st_ino)
        self._handles.append(handle)
        return handle

    def verify_candidate(self, handle: CandidateHandle, name: str = None) -> bool:
        """The candidate leaf `name` (default the created name; the DEST name after the
        activation rename) under the held parent must STILL be a real directory with the
        recorded device/inode — i.e. it was not swapped (for a symlink, regular file, or a
        DIFFERENT replacement directory)."""
        try:
            st = os.stat(name or handle.name, dir_fd=self.fd, follow_symlinks=False)
        except OSError:
            return False
        return (_stat.S_ISDIR(st.st_mode) and st.st_dev == handle.st_dev
                and st.st_ino == handle.st_ino)

    def pinned_path(self) -> str:
        """Controller-pinned `/proc/<lhpc-pid>/fd/<held-fd>` path for Git/copy staging."""
        return f"/proc/{os.getpid()}/fd/{self.fd}"

    def child_pinned_path(self, name: str) -> str:
        """Controller-pinned path to child `name` under the held parent — the ONLY pathname a
        Git/copy/provenance call may receive, since it is backed by the still-held fd and
        cannot be redirected by a parent-path swap."""
        return f"{self.pinned_path()}/{name}"

    def create_link(self, target, name: str) -> "LinkHandle":
        """Create the link-strategy runtime symlink leaf `name` -> `target` via the held fd and
        capture a `LinkHandle` recording its no-follow device/inode, the exact readlink string,
        and the validated local target — so activation can prove the leaf is still OUR symlink
        (not swapped) before promoting it."""
        os.symlink(os.fspath(target), name, dir_fd=self.fd)
        st = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        link = os.readlink(name, dir_fd=self.fd)
        handle = LinkHandle(name, st.st_dev, st.st_ino, link, os.fspath(target))
        self._handles.append(handle)
        return handle

    def verify_link(self, handle: "LinkHandle", name: str = None) -> bool:
        """The link leaf `name` (default the created name; the DEST name after the activation
        rename) is STILL our exact symlink: same no-follow device/inode, still a symlink, its
        readlink still equals the recorded target, and that target resolves to a directory."""
        leaf = name or handle.name
        try:
            st = os.stat(leaf, dir_fd=self.fd, follow_symlinks=False)
        except OSError:
            return False
        if (not _stat.S_ISLNK(st.st_mode) or st.st_dev != handle.st_dev
                or st.st_ino != handle.st_ino):
            return False
        try:
            if os.readlink(leaf, dir_fd=self.fd) != handle.target:
                return False
            tst = os.stat(leaf, dir_fd=self.fd, follow_symlinks=True)   # target resolves...
        except OSError:
            return False
        return _stat.S_ISDIR(tst.st_mode)                              # ...to a directory

    def usable(self, name: str) -> bool:
        """Active-source usability via the held fd: child `name` resolves (a linked source's
        symlink IS followed here — deliberately, to confirm the target is a directory) to a
        real directory. A dangling symlink / regular file / absent leaf is NOT usable."""
        try:
            st = os.stat(name, dir_fd=self.fd, follow_symlinks=True)
        except OSError:
            return False
        return _stat.S_ISDIR(st.st_mode)

    def fsync(self) -> None:
        """Flush the held parent directory after a durable create/rename/unlink transition."""
        try:
            os.fsync(self.fd)
        except OSError:
            pass


@contextlib.contextmanager
def pinned_parent(paths: Paths, parent: Path):
    """Hold the managed-source `parent` open `O_DIRECTORY|O_NOFOLLOW` and yield a STABLE
    CONTROLLER-pinned path `/proc/<lhpc-pid>/fd/<fd>` for it. Clone/copy/symlink into
    `<pinned>/<candidate>` then writes into the HELD inode and cannot be redirected by a
    parent-path swap after the check. The path is bound to the LHPC controller pid (NOT
    `/proc/self/...`) so it resolves to LHPC's held fd even from a CHILD process — Git's own
    `self` is Git, so `/proc/self/fd/<n>` would refer to Git's descriptors, not ours. A
    symlinked/non-directory/escaping source parent fails closed with PathContainmentError."""
    # `_walk_parent` opens `parent` no-follow and yields its fd (the ".pin" leaf need not
    # exist — we only use the parent fd). The fd stays open for the whole `with`, so the
    # controller `/proc/<pid>/fd/<fd>` magic symlink resolves to the held inode throughout.
    with runtime_fs._walk_parent(paths, parent / ".pin", create=False) as (parent_fd, _leaf):
        yield f"/proc/{os.getpid()}/fd/{parent_fd}"


def leaf_kind(paths: Paths, path: Path) -> str:
    """No-follow classification of a runtime source leaf, relative to its no-follow-walked
    parent: 'absent' | 'dir' | 'symlink' | 'file' | 'special'. Used as the descriptor-safe
    replacement for `Path.exists()`/`Path.is_symlink()` as mutation authority. A swapped/
    symlinked/non-directory source PARENT raises `PathContainmentError`."""
    try:
        with runtime_fs._walk_parent(paths, path, create=False) as (parent_fd, name):
            try:
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return "absent"
            m = st.st_mode
            if _stat.S_ISLNK(m):
                return "symlink"
            if _stat.S_ISDIR(m):
                return "dir"
            if _stat.S_ISREG(m):
                return "file"
            return "special"
    except FileNotFoundError:
        return "absent"


def create_candidate_dir(paths: Paths, parent: Path, name: str) -> None:
    """EXCLUSIVELY create candidate directory `name` under the managed-source `parent`
    through the no-follow-walked parent fd, and verify it is a fresh EMPTY directory. Raises
    PathContainmentError on an unsafe/swapped parent, if a leaf with that name ALREADY exists
    (symlink / file / special / dir — no clobber, atomic O_EXCL-style `mkdir`), or if the
    created leaf is somehow not a real empty directory. Git/copy then write INTO this
    verified candidate, never letting an attacker pre-seed the destination."""
    with runtime_fs._walk_parent(paths, parent / name, create=False) as (parent_fd, leaf):
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent_fd)      # fails closed if the leaf exists
        except FileExistsError as exc:
            raise PathContainmentError(
                f"candidate {leaf!r} already exists — refusing to reuse it") from exc
        st = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if not _stat.S_ISDIR(st.st_mode):
            raise PathContainmentError(f"candidate {leaf!r} is not a directory after create")
        dfd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            if os.listdir(dfd):
                raise PathContainmentError(f"candidate {leaf!r} not empty after create")
        finally:
            os.close(dfd)


def rename_child(paths: Paths, parent: Path, old_name: str, new_name: str) -> None:
    """Descriptor-anchored rename of a SIBLING leaf `old_name` -> `new_name`, both direct
    children of the managed-source `parent`. Walks `parent` no-follow and renames relative
    to its held fd (`src_dir_fd == dst_dir_fd`), so a swapped/symlinked source parent fails
    closed and the rename can never cross into a different directory inode."""
    with runtime_fs._walk_parent(paths, parent / old_name, create=False) as (parent_fd, oname):
        os.rename(oname, new_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)


def _rmtree_fd(parent_fd: int, name: str) -> None:
    """Remove entry `name` under `parent_fd`, recursing NO-FOLLOW. A symlink or regular
    leaf is unlinked (never followed); a directory is opened `O_NOFOLLOW`, its children
    removed relative to ITS fd, then rmdir'd; a special (fifo/socket/device) leaf fails
    closed (a managed source tree never legitimately contains one) so evidence is retained."""
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)   # lstat, no-follow
    except FileNotFoundError:
        return
    mode = st.st_mode
    if _stat.S_ISDIR(mode):
        dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            for child in os.listdir(dfd):
                _rmtree_fd(dfd, child)
        finally:
            os.close(dfd)
        os.rmdir(name, dir_fd=parent_fd)
        return
    if _stat.S_ISLNK(mode) or _stat.S_ISREG(mode):
        os.unlink(name, dir_fd=parent_fd)          # never follows a symlink leaf
        return
    # fifo / socket / block / char device -> fail closed, retain as evidence.
    raise PathContainmentError(
        f"refusing to remove a non-regular source leaf {name!r} (mode {oct(mode)})")


def rmtree_at(paths: Paths, path: Path) -> None:
    """Descriptor-anchored recursive removal of a runtime-owned (managed-source) tree.

    Walks `path`'s parent no-follow, then removes the leaf relative to the held parent fd:
      * a MISSING leaf is a no-op;
      * a symlink or regular-file leaf is unlinked (never followed) — so a LINKED external
        source is removed by dropping only its runtime symlink leaf, never its target;
      * a directory is recursed NO-FOLLOW and rmdir'd;
      * a special/unknown leaf fails closed (`PathContainmentError`).
    A swapped/symlinked/non-directory source PARENT raises `PathContainmentError` before any
    mutation — the recursion can never escape the held parent inode."""
    try:
        with runtime_fs._walk_parent(paths, path, create=False) as (parent_fd, name):
            _rmtree_fd(parent_fd, name)
    except FileNotFoundError:
        pass

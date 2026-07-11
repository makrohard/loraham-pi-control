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
           "create_candidate_dir", "ManagedSourceTransaction", "CandidateHandle", "LinkHandle",
           "SourceLeafHandle", "capture_leaf", "detach_and_remove", "quarantine_siblings",
           "race_seam", "verify_leaf_path", "require_atomic_rename", "remove_bound",
           "AtomicRenameUnavailable", "PathContainmentError"]


def race_seam(point: str, path: str = "") -> None:
    """DETERMINISTIC TEST SEAM — a no-op hook invoked at defined points between an identity
    proof and the following irreversible mutation. Tests monkeypatch it to substitute the
    destination leaf and prove the protocols refuse rather than destroy. Never carries any
    production behaviour."""


class SourceLeafHandle:
    """Captured no-follow identity of an EXISTING managed source leaf — the authority every
    destructive operation must re-prove at its irreversible step ("is this still the leaf I
    verified?"). For a DIRECTORY leaf a no-follow O_RDONLY fd is RETAINED (its
    `/proc/<pid>/fd/N` pinned path lets git/dirty checks run against the exact captured
    inode); for a SYMLINK leaf the identity is device/inode + the exact readlink string
    (a symlink cannot hold a directory fd)."""

    def __init__(self, name: str, kind: str, fd: int, st_dev: int, st_ino: int,
                 target: str = ""):
        self.name = name
        self.kind = kind                  # "dir" | "symlink"
        self.fd = fd                      # retained no-follow fd (dirs), -1 for symlinks
        self.st_dev = st_dev
        self.st_ino = st_ino
        self.target = target              # exact readlink (symlinks)

    def pinned_path(self) -> str:
        return f"/proc/{os.getpid()}/fd/{self.fd}"

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def _verify_leaf_at(parent_fd: int, name: str, handle: SourceLeafHandle) -> bool:
    """Leaf `name` under `parent_fd` is STILL the captured leaf: same no-follow kind,
    device and inode (and readlink string for a symlink)."""
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    if st.st_dev != handle.st_dev or st.st_ino != handle.st_ino:
        return False
    if handle.kind == "dir":
        return _stat.S_ISDIR(st.st_mode)
    if handle.kind == "symlink":
        if not _stat.S_ISLNK(st.st_mode):
            return False
        try:
            return os.readlink(name, dir_fd=parent_fd) == handle.target
        except OSError:
            return False
    return False


# renameat2(2) with RENAME_NOREPLACE: the Linux atomic no-clobber rename. glibc exposes the
# wrapper since 2.28; when unavailable we fall back to check-then-rename (a narrow residual
# race, flagged via `RENAMEAT2_AVAILABLE` so tests can assert the atomic path is in use).
_RENAME_NOREPLACE = 1
try:
    import ctypes as _ctypes
    _libc = _ctypes.CDLL(None, use_errno=True)
    _renameat2_fn = getattr(_libc, "renameat2", None)
except OSError:                                             # pragma: no cover
    _renameat2_fn = None
RENAMEAT2_AVAILABLE = _renameat2_fn is not None


class AtomicRenameUnavailable(OSError):
    """renameat2(RENAME_NOREPLACE) is unavailable or unsupported here. Source lifecycle
    mutation REFUSES rather than falling back to check-then-plain-rename (which would
    reintroduce the clobber race)."""


def _rename_noreplace_at(parent_fd: int, old: str, new: str) -> None:
    """Atomically rename `old` -> `new` under `parent_fd`, FAILING (FileExistsError) if any
    leaf exists at `new` — an injected leaf (even an empty directory, which plain rename(2)
    would silently replace) is never clobbered. NO FALLBACK: an unavailable/unsupported
    primitive raises `AtomicRenameUnavailable` so callers refuse typed, BEFORE mutation."""
    import errno as _errno
    if _renameat2_fn is None:
        raise AtomicRenameUnavailable(
            "renameat2(RENAME_NOREPLACE) is not available on this system — source "
            "lifecycle mutation refused (no unsafe fallback)")
    rc = _renameat2_fn(parent_fd, os.fsencode(old), parent_fd, os.fsencode(new),
                       _RENAME_NOREPLACE)
    if rc == 0:
        return
    err = _ctypes.get_errno()
    if err in (_errno.ENOSYS, _errno.EINVAL):
        raise AtomicRenameUnavailable(
            "renameat2(RENAME_NOREPLACE) is unsupported by this kernel/filesystem — "
            "source lifecycle mutation refused (no unsafe fallback)")
    raise OSError(err, os.strerror(err), old)


# Positive renameat2 capability, cached PER FILESYSTEM DEVICE (a libc symbol alone proves
# nothing — the actual managed-source filesystem may reject the flags at runtime). Only the
# POSITIVE result is cached; an unsupported probe is re-checked every time and NEVER decays
# into a plain-rename fallback.
_ATOMIC_OK_DEVS: set = set()


def require_atomic_rename(paths: Paths = None, parent: Path = None) -> str:
    """Pre-mutation gate: '' when renameat2(RENAME_NOREPLACE) PROVABLY WORKS for the managed
    source parent's filesystem, else a typed actionable reason — callers refuse BEFORE any
    candidate, journal, source, registry, or cleanup mutation.

    With `paths`+`parent` the capability is PROBED on the actual filesystem (a scratch
    `.lhpc-atomic-probe-*` rename under the held parent fd, cleaned up afterwards); a
    positive result is cached per st_dev. Without them only the libc-symbol presence is
    checked (a weaker preliminary gate)."""
    import time as _time
    if _renameat2_fn is None:
        return ("renameat2(RENAME_NOREPLACE) is not available on this system — source "
                "lifecycle mutation refused (no unsafe fallback); upgrade the kernel/libc")
    if paths is None or parent is None:
        return ""
    try:
        with runtime_fs._walk_parent(paths, parent / ".probe", create=False) as (pfd, _leaf):
            dev = os.fstat(pfd).st_dev
            if dev in _ATOMIC_OK_DEVS:
                return ""
            nonce = f".lhpc-atomic-probe-{os.getpid()}-{_time.monotonic_ns()}"
            try:
                os.mkdir(nonce, dir_fd=pfd)
            except OSError as exc:
                return f"cannot probe atomic rename on the source filesystem: {exc}"
            try:
                try:
                    _rename_noreplace_at(pfd, nonce, nonce + "-b")
                    os.rmdir(nonce + "-b", dir_fd=pfd)
                except AtomicRenameUnavailable as exc:
                    os.rmdir(nonce, dir_fd=pfd)
                    return str(exc)
            except OSError as exc:
                return f"atomic-rename probe failed on the source filesystem: {exc}"
            _ATOMIC_OK_DEVS.add(dev)
            return ""
    except (OSError, PathContainmentError) as exc:
        return f"cannot probe atomic rename (source parent unsafe): {exc}"


def _ident_of_stat(st, *, with_ctime: bool):
    """[dev, ino] or [dev, ino, ctime_ns] from a stat result. `ctime_ns` is kernel-controlled —
    `utimensat` can forge atime/mtime but never sets ctime to an arbitrary past value — so a v5
    ident survives inode RECYCLING (a recreated leaf on the same recycled inode gets a fresh
    ctime, which will not match the journaled one)."""
    return [st.st_dev, st.st_ino, st.st_ctime_ns] if with_ctime else [st.st_dev, st.st_ino]


def _ident_cmp(st, ident) -> bool:
    """dev+ino always; ctime_ns ONLY when the stored `ident` carries a third element. So a 2-element
    (live/in-process) ident compares exactly as before, and a 3-element (v5 journal) ident also
    enforces ctime. Length is validated by the caller."""
    if st.st_dev != ident[0] or st.st_ino != ident[1]:
        return False
    return len(ident) < 3 or st.st_ctime_ns == ident[2]


def leaf_ident_at(parent_fd: int, name: str, *, with_ctime: bool = False):
    """No-follow [dev, ino] (or [dev, ino, ctime_ns] when `with_ctime`) of leaf `name` under
    `parent_fd`, or None when absent/unreadable. The v5 (with_ctime) form is the strict identity
    evidence the crash-recovery journal persists; the 2-element form is the live/in-process one."""
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return None
    return _ident_of_stat(st, with_ctime=with_ctime)


def ident_matches(parent_fd: int, name: str, ident) -> bool:
    """Leaf `name` still has the recorded identity — dev+ino, plus ctime_ns when `ident` is v5
    (3-element). Length-tolerant so both live [dev,ino] and journal [dev,ino,ctime_ns] idents work."""
    if not (isinstance(ident, (list, tuple)) and len(ident) in (2, 3)):
        return False
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return _ident_cmp(st, ident)


def remove_bound(parent_fd: int, name: str, ident) -> tuple:
    """THE identity-bound destructive removal: leaf `name` under `parent_fd` is removed ONLY
    while it matches `ident` ([dev, ino] or v5 [dev, ino, ctime_ns]) — never through the mutable
    name alone.

      * a DIRECTORY leaf is OPENED no-follow and BOUND (fstat == ident); its contents are
        removed THROUGH that bound fd (a concurrent name swap cannot redirect the recursion);
        the empty directory entry is removed only after a final ident re-proof;
      * a SYMLINK/FILE leaf is ident-proven immediately before unlink (no-follow);
      * ANY mismatch/absence leaves the (substituted) leaf untouched.

    Returns (ok, reason). `ident` is REQUIRED — callers without identity evidence must not
    delete (they retain evidence instead)."""
    if not (isinstance(ident, (list, tuple)) and len(ident) in (2, 3)):
        return False, "no identity evidence — refusing to remove (leaf retained)"
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False, "leaf is absent/unreadable — nothing removed"
    if not _ident_cmp(st, ident):
        return False, "leaf was substituted — refusing to remove (substitute retained)"
    if _stat.S_ISDIR(st.st_mode):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            return False, f"leaf could not be bound: {exc}"
        try:
            bst = os.fstat(fd)
            if not _ident_cmp(bst, ident):
                return False, "leaf was substituted during binding — refusing"
            for child in os.listdir(fd):
                _rmtree_fd(fd, child)                 # contents via the BOUND fd only
            if os.listdir(fd):
                return False, "contents could not be fully removed (remainder retained)"
        except OSError as exc:
            return False, f"bound removal incomplete: {exc} (remainder retained)"
        finally:
            os.close(fd)
        # Final re-proof before rmdir is dev+ino ONLY: the inode was already ctime-bound above and
        # pinned through the bound fd; our own content removal legitimately bumped its ctime, so a v5
        # ctime check here would spuriously refuse. This guards a NAME swap to a different inode.
        if not ident_matches(parent_fd, name, list(ident[:2])):
            return False, "leaf was substituted after content removal — refusing rmdir"
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as exc:
            return False, f"empty leaf could not be removed: {exc}"
        return True, "removed"
    # symlink / regular file: ident just proven no-follow -> unlink no-follow
    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError as exc:
        return False, f"leaf could not be unlinked: {exc}"
    return True, "removed"


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

    def rename_noreplace(self, old_name: str, new_name: str) -> None:
        """Atomic NO-CLOBBER sibling rename (renameat2 RENAME_NOREPLACE): raises
        FileExistsError if ANY leaf exists at `new_name` — an injected leaf (even an empty
        directory) is never silently replaced."""
        _rename_noreplace_at(self.fd, old_name, new_name)

    def capture_leaf(self, name: str) -> "SourceLeafHandle":
        """Capture the EXISTING leaf `name` (no-follow) under the held fd: a directory leaf
        retains an O_RDONLY|O_NOFOLLOW fd; a symlink leaf records dev/ino + readlink. Any
        other kind fails closed (never a destructible target)."""
        st = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if _stat.S_ISDIR(st.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            h = SourceLeafHandle(name, "dir", fd, st.st_dev, st.st_ino)
            self._handles.append(h)
            return h
        if _stat.S_ISLNK(st.st_mode):
            return SourceLeafHandle(name, "symlink", -1, st.st_dev, st.st_ino,
                                    target=os.readlink(name, dir_fd=self.fd))
        raise PathContainmentError(f"leaf {name!r} is not a capturable source leaf")

    def verify_leaf(self, handle: "SourceLeafHandle", name: str = None) -> bool:
        """Leaf `name` (default the captured name) is STILL the captured leaf — the identity
        proof every irreversible archive/detach/removal re-runs at its own step."""
        return _verify_leaf_at(self.fd, name or handle.name, handle)

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


# ---- race-safe destructive removal (uninstall / Clean all) -----------------------------------

_QUARANTINE_RE = None


def _quarantine_name(name: str) -> str:
    import time as _time
    return f".{name}.quarantine-{os.getpid()}-{_time.monotonic_ns()}"


def is_quarantine_name(dest_name: str, leaf: str) -> bool:
    import re as _re
    return bool(_re.fullmatch(rf"\.{_re.escape(dest_name)}\.quarantine-\d+-\d+", leaf))


def quarantine_siblings(paths: Paths, path: Path) -> list:
    """Orphaned quarantine leaves for `path` (crash evidence from an interrupted removal).
    They are RETAINED, never auto-deleted; destructive operations refuse while one exists."""
    try:
        entries = runtime_fs.scandir_nofollow(paths, path.parent)
    except PathContainmentError:
        return ["(source parent unsafe)"]
    return [n for n, _ in entries if is_quarantine_name(path.name, n)]


def verify_leaf_path(paths: Paths, path: Path, handle: SourceLeafHandle) -> bool:
    """Module-level re-proof: the leaf at `path` is STILL the captured `handle` (no-follow
    parent walk + kind/dev/ino[/readlink] comparison). Used to re-prove a handle immediately
    before persisting state derived from it (e.g. a backfill ownership record)."""
    try:
        with runtime_fs._walk_parent(paths, path, create=False) as (parent_fd, name):
            return _verify_leaf_at(parent_fd, name, handle)
    except (OSError, PathContainmentError):
        return False


def capture_leaf(paths: Paths, path: Path) -> SourceLeafHandle:
    """Module-level capture of an EXISTING source leaf (no-follow parent walk): directory
    leaves retain a no-follow fd (caller must `close()`); symlink leaves capture identity
    only. File/special/absent leaves fail closed with PathContainmentError."""
    with runtime_fs._walk_parent(paths, path, create=False) as (parent_fd, name):
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _stat.S_ISDIR(st.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            return SourceLeafHandle(name, "dir", fd, st.st_dev, st.st_ino)
        if _stat.S_ISLNK(st.st_mode):
            return SourceLeafHandle(name, "symlink", -1, st.st_dev, st.st_ino,
                                    target=os.readlink(name, dir_fd=parent_fd))
        raise PathContainmentError(f"leaf {path.name!r} is not a capturable source leaf")


def detach_and_remove(paths: Paths, path: Path, handle: SourceLeafHandle,
                      final_check=None) -> tuple:
    """Race-safe destructive removal of a VERIFIED existing source leaf. The caller captured
    `handle` and ran its ownership/identity/dirty checks against it; this function performs
    the irreversible step under a quarantine protocol so an EXTERNAL substitution between
    those checks and the removal can never destroy unknown content:

      1. re-prove the leaf is STILL the captured one (no-follow, under the held parent fd);
      2. atomically DETACH it to a controller-owned quarantine name (NOREPLACE rename);
      3. re-prove the QUARANTINED leaf is the captured one — a substitution that raced the
         rename is put BACK (NOREPLACE) and reported; nothing is deleted;
      4. only then recursively remove the quarantined tree (or unlink the symlink leaf —
         a linked source's external target is never touched).

    Returns (ok, message). On any unproven step the substituted/quarantined leaf is RETAINED
    as evidence and (False, truthful-message) is returned — never a false success.

    KERNEL-LEVEL LIMITATION: a process that already holds an OPEN DIRECTORY FD inside the
    tree can still create entries after the post-detach scan and before the bound deletion
    (no filesystem primitive can exclude that). Ordinary PATHNAME-based writers are stopped:
    the detach removed the path, and the post-detach scan catches anything written before
    it."""
    unavailable = require_atomic_rename(paths, path.parent)
    if unavailable:
        return False, unavailable                  # refuse BEFORE any detach/removal
    qname = _quarantine_name(path.name)
    try:
        with ManagedSourceTransaction(paths, path.parent) as txn:
            if not txn.verify_leaf(handle, path.name):
                return False, ("destination was concurrently replaced after verification — "
                               "nothing removed (substituted content untouched)")
            if final_check is not None:
                # FINAL recheck immediately before the irreversible step (e.g. a fresh
                # dirty scan against the CAPTURED leaf) — a reason aborts with zero mutation.
                why = final_check()
                if why:
                    return False, why
            race_seam("pre-detach", str(path))
            try:
                txn.rename_noreplace(path.name, qname)
            except FileExistsError:
                return False, "quarantine name collision — nothing removed"
            if not txn.verify_leaf(handle, qname):
                # The rename raced a substitution: what we detached is NOT the verified leaf.
                # Put the substitute back where it was; if that slot was re-occupied, retain
                # the quarantined substitute as evidence.
                try:
                    txn.rename_noreplace(qname, path.name)
                    return False, ("destination was concurrently replaced during detach — "
                                   "nothing removed (substituted content restored)")
                except (OSError, PathContainmentError):
                    return False, (f"destination was concurrently replaced; the substituted "
                                   f"leaf is preserved at {qname!r} — nothing deleted")
            txn.fsync()
            race_seam("pre-quarantine-delete", str(path))
            if final_check is not None:
                # SECOND scan THROUGH THE CAPTURED HANDLE, after the detach and the
                # quarantine identity proof: a file created INSIDE the unchanged directory
                # after the pre-detach check (pathname-based writer) is caught here. If
                # dirty: restore the verified quarantine leaf to its original path
                # (no-clobber), re-prove its identity, refuse truthfully — registry/config/
                # log/profile state stays untouched by the caller on a False return.
                why = final_check()
                if why:
                    try:
                        txn.rename_noreplace(qname, path.name)
                    except (OSError, PathContainmentError):
                        return False, (f"{why}; the original path was reoccupied — the "
                                       f"source is preserved at {qname!r} (recovery "
                                       "required; nothing deleted)")
                    txn.fsync()
                    if not txn.verify_leaf(handle, path.name):
                        return False, (f"{why}; restoration could not be proven — evidence "
                                       f"retained (recovery required)")
                    return False, f"{why} (source restored at its original path)"
            # IDENT-BOUND deletion of the quarantined leaf: contents through a bound fd,
            # the entry only after a final identity re-proof — a leaf substituted even at
            # the quarantine name is retained, never deleted.
            ok, why = remove_bound(txn.fd, qname, [handle.st_dev, handle.st_ino])
            if not ok:
                return False, f"quarantined removal refused — {why} (evidence at {qname!r})"
            if txn.leaf_kind(qname) != "absent":
                return False, f"removal could not be proven — remainder at {qname!r}"
            txn.fsync()
            return True, "removed"
    except PathContainmentError as exc:
        return False, f"source parent unsafe: {exc}"

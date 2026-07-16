"""One authoritative runtime-owned filesystem API (Workstream §6).

Every mutation of a controller-owned runtime path (config, state, logs, markers,
launchers, locks) goes through this module so containment and no-follow
guarantees are enforced in ONE place rather than re-implemented per site.

Guarantees (Linux):
  * DESCRIPTOR-ANCHORED traversal: the runtime root is opened as a directory and each
    parent component is opened relative to its parent's fd with `O_DIRECTORY|O_NOFOLLOW`
    (created one component at a time when requested). A parent swapped to a symlink — or
    to a non-directory — between validation and use is refused AT THE SYSCALL, so there is
    no check-then-open / check-then-mutate race;
  * a pre-existing symlink LEAF is refused (we never write/open/delete through a link);
  * logs and locks are opened with `O_NOFOLLOW` relative to the held parent fd;
  * `atomic_write` writes a temp leaf via the parent fd, fsyncs the file, sets mode,
    renames via src/dst dir-fds, then fsyncs the held parent directory fd (durable);
  * deletions are containment- and no-follow-checked, relative to the held parent fd.

This is for RUNTIME STATE only. Source-tree reads/writes are a separate policy and must
not be routed here (a linked external source is never a runtime-state target).
"""

from __future__ import annotations

import os
import stat as _stat
from contextlib import contextmanager
from pathlib import Path

from .paths import Paths, PathContainmentError

__all__ = [
    "PathContainmentError", "mkdir", "ensure_dir", "atomic_write", "atomic_write_bytes",
    "write_marker", "write_launcher", "open_marker_excl", "open_existing_marker", "OwnedMarker", "open_log_append", "open_log_truncate", "open_lock",
    "unlink", "chmod", "replace_symlink", "read_bytes", "read_text", "tail", "listdir",
    "scandir_nofollow", "rename_leaf",
]


def _rel_parts(paths: Paths, path: Path) -> tuple[str, ...]:
    """Decompose an absolute runtime path into safe parts relative to the runtime root.
    Rejects an empty path or a `..` traversal before any descriptor is opened."""
    rel = os.path.relpath(str(path), str(paths.runtime_root))
    parts = tuple(p for p in rel.split(os.sep) if p and p != ".")
    if not parts or ".." in parts:
        raise PathContainmentError(f"path escapes the runtime root: {path}")
    return parts


@contextmanager
def _walk_parent(paths: Paths, path: Path, *, create: bool):
    """Descriptor-anchored descent to `path`'s PARENT under the runtime root. Yields
    (parent_fd, leaf_name). The runtime root is opened `O_DIRECTORY|O_NOFOLLOW`; each
    intermediate component is opened relative to its parent fd with the same flags (a
    symlink or non-directory component raises `PathContainmentError`). With `create=True`,
    intermediate directories are created one component at a time relative to the held
    parent fd. Every descriptor is closed on exit."""
    parts = _rel_parts(paths, path)
    root = str(paths.runtime_root)
    if create:
        os.makedirs(root, exist_ok=True)             # the root (and its external parents)
    # The runtime ROOT is the trusted anchor — it may itself legitimately be a symlink in
    # the operator's setup, so it is NOT opened O_NOFOLLOW; every component UNDER it is.
    fds = [os.open(root, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for comp in parts[:-1]:
            if create:
                try:
                    os.mkdir(comp, 0o755, dir_fd=fds[-1])
                except FileExistsError:
                    pass
            try:
                fds.append(os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=fds[-1]))
            except FileNotFoundError:
                raise                                 # missing intermediate -> leaf absent
            except OSError as exc:                    # ELOOP (symlink) / ENOTDIR (non-dir)
                raise PathContainmentError(
                    f"runtime path component {comp!r} is a symlink or not a "
                    f"directory: {exc}") from exc
        yield fds[-1], parts[-1]
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _is_symlink_leaf(parent_fd: int, leaf: str) -> bool:
    try:
        st = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return _stat.S_ISLNK(st.st_mode)


def ensure_dir(paths: Paths, path: Path) -> Path:
    """Create a contained runtime directory (parents included) via descriptor-anchored
    traversal — each component made/opened `O_DIRECTORY|O_NOFOLLOW` relative to its parent
    fd, so a symlinked component can never redirect the creation outside the root."""
    with _walk_parent(paths, path, create=True) as (parent_fd, name):
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _stat.S_ISDIR(st.st_mode):
            raise PathContainmentError(f"runtime dir leaf is not a directory: {path}")
    return path


def mkdir(paths: Paths, *parts: str) -> Path:
    """Create (parents=True) a contained runtime directory and return it."""
    p = paths.runtime_root.joinpath(*parts)
    ensure_dir(paths, p)
    return p


def atomic_write(paths: Paths, path: Path, text: str, mode: int = 0o644) -> None:
    """Atomically write `text` (UTF-8) to a contained runtime leaf. Thin wrapper over the
    byte-oriented core `atomic_write_bytes` (defaults 0o644 for text config)."""
    atomic_write_bytes(paths, path, text.encode("utf-8"), mode)


def atomic_write_bytes(paths: Paths, path: Path, data: bytes, mode: int = 0o600) -> None:
    """Atomically write BINARY `data` to a contained runtime leaf, descriptor-anchored — the
    single core of the write protocol (`atomic_write` encodes text and delegates here). A unique
    temp leaf is created via the held parent fd (O_CREAT|O_EXCL|O_NOFOLLOW), written, its mode set
    on the OPEN fd (`fchmod` — no leaf re-resolve, umask-proof), fsynced, then renamed over the
    target via src/dst dir-fds and the parent dir fd fsynced. Refuses a symlink leaf or an
    escaping/symlinked-parent path. Used for secret-bearing artifacts (e.g. PKCS#12); defaults 0600."""
    with _walk_parent(paths, path, create=True) as (parent_fd, name):
        if _is_symlink_leaf(parent_fd, name):
            raise PathContainmentError(f"refusing to write through a symlink leaf: {path}")
        # COLLISION-SAFE temp leaf: a unique random nonce + O_CREAT|O_EXCL|O_NOFOLLOW, so we
        # never TRUNCATE/consume an existing temp (e.g. another same-process write to the
        # same leaf). Bounded retry on the astronomically-unlikely FileExistsError.
        tmp, fd = None, None
        for _ in range(64):
            cand = f".{name}.tmp-{os.getpid()}-{os.urandom(8).hex()}"
            try:
                fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             mode, dir_fd=parent_fd)
                tmp = cand
                break
            except FileExistsError:
                continue
        if tmp is None:
            raise OSError(f"could not create a unique temp file for {path}")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fchmod(fh.fileno(), mode)          # mode on the HELD fd (umask-proof, no re-resolve)
                os.fsync(fh.fileno())                 # fsync AFTER the mode is set -> metadata durable
            os.rename(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)                       # durable rename (parent dir entry)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=parent_fd)
            except OSError:
                pass
            raise


def write_marker(paths: Paths, path: Path, text: str, mode: int = 0o600) -> None:
    """Write a small runtime state marker atomically (containment + no-follow)."""
    atomic_write(paths, path, text, mode)


def write_launcher(paths: Paths, path: Path, text: str) -> Path:
    """Write a generated launcher/spec to a contained runtime leaf (no-follow)."""
    atomic_write(paths, path, text, 0o600)
    return paths.runtime_root.joinpath(*_rel_parts(paths, path))


class OwnedMarker:
    """A retained-descriptor handle for a transaction-owned marker (e.g. the source-txn
    journal): the journal PARENT dir fd, the journal FILE fd, the leaf name, and the original
    `st_dev`/`st_ino`. Every update/removal verifies the VISIBLE leaf (via the held parent fd,
    no-follow) still matches that identity BEFORE and AFTER writing through the retained file
    fd — so a replacement leaf injected after creation is never written, trusted, or removed.
    Close releases both fds (idempotent)."""

    def __init__(self, name: str, parent_fd: int, file_fd: int, st_dev: int, st_ino: int):
        self.name = name
        self.parent_fd = parent_fd
        self.file_fd = file_fd
        self.st_dev = st_dev
        self.st_ino = st_ino

    def _visible_matches(self) -> bool:
        try:
            st = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except OSError:
            return False
        return (_stat.S_ISREG(st.st_mode) and st.st_dev == self.st_dev
                and st.st_ino == self.st_ino)

    def _write_all(self, data: bytes) -> None:
        """Write the COMPLETE payload — `os.write` may consume fewer bytes than given, so
        loop until every byte is written."""
        mv = memoryview(data)
        while mv:
            n = os.write(self.file_fd, mv)
            if n <= 0:
                raise OSError("short write to owned marker")
            mv = mv[n:]

    def read(self) -> bytes:
        """Read the FULL journal payload through the RETAINED file fd, ONLY while the visible
        leaf is still our inode. Raises OSError on a mismatch/read error (the caller treats it
        as an unreadable/replaced journal)."""
        if not self._visible_matches():
            raise OSError("owned marker identity lost before read")
        os.lseek(self.file_fd, 0, os.SEEK_SET)
        chunks = []
        while True:
            b = os.read(self.file_fd, 65536)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)

    def rewrite(self, text: str) -> bool:
        """Rewrite the journal to `text` through the RETAINED file fd, but only while the
        VISIBLE leaf is still our inode. Verifies visible identity before AND after the write
        (a replacement swapped in during the write is detected), fsyncs the file. Returns
        False on any mismatch — the caller then rolls back and retains evidence."""
        if not self._visible_matches():
            return False
        try:
            os.ftruncate(self.file_fd, 0)
            os.lseek(self.file_fd, 0, os.SEEK_SET)
            self._write_all(text.encode("utf-8"))       # COMPLETE write (no partial-write bug)
            os.fsync(self.file_fd)
        except OSError:
            return False
        return self._visible_matches()          # re-verify: a mid-write swap is caught here

    def remove(self) -> bool:
        """Unlink the journal ONLY if the visible leaf is still our inode, then fsync the
        held journal parent (AFTER the unlink). Returns False (leaving any replacement leaf
        untouched) if identity no longer matches. A tiny window between the final identity
        observation and the `unlink` syscall is an unavoidable same-account namespace race."""
        if not self._visible_matches():
            return False
        try:
            os.unlink(self.name, dir_fd=self.parent_fd)
        except OSError:
            return False
        try:
            os.fsync(self.parent_fd)            # durable removal (parent fsync AFTER unlink)
        except OSError:
            pass
        return True

    def close(self) -> None:
        for attr in ("file_fd", "parent_fd"):
            fd = getattr(self, attr)
            if fd is not None and fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, -1)


def open_marker_excl(paths: Paths, path: Path, text: str, mode: int = 0o600) -> "OwnedMarker":
    """Create a NEW marker EXCLUSIVELY (`O_CREAT|O_EXCL|O_NOFOLLOW`) under a descriptor-walked
    parent, RETAINING both the journal file fd and a dup of the parent dir fd. Raises
    `FileExistsError` if ANY leaf already exists (regular/symlink/special/stale) — never
    overwritten. fsyncs the file AND the parent dir (durable creation). Returns an
    `OwnedMarker`; the caller MUST `close()` it."""
    data = text.encode("utf-8")
    with _walk_parent(paths, path, create=True) as (parent_fd, name):
        file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode,
                          dir_fd=parent_fd)
        try:
            # os.dup can itself fail under fd exhaustion (EMFILE/ENFILE); build the marker
            # INSIDE the try so file_fd is always closed on any failure (AUDIT FS4 — and
            # its re-review: the dup must be guarded too, not just the write).
            marker = OwnedMarker(name, os.dup(parent_fd), file_fd, 0, 0)
            marker._write_all(data)             # COMPLETE write (loops over partial writes)
            os.fsync(file_fd)
            st = os.fstat(file_fd)
            marker.st_dev, marker.st_ino = st.st_dev, st.st_ino
        except BaseException:
            try:
                marker.close()                  # closes file_fd AND the dup'd parent fd
            except NameError:
                os.close(file_fd)               # dup failed before marker existed
            raise
        try:
            os.fsync(parent_fd)                 # the new dir entry is durable
        except OSError:
            pass
    return marker


def open_existing_marker(paths: Paths, path: Path) -> "OwnedMarker":
    """Open an EXISTING regular marker for owned handling: walk the parent no-follow, open the
    leaf `O_RDWR|O_NOFOLLOW`, RETAIN the file fd + a dup of the parent fd, and capture its
    device/inode. Raises OSError if the leaf is absent, a symlink, or non-regular. Used by
    recovery so a journal it validated cannot be replaced-then-removed: every later
    read/rewrite/remove re-verifies the visible leaf is still this exact inode. Caller MUST
    `close()`."""
    with _walk_parent(paths, path, create=False) as (parent_fd, name):
        file_fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            st = os.fstat(file_fd)
            if not _stat.S_ISREG(st.st_mode):
                raise OSError(f"marker {path} is not a regular file")
            pfd = os.dup(parent_fd)
        except BaseException:
            os.close(file_fd)
            raise
    return OwnedMarker(name, pfd, file_fd, st.st_dev, st.st_ino)


def _open_leaf(paths: Paths, path: Path, flags: int, mode: int, *, create_dirs: bool):
    """Open a runtime leaf relative to its descriptor-anchored parent fd. Returns the open
    fd (the parent fds are closed; the leaf fd stays open)."""
    with _walk_parent(paths, path, create=create_dirs) as (parent_fd, name):
        return os.open(name, flags, mode, dir_fd=parent_fd)


def open_log_append(paths: Paths, path: Path):
    """Open a runtime log for append with O_NOFOLLOW, anchored to its parent fd."""
    fd = _open_leaf(paths, path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                    0o644, create_dirs=True)
    return os.fdopen(fd, "ab")


def open_log_truncate(paths: Paths, path: Path):
    """Create/TRUNCATE a runtime log with O_NOFOLLOW (anchored), returning a text handle."""
    fd = _open_leaf(paths, path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    0o644, create_dirs=True)
    return os.fdopen(fd, "w", encoding="utf-8")


def open_lock(paths: Paths, path: Path):
    """Open a runtime lock file for exclusive flock with O_NOFOLLOW (anchored). Caller flocks."""
    fd = _open_leaf(paths, path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600,
                    create_dirs=True)
    return os.fdopen(fd, "w")


def read_bytes(paths: Paths, path: Path) -> bytes:
    """Read a contained runtime leaf as bytes, descriptor-anchored: the leaf is opened
    `O_RDONLY|O_NOFOLLOW` relative to its parent fd, so a symlink leaf (or a parent swapped
    to a symlink) is refused AT THE OPEN. Raises PathContainmentError on escape, OSError if
    missing/symlinked."""
    with _walk_parent(paths, path, create=False) as (parent_fd, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as fh:
            return fh.read()


def read_text(paths: Paths, path: Path) -> str:
    """No-follow text read (see `read_bytes`)."""
    return read_bytes(paths, path).decode("utf-8")


def read_text_regular(paths: Paths, path: Path, *, max_bytes: int = 1 << 20) -> str:
    """Descriptor-safe read of a runtime leaf that MUST be a REGULAR file — the inspection primitive
    for untrusted state markers. It distinguishes a safely-ABSENT leaf (raises `FileNotFoundError`)
    from a present-but-UNSAFE one (symlink, FIFO/device, directory, escaped/unsafe parent — raises
    `OSError`/`PathContainmentError`), WITHOUT any check-then-open or path-following existence probe:
    the parent is descended `O_NOFOLLOW` and the leaf opened `O_RDONLY|O_NOFOLLOW|O_NONBLOCK` (never
    follows a symlink, never blocks on a FIFO), then `fstat`-verified as a regular file before reading.
    Bounded to `max_bytes`."""
    with _walk_parent(paths, path, create=False) as (parent_fd, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            if not _stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"refusing to read a non-regular runtime leaf: {path}")
            chunks: list[bytes] = []
            got = 0
            while got < max_bytes:
                b = os.read(fd, min(65536, max_bytes - got))
                if not b:
                    break
                chunks.append(b)
                got += len(b)
            return b"".join(chunks).decode("utf-8", "replace")
        finally:
            os.close(fd)


def tail(paths: Paths, path: Path, lines: int = 200, max_bytes: int = 256 * 1024) -> list[str]:
    """Bounded NO-FOLLOW, descriptor-anchored tail read of a contained runtime log. A
    missing leaf, a symlinked leaf/parent, or an escape returns []."""
    try:
        with _walk_parent(paths, path, create=False) as (parent_fd, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            with os.fdopen(fd, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                data = fh.read()
    except (OSError, PathContainmentError):
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def tail_since(paths: Paths, path: Path, floor: int, lines: int = 200,
               max_bytes: int = 256 * 1024) -> list[str]:
    """Like `tail`, but only content at/after byte offset `floor`. Used to CLEAR a live feed at a
    lifecycle boundary without truncating the underlying append-only log (which the logs view still
    shows in full). A `floor` at/beyond the current size, or a size SMALLER than `floor` (the log was
    rotated/replaced shorter), is treated as 0 so a shrunken log shows its whole new content rather
    than nothing. NO-FOLLOW, descriptor-anchored; a missing/symlinked/escaping leaf returns []."""
    try:
        with _walk_parent(paths, path, create=False) as (parent_fd, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            with os.fdopen(fd, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                start = floor if 0 < floor <= size else 0
                if size - start > max_bytes:      # keep the LAST max_bytes of the post-floor span
                    start = size - max_bytes
                if start:
                    fh.seek(start)
                data = fh.read()
    except (OSError, PathContainmentError):
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def listdir(paths: Paths, path: Path) -> list[str]:
    """List the entry NAMES of a contained runtime directory through a descriptor-anchored,
    no-follow directory fd (the dir is opened `O_DIRECTORY|O_NOFOLLOW` relative to its
    parent fd). A missing or symlinked/non-directory target returns []. Callers must still
    open each entry no-follow via `read_bytes`/etc."""
    try:
        with _walk_parent(paths, path, create=False) as (parent_fd, name):
            dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                return sorted(os.listdir(dfd))
            finally:
                os.close(dfd)
    except (OSError, PathContainmentError):
        return []


def scandir_nofollow(paths: Paths, path: Path) -> list[tuple[str, bool]]:
    """Enumerate a runtime directory through a descriptor-anchored, no-follow dir fd,
    returning a sorted list of (entry_name, is_symlink). Unlike `listdir`, this makes the
    security-relevant distinction a caller needs BEFORE mutating:
      * a MISSING directory (or missing intermediate) returns [];
      * a directory that is itself a SYMLINK, escaping, or a non-directory raises
        PathContainmentError — an unsafe container is NEVER reported as empty.
    Each entry's is_symlink flag comes from a no-follow lstat relative to the held dir fd,
    so a caller can BLOCK on a symlinked entry rather than skip it."""
    try:
        with _walk_parent(paths, path, create=False) as (parent_fd, name):
            try:
                dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=parent_fd)
            except FileNotFoundError:
                return []
            except OSError as exc:                    # ELOOP (symlink) / ENOTDIR (non-dir)
                raise PathContainmentError(
                    f"runtime dir {path} is a symlink or not a directory: {exc}") from exc
            try:
                out: list[tuple[str, bool]] = []
                for entry in sorted(os.listdir(dfd)):
                    try:
                        st = os.stat(entry, dir_fd=dfd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    out.append((entry, _stat.S_ISLNK(st.st_mode)))
                return out
            finally:
                os.close(dfd)
    except FileNotFoundError:
        return []                                      # a missing intermediate -> absent


def stat_leaf_nofollow(paths: Paths, path: Path):
    """Descriptor-anchored, no-follow lstat of a runtime leaf: the parent is walked
    O_NOFOLLOW and the leaf is stat'd RELATIVE TO the held parent fd, so neither a swapped
    parent NOR a symlink leaf is ever followed (a path-based `os.stat` would follow a
    parent swapped between enumeration and stat). Returns `os.stat_result`, or `None` when
    the leaf is absent/unreadable/escaping — the caller treats `None` as "uncertain, do
    not act on it"."""
    try:
        with _walk_parent(paths, path, create=False) as (parent_fd, name):
            try:
                return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                return None
    except (OSError, PathContainmentError):
        return None


def unlink(paths: Paths, path: Path) -> None:
    """Delete a contained runtime leaf safely, descriptor-anchored: never through a symlink
    leaf (refused), never following a swapped parent. A missing leaf is a no-op."""
    try:
        with _walk_parent(paths, path, create=False) as (parent_fd, name):
            if _is_symlink_leaf(parent_fd, name):
                raise PathContainmentError(f"refusing to unlink a symlink leaf: {path}")
            try:
                os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def _renameat2_noreplace(parent_fd: int, src_name: str, dst_name: str) -> bool:
    """Attempt a single-syscall no-overwrite rename via Linux `renameat2(RENAME_NOREPLACE)`
    under the shared parent fd. Returns True on success; raises FileExistsError if the
    destination already exists (EEXIST). Returns False (does nothing) when renameat2 is
    unavailable on this libc/kernel (ENOSYS / no wrapper) so the caller can fall back."""
    import ctypes
    import ctypes.util
    import errno as _errno
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fn = libc.renameat2
    except (OSError, AttributeError):
        return False
    fn.restype = ctypes.c_int
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    RENAME_NOREPLACE = 1
    ctypes.set_errno(0)
    rc = fn(parent_fd, os.fsencode(src_name), parent_fd, os.fsencode(dst_name), RENAME_NOREPLACE)
    if rc == 0:
        return True
    eno = ctypes.get_errno()
    if eno == _errno.EEXIST:
        raise FileExistsError(_errno.EEXIST, os.strerror(_errno.EEXIST), dst_name)
    if eno in (_errno.ENOSYS, _errno.EINVAL):
        return False                                  # unsupported flag/syscall -> fall back
    raise OSError(eno, os.strerror(eno), dst_name)


def rename_leaf(paths: Paths, src: Path, dst: Path, *, replace: bool = True) -> None:
    """Rename a runtime leaf to a sibling in the SAME contained directory (descriptor-anchored,
    no-follow on either leaf). Refuses a symlinked source leaf. Raises FileNotFoundError if the
    source is absent. `src` and `dst` MUST share a parent dir.

    `replace=True` (default): plain `os.rename` — atomically REPLACES an existing destination.
    `replace=False`: CLAIM semantics — atomically fail-closed if the destination already exists
    (raise FileExistsError), leaving BOTH leaves untouched. Used by the self-update helper to
    claim `selfupdate.request` -> `selfupdate.inflight` without ever clobbering an in-flight
    record. Atomicity is a single VFS op (Linux `renameat2(RENAME_NOREPLACE)`, with a
    `link`+`unlink` fallback — never a racy check-then-rename)."""
    if src.parent != dst.parent:
        raise ValueError("rename_leaf requires src and dst in the same directory")
    with _walk_parent(paths, src, create=False) as (parent_fd, src_name):
        if _is_symlink_leaf(parent_fd, src_name):
            raise PathContainmentError(f"refusing to rename a symlink leaf: {src}")
        if replace:
            os.rename(src_name, dst.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        elif not _renameat2_noreplace(parent_fd, src_name, dst.name):
            # Fallback (renameat2 unavailable): hardlink is kernel-atomic (EEXIST if dst
            # exists), then drop the source name — never a check-then-act race.
            os.link(src_name, dst.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.unlink(src_name, dir_fd=parent_fd)
        try:
            os.fsync(parent_fd)
        except OSError:
            pass


def chmod(paths: Paths, path: Path, mode: int, *, create_dir: bool = False) -> None:
    """chmod a contained runtime leaf, descriptor-anchored. Refuses to chmod THROUGH a
    symlink leaf (never touches the link target). With `create_dir=True` the leaf is
    created as a directory first (the mkdir-with-mode pre-step). The parent dir is fsynced.
    A swapped/symlinked runtime parent fails closed with PathContainmentError."""
    with _walk_parent(paths, path, create=create_dir) as (parent_fd, name):
        if create_dir:
            try:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
            except FileExistsError:
                pass
            # A `mkdir` pre-step MUST guarantee a DIRECTORY leaf: an existing regular file,
            # special file, or symlink at the leaf is refused (never chmod'd), so a planted
            # file can't absorb the requested mode. lstat (no-follow) relative to the fd.
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not _stat.S_ISDIR(st.st_mode):
                raise PathContainmentError(
                    f"mkdir pre-step leaf exists and is not a directory: {path}")
        if _is_symlink_leaf(parent_fd, name):
            raise PathContainmentError(f"refusing to chmod through a symlink leaf: {path}")
        # follow_symlinks=False: even if the leaf is (racily) a symlink, never chmod its
        # target; on a regular file/dir this sets the leaf's own mode. If the leaf IS a symlink
        # at syscall time (the narrow race past the check above), CPython raises ValueError
        # ("cannot use dir_fd and follow_symlinks together") — that is a symlink-leaf refusal, so
        # re-raise it typed. (Only ValueError/NotImplementedError — ordinary OSError stays truthful.)
        try:
            os.chmod(name, mode, dir_fd=parent_fd, follow_symlinks=False)
        except (ValueError, NotImplementedError) as exc:
            raise PathContainmentError(f"refusing to chmod through a symlink leaf: {path}") from exc
        try:
            os.fsync(parent_fd)
        except OSError:
            pass


def replace_symlink(paths: Paths, path: Path, target: str) -> None:
    """Atomically make `path` a symlink to `target`, descriptor-anchored. May REMOVE an
    existing symlink or regular-file leaf, but REFUSES a real directory (never rmtree). The
    swap is done via a unique temp symlink + rename over the leaf, so a concurrent reader
    never sees a missing link; the parent dir fd is fsynced for durability. A swapped or
    symlinked runtime PARENT fails closed with PathContainmentError."""
    with _walk_parent(paths, path, create=True) as (parent_fd, name):
        try:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _stat.S_ISDIR(st.st_mode):
                raise PathContainmentError(
                    f"refusing to replace a real directory with a symlink: {path}")
        except FileNotFoundError:
            pass
        tmp = None
        for _ in range(64):
            cand = f".{name}.lnk-{os.getpid()}-{os.urandom(8).hex()}"
            try:
                os.symlink(target, cand, dir_fd=parent_fd)
                tmp = cand
                break
            except FileExistsError:
                continue
        if tmp is None:
            raise OSError(f"could not create a unique temp symlink for {path}")
        try:
            os.rename(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=parent_fd)
            except OSError:
                pass
            raise

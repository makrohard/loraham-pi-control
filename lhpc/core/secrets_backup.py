"""`lhpc secrets backup` / `restore`: ONE plain tar of the box's certificates, secrets and stack
identities, and its restore (A5). The service layer (`service_secrets.py`) holds the locks and
knows the stacks; this module reads and checks the files, writes the backup file and, on a restore,
the targets.

The file: every member but `MANIFEST.json` is a directory or a regular file under `config/tls/`,
`config/secrets/`, `config/secrets.toml` or a declared `state/<name>/`, with its runtime-relative
name and mode (no timestamps, no ownership); `MANIFEST.json` is the LAST member and lists every other member (type,
mode, size, sha256) and the absent targets. Nothing here is encrypted: the file is written 0600
and the operator is told to encrypt it before it leaves the box.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import shutil
import stat
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = "lhpc-secrets-backup"
FORMAT_VERSION = 1
MANIFEST = "MANIFEST.json"
TLS, SECRETS, SECRETS_TOML = "config/tls", "config/secrets", "config/secrets.toml"
PKI_ONLY = ("config/tls/server-ca", "config/tls/client-ca")
_INDEX = "config/tls/client-ca/client-index.json"
_MARKER = "config/tls/unverified-clock"
_CHUNK = 1 << 20

CLEAR_TEXT_LINE = ("This file holds the box's secrets and node identities in clear, readable only by you. "
                   "Keep it on this box or copy it off with care (for example encrypt it first: gpg -c <file>).")
BUNDLE_LINE = ("the bundles' one-time passphrases are not in this file; a device whose passphrase is lost "
                   "gets a new certificate with lhpc webserver cert reissue <label>")
NOT_COMPLETE = "not a complete LHPC secrets backup"


class SecretsError(Exception):
    """A refusal: nothing was written to a restore target (or the partial backup file was removed)."""


class ApplyFailed(Exception):
    """A restore stopped part-way: the targets before `current` are restored, `current` is partial."""

    def __init__(self, reason: str, done: list, current: str):
        super().__init__(reason)
        self.reason, self.done, self.current = reason, done, current


@dataclass
class Member:
    name: str
    kind: str                       # "dir" | "file"
    mode: int
    size: int = 0
    sha256: str = ""


@dataclass
class Archive:
    """Pass 1's result: the members in archive order (MANIFEST excluded), the manifest itself."""
    infos: list
    manifest: dict
    absent: set = field(default_factory=set)

    @property
    def names(self) -> set:
        return {i.name for i in self.infos}

    def carries(self, target: str) -> bool:
        return target in self.names


# ---- backup ---------------------------------------------------------------------------------

def _walk(root: Path, rel: str, out: list) -> None:
    """Append `rel` and everything below it (directories first, then their entries, sorted);
    anything but a directory or a regular file is a refusal. Never follows a symlink."""
    st = os.lstat(root / rel)
    if stat.S_ISDIR(st.st_mode):
        out.append(Member(rel, "dir", stat.S_IMODE(st.st_mode)))
        with os.scandir(root / rel) as it:
            names = sorted(e.name for e in it)
        for n in names:
            _walk(root, f"{rel}/{n}", out)
    elif stat.S_ISREG(st.st_mode):
        out.append(Member(rel, "file", stat.S_IMODE(st.st_mode), size=st.st_size))
    else:
        raise SecretsError(f"{rel} is a symlink or a special file; the backup takes directories "
                           "and regular files only")


def inventory(root: Path, state_roots) -> tuple[list, list]:
    """(members, absent targets) of a runtime root, in the order they are written."""
    members, absent = [], []

    def _kind(rel, want):            # the restore's type rule for a top-level target (pass 1)
        st = os.lstat(root / rel)
        ok = stat.S_ISDIR(st.st_mode) if want == "folder" else stat.S_ISREG(st.st_mode)
        if not ok:
            raise SecretsError(f"{rel} is not a {want}; a restore would refuse this backup")
    if os.path.lexists(root / TLS):
        _kind(TLS, "folder")
        _walk(root, TLS, members)
    else:
        absent.append(TLS)                      # a root where `webserver init` never ran
    if not os.path.isdir(root / SECRETS) or os.path.islink(root / SECRETS):
        raise SecretsError("the runtime root is not bootstrapped (config/secrets is missing)")
    _walk(root, SECRETS, members)
    if os.path.lexists(root / SECRETS_TOML):
        _kind(SECRETS_TOML, "file")
        _walk(root, SECRETS_TOML, members)
    for r in sorted(state_roots):
        if os.path.lexists(root / r):
            _kind(r, "folder")
            _walk(root, r, members)
        else:
            absent.append(r)
    return members, absent


def estimated_size(members) -> int:
    """The tar's size, bounded from above: a header per member, the data in 512-byte blocks,
    the manifest and the end blocks."""
    data = sum(512 + -(-m.size // 512) * 512 for m in members)
    return data + 512 * (len(members) + 64) + 10240


class _HashingReader(io.RawIOBase):
    def __init__(self, fd: int):
        self.fd, self.h, self.count = fd, hashlib.sha256(), 0

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        b = os.read(self.fd, _CHUNK if n is None or n < 0 else n)
        self.h.update(b)
        self.count += len(b)
        return b


def _tarinfo(name: str, kind: str, mode: int, size: int = 0) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.DIRTYPE if kind == "dir" else tarfile.REGTYPE
    ti.mode, ti.size, ti.mtime = mode, size, 0
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def create_file(dirfd: int, name: str) -> int:
    """The backup file, created in the open folder `dirfd`: never over an existing name or a
    symlink (O_EXCL, O_NOFOLLOW), mode 0600."""
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=dirfd)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)                 # the file is ours (O_EXCL): leave nothing behind
        os.unlink(name, dir_fd=dirfd)
        raise
    return fd


def write_backup(fd: int, dirfd: int, root: Path, members: list, meta: dict, absent: list) -> dict:
    """Stream every member into the created file `fd` (each file as its own regular member, read
    once, size and sha256 recorded), then MANIFEST.json last; fsync it and its folder. Returns
    the manifest. Closes `fd`; the caller unlinks the file on any error."""
    with os.fdopen(fd, "wb") as f:
        with tarfile.open(fileobj=f, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for m in members:
                if m.kind == "dir":
                    tar.addfile(_tarinfo(m.name, "dir", m.mode))
                    continue
                rfd = os.open(root / m.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    st = os.fstat(rfd)
                    if not stat.S_ISREG(st.st_mode):
                        raise SecretsError(f"{m.name} changed type during the backup")
                    m.size, m.mode = st.st_size, stat.S_IMODE(st.st_mode)
                    rd = _HashingReader(rfd)
                    try:
                        tar.addfile(_tarinfo(m.name, "file", m.mode, m.size), rd)
                    except OSError as exc:
                        raise SecretsError(f"{m.name} changed during the backup ({exc})") from None
                    if os.read(rfd, 1):
                        raise SecretsError(f"{m.name} grew during the backup")
                    m.sha256 = rd.h.hexdigest()
                finally:
                    os.close(rfd)
            manifest = {**meta, "format": FORMAT, "version": FORMAT_VERSION,
                        "members": [{"name": m.name, "type": m.kind, "mode": m.mode,
                                     **({"size": m.size, "sha256": m.sha256} if m.kind == "file" else {})}
                                    for m in members],
                        "absent": sorted(absent)}
            raw = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
            tar.addfile(_tarinfo(MANIFEST, "file", 0o600, len(raw)), io.BytesIO(raw))
        f.flush()
        os.fsync(f.fileno())
    os.fsync(dirfd)
    return manifest


def _sha256_of(fd: int) -> tuple[int, str]:
    h, n = hashlib.sha256(), 0
    while True:
        b = os.read(fd, _CHUNK)
        if not b:
            return n, h.hexdigest()
        h.update(b)
        n += len(b)


def recheck(root: Path, members: list) -> None:
    """Defence in depth: every member read again equals what was written (a refusal otherwise)."""
    for m in members:
        p = root / m.name
        try:
            st = os.lstat(p)
            if m.kind == "dir":
                ok = stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == m.mode
            else:
                fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    ok = (stat.S_IMODE(os.fstat(fd).st_mode) == m.mode
                          and _sha256_of(fd) == (m.size, m.sha256))
                finally:
                    os.close(fd)
        except OSError:
            ok = False
        if not ok:
            raise SecretsError(f"{m.name} changed during the backup")


def file_digest(dirfd: int, name: str) -> tuple[int, str]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
    try:
        return _sha256_of(fd)
    finally:
        os.close(fd)


# ---- restore: pass 1 ------------------------------------------------------------------------

def _canonical(name: str) -> bool:
    return bool(name) and "\0" not in name and not name.startswith("/") and all(
        c not in ("", ".", "..") for c in name.split("/"))


def _top_target(name: str, declared: set) -> str | None:
    """The restore target a member belongs to (`config/tls`, `config/secrets`,
    `config/secrets.toml`, `state/<declared>`), or None outside them."""
    if name == SECRETS_TOML:
        return SECRETS_TOML
    for t in (TLS, SECRETS):
        if name == t or name.startswith(t + "/"):
            return t
    parts = name.split("/")
    if len(parts) >= 2 and parts[0] == "state" and f"state/{parts[1]}" in declared:
        return f"state/{parts[1]}"
    return None


def _reader(snapfd: int):
    """The snapshot from its start, through its own descriptor (the offset is shared)."""
    f = os.fdopen(os.dup(snapfd), "rb")
    f.seek(0)
    return f


def _hash_stream(src) -> str:
    h = hashlib.sha256()
    while b := src.read(_CHUNK):
        h.update(b)
    return h.hexdigest()


def read_archive(snapfd: int, declared) -> Archive:
    """Pass 1 on the private snapshot: the archive is the backup's own uncompressed tar and
    complete, or `SecretsError`. Reads every file member once to compare it with the manifest."""
    head = os.pread(snapfd, 512, 0)
    if len(head) < 512 or head[257:262] != b"ustar":
        raise SecretsError("not an uncompressed LHPC secrets backup")
    with _reader(snapfd) as f:
        try:
            with tarfile.open(fileobj=f, mode="r:") as tar:      # "r:": never decompress
                return _check(tar, set(declared))
        except (tarfile.TarError, OSError, EOFError):
            raise SecretsError(f"{NOT_COMPLETE} (unreadable tar)") from None


def _check(tar, declared: set) -> Archive:
    infos = tar.getmembers()
    if not infos or infos[-1].name != MANIFEST or not infos[-1].isfile():
        raise SecretsError(f"{NOT_COMPLETE} ({MANIFEST} is not the last member)")
    seen = set()
    for i in infos:
        if i.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} is neither a file nor a directory)")
        if not _canonical(i.name):
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} is not a plain relative name)")
        if i.name in seen:
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} appears twice)")
        seen.add(i.name)
        if i.name != MANIFEST and _top_target(i.name, declared) is None:
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} is outside the backup's folders)")
    members = infos[:-1]
    try:
        manifest = json.loads(tar.extractfile(infos[-1]).read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SecretsError(f"{NOT_COMPLETE} ({MANIFEST} is unreadable)") from None
    if not (isinstance(manifest, dict) and manifest.get("format") == FORMAT
            and manifest.get("version") == FORMAT_VERSION
            and isinstance(manifest.get("members"), list) and isinstance(manifest.get("absent"), list)):
        raise SecretsError(f"{NOT_COMPLETE} ({MANIFEST} is not an LHPC secrets manifest)")
    absent = manifest["absent"]
    if (not all(isinstance(a, str) for a in absent) or len(set(absent)) != len(absent)
            or any(a != TLS and a not in declared for a in absent)):
        raise SecretsError(f"{NOT_COMPLETE} (an absent target is not one of this box's)")
    absent = set(absent)
    by_name = {i.name: i for i in members}
    sec, tls, toml = by_name.get(SECRETS), by_name.get(TLS), by_name.get(SECRETS_TOML)
    if sec is None or not sec.isdir():
        raise SecretsError(f"{NOT_COMPLETE} (config/secrets is missing)")
    if (tls is None) == (TLS not in absent) or (tls is not None and not tls.isdir()):
        raise SecretsError(f"{NOT_COMPLETE} (config/tls is neither a folder nor listed absent)")
    if toml is not None and not toml.isfile():
        raise SecretsError(f"{NOT_COMPLETE} (config/secrets.toml is not a file)")
    for i in members:
        top = _top_target(i.name, declared)
        if i.name == top:                      # a top-level target: exempt from the parent rule
            if top.startswith("state/") and (not i.isdir() or top in absent):
                raise SecretsError(f"{NOT_COMPLETE} ({top} is not a folder, or is also listed absent)")
            continue
        parent = by_name.get(posixpath.dirname(i.name))
        if parent is None or not parent.isdir():
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} has no folder above it)")
    listed, want = manifest["members"], {}
    for e in listed:
        if not isinstance(e, dict) or e.get("name") not in by_name or e["name"] in want:
            raise SecretsError(f"{NOT_COMPLETE} (the manifest and the members differ)")
        want[e["name"]] = e
    if len(want) != len(members):
        raise SecretsError(f"{NOT_COMPLETE} (the manifest and the members differ)")
    for i in members:
        e = want[i.name]
        kind = "dir" if i.isdir() else "file"
        if not 0 <= i.mode <= 0o7777:
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} has an impossible mode {i.mode:o})")
        if e.get("type") != kind or e.get("mode") != i.mode or (kind == "file" and (
                e.get("size") != i.size or e.get("sha256") != _hash_stream(tar.extractfile(i)))):
            raise SecretsError(f"{NOT_COMPLETE} ({i.name!r} differs from the manifest)")
    return Archive(infos=members, manifest=manifest, absent=absent)


def member_bytes(snapfd: int, name: str) -> bytes | None:
    """One small member's bytes from the snapshot (the client index for the revocation warning)."""
    with _reader(snapfd) as f, tarfile.open(fileobj=f, mode="r:") as tar:
        try:
            src = tar.extractfile(name)
        except KeyError:
            return None
        return src.read() if src is not None else None


# ---- restore: the plan and the apply -------------------------------------------------------

def targets(archive: Archive, only_pki: bool) -> list:
    """The restore targets the archive carries, in the order they are written."""
    if only_pki:
        return [t for t in PKI_ONLY if archive.carries(t)]
    return [t for t in (TLS, SECRETS, SECRETS_TOML) if archive.carries(t)] + sorted(
        i.name for i in archive.infos if i.name.startswith("state/") and i.name.count("/") == 1)


def classify(root: Path, archive: Archive, declared, only_pki: bool) -> dict:
    """OVERWRITTEN / CREATED / LEFT AS IT IS, from the box as it is now."""
    carried = targets(archive, only_pki)
    over = [t for t in carried if os.path.lexists(root / t)]
    created = [t for t in carried if t not in over]
    left = []
    if not only_pki:
        left = [r for r in sorted(declared) if r not in carried and os.path.lexists(root / r)]
        if TLS in archive.absent and os.path.lexists(root / TLS):
            left.append(TLS)
        if not archive.carries(SECRETS_TOML) and os.path.lexists(root / SECRETS_TOML):
            left.append(SECRETS_TOML)
    return {"overwritten": over, "created": created, "left": left}


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_dirs(start: int | Path, rel: str) -> int:
    """A descriptor of the folder `rel` below `start` (a path or a folder descriptor), reached one
    component at a time with O_NOFOLLOW: a symlink anywhere on the way fails (ELOOP/ENOTDIR),
    and a later swap of any of them cannot redirect what is done relative to the result."""
    fd = os.open(start, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC) if isinstance(start, Path) \
        else os.dup(start)
    try:
        for part in (p for p in rel.split("/") if p):
            nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except OSError:
        os.close(fd)
        raise
    return fd


def _remove_at(pfd: int, name: str) -> None:
    """Remove `name` in the folder `pfd`: a folder with its contents (rmtree relative to the
    descriptor, never following a link), anything else (a link included) by unlink."""
    if stat.S_ISDIR(os.stat(name, dir_fd=pfd, follow_symlinks=False).st_mode):
        shutil.rmtree(name, dir_fd=pfd)
    else:
        os.unlink(name, dir_fd=pfd)


def _write_all(fd: int, b: bytes) -> None:
    view = memoryview(b)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def check_ancestors(root: Path, target: str) -> None:
    """Every folder between the runtime root and `target` is a real folder, not a symlink
    (`lstat`): a restore never removes or writes through a link that leads out of the root."""
    parts = target.split("/")
    for i in range(1, len(parts)):
        rel = "/".join(parts[:i])
        try:
            st = os.lstat(root / rel)
        except FileNotFoundError:
            raise SecretsError(f"{rel} is missing") from None
        if not stat.S_ISDIR(st.st_mode):
            raise SecretsError(f"{rel} is a symlink or not a folder; a restore writes only through "
                               "real folders")


def apply(root: Path, snapfd: int, archive: Archive, plan: dict, only_pki: bool) -> list:
    """Pass 2: target by target, remove the box's (OVERWRITTEN only) and write the archive's
    members from the same snapshot. Every step runs relative to folder descriptors opened with
    O_NOFOLLOW from the runtime root, so no symlink, even one swapped in meanwhile, can lead it
    out. A CREATED target is made so that its creation FAILS if it exists meanwhile; nothing found
    there is removed. Raises `ApplyFailed` part-way (`current` None: that target was not touched)."""
    sha = {e["name"]: e.get("sha256", "") for e in archive.manifest["members"]}
    done = []
    with _reader(snapfd) as f, tarfile.open(fileobj=f, mode="r:") as tar:
        for t in plan["overwritten"] + plan["created"]:
            parent, _, base = t.rpartition("/")
            mine = [i for i in archive.infos if i.name == t or i.name.startswith(t + "/")]
            try:
                check_ancestors(root, t)             # again, right before the removal
                pfd = _open_dirs(root, parent)
            except (SecretsError, OSError) as exc:
                raise ApplyFailed(f"{parent} cannot be opened as a real folder ({exc})",
                                  done, None) from None
            try:
                if t in plan["overwritten"]:
                    try:
                        _remove_at(pfd, base)
                    except FileNotFoundError:
                        pass
                dirs = sorted((i for i in mine if i.isdir()), key=lambda i: i.name.count("/"))
                for i in dirs:
                    rel = i.name[len(parent) + 1:]
                    d, _, n = rel.rpartition("/")
                    dfd = _open_dirs(pfd, d)
                    try:
                        os.mkdir(n, 0o700, dir_fd=dfd)
                        nfd = os.open(n, _DIR_FLAGS, dir_fd=dfd)
                        os.fchmod(nfd, 0o700)    # writable whatever the umask; its mode at the end
                        os.close(nfd)
                    finally:
                        os.close(dfd)
                for i in mine:
                    if i.isfile() and _write_member_checked(pfd, i.name[len(parent) + 1:], tar, i) \
                            != sha[i.name]:
                        raise ApplyFailed(f"{i.name} was written differently from the backup",
                                          done, t)
                for i in sorted(dirs, key=lambda i: -i.name.count("/")):
                    dfd = _open_dirs(pfd, i.name[len(parent) + 1:])
                    try:
                        os.fchmod(dfd, i.mode)
                    finally:
                        os.close(dfd)
            except FileExistsError as exc:
                raise ApplyFailed(f"{exc.filename or t} appeared meanwhile; nothing there was "
                                  "removed", done, t) from None
            except OSError as exc:
                raise ApplyFailed(str(exc), done, t) from None
            finally:
                os.close(pfd)
            done.append(t)
    return done


def _write_member_checked(pfd: int, rel: str, tar, info) -> str:
    d, _, n = rel.rpartition("/")
    dfd = _open_dirs(pfd, d)
    try:
        fd = os.open(n, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                     dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        h = hashlib.sha256()
        src = tar.extractfile(info)
        while b := src.read(_CHUNK):
            h.update(b)
            _write_all(fd, b)
        os.fsync(fd)
        os.fchmod(fd, info.mode)
        return h.hexdigest()
    finally:
        os.close(fd)

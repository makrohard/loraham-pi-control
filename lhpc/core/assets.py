"""Locate packaged data assets (the tracked TOML manifest, defaults, profile
catalogue and config templates) so they resolve identically from a source checkout
and from an installed wheel.

These files live under ``lhpc/data/`` and are shipped as package data, loaded via
``importlib.resources`` — never via ``Path(__file__).parents[...]`` to an assumed
repository root (which does not exist once the package is installed).
"""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

_PACKAGE = "lhpc"
_DATA = "data"


def asset_path(name: str) -> Path:
    """Filesystem path to ``lhpc/data/<name>`` (works for the normal unzipped
    install and the source tree). For zipped installs use ``asset_text``."""
    return Path(str(resources.files(_PACKAGE) / _DATA / name))


def asset_text(name: str) -> str:
    """Text of a packaged data file (zip-safe)."""
    return (resources.files(_PACKAGE) / _DATA / name).read_text(encoding="utf-8")


_DIGEST_SKIP_DIRS = frozenset({"__pycache__"})
_DIGEST_SKIP_SUFFIXES = (".pyc", ".pyo")

# digest cache: resolved asset path -> (stat fingerprint, sha256). `is_built` is consulted on
# rendered pages (the stacks overview) and on every start, so the digest of a 12 MB web dist must
# not be a 12 MB read each time: it is recomputed only when a stat walk says the content may have
# changed. The trade-off: an in-place edit that keeps every size and mtime is not noticed until the
# process restarts — package data is never edited that way in production, and a controller update
# restarts the console.
_digest_cache: dict = {}


def _digest_members(root: Path):
    """The regular files a directory asset consists of, in sorted relative-path order."""
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _DIGEST_SKIP_DIRS for part in rel.parts) or p.suffix in _DIGEST_SKIP_SUFFIXES:
            continue
        if p.is_symlink() or not p.is_file():
            continue
        yield rel, p


def _fingerprint(root: Path) -> tuple:
    """Stats only, no reads: (size, mtime) of a file; (count, total size, newest mtime) of a tree."""
    if root.is_file():
        st = root.stat()
        return (st.st_size, st.st_mtime_ns)
    n = total = newest = 0
    for _rel, p in _digest_members(root):
        st = p.stat()
        n += 1
        total += st.st_size
        newest = max(newest, st.st_mtime_ns)
    return (n, total, newest)


def clear_digest_cache() -> None:
    _digest_cache.clear()


def asset_digest(name: str) -> str:
    """sha256 of a packaged asset's CONTENT — what a build step that consumes it actually gets.

    A file digests as its bytes. A directory (a pip-installable package shipped as package data,
    a pre-built web dist) digests as every regular file under it in sorted relative-path order,
    each as `<relative path> NUL <bytes> NUL`, so a rename, an added file and a changed byte all
    change the digest. Python bytecode caches are skipped: they are a side effect of importing
    the controller, not part of what ships, and differ per interpreter. Symlinks are skipped for
    the same reason `_asset_token` reads package data only: what they point at is not the asset.

    Raises FileNotFoundError for an asset that does not exist — the caller decides whether that
    is "not built" (a status read) or a failed build (a build)."""
    root = asset_path(name)
    if root.is_symlink() or not (root.is_file() or root.is_dir()):
        raise FileNotFoundError(f"packaged asset not found: {name!r}")
    key = str(root)
    fp = _fingerprint(root)
    hit = _digest_cache.get(key)
    if hit is not None and hit[0] == fp:
        return hit[1]
    h = hashlib.sha256()
    if root.is_file():
        h.update(root.read_bytes())
    else:
        for rel, p in _digest_members(root):
            h.update(rel.as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    digest = h.hexdigest()
    _digest_cache[key] = (fp, digest)
    return digest

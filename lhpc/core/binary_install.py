"""Binary-channel install transaction — index → verify → extract → publish → probe → receipt.

Pure core (no service facade): every step is a typed, bounded operation, and every failure
leaves the runtime root either untouched or recoverable through the crash journal. The
precedent for the publish half is meshcom-qemu's fetch-qemu.sh/lib-publish.sh (stage → back up
→ rename → restore-on-failure), the precedent for the verification half is lhpc's own
descriptor-safe runtime_fs discipline.

Ordering is the security contract:

  1. index.json is fetched bounded + parsed STRICTLY (schema 2 only);
  2. the entry's target/os must match this box, and its `components` map must equal the
     manifest pins for EVERY covered component (`built_from` is display-only, never the gate);
  3. the tarball is downloaded bounded, then sha256 AND size are verified BEFORE any
     extraction — nothing untrusted is unpacked on an unverified byte stream;
  4. members are validated while streaming (`zstd -dc | tarfile`): regular files/dirs only, no
     absolute paths, no `..`, no symlinks/hardlinks, and EVERY member must fall beneath a
     declared publish root — archive top levels are NEVER treated as ownership (replacing all
     of `src/` or `build/` would destroy unrelated stacks);
  5. only declared publish roots are backed up and replaced, under a three-state journal
     (prepared → publishing → committed) so a crash mid-rename is recoverable;
  6. the exec probe runs from the FINAL path (a staged probe proves nothing about the
     installed tree), with honest missing-library classification;
  7. the receipt is written LAST — an interrupted install reads "not installed", never
     "installed" (that is what makes "no rollback" safe).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import runtime_fs
from .paths import PathContainmentError, Paths

INDEX_SCHEMA = 2
_INDEX_MAX_BYTES = 1 << 20            # index.json is a few KiB; 1 MiB is a generous ceiling
_ARTIFACT_MAX_BYTES = 512 << 20       # meshtastic (web assets) is the big one at ~53 MB
_HTTP_TIMEOUT_S = 60.0
_PROBE_TIMEOUT_S = 60.0
JOURNAL_REL = ("state", "binary", "install.journal.json")
JOURNAL_STATES = ("prepared", "publishing", "committed")


class BinaryInstallError(Exception):
    """Typed, operator-readable failure. `.remedy` names the source-channel fallback when the
    caller should offer it (the settled decision: an explicit confirm, never a silent switch)."""

    def __init__(self, message: str, *, remedy: str = "", offer_source: bool = True):
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.offer_source = offer_source


@dataclass(frozen=True)
class IndexEntry:
    stack: str
    filename: str
    url: str
    sha256: str
    size: int
    built_from: str
    components: dict
    runtime_deps: tuple[str, ...]
    target: str
    os_name: str
    provenance: dict


# --- index ---------------------------------------------------------------------------------

def _open_stream(url: str):
    """The single streaming-download seam (HTTPS-only, redirect-guarded). Tests stub this."""
    req = urllib.request.Request(url, headers={"User-Agent": "lhpc-binary-channel"})
    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    return opener.open(req, timeout=_HTTP_TIMEOUT_S)


def _unlink_quiet(p) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect that leaves HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).startswith("https://"):
            raise urllib.error.URLError(f"refusing a non-HTTPS redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_get(url: str, max_bytes: int) -> bytes:
    if not url.startswith("https://"):
        raise BinaryInstallError(f"refusing a non-HTTPS download URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "lhpc-binary-channel"})
    try:
        # Release assets ALWAYS redirect; urllib would happily follow https -> http, and the
        # index carries the artifact hashes, so a downgraded hop defeats the whole chain.
        opener = urllib.request.build_opener(_HttpsOnlyRedirect)
        with opener.open(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BinaryInstallError(f"download failed: {exc}") from None
    if len(data) > max_bytes:
        raise BinaryInstallError(f"download exceeds the {max_bytes} byte bound: {url}")
    return data


def fetch_index(index_url: str) -> dict:
    """Bounded fetch + STRICT parse of the schema-2 index."""
    raw = _http_get(index_url, _INDEX_MAX_BYTES)
    try:
        idx = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BinaryInstallError(f"binary index is not valid JSON ({exc})") from None
    if not isinstance(idx, dict) or idx.get("schema") != INDEX_SCHEMA:
        raise BinaryInstallError(
            f"binary index schema {idx.get('schema') if isinstance(idx, dict) else '?'} is not "
            f"supported (this lhpc expects schema {INDEX_SCHEMA}) — update lhpc or use --source")
    if not isinstance(idx.get("stacks"), dict):
        raise BinaryInstallError("binary index has no stacks table")
    return idx


def index_entry(idx: dict, stack_id: str) -> IndexEntry:
    e = idx["stacks"].get(stack_id)
    if not isinstance(e, dict):
        raise BinaryInstallError(f"no binary is published for {stack_id!r}")
    try:
        entry = IndexEntry(
            stack=stack_id, filename=str(e["filename"]), url=str(e["url"]),
            sha256=str(e["sha256"]), size=int(e["size"]), built_from=str(e["built_from"]),
            components=dict(e["components"]),
            runtime_deps=tuple(str(x) for x in e.get("runtime_deps", ())),
            target=str(e["target"]), os_name=str(e.get("os", "")),
            provenance={k: e.get(k) for k in
                        ("lhpc_commit", "builder_commit", "container_digest", "smoke",
                         "built_from", "target", "os")},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BinaryInstallError(f"binary index entry for {stack_id!r} is invalid ({exc})") from None
    if len(entry.sha256) != 64 or any(c not in "0123456789abcdef" for c in entry.sha256):
        raise BinaryInstallError("binary index entry has a malformed sha256")
    if entry.size <= 0 or entry.size > _ARTIFACT_MAX_BYTES:
        raise BinaryInstallError(f"binary artifact size {entry.size} is out of bounds")
    if entry.filename != f"{stack_id}-{entry.sha256}.tar.zst":
        raise BinaryInstallError("binary index entry filename is not content-addressed")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in entry.components.items()):
        raise BinaryInstallError("binary index components map is malformed")
    smoke = entry.provenance.get("smoke")
    if smoke != {"mode": "mandatory", "result": "passed"}:
        raise BinaryInstallError(
            f"binary artifact for {stack_id!r} did not pass a mandatory smoke test — refusing")
    return entry


def check_pins(entry: IndexEntry, pins: dict) -> None:
    """THE acceptance gate: the index components map must match the manifest pins for EVERY
    covered component. `built_from` is never consulted (display only)."""
    missing = [cid for cid in pins if cid not in entry.components]
    if missing:
        raise BinaryInstallError(
            "the published binary does not record commits for " + ", ".join(sorted(missing)) +
            " — it cannot be matched against the manifest pins")
    lagging = {cid: (entry.components[cid], want)
               for cid, want in pins.items() if entry.components[cid] != want}
    if lagging:
        detail = "; ".join(f"{cid}: binary {got[:9]}, pin {want[:9]}"
                           for cid, (got, want) in sorted(lagging.items()))
        raise BinaryInstallError(
            f"the published binary was built from different commits than this lhpc pins "
            f"({detail}) — wait for a rebuilt binary or install from source")


def check_target(entry: IndexEntry, target: str) -> None:
    if entry.target != target:
        raise BinaryInstallError(
            f"the published binary targets {entry.target!r}, this box is {target!r}")


def missing_runtime_deps(entry: IndexEntry, dpkg_query) -> list:
    """Packages the artifact needs that are not installed. `dpkg_query(pkg) -> bool` is injected
    (the service passes a real dpkg-s probe; tests pass a fake). lhpc has no sudo, so a gap is a
    typed refusal that NAMES the apt command — never an attempted install."""
    return [p for p in entry.runtime_deps if not dpkg_query(p)]


# --- download + verify ----------------------------------------------------------------------

def download_artifact(entry: IndexEntry, dest_path) -> None:
    """STREAM the artifact to a staging file with an incremental hash, then verify sha256 AND
    size BEFORE the caller may extract anything. Streaming matters: this feature exists for
    small boxes, and buffering a 50 MB artifact in RAM on a 512 MB Zero is how you OOM the
    controller before the hash could ever reject it. A failed verification removes the file."""
    if not entry.url.startswith("https://"):
        raise BinaryInstallError(f"refusing a non-HTTPS download URL: {entry.url}")
    h = hashlib.sha256()
    total = 0
    try:
        with _open_stream(entry.url) as resp, open(dest_path, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > entry.size:            # bounded by the PUBLISHED size, not a ceiling
                    raise BinaryInstallError(
                        f"artifact is larger than its published size ({entry.size}) — refusing")
                h.update(chunk)
                fh.write(chunk)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _unlink_quiet(dest_path)
        raise BinaryInstallError(f"download failed: {exc}") from None
    except BinaryInstallError:
        _unlink_quiet(dest_path)
        raise
    if total != entry.size:
        _unlink_quiet(dest_path)
        raise BinaryInstallError(
            f"downloaded artifact size {total} != published size {entry.size}")
    if h.hexdigest() != entry.sha256:
        _unlink_quiet(dest_path)
        raise BinaryInstallError(
            f"artifact sha256 mismatch (got {h.hexdigest()[:12]}…, expected "
            f"{entry.sha256[:12]}…) — refusing to extract")


# --- extraction -------------------------------------------------------------------------------

def _under_roots(name: str, roots) -> bool:
    """A path that IS a publish root or lives beneath one."""
    return any(name == r or name.startswith(r + "/") for r in roots)


def _is_root_ancestor(name: str, roots) -> bool:
    """A DIRECTORY entry that is a parent of a publish root (e.g. `src` for
    `src/loraham-daemon/loraham_daemon`). Such directories must exist for the root to be
    placed at all, so the archive legitimately carries them — but ONLY as directories: a
    FILE outside the roots stays refused (that is the rule protecting other stacks)."""
    return any(r == name or r.startswith(name + "/") for r in roots)


# Expansion bounds (the sha256 covers the COMPRESSED bytes only).
_MAX_MEMBERS = 20000
_MAX_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def require_zstd() -> None:
    """The artifacts are zstd tarballs and Python 3.11-3.13 has no stdlib zstd — refuse with the
    exact apt command rather than failing halfway through an extraction."""
    if shutil.which("zstd") is None:
        raise BinaryInstallError(
            "zstd is not installed — the binary channel cannot unpack published artifacts "
            "(install it with: sudo apt install -y zstd)")


def validate_and_extract(tar_path, stage_dir, publish_roots) -> list:
    """Stream the verified tarball through member validation into `stage_dir`.

    Returns the runtime-root-relative REGULAR file paths extracted. Every member must be a
    regular file or directory, relative, free of `..`, and beneath a declared publish root."""
    roots = tuple(publish_roots)
    files, seen, total, members = [], set(), 0, 0
    proc = subprocess.Popen(["zstd", "-dc", str(tar_path)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            for m in tf:
                # The compressed artifact is sha256-verified, but its EXPANSION is not bounded
                # by that: a decompression bomb (or a duplicate member overwriting an
                # already-validated file) must be refused before it fills the SD card. Same
                # bounds the publisher enforces (audit finding).
                members += 1
                if members > _MAX_MEMBERS:
                    raise BinaryInstallError(
                        f"artifact has more than {_MAX_MEMBERS} members")
                total += max(0, m.size)
                if m.size > _MAX_MEMBER_BYTES or total > _MAX_TOTAL_BYTES:
                    raise BinaryInstallError(
                        "artifact expands beyond the accepted size limit")
                # NOT `lstrip("./")`: that strips a CHARACTER SET and would rewrite
                # "../../x" into "x", making the traversal check below dead code.
                name = m.name.removeprefix("./")
                if not name or name == ".":
                    continue
                if m.issym() or m.islnk():
                    raise BinaryInstallError(f"artifact contains a link member: {m.name!r}")
                if not (m.isreg() or m.isdir()):
                    raise BinaryInstallError(f"artifact contains a special member: {m.name!r}")
                if m.name.startswith("/") or ".." in name.split("/"):
                    raise BinaryInstallError(f"artifact member escapes the root: {m.name!r}")
                inside = _under_roots(name, roots)
                if not inside and not (m.isdir() and _is_root_ancestor(name, roots)):
                    raise BinaryInstallError(
                        f"artifact member {name!r} is outside this stack's declared publish "
                        "roots — refusing (it could replace unrelated stacks' files)")
                if name in seen:
                    raise BinaryInstallError(f"artifact contains {name!r} twice")
                seen.add(name)
                target = os.path.join(stage_dir, name)
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    raise BinaryInstallError(f"artifact member {name!r} is unreadable")
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 20)
                os.chmod(target, 0o755 if (m.mode & 0o111) else 0o644)
                files.append(name)
    finally:
        if proc.stdout:
            proc.stdout.close()
        rc = proc.wait()
    if rc != 0:
        raise BinaryInstallError("artifact decompression failed (zstd)")
    if not files:
        raise BinaryInstallError("artifact contains no files")
    return sorted(files)


# --- crash journal ------------------------------------------------------------------------

def safe_rel(p: str) -> bool:
    """A runtime-root-relative path that cannot escape (same rule the manifest parser uses)."""
    if not isinstance(p, str) or not p or p.startswith(("/", "~")):
        return False
    return all(seg not in ("", ".", "..") for seg in p.split("/"))


def journal_path(paths: Paths):
    return paths.under(*JOURNAL_REL)


def write_journal(paths: Paths, data: dict) -> bool:
    try:
        runtime_fs.mkdir(paths, "state", "binary")
        runtime_fs.write_marker(paths, journal_path(paths), json.dumps(data, indent=1), 0o644)
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def read_journal(paths: Paths):
    """(journal|None, state) with state 'absent' | 'valid' | 'unsafe'."""
    try:
        raw = runtime_fs.read_text_regular(paths, journal_path(paths), max_bytes=_INDEX_MAX_BYTES)
    except FileNotFoundError:
        return None, "absent"
    except (OSError, PathContainmentError, ValueError):
        return None, "unsafe"
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None, "unsafe"
    if (not isinstance(d, dict) or d.get("state") not in JOURNAL_STATES
            or not isinstance(d.get("stack"), str) or not isinstance(d.get("txn"), str)
            or not isinstance(d.get("roots"), list)
            or not isinstance(d.get("backups"), dict)):
        return None, "unsafe"
    # Every path in a journal is used to MOVE files — validate them like the manifest does
    # (runtime-root-relative, no escapes) instead of trusting on-disk state.
    if not all(isinstance(x, str) and safe_rel(x) for x in d["roots"]):
        return None, "unsafe"
    if not all(isinstance(k, str) and safe_rel(k) and isinstance(v, str) and safe_rel(v)
               for k, v in d["backups"].items()):
        return None, "unsafe"
    dirs = d.get("dirs", {})
    if not isinstance(dirs, dict) or not all(
            isinstance(k, str) and safe_rel(k) and isinstance(v, str) and safe_rel(v)
            for k, v in dirs.items()):
        return None, "unsafe"
    made = d.get("created_dirs", [])
    if not isinstance(made, list) or not all(isinstance(x, str) and safe_rel(x) for x in made):
        return None, "unsafe"
    return d, "valid"


def clear_journal(paths: Paths) -> bool:
    try:
        runtime_fs.unlink(paths, journal_path(paths))
        return True
    except FileNotFoundError:
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def open_txn(paths: Paths, stack_id: str, txn: str, *, old_receipt=None, auth=None) -> None:
    """Open the transaction BEFORE anything is changed — including the auth setting, which used
    to be blanked before any journal existed (a crash then left MeshCom open-auth with nothing
    to recover from). From here on every mutation is recorded and can be undone."""
    if not write_journal(paths, {"state": "prepared", "stack": stack_id, "txn": txn,
                                 "roots": [], "backups": {}, "created": [], "removed": {},
                                 "dirs": {}, "created_dirs": [],
                                 "old_receipt": old_receipt, "auth": auth or {},
                                 "at": time.time()}):
        raise BinaryInstallError("could not write the binary-install journal")


def _journal_update(paths: Paths, **fields) -> dict:
    j, state = read_journal(paths)
    if state != "valid" or j is None:
        raise BinaryInstallError("the binary-install journal disappeared mid-transaction")
    j.update(fields)
    if not write_journal(paths, j):
        raise BinaryInstallError("could not update the binary-install journal")
    return j


def backup_path(txn: str, rel: str) -> str:
    return f"state/binary/.backup-{txn}/{rel.replace('/', '__')}"


def displace(paths: Paths, txn: str, rels) -> dict:
    """Move existing files aside INTO the transaction (journaled BEFORE the move). Used for the
    previous meshtastic venv and for files only the OLD artifact owned — without this they were
    unlinked with no way back (audit finding)."""
    j, state = read_journal(paths)
    if state != "valid" or j is None:
        raise BinaryInstallError("the binary-install journal disappeared mid-transaction")
    # Any NON-DIRECTORY leaf, symlinks included: a virtualenv's `bin/python3` is a symlink, and
    # leaving those behind made `python3 -m venv` believe the environment still existed — it then
    # skipped ensurepip and the next step failed with "pip install failed" (live-found on the
    # Zero). `os.replace` moves the symlink itself; a dangling one is moved too.
    planned = {rel: backup_path(txn, rel) for rel in rels
               if os.path.lexists(paths.under(*rel.split("/")))
               and not os.path.isdir(paths.under(*rel.split("/")))}
    if not planned:
        return {}
    _journal_update(paths, removed={**j.get("removed", {}), **planned})
    for rel, bak_rel in sorted(planned.items()):
        bak = paths.under(*bak_rel.split("/"))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        os.replace(paths.under(*rel.split("/")), bak)
    return planned


def displace_dir(paths: Paths, txn: str, rel_dir: str) -> bool:
    """Move a whole DIRECTORY aside into the transaction (journaled before the move).

    Used for a provisioned virtualenv: it is half symlinks pointing outside the runtime root,
    so it can neither be owned file-by-file nor emptied leaf-by-leaf through the runtime path
    guard. One rename moves it intact — no traversal, no symlink ever followed — and an unwind
    renames it straight back, which is the only way a failed provisioning can hand the operator
    their WORKING venv again (live-found on the Zero: displacing only the regular files left
    `bin/python3` behind, `python3 -m venv` then skipped ensurepip, and pip was missing)."""
    live = paths.under(*rel_dir.split("/"))
    if not os.path.isdir(live) or os.path.islink(live):
        return False
    j, state = read_journal(paths)
    if state != "valid" or j is None:
        raise BinaryInstallError("the binary-install journal disappeared mid-transaction")
    bak_rel = backup_path(txn, rel_dir.rstrip("/") + ".dir")
    _journal_update(paths, dirs={**j.get("dirs", {}), rel_dir: bak_rel})
    bak = paths.under(*bak_rel.split("/"))
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    os.replace(live, bak)
    return True


def note_created_dir(paths: Paths, rel_dir: str) -> None:
    """Record a directory this transaction CREATES from nothing (a first-time venv), BEFORE the
    first command runs. `displace_dir` covers the replace case; without this, a hard crash
    mid-provisioning left a half-built directory that no journal, receipt or recovery knew
    about (audit finding)."""
    j, state = read_journal(paths)
    if state != "valid" or j is None:
        raise BinaryInstallError("the binary-install journal disappeared mid-transaction")
    _journal_update(paths,
                    created_dirs=sorted(set(j.get("created_dirs", [])) | {rel_dir}))


def note_created(paths: Paths, rels) -> None:
    """Record extra files this transaction created (e.g. the provisioned venv) so recovery
    removes them. They have no backup by definition."""
    j, state = read_journal(paths)
    if state != "valid" or j is None:
        raise BinaryInstallError("the binary-install journal disappeared mid-transaction")
    _journal_update(paths, created=sorted(set(j.get("created", [])) | set(rels)))


def publish(paths: Paths, stack_id: str, stage_dir, files, txn: str) -> None:
    """Promote the staged artifact FILE BY FILE inside the OPEN transaction.

    A MERGE, never a directory replacement: a publish root may live inside a git checkout, and
    replacing that directory destroys tracked files. Displaced files are journaled with their
    backups; files this run CREATES are journaled too (no backup — recovery deletes them)."""
    if not files:
        raise BinaryInstallError("artifact populated none of the declared publish roots")
    j, _st = read_journal(paths)
    backups, created = {}, list((j or {}).get("created", []))
    for rel in files:
        if os.path.isfile(paths.under(*rel.split("/"))):
            backups[rel] = backup_path(txn, rel)
        else:
            created.append(rel)
    _journal_update(paths, state="publishing", roots=list(files),
                    backups=backups, created=sorted(set(created)))
    for rel, bak_rel in sorted(backups.items()):
        bak = paths.under(*bak_rel.split("/"))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        os.replace(paths.under(*rel.split("/")), bak)
    for rel in files:
        live = paths.under(*rel.split("/"))
        os.makedirs(os.path.dirname(live), exist_ok=True)
        os.replace(os.path.join(stage_dir, rel), live)


def commit(paths: Paths) -> bool:
    """THE commit point — only after probes passed and the new receipt is durable. `committed`
    must be DURABLE before any backup is dropped: until it is, recovery must still be able to
    unwind (deleted backups + a half-written journal is unrecoverable)."""
    j, state = read_journal(paths)
    if state != "valid" or j is None:
        return state == "absent"
    if not write_journal(paths, {**j, "state": "committed"}):
        return False                       # keep the backups — the caller unwinds
    return drop_txn(paths, j)


def rollback_files(paths: Paths, j: dict) -> tuple[bool, str]:
    """FILE half of recovery (the service layer owns receipt + auth).

    ORDER IS THE CONTRACT: remove what this transaction created, then restore DIRECTORIES, then
    restore FILES. A displaced file can live INSIDE a displaced directory — the artifact's own
    binary sits in the source checkout a channel switch sets aside — so restoring files first
    would put them back only for the directory restore to wipe them (audit finding).
    """
    for rel in sorted(j.get("created", []), key=len, reverse=True):
        try:
            live = paths.under(*rel.split("/"))
            if os.path.isfile(live):
                os.unlink(live)
        except (OSError, PathContainmentError, ValueError) as exc:
            return False, f"could not remove {rel!r} while unwinding an install ({exc})"
    for rel_dir in sorted(j.get("created_dirs", []), key=len, reverse=True):
        try:
            live = paths.under(*rel_dir.split("/"))
            if os.path.isdir(live) and not os.path.islink(live):
                shutil.rmtree(live)          # created by THIS run — nothing of the operator's
        except (OSError, PathContainmentError, ValueError) as exc:
            return False, (f"could not remove the {rel_dir!r} directory this install created "
                           f"({exc})")
    for rel_dir, bak_rel in sorted(j.get("dirs", {}).items()):
        try:
            live = paths.under(*rel_dir.split("/"))
            bak = paths.under(*bak_rel.split("/"))
            if not os.path.isdir(bak):
                continue
            if os.path.isdir(live) and not os.path.islink(live):
                # whatever this run built there is ours and unfinished — the operator's working
                # directory is the one in the backup
                shutil.rmtree(live, ignore_errors=True)
            os.makedirs(os.path.dirname(live), exist_ok=True)
            os.replace(bak, live)
        except (OSError, PathContainmentError, ValueError) as exc:
            return False, (f"could not restore the {rel_dir!r} directory after an interrupted "
                           f"install ({exc})")
    for rel, bak_rel in sorted({**j.get("backups", {}), **j.get("removed", {})}.items()):
        try:
            live = paths.under(*rel.split("/"))
            bak = paths.under(*bak_rel.split("/"))
            if os.path.exists(bak):
                if os.path.isfile(live):
                    os.unlink(live)
                os.makedirs(os.path.dirname(live), exist_ok=True)
                os.replace(bak, live)
        except (OSError, PathContainmentError, ValueError) as exc:
            return False, f"could not restore {rel!r} after an interrupted install ({exc})"
    return True, ""


def drop_txn(paths: Paths, j: dict) -> bool:
    """Delete the transaction's backups and clear its journal."""
    shutil.rmtree(paths.under(*f"state/binary/.backup-{j.get('txn', '')}".split("/")),
                  ignore_errors=True)
    return clear_journal(paths)


def run_probe(paths: Paths, argv) -> str:
    """Run one post-install probe from the FINAL path. Returns the first output line; raises
    with an honest classification when the failure is a missing shared library."""
    binary = paths.under(*argv[0].split("/"))
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        raise BinaryInstallError(f"installed binary {argv[0]} is missing or not executable")
    cmd = [str(binary), *argv[1:]]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
                             check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BinaryInstallError(f"could not run {argv[0]} ({exc})") from None
    out = (res.stdout or "").strip() or (res.stderr or "").strip()
    if res.returncode != 0:
        blob = f"{res.stdout}\n{res.stderr}"
        if "error while loading shared libraries" in blob or "cannot open shared object" in blob:
            missing = blob.split("error while loading shared libraries:", 1)[-1].strip()
            raise BinaryInstallError(
                f"the downloaded binary cannot run on this system — missing library: "
                f"{missing.splitlines()[0] if missing else 'unknown'}")
        raise BinaryInstallError(
            f"the downloaded binary failed its check ({argv[0]} exited {res.returncode})")
    return (out.strip().splitlines() or [""])[0]


# --- receipt ----------------------------------------------------------------------------------

def build_receipt(paths: Paths, stack_id: str, entry: IndexEntry, files, proof_paths,
                  registry_baseline: dict, probe: str, owned_dirs=()):
    """Build the receipt for a completed transaction. Every listed file gets a hash (the
    receipt validator requires the two sets to match exactly)."""
    from . import binary_receipt as brx
    hashes = {rel: brx.sha256_file(paths, rel) for rel in files}
    return brx.BinaryReceipt(
        stack=stack_id, artifact_sha256=entry.sha256, artifact_size=entry.size,
        filename=entry.filename, url=entry.url, components=dict(entry.components),
        provenance=dict(entry.provenance), files=tuple(files), file_hashes=hashes,
        proof_paths=tuple(proof_paths), registry_baseline=dict(registry_baseline),
        probe=probe, owned_dirs=tuple(owned_dirs))

"""Binary-channel receipt — LHPC's record of a stack installed from a PREBUILT artifact.

One JSON record per stack under `state/binary/`, written LAST in the binary install
transaction (binary_install.py): while a receipt is valid, the stack's covered components are
"installed from binary" — no clone, no build, no build marker of their own in the general case.

Receipt state is FOUR-valued and deliberately cheap to compute on a status read:

  * absent      — no receipt (the stack is source-managed as before);
  * valid       — structure OK, every proof path still present, and every covered source path's
                  ownership-registry identity still matches the baseline recorded at install;
  * superseded  — a covered source path's registry TRANSACTION ID differs from the recorded
                  baseline (recorded absent -> now present, or A -> B): a source adoption has
                  taken ownership, so the binary install is history;
  * unsafe      — malformed/unreadable receipt, OR a covered path's registry record is itself
                  unsafe/was removed. NEVER equivalent to absent: an unreadable receipt must
                  block destructive work rather than silently look source-managed.

Transaction ids are OPAQUE identifiers: supersession is DIFFERENCE from the baseline, never
"newer than", and never a timestamp comparison (clocks and ordering are not evidence).

The per-file hashes recorded here are NOT re-checked on ordinary status reads (that would make
every dashboard render hash a QEMU tree). They are verified by `verify_files` only before
destructive work: binary replacement, switching to a source channel, uninstall and clean.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import runtime_fs, source_registry, validators
from .paths import Paths, PathContainmentError

RECEIPT_VERSION = 1
# 4 MiB: a receipt legitimately lists every file of a provisioned virtualenv
# (the meshtastic CLI venv alone is ~1800 paths + hashes, ~0.5 MB).
_MAX_BYTES = 4 << 20
# Largest single artifact file we will hash (meshtasticd + web assets are the big ones).
_MAX_HASH_BYTES = 512 << 20


@dataclass(frozen=True)
class BinaryReceipt:
    stack: str
    artifact_sha256: str
    artifact_size: int
    filename: str
    url: str
    components: dict                 # manifest component id -> commit (index-v2 provenance)
    provenance: dict                 # lhpc_commit/builder_commit/container_digest/target/os/…
    files: tuple[str, ...]           # runtime-root-relative paths the artifact installed
    file_hashes: dict                # path -> sha256 (regular files only)
    proof_paths: tuple[str, ...]     # subset of files whose presence proves the install
    registry_baseline: dict          # covered source path -> txn id ("" = no record at install)
    probe: str                       # first line of the post-install exec probe output
    # Whole directories lhpc CREATED for this install and therefore owns outright — today only
    # a provisioned virtualenv. A venv cannot be owned file-by-file: half of it is symlinks
    # pointing OUTSIDE the runtime root (`bin/python3` -> /usr/bin/python3), which the runtime
    # path guard refuses to touch (correctly). The directory is the honest unit of ownership.
    owned_dirs: tuple[str, ...] = ()
    installed_at: float = field(default_factory=time.time)
    version: int = RECEIPT_VERSION


def receipt_dir(paths: Paths) -> Path:
    return paths.under("state", "binary")


def receipt_path(paths: Paths, stack_id: str) -> Path:
    stem = validators.path_component(stack_id, field="stack")
    return receipt_dir(paths) / f"{stem}.json"


def _safe_rel(p: str) -> bool:
    """Runtime-root-relative, non-escaping (mirrors the manifest's own path rule)."""
    if not isinstance(p, str) or not p or p.startswith("/") or p.startswith("~"):
        return False
    return all(seg not in ("", ".", "..") for seg in p.split("/"))


def _valid(d: object, stack_id: str) -> bool:
    if not isinstance(d, dict) or d.get("version") != RECEIPT_VERSION:
        return False
    if d.get("stack") != stack_id:
        return False
    for f in ("artifact_sha256", "filename", "url", "probe"):
        if not isinstance(d.get(f), str):
            return False
    if len(d["artifact_sha256"]) != 64 or not all(c in "0123456789abcdef"
                                                  for c in d["artifact_sha256"]):
        return False
    size = d.get("artifact_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return False
    for f in ("components", "provenance", "file_hashes", "registry_baseline"):
        if not isinstance(d.get(f), dict):
            return False
    od = d.get("owned_dirs", [])
    if not isinstance(od, list) or not all(isinstance(x, str) and _safe_rel(x) for x in od):
        return False
    if len(set(od)) != len(od):
        return False
    for f in ("files", "proof_paths"):
        v = d.get(f)
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            return False
        # Every listed path is DELETED at retirement — validate containment here so a
        # hand-edited receipt reads UNSAFE instead of reaching the filesystem.
        if not all(_safe_rel(x) for x in v):
            return False
    if not d["files"] or not d["proof_paths"]:
        return False
    if not set(d["proof_paths"]) <= set(d["files"]):
        return False
    # Retirement deletes every entry in `files`, and verify_files only checks hashed ones —
    # so the two sets must match EXACTLY, with no duplicates and well-formed digests.
    if len(set(d["files"])) != len(d["files"]) or len(set(d["proof_paths"])) != len(d["proof_paths"]):
        return False
    if set(d["file_hashes"]) != set(d["files"]):
        return False
    if not all(isinstance(v, str) and len(v) == 64
               and all(c in "0123456789abcdef" for c in v)
               for v in d["file_hashes"].values()):
        return False
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in d["components"].items()):
        return False
    if not all(_safe_rel(k) and isinstance(v, str) for k, v in d["file_hashes"].items()):
        return False
    if not all(isinstance(k, str) and isinstance(v, str)
               for k, v in d["registry_baseline"].items()):
        return False
    at = d.get("installed_at")
    return isinstance(at, (int, float)) and not isinstance(at, bool)


def _from_dict(d: dict) -> BinaryReceipt:
    return BinaryReceipt(
        stack=d["stack"], artifact_sha256=d["artifact_sha256"],
        artifact_size=int(d["artifact_size"]), filename=d["filename"], url=d["url"],
        components=dict(d["components"]), provenance=dict(d.get("provenance", {})),
        files=tuple(d["files"]), file_hashes=dict(d["file_hashes"]),
        proof_paths=tuple(d["proof_paths"]),
        registry_baseline=dict(d["registry_baseline"]), probe=d["probe"],
        owned_dirs=tuple(d.get("owned_dirs", [])),
        installed_at=float(d["installed_at"]), version=int(d["version"]))


def write_receipt(paths: Paths, rec: BinaryReceipt) -> bool:
    """Atomically persist the receipt (written LAST in the install transaction). False on any
    failure so the caller fails closed rather than reporting an unrecorded binary install."""
    payload = {
        "version": RECEIPT_VERSION, "stack": rec.stack,
        "artifact_sha256": rec.artifact_sha256, "artifact_size": rec.artifact_size,
        "filename": rec.filename, "url": rec.url, "components": dict(rec.components),
        "provenance": dict(rec.provenance), "files": list(rec.files),
        "file_hashes": dict(rec.file_hashes), "proof_paths": list(rec.proof_paths),
        "registry_baseline": dict(rec.registry_baseline), "probe": rec.probe,
        "owned_dirs": list(rec.owned_dirs), "installed_at": rec.installed_at,
    }
    try:
        runtime_fs.mkdir(paths, "state", "binary")
        runtime_fs.write_marker(paths, receipt_path(paths, rec.stack),
                                json.dumps(payload, indent=1), 0o644)
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def remove_receipt(paths: Paths, stack_id: str) -> bool:
    """Delete the receipt (retirement after switching/uninstall/clean). True when it is gone."""
    try:
        runtime_fs.unlink(paths, receipt_path(paths, stack_id))
        return True
    except FileNotFoundError:
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def read_raw(paths: Paths, stack_id: str):
    """The receipt file's RAW text (or None) — journaled before a replacement so an unwind can
    restore the previous receipt byte-for-byte."""
    try:
        return runtime_fs.read_text_regular(paths, receipt_path(paths, stack_id),
                                            max_bytes=_MAX_BYTES)
    except (FileNotFoundError, OSError, PathContainmentError, ValueError):
        return None


def write_raw(paths: Paths, stack_id: str, raw: str) -> bool:
    """Restore a previously journaled receipt verbatim."""
    try:
        runtime_fs.mkdir(paths, "state", "binary")
        runtime_fs.write_marker(paths, receipt_path(paths, stack_id), raw, 0o644)
        return True
    except (OSError, PathContainmentError, ValueError):
        return False


def _read(paths: Paths, stack_id: str):
    """(state, receipt|None, reason) for the FILE alone — no world checks."""
    rp = receipt_path(paths, stack_id)
    try:
        raw = runtime_fs.read_text_regular(paths, rp, max_bytes=_MAX_BYTES)
    except FileNotFoundError:
        return "absent", None, ""
    except (OSError, PathContainmentError, ValueError) as exc:
        return "unsafe", None, (f"binary receipt for {stack_id!r} is present but "
                                f"unreadable/unsafe ({exc}) — resolve it manually")
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return "unsafe", None, (f"binary receipt for {stack_id!r} is malformed — "
                                "resolve it manually")
    if not _valid(d, stack_id):
        return "unsafe", None, (f"binary receipt for {stack_id!r} fails strict validation — "
                                "resolve it manually")
    return "valid", _from_dict(d), ""


def receipt_state(paths: Paths, stack_id: str) -> tuple:
    """THE cheap four-state read: (state, receipt|None, reason).

    Cost is bounded: one small JSON read, one lstat per proof path, and one registry read per
    covered source path. No hashing (see `verify_files`)."""
    state, rec, reason = _read(paths, stack_id)
    if state != "valid":
        return state, rec, reason

    # 1. Proof paths must still exist (an operator `rm -rf` of the artifact must not keep
    #    reading "installed" — the same discipline the strict build marker enforces).
    for rel in rec.proof_paths:
        try:
            st = runtime_fs.stat_leaf_nofollow(paths, paths.under(*rel.split("/")))
        except (OSError, PathContainmentError, ValueError):
            st = None
        if st is None:
            # Drift/corruption — NOT supersession (which means a source adoption took
            # ownership). Unsafe blocks destructive work until the operator resolves it.
            return "unsafe", rec, (f"binary artifact file {rel!r} is gone — the install is "
                                   "incomplete; reinstall it or switch to the source channel")

    # 2. Ownership supersession: opaque txn-id DIFFERENCE from the recorded baseline.
    for source_rel, baseline in sorted(rec.registry_baseline.items()):
        rstate, rrec, rreason = source_registry.record_state(paths, source_rel)
        if rstate == "unsafe":
            return "unsafe", rec, (f"ownership record for {source_rel!r} is unsafe "
                                   f"({rreason}) — cannot judge the binary install")
        if rstate == "absent":
            if baseline:
                # Recorded a registry txn at install, now the record is GONE: conservative —
                # this is not proof of a source adoption, and not proof of anything else.
                return "unsafe", rec, (f"ownership record for {source_rel!r} disappeared since "
                                       "the binary install — resolve it manually")
            continue
        current = rrec.txn_id if rrec else ""
        if current != baseline:
            return "superseded", rec, (f"source {source_rel!r} was adopted after the binary "
                                       "install — the source channel now owns this stack")
    return "valid", rec, ""


def sha256_file(paths: Paths, rel: str) -> str:
    """Descriptor-safe hash of one receipt-listed regular file ("" when unreadable)."""
    try:
        # Bounded, containment-checked read; a file larger than the bound (or any
        # unreadable/irregular leaf) yields "" and therefore a mismatch — fail closed.
        data = runtime_fs.read_bytes(paths, paths.under(*rel.split("/")),
                                     max_bytes=_MAX_HASH_BYTES)
    except (OSError, PathContainmentError, ValueError):
        return ""
    return hashlib.sha256(data).hexdigest()


def verify_files(paths: Paths, rec: BinaryReceipt) -> tuple[bool, list]:
    """STRICT pre-destructive check: every recorded regular file must still hash as recorded.
    Returns (ok, mismatches). Deliberately NOT part of `receipt_state` — hashing a QEMU tree
    on every dashboard render is exactly the cost the four-state read avoids."""
    bad = []
    for rel, want in sorted(rec.file_hashes.items()):
        got = sha256_file(paths, rel)
        if got != want:
            bad.append({"path": rel, "expected": want, "actual": got})
    return (not bad), bad

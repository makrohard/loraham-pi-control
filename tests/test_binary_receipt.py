"""Binary-channel receipt: the four-state contract (B2).

absent | valid | superseded | unsafe — with supersession driven by OPAQUE registry
transaction-id DIFFERENCE (never "newer than", never timestamps), a malformed receipt reading
UNSAFE (never absent), and file hashing confined to the pre-destructive `verify_files` path.
"""

import json

import pytest

from lhpc.core import binary_receipt as brx
from lhpc.core import runtime_fs, source_registry
from lhpc.core.paths import Paths


def _paths(tmp_path):
    return Paths(runtime_root=tmp_path)


def _install_file(tmp_path, rel, data=b"artifact"):
    p = tmp_path
    for seg in rel.split("/")[:-1]:
        p = p / seg
    p.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(data)
    return tmp_path / rel


def _receipt(tmp_path, **over):
    rel = "src/demo/bin/demo"
    _install_file(tmp_path, rel)
    import hashlib
    base = dict(
        stack="demo", artifact_sha256="a" * 64, artifact_size=123,
        filename="demo-" + "a" * 64 + ".tar.zst", url="https://example.invalid/x.tar.zst",
        components={"demo-main": "b" * 40}, provenance={"lhpc_commit": "c" * 40},
        files=(rel,), file_hashes={rel: hashlib.sha256(b"artifact").hexdigest()},
        proof_paths=(rel,), registry_baseline={"src/demo": ""}, probe="demo 1.0",
    )
    base.update(over)
    return brx.BinaryReceipt(**base)


def _write_registry(tmp_path, source_rel, txn_id):
    rec = source_registry.RegistryRecord(
        source_rel=source_rel, remote="https://example.invalid/demo.git", selector="pinned",
        resolved_commit="d" * 40, adopted_at=1000.0, txn_id=txn_id, strategy="adopt",
        components=("demo-main",))
    assert source_registry.write_record(_paths(tmp_path), rec)


# --- absent / valid -----------------------------------------------------------------------------

def test_absent_when_no_receipt(tmp_path):
    assert brx.receipt_state(_paths(tmp_path), "demo") == ("absent", None, "")


def test_valid_round_trip(tmp_path):
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    state, got, reason = brx.receipt_state(paths, "demo")
    assert state == "valid" and reason == ""
    assert got.components == rec.components and got.files == rec.files
    assert got.artifact_sha256 == rec.artifact_sha256


def test_valid_with_matching_registry_baseline(tmp_path):
    # A source record that ALREADY existed at install time is recorded as the baseline and
    # must keep reading valid while it is unchanged.
    paths = _paths(tmp_path)
    _write_registry(tmp_path, "src/demo", "txn-1")
    assert brx.write_receipt(paths, _receipt(tmp_path, registry_baseline={"src/demo": "txn-1"}))
    assert brx.receipt_state(paths, "demo")[0] == "valid"


# --- superseded ---------------------------------------------------------------------------------

def test_superseded_when_registry_record_appears(tmp_path):
    # baseline "" (no record at install) -> a source adoption wrote one: superseded.
    paths = _paths(tmp_path)
    assert brx.write_receipt(paths, _receipt(tmp_path))
    _write_registry(tmp_path, "src/demo", "txn-new")
    state, _rec, reason = brx.receipt_state(paths, "demo")
    assert state == "superseded" and "source channel now owns" in reason


def test_superseded_when_txn_id_differs(tmp_path):
    paths = _paths(tmp_path)
    _write_registry(tmp_path, "src/demo", "txn-1")
    assert brx.write_receipt(paths, _receipt(tmp_path, registry_baseline={"src/demo": "txn-1"}))
    _write_registry(tmp_path, "src/demo", "txn-2")          # re-adopted
    assert brx.receipt_state(paths, "demo")[0] == "superseded"


def test_supersession_is_difference_not_ordering(tmp_path):
    # Txn ids are OPAQUE: a lexically SMALLER id is still a different adoption.
    paths = _paths(tmp_path)
    _write_registry(tmp_path, "src/demo", "zzz")
    assert brx.write_receipt(paths, _receipt(tmp_path, registry_baseline={"src/demo": "zzz"}))
    _write_registry(tmp_path, "src/demo", "aaa")
    assert brx.receipt_state(paths, "demo")[0] == "superseded"


def test_missing_proof_path_is_unsafe_not_superseded(tmp_path):
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    (tmp_path / rec.proof_paths[0]).unlink()
    state, _r, reason = brx.receipt_state(paths, "demo")
    # a missing artifact file is DRIFT, not source supersession (audit correction)
    assert state == "unsafe" and "is gone" in reason


# --- unsafe -------------------------------------------------------------------------------------

def test_malformed_receipt_is_unsafe_never_absent(tmp_path):
    paths = _paths(tmp_path)
    runtime_fs.mkdir(paths, "state", "binary")
    brx.receipt_path(paths, "demo").write_text("{not json")
    state, rec, reason = brx.receipt_state(paths, "demo")
    assert state == "unsafe" and rec is None and "malformed" in reason


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(version=99),
    lambda d: d.update(stack="other"),
    lambda d: d.update(artifact_sha256="short"),
    lambda d: d.update(artifact_size=0),
    lambda d: d.update(files=[]),
    lambda d: d.update(proof_paths=["not/in/files"]),
    lambda d: d.update(components={"x": 7}),
    lambda d: d.update(registry_baseline={"src/demo": 5}),
    lambda d: d.pop("installed_at"),
])
def test_structurally_invalid_receipt_is_unsafe(tmp_path, mutate):
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    d = json.loads(brx.receipt_path(paths, "demo").read_text())
    mutate(d)
    brx.receipt_path(paths, "demo").write_text(json.dumps(d))
    assert brx.receipt_state(paths, "demo")[0] == "unsafe"


def test_unsafe_registry_record_blocks_judgement(tmp_path):
    paths = _paths(tmp_path)
    _write_registry(tmp_path, "src/demo", "txn-1")
    assert brx.write_receipt(paths, _receipt(tmp_path, registry_baseline={"src/demo": "txn-1"}))
    # corrupt the ownership record: we can no longer judge the binary install either way
    source_registry.record_path(paths, "src/demo").write_text("{broken")
    state, _r, reason = brx.receipt_state(paths, "demo")
    assert state == "unsafe" and "cannot judge" in reason


def test_disappeared_registry_record_is_conservative(tmp_path):
    # Recorded baseline txn -> record REMOVED: not proof of adoption, not proof of anything.
    paths = _paths(tmp_path)
    _write_registry(tmp_path, "src/demo", "txn-1")
    assert brx.write_receipt(paths, _receipt(tmp_path, registry_baseline={"src/demo": "txn-1"}))
    source_registry.record_path(paths, "src/demo").unlink()
    state, _r, reason = brx.receipt_state(paths, "demo")
    assert state == "unsafe" and "disappeared" in reason


# --- file verification (pre-destructive only) ----------------------------------------------------

def test_verify_files_detects_modification(tmp_path):
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    ok, bad = brx.verify_files(paths, rec)
    assert ok and bad == []
    (tmp_path / rec.files[0]).write_bytes(b"tampered")
    ok, bad = brx.verify_files(paths, rec)
    assert not ok and bad[0]["path"] == rec.files[0]


def test_verify_files_missing_file_is_mismatch(tmp_path):
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    (tmp_path / rec.files[0]).unlink()
    ok, bad = brx.verify_files(paths, rec)
    assert not ok and bad[0]["actual"] == ""


def test_status_read_does_not_hash(tmp_path, monkeypatch):
    # The cheap read must never hash owned files (a QEMU tree on every dashboard render).
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    monkeypatch.setattr(brx, "sha256_file",
                        lambda *a, **k: pytest.fail("receipt_state must not hash files"))
    assert brx.receipt_state(paths, "demo")[0] == "valid"


# --- removal ------------------------------------------------------------------------------------

def test_remove_receipt_is_idempotent(tmp_path):
    paths = _paths(tmp_path)
    assert brx.write_receipt(paths, _receipt(tmp_path))
    assert brx.remove_receipt(paths, "demo") is True
    assert brx.receipt_state(paths, "demo")[0] == "absent"
    assert brx.remove_receipt(paths, "demo") is True        # already gone


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../etc/passwd", "a/../../b", "~/x"])
def test_receipt_with_escaping_paths_is_unsafe(tmp_path, bad):
    # Every listed path is DELETED at retirement — a hand-edited receipt must read UNSAFE,
    # never reach the filesystem (audit finding).
    paths = _paths(tmp_path)
    rec = _receipt(tmp_path)
    assert brx.write_receipt(paths, rec)
    d = json.loads(brx.receipt_path(paths, "demo").read_text())
    d["files"] = [bad]
    d["proof_paths"] = [bad]
    brx.receipt_path(paths, "demo").write_text(json.dumps(d))
    assert brx.receipt_state(paths, "demo")[0] == "unsafe"


@pytest.mark.parametrize("mutate", [
    lambda d: d["file_hashes"].pop(d["files"][0]),          # a file with no hash
    lambda d: d["file_hashes"].update({"src/demo/extra": "a" * 64}),   # hash without a file
    lambda d: d["file_hashes"].update({d["files"][0]: "NOTHEX" + "a" * 58}),
    lambda d: d["files"].extend(d["files"]),                 # duplicate entry
])
def test_receipt_hash_set_must_match_file_set(tmp_path, mutate):
    """Retirement deletes every `files` entry while verify_files only checks hashed ones — an
    unhashed file could authorize an unverified deletion (audit finding)."""
    paths = _paths(tmp_path)
    assert brx.write_receipt(paths, _receipt(tmp_path))
    d = json.loads(brx.receipt_path(paths, "demo").read_text())
    mutate(d)
    brx.receipt_path(paths, "demo").write_text(json.dumps(d))
    assert brx.receipt_state(paths, "demo")[0] == "unsafe"

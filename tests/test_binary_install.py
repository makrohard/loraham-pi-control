"""Binary install transaction (B4): index → verify → extract → publish → probe → receipt.

Everything runs against a LOCAL fake release (no network): `_http_get` is stubbed with a
byte-serving fake, and tarballs are built on the fly — including the hostile ones (symlink
escape, `..`, member outside the declared publish roots, oversize, sha/size mismatch).
"""

import hashlib
import json
import os
import subprocess
import tarfile

import pytest

from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths


def _paths(tmp_path):
    return Paths(runtime_root=tmp_path)


def _make_tar(tmp_path, members, name="art.tar.zst"):
    """members: {relpath: bytes|('dir',)|('sym', target)}"""
    stage = tmp_path / "mk"
    stage.mkdir(exist_ok=True)
    tar_plain = tmp_path / "plain.tar"
    with tarfile.open(tar_plain, "w") as tf:
        for rel, val in members.items():
            if isinstance(val, tuple) and val[0] == "dir":
                info = tarfile.TarInfo(rel)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
            elif isinstance(val, tuple) and val[0] == "sym":
                info = tarfile.TarInfo(rel)
                info.type = tarfile.SYMTYPE
                info.linkname = val[1]
                tf.addfile(info)
            else:
                p = stage / "f"
                p.write_bytes(val)
                info = tf.gettarinfo(str(p), arcname=rel)
                info.mode = 0o755
                with open(p, "rb") as fh:
                    tf.addfile(info, fh)
    out = tmp_path / name
    subprocess.run(["zstd", "-q", "-f", str(tar_plain), "-o", str(out)], check=True)
    return out


def _entry(tar_path, stack="demo", **over):
    data = tar_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    d = dict(stack=stack, filename=f"{stack}-{sha}.tar.zst",
             url=f"https://example.invalid/{stack}-{sha}.tar.zst", sha256=sha,
             size=len(data), built_from="b" * 40, components={"demo-main": "c" * 40},
             runtime_deps=("libc6",), target="aarch64-trixie", os_name="trixie",
             provenance={"smoke": {"mode": "mandatory", "result": "passed"}})
    d.update(over)
    return bi.IndexEntry(**d)


def _index(entry):
    return {"schema": 2, "stacks": {entry.stack: {
        "filename": entry.filename, "url": entry.url, "sha256": entry.sha256,
        "size": entry.size, "built_from": entry.built_from, "components": entry.components,
        "runtime_deps": list(entry.runtime_deps), "target": entry.target, "os": entry.os_name,
        "smoke": {"mode": "mandatory", "result": "passed"},
        "lhpc_commit": "d" * 40, "builder_commit": "e" * 40,
        "container_digest": "debian@sha256:" + "f" * 64, "extract_to": "runtime-root"}}}


def _serve_stream(monkeypatch, blobs):
    """Stub the streaming seam used by download_artifact."""
    import contextlib
    import io

    def fake_stream(url):
        if url not in blobs:
            raise OSError(f"no route to {url}")
        return contextlib.closing(io.BytesIO(blobs[url]))
    monkeypatch.setattr(bi, "_open_stream", fake_stream)


def _serve(monkeypatch, blobs):
    def fake_get(url, max_bytes):
        if url not in blobs:
            raise bi.BinaryInstallError(f"download failed: no route to {url}")
        data = blobs[url]
        if len(data) > max_bytes:
            raise bi.BinaryInstallError(f"download exceeds the {max_bytes} byte bound: {url}")
        return data
    monkeypatch.setattr(bi, "_http_get", fake_get)


# --- index parsing ------------------------------------------------------------------------------

def test_index_and_entry_round_trip(tmp_path, monkeypatch):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY"})
    e = _entry(tar)
    _serve(monkeypatch, {"https://idx/index.json": json.dumps(_index(e)).encode()})
    idx = bi.fetch_index("https://idx/index.json")
    got = bi.index_entry(idx, "demo")
    assert got.sha256 == e.sha256 and got.target == "aarch64-trixie"
    assert got.components == {"demo-main": "c" * 40}


@pytest.mark.parametrize("payload,msg", [
    (b"{not json", "not valid JSON"),
    (json.dumps({"schema": 1, "stacks": {}}).encode(), "schema 1 is not supported"),
    (json.dumps({"schema": 3, "stacks": {}}).encode(), "not supported"),
    (json.dumps({"schema": 2}).encode(), "no stacks table"),
])
def test_index_rejections(monkeypatch, payload, msg):
    _serve(monkeypatch, {"https://idx/index.json": payload})
    with pytest.raises(bi.BinaryInstallError, match=msg):
        bi.fetch_index("https://idx/index.json")


def test_unknown_stack_in_index(tmp_path, monkeypatch):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"X"})
    idx = _index(_entry(tar))
    with pytest.raises(bi.BinaryInstallError, match="no binary is published"):
        bi.index_entry(idx, "other")


def test_entry_requires_content_addressed_filename(tmp_path):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"X"})
    idx = _index(_entry(tar))
    idx["stacks"]["demo"]["filename"] = "demo.tar.zst"
    with pytest.raises(bi.BinaryInstallError, match="content-addressed"):
        bi.index_entry(idx, "demo")


def test_entry_requires_mandatory_passed_smoke(tmp_path):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"X"})
    idx = _index(_entry(tar))
    idx["stacks"]["demo"]["smoke"] = {"mode": "skipped", "result": "skipped"}
    with pytest.raises(bi.BinaryInstallError, match="mandatory smoke test"):
        bi.index_entry(idx, "demo")


def test_non_https_url_refused(monkeypatch):
    # The REAL implementation must refuse plain http BEFORE opening any socket. Drop the
    # hermetic conftest stub for this one assertion (undo() restores the real attribute).
    monkeypatch.undo()
    with pytest.raises(bi.BinaryInstallError, match="non-HTTPS"):
        bi._http_get("http://example.invalid/index.json", 1024)


# --- pins / target / deps -----------------------------------------------------------------------

def test_check_pins_accepts_exact_match(tmp_path):
    e = _entry(_make_tar(tmp_path, {"src/demo/bin/demo": b"X"}))
    bi.check_pins(e, {"demo-main": "c" * 40})               # no raise


def test_check_pins_rejects_lagging_component(tmp_path):
    e = _entry(_make_tar(tmp_path, {"src/demo/bin/demo": b"X"}))
    with pytest.raises(bi.BinaryInstallError, match="different commits"):
        bi.check_pins(e, {"demo-main": "9" * 40})


def test_check_pins_rejects_missing_component(tmp_path):
    e = _entry(_make_tar(tmp_path, {"src/demo/bin/demo": b"X"}))
    with pytest.raises(bi.BinaryInstallError, match="does not record commits"):
        bi.check_pins(e, {"demo-main": "c" * 40, "demo-lib": "a" * 40})


def test_check_target_mismatch(tmp_path):
    e = _entry(_make_tar(tmp_path, {"src/demo/bin/demo": b"X"}))
    with pytest.raises(bi.BinaryInstallError, match="targets 'aarch64-trixie'"):
        bi.check_target(e, "aarch64-bookworm")


def test_missing_runtime_deps_listed(tmp_path):
    e = _entry(_make_tar(tmp_path, {"src/demo/bin/demo": b"X"}),
               runtime_deps=("libc6", "liblgpio1"))
    assert bi.missing_runtime_deps(e, lambda p: p == "libc6") == ["liblgpio1"]
    assert bi.missing_runtime_deps(e, lambda p: True) == []


# --- download verification ----------------------------------------------------------------------

def test_download_verifies_sha_and_size(tmp_path, monkeypatch):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY"})
    e = _entry(tar)
    _serve_stream(monkeypatch, {e.url: tar.read_bytes()})
    dest = tmp_path / "dl.tar.zst"
    bi.download_artifact(e, dest)
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == e.sha256


def test_download_sha_mismatch_refuses(tmp_path, monkeypatch):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY"})
    e = _entry(tar, sha256="0" * 64)
    _serve_stream(monkeypatch, {e.url: tar.read_bytes()})
    with pytest.raises(bi.BinaryInstallError, match="sha256 mismatch"):
        bi.download_artifact(e, tmp_path / "dl.tar.zst")
    assert not (tmp_path / "dl.tar.zst").exists()           # nothing written on mismatch


def test_download_size_mismatch_refuses(tmp_path, monkeypatch):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY"})
    e = _entry(tar, size=7)
    _serve_stream(monkeypatch, {e.url: tar.read_bytes()})
    with pytest.raises(bi.BinaryInstallError, match="size"):
        bi.download_artifact(e, tmp_path / "dl.tar.zst")


# --- extraction: member validation ---------------------------------------------------------------

ROOTS = ("src/demo/bin", "build/tools/demo")


def test_extract_valid_members(tmp_path):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY",
                               "build/tools/demo/asset": b"A"})
    stage = tmp_path / "stage"; stage.mkdir()
    files = bi.validate_and_extract(tar, stage, ROOTS)
    assert files == ["build/tools/demo/asset", "src/demo/bin/demo"]
    assert (stage / "src/demo/bin/demo").read_bytes() == b"BINARY"
    assert os.access(stage / "src/demo/bin/demo", os.X_OK)


def test_extract_rejects_symlink_escape(tmp_path):
    tar = _make_tar(tmp_path, {"src/demo/bin/evil": ("sym", "/etc/passwd")})
    stage = tmp_path / "stage"; stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match="link member"):
        bi.validate_and_extract(tar, stage, ROOTS)


def test_extract_rejects_parent_traversal(tmp_path):
    tar = _make_tar(tmp_path, {"src/demo/bin/../../../etc/x": b"X"})
    stage = tmp_path / "stage"; stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match="escapes the root|outside this stack"):
        bi.validate_and_extract(tar, stage, ROOTS)


def test_extract_rejects_member_outside_publish_roots(tmp_path):
    # THE plan's key rule: an artifact may never touch paths the manifest did not declare.
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"OK",
                               "src/other-stack/thing": b"EVIL"})
    stage = tmp_path / "stage"; stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match="outside this stack's declared publish"):
        bi.validate_and_extract(tar, stage, ROOTS)


def test_extract_rejects_a_duplicate_member(tmp_path):
    """A second member with the same name would overwrite an already-validated file after it
    was checked — refuse the archive instead (audit finding)."""
    import tarfile as _t
    plain = tmp_path / "dup.tar"
    with _t.open(plain, "w") as tf:
        for data in (b"first", b"second"):
            info = _t.TarInfo("src/demo/bin/demo")
            info.size = len(data)
            tf.addfile(info, __import__("io").BytesIO(data))
    out = tmp_path / "dup.tar.zst"
    subprocess.run(["zstd", "-q", "-f", str(plain), "-o", str(out)], check=True)
    with pytest.raises(bi.BinaryInstallError, match="twice"):
        bi.validate_and_extract(out, str(tmp_path / "st"), ["src/demo/bin"])


def test_extract_refuses_an_oversized_expansion(tmp_path, monkeypatch):
    """The sha256 covers the COMPRESSED bytes; the expansion must be bounded separately or a
    zstd bomb fills the SD card before anything notices (audit finding)."""
    monkeypatch.setattr(bi, "_MAX_TOTAL_BYTES", 8)
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"0123456789"})
    with pytest.raises(bi.BinaryInstallError, match="size limit"):
        bi.validate_and_extract(tar, str(tmp_path / "st2"), ["src/demo/bin"])


def test_extract_refuses_too_many_members(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "_MAX_MEMBERS", 2)
    tar = _make_tar(tmp_path, {f"src/demo/bin/f{i}": b"x" for i in range(5)})
    with pytest.raises(bi.BinaryInstallError, match="members"):
        bi.validate_and_extract(tar, str(tmp_path / "st3"), ["src/demo/bin"])


def test_extract_rejects_empty_archive(tmp_path):
    tar = _make_tar(tmp_path, {})
    stage = tmp_path / "stage"; stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match="no files"):
        bi.validate_and_extract(tar, stage, ROOTS)


# --- publish + crash journal -----------------------------------------------------------------

def _staged(tmp_path, rel="src/demo/bin/demo", data=b"NEW"):
    stage = tmp_path / "stage"
    (stage / os.path.dirname(rel)).mkdir(parents=True, exist_ok=True)
    (stage / rel).write_bytes(data)
    return stage


def _open(paths, txn="txn1", **kw):
    """Open the transaction the way the service does — every mutation below is journaled."""
    bi.open_txn(paths, "demo", txn, **kw)


def _unwind(paths):
    """The file half of recovery (the service layer owns receipt + auth)."""
    j, state = bi.read_journal(paths)
    assert state == "valid"
    ok, why = bi.rollback_files(paths, j)
    if ok:
        bi.drop_txn(paths, j)
    return ok, why


def test_publish_replaces_only_declared_roots(tmp_path):
    paths = _paths(tmp_path)
    # a pre-existing unrelated tree that must survive untouched
    (tmp_path / "src" / "other").mkdir(parents=True)
    (tmp_path / "src" / "other" / "keep").write_bytes(b"KEEP")
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "bin" / "demo").write_bytes(b"OLD")
    stage = _staged(tmp_path)
    _open(paths)
    bi.publish(paths, "demo", stage, ["src/demo/bin/demo"], "txn1")
    assert (tmp_path / "src/demo/bin/demo").read_bytes() == b"NEW"
    assert (tmp_path / "src/other/keep").read_bytes() == b"KEEP"
    # the transaction stays OPEN until commit() — that is what lets a failed probe restore
    # the PREVIOUS install instead of destroying it (audit finding)
    assert bi.read_journal(paths)[1] == "valid"
    assert bi.commit(paths)
    assert bi.read_journal(paths)[1] == "absent"


def test_publish_without_an_open_transaction_refuses(tmp_path):
    """Nothing may be promoted outside a journaled transaction — a crash would then leave
    published files with no record of what they replaced (audit finding)."""
    paths = _paths(tmp_path)
    with pytest.raises(bi.BinaryInstallError, match="journal"):
        bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnX")


def test_open_txn_records_auth_and_receipt_before_any_mutation(tmp_path):
    # The MeshCom password is switched off before the download even starts, so the journal
    # must already carry the previous value when that happens.
    paths = _paths(tmp_path)
    _open(paths, txn="txnA", old_receipt='{"stack": "demo"}',
          auth={"param": "password_file", "previous": "config/secrets/xr_pw"})
    j, state = bi.read_journal(paths)
    assert state == "valid" and j["state"] == "prepared"
    assert j["auth"]["previous"] == "config/secrets/xr_pw"
    assert j["old_receipt"] == '{"stack": "demo"}'


def test_backups_survive_until_commit_then_go(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "bin" / "demo").write_bytes(b"OLD")
    _open(paths, txn="txn2")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txn2")
    assert list((tmp_path / "state" / "binary").glob(".backup-*"))   # kept while OPEN
    assert bi.commit(paths)
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []


def test_rollback_restores_the_previous_binary(tmp_path):
    """THE destructive-update regression: a probe/receipt failure after promotion must put the
    previous artifact back (its backup must still exist at that point)."""
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "bin" / "demo").write_bytes(b"BINARY-A")
    _open(paths, txn="txnR")
    bi.publish(paths, "demo", _staged(tmp_path, data=b"BINARY-B"), ["src/demo/bin/demo"], "txnR")
    assert (tmp_path / "src/demo/bin/demo").read_bytes() == b"BINARY-B"
    ok, why = _unwind(paths)                           # e.g. B's probe failed
    assert ok and why == ""
    assert (tmp_path / "src/demo/bin/demo").read_bytes() == b"BINARY-A"
    assert bi.read_journal(paths)[1] == "absent"


def test_rollback_removes_a_file_the_run_created(tmp_path):
    # A file that did NOT exist before has no backup — recovery must DELETE it, or an
    # unreceipted partial install survives (audit finding).
    paths = _paths(tmp_path)
    _open(paths, txn="txnC")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnC")
    assert (tmp_path / "src/demo/bin/demo").exists()
    ok, _why = _unwind(paths)
    assert ok and not (tmp_path / "src/demo/bin/demo").exists()


def test_displaced_old_only_file_comes_back_on_rollback(tmp_path):
    """A file the PREVIOUS artifact owned and the new one no longer ships is displaced INTO
    the transaction, never unlinked — an unwind must restore the old install completely."""
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "bin" / "gone").write_bytes(b"OLD-ONLY")
    _open(paths, txn="txnD")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnD")
    bi.displace(paths, "txnD", ["src/demo/bin/gone"])
    assert not (tmp_path / "src/demo/bin/gone").exists()
    ok, _why = _unwind(paths)
    assert ok and (tmp_path / "src/demo/bin/gone").read_bytes() == b"OLD-ONLY"


def test_displaced_file_is_gone_after_commit(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "bin" / "gone").write_bytes(b"OLD-ONLY")
    _open(paths, txn="txnE")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnE")
    bi.displace(paths, "txnE", ["src/demo/bin/gone"])
    assert bi.commit(paths)
    assert not (tmp_path / "src/demo/bin/gone").exists()
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []


def test_note_created_files_are_removed_on_rollback(tmp_path):
    """Provisioned files (the meshtastic CLI venv) join the transaction, so a later failure
    cannot leave a half-built venv behind."""
    paths = _paths(tmp_path)
    _open(paths, txn="txnV")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnV")
    venv = tmp_path / "build" / "tools" / "x" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "cli").write_bytes(b"#!/x")
    bi.note_created(paths, ["build/tools/x/.venv/bin/cli"])
    ok, _why = _unwind(paths)
    assert ok and not (venv / "cli").exists()


def test_publish_refuses_when_no_root_populated(tmp_path):
    paths = _paths(tmp_path)
    stage = tmp_path / "stage"; stage.mkdir()
    _open(paths, txn="txn3")
    with pytest.raises(bi.BinaryInstallError, match="populated none"):
        bi.publish(paths, "demo", stage, [], "txn3")


def test_rollback_restores_an_interrupted_publish(tmp_path):
    paths = _paths(tmp_path)
    # simulate a crash between backup and rename: backup exists, live root gone, journal says
    # "publishing"
    (tmp_path / "state" / "binary").mkdir(parents=True)
    bak_rel = "state/binary/.backup-txn9/src__demo__bin__demo"
    (tmp_path / "state" / "binary" / ".backup-txn9").mkdir(parents=True, exist_ok=True)
    (tmp_path / bak_rel).write_bytes(b"OLD")
    assert bi.write_journal(paths, {"state": "publishing", "stack": "demo", "txn": "txn9",
                                    "roots": ["src/demo/bin/demo"],
                                    "backups": {"src/demo/bin/demo": bak_rel}, "at": 1.0})
    ok, why = _unwind(paths)
    assert ok and why == ""
    assert (tmp_path / "src/demo/bin/demo").read_bytes() == b"OLD"   # restored
    assert bi.read_journal(paths)[1] == "absent"


def test_unreadable_journal_reads_unsafe(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "state" / "binary").mkdir(parents=True)
    bi.journal_path(paths).write_text("{broken")
    assert bi.read_journal(paths) == (None, "unsafe")


def test_journal_with_escaping_paths_reads_unsafe(tmp_path):
    # Every journal path is used to MOVE files: a hand-edited journal must never be executed.
    paths = _paths(tmp_path)
    assert bi.write_journal(paths, {"state": "publishing", "stack": "demo", "txn": "t",
                                    "roots": ["../../etc/passwd"], "backups": {}, "at": 1.0})
    assert bi.read_journal(paths) == (None, "unsafe")


def test_commit_failure_keeps_the_backups(tmp_path, monkeypatch):
    """Until `committed` is durable, recovery must still be able to unwind — dropping the
    backups first would make a crash at that instant unrecoverable."""
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "bin" / "demo").write_bytes(b"OLD")
    _open(paths, txn="txnF")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnF")
    monkeypatch.setattr(bi, "write_journal", lambda *a, **k: False)
    assert bi.commit(paths) is False
    monkeypatch.undo()
    assert list((tmp_path / "state" / "binary").glob(".backup-*"))
    ok, _why = _unwind(paths)
    assert ok and (tmp_path / "src/demo/bin/demo").read_bytes() == b"OLD"


def test_no_journal_is_a_noop_for_reads(tmp_path):
    assert bi.read_journal(_paths(tmp_path)) == (None, "absent")


# --- probe --------------------------------------------------------------------------------------

def test_probe_reads_first_line(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    p = tmp_path / "src/demo/bin/demo"
    p.write_text("#!/bin/sh\necho 'demo 1.2.3'\n")
    p.chmod(0o755)
    assert bi.run_probe(paths, ["src/demo/bin/demo", "--version"]) == "demo 1.2.3"


def test_probe_missing_binary(tmp_path):
    with pytest.raises(bi.BinaryInstallError, match="missing or not executable"):
        bi.run_probe(_paths(tmp_path), ["src/demo/bin/demo", "--version"])


def test_probe_nonzero_exit(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    p = tmp_path / "src/demo/bin/demo"
    p.write_text("#!/bin/sh\nexit 3\n")
    p.chmod(0o755)
    with pytest.raises(bi.BinaryInstallError, match="exited 3"):
        bi.run_probe(paths, ["src/demo/bin/demo", "--version"])


def test_probe_classifies_missing_shared_library(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    p = tmp_path / "src/demo/bin/demo"
    p.write_text("#!/bin/sh\n"
                 "echo 'demo: error while loading shared libraries: liblgpio.so.1: "
                 "cannot open shared object file' >&2\nexit 127\n")
    p.chmod(0o755)
    with pytest.raises(bi.BinaryInstallError, match="missing library: liblgpio"):
        bi.run_probe(paths, ["src/demo/bin/demo", "--version"])


# --- receipt construction -----------------------------------------------------------------------

def test_build_receipt_hashes_installed_files(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "src" / "demo" / "bin").mkdir(parents=True)
    (tmp_path / "src/demo/bin/demo").write_bytes(b"BINARY")
    e = _entry(_make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY"}))
    rec = bi.build_receipt(paths, "demo", e, ["src/demo/bin/demo"], ["src/demo/bin/demo"],
                           {"src/demo": ""}, "demo 1.0")
    assert rec.file_hashes["src/demo/bin/demo"] == hashlib.sha256(b"BINARY").hexdigest()
    assert rec.components == e.components and rec.artifact_sha256 == e.sha256
    assert rec.registry_baseline == {"src/demo": ""}


def test_require_zstd_refuses_with_apt_command(monkeypatch):
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda _n: None)
    with pytest.raises(bi.BinaryInstallError, match="sudo apt install -y zstd"):
        bi.require_zstd()


def test_extract_allows_ancestor_directory_entries(tmp_path):
    # A real tarball (`tar -C stage -cf . `) carries the intermediate DIRECTORY entries that lead
    # to a publish root. They must be accepted (the root cannot be placed otherwise) — while a
    # FILE at the same level stays refused. Live-found on the Zero's first binary install.
    tar = _make_tar(tmp_path, {"src": ("dir",), "src/demo": ("dir",),
                               "src/demo/bin": ("dir",), "src/demo/bin/demo": b"BINARY"})
    stage = tmp_path / "stage"; stage.mkdir()
    files = bi.validate_and_extract(tar, stage, ROOTS)
    assert files == ["src/demo/bin/demo"]          # dirs are not reported as installed files


def test_extract_still_rejects_file_at_ancestor_level(tmp_path):
    tar = _make_tar(tmp_path, {"src": ("dir",), "src/rogue": b"EVIL",
                               "src/demo/bin/demo": b"OK"})
    stage = tmp_path / "stage"; stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match="outside this stack's declared publish"):
        bi.validate_and_extract(tar, stage, ROOTS)


def test_publish_never_destroys_sibling_files(tmp_path):
    """THE data-loss regression: a publish root may live INSIDE a git checkout (the daemon
    binary sits next to its tracked sources). Replacing that directory wholesale destroyed
    tracked files on the Zero. Publishing must merge — only the artifact's own paths move."""
    paths = _paths(tmp_path)
    live_dir = tmp_path / "src" / "demo" / "bin"
    live_dir.mkdir(parents=True)
    (live_dir / "demo").write_bytes(b"OLD BINARY")
    (live_dir / "build.sh").write_bytes(b"TRACKED SOURCE")      # must survive
    (live_dir / "README.md").write_bytes(b"TRACKED DOC")        # must survive
    _open(paths, txn="txnX")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnX")
    assert (live_dir / "demo").read_bytes() == b"NEW"
    assert (live_dir / "build.sh").read_bytes() == b"TRACKED SOURCE"
    assert (live_dir / "README.md").read_bytes() == b"TRACKED DOC"


def test_download_is_streamed_not_buffered(tmp_path, monkeypatch):
    """The artifact must never be held whole in RAM: this feature exists for 512 MB boxes.
    A reader that refuses an unbounded read proves chunking (audit finding)."""
    import contextlib
    import io
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"B" * 4096})
    e = _entry(tar)
    data = tar.read_bytes()

    class ChunkedOnly(io.BytesIO):
        def read(self, n=-1):
            assert n is not None and n > 0, "download_artifact must read in bounded chunks"
            return super().read(n)

    monkeypatch.setattr(bi, "_open_stream",
                        lambda url: contextlib.closing(ChunkedOnly(data)))
    dest = tmp_path / "dl.tar.zst"
    bi.download_artifact(e, dest)
    assert dest.read_bytes() == data


def _venv(tmp_path, rel="build/tools/meshtastic-cli", marker=b"OLD"):
    """A venv-shaped tree: regular files PLUS symlinks pointing outside the runtime root —
    exactly what the per-file path guard refuses to touch."""
    d = tmp_path / rel / ".venv" / "bin"
    d.mkdir(parents=True)
    (d / "pip").write_bytes(marker)
    (d / "meshtastic").write_bytes(marker)
    (d / "python3").symlink_to("/usr/bin/python3")      # ESCAPES the runtime root
    (d / "python").symlink_to("python3")
    return d


def test_displace_dir_moves_a_venv_intact_and_puts_it_back(tmp_path):
    """A provisioned venv is owned and restored as a DIRECTORY. Displacing only its regular
    files left `bin/python3` behind, `python3 -m venv` then treated the environment as existing
    and skipped ensurepip, and the next step failed with "pip install failed" (live-found on
    the Zero)."""
    paths = _paths(tmp_path)
    d = _venv(tmp_path)
    _open(paths, txn="txnS")
    assert bi.displace_dir(paths, "txnS", "build/tools/meshtastic-cli") is True
    assert not (tmp_path / "build" / "tools" / "meshtastic-cli").exists()
    # …the provisioning builds a fresh, BROKEN one in its place, then fails
    d2 = tmp_path / "build" / "tools" / "meshtastic-cli" / ".venv" / "bin"
    d2.mkdir(parents=True)
    (d2 / "half-built").write_bytes(b"NEW")
    ok, why = _unwind(paths)
    assert ok, why
    assert (d / "pip").read_bytes() == b"OLD"                 # the WORKING venv is back
    assert os.path.islink(d / "python3") and os.readlink(d / "python3") == "/usr/bin/python3"
    assert not (d / "half-built").exists()                    # …and the half-built one is gone


def test_displaced_venv_is_dropped_on_commit(tmp_path):
    paths = _paths(tmp_path)
    _venv(tmp_path)
    _open(paths, txn="txnU")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnU")
    assert bi.displace_dir(paths, "txnU", "build/tools/meshtastic-cli")
    assert bi.commit(paths)
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []


def test_displace_dir_ignores_an_absent_directory(tmp_path):
    paths = _paths(tmp_path)
    _open(paths, txn="txnV2")
    assert bi.displace_dir(paths, "txnV2", "build/tools/meshtastic-cli") is False


def test_displace_never_moves_a_directory(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "build" / "tools" / "x").mkdir(parents=True)
    _open(paths, txn="txnT")
    assert bi.displace(paths, "txnT", ["build/tools/x"]) == {}
    assert (tmp_path / "build" / "tools" / "x").is_dir()


def test_rollback_removes_a_venv_this_run_created(tmp_path):
    """FIRST-TIME provisioning: there is no previous venv to displace, so the directory the run
    is about to create is journaled before the first command. A hard crash mid-provisioning
    otherwise left a half-built venv that no journal, receipt or recovery knew about."""
    paths = _paths(tmp_path)
    _open(paths, txn="txnW")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnW")
    assert bi.displace_dir(paths, "txnW", "build/tools/meshtastic-cli") is False
    bi.note_created_dir(paths, "build/tools/meshtastic-cli")
    half = tmp_path / "build" / "tools" / "meshtastic-cli" / ".venv" / "bin"
    half.mkdir(parents=True)                       # …the crash lands here
    (half / "python3").symlink_to("/usr/bin/python3")
    ok, why = _unwind(paths)                       # the next binary operation recovers
    assert ok, why
    assert not (tmp_path / "build" / "tools" / "meshtastic-cli").exists()


def test_created_dir_survives_a_commit(tmp_path):
    paths = _paths(tmp_path)
    _open(paths, txn="txnW2")
    bi.publish(paths, "demo", _staged(tmp_path), ["src/demo/bin/demo"], "txnW2")
    bi.note_created_dir(paths, "build/tools/meshtastic-cli")
    (tmp_path / "build" / "tools" / "meshtastic-cli").mkdir(parents=True)
    assert bi.commit(paths)
    assert (tmp_path / "build" / "tools" / "meshtastic-cli").is_dir()


def test_journal_with_an_escaping_created_dir_reads_unsafe(tmp_path):
    paths = _paths(tmp_path)
    assert bi.write_journal(paths, {"state": "publishing", "stack": "demo", "txn": "t",
                                    "roots": [], "backups": {},
                                    "created_dirs": ["../../etc"], "at": 1.0})
    assert bi.read_journal(paths) == (None, "unsafe")


def test_rollback_restores_directories_before_the_files_inside_them(tmp_path):
    """ORDER IS THE CONTRACT: a displaced FILE can live inside a displaced DIRECTORY — the
    artifact's binary sits in the source checkout a channel switch sets aside. Restoring files
    first put them back only for the directory restore to wipe them (audit finding)."""
    paths = _paths(tmp_path)
    checkout = tmp_path / "src" / "app"
    (checkout / "bin").mkdir(parents=True)
    (checkout / "tracked.txt").write_text("SOURCE")
    (checkout / "bin" / "artifact").write_bytes(b"ARTIFACT")
    _open(paths, txn="txnO2")
    bi.displace(paths, "txnO2", ["src/app/bin/artifact"])       # the artifact file
    bi.displace_dir(paths, "txnO2", "src/app")                  # …and the checkout around it
    assert not checkout.exists()
    (checkout / "bin").mkdir(parents=True)                      # what the switch built instead
    (checkout / "bin" / "new").write_text("x")
    ok, why = _unwind(paths)
    assert ok, why
    assert (checkout / "tracked.txt").read_text() == "SOURCE"
    assert (checkout / "bin" / "artifact").read_bytes() == b"ARTIFACT"
    assert not (checkout / "bin" / "new").exists()

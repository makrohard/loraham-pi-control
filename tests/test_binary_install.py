"""Binary install transaction (B4): index → verify → extract → publish → probe → receipt.

Everything runs against a LOCAL fake release (no network): `_http_get` is stubbed with a
byte-serving fake, and tarballs are built on the fly — including the hostile ones (symlink
escape, `..`, member outside the declared publish roots, oversize, sha/size mismatch)."""


import hashlib
import io
import json
import os
import tarfile
import pytest
import zstandard
from lhpc.core import binary_install as bi, binary_receipt as brx, source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService
from lhpc.core.probes import RealSystem


pytestmark = pytest.mark.requires_zstd


# ===== merged from test_binary_install.py =====
def _paths(tmp_path):
    return Paths(runtime_root=tmp_path)


def _make_tar(tmp_path, members, name="art.tar.zst"):
    """Build a genuine zstd tarball in-process.

    `members` is EITHER a mapping {relpath: bytes|('dir',)|('sym', target)} OR an ordered
    sequence [(relpath, val), ...] — the sequence form allows DUPLICATE member names (the
    "same file twice" hostile archive) that a dict cannot express.
    """
    items = members.items() if isinstance(members, dict) else list(members)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for rel, val in items:
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
                info = tarfile.TarInfo(rel)
                info.mode = 0o755
                info.size = len(val)
                tf.addfile(info, io.BytesIO(val))
    out = tmp_path / name
    out.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))
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


ROOTS = ("src/demo/bin", "build/tools/demo")


def test_extract_valid_members(tmp_path):
    tar = _make_tar(tmp_path, {"src/demo/bin/demo": b"BINARY",
                               "build/tools/demo/asset": b"A"})
    stage = tmp_path / "stage"; stage.mkdir()
    files = bi.validate_and_extract(tar, stage, ROOTS)
    assert files == ["build/tools/demo/asset", "src/demo/bin/demo"]
    assert (stage / "src/demo/bin/demo").read_bytes() == b"BINARY"
    assert os.access(stage / "src/demo/bin/demo", os.X_OK)


@pytest.mark.parametrize("members,match", [
    pytest.param({"src/demo/bin/evil": ("sym", "/etc/passwd")}, "link member",
                 id="test_extract_rejects_symlink_escape"),
    pytest.param({"src/demo/bin/../../../etc/x": b"X"}, "escapes the root|outside this stack",
                 id="test_extract_rejects_parent_traversal"),
    # THE plan's key rule: an artifact may never touch paths the manifest did not declare.
    pytest.param({"src/demo/bin/demo": b"OK", "src/other-stack/thing": b"EVIL"},
                 "outside this stack's declared publish",
                 id="test_extract_rejects_member_outside_publish_roots"),
])
def test_extract_rejects_hostile_archive(tmp_path, members, match):
    tar = _make_tar(tmp_path, members)
    stage = tmp_path / "stage"; stage.mkdir()
    with pytest.raises(bi.BinaryInstallError, match=match):
        bi.validate_and_extract(tar, stage, ROOTS)


def test_extract_rejects_a_duplicate_member(tmp_path):
    """A second member with the same name would overwrite an already-validated file after it
    was checked — refuse the archive instead (audit finding)."""
    # Ordered-sequence form: two members with the SAME name (a dict cannot express this).
    out = _make_tar(tmp_path, [("src/demo/bin/demo", b"first"),
                               ("src/demo/bin/demo", b"second")], name="dup.tar.zst")
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


# ===== merged from test_binary_switching.py =====
def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _pins(svc, stack="daemon"):
    return svc._binary_pins(stack)


def _lay_down(svc, tmp_path, stack="daemon", commits=None, extra_files=()):
    """Simulate a completed binary install (artifact files + receipt)."""
    spec = svc.binary_spec(stack)
    files = list(spec.proof_paths) + list(extra_files)
    hashes = {}
    import hashlib
    for rel in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
        hashes[rel] = hashlib.sha256(b"ELF").hexdigest()
    rec = brx.BinaryReceipt(
        stack=stack, artifact_sha256="ab" * 32, artifact_size=9,
        filename=f"{stack}-{'ab' * 32}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=commits if commits is not None else dict(_pins(svc, stack)),
        provenance={}, files=tuple(files), file_hashes=hashes,
        proof_paths=tuple(spec.proof_paths), registry_baseline={}, probe="ok")
    assert brx.write_receipt(svc._paths, rec)
    svc.invalidate_snapshot()
    return rec


@pytest.mark.contract
def test_install_binary_channel_dispatches(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    called = {}
    monkeypatch.setattr(ControllerService, "binary_install",
                        lambda self, sid, apply=False: called.setdefault("args", (sid, apply)))
    svc.install("daemon", apply=True, source="binary")
    assert called["args"] == ("daemon", True)


@pytest.mark.contract
def test_install_binary_channel_refuses_all_stacks(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = svc.install(None, apply=True, source="binary")
    assert not res.ok and "ONE stack at a time" in res.summary


class _Adopted:
    """What a SUCCESSFUL `_adopt_dev_fallback` returns."""

    status, detail, provenance = "done", "", ""


def _stub_adopt(svc, monkeypatch, *, records=True):
    """A SUCCESSFUL adoption INCLUDING the ownership record it writes — the switch is not
    complete until every adopted path is recorded, so a stub that skips the record is a failed
    switch, not a successful one. `records=False` simulates exactly that."""
    from lhpc.core import source_registry

    def _adopt(self, inst, st, comp, selector, resolved, force=False, locked=False):
        if records:
            source_registry.write_record(svc._paths, source_registry.RegistryRecord(
                source_rel=comp.source.path,
                remote=comp.source.remote or "https://example.invalid/x.git",
                selector=selector, resolved_commit="e" * 40, adopted_at=1.0,
                txn_id="txn-" + comp.id, strategy="adopt", components=(comp.id,)))
        return _Adopted()
    monkeypatch.setattr(ControllerService, "_adopt_dev_fallback", _adopt)


def test_successful_switch_retires_the_binary_for_good(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    _stub_adopt(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="pinned")
    assert res.ok, res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"
    assert not (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"                 # transaction committed
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []


def test_failed_source_switch_restores_the_binary_without_the_network(tmp_path, monkeypatch):
    """THE switch regression: the artifact is moved aside locally, so a failed adoption puts
    the EXACT previous install back — no download, no release lookup, no pin re-check. An
    operator switching to source is usually doing it BECAUSE the published binary is behind;
    a restore that re-downloads would hit that same pin gate and refuse (audit finding)."""
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    before = (tmp_path / rec.proof_paths[0]).read_bytes()
    monkeypatch.setattr(ControllerService, "binary_install",
                        lambda *a, **k: pytest.fail("restoring must never reach the network"))
    res = svc.install("daemon", apply=True, source="pinned")          # clone fails in the fake env
    assert not res.ok
    assert any("restored the binary install from disk" in d for d in res.details)
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).read_bytes() == before
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_failed_switch_restores_an_owned_directory_too(tmp_path, monkeypatch):
    import dataclasses
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    venv = tmp_path / "build" / "tools" / "meshtastic-cli" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "meshtastic").write_bytes(b"CLI")
    (venv / "python3").symlink_to("/usr/bin/python3")
    assert brx.write_receipt(svc._paths, dataclasses.replace(
        rec, owned_dirs=("build/tools/meshtastic-cli",)))
    svc.invalidate_snapshot()
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok
    assert (venv / "meshtastic").read_bytes() == b"CLI"
    assert os.path.islink(venv / "python3")


def test_switch_refuses_when_the_receipt_cannot_be_read(tmp_path, monkeypatch):
    """"Ownership unknown" must never become a silent switch: the receipt stays as evidence."""
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path, monkeypatch)
    runtime_fs.mkdir(svc._paths, "state", "binary")
    brx.receipt_path(svc._paths, "daemon").write_text("{not json")
    svc.invalidate_snapshot()
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "malformed" in res.summary
    assert brx.receipt_path(svc._paths, "daemon").exists()          # evidence retained
    assert bi.read_journal(svc._paths)[1] == "absent"               # …and nothing left open


def test_superseded_receipt_is_retired_on_a_switch(tmp_path, monkeypatch):
    """A SUPERSEDED receipt still names files this box owns — `on_binary_channel` (valid only)
    let those bypass retirement entirely (audit finding)."""
    from lhpc.core import source_registry
    import dataclasses
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    # recorded "no source record at install" -> a record appears == the source channel took over
    rec = dataclasses.replace(rec, registry_baseline={"src/loraham-daemon": ""})
    assert brx.write_receipt(svc._paths, rec)
    assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel="src/loraham-daemon", remote="https://example.invalid/d.git",
        selector="pinned", resolved_commit="d" * 40, adopted_at=1.0, txn_id="later",
        strategy="adopt", components=("loraham-daemon",)))
    svc.invalidate_snapshot()
    assert brx.receipt_state(svc._paths, "daemon")[0] == "superseded"
    _stub_adopt(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="pinned")
    assert res.ok, res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"
    assert not (tmp_path / rec.proof_paths[0]).exists()


def test_source_dry_run_never_retires(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    svc.install("daemon", apply=False, source="pinned")
    assert (tmp_path / rec.proof_paths[0]).exists()
    assert svc.on_binary_channel("daemon") is True


def test_retire_refuses_when_files_changed(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    (tmp_path / rec.files[0]).write_bytes(b"OPERATOR EDIT")
    res = svc.binary_retire("daemon")
    assert not res.ok and "changed since installation" in res.summary
    assert (tmp_path / rec.files[0]).exists()               # nothing deleted
    # ...and a source install therefore refuses too, instead of clobbering
    res2 = svc.install("daemon", apply=True, source="pinned")
    assert not res2.ok and "changed since installation" in res2.summary


def test_retire_force_ignores_hash_mismatch(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    (tmp_path / rec.files[0]).write_bytes(b"EDIT")
    assert svc.binary_retire("daemon", force=True).ok
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"


def test_retire_without_receipt_is_noop(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = svc.binary_retire("daemon")
    assert res.ok and "no binary install" in res.summary


def test_update_binary_to_binary_when_current(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)                                 # components == manifest pins
    seen = {}
    monkeypatch.setattr(ControllerService, "binary_install",
                        lambda self, sid, apply=False: seen.setdefault("args", (sid, apply)))
    # "binary" is what the CLI resolves to for a binary-installed stack with no --source
    svc.update("daemon", apply=True, source="binary")
    assert seen["args"] == ("daemon", True)                  # fast path, no dialog


def test_update_offers_source_when_binary_lags(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    stale = {cid: "9" * 40 for cid in _pins(svc)}
    _lay_down(svc, tmp_path, commits=stale)
    res = svc.update("daemon", apply=True, source="binary")
    assert not res.ok
    assert "only as source" in res.summary
    assert res.data["binary_behind"] and res.data["offer_source"] is True
    assert any("--source pinned" in c for c in res.next_commands)
    assert any("hours" in d for d in res.details)            # the long-compile warning


@pytest.mark.parametrize("selector", ["dev", "stable", "pinned"])
def test_update_with_explicit_source_selector_points_at_install(tmp_path, monkeypatch, selector):
    # EVERY source selector is an explicit channel switch — including "pinned" (it must not be
    # silently hijacked into a binary update; audit finding).
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    res = svc.update("daemon", apply=True, source=selector)
    assert not res.ok and "is an install, not an update" in res.summary
    assert any(f"--source {selector}" in c for c in res.next_commands)


def test_update_unaffected_for_source_stacks(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)                                 # daemon on binary
    res = svc.update("kiss", apply=False, source="pinned")   # a source stack
    assert "only as source" not in res.summary
    assert "is an install" not in res.summary


def test_freshness_current_and_behind(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert svc.binary_freshness("daemon") == {"state": "n/a", "behind": []}
    _lay_down(svc, tmp_path)
    assert svc.binary_freshness("daemon")["state"] == "current"
    brx.remove_receipt(svc._paths, "daemon")
    _lay_down(svc, tmp_path, commits={cid: "9" * 40 for cid in _pins(svc)})
    f = svc.binary_freshness("daemon")
    assert f["state"] == "behind" and "loraham-daemon" in f["behind"]


def test_freshness_is_local_only(tmp_path, monkeypatch):
    # GET-safe: the freshness answer must never touch the network.
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    monkeypatch.setattr(bi, "_http_get",
                        lambda *a, **k: pytest.fail("freshness must not fetch"))
    assert svc.binary_freshness("daemon")["state"] == "current"


def test_clone_required_is_adopted_before_the_overlay(tmp_path, monkeypatch):
    # HYBRID stacks (meshcom): the artifact overlays build output, but the run scripts live in
    # the repo — the pinned clone MUST be adopted or the stack installs "fine" and cannot start.
    # (Live-found on the Zero, where a pre-existing clone had masked the gap.)
    svc = _svc(tmp_path, monkeypatch)
    adopted = []

    class _FakeAction:
        status, detail = "done", "cloned"

    class _FakeInstaller:
        # the source-operation guard also asks the installer for its index key + journal state
        def adopt_source(self, comp, *, source="pinned", **kw):
            adopted.append((comp.id, source))
            return _FakeAction()

        def _index_key(self):
            return "source-txn-index"

        def _recover_scan(self):
            return None

        def _pending_journals(self):
            return []

    monkeypatch.setattr(ControllerService, "_installer", lambda self: _FakeInstaller())
    monkeypatch.setattr(bi, "fetch_index",
                        lambda url: (_ for _ in ()).throw(bi.BinaryInstallError("stop here")))
    svc.binary_install("meshcom", apply=True)
    # the index fetch happens FIRST (gates before mutation), so nothing was adopted yet
    assert adopted == []

    monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 2, "stacks": {}})
    monkeypatch.setattr(bi, "index_entry", lambda idx, sid: _fake_entry(svc, sid))
    monkeypatch.setattr(bi, "check_target", lambda e, t: None)
    monkeypatch.setattr(bi, "check_pins", lambda e, p: None)
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, p: True)
    monkeypatch.setattr(bi, "download_artifact",
                        lambda e, d: (_ for _ in ()).throw(bi.BinaryInstallError("stop after clone")))
    svc.binary_install("meshcom", apply=True)
    assert ("meshcom-qemu", "pinned") in adopted        # adopted BEFORE the download


def _fake_entry(svc, sid):
    return bi.IndexEntry(
        stack=sid, filename=f"{sid}-{'a' * 64}.tar.zst", url="https://example.invalid/a.tar.zst",
        sha256="a" * 64, size=10, built_from="b" * 40,
        components=dict(svc._binary_pins(sid)), runtime_deps=(), target="aarch64-trixie",
        os_name="trixie", provenance={"smoke": {"mode": "mandatory", "result": "passed"}})


def test_switch_plan_counts_a_change_even_when_sources_exist(tmp_path, monkeypatch):
    # The CLI's dry-run short-circuit skips apply when `changes == 0`. With the source dirs
    # already present the adoption plan is empty, so the RETIREMENT must be counted — otherwise
    # `lhpc install <stack> --source pinned --yes` reports "Nothing to do" and silently leaves
    # the stack on the binary channel (live-found on the Zero).
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    res = svc.install("daemon", apply=False, source="pinned")
    assert res.data.get("changes", 0) >= 1
    assert any("retire the binary install" in d for d in res.details)


def test_switch_plan_unchanged_without_receipt(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = svc.install("daemon", apply=False, source="pinned")
    assert not any("retire the binary install" in d for d in res.details)


def test_retire_leaves_sibling_source_files_intact(tmp_path, monkeypatch):
    """Retirement removes ONLY the receipt's own files and prunes only the directories they
    left empty — a source directory that also holds tracked files must survive untouched."""
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    live_dir = (tmp_path / rec.files[0]).parent
    (live_dir / "build.sh").write_bytes(b"TRACKED")
    assert svc.binary_retire("daemon").ok
    assert not (tmp_path / rec.files[0]).exists()          # artifact gone
    assert (live_dir / "build.sh").read_bytes() == b"TRACKED"   # source intact
    assert live_dir.is_dir()                                # shared dir not pruned


def _running(monkeypatch, comps):
    monkeypatch.setattr(ControllerService, "_binary_running_components", lambda self, sid: comps)


def test_binary_install_refuses_while_running(tmp_path, monkeypatch):
    """A binary update replaces the very executable/firmware the stack is running from — the
    authoritative recheck happens UNDER the held locks (audit finding)."""
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    _running(monkeypatch, ["loraham-daemon"])
    # the read-only gates (index, pins, target) run first — the RUNNING check guards the
    # mutation, under the held locks
    monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 2, "stacks": {}})
    monkeypatch.setattr(bi, "index_entry", lambda idx, sid: _fake_entry(svc, sid))
    monkeypatch.setattr(bi, "check_target", lambda e, tgt: None)
    monkeypatch.setattr(bi, "check_pins", lambda e, p: None)
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, p: True)
    monkeypatch.setattr(bi, "download_artifact",
                        lambda e, d: (_ for _ in ()).throw(AssertionError("must not download")))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and "running" in res.summary


def test_binary_retire_refuses_while_running(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    _running(monkeypatch, ["loraham-daemon"])
    res = svc.binary_retire("daemon")
    assert not res.ok and "running" in res.summary
    assert (tmp_path / rec.files[0]).exists()          # nothing deleted


def test_retire_keeps_the_receipt_when_a_file_cannot_be_removed(tmp_path, monkeypatch):
    """A swallowed unlink failure would leave binary files with NO ownership record."""
    import os as _os
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    monkeypatch.setattr(_os, "unlink",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("EPERM")))
    res = svc.binary_retire("daemon", force=True)
    assert not res.ok and "INCOMPLETE" in res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"   # still owned


def test_update_source_binary_on_a_source_stack_routes_to_binary_install(tmp_path, monkeypatch):
    """`--source binary` on a source-installed stack must reach the binary channel. The source
    planners only understand pinned/dev/stable, so the selector used to be ignored and a full
    SOURCE update ran instead (audit finding)."""
    svc = _svc(tmp_path, monkeypatch)
    seen = {}

    def _plan(self, sid, apply=False, locked=False):
        seen["args"] = (sid, apply)
        return ActionResult(True, "binary plan")
    monkeypatch.setattr(ControllerService, "binary_install", _plan)
    res = svc.update("daemon", apply=False, source="binary")
    assert res.ok and seen["args"] == ("daemon", False)


def test_update_source_binary_refuses_where_unavailable(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = svc.update("kiss", apply=False, source="binary")
    assert not res.ok and "no prebuilt binary is published" in res.summary


def test_update_source_binary_refuses_the_all_target(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = svc.update("", apply=False, source="binary")
    assert not res.ok and "ONE stack at a time" in res.summary


def test_switch_transaction_is_resolved_even_when_a_later_step_fails(tmp_path, monkeypatch):
    """The transaction must be resolved on the ADOPTION outcome, before any later early return.
    A still-open journal would make the next binary operation roll the old artifact back OVER
    the freshly installed sources."""
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    _stub_adopt(svc, monkeypatch)
    monkeypatch.setattr(ControllerService, "_retire_candidates_for_paths",
                        lambda *a, **k: False)                   # a LATER step fails
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "candidate cleanup INCOMPLETE" in res.summary
    assert bi.read_journal(svc._paths)[1] == "absent"             # committed, not left open
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"


def test_superseded_web_job_puts_the_artifact_back(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    res = svc.install("daemon", apply=True, source="pinned", on_admit=lambda: False)
    assert not res.ok and "superseded" in res.summary
    assert (tmp_path / rec.proof_paths[0]).exists()
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert bi.read_journal(svc._paths)[1] == "absent"


# ===== merged from test_binary_switch_selector.py =====
DAEMON_PATH = "src/loraham-daemon"


RADIOLIB_PATH = "src/RadioLib"


def _svc_binary_switch_selector(tmp_path, monkeypatch):
    """A REAL runner: these tests turn on actual git checkouts (HEAD, remote, dirtiness), which
    is exactly what the switch pre-flight reads. No network — every repo is local.

    `RealSystem` also scans the REAL procfs, so `_binary_running_components()` saw whatever the
    developer's box happened to be running: with the daemon up, eight of these failed with
    "Refusing to retire ... component(s) running", and passed again once it was stopped. A suite
    whose colour depends on machine state gets believed when it should not be, or ignored when it
    should not be. The running-probe is therefore stubbed here; the tests that are ABOUT that
    refusal set their own (see `_running`).
    """
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    monkeypatch.setattr(ControllerService, "_binary_running_components", lambda self, sid: [])
    return ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))


def test_the_binary_switch_fixture_ignores_the_real_machine(tmp_path, monkeypatch):
    """This fixture builds a service on `RealSystem`, whose process probe scans the REAL procfs.
    Eight tests in this file failed whenever the developer's own daemon was running ("Refusing to
    retire ... component(s) running") and passed when it was stopped.

    A suite whose result depends on machine state gets believed when it should not be — so the
    running-probe is stubbed here, and tests ABOUT the refusal set their own.
    """
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    assert svc._binary_running_components("daemon") == [], (
        "the fixture must not consult the real machine")


def _lay_down_binary_switch_selector(svc, tmp_path, stack="daemon"):
    """A completed binary install: the artifact's files plus its receipt."""
    spec = svc.binary_spec(stack)
    files, hashes = [], {}
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
        files.append(rel)
        hashes[rel] = hashlib.sha256(b"ELF").hexdigest()
    rec = brx.BinaryReceipt(
        stack=stack, artifact_sha256="ab" * 32, artifact_size=9,
        filename=f"{stack}-{'ab' * 32}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=dict(svc._binary_pins(stack)), provenance={}, files=tuple(files),
        file_hashes=hashes, proof_paths=tuple(spec.proof_paths), registry_baseline={},
        probe="ok")
    assert brx.write_receipt(svc._paths, rec)
    svc.invalidate_snapshot()
    return rec


def _git(svc, cwd, *args):
    r = svc._system.runner.run(["git", "-C", str(cwd), *args], 20.0)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return (r.stdout or "").strip()


def _checkout(svc, tmp_path, rel, comp_id, *, commits=2, remote=None, dirty=False,
              at_first=False):
    """A REAL managed checkout at `rel` with an ownership record — the state an operator has
    after a source install (or, for meshcom, after a binary install kept its pinned clone).

    Returns the commit the checkout sits at.
    """
    comp = next(c for st in svc.stacks() for c in st.components if c.id == comp_id)
    origin = remote if remote is not None else (comp.source.remote or "https://x.invalid/r.git")
    d = tmp_path / rel
    d.mkdir(parents=True, exist_ok=True)
    _git(svc, d, "init", "-q", "-b", "main")
    _git(svc, d, "config", "user.email", "t@example.invalid")
    _git(svc, d, "config", "user.name", "t")
    shas = []
    for i in range(commits):
        (d / f"f{i}").write_text(str(i))
        _git(svc, d, "add", "-A")
        _git(svc, d, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(svc, d, "rev-parse", "HEAD"))
    _git(svc, d, "remote", "add", "origin", origin)
    if at_first:
        _git(svc, d, "checkout", "-q", shas[0])
    head = _git(svc, d, "rev-parse", "HEAD")
    assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel=rel, remote=origin, selector="pinned", resolved_commit=head,
        adopted_at=1.0, txn_id="txn-" + comp_id, strategy="adopt", components=(comp_id,)))
    if dirty:
        (d / "operator-notes.txt").write_text("mine")
    return head


class _Adopted_binary_switch_selector:
    status, detail, provenance = "done", "", ""


class _Failed:
    status, detail, provenance = "failed", "clone failed", ""


def _stub_adopt_binary_switch_selector(svc, monkeypatch, *, fail_paths=(), record=True):
    """A successful adoption INCLUDING the ownership record it writes (the switch is not
    complete until every adopted path is recorded). Paths in `fail_paths` fail instead."""
    seen = []

    def _adopt(self, inst, st, comp, selector, resolved, force=False, locked=False):
        path = comp.source.path
        seen.append((path, selector, force))
        if path in fail_paths:
            return _Failed()
        (svc._paths.resolve_source(path)).mkdir(parents=True, exist_ok=True)
        if record:
            source_registry.write_record(svc._paths, source_registry.RegistryRecord(
                source_rel=path, remote=comp.source.remote or "https://x.invalid/r.git",
                selector=selector, resolved_commit="e" * 40, adopted_at=2.0,
                txn_id="txn-new-" + comp.id, strategy="adopt", components=(comp.id,)))
        return _Adopted_binary_switch_selector()
    monkeypatch.setattr(ControllerService, "_adopt_dev_fallback", _adopt)
    return seen


def test_pinned_checkout_switching_to_dev_is_replaced(tmp_path, monkeypatch):
    """The reported case: meshcom keeps its PINNED clone on the binary channel, the operator
    asks for `dev`, and the pinned tree used to be accepted as "already installed"."""
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    _lay_down_binary_switch_selector(svc, tmp_path)
    _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    seen = _stub_adopt_binary_switch_selector(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="dev")
    assert res.ok, res.summary
    assert (DAEMON_PATH, "dev", True) in seen, "the pinned tree must be REPLACED, not skipped"


def test_dev_checkout_switching_to_pinned_reaches_the_pin(tmp_path, monkeypatch):
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    _lay_down_binary_switch_selector(svc, tmp_path)
    _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")   # at some other commit
    seen = _stub_adopt_binary_switch_selector(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="pinned")
    assert res.ok, res.summary
    assert (DAEMON_PATH, "pinned", True) in seen


def test_checkout_already_at_the_pin_is_a_no_op(tmp_path, monkeypatch):
    """An already-correct checkout must not be re-cloned."""
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    _lay_down_binary_switch_selector(svc, tmp_path)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "loraham-daemon")
    head = _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")
    monkeypatch.setattr(type(comp.source), "pin_commit", property(lambda _s: head), raising=False)
    replace, refusals = svc.switch_source_plan(
        [(DAEMON_PATH, comp, "pinned", (head, ""))], owned_files=())
    assert replace == set() and refusals == []


@pytest.mark.parametrize("kind", ["dirty", "wrong-remote"])
def test_unprovable_checkout_refuses_without_retiring_the_binary(tmp_path, monkeypatch, kind):
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    rec = _lay_down_binary_switch_selector(svc, tmp_path)
    _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon",
              dirty=(kind == "dirty"),
              remote=("https://elsewhere.invalid/other.git" if kind == "wrong-remote" else None))
    monkeypatch.setattr(ControllerService, "binary_retire",
                        lambda *a, **k: pytest.fail("the binary must not be touched"))
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "cannot be taken over" in res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_second_group_failing_restores_the_binary_and_undoes_what_it_created(tmp_path,
                                                                             monkeypatch):
    """One source group succeeds, a later one fails: the previous binary must be back, and the
    checkout this switch created must be gone again (a pre-existing one is never touched)."""
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    rec = _lay_down_binary_switch_selector(svc, tmp_path)
    seen = _stub_adopt_binary_switch_selector(svc, monkeypatch, fail_paths=(RADIOLIB_PATH,))
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "FAILED" in res.summary
    assert {p for p, _s, _f in seen} == {DAEMON_PATH, RADIOLIB_PATH}
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"      # the binary is back
    assert (tmp_path / rec.proof_paths[0]).read_bytes() == b"ELF"
    assert source_registry.record_state(svc._paths, DAEMON_PATH)[0] == "absent"
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_incomplete_ownership_record_rolls_the_switch_back(tmp_path, monkeypatch):
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    rec = _lay_down_binary_switch_selector(svc, tmp_path)
    _stub_adopt_binary_switch_selector(svc, monkeypatch, record=False)          # adoption "succeeds" but records nothing
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "ownership record" in res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()


def test_failed_hmac_enablement_restores_the_binary_and_open_auth(tmp_path, monkeypatch):
    """MeshCom source adoption succeeds but the password cannot be enabled: the switch is not
    complete, so the binary (which runs OPEN auth) must be restored unchanged."""
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    rec = _lay_down_binary_switch_selector(svc, tmp_path, stack="meshcom")
    _stub_adopt_binary_switch_selector(svc, monkeypatch)
    monkeypatch.setattr(ControllerService, "hmac_set_secret",
                        lambda self, sid, action, **k: ActionResult(False, "keyfile unwritable"))
    res = svc.install("meshcom", apply=True, source="pinned")
    assert not res.ok and "HMAC password" in res.summary
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()
    hc = svc._hmac_component("meshcom")
    assert svc._resolved_param_value("meshcom", "run", hc.id, "password_file") == ""
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_complete_switch_commits_the_retirement(tmp_path, monkeypatch):
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    rec = _lay_down_binary_switch_selector(svc, tmp_path)
    _stub_adopt_binary_switch_selector(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="pinned")
    assert res.ok, res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"
    assert not (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []


def _baseline_receipt(svc, tmp_path, stack, paths_):
    """A binary receipt whose registry baseline records the CURRENT txn id of each covered
    source path — the comparison that decides valid vs superseded."""
    rec = _lay_down_binary_switch_selector(svc, tmp_path, stack=stack)
    import dataclasses
    base = {}
    for rel in paths_:
        state, rrec, _why = source_registry.record_state(svc._paths, rel)
        base[rel] = rrec.txn_id if (state == "valid" and rrec) else ""
    rec = dataclasses.replace(rec, registry_baseline=base)
    assert brx.write_receipt(svc._paths, rec)
    svc.invalidate_snapshot()
    return rec


def test_meshcom_pinned_clone_is_restored_when_hmac_fails(tmp_path, monkeypatch):
    """THE realistic case: meshcom keeps its PINNED clone on the binary channel, `--source dev`
    replaces it, and the HMAC step then fails. The clone, its ownership record AND the binary
    receipt must all be back — a restored receipt whose baseline no longer matches the registry
    reads SUPERSEDED, which is not a restored install (audit finding)."""
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    qemu_path = next(c.source.path for st in svc.stacks() for c in st.components
                     if c.id == "meshcom-qemu")
    head = _checkout(svc, tmp_path, qemu_path, "meshcom-qemu")
    old_txn = source_registry.record_state(svc._paths, qemu_path)[1].txn_id
    rec = _baseline_receipt(svc, tmp_path, "meshcom", [qemu_path])
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"

    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    _stub_adopt_binary_switch_selector(svc, monkeypatch)                       # replaces the clone, writes a NEW record
    monkeypatch.setattr(ControllerService, "hmac_set_secret",
                        lambda self, sid, action, **k: ActionResult(False, "keyfile unwritable"))

    res = svc.install("meshcom", apply=True, source="dev")
    assert not res.ok and "HMAC password" in res.summary
    # the pinned clone is back, at its old commit and under its old ownership record
    assert _git(svc, tmp_path / qemu_path, "rev-parse", "HEAD") == head
    state, rrec, _why = source_registry.record_state(svc._paths, qemu_path)
    assert state == "valid" and rrec.txn_id == old_txn
    # …so the restored receipt is VALID, not superseded
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_replaced_first_source_is_restored_when_a_later_group_fails(tmp_path, monkeypatch):
    """First group: a pre-existing checkout is REPLACED. Second group fails. The first checkout
    and its record must return to their pre-switch state."""
    svc = _svc_binary_switch_selector(tmp_path, monkeypatch)
    head = _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")
    old_txn = source_registry.record_state(svc._paths, DAEMON_PATH)[1].txn_id
    rec = _baseline_receipt(svc, tmp_path, "daemon", [DAEMON_PATH])
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    _stub_adopt_binary_switch_selector(svc, monkeypatch, fail_paths=(RADIOLIB_PATH,))

    res = svc.install("daemon", apply=True, source="dev")
    assert not res.ok and "FAILED" in res.summary
    assert _git(svc, tmp_path / DAEMON_PATH, "rev-parse", "HEAD") == head
    state, rrec, _why = source_registry.record_state(svc._paths, DAEMON_PATH)
    assert state == "valid" and rrec.txn_id == old_txn
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).read_bytes() == b"ELF"
    assert bi.read_journal(svc._paths)[1] == "absent"

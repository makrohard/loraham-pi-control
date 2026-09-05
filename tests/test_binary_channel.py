"""Binary CHANNEL resolution + coverage predicates (B3).

The channel is a fourth selector, never a persisted preference: declaration + platform decide
availability, a valid receipt decides "is it on binary now", and SOURCE_CHOICES stays 3-valued
so the git planners can never see "binary"."""


import pytest
import json
from lhpc.core import binary_receipt as brx, runtime_fs, source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.model import SourceState


# ===== merged from test_binary_channel.py =====
def _h(root, rel):
    """sha256 of an installed test file — the receipt validator requires one hash per file."""
    import hashlib
    return hashlib.sha256((root / rel).read_bytes()).hexdigest()


def _svc(tmp_path, target="aarch64-trixie", monkeypatch=None):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    if monkeypatch is not None:
        monkeypatch.setattr(ControllerService, "binary_target", lambda self: target)
    return svc


def _receipt_for(svc, tmp_path, stack_id="daemon"):
    spec = svc.binary_spec(stack_id)
    files = []
    for rel in spec.proof_paths:
        p = tmp_path
        for seg in rel.split("/")[:-1]:
            p = p / seg
        p.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
        files.append(rel)
    return brx.BinaryReceipt(
        stack=stack_id, artifact_sha256="a" * 64, artifact_size=9,
        filename=f"{stack_id}-{'a' * 64}.tar.zst", url="https://example.invalid/a.tar.zst",
        components={c: "b" * 40 for c in spec.covers}, provenance={},
        files=tuple(files), file_hashes={r: _h(tmp_path, r) for r in files},
        proof_paths=tuple(files),
        registry_baseline={}, probe="ok")


def test_declared_stacks_offer_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    for sid in ("daemon", "meshtastic", "meshcom"):
        ok, why = svc.binary_available(sid)
        assert ok and why == ""
        assert svc.allowed_channels(sid)[0] == "binary"
        assert svc.default_channel(sid) == "binary"


def test_undeclared_stack_has_no_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    ok, why = svc.binary_available("kiss")
    assert not ok and "no prebuilt binary" in why
    assert svc.allowed_channels("kiss") == svc.SOURCE_CHOICES
    assert svc.default_channel("kiss") == "dev"


def test_unsupported_platform_disables_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, target="", monkeypatch=monkeypatch)
    ok, why = svc.binary_available("daemon")
    assert not ok and "not a supported binary target" in why
    assert svc.default_channel("daemon") == "dev"


def test_other_target_disables_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, target="aarch64-bookworm", monkeypatch=monkeypatch)
    ok, why = svc.binary_available("daemon")
    assert not ok and "aarch64-bookworm" in why


def test_channel_choices_keep_source_choices_pure():
    # adopt_source/the git planners must never be handed "binary".
    assert ControllerService.SOURCE_CHOICES == ("pinned", "dev", "stable")
    assert ControllerService.CHANNEL_CHOICES == ("pinned", "dev", "stable", "binary")


@pytest.mark.parametrize("channel,stack,ok", [
    ("pinned", "kiss", True), ("dev", "kiss", True), ("stable", "kiss", True),
    ("binary", "daemon", True),
    ("binary", "kiss", False),          # not declared
    ("bogus", "daemon", False),      # -> "invalid source" (historical wording)
])
def test_channel_error(tmp_path, monkeypatch, channel, stack, ok):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    err = svc.channel_error(stack, channel)
    assert (err == "") is ok


def test_receipt_drives_on_binary_channel(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.on_binary_channel("daemon") is False
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    assert svc.on_binary_channel("daemon") is True
    assert svc.binary_receipt_state("daemon")[0] == "valid"


def test_binary_covers_only_covered_components(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    assert svc.binary_covers("loraham-daemon") is True
    assert svc.binary_covers("radiolib") is True            # covered by the same artifact
    assert svc.binary_covers("loraham-kiss-tnc") is False   # different stack, no receipt


def test_stack_without_declaration_never_covers(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.binary_receipt_state("kiss") == ("absent", None, "")
    assert svc.binary_covers("loraham-kiss-tnc") is False


def test_superseded_receipt_is_not_on_binary_channel(tmp_path, monkeypatch):
    from lhpc.core import source_registry
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    rec = brx.BinaryReceipt(**{**rec.__dict__, "registry_baseline": {"src/loraham-daemon": ""}})
    assert brx.write_receipt(svc._paths, rec)
    assert svc.on_binary_channel("daemon") is True
    source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel="src/loraham-daemon", remote="https://example.invalid/d.git",
        selector="pinned", resolved_commit="c" * 40, adopted_at=1.0, txn_id="txn-x",
        strategy="adopt", components=("loraham-daemon",)))
    assert svc.binary_receipt_state("daemon")[0] == "superseded"
    assert svc.on_binary_channel("daemon") is False
    assert svc.binary_covers("loraham-daemon") is False


def test_block_reason_only_while_on_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.binary_block_reason("daemon", "build") == ""
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    msg = svc.binary_block_reason("daemon", "build")
    assert "prebuilt binary" in msg and "build" in msg


def test_open_journal_makes_the_receipt_unsafe(tmp_path, monkeypatch):
    """A receipt written INSIDE an unfinished transaction is not the truth yet: recovery may
    still unwind it, so status must not report the stack as binary-installed (audit finding)."""
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "valid"
    bi.open_txn(svc._paths, "daemon", "txnO")
    svc.invalidate_snapshot()
    state, rec, why = svc.binary_receipt_state("daemon")
    assert state == "unsafe" and rec is None and "interrupted" in why
    assert svc.on_binary_channel("daemon") is False


def test_open_journal_for_another_stack_does_not_shadow_this_one(tmp_path, monkeypatch):
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    bi.open_txn(svc._paths, "meshcom", "txnP")
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "valid"


def test_committed_journal_leaves_the_receipt_authoritative(tmp_path, monkeypatch):
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    bi.open_txn(svc._paths, "daemon", "txnQ")
    j, _st = bi.read_journal(svc._paths)
    assert bi.write_journal(svc._paths, {**j, "state": "committed"})
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "valid"


def test_unreadable_journal_reads_unsafe_for_every_stack(tmp_path, monkeypatch):
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    bi.journal_path(svc._paths).write_text("{broken")
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "unsafe"


def test_retire_recovers_an_interrupted_transaction_first(tmp_path, monkeypatch):
    """Retiring must act on the SETTLED install: an open transaction is recovered first, so a
    half-published artifact is never the thing that gets removed."""
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    bi.open_txn(svc._paths, "daemon", "txnR", old_receipt=brx.read_raw(svc._paths, "daemon"))
    svc.invalidate_snapshot()
    res = svc.binary_retire("daemon")
    assert res.ok, res.summary
    assert bi.read_journal(svc._paths)[1] == "absent"
    assert svc.binary_receipt_state("daemon")[0] == "absent"
    assert not (tmp_path / rec.files[0]).exists()


def test_retire_refuses_on_an_unrecoverable_journal(tmp_path, monkeypatch):
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    bi.journal_path(svc._paths).write_text("{broken")
    svc.invalidate_snapshot()
    res = svc.binary_retire("daemon")
    assert not res.ok and "unreadable" in res.summary


def test_forced_retire_discards_an_unrecoverable_journal(tmp_path, monkeypatch):
    """`clean --purge` is the escape hatch: it must still be able to remove every trace."""
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    bi.journal_path(svc._paths).write_text("{broken")
    svc.invalidate_snapshot()
    res = svc.binary_retire("daemon", force=True)
    assert res.ok, res.summary
    assert bi.read_journal(svc._paths)[1] == "absent"
    assert not (tmp_path / rec.files[0]).exists()


def test_doctor_names_a_broken_binary_install(tmp_path, monkeypatch):
    """A receipt whose files are gone (a source adoption of a shared checkout displaced them —
    live-found on the Zero) reads as an ordinary source state in `status`. `doctor` is where
    that must surface, with the reason and the command that fixes it."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    (tmp_path / rec.proof_paths[0]).unlink()          # what the source adoption did
    svc.invalidate_snapshot()
    out = svc.doctor()
    line = next((d for d in out.details if "binary install unsafe" in d), "")
    assert line and "daemon" in line
    assert any("lhpc install daemon --yes" in d for d in out.details)


def test_doctor_is_quiet_for_a_healthy_binary_install(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    svc.invalidate_snapshot()
    assert not any("binary install" in d for d in svc.doctor().details)


def _stub_pipeline(monkeypatch, svc):
    """Everything up to the download stubbed out (the index/target/pin checks are covered in
    test_binary_install.py) so these tests observe ONLY the receipt gate."""
    from lhpc.core import binary_install as bi
    entry = bi.IndexEntry(
        stack="daemon", filename="daemon-" + "a" * 64 + ".tar.zst",
        url="https://example.invalid/daemon-" + "a" * 64 + ".tar.zst", sha256="a" * 64,
        size=10, built_from="b" * 40, components=dict(svc._binary_pins("daemon")),
        runtime_deps=(), target="aarch64-trixie", os_name="trixie",
        provenance={"smoke": {"mode": "mandatory", "result": "passed"}})
    monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 2, "stacks": {}})
    monkeypatch.setattr(bi, "index_entry", lambda idx, sid: entry)
    monkeypatch.setattr(bi, "require_zstd", lambda: None)

    def _boom(*a, **k):
        raise bi.BinaryInstallError("DOWNLOAD-REACHED")
    monkeypatch.setattr(bi, "download_artifact", _boom)


def test_reinstall_repairs_a_drifted_receipt(tmp_path, monkeypatch):
    """A receipt whose artifact file vanished reads unsafe, and re-installing is the DOCUMENTED
    repair — it must not be refused (doctor points at exactly this command). Only an unreadable
    receipt blocks, because then we cannot know what the previous install owned."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    (tmp_path / rec.proof_paths[0]).unlink()
    svc.invalidate_snapshot()
    _stub_pipeline(monkeypatch, svc)
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and "DOWNLOAD-REACHED" in res.summary      # got past the receipt gate


def test_unreadable_receipt_still_blocks_a_reinstall(tmp_path, monkeypatch):
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    runtime_fs.mkdir(svc._paths, "state", "binary")
    brx.receipt_path(svc._paths, "daemon").write_text("{not json")
    svc.invalidate_snapshot()
    _stub_pipeline(monkeypatch, svc)
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and "malformed" in res.summary
    assert res.next_commands == ["lhpc clean daemon --purge --yes"]


def test_retire_removes_a_receipt_whose_file_is_gone(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    (tmp_path / rec.files[0]).unlink()
    svc.invalidate_snapshot()
    res = svc.binary_retire("daemon")
    assert res.ok, res.summary
    assert svc.binary_receipt_state("daemon")[0] == "absent"


def test_retire_still_refuses_a_modified_file(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    (tmp_path / rec.files[0]).write_bytes(b"operator edit")
    svc.invalidate_snapshot()
    res = svc.binary_retire("daemon")
    assert not res.ok and "changed since installation" in res.summary


def test_retire_removes_an_owned_directory(tmp_path, monkeypatch):
    """A provisioned venv is owned as a DIRECTORY (half of it is symlinks the per-file guard
    cannot touch) — retirement must take the whole thing, not leave a broken environment."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    venv = tmp_path / "build" / "tools" / "meshtastic-cli" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "meshtastic").write_bytes(b"x")
    (venv / "python3").symlink_to("/usr/bin/python3")
    import dataclasses
    rec = dataclasses.replace(rec, owned_dirs=("build/tools/meshtastic-cli",))
    assert brx.write_receipt(svc._paths, rec)
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "valid"     # owned_dirs round-trips
    res = svc.binary_retire("daemon")
    assert res.ok, res.summary
    assert not (tmp_path / "build" / "tools" / "meshtastic-cli").exists()


def test_receipt_with_an_escaping_owned_dir_is_unsafe(tmp_path, monkeypatch):
    import json
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    p = brx.receipt_path(svc._paths, "daemon")
    d = json.loads(p.read_text())
    d["owned_dirs"] = ["../../etc"]                 # every owned dir is rmtree'd at retirement
    p.write_text(json.dumps(d))
    svc.invalidate_snapshot()
    assert svc.binary_receipt_state("daemon")[0] == "unsafe"


def test_stale_paths_never_include_what_the_new_install_owns(tmp_path, monkeypatch):
    """The previous receipt may list a venv FILE-BY-FILE while the new one owns it as a
    DIRECTORY. Those paths are not stale: displacing them deleted the CLI the very same run had
    just provisioned, and the stack then refused to start (live-found on the Zero)."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    prev = ["src/loraham-daemon/loraham_daemon/daemon", "build/tools/x/.venv/bin/cli",
            "build/tools/x/.venv/pyvenv.cfg", "build/old/dropped"]
    new = ["src/loraham-daemon/loraham_daemon/daemon"]
    assert svc._stale_paths(prev, new, ["build/tools/x"]) == ["build/old/dropped"]
    # …with no owned directory, every path the new install lacks IS stale
    assert svc._stale_paths(prev, new, []) == [
        "build/old/dropped", "build/tools/x/.venv/bin/cli", "build/tools/x/.venv/pyvenv.cfg"]
    # a directory name must match on a SEGMENT boundary, never a prefix
    assert svc._stale_paths(["build/tools/xyz/f"], [], ["build/tools/x"]) == ["build/tools/xyz/f"]


def test_recovery_sweeps_staging_left_by_a_killed_run(tmp_path, monkeypatch):
    """A killed install (OOM, power cut) cannot clean up after itself, and each staging
    directory holds a whole artifact — tens of megabytes on an SD card (live-found)."""
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    runtime_fs.mkdir(svc._paths, "state")
    orphan = tmp_path / "state" / "lhpc-binary-deadbeef"
    (orphan / "stage").mkdir(parents=True)
    (orphan / "art.tar.zst").write_bytes(b"x" * 100)
    keep = tmp_path / "state" / "jobs"
    keep.mkdir()
    ok, why = svc.binary_recover()
    assert ok and why == ""
    assert not orphan.exists()
    assert keep.is_dir()                       # only our own staging prefix is swept


def test_recovery_fails_loudly_when_the_receipt_cannot_be_removed(tmp_path, monkeypatch):
    """There was no receipt before the run, so the failed install's one must go. If removal
    fails, the journal and backups MUST stay — dropping them discards the only evidence a
    later attempt could converge from (audit finding)."""
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    bi.open_txn(svc._paths, "daemon", "txnZ", old_receipt=None)     # nothing to restore
    monkeypatch.setattr(brx, "remove_receipt", lambda *a, **k: False)
    ok, why = svc.binary_recover()
    assert not ok and "receipt could not be removed" in why
    assert bi.read_journal(svc._paths)[1] == "valid"                # evidence retained
    monkeypatch.undo()
    assert svc.binary_recover()[0] is True                          # …and it converges later
    assert bi.read_journal(svc._paths)[1] == "absent"


# ===== merged from test_binary_receipt.py =====
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


# ===== merged from test_binary_status.py =====
def _svc_binary_status(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _install_binary(svc, tmp_path, stack="daemon", sha="ab" * 32, commits=None):
    spec = svc.binary_spec(stack)
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
    comps = commits or {c: "cd" * 20 for c in spec.covers}
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack=stack, artifact_sha256=sha, artifact_size=9,
        filename=f"{stack}-{sha}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=comps, provenance={"lhpc_commit": "ee" * 20},
        files=tuple(spec.proof_paths),
        file_hashes={r: _h(tmp_path, r) for r in spec.proof_paths},
        proof_paths=tuple(spec.proof_paths), registry_baseline={}, probe="ok"))
    svc._snapshot_state.cache = None


def _cs(svc, stack, cid):
    return svc.build_snapshot().stack(stack).components[cid]


def test_without_receipt_source_reads_missing(tmp_path, monkeypatch):
    svc = _svc_binary_status(tmp_path, monkeypatch)
    st = _cs(svc, "daemon", "loraham-daemon")
    assert st.source_state is SourceState.MISSING
    assert st.run_state.value == "not-installed"


def test_binary_receipt_gives_binary_state_and_provenance(tmp_path, monkeypatch):
    svc = _svc_binary_status(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    st = _cs(svc, "daemon", "loraham-daemon")
    assert st.source_state is SourceState.BINARY
    assert st.source_version == "binary@" + ("ab" * 32)[:9]
    assert st.source_head == "cd" * 20                      # the artifact's component commit
    assert st.run_state.value != "not-installed"            # THE bug this branch prevents


def test_every_covered_component_reports_binary(tmp_path, monkeypatch):
    # RadioLib has no clone at all in binary mode; it must not read "missing".
    svc = _svc_binary_status(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    assert _cs(svc, "daemon", "radiolib").source_state is SourceState.BINARY


def test_uncovered_stacks_are_untouched(tmp_path, monkeypatch):
    svc = _svc_binary_status(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    assert _cs(svc, "kiss", "loraham-kiss-tnc").source_state is SourceState.MISSING


def test_superseded_receipt_falls_back_to_git_probe(tmp_path, monkeypatch):
    from lhpc.core import source_registry
    svc = _svc_binary_status(tmp_path, monkeypatch)
    spec = svc.binary_spec("daemon")
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack="daemon", artifact_sha256="ab" * 32, artifact_size=9,
        filename="daemon-x.tar.zst", url="https://example.invalid/a", components={},
        provenance={}, files=tuple(spec.proof_paths),
        file_hashes={r: _h(tmp_path, r) for r in spec.proof_paths},
        proof_paths=tuple(spec.proof_paths),
        registry_baseline={"src/loraham-daemon": ""}, probe="ok"))
    svc._snapshot_state.cache = None
    assert _cs(svc, "daemon", "loraham-daemon").source_state is SourceState.BINARY
    source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel="src/loraham-daemon", remote="https://example.invalid/d.git",
        selector="pinned", resolved_commit="c" * 40, adopted_at=1.0, txn_id="txn-1",
        strategy="adopt", components=("loraham-daemon",)))
    svc._snapshot_state.cache = None
    # receipt superseded -> the ordinary git probe answers again
    assert _cs(svc, "daemon", "loraham-daemon").source_state is not SourceState.BINARY


def test_status_versions_shows_artifact_provenance(tmp_path, monkeypatch):
    svc = _svc_binary_status(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    res = svc.status_versions()
    line = next(d for d in res.details if "loraham-daemon" in d)
    assert "binary" in line and "binary@" in line and "built_from=" in line


def test_status_versions_unchanged_for_source_stacks(tmp_path, monkeypatch):
    svc = _svc_binary_status(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    line = next(d for d in svc.status_versions().details if "loraham-kiss-tnc" in d)
    assert "pin=" in line and "tag=" in line and "binary@" not in line


def test_web_pill_renders_provenance(tmp_path, monkeypatch):
    from lhpc.adapters.web.app import create_app
    svc = _svc_binary_status(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    client = create_app(service_factory=lambda: svc).test_client()
    # the source pill lives in the stack SUMMARY row on the overview page
    body = client.get("/stacks").get_data(as_text=True)
    assert "src: binary" in body and "binary@" + ("ab" * 32)[:9] in body
    # ONE version pill in the row (the version cell); the artifact tooltip rides on it
    assert body.count("binary@" + ("ab" * 32)[:9]) == 1
    assert "verified prebuilt artifact" in body


# ===== merged from test_binary_predicates.py =====
def _svc_binary_predicates(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _install_daemon_binary(svc, tmp_path):
    """Lay down exactly what the artifact lays down (daemon binary only — NO clone, no
    RadioLib) plus the receipt."""
    spec = svc.binary_spec("daemon")
    for rel in spec.proof_paths:
        p = tmp_path
        for seg in rel.split("/")[:-1]:
            p = p / seg
        p.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
    rec = brx.BinaryReceipt(
        stack="daemon", artifact_sha256="a" * 64, artifact_size=10,
        filename=f"daemon-{'a' * 64}.tar.zst", url="https://example.invalid/d.tar.zst",
        components={c: "b" * 40 for c in spec.covers}, provenance={},
        files=tuple(spec.proof_paths),
        file_hashes={r: _h(tmp_path, r) for r in spec.proof_paths},
        proof_paths=tuple(spec.proof_paths),
        registry_baseline={}, probe="loraham_daemon 1.0")
    assert brx.write_receipt(svc._paths, rec)


def _comp(svc, cid):
    for st in svc.stacks():
        for c in st.components:
            if c.id == cid:
                return c
    raise AssertionError(cid)


def test_is_built_is_unchanged_for_binary_artifacts(tmp_path, monkeypatch):
    # The artifact lands exactly at the manifest `bin` path, so the PHYSICAL probe answers
    # "built" with no receipt involvement at all.
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    assert svc.is_built(_comp(svc, "loraham-daemon")) is True


def test_install_blocker_accepts_covered_component_without_clone(tmp_path, monkeypatch):
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    daemon = _comp(svc, "loraham-daemon")
    assert "not installed" in svc.install_blocker(daemon)     # nothing there yet
    _install_daemon_binary(svc, tmp_path)
    assert svc.install_blocker(daemon) == ""                  # binary-covered: no clone needed


def test_install_blocker_unchanged_for_uncovered_component(tmp_path, monkeypatch):
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    kiss = _comp(svc, "loraham-kiss-tnc")
    assert "not installed" in svc.install_blocker(kiss)       # different stack, still source


def test_auto_install_installed_predicate_ignores_covered_clones(tmp_path, monkeypatch):
    # RadioLib has NO clone in binary mode; the stack must still read "installed".
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    st = svc.stack("daemon")
    assert svc._auto_install_stack_installed(st) is False
    _install_daemon_binary(svc, tmp_path)
    assert svc._auto_install_stack_installed(st) is True


def test_predicates_revert_when_receipt_retired(tmp_path, monkeypatch):
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    assert svc._auto_install_stack_installed(svc.stack("daemon")) is True
    assert brx.remove_receipt(svc._paths, "daemon")
    # without the receipt the source-dir requirement is back (RadioLib is missing)
    assert svc._auto_install_stack_installed(svc.stack("daemon")) is False


def test_build_refused_on_binary_stack(tmp_path, monkeypatch):
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    res = svc.build("daemon", apply=True)
    assert not res.ok and "prebuilt binary" in res.summary
    assert res.data.get("binary_channel") is True
    assert any("--source pinned" in c for c in res.next_commands)


def test_host_test_refused_on_binary_stack(tmp_path, monkeypatch):
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    res = svc.test("daemon", apply=True)
    assert not res.ok and "host tests" in res.summary
    assert res.data.get("skipped") == "binary-install"


def test_build_and_test_allowed_without_receipt(tmp_path, monkeypatch):
    # No receipt -> the historical behaviour must be byte-identical (these fail for the
    # ordinary "not installed" reasons, never the binary refusal).
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    for res in (svc.build("daemon", apply=False), svc.test("daemon", apply=False)):
        assert "prebuilt binary" not in res.summary


def test_web_job_refuses_build_on_binary_stack(tmp_path, monkeypatch):
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    _job, state, reason = svc.spawn_web_job("build", "daemon")
    assert state == "blocked" and "prebuilt binary" in reason


def test_web_job_accepts_binary_channel_for_install(tmp_path, monkeypatch):
    # The web install path must accept the new channel (and still reject nonsense).
    svc = _svc_binary_predicates(tmp_path, monkeypatch)
    _job, state, reason = svc.spawn_web_job("install", "kiss", source="binary")
    assert state == "blocked" and "binary channel unavailable" in reason
    _job2, state2, reason2 = svc.spawn_web_job("install", "daemon", source="bogus")
    assert state2 == "blocked" and "invalid source" in reason2


# ===== merged from test_binary_hmac_firewall.py =====
def _svc_binary_hmac_firewall(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _lay_down(svc, tmp_path, stack="meshcom"):
    import hashlib
    spec = svc.binary_spec(stack)
    files, hashes = [], {}
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
        files.append(rel)
        hashes[rel] = hashlib.sha256(b"ELF").hexdigest()
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack=stack, artifact_sha256="ab" * 32, artifact_size=9,
        filename=f"{stack}-{'ab' * 32}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=dict(svc._binary_pins(stack)), provenance={}, files=tuple(files),
        file_hashes=hashes, proof_paths=tuple(spec.proof_paths), registry_baseline={},
        probe="qemu 9.0"))
    svc.invalidate_snapshot()


def test_hmac_applies_stays_true_but_blocks_with_reason(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    # NOT flipped to "does not apply" — the UI must show the reason
    assert svc.hmac_applies("meshcom") is True
    reason = svc.hmac_binary_block("meshcom")
    assert "NO mesh password" in reason and "open auth" in reason


def test_hmac_apply_start_refused(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    res = svc.hmac_apply_start("meshcom", "enable")
    assert not res.ok and res.data.get("binary_channel") is True
    assert any("--source pinned" in c for c in res.next_commands)


def test_hmac_cli_refused(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    lines = []
    assert svc.hmac_apply_cli("meshcom", "enable", lines.append) == 1
    assert any("NO mesh password" in ln for ln in lines)


def test_hmac_driver_refused_authoritatively(tmp_path, monkeypatch):
    # The shared step runner gates too — a CLI/web-only check could be bypassed.
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    lines = []
    assert svc._hmac_run_steps("meshcom", "enable", "f" * 32, lines.append) == 1
    assert any("refused" in ln and "NO mesh password" in ln for ln in lines)


def test_hmac_set_secret_enable_refused_disable_allowed(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    assert svc.hmac_set_secret("meshcom", "enable").ok is False
    # `disable` is what the binary install itself performs — it must stay available
    assert "NO mesh password" not in svc.hmac_set_secret("meshcom", "disable").summary


def test_hmac_unblocked_without_receipt(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    assert svc.hmac_binary_block("meshcom") == ""


def test_hmac_page_shows_reason(tmp_path, monkeypatch):
    from lhpc.adapters.web.app import create_app
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    client = create_app(service_factory=lambda: svc).test_client()
    body = client.get("/stacks/meshcom/hmac/enable").get_data(as_text=True)
    assert "not available" in body and "NO mesh password" in body
    assert "--source pinned" in body


def _bridge_scope(svc):
    """Resolve the meshcom bridge listener's firewall scope directly — a loopback-bound
    listener is not offered as a selectable candidate row, but its AUTH classification is what
    the exposure model reasons with."""
    st = svc.stack("meshcom")
    for comp in st.components:
        for ep in comp.endpoints:
            if ep.kind == "tcp" and ep.role == "listener" and ep.firewall:
                return svc._fw_resolve_scope(st, comp, ep)
    return None


def test_bridge_listener_is_password_auth_on_source(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    ep = _bridge_scope(svc)
    assert ep is not None and ep["auth"] == "password"


def test_bridge_listener_is_open_auth_on_binary(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    ep = _bridge_scope(svc)
    assert ep is not None and ep["auth"] == "none"


def test_clean_force_retires_binary(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    proof = tmp_path / svc.binary_spec("meshcom").proof_paths[0]
    assert proof.exists()
    svc.clean("meshcom", apply=True, purge=True)
    assert not proof.exists()
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "absent"


def test_uninstall_retires_binary(tmp_path, monkeypatch):
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path, stack="daemon")
    proof = tmp_path / svc.binary_spec("daemon").proof_paths[0]
    assert proof.exists()
    res = svc.uninstall("daemon", apply=True)
    assert not proof.exists()
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"
    assert any("binary" in d for d in res.details)


def test_clean_keeps_the_binary_when_a_component_started_mid_flight(tmp_path, monkeypatch):
    """`clean` retires the artifact FORCEFULLY, so it must happen only after the authoritative
    post-lock running recheck — otherwise a start that slipped in loses its binary and the
    clean then aborts with nothing else done (audit finding)."""
    from lhpc.core.model import RunState
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path, stack="daemon")
    proof = tmp_path / svc.binary_spec("daemon").proof_paths[0]
    real = svc.build_snapshot

    def _started(fresh=False):
        snap = real(fresh=fresh)
        if fresh:                                  # the UNDER-LOCK read sees it running
            for ss in snap.stacks:
                for cid, cs in ss.components.items():
                    if cid == "loraham-daemon":
                        cs.run_state = RunState.RUNNING
        return snap
    monkeypatch.setattr(svc, "build_snapshot", _started)
    res = svc.clean("daemon", apply=True, purge=True)
    assert not res.ok and "started while" in res.summary
    assert proof.exists()                          # ZERO mutation
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"


def test_interrupted_install_restores_the_mesh_password(tmp_path, monkeypatch):
    """The install switches meshcom to open auth BEFORE downloading. The journal is opened
    first and carries the previous value, so an interrupted run puts password auth back —
    the crash used to leave the bridge open with nothing to recover from (audit finding)."""
    from lhpc.core import binary_install as bi
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    r = svc.hmac_set_secret("meshcom", "enable")
    assert r.ok, r.summary
    before = svc._resolved_param_value("meshcom", "run",
                                       svc._hmac_component("meshcom").id, "password_file")
    assert before
    # exactly what binary_install does before touching anything else
    bi.open_txn(svc._paths, "meshcom", "txnH", old_receipt=None,
                auth={"param": "password_file", "previous": before})
    assert svc.save_config_bundle("meshcom", values={"password_file": ""},
                                  _allow_managed_params=frozenset({"password_file"})).ok
    svc.invalidate_snapshot()
    assert svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id, "password_file") == ""
    ok, why = svc.binary_recover()                    # …the box comes back up
    assert ok, why
    assert svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id,
                                     "password_file") == before
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_committed_transaction_keeps_open_auth(tmp_path, monkeypatch):
    """Past the commit point the NEW install is the truth: recovery must NOT put the password
    back (the installed firmware has none)."""
    from lhpc.core import binary_install as bi
    svc = _svc_binary_hmac_firewall(tmp_path, monkeypatch)
    assert svc.hmac_set_secret("meshcom", "enable").ok
    prev = svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id, "password_file")
    bi.open_txn(svc._paths, "meshcom", "txnI", auth={"param": "password_file",
                                                     "previous": prev})
    assert svc.save_config_bundle("meshcom", values={"password_file": ""},
                                  _allow_managed_params=frozenset({"password_file"})).ok
    j, _st = bi.read_journal(svc._paths)
    assert bi.write_journal(svc._paths, {**j, "state": "committed"})
    ok, why = svc.binary_recover()
    assert ok, why
    svc.invalidate_snapshot()
    assert svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id, "password_file") == ""


def test_welcome_banner_does_not_call_a_binary_install_an_unmanaged_tree(tmp_path, monkeypatch):
    """A binary artifact publishes INTO the component's source path and adopts no source, so there
    is no ownership record by design. Calling that an "unmanaged tree" told the operator to "move
    it away or Clean" — which would destroy a working binary install (live-found on a fresh Zero)."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rec = _receipt_for(svc, tmp_path)
    assert brx.write_receipt(svc._paths, rec)
    # the artifact's own file under the daemon's source path, with NO registry record
    src = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    src.mkdir(parents=True, exist_ok=True)
    (src / "loraham_daemon").write_bytes(b"ELF")
    svc.invalidate_snapshot()
    assert svc.auto_install_welcome() is None, "a covered binary install is a managed install"


def test_welcome_banner_still_flags_a_truly_unmanaged_tree(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    d = tmp_path / "src" / "loraham-daemon"
    d.mkdir(parents=True)
    (d / "somebody-elses-checkout").write_text("x")
    svc.invalidate_snapshot()
    w = svc.auto_install_welcome()
    assert w and w["fresh"] is False and "unmanaged tree" in w["recovery"]


def _unmet(svc, sid):
    g = svc.deps_report(sid)
    return {d.label for d in g["system"] + g["build"] if not d.satisfied}


def test_binary_channel_drops_build_only_prerequisites(tmp_path, monkeypatch):
    """`build` is REFUSED on the binary channel, so demanding its inputs is asking the operator
    for something they cannot act on. A fresh Zero was told to install a RadioLib checkout and
    PlatformIO for stacks it had just installed as binaries (live-found)."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    before = _unmet(svc, "daemon")
    assert any("radiolib source checkout" in lbl for lbl in before)
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    svc.invalidate_snapshot()
    after = _unmet(svc, "daemon")
    assert not any("radiolib source checkout" in lbl for lbl in after), after
    # the RUNTIME/system prerequisites are untouched — they still gate a start
    assert len(after) == len(before) - 1


def test_binary_channel_separates_build_tools_from_artifact_delivered_runtime_deps(
        tmp_path, monkeypatch):
    """The dependency report and the START gate must classify a provisioned requirement the SAME
    way (audit): a pure BUILD tool the artifact never ships is irrelevant on the binary channel,
    while a path the receipt OWNS is a real, missing runtime dependency — cheap receipt validation
    only restats proof paths, so nothing else would notice it is gone."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    _QEMU = "qemu-system-xtensa (headless"      # PROVISIONED *and* a receipt proof path
    _PIO = "PlatformIO CLI"                     # PROVISIONED build tool, never shipped
    before = _unmet(svc, "meshcom")
    assert any(lbl.startswith(_QEMU) for lbl in before), before

    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path, stack_id="meshcom"))
    svc.invalidate_snapshot()
    assert svc.on_binary_channel("meshcom")
    report = [d for g in svc.deps_report("meshcom").values() for d in g]
    after = [d.label for d in report if not d.satisfied]

    assert not any(lbl.startswith(_PIO) for lbl in after), after      # build-only -> irrelevant
    comp_q = next(c for c in svc.stack("meshcom").components if c.id == "meshcom-qemu")
    pio_req = next(r for r in comp_q.requires if (r.note or "").startswith(_PIO))
    assert svc.binary_requirement_class("meshcom-qemu", pio_req) == "irrelevant"
    qemu = next(d for d in report if d.label.startswith(_QEMU))
    assert not qemu.satisfied                                         # artifact-owned -> still real
    assert "reinstall the binary artifact" in qemu.detail
    assert qemu.install_cmd == "lhpc install meshcom --source binary --yes"

    # ...and the start gate says exactly the same thing, with the same command.
    blocking = svc.start_blocking_requirements(comp_q)
    qreq = next(r for r in blocking if (r.note or "").startswith("binary-managed runtime"))
    assert qreq.install == qemu.install_cmd
    assert not any((r.note or "").startswith("PlatformIO") for r in blocking)
    assert any("libpixman-1-dev" in lbl for lbl in after)   # plain packages untouched


def test_source_channel_still_demands_its_build_inputs(tmp_path, monkeypatch):
    """The suppression is conditional — a source-channel stack must still be told what it needs."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert any("radiolib source checkout" in lbl for lbl in _unmet(svc, "daemon"))
    assert any(lbl.startswith("qemu-system-xtensa (headless")
               for lbl in _unmet(svc, "meshcom"))


def test_is_installed_rests_on_the_receipt_not_on_a_lucky_directory(tmp_path, monkeypatch):
    """Every artifact today happens to create something under the main source path, so the
    directory probe is accidentally right. Make it intentional: with a valid receipt the stack is
    installed even if no source directory exists at all."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    assert svc.is_installed("daemon") is False
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    svc.invalidate_snapshot()
    assert svc.is_installed("daemon") is True


def test_doctor_counts_binary_covered_sources_as_provided_not_missing(tmp_path, monkeypatch):
    """A binary-covered component has no checkout BY DESIGN. Counting it as a missing source made
    a healthy binary install read half-installed (live-found on a fresh box)."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    before = next(d for d in svc.doctor().details if "configured sources" in d)
    assert "missing" in before
    assert brx.write_receipt(svc._paths, _receipt_for(svc, tmp_path))
    svc.invalidate_snapshot()
    after = next(d for d in svc.doctor().details if "configured sources" in d)
    assert "provided by a binary artifact" in after


def test_prune_empty_dirs_clears_a_whole_emptied_tree(tmp_path, monkeypatch):
    """Retiring an artifact must leave no empty skeleton behind: a parent is tried only after
    its children (live-found — build/tools/meshtasticd/web/i18n/locales survived a retire).
    Shared and non-empty directories stay, and so does the runtime root's own top level."""
    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    rels = ["build/tools/mt/web/i18n/locales/de.json",      # short name, deep
            "build/tools/mt/web/static/a-very-long-file-name.js",   # long name, shallower
            "build/tools/mt/bin/mt",
            "src/checkout/keep/binary-file"]                # sits next to a foreign file
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (tmp_path / "src/checkout/keep/NOT-OURS").write_bytes(b"x")
    (tmp_path / "build/tools/other").mkdir(parents=True, exist_ok=True)
    for rel in rels:
        (tmp_path / rel).unlink()
    svc._prune_empty_dirs(rels)
    assert not (tmp_path / "build/tools/mt").exists()        # the whole emptied tree is gone
    assert (tmp_path / "src/checkout/keep").is_dir()         # foreign file -> untouched
    assert (tmp_path / "build/tools/other").is_dir()         # sibling -> untouched
    assert (tmp_path / "build").is_dir()                     # runtime skeleton -> never pruned


def test_start_gate_uses_the_shared_classifier(tmp_path, monkeypatch):
    """`start_blocking_requirements()` returns real `Requirement` objects (downstream renderers
    depend on that) and drops only what the shared classifier calls irrelevant."""
    from lhpc.core.model import Requirement

    class _Comp:
        id = "meshcom-qemu"

    svc = _svc(tmp_path, monkeypatch=monkeypatch)
    owned = Requirement(check_file="{runtime}/build/tool-cache/x/qemu", provisioned=True,
                        install="lhpc build meshcom", note="QEMU")
    build_only = Requirement(check_file="{runtime}/build/tools/platformio/.venv/bin/pio",
                             provisioned=True, install="lhpc build meshcom", note="PlatformIO CLI")
    plain = Requirement(check_file="/usr/include/pixman-1/pixman.h", install="sudo apt install x",
                        note="pixman headers")
    monkeypatch.setattr(type(svc._lifecycle()), "missing_requirements",
                        lambda self, comp: [owned, build_only, plain], raising=False)
    monkeypatch.setattr(ControllerService, "binary_covers", lambda self, cid: True)
    monkeypatch.setattr(ControllerService, "binary_requirement_class",
                        lambda self, cid, req: {"QEMU": "artifact-missing",
                                                "PlatformIO CLI": "irrelevant"}.get(
                                                    req.note, "blocker"))
    out = svc.start_blocking_requirements(_Comp())
    assert [r.note for r in out] == [svc.ARTIFACT_MISSING_NOTE, "pixman headers"]
    assert out[0].install == "lhpc install meshcom --source binary --yes"   # artifact remedy
    assert out[1].install == "sudo apt install x"                           # untouched


"""Binary CHANNEL resolution + coverage predicates (B3).

The channel is a fourth selector, never a persisted preference: declaration + platform decide
availability, a valid receipt decides "is it on binary now", and SOURCE_CHOICES stays 3-valued
so the git planners can never see "binary".
"""

import pytest

from lhpc.core import binary_receipt as brx
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService



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


# --- capability ---------------------------------------------------------------------------------

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


# --- current state ------------------------------------------------------------------------------

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


# --- an OPEN transaction makes the receipt non-authoritative ------------------------------------

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

"""U9 (D10) gate-1 fix: the "nothing to install" decision comes FIRST. An installed binary the index offers nothing
newer than (also when its own entry was withdrawn) stays, untouched: no clone is moved, no package is asked for,
and its files are verified whatever the published entry's age; changed files are a refusal (never an older
replacement), with the source install as the way out. Driven through the public install / update paths."""
from __future__ import annotations

import dataclasses
import json

import pytest

from lhpc.core import binary_install as bi, binary_receipt as brx, reslock, source_registry
from lhpc.core.install import DirtyReport
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService

TARGET = "aarch64-trixie"
INSTALLED, OLDER, NEWER = "2026-11-10T03:51:59Z", "2026-11-09T17:54:53Z", "2026-11-11T00:00:00Z"


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: TARGET)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _entry(sid, sha, commit, published, deps=()):
    filename = f"{sid}-{sha}.tar.zst"
    svc = ControllerService()
    comps = {cid: commit for cid in svc._binary_pins(sid)}
    return {"filename": filename, "url": f"https://example.invalid/{filename}", "sha256": sha, "size": 10,
            "built_from": "b" * 40, "components": comps, "lhpc_commit": "c" * 40, "builder_commit": "d" * 40,
            "target": TARGET, "os": "trixie", "container_digest": "debian@sha256:" + "e" * 64,
            "runtime_deps": list(deps), "smoke": {"mode": "mandatory", "result": "passed"},
            "extract_to": "runtime-root", "inputs": {}, "published_at": published}


def _serve(monkeypatch, sid, *entries):
    index = {"schema": 3, "stacks": {sid: list(entries)}}
    monkeypatch.setattr(bi, "_http_get", lambda url, max_bytes: json.dumps(index).encode())
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: (_ for _ in ()).throw(
        bi.BinaryInstallError("DOWNLOAD-REACHED")))


def _installed(svc, binary_receipt, sid, commit="b" * 40):
    return binary_receipt(svc, sid, sha="b" * 64, commits={c: commit for c in svc._binary_pins(sid)},
                          provenance={"published_at": INSTALLED})


def _clean_clone_at(monkeypatch, svc, commit):
    """meshcom-qemu's checkout present, proven ours, clean, at `commit`; a move or adoption is recorded."""
    comp = svc.stack("meshcom").component("meshcom-qemu")
    svc._paths.resolve_source(comp.source.path).mkdir(parents=True, exist_ok=True)
    moved = []

    class _Rec:
        resolved_commit = commit

    class _Installer:
        def dirty_report(self, d, path):
            return DirtyReport()

        def adopt_source(self, c, **kw):
            moved.append(("adopt", c.id))
            raise AssertionError("must not adopt")

        def _index_key(self):                    # the source-transaction seams the locks read (as the
            return "source-txn-index"            # stale-clone fixture in test_binary_install.py)

        def _recover_scan(self):
            return None

        def _pending_journals(self):
            return []

    monkeypatch.setattr(ControllerService, "_installer", lambda self: _Installer())
    monkeypatch.setattr(source_registry, "verify_identity", lambda *a, **k: (_Rec(), ""))
    monkeypatch.setattr(ControllerService, "update", lambda self, target="", **kw: (
        moved.append(("update", target)), ActionResult(True, "moved"))[1])
    return moved


def test_a_withdrawn_installed_binary_keeps_its_clone_where_it_is(tmp_path, monkeypatch, binary_receipt):
    # B1: B installed (its clone at B's commit), B withdrawn, only the older A listed.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    moved = _clean_clone_at(monkeypatch, svc, "b" * 40)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, OLDER))
    for apply in (False, True):                  # `update` is stubbed (the clone move): the install path
        res = svc.install("meshcom", apply=apply, source="binary")
        assert res.ok and res.data["changes"] == 0, res.summary
    assert moved == []
    assert svc.binary_receipt_state("meshcom")[1].artifact_sha256 == "b" * 64


def test_a_changed_installed_binary_with_nothing_newer_is_refused_not_replaced(tmp_path, monkeypatch,
                                                                               binary_receipt):
    # B2: B's files changed since its install; only the older A listed: refused, B and its files left as they are.
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "daemon")
    changed = tmp_path / rec.files[0]
    changed.write_bytes(b"EDIT")
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, OLDER))
    for apply in (False, True):
        res = svc.update("daemon", apply=apply, source="binary")
        assert not res.ok, res.summary
        assert f"the installed binary {rec.filename} changed since its install" in res.summary
        assert "lhpc install daemon --source pinned --yes" in res.next_commands
    assert changed.read_bytes() == b"EDIT"
    assert brx.receipt_state(svc._paths, "daemon")[1].artifact_sha256 == "b" * 64
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_the_refusal_leaves_the_installed_files_and_the_clone_untouched(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "meshcom")
    moved = _clean_clone_at(monkeypatch, svc, "b" * 40)
    before = {rel: (tmp_path / rel).read_bytes() for rel in rec.files}
    (tmp_path / rec.files[0]).write_bytes(b"EDIT")
    before[rec.files[0]] = b"EDIT"
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, OLDER))
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "changed since its install" in res.summary
    assert moved == []
    assert {rel: (tmp_path / rel).read_bytes() for rel in rec.files} == before


def test_a_newer_entry_still_replaces_a_changed_installed_binary(tmp_path, monkeypatch, binary_receipt):
    # Control: a newer entry is the repair.
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "daemon")
    (tmp_path / rec.files[0]).write_bytes(b"EDIT")
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, NEWER))
    res = svc.update("daemon", apply=True, source="binary")
    assert "DOWNLOAD-REACHED" in res.summary, res.summary


def test_an_older_entrys_packages_are_not_asked_for(tmp_path, monkeypatch, binary_receipt):
    # N3: nothing changes, so the older entry's runtime dependency is not a refusal.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "daemon")
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, pkg: pkg != "libonly-a1")
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, OLDER, deps=("libonly-a1",)))
    for apply in (False, True):
        res = svc.update("daemon", apply=apply, source="binary")
        assert res.ok and res.data["changes"] == 0, res.summary


def test_after_an_interrupted_transaction_the_locked_decision_keeps_the_clone(tmp_path, monkeypatch, binary_receipt):
    # A transaction opened over B and interrupted: before the locks nothing is decided (and no clone moved); the
    # recovery restores B, then the decision under the locks keeps B and its clone.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    assert svc.binary_receipt_state("meshcom")[0] == "unsafe"
    moved = _clean_clone_at(monkeypatch, svc, "b" * 40)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, OLDER))
    res = svc.install("meshcom", apply=True, source="binary")
    assert res.ok and res.data["changes"] == 0, res.summary
    assert moved == [] and bi.read_journal(svc._paths)[1] == "absent"
    assert svc.binary_receipt_state("meshcom")[1].artifact_sha256 == "b" * 64


def test_after_an_interrupted_transaction_changed_files_are_refused_under_the_locks(tmp_path, monkeypatch,
                                                                                   binary_receipt):
    # The locks decide (an open transaction): the recovery restores B, whose files changed, and only an older
    # entry is listed: the refusal, not an install of the older one.
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "daemon")
    (tmp_path / rec.files[0]).write_bytes(b"EDIT")
    bi.open_txn(svc._paths, "daemon", "t" * 16, old_receipt=brx.read_raw(svc._paths, "daemon"), auth={})
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, OLDER))
    res = svc.update("daemon", apply=True, source="binary")
    assert not res.ok and f"the installed binary {rec.filename} changed since its install" in res.summary
    assert (tmp_path / rec.files[0]).read_bytes() == b"EDIT"
    assert brx.receipt_state(svc._paths, "daemon")[1].artifact_sha256 == "b" * 64


def _before_the_lock(monkeypatch, concurrent):
    """Barrier (no sleeps): `concurrent()` runs when the apply reaches its first lock, i.e. between the start of the
    install and its locks, as a transaction of another process would. Returns the list of barrier hits."""
    hits = []
    real_admit = ControllerService._admit

    def _barrier(self, *a, **k):
        if not hits:                             # the first lock; a nested acquire re-enters it
            hits.append(1)
            concurrent()
        return real_admit(self, *a, **k)
    monkeypatch.setattr(ControllerService, "_admit", _barrier)
    return hits


def test_a_receipt_removed_before_the_locks_gets_the_locked_decision(tmp_path, monkeypatch, binary_receipt):
    # B installed, only the older A listed: the plan reads "nothing to install". A concurrent retire removes B
    # before the apply's locks: the locked decision (nothing installed) installs A, not the stale no-op.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "daemon")
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, OLDER))
    assert svc.update("daemon", apply=False, source="binary").data["changes"] == 0
    hits = _before_the_lock(monkeypatch, lambda: brx.remove_receipt(svc._paths, "daemon"))
    res = svc.update("daemon", apply=True, source="binary")
    assert hits == [1] and "DOWNLOAD-REACHED" in res.summary, res.summary


def test_a_transaction_opened_before_the_locks_is_recovered_then_decided(tmp_path, monkeypatch, binary_receipt):
    # B installed with intact files, only the older A listed. Before the apply's locks a concurrent transaction
    # opens over B and changes a file, then dies: under the locks the recovery runs first, then the decision on
    # the recovered B (changed files, nothing newer) refuses; the stale snapshot would have read "nothing to do".
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "daemon")
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, OLDER))

    def _concurrent():
        bi.open_txn(svc._paths, "daemon", "t" * 16, old_receipt=brx.read_raw(svc._paths, "daemon"), auth={})
        (tmp_path / rec.files[0]).write_bytes(b"EDIT")
    hits = _before_the_lock(monkeypatch, _concurrent)
    res = svc.update("daemon", apply=True, source="binary")
    assert hits == [1] and not res.ok, res.summary
    assert f"the installed binary {rec.filename} changed since its install" in res.summary
    assert bi.read_journal(svc._paths)[1] == "absent"
    assert (tmp_path / rec.files[0]).read_bytes() == b"EDIT"


def test_a_newer_entrys_missing_packages_are_refused_before_any_download(tmp_path, monkeypatch, binary_receipt):
    # Control for N3: when the entry WOULD replace B, its missing package is the refusal, in the plan and in
    # the apply (decided under the locks), with the apt command named and nothing downloaded.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "daemon")
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, pkg: pkg != "libonly-a1")
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, NEWER, deps=("libonly-a1",)))
    for apply in (False, True):
        res = svc.update("daemon", apply=apply, source="binary")
        assert not res.ok and "needs system packages" in res.summary, res.summary
        assert res.data["missing_runtime_deps"] == ["libonly-a1"]
        assert "sudo apt install -y libonly-a1" in res.next_commands


def _moving_clone(monkeypatch, svc, commit):
    """meshcom-qemu's checkout present, proven ours, clean, at `commit`; the adoption really moves it (to its
    `pinned_expected` commit), is recorded, and writes the path's ownership record with a new transaction id, as
    the real adoption does."""
    comp = svc.stack("meshcom").component("meshcom-qemu")
    svc._paths.resolve_source(comp.source.path).mkdir(parents=True, exist_ok=True)
    state = {"commit": commit, "adopted": []}

    class _Rec:
        @property
        def resolved_commit(self):
            return state["commit"]

    class _Done:
        status, detail = "done", ""

    class _Installer:
        def dirty_report(self, d, path):
            return DirtyReport(tracked=list(state.get("dirty", ())))

        def adopt_source(self, c, *, pinned_expected=None, **kw):
            state["adopted"].append(pinned_expected[0])
            if "lock_held" in state:                # is this path's source lock held while it moves?
                state["lock_held"].append(_lock_busy(svc, c.source.path))
            if state.get("refuse"):                 # a failed adoption changes nothing
                return type("_Failed", (), {"status": "failed", "detail": state["refuse"]})()
            state["commit"] = pinned_expected[0]
            assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
                source_rel=c.source.path, remote="", selector="pinned", resolved_commit=pinned_expected[0],
                adopted_at=1.0, txn_id=f"adopt-{len(state['adopted'])}", components=(c.id,)))
            if state.get("corrupt"):                 # the record this move wrote is damaged
                _corrupt_record(svc, c.source.path)
            return _Done()

        def _index_key(self):
            return "source-txn-index"

        def _recover_scan(self):
            return None

        def _pending_journals(self):
            return state["pending"]() if "pending" in state else []

    monkeypatch.setattr(ControllerService, "_installer", lambda self: _Installer())
    monkeypatch.setattr(source_registry, "verify_identity", lambda *a, **k: (_Rec(), ""))
    return state


def _lock_busy(svc, rel):
    try:
        with reslock.operation_lock(svc._paths, reslock.source_lock_key(rel), "update", "probe"):
            return False
    except reslock.ResourceBusy:
        return True


def _meshcom_interrupted_then_daemon(monkeypatch, svc, binary_receipt):
    """A meshcom binary install died after moving meshcom-qemu from B's commit (journal open); a daemon binary
    install comes next and must recover it first."""
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "a" * 40)
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    bi.record_clone(svc._paths, svc.stack("meshcom").component("meshcom-qemu"), "b" * 40)
    _serve(monkeypatch, "daemon", _entry("daemon", "a" * 64, "a" * 40, NEWER))      # its download refuses
    return state, svc.stack("meshcom").component("meshcom-qemu").source.path


def _corrupt_record(svc, rel):
    path = source_registry.record_path(svc._paths, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")


def _owned_at_install(svc):
    """The installed receipt's ownership baseline as the real install records it: meshcom-qemu's checkout owned by
    the source transaction "at-install" (written, then read back into the receipt)."""
    rel = svc.stack("meshcom").component("meshcom-qemu").source.path
    assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel=rel, remote="", selector="pinned", resolved_commit="b" * 40, adopted_at=1.0,
        txn_id="at-install", components=("meshcom-qemu",)))
    rec = brx.receipt_state(svc._paths, "meshcom")[1]
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, registry_baseline={rel: "at-install"}))
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"


def _fail(what):
    def _raise(*a, **k):
        raise bi.BinaryInstallError(what)
    return _raise


@pytest.mark.parametrize("step", ["download_artifact", "validate_and_extract", "publish"])
def test_a_failed_install_leaves_the_binary_and_its_checkout_as_before(tmp_path, monkeypatch, binary_receipt, step):
    # B installed, its checkout at B's commit; the newer A was built from another commit. A failed download,
    # extract or swap leaves B and B's checkout exactly as before (one rollback boundary).
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "meshcom")
    files = {rel: (tmp_path / rel).read_bytes() for rel in rec.files}
    state = _moving_clone(monkeypatch, svc, "b" * 40)
    _owned_at_install(svc)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(bi, "validate_and_extract", lambda *a, **k: [])
    monkeypatch.setattr(bi, step, _fail(f"{step} FAILED"))
    res = svc.update("meshcom", apply=True, source="binary")
    assert not res.ok and f"{step} FAILED" in res.summary and res.data["rolled_back"], res
    assert state["commit"] == "b" * 40
    assert state["adopted"] == ([] if step != "publish" else ["a" * 40, "b" * 40])   # staged first, moved back
    # B is still installed and VALID: moving the checkout back is the binary's own move, not a source adoption
    st, back, why = brx.receipt_state(svc._paths, "meshcom")
    assert st == "valid" and back.artifact_sha256 == "b" * 64, why
    assert {rel: (tmp_path / rel).read_bytes() for rel in rec.files} == files
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_an_install_interrupted_after_the_checkout_move_is_moved_back_by_the_next_install(tmp_path, monkeypatch,
                                                                                         binary_receipt):
    # A run died after journaling and moving the checkout: another command leaves the journal (it cannot move a
    # checkout without the install's locks); the next binary install moves it back, then decides.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "a" * 40)
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    bi.record_clone(svc._paths, svc.stack("meshcom").component("meshcom-qemu"), "b" * 40)
    ok, why = svc.binary_recover()
    assert not ok and "lhpc install meshcom --source binary --yes" in why
    assert bi.read_journal(svc._paths)[1] == "valid" and state["commit"] == "a" * 40
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, OLDER))
    res = svc.install("meshcom", apply=True, source="binary")
    assert res.ok and res.data["changes"] == 0, res.summary
    assert state["commit"] == "b" * 40 and state["adopted"] == ["b" * 40]
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_plan_and_apply_agree_on_a_missing_package_of_the_installed_artifact(tmp_path, monkeypatch, binary_receipt):
    # The same intact artifact B is listed, and a package its entry names is gone: the plan says what the apply
    # says (the package refusal with its apt command), not "nothing to install".
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "daemon")
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, pkg: pkg != "libonly-b1")
    _serve(monkeypatch, "daemon", _entry("daemon", "b" * 64, "b" * 40, INSTALLED, deps=("libonly-b1",)))
    plan, apply = (svc.update("daemon", apply=a, source="binary") for a in (False, True))
    for res in (plan, apply):
        assert not res.ok and "needs system packages" in res.summary, res.summary
    assert plan.summary == apply.summary and plan.next_commands == apply.next_commands


def test_the_same_intact_artifact_moves_a_stale_checkout_to_its_commit_and_touches_no_file(
        tmp_path, monkeypatch, binary_receipt):
    # B installed and intact, its checkout left at another commit: installing B again is the repair. The checkout
    # moves to B's commit; nothing is downloaded and no installed file changes.
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "meshcom")
    files = {rel: (tmp_path / rel).read_bytes() for rel in rec.files}
    state = _moving_clone(monkeypatch, svc, "c" * 40)
    _owned_at_install(svc)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "b" * 64, "b" * 40, INSTALLED))   # downloads refuse
    res = svc.install("meshcom", apply=True, source="binary")
    assert res.ok and res.data["changes"] == 1 and "nothing to download" in res.summary, res.summary   # the move
    assert "  adopted pinned source for meshcom-qemu (run scripts)" in res.details, res.details
    assert state["adopted"] == ["b" * 40] and state["commit"] == "b" * 40
    st, _rec, why = brx.receipt_state(svc._paths, "meshcom")
    assert st == "valid", why                    # the repair is the binary's own move: B is not superseded
    assert {rel: (tmp_path / rel).read_bytes() for rel in rec.files} == files
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_the_same_artifacts_checkout_repair_is_refused_while_the_stack_runs(tmp_path, monkeypatch, binary_receipt):
    # The running stack runs its scripts from that checkout: the repair is refused under the locks, nothing moved.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "c" * 40)
    monkeypatch.setattr(ControllerService, "_binary_running_components", lambda self, sid: ["meshcom-qemu"])
    _serve(monkeypatch, "meshcom", _entry("meshcom", "b" * 64, "b" * 40, INSTALLED))
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "component(s) running" in res.summary, res.summary
    assert state["adopted"] == [] and state["commit"] == "c" * 40


def test_a_checkout_that_cannot_be_moved_back_blocks_the_next_install_by_name_and_keeps_the_journal(
        tmp_path, monkeypatch, binary_receipt):
    # An interrupted install moved the checkout from B's commit; moving it back fails: the next install refuses,
    # naming the checkout, the commit and why, and the journal stays for the retry.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "a" * 40)
    state["refuse"] = "disk full"
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    bi.record_clone(svc._paths, svc.stack("meshcom").component("meshcom-qemu"), "b" * 40)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, OLDER))
    res = svc.install("meshcom", apply=True, source="binary")
    path = svc.stack("meshcom").component("meshcom-qemu").source.path
    assert not res.ok and f"the {path} checkout could not be moved back to {'b' * 9} (disk full)" in res.summary, (
        res.summary)
    assert bi.read_journal(svc._paths)[1] == "valid" and state["commit"] == "a" * 40


def test_a_binary_install_that_moves_its_checkout_writes_a_receipt_that_reads_back_valid(tmp_path, monkeypatch):
    # The lab reset's meshcom install (testlab 43789810157 on e68dd3dd): the checkout is moved inside the
    # transaction, and its adoption writes a new ownership record. The receipt's baseline is read after that move,
    # so the written receipt reads back valid (it was "superseded": the install failed and rolled back).
    import os
    svc = _svc(tmp_path, monkeypatch)
    state = _moving_clone(monkeypatch, svc, "c" * 40)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    spec = svc.binary_spec("meshcom")
    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})

    def stage(tar, stage_dir, roots):
        for rel in files:
            os.makedirs(os.path.dirname(os.path.join(stage_dir, rel)), exist_ok=True)
            with open(os.path.join(stage_dir, rel), "w") as fh:
                fh.write("staged")
        return files
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(bi, "validate_and_extract", stage)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])
    res = svc.install("meshcom", apply=True, source="binary")
    assert res.ok, res.summary
    assert state["adopted"] == ["a" * 40] and state["commit"] == "a" * 40
    st, rec, why = brx.receipt_state(svc._paths, "meshcom")
    assert st == "valid" and rec.artifact_sha256 == "a" * 64, why
    rel = svc.stack("meshcom").component("meshcom-qemu").source.path
    assert rec.registry_baseline[rel] == "adopt-1"          # the record the move wrote, read after it


def _staged_to_commit(monkeypatch, svc):
    # the install runs to its receipt: proof and probe files staged and published for real, probes answered
    import os
    spec = svc.binary_spec("meshcom")
    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})

    def stage(tar, stage_dir, roots):
        for rel in files:
            os.makedirs(os.path.dirname(os.path.join(stage_dir, rel)), exist_ok=True)
            with open(os.path.join(stage_dir, rel), "w") as fh:
                fh.write("staged")
        return files
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(bi, "validate_and_extract", stage)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])


def test_an_ownership_record_damaged_by_the_move_fails_the_install_and_rolls_it_back(tmp_path, monkeypatch):
    # The baseline read after the move refuses a damaged record (supersession could not be judged later): the
    # install fails, rolled back, and no receipt is left.
    svc = _svc(tmp_path, monkeypatch)
    state = _moving_clone(monkeypatch, svc, "c" * 40)
    state["corrupt"] = True
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    _staged_to_commit(monkeypatch, svc)
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and res.data.get("rolled_back") and "src/meshcom-qemu-raspi" in res.summary, res.summary
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "absent"


def test_a_damaged_ownership_record_refuses_the_install_before_anything_changes(tmp_path, monkeypatch):
    # A covered path's record is damaged before the install: refused by name, nothing downloaded or moved.
    svc = _svc(tmp_path, monkeypatch)
    state = _moving_clone(monkeypatch, svc, "c" * 40)
    _corrupt_record(svc, "src/MeshCom-Firmware")
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))      # a download refuses
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "src/MeshCom-Firmware" in res.summary and "DOWNLOAD-REACHED" not in res.summary, res
    assert any("source ownership record named above is damaged" in d for d in res.details), res.details
    assert state["adopted"] == [] and bi.read_journal(svc._paths)[1] == "absent"


def _repair_setup(tmp_path, monkeypatch, binary_receipt):
    """B installed and intact (its receipt owning meshcom-qemu at "at-install"), the checkout left at another
    commit: installing B again is the checkout repair."""
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "c" * 40)
    _owned_at_install(svc)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "b" * 64, "b" * 40, INSTALLED))
    return svc, state


def _repaired_back(svc, state):
    # moved to B's commit, then back; B's receipt valid again (the move back is the binary's own); no journal
    assert state["adopted"] == ["b" * 40, "c" * 40] and state["commit"] == "c" * 40, state
    st, _rec, why = brx.receipt_state(svc._paths, "meshcom")
    assert st == "valid", why
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_a_repair_whose_receipt_cannot_be_rewritten_is_refused_by_name_and_moves_the_checkout_back(
        tmp_path, monkeypatch, binary_receipt):
    # The repair moved the checkout, then the receipt cannot be rewritten (a full disk): refused by name, and the
    # repair is undone like a failed install: the old checkout is back and B's receipt is valid.
    svc, state = _repair_setup(tmp_path, monkeypatch, binary_receipt)
    real, calls = brx.write_receipt, []
    monkeypatch.setattr(brx, "write_receipt", lambda *a, **k: bool(calls.append(1) or len(calls) > 1) and real(*a, **k))
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "the binary receipt could not be rewritten after its checkout moved" in res.summary, (
        res.summary)
    _repaired_back(svc, state)


def test_a_repair_interrupted_after_the_move_moves_the_checkout_back(tmp_path, monkeypatch, binary_receipt):
    # Ctrl-C between the move and the receipt rewrite: unwound before the interrupt propagates.
    svc, state = _repair_setup(tmp_path, monkeypatch, binary_receipt)
    real, calls = bi.rebase_receipt, []

    def interrupted(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real(*a, **k)
    monkeypatch.setattr(bi, "rebase_receipt", interrupted)
    with pytest.raises(KeyboardInterrupt):
        svc.install("meshcom", apply=True, source="binary")
    _repaired_back(svc, state)



def test_a_restore_whose_receipt_cannot_be_rewritten_keeps_the_journal(tmp_path, monkeypatch, binary_receipt):
    # The failed swap moved the checkout and back; the restored receipt cannot take the re-read baseline: the
    # rollback is not finished, the journal stays for the next install, and the reason is named.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    _moving_clone(monkeypatch, svc, "b" * 40)
    _owned_at_install(svc)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(bi, "validate_and_extract", lambda *a, **k: [])
    monkeypatch.setattr(bi, "publish", _fail("publish FAILED"))
    monkeypatch.setattr(brx, "write_receipt", lambda *a, **k: False)
    res = svc.update("meshcom", apply=True, source="binary")
    text = " ".join([res.summary, *res.details])
    assert not res.ok and "the binary receipt could not be rewritten after its checkout moved" in text, text
    assert bi.read_journal(svc._paths)[1] == "valid"


def test_the_rebase_rereads_only_the_moved_paths_so_a_source_adoption_elsewhere_stays_visible(
        tmp_path, monkeypatch, binary_receipt):
    # The receipt owned the firmware checkout at "fw-at-install"; the source channel adopted it since ("fw-later").
    # Re-reading the baseline after the binary's own move of meshcom-qemu leaves the firmware's baseline: the
    # receipt reads superseded by that source adoption.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    _owned_at_install(svc)
    rec = brx.receipt_state(svc._paths, "meshcom")[1]
    fw = "src/MeshCom-Firmware"
    for txn in ("fw-at-install", "fw-later"):
        assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
            source_rel=fw, remote="", selector="pinned", resolved_commit="f" * 40, adopted_at=1.0, txn_id=txn,
            components=("meshcom-firmware",)))
        if txn == "fw-at-install":
            assert brx.write_receipt(svc._paths, dataclasses.replace(
                rec, registry_baseline={**rec.registry_baseline, fw: txn}))
    assert bi.rebase_receipt(svc, "meshcom", [svc.stack("meshcom").component("meshcom-qemu").source.path]) == ""
    st, _rec, why = brx.receipt_state(svc._paths, "meshcom")
    assert st == "superseded" and fw in why, (st, why)


def test_another_stacks_install_moves_a_checkout_back_only_under_that_checkouts_own_lock(
        tmp_path, monkeypatch, binary_receipt):
    # The daemon install's recovery moves meshcom-qemu back while holding meshcom-qemu's source lock, not under
    # the daemon's locks alone.
    svc = _svc(tmp_path, monkeypatch)
    state, _rel = _meshcom_interrupted_then_daemon(monkeypatch, svc, binary_receipt)
    state["lock_held"] = []
    res = svc.install("daemon", apply=True, source="binary")
    assert not res.ok and "DOWNLOAD-REACHED" in res.summary, res.summary          # recovered, then went on
    assert state["adopted"] == ["b" * 40] and state["lock_held"] == [True]
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_another_stacks_install_leaves_a_checkout_whose_lock_is_busy_and_names_its_stack(
        tmp_path, monkeypatch, binary_receipt):
    # Another operation holds meshcom-qemu's source lock: the daemon install refuses, naming meshcom and the
    # move-back command; the checkout is not touched and the journal stays.
    svc = _svc(tmp_path, monkeypatch)
    state, rel = _meshcom_interrupted_then_daemon(monkeypatch, svc, binary_receipt)
    monkeypatch.setattr(ControllerService, "_SELF_LOCK_WAIT_S", 0.0)
    with reslock.operation_lock(svc._paths, reslock.source_lock_key(rel), "update", "probe"):
        res = svc.install("daemon", apply=True, source="binary")
    assert not res.ok and "a binary install of 'meshcom'" in res.summary and "busy now" in res.summary, res.summary
    assert "lhpc install meshcom --source binary --yes moves it back" in res.summary, res.summary
    assert state["adopted"] == [] and state["commit"] == "a" * 40
    assert bi.read_journal(svc._paths)[1] == "valid"


def test_another_stacks_install_leaves_a_checkout_whose_source_transaction_is_open_and_names_its_stack(
        tmp_path, monkeypatch, binary_receipt):
    # A source transaction appears after the daemon install's own guard passed: the restore's guard sees it and
    # refuses by name (no traceback); nothing moved, the journal stays.
    svc = _svc(tmp_path, monkeypatch)
    state, _rel = _meshcom_interrupted_then_daemon(monkeypatch, svc, binary_receipt)
    calls = []
    state["pending"] = lambda: [] if calls.append(1) or len(calls) == 1 else ["src/meshcom-qemu-raspi.journal"]
    res = svc.install("daemon", apply=True, source="binary")
    assert not res.ok and "a binary install of 'meshcom'" in res.summary and "busy now" in res.summary, res.summary
    assert "unresolved source-transaction journal" in res.summary, res.summary
    assert state["adopted"] == [] and bi.read_journal(svc._paths)[1] == "valid"


def test_the_plan_names_the_same_artifacts_checkout_repair_the_apply_makes(tmp_path, monkeypatch, binary_receipt):
    # B intact and listed, its checkout clean at another commit: the plan says the checkout moves (a change), and
    # moves nothing; the apply makes that move.
    svc, state = _repair_setup(tmp_path, monkeypatch, binary_receipt)
    plan = svc.install("meshcom", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 1, plan
    assert f"  moves the pinned source for meshcom-qemu to {'b' * 9} (run scripts)" in plan.details, plan.details
    assert state["adopted"] == []
    apply = svc.install("meshcom", apply=True, source="binary")
    assert apply.ok and apply.data["changes"] == 1 and state["adopted"] == ["b" * 40], apply


def test_plan_and_apply_refuse_a_dirty_stale_checkout_of_the_same_artifact_alike(tmp_path, monkeypatch,
                                                                                 binary_receipt):
    svc, state = _repair_setup(tmp_path, monkeypatch, binary_receipt)
    state["dirty"] = ["run.sh"]
    plan, apply = (svc.install("meshcom", apply=a, source="binary") for a in (False, True))
    for res in (plan, apply):
        assert not res.ok and "the artifact's run scripts must come from that checkout" in res.summary, res.summary
    assert plan.summary == apply.summary and plan.next_commands == apply.next_commands
    assert state["adopted"] == []


# ---- G2-UPDCHAIN-A named fixes: identity, a repair's receipt, a recovery's journaled checkouts ----------------


def test_a_kept_binary_is_identified_by_its_sha_never_by_its_file_name(tmp_path, monkeypatch, binary_receipt):
    # B installed and intact, its receipt naming the file of the listed entry C (another sha, other commits, not
    # newer): B is kept by its sha, so its checkout stays at B's commit; C's commits are never applied to it.
    svc = _svc(tmp_path, monkeypatch)
    rec = _installed(svc, binary_receipt, "meshcom")
    c = _entry("meshcom", "c" * 64, "a" * 40, OLDER)
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, filename=c["filename"]))
    state = _moving_clone(monkeypatch, svc, "b" * 40)
    _owned_at_install(svc)
    _serve(monkeypatch, "meshcom", c)
    for apply in (False, True):
        res = svc.install("meshcom", apply=apply, source="binary")
        assert res.ok and res.data["changes"] == 0, (res.summary, res.details)
        assert any("not newer than the installed one" in d for d in res.details), res.details   # kept, not "same"
    assert state["adopted"] == [] and state["commit"] == "b" * 40


def test_the_same_artifact_keeps_its_checkouts_at_its_receipts_commits(tmp_path, monkeypatch, binary_receipt):
    # The index lists the installed sha with other commits (its metadata re-published): the same artifact's checkout
    # is checked against ITS receipt's commits, so nothing moves, in the plan and the apply.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "b" * 40)
    _owned_at_install(svc)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "b" * 64, "a" * 40, INSTALLED))
    for apply in (False, True):
        res = svc.install("meshcom", apply=apply, source="binary")
        assert res.ok and res.data["changes"] == 0, (res.summary, res.details)
    assert state["adopted"] == [] and state["commit"] == "b" * 40


def test_a_repair_whose_receipt_reads_back_unreadable_is_refused_and_moves_the_checkout_back(
        tmp_path, monkeypatch, binary_receipt):
    # The repair moved the checkout, then its receipt reads back unreadable (a transient I/O error): the repair is
    # refused by name, never committed with the receipt left un-rebased, and undone like a failed install.
    svc, state = _repair_setup(tmp_path, monkeypatch, binary_receipt)
    real_move, real_state, after_move = bi.move_clones, brx.receipt_state, []

    def move(*a, **k):
        out = real_move(*a, **k)
        after_move.append("moved")
        return out

    def receipt_state(*a, **k):
        if after_move == ["moved"]:                  # the first read after the move, once
            after_move.append("read")
            return "unsafe", None, "the binary receipt could not be read (I/O error)"
        return real_state(*a, **k)
    monkeypatch.setattr(bi, "move_clones", move)
    monkeypatch.setattr(brx, "receipt_state", receipt_state)
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "the binary receipt could not be read (I/O error)" in res.summary, res.summary
    _repaired_back(svc, state)


def _fails_after_the_move(monkeypatch, svc, entry):
    """`entry` served; its download and extract pass, the publish after the checkout move fails."""
    _serve(monkeypatch, "meshcom", entry)
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(bi, "validate_and_extract", lambda *a, **k: [])
    monkeypatch.setattr(bi, "publish", _fail("PUBLISH-FAILED"))


def _died_after_the_move(monkeypatch, svc, binary_receipt):
    """C installed (meshcom-qemu at C's commit); an install of the newer B moved the checkout to B's commit and
    died before its own unwind (a power cut): the journal stays open with the moved checkout in it."""
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "b" * 40)
    _fails_after_the_move(monkeypatch, svc, _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    with monkeypatch.context() as m:
        m.setattr(ControllerService, "binary_recover", lambda self, **k: (True, ""))   # no unwind: it died
        svc.install("meshcom", apply=True, source="binary")
    assert state["commit"] == "a" * 40 and bi.read_journal(svc._paths)[1] == "valid"
    return state


@pytest.mark.parametrize("change", ["removed", "moved"])
def test_a_journaled_checkout_the_manifest_no_longer_names_there_blocks_the_recovery_and_keeps_the_journal(
        tmp_path, monkeypatch, binary_receipt, change):
    # Before the recovery the manifest renamed meshcom-qemu (or moved its source path): nothing is moved, at the
    # old path or the new one, the recovery refuses by name and the journal stays.
    svc = _svc(tmp_path, monkeypatch)
    state = _died_after_the_move(monkeypatch, svc, binary_receipt)
    real, st = ControllerService.stack, svc.stack("meshcom")
    comp = st.component("meshcom-qemu")
    if change == "removed":
        comps = tuple(c for c in st.components if c.id != "meshcom-qemu")
    else:
        comps = tuple(dataclasses.replace(c, source=dataclasses.replace(c.source, path="src/elsewhere"))
                      if c.id == "meshcom-qemu" else c for c in st.components)
    changed = dataclasses.replace(st, components=comps)
    monkeypatch.setattr(ControllerService, "stack", lambda self, sid: changed if sid == "meshcom" else real(self, sid))
    plan = svc.install("meshcom", apply=False, source="binary")
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "meshcom-qemu" in res.summary and comp.source.path in res.summary, res.summary
    assert not plan.ok and plan.summary == res.summary, (plan.summary, res.summary)     # G2-UPDCHAIN-A D4
    assert state["adopted"] == ["a" * 40] and state["commit"] == "a" * 40, state
    assert bi.read_journal(svc._paths)[1] == "valid"


def test_a_failed_install_names_the_checkout_it_created_and_kept(tmp_path, monkeypatch):
    # The checkout did not exist before the install: the rollback has nothing older to return to and keeps it,
    # and says so by name.
    svc = _svc(tmp_path, monkeypatch)
    state = _moving_clone(monkeypatch, svc, "b" * 40)
    path = svc.stack("meshcom").component("meshcom-qemu").source.path
    svc._paths.resolve_source(path).rmdir()                                          # a first install: no checkout
    _fails_after_the_move(monkeypatch, svc, _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and state["adopted"] == ["a" * 40], res.summary
    assert any(path in d and "kept" in d for d in res.details), res.details
    assert bi.read_journal(svc._paths)[1] == "absent"


@pytest.mark.parametrize("manifest", ["as-journaled", "moved-since"])
def test_a_crashed_install_s_kept_checkout_is_named_by_the_install_that_recovers_it(tmp_path, monkeypatch, manifest):
    # G2-UPDCHAIN-A D5: a first install created its checkout and died before its own unwind; the next install
    # recovers it (the checkout is kept: nothing older, also when the manifest moved that source since: nothing to
    # move back) and stops at its packages. Its result names the kept checkout first, never drops what the recovery
    # said.
    svc = _svc(tmp_path, monkeypatch)
    _moving_clone(monkeypatch, svc, "b" * 40)
    path = svc.stack("meshcom").component("meshcom-qemu").source.path
    svc._paths.resolve_source(path).rmdir()                                          # a first install: no checkout
    _fails_after_the_move(monkeypatch, svc, _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    with monkeypatch.context() as m:
        m.setattr(ControllerService, "binary_recover", lambda self, **k: (True, ""))   # no unwind: it died
        svc.install("meshcom", apply=True, source="binary")
    assert bi.read_journal(svc._paths)[1] == "valid"
    if manifest == "moved-since":
        real, st = ControllerService.stack, svc.stack("meshcom")
        moved = dataclasses.replace(st, components=tuple(
            dataclasses.replace(c, source=dataclasses.replace(c.source, path="src/elsewhere"))
            if c.id == "meshcom-qemu" else c for c in st.components))
        monkeypatch.setattr(ControllerService, "stack", lambda self, sid: moved if sid == "meshcom" else real(self, sid))
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, pkg: pkg != "libonly-extra")
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER, deps=("libonly-extra",)))
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and res.data.get("missing_runtime_deps") == ["libonly-extra"], res.summary
    assert res.details[0] == (f"  recovered first: the {path} checkout this install created was kept: nothing older "
                              "to return to"), res.details
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_a_receipt_whose_backup_cannot_be_read_is_never_deleted_by_a_failed_install(tmp_path, monkeypatch,
                                                                                     binary_receipt):
    # G2-UPDCHAIN-A D2: the receipt reads as installed, its raw text for the transaction's backup cannot be read.
    # Taken for "no receipt", the rollback of a failed install would delete it: refused before anything moves.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    state = _moving_clone(monkeypatch, svc, "b" * 40)
    _fails_after_the_move(monkeypatch, svc, _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    monkeypatch.setattr(brx, "read_raw", lambda *a, **k: None)
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "could not be read for its backup" in res.summary, res.summary
    assert state["adopted"] == [], state
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"
    assert bi.read_journal(svc._paths)[1] == "absent"


@pytest.mark.parametrize("clones", [
    {"meshcom-qemu": {"commit": ""}},                                   # its path lost: was a KeyError
    {"meshcom-qemu": {"commit": None, "path": "PATH"}},
    {"meshcom-qemu": {"commit": "", "path": "../escape"}},
    {"meshcom-qemu": "a" * 40},
    ["meshcom-qemu"],                                                   # not a map: was an AttributeError
], ids=["no-path", "no-commit", "escaping-path", "not-a-record", "not-a-map"])
def test_a_hand_damaged_journal_checkout_entry_is_refused_by_name_never_a_crash(tmp_path, monkeypatch, binary_receipt,
                                                                               clones):
    # An interrupted install's journal whose checkout entries are damaged by hand: the recovery reads each one's
    # commit and path, so the journal is unreadable, refused by name and kept, never a crash.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    jp = bi.journal_path(svc._paths)
    path = svc.stack("meshcom").component("meshcom-qemu").source.path
    jp.write_text(json.dumps({**json.loads(jp.read_text()), "clones": clones}).replace('"PATH"', json.dumps(path)))
    svc.invalidate_snapshot()
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: pytest.fail("must refuse first"))
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "resolve state/binary/install.journal.json by hand" in res.summary, res.summary
    assert jp.exists()


@pytest.mark.parametrize("unreadable", ["journal", "receipt"])
def test_the_plan_refuses_what_the_apply_refuses_when_its_state_cannot_be_read(tmp_path, monkeypatch, binary_receipt,
                                                                              unreadable):
    # An unreadable install journal or receipt: the apply refuses before any download; the dry run says the same,
    # never a download plan.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    _serve(monkeypatch, "meshcom", _entry("meshcom", "a" * 64, "a" * 40, NEWER))
    bad = bi.journal_path(svc._paths) if unreadable == "journal" else brx.receipt_path(svc._paths, "meshcom")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json")
    svc.invalidate_snapshot()
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: pytest.fail("must refuse first"))
    plan = svc.install("meshcom", apply=False, source="binary")
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "blocked" in res.summary, res.summary
    assert not plan.ok and plan.summary == res.summary, (plan.summary, res.summary)


@pytest.mark.parametrize("served", ["newer", "same", "older-needing-packages"])
def test_the_plan_of_an_interrupted_install_claims_nothing_the_recovery_decides(tmp_path, monkeypatch,
                                                                                binary_receipt, served):
    # G2-UPDCHAIN-A D4: an interrupted (valid, uncommitted) journal of this stack. The apply recovers it first, then
    # decides from what it restored: a download (newer entry), nothing (the same intact artifact), or the restored
    # newer artifact kept (an older entry needing packages). The plan cannot read that state without the recovery:
    # it says the recovery comes first and claims no download, no "nothing to download", no package verdict.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    _clean_clone_at(monkeypatch, svc, "b" * 40)
    monkeypatch.setattr(ControllerService, "_dpkg_installed", lambda self, pkg: pkg != "libonly-extra")
    entry = {"newer": _entry("meshcom", "a" * 64, "a" * 40, NEWER),
             "same": _entry("meshcom", "b" * 64, "b" * 40, INSTALLED),
             "older-needing-packages": _entry("meshcom", "a" * 64, "a" * 40, OLDER, deps=("libonly-extra",))}[served]
    _serve(monkeypatch, "meshcom", entry)
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    svc.invalidate_snapshot()
    plan = svc.install("meshcom", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 1, (plan.summary, plan.details)
    assert "interrupted binary install of 'meshcom' is open" in plan.summary, plan.summary
    assert plan.details == [f"  the apply first tries to recover it; whether {entry['filename']} is then downloaded "
                            "depends on what that restores"], plan.details
    res = svc.install("meshcom", apply=True, source="binary")
    if served == "newer":
        assert not res.ok and "DOWNLOAD-REACHED" in res.summary, res.summary
    else:
        assert res.ok and res.data["changes"] == 0 and "nothing to download" in res.summary, res.summary
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_the_plan_with_a_committed_journal_decides_as_the_apply_does(tmp_path, monkeypatch, binary_receipt):
    # A committed journal only drops its bookkeeping in the recovery: the plan decides as without one.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "meshcom")
    _clean_clone_at(monkeypatch, svc, "b" * 40)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "b" * 64, "b" * 40, INSTALLED))
    bi.open_txn(svc._paths, "meshcom", "t" * 16, old_receipt=brx.read_raw(svc._paths, "meshcom"), auth={})
    assert bi.write_journal(svc._paths, {**bi.read_journal(svc._paths)[0], "state": "committed"})
    svc.invalidate_snapshot()
    plan = svc.install("meshcom", apply=False, source="binary")
    res = svc.install("meshcom", apply=True, source="binary")
    assert plan.ok and plan.data["changes"] == 0 and "nothing to download" in plan.summary, plan.summary
    assert res.ok and res.data["changes"] == 0 and plan.summary == res.summary, res.summary
    assert bi.read_journal(svc._paths)[1] == "absent"

"""U9 (D10) gate-1 fix: the "nothing to install" decision comes FIRST. An installed binary the index offers nothing
newer than (also when its own entry was withdrawn) stays, untouched: no clone is moved, no package is asked for,
and its files are verified whatever the published entry's age; changed files are a refusal (never an older
replacement), with the source install as the way out. Driven through the public install / update paths."""
from __future__ import annotations

import json

from lhpc.core import binary_install as bi, binary_receipt as brx, source_registry
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

"""Install/update channel dispatch + switching (B7/B8).

Binary→binary updates run silently when the publisher has caught up; a lagging artifact
produces the explicit "source only, long compile" choice. Switching to a source channel retires
the artifact FIRST so the ordinary clone path meets a clean destination (never the silent
"destination already exists" skip).
"""


import os

import pytest

from lhpc.core import binary_install as bi
from lhpc.core import binary_receipt as brx
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


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


# --- install dispatch ---------------------------------------------------------------------------

def test_install_binary_channel_dispatches(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    called = {}
    monkeypatch.setattr(ControllerService, "binary_install",
                        lambda self, sid, apply=False: called.setdefault("args", (sid, apply)))
    svc.install("daemon", apply=True, source="binary")
    assert called["args"] == ("daemon", True)


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


# --- update dispatch ----------------------------------------------------------------------------

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


# --- freshness ----------------------------------------------------------------------------------

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


# --- audit round 2: locked, stopped-state-checked mutation ---------------------------------------

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


# --- source -> binary is an INSTALL, never a source update ---------------------------------------

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

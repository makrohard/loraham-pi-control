"""Race-safe destructive source operations: an EXTERNAL process substituting the managed
source leaf between LHPC's identity proof and the irreversible mutation must never get its
content archived, replaced, unlinked, or deleted. The deterministic `source_fs.race_seam`
hook fires exactly between proof and mutation; these tests substitute the destination there
and prove refusal + preservation — and that legitimate unchanged operations still succeed."""

import os
import shutil
import subprocess
import time
from pathlib import Path

from lhpc.core import known_working, source_fs, source_registry
from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.services import ControllerService
from lhpc.core.probes.backends import FakeSystem


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    out = subprocess.run(["git", "-C", str(repo), *args], check=True,
                         capture_output=True, text=True, env=env)
    return out.stdout.strip()


def _make_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "file.txt").write_text("hello\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return _git(path, "rev-parse", "HEAD")


def _comp():
    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app"))


def _inst(tmp_path, comp):
    cfg = Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}})
    stacks = (Stack(id="s", name="s", main=comp.id, components=(comp,)),)
    return Installer(Paths(runtime_root=tmp_path / "rt"), stacks, cfg, RealSystem())


def _seam(monkeypatch, point: str, action):
    """Fire `action(path)` exactly once at seam `point`."""
    fired = {"done": False}
    def hook(p, path=""):
        if p == point and not fired["done"]:
            fired["done"] = True
            action(path)
    monkeypatch.setattr(source_fs, "race_seam", hook)
    return fired


# --- update: substitution between identity proof and the archive rename ----------------------

def test_update_refuses_substituted_dir_at_archive(tmp_path, monkeypatch):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"       # v1 active + recorded
    dest = inst.paths.under("src", "app")

    def swap(_path):
        # external process: replace the verified leaf with an unknown directory
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "unknown.txt").write_text("injected")
    fired = _seam(monkeypatch, "pre-archive", swap)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "concurrently replaced" in action.detail
    assert (dest / "unknown.txt").read_text() == "injected"             # substitute UNTOUCHED
    assert not dest.with_name(".app.prev").exists()                     # nothing archived
    assert not inst._journal_path(dest).exists()                        # no retained journal
    assert not list(dest.parent.glob(".app.candidate-*"))               # candidate cleaned


def test_update_refuses_substituted_symlink_at_archive(tmp_path, monkeypatch):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    outside = tmp_path / "outside"
    outside.mkdir()

    def swap(_path):
        shutil.rmtree(dest)
        dest.symlink_to(outside)                                        # symlink substitution
    fired = _seam(monkeypatch, "pre-archive", swap)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "concurrently replaced" in action.detail
    assert dest.is_symlink() and os.readlink(dest) == str(outside)      # substitute untouched
    assert outside.exists()                                             # target untouched


# --- fresh install: a leaf appears after absence was observed --------------------------------

def test_fresh_install_refuses_injected_empty_dir(tmp_path, monkeypatch):
    # plain rename(2) silently REPLACES an empty directory — the atomic NOREPLACE promotion
    # must refuse instead, leaving the injected directory exactly in place.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    dest = inst.paths.under("src", "app")

    def inject(_path):
        dest.mkdir(parents=True)                                        # injected EMPTY dir
    fired = _seam(monkeypatch, "pre-promote", inject)
    action = inst.adopt_source(comp, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared at the destination" in action.detail
    assert dest.is_dir() and list(dest.iterdir()) == []                 # injected dir UNTOUCHED
    assert source_registry.read_record(inst.paths, "src/app") is None   # no false ownership
    assert not inst._journal_path(dest).exists()
    assert not list(dest.parent.glob(".app.candidate-*"))               # candidate cleaned


def test_fresh_install_refuses_injected_symlink(tmp_path, monkeypatch):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    dest = inst.paths.under("src", "app")
    outside = tmp_path / "outside"
    outside.mkdir()

    def inject(_path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(outside)
    fired = _seam(monkeypatch, "pre-promote", inject)
    action = inst.adopt_source(comp, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared at the destination" in action.detail
    assert dest.is_symlink()                                            # injected leaf untouched
    assert not any(outside.iterdir())                                   # target never written


def test_unchanged_update_and_install_still_succeed(tmp_path):
    # The protocols must not break legitimate operation: fresh install then a clean update.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "done", action.detail
    dest = inst.paths.under("src", "app")
    assert (dest / "file.txt").read_text() == "v2\n"
    assert not list(dest.parent.glob(".app.quarantine-*"))              # no artifacts


# --- uninstall / Clean all: substitution between proof and detach ----------------------------

def _svc_env(tmp_path):
    """A registered kiss checkout under a FakeSystem service, identity-verifiable."""
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)
    (dest / "code.c").write_text("x")
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "legacy", "", time.time(),
                                       "", "", ("loraham-kiss-tnc", "loraham-kiss-serial")))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    from lhpc.core.probes.backends import CommandResult
    real_run = svc._system.runner.run
    dest_real = os.path.realpath(str(dest))
    def run(argv, timeout, *a, **k):
        argv = list(argv)
        if (len(argv) >= 4 and argv[:2] == ["git", "-C"]
                and os.path.realpath(argv[2]) == dest_real
                and argv[3:] == ["config", "--get", "remote.origin.url"]):
            return CommandResult(
                0, "https://github.com/makrohard/loraham-kiss-tnc.git\n", "")
        return real_run(argv, timeout, *a, **k)
    svc._system.runner.run = run
    return svc, dest


def test_uninstall_refuses_substituted_dir_at_detach(tmp_path, monkeypatch):
    svc, dest = _svc_env(tmp_path)

    def swap(_path):
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "precious.txt").write_text("user data")
    fired = _seam(monkeypatch, "pre-detach", swap)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok                                                   # truthful failure
    assert (dest / "precious.txt").read_text() == "user data"           # substitute PRESERVED
    assert not list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))  # nothing quarantined


def test_uninstall_refuses_substituted_symlink_at_detach(tmp_path, monkeypatch):
    svc, dest = _svc_env(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")

    def swap(_path):
        shutil.rmtree(dest)
        dest.symlink_to(outside)
    fired = _seam(monkeypatch, "pre-detach", swap)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok
    assert dest.is_symlink()                                            # substitute preserved
    assert (outside / "keep").exists()                                  # target untouched


def test_clean_refuses_substituted_dir_at_detach(tmp_path, monkeypatch):
    svc, dest = _svc_env(tmp_path)

    def swap(_path):
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "precious.txt").write_text("user data")
    fired = _seam(monkeypatch, "pre-detach", swap)
    res = svc.clean("kiss", apply=True, purge=True)
    assert fired["done"]
    assert not res.ok
    assert (dest / "precious.txt").read_text() == "user data"           # substitute PRESERVED


def test_uninstall_unchanged_still_succeeds_and_leaves_no_quarantine(tmp_path):
    svc, dest = _svc_env(tmp_path)
    res = svc.uninstall("kiss", apply=True)
    assert res.ok, res.details
    assert not dest.exists()
    assert not list((tmp_path / "src").glob(".loraham-kiss-tnc.quarantine-*"))


def test_orphan_quarantine_evidence_blocks_and_is_retained(tmp_path):
    # A crash between detach and removal leaves a quarantine leaf: destructive ops refuse
    # (actionable), and the evidence is never auto-deleted.
    svc, dest = _svc_env(tmp_path)
    q = dest.parent / ".loraham-kiss-tnc.quarantine-1-2"
    q.mkdir()
    (q / "evidence").write_text("crash remainder")
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("quarantine evidence" in d for d in res.details)
    assert (q / "evidence").exists()                                    # retained
    assert dest.exists()                                                # source untouched


# --- FINAL M1: NOREPLACE prerequisite, injected transaction leaves, recovery retention -------

def test_unavailable_renameat2_refuses_before_any_mutation(tmp_path, monkeypatch):
    # Without the atomic no-clobber primitive, source lifecycle mutation refuses TYPED —
    # no journal, candidate, source, or registry change; and NO check-then-rename fallback.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    monkeypatch.setattr(source_fs, "_renameat2_fn", None)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "renameat2" in action.detail
    dest = inst.paths.under("src", "app")
    assert not dest.exists()                                            # no source
    assert not inst._journal_path(dest).exists()                        # no journal
    assert source_registry.read_record(inst.paths, "src/app") is None   # no registry
    assert not list(dest.parent.glob(".app.candidate-*")) if dest.parent.exists() else True
    # uninstall/clean refuse likewise, before any detach
    svc, sdest = _svc_env(tmp_path / "svc")
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok and any("renameat2" in d for d in res.details)
    assert sdest.exists()


def test_injected_prev_at_archive_blocks_with_zero_mutation(tmp_path, monkeypatch):
    # A leaf injected at `.prev` between the preflight and the archive rename: the NOREPLACE
    # archive refuses — nothing renamed, injected leaf + active source untouched.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def inject(_path):
        prev.mkdir()
        (prev / "foreign").write_text("injected")
    fired = _seam(monkeypatch, "pre-archive", inject)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared" in action.detail
    assert (prev / "foreign").read_text() == "injected"                 # injected UNTOUCHED
    assert (dest / "file.txt").exists()                                 # active untouched
    assert not inst._journal_path(dest).exists()


def test_recovery_retains_occupied_dest_and_substituted_prev(tmp_path):
    # prior-archived crash state: an OCCUPIED destination (injected dir) is never deleted to
    # restore the prior; a SUBSTITUTED `.prev` (v4 ident mismatch) is never restored/removed.
    import json
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    prev = src / ".app.prev"
    prev.mkdir(); (prev / "m").write_text("PRIOR")
    dest.mkdir(); (dest / "foreign").write_text("injected occupant")
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    cand_rel = rel(src / ".app.candidate-1-2")
    st = os.stat(prev, follow_symlinks=False)
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "dev", "resolved_commit": "a" * 40, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": True},
        "idents": {"candidate": None, "prev": [st.st_dev, st.st_ino, st.st_ctime_ns]}}))
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and ("occupied" in m or "unverified occupant" in m)
               for m in msgs)
    assert (dest / "foreign").exists()                                  # occupant retained
    assert (prev / "m").read_text() == "PRIOR"                          # prior retained
    assert inst._journal_path(dest).exists()                            # journal retained
    # now clear the occupant but SUBSTITUTE .prev: recovery must refuse to restore it
    import shutil
    shutil.rmtree(dest)
    shutil.rmtree(prev)
    prev.mkdir(); (prev / "m").write_text("SUBSTITUTE")                 # different inode
    msgs2 = inst.recover_source_activations()
    assert any("substituted" in m for m in msgs2)
    assert (prev / "m").read_text() == "SUBSTITUTE"                     # untouched
    assert inst._journal_path(dest).exists()


# --- FINAL: substitution at every last-cleanup proof; frozen plans; marker retirement ---------

def test_v5_recovery_promotion_substitution_after_preproof(tmp_path, monkeypatch):
    # The candidate is swapped between the recovery pre-rename ident proof and the rename:
    # the POST-promotion re-proof (dev+ino) detects it — no foreign promotion, no cleanup, retained.
    import json
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"
    staging.mkdir(); (staging / "m").write_text("CANDIDATE")
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    st = os.stat(staging, follow_symlinks=False)
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(src / ".app.prev"), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "", "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": False},
        "idents": {"candidate": [st.st_dev, st.st_ino, st.st_ctime_ns], "prev": None}}))

    def swap(_path):
        shutil.move(str(staging), str(tmp_path / "stolen"))
        staging.mkdir(); (staging / "m").write_text("FOREIGN")
    fired = _seam(monkeypatch, "pre-recovery-promote", swap)
    msgs = inst.recover_source_activations()
    assert fired["done"]
    assert any("recovery-required" in m for m in msgs)
    # the foreign leaf was moved to dest by the atomic rename? NO — post-proof detects it;
    # whatever leaf sits at dest/staging is retained, never deleted
    assert (dest / "m").read_text() == "FOREIGN" or (staging / "m").read_text() == "FOREIGN"
    assert inst._journal_path(dest).exists()                    # journal retained


def _substitute_dir(path):
    shutil.rmtree(path)
    path.mkdir()
    (path / "foreign").write_text("substitute")


def test_substitution_at_prev_delete_is_retained(tmp_path, monkeypatch):
    # Normal activation: `.prev` swapped between its final proof point and deletion —
    # the ident-bound remove refuses; journal retained (recovery-required), prior safe.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    prev = inst.paths.under("src", ".app.prev")
    fired = _seam(monkeypatch, "pre-prev-delete", lambda _p: _substitute_dir(prev))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (prev / "foreign").read_text() == "substitute"       # substitute retained
    dest = inst.paths.under("src", "app")
    assert inst._journal_path(dest).exists()


def test_substitution_at_quarantine_delete_is_retained(tmp_path, monkeypatch):
    # Uninstall: the QUARANTINED leaf is swapped between detach-proof and deletion — the
    # ident-bound removal refuses; the substitute is preserved at the quarantine name.
    svc, dest = _svc_env(tmp_path)

    def swap(_path):
        q = next(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
        _substitute_dir(q)
    fired = _seam(monkeypatch, "pre-quarantine-delete", swap)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok
    q = list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
    assert q and (q[0] / "foreign").read_text() == "substitute"  # evidence retained


def test_probe_level_renameat2_unsupported_refuses(tmp_path, monkeypatch):
    # The libc symbol exists but the PROBE on the actual filesystem fails: refusal before
    # any candidate/journal/source/registry mutation.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    real = source_fs._rename_noreplace_at
    def unsupported(parent_fd, old, new):
        if ".lhpc-atomic-probe-" in old:
            raise source_fs.AtomicRenameUnavailable("probe: unsupported filesystem")
        return real(parent_fd, old, new)
    monkeypatch.setattr(source_fs, "_rename_noreplace_at", unsupported)
    monkeypatch.setattr(source_fs, "_ATOMIC_OK_DEVS", set())    # no cached positive
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "unsupported" in action.detail
    dest = inst.paths.under("src", "app")
    assert not dest.exists()
    assert not inst._journal_path(dest).exists()
    assert source_registry.read_record(inst.paths, "src/app") is None


def test_dirty_file_created_during_staging_blocks_archive(tmp_path, monkeypatch):
    # A non-ignored untracked file appears AFTER the initial dirty check (during staging):
    # the FINAL recheck before the archive preserves the source and refuses.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    fired = _seam(monkeypatch, "pre-archive",
                  lambda _p: (dest / "new-user-file.txt").write_text("late"))
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "appeared during staging" in action.detail
    assert (dest / "file.txt").read_text() == "hello\n"          # ORIGINAL source preserved
    assert (dest / "new-user-file.txt").read_text() == "late"    # user file preserved
    assert not dest.with_name(".app.prev").exists()              # never archived
    assert not inst._journal_path(dest).exists()


def test_dirty_file_created_before_uninstall_removal_blocks(tmp_path, monkeypatch):
    # A file created between the initial dirty check and the irreversible detach: the
    # final recheck preserves the source and returns incomplete.
    from lhpc.core.install import Installer, DirtyReport
    svc, dest = _svc_env(tmp_path)
    (dest / ".git").mkdir()                                      # dirty checks engage
    calls = {"n": 0}
    def wrapped(self, d, path):
        calls["n"] += 1
        if calls["n"] == 1:
            # the INITIAL check sees a clean tree; the file appears right after it
            (dest / "late-user-file.txt").write_text("late")
            return DirtyReport()
        return DirtyReport(untracked=("late-user-file.txt",))   # FINAL recheck: dirty
    monkeypatch.setattr(Installer, "dirty_report", wrapped)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("appeared before removal" in d for d in res.details)
    assert dest.exists() and (dest / "late-user-file.txt").exists()   # source preserved


# --- FINAL: dirty state changing AFTER the final pre-removal check ----------------------------

def test_update_dirty_after_archive_restores_prior(tmp_path, monkeypatch):
    # An untracked file lands INSIDE the (unchanged) prior directory AFTER the pre-archive
    # dirty check, once it is already archived at `.prev`: the post-archive rescan through
    # the captured handle catches it — no promotion, prior restored no-clobber at its
    # original path, the new file survives, registry/journal state stays consistent.
    head1 = _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    rec_before = source_registry.read_record(inst.paths, "src/app")
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def late_file(_path):
        assert prev.is_dir()                              # the prior IS archived right now
        (prev / "late-user-file.txt").write_text("late")  # pathname write into the tree
    fired = _seam(monkeypatch, "post-archive", late_file)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed"                      # truthful refusal, no false success
    assert "local modifications appeared" in action.detail
    assert (dest / "file.txt").read_text() == "hello\n"   # OLD source restored at dest
    assert (dest / "late-user-file.txt").read_text() == "late"   # the new file SURVIVES
    assert not prev.exists()                              # nothing left archived
    assert not list(dest.parent.glob(".app.candidate-*")) # candidate NOT activated, cleaned
    assert source_registry.read_record(inst.paths, "src/app") == rec_before  # registry intact
    assert not inst._journal_path(dest).exists()          # proven restore -> journal cleared


def test_update_dirty_after_archive_unprovable_restore_is_recovery(tmp_path, monkeypatch):
    # Same window, but the freed destination slot is REOCCUPIED before the restore: the
    # no-clobber restore cannot land — journal + `.prev` + injected leaf are all retained.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    dest = inst.paths.under("src", "app")
    prev = dest.with_name(".app.prev")

    def late_file_and_occupy(_path):
        (prev / "late-user-file.txt").write_text("late")
        dest.mkdir()                                      # inject into the freed slot
        (dest / "foreign").write_text("occupied")
    fired = _seam(monkeypatch, "post-archive", late_file_and_occupy)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (prev / "late-user-file.txt").exists()         # evidence retained at .prev
    assert (dest / "foreign").exists()                    # injected leaf untouched
    assert inst._journal_path(dest).exists()              # journal retained (recovery)


def test_uninstall_dirty_after_detach_restores_source(tmp_path, monkeypatch):
    # An untracked file lands inside the quarantined directory AFTER the pre-detach check:
    # the post-detach rescan catches it — the source is restored no-clobber at its original
    # path, the new file survives, the registry record and config stay untouched, and the
    # result is a truthful incomplete (never success).
    import subprocess
    from lhpc.core.probes import RealSystem
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    _make_repo(dest)
    _git(dest, "remote", "add", "origin",
         "https://github.com/makrohard/loraham-kiss-tnc.git")
    head = _git(dest, "rev-parse", "HEAD")
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc",
                                       "https://github.com/makrohard/loraham-kiss-tnc.git",
                                       "legacy", head, time.time(), "", "",
                                       ("loraham-kiss-tnc", "loraham-kiss-serial")))
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    rec_before = source_registry.read_record(svc._paths, "src/loraham-kiss-tnc")

    def late_file(path):
        q = next(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
        (q / "late-user-file.txt").write_text("late")     # pathname write post-detach
    fired = _seam(monkeypatch, "pre-quarantine-delete", late_file)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok                                     # truthful incomplete, no success
    assert any("local changes appeared" in d and "restored" in d for d in res.details)
    assert (dest / "file.txt").read_text() == "hello\n"   # source RESTORED at original path
    assert (dest / "late-user-file.txt").read_text() == "late"   # the new file SURVIVES
    assert not list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))  # nothing left behind
    assert source_registry.read_record(svc._paths,
                                       "src/loraham-kiss-tnc") == rec_before  # record intact


def test_uninstall_dirty_after_detach_reoccupied_is_recovery(tmp_path, monkeypatch):
    # Same window, but the original path is REOCCUPIED before the restore: the quarantine
    # evidence is preserved, the injected leaf untouched, the record retained — recovery.
    import subprocess
    from lhpc.core.probes import RealSystem
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    _make_repo(dest)
    _git(dest, "remote", "add", "origin",
         "https://github.com/makrohard/loraham-kiss-tnc.git")
    head = _git(dest, "rev-parse", "HEAD")
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc",
                                       "https://github.com/makrohard/loraham-kiss-tnc.git",
                                       "legacy", head, time.time(), "", "",
                                       ("loraham-kiss-tnc", "loraham-kiss-serial")))
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))

    def late_and_occupy(path):
        q = next(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
        (q / "late-user-file.txt").write_text("late")
        dest.mkdir()
        (dest / "foreign").write_text("occupied")         # reoccupy the original path
    fired = _seam(monkeypatch, "pre-quarantine-delete", late_and_occupy)
    res = svc.uninstall("kiss", apply=True)
    assert fired["done"]
    assert not res.ok
    assert any("reoccupied" in d and "recovery" in d for d in res.details)
    q = list(dest.parent.glob(".loraham-kiss-tnc.quarantine-*"))
    assert q and (q[0] / "late-user-file.txt").exists()   # quarantine evidence retained
    assert (dest / "foreign").exists()                    # injected leaf untouched
    assert source_registry.read_record(svc._paths,
                                       "src/loraham-kiss-tnc") is not None  # record retained


# --- FINAL P1: late writes into `.prev` are never destroyed by its cleanup -------------------

def _v2_update_env(tmp_path):
    """Installed v1, local advanced to v2 — ready for a force update."""
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text("v2\n")
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    v2_head = _git(tmp_path / "rt" / "local" / "app", "rev-parse", "HEAD")
    return comp, inst, inst.paths.under("src", "app"), v2_head


def test_prev_dirty_before_cleanup_is_retained_operator_only(tmp_path, monkeypatch):
    # An untracked file lands inside the archived `.prev` AFTER the post-archive recheck,
    # immediately before the cleanup: the file survives, `.prev` stays, the journal is
    # marked prior-dirty-retained, the ACTIVE NEW source + its record stay coherent, the
    # result is truthful incomplete — and no later automatic recovery deletes the prior.
    import json
    comp, inst, dest, v2_head = _v2_update_env(tmp_path)
    prev = dest.with_name(".app.prev")

    def late_file(_path):
        (prev / "late-user-file.txt").write_text("late")
    fired = _seam(monkeypatch, "pre-prev-cleanup", late_file)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert fired["done"]
    assert action.status == "failed"                       # NEVER a successful update
    assert action.detail.startswith("prior-dirty:")
    assert ".app.prev" in action.detail                    # names the retained path
    assert (prev / "late-user-file.txt").read_text() == "late"   # the new file SURVIVES
    assert (prev / "file.txt").read_text() == "hello\n"    # prior content intact
    assert (dest / "file.txt").read_text() == "v2\n"       # NEW source stays active
    rec = source_registry.read_record(inst.paths, "src/app")
    assert rec is not None and rec.resolved_commit == v2_head    # registry truthful
    jf = inst._journal_path(dest)
    assert jf.exists()
    assert json.loads(jf.read_text())["state"] == "prior-dirty-retained"
    # AUTOMATIC RECOVERY never retries the deletion — operator-only, everything retained
    for _ in range(2):
        msgs = inst.recover_source_activations()
        assert any("late local changes" in m and "recovery-required" in m for m in msgs)
        assert (prev / "late-user-file.txt").exists() and jf.exists()
    # and further source mutation stays blocked while the journal is unresolved
    blocked = inst.adopt_source(comp, force=True, source="dev")
    assert blocked.status == "failed" and "recovery-required" in blocked.detail


def test_prev_dirty_during_recovery_cleanup_is_retained(tmp_path, monkeypatch):
    # Interrupted activation (journal 'activated', record complete, `.prev` still present):
    # a file created inside `.prev` right before RECOVERY's cleanup marks the transaction
    # prior-dirty-retained — recovery completes nothing destructive, everything retained.
    import json
    comp, inst, dest, v2_head = _v2_update_env(tmp_path)
    prev = dest.with_name(".app.prev")
    # Build the crash state MANUALLY (a real run removes .prev before the journal, so the
    # needed interruption point — record written, .prev still archived — is crafted):
    # dest = the NEW v2 tree, .prev = the archived v1 prior, journal v4 'activated'.
    shutil.move(str(dest), str(prev))                      # archive the v1 prior
    shutil.copytree(str(tmp_path / "rt" / "local" / "app"), str(dest), symlinks=True)
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    staging_rel = rel(dest.with_name(".app.candidate-1-2"))

    def ident(q):
        st = os.stat(q, follow_symlinks=False)
        return [st.st_dev, st.st_ino, st.st_ctime_ns]   # v5 ctime-hardened ident
    import json as _json
    jf = inst._journal_path(dest)
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(_json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": staging_rel,
        "txn_id": inst._txn_id(staging_rel),
        "meta": {"selector": "dev", "resolved_commit": v2_head, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": True},
        "idents": {"candidate": ident(dest), "prev": ident(prev)}}))
    assert jf.exists() and prev.is_dir()                   # archived prior + journal remain
    assert (dest / "file.txt").read_text() == "v2\n"       # new source already active

    def late_file(_path):
        (prev / "late-user-file.txt").write_text("late")
    fired = _seam(monkeypatch, "pre-prev-cleanup", late_file)
    msgs = inst.recover_source_activations()
    assert fired["done"]
    assert any("late local changes" in m and "recovery-required" in m for m in msgs)
    assert (prev / "late-user-file.txt").read_text() == "late"   # file survives
    assert prev.is_dir()                                   # `.prev` retained
    assert json.loads(jf.read_text())["state"] == "prior-dirty-retained"
    rec = source_registry.read_record(inst.paths, "src/app")
    assert rec is not None and rec.resolved_commit == v2_head    # active record truthful
    # a SECOND automatic recovery still refuses to delete the dirty prior
    monkeypatch.undo()
    msgs2 = inst.recover_source_activations()
    assert any("late local changes" in m for m in msgs2)
    assert (prev / "late-user-file.txt").exists() and jf.exists()


def test_clean_prev_cleanup_still_succeeds_when_not_dirty(tmp_path):
    # Sanity: an update whose archived prior stays clean completes exactly as before —
    # `.prev` removed, journal cleared, record updated.
    comp, inst, dest, v2_head = _v2_update_env(tmp_path)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "done", action.detail
    assert not dest.with_name(".app.prev").exists()
    assert not inst._journal_path(dest).exists()
    assert source_registry.read_record(inst.paths, "src/app").resolved_commit == v2_head

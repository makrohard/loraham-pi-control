"""Durable source ownership registry: records are written TRANSACTIONALLY with source
activation (v3 journal carries the metadata), recovery completes them from the journal (or
skips a rolled-back state), and destructive operations prove — or origin-verify + backfill —
ownership before acting. Real git in tmp repos via the local-fallback adoption path."""

import json
import os
import subprocess
from pathlib import Path

from lhpc.core import source_registry
from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem


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


def _comp(path="src/app", local_dir="app", remote="", pin="", branch=""):
    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path=path, local_dir=local_dir, remote=remote,
                                       pin_commit=pin, branch=branch))


def _inst(tmp_path, comp, extra=()):
    cfg = Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}})
    stacks = (Stack(id="s", name="s", main=comp.id, components=(comp, *extra)),)
    return Installer(Paths(runtime_root=tmp_path / "rt"), stacks, cfg, RealSystem())


def _rec(inst, rel="src/app"):
    return source_registry.read_record(inst.paths, rel)


# --- record store basics ------------------------------------------------------------------

def test_record_roundtrip_and_remove(tmp_path):
    paths = Paths(runtime_root=tmp_path / "rt")
    (tmp_path / "rt").mkdir()
    rec = source_registry.RegistryRecord(
        source_rel="src/app", remote="https://github.com/x/y.git", selector="pinned",
        resolved_commit="a" * 40, adopted_at=1.0, txn_id="t" * 64, strategy="",
        components=("app", "app2"))
    assert source_registry.write_record(paths, rec)
    got = source_registry.read_record(paths, "src/app")
    assert got == rec
    assert source_registry.read_record(paths, "src/other") is None      # distinct identity
    assert source_registry.remove_record(paths, "src/app")
    assert source_registry.read_record(paths, "src/app") is None
    assert source_registry.remove_record(paths, "src/app")              # missing = success


def test_malformed_and_symlinked_records_are_absent(tmp_path):
    paths = Paths(runtime_root=tmp_path / "rt")
    rp = source_registry.record_path(paths, "src/app")
    rp.parent.mkdir(parents=True)
    rp.write_text("not json {{{")
    assert source_registry.read_record(paths, "src/app") is None        # malformed
    rp.unlink()
    rp.write_text(json.dumps({"version": 99}))
    assert source_registry.read_record(paths, "src/app") is None        # wrong version
    rp.unlink()
    (rp.parent / "real.json").write_text(json.dumps({
        "version": 1, "source_rel": "src/app", "remote": "", "selector": "legacy",
        "resolved_commit": "", "adopted_at": 1.0, "txn_id": "", "strategy": "",
        "components": ["app"]}))
    os.symlink("real.json", rp)
    assert source_registry.read_record(paths, "src/app") is None        # symlink leaf refused
    # a record claiming a DIFFERENT source_rel than its filename identity is refused
    rp.unlink()
    rp.write_text(json.dumps({
        "version": 1, "source_rel": "src/evil", "remote": "", "selector": "legacy",
        "resolved_commit": "", "adopted_at": 1.0, "txn_id": "", "strategy": "",
        "components": ["app"]}))
    assert source_registry.read_record(paths, "src/app") is None


# --- transactional write on adoption ------------------------------------------------------

def test_adopt_writes_registry_record(tmp_path):
    head = _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    action = inst.adopt_source(comp, source="dev")                      # local fallback, no remote
    assert action.status == "done"
    rec = _rec(inst)
    assert rec is not None
    assert rec.selector == "dev" and rec.resolved_commit == head
    assert rec.components == ("app",) and rec.txn_id                    # txn-bound record
    # journal is gone (transaction committed)
    assert not inst._journal_path(inst.paths.under("src", "app")).exists()


def test_shared_source_record_lists_all_consumers(tmp_path):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    sibling = Component(id="app2", name="app2", kind=ComponentKind.SERVICE,
                        source=SourceSpec(path="src/app", local_dir="app"))
    inst = _inst(tmp_path, comp, extra=(sibling,))
    assert inst.adopt_source(comp, source="dev").status == "done"
    assert set(_rec(inst).components) == {"app", "app2"}


def test_failed_adoption_writes_no_record(tmp_path):
    comp = _comp()                                                      # no remote, no local
    inst = _inst(tmp_path, comp)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed"
    assert _rec(inst) is None


def test_pinned_adopt_records_pin_commit(tmp_path):
    head = _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp(pin=head)
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="pinned").status == "done"
    rec = _rec(inst)
    assert rec.selector == "pinned" and rec.resolved_commit == head


# --- record-write failure is recovery-required, then recovered from the journal ------------

def _advance_local(tmp_path, text="v2\n"):
    (tmp_path / "rt" / "local" / "app" / "file.txt").write_text(text)
    _git(tmp_path / "rt" / "local" / "app", "add", "-A")
    _git(tmp_path / "rt" / "local" / "app", "commit", "-qm", "v2")
    return _git(tmp_path / "rt" / "local" / "app", "rev-parse", "HEAD")


def test_record_write_failure_on_update_rolls_back_in_process(tmp_path, monkeypatch):
    # A registry-write failure during an UPDATE must not leave the new tree active under
    # old metadata: the activation ROLLS BACK to the verified `.prev`, the prior record
    # (never touched) still matches, and the journal is cleared (proven rollback).
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"       # v1 active + recorded
    old = _rec(inst)
    _advance_local(tmp_path)
    monkeypatch.setattr(Installer, "_write_registry_record", lambda *a, **k: False)
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "rolled back" in action.detail
    dest = inst.paths.under("src", "app")
    assert (dest / "file.txt").read_text() == "hello\n"                 # PRIOR tree restored
    assert _rec(inst) == old                                            # prior record intact
    assert not inst._journal_path(dest).exists()                        # journal cleared
    assert not dest.with_name(".app.prev").exists()                     # no .prev orphan
    # the source stays fully operable: a later update (write OK) succeeds
    monkeypatch.undo()
    assert inst.adopt_source(comp, force=True, source="dev").status == "done"


def test_record_write_failure_on_fresh_install_undoes_in_process(tmp_path, monkeypatch):
    # Fresh install + persistent record-write failure: the promoted candidate is removed —
    # no active source, no record, no journal, never a success.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    monkeypatch.setattr(Installer, "_write_registry_record", lambda *a, **k: False)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "rolled back" in action.detail
    dest = inst.paths.under("src", "app")
    assert not dest.exists()                                            # no active source
    assert _rec(inst) is None                                           # no record
    assert not inst._journal_path(dest).exists()                        # no journal


def _crash_state_after_activation(tmp_path, inst, had_prior: bool, text="v2\n"):
    """Craft the post-crash state of an activation whose record write never happened:
    dest = the NEW tree, `.prev` = the prior tree (update only), journal state `activated`
    with v3 meta (new HEAD + had_prior)."""
    import shutil
    dest = inst.paths.under("src", "app")
    new_head = _advance_local(tmp_path, text)
    if had_prior:
        dest.rename(dest.with_name(".app.prev"))                        # archive the prior
    else:
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(tmp_path / "rt" / "local" / "app", dest, symlinks=True)    # the NEW tree at dest
    rel = lambda q: str(q.relative_to(inst.paths.runtime_root))
    staging = dest.with_name(".app.candidate-1-2")
    cand_rel = rel(staging)

    def ident(q):
        try:
            st = os.stat(q, follow_symlinks=False)
            return [st.st_dev, st.st_ino, st.st_ctime_ns]   # v5 ctime-hardened ident
        except OSError:
            return None
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "dev", "resolved_commit": new_head, "remote": "",
                 "strategy": "", "components": ["app"], "had_prior": had_prior},
        "idents": {"candidate": ident(dest),           # dest IS the promoted candidate
                   "prev": ident(dest.with_name(".app.prev"))}}))
    return dest, new_head


def test_recovery_restores_prior_when_record_still_unwritable(tmp_path, monkeypatch):
    # CRASH between activation and record write, and the record STILL cannot persist during
    # recovery (one retry): recovery rolls back to `.prev`; the prior record still matches.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"       # v1 active + recorded
    old = _rec(inst)
    dest, _ = _crash_state_after_activation(tmp_path, inst, had_prior=True)
    monkeypatch.setattr(Installer, "_write_registry_record", lambda *a, **k: False)
    msgs = inst.recover_source_activations()
    assert any("rolled back" in m for m in msgs)
    assert (dest / "file.txt").read_text() == "hello\n"                 # prior tree restored
    assert _rec(inst) == old                                            # prior record intact
    assert not inst._journal_path(dest).exists()                        # journal cleared
    # recovery with the write WORKING completes the record instead (normal path)
    monkeypatch.undo()
    dest, new_head = _crash_state_after_activation(tmp_path, inst, had_prior=True,
                                                   text="v3\n")
    msgs = inst.recover_source_activations()
    assert any("recovered" in m for m in msgs)
    assert _rec(inst).resolved_commit == new_head                       # record completed


def test_recovery_undoes_fresh_install_when_record_still_unwritable(tmp_path, monkeypatch):
    # CRASH after a FRESH install's activation; record write keeps failing: recovery removes
    # the tree — no active source, no record, no falsely successful state.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    # simulate: the record from the first install never existed (fresh-install crash)
    source_registry.remove_record(inst.paths, "src/app")
    dest, _ = _crash_state_after_activation(tmp_path, inst, had_prior=False)
    monkeypatch.setattr(Installer, "_write_registry_record", lambda *a, **k: False)
    msgs = inst.recover_source_activations()
    assert any("rolled back fresh install" in m for m in msgs)
    assert not dest.exists()                                            # no active source
    assert _rec(inst) is None                                           # no record
    assert not inst._journal_path(dest).exists()                        # no journal


def test_recovery_of_rolled_back_state_writes_no_record(tmp_path):
    # dest holds the (restored) PRIOR tree; a retained v3 journal claims a DIFFERENT commit.
    # Recovery must clear the journal WITHOUT re-registering the prior under the new metadata.
    head = _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    staging = dest.with_name(".app.candidate-1-2")
    cand_rel = rel(staging)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "stable", "resolved_commit": "f" * 40,
                 "remote": "", "strategy": "", "components": ["app"]},
        "idents": {"candidate": None, "prev": None}}))
    msgs = inst.recover_source_activations()
    assert any("active source intact" in m for m in msgs)
    assert not inst._journal_path(dest).exists()                        # journal cleared
    rec = _rec(inst)
    assert rec.resolved_commit == head and rec.selector == "dev"        # prior record UNTOUCHED


def test_v3_journal_with_invalid_meta_is_retained(tmp_path):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    cand_rel = rel(dest.with_name(".app.candidate-1-2"))
    inst._journal_path(dest).write_text(json.dumps({
        "version": 5, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel),
        "meta": {"selector": "evil", "resolved_commit": 5},             # invalid meta
        "idents": {"candidate": None, "prev": None}}))
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "invalid" in m for m in msgs)
    assert inst._journal_path(dest).exists()                            # retained, blocks


def test_v2_journal_recovery_is_generation_blocked(tmp_path):
    # Legacy v2 journal (no identity evidence): automatic recovery REFUSES — nothing is
    # promoted, restored, or cleaned; the journal is retained with an operator diagnostic,
    # and further source mutation stays blocked.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    dest = inst.paths.under("src", "app")
    dest.mkdir(parents=True)
    (dest / "marker").write_text("LIVE")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    cand_rel = rel(dest.with_name(".app.candidate-1-2"))
    d = inst.paths.under("state", "source-txn")
    d.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 2, "state": "activated", "source_rel": rel(dest),
        "prev_rel": rel(dest.with_name(".app.prev")), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel)}))
    msgs = inst.recover_source_activations()
    assert any("generation" in m and "recovery-required" in m for m in msgs)
    assert (dest / "marker").read_text() == "LIVE"                      # nothing touched
    assert inst._journal_path(dest).exists()                            # journal retained
    assert _rec(inst) is None                                           # no fabricated ownership
    blocked = inst.adopt_source(comp, force=True, source="dev")
    assert blocked.status == "failed" and "recovery-required" in blocked.detail


# --- verify_or_backfill (legacy ownership proof) --------------------------------------------

def _svc_bits(tmp_path, remote):
    comp = _comp(remote=remote)
    inst = _inst(tmp_path, comp)
    dest = inst.paths.under("src", "app")
    return comp, inst, dest


def test_backfill_accepts_matching_origin(tmp_path):
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    head = _make_repo(dest)
    _git(dest, "remote", "add", "origin", "https://github.com/x/y.git")
    rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  comp, dest, components=("app",))
    assert rec is not None and why == "backfilled"
    assert rec.selector == "legacy" and rec.resolved_commit == head
    assert _rec(inst) is not None                                       # persisted


def test_backfill_normalizes_ssh_vs_https(tmp_path):
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    _make_repo(dest)
    _git(dest, "remote", "add", "origin", "git@github.com:x/y.git")
    rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  comp, dest)
    assert rec is not None and why == "backfilled"


def test_backfill_refuses_mismatched_origin(tmp_path):
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    _make_repo(dest)
    _git(dest, "remote", "add", "origin", "https://github.com/other/z.git")
    rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  comp, dest)
    assert rec is None and "does not match" in why
    assert _rec(inst) is None                                           # nothing persisted


def test_backfill_refuses_unknown_tree_and_missing_remote(tmp_path):
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    dest.mkdir(parents=True)
    (dest / "data.txt").write_text("user data")                         # NOT a git checkout
    rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  comp, dest)
    assert rec is None and "not a git checkout" in why
    # a git tree but NO configured remote -> ownership not provable
    comp2, inst2, dest2 = _svc_bits(tmp_path / "b", "")
    _make_repo(dest2)
    _git(dest2, "remote", "add", "origin", "https://github.com/x/y.git")
    rec2, why2 = source_registry.verify_or_backfill(inst2.paths, inst2.system, inst2.config,
                                                    comp2, dest2)
    assert rec2 is None and "no configured remote" in why2


def test_registered_record_wins_over_backfill(tmp_path):
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    _make_repo(dest)
    rec = source_registry.RegistryRecord("src/app", "https://github.com/x/y.git", "pinned",
                                         "a" * 40, 1.0, "t" * 64, "", ("app",))
    assert source_registry.write_record(inst.paths, rec)
    got, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  comp, dest)
    assert got == rec and why == "registered"                           # no git needed


def test_backfill_linked_source(tmp_path):
    # backfill-link is legitimate ONLY for a manifest-declared link strategy; a symlink at
    # a non-link source is refused (not an LHPC adoption).
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    external = tmp_path / "external"
    head = _make_repo(external)
    dest.parent.mkdir(parents=True)
    os.symlink(str(external), dest)                                     # linked adoption leaf
    rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  comp, dest)
    assert rec is None and "unexpected symlink" in why                  # non-link comp: refused
    link_comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                          source=SourceSpec(path="src/app", local_dir="app",
                                            remote="https://github.com/x/y.git",
                                            strategy="link"))
    rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                  link_comp, dest)
    assert rec is not None and why == "backfilled-link"
    assert rec.strategy == "link" and rec.link_target == str(external)
    assert rec.resolved_commit == ""          # the external tree is mutable — never pinned
    assert head                               # (sanity: the external repo exists)


# --- M2: dirty_report — untracked counts, with the regenerable-artifact carve-out -----------

def test_dirty_report_untracked_blocks_but_artifacts_do_not(tmp_path):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE, bin="out/app.bin",
                     source=SourceSpec(path="src/app", local_dir="app"))
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    assert not inst.dirty_report(dest, "src/app")                       # clean after adopt
    # a TRACKED modification is dirty
    (dest / "file.txt").write_text("edited\n")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any("file.txt" in p for p in rep.tracked)
    _git(dest, "checkout", "--", "file.txt")
    # a plain UNTRACKED file is dirty (never silently discarded)
    (dest / "notes.txt").write_text("operator notes")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any("notes.txt" in p for p in rep.untracked)
    (dest / "notes.txt").unlink()
    # regenerable artifacts do NOT count: ignore-dir names + the declared built binary
    (dest / "build").mkdir()
    (dest / "build" / "obj.o").write_text("obj")
    (dest / "__pycache__").mkdir()
    (dest / "__pycache__" / "m.pyc").write_text("pyc")
    (dest / "out").mkdir()
    (dest / "out" / "app.bin").write_text("ELF")                        # declared comp.bin
    assert not inst.dirty_report(dest, "src/app")
    # .gitignore'd files never count (untracked-files=normal honours it)
    (dest / ".gitignore").write_text("*.log\n")
    _git(dest, "add", ".gitignore"); _git(dest, "commit", "-qm", "ignore")
    (dest / "run.log").write_text("log")
    assert not inst.dirty_report(dest, "src/app")


def test_update_overwrite_refuses_untracked_changes(tmp_path):
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "precious.txt").write_text("operator work")                 # untracked, non-ignored
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "local modifications" in action.detail
    assert "precious.txt" in action.detail                              # itemized
    assert (dest / "precious.txt").exists()                            # nothing discarded


# --- M3: selector semantics — git-only stable, artifact sources, strict dev -----------------

def _tagged_repo(path: Path):
    """A repo with: version tags v0.9.0 < v1.2.0 (v1.2.0 on an OLDER commit than a
    non-version tag 'nightly' that is NEWEST by date) + a final untagged commit."""
    _make_repo(path)
    _git(path, "tag", "v0.9.0")
    (path / "file.txt").write_text("two\n")
    _git(path, "add", "-A"); _git(path, "commit", "-qm", "two")
    _git(path, "tag", "v1.2.0")
    v120 = _git(path, "rev-parse", "HEAD")
    (path / "file.txt").write_text("three\n")
    _git(path, "add", "-A"); _git(path, "commit", "-qm", "three")
    _git(path, "tag", "nightly")                       # newest by date, NOT version-shaped
    (path / "file.txt").write_text("four\n")
    _git(path, "add", "-A"); _git(path, "commit", "-qm", "four")
    return v120


def test_stable_resolves_newest_version_tag(tmp_path):
    v120 = _tagged_repo(tmp_path / "repo")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    tag = inst._resolve_stable_tag(str(tmp_path / "repo"))
    assert tag == "v1.2.0"                             # version tag beats newer-dated 'nightly'
    assert v120                                        # (sanity)


def test_stable_falls_back_to_newest_tag_then_head(tmp_path):
    # only NON-version tags -> newest by creation date
    repo = tmp_path / "r1"
    _make_repo(repo)
    _git(repo, "tag", "alpha")
    (repo / "file.txt").write_text("2\n")
    _git(repo, "add", "-A")
    # a DISTINCT, later committer date so `-creatordate` ordering is deterministic
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_COMMITTER_DATE": "2030-01-01T00:00:00", "GIT_AUTHOR_DATE": "2030-01-01T00:00:00"}
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "2"], check=True,
                   capture_output=True, env=env)
    _git(repo, "tag", "beta")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst._resolve_stable_tag(str(repo)) == "beta"
    # NO tags at all -> "" (caller stays on the default-branch HEAD)
    repo2 = tmp_path / "r2"
    _make_repo(repo2)
    assert inst._resolve_stable_tag(str(repo2)) == ""


def test_artifact_source_same_for_every_selector(tmp_path):
    # An artifact source adopts the SAME declared artifact for pinned/dev/stable — including
    # `pinned` with NO configured pin (no unverified-blocked for artifacts).
    head = _make_repo(tmp_path / "rt" / "local" / "app")
    for sel in ("pinned", "dev", "stable"):
        comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                         source=SourceSpec(path="src/app", local_dir="app", artifact=True))
        inst = _inst(tmp_path / sel, comp)
        (tmp_path / sel / "rt" / "local").mkdir(parents=True, exist_ok=True)
        (tmp_path / sel / "rt" / "local" / "app").symlink_to(
            tmp_path / "rt" / "local" / "app")
        action = inst.adopt_source(comp, source=sel)
        assert action.status == "done", f"{sel}: {action.detail}"
        assert action.provenance == "artifact-head"
        assert _rec(inst).resolved_commit == head      # identical resolution


def test_dev_unavailable_branch_is_typed(tmp_path):
    # dev with a configured branch the local fallback is NOT on: the SELECTOR is unavailable —
    # never a silent adoption of a different ref.
    _make_repo(tmp_path / "rt" / "local" / "app")             # on master/main, not 'feature/x'
    comp = _comp(branch="feature/x")
    inst = _inst(tmp_path, comp)
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed"
    assert "selector unavailable" in action.detail and "feature/x" in action.detail
    assert _rec(inst) is None


def test_shared_path_coherence_check(tmp_path):
    from lhpc.core.manifest import parse_manifest, ManifestError
    import pytest
    base = {
        "stack": [{
            "id": "s", "name": "s", "main": "a",
            "component": [
                {"id": "a", "name": "a", "kind": "service", "run": "true",
                 "readiness": "process",
                 "source": {"path": "src/x", "remote": "https://github.com/x/y.git"}},
                {"id": "b", "name": "b", "kind": "service", "run": "true",
                 "readiness": "process",
                 "source": {"path": "src/x", "remote": "https://github.com/OTHER/z.git"}},
            ],
        }],
    }
    with pytest.raises(ManifestError, match="share source path"):
        parse_manifest(base)
    base["stack"][0]["component"][1]["source"]["remote"] = "https://github.com/x/y.git"
    assert parse_manifest(base)                        # identical specs -> valid


# --- current-identity gate on UPDATE + hostile destination leaves ---------------------------

def test_update_refuses_unknown_non_git_tree(tmp_path):
    # An existing CLEAN tree that is not a git checkout (and unregistered) is unknown —
    # update refuses and changes nothing.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    dest = inst.paths.under("src", "app")
    dest.mkdir(parents=True)
    (dest / "data.txt").write_text("operator data")
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "ownership/identity not proven" in action.detail
    assert (dest / "data.txt").read_text() == "operator data"           # tree unchanged


def test_update_refuses_wrong_origin(tmp_path):
    # An existing clean git tree whose origin differs from the configured remote is not
    # LHPC's adoption — update refuses, tree unchanged.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp(remote="https://github.com/x/y.git")
    inst = _inst(tmp_path, comp)
    dest = inst.paths.under("src", "app")
    _make_repo(dest)
    _git(dest, "remote", "add", "origin", "https://github.com/OTHER/z.git")
    before = _git(dest, "rev-parse", "HEAD")
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "ownership/identity not proven" in action.detail
    assert _git(dest, "rev-parse", "HEAD") == before                    # tree unchanged


def test_update_refuses_registered_source_at_drifted_commit(tmp_path):
    # A registered source manually moved to a different CLEAN commit: update refuses.
    head1 = _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "file.txt").write_text("moved\n")
    _git(dest, "add", "-A"); _git(dest, "commit", "-qm", "moved")       # clean, NEW commit
    action = inst.adopt_source(comp, force=True, source="dev")
    assert action.status == "failed" and "identity drift" in action.detail
    assert _git(dest, "rev-parse", "HEAD") != head1                     # tree left as found


def test_install_and_update_refuse_hostile_destination_leaves(tmp_path):
    # A dangling symlink, a regular file, or a special leaf at the destination is NOT an
    # installable empty destination: refuse with ZERO rename/cleanup/deletion.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = _comp()
    for maker, label in (
        (lambda d: os.symlink("does-not-exist", d), "dangling symlink"),
        (lambda d: d.write_text("a file"), "regular file"),
        (lambda d: os.mkfifo(d), "special"),
    ):
        root = tmp_path / label.replace(" ", "-")
        inst = _inst(root if False else tmp_path, comp)                 # fresh rt per case below
        # per-case runtime root to isolate
        from lhpc.core.paths import Paths as _P
        from lhpc.core.config import Config as _C
        from lhpc.core.probes import RealSystem as _RS
        inst = Installer(_P(runtime_root=root / "rt"), inst.stacks,
                         _C(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}}),
                         _RS())
        dest = inst.paths.under("src", "app")
        dest.parent.mkdir(parents=True)
        maker(dest)
        for force in (False, True):                                     # install AND update
            action = inst.adopt_source(comp, force=force, source="dev")
            assert action.status == "failed", (label, force, action.detail)
            assert "refusing" in action.detail
        assert os.path.lexists(dest), label                             # leaf untouched
        assert not dest.with_name(".app.prev").exists()                 # zero rename
        assert _rec(inst) is None


# --- FINAL M1: tri-state registry, handle-bound backfill, strategy identity ------------------

def _mk_unsafe_registry(paths, rel, shape):
    rp = source_registry.record_path(paths, rel)
    rp.parent.mkdir(parents=True, exist_ok=True)
    if shape == "malformed":
        rp.write_text("not json {{{")
    elif shape == "symlinked":
        (rp.parent / "real.json").write_text("{}")
        os.symlink("real.json", rp)
    elif shape == "dangling":
        os.symlink("does-not-exist", rp)
    elif shape == "directory":
        rp.mkdir()
    elif shape == "special":
        os.mkfifo(rp)
    elif shape == "inaccessible":
        rp.write_text("{}")
        rp.chmod(0)
    return rp


def test_unsafe_registry_states_block_everything(tmp_path):
    # Every PRESENT-but-unsafe registry state blocks update/adopt-over-existing, and the
    # tri-state reader reports it distinctly ("unsafe", never "absent").
    import pytest
    shapes = ["malformed", "symlinked", "dangling", "directory", "special"]
    if os.geteuid() != 0:
        shapes.append("inaccessible")
    for shape in shapes:
        root = tmp_path / shape
        head = _make_repo(root / "rt" / "local" / "app")
        comp = _comp()
        inst = _inst(root, comp)
        assert inst.adopt_source(comp, source="dev").status == "done"    # genuine install
        source_registry.remove_record(inst.paths, "src/app")
        _mk_unsafe_registry(inst.paths, "src/app", shape)
        state, rec, why = source_registry.record_state(inst.paths, "src/app")
        assert state == "unsafe" and rec is None and why, shape
        action = inst.adopt_source(comp, force=True, source="dev")       # update blocked
        assert action.status == "failed", shape
        assert "unsafe" in action.detail or "malformed" in action.detail \
            or "unreadable" in action.detail or "validation" in action.detail, shape
        dest = inst.paths.under("src", "app")
        assert (dest / "file.txt").exists(), shape                       # zero source mutation


def test_unsafe_registry_blocks_uninstall_clean_and_confirm(tmp_path):
    from lhpc.core import known_working
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    paths = Paths(runtime_root=tmp_path)
    dest = tmp_path / "src" / "loraham-kiss-tnc"
    dest.mkdir(parents=True)
    _mk_unsafe_registry(paths, "src/loraham-kiss-tnc", "malformed")
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok and any("malformed" in d or "unsafe" in d for d in res.details)
    assert dest.exists()
    res2 = svc.clean("kiss", apply=True, purge=True)
    assert not res2.ok and dest.exists()
    # confirmation path
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    _mk_unsafe_registry(paths, "src/LoRaHAM_Daemon", "malformed")
    entries = {"loraham-chat": {"commit": "a" * 40, "selector": "dev", "remote": "",
                                "source_rel": "src/LoRaHAM_Daemon", "strategy": ""}}
    assert known_working.write_candidate(paths, "chat", entries, "433")
    svc2 = ControllerService(system=FakeSystem(cmdlines_data={5: ["loraham_chat"]}).system,
                             paths=paths)
    res3 = svc2.confirm_known_working("chat")
    assert not res3.ok
    assert known_working.load(paths, "chat") == []
    # SAFELY ABSENT still permits genuine legacy backfill (existing coverage re-proven)
    state, _, _ = source_registry.record_state(paths, "src/never-touched")
    assert state == "absent"


def test_backfill_never_registers_substituted_leaf(tmp_path):
    # Capture a handle on the ORIGINAL tree, replace the path leaf, then backfill with the
    # stale handle: inspection runs on the CAPTURED inode, the pre-persist re-proof fails,
    # nothing is registered, nothing mutated.
    import shutil
    from lhpc.core import source_fs
    comp, inst, dest = _svc_bits(tmp_path, "https://github.com/x/y.git")
    _make_repo(dest)
    _git(dest, "remote", "add", "origin", "https://github.com/x/y.git")
    handle = source_fs.capture_leaf(inst.paths, dest)
    try:
        shutil.move(str(dest), str(tmp_path / "stolen"))
        dest.mkdir()
        (dest / "unknown.txt").write_text("substitute")
        rec, why = source_registry.verify_or_backfill(inst.paths, inst.system, inst.config,
                                                      comp, dest, handle=handle)
        assert rec is None and "concurrently replaced" in why
        assert source_registry.read_record(inst.paths, "src/app") is None   # NOT registered
        assert (dest / "unknown.txt").exists()                              # untouched
    finally:
        handle.close()


def test_link_target_substitution_blocks_destructive_ops(tmp_path):
    # A registered link whose runtime symlink was RE-POINTED is identity drift.
    import time as _t
    comp, inst, dest = _svc_bits(tmp_path, "")
    target_a = tmp_path / "target-a"; target_a.mkdir()
    target_b = tmp_path / "target-b"; target_b.mkdir()
    dest.parent.mkdir(parents=True)
    os.symlink(str(target_a), dest)
    assert source_registry.write_record(inst.paths, source_registry.RegistryRecord(
        "src/app", "", "legacy", "", _t.time(), "", "link", ("app",),
        link_target=str(target_a)))
    rec, why = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                               comp, dest)
    assert rec is not None and why == "verified"                     # genuine target ok
    dest.unlink()
    os.symlink(str(target_b), dest)                                  # RE-POINTED
    rec2, why2 = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                                 comp, dest)
    assert rec2 is None and "link target" in why2
    assert dest.is_symlink() and os.readlink(dest) == str(target_b)  # untouched


def test_non_git_directory_is_never_destructively_authorized(tmp_path):
    # A registered path occupied by a clean NON-git directory with nothing provable
    # (no commit, no origin) is NOT ownership — refuse destructive authorization.
    import time as _t
    comp, inst, dest = _svc_bits(tmp_path, "")
    dest.mkdir(parents=True)
    (dest / "replaced.txt").write_text("manually placed")
    assert source_registry.write_record(inst.paths, source_registry.RegistryRecord(
        "src/app", "", "legacy", "", _t.time(), "", "", ("app",)))
    rec, why = source_registry.verify_identity(inst.paths, inst.system, inst.config,
                                               comp, dest)
    assert rec is None and "unprovable" in why
    assert (dest / "replaced.txt").exists()                          # never deleted


def test_dirty_carveout_is_exact_leaf_only(tmp_path):
    # Only the EXACT declared generated binary is ignorable; sibling/nested/unusual
    # untracked files — including newline-containing names — block. NUL-safe parsing.
    _make_repo(tmp_path / "rt" / "local" / "app")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE, bin="out/app.bin",
                     source=SourceSpec(path="src/app", local_dir="app"))
    inst = _inst(tmp_path, comp)
    assert inst.adopt_source(comp, source="dev").status == "done"
    dest = inst.paths.under("src", "app")
    (dest / "out").mkdir()
    (dest / "out" / "app.bin").write_text("ELF")
    assert not inst.dirty_report(dest, "src/app")                    # exact leaf allowed
    # a SIBLING under the binary's parent blocks (the dir is not ignorable wholesale)
    (dest / "out" / "notes.txt").write_text("user data")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any("notes.txt" in p for p in rep.untracked)
    (dest / "out" / "notes.txt").unlink()
    # a NESTED file under the parent blocks too
    (dest / "out" / "deep").mkdir()
    (dest / "out" / "deep" / "x").write_text("x")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any("deep/x" in p for p in rep.untracked)
    import shutil as _sh
    _sh.rmtree(dest / "out" / "deep")
    # newline/quote names parse EXACTLY (NUL-safe) and block
    weird = dest / 'we"ird\nname.txt'
    weird.write_text("x")
    rep = inst.dirty_report(dest, "src/app")
    assert rep and any(p == 'we"ird\nname.txt' for p in rep.untracked)
    weird.unlink()
    assert not inst.dirty_report(dest, "src/app")                    # clean again

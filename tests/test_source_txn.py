"""§10 — source activation transaction: an interrupted update is finished or rolled
back, the active source is never left missing, concurrent updates are blocked, and
recovery never touches an untrusted (escaping) journal target."""

import json

from pathlib import Path

from lhpc.core.install import Installer
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack


def _inst(tmp_path) -> Installer:
    cfg = Config(values={"install": {"adopt_search_root": str(tmp_path / "rt")}})
    # Declare src/app as a MANAGED source so recovery accepts its journal (§1: recovery
    # only ever operates on manifest-declared managed-source destinations).
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    return Installer(Paths(runtime_root=tmp_path / "rt"), stacks, cfg, RealSystem())


def _ident_of(p):
    import os as _os
    try:
        st = _os.stat(p, follow_symlinks=False)
        return [st.st_dev, st.st_ino]
    except OSError:
        return None


def _journal(inst, dest, prev, staging, state, version=4):
    # v4 journal with LOGICAL runtime-relative names + leaf-identity evidence computed from
    # the on-disk leaves the test just created (v2 crafted journals are generation-blocked).
    d = inst.paths.under("state", "source-txn")
    d.mkdir(parents=True, exist_ok=True)
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    cand_rel = rel(staging)
    payload = {
        "version": version, "state": state, "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": cand_rel,
        "txn_id": inst._txn_id(cand_rel)}
    if version == 4:
        payload["meta"] = {"selector": "legacy", "resolved_commit": "", "remote": "",
                           "strategy": "", "components": [dest.name]}
        payload["idents"] = {"candidate": _ident_of(staging), "prev": _ident_of(prev)}
    inst._journal_path(dest).write_text(json.dumps(payload))


def _fin(inst, dest, prev, staging):
    """Open the journal as an OwnedMarker and drive _finish_or_rollback (recovery API),
    supplying v4-style leaf-identity evidence computed from the on-disk leaves."""
    from lhpc.core import runtime_fs
    jf = inst._journal_path(dest); jf.parent.mkdir(parents=True, exist_ok=True)
    if not jf.exists():
        jf.write_text("{}")
    m = runtime_fs.open_existing_marker(inst.paths, jf)
    try:
        return inst._finish_or_rollback(
            dest, prev, staging, m,
            idents={"candidate": _ident_of(staging), "prev": _ident_of(prev)})
    finally:
        m.close()


def _fail_noreplace(monkeypatch, suffixes=(".app.candidate-1-2", ".app.prev"),
                    plant_dangling=False):
    """Redirect the failure-injection seam to the ATOMIC promotion primitive
    (`source_fs._rename_noreplace_at`) the activation now uses instead of os.rename."""
    import os as _os
    from lhpc.core import source_fs as _sf
    real = _sf._rename_noreplace_at
    def failing(parent_fd, old, new):
        if any(old.endswith(sfx) for sfx in suffixes):
            if plant_dangling and old.endswith(".app.candidate-1-2"):
                _os.symlink("gone", new, dir_fd=parent_fd)   # race: dangling symlink at dest
            raise OSError("simulated rename failure")
        return real(parent_fd, old, new)
    monkeypatch.setattr(_sf, "_rename_noreplace_at", failing)


def test_recover_rolls_back_after_prior_archived(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    prev = src / ".app.prev"
    prev.mkdir(); (prev / "marker").write_text("PRIOR")     # active was archived, dest gone
    _journal(inst, dest, prev, src / ".app.candidate-1-2", "prior-archived")
    msgs = inst.recover_source_activations()
    assert dest.is_dir() and (dest / "marker").read_text() == "PRIOR"   # restored
    assert any("rolled back" in m for m in msgs)


def test_recover_completes_activation(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"
    staging.mkdir(); (staging / "marker").write_text("NEW")  # died before staging->dest
    _journal(inst, dest, src / ".app.prev", staging, "prior-archived")
    inst.recover_source_activations()
    assert dest.is_dir() and (dest / "marker").read_text() == "NEW"


def test_recover_leaves_active_intact(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "marker").write_text("LIVE")
    prev = src / ".app.prev"; prev.mkdir()
    _journal(inst, dest, prev, src / ".app.candidate-1-2", "prior-archived")
    inst.recover_source_activations()
    assert (dest / "marker").read_text() == "LIVE" and not prev.exists()  # prior cleaned


def test_recover_refuses_escaping_journal_path(tmp_path):
    # A journal whose source_rel escapes the runtime root must be retained + blocked,
    # and never touch the outside path.
    inst = _inst(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived",
        "source_rel": "../outside/app", "prev_rel": "../outside", "candidate_rel": "../outside"}))
    msgs = inst.recover_source_activations()
    assert (outside / "keep").read_text() == "KEEP"
    assert any("invalid activation journal" in m for m in msgs)
    assert (d / "app.json").exists()                     # journal retained


def test_recover_refuses_non_controller_candidate_name(tmp_path):
    # Even a contained journal is rejected if the candidate/prior names don't match the
    # controller's transaction naming (so an attacker can't point recovery at a victim).
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    (src / "victim").mkdir(); (src / "victim" / "x").write_text("V")
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    # Identity-bound filename for src/app (so it passes the filename check and REACHES the
    # non-controller candidate/prior name refusal — the point of this test).
    inst._journal_path(src / "app").write_text(json.dumps({
        "version": 2, "state": "prior-archived",
        "source_rel": "src/app", "prev_rel": "src/victim", "candidate_rel": "src/victim"}))
    msgs = inst.recover_source_activations()
    assert (src / "victim" / "x").read_text() == "V"     # victim untouched
    assert any("non-controller" in m for m in msgs)
    assert inst._journal_path(src / "app").exists()      # journal retained (evidence)


def test_shared_source_serializes_on_one_lock(tmp_path):
    # chat + igate share src/LoRaHAM_Daemon; a held lock on that source path blocks
    # an update of EITHER consumer.
    from lhpc.core import reslock
    inst = _inst(tmp_path)
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    inst.paths.under("src", "app").mkdir(parents=True)               # overwrite target
    with reslock.operation_lock(inst.paths, inst._source_lock_key("src/app"), "update", "x"):
        action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "in progress" in action.detail


# --- P0.3 local-fallback candidate verification ------------------------------

def _git(repo, *args):
    import subprocess, os
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _local_repo(tmp_path, name):
    import subprocess
    repo = tmp_path / "rt" / name; repo.mkdir(parents=True)
    _git(repo, "init", "-q"); (repo / "f").write_text("x"); _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    return repo, head


def test_fallback_pin_mismatch_blocks_activation(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src",
                                       pin_commit="deadbeef" * 5))   # wrong pin
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed" and "does not satisfy" in action.detail
    assert not inst.paths.under("src", "app").exists()              # active source untouched


def test_fallback_pin_match_activates(tmp_path):
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done" and inst.paths.under("src", "app").is_dir()


# --- P0.5 source-path lock contention ----------------------------------------

def test_build_blocked_by_held_source_lock(tmp_path):
    from lhpc.core import reslock
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/loraham-daemon"),
                                "update", "x"):
        res = svc.build("daemon", apply=True)
    assert not res.ok and "blocked" in res.summary.lower()


def test_uninstall_blocked_by_held_source_lock(tmp_path):
    from lhpc.core import reslock
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    src = svc._paths.under("src", "loraham-daemon"); src.mkdir(parents=True)
    with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/loraham-daemon"),
                                "update", "x"):
        res = svc.uninstall("daemon", apply=True)
    assert not res.ok and "blocked" in res.summary.lower()      # atomic guard fails closed


def test_adopt_blocks_when_recovery_required(tmp_path):
    # An unresolved/invalid journal for THIS source must block adopt/update before any
    # candidate creation (P0.2 caller enforcement).
    inst = _inst(tmp_path)
    inst.paths.under("src", "app").mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({          # invalid -> retained -> blocks
        "version": 2, "state": "prior-archived",
        "source_rel": "../escape", "prev_rel": "../escape", "candidate_rel": "../escape"}))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (d / "app.json").exists()                  # journal retained, source untouched


def test_activate_failed_restore_retains_journal(tmp_path, monkeypatch):
    # dest->prev archives, staging->dest fails, AND prev->dest restore fails ->
    # the journal MUST be retained (active source missing -> recovery-required).
    import os as _os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    real_rename = _os.rename
    def failing(a, b, *args, **kw):
        if str(a).endswith(".app.candidate-1-2") or str(a).endswith(".app.prev"):
            raise OSError("simulated rename failure")
        return real_rename(a, b, *args, **kw)
    monkeypatch.setattr("lhpc.core.install.os.rename", failing)
    _fail_noreplace(monkeypatch)                          # promotion is atomic NOREPLACE now
    assert inst._activate(dest, staging) == "recovery-required"
    assert inst._journal_path(dest).exists()             # journal RETAINED (recovery-required)


def test_adopt_blocked_by_filename_mismatch_journal(tmp_path):
    # A journal named app.json but declaring a different source is invalid -> retained
    # under app.json -> adopt of app is blocked.
    inst = _inst(tmp_path)
    inst.paths.under("src", "app").mkdir(parents=True)
    inst.paths.under("src", "other").mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived",
        "source_rel": "src/other", "prev_rel": "src/.other.prev",
        "candidate_rel": "src/.other.candidate-1-2"}))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "recovery-required" in action.detail


def test_fallback_stable_tag_mismatch_blocks(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_tag="v9.9.9"))
    action = inst.adopt_source(comp, source="stable")
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_fallback_dev_branch_mismatch_blocks(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")          # default branch (master/main), not "nope"
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", branch="nope"))
    action = inst.adopt_source(comp, source="dev")
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_host_test_blocked_by_held_source_lock(tmp_path):
    from lhpc.core import reslock
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/loraham-daemon"),
                                "update", "x"):
        res = svc.test("daemon", apply=True)          # host test (no --tx)
    assert not res.ok and "blocked" in res.summary.lower()


def test_unknown_prev_blocks_and_is_not_discarded(tmp_path):
    # A pre-existing .app.prev with NO active journal is an unowned orphan: activation
    # must block and must NOT recursively discard it.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    orphan = src / ".app.prev"; orphan.mkdir(); (orphan / "keep").write_text("ORPHAN")
    staging = src / ".app.candidate-9-9"; staging.mkdir(); (staging / "m").write_text("NEW")
    assert inst._activate(dest, staging) == "failed-clean"
    assert (orphan / "keep").read_text() == "ORPHAN"     # orphan untouched
    assert (dest / "m").read_text() == "LIVE"            # active source untouched


# --- P0 detached web-job launcher holds the source lock for its lifetime -----

def test_build_launcher_acquires_and_blocks_on_source_lock(tmp_path):
    import subprocess, sys, fcntl, os
    from lhpc.core import commands
    from lhpc.core.paths import Paths
    rt = tmp_path / "rt"
    locks = Paths(runtime_root=rt).under("state", "locks"); locks.mkdir(parents=True, exist_ok=True)
    lock = locks / "src.lock"; lock.touch()           # the runtime-structured source lock
    marker = tmp_path / "ran"
    # A step that creates a marker so we can prove it ran only when unlocked.
    steps = [{"argv": ["touch", str(marker)]}]
    script = commands.render_build_launcher(steps, str(rt), str(tmp_path), [str(lock)])
    launcher = tmp_path / "launch.py"; launcher.write_text(script)

    # 1) lock HELD by us -> launcher must fail fast (exit 3) and not run the step.
    fd = os.open(str(lock), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        env = {**os.environ, "LHPC_BUILD_LOCK_WAIT_S": "0.4"}
        r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
        assert r.returncode == 3 and "could not acquire source lock" in r.stderr
        assert not marker.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    # 2) lock FREE -> launcher acquires it, runs the step, exits 0.
    r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True)
    assert r.returncode == 0 and marker.exists()


def test_build_launcher_lock_contends_with_operation_lock(tmp_path):
    # The launcher's lock file is the SAME one reslock.operation_lock uses.
    import subprocess, sys
    from lhpc.core import commands, reslock
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    paths.under("state", "locks").mkdir(parents=True, exist_ok=True)
    lp = str(reslock.lock_file_path(paths, reslock.source_lock_key("src/app")))
    script = commands.render_build_launcher([{"argv": ["true"]}], str(tmp_path),
                                            str(tmp_path), [lp])
    launcher = tmp_path / "l.py"; launcher.write_text(script)
    import os
    env = {**os.environ, "LHPC_BUILD_LOCK_WAIT_S": "0.4"}
    with reslock.operation_lock(paths, reslock.source_lock_key("src/app"), "update", "x"):
        r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "another source operation is in progress" in r.stderr


# --- P0.3 link strategy must verify pinned/stable/dev selection --------------

def test_link_pinned_mismatch_rejected(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")            # HEAD != the wrong pin below
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src",
                                       strategy="link", pin_commit="deadbeef" * 5))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed" and "does not satisfy" in action.detail
    assert not inst.paths.under("src", "app").exists()       # nothing linked


def test_link_pinned_match_links(tmp_path):
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src",
                                       strategy="link", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done"
    assert (inst.paths.runtime_root / "src" / "app").is_symlink()


def test_link_dev_default_links(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", strategy="link"))
    action = inst.adopt_source(comp, source="dev")          # dev w/o branch -> permissive
    assert action.status == "done"
    assert (inst.paths.runtime_root / "src" / "app").is_symlink()


# --- P0.2 index lock + global blocking on any unresolved journal -------------

def test_malformed_journal_blocks_unrelated_source(tmp_path):
    # A malformed journal with NO safely derivable source must block ALL source mutation,
    # even for an unrelated source.
    inst = _inst(tmp_path)
    inst.paths.under("src", "app").mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{ not valid json")          # unparseable -> retained
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "recovery-required" in action.detail
    assert (d / "garbage.json").exists()                         # retained, not discarded


def test_adopt_blocked_while_index_lock_held(tmp_path):
    from lhpc.core import reslock
    inst = _inst(tmp_path)
    inst.paths.under("src", "app").mkdir(parents=True)
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    with reslock.operation_lock(inst.paths, inst._index_key(), "recover", "x"):
        action = inst.adopt_source(comp, force=True)
    assert action.status == "failed" and "in progress" in action.detail


# --- P0.5 version selection fails closed -------------------------------------

def test_pinned_without_configured_pin_rejected(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))   # NO pin_commit
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_stable_without_any_tag_rejected(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")                 # repo has NO tags
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))   # NO pin_tag
    action = inst.adopt_source(comp, source="stable")
    assert action.status == "failed" and "does not satisfy" in action.detail


def test_link_pinned_without_pin_rejected(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", strategy="link"))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed" and "does not satisfy" in action.detail


# --- P0.2 a valid TARGET journal is recovered through adopt (no self-contention) ---

def test_valid_target_journal_recovered_through_adopt(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    _journal(inst, dest, src / ".app.prev", staging, "prior-archived")   # interrupted activation
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=False)
    # Recovery COMPLETED the interrupted activation under the index lock, then adopt
    # proceeded — it did NOT become permanently "busy"/"recovery-required".
    assert action.status == "skipped" and "already exists" in action.detail
    assert dest.is_dir() and (dest / "m").read_text() == "NEW"
    assert not inst._journal_path(dest).exists()        # journal cleared by recovery


def test_adopt_target_does_not_self_contend(tmp_path):
    # Same source has a valid completing journal; adopt(force) must recover it and then
    # re-stage, never blocking on its own source lock.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    _journal(inst, dest, src / ".app.prev", src / ".app.candidate-1-2", "prior-archived")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src"))
    (tmp_path / "rt" / "app-src").mkdir(parents=True)
    action = inst.adopt_source(comp, force=True)
    assert action.status != "failed" or "in progress" not in action.detail
    assert not inst._journal_path(dest).exists()


# --- P0.4 _activate returns structured state; evidence preserved -------------

def test_recovery_required_preserves_candidate_and_prior(tmp_path, monkeypatch):
    import os as _os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    real = _os.rename
    def failing(a, b, *args, **kw):
        if str(a).endswith(".app.candidate-1-2") or str(a).endswith(".app.prev"):
            raise OSError("simulated rename failure")
        return real(a, b, *args, **kw)
    monkeypatch.setattr("lhpc.core.install.os.rename", failing)
    _fail_noreplace(monkeypatch)                          # promotion is atomic NOREPLACE now
    assert inst._activate(dest, staging) == "recovery-required"
    assert staging.is_dir() and (staging / "m").read_text() == "NEW"   # candidate PRESERVED
    assert inst._journal_path(dest).exists()                            # journal retained


def test_journal_unlink_failure_after_activation_is_recovery_required(tmp_path, monkeypatch):
    from lhpc.core import runtime_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    # The activation renames succeed, but the owned-journal removal fails -> typed
    # recovery-required (never an untyped exception), journal retained.
    monkeypatch.setattr(runtime_fs.OwnedMarker, "remove", lambda self: False)
    assert inst._activate(dest, staging) == "recovery-required"
    assert (dest / "m").read_text() == "NEW"                  # activation DID happen
    assert inst._journal_path(dest).exists()                  # journal retained for recovery


def test_malformed_journal_blocks_build_and_uninstall(tmp_path):
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    src = svc._paths.under("src", "loraham-daemon"); src.mkdir(parents=True)
    d = svc._paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{ not valid")        # unresolved -> blocks all mutation
    rb = svc.build("daemon", apply=True)
    assert not rb.ok and "blocked" in rb.summary.lower()
    ru = svc.uninstall("daemon", apply=True)
    assert any("blocked" in x.lower() for x in ([ru.summary] + list(ru.details)))


# --- P0.3 detached launcher index-to-source handoff --------------------------

def _render_launcher(tmp_path, marker, with_journal):
    from lhpc.core import commands, reslock
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    paths.under("state", "locks").mkdir(parents=True, exist_ok=True)
    txn = paths.under("state", "source-txn"); txn.mkdir(parents=True, exist_ok=True)
    if with_journal:
        (txn / "garbage.json").write_text("{ unresolved")
    idx = str(reslock.lock_file_path(paths, "source-txn-index"))
    script = commands.render_build_launcher([{"argv": ["touch", str(marker)]}], str(tmp_path),
                                            str(tmp_path), [], index_lock=idx, txn_dir=str(txn))
    launcher = tmp_path / "l.py"; launcher.write_text(script)
    return launcher


def test_detached_launcher_blocks_on_pending_journal(tmp_path):
    import subprocess, sys, os
    marker = tmp_path / "ran"
    launcher = _render_launcher(tmp_path, marker, with_journal=True)
    env = {**os.environ, "LHPC_BUILD_LOCK_WAIT_S": "0.4"}
    r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "unresolved source-transaction journal" in r.stderr
    assert not marker.exists()                    # never touched the source


def test_detached_launcher_runs_when_no_journal(tmp_path):
    import subprocess, sys, os
    marker = tmp_path / "ran"
    launcher = _render_launcher(tmp_path, marker, with_journal=False)
    env = {**os.environ, "LHPC_BUILD_LOCK_WAIT_S": "0.4"}
    r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and marker.exists()


def test_detached_launcher_blocks_while_index_held(tmp_path):
    import subprocess, sys, os
    from lhpc.core import reslock
    from lhpc.core.paths import Paths
    marker = tmp_path / "ran"
    launcher = _render_launcher(tmp_path, marker, with_journal=False)
    paths = Paths(runtime_root=tmp_path)
    env = {**os.environ, "LHPC_BUILD_LOCK_WAIT_S": "0.4"}
    with reslock.operation_lock(paths, "source-txn-index", "adopt", "x"):
        r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "index busy" in r.stderr
    assert not marker.exists()


# --- P0.1 atomic source-operation guard: a retained journal blocks every op ---

def test_retained_journal_blocks_every_source_op(tmp_path):
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc._paths.under("src", "loraham-daemon").mkdir(parents=True)
    svc._paths.under("src", "LoRaHAM_Pi").mkdir(parents=True)
    d = svc._paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "garbage.json").write_text("{ retained")        # unresolved -> blocks all source ops
    assert "blocked" in svc.build("daemon", apply=True).summary.lower()
    assert "blocked" in svc.test("daemon", apply=True).summary.lower()
    assert "blocked" in svc.uninstall("daemon", apply=True).summary.lower()
    rs = svc.start("meshtastic", apply=True)
    assert not rs.ok and "unresolved" in rs.summary.lower()


def test_source_guard_holds_index_during_handoff(tmp_path):
    # While the index lock is held externally, the guard cannot even check -> ResourceBusy
    # (no window where a clean op proceeds past a concurrently-created journal).
    from lhpc.core.services import ControllerService, SourceTxnBlocked
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core import reslock
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc._paths.under("src", "loraham-daemon").mkdir(parents=True)
    with reslock.operation_lock(svc._paths, "source-txn-index", "adopt", "x"):
        res = svc.build("daemon", apply=True)
    assert not res.ok and "blocked" in res.summary.lower()


# --- P0.3 typed recovery cleanup --------------------------------------------

def test_broken_active_symlink_not_treated_as_intact(tmp_path):
    import os as _os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    _os.symlink(src / "does-not-exist", dest)            # dangling active symlink
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("PRIOR")
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")
    assert "intact" not in msg                            # broken symlink != usable source
    # the INJECTED occupant is never deleted to continue: retained as evidence, prior kept
    assert "recovery-required" in msg and "occupied" in msg
    assert dest.is_symlink()                              # injected leaf UNTOUCHED
    assert (prev / "m").read_text() == "PRIOR"            # prior retained at .prev


def test_failed_journal_unlink_is_recovery_required(tmp_path, monkeypatch):
    from lhpc.core import runtime_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    prev = src / ".app.prev"; prev.mkdir()
    jf = inst._journal_path(dest); jf.parent.mkdir(parents=True, exist_ok=True); jf.write_text("{}")
    monkeypatch.setattr(runtime_fs.OwnedMarker, "remove", lambda self: False)   # removal "fails"
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")   # must NOT raise
    assert "recovery-required" in msg and "journal could not be removed" in msg
    assert jf.exists()


def test_failed_prev_cleanup_after_activation_retains_journal(tmp_path, monkeypatch):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    prev = src / ".app.prev"; prev.mkdir()
    jf = inst._journal_path(dest); jf.parent.mkdir(parents=True, exist_ok=True); jf.write_text("{}")
    monkeypatch.setattr(type(inst), "_prev_cleanup_ok",
                        lambda self, txn, prev, ident=None: False)   # prev removal "fails"
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")
    assert "recovery-required" in msg and "prior could not be removed" in msg
    assert jf.exists() and prev.exists()                  # journal + prior retained


# --- P0 source-activation truth: dangling/non-dir active source is NOT activated ---

def test_dangling_linked_source_not_activated(tmp_path):
    import os as _os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"
    _os.symlink(src / "gone", staging)              # candidate symlink -> NONEXISTENT dir
    outcome = inst._activate(dest, staging)
    assert outcome == "recovery-required"           # dangling link is NOT a usable source
    assert inst._journal_path(dest).exists()        # journal retained (not deleted)
    assert dest.is_symlink() and not dest.is_dir()  # the dangling link occupies dest


def test_regular_file_active_source_not_activated(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"; staging.write_text("not a dir")  # regular file
    outcome = inst._activate(dest, staging)
    assert outcome == "recovery-required"           # a regular file is not a source tree
    assert inst._journal_path(dest).exists()


def test_real_dir_candidate_activates(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "f").write_text("x")
    assert inst._activate(dest, staging) == "activated"
    assert dest.is_dir() and not inst._journal_path(dest).exists()


def test_recovery_rejects_regular_file_active_source(tmp_path):
    # recovery must also require a usable DIRECTORY before clearing the journal.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.write_text("regular file")      # not a dir
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("PRIOR")
    msg = _fin(inst, dest, prev, src / ".app.candidate-1-2")
    assert "intact" not in msg                       # a file is not a usable active source


def test_activate_failed_rename_leaving_dangling_dest_restores_prior(tmp_path, monkeypatch):
    # dest->prev archives; staging->dest fails AND an external race leaves dest a DANGLING
    # symlink. _activate must NOT accept the dangling symlink as usable: it restores the
    # prior to a usable dir before clearing the journal (no erased recovery evidence).
    import os as _os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    real = _os.rename
    def fake_rename(a, b, *args, **kw):
        if str(a).endswith(".app.candidate-1-2"):     # staging -> dest fails
            _os.symlink(src / "gone", b, dir_fd=kw.get("dst_dir_fd"))  # race: dangling symlink at dest
            raise OSError("simulated activation failure")
        return real(a, b, *args, **kw)
    monkeypatch.setattr("lhpc.core.install.os.rename", fake_rename)
    _fail_noreplace(monkeypatch, suffixes=(".app.candidate-1-2",), plant_dangling=True)
    outcome = inst._activate(dest, staging)
    # the injected dangling symlink is NEVER deleted to continue: evidence retained,
    # prior stays archived at .prev, journal retained for recovery
    assert outcome == "recovery-required"
    assert dest.is_symlink()                                      # injected leaf UNTOUCHED
    assert (src / ".app.prev" / "m").read_text() == "LIVE"        # prior safe at .prev
    assert inst._journal_path(dest).exists()


def test_activate_dangling_dest_unrestorable_retains_journal(tmp_path, monkeypatch):
    # Same race, but the prior restore ALSO fails -> retain journal (recovery-required),
    # never clear it leaving an unusable active source.
    import os as _os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir()
    real = _os.rename
    def fake_rename(a, b, *args, **kw):
        if str(a).endswith(".app.candidate-1-2"):
            _os.symlink(src / "gone", b, dir_fd=kw.get("dst_dir_fd")); raise OSError("activation failed")
        if str(a).endswith(".app.prev"):               # prior restore also fails
            raise OSError("restore failed")
        return real(a, b, *args, **kw)
    monkeypatch.setattr("lhpc.core.install.os.rename", fake_rename)
    _fail_noreplace(monkeypatch, plant_dangling=True)
    assert inst._activate(dest, staging) == "recovery-required"
    assert inst._journal_path(dest).exists()           # journal retained (recovery route)


# --- P0 .prev is a transaction artifact (repeatable updates) ------------------

def test_activation_prev_cleanup_failure_recovery_required_then_recoverable(tmp_path, monkeypatch):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("OLD")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    real = type(inst)._prev_cleanup_ok
    fail = {"on": True}
    monkeypatch.setattr(
        type(inst), "_prev_cleanup_ok",
        lambda self, txn, prev, ident=None: False if fail["on"]
        else real(self, txn, prev, ident))
    # Activation succeeds, but the .prev cleanup fails -> recovery-required (typed).
    assert inst._activate(dest, staging) == "recovery-required"
    assert dest.is_dir() and (dest / "m").read_text() == "NEW"   # active source usable
    assert inst._journal_path(dest).exists()                     # journal retained
    assert (src / ".app.prev").exists()                          # .prev retained
    # A later recovery (cleanup now works) clears the journal + .prev safely.
    fail["on"] = False
    inst._recover_scan()
    assert not inst._journal_path(dest).exists()
    assert not (src / ".app.prev").exists()
    assert (dest / "m").read_text() == "NEW"


# --- §2 descriptor-safe journal enumeration: unsafe state BLOCKS, never absent -----

def _txn_dir(inst):
    return inst.paths.under("state", "source-txn")


def test_symlinked_journal_blocks_recovery_not_skipped(tmp_path):
    import os
    inst = _inst(tmp_path)
    d = _txn_dir(inst); d.mkdir(parents=True)
    outside = tmp_path / "evil.json"
    outside.write_text('{"version": 2, "state": "planned", "source_rel": "src/x", '
                       '"prev_rel": "src/.x.prev", "candidate_rel": "src/.x.candidate-1-2"}')
    os.symlink(outside, d / "app.json")                     # symlinked journal entry
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "symlink" in m for m in msgs)   # blocks, not skipped
    assert inst._pending_journals() is True                 # still blocks all mutation
    assert (d / "app.json").is_symlink()                    # evidence retained (not deleted)


def test_symlinked_txn_dir_blocks(tmp_path):
    import os
    inst = _inst(tmp_path)
    (inst.paths.under("state")).mkdir(parents=True)
    outside = tmp_path / "evil-txn"; outside.mkdir()
    (outside / "app.json").write_text("{}")
    os.symlink(outside, _txn_dir(inst))                     # the txn DIR is a symlink
    assert inst._pending_journals() is True                 # unsafe container -> block
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m for m in msgs)


def test_malformed_journal_is_retained_and_blocks(tmp_path):
    inst = _inst(tmp_path)
    d = _txn_dir(inst); d.mkdir(parents=True)
    (d / "app.json").write_text("{ this is not json")
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "invalid" in m for m in msgs)
    assert (d / "app.json").exists()                        # evidence preserved
    assert inst._pending_journals() is True


def test_absent_txn_dir_is_empty_not_blocked(tmp_path):
    inst = _inst(tmp_path)                                   # no state/source-txn dir at all
    assert inst._pending_journals() is False
    assert inst.recover_source_activations() == []


def test_journal_targeting_non_managed_path_is_blocked(tmp_path):
    # §1: a journal whose destination is a CONTAINED but non-managed runtime path
    # (config/foo, state/foo, …) is retained + blocked — recovery never renames/deletes
    # outside the manifest's managed-source set, even if the filename looks plausible.
    inst = _inst(tmp_path)                                    # only src/app is managed
    victim = inst.paths.under("config", "foo"); victim.mkdir(parents=True)
    (victim / "keep").write_text("KEEP")
    d = _txn_dir(inst); d.mkdir(parents=True, exist_ok=True)
    (d / "foo.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "config/foo",
        "prev_rel": "config/.foo.prev", "candidate_rel": "config/.foo.candidate-1-2"}))
    msgs = inst.recover_source_activations()
    assert any("recovery-required" in m and "not a known managed source" in m for m in msgs)
    assert (victim / "keep").read_text() == "KEEP"           # non-source path untouched
    assert (d / "foo.json").exists()                         # evidence retained
    assert inst._pending_journals() is True


def test_adopt_reports_pinned_provenance(tmp_path):
    # §C wired: a real local pinned repo -> adopt reports pinned-verified provenance in
    # the action state + detail (local git, no network).
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done" and action.provenance == "pinned-verified"
    assert "provenance: pinned-verified" in action.detail


def test_adopt_reports_mutable_dev_provenance(tmp_path):
    inst = _inst(tmp_path)
    _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src"))
    action = inst.adopt_source(comp, source="dev")           # explicit mutable selection
    assert action.status == "done" and action.provenance == "mutable-dev"



def _register_tree(inst, dest, comp, remote=""):
    """Make an EXISTING tree pass the current-identity gate: turn it into a committed git
    repo and write a matching ownership record (HEAD + remote + strategy '')."""
    import subprocess, time as _t
    from lhpc.core import source_registry
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True, env=env)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(dest), "commit", "-qm", "prior"], check=True,
                   capture_output=True, env=env)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()
    rel = str(dest.relative_to(inst.paths.runtime_root))
    assert source_registry.write_record(inst.paths, source_registry.RegistryRecord(
        rel, remote, "legacy", head, _t.time(), "", "", (comp.id,)))
    return head

def test_provenance_not_ok_blocks_activation_prior_intact(tmp_path, monkeypatch):
    # §4: a not-ok provenance result BLOCKS activation BEFORE the active source is touched.
    from lhpc.core import provenance
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    active = inst.paths.under("src", "app"); active.mkdir(parents=True)
    (active / "OLD").write_text("keep")                  # a prior active source
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src",
                                       strategy="link", pin_commit=head))
    _register_tree(inst, active, comp)                   # identity gate passes -> reaches provenance
    monkeypatch.setattr(provenance, "evaluate", lambda *a, **k: provenance.ProvenanceResult(
        provenance.UNVERIFIED_BLOCKED, False, False, "forced block"))
    action = inst.adopt_source(comp, source="pinned", force=True)
    assert action.status == "failed" and "provenance blocked before activation" in action.detail
    assert action.provenance == provenance.UNVERIFIED_BLOCKED
    assert (active / "OLD").read_text() == "keep"        # prior active source UNTOUCHED


def test_signer_config_diagnostics_reach_result(tmp_path):
    # §4: trusted-signer config diagnostics are surfaced in the install/adopt result.
    from lhpc.core.config import Config
    from lhpc.core.probes import RealSystem
    _, head = _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    cfg = Config(values={"install": {"adopt_search_root": str(tmp_path / "rt")},
                         "provenance": {"trusted_signers": ["not-a-fingerprint"]}})
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    inst = Installer(Paths(runtime_root=tmp_path / "rt"), stacks, cfg, RealSystem())
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done"
    assert "signer-config" in action.detail and "malformed" in action.detail


# --- §2: build/test launcher — per-step timeout, own group, safe timeout policy ----

def _dead_or_zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            st = fh.read()
        return st[st.rindex(")") + 2] in ("Z", "X", "x")
    except (OSError, ValueError):
        return True


def test_build_launcher_step_timeout_kills_child_group(tmp_path):
    import subprocess, sys, os, time
    from lhpc.core import commands
    prog = ("import subprocess, sys, time\n"
            "c = subprocess.Popen(['sleep', '60'])\n"
            "open(sys.argv[1], 'w').write(str(c.pid))\n"
            "time.sleep(60)\n")
    steps = [{"argv": [sys.executable, "-c", prog, str(tmp_path / "childpid")]}]
    launcher = tmp_path / "l.py"
    launcher.write_text(commands.render_build_launcher(steps, str(tmp_path), str(tmp_path), []))
    env = {**os.environ, "LHPC_BUILD_STEP_TIMEOUT_S": "0.6"}
    t0 = time.time()
    r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
    assert r.returncode == 124 and "step timed out" in r.stderr
    assert time.time() - t0 < 10
    child = int((tmp_path / "childpid").read_text())
    for _ in range(60):
        if _dead_or_zombie(child):
            break
        time.sleep(0.1)
    assert _dead_or_zombie(child)                          # step's child killed with the group


def test_build_launcher_malformed_timeout_fails_safe(tmp_path):
    import subprocess, sys, os
    from lhpc.core import commands
    launcher = tmp_path / "l.py"
    launcher.write_text(commands.render_build_launcher([{"argv": ["true"]}], str(tmp_path),
                                                       str(tmp_path), []))
    env = {**os.environ, "LHPC_BUILD_STEP_TIMEOUT_S": "not-a-number"}
    r = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "invalid LHPC_BUILD_STEP_TIMEOUT_S" in r.stderr   # not unlimited


def test_adopt_source_parent_swap_before_staging_blocks(tmp_path):
    # §1.6.4: a symlinked source parent before staging fails closed; nothing outside touched.
    import os
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    outside = tmp_path / "out"; outside.mkdir(); (outside / "keep").write_text("KEEP")
    inst.paths.runtime_root.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, inst.paths.runtime_root / "src")            # source parent -> outside
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed"                               # symlinked parent fails closed
    assert list(outside.iterdir()) == [outside / "keep"]           # nothing staged outside
    assert (outside / "keep").read_text() == "KEEP"


def test_same_basename_sources_get_distinct_journals(tmp_path):
    # §3/#2: src/a/app and src/b/app must never share a journal identity.
    inst = _inst(tmp_path)
    root = inst.paths.runtime_root
    ja = inst._journal_path(root / "src" / "a" / "app")
    jb = inst._journal_path(root / "src" / "b" / "app")
    assert ja.name != jb.name and ja.name.startswith("app-") and jb.name.startswith("app-")


def test_legacy_basename_journal_is_retained_and_blocks(tmp_path):
    # §3/#5: a legacy basename-only journal (app.json) does not match the identity-bound
    # name, so recovery retains it and blocks — never silently migrates or deletes it.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    d = inst.paths.under("state", "source-txn"); d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "src/app",
        "prev_rel": "src/.app.prev", "candidate_rel": "src/.app.candidate-1-2"}))
    msgs = inst.recover_source_activations()
    assert any("filename does not match" in m for m in msgs)
    assert (d / "app.json").exists()                     # legacy journal RETAINED, not migrated


def test_post_activation_provenance_mismatch_restores_prior(tmp_path, monkeypatch):
    # §4: a post-activation provenance failure rolls back to the prior via the held FD BEFORE
    # `.prev`/journal are cleared — prior restored, journal retained, never a green success.
    from lhpc.core import provenance
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    active = inst.paths.under("src", "app"); active.mkdir(parents=True)
    (active / "OLD").write_text("PRIOR")                 # a prior active source
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    _register_tree(inst, active, comp)                   # identity gate passes -> reaches provenance
    calls = {"n": 0}
    real = provenance.evaluate
    def fake(runner, path, spec, source, trusted, expected_commit=""):
        calls["n"] += 1
        if calls["n"] >= 2:                              # #1 = pre-gate (ok); later = post -> fail
            return provenance.ProvenanceResult(provenance.UNVERIFIED_BLOCKED, False, False, "forced")
        return real(runner, path, spec, source, trusted)
    monkeypatch.setattr(provenance, "evaluate", fake)
    action = inst.adopt_source(comp, source="pinned", force=True)
    assert action.status == "failed" and "rolled back" in action.detail
    assert (active / "OLD").read_text() == "PRIOR"       # prior RESTORED via held-FD rollback
    # a PROVEN rollback leaves a coherent state -> the journal is CLEARED (it is retained
    # only when rollback/record completion cannot be proven)
    assert not inst._journal_path(active).exists()


def test_successful_adopt_clears_journal_only_after_provenance(tmp_path):
    # §4: the normal success path clears the journal/.prev ONLY after final provenance passes.
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app-src", pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    dest = inst.paths.under("src", "app")
    assert action.status == "done" and dest.is_dir()
    assert not inst._journal_path(dest).exists()         # journal cleared (provenance passed)
    assert not (dest.parent / ".app.prev").exists()      # .prev cleaned


def test_journal_filename_uses_full_sha256(tmp_path):
    import re
    inst = _inst(tmp_path)
    name = inst._journal_path(inst.paths.runtime_root / "src" / "app").name
    assert re.fullmatch(r"app-[0-9a-f]{64}\.json", name)     # FULL digest, not truncated


def test_journal_missing_txn_id_retained_and_blocks(tmp_path):
    # §3: a journal at the identity-bound path but with NO txn_id (legacy payload) is
    # retained + blocked by recovery, never resumed.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir()
    inst._journal_path(dest).write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "src/app",
        "prev_rel": "src/.app.prev", "candidate_rel": "src/.app.candidate-1-2"}))  # no txn_id
    msgs = inst.recover_source_activations()
    assert any("transaction id" in m for m in msgs)
    assert inst._journal_path(dest).exists()                # retained as evidence


def test_journal_altered_txn_id_retained_and_blocks(tmp_path):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir()
    inst._journal_path(dest).write_text(json.dumps({
        "version": 2, "state": "prior-archived", "source_rel": "src/app",
        "prev_rel": "src/.app.prev", "candidate_rel": "src/.app.candidate-1-2",
        "txn_id": "deadbeef"}))                              # wrong txn_id
    msgs = inst.recover_source_activations()
    assert any("transaction id" in m for m in msgs)
    assert inst._journal_path(dest).exists()


def test_copy_into_candidate_preserves_symlinks_and_ignores(tmp_path):
    # §2: local-fallback copy fills the pre-created empty candidate per-entry (no
    # dirs_exist_ok merge), preserving symlinks unfollowed and honoring the ignore set.
    import os
    from lhpc.core.install import Installer
    local = tmp_path / "local"; (local / "sub").mkdir(parents=True)
    (local / "f").write_text("F"); (local / "sub" / "g").write_text("G")
    os.symlink("f", local / "ln")                       # relative symlink
    (local / "__pycache__").mkdir(); (local / "__pycache__" / "x").write_text("junk")
    cand = tmp_path / "cand"; cand.mkdir()              # pre-created empty candidate
    Installer._copy_into_candidate(local, str(cand))
    assert (cand / "f").read_text() == "F" and (cand / "sub" / "g").read_text() == "G"
    assert (cand / "ln").is_symlink() and os.readlink(cand / "ln") == "f"   # not followed
    assert not (cand / "__pycache__").exists()         # ignore set honored


# --- §2/§3: exclusive journal creation + inode-bound ownership ----------------

def test_journal_exclusive_create_refuses_existing_leaf(tmp_path):
    import os
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; prev = src / ".app.prev"; staging = src / ".app.candidate-1-2"
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    jp = inst._journal_path(dest)
    jp.write_text("{injected}")                                  # regular file injected
    assert inst._create_journal(dest, prev, staging) is None     # O_EXCL refuses
    assert jp.read_text() == "{injected}"                        # never overwritten
    jp.unlink(); os.symlink(tmp_path / "x", jp)                  # symlink injected
    assert inst._create_journal(dest, prev, staging) is None     # O_NOFOLLOW refuses


def test_injected_journal_blocks_before_prev_change(tmp_path):
    # §2/#6: a journal appearing after the absent-preflight blocks BEFORE any dest->.prev.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    inst._journal_path(dest).write_text("{injected regular journal}")
    outcome = inst._activate(dest, staging)
    assert outcome == "recovery-required"
    assert (dest / "m").read_text() == "LIVE"                    # dest untouched
    assert not (src / ".app.prev").exists()                      # .prev NEVER created
    assert inst._journal_path(dest).exists()                     # injected journal retained


def test_activate_verifies_candidate_identity_before_promotion(tmp_path):
    # §1: if the candidate is not the FD-verified inode, activation refuses (via _activate_held
    # receiving a mismatched handle) — proven through the transaction's verify_candidate.
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")

    class _BadHandle:
        name = ".app.candidate-1-2"
        st_dev = -1
        st_ino = -1
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        outcome = inst._activate_held(txn, dest, staging, handle=_BadHandle())
    assert outcome in ("recovery-required", "failed-clean")
    assert (dest / "m").read_text() == "LIVE"                    # active source not replaced


# --- §1/§2/§3: link & candidate substitution, owned-journal ownership loss -----

def test_candidate_substitution_is_recovery_required_and_preserved(tmp_path):
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "x").write_text("NEW")
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        bad = source_fs.CandidateHandle(".app.candidate-1-2", -1, -1, -1)   # wrong inode
        outcome = inst._activate_held(txn, dest, staging, handle=bad)
    assert outcome == "recovery-required"                     # NOT failed-clean
    assert (dest / "m").read_text() == "LIVE"                 # active source untouched
    assert (staging / "x").read_text() == "NEW"              # substituted staging RETAINED
    assert inst._journal_path(dest).exists()                  # journal retained


def test_link_substitution_pre_archive_blocks(tmp_path):
    import os
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    tgt = tmp_path / "ext"; tgt.mkdir()
    staging = src / ".app.candidate-1-2"; os.symlink(tgt, staging)
    bad = source_fs.LinkHandle(".app.candidate-1-2", -1, -1, str(tgt), str(tgt))  # wrong ino
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        outcome = inst._activate_held(txn, dest, staging, handle=bad)
    assert outcome == "recovery-required"
    assert (dest / "m").read_text() == "LIVE"                 # dest never archived
    assert staging.is_symlink()                               # substituted leaf retained
    assert inst._journal_path(dest).exists()


def test_link_substitution_after_archive_restores_prior(tmp_path, monkeypatch):
    import os
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    tgt = tmp_path / "ext"; tgt.mkdir()
    staging = src / ".app.candidate-1-2"; os.symlink(tgt, staging)
    st = os.lstat(staging)
    lh = source_fs.LinkHandle(".app.candidate-1-2", st.st_dev, st.st_ino,
                              os.readlink(staging), str(tgt))
    calls = {"n": 0}
    monkeypatch.setattr(type(inst), "_verify_staged",
                        lambda self, txn, h, name: (calls.__setitem__("n", calls["n"] + 1)
                                                    or calls["n"] == 1))   # pass then fail
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        outcome = inst._activate_held(txn, dest, staging, handle=lh)
    assert outcome == "recovery-required"
    assert (dest / "m").read_text() == "LIVE"                 # prior RESTORED via held FD
    assert staging.is_symlink()                               # substituted leaf retained
    assert inst._journal_path(dest).exists()                  # journal retained


def test_journal_ownership_lost_before_update_rolls_back(tmp_path, monkeypatch):
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    staging = src / ".app.candidate-1-2"; staging.mkdir(); (staging / "m").write_text("NEW")
    monkeypatch.setattr(type(inst), "_update_journal",
                        lambda self, jh, d, p, s, state: False)   # ownership lost on update
    outcome = inst._activate(dest, staging)
    assert outcome == "recovery-required"
    assert (dest / "m").read_text() == "LIVE"                 # prior restored
    assert inst._journal_path(dest).exists()                  # journal retained


def test_normal_link_activation_still_succeeds(tmp_path):
    inst = _inst(tmp_path)
    _, head = _local_repo(tmp_path, "app-src")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", strategy="link", local_dir="app-src",
                                       pin_commit=head))
    action = inst.adopt_source(comp, source="pinned")
    dest = inst.paths.under("src") / "app"                    # plain join (leaf is a symlink)
    assert action.status == "done" and dest.is_symlink() and dest.is_dir()
    assert not inst._journal_path(dest).exists()              # journal cleared on success


# --- closure: handle-safe cleanup, reverify-after-provenance, recovery marker --

def test_cleanup_owned_staging_removes_intact_retains_substituted(tmp_path):
    import os, shutil
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    with source_fs.ManagedSourceTransaction(inst.paths, src) as txn:
        h = txn.create_candidate(".app.candidate-1-2")
        assert inst._cleanup_owned_staging(txn, h, ".app.candidate-1-2") == "removed"
        assert txn.leaf_kind(".app.candidate-1-2") == "absent"
        h2 = txn.create_candidate(".app.candidate-3-4")
        shutil.rmtree(src / ".app.candidate-3-4")
        os.symlink(tmp_path, src / ".app.candidate-3-4")        # substitute the leaf
        assert inst._cleanup_owned_staging(txn, h2, ".app.candidate-3-4") == "identity-lost"
        assert (src / ".app.candidate-3-4").is_symlink()        # substitute RETAINED


def test_substitution_during_successful_provenance_is_recovery_required(tmp_path):
    import shutil
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        h = txn.create_candidate(".app.candidate-1-2")
        staging = src / ".app.candidate-1-2"

        def va():      # provenance "passes" but swaps the now-active dest for a NEW inode
            shutil.rmtree(src / "app"); (src / "app").mkdir(); (src / "app" / "evil").write_text("x")
            return True
        outcome = inst._activate_held(txn, dest, staging, verify_active=va, handle=h)
    assert outcome == "recovery-required"                       # never reported activated
    assert (src / ".app.prev").exists()                        # .prev retained
    assert inst._journal_path(dest).exists()                   # journal retained
    assert (src / "app" / "evil").exists()                     # substituted active leaf retained


def test_substitution_during_failed_provenance_does_not_delete_dest(tmp_path):
    import shutil
    from lhpc.core import source_fs
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    inst.paths.under("state", "source-txn").mkdir(parents=True, exist_ok=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("LIVE")
    with source_fs.ManagedSourceTransaction(inst.paths, dest.parent) as txn:
        h = txn.create_candidate(".app.candidate-1-2")
        staging = src / ".app.candidate-1-2"

        def va():      # provenance FAILS, and the active dest was swapped meanwhile
            shutil.rmtree(src / "app"); (src / "app").mkdir(); (src / "app" / "evil").write_text("x")
            return False
        outcome = inst._activate_held(txn, dest, staging, verify_active=va, handle=h)
    assert outcome == "recovery-required"
    assert (src / "app" / "evil").exists()                     # substituted dest NOT deleted
    assert (src / ".app.prev").exists() and inst._journal_path(dest).exists()


def test_failed_staging_cleans_controller_candidate(tmp_path):
    inst = _inst(tmp_path)
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))         # no remote, no local -> fails
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "failed"
    src = inst.paths.under("src")
    leftovers = [p.name for p in src.iterdir() if p.name.startswith(".app.candidate")] \
        if src.exists() else []
    assert leftovers == []                                     # intact candidate cleaned up


# --- FINAL: v2/v3 journals are generation-blocked, leaves retained ----------------------------

def test_v3_journal_generation_blocked_with_substituted_leaves(tmp_path):
    # A structurally-valid v3 journal — even with substituted candidate/dest/prev leaves —
    # triggers NO automatic promotion/restore/cleanup: typed recovery-required, everything
    # retained, further mutation blocked.
    inst = _inst(tmp_path)
    src = inst.paths.under("src"); src.mkdir(parents=True)
    dest = src / "app"; dest.mkdir(); (dest / "m").write_text("SUBSTITUTED DEST")
    prev = src / ".app.prev"; prev.mkdir(); (prev / "m").write_text("SUBSTITUTED PRIOR")
    staging = src / ".app.candidate-1-2"; staging.mkdir()
    (staging / "m").write_text("SUBSTITUTED CANDIDATE")
    rel = lambda p: str(p.relative_to(inst.paths.runtime_root))
    inst._journal_path(dest).parent.mkdir(parents=True, exist_ok=True)
    inst._journal_path(dest).write_text(json.dumps({
        "version": 3, "state": "prior-archived", "source_rel": rel(dest),
        "prev_rel": rel(prev), "candidate_rel": rel(staging),
        "txn_id": inst._txn_id(rel(staging)),
        "meta": {"selector": "dev", "resolved_commit": "a" * 40, "remote": "",
                 "strategy": "", "components": ["app"]}}))
    msgs = inst.recover_source_activations()
    assert any("generation" in m and "recovery-required" in m for m in msgs)
    assert (dest / "m").read_text() == "SUBSTITUTED DEST"       # nothing touched
    assert (prev / "m").read_text() == "SUBSTITUTED PRIOR"
    assert (staging / "m").read_text() == "SUBSTITUTED CANDIDATE"
    assert inst._journal_path(dest).exists()                    # journal retained

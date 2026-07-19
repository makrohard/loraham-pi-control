"""P0.5 + M2 — uninstall never removes a running component or a source checkout still
used by another component (shared source), never touches config/secrets, removes only
LHPC-OWNED verified trees, refuses dirty trees, and update refuses while any consumer
of an affected source is running."""

import time

from lhpc.core import source_registry
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def _svc(tmp_path, cmdlines=None, commands=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {},
                                               commands=commands or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _identity_cmds(tmp_path, rel, remote):
    """Fake the current-identity git queries so a seeded record verifies."""
    from lhpc.core.probes.backends import CommandResult
    dest = str(tmp_path / "src" / rel)
    return {("git", "-C", dest, "config", "--get", "remote.origin.url"):
            CommandResult(0, remote + "\n", "")}


_KISS_REMOTE = "https://github.com/makrohard/loraham-kiss-tnc.git"


def _bind_identity(svc, dest, remote, head=""):
    """Answer the identity git queries regardless of HOW the path is spelled — the
    verifier now runs them against the captured leaf's fd-pinned /proc path, which
    realpath-resolves to `dest`."""
    import os as _os
    from lhpc.core.probes.backends import CommandResult
    real_run = svc._system.runner.run
    dest_real = _os.path.realpath(str(dest))
    def run(argv, timeout, *a, **k):
        argv = list(argv)
        if (len(argv) >= 4 and argv[:2] == ["git", "-C"]
                and _os.path.realpath(argv[2]) == dest_real):
            if argv[3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(0, remote + "\n", "")
            if argv[3:] == ["rev-parse", "HEAD"] and head:
                return CommandResult(0, head + "\n", "")
        return real_run(argv, timeout, *a, **k)
    svc._system.runner.run = run
    return svc



def _mksrc(tmp_path, *rel):
    for r in rel:
        (tmp_path / "src" / r).mkdir(parents=True, exist_ok=True)


def _own(tmp_path, rel, comps=("x",)):
    """Seed an ownership record — the tree is a registered LHPC adoption."""
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord(f"src/{rel}", "", "backfilled", "", time.time(), "",
                                       "", tuple(comps)))


def test_uninstall_keeps_source_shared_by_another_stack(tmp_path):
    # chat and iGate share src/LoRaHAM_Daemon. Uninstalling chat must KEEP it.
    _mksrc(tmp_path, "LoRaHAM_Daemon")
    svc = _svc(tmp_path)
    res = svc.uninstall("chat", apply=True)
    assert res.ok
    assert (tmp_path / "src" / "LoRaHAM_Daemon").exists(), "shared source wrongly removed"
    assert any("shared" in d.lower() and "loraham-igate" in d for d in res.details)


def test_uninstall_keeps_source_shared_within_stack(tmp_path):
    # kiss-tnc and serial-kiss share src/loraham-kiss-tnc. Uninstalling just the
    # serial component must keep the checkout (kiss-tnc still uses it).
    _mksrc(tmp_path, "loraham-kiss-tnc")
    svc = _svc(tmp_path)
    res = svc.uninstall("loraham-kiss-serial", apply=True)
    assert res.ok
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()


def test_uninstall_refuses_while_running(tmp_path):
    _mksrc(tmp_path, "LoRaHAM_Daemon")
    # a chat process is running
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    res = svc.uninstall("chat", apply=True)
    assert not res.ok and "running" in res.summary.lower()
    assert (tmp_path / "src" / "LoRaHAM_Daemon").exists()


def test_uninstall_removes_unshared_owned_source(tmp_path):
    # Uninstalling the whole kiss stack (both consumers) removes the REGISTERED checkout,
    # and drops its ownership record.
    _mksrc(tmp_path, "loraham-kiss-tnc")
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc", "loraham-kiss-serial"))
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc", _KISS_REMOTE)
    res = svc.uninstall("kiss", apply=True)
    assert res.ok
    assert not (tmp_path / "src" / "loraham-kiss-tnc").exists()
    assert source_registry.read_record(Paths(runtime_root=tmp_path),
                                       "src/loraham-kiss-tnc") is None


# --- M2: ownership + dirty gates -----------------------------------------------------------

def test_uninstall_refuses_unknown_tree(tmp_path):
    # No ownership record, not a git checkout: an UNKNOWN tree is never removed.
    _mksrc(tmp_path, "loraham-kiss-tnc")
    (tmp_path / "src" / "loraham-kiss-tnc" / "user-data.txt").write_text("precious")
    svc = _svc(tmp_path)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("refused" in d and "not a git checkout" in d for d in res.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc" / "user-data.txt").exists()


def test_uninstall_fails_toward_dirty_when_status_unprovable(tmp_path):
    # Owned tree WITH a .git dir; identity verifies (faked origin) but `git status` cannot
    # run: the dirty check fails TOWARD dirty — refused, never silently removed.
    _mksrc(tmp_path, "loraham-kiss-tnc")
    (tmp_path / "src" / "loraham-kiss-tnc" / ".git").mkdir()
    _own(tmp_path, "loraham-kiss-tnc")
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc", _KISS_REMOTE)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("local changes present" in d for d in res.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()


def test_uninstall_refuses_identity_drift(tmp_path):
    # A registered tree whose ORIGIN no longer matches the record/config, or whose HEAD
    # moved to a different commit, is NOT LHPC's anymore: refused, tree unchanged.
    import time as _t
    _mksrc(tmp_path, "loraham-kiss-tnc")
    (tmp_path / "src" / "loraham-kiss-tnc" / ".git").mkdir()
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", _KISS_REMOTE, "pinned",
                                       "a" * 40, _t.time(), "", "", ("loraham-kiss-tnc",)))
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc",
                         _KISS_REMOTE, head="b" * 40)        # HEAD drifted a… -> b…
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("identity drift" in d for d in res.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()   # tree unchanged


def test_uninstall_linked_source_unlinks_leaf_only(tmp_path):
    # A REGISTERED linked source (symlink into an external tree): uninstall drops only the
    # leaf; the external target is never touched. An UNREGISTERED symlink at a non-link
    # source is refused (not an LHPC adoption).
    import time as _t
    external = tmp_path / "external-tree"
    external.mkdir()
    (external / "keep.txt").write_text("external")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "loraham-kiss-tnc").symlink_to(external)
    # unregistered symlink at a non-link source -> refused, leaf + target untouched
    svc = _svc(tmp_path)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert (tmp_path / "src" / "loraham-kiss-tnc").is_symlink()
    # a REGISTERED link record makes it LHPC's leaf -> removed (leaf only)
    # a LEGACY (v1-style) link record without a recorded target stays NON-DESTRUCTIVE
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "backfilled", "", _t.time(),
                                       "", "link", ("loraham-kiss-tnc",)))
    import json as _json
    rp = source_registry.record_path(Paths(runtime_root=tmp_path), "src/loraham-kiss-tnc")
    legacy = _json.loads(rp.read_text()); legacy["version"] = 1; legacy.pop("link_target")
    rp.write_text(_json.dumps(legacy))
    res_legacy = _svc(tmp_path).uninstall("kiss", apply=True)
    assert not res_legacy.ok
    assert any("non-destructive" in d for d in res_legacy.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc").is_symlink()      # leaf retained
    # a CURRENT record with the exact link target authorizes leaf-only removal
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "backfilled", "", _t.time(),
                                       "", "link", ("loraham-kiss-tnc",),
                                       link_target=str(external)))
    res2 = _svc(tmp_path).uninstall("kiss", apply=True)
    assert res2.ok, res2.details
    assert not (tmp_path / "src" / "loraham-kiss-tnc").is_symlink()
    assert (external / "keep.txt").exists()                     # external target untouched


# --- M2: update requires the affected stacks stopped ----------------------------------------

def test_update_refuses_while_target_running(tmp_path):
    _mksrc(tmp_path, "LoRaHAM_Daemon")
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    res = svc.update("chat", apply=True)
    assert not res.ok and "running" in res.summary.lower()
    assert any("stop" in c for c in res.next_commands)


def test_update_refuses_while_shared_sibling_running(tmp_path):
    # chat (running) and igate share src/LoRaHAM_Daemon: updating IGATE is refused
    # because the swap would break the running chat.
    _mksrc(tmp_path, "LoRaHAM_Daemon")
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    res = svc.update("loraham-igate", apply=True)
    assert not res.ok and "running" in res.summary.lower()
    assert any("loraham-chat" in d for d in res.details)


def test_update_proceeds_when_unrelated_stack_running(tmp_path):
    # chat running does NOT block updating the kiss stack (different source path):
    # the running-gate passes and the update reaches the adopt step.
    _mksrc(tmp_path, "loraham-kiss-tnc")
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    res = svc.update("kiss", apply=True)
    assert "Refusing to update" not in res.summary


def test_uninstall_registry_removal_failure_is_incomplete_then_retry(tmp_path, monkeypatch):
    from lhpc.core import source_registry as sreg
    _mksrc(tmp_path, "loraham-kiss-tnc")
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc", "loraham-kiss-serial"))
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc", _KISS_REMOTE)
    monkeypatch.setattr(sreg, "remove_record", lambda *a, **k: False)
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok                                                   # INCOMPLETE, not success
    assert any("record could not be dropped" in d for d in res.details)
    assert not (tmp_path / "src" / "loraham-kiss-tnc").exists()         # source itself removed
    assert sreg.read_record(Paths(runtime_root=tmp_path),
                            "src/loraham-kiss-tnc") is not None         # orphan remains
    # a later explicit uninstall retries the orphan cleanup even though the leaf is absent
    monkeypatch.undo()
    res2 = _svc(tmp_path).uninstall("kiss", apply=True)
    assert res2.ok, res2.details
    assert any("orphaned ownership record" in d for d in res2.details)
    assert sreg.read_record(Paths(runtime_root=tmp_path), "src/loraham-kiss-tnc") is None


def test_stale_record_never_authorizes_a_future_tree(tmp_path):
    # An orphan record left at an absent leaf must not authorize a NEW tree later placed at
    # the same path: the identity verifier refuses it (commit/origin unprovable or drifted).
    import time as _t
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", _KISS_REMOTE, "pinned",
                                       "a" * 40, _t.time(), "", "", ("loraham-kiss-tnc",)))
    _mksrc(tmp_path, "loraham-kiss-tnc")                                # a NEW unrelated tree
    (tmp_path / "src" / "loraham-kiss-tnc" / "new.txt").write_text("x")
    svc = _svc(tmp_path)                                                # git queries unanswered
    res = svc.uninstall("kiss", apply=True)
    assert not res.ok
    assert any("identity drift" in d or "refused" in d for d in res.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc" / "new.txt").exists() # tree untouched


# --- shared-source LIVE membership accounting (chat/igate leaf, user-reported) ----------------

def _seed_shared(tmp_path):
    from lhpc.core import source_registry
    import time as _t
    dest = tmp_path / "src" / "LoRaHAM_Daemon"
    dest.mkdir(parents=True)
    (dest / "app.c").write_text("x")
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/LoRaHAM_Daemon", "", "backfilled", "", _t.time(),
                                       "", "", ("loraham-chat", "loraham-igate")))
    return dest


def _members(tmp_path):
    from lhpc.core import source_registry
    rec = source_registry.read_record(Paths(runtime_root=tmp_path), "src/LoRaHAM_Daemon")
    return tuple(rec.components) if rec else None


_APPS_REMOTE = "https://github.com/makrohard/LoRaHAM_Daemon.git"


def _shared_svc(tmp_path):
    return _bind_identity(_svc(tmp_path), tmp_path / "src" / "LoRaHAM_Daemon",
                          _APPS_REMOTE)


def test_sequential_uninstall_removes_shared_leaf(tmp_path):
    # igate departs (kept + membership decremented); chat's uninstall then REMOVES the
    # leaf — the user-reported "kept forever" accounting bug.
    dest = _seed_shared(tmp_path)
    svc = _shared_svc(tmp_path)
    r1 = svc.uninstall("igate", apply=True)
    assert r1.ok, r1.details
    assert dest.exists()                                         # kept for chat
    assert _members(tmp_path) == ("loraham-chat",)               # igate departed
    assert any("departed" in d for d in r1.details)
    r2 = svc.uninstall("chat", apply=True)
    assert r2.ok, r2.details
    assert not dest.exists()                                     # LAST sharer -> removed
    assert _members(tmp_path) is None                            # record dropped
    assert not svc.is_installed("chat") and not svc.is_installed("igate")


def test_sequential_clean_removes_shared_leaf(tmp_path):
    dest = _seed_shared(tmp_path)
    svc = _shared_svc(tmp_path)
    assert svc.clean("chat", apply=True, purge=True).ok
    assert dest.exists() and _members(tmp_path) == ("loraham-igate",)
    assert svc.clean("igate", apply=True, purge=True).ok
    assert not dest.exists()


def test_mixed_clean_then_uninstall_removes_shared_leaf(tmp_path):
    dest = _seed_shared(tmp_path)
    svc = _shared_svc(tmp_path)
    assert svc.clean("igate", apply=True, purge=True).ok
    assert dest.exists()
    assert svc.uninstall("chat", apply=True).ok
    assert not dest.exists()


def test_departure_rewrite_failure_is_truthful_incomplete(tmp_path, monkeypatch):
    from lhpc.core import source_registry
    dest = _seed_shared(tmp_path)
    svc = _shared_svc(tmp_path)
    monkeypatch.setattr(source_registry, "update_components", lambda *a: False)
    r = svc.uninstall("igate", apply=True)
    assert not r.ok                                              # INCOMPLETE, not silent
    assert any("could not be updated" in d for d in r.details)
    assert dest.exists() and _members(tmp_path) == ("loraham-chat", "loraham-igate")
    monkeypatch.undo()
    assert svc.uninstall("igate", apply=True).ok                 # retry converges
    assert _members(tmp_path) == ("loraham-chat",)


def test_install_skip_rejoins_shared_membership(tmp_path):
    # chat departs, then chat is INSTALLED again (healthy-dir skip): its membership is
    # restored, so a later igate departure keeps the leaf for chat.
    dest = _seed_shared(tmp_path)
    svc = _shared_svc(tmp_path)
    assert svc.uninstall("chat", apply=True).ok
    assert _members(tmp_path) == ("loraham-igate",)
    svc.install("chat", apply=True)
    assert set(_members(tmp_path)) == {"loraham-chat", "loraham-igate"}   # re-joined
    r2 = svc.uninstall("igate", apply=True)
    assert r2.ok and dest.exists()                               # kept for chat again
    assert _members(tmp_path) == ("loraham-chat",)


def test_recordless_leaf_keeps_manifest_fallback(tmp_path):
    # No ownership record (legacy/unowned): keep-decisions stay manifest-driven and the
    # ownership gates refuse removal exactly as before — never a surprise deletion.
    dest = tmp_path / "src" / "LoRaHAM_Daemon"
    dest.mkdir(parents=True)
    svc = _svc(tmp_path)
    svc.uninstall("igate", apply=True)
    assert dest.exists()                                         # kept (manifest fallback)
    r2 = svc.uninstall("chat", apply=True)
    assert dest.exists()                                         # manifest fallback: kept
    assert any("kept" in d for d in r2.details)                  # never a surprise removal


def test_adopt_over_shrunk_record_restores_manifest_membership(tmp_path):
    # A genuine re-adopt (update/auto-install) of the shared checkout refreshes it for EVERY
    # declared consumer: membership = record ∪ manifest declarers again.
    from lhpc.core.install import Installer
    from lhpc.core.config import Config
    _seed_shared(tmp_path)
    svc = _shared_svc(tmp_path)
    assert svc.uninstall("igate", apply=True).ok
    assert _members(tmp_path) == ("loraham-chat",)
    comp = next(c for s in svc.stacks() if s.id == "chat"
                for c in s.components if c.id == "loraham-chat")
    inst = Installer(svc._paths, svc.stacks(), Config(values={}), svc._system)
    meta = inst._txn_meta(comp, comp.source, "pinned", "", str(tmp_path))
    assert set(meta["components"]) >= {"loraham-chat", "loraham-igate"}  # merged back


def test_auto_install_reconcile_absent_leaf_never_reports_dirty(tmp_path):
    # Addendum regression: an ABSENT source leaf can never yield the "local changes
    # present" row text (the user saw a HISTORICAL run's rows; the card now shows its
    # started/finished time).
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() if s.id == "voice"
                for c in s.components if c.id == "loraham-voice")
    action, why = svc._reconcile_group("src/LoRaHAM_Voice", comp)
    assert action == "install" and "local changes" not in why

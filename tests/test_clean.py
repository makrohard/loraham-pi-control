"""M7 — Clean all: DESTRUCTIVE per-stack purge behind strong confirmation. Removes
LHPC-owned sources (ownership-verified, shared-refcounted, dirty allowed), per-stack
config, markers, known-working history, logs (never an active job's), and registry
records. Never follows symlinks; local.toml/secrets/other stacks untouched."""

import json
import os
import time

from lhpc.core import known_working, source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, cmdlines=None, commands=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {},
                                               commands=commands or {}).system,
                             paths=Paths(runtime_root=tmp_path))


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



def _identity_cmds(tmp_path, rel, remote=_KISS_REMOTE):
    """Fake the current-identity git queries so a seeded record verifies."""
    from lhpc.core.probes.backends import CommandResult
    dest = str(tmp_path / "src" / rel)
    return {("git", "-C", dest, "config", "--get", "remote.origin.url"):
            CommandResult(0, remote + "\n", "")}


def _own(tmp_path, rel, comps):
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord(f"src/{rel}", "", "legacy", "", time.time(), "",
                                       "", tuple(comps)))


def _seed_kiss(tmp_path):
    """An installed kiss stack with config, logs, markers and history."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    (tmp_path / "src" / "loraham-kiss-tnc" / "code.c").write_text("x")
    (tmp_path / "src" / "loraham-kiss-tnc" / ".git").mkdir()
    _own(tmp_path, "loraham-kiss-tnc", ("loraham-kiss-tnc", "loraham-kiss-serial"))
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    (tmp_path / "config" / "stacks" / "kiss.toml").write_text('x = "1"\n')
    (tmp_path / "config" / "stacks" / "kiss@868.toml").write_text('x = "2"\n')
    (tmp_path / "config" / "local.toml").write_text("[operator]\n")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "build-loraham-kiss-tnc.log").write_text("log")
    (tmp_path / "logs" / "start-loraham-kiss-serial.log").write_text("log")
    (tmp_path / "logs" / "start-loraham-chat.log").write_text("other stack")
    known_working.record(paths, "kiss",
                         {"loraham-kiss-tnc": {"commit": "a" * 40, "selector": "pinned",
                                               "remote": "", "source_rel": "src/loraham-kiss-tnc"}},
                         {"confirmed_at": 1.0})
    d = tmp_path / "state" / "restart-required"
    d.mkdir(parents=True)
    (d / "kiss.json").write_text(json.dumps({"version": 1, "stack": "kiss"}))
    return paths


def test_clean_dry_run_names_removals_without_mutation(tmp_path):
    _seed_kiss(tmp_path)
    svc = _svc(tmp_path)
    plan = svc.clean("kiss", apply=False)
    assert plan.ok and "DESTRUCTIVE" in plan.summary
    blob = "\n".join(plan.details)
    assert "src/loraham-kiss-tnc" in blob and "kiss.toml" in blob
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()          # nothing mutated


def test_clean_requires_purge_flag(tmp_path):
    _seed_kiss(tmp_path)
    svc = _svc(tmp_path)
    res = svc.clean("kiss", apply=True, purge=False)
    assert not res.ok and "purge" in res.summary
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()


def test_clean_refuses_while_running(tmp_path):
    _seed_kiss(tmp_path)
    svc = _svc(tmp_path, cmdlines={555: ["loraham_kiss_tnc"]})
    res = svc.clean("kiss", apply=True, purge=True)
    assert not res.ok and "running" in res.summary.lower()
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()


def test_clean_removes_exact_set_and_preserves_the_rest(tmp_path):
    paths = _seed_kiss(tmp_path)
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc", _KISS_REMOTE)
    res = svc.clean("kiss", apply=True, purge=True)
    assert res.ok, res.details
    assert not (tmp_path / "src" / "loraham-kiss-tnc").exists()           # source gone
    assert not (tmp_path / "config" / "stacks" / "kiss.toml").exists()    # per-stack config gone
    assert not (tmp_path / "config" / "stacks" / "kiss@868.toml").exists()
    assert not (tmp_path / "logs" / "build-loraham-kiss-tnc.log").exists()
    assert not (tmp_path / "logs" / "start-loraham-kiss-serial.log").exists()
    assert not (tmp_path / "state" / "restart-required" / "kiss.json").exists()
    assert known_working.load(paths, "kiss") == []                        # history gone
    assert source_registry.read_record(paths, "src/loraham-kiss-tnc") is None
    # PRESERVED: operator config, other stacks' logs
    assert (tmp_path / "config" / "local.toml").exists()
    assert (tmp_path / "logs" / "start-loraham-chat.log").exists()


def test_clean_allows_dirty_tree_but_still_requires_ownership(tmp_path):
    # Dirty is the escape hatch — but an UNKNOWN (unowned) tree is still refused.
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    (tmp_path / "src" / "loraham-kiss-tnc" / "user.txt").write_text("data")
    svc = _svc(tmp_path)
    res = svc.clean("kiss", apply=True, purge=True)
    assert not res.ok
    assert any("refused" in d and "not a git checkout" in d for d in res.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc" / "user.txt").exists()


def test_clean_keeps_shared_source(tmp_path):
    # chat + igate share src/LoRaHAM_Daemon: cleaning CHAT keeps the shared checkout.
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    _own(tmp_path, "LoRaHAM_Daemon", ("loraham-chat", "loraham-igate"))
    svc = _svc(tmp_path)
    res = svc.clean("chat", apply=True, purge=True)
    assert res.ok, res.details
    assert (tmp_path / "src" / "LoRaHAM_Daemon").exists()
    assert any("kept" in d and "shared" in d for d in res.details)


def test_clean_linked_source_unlinks_leaf_only(tmp_path):
    import time as _t
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").write_text("x")
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "loraham-kiss-tnc").symlink_to(external)
    # an UNREGISTERED symlink at a non-link source is refused even by Clean
    res0 = _svc(tmp_path).clean("kiss", apply=True, purge=True)
    assert not res0.ok
    assert (tmp_path / "src" / "loraham-kiss-tnc").is_symlink()
    # a REGISTERED link record makes it LHPC's leaf -> removed (leaf only)
    # a LEGACY (v1-style) link record without a recorded target stays NON-DESTRUCTIVE
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "legacy", "", _t.time(),
                                       "", "link", ("loraham-kiss-tnc",)))
    import dataclasses as _dc, json as _json
    rp = source_registry.record_path(Paths(runtime_root=tmp_path), "src/loraham-kiss-tnc")
    legacy = _json.loads(rp.read_text()); legacy["version"] = 1; legacy.pop("link_target")
    rp.write_text(_json.dumps(legacy))
    res_legacy = _svc(tmp_path).clean("kiss", apply=True, purge=True)
    assert not res_legacy.ok
    assert any("non-destructive" in d for d in res_legacy.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc").is_symlink()      # leaf retained
    # a CURRENT record with the exact link target authorizes leaf-only removal
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "legacy", "", _t.time(),
                                       "", "link", ("loraham-kiss-tnc",),
                                       link_target=str(external)))
    res = _svc(tmp_path).clean("kiss", apply=True, purge=True)
    assert res.ok, res.details
    assert not (tmp_path / "src" / "loraham-kiss-tnc").is_symlink()
    assert (external / "keep").exists()                               # target untouched


def test_clean_component_target_refused(tmp_path):
    svc = _svc(tmp_path)
    res = svc.clean("loraham-kiss-tnc", apply=True, purge=True)
    assert not res.ok                                                 # stack targets only


def test_clean_refuses_identity_drift(tmp_path):
    # A registered tree whose HEAD moved to a different clean commit: even Clean refuses
    # (dirty is the escape hatch; a DIFFERENT tree is not LHPC's to purge).
    from lhpc.core.probes.backends import CommandResult
    _seed_kiss(tmp_path)
    # re-register with an exact commit, then fake a DIFFERENT actual HEAD
    _own2 = source_registry.RegistryRecord("src/loraham-kiss-tnc", _KISS_REMOTE, "pinned",
                                           "a" * 40, time.time(), "", "",
                                           ("loraham-kiss-tnc", "loraham-kiss-serial"))
    assert source_registry.write_record(Paths(runtime_root=tmp_path), _own2)
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc",
                         _KISS_REMOTE, head="b" * 40)
    res = svc.clean("kiss", apply=True, purge=True)
    assert not res.ok
    assert any("identity drift" in d for d in res.details)
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()             # tree unchanged


def test_clean_registry_removal_failure_is_incomplete_then_retry_cleans(tmp_path, monkeypatch):
    from lhpc.core import source_registry as sreg
    paths = _seed_kiss(tmp_path)
    svc = _bind_identity(_svc(tmp_path), tmp_path / "src" / "loraham-kiss-tnc", _KISS_REMOTE)
    monkeypatch.setattr(sreg, "remove_record", lambda *a, **k: False)
    res = svc.clean("kiss", apply=True, purge=True)
    assert not res.ok                                                   # INCOMPLETE, not success
    assert any("record could not be dropped" in d for d in res.details)
    assert not (tmp_path / "src" / "loraham-kiss-tnc").exists()         # source itself removed
    assert sreg.read_record(paths, "src/loraham-kiss-tnc") is not None   # orphan remains
    # an explicit RETRY (removal working again) cleans the orphan record
    monkeypatch.undo()
    res2 = _svc(tmp_path).clean("kiss", apply=True, purge=True)
    assert res2.ok, res2.details
    assert any("orphaned ownership record" in d for d in res2.details)
    assert sreg.read_record(paths, "src/loraham-kiss-tnc") is None

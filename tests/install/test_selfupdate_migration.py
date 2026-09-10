"""Config migration after a successful advance.

A persisted value that merely EQUALS the old default has to follow the new default forward; a
value the operator actually chose must not. Deciding that needs the pre-update semantics, so the
decision is recorded in a durable journal anchored to the git transaction that authorised it —
and every gate here fails CLOSED: an unreadable journal, a pending migration that must never be
overwritten, a candidate that must not select its own manifest.
"""
from __future__ import annotations

import pytest

import gitrepo
from lhpc.core import selfupdate
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, RealSystem


# --- a persisted value equal to the OLD default follows the new one, after a successful update -

def _seed_manifest(work, up, text) -> str:
    """Commit `text` as work/manifest.toml — a SOURCE-tracked manifest so a candidate's pre-update
    default is provable via `git show <from_head>:manifest.toml` — and resync the disposable upstream
    clone. Returns the new work HEAD. Call BEFORE any `gitrepo.upstream_commit(up)`."""
    (work / "manifest.toml").write_text(text)
    gitrepo.git(work, "add", "manifest.toml"); gitrepo.git(work, "commit", "-m", "manifest")
    gitrepo.git(work, "push", "-q", "origin", "main")
    gitrepo.git(up, "fetch", "-q", "origin"); gitrepo.git(up, "reset", "-q", "--hard", "origin/main")
    return gitrepo.git(work, "rev-parse", "HEAD")


def _svc(tmp_path, work):
    from lhpc.core.services import ControllerService
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (rt / "config" / "files").mkdir(parents=True, exist_ok=True)
    man = work / "manifest.toml"
    return ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt)), man, rt


def _svc_with_manifest(tmp_path, work, monkeypatch, *, opt_default):
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, tmp_path / "up",
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="opt"\n  kind="str"\n  default="{opt_default}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [[stack.component.param]]\n  name="empt"\n  kind="str"\n  default="HASDEF"\n')
    return _svc(tmp_path, work)


def test_legacy_default_migrates_overrides_preserved(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    _origin, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_with_manifest(tmp_path, work, monkeypatch, opt_default="OLD")
    gitrepo.upstream_commit(up)                                           # a real update is available
    # LEGACY seed (bypasses the overrides-only save path — writes every value verbatim):
    #   opt == old default (migrate), keep = genuine override, empt = intentional EMPTY override.
    cfgmod.save_stack_config(svc._paths, "s", {"opt": "OLD", "keep": "MINE", "empt": ""})
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 1
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "opt" not in stored                                    # default-equal removed
    assert stored.get("keep") == "MINE" and stored.get("empt") == ""   # overrides (incl. empty) kept
    # Post-update manifest (default OLD -> NEW): the migrated value now resolves to the NEW default.
    man.write_text(man.read_text().replace('default="OLD"', 'default="NEW"'))
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    assert svc2.stack_config("s")["opt"] == "NEW"                 # new default effective
    assert svc2.stack_config("s")["keep"] == "MINE" and svc2.stack_config("s")["empt"] == ""


def test_refused_update_does_not_migrate_config(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _origin, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_with_manifest(tmp_path, work, monkeypatch, opt_default="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"opt": "OLD", "keep": "MINE"})
    (work / "dirty.txt").write_text("uncommitted")                # dirty -> apply refused (no force)
    res = svc.self_update_apply()
    assert res.ok is False
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert stored.get("opt") == "OLD" and stored.get("keep") == "MINE"   # config UNCHANGED


def test_already_up_to_date_does_not_migrate(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _origin, work, up = gitrepo.repos(tmp_path)                          # no upstream commit -> already current
    svc, man, rt = _svc_with_manifest(tmp_path, work, monkeypatch, opt_default="OLD")
    cfgmod.save_stack_config(svc._paths, "s", {"opt": "OLD"})
    res = svc.self_update_apply()
    assert res.ok and res.data.get("already") is True
    assert cfgmod.load_stack_config(svc._paths, "s").get("opt") == "OLD"   # nothing migrated


def test_migration_honors_operator_token_and_canonical_form(tmp_path, monkeypatch):
    # A default may carry an operator token ({callsign}) and a stored value may be non-canonical;
    # migration must compare CANONICAL, operator-substituted forms.
    from lhpc.core import config as cfgmod
    _origin, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, up,
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="who"\n  kind="str"\n  default="{callsign}"\n'
        '  [[stack.component.param]]\n  name="num"\n  kind="int"\n  default="10"\n')
    gitrepo.upstream_commit(up)
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_operator_config(svc._paths, "XX0XXA"); svc._invalidate_config()
    # legacy: who == operator-substituted default; num == default (int, plain form)
    cfgmod.save_stack_config(svc._paths, "s", {"who": "XX0XXA", "num": "10"})
    assert svc.self_update_apply().ok
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "who" not in stored and "num" not in stored          # both recognised as at-default -> migrated


# --- every legacy form of the value, plus race safety and a durable retry ---------------------

def _rf_manifest(ropt="OLD", fopt="FOLD"):
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{ropt}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [stack.component.config_file]\n  path="{runtime}/config/files/x.conf"\n  fmt="env"\n'
        f'    [[stack.component.config_file.param]]\n    name="fopt"\n    key="FOPT"\n    default="{fopt}"\n')


def _svc_rf(tmp_path, work, monkeypatch, *, ropt="OLD", fopt="FOLD"):
    """A single-component stack 's' with a RUN param `ropt` (+ `keep`) and a FILE param `fopt`, so
    tests can seed scoped/flat legacy forms of both. The manifest is SOURCE-tracked in `work` (commit
    BEFORE any `gitrepo.upstream_commit(up)`)."""
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    _seed_manifest(work, tmp_path / "up", _rf_manifest(ropt, fopt))
    return _svc(tmp_path, work)


def _bump_default(work, up, *, ropt="NEW", fopt="FOLD") -> str:
    """Commit a manifest default CHANGE and advance origin/main (a real source transition); returns
    the new work HEAD."""
    return _seed_manifest(work, up, _rf_manifest(ropt, fopt))


def _bump_default_text(work, up, text) -> str:
    """Commit an arbitrary new manifest text and advance origin/main; returns the new work HEAD."""
    return _seed_manifest(work, up, text)


def _cand(from_head, *, key="ropt", kind="r", name="ropt", comp="c", band="", expected="OLD"):
    return {"stack": "s", "band": band, "key": key, "kind": kind, "comp": comp, "name": name,
            "expected": expected, "from_head": from_head}


def _seed_journal(svc, *, from_head, to_head, pending, slot="completed", branch="main", anchored=True):
    """Write a v3 journal record (default in the `completed` slot) and — unless `anchored=False` —
    create the MATCHING durable git transaction anchor it references. Returns the txid."""
    txid = selfupdate.new_txid()
    payload = {"from_head": from_head, "to_head": to_head, "branch": branch, "pending": pending}
    if anchored:
        assert selfupdate.create_anchor(svc._system, txid, payload)
    rec = {**payload, "txid": txid}
    selfupdate.write_migration_journal(svc._paths, {
        "completed": rec if slot == "completed" else None,
        "prepared": rec if slot == "prepared" else None})
    return txid


def test_scoped_run_param_legacy_form_migrates(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"__r__c__ropt": "OLD"})   # scoped legacy form only
    assert svc.self_update_apply().ok
    assert "__r__c__ropt" not in cfgmod.load_stack_config(svc._paths, "s")   # migrated


def test_scoped_file_param_legacy_form_migrates(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, fopt="FOLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"__f__c__fopt": "FOLD"})   # scoped file legacy form
    assert svc.self_update_apply().ok
    assert "__f__c__fopt" not in cfgmod.load_stack_config(svc._paths, "s")


def test_flat_and_scoped_precedence_both_forms(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    # both forms present: scoped is a genuine override, flat is the old default. Precedence: scoped
    # wins. Migration must remove the flat DEFAULT and keep the scoped OVERRIDE (precedence intact).
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "__r__c__ropt": "MINE"})
    assert svc.self_update_apply().ok
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored and stored.get("__r__c__ropt") == "MINE"
    val, amb = svc._resolve_stored(stored, "r", "c", "ropt", 1)
    assert val == "MINE" and not amb                                # scoped override still wins


def test_both_forms_at_default_both_migrate(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "__r__c__ropt": "OLD"})  # both == default
    svc.self_update_apply()
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored and "__r__c__ropt" not in stored    # every valid form migrated


def test_race_change_to_nonempty_override_survives(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    real = selfupdate.apply_update
    def racing(system, paths, **kw):                               # concurrent writer AFTER the snapshot
        r = real(system, paths, **kw)
        cfgmod.update_stack_config(svc._paths, "s", {"ropt": "RACED"})
        return r
    monkeypatch.setattr(selfupdate, "apply_update", racing)
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0               # value changed under us -> not removed
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "RACED"   # override survives


def test_race_change_to_empty_override_survives(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    real = selfupdate.apply_update
    def racing(system, paths, **kw):
        r = real(system, paths, **kw)
        cfgmod.update_stack_config(svc._paths, "s", {"ropt": ""}, clear_empty=False)  # intentional empty
        return r
    monkeypatch.setattr(selfupdate, "apply_update", racing)
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == ""   # empty override survives


def test_migration_write_failure_is_durable_and_retried(tmp_path, monkeypatch):
    import lhpc.core.services as services_mod
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    real_clear = services_mod.conditional_clear_stack_config
    calls = {"n": 0}
    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")                            # fail the FIRST migration write
        return real_clear(*a, **k)
    monkeypatch.setattr(services_mod, "conditional_clear_stack_config", flaky)
    # 1st apply: checkout advances, migration write FAILS -> reported incomplete, config untouched,
    #            intent persisted durably.
    res1 = svc.self_update_apply()
    assert res1.ok and res1.data.get("migrated") == 0 and res1.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"   # nothing discarded
    env, bad = selfupdate.read_migration_journal(svc._paths)
    assert not bad and env and env["completed"]["pending"]                  # durable intent kept
    # 2nd apply (now already-up-to-date): journal proves the transition -> recovery retries + succeeds.
    res2 = svc.self_update_apply()
    assert res2.ok and res2.data.get("already") is True
    assert res2.data.get("migrated") >= 1 and res2.data.get("pending_migrations") == 0
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")          # finally migrated
    assert selfupdate.read_migration_journal(svc._paths)[0] is None          # journal cleared


# --- a nested untracked repository, and a cleanup command that fails --------------------------

def test_nested_untracked_repo_blocks_then_overwrite_removes(env):
    new_head = gitrepo.upstream_commit(env["up"])
    w = env["work"]
    (w / "nested").mkdir()                                        # a NESTED untracked git repo
    gitrepo.git(w / "nested", "init", "-b", "main", ".")
    (w / "nested" / "f.txt").write_text("x")
    assert selfupdate.local_state(env["sys"])["dirty"] is True    # nested repo -> untracked -> dirty
    assert selfupdate.apply_update(env["sys"], env["paths"])["ok"] is False   # default: refuse
    assert (w / "nested").exists()
    res = selfupdate.apply_update(env["sys"], env["paths"], force=True)       # clean -ffd removes it
    assert res["ok"] and not res.get("cleanup_failed")
    assert not (w / "nested").exists() and selfupdate.local_state(env["sys"])["head"] == new_head


def _fake_commands(root, *, clean_rc):
    def R(rc=0, out="", err=""):
        return CommandResult(returncode=rc, stdout=out, stderr=err)
    r = str(root)
    return {
        ("git", "-C", r, "rev-parse", "HEAD"): R(out="aaaaaaaaa\n"),
        ("git", "-C", r, "rev-parse", "--abbrev-ref", "HEAD"): R(out="main\n"),
        ("git", "-C", r, "status", "--porcelain"): R(out="?? junk\n"),         # dirty -> needs force
        ("git", "-C", r, "fetch", "--quiet", "--force", "origin", "--",
         "refs/heads/main:refs/remotes/origin/main"): R(),
        ("git", "-C", r, "rev-parse", "origin/main"): R(out="bbbbbbbbb\n"),
        ("git", "-C", r, "show", "origin/main:lhpc/version.py"): R(out='__version__ = "0.1.2"\n'),
        ("git", "-C", r, "diff", "--name-only", "HEAD..origin/main", "--", "pyproject.toml"): R(),
        ("git", "-C", r, "reset", "--hard", "origin/main"): R(),               # reset SUCCEEDS
        ("git", "-C", r, "clean", "-ffd"): R(rc=clean_rc, err="cannot unlink 'x': Permission denied"),
    }


@pytest.mark.contract
def test_apply_cleanup_failure_is_truthful_partial(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    root = tmp_path / "repo"; root.mkdir()
    monkeypatch.setattr(selfupdate, "repo_root", lambda: root)
    fs = FakeSystem(commands=_fake_commands(root, clean_rc=1))
    res = selfupdate.apply_update(fs.system, gitrepo.runtime_paths(tmp_path), force=True)
    assert res["ok"] is True and res["cleanup_failed"] is True    # reset worked, cleanup did not
    assert "Permission denied" in res["cleanup_error"]
    assert "could NOT be removed" in res["message"]              # truthful, not a plain success


def test_service_maps_cleanup_failure_to_partial(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    monkeypatch.setattr(selfupdate, "apply_update", lambda *a, **k: {
        "ok": True, "cleanup_failed": True, "cleanup_error": "cannot unlink 'x'",
        "message": "Update aligned to upstream, but some untracked files could NOT be removed "
                   "— delete them manually, then restart the console.", "deps_changed": False})
    res = svc.self_update_apply(force=True)
    assert res.ok is False                                        # partial -> not a plain success
    assert "could NOT be removed" in res.summary
    assert any("cannot unlink" in d for d in res.details)


# --- the journal is a durable transaction: strictly validated, serialized between processes ---

import fcntl                                                              # noqa: E402
import json as _json                                                     # noqa: E402
from lhpc.core import runtime_fs as _rfs                                 # noqa: E402


def _head(work):
    return gitrepo.git(work, "rev-parse", "HEAD")


def test_interrupted_migration_recovered_by_fresh_service(tmp_path, monkeypatch):
    """Crash AFTER source transition, BEFORE config migration: a fresh post-update service must
    complete the original migration from the durable journal on the next explicit invocation."""
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")                    # commit OLD manifest, HEAD=A
    a = _head(work)
    b = _bump_default(work, up, ropt="NEW")                             # real transition -> NEW, HEAD=B
    svc, man, rt = _svc(tmp_path, work)                                 # FRESH post-update service (NEW default)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})          # value still at OLD default
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])   # anchored completed A->B
    res = svc.self_update_apply()
    assert res.ok and res.data.get("already") is True and res.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")       # original migration completed
    assert svc.stack_config("s")["ropt"] == "NEW"                        # now follows the new default
    assert selfupdate.read_migration_journal(svc._paths)[0] is None      # journal cleared


def test_journal_persist_failure_refuses_before_mutation(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before = _head(work)
    monkeypatch.setattr(selfupdate, "write_migration_journal", lambda *a, **k: False)   # cannot persist
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("journal_write_failed") is True
    assert _head(work) == before                                         # source NOT advanced
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # config untouched


def test_migration_failure_recovered_after_fresh_service(tmp_path, monkeypatch):
    import lhpc.core.services as services_mod
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    fail = {"on": True}
    real = services_mod.conditional_clear_stack_config
    def clr(*a, **k):
        if fail["on"]:
            raise OSError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(services_mod, "conditional_clear_stack_config", clr)
    res1 = svc.self_update_apply()                                       # advances, migration write FAILS
    assert res1.ok and res1.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"
    # a FRESH service (restart boundary) recovers the durable journal and completes it
    fail["on"] = False
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    res2 = svc2.self_update_apply()
    assert res2.ok and res2.data.get("already") is True and res2.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc2._paths, "s")
    assert selfupdate.read_migration_journal(svc2._paths)[0] is None


_A, _B = "a" * 40, "b" * 40                                                          # distinct valid shas
_CAND = {"stack": "s", "band": "", "key": "ropt", "kind": "r", "comp": "c", "name": "ropt",
         "expected": "OLD"}


def _rec(**over):
    r = {"from_head": _A, "to_head": _B, "branch": "main", "pending": [dict(_CAND)]}
    r.update(over)
    return r


_BAD_JOURNALS = [
    "not json {{{",                                                                  # corrupt JSON
    _json.dumps({"version": 1, "completed": None, "prepared": None}),                # wrong version
    _json.dumps({"version": 2, "completed": {"pending": "x"}}),                      # completed not a record
    _json.dumps({"version": 2, "completed": _rec(from_head="xyz")}),                 # INVALID sha form
    _json.dumps({"version": 2, "completed": _rec(to_head=_A)}),                      # EQUAL from_head/to_head
    _json.dumps({"version": 2, "completed": _rec(branch="bad branch!")}),            # invalid branch token
    _json.dumps({"version": 2, "completed": _rec(pending=[{"stack": "s"}])}),        # candidate missing fields
    _json.dumps({"version": 2, "completed": _rec(pending=[                           # dp_* target candidate
        {"stack": "s", "band": "", "key": "dp_433_TXPOWER", "kind": "r", "comp": "c",
         "name": "dp_433_TXPOWER", "expected": "x"}])}),
    _json.dumps({"version": 2, "prepared": _rec(pending=[                            # unsafe key form (prepared)
        {"stack": "s", "band": "", "key": "../evil", "kind": "r", "comp": "c",
         "name": "ropt", "expected": "x"}])}),
]


@pytest.mark.parametrize("bad", _BAD_JOURNALS)
def test_malformed_journal_blocks_without_mutation_or_deletion(tmp_path, monkeypatch, bad):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)                # a REAL update is available
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "dp_433_X": "keep"})   # protect a dp_* key
    before = _head(work)
    _rfs.write_marker(svc._paths, svc._paths.under("state", "selfupdate-migrate.json"), bad, 0o600)
    res = svc.self_update_apply()                                        # must not crash
    assert res.ok is False and res.data.get("journal_corrupt") is True   # typed recovery-blocked
    assert _head(work) == before                                         # NO source update
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert stored.get("ropt") == "OLD" and stored.get("dp_433_X") == "keep"   # NO deletion


def test_concurrent_apply_returns_busy_without_git_or_migration(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before = _head(work)
    held = _rfs.open_lock(svc._paths, svc._paths.under("state", "locks", "selfupdate.lock"))
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)                            # another process owns the op
    try:
        res = svc.self_update_apply()
        assert res.ok is False and res.data.get("busy") is True
        assert _head(work) == before                                    # no git ran
        assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # no migration ran
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()


def test_check_defers_while_apply_lock_held(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)                    # a pre-existing cached status
    before = selfupdate.read_cache(svc._paths).get("checked_at")
    held = _rfs.open_lock(svc._paths, svc._paths.under("state", "locks", "selfupdate.lock"))
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)                            # an apply owns the lock
    try:
        # the explicit check (also the code path the startup freshness thread uses) must DEFER
        res = svc.self_update_check()
        assert res.ok and res.data.get("deferred") is True
        assert selfupdate.read_cache(svc._paths).get("checked_at") == before   # no competing fetch/cache write
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()


# --- a present-but-unreadable journal fails CLOSED (the read is tri-state) --------------------

def _journal_path(svc):
    return svc._paths.under("state", "selfupdate-migrate.json")


def _make_state_dir(svc):
    p = _journal_path(svc).parent
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "directory", "fifo"])
def test_unreadable_journal_blocks_without_any_mutation(tmp_path, monkeypatch, kind):
    import os
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)                # a REAL update is available
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before_head = _head(work)
    before_cfg = dict(cfgmod.load_stack_config(svc._paths, "s"))
    selfupdate.refresh_cache(svc._system, svc._paths)                    # a pre-existing cache
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    _make_state_dir(svc)
    jp = _journal_path(svc)
    if kind == "symlink":
        (jp.parent / "real.json").write_text('{"version": 2, "completed": null, "prepared": null}')
        os.symlink("real.json", jp)                                      # a symlinked leaf -> unsafe
    elif kind == "dangling_symlink":
        os.symlink("does-not-exist.json", jp)
    elif kind == "directory":
        jp.mkdir()
    elif kind == "fifo":
        os.mkfifo(jp)                                                    # must NOT hang the reader
    env, blocked = selfupdate.read_migration_journal(svc._paths)
    assert env is None and blocked is True                              # tri-state: unreadable -> BLOCK
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("journal_corrupt") is True
    assert _head(work) == before_head                                   # no fetch/git mutation reached
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg      # no config mutation
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no cache mutation


def test_absent_journal_is_not_blocked(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    env, blocked = selfupdate.read_migration_journal(svc._paths)         # truly absent
    assert env is None and blocked is False                             # ONLY absent proceeds


# --- a pending migration can never be overwritten or lost -------------------------------------

def _seed_completed(svc, from_head, to_head, expected="OLD"):
    """A COMPLETED from_head->to_head transition whose migration is pending, ANCHORED (durable git
    provenance) so it is authorised."""
    _seed_journal(svc, from_head=from_head, to_head=to_head,
                  pending=[_cand(from_head, expected=expected)])


def _old_to_new_repo(tmp_path, monkeypatch):
    """A real OLD->NEW source transition: commit manifest OLD (A), bump to NEW (B). Returns
    (svc-at-B, work, up, a, b)."""
    _o, work, up = gitrepo.repos(tmp_path)
    _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")                     # manifest OLD, HEAD=A
    a = _head(work)
    b = _bump_default(work, up, ropt="NEW")                              # manifest NEW, HEAD=B
    svc, man, rt = _svc(tmp_path, work)                                  # post-transition service (NEW)
    return svc, man, rt, work, up, a, b


def test_prior_pending_migration_write_failure_defers_and_recovers(tmp_path, monkeypatch):
    # A prior completed transition's pending is migrated FIRST (proven against its OWN record). If the
    # config write fails, the pending is PRESERVED and the update is DEFERRED (never stranded); a later
    # invocation recovers it.
    import lhpc.core.services as services_mod
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_completed(svc, a, b)
    fail = {"on": True}
    real_clear = services_mod.conditional_clear_stack_config
    def clr(*args, **k):
        if fail["on"]:
            raise OSError("disk full")
        return real_clear(*args, **k)
    monkeypatch.setattr(services_mod, "conditional_clear_stack_config", clr)
    res1 = svc.self_update_apply()
    assert res1.ok and res1.data.get("deferred_recovery") is True and res1.data.get("pending_migrations") >= 1
    env, bad = selfupdate.read_migration_journal(svc._paths)
    assert not bad and env["completed"]["pending"]                       # prior pending PRESERVED
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # nothing deleted
    fail["on"] = False
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    res2 = svc2.self_update_apply()
    assert res2.data.get("migrated") >= 1 and "ropt" not in cfgmod.load_stack_config(svc2._paths, "s")
    assert svc2.stack_config("s")["ropt"] == "NEW"


def test_finalization_clear_failure_self_heals(tmp_path, monkeypatch):
    # If the journal-clear write after a successful recovery migration fails, the config change stands
    # and the stale journal is self-healed (re-run finds keys absent and clears) on a later invocation.
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_completed(svc, a, b)
    real_clear = selfupdate.clear_migration_journal
    calls = {"n": 0}
    def clr(paths):
        calls["n"] += 1
        if calls["n"] == 1:                                             # finalize clear FAILS to run
            return
        return real_clear(paths)
    monkeypatch.setattr(selfupdate, "clear_migration_journal", clr)
    svc.self_update_apply()
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")       # migration applied
    assert selfupdate.read_migration_journal(svc._paths)[0] is not None   # stale journal lingered
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    svc2.self_update_apply()
    assert selfupdate.read_migration_journal(svc2._paths)[0] is None      # self-healed / cleared
    assert svc2.stack_config("s")["ropt"] == "NEW"


def test_head_matches_neither_transition_state_blocks(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    before_head = _head(work)
    _seed_completed(svc, "a" * 40, "b" * 40)                             # completed.to_head is NOT current head
    before_env = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert _head(work) == before_head                                   # no git mutation
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"  # no config mutation
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_env  # journal preserved (evidence)


# --- the journal's `expected` is not authority: the pre-update default is proven from source --

def test_forged_expected_run_param_does_not_delete_override(tmp_path, monkeypatch):
    """A structurally-valid journal candidate with forged expected="MINE" must NOT delete an
    operator's current MINE override when the real pre-update default (proven from source) differs."""
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)   # real old default = OLD
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "MINE"})              # a genuine override
    _seed_journal(svc, from_head=a, to_head=b,                               # ANCHORED, but `expected` FORGED
                  pending=[_cand(a, key="ropt", name="ropt", expected="MINE")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0                          # forged expected ignored
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "MINE"   # override PRESERVED


def test_forged_expected_file_param_does_not_delete_override(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)   # real fopt default = FOLD
    cfgmod.save_stack_config(svc._paths, "s", {"file_fopt": "MINE"})
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="file_fopt", kind="f", name="fopt", expected="MINE")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_fopt") == "MINE"


def test_candidate_with_non_owned_band_is_not_deleted(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})               # band "" value
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, band="999")])   # band not owned by stack s
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") == 0 and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"    # nothing deleted


def test_genuine_transition_migrates_default_and_preserves_overrides(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s",
                             {"ropt": "OLD", "keep": "MINE", "file_fopt": ""})   # default / override / empty override
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 1
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored                                              # old-default value migrated
    assert stored.get("keep") == "MINE" and stored.get("file_fopt") == ""    # true + empty overrides kept
    assert svc.stack_config("s")["ropt"] == "NEW"                            # now follows the new default


# --- the fail-closed journal gate also covers explicit and startup freshness checks -----------

def _write_bad_journal(jp, kind):
    import os
    jp.parent.mkdir(parents=True, exist_ok=True)
    if kind == "malformed":
        jp.write_text("not json {{{")
    elif kind == "symlink":
        (jp.parent / "real.json").write_text('{"version": 2, "completed": null, "prepared": null}')
        os.symlink("real.json", jp)
    elif kind == "dangling_symlink":
        os.symlink("does-not-exist.json", jp)
    elif kind == "directory":
        jp.mkdir()
    elif kind == "fifo":
        os.mkfifo(jp)


@pytest.mark.parametrize("kind", ["malformed", "symlink", "dangling_symlink", "directory", "fifo"])
def test_check_blocks_on_unsafe_journal_without_fetch(tmp_path, monkeypatch, kind):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)                                                     # a REAL update is available
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    selfupdate.refresh_cache(svc._system, svc._paths)                        # a pre-existing cache
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    before_head, before_cfg = _head(work), dict(cfgmod.load_stack_config(svc._paths, "s"))
    _write_bad_journal(_journal_path(svc), kind)
    before_jbytes = _journal_path(svc).read_bytes() if kind in ("malformed", "symlink") else None
    res = svc.self_update_check()                                            # explicit check path
    assert res.ok is False and res.data.get("journal_corrupt") is True       # typed blocked, no 500
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # NO fetch / cache write
    assert _head(work) == before_head                                        # no git mutation
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg           # no config mutation
    if before_jbytes is not None:
        assert _journal_path(svc).read_bytes() == before_jbytes              # journal unchanged


def test_startup_freshness_check_under_corrupt_journal_makes_no_fetch(tmp_path, monkeypatch):
    # The startup thread calls ControllerService().self_update_check(); under a corrupt journal it must
    # block with no competing fetch or cache write (and never raise).
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    _write_bad_journal(_journal_path(svc), "malformed")
    res = svc.self_update_check()                                            # the exact call the startup thread makes
    assert res.ok is False and res.data.get("journal_corrupt") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no competing fetch/cache write


# --- a candidate's own from_head must not select the manifest; the recorded transition binds --

def _typed_manifest(kind, default):
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="{kind}"\n  default="{default}"\n')


def test_forged_candidate_from_head_does_not_select_manifest_run(tmp_path, monkeypatch):
    """A candidate forged to point at a DIFFERENT reachable commit X (where the default is MINE), while
    the recorded transition is A->B, must not delete a genuine MINE override: the RECORD's from_head
    (A) selects the manifest, not the candidate's."""
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    x = _seed_manifest(work, up, _rf_manifest(ropt="MINE"))              # commit X: default MINE
    _seed_manifest(work, up, _rf_manifest(ropt="OLD"))                   # commit A: default OLD
    b = _seed_manifest(work, up, _rf_manifest(ropt="NEW"))               # commit B (HEAD): default NEW
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "MINE"})          # a genuine override
    # forged journal: from_head = X (a MINE manifest), to_head = current HEAD, but NO matching anchor
    _seed_journal(svc, from_head=x, to_head=b, pending=[_cand(x, expected="MINE")], anchored=False)
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True     # typed recovery block
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "MINE"   # override PRESERVED


def test_forged_candidate_from_head_does_not_select_manifest_file(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    x = _seed_manifest(work, up, _rf_manifest(fopt="MINE"))
    _seed_manifest(work, up, _rf_manifest(fopt="FOLD"))
    b = _seed_manifest(work, up, _rf_manifest(fopt="FNEW"))
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"file_fopt": "MINE"})
    _seed_journal(svc, from_head=x, to_head=b, anchored=False,
                  pending=[_cand(x, key="file_fopt", kind="f", name="fopt", expected="MINE")])
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_fopt") == "MINE"


def test_real_apply_created_journal_migrates_genuine_old_default(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")      # work + origin at A (manifest OLD)
    (up / "manifest.toml").write_text(_rf_manifest("NEW"))               # upstream advances to B (manifest NEW)
    gitrepo.git(up, "add", "manifest.toml"); gitrepo.git(up, "commit", "-m", "NEW"); gitrepo.git(up, "push", "-q", "origin", "main")
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})           # at the (old) default
    res = svc.self_update_apply()                                        # REAL A->B; journal created by apply
    assert res.ok and not res.data.get("already") and res.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")       # genuine old-default migrated
    svc2, _m, _r = _svc(tmp_path, work)
    assert svc2.stack_config("s")["ropt"] == "NEW"


# --- both sides of the comparison use the OLD (pre-update) parameter semantics ----------------

def test_typed_schema_change_preserves_override_under_old_semantics(tmp_path, monkeypatch):
    """Old param is str default '10'; new param is int (which normalises ' 10' -> '10'). A stored ' 10'
    is a genuine override under OLD str semantics and must survive — it must NOT be normalised into the
    old default by the NEW int validator."""
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _typed_manifest("str", "10"))           # OLD: str default "10"
    b = _seed_manifest(work, up, _typed_manifest("int", "10"))           # NEW: int (normalises " 10")
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": " 10"})           # override (leading space) under str
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="10")])   # anchored
    res = svc.self_update_apply()
    assert res.data.get("migrated") == 0                                 # NOT normalised into old default
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == " 10"   # override survives


# --- the recovery-state gate holds on explicit and startup checks: no fetch, no mutation ------

def test_check_blocks_on_head_mismatched_prepared_no_fetch(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)                                                 # a real update available (fetch would act)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    selfupdate.refresh_cache(svc._system, svc._paths)
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    before_head, before_cfg = _head(work), dict(cfgmod.load_stack_config(svc._paths, "s"))
    _seed_journal(svc, from_head="a" * 40, to_head="b" * 40, slot="prepared",   # anchored, HEAD matches neither
                  pending=[_cand("a" * 40)])
    before_journal = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_check()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # NO fetch / cache write
    assert _head(work) == before_head                                            # no source mutation
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg               # no config mutation
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_journal    # no journal mutation


def test_startup_check_blocks_on_recovery_state_no_fetch(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")
    gitrepo.upstream_commit(up)
    selfupdate.refresh_cache(svc._system, svc._paths)
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    _seed_journal(svc, from_head="a" * 40, to_head="b" * 40,            # anchored completed, HEAD != to_head
                  pending=[_cand("a" * 40)])
    before_journal = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_check()                                        # the exact call the startup thread makes
    assert res.ok is False and res.data.get("recovery_required") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no competing fetch/cache write
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_journal


# --- only the durable git transaction anchor authorises a migration; the journal alone never --

def _dup_manifest(kind, name="dup", default="D"):
    """A stack whose param `name` is declared by TWO components (ambiguous flat key). `kind`: 'run' or
    'file'."""
    def comp(cid):
        p = (f'  [[stack.component.param]]\n  name="{name}"\n  kind="str"\n  default="{default}"\n'
             if kind == "run" else
             '  [stack.component.config_file]\n  path="{runtime}/config/files/' + cid + '.conf"\n  fmt="env"\n'
             f'    [[stack.component.config_file.param]]\n    name="{name}"\n    key="K"\n    default="{default}"\n')
        return (f'[[stack.component]]\nid="{cid}"\nname="{cid.upper()}"\nkind="service"\nrun="true"\n'
                f'readiness="process"\n{p}')
    return '[[stack]]\nid="s"\nname="S"\nmain="c1"\n' + comp("c1") + comp("c2")


def test_modified_journal_field_disagrees_with_anchor_blocked(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})           # would migrate if not blocked
    txid = _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    before_cfg = dict(cfgmod.load_stack_config(svc._paths, "s"))
    # tamper the runtime journal (a transition field) while the genuine ANCHOR is unchanged
    selfupdate.write_migration_journal(svc._paths, {
        "completed": {"from_head": "c" * 40, "to_head": b, "branch": "main", "txid": txid,
                      "pending": [_cand(a, expected="OLD")]}, "prepared": None})
    res = svc.self_update_apply()
    assert res.ok is False and res.data.get("recovery_required") is True   # disagrees with anchor -> block
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg          # nothing deleted


def test_duplicate_run_param_forged_flat_key_not_deleted(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _dup_manifest("run"))
    b = _bump_default_text(work, up, _dup_manifest("run", default="D2"))
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"dup": "D"})              # a FLAT value == old default
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="dup", name="dup", comp="c1")])  # forged flat key for an AMBIGUOUS name
    res = svc.self_update_apply()
    assert res.data.get("migrated") == 0 and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("dup") == "D"   # ambiguous flat -> NOT deleted


def test_duplicate_file_param_forged_flat_key_not_deleted(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _dup_manifest("file"))
    b = _bump_default_text(work, up, _dup_manifest("file", default="D2"))
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"file_dup": "D"})
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="file_dup", kind="f", name="dup", comp="c1")])
    res = svc.self_update_apply()
    assert res.data.get("migrated") == 0 and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_dup") == "D"


def test_unique_flat_run_and_file_candidates_migrate(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(ropt="OLD", fopt="FOLD"))
    b = _bump_default(work, up, ropt="NEW", fopt="FOLD")                 # ropt default changes; fopt unchanged
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "file_fopt": "FOLD"})   # unique FLAT keys at old default
    _seed_journal(svc, from_head=a, to_head=b, pending=[
        _cand(a, key="ropt", name="ropt", expected="OLD"),
        _cand(a, key="file_fopt", kind="f", name="fopt", expected="FOLD")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 2
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored and "file_fopt" not in stored           # both unique flat keys migrated


@pytest.mark.parametrize("state", ["missing", "stale", "mismatched"])
@pytest.mark.parametrize("action", ["apply", "check"])
def test_bad_anchor_blocks_apply_and_check_without_mutation(tmp_path, monkeypatch, state, action):
    from lhpc.core import config as cfgmod
    svc, man, rt, work, up, a, b = _old_to_new_repo(tmp_path, monkeypatch)
    gitrepo.upstream_commit(up)                                                 # a real update -> a fetch would act
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    selfupdate.refresh_cache(svc._system, svc._paths)
    if state == "missing":
        _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a)], anchored=False)
    elif state == "stale":
        txid = _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a)])
        selfupdate.delete_anchor(svc._system, txid)                     # anchor removed after the fact
    else:  # mismatched: genuine anchor, journal tampered to disagree
        txid = _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a)])
        selfupdate.write_migration_journal(svc._paths, {
            "completed": {"from_head": "c" * 40, "to_head": b, "branch": "main", "txid": txid,
                          "pending": [_cand(a)]}, "prepared": None})
    before_cache = selfupdate.read_cache(svc._paths).get("checked_at")
    before_head = _head(work)
    before_cfg = dict(cfgmod.load_stack_config(svc._paths, "s"))
    before_journal = selfupdate.read_migration_journal(svc._paths)[0]
    res = svc.self_update_apply() if action == "apply" else svc.self_update_check()
    assert res.ok is False and res.data.get("recovery_required") is True
    assert selfupdate.read_cache(svc._paths).get("checked_at") == before_cache   # no fetch / cache write
    assert _head(work) == before_head                                            # no source mutation
    assert cfgmod.load_stack_config(svc._paths, "s") == before_cfg               # no config deletion
    assert selfupdate.read_migration_journal(svc._paths)[0] == before_journal    # no journal mutation


# --- an update with no candidate writes neither anchor nor journal ----------------------------

def test_no_candidate_update_leaves_no_invalid_journal(tmp_path, monkeypatch):
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")      # work + origin at A
    (up / "manifest.toml").write_text(_rf_manifest("NEW"))               # upstream advances to B
    gitrepo.git(up, "add", "manifest.toml"); gitrepo.git(up, "commit", "-m", "NEW"); gitrepo.git(up, "push", "-q", "origin", "main")
    # NO stored config -> zero legacy candidates. Inject a finalization/clear failure that (with the
    # fix) is never even reached, so no invalid intermediate record can be left behind.
    monkeypatch.setattr(selfupdate, "clear_migration_journal", lambda *a, **k: False)
    res = svc.self_update_apply()                                        # REAL A->B advance, no candidates
    assert res.ok and not res.data.get("already") and res.data.get("migrated") == 0
    env, blocked = selfupdate.read_migration_journal(svc._paths)
    assert env is None and blocked is False                              # NO journal (valid or invalid) left
    refs = svc._system.runner.run(["git", "-C", str(work), "for-each-ref", selfupdate._ANCHOR_NS], timeout=5.0)
    assert refs.stdout.strip() == ""                                     # NO anchor left
    # a fresh service can still check + apply normally (no recovery block)
    svc2, _m, _r = _svc(tmp_path, work)
    assert svc2.self_update_check().data.get("recovery_required") is None
    assert svc2.self_update_apply().data.get("recovery_required") is None


def test_write_migration_journal_rejects_invalid_non_null_record(tmp_path):
    paths = gitrepo.runtime_paths(tmp_path)
    good = {"from_head": "a" * 40, "to_head": "b" * 40, "branch": "main",
            "txid": "a" * 16, "pending": [_cand("a" * 40)]}
    assert selfupdate.write_migration_journal(paths, {"completed": {**good, "txid": ""},   # EMPTY txid
                                                      "prepared": None}) is False
    assert selfupdate.write_migration_journal(paths, {"completed": {**good, "pending": []},  # EMPTY pending
                                                      "prepared": None}) is False
    d = {"completed": {"from_head": "a" * 40, "to_head": "b" * 40, "branch": "main", "txid": "",
                       "pending": [_cand("a" * 40)]}}
    assert selfupdate.write_migration_journal(paths, d) is False
    assert selfupdate.read_migration_journal(paths)[0] is None       # nothing was persisted
    assert selfupdate.write_migration_journal(paths, {"completed": good, "prepared": None}) is True   # valid accepted


# --- the current-manifest safety gate re-parses AFTER the update, never reusing a stale parse -

def _rf_manifest_no_ropt(fopt="FOLD"):
    """Post-update manifest where the run param `ropt` was REMOVED (keep + file `fopt` remain)."""
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n'
        '  [stack.component.config_file]\n  path="{runtime}/config/files/x.conf"\n  fmt="env"\n'
        f'    [[stack.component.config_file.param]]\n    name="fopt"\n    key="FOPT"\n    default="{fopt}"\n')


def _rf_manifest_no_fopt(ropt="OLD"):
    """Post-update manifest where the file param `fopt` was REMOVED (run params remain)."""
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{ropt}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n')


def test_run_param_removed_in_new_manifest_is_preserved_pending(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(ropt="OLD"))              # OLD: ropt exists
    b = _bump_default_text(work, up, _rf_manifest_no_ropt())            # NEW: ropt REMOVED, HEAD=B
    svc, man, rt = _svc(tmp_path, work)                                 # SAME service, current manifest = NEW
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})          # value at old default
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    res = svc.self_update_apply()
    assert res.data.get("deferred_recovery") is True and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"   # removed param -> NOT deleted


def test_file_param_removed_in_new_manifest_is_preserved_pending(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(fopt="FOLD"))
    b = _bump_default_text(work, up, _rf_manifest_no_fopt())            # fopt REMOVED
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"file_fopt": "FOLD"})
    _seed_journal(svc, from_head=a, to_head=b,
                  pending=[_cand(a, key="file_fopt", kind="f", name="fopt", expected="FOLD")])
    res = svc.self_update_apply()
    assert res.data.get("deferred_recovery") is True and res.data.get("pending_migrations") >= 1
    assert cfgmod.load_stack_config(svc._paths, "s").get("file_fopt") == "FOLD"


def test_fresh_service_retry_of_removed_param_is_non_destructive(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest(ropt="OLD"))
    b = _bump_default_text(work, up, _rf_manifest_no_ropt())
    svc, man, rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    svc.self_update_apply()                                             # deferred (ropt removed)
    # a FRESH post-update service retries the pending candidate: still unprovable -> non-destructive
    svc2 = ControllerService(manifest_path=man, system=RealSystem(), paths=Paths(runtime_root=rt))
    res2 = svc2.self_update_apply()
    assert res2.data.get("deferred_recovery") is True and res2.data.get("recovery_required") is None
    assert cfgmod.load_stack_config(svc2._paths, "s").get("ropt") == "OLD"   # still preserved


def test_retained_param_migrates_with_stale_incumbent_service_cache(tmp_path, monkeypatch):
    # The service's manifest cache is populated PRE-transition (old); the fresh current-manifest parse
    # must still let a RETAINED param migrate, preserving genuine + empty overrides.
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")     # work + origin at A (ropt OLD)
    svc.stacks()                                                        # populate _stacks from the OLD tree
    (up / "manifest.toml").write_text(_rf_manifest("NEW"))              # upstream -> B (ropt retained, default NEW)
    gitrepo.git(up, "add", "manifest.toml"); gitrepo.git(up, "commit", "-m", "NEW"); gitrepo.git(up, "push", "-q", "origin", "main")
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD", "keep": "MINE", "file_fopt": ""})
    res = svc.self_update_apply()                                       # REAL A->B; migration in-process
    assert res.ok and res.data.get("migrated") >= 1
    stored = cfgmod.load_stack_config(svc._paths, "s")
    assert "ropt" not in stored                                        # retained param migrated
    assert stored.get("keep") == "MINE" and stored.get("file_fopt") == ""   # overrides preserved


def test_removed_param_preserved_despite_stale_service_cache(tmp_path, monkeypatch):
    # THE defect-2 case: the incumbent service populated _stacks PRE-transition (OLD, HAS ropt), then
    # advances in-process to a manifest where ropt is REMOVED. Using the stale cache would delete the
    # stored key; the fresh post-update parse keeps it pending instead.
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    svc, man, rt = _svc_rf(tmp_path, work, monkeypatch, ropt="OLD")     # work + origin at A (ropt OLD)
    svc.stacks()                                                        # populate _stacks from OLD (HAS ropt)
    (up / "manifest.toml").write_text(_rf_manifest_no_ropt())           # upstream -> B: ropt REMOVED
    gitrepo.git(up, "add", "manifest.toml"); gitrepo.git(up, "commit", "-m", "rm ropt"); gitrepo.git(up, "push", "-q", "origin", "main")
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})          # at old default -> a candidate at A
    res = svc.self_update_apply()                                       # REAL A->B in the SAME (stale) service
    assert res.ok and res.data.get("pending_migrations", 0) >= 1        # ropt unprovable in NEW -> pending
    assert cfgmod.load_stack_config(svc._paths, "s").get("ropt") == "OLD"   # stale cache must NOT delete it




# --- a manifest from an EARLIER release still proves an old default --------------------------

def _rf_manifest_legacy_build_inputs(ropt="OLD"):
    """The same stack as `_rf_manifest`, plus a `build_inputs` entry in the shape releases before
    this one shipped: a name and a value, and NO `token` naming the argv token the value fills.
    The build keys sit BEFORE the param sub-tables, or TOML would bind them to the last table."""
    return (
        '[[stack]]\nid="s"\nname="S"\nmain="c"\n'
        '[[stack.component]]\nid="c"\nname="C"\nkind="service"\nrun="true"\nreadiness="process"\n'
        'build_marker=".done"\n'
        'build_steps=[{argv=["pip","install","meshtastic==2.7.11"]}]\n'
        'build_inputs=[{name="cli",value="2.7.11"}]\n'
        f'  [[stack.component.param]]\n  name="ropt"\n  kind="str"\n  default="{ropt}"\n'
        '  [[stack.component.param]]\n  name="keep"\n  kind="str"\n  default="D"\n')


def test_a_pre_token_manifest_still_migrates_its_old_default(tmp_path, monkeypatch):
    """The pre-update manifest is read for its PARAMETER definitions, and it was written by an
    OLDER release. Tightening how `build_inputs` must be declared (each entry now names the argv
    token it fills) made the whole parse fail on such a manifest, so `_prove_candidate` answered
    "unprovable" and every stored value equal to the old default stayed pending forever instead of
    following the new default.
    """
    from lhpc.core import config as cfgmod
    _o, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    a = _seed_manifest(work, up, _rf_manifest_legacy_build_inputs(ropt="OLD"))
    b = _seed_manifest(work, up, _rf_manifest(ropt="NEW"))
    svc, _man, _rt = _svc(tmp_path, work)
    cfgmod.save_stack_config(svc._paths, "s", {"ropt": "OLD"})
    _seed_journal(svc, from_head=a, to_head=b, pending=[_cand(a, expected="OLD")])
    res = svc.self_update_apply()
    assert res.ok and res.data.get("migrated") >= 1
    assert "ropt" not in cfgmod.load_stack_config(svc._paths, "s")
    assert svc.stack_config("s")["ropt"] == "NEW"

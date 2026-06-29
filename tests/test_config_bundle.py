"""3.9/§9 — the Config page is one validate-first, all-or-recoverable transaction:
an invalid value anywhere changes NO file; a failure mid-write rolls back; a pending
journal is recovered before the next save."""

import json

import pytest

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core import config as cfgmod


def _svc(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _snapshot(tmp_path):
    out = {}
    for p in (tmp_path / "config").rglob("*.toml"):
        out[str(p)] = p.read_text()
    return out


def _seed(svc, tmp_path):
    # A known-good baseline (local.toml + stacks/daemon.toml).
    r = svc.save_config_bundle("daemon", values={"radio": "both"},
                               callsign="N0CALL", locator="",
                               remotes={"loraham-daemon": "", "radiolib": ""})
    assert r.ok
    return _snapshot(tmp_path)


def test_valid_first_remote_invalid_second_changes_nothing(tmp_path):
    svc = _svc(tmp_path)
    before = _seed(svc, tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "433"},
                               remotes={"loraham-daemon": "https://github.com/x/y.git",
                                        "radiolib": "--upload-pack=evil"})
    assert not r.ok
    assert _snapshot(tmp_path) == before          # neither file changed


def test_valid_operator_invalid_stack_setting_changes_nothing(tmp_path):
    svc = _svc(tmp_path)
    before = _seed(svc, tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "999"},   # invalid enum
                               callsign="N0CALL-7")
    assert not r.ok and _snapshot(tmp_path) == before


def test_unknown_field_rejected_zero_mutation(tmp_path):
    svc = _svc(tmp_path)
    before = _seed(svc, tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "433", "bogus_key": "x"})
    assert not r.ok and any("unknown config field" in d for d in r.details)
    assert _snapshot(tmp_path) == before


def test_failure_after_first_replacement_restores_all(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    before = _seed(svc, tmp_path)
    # Fail the SECOND target write; the transaction must roll the first back.
    real = cfgmod._atomic_write
    calls = {"n": 0}
    def flaky(paths, path, text, mode=0o644):
        calls["n"] += 1
        # journal write is first; then target writes — fail the 2nd target write.
        if calls["n"] == 3:
            raise OSError("simulated mid-transaction failure")
        return real(paths, path, text, mode)
    monkeypatch.setattr(cfgmod, "_atomic_write", flaky)
    r = svc.save_config_bundle("daemon", values={"radio": "433"},
                               callsign="N0CALL", remotes={"loraham-daemon": "", "radiolib": ""})
    assert not r.ok
    monkeypatch.undo()
    assert _snapshot(tmp_path) == before          # both files restored
    assert not (tmp_path / "state" / "config-txn.json").exists()   # journal cleared


def test_pending_journal_is_recovered_before_next_save(tmp_path):
    svc = _svc(tmp_path)
    _seed(svc, tmp_path)
    stack_file = tmp_path / "config" / "stacks" / "daemon.toml"
    # Simulate a crash: tamper a file and leave a journal with its pre-image.
    pre = stack_file.read_text()
    stack_file.write_text("# CORRUPT partial write\n")
    journal = tmp_path / "state" / "config-txn.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"version": 1, "targets": [
        {"kind": "stack", "rel": "config/stacks/daemon.toml", "pre": pre,
         "existed": True, "mode": 0o644}]}))
    # The next bundle must recover (restore pre-image) before applying.
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert r.ok
    assert not journal.exists()
    assert 'radio = "868"' in stack_file.read_text()   # new value applied after recovery


def _write_journal(tmp_path, obj):
    j = tmp_path / "state" / "config-txn.json"
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return j


@pytest.mark.parametrize("obj", [
    "{ this is not json",                                            # malformed
    {"targets": [{"path": "/etc/passwd"}]},                          # wrong schema (no version)
    {"version": 1, "targets": [{"kind": "evil", "rel": "config/x"}]},  # unknown kind
    {"version": 1, "targets": [{"kind": "local", "rel": "/etc/passwd"}]},  # absolute
    {"version": 1, "targets": [{"kind": "stack", "rel": "../../etc/x.toml"}]},  # traversal
    {"version": 1, "targets": [                                       # duplicate target
        {"kind": "stack", "rel": "config/stacks/daemon.toml", "existed": False},
        {"kind": "stack", "rel": "config/stacks/daemon.toml", "existed": False}]},
])
def test_malicious_or_malformed_journal_blocks(tmp_path, obj):
    svc = _svc(tmp_path)
    before = _seed(svc, tmp_path)
    _write_journal(tmp_path, obj)
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r.ok and any("recovery-required" in d for d in r.details)
    assert (tmp_path / "state" / "config-txn.json").exists()   # journal retained
    assert _snapshot(tmp_path) == before                       # nothing mutated


def test_journal_absolute_target_not_touched(tmp_path):
    # An arbitrary absolute path in the journal must never be written/deleted.
    svc = _svc(tmp_path)
    _seed(svc, tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT TOUCH")
    _write_journal(tmp_path, {"version": 1, "targets": [
        {"kind": "local", "rel": str(victim), "existed": False}]})
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r.ok
    assert victim.read_text() == "DO NOT TOUCH"                # untouched


def test_rollback_failure_retains_journal_and_blocks_later(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _seed(svc, tmp_path)
    real = cfgmod._atomic_write
    stack_file = str(tmp_path / "config" / "stacks" / "daemon.toml")
    def fail_stack(paths, path, text, mode=0o644):
        if str(path) == stack_file:        # both the write AND its rollback fail
            raise OSError("simulated disk failure on stack file")
        return real(paths, path, text, mode)
    monkeypatch.setattr(cfgmod, "_atomic_write", fail_stack)
    r = svc.save_config_bundle("daemon", values={"radio": "433"})
    assert not r.ok and any("recovery-required" in d for d in r.details)
    assert (tmp_path / "state" / "config-txn.json").exists()       # journal retained
    # A later mutation must stay blocked until recovery can complete.
    r2 = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r2.ok and any("recovery-required" in d for d in r2.details)


def test_symlinked_config_txn_journal_blocks_recovery(tmp_path):
    # A symlinked transaction journal must not be read/followed -> recovery BLOCKS ("").
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    outside = tmp_path / "evil.json"
    outside.write_text('{"version": 1, "targets": [{"kind": "local", "rel": "x", "pre": "P", "existed": true, "mode": 420}]}')
    os.symlink(outside, cfgmod._txn_journal(paths))     # symlinked journal
    assert cfgmod.recover_config_transaction(paths) == ""   # blocked, never followed


def test_dangling_internal_journal_symlink_blocks_not_absent(tmp_path):
    # A journal that is a DANGLING symlink (to a nonexistent path INSIDE the root) must
    # NOT read as absent: Path.exists() follows the link and would return None (absent),
    # so recovery uses a no-follow presence check and BLOCKS instead.
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    # target stays inside the root (so _txn_journal/under does not raise) but does NOT exist
    os.symlink(tmp_path / "state" / "ghost.json", cfgmod._txn_journal(paths))
    assert not (tmp_path / "state" / "ghost.json").exists()          # genuinely dangling
    assert cfgmod.recover_config_transaction(paths) == ""            # BLOCK, not None

    # save_config_bundle must refuse while that journal entry is present.
    svc = _svc(tmp_path)
    r = svc.save_config_bundle("daemon", values={"radio": "868"})
    assert not r.ok and any("recovery-required" in d for d in r.details)


def test_external_journal_symlink_blocks_not_raises(tmp_path):
    # A journal symlink whose target ESCAPES the runtime root makes Paths.under() (via
    # realpath) raise PathContainmentError while locating the journal — recovery must
    # convert that into a clean BLOCK, never an uncaught exception or "absent".
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    outside = tmp_path.parent / "evil_external_journal.json"        # OUTSIDE the runtime root
    outside.write_text('{"version": 1, "targets": [{"kind": "local", "rel": "config/local.toml", "pre": "P", "existed": true, "mode": 420}]}')
    os.symlink(outside, tmp_path / "state" / "config-txn.json")     # escaping journal symlink
    try:
        assert cfgmod.recover_config_transaction(paths) == ""       # BLOCK, no exception
        svc = _svc(tmp_path)
        r = svc.save_config_bundle("daemon", values={"radio": "868"})
        assert not r.ok and any("recovery-required" in d for d in r.details)
        assert outside.read_text().startswith('{"version"')         # external file untouched
    finally:
        outside.unlink(missing_ok=True)


def test_save_config_bundle_refuses_symlinked_local(tmp_path):
    # The bundle's local.toml pre-read must go through the no-follow runtime reader: a
    # symlinked/escaping local.toml is refused (ConfigError -> bundle fails), never read
    # through. Operator change triggers the local.toml read path.
    import os
    svc = _svc(tmp_path)
    _seed(svc, tmp_path)
    local = tmp_path / "config" / "local.toml"
    outside = tmp_path.parent / "evil_local.toml"; outside.write_text("[operator]\ncallsign='X'\n")
    local.unlink()
    os.symlink(outside, local)                                     # symlinked leaf
    try:
        r = svc.save_config_bundle("daemon", values={"radio": "868"}, callsign="N0CALL-9")
        assert not r.ok
        assert outside.read_text() == "[operator]\ncallsign='X'\n"  # never written through
    finally:
        outside.unlink(missing_ok=True)

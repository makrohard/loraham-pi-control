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


# --- Area 2: type-safe, fail-closed local.toml rendering -------------------------------------

def _local(tmp_path):
    return tmp_path / "config" / "local.toml"


def test_local_root_scalars_and_types_survive_bundle_save(tmp_path):
    import tomllib
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('rootstr = "hi"\nenabled = true\nlimit = 5\nratio = 1.25\n'
                 '"quoted.key" = "q"\n[operator]\ncallsign = "OLD"\n[extra]\nflag = false\nn = 9\n')
    assert svc.save_config_bundle("meshcom", values={}, callsign="DK0ABC", locator="JO31aa").ok
    d = tomllib.loads(p.read_text())
    assert d["rootstr"] == "hi" and d["enabled"] is True and d["limit"] == 5 and d["ratio"] == 1.25
    assert d["quoted.key"] == "q"                              # quoted root key stays literal
    assert d["extra"]["flag"] is False and d["extra"]["n"] == 9   # unrelated table types exact
    assert d["operator"]["callsign"] == "DK0ABC"


def test_local_control_and_multiline_strings_round_trip(tmp_path):
    import tomllib
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text("")
    cfgmod._write_local_tables(_svc(tmp_path)._paths, p, {"t": {"s": "a\tb\nc\r\\\"\x00é中"}})
    assert tomllib.loads(p.read_text())["t"]["s"] == "a\tb\nc\r\\\"\x00é中"


@pytest.mark.parametrize("bad", ['arr = [1, 2]\n', '[a.b]\nx = 1\n',
                                 'when = 2020-01-01T00:00:00\n'])
def test_local_unsupported_structures_block_and_preserve(tmp_path, bad):
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(bad)
    before = p.read_text()
    r = svc.save_config_bundle("meshcom", values={}, callsign="DK0ABC", locator="JO31aa")
    assert not r.ok and p.read_text() == before                # refused, byte-for-byte preserved


def test_operator_and_component_remote_use_safe_renderer(tmp_path):
    # save_operator_config / save_component_remote must preserve unrelated root scalars + types.
    import tomllib
    paths = _svc(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('keepme = 42\nenabled = true\n')
    cfgmod.save_operator_config(paths, "DL1ABC", "JO31")
    cfgmod.save_component_remote(paths, "loraham-daemon", "https://x/y.git")
    d = tomllib.loads(p.read_text())
    assert d["keepme"] == 42 and d["enabled"] is True          # unrelated root scalars/types kept
    assert d["operator"]["callsign"] == "DL1ABC"
    assert d["remotes"]["loraham-daemon"] == "https://x/y.git"


def test_operator_save_refuses_when_local_has_unsupported(tmp_path):
    paths = _svc(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('arr = [1, 2]\n'); before = p.read_text()
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.save_operator_config(paths, "DL1ABC", "JO31")
    assert p.read_text() == before                              # preserved, not mutated


# --- Area 3: remote patch ownership (service-enforced) ---------------------------------------

def test_remote_patch_rejects_foreign_component(tmp_path):
    import tomllib
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcore-pi" = "https://b/mc.git"\n'); before = p.read_text()
    # meshcore-pi is NOT a component of meshcom -> reject, zero mutation
    r = svc.save_config_bundle("meshcom", values={}, remotes={"meshcore-pi": "https://evil/x.git"})
    assert not r.ok and p.read_text() == before


def test_remote_patch_own_component_preserves_others(tmp_path):
    import tomllib
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcore-pi" = "https://b/mc.git"\n')
    assert svc.save_config_bundle("meshcom", values={},
                                  remotes={"meshcom-bridge": "https://c/br.git"}).ok
    rem = tomllib.loads(p.read_text())["remotes"]
    assert rem["meshcom-bridge"] == "https://c/br.git" and rem["meshcore-pi"] == "https://b/mc.git"


def test_remote_clear_own_preserves_other_components(tmp_path):
    import tomllib
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcom-bridge" = "https://c/br.git"\n"meshcore-pi" = "https://b/mc.git"\n')
    assert svc.save_config_bundle("meshcom", values={}, remotes={"meshcom-bridge": ""}).ok
    rem = tomllib.loads(p.read_text())["remotes"]
    assert "meshcom-bridge" not in rem and rem["meshcore-pi"] == "https://b/mc.git"


# --- Patch [operator]/[remotes] by owned keys; fail closed on wrong shape --------------------

def test_save_operator_config_patches_and_preserves_extra_keys(tmp_path):
    import tomllib
    from lhpc.core import config as cfg
    paths = _svc(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('rootn = 3\n[operator]\ncallsign = "OLD"\nlocator = "AA00"\n'
                 'note = "portable profile"\nenabled = true\ncount = 5\n[extra]\nx = 1\n')
    cfg.save_operator_config(paths, "DJ0CHE", "JO31")
    d = tomllib.loads(p.read_text())
    assert d["operator"]["callsign"] == "DJ0CHE" and d["operator"]["locator"] == "JO31"
    assert d["operator"]["note"] == "portable profile"        # extra string preserved
    assert d["operator"]["enabled"] is True and d["operator"]["count"] == 5   # bool/int types kept
    assert d["rootn"] == 3 and d["extra"]["x"] == 1            # unrelated root scalar + table kept


def test_bundle_operator_update_preserves_extra_operator_keys(tmp_path):
    import tomllib
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[operator]\ncallsign = "OLD"\nlocator = "AA00"\nnote = "keep"\nflag = false\n')
    assert svc.save_config_bundle("meshcom", values={}, callsign="DK0ABC", locator="JO31aa").ok
    op = tomllib.loads(p.read_text())["operator"]
    assert op["callsign"] == "DK0ABC" and op["note"] == "keep" and op["flag"] is False


def test_scalar_operator_shape_rejects_operator_save(tmp_path):
    from lhpc.core import config as cfg
    paths = _svc(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('operator = "manual text"\n'); before = p.read_text()
    with pytest.raises(cfg.ConfigError):
        cfg.save_operator_config(paths, "DJ0CHE", "JO31")
    assert p.read_text() == before                            # byte-for-byte preserved


def test_scalar_remotes_shape_rejects_bundle_remote_save(tmp_path):
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('remotes = "not a table"\n'); before = p.read_text()
    r = svc.save_config_bundle("meshcom", values={}, remotes={"meshcom-bridge": "https://c/br.git"})
    assert not r.ok and p.read_text() == before


def test_scalar_remotes_via_component_remote_is_controlled_failure(tmp_path):
    # No raw ValueError/TypeError from dict("string") — a normal failed ActionResult, file intact.
    svc = _svc(tmp_path)
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('remotes = "x"\n'); before = p.read_text()
    r = svc.save_component_remote("loraham-daemon", "https://x/y.git")
    assert not r.ok and p.read_text() == before


def test_component_remote_set_and_clear_preserve_others(tmp_path):
    import tomllib
    from lhpc.core import config as cfg
    paths = _svc(tmp_path)._paths
    p = _local(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[remotes]\n"meshcore-pi" = "https://b/mc.git"\n')
    cfg.save_component_remote(paths, "loraham-daemon", "https://x/y.git")
    rem = tomllib.loads(p.read_text())["remotes"]
    assert rem["loraham-daemon"] == "https://x/y.git" and rem["meshcore-pi"] == "https://b/mc.git"
    cfg.save_component_remote(paths, "loraham-daemon", "")     # clear
    rem = tomllib.loads(p.read_text())["remotes"]
    assert "loraham-daemon" not in rem and rem["meshcore-pi"] == "https://b/mc.git"


def test_audit_config_lock_is_bounded(tmp_path):
    # AUDIT CC1: a held exclusive config lock must make a second acquire fail fast with
    # ConfigLockBusy, not block forever (which would wedge the fixed web thread pool).
    import threading, time
    from lhpc.core import config as cfg
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    paths = Paths(runtime_root=tmp_path)
    held, release = threading.Event(), threading.Event()
    def holder():
        with cfg.config_lock(paths):
            held.set(); release.wait(10)
    threading.Thread(target=holder, daemon=True).start()
    assert held.wait(5)
    t0 = time.monotonic()
    try:
        with cfg.config_lock(paths, timeout=0.5):
            assert False, "should not have acquired"
    except cfg.ConfigLockBusy:
        pass
    assert time.monotonic() - t0 < 3.0                # bounded, not wedged
    assert isinstance(cfg.ConfigLockBusy("x"), cfg.ConfigError)   # caught by existing handlers
    release.set()


def test_audit_deep_toml_is_diagnostic_not_crash(tmp_path):
    # AUDIT IN2: pathologically deep inline-table nesting -> ConfigError, never RecursionError.
    from lhpc.core import config as cfg
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text("a = " + "{x = " * 3000 + "1" + "}" * 3000)
    paths = Paths(runtime_root=tmp_path)
    cfgobj = cfg.load_config(paths)                   # must not raise RecursionError
    assert cfgobj.diagnostics                         # surfaced as a diagnostic

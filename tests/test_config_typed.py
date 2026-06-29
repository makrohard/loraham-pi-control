"""3.3 — config cache is invalidated on save (no stale values across web/CLI actions);
runtime TOML structure is validated at load (wrong-typed sections -> diagnostics + safe
defaults, never a crash; non-string remotes are dropped so they never reach Git)."""

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.config import load_config


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_saved_operator_is_visible_immediately(tmp_path):
    svc = _svc(tmp_path)
    assert svc.config().operator.callsign == ""            # primes the cache
    r = svc.save_config_bundle("daemon", callsign="DJ0CHE-7", locator="")
    assert r.ok
    assert svc.config().operator.callsign == "DJ0CHE-7"    # NOT the stale cache


def test_saved_remote_is_visible_immediately(tmp_path):
    svc = _svc(tmp_path)
    _ = svc.config()                                       # prime cache
    r = svc.save_component_remote("loraham-daemon", "https://github.com/x/y.git")
    assert r.ok
    assert svc.config().remotes.get("loraham-daemon") == "https://github.com/x/y.git"


def test_reset_config_reloads_fresh(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})
    _ = svc.config()
    assert svc.reset_config("daemon").ok
    assert svc.config() is not None                        # fresh read, no crash


def test_operator_wrong_type_is_diagnostic_not_crash(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('operator = "x"\n')
    cfg = load_config(Paths(runtime_root=tmp_path))        # must NOT raise
    assert cfg.operator.callsign == "" and cfg.operator.locator == ""
    assert any("operator" in d for d in cfg.diagnostics)


def test_remotes_wrong_type_is_diagnostic_not_crash(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('remotes = "x"\n')
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.remotes == {} and any("remotes" in d for d in cfg.diagnostics)


def test_non_string_remote_value_dropped(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text(
        '[remotes]\ngood = "https://github.com/x/y.git"\nbad = 123\n')
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.remotes == {"good": "https://github.com/x/y.git"}
    assert any("bad" in d for d in cfg.diagnostics)


def test_operator_non_string_field_is_unset(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('[operator]\ncallsign = 12345\n')
    cfg = load_config(Paths(runtime_root=tmp_path))
    assert cfg.operator.callsign == "" and any("callsign" in d for d in cfg.diagnostics)


# --- §5.1: Config.get is safe on a wrong-typed section ------------------------

def test_config_get_non_table_section_returns_default():
    from lhpc.core.config import Config
    cfg = Config(values={"install": "oops-a-string", "ok": {"k": "v"}})
    assert cfg.get("install", "adopt_search_root", "~/src") == "~/src"   # no AttributeError
    assert cfg.get("ok", "k") == "v"
    assert cfg.get("missing", "k", 42) == 42


# --- §5.2: changed-param hints computed against the PRE-SAVE config -----------

def test_changed_param_produces_apply_hint(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})           # baseline
    r = svc.save_config_bundle("daemon", values={"radio": "868"})       # CHANGE (restart)
    assert r.ok and any("Start-time change" in d or "Restart" in d for d in r.details)


def test_unchanged_param_produces_no_apply_hint(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})
    r = svc.save_config_bundle("daemon", values={"radio": "433"})       # no change
    assert r.ok and not any("Start-time change" in d or "Restart" in d for d in r.details)


# --- E: reset_config is descriptor-safe + typed ------------------------------

def test_reset_config_refuses_symlinked_leaf(tmp_path):
    import os
    from lhpc.core.config import _stack_config_path
    svc = _svc(tmp_path)
    cfg_band = svc._config_band("daemon", "")
    p = _stack_config_path(svc._paths, "daemon", cfg_band)
    p.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil.toml"; outside.write_text("x=1")
    os.symlink(outside, p)                        # symlinked config leaf
    r = svc.reset_config("daemon")
    assert not r.ok and "unsafe" in r.summary     # typed block, no crash / no 500
    assert outside.read_text() == "x=1"           # never unlinked through the symlink


def test_reset_config_normal_then_idempotent(tmp_path):
    svc = _svc(tmp_path)
    svc.save_config_bundle("daemon", values={"radio": "433"})
    r = svc.reset_config("daemon")
    assert r.ok and "reset to defaults" in r.summary
    r2 = svc.reset_config("daemon")
    assert r2.ok and "already at defaults" in r2.summary

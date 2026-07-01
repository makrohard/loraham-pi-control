"""P1 — config writes are atomic + locked with restrictive permissions; malformed
local config is a diagnostic, not a crash."""

import os

import pytest

from lhpc.core.config import (load_config, save_operator_config, save_stack_config,
                              _atomic_write, config_lock)
from lhpc.core.paths import Paths


def test_stack_config_write_is_atomic_and_no_temp_left(tmp_path):
    p = save_stack_config(Paths(runtime_root=tmp_path), "kiss", {"a": "1"}, "868")
    assert p.exists() and p.read_text().strip().endswith('a = "1"')
    # no leftover temp files in the directory
    assert not [f for f in p.parent.iterdir() if f.name.endswith(".tmp")]


def test_local_config_is_mode_0600(tmp_path):
    p = save_operator_config(Paths(runtime_root=tmp_path), "N0CALL", "")
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_stack_config_is_mode_0644(tmp_path):
    p = save_stack_config(Paths(runtime_root=tmp_path), "kiss", {"a": "1"}, "868")
    assert oct(p.stat().st_mode & 0o777) == "0o644"


def test_malformed_local_config_is_a_diagnostic_not_a_crash(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text("this = is = not valid toml [[[")
    cfg = load_config(Paths(runtime_root=tmp_path))     # must NOT raise
    assert cfg.diagnostics and "malformed" in cfg.diagnostics[0]
    assert cfg.operator.callsign == ""                  # fell back to defaults


def test_atomic_write_replaces_without_partial(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    target = tmp_path / "x.txt"
    _atomic_write(paths, target, "first")
    _atomic_write(paths, target, "second")
    assert target.read_text() == "second"


def test_config_lock_serializes_without_deadlock(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    with config_lock(paths):
        pass
    with config_lock(paths):     # a second acquisition after release must not block
        pass


def test_runtime_config_write_goes_through_runtime_fs(tmp_path, monkeypatch):
    # config._atomic_write must delegate to runtime_fs.atomic_write (containment + fsync).
    from lhpc.core import config as cfgmod, runtime_fs
    seen = {}
    real = runtime_fs.atomic_write
    def spy(paths, path, text, mode=0o644):
        seen["called"] = str(path)
        return real(paths, path, text, mode)
    monkeypatch.setattr(runtime_fs, "atomic_write", spy)
    paths = Paths(runtime_root=tmp_path)
    cfgmod.save_operator_config(paths, "N0CALL", "")
    assert seen.get("called", "").endswith("config/local.toml")


def test_save_operator_config_refuses_symlinked_local(tmp_path):
    import os
    from lhpc.core import config as cfgmod
    from lhpc.core.paths import PathContainmentError
    (tmp_path / "config").mkdir(parents=True)
    outside = tmp_path / "evil.toml"; outside.write_text("")
    os.symlink(outside, tmp_path / "config" / "local.toml")     # symlinked leaf
    paths = Paths(runtime_root=tmp_path)
    # A symlinked runtime config is refused at the no-follow READ (ConfigError) before any
    # write, OR at the no-follow write — either way it is never followed/written through.
    with pytest.raises((OSError, PathContainmentError, cfgmod.ConfigError)):
        cfgmod.save_operator_config(paths, "N0CALL", "")
    assert outside.read_text() == ""                            # never written through


def test_save_profile_is_contained(tmp_path):
    import os
    from lhpc.core import profiles
    from lhpc.core.paths import PathContainmentError
    d = profiles.profiles_dir(Paths(runtime_root=tmp_path)); d.mkdir(parents=True)
    outside = tmp_path / "p.toml"; outside.write_text("orig")
    os.symlink(outside, d / "loraham-daemon.toml")
    from lhpc.core.profiles import ConfirmedProfile
    prof = ConfirmedProfile(component_id="loraham-daemon")
    with pytest.raises((OSError, PathContainmentError)):
        profiles.save_profile(Paths(runtime_root=tmp_path), prof)
    assert outside.read_text() == "orig"


def test_reset_config_preserves_daemon_profile_and_unrelated(tmp_path):
    # reset_config owns ONLY normal Config-page keys (run/file/autostart). Daemon-profile dp_*
    # overrides and unrelated manual scalars are PRESERVED (removed via the locked safe merge).
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core import config as cfgmod
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    p = cfgmod._stack_config_path(svc._paths, "daemon", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('radio = "868"\ndp_433_CADIDLE = "40"\nmanual = 7\n')   # normal + dp_ + unrelated
    assert svc.reset_config("daemon").ok
    stored = cfgmod.load_stack_config(svc._paths, "daemon")
    assert "radio" not in stored                    # normal run-param reset to default
    assert stored["dp_433_CADIDLE"] == "40"          # daemon-profile override preserved
    assert stored["manual"] == 7                     # unrelated manual scalar preserved

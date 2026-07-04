"""E — path containment and config safety: mutable paths stay under the runtime
root, corrupt local config is preserved (not overwritten), and remote overrides
are validated before persistence."""

import pytest

from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.config import (ConfigError, save_operator_config, save_component_remote,
                              save_stack_config, load_config)
from lhpc.core import validators


def test_resolve_source_rejects_escape(tmp_path):
    p = Paths(runtime_root=tmp_path)
    assert p.resolve_source("src/daemon") == (tmp_path / "src" / "daemon")
    for bad in ("../escape", "src/../../etc", "/etc/passwd", "a/../../b"):
        with pytest.raises(PathContainmentError):
            p.resolve_source(bad)


def test_under_confines(tmp_path):
    p = Paths(runtime_root=tmp_path)
    assert p.under("config", "stacks", "kiss.toml").parent == tmp_path / "config" / "stacks"
    with pytest.raises(PathContainmentError):
        p.under("..", "outside")


def test_corrupt_local_config_is_preserved_not_overwritten(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    corrupt = "this is = not [valid toml"
    (cfg_dir / "local.toml").write_text(corrupt)
    # Saving operator identity must REFUSE (raise), leaving the file untouched.
    with pytest.raises(ConfigError):
        save_operator_config(Paths(runtime_root=tmp_path), "N0CALL", "")
    assert (cfg_dir / "local.toml").read_text() == corrupt        # preserved verbatim


def test_remote_override_rejects_unsafe(tmp_path):
    for bad in ("--upload-pack=evil", "file:///etc", "ext::sh -c id", "http://x;rm",
                "git@host:path; rm", "ftp://x"):
        with pytest.raises(validators.ValidationError):
            save_component_remote(Paths(runtime_root=tmp_path), "loraham-daemon", bad)


def test_remote_override_accepts_safe(tmp_path):
    p = save_component_remote(Paths(runtime_root=tmp_path), "loraham-daemon",
                              "https://github.com/x/y.git")
    assert p.exists()
    save_component_remote(Paths(runtime_root=tmp_path), "loraham-daemon",
                          "git@github.com:x/y.git")   # scp-style ssh allowed


def test_stack_config_path_rejected_for_bad_band(tmp_path):
    from lhpc.core.config import _stack_config_path
    with pytest.raises(validators.ValidationError):
        _stack_config_path(Paths(runtime_root=tmp_path), "kiss", "../../x")


def test_under_rejects_symlink_escape(tmp_path):
    import os
    rt = tmp_path / "rt"
    (rt).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # a symlink INSIDE the runtime root that points OUTSIDE it
    os.symlink(outside, rt / "evil")
    p = Paths(runtime_root=rt)
    with pytest.raises(PathContainmentError):
        p.under("evil", "secret.toml")        # resolves outside the runtime root


def test_generated_wrapper_cannot_escape_via_symlinked_start_dir(tmp_path):
    import os
    from lhpc.core.config import Config
    from lhpc.core.install import Installer
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    from lhpc.core.probes import RealSystem
    rt = tmp_path / "rt"
    (rt).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, rt / "start")          # start/ is a symlink escaping the root
    comp = Component(id="s-app", name="app", kind=ComponentKind.SERVICE,
                     run_argv=("./app",), run_cwd="{source}", start_order=0,
                     source=SourceSpec(path="src/app"))
    inst = Installer(Paths(runtime_root=rt),
                     (Stack(id="s", name="s", main="s-app", components=(comp,)),),
                     Config(values={"install": {"adopt_search_root": str(tmp_path / "rt")}}),
                     RealSystem())
    plan = inst.apply_bootstrap()
    # the wrapper action must FAIL (containment), and nothing is written outside.
    assert any(a.kind == "wrapper" and a.status == "failed" for a in plan.actions)
    assert not list(outside.iterdir())


def test_atomic_write_rejects_symlink_leaf(tmp_path):
    import os
    from lhpc.core.config import _atomic_write
    from lhpc.core.paths import Paths, PathContainmentError
    outside = tmp_path / "outside.toml"
    outside.write_text("original")
    target = tmp_path / "f.toml"
    os.symlink(outside, target)            # pre-existing symlink leaf
    with pytest.raises((OSError, PathContainmentError)):
        _atomic_write(Paths(runtime_root=tmp_path), target, "new data")
    assert outside.read_text() == "original"   # link was not followed


def test_log_open_rejects_symlink_leaf(tmp_path):
    import os
    from lhpc.core import runtime_fs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "logs").mkdir()
    outside = tmp_path / "evil.log"
    outside.write_text("")
    link = tmp_path / "logs" / "start-x.log"
    os.symlink(outside, link)                       # planted symlink leaf in logs/
    with pytest.raises(OSError):                    # anchored open_log_append refuses it
        runtime_fs.open_log_append(paths, link)


def test_config_lock_rejects_symlink_leaf(tmp_path):
    import os, pytest as _pt
    from lhpc.core.config import config_lock
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("")
    os.symlink(outside, tmp_path / "config" / ".lock")     # symlinked lock leaf
    with _pt.raises(OSError):
        with config_lock(Paths(runtime_root=tmp_path)):
            pass


def test_config_lock_acquires_normally(tmp_path):
    from lhpc.core.config import config_lock
    from lhpc.core.paths import Paths
    with config_lock(Paths(runtime_root=tmp_path)):
        pass            # no exception -> acquired + released cleanly
    assert (tmp_path / "config" / ".lock").exists()

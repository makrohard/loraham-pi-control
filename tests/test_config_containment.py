"""P1 — generated-config destination containment: runtime ({runtime}/...) vs managed
source (relative) vs rejected (absolute/placeholder/traversal); parent-symlink escape
and symlink leaves refused; linked external sources never written."""

import os
import pytest

from lhpc.core.services import ControllerService
from lhpc.core.model import Component, ComponentKind, SourceSpec, FileConfig
from lhpc.core.paths import Paths, PathContainmentError
from lhpc.core.probes.backends import FakeSystem


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _comp():
    return Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"),
                     config_file=FileConfig(path="conf/app.toml", fmt="keyval", params=()))


def test_runtime_destination_policy(tmp_path):
    dest = _svc(tmp_path)._resolve_config_dest(_comp(), "{runtime}/config/files/x.conf")
    assert dest.status == "ok" and dest.policy == "runtime"


def test_relative_source_destination_policy(tmp_path):
    dest = _svc(tmp_path)._resolve_config_dest(_comp(), "conf/x.conf")
    assert dest.status == "ok" and dest.policy == "source"


def test_arbitrary_absolute_rejected(tmp_path):
    for raw in ("/etc/passwd", "{home}/x", "../../escape/x.conf"):
        dest = _svc(tmp_path)._resolve_config_dest(_comp(), raw)
        assert dest.status == "failed" and dest.policy == "reject"


def test_linked_source_is_readonly(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle, "is_linked_source", lambda self, c: True)
    dest = _svc(tmp_path)._resolve_config_dest(_comp(), "conf/x.conf")
    assert dest.status == "linked-readonly"


def test_source_config_through_symlinked_parent_rejected(tmp_path):
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, src / "conf")               # parent symlink escapes source root
    with pytest.raises(PathContainmentError):
        svc._write_source_config(_comp(), src / "conf" / "app.toml", "data")
    assert not (outside / "app.toml").exists()


def test_runtime_config_through_symlinked_parent_rejected(tmp_path):
    rt = tmp_path / "rt"; rt.mkdir()
    svc = _svc(rt)
    (rt / "config").mkdir()
    outside = tmp_path / "outside"; outside.mkdir()       # OUTSIDE the runtime root (rt)
    os.symlink(outside, rt / "config" / "files")          # symlink escapes runtime root
    dest = svc._resolve_config_dest(_comp(), "{runtime}/config/files/x.conf")
    assert dest.status == "failed" and "escapes" in dest.detail


def test_base_file_escape_rejected_at_manifest_parse(tmp_path):
    from lhpc.core.manifest import _parse_file_config, ManifestError
    with pytest.raises(ManifestError):
        _parse_file_config({"path": "conf/x.toml", "fmt": "toml-update", "base": "/etc/hosts"})


def test_normal_runtime_config_writes(tmp_path):
    svc = _svc(tmp_path)
    res = svc.write_config_files("voice")           # {runtime}/config/files/... (shipped)
    assert any(w.status == "written" and "/config/files/" in w.path for w in res)


def test_nested_symlink_escape_creates_nothing(tmp_path):
    # source/conf -> outside ; config output conf/newdir/app.toml must create NEITHER
    # outside/newdir NOR a config file (containment proven before any mkdir).
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, src / "conf")                 # source/conf -> outside
    with pytest.raises(PathContainmentError):
        svc._write_source_config(_comp(), "conf/newdir/app.toml", "data=1")
    assert not (outside / "newdir").exists()          # no dir created through the symlink
    assert not any(outside.rglob("*.toml"))           # no config file created


def test_intermediate_dirs_created_safely(tmp_path):
    # A legitimate nested relative path creates real intermediate dirs under the source.
    svc = _svc(tmp_path)
    (tmp_path / "src" / "app").mkdir(parents=True)
    svc._write_source_config(_comp(), "a/b/app.toml", "data=1")
    leaf = tmp_path / "src" / "app" / "a" / "b" / "app.toml"
    assert leaf.read_text() == "data=1"
    assert (tmp_path / "src" / "app" / "a").is_dir() and not (tmp_path / "src" / "app" / "a").is_symlink()


def test_descriptor_walk_refuses_swapped_symlink_component(tmp_path):
    # A multi-level path where an intermediate dir is a symlink (as a swap-after-check
    # would produce) must be refused at the syscall — nothing created in the target.
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, src / "a")                  # intermediate component is a symlink
    with pytest.raises(PathContainmentError):
        svc._write_source_config(_comp(), "a/b/c.toml", "x")
    assert not (outside / "b").exists()             # never descended through the symlink


def test_descriptor_walk_refuses_symlink_leaf(tmp_path):
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "secret.toml"; outside.write_text("orig")
    os.symlink(outside, src / "app.toml")           # leaf is a symlink
    # The write now routes through runtime_fs.atomic_write, which refuses a symlink leaf
    # with the typed PathContainmentError (was a bare OSError from the hand-rolled writer).
    from lhpc.core.paths import PathContainmentError
    with pytest.raises((OSError, PathContainmentError)):
        svc._write_source_config(_comp(), "app.toml", "x")
    assert outside.read_text() == "orig"            # not clobbered through the link


def test_source_base_read_refuses_symlink_leaf(tmp_path):
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "secret"; outside.write_text("SECRET")
    os.symlink(outside, src / "base.toml")          # base file swapped to a symlink
    with pytest.raises(OSError):
        svc._read_source_base(_comp(), "base.toml")


def test_source_base_read_refuses_symlinked_parent(tmp_path):
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "base.toml").write_text("X")
    os.symlink(outside, src / "sub")                # parent swapped to a symlink
    with pytest.raises(PathContainmentError):
        svc._read_source_base(_comp(), "sub/base.toml")


def test_source_base_read_reads_real_file(tmp_path):
    svc = _svc(tmp_path)
    src = tmp_path / "src" / "app"; src.mkdir(parents=True)
    (src / "base.toml").write_text("k = 1\n")
    assert svc._read_source_base(_comp(), "base.toml") == "k = 1\n"

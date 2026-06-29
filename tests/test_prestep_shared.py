"""One shared, execution-time-safe pre-step engine: the in-process controller start
(commands.run_pre_steps) and the generated wrapper (wrapper_runtime.apply_pre_steps)
use the SAME normalizer + mutation core, with identical containment/symlink policy."""

import os
import pytest

from pathlib import Path
from lhpc.core import commands, wrapper_runtime
from lhpc.core.paths import Paths, PathContainmentError


def test_normalizer_feeds_the_shared_engine(tmp_path):
    steps = [{"kind": "mkdir", "path": "{runtime}/d", "mode": "755"}]
    tuples = commands.normalize_pre_steps(steps, str(tmp_path), str(tmp_path))
    assert tuples == [("mkdir", str(tmp_path / "d"), "755")]


def test_render_wrapper_uses_same_normalized_steps(tmp_path):
    from lhpc.core.model import Component, ComponentKind
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE, run_argv=("./app",),
                     pre_steps=({"kind": "mkdir", "path": "{runtime}/w", "mode": "700"},))
    script = commands.render_wrapper(comp, "start", str(tmp_path), str(tmp_path))
    # the wrapper embeds the SAME tuple the normalizer produces
    assert repr(("mkdir", str(tmp_path / "w"), "700")) in script


def test_controller_and_wrapper_produce_identical_tree(tmp_path):
    steps = [{"kind": "mkdir", "path": "{runtime}/shared", "mode": "750"},
             {"kind": "symlink", "src": "{source}/x", "dst": "{runtime}/shared/link"}]
    a = tmp_path / "a"; a.mkdir(); b = tmp_path / "b"; b.mkdir()
    commands.run_pre_steps(steps, str(a), str(a), "")              # controller path
    tuples = commands.normalize_pre_steps(steps, str(b), str(b))
    wrapper_runtime.apply_pre_steps(str(b), tuples)               # wrapper path
    for root in (a, b):
        assert (root / "shared").is_dir()
        assert (root / "shared" / "link").is_symlink()
    assert oct((a / "shared").stat().st_mode)[-3:] == oct((b / "shared").stat().st_mode)[-3:]


def test_escaping_destination_rejected_in_controller(tmp_path):
    steps = [{"kind": "mkdir", "path": "/etc/evil", "mode": "755"}]
    with pytest.raises(commands.CommandError):
        commands.run_pre_steps(steps, str(tmp_path), str(tmp_path), "")


def test_mkdir_through_symlink_leaf_rejected(tmp_path):
    # destination leaf is a symlink (introduced after generation) -> refuse to mkdir it.
    outside = tmp_path / "outside"; outside.mkdir()
    link = tmp_path / "rt" / "d"; link.parent.mkdir(parents=True)
    os.symlink(outside, link)
    paths = Paths(runtime_root=tmp_path / "rt")
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("mkdir", str(link), "755")])


def test_symlink_replaces_existing_link_but_not_real_dir(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    realdir = tmp_path / "realdir"; realdir.mkdir()
    with pytest.raises(PathContainmentError):
        wrapper_runtime.apply_steps(paths, [("symlink", str(tmp_path / "t"), str(realdir))])
    # an existing symlink leaf IS replaceable
    link = tmp_path / "l"; os.symlink(tmp_path / "old", link)
    wrapper_runtime.apply_steps(paths, [("symlink", str(tmp_path / "new"), str(link))])
    assert os.readlink(link) == str(tmp_path / "new")

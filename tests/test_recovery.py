"""Tests for the source commands (install/update/uninstall)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lhpc.core.probes import RealSystem
from lhpc.core.services import ControllerService


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "f").write_text("x\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "c")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _svc(tmp_path: Path):
    """A controller whose manifest has one component sourced from a temp repo."""
    head = _repo(tmp_path / "rt" / "local" / "comp")
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[[stack]]\nid = "s"\nname = "s"\n'
        '[[stack.component]]\nid = "c"\nname = "c"\nkind = "service"\n'
        '[stack.component.source]\n'
        f'path = "src/comp"\npin_commit = "{head}"\nlocal_dir = "comp"\n'
    )
    rt = tmp_path / "rt"
    (rt / "config").mkdir(parents=True)
    (rt / "config" / "local.toml").write_text(
        f'[install]\nadopt_search_root = "{tmp_path / "rt" / "local"}"\n'
    )
    from lhpc.core.paths import Paths
    return ControllerService(manifest_path=manifest, system=RealSystem(),
                             paths=Paths(runtime_root=rt)), rt


def test_install_readopts_missing_source(tmp_path):
    svc, rt = _svc(tmp_path)
    assert not (rt / "src" / "comp").exists()
    result = svc.install("s", apply=True)
    assert result.ok and (rt / "src" / "comp" / "f").exists()


def test_update_overview_lists_source(tmp_path):
    svc, _ = _svc(tmp_path)
    result = svc.update("s")
    assert result.ok and any("c" in d for d in result.details)


def test_uninstall_removes_source_but_keeps_config(tmp_path):
    svc, rt = _svc(tmp_path)
    svc.install("s", apply=True)
    assert (rt / "src" / "comp").exists()
    svc.uninstall("s", apply=True)
    assert not (rt / "src" / "comp").exists()
    assert (rt / "config" / "local.toml").exists()   # config preserved

"""Tests for runtime bootstrap, readable wrappers and safe source adoption."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lhpc.core.config import Config
from lhpc.core.install import RUNTIME_SUBDIRS, Installer
from lhpc.core.model import (
    Component,
    ComponentKind,
    SourceSpec,
    Stack,
)
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env=env)


def _make_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "file.txt").write_text("hello\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _installer(runtime: Path, stacks, search_root: Path) -> Installer:
    cfg = Config(values={"install": {"adopt_search_root": str(search_root)}})
    return Installer(Paths(runtime_root=runtime), stacks, cfg, RealSystem())


def _stack(component: Component) -> tuple[Stack, ...]:
    return (Stack(id="s", name="s", components=(component,)),)


def test_bootstrap_creates_layout(tmp_path):
    inst = _installer(tmp_path / "rt", (), tmp_path / "rt")
    inst.apply_bootstrap()
    rt = tmp_path / "rt"
    for sub in RUNTIME_SUBDIRS:
        assert (rt / sub).is_dir()
    assert (rt / "config" / "local.toml").exists()
    secrets = rt / "config" / "secrets.toml"
    assert secrets.exists()
    assert (secrets.stat().st_mode & 0o777) == 0o600


def test_bootstrap_is_idempotent_and_preserves_local_config(tmp_path):
    rt = tmp_path / "rt"
    inst = _installer(rt, (), tmp_path / "rt")
    inst.apply_bootstrap()
    (rt / "config" / "local.toml").write_text("[operator]\ncallsign = \"KEEP\"\n")
    inst.apply_bootstrap()  # second run
    assert "KEEP" in (rt / "config" / "local.toml").read_text()


def test_bootstrap_hardens_runtime_root_to_owner_only(tmp_path):
    """The runtime root must not be group/other-WRITABLE (the controller-identity boundary):
    bootstrap enforces 0700, fixing the "identity UNSAFE: runtime root is group/other-
    writable" seen on a default-umask (0775) root — and tightens an already-loose root."""
    import os
    rt = tmp_path / "rt"
    inst = _installer(rt, (), tmp_path / "rt")
    inst.apply_bootstrap()
    assert (rt.stat().st_mode & 0o022) == 0                # no group/other write
    # A subsequently-loosened root is re-tightened on the next bootstrap (idempotent fix).
    os.chmod(rt, 0o775)
    plan = inst.plan_bootstrap()
    assert any(a.kind == "harden" and a.status == "planned" for a in plan.actions)
    inst.apply_bootstrap()
    assert (rt.stat().st_mode & 0o022) == 0


def test_adopt_clean_repo_verifies_pin_match(tmp_path):
    head = _make_repo(tmp_path / "rt" / "local" / "myrepo")
    comp = Component(id="s-c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/myrepo", pin_commit=head,
                                       local_dir="myrepo"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt" / "local")
    action = inst.adopt_source(comp)
    assert action.status == "done" and "match" in action.detail
    assert (tmp_path / "rt" / "src" / "myrepo" / "file.txt").exists()


def test_adopt_missing_local_fails(tmp_path):
    comp = Component(id="s-c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/nope", local_dir="nope"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt" / "local")
    action = inst.adopt_source(comp)
    assert action.status == "failed" and "no local checkout" in action.detail


def test_adopt_refuses_overwrite_without_force(tmp_path):
    _make_repo(tmp_path / "rt" / "local" / "myrepo")
    dest = tmp_path / "rt" / "src" / "myrepo"
    dest.mkdir(parents=True)
    comp = Component(id="s-c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/myrepo", local_dir="myrepo"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt" / "local")
    action = inst.adopt_source(comp)
    assert action.status == "skipped"


def test_adopt_link_strategy_symlinks_in_place(tmp_path):
    _make_repo(tmp_path / "rt" / "local" / "myrepo")
    comp = Component(id="s-c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/myrepo", local_dir="myrepo", strategy="link"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt" / "local")
    # A linked external tree is an explicit MUTABLE dev checkout (it has no pin to satisfy
    # the production-safe 'pinned' default), so the operator selects 'dev' explicitly.
    action = inst.adopt_source(comp, source="dev")
    dest = tmp_path / "rt" / "src" / "myrepo"
    assert action.status == "done"
    assert dest.is_symlink() and dest.resolve() == (tmp_path / "rt" / "local" / "myrepo")


def test_plan_install_reports_present_and_absent(tmp_path):
    head = _make_repo(tmp_path / "rt" / "local" / "myrepo")
    comp = Component(id="s-c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/myrepo", pin_commit=head, local_dir="myrepo"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt" / "local")
    assert inst.plan_install().actions[0].kind == "adopt"   # absent -> adopt
    inst.adopt_source(comp)
    assert inst.plan_install().actions[0].kind == "verify"  # present -> verify



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


def test_wrapper_is_a_python_launcher_no_shell(tmp_path):
    # Wrappers are generated as Python launchers (os.execvpe), never Bash.
    comp = Component(id="s-app", name="app", kind=ComponentKind.SERVICE,
                     run_argv=("python3", "app.py"), run_cwd="{source}", start_order=0,
                     source=SourceSpec(path="src/app"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt")
    inst.apply_bootstrap()
    wrapper = tmp_path / "rt" / "start" / "s-0-app-start"
    assert wrapper.exists() and os.access(wrapper, os.X_OK)
    text = wrapper.read_text()
    assert text.startswith("#!/usr/bin/env python3")
    assert "os.execvpe" in text and "sys.argv[1:]" in text
    for bad in ("#!/usr/bin/env bash", "exec cd", "eval", "/bin/sh", "bash -c"):
        assert bad not in text


def test_wrapper_execs_with_exact_argv_cwd_env_and_forwarded_args(tmp_path):
    import subprocess, sys
    # A component whose run argv is a python one-liner dumping argv/cwd/env (one per
    # line — no braces, which are reserved for placeholders) to a file.
    out = tmp_path / "rt" / "work" / "out.txt"
    (tmp_path / "rt" / "work").mkdir(parents=True)
    script = ("import os,sys; "
              f"open({str(out)!r},'w').write(chr(10).join("
              "sys.argv[1:] + [os.getcwd(), os.environ.get('WHO','')]))")
    comp = Component(id="s-app", name="app", kind=ComponentKind.SERVICE,
                     run_argv=(sys.executable, "-c", script),
                     run_cwd="{runtime}/work", run_env=(("WHO", "lhpc"),),
                     start_order=0, source=SourceSpec(path="src/app"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt")
    inst.apply_bootstrap()
    wrapper = tmp_path / "rt" / "start" / "s-0-app-start"
    subprocess.run([sys.executable, str(wrapper), "--extra", "x y"], check=True, timeout=20)
    lines = out.read_text().split("\n")
    assert lines[0:2] == ["--extra", "x y"]              # forwarded as separate tokens
    assert lines[-2] == str(tmp_path / "rt" / "work")    # required cwd
    assert lines[-1] == "lhpc"                            # env preserved


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


def test_wrapper_runtime_helper_runs_presteps_and_revalidates(tmp_path):
    # Workstream C: wrapper pre-steps run through the installed runtime helper, which
    # rejects a symlink leaf introduced at a pre-step destination AFTER generation.
    import os, subprocess, sys
    out = tmp_path / "rt" / "out.txt"
    comp = Component(id="s-app", name="app", kind=ComponentKind.SERVICE,
                     run_argv=(sys.executable, "-c",
                               f"open({str(out)!r},'w').write('ran')"),
                     run_cwd="{runtime}",
                     pre_steps=({"kind": "mkdir", "path": "{runtime}/work"},),
                     start_order=0, source=SourceSpec(path="src/app"))
    inst = _installer(tmp_path / "rt", _stack(comp), tmp_path / "rt")
    inst.apply_bootstrap()
    wrapper = tmp_path / "rt" / "start" / "s-0-app-start"
    repo = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": repo}
    # happy path: pre-step mkdir runs, then exec writes the file
    subprocess.run([sys.executable, str(wrapper)], check=True, env=env, timeout=20)
    assert out.read_text() == "ran" and (tmp_path / "rt" / "work").is_dir()
    # attack: replace the pre-step destination with a symlink escaping the root
    (tmp_path / "rt" / "work").rmdir()
    outside = tmp_path / "evil"; outside.mkdir()
    os.symlink(outside, tmp_path / "rt" / "work")
    r = subprocess.run([sys.executable, str(wrapper)], env=env, timeout=20,
                       capture_output=True, text=True)
    assert r.returncode == 4 and "unsafe pre-step" in r.stderr

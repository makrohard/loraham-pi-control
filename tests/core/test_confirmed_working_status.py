"""Confirmed-working status wiring: the badge derives from OPERATOR-CONFIRMED known-working
compositions (core/known_working.py) — a clean source whose HEAD appears in a stored
composition of its stack; a dirty tree is never confirmed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lhpc.core import known_working
from lhpc.core.model import Component, ComponentKind, ProfileState, SourceSpec
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.status import StatusProber


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _repo_at(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "f").write_text("x\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "c")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _confirmed_map(paths: Paths, stack_id: str) -> dict:
    """{comp_id: {commits}} — the same map build_snapshot derives from the store."""
    out: dict = {}
    for comp in known_working.load(paths, stack_id):
        for cid, entry in comp["entries"].items():
            out.setdefault(cid, set()).add(entry["commit"])
    return out


def _record(paths: Paths, head: str):
    ok, msg = known_working.record(
        paths, "s", {"c": {"commit": head, "selector": "pinned", "remote": "",
                           "source_rel": "src/c"}},
        {"confirmed_at": 1.0})
    assert ok, msg


def test_clean_source_in_composition_is_confirmed_working(tmp_path):
    head = _repo_at(tmp_path / "src" / "c")
    paths = Paths(runtime_root=tmp_path)
    _record(paths, head)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c", pin_commit=head))
    prober = StatusProber(RealSystem(), paths, _confirmed_map(paths, "s"))
    assert prober.assess_component(comp).profile_state is ProfileState.CONFIRMED_WORKING


def test_clean_source_at_other_confirmed_commit_is_confirmed(tmp_path):
    # A confirmed composition commit that is NOT the manifest pin (e.g. a stable update the
    # operator confirmed) still shows confirmed-working when the tree is clean at it.
    head = _repo_at(tmp_path / "src" / "c")
    paths = Paths(runtime_root=tmp_path)
    _record(paths, head)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c", pin_commit="0" * 40))   # pin differs
    prober = StatusProber(RealSystem(), paths, _confirmed_map(paths, "s"))
    assert prober.assess_component(comp).profile_state is ProfileState.CONFIRMED_WORKING


def test_dirty_source_is_never_confirmed(tmp_path):
    head = _repo_at(tmp_path / "src" / "c")
    (tmp_path / "src" / "c" / "f").write_text("changed\n")  # make it dirty
    paths = Paths(runtime_root=tmp_path)
    _record(paths, head)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c", pin_commit=head))
    prober = StatusProber(RealSystem(), paths, _confirmed_map(paths, "s"))
    assert prober.assess_component(comp).profile_state is ProfileState.LOCALLY_MODIFIED


def test_no_composition_is_not_confirmed(tmp_path):
    head = _repo_at(tmp_path / "src" / "c")
    paths = Paths(runtime_root=tmp_path)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c", pin_commit=head))
    prober = StatusProber(RealSystem(), paths, _confirmed_map(paths, "s"))
    assert prober.assess_component(comp).profile_state is not ProfileState.CONFIRMED_WORKING

"""Tests for the confirmed-working profile catalogue and its status wiring."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lhpc.core.model import (
    Component,
    ComponentKind,
    ConfirmedProfile,
    ProfileState,
    SourceSpec,
)
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.profiles import load_profiles, save_profile
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


def test_profile_roundtrip(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "profiles").mkdir()
    save_profile(paths, ConfirmedProfile(component_id="x", commit="abc",
                                         daemon_version="111a",
                                         tests_passed=("live", "tx")))
    loaded = load_profiles(paths)
    assert loaded["x"].commit == "abc" and loaded["x"].tests_passed == ("live", "tx")


def _component():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c"))


def test_clean_match_with_profile_is_confirmed_working(tmp_path):
    head = _repo_at(tmp_path / "src" / "c")
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c", pin_commit=head))
    profiles = {"c": ConfirmedProfile(component_id="c", commit=head)}
    prober = StatusProber(RealSystem(), Paths(runtime_root=tmp_path), profiles)
    assert prober.assess_component(comp).profile_state is ProfileState.CONFIRMED_WORKING


def test_dirty_source_is_never_confirmed(tmp_path):
    head = _repo_at(tmp_path / "src" / "c")
    (tmp_path / "src" / "c" / "f").write_text("changed\n")  # make it dirty
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/c", pin_commit=head))
    profiles = {"c": ConfirmedProfile(component_id="c", commit=head)}
    prober = StatusProber(RealSystem(), Paths(runtime_root=tmp_path), profiles)
    assert prober.assess_component(comp).profile_state is ProfileState.LOCALLY_MODIFIED

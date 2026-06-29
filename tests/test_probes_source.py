"""Tests for the bounded local-git source/pin probe."""

from __future__ import annotations

from lhpc.core.model import SourceSpec, SourceState
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.probes.source import probe_source

_PIN = "a" * 40
_OTHER = "b" * 40
_ABS = "/runtime/src/comp"


def _fake(head: str, porcelain: str = "", *, repo: bool = True) -> FakeSystem:
    paths = {_ABS}
    if repo:
        paths.add(f"{_ABS}/.git")
    return FakeSystem(
        paths=paths,
        commands={
            ("git", "-C", _ABS, "rev-parse", "HEAD"): CommandResult(0, head + "\n", ""),
            ("git", "-C", _ABS, "status", "--porcelain", "--untracked-files=no"):
                CommandResult(0, porcelain, ""),
        },
    )


def test_source_missing():
    fake = FakeSystem()  # path not present
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.MISSING


def test_source_not_a_repo():
    fake = _fake(_PIN, repo=False)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.NOT_A_REPO


def test_source_pin_match():
    fake = _fake(_PIN)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.MATCH and p.head == _PIN


def test_source_pin_differs():
    fake = _fake(_OTHER)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.DIFFERS


def test_source_dirty_overrides_pin():
    fake = _fake(_PIN, porcelain=" M file.c\n")
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.DIRTY


def test_source_unknown_on_git_error():
    fake = FakeSystem(
        paths={_ABS, f"{_ABS}/.git"},
        commands={("git", "-C", _ABS, "rev-parse", "HEAD"): CommandResult(128, "", "fatal")},
    )
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.UNKNOWN

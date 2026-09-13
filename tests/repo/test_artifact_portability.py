"""A published artifact must be installable by controllers OLDER than itself.

Learned twice in one evening, the second time on the live index. Publish roots come from the
INSTALLED manifest, never from the artifact, so a candidate that widens its own roots proves
nothing: the artifact installed perfectly on that candidate and was refused on every released box,
which also killed an image build. The lane was green throughout, because the lane runs the
candidate.

So the rule is checked against what RELEASED controllers actually declare, read from their tags.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tomllib

import pytest

import repo_paths
from lhpc.core.manifest import load_manifest

REPO = repo_paths.REPO

# Every stack whose artifact carries recorded build inputs: the members this guard checks.
# Derived from the shipped manifest so a new binary-channel stack joins the guard by itself.
BINARY_STACKS_WITH_INPUTS = sorted(
    s.id for s in load_manifest() if s.binary and any(c.build_inputs for c in s.components))


def _released_tags(limit: int = 3) -> list:
    out = subprocess.run(["git", "-C", str(REPO), "tag", "--sort=-v:refname"],
                         capture_output=True, text=True, check=False).stdout.split()
    return [t for t in out if t.startswith("v")][:limit]


def _roots_at(tag: str, stack: str) -> list:
    blob = subprocess.run(["git", "-C", str(REPO), "show",
                           f"{tag}:lhpc/data/manifest.example.toml"],
                          capture_output=True, text=True, check=True).stdout
    doc = tomllib.loads(blob)
    return next(s["binary"]["publish_roots"] for s in doc["stack"] if s["id"] == stack)


def _members_this_release_adds(stack: str) -> list:
    """Files this controller writes into an artifact that older ones may not know about.

    Asked of the PRODUCTION function, never recomputed here. A test that derives the path itself
    keeps passing when the path moves, which is exactly the mistake that shipped.
    """
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    root = "/ARTIFACT-ROOT"
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
    out = []
    for st in svc.stacks():
        if st.id != stack:
            continue
        for c in st.components:
            if c.build_inputs:
                out.append(str(svc.build_inputs_path(c)).removeprefix(root + "/"))
    return out


def _inside(member: str, roots: list) -> bool:
    return any(member == r or member.startswith(r + "/") for r in roots)


@pytest.fixture
def released_tags():
    """The newest released tags, or a skip — and in CI a FAILURE. A shallow checkout has no
    tags to compare against, which is a fine reason to skip on a workstation and NO reason at
    all in CI: this guard exists because the released roots are the only ones that matter, and
    a required check that silently skips is not a check. CI fetches the tags; if they are
    missing there, the fetch broke and this must say so."""
    tags = _released_tags()
    if not tags:
        if os.environ.get("CI"):
            pytest.fail("no release tags in this checkout — CI must fetch them for this guard "
                        "(actions/checkout needs fetch-depth: 0), not skip it")
        pytest.skip("no release tags in this checkout")
    return tags


def test_the_guard_covers_a_stack():
    assert BINARY_STACKS_WITH_INPUTS, "no binary stack records build inputs — the guard checks nothing"


@pytest.mark.parametrize("stack", BINARY_STACKS_WITH_INPUTS)
def test_every_member_this_release_adds_is_inside_older_publish_roots(stack, released_tags, capsys):
    tags = released_tags
    print(f"comparing against released publish roots at: {', '.join(tags)}")
    members = _members_this_release_adds(stack)
    assert members, f"{stack} records no build inputs — this guard has stopped checking anything"
    for tag in tags:
        roots = _roots_at(tag, stack)
        for member in members:
            assert _inside(member, roots), (
                f"{member!r} is outside {tag}'s publish roots {roots} — an artifact carrying it "
                f"would be REFUSED on every box running {tag}")


@pytest.mark.parametrize("stack", BINARY_STACKS_WITH_INPUTS)
def test_a_member_outside_those_roots_is_caught(stack, released_tags):
    """The guard's own teeth. A member one directory above a publish root is exactly the shape
    that shipped and was refused on every released box; if this ever passes, the comparison
    above has stopped comparing."""
    tags = released_tags
    roots = _roots_at(tags[0], stack)
    assert roots, f"{tags[0]} declares no publish roots for {stack}"
    outside = str(pathlib.PurePosixPath(roots[0]).parent / ".lhpc-build-inputs")
    assert not _inside(outside, roots), (
        f"{outside!r} was accepted as inside {roots} — the containment check is not checking")

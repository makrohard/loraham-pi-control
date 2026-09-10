"""A published artifact must be installable by controllers OLDER than itself.

Learned twice in one evening, the second time on the live index. Publish roots come from the
INSTALLED manifest, never from the artifact, so a candidate that widens its own roots proves
nothing: the artifact installed perfectly on that candidate and was refused on every released box,
which also killed an image build. The lane was green throughout, because the lane runs the
candidate.

So the rule is checked against what RELEASED controllers actually declare, read from their tags.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


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


@pytest.mark.parametrize("stack", ["meshtastic"])
def test_every_member_this_release_adds_is_inside_older_publish_roots(stack):
    tags = _released_tags()
    if not tags:                      # a shallow checkout has no tags to compare against
        pytest.skip("no release tags in this checkout")
    members = _members_this_release_adds(stack)
    assert members, f"{stack} records no build inputs — this guard has stopped checking anything"
    for tag in tags:
        roots = _roots_at(tag, stack)
        for member in members:
            assert any(member == r or member.startswith(r + "/") for r in roots), (
                f"{member!r} is outside {tag}'s publish roots {roots} — an artifact carrying it "
                f"would be REFUSED on every box running {tag}")

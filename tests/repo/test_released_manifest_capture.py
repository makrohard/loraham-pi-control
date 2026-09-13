"""The manifest of a RELEASED controller must come back whole and parseable through the real
runner: the self-update config migration reads the pre-update manifest with
`git show <from_head>:lhpc/data/manifest.example.toml` to learn a parameter's OLD default.

Both properties are about this repository's own history and the runner's capture cap, so they
live with the other repository invariants (the released-tag dependence is the one
`test_artifact_portability.py` has).
"""
from __future__ import annotations

import os
import re
import subprocess
import tomllib

import pytest

import repo_paths

REL = "lhpc/data/manifest.example.toml"


def _released_tags() -> list:
    """Every release tag, newest first — each one is a valid upgrade source."""
    out = subprocess.run(["git", "-C", str(repo_paths.REPO), "tag", "--sort=-v:refname"],
                         capture_output=True, text=True, check=False).stdout.split()
    return [t for t in out if t.startswith("v")]


def _param_keys(text: str) -> set:
    """The bare TOML keys a manifest writes (`key = …` at any indent) — the vocabulary a release
    used, compared across releases below."""
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", text, re.M))


def test_current_manifest_survives_the_runner_capture_cap():
    """`git show HEAD:manifest` must come back WHOLE through the real runner.

    The runner keeps only the last _MAX_CAPTURE_BYTES of a stream — right for a build log, fatal
    for output that is DATA. Once the manifest outgrew the cap, git show returned a beheaded
    file, TOML parsing failed at line 1, `_prove_candidate` swallowed it as "unprovable", and
    config-default migrations silently stopped completing while self-update deferred.

    The size guard names the private cap so the message can say what to raise; the behavioural
    half below is the real runner returning the whole file.
    """
    from lhpc.core import manifest as manifest_mod
    from lhpc.core.probes import RealSystem
    from lhpc.core.probes.backends import _MAX_CAPTURE_BYTES

    repo = repo_paths.REPO
    size = (repo / REL).stat().st_size
    assert size < _MAX_CAPTURE_BYTES, (
        f"{REL} is {size} bytes, at/over the {_MAX_CAPTURE_BYTES}-byte runner capture cap — "
        f"`git show` would return it beheaded and every config-default migration would stop "
        f"completing. Raise _MAX_CAPTURE_BYTES.")

    r = RealSystem().runner.run(["git", "-C", str(repo), "show", f"HEAD:{REL}"], timeout=20.0)
    assert r.returncode == 0, r.stderr[:200]
    # Truncation keeps the TAIL, so the tell is a missing HEAD: compare the first line, not a
    # length (len() counts characters after UTF-8 decode, and this file is full of em dashes).
    first_line = (repo / REL).read_text(encoding="utf-8").split("\n", 1)[0]
    assert r.stdout.startswith(first_line), (
        f"output was beheaded by the capture cap — starts {r.stdout[:40]!r}")
    stacks = manifest_mod.parse_manifest(tomllib.loads(r.stdout))      # the step that used to fail
    assert {s.id for s in stacks} >= {"daemon", "kiss", "graywolf"}


def test_released_manifests_still_parse():
    """The config-default migration parses the manifest of the release being upgraded FROM
    (`service_params`: `git show <from_head>:...`). Any released tag is a valid upgrade source,
    so EVERY one is read the way the migration reads them: a key an older release wrote must
    stay accepted even when nothing reads it any more. The last assertion proves the claim is
    exercised — some historical manifest carries a key today's manifest no longer writes — so
    dropping such a key's acceptance from the parser fails here, not on an operator's upgrade.
    Three early manifests are refused by today's fail-closed parser (a removed `source.strategy`
    field, a component key misplaced into a param table): the migration treats such a source as
    unprovable and defers, so those three are pinned as the known exceptions — a fourth is a
    regression."""
    from lhpc.core import manifest as manifest_mod
    from lhpc.core.probes import RealSystem

    tags = _released_tags()
    if not tags:
        # CI fetches the full history and tags for exactly this kind of guard; a missing tag
        # there is a broken fetch, not a reason to skip (same rule as test_artifact_portability).
        if os.environ.get("CI"):
            pytest.fail("no release tags in this checkout — CI must fetch them for this guard "
                        "(actions/checkout needs fetch-depth: 0), not skip it")
        pytest.skip("no release tags in this checkout")
    historical, refused = set(), {}
    for tag in tags:
        r = RealSystem().runner.run(["git", "-C", str(repo_paths.REPO), "show", f"{tag}:{REL}"],
                                    timeout=20.0)
        assert r.returncode == 0, (tag, r.stderr[:200])
        try:
            stacks = manifest_mod.parse_manifest(tomllib.loads(r.stdout))
        except manifest_mod.ManifestError as exc:
            refused[tag] = str(exc)
            continue
        assert "daemon" in {s.id for s in stacks}, tag      # the one stack every release has had
        historical |= _param_keys(r.stdout)
    assert set(refused) <= {"v0.1.2", "v0.1.3", "v0.1.4"}, refused
    current = _param_keys((repo_paths.REPO / REL).read_text())
    assert historical - current, "no release tag carries a key today's manifest lacks: the guard proves nothing"

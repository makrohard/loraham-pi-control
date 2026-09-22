"""No test may assert a manifest pin by literal.

The release bot moves pins on its own (`lhpc-release-bot`, policy.toml), and it proves a candidate
with THIS suite. A test that compares a pinned commit or tag against a literal turns that
candidate red the moment the bot does its job: on 2026-09-22 the first 0.8.2 candidate failed one
test out of 5174 — an equality on meshcore-cli's commit — with every moved stack otherwise proven,
and nothing was released. A test about a pin states a CONTRACT (a lower bound, a tag shape, a
guard step in the recipe), never the value the manifest happens to hold today.

Scans every test file for the manifest's current `pin_commit` values (full or a 12+ character
prefix — the 7-character short forms appear legitimately inside upstream tag names such as
`v2.7.26.54e0d8d`) and for a current `pin_tag` on a line that talks about `pin_tag`/`pin_commit`.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

import repo_paths

TESTS = pathlib.Path(repo_paths.REPO) / "tests"
MANIFEST = pathlib.Path(repo_paths.REPO) / "lhpc" / "data" / "manifest.example.toml"


def _pins() -> list[tuple[str, str, str]]:
    """(path, pin_commit, pin_tag) for every pinned source in the manifest."""
    out: list[tuple[str, str, str]] = []

    def walk(o):
        if isinstance(o, dict):
            if "pin_commit" in o and "path" in o:
                out.append((o["path"], o["pin_commit"], str(o.get("pin_tag", ""))))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(tomllib.loads(MANIFEST.read_text()))
    return out


def test_no_test_hardcodes_a_manifest_pin():
    pins = _pins()
    assert len(pins) >= 10, "manifest parse returned too few pinned sources"
    me = pathlib.Path(__file__).resolve()
    hits = []
    for f in sorted(TESTS.rglob("*.py")):
        if f.resolve() == me:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            for path, sha, tag in pins:
                if re.search(r"\b" + re.escape(sha[:12]) + r"[0-9a-f]*\b", line):
                    hits.append(f"{f.relative_to(TESTS.parent)}:{n}: commit of {path}: {line.strip()[:100]}")
                elif tag and re.search(r"pin_tag|pin_commit", line) and re.search(
                        r"[\"']" + re.escape(tag) + r"[\"']", line):
                    hits.append(f"{f.relative_to(TESTS.parent)}:{n}: tag of {path}: {line.strip()[:100]}")
    assert not hits, (
        "these tests assert a manifest pin by literal; the release bot moves pins, so assert the "
        "contract (lower bound, tag shape, recipe step) instead:\n  " + "\n  ".join(hits))

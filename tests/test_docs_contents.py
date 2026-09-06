"""Every documentation file with two or more `##` sections carries a `## Contents` block that
lists exactly those sections, once each, in order, with GitHub-style anchors; the docs index
links every file under docs/ exactly once and nothing that does not exist."""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_DOC_FILES = sorted(
    [*(_REPO / "docs").glob("*.md"), *(_REPO / "docs" / "stacks").glob("*.md"),
     _REPO / "README.md", _REPO / "README.de.md",
     _REPO / "testlab" / "README.md", _REPO / "tests" / "README.md", _REPO / "demo" / "README.md"])
_ENTRY = re.compile(r"^- \[(?P<text>[^\]]+)\]\(#(?P<anchor>[^)]+)\)\s*$")


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lower-case, punctuation dropped, spaces to hyphens."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _sections(text: str) -> list[str]:
    """The `##` headings outside fenced code blocks, in order (the Contents heading excluded)."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("## ") and line[3:].strip() != "Contents":
            out.append(line[3:].strip())
    return out


def _contents_entries(text: str) -> list[tuple[str, str]] | None:
    """(text, anchor) per bullet of the `## Contents` block, or None when there is no block."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Contents")
    except StopIteration:
        return None
    entries = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        m = _ENTRY.match(ln)
        if m:
            entries.append((m.group("text"), m.group("anchor")))
    return entries


@pytest.mark.parametrize("path", _DOC_FILES, ids=lambda p: str(p.relative_to(_REPO)))
def test_contents_block_matches_the_sections(path):
    text = path.read_text(encoding="utf-8")
    sections = _sections(text)
    entries = _contents_entries(text)
    if len(sections) < 2:
        assert entries is None, f"{path.name}: a Contents block needs at least two sections"
        return
    assert entries is not None, f"{path.name}: {len(sections)} sections but no '## Contents'"
    assert [t for t, _ in entries] == sections, (
        f"{path.name}: Contents entries {[t for t, _ in entries]} != sections {sections}")
    for text_, anchor in entries:
        assert anchor == _slug(text_), f"{path.name}: anchor #{anchor} != #{_slug(text_)}"


def test_docs_index_links_every_doc_once():
    index = (_REPO / "docs" / "README.md").read_text(encoding="utf-8")
    linked = re.findall(r"\]\(((?:stacks/)?[\w.-]+\.md)(?:#[^)]*)?\)", index)
    expected = sorted(str(p.relative_to(_REPO / "docs"))
                      for p in [*(_REPO / "docs").glob("*.md"), *(_REPO / "docs" / "stacks").glob("*.md")]
                      if p.name != "README.md")
    assert sorted(set(linked)) == expected, (
        f"index links {sorted(set(linked))} but docs/ holds {expected}")
    dup = {x for x in linked if linked.count(x) > 1}
    assert not dup, f"linked more than once: {dup}"

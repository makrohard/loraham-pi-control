"""The invariant table in docs/architecture.md names each owner as `path` `symbol` (continued by
`, `symbol`` in the same file). Each named symbol must still be defined in its file, so a rename
or a removal fails here instead of leaving the doc pointing at nothing.

A documentation-integrity test (tests/README.md, rule 1's second exception): it reads the
production files only to find each name's definition, never behaviour or source text.
"""

import re

import repo_paths

ROOT = repo_paths.REPO

_POINTER = re.compile(r"`((?:lhpc|testlab)/[\w/]+\.py)` `(\w+)`((?:, `\w+`)*)")


def _table_rows():
    text = (ROOT / "docs" / "architecture.md").read_text()
    return [row for row in text.splitlines() if row.startswith("|")]


def _pointers():
    for row in _table_rows():
        for m in _POINTER.finditer(row):
            yield m.group(1), m.group(2)
            for name in re.findall(r"`(\w+)`", m.group(3)):
                yield m.group(1), name


def test_every_named_owner_is_defined_in_its_file():
    pointers = list(_pointers())
    assert pointers
    missing = []
    for path, name in pointers:
        src = ROOT / path
        defined = rf"^(\s*(async\s+)?(def|class)\s+{name}\b|{name}\s*[:=])"
        if not (src.is_file() and re.search(defined, src.read_text(), re.MULTILINE)):
            missing.append(f"{path} `{name}`")
    assert not missing, f"docs/architecture.md names owners not defined there: {', '.join(missing)}"


def test_owner_pointers_carry_no_line_numbers():
    numbered = [row[:60] for row in _table_rows() if re.search(r"\.py:\d+`|`:\d+`", row)]
    assert not numbered, f"a `path:N` pointer drifts and escapes the check above: {numbered}"

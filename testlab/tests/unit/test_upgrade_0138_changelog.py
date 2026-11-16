"""The "Upgrading from 0.13.x" self-update step has ONE source: the slow-build lane runs lhpc_testlab.upgrade_0138
as written, and the CHANGELOG must give exactly those lines (each in backticks, in that order) and the lock text."""
from __future__ import annotations

import subprocess
from pathlib import Path

from lhpc_testlab import upgrade_0138

ROOT = Path(__file__).resolve().parents[3]


def _section() -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    start = text.index("Upgrading from 0.13.x")
    end = text.find("\n## ", start)
    return text[start:end if end > 0 else len(text)]


def test_the_changelog_gives_the_tested_0138_step_line_by_line():
    sec = _section()
    at = [sec.find(f"`{cmd}`") for cmd in upgrade_0138.STEPS]
    assert -1 not in at and at == sorted(at), dict(zip(upgrade_0138.STEPS, at, strict=True))
    assert upgrade_0138.LOCK[0] in sec and f"`{upgrade_0138.LOCK[1]}`" in sec
    assert sec.index(upgrade_0138.LOCK[0]) > at[1]                 # the lock line follows the reset it is keyed to


def test_no_tracked_path_is_ignored_so_the_reset_never_writes_over_an_ignored_file():
    """`git status --short` (the step's first line) does not list ignored files; the step's reset can replace one only
    at a tracked path that .gitignore matches. There is none, so status shows everything the reset replaces."""
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-ci", "--exclude-standard"], capture_output=True,
                       text=True, check=True, timeout=60)
    assert r.stdout == "", r.stdout

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


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True,
                          text=True, check=True, timeout=60).stdout


def test_the_steps_first_line_names_the_ignored_files_the_reset_replaces(tmp_path):
    """The reset writes over ignored files the operator's .gitignore cannot vouch for: one excluded in
    .git/info/exclude at a path the new commit adds, and an untracked directory of ignored files where it adds a file.
    The step's first line, run as written, names both before the reset replaces them."""
    up, co = tmp_path / "up", tmp_path / "co"
    _git(tmp_path, "init", "-q", "-b", "main", str(up))
    (up / "a").write_text("a\n")
    _git(up, "add", "a")
    _git(up, "commit", "-q", "-m", "one")
    _git(tmp_path, "clone", "-q", str(up), str(co))
    (up / "tool.sh").write_text("upstream\n")
    (up / "cache").write_text("upstream\n")
    _git(up, "add", "tool.sh", "cache")
    _git(up, "commit", "-q", "-m", "two")
    _git(co, "fetch", "-q")
    (co / ".git" / "info" / "exclude").write_text("tool.sh\n*.pyc\n")
    (co / "tool.sh").write_text("mine\n")
    (co / "cache").mkdir()
    (co / "cache" / "x.pyc").write_text("mine\n")
    shown = subprocess.run(["sh", "-c", upgrade_0138.STEPS[0]], cwd=co, capture_output=True, text=True, check=True,
                           timeout=60).stdout.splitlines()
    assert "!! tool.sh" in shown and any(ln.startswith("!! cache") for ln in shown), shown
    subprocess.run(["sh", "-c", upgrade_0138.STEPS[1]], cwd=co, capture_output=True, text=True, check=True, timeout=60)
    assert (co / "tool.sh").read_text() == "upstream\n" and (co / "cache").is_file()   # the case is real: both replaced

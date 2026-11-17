"""The "Upgrading from 0.13.x" self-update step has ONE source: the slow-build lane runs lhpc_testlab.upgrade_0138
as written, and the CHANGELOG's delimited block names exactly those commands (in backticks, in that order, nothing
else) and the lock text."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from lhpc_testlab import upgrade_0138

ROOT = Path(__file__).resolve().parents[3]


def _block() -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    start = text.index("<!-- upgrade-0138:")
    return text[start:text.index("<!-- /upgrade-0138 -->", start)]


def test_the_changelog_block_names_exactly_the_tested_0138_commands():
    blk = _block()
    assert re.findall(r"`([^`]+)`", blk) == list(upgrade_0138.PROCEDURE), blk   # an added command fails it
    assert upgrade_0138.LOCK[0] in blk and upgrade_0138.LOCK_SAFE in blk, blk     # the lock line keeps its precondition


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
    subprocess.run(["sh", "-c", upgrade_0138.STEPS[2]], cwd=co, capture_output=True, text=True, check=True, timeout=60)
    assert (co / "tool.sh").read_text() == "upstream\n" and (co / "cache").is_file()   # the case is real: both replaced


def test_the_step_names_the_local_commits_the_reset_abandons(tmp_path):
    """A checkout with a commit of the operator's on top: status shows a clean tree, the log line names the commit,
    and the reset, run as written, leaves it on no branch (the step says to keep it first)."""
    up, co = tmp_path / "up", tmp_path / "co"
    _git(tmp_path, "init", "-q", "-b", "main", str(up))
    (up / "a").write_text("a\n")
    _git(up, "add", "a")
    _git(up, "commit", "-q", "-m", "one")
    _git(tmp_path, "clone", "-q", str(up), str(co))
    (co / "mine").write_text("mine\n")
    _git(co, "add", "mine")
    _git(co, "commit", "-q", "-m", "my local work")
    def run(line):
        return subprocess.run(["sh", "-c", line], cwd=co, capture_output=True, text=True, check=True, timeout=60).stdout
    assert run(upgrade_0138.STEPS[0]) == "" and "my local work" in run(upgrade_0138.STEPS[1])
    run(upgrade_0138.STEPS[2])
    assert "my local work" not in _git(co, "log", "--oneline")                  # the case is real: abandoned


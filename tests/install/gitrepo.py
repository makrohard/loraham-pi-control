"""A throwaway git world for the self-update tests: a bare `origin`, a working clone that
`selfupdate.repo_root()` is pointed at, and an `up` clone used to advance `origin/main`.

REAL git in temp directories, never the developer's checkout and never the network. It lives in
a plain module rather than in a test file, so all three self-update suites share one rig without
importing one another; `tests/install/` is on `sys.path` for its own tests.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from lhpc.core import selfupdate
from lhpc.core.paths import Paths

ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent",
}


# The binary is resolved ONCE from the developer's PATH; the child then runs under the hermetic
# environment above, whose PATH is deliberately minimal (nothing of the host's toolchain leaks in).
GIT = shutil.which("git")
assert GIT, "the install suites drive real git in temp directories; `git` must be on PATH"


def git(cwd, *args, env=None):
    """`git(cwd, *args) -> stdout.strip()`; a non-zero exit fails the test naming the command.
    `env` adds to the hermetic environment (a committer date, say) without replacing it."""
    r = subprocess.run([GIT, *args], cwd=str(cwd), env={**ENV, "PATH": "/usr/bin:/bin", **(env or {})},
                       capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def seed(repo: Path, version: str):
    (repo / "lhpc").mkdir(parents=True, exist_ok=True)
    (repo / "lhpc" / "version.py").write_text(f'__version__ = "{version}"\n')
    (repo / "pyproject.toml").write_text('[project]\nname="x"\nversion="0.0.0"\ndependencies=[]\n')
    (repo / ".gitignore").write_text(".venv/\nvenv/\n")   # ignored runtime artifacts (like a real repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "seed")
    git(repo, "push", "-u", "origin", "main")


def repos(tmp: Path):
    """(origin, work, up): a bare origin, a working clone (monkeypatch target), and an upstream
    clone for advancing origin/main."""
    origin, work, up = tmp / "origin.git", tmp / "work", tmp / "up"
    git(tmp, "init", "--bare", "-b", "main", str(origin))
    git(tmp, "clone", str(origin), str(work))
    git(work, "checkout", "-b", "main")
    seed(work, selfupdate.__version__)
    git(tmp, "clone", str(origin), str(up))
    return origin, work, up


def upstream_commit(up: Path, *, version: str | None = None, touch_pyproject: bool = False):
    if version is not None:
        (up / "lhpc" / "version.py").write_text(f'__version__ = "{version}"\n')
    if touch_pyproject:
        (up / "pyproject.toml").write_text(
            '[project]\nname="x"\nversion="0.0.0"\ndependencies=["flask"]\n')
    (up / "note.txt").write_text("upstream change\n")
    git(up, "add", "-A")
    git(up, "commit", "-m", "upstream")
    git(up, "push", "origin", "main")
    return git(up, "rev-parse", "HEAD")


def runtime_paths(tmp: Path) -> Paths:
    (tmp / "rt" / "state").mkdir(parents=True, exist_ok=True)
    return Paths(runtime_root=tmp / "rt")

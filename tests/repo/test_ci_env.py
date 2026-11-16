"""`ci_env.under_ci` through the gate it feeds: a `needs_git_checkout` test in a source export (no `.git`) skips in a
local run, CI=0 and CI=False included, and fails at setup under CI (CI=1, CI=TRUE). The export is the suite's own
conftest and the files it imports, run in a child pytest."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest
import repo_paths

_EXPORT = ("pyproject.toml", "tests/conftest.py", "tests/repo_paths.py", "tests/band_socket_guard.py", "tests/ci_env.py")


@pytest.mark.parametrize("ci, outcome", [(None, "skipped"), ("0", "skipped"), ("False", "skipped"),
                                         ("1", "error"), ("TRUE", "error")])
def test_a_needs_git_checkout_test_in_a_source_export(tmp_path, ci, outcome):
    root = tmp_path / "export"
    for rel in _EXPORT:
        if (repo_paths.REPO / rel).exists():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(repo_paths.REPO / rel, root / rel)
    (root / "tests" / "test_needs_git.py").write_text(
        "import pytest\n\n\n@pytest.mark.needs_git_checkout\ndef test_reads_the_history():\n    pass\n")
    env = {k: v for k, v in os.environ.items() if k != "CI"} | ({} if ci is None else {"CI": ci})
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_needs_git.py",
                        f"--basetemp={tmp_path / 'bt'}"], cwd=root, env=env, capture_output=True, text=True,
                       timeout=120, check=False)
    assert f"1 {outcome}" in r.stdout, r.stdout[-2000:]
    assert ("CI must run from a git checkout" in r.stdout) == (outcome == "error"), r.stdout[-2000:]

"""The builder and the lane must answer the attribution question identically.

The binary builder cannot import the test lab, so it runs `tools/build_regression.py` against the
controller it was told to build. If that tool and the lane ever disagreed, a pin could be frozen
by one path and not the other — so both go through `lhpc.core.build_regression`, and this drives
the tool as the builder actually drives it: a subprocess, over a captured output file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import repo_paths

TOOL = repo_paths.REPO / "tools" / "build_regression.py"


def _run(stack: str, captured: str, tmp_path: Path):
    f = tmp_path / "out.txt"
    f.write_text(captured)
    r = subprocess.run([sys.executable, str(TOOL), stack, str(f)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _owned_log(stack_id: str) -> str:
    from lhpc.core.build_regression import own_step_logs
    from lhpc.core.manifest import load_manifest
    stack = next(s for s in load_manifest() if s.id == stack_id)      # the shipped manifest
    return sorted(own_step_logs(stack))[0]


def test_a_step_the_recipe_declares_its_own_attributes(tmp_path):
    log = _owned_log("meshcore")
    out = _run("meshcore", f"  [failed] build x (rc 1, log /l/{log})", tmp_path)
    assert out == "STACK-REGRESSION stack=meshcore phase=build"


@pytest.mark.parametrize("captured,why", [
    ("  [failed] build meshcore-node (rc 1, log /l/build-meshcore-node-1.log)",
     "a networked pip step is not the recipe's own"),
    ("  [timeout] build meshcore-node (rc 124, log /l/build-meshcore-node-0.log)",
     "a runner that ran out of time says nothing about upstream"),
    ("ERR   Refusing to build 'meshcore': not installed.",
     "a refusal executed no step at all"),
    ("  [failed] build meshcore-node (rc 1, log )",
     "every command succeeded and a local write failed"),
    ("", "no output at all"),
])
def test_everything_else_attributes_nothing(captured, why, tmp_path):
    assert _run("meshcore", captured, tmp_path) == "", why

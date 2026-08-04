"""Properties of the SUITE itself, so a test can only pass here for the reason it passes in CI.

The local lane runs `python -m pytest`, and `-m` puts the working directory on `sys.path`, so the
repo root is importable and `tests` resolves as a namespace package. CI runs the `pytest` console
script, which does not — the editable install is no help either, since its finder exposes only
`lhpc` (verified: `import tests` fails from any other directory). A module that reaches outside
the import surface CI actually has therefore collects fine here and dies there — which is what
happened to 0.1.8: green on both boxes, `ModuleNotFoundError: No module named 'tests'` in CI.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
MODULES = sorted(p for p in TESTS_DIR.glob("*.py") if p.name != "conftest.py")


def _imported_roots(tree):
    """Every top-level module name the file imports, from both import forms."""
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_test_module_imports_another_test_module(path):
    """Shared helpers belong in `conftest.py` (a fixture), not in a sibling test module.

    `from tests.test_gps import ...` needs the repo root importable; `from test_gps import ...`
    needs the tests directory importable and silently re-imports a module pytest also collects.
    Neither is guaranteed, and the failure mode is a collection error in CI only.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = {r for r in _imported_roots(tree) if r == "tests" or r.startswith("test_")}
    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}. Move the shared helper into tests/conftest.py "
        f"and take it as a fixture — see fake_gpsd.")

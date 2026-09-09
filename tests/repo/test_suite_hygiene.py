"""Properties of the SUITE itself, so a test can only pass here for the reason it passes in CI.

The local lane runs `python -m pytest`, and `-m` puts the working directory on `sys.path`, so the
repo root is importable and `tests` resolves as a namespace package. CI runs the `pytest` console
script, which does not — the editable install is no help either, since its finder exposes only
`lhpc` (verified: `import tests` fails from any other directory). A module that reaches outside
the import surface CI actually has therefore collects fine here and dies there — which is what
happened once: green on both boxes, `ModuleNotFoundError: No module named 'tests'` in CI.
"""
from __future__ import annotations

import ast

import pytest

import repo_paths

TESTS_DIR = repo_paths.TESTS
# EVERY module in the suite, at any depth — the tests live in per-owner subdirectories, so a
# non-recursive glob would quietly check nothing and still report green.
MODULES = sorted(p for p in TESTS_DIR.rglob("*.py")
                 if p.name != "conftest.py" and "__pycache__" not in p.parts)


def _imported_roots(tree):
    """Every top-level module name the file imports, from both import forms."""
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(TESTS_DIR)))
def test_no_test_module_imports_another_test_module(path):
    """Shared helpers belong in `conftest.py` (a fixture), not in a sibling test module.

    `from tests.test_gps import ...` needs the repo root importable; `from test_gps import ...`
    needs the tests directory importable and silently re-imports a module pytest also collects.
    Neither is guaranteed, and the failure mode is a collection error in CI only.

    The two plain helper modules at the root of `tests/` (`repo_paths`, `htmlq`) are NOT test
    modules and are fine to import: pytest puts `tests/` on `sys.path` for its conftest, so they
    resolve from every subdirectory under both lanes.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = {r for r in _imported_roots(tree) if r == "tests" or r.startswith("test_")}
    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}. Move the shared helper into tests/conftest.py "
        f"and take it as a fixture — see fake_gpsd.")


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(TESTS_DIR)))
def test_no_test_module_needs_a_browser(path):
    """`tests/` must run on a machine with no browser at all.

    Chromium belongs to the testlab browser lane, which is opt-in and skips with a reason when
    the browser is missing. A single import here would make the ordinary suite — the one that
    runs on a Pi and on every developer box — uncollectable without it.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    browsers = {r for r in _imported_roots(tree) if r in {"playwright", "selenium", "pyppeteer"}}
    assert not browsers, (
        f"{path.name} imports {sorted(browsers)}. Browser tests live in testlab/tests/browser, "
        f"which is gated on LHPC_BROWSER=1 and a usable Chromium.")

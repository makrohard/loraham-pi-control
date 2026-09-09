"""Where the checkout and the suite's own data files are.

Found by walking UP to the directory that holds `pyproject.toml`, never by counting
`parents[...]`: a fixed count is a silent trap — it breaks every test that reads a shipped
file the moment the suite grows a directory level, and it fails as a missing file rather
than as anything a maintainer would connect to the move.

`tests/` is on `sys.path` (pytest puts each conftest's directory there), so a test in any
subdirectory can `import repo_paths` without importing another test module.
"""
from __future__ import annotations

import pathlib

REPO = next(p for p in pathlib.Path(__file__).resolve().parents
            if (p / "pyproject.toml").is_file())
TESTS = REPO / "tests"
DATA = TESTS / "data"                 # recorded real-world payloads (nft dumps, manifests)
FIXTURES = TESTS / "fixtures"         # rendered pages and other captured artefacts

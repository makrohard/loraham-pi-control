"""`lhpc/version.py` is the single source of truth, but `pyproject.toml` carries
its own copy — and selfupdate compares version.py while pip reads pyproject.
Letting them drift means a released bump is invisible to `lhpc selfupdate`.
"""

import re
import tomllib
from pathlib import Path

from lhpc.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_matches_version_module():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__


def test_version_is_a_plain_release_triple():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__

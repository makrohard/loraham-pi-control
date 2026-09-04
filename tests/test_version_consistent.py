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


def test_changelog_leads_with_the_current_version():
    # A bump that forgets (or misnumbers) its CHANGELOG section stayed green: selfupdate would
    # announce a version with no entry. The first `## X.Y.Z` heading must be this release.
    text = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(r"^## (\d+\.\d+\.\d+)", text, re.M)
    assert m and m.group(1) == __version__, (m.group(1) if m else None, __version__)

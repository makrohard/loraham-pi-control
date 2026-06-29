"""Locate packaged data assets (the tracked TOML manifest, defaults, profile
catalogue and config templates) so they resolve identically from a source checkout
and from an installed wheel.

These files live under ``lhpc/data/`` and are shipped as package data, loaded via
``importlib.resources`` — never via ``Path(__file__).parents[...]`` to an assumed
repository root (which does not exist once the package is installed).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

_PACKAGE = "lhpc"
_DATA = "data"


def asset_path(name: str) -> Path:
    """Filesystem path to ``lhpc/data/<name>`` (works for the normal unzipped
    install and the source tree). For zipped installs use ``asset_text``."""
    return Path(str(resources.files(_PACKAGE) / _DATA / name))


def asset_text(name: str) -> str:
    """Text of a packaged data file (zip-safe)."""
    return (resources.files(_PACKAGE) / _DATA / name).read_text(encoding="utf-8")

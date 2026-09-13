"""Fixtures for the `lhpc` command's tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def no_venv_sync(monkeypatch):
    """Stub the two post-apply steps of `self-update --apply` that a CLI-wiring test is not
    about. The apply wrapper syncs the venv with a REAL `pip install -e <repo>`; a test must never
    install into the developer's venv (tests/conftest.py::_no_pip_install), so the repo root is
    pointed at None, which skips the sync at its own seam — the sync itself is covered in
    tests/install/test_selfupdate.py. The post-update unit refresh has nothing real to verify
    against a temp runtime root with no checkout; its contract is covered by test_selfupdate.py /
    tests/host/test_updater_units.py against a canonical unit set."""
    from lhpc.core import selfupdate as _su
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(_su, "repo_root", lambda: None)
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (True, "n/a"))

"""Test-lab suite fixtures: register the `covers` marker and make ControllerService pick
up the lab provider (the same env the devcontainer sets) for the whole session."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: slow test (builds/containers)")
    config.addinivalue_line(
        "markers", "covers(*ids): coverage-matrix rows (route:/cli:/form:/stack:) an "
                   "acceptance/browser test proves (AST-scanned by test_coverage_matrix).")


@pytest.fixture(autouse=True)
def _lab_provider(monkeypatch):
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")

"""Test-lab suite fixtures: make ControllerService pick up the lab provider (the same env
the devcontainer sets) for the whole session."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: slow test (builds/containers)")


@pytest.fixture(autouse=True)
def _lab_provider(monkeypatch):
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")

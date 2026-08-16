"""Shared test-lab helpers (non-test module per suite hygiene: test modules must not
import each other, so anything two suites need lives here)."""
from __future__ import annotations

from lhpc.core.paths import Paths


def make_lab_root(tmp_path, monkeypatch) -> Paths:
    """A latched lab root: env key set, marker present, state skeleton created."""
    monkeypatch.setenv("LHPC_TESTLAB", "1")
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "lhpc_testlab.provider:build")
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "testlab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "state" / "testlab" / "enabled").write_text("lab\n")
    return paths

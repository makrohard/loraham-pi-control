"""Fixtures shared by the three self-update suites (advance, migration, updater service)."""
from __future__ import annotations

import pytest

import gitrepo
from lhpc.core import selfupdate
from lhpc.core.probes.backends import RealSystem


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A working clone with an origin to advance, and `selfupdate.repo_root()` pointed at it.

    The code under test drives git through `RealSystem()` — a real sub-process against these
    temp repositories — so the actual git integration is exercised, not a stub of it.
    """
    _origin, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    return {"sys": RealSystem(), "paths": gitrepo.runtime_paths(tmp_path), "work": work, "up": up}


@pytest.fixture()
def op_svc(tmp_path, monkeypatch):
    """A controller on a FakeSystem, standing in for the operator's box during an update.

    Call it with a `{argv-tuple: CommandResult}` table; it returns `(service, fake)` so a test
    can assert on `fake.calls`. The checkout the venv sync would reinstall is a temp directory
    and its `pip install -e` already succeeds, so a test about a LATER step is not tripped by
    that one.
    """
    def build(cmds=None):
        import sys

        from lhpc.core import selfupdate
        from lhpc.core.paths import Paths
        from lhpc.core.probes.backends import CommandResult, FakeSystem
        from lhpc.core.services import ControllerService

        root = tmp_path / "checkout"
        root.mkdir(exist_ok=True)
        monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: root))
        table = {(sys.executable, "-m", "pip", "install", "-e", str(root)): CommandResult(0, "", "")}
        table.update(cmds or {})
        fake = FakeSystem(commands=table)
        return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)), fake
    return build

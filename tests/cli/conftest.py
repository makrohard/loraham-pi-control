"""Fixtures for the `lhpc` command's tests."""

from __future__ import annotations

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, radio_start, radios
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


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


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    """The stack-instance tests' box (item 7), a FakeSystem service over a temporary runtime root:
    box(rows, listed, procs, ports) -> run(*argv) -> (rc, out, launches)."""
    def make(rows, listed, procs=None, ports=()):
        radios_fixture.write(tmp_path, rows)
        assert radios.load(Paths(runtime_root=tmp_path)).kind == radios.VALID
        if listed:
            (tmp_path / "config" / "instances.toml").write_text(listed)
        for name, value in (("hardware_configured", lambda self: True), ("radio_bands", lambda self: ["433", "868"]),
                            ("binary_behind", lambda self, c: ""), ("is_built", lambda self, c: True),
                            ("_source_present", lambda self, c: True), ("_verify_band_up", lambda self, b: True)):
            monkeypatch.setattr(ControllerService, name, value)
        monkeypatch.setattr(radio_start, "verify_identity", lambda svc, rid: "")
        launches = []

        def launch(self, stack, comp, params=None, band="", **kw):
            inst = kw.get("inst") or commands.SLOT0
            launches.append((comp.id, band, inst.slot, inst.rid))
            return StartLaunch(True, "")
        monkeypatch.setattr(Lifecycle, "start", launch)
        system = FakeSystem(unix_replies=radios_fixture.INSTANCE_READY, cmdlines_data=procs or {},
                            listeners=[Listener(family="ipv4", ip="127.0.0.1", port=p, inode=i)
                                       for i, p in enumerate(ports, 1)]).system
        real = ControllerService
        monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: real(system=system, paths=Paths(runtime_root=tmp_path)))

        def run(*argv):
            rc = cli.main(["stack", *argv, "--yes"])
            return rc, capsys.readouterr().out, launches
        return run
    return make

"""LEG-D32 (found by the live leg): the daemon opens `--rflog-path {runtime}/logs/<rid>/rf-daemon.log` at init and
exits when it cannot, so the first start of a radio's daemon creates `logs/<rid>/` first: a real directory under
the contained logs/, never through a link. An unusable logs/<rid> refuses the start; nothing is launched."""
import os

import pytest
import radios_fixture

from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path)                        # r1 on 433, r2 on 868
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "radio_bands", lambda self: ["433", "868"])
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    seen = []

    def launch(self, s, c, *a, **k):
        d = tmp_path / "logs" / "r1"
        seen.append((c.id, d.is_dir() and not d.is_symlink()))
        return StartLaunch(True, "")
    monkeypatch.setattr(Lifecycle, "start", launch)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)), seen


def test_the_first_start_creates_the_radios_rf_log_directory_before_the_launch(tmp_path, monkeypatch):
    svc, seen = _svc(tmp_path, monkeypatch)
    assert not (tmp_path / "logs").exists()                     # a fresh runtime: no logs/ at all
    svc.start("daemon", apply=True, band="433")
    assert seen == [("loraham-daemon", True)]                   # launched, and logs/r1/ was there for it
    assert (tmp_path / "logs" / "r1").is_dir() and not (tmp_path / "logs" / "r1").is_symlink()


@pytest.mark.parametrize("make", [
    lambda root, out: ((root / "logs").mkdir(), (root / "logs" / "r1").write_text("x")),          # a file
    lambda root, out: ((root / "logs").mkdir(), (out / "elsewhere").mkdir(parents=True),
                       (root / "logs" / "r1").symlink_to(out / "elsewhere", target_is_directory=True)),
    lambda root, out: ((out / "elsewhere").mkdir(parents=True),
                       (root / "logs").symlink_to(out / "elsewhere", target_is_directory=True)),  # escaping logs/
    lambda root, out: (root / "logs").symlink_to(out / "nowhere"),                                 # dangling logs/
], ids=["file", "r1-link-out", "logs-link-out", "logs-dangling"])
def test_an_unusable_rf_log_directory_refuses_and_creates_nothing(tmp_path, monkeypatch, make):
    root = tmp_path / "rt"
    root.mkdir()
    out = tmp_path / "out"
    svc, seen = _svc(root, monkeypatch)
    make(root, out)
    def logs_and_outside():                                    # what the claim is about (a start writes its state)
        return sorted([str(p.relative_to(tmp_path)) for p in out.rglob("*")] + [os.readlink(root / "logs")
                      if (root / "logs").is_symlink() else str(p.relative_to(tmp_path)) for p in
                      ([root / "logs"] if (root / "logs").is_symlink() else (root / "logs").rglob("*"))])
    before = logs_and_outside()
    res = svc.start("daemon", apply=True, band="433")
    assert not res.ok and seen == []
    assert any("logs/r1 cannot hold its RF log" in line for line in res.details), res.details
    assert logs_and_outside() == before


def test_a_present_directory_is_used_as_it_is(tmp_path, monkeypatch):
    svc, seen = _svc(tmp_path, monkeypatch)
    (tmp_path / "logs" / "r1").mkdir(parents=True)
    (tmp_path / "logs" / "r1" / "rf-daemon.log").write_text("kept\n")
    svc.start("daemon", apply=True, band="433")
    assert seen == [("loraham-daemon", True)]
    assert (tmp_path / "logs" / "r1" / "rf-daemon.log").read_text() == "kept\n"
    assert oct(os.stat(tmp_path / "logs" / "r1").st_mode)[-3:] in ("755", "775", "700")

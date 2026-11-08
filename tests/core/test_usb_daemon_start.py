"""LEG-D41 (found by the live leg): a USB radio's daemon start passes --hw <the model variant's preset>; the
daemon's hw parameter admits it, so the start reaches the launch (it was refused "invalid configuration: hw:
'meshstick-1262' not in (…)" before any launch)."""
import radios_fixture
import repo_paths

from lhpc.core import radios, usb_access
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Probe

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial


def test_a_usb_radios_daemon_start_reaches_the_launch_with_its_preset(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "loraham", "433"),
                                    radios.Radio("r3", "stick", "meshstick-1262", "868", usb_port="1-1.3")))
    real_scan = usb_access.scan
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: real_scan(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "radio_bands", lambda self: ["433", "868"])
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    spawned = []

    def spawn(self, argv, log, cwd=None, env=None):
        spawned.append(list(argv))
        raise OSError("captured (no process)")
    monkeypatch.setattr(Lifecycle, "_real_spawn", spawn)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("daemon", apply=True, band="868")
    assert spawned, res.details                                   # the launch was reached (then captured)
    argv = spawned[0]
    assert argv[argv.index("--hw") + 1] == "meshstick-1262" and argv[argv.index("--id") + 1] == "r3"
    assert not any("invalid configuration" in line for line in res.details), res.details


def test_a_shared_band_starts_each_radios_daemon_with_its_own_preset(tmp_path, monkeypatch):
    # Item 16 (DEC-W53), replacing PKT-D20 item 5's refusal: the board's r2 and an added stick r3 on 868 each get
    # their own 868 daemon, the board's with its preset and the stick's with its own.
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "loraham", "433"),
                                    radios.Radio("r2", "high", "loraham", "868"),
                                    radios.Radio("r3", "stick", "meshstick-1262", "868", usb_port="1-1.3")))
    real_scan = usb_access.scan
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: real_scan(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "radio_bands", lambda self: ["433", "868"])
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    spawned = []
    monkeypatch.setattr(Lifecycle, "_real_spawn", lambda self, argv, *a, **k: spawned.append(argv))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("daemon", apply=True, band="868")
    assert [(a[a.index("--id") + 1], a[a.index("--hw") + 1]) for a in spawned] == [
        ("r2", "loraham"), ("r3", "meshstick-1262")], res.details
    assert not any("two enabled radios" in line for line in res.details), res.details

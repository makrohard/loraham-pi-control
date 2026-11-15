"""K8f S4-4c (K8F-S4-DESIGN-v4, the radio_start rows + G1-K8F-S4-D3 N3): a radio's daemon launches with ITS daemon
store (daemon@rN's file, its band): two radios on 433 whose daemon stores differ (rf_log) launch each with its own."""
import radios_fixture

from lhpc.core import instances, radios
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_a_radios_daemon_launches_with_its_own_store(tmp_path, monkeypatch):
    # red on 097ba31e: r2's daemon launched with the fallback instance's (r1's) rf_log
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "loraham", "433"), radios.Radio("r2", "b", "waveshare-sx1262", "433")))
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "stacks" / "daemon@r1.toml").write_text('rf_log = "on"\n')
    (tmp_path / "config" / "stacks" / "daemon@r2.toml").write_text('rf_log = "off"\n')
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    seen = []

    def start(self, stack, comp, params=None, band="", **kw):
        seen.append((getattr(kw.get("inst"), "rid", ""), getattr(kw.get("inst"), "band", ""), (params or {}).get("rf_log")))
        return StartLaunch(False, "", "captured (no process)")
    monkeypatch.setattr(Lifecycle, "start", start)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.start("daemon", apply=True, inst=instances.parse_target(svc, "daemon@r2", "start")[1])
    assert seen and seen[0] == ("r2", "433", "off"), seen

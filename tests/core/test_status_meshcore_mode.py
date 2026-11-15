"""K8f S4-4a1 (K8F-S4-DESIGN-v4, D2-CTX B1): `lhpc status` names each MeshCore radio's own saved mode; one radio keeps
today's `mode:` line. Two radios on 868 (r3, r2): r3 saved as a repeater, r2 chat."""
import radios_fixture

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _mode_lines(svc):
    return [d for d in svc.status("meshcore").details if d.startswith("  mode")]


def test_status_names_each_meshcore_radios_mode(tmp_path):
    # red on 99da4eb1: one "  mode: chat" line (the fallback radio's), r3's repeater mode not shown
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r3 = instances.parse_target(svc, "meshcore@r3", "start")[1]
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "RPT3"}, inst=r3).ok
    assert svc.save_config_bundle("meshcore", values={"file_mode": "repeater"}, inst=r3).ok
    assert _mode_lines(svc) == ["  mode r2: chat", "  mode r3: repeater"], _mode_lines(svc)


def test_status_of_one_meshcore_radio_keeps_its_mode_line(tmp_path):
    # the baseline box: meshcore runs on r2 only -> today's line, unchanged (a pin, green before)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert _mode_lines(svc) == ["  mode: chat"]

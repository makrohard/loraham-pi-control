"""K8f S4-2 (K8F-S4-DESIGN-v4, G1-K8F-S4-D2-CTX B1/B2): a MeshCore mode question about one radio is that radio's:
the RF-log decoder of meshcore@r2 runs in r2's RUNNING role while r2 runs (its generated config), else in r2's saved
mode, never another radio's. Two radios on 868 (r3, r2)."""
import pytest
import radios_fixture

from lhpc.core import instances, rflog
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _box(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return svc, {r: instances.parse_target(svc, f"meshcore@{r}", "start")[1] for r in ("r2", "r3")}


def _mode_arg(svc, rid):
    args = rflog.meshcore_decode_args(svc, "meshcore", rid)
    return args[args.index("--mode") + 1]


@pytest.mark.parametrize("live, idle", [("r2", "r3"), ("r3", "r2")])
def test_a_running_nodes_log_is_decoded_in_its_running_role(tmp_path, monkeypatch, live, idle):
    # `live` runs as a repeater (its generated config) while its SAVED mode is chat again; `idle` is stopped.
    # red on a3dce2ea: the live radio's decoder took its saved "chat" (a radio's job never asked whether it runs)
    svc, ins = _box(tmp_path)
    gen = tmp_path / "config" / "files" / live / "meshcore.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    gen.write_text('[repeater]\nrole = "repeater"\n')
    monkeypatch.setattr(instances, "instance_runs", lambda s, comp, inst, band: getattr(inst, "rid", "") == live)
    assert (_mode_arg(svc, live), _mode_arg(svc, idle)) == ("repeater", "chat")

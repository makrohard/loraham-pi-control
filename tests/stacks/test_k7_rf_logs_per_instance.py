"""K7 (MAY out): two meshtastic or meshcom instances never share an RF log. meshtasticd's trace (Logging.TraceFile) is
the instance's own logs/<rid>/rf-meshtastic.log, from the instance's own switch (K6: one per instance); the viewer and
Clear take every instance's <rid>/rf-<x>.log (before: rf-daemon, rf-kiss, rf-meshcore only)."""
import pytest
import radios_fixture

from lhpc.core import rflog
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_868 = (radios_fixture.R3, radios_fixture.R2_868)        # r3 and r2, both 868: two meshtastic instances


def _trace(svc, tmp_path, inst):
    svc.write_config_files("meshtastic", "868", inst=inst)
    text = (tmp_path / "config" / "files" / inst.rid / "meshtasticd.yaml").read_text()
    return next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.strip().startswith("TraceFile:")), "")


def test_k7_each_meshtastic_instance_traces_to_its_own_file_by_its_own_switch(tmp_path):
    radios_fixture.write(tmp_path, TWO_868)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r2, r3 = Instance("r2", band="868"), Instance("r3", band="868")
    assert _trace(svc, tmp_path, r2) == f"{tmp_path}/logs/r2/rf-meshtastic.log"
    assert _trace(svc, tmp_path, r3) == f"{tmp_path}/logs/r3/rf-meshtastic.log"
    assert svc.save_config_bundle("meshtastic", values={"rf_log": "off"}, inst=r3).ok
    assert _trace(svc, tmp_path, r3) == "" and _trace(svc, tmp_path, r2) == f"{tmp_path}/logs/r2/rf-meshtastic.log"


@pytest.mark.parametrize("job,surface", [("r2/rf-meshcom.log", "meshcom"), ("r3/rf-meshtastic.log", "meshtastic")])
def test_k7_an_instances_rf_log_is_a_registered_job(job, surface):
    e, rid = rflog.by_job(job)
    assert (e.surface, rid) == (surface, job.partition("/")[0])

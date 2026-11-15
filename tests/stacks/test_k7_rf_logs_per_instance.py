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


def _fresh(tmp_path):
    """A runtime whose radios exist but whose daemons never ran: no logs/<rid>/ yet."""
    radios_fixture.write(tmp_path, TWO_868)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert not (tmp_path / "logs" / "r2").exists()
    return svc, Instance("r2", band="868")


def test_a_trace_file_is_named_only_with_its_directory_there(tmp_path):
    """meshtasticd opens Logging.TraceFile at start and crashes ("traceFile open failure", then
    "free(): invalid pointer") when logs/<rid>/ is missing, which is the case on a radio whose
    daemon never started. The config that names the trace makes the directory it names."""
    svc, r2 = _fresh(tmp_path)
    assert _trace(svc, tmp_path, r2) == f"{tmp_path}/logs/r2/rf-meshtastic.log"
    d = tmp_path / "logs" / "r2"
    assert d.is_dir() and not d.is_symlink()


def test_no_trace_makes_no_directory(tmp_path):
    svc, r2 = _fresh(tmp_path)
    assert svc.save_config_bundle("meshtastic", values={"rf_log": "off"}, inst=r2).ok
    assert _trace(svc, tmp_path, r2) == ""
    assert not (tmp_path / "logs" / "r2").exists()


@pytest.mark.parametrize("kind", ["file", "link"])
def test_a_trace_directory_that_cannot_hold_the_log_blocks_the_config(tmp_path, kind):
    """logs/r2 as a file, or as a link out of the runtime: the config is not written (the start is
    blocked with the reason) and nothing is created through the link."""
    svc, r2 = _fresh(tmp_path)
    (tmp_path / "logs").mkdir(exist_ok=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    if kind == "file":
        (tmp_path / "logs" / "r2").write_text("not a directory")
    else:
        (tmp_path / "logs" / "r2").symlink_to(outside, target_is_directory=True)
    written = {w.component: w for w in svc.write_config_files("meshtastic", "868", inst=r2)}
    mesh = written["meshtastic"]
    assert mesh.status == "failed" and "logs/r2" in mesh.detail, mesh
    assert not (tmp_path / "config" / "files" / "r2" / "meshtasticd.yaml").exists()
    assert not outside.exists()

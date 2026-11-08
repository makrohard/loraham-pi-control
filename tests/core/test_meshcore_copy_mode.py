"""Must-fix 7 of the stack-copy cleanup: a listed MeshCore copy's Companion client is judged by the copy's OWN
running mode (its node process and its generated config), never slot 0's. The copy runs repeater-only (its generated
file says so) while its saved mode is chat again, not yet restarted: its client is refused; slot 0's is not."""
import radios_fixture
from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('meshcore = ["r4"]\n')
    paths = Paths(runtime_root=tmp_path)
    cfgmod.update_stack_config(paths, "meshcore", {"file_mode": "repeater", "file_repeater_name": "RptR4"}, band="r4")
    svc = ControllerService(system=FakeSystem().system, paths=paths)
    svc.write_config_files("meshcore-node", "868", inst=instances.copy_of(svc, "meshcore", "r4")[0])   # its launch
    cfgmod.update_stack_config(paths, "meshcore", {"file_mode": "chat"}, band="r4")                     # saved since
    node = ["/usr/bin/python", "-m", "meshcore_host", str(tmp_path / "config" / "files" / "r4" / "meshcore.toml")]
    return ControllerService(system=FakeSystem(cmdlines_data={51: node}).system, paths=paths)


def test_a_copys_client_is_refused_by_the_copys_own_running_repeater_only_mode(tmp_path):
    svc = _svc(tmp_path)
    res = svc.start("meshcore-webui", apply=False, inst=instances.copy_of(svc, "meshcore", "r4")[0])
    assert res.ok is False and "repeater-only mode" in res.summary


def test_slot0s_client_is_not_refused_by_the_copys_mode(tmp_path):
    res = _svc(tmp_path).start("meshcore-webui", apply=False)
    assert "repeater-only mode" not in res.summary


def test_a_stopped_copys_client_follows_its_saved_mode_while_slot0_runs(tmp_path):
    # Slot 0's node runs; the copy's does not (its generated file is the last launch's, repeater-only): the copy is
    # judged by its own saved mode (chat), never by slot 0's running node.
    svc = _svc(tmp_path)
    slot0 = ["/usr/bin/python", "-m", "meshcore_host", str(tmp_path / "config" / "files" / "meshcore.toml")]
    svc = ControllerService(system=FakeSystem(cmdlines_data={52: slot0}).system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("meshcore-webui", apply=False, inst=instances.copy_of(svc, "meshcore", "r4")[0])
    assert "repeater-only mode" not in res.summary


def test_a_live_node_whose_running_mode_cannot_be_read_refuses_its_client(tmp_path):
    # G1-C40: the copy's node runs, its generated config is gone, its saved mode says chat: the client is refused
    # (fail closed), never started against a node that may serve no TCP 5000. Slot 0 by the same rule.
    svc = _svc(tmp_path)
    (tmp_path / "config" / "files" / "r4" / "meshcore.toml").unlink()
    res = svc.start("meshcore-webui", apply=False, inst=instances.copy_of(svc, "meshcore", "r4")[0])
    assert res.ok is False and "the running mode of meshcore@r4 cannot be read" in res.summary
    slot0 = ["/usr/bin/python", "-m", "meshcore_host", str(tmp_path / "config" / "files" / "meshcore.toml")]
    svc = ControllerService(system=FakeSystem(cmdlines_data={53: slot0}).system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("meshcore-webui", apply=False)
    assert res.ok is False and "the running mode of meshcore cannot be read" in res.summary

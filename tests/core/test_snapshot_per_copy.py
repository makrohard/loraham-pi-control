"""C7 (CLEANUP-DESIGN-v6 section 12): the snapshot judges a stack's components per copy. A running listed copy's
process is not slot 0's: with meshcore@r4 running and slot 0 stopped, slot 0 (its Home card included) shows not
running, never RUNNING (W286)."""
import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.model import RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))


def _svc(tmp_path, argv_of):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('meshcore = ["r4"]\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    node = svc.stack("meshcore").component("meshcore-node")
    table = {71: ["/usr/bin/python", "-m", "meshcore_host", *argv_of(svc, node)]}
    return ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))


def _node_state(svc):
    return svc.build_snapshot(fresh=True).stack("meshcore").components["meshcore-node"].run_state


def test_a_running_copy_does_not_make_slot0_run(tmp_path):
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0], "868")[1]])
    assert _node_state(svc) not in (RunState.RUNNING, RunState.DEGRADED)


def test_slot0s_own_process_still_runs(tmp_path):
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", band="868")[0], "868")[1]])
    assert _node_state(svc) not in (RunState.STOPPED,)


def test_a_running_copy_is_a_user_of_its_radio(tmp_path):
    # C7 (R7v4's delta look at _radio_users): with the snapshot per copy, a running meshcore@r4 must still hold r4
    # against its removal, by its own key.
    from lhpc.core import radio_start
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0], "868")[1]])
    r4 = next(r for r in radios.load(svc._paths).radios if r.id == "r4")
    assert radio_start._radio_users(svc, r4) == ["meshcore@r4"]


def test_a_copys_verified_record_does_not_make_slot0_run(tmp_path):
    # A copy's process the spec misses is still the copy's by its ownership record (slot 1): never slot 0's.
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        radios_fixture.write(tmp_path, ROWS)
        (tmp_path / "config" / "instances.toml").write_text('meshcore = ["r4"]\n')
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("meshcore")
        assert svc._lifecycle().record_launch(stk, stk.component("meshcore-node"), p.pid, band="868",
                                              extra={"rid": "r4", "slot": 1})
        assert _node_state(svc) not in (RunState.RUNNING, RunState.DEGRADED)
    finally:
        p.kill()
        p.wait(timeout=5)

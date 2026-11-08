"""C7 (CLEANUP-DESIGN-v6 section 12, decision B): with meshcore@r4 running and slot 0 stopped, slot 0's DISPLAYS
(ComponentStatus.own_run_state: the CLI status rows, the stack's rollup) show it not running, never RUNNING (W286),
while every safety reader keeps `run_state`, any process of the spec, and still sees the copy run."""
from lhpc.core.status import rollup_states
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


def _node(svc):
    return svc.build_snapshot(fresh=True).stack("meshcore").components["meshcore-node"]


def test_a_running_copy_does_not_make_slot0s_display_run_and_safety_still_sees_it(tmp_path):
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0], "868")[1]])
    assert svc.stack_running("meshcore") is True        # the safety readers' predicate: a copy runs
    assert rollup_states(svc.build_snapshot(fresh=True))["meshcore"] == "stopped"
    assert ["meshcore-node", "stopped"] in [line.split()[:2] for line in svc.status("meshcore").details]   # the CLI row
    assert _node(svc).own_run_state is RunState.STOPPED


def test_slot0s_own_process_still_runs(tmp_path):
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", band="868")[0], "868")[1]])
    assert _node(svc).own_run_state is not RunState.STOPPED


def test_a_running_copy_is_a_user_of_its_radio(tmp_path):
    # C7 (R7v4's delta look at _radio_users), a safety reader: a running meshcore@r4 still holds r4 against removal.
    from lhpc.core import radio_start
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0], "868")[1]])
    r4 = next(r for r in radios.load(svc._paths).radios if r.id == "r4")
    assert radio_start._radio_users(svc, r4)                 # the radio stays held against its removal


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
        assert _node(svc).own_run_state is RunState.STOPPED
    finally:
        p.kill()
        p.wait(timeout=5)


def _spawn():
    import subprocess
    import sys
    return subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)


def test_a_copys_record_counts_as_the_copys_even_when_no_copy_is_listed_now(tmp_path):
    # G1-C48 (1): meshcore@r4 runs by its verified slot-1 record but is listed no more: still not slot 0's.
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, ROWS)
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("meshcore")
        assert svc._lifecycle().record_launch(stk, stk.component("meshcore-node"), p.pid, band="868",
                                              extra={"rid": "r4", "slot": 1})
        assert _node(svc).own_run_state is RunState.STOPPED and svc.stack_running("meshcore") is True
    finally:
        p.kill()
        p.wait(timeout=5)


def test_a_recorded_copy_stays_the_copys_after_a_restart_mode_save(tmp_path):
    # G1-C48 (2): the copy's saved port changed after its launch, so its token no longer matches: its record still
    # names it, so slot 0's display stays stopped.
    from lhpc.core import config as cfgmod
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, ROWS)
        (tmp_path / "config" / "instances.toml").write_text('meshcore = ["r4"]\n')
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("meshcore")
        assert svc._lifecycle().record_launch(stk, stk.component("meshcore-node"), p.pid, band="868",
                                              extra={"rid": "r4", "slot": 1})
        cfgmod.update_stack_config(svc._paths, "meshcore", {"file_mode": "repeater"}, band="r4")
        assert _node(svc).own_run_state is RunState.STOPPED
    finally:
        p.kill()
        p.wait(timeout=5)


def test_a_copys_record_of_a_component_without_a_process_spec_is_the_copys(tmp_path, monkeypatch):
    # G1-C48 (3): a component whose process spec is gone still has its copy's verified record kept out of slot 0's.
    import dataclasses
    from lhpc.core import manifest as manifest_mod
    real = manifest_mod.load_manifest

    def load(path=None):
        return tuple(dataclasses.replace(s, components=tuple(dataclasses.replace(c, process=None) if c.id == "meshcore-node"
                                                             else c for c in s.components)) for s in real(path))
    monkeypatch.setattr(manifest_mod, "load_manifest", load)
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, ROWS)
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("meshcore")
        assert svc._lifecycle().record_launch(stk, stk.component("meshcore-node"), p.pid, band="868",
                                              extra={"rid": "r4", "slot": 1})
        assert _node(svc).copy_pids == [p.pid]
    finally:
        p.kill()
        p.wait(timeout=5)

"""A radio's users per instance (G1-C49/C51/C53; K5: decision B's display split is gone, every instance is told by
its own token or verified record): an instance holds its own radio by its key `<stack>@<rid>`; a record whose radio
left the hardware list holds every radio; a process no instance claims (no token, no record) holds its band's radios
by the bare stack id, fail-safe for a removal."""
import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))


def _svc(tmp_path, argv_of):
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    node = svc.stack("meshcore").component("meshcore-node")
    table = {71: ["/usr/bin/python", "-m", "meshcore_host", *argv_of(svc, node)]}
    return ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))


def _node(svc):
    return svc.build_snapshot(fresh=True).stack("meshcore").components["meshcore-node"]


def test_a_running_copy_is_a_user_of_its_radio(tmp_path):
    # C7 (R7v4's delta look at _radio_users), a safety reader: a running meshcore@r4 still holds r4 against removal.
    from lhpc.core import radio_start
    svc = _svc(tmp_path, lambda svc, node: [instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0], "868")[1]])
    r4 = next(r for r in radios.load(svc._paths).radios if r.id == "r4")
    assert radio_start._radio_users(svc, r4) == ["meshcore@r4"]      # held, by the copy's own key


def _spawn():
    import subprocess
    import sys
    return subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)


def test_a_radio_is_held_by_the_copy_on_it_and_free_beside_it(tmp_path):
    # G1-C49: a radio's users are judged per radio. kiss@r3 (868) runs, no other kiss does: r3 is held by kiss@r3; r1
    # (433) and r2 (868, the band's first radio) are free, never refused or freed by the stack's running state.
    from lhpc.core import radio_start
    rows = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
            radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    tnc = svc.stack("kiss").component("loraham-kiss-tnc")
    opt, val = instances._token(svc, tnc, instances.copy_of(svc, "kiss", "r3")[0], "868")
    svc = ControllerService(system=FakeSystem(cmdlines_data={81: ["/x/loraham-kiss-tnc", opt, val]}).system,
                            paths=Paths(runtime_root=tmp_path))
    users = {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios}
    assert users == {"r1": [], "r2": [], "r3": ["kiss@r3"]}


def test_a_record_whose_radio_left_the_list_holds_every_radio(tmp_path):
    # G1-C49 fail-safe: a verified record of meshcore@r9, r9 gone from the hardware list: its band is not known any
    # more, so every radio stays held by that instance, never freed.
    from lhpc.core import radio_start
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, ROWS)
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("meshcore")
        assert svc._lifecycle().record_launch(stk, stk.component("meshcore-node"), p.pid, band="868", extra={"rid": "r9"})
        assert {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios} == {
            "r2": ["meshcore@r9"], "r4": ["meshcore@r9"]}
    finally:
        p.kill()
        p.wait(timeout=5)


def test_two_instances_each_hold_their_own_radio(tmp_path):
    from lhpc.core import radio_start
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    node = svc.stack("meshcore").component("meshcore-node")
    argv = [["/usr/bin/python", "-m", "meshcore_host", instances._token(svc, node, instances.copy_of(svc, "meshcore", r)[0],
                                                                          "868")[1]] for r in ("r2", "r4")]
    svc = ControllerService(system=FakeSystem(cmdlines_data={91: argv[0], 92: argv[1]}).system, paths=Paths(runtime_root=tmp_path))
    assert {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios} == {
        "r2": ["meshcore@r2"], "r4": ["meshcore@r4"]}


def test_an_instance_that_does_not_run_leaves_its_radio_free(tmp_path):
    from lhpc.core import radio_start
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios} == {"r2": [], "r4": []}


def test_an_unclaimed_process_beside_a_running_instance_holds_every_radio_of_its_band(tmp_path):
    # G1-C51 (1): meshcore@r4 runs by its own token AND a meshcore node with no token and no record runs: no instance
    # claims that pid, so the band's radios stay held by the stack (a running instance never frees the band).
    from lhpc.core import radio_start
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    node = svc.stack("meshcore").component("meshcore-node")
    own = ["/usr/bin/python", "-m", "meshcore_host", instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0],
                                                                        "868")[1]]
    bare = [node.process.exec_name, *node.process.all_args]
    svc = ControllerService(system=FakeSystem(cmdlines_data={92: own, 94: bare}).system, paths=Paths(runtime_root=tmp_path))
    assert {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios} == {
        "r2": ["meshcore"], "r4": ["meshcore", "meshcore@r4"]}


def test_a_hand_started_non_main_component_holds_its_band_beside_a_running_instance(tmp_path):
    # G1-C51 (2): the main of meshcore@r2 is stopped but a meshcore-webui no instance claims runs (pid 93), beside
    # meshcore@r4: the band stays held by the stack (before: only the main counted, so r2 read free).
    from lhpc.core import radio_start
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    stk = svc.stack("meshcore")
    node, web = stk.component("meshcore-node"), stk.component("meshcore-webui")
    copy = ["/usr/bin/python", "-m", "meshcore_host", instances._token(svc, node, instances.copy_of(svc, "meshcore", "r4")[0], "868")[1]]
    ui = [web.process.exec_name, *web.process.all_args, *web.process.any_args[:1]]
    svc = ControllerService(system=FakeSystem(cmdlines_data={92: copy, 93: ui}).system, paths=Paths(runtime_root=tmp_path))
    assert svc.build_snapshot(fresh=True).stack("meshcore").components["meshcore-webui"].pids == [93]
    assert {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios} == {
        "r2": ["meshcore"], "r4": ["meshcore", "meshcore@r4"]}


def test_an_instances_record_holds_its_own_radios_band_and_the_other_instance_its_own(tmp_path):
    # G1-C53: kiss@r1 runs on 433 (its own token) and a verified record of kiss@r3 (868) runs: each holds its own
    # radio; r2 (433, beside r1) is not needlessly held.
    from lhpc.core import radio_start
    rows = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"),
            radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, rows)
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        stk = svc.stack("kiss")
        tnc = stk.component("loraham-kiss-tnc")
        opt, val = instances._token(svc, tnc, instances.copy_of(svc, "kiss", "r1")[0], "433")
        svc = ControllerService(system=FakeSystem(cmdlines_data={81: ["/x/loraham-kiss-tnc", opt, val]}).system,
                                paths=Paths(runtime_root=tmp_path))
        assert svc._lifecycle().record_launch(stk, tnc, p.pid, band="868", extra={"rid": "r3"})
        assert {r.id: radio_start._radio_users(svc, r) for r in radios.load(svc._paths).radios} == {
            "r1": ["kiss@r1"], "r2": [], "r3": ["kiss@r3"]}
    finally:
        p.kill()
        p.wait(timeout=5)

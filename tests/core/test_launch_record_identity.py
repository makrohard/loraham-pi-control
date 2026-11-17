"""G1-K4B 3 and 6: a running instance is its verified launch record's (13.16), whatever the hardware list says now.
A radio that left the list still addresses its running instance for a stop (a start refuses on the why), and the
band a running stack holds is the one it was launched on, not an edited row's."""
import subprocess
import sys

import radios_fixture

from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

R1, R3 = radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1")


def _running_kiss_on_r3(tmp_path):
    radios_fixture.write(tmp_path, (R1, R3))
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    kiss = svc.stack("kiss")
    assert svc._lifecycle().record_launch(kiss, kiss.component("loraham-kiss-tnc"), p.pid, band="433",
                                          extra={"rid": "r3"})
    return p


def test_a_running_instance_whose_radio_left_the_list_is_still_stopped_by_name(tmp_path):
    p = _running_kiss_on_r3(tmp_path)
    try:
        radios_fixture.write(tmp_path, (R1,))                       # r3 removed while kiss@r3 runs
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        _sid, inst, band, why = instances.parse_target(svc, "kiss@r3", "stop")
        assert (inst.rid, band, why) == ("r3", "433", "kiss@r3: r3 is not in the hardware list"), (inst, band, why)
        res = svc.stop("kiss", apply=True, inst=inst)
        assert res.ok, (res.summary, res.details)
        assert p.wait(timeout=10) is not None
        assert instances.parse_target(svc, "kiss@r3", "start")[1] is None       # nothing to start there
    finally:
        p.kill()
        p.wait(timeout=5)


def test_a_running_stacks_band_is_its_launch_band_not_an_edited_row(tmp_path):
    import dataclasses
    p = _running_kiss_on_r3(tmp_path)
    try:
        radios_fixture.write(tmp_path, (R1, dataclasses.replace(R3, band="868")))   # r3's row edited to 868
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        assert instances.launch_bands(svc, "kiss") == {"433"}
        assert svc._live_bands("kiss", "") == {"433"}
    finally:
        p.kill()
        p.wait(timeout=5)


def _spawn():
    return subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)


def test_re1_a_stop_after_the_row_moved_to_another_allowed_band_uses_the_launch_band(tmp_path):
    # kiss runs on 433 and 868: r3's row edited 433 -> 868 while kiss@r3 runs; the stop addresses it with its launch
    # band (433) and reaches it; a start refuses (it runs as launched).
    import dataclasses
    p = _running_kiss_on_r3(tmp_path)
    try:
        radios_fixture.write(tmp_path, (R1, dataclasses.replace(R3, band="868")))
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        _sid, inst, band, why = instances.parse_target(svc, "kiss@r3", "stop")
        assert (inst.rid, band) == ("r3", "433") and "runs as launched on 433 MHz" in why, (band, why)
        assert svc.stop("kiss", apply=True, inst=inst).ok
        assert p.wait(timeout=10) is not None
    finally:
        p.kill()
        p.wait(timeout=5)


def test_re3_two_verified_records_that_disagree_on_a_radios_band_tell_no_band(tmp_path):
    radios_fixture.write(tmp_path, (R1, R3))
    a, b = _spawn(), _spawn()
    try:
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        kiss = svc.stack("kiss")
        for p, band, comp in ((a, "433", "loraham-kiss-tnc"), (b, "868", "loraham-kiss-serial")):
            assert svc._lifecycle().record_launch(kiss, kiss.component(comp), p.pid, band=band, extra={"rid": "r3"})
        assert instances.recorded_bands(svc, "kiss") == {"r3": ""}
        assert instances.launch_bands(svc, "kiss") == set()          # the caller holds every band
    finally:
        for p in (a, b):
            p.kill()
            p.wait(timeout=5)


def test_re2_a_bare_kill_takes_an_unrecorded_process_of_a_removed_radio(tmp_path):
    from lhpc.core import kill
    from lhpc.core.commands import Instance
    radios_fixture.write(tmp_path, (R1, R3))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    tok = instances._token(plain, plain.stack("kiss").component("loraham-kiss-tnc"), Instance("r3"), "433")
    p = _spawn()
    try:
        radios_fixture.write(tmp_path, (R1,))                          # r3 removed; its hand-started TNC runs on
        svc = ControllerService(system=FakeSystem(cmdlines_data={p.pid: ["/x/loraham-kiss-tnc", *tok]}).system,
                                paths=Paths(runtime_root=tmp_path))
        plan = kill.kill(svc, "kiss", "loraham-kiss-tnc")
        assert plan.ok and plan.data["pids"] == [p.pid], (plan.summary, plan.details)
    finally:
        p.kill()
        p.wait(timeout=5)


def test_re2_guard_a_stop_still_leaves_a_hand_started_spec_process_alone(tmp_path):
    # The bare kill's spec match is KILL only: `stop kiss@r1` never signals a hand-started TNC that carries no token
    # and no record (slice 2's safety fix); the stop does not claim it stopped.
    from lhpc.core.commands import Instance
    radios_fixture.write(tmp_path, (R1, R3))
    p = _spawn()
    try:
        svc = ControllerService(system=FakeSystem(cmdlines_data={p.pid: ["/x/loraham-kiss-tnc"]}).system,
                                paths=Paths(runtime_root=tmp_path))
        res = svc.stop("kiss", apply=True, inst=Instance("r1", band="433"))
        from lhpc.core.outcomes import Outcome
        assert p.poll() is None, res.summary                          # alive
        tnc = next(r for r in res.results if r.component == "loraham-kiss-tnc")
        assert not res.ok and tnc.outcome == Outcome.MANUAL_REQUIRED and f"kill {p.pid}" in tnc.summary, tnc.summary
    finally:
        p.kill()
        p.wait(timeout=5)


def test_a_launch_records_the_bases_it_ran_on(tmp_path):
    # G2-UIU3-R3 H2a (FIX-PLAN-v2 §2): the record names the ports the instance launched on ("bases", {} = the
    # defaults); a launch without a radio records none. The record stays a valid v1 record (stop/restore unchanged)
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system)
        life.OBSERVE_TIMEOUT_S = 0
        comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("x",))
        for rid, bases, want in (("r2", {8001: 9000}, {"8001": 9000}), ("r3", None, {}), ("", {8001: 9000}, None)):
            assert life._observe_and_record(Stack(id="s", name="s", main="c"), comp, p.pid, "", ["x"], rid=rid,
                                            bases=bases) == "ok"
            [rec] = [r for r in life.owned_records("c", role=None) if r.get("rid", "") == rid]
            assert ("bases" in rec, rec.get("bases")) == (want is not None, want), rec
            assert life._validate_owned_record(f"{rec['launch_id']}.json", rec) == ""
    finally:
        p.kill()
        p.wait(timeout=5)


def _kiss_rows_after_a_second_move(tmp_path, bases, alive=True, argv=None, stopped=None):
    """kiss@r2 launched with the record's `bases` (a real process: the record verifies), then saved again at 9500
    (:9633); the launch's own port :9133 still listens on every address. `argv`: the process's command line (None:
    unreadable, so a running kiss's port reads "unverified"); `stopped`: a monkeypatch, and the snapshot reads kiss
    as not started though its launch still runs (its main down). The kiss rows that are not "absent", and the pill."""
    from lhpc.core.probes.backends import Listener
    radios_fixture.write(tmp_path, (R1, radios.Radio("r2", "b", "uputronics", "433", ce="ce1")))
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = ControllerService(system=FakeSystem(listeners=[Listener("ipv4", "0.0.0.0", 9133, 1)],
                                                  cmdlines_data={p.pid: argv} if argv else {}).system,
                                paths=Paths(runtime_root=tmp_path))
        kiss = svc.stack("kiss")
        assert svc._lifecycle().record_launch(kiss, kiss.component("loraham-kiss-tnc"), p.pid, band="433",
                                              extra={"rid": "r2", **({} if bases is None else {"bases": bases})})
        assert svc.save_config_bundle("kiss", values={"kiss_port": "9500"},
                                      inst=instances.copy_of(svc, "kiss", "r2")[0]).ok
        if not alive:
            p.kill()
            p.wait(timeout=5)
        if stopped is not None:
            from lhpc.core.model import RunState
            snap = svc.build_snapshot()
            for cs in next(ss for ss in snap.stacks if ss.stack.id == "kiss").components.values():
                cs.run_state = RunState.STOPPED
            stopped.setattr(type(svc), "build_snapshot", lambda self, *a, **k: snap)
        rows = svc.dashboard_webservers()
        return (sorted((r["rid"], r["port"], r["live_scope"], r["exposure"]["label"]) for r in rows
                       if r.get("kind") == "port" and r.get("sid") == "kiss" and r["live_scope"] != "absent"),
                svc.security_pill(rows)["level"])
    finally:
        p.kill()
        p.wait(timeout=5)


def test_a_listener_left_on_an_earlier_saved_port_is_its_launchs_row(tmp_path):
    # G2-UIU3-R3 H2a/H3 (SWEEP-CLASS-UI-U3-LISTENER-v2): r2 launched at 9000 (:9133), saved again at 9500. Its argv
    # names :9133: r2's one row is that public socket, the pill "bad"
    assert _kiss_rows_after_a_second_move(tmp_path, {"8001": 9000}, argv=["./loraham-kiss-tnc", "--kiss-port", "9133"]) \
        == ([("r2", "9133", "exposed", "public")], "bad")


def test_a_listener_whose_argv_names_no_port_is_still_its_launchs_row(tmp_path):
    # The same with an argv that names no port: only the launch record names :9133, so it has its own r2 row beside
    # the saved :9633 one; a running kiss with no port in its argv reads "unverified" (review) on both. red on
    # d9436671: no :9133 row
    assert _kiss_rows_after_a_second_move(tmp_path, {"8001": 9000}) == (
        [("r2", "9133", "unverified", "unverified"), ("r2", "9633", "unverified", "unverified")], "warn")


def test_a_not_started_stacks_leftover_on_an_earlier_saved_port_is_its_launchs_row(tmp_path, monkeypatch):
    # second read v7 M1 (Y1): kiss reads as not started (its main down) while r2's verified launch still listens on
    # :9133, its port before the move to 9500. The launch record still names it: one public r2 row, pill "bad"
    assert _kiss_rows_after_a_second_move(tmp_path, {"8001": 9000}, stopped=monkeypatch) == (
        [("r2", "9133", "exposed", "public")], "bad")


def test_a_start_records_the_bases_of_the_instance_it_launches(tmp_path, monkeypatch):
    # second read v7 M1 (Y3): Lifecycle.start hands the launched instance's own bases to its record
    from lhpc.core import commands
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system,
                     spawn=lambda argv, log, cwd=None, env=None: 4321)
    life.OBSERVE_TIMEOUT_S = 0.0
    monkeypatch.setattr(life, "_proc_alive", lambda pid: True)
    monkeypatch.setattr(life, "_argv_confirms", lambda cmd, argv: True)
    monkeypatch.setattr(life, "_capture_identity", lambda pid: None)
    extras = []
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: extras.append(k.get("extra")) or True)
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, run_argv=("true",))
    assert life.start(Stack(id="s", name="s", main="c"), comp, inst=commands.Instance("r2", bases={8001: 9000})).ok
    assert extras == [{"rid": "r2", "bases": {"8001": 9000}}]

def test_a_record_without_bases_or_with_bad_ones_adds_no_row(tmp_path):
    # FIX-PLAN-v2 §6: a record written before "bases" names no port (the stated residual); a bad value adds nothing
    for bases in (None, {"8001": "9000"}, {"x": 9000}, {"8001": 80}, [9000]):
        assert _kiss_rows_after_a_second_move(tmp_path, bases)[0] == [("r2", "9633", "unverified", "unverified")], bases
    # a record whose process is gone is no launch's (verify_owned), whatever its bases
    assert _kiss_rows_after_a_second_move(tmp_path, {"8001": 9000}, alive=False)[0] == []

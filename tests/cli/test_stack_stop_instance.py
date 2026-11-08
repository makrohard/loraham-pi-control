"""Item 7c2b: `lhpc stack stop|restart <stack>@<rid>` act on that instance alone, and a plain stop is slot 0's alone.

A stop is record-driven (Lifecycle.stop): an instance's stop takes only the records of its radio (rid), judges a
leftover process by the instance's own token and verifies cessation on the instance's own endpoints; slot 0's stop,
once the stack has copies (the hardware list's, K4ac), leaves those copies' records alone. The service level records what each
component's Lifecycle.stop is asked; the Lifecycle level runs the real filter on given records."""
import pytest
from radios_fixture import R1, R2_868, R3, kiss_on as _kiss_on

from lhpc.adapters.cli import main as cli
from lhpc.core import commands
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener

@pytest.fixture
def stops(monkeypatch):
    """Every Lifecycle.stop call: (component, its scope keywords); each answers STOPPED."""
    calls = []

    def stop(self, comp, band=None, **kw):
        calls.append((comp.id, {k: v for k, v in kw.items() if v not in (None, (), [])}))
        return CompResult(component=comp.id, action="stop", outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", stop)
    return calls


def _tnc(calls):
    return [kw for cid, kw in calls if cid == "loraham-kiss-tnc"]


def test_an_instance_stop_takes_its_radios_records_its_token_and_its_endpoints(box, stops):
    rc, out, _ = box((R1, R2_868, R3))("stop", "kiss@r3")
    assert rc == 0, out
    (kw,) = _tnc(stops)
    assert (kw["rid"], kw["token"]) == ("r3", ("--data-socket", "/tmp/loraf-r3.sock"))
    assert [e.address for e in kw["endpoints"]] == ["127.0.0.1:8267"]          # DEC-W348: 8001 + 133 x 2


def test_slot_0s_stop_leaves_the_copies_alone(box, stops):
    run = box((R1, R2_868, R3), procs={4100: _kiss_on("r1")})
    svc = cli.ControllerService()
    assert svc._set_running_band("kiss", "433")
    rc, out, _ = run("stop", "kiss@r1")                          # K3: slot 0 named on its radio
    (kw,) = _tnc(stops)
    assert (kw["exclude_rids"], kw["token"]) == (("r3",), ("--data-socket", "/tmp/loraf-r1.sock"))


def test_without_copies_the_stop_is_as_before(box, stops):
    box((R1, R3))("stop", "kiss@r1")
    assert _tnc(stops) == [{}]


def test_an_instance_restart_stops_and_starts_that_instance(box, stops):
    rc, out, launches = box((R1, R2_868, R3), ports=(8267,))("restart", "kiss@r3")
    assert [kw["rid"] for kw in _tnc(stops)] == ["r3"], (stops, out)
    assert ("loraham-kiss-tnc", "868", 1, "r3") in launches, (launches, out)


def test_an_instance_stop_leaves_slot_0s_band_marker(box, stops, tmp_path):
    run = box((R1, R2_868, R3))
    svc = cli.ControllerService()
    assert svc._set_running_band("kiss", "433")
    assert run("stop", "kiss@r3")[0] == 0
    assert cli.ControllerService().running_band("kiss", "") == "433"


# ---- Lifecycle.stop's own filter -------------------------------------------------------------------------

def _life(tmp_path, recs, procs=(), ports=()):
    from lhpc.core.config import Config, OperatorConfig
    system = FakeSystem(cmdlines_data=dict(procs),
                        listeners=[Listener(family="ipv4", ip="127.0.0.1", port=p, inode=i) for i, p in enumerate(ports, 1)])
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), system.system)
    life.owned_inventory = lambda: (list(recs), [], "ok")
    return life


def _tnc_comp():
    from lhpc.core.manifest import load_manifest
    return next(c for s in load_manifest(None) for c in s.components if c.id == "loraham-kiss-tnc")


def test_owned_records_filter_by_rid_and_role(tmp_path):
    recs = [{"component": "loraham-kiss-tnc", "role": "", "band": "433", "rid": "r1"},
            {"component": "loraham-kiss-tnc", "role": "", "band": "868", "rid": "r3"}]
    life = _life(tmp_path, recs)
    assert [r["rid"] for r in life.owned_records("loraham-kiss-tnc", rid="r3")] == ["r3"]
    assert [r["rid"] for r in life.owned_records("loraham-kiss-tnc")] == ["r1", "r3"]


def test_an_instance_stop_with_no_record_never_names_another_instances_process(tmp_path):
    comp = _tnc_comp()
    inst = commands.Instance(1, "r3")
    life = _life(tmp_path, [], procs={4100: _kiss_on("r1")}, ports=(8001,))     # only slot 0 runs, on 8001
    from lhpc.core import instances
    res = life.stop(comp, rid="r3", token=("--data-socket", "/tmp/loraf-r3.sock"),
                    endpoints=instances.endpoints_for(comp.endpoints, inst))
    assert res.outcome == Outcome.ALREADY_STOPPED, res.summary


def test_an_unowned_process_of_the_instance_itself_is_named(tmp_path):
    comp = _tnc_comp()
    life = _life(tmp_path, [], procs={4100: _kiss_on("r1"), 4200: _kiss_on("r3")})
    res = life.stop(comp, rid="r3", token=("--data-socket", "/tmp/loraf-r3.sock"))
    assert res.outcome == Outcome.MANUAL_REQUIRED and res.summary.split("kill ")[1] == "4200", res.summary


def test_slot_0s_stop_never_takes_a_listed_instances_record(tmp_path):
    comp = _tnc_comp()
    recs = [{"component": "loraham-kiss-tnc", "role": "", "band": "868", "rid": "r3", "pid": 4200}]
    life = _life(tmp_path, recs, procs={4200: _kiss_on("r3")})
    res = life.stop(comp, exclude_rids=("r3",), token=("--data-socket", "/tmp/loraf-r1.sock"))
    assert res.outcome == Outcome.ALREADY_STOPPED and res.pid is None, res.summary


# ---- G1-B1 (1): a running instance keeps its recorded key when the hardware list changes (13.16) -----------------

def _running_with_record(tmp_path, rows, rid, slot):
    """A real process of kiss-tnc recorded as `kiss` on `rid` at `slot` (its verified launch record) over `rows`."""
    import subprocess
    import sys

    import radios_fixture
    from lhpc.core.services import ControllerService
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    stk = svc.stack("kiss")
    assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433",
                                          extra={"rid": rid, "slot": slot})
    return p


A433 = (__import__("lhpc.core.radios", fromlist=["Radio"]).Radio("r1", "a", "uputronics", "433", ce="ce0"),
        __import__("lhpc.core.radios", fromlist=["Radio"]).Radio("r3", "b", "uputronics", "433", ce="ce1"))


def test_g1_b1_a_running_copy_stays_the_copy_when_its_bands_first_radio_is_disabled(box, stops, tmp_path):
    import dataclasses

    import radios_fixture
    run = box(A433)
    p = _running_with_record(tmp_path, A433, "r3", 1)                            # kiss@r3 runs, slot 1
    try:
        radios_fixture.write(tmp_path, (dataclasses.replace(A433[0], enabled=False), A433[1]))   # r3 is 433's first now
        rc, out, _ = run("stop", "kiss@r3")
        assert [kw.get("rid") for kw in _tnc(stops)] == ["r3"], (stops, out)     # the copy's records, never slot 0's
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_a_running_slot_0_stays_slot_0_when_a_lower_radio_joins_its_band(box, stops, tmp_path):
    import radios_fixture
    run = box(A433[1:])
    p = _running_with_record(tmp_path, A433[1:], "r3", 0)                        # slot 0 runs on r3, 433's only radio
    try:
        radios_fixture.write(tmp_path, A433)                                     # r1 joins: the list now makes r3 a copy
        rc, out, _ = run("stop", "kiss@r3")
        (kw,) = _tnc(stops)
        assert "rid" not in kw and "r3" not in kw.get("exclude_rids", ()), (kw, out)   # slot 0's own record is taken
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_re_a_running_slot_0_on_a_disabled_radio_stops_but_never_starts(box, stops, tmp_path, capsys):
    # G1-B1-RE: slot 0 runs on r3 by its record and the list makes r3 a copy now (r1 joined); r3 is then disabled.
    # A start or restart of kiss@r3 is refused naming the disabled radio; its stop still reaches the running slot 0.
    import dataclasses

    import radios_fixture
    run = box(A433[1:])
    p = _running_with_record(tmp_path, A433[1:], "r3", 0)
    try:
        radios_fixture.write(tmp_path, (A433[0], dataclasses.replace(A433[1], enabled=False)))
        for verb in ("start", "restart"):
            rc = cli.main(["stack", verb, "kiss@r3", "--yes"])
            got = capsys.readouterr()
            assert rc != 0 and "kiss@r3: r3 is disabled" in got.out + got.err, (verb, got.out, got.err)
        assert stops == []
        run("stop", "kiss@r3")
        (kw,) = _tnc(stops)
        assert "rid" not in kw and "r3" not in kw.get("exclude_rids", ()), kw              # slot 0's own record
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_re_one_stop_reads_one_record_snapshot_for_all_its_components(box, stops, monkeypatch):
    # G1-B1-RE: every read of the records answers another recorded copy (as records come and go); slot 0's stop
    # takes ONE snapshot, so its two components exclude the same copies.
    from lhpc.core import instances
    n = iter(range(4, 99))
    monkeypatch.setattr(instances, "recorded", lambda svc, sid: {f"r{next(n)}": 1})
    run = box((R1, R3))
    run("stop", "kiss@r1")
    excl = [kw.get("exclude_rids") for cid, kw in stops if cid in ("loraham-kiss-serial", "loraham-kiss-tnc")]
    assert len(excl) == 2 and excl[0] == excl[1] and excl[0], stops

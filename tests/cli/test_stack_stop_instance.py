"""Item 7c2b: `lhpc stack stop|restart <stack>@<rid>` act on that instance alone.

A stop is record-driven (Lifecycle.stop): an instance's stop takes only the records of its radio (rid), judges a
leftover process by the instance's own token and verifies cessation on the instance's own endpoints. K4b: every
instance's stop is scoped so, with or without other instances on the list (no slots, no exclusion list). The service level records what each
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


@pytest.mark.parametrize("rows", [(R1, R2_868, R3), (R1, R3)], ids=["another-868-instance", "one-per-band"])
def test_kiss_at_r1s_stop_takes_only_r1s_records_and_token(box, stops, rows):
    # Every pass (the stop, then the daemon stop's dependents check while the faked process still runs) is r1's.
    run = box(rows, procs={4100: _kiss_on("r1")})
    rc, out, _ = run("stop", "kiss@r1")
    tnc = _tnc(stops)
    assert tnc, (stops, out)
    assert {(kw["rid"], kw["token"]) for kw in tnc} == {("r1", ("--data-socket", "/tmp/loraf-r1.sock"))}
    assert all("exclude_rids" not in kw for _cid, kw in stops)


def test_an_instance_restart_stops_and_starts_that_instance(box, stops):
    rc, out, launches = box((R1, R2_868, R3), ports=(8267,))("restart", "kiss@r3")
    assert [kw["rid"] for kw in _tnc(stops)] == ["r3"], (stops, out)
    assert ("loraham-kiss-tnc", "868", "r3") in launches, (launches, out)


@pytest.mark.parametrize("marker", [None, "433\n"], ids=["no-marker", "a-0.13.x-marker"])
def test_the_stop_writes_and_clears_no_per_stack_band_marker(box, stops, tmp_path, marker):
    # K4b (design 13.12.5): an instance's band is its radio's; state/running/<sid>.band is not read, so no stop
    # writes one, and a 0.13.x file is left as it was (it goes with the band choosers, K6).
    run = box((R1, R2_868, R3))
    f = tmp_path / "state" / "running" / "kiss.band"
    if marker is not None:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(marker)
    for target in ("kiss@r1", "kiss@r3"):
        run("stop", target)
    assert (f.read_text() if f.exists() else None) == marker


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
    inst = commands.Instance("r3")
    life = _life(tmp_path, [], procs={4100: _kiss_on("r1")}, ports=(8001,))     # only kiss@r1 runs, on 8001
    from lhpc.core import instances
    res = life.stop(comp, rid="r3", token=("--data-socket", "/tmp/loraf-r3.sock"),
                    endpoints=instances.endpoints_for(comp.endpoints, inst))
    assert res.outcome == Outcome.ALREADY_STOPPED, res.summary


def test_an_unowned_process_of_the_instance_itself_is_named(tmp_path):
    comp = _tnc_comp()
    life = _life(tmp_path, [], procs={4100: _kiss_on("r1"), 4200: _kiss_on("r3")})
    res = life.stop(comp, rid="r3", token=("--data-socket", "/tmp/loraf-r3.sock"))
    assert res.outcome == Outcome.MANUAL_REQUIRED and res.summary.split("kill ")[1] == "4200", res.summary


def test_an_instance_stop_never_takes_another_radios_record(tmp_path):
    # K4b: kiss@r1's stop takes r1's records alone; kiss@r3's record (and its process, by r3's token) are r3's
    comp = _tnc_comp()
    recs = [{"component": "loraham-kiss-tnc", "role": "", "band": "868", "rid": "r3", "pid": 4200}]
    life = _life(tmp_path, recs, procs={4200: _kiss_on("r3")})
    res = life.stop(comp, rid="r1", token=("--data-socket", "/tmp/loraf-r1.sock"))
    assert res.outcome == Outcome.ALREADY_STOPPED and res.pid is None, res.summary


# ---- G1-B1 (1): a running instance keeps its recorded key when the hardware list changes (13.16) -----------------

def _running_with_record(tmp_path, rows, rid):
    """A real process of kiss-tnc recorded as `kiss@<rid>` (its verified launch record) over `rows`."""
    import subprocess
    import sys

    import radios_fixture
    from lhpc.core.services import ControllerService
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    radios_fixture.write(tmp_path, rows)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    stk = svc.stack("kiss")
    assert svc._lifecycle().record_launch(stk, stk.component("loraham-kiss-tnc"), p.pid, band="433",
                                          extra={"rid": rid})
    return p


A433 = (__import__("lhpc.core.radios", fromlist=["Radio"]).Radio("r1", "a", "uputronics", "433", ce="ce0"),
        __import__("lhpc.core.radios", fromlist=["Radio"]).Radio("r3", "b", "uputronics", "433", ce="ce1"))


def test_g1_b1_a_running_copy_stays_the_copy_when_its_bands_first_radio_is_disabled(box, stops, tmp_path):
    import dataclasses

    import radios_fixture
    run = box(A433)
    p = _running_with_record(tmp_path, A433, "r3")                               # kiss@r3 runs
    try:
        radios_fixture.write(tmp_path, (dataclasses.replace(A433[0], enabled=False), A433[1]))   # r3 is 433's first now
        rc, out, _ = run("stop", "kiss@r3")
        assert {kw.get("rid") for kw in _tnc(stops)} == {"r3"}, (stops, out)     # its own records, never r1's
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_a_running_instance_keeps_its_radio_when_a_lower_radio_joins_its_band(box, stops, tmp_path):
    import radios_fixture
    run = box(A433[1:])
    p = _running_with_record(tmp_path, A433[1:], "r3")                           # kiss@r3 runs, 433's only radio
    try:
        radios_fixture.write(tmp_path, A433)                                     # r1 joins 433, below r3
        rc, out, _ = run("stop", "kiss@r3")
        assert {kw.get("rid") for kw in _tnc(stops)} == {"r3"}, (stops, out)     # kiss@r3's own record is taken
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_re_a_running_instance_on_a_disabled_radio_stops_but_never_starts(box, stops, tmp_path, capsys):
    # G1-B1-RE: kiss@r3 runs by its record; r1 joins 433 and r3 is then disabled. A start or restart of kiss@r3 is
    # refused naming the disabled radio; its stop still reaches the running kiss@r3.
    import dataclasses

    import radios_fixture
    run = box(A433[1:])
    p = _running_with_record(tmp_path, A433[1:], "r3")
    try:
        radios_fixture.write(tmp_path, (A433[0], dataclasses.replace(A433[1], enabled=False)))
        for verb in ("start", "restart"):
            rc = cli.main(["stack", verb, "kiss@r3", "--yes"])
            got = capsys.readouterr()
            assert rc != 0 and "kiss@r3: r3 is disabled" in got.out + got.err, (verb, got.out, got.err)
        assert stops == []
        run("stop", "kiss@r3")
        assert {kw.get("rid") for kw in _tnc(stops)} == {"r3"}, stops                    # kiss@r3's own record
    finally:
        p.kill()
        p.wait(timeout=5)


def test_g1_b1_re_records_coming_and_going_never_widen_an_instances_stop(box, stops, monkeypatch):
    # G1-B1-RE, K4b: every read of the records answers another recorded instance (as records come and go); kiss@r1's
    # stop scopes both its components to r1 all the same (no exclusion list to drift between them).
    from lhpc.core import instances
    n = iter(range(4, 99))
    monkeypatch.setattr(instances, "recorded", lambda svc, sid: {f"r{next(n)}"})       # recorded() is a set of rids
    run = box((R1, R3))
    run("stop", "kiss@r1")
    rids = [kw.get("rid") for cid, kw in stops if cid in ("loraham-kiss-serial", "loraham-kiss-tnc")]
    assert rids == ["r1", "r1"], stops

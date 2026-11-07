"""Item 7c2b: `lhpc stack stop|restart <stack>@<rid>` act on that instance alone, and a plain stop is slot 0's alone.

A stop is record-driven (Lifecycle.stop): an instance's stop takes only the records of its radio (rid), judges a
leftover process by the instance's own token and verifies cessation on the instance's own endpoints; slot 0's stop,
once the stack has listed instances, leaves those instances' records alone. The service level records what each
component's Lifecycle.stop is asked; the Lifecycle level runs the real filter on given records."""
import pytest
from radios_fixture import R1, R3, kiss_on as _kiss_on

from lhpc.adapters.cli import main as cli
from lhpc.core import commands
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener

KISS = 'kiss = ["r3"]\n'


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
    rc, out, _ = box((R1, R3), KISS)("stop", "kiss@r3")
    assert rc == 0, out
    (kw,) = _tnc(stops)
    assert (kw["rid"], kw["token"]) == ("r3", ("--data-socket", "/tmp/loraf-r3.sock"))
    assert [e.address for e in kw["endpoints"]] == ["127.0.0.1:8011"]


def test_slot_0s_stop_leaves_the_listed_instances_alone(box, stops):
    run = box((R1, R3), KISS, procs={4100: _kiss_on("r1")})
    svc = cli.ControllerService()
    assert svc._set_running_band("kiss", "433")
    rc, out, _ = run("stop", "kiss")
    (kw,) = _tnc(stops)
    assert (kw["exclude_rids"], kw["token"]) == (("r3",), ("--data-socket", "/tmp/loraf-r1.sock"))


def test_without_listed_instances_the_stop_is_as_before(box, stops):
    box((R1, R3), None)("stop", "kiss")
    assert _tnc(stops) == [{}]


def test_an_instance_restart_stops_and_starts_that_instance(box, stops):
    rc, out, launches = box((R1, R3), KISS, ports=(8011,))("restart", "kiss@r3")
    assert [kw["rid"] for kw in _tnc(stops)] == ["r3"], (stops, out)
    assert ("loraham-kiss-tnc", "868", 1, "r3") in launches, (launches, out)


def test_an_instance_stop_leaves_slot_0s_band_marker(box, stops, tmp_path):
    run = box((R1, R3), KISS)
    svc = cli.ControllerService()
    assert svc._set_running_band("kiss", "433")
    assert run("stop", "kiss@r3")[0] == 0
    assert cli.ControllerService().running_band("kiss", "") == "433"


def test_a_running_instance_is_not_removed(box, capsys):
    box((R1, R3), KISS, procs={4100: _kiss_on("r3")})
    rc = cli.main(["stack", "remove-instance", "kiss", "r3"])
    assert rc == 1 and "Cannot remove kiss@r3: it is running — stop it first" in capsys.readouterr().out
    assert (cli.ControllerService()._paths.runtime_root / "config" / "instances.toml").read_text().count('"r3"') == 1


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

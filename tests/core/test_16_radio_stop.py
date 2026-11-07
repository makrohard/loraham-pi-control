"""Item 16 (DEC-W53): one radio's daemon stops alone, `lhpc stack stop|restart daemon --radio rN`, with the clients on
that radio first; a per-band stop of a band two radios share stops each radio's daemon (S3; it was refused, W1-11).
The box: kiss slot 0 on r1 and kiss@r2, both 433. The stops are captured at Lifecycle.stop as (component, band, rid,
exclude_rids); the snapshot's running answers are stubbed: kiss runs as slot 0 (stop_dependents) and kiss@r2 runs."""
from __future__ import annotations

import pytest
import radios_fixture

from lhpc.adapters.cli.main import main
from lhpc.core import instances, radio_start, radios
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"))
KISS = ("loraham-kiss-serial", "loraham-kiss-tnc")


@pytest.fixture
def box(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, TWO_433)
    (tmp_path / "config" / "instances.toml").write_text('kiss = ["r2"]\n')
    stops = []

    def stop(self, comp, band=None, rid=None, exclude_rids=(), token=None, endpoints=None):
        stops.append((comp.id, band, rid, tuple(exclude_rids)))
        return CompResult(component=comp.id, stack="", action="stop", outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", stop)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: i.rid == "r2")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return svc, stops


def test_one_radios_daemon_stops_alone_after_its_instance(box):
    svc, stops = box
    res = svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2"))
    assert res.ok, res.details
    assert stops == [*((c, None, "r2", ()) for c in KISS), ("loraham-daemon", "433", "r2", ())]


def test_slot0s_radios_daemon_stops_alone_after_slot0(box):
    svc, stops = box
    assert svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r1")).ok
    assert stops == [*((c, None, None, ("r2",)) for c in KISS), ("loraham-daemon", "433", "r1", ())]


def test_a_per_band_stop_of_a_shared_band_stops_every_client_and_each_radios_daemon(box):
    # S3: before item 16 this was refused naming both radios, and nothing was signalled.
    svc, stops = box
    res = svc.stop("daemon", apply=True, band="433")
    assert res.ok, res.details
    assert stops == [*((c, None, None, ("r2",)) for c in KISS), *((c, None, "r2", ()) for c in KISS),
                     ("loraham-daemon", "433", None, ())]


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    radios_fixture.write(tmp_path, (*TWO_433, radios.Radio("r3", "c", "meshstick-1262", "868", enabled=False, usb_serial="MS123456")))
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    calls = []

    def run_action(self, action, sid, apply=False, band="", stop_owners=False, inst=None):
        calls.append((action, sid, apply, band, getattr(inst, "slot", None), getattr(inst, "rid", None)))
        from lhpc.core.services import ActionResult
        return ActionResult(True, "done", data={"changes": 1})          # a plan with something to do
    monkeypatch.setattr(ControllerService, "run_action", run_action)

    def run(*argv):
        rc = main(["stack", *argv])
        return rc, capsys.readouterr(), calls
    return run


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_the_cli_addresses_one_radios_daemon(cli, action):
    rc, _out, calls = cli(action, "daemon", "--radio", "r2", "--yes")
    assert rc == 0 and (action, "daemon", True, "433", 0, "r2") in calls                  # the apply
    assert {c[3:] for c in calls} == {("433", 0, "r2")}                                      # the plan too


@pytest.mark.parametrize("argv, rc, why", [
    (("stop", "daemon", "--radio", "r9", "--yes"), 1, "r9 is not a radio of the hardware list"),
    (("stop", "daemon", "--radio", "r3", "--yes"), 1, "r3 is disabled"),
    (("stop", "kiss", "--radio", "r2", "--yes"), 2, "--radio applies to the daemon only, without --band"),
    (("stop", "daemon", "--radio", "r2", "--band", "433", "--yes"), 2,
     "--radio applies to the daemon only, without --band"),
])
def test_the_cli_refuses_a_radio_it_cannot_address(cli, argv, rc, why):
    got, out, calls = cli(*argv)
    assert (got, calls) == (rc, []) and out.err == f"ERR   {why}\n"

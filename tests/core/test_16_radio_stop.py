"""Item 16 (DEC-W53): one radio's daemon stops alone, `lhpc stack stop|restart daemon --radio rN`, with the clients on
that radio first; a per-band stop of a band two radios share stops each radio's daemon (S3; it was refused, W1-11).
The box: kiss slot 0 on r1 and kiss@r2 (r2: 433's second radio, K4ac), both 433. The stops are captured at Lifecycle.stop as (component, band, rid,
exclude_rids); the snapshot's running answers are stubbed: kiss runs as slot 0 (stop_dependents) and kiss@r2 runs
(the other copies on r2, chat@r2 and meshcore@r2, do not)."""
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
    stops = []

    def stop(self, comp, band=None, rid=None, exclude_rids=(), token=None, endpoints=None):
        stops.append((comp.id, band, rid, tuple(exclude_rids)))
        return CompResult(component=comp.id, stack="", action="stop", outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", stop)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: i.rid == "r2" and c.id in KISS)
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
    (("stop", "daemon", "--radio", "r9", "--yes"), 1, "ERR   daemon@r9: r9 is not a radio of the hardware list"),
    (("stop", "kiss", "--radio", "r2", "--yes"), 2, "ERR   --radio applies to the daemon only: daemon@RID"),
    (("stop", "daemon", "--radio", "r2", "--band", "433", "--yes"), 2, "lhpc: error: unrecognized arguments: --band 433"),
])
def test_the_cli_refuses_a_radio_it_cannot_address(cli, argv, rc, why):
    got, out, calls = cli(*argv)
    assert (got, calls) == (rc, []) and out.err == f"{why}\n"


def test_a_disabled_radios_daemon_still_stops(cli):
    # G1-C7 B3, K3: the stop of a disabled radio's daemon reaches it (`daemon --radio r3` is daemon@r3); was refused.
    got, _out, calls = cli("stop", "daemon", "--radio", "r3", "--yes")
    assert got == 0 and calls[-1] == ("stop", "daemon", True, "868", 0, "r3")


def test_a_listed_instance_that_does_not_run_is_not_stopped(box, monkeypatch):
    svc, stops = box
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: False)
    assert svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2")).ok
    assert stops == [("loraham-daemon", "433", "r2", ())]


def test_a_stray_daemon_on_a_band_the_box_does_not_serve_stays_stoppable(box, monkeypatch):
    # A stop is never refused by its band: a daemon left on 868 after the list moved to 433 is still stopped.
    svc, stops = box
    monkeypatch.setattr(ControllerService, "radio_bands", lambda self: ["433"])
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: [])
    assert svc.stop("daemon", apply=True, band="868").ok
    assert stops == [("loraham-daemon", "868", None, ())]


def test_a_bandless_daemon_stop_stops_every_client_instance_too(box):
    # G1-C18 (1): `lhpc stack stop daemon@r1` without a band left kiss@r2 running (only slot 0 was stopped).
    svc, stops = box
    assert svc.stop("daemon", apply=True).ok
    assert stops == [*((c, None, None, ("r2",)) for c in KISS), *((c, None, "r2", ()) for c in KISS),
                     ("loraham-daemon", None, None, ())]


def test_one_radios_daemon_stop_clears_its_own_window_only(box, tmp_path):
    # G1-C21 note 1, the stop's side: r2's daemon stops; r1's daemon runs on and keeps its RF activity window
    svc, _stops = box
    floors = {rid: tmp_path / "state" / f"daemon-feed-floor-433-{rid}" for rid in ("r1", "r2")}
    for p in floors.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("123")
    assert svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2")).ok
    assert floors["r2"].read_text() != "123" and floors["r1"].read_text() == "123"

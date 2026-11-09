"""Item 16 (DEC-W53): one radio's daemon stops alone, `lhpc stack stop|restart daemon --radio rN`, with the clients on
that radio first; a per-band stop of a band two radios share stops each radio's daemon (S3; it was refused, W1-11).
The box: kiss@r1 and kiss@r2, both 433. The stops are captured at Lifecycle.stop as (component, band, rid); the
instances' running answers are stubbed: kiss@r1 and kiss@r2 run (chat and meshcore do not)."""
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

    def stop(self, comp, band=None, rid=None, token=None, endpoints=None):
        stops.append((comp.id, band, rid))
        return CompResult(component=comp.id, stack="", action="stop", outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", stop)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: [])   # no unclaimed process
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: i.rid in ("r1", "r2") and c.id in KISS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return svc, stops


def test_one_radios_daemon_stops_alone_after_its_instance(box):
    svc, stops = box
    res = svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2"))
    assert res.ok, res.details
    assert stops == [*((c, None, "r2") for c in KISS), ("loraham-daemon", "433", "r2")]


def test_the_other_radios_daemon_stops_alone_after_its_own_instance(box):
    svc, stops = box
    assert svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r1")).ok
    assert stops == [*((c, None, "r1") for c in KISS), ("loraham-daemon", "433", "r1")]


def test_a_per_band_stop_of_a_shared_band_stops_every_client_and_each_radios_daemon(box):
    # S3: before item 16 this was refused naming both radios, and nothing was signalled.
    svc, stops = box
    res = svc.stop("daemon", apply=True, band="433")
    assert res.ok, res.details
    assert stops == [*((c, None, "r1") for c in KISS), *((c, None, "r2") for c in KISS), ("loraham-daemon", "433", None)]


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    radios_fixture.write(tmp_path, (*TWO_433, radios.Radio("r3", "c", "meshstick-1262", "868", enabled=False, usb_serial="MS123456")))
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    calls = []

    def run_action(self, action, sid, apply=False, band="", stop_owners=False, inst=None):
        calls.append((action, sid, apply, band, getattr(inst, "rid", None)))
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
    assert rc == 0 and (action, "daemon", True, "433", "r2") in calls                     # the apply
    assert {c[3:] for c in calls} == {("433", "r2")}                                          # the plan too


@pytest.mark.parametrize("argv, rc, why", [
    (("stop", "daemon", "--radio", "r9", "--yes"), 1, "ERR   daemon@r9: r9 is not a radio of the hardware list"),
    (("stop", "kiss", "--radio", "r2", "--yes"), 2, "ERR   --radio applies to the daemon only: daemon@RID"),
])
def test_the_cli_refuses_a_radio_it_cannot_address(cli, argv, rc, why):
    got, out, calls = cli(*argv)
    assert (got, calls) == (rc, []) and out.err == f"{why}\n"


def test_a_disabled_radios_daemon_still_stops(cli):
    # G1-C7 B3, K3: the stop of a disabled radio's daemon reaches it (`daemon --radio r3` is daemon@r3); was refused.
    got, _out, calls = cli("stop", "daemon", "--radio", "r3", "--yes")
    assert got == 0 and calls[-1] == ("stop", "daemon", True, "868", "r3")


def test_a_listed_instance_that_does_not_run_is_not_stopped(box, monkeypatch):
    svc, stops = box
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: False)
    assert svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2")).ok
    assert stops == [("loraham-daemon", "433", "r2")]


def test_a_stray_daemon_on_a_band_the_box_does_not_serve_stays_stoppable(box, monkeypatch):
    # A stop is never refused by its band: a daemon left on 868 after the list moved to 433 is still stopped.
    svc, stops = box
    monkeypatch.setattr(ControllerService, "radio_bands", lambda self: ["433"])
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: [])
    assert svc.stop("daemon", apply=True, band="868").ok
    assert stops == [("loraham-daemon", "868", None)]


def test_a_bandless_daemon_stop_stops_every_client_instance_too(box):
    # G1-C18 (1): a whole-daemon stop without a band stops every client instance first (kiss@r1 and kiss@r2).
    svc, stops = box
    assert svc.stop("daemon", apply=True).ok
    assert stops == [*((c, None, "r1") for c in KISS), *((c, None, "r2") for c in KISS), ("loraham-daemon", None, None)]


def test_one_radios_daemon_stop_clears_its_own_window_only(box, tmp_path):
    # G1-C21 note 1, the stop's side: r2's daemon stops; r1's daemon runs on and keeps its RF activity window
    svc, _stops = box
    floors = {rid: tmp_path / "state" / f"daemon-feed-floor-433-{rid}" for rid in ("r1", "r2")}
    for p in floors.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("123")
    assert svc.stop("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2")).ok
    assert floors["r2"].read_text() != "123" and floors["r1"].read_text() == "123"


def test_g1_k4b_4_an_unclaimed_dependent_is_kept_though_another_radio_has_a_claimed_one(tmp_path, monkeypatch):
    # kiss@r1 runs by its own token; a second kiss process carries no token and has no record (hand-started on r2):
    # a stop of r2's daemon names it by its bare id; kiss@r1's token does not vouch for it.
    from lhpc.core.commands import Instance
    radios_fixture.write(tmp_path, TWO_433)
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    tnc = plain.stack("kiss").component("loraham-kiss-tnc")
    tok = instances._token(plain, tnc, Instance("r1"), "433")
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])  # the band's view
    svc = ControllerService(system=FakeSystem(cmdlines_data={71: ["/x/loraham-kiss-tnc", *tok],
                                                             72: ["/x/loraham-kiss-tnc"]}).system,
                            paths=Paths(runtime_root=tmp_path))
    assert instances.running_keys(svc, "kiss") == ["kiss@r1"]
    assert radio_start.radio_dependents(svc, "433", inst=radio_start.daemon_inst("r2")) == ["kiss"]
    claimed = ControllerService(system=FakeSystem(cmdlines_data={71: ["/x/loraham-kiss-tnc", *tok]}).system,
                                paths=Paths(runtime_root=tmp_path))
    assert radio_start.radio_dependents(claimed, "433", inst=radio_start.daemon_inst("r2")) == []   # all told: none


def test_k8_a_band_option_on_a_radio_stop_is_refused_by_name(cli):
    # K8 (W308/W310): the stop takes the radio alone; a --band beside it is refused by name, nothing stopped
    got, out, calls = cli("stop", "daemon", "--radio", "r2", "--band", "433", "--yes")
    assert (got, calls) == (2, []) and out.out.split(":", 1)[0] == "ERR   --band is gone (W308/W310)"

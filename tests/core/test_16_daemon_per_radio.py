"""Item 16 S1 (DESIGN-16 f3a881f9, DEC-W53): every enabled radio of the hardware list runs its own daemon, also two
on one band. A daemon start with a band ensures each of that band's enabled radios; a client start ensures its
instance's radio only (a band caller: the band's first radio, r2 being kiss@r2's; `<stack>@<rid>`: that radio). The launches
are captured at Lifecycle.start (each one's `id` parameter names its radio); a READY daemon is a CONF socket that
answers READY."""
from __future__ import annotations

import radios_fixture

from lhpc.core import instances, radio_daemon, radio_start, radios
from lhpc.core.lifecycle import Lifecycle, StartLaunch
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"))
DAEMON = "loraham-daemon"


def _ready(rid):
    return {f"/tmp/loraconf-{rid}.sock": f"STATUS ID={rid} BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 "
                                         "CADRSSI=-90\n".encode()}


def _box(tmp_path, monkeypatch, *, ready=()):
    """The two-radio 433 box: (service, launches). Every launch fails after it is captured, as (component, radio,
    band): nothing runs, so each start's own daemon decisions are what is seen."""
    radios_fixture.write(tmp_path, TWO_433)
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "radio_bands", lambda self: ["433"])
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    launches, recorded = [], []

    def start(self, stack, comp, params=None, band="", **kw):
        launches.append((comp.id, (params or {}).get("id", ""), band))
        recorded.append(getattr(kw.get("inst"), "rid", None))
        return StartLaunch(False, "", "captured (no process)")
    monkeypatch.setattr(Lifecycle, "start", start)
    replies = {}
    for rid in ready:
        replies.update(_ready(rid))
    fake = FakeSystem(unix_replies=replies)
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    svc.recorded, svc.fake = recorded, fake      # the rid each launch's record will name; the sockets' SETs
    return svc, launches


def test_a_daemon_start_on_a_shared_band_launches_each_radios_daemon(tmp_path, monkeypatch):
    # Before item 16: refused, "a band with two enabled radios starts no daemon". Each launch is tried, one at a time;
    # the first one's failure stops no other.
    svc, launches = _box(tmp_path, monkeypatch)
    svc.start("daemon", apply=True, band="433")
    assert launches == [(DAEMON, "r1", "433"), (DAEMON, "r2", "433")]
    assert svc.recorded == ["r1", "r2"]                  # each record names its radio: one radio's daemon stops alone


def test_a_band_callers_start_ensures_its_own_radios_daemon_only(tmp_path, monkeypatch):
    svc, launches = _box(tmp_path, monkeypatch)
    svc.start("kiss", apply=True, band="433")
    assert [x for x in launches if x[0] == DAEMON] == [(DAEMON, "r1", "433")]


def test_an_instances_start_ensures_its_own_radios_daemon_only(tmp_path, monkeypatch):
    svc, launches = _box(tmp_path, monkeypatch)
    sid, inst, band, why = instances.resolve(svc, "kiss@r2")
    assert why == ""
    svc.start(sid, apply=True, band=band, inst=inst)
    assert [x for x in launches if x[0] == DAEMON] == [(DAEMON, "r2", "433")]


def test_a_ready_radio_is_not_launched_again_and_the_other_one_is(tmp_path, monkeypatch):
    svc, launches = _box(tmp_path, monkeypatch, ready=("r1",))
    svc.start("daemon", apply=True, band="433")
    assert launches == [(DAEMON, "r2", "433")]


def test_a_client_whose_radios_daemon_is_ready_launches_no_daemon(tmp_path, monkeypatch):
    # kiss@r2's radio is READY; r1's is not, and the instance never starts it.
    svc, launches = _box(tmp_path, monkeypatch, ready=("r2",))
    sid, inst, band, _why = instances.resolve(svc, "kiss@r2")
    svc.start(sid, apply=True, band=band, inst=inst)
    assert [x for x in launches if x[0] == DAEMON] == []


def test_an_instances_daemon_settings_go_to_its_own_radios_socket(tmp_path, monkeypatch):
    svc, _launches = _box(tmp_path, monkeypatch, ready=("r2",))
    sid, inst, band, _why = instances.resolve(svc, "kiss@r2")
    res = svc.start(sid, apply=True, band=band, inst=inst)
    sent = {path for path, _payload in svc.fake.sent}
    assert sent == {"/tmp/loraconf-r2.sock"}
    assert "  [ok] 433: TXMODE already MANAGED" in res.details, res.details      # read back from r2's socket


def test_a_radio_a_daemon_process_still_holds_is_not_launched_again(tmp_path, monkeypatch):
    # r1's socket is down, but a daemon process with --id r1 holds the radio: r1 is not relaunched (the stuck
    # instance is named), and r2 is launched.
    svc, launches = _box(tmp_path, monkeypatch)
    monkeypatch.setattr(radio_start, "_daemon_procs",
                        lambda svc: [radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")])
    res = svc.start("daemon", apply=True, band="433")
    assert launches == [(DAEMON, "r2", "433")]
    assert any("[fail] daemon on 433 (r1): CONF socket unreachable but a daemon process" in line
               for line in res.details), res.details


def test_a_daemon_start_for_one_radio_launches_that_radios_daemon_only(tmp_path, monkeypatch):
    # `lhpc stack start|restart daemon --radio r2`: the other radio of the band is left as it is.
    svc, launches = _box(tmp_path, monkeypatch)
    svc.start("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r2"))
    assert launches == [(DAEMON, "r2", "433")]


def test_another_stacks_client_on_the_other_radio_keeps_no_settings_from_this_radio(tmp_path, monkeypatch):
    # G1-C18 (2): chat@r1 runs; kiss@r2 starts on its retained r2 daemon: r2's settings are applied.
    svc, _launches = _box(tmp_path, monkeypatch, ready=("r2",))
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: (svc.stack_of(c.id), i.rid) == ("chat", "r1"))
    sid, inst, band, _why = instances.resolve(svc, "kiss@r2")
    res = svc.start(sid, apply=True, band=band, inst=inst)
    assert {path for path, _payload in svc.fake.sent} == {"/tmp/loraconf-r2.sock"}, res.details
    assert not any("[keep]" in line for line in res.details), res.details


def test_the_same_stack_on_the_other_radio_keeps_no_settings_from_this_radio(tmp_path, monkeypatch):
    # G1-C18 (2): kiss@r1 runs; kiss@r2 starts on its retained r2 daemon: r2's settings are applied.
    svc, _launches = _box(tmp_path, monkeypatch, ready=("r2",))
    monkeypatch.setattr(instances, "instance_runs", lambda svc, c, i, band: (svc.stack_of(c.id), i.rid) == ("kiss", "r1"))
    sid, inst, band, _why = instances.resolve(svc, "kiss@r2")
    res = svc.start(sid, apply=True, band=band, inst=inst)
    assert {path for path, _payload in svc.fake.sent} == {"/tmp/loraconf-r2.sock"}, res.details


def test_a_daemon_start_for_a_radio_not_on_the_band_launches_nothing(tmp_path, monkeypatch):
    # G1-C18 (3): an explicit radio the band does not have is refused, never every radio of the band.
    svc, launches = _box(tmp_path, monkeypatch)
    res = svc.start("daemon", apply=True, band="433", inst=radio_start.daemon_inst("r3"))
    assert launches == [] and "  [BLOCKED] daemon: r3 is no enabled radio of 433 MHz" in res.details, res.details

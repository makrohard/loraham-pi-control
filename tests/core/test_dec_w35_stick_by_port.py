"""DEC-W35: at every daemon start lhpc resolves a USB radio's serial to the port sysfs lists it on now and starts the
daemon with --usb-port (daemon 1.4.0; the library's own serial match stays a fallback, because a stick such as the
433 build answers its serial unreadably). Through radio_start.start_plan (the launch params) and verify_identity (the
BOUND= after READY, `<serial>@<port>`), on a faked USB listing and a faked CONF socket."""
from __future__ import annotations

import radios_fixture

from lhpc.core import radio_start, radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Field, Probe, Scan, UsbDevice

STICK = radios.Radio("r2", "stick", "meshstick-1262", "868", usb_serial="CHE1W868")


def _dev(port, serial=""):
    p = Field(Probe.PRESENT, "")
    return UsbDevice(port, Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p,
                     Field(Probe.ABSENT), Field(Probe.PRESENT, serial) if serial else Field(Probe.ABSENT))


def _svc(tmp_path, monkeypatch, devices, status=b""):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "loraham", "433"), STICK))
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: Scan(Probe.PRESENT, tuple(devices)))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    replies = {"/tmp/loraconf-r2.sock": status} if status else {}
    return ControllerService(system=FakeSystem(unix_replies=replies).system, paths=Paths(runtime_root=tmp_path))


def test_a_replugged_stick_starts_on_its_new_port(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, [_dev("1-1.4"), _dev("3-1", "CHE1W868")])
    params, why, _notes = radio_start.start_plan(svc, "r2")
    assert why == "" and params["usb_port"] == "3-1" and "usb_serial" not in params


def test_a_stick_that_is_not_plugged_in_refuses_naming_its_serial(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, [_dev("1-1.4")])
    _params, why, _notes = radio_start.start_plan(svc, "r2")
    assert why == "r2: no stick with serial CHE1W868 is plugged in"


def test_two_sticks_with_its_serial_refuse(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, [_dev("1-1.2", "CHE1W868"), _dev("3-1", "CHE1W868XX")])
    _params, why, _notes = radio_start.start_plan(svc, "r2")
    assert why == "r2: more than one stick reports serial CHE1W868"


def test_bound_with_an_unreadable_serial_is_accepted_on_the_resolved_port_only(tmp_path, monkeypatch):
    ok = _svc(tmp_path, monkeypatch, [_dev("3-1", "CHE1W868")],
              status=b"STATUS RADIO=READY ID=r2 BOUND=?@3-1\n")
    assert radio_start.verify_identity(ok, "r2") == ""
    other = _svc(tmp_path, monkeypatch, [_dev("3-1", "CHE1W868")],
                 status=b"STATUS RADIO=READY ID=r2 BOUND=?@1-1.4\n")
    assert radio_start.verify_identity(other, "r2") == "wrong stick at start: expected CHE1W868@3-1, got ?@1-1.4"


def test_bound_names_the_serial_and_the_resolved_port(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, [_dev("3-1", "CHE1W868")], status=b"STATUS RADIO=READY ID=r2 BOUND=CHE1W868@3-1\n")
    assert radio_start.verify_identity(svc, "r2") == ""

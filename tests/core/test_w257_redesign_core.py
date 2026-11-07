"""W257 (mock v13), the core under the redesigned Hardware page: each row's read-only `problems`, the one radio's
detail view model, and add_hat, one SPI row added under the hardware list's lock (W217) with the list's own checks.
Driven through radio_start.hardware_view / radio_detail / add_hat on a written list and a faked USB listing."""
from __future__ import annotations

import radios_fixture

from lhpc.core import radio_start, radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Field, Probe, Scan, UsbDevice


def _dev(port, serial=""):
    p = Field(Probe.PRESENT, "")
    return UsbDevice(port, Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p,
                     Field(Probe.ABSENT), Field(Probe.PRESENT, serial) if serial else Field(Probe.ABSENT))


def _svc(tmp_path, monkeypatch, rows, devices=(), ready=()):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    radios_fixture.write(tmp_path, rows)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: Scan(Probe.PRESENT, tuple(devices)))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: [])
    replies = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=-\n".encode() for r in ready}
    return ControllerService(system=FakeSystem(unix_replies=replies).system, paths=Paths(runtime_root=tmp_path))


def _row(view, rid):
    return next(r for r in view["rows"] if r["rid"] == rid)


# ---- (1) problems: read-only notes, each from a fact on disk or on the socket ------------------------------------

def test_a_working_radio_has_no_problems(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),), ready=("r1",))
    assert _row(radio_start.hardware_view(svc), "r1")["problems"] == []


def test_an_enabled_radio_whose_daemon_does_not_answer_is_offline(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))
    assert _row(radio_start.hardware_view(svc), "r1")["problems"] == ["daemon offline"]


def test_a_disabled_radio_is_not_offline(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433", enabled=False),))
    assert _row(radio_start.hardware_view(svc), "r1")["problems"] == []


def test_a_stick_not_plugged_in_is_named_by_its_binding(tmp_path, monkeypatch):
    rows = (radios.Radio("r2", "stick", "meshstick-1262", "868", usb_serial="26073012"),
            radios.Radio("r3", "spare", "meshstick-1262", "868", enabled=False, usb_port="1-1.4"))
    svc = _svc(tmp_path, monkeypatch, rows, devices=[_dev("1-1.2")], ready=("r2",))
    view = radio_start.hardware_view(svc)
    assert _row(view, "r2")["problems"] == ["stick not plugged in: serial 26073012"]
    assert _row(view, "r3")["problems"] == ["stick not plugged in on port 1-1.4"]


def test_problems_come_in_their_order(tmp_path, monkeypatch):
    rows = (radios.Radio("r2", "stick", "meshstick-1262", "868", usb_serial="26073012"),)
    svc = _svc(tmp_path, monkeypatch, rows)                                        # offline and not plugged in
    assert _row(radio_start.hardware_view(svc), "r2")["problems"] == ["daemon offline",
                                                                       "stick not plugged in: serial 26073012"]


# ---- (2) radio_detail ---------------------------------------------------------------------------------------------

def test_the_detail_of_a_radio_is_its_row_plus_remove_and_detect(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))      # stopped: removable
    d = radio_start.radio_detail(svc, "r1")
    assert d["rid"] == "r1" and d["problems"] == ["daemon offline"] and d["detect"] is True
    assert d["remove"]["facts"]["state"] == "enabled" and d["stop_first"] == ""


def test_the_detail_of_a_running_radio_names_what_to_stop(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),), ready=("r1",))
    monkeypatch.setattr(radio_start, "daemon_running", lambda svc, radio, procs=None: True)
    assert radio_start.radio_detail(svc, "r1")["stop_first"] == "stop its daemon, then remove r1"


def test_the_detail_of_no_such_radio_is_none(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))
    assert radio_start.radio_detail(svc, "r9") is None


# ---- (3) add_hat --------------------------------------------------------------------------------------------------

def _ids(tmp_path):
    return [(r.id, r.model, r.band, r.ce) for r in radios.load(Paths(runtime_root=tmp_path)).radios]


def test_a_hat_is_added_as_the_next_free_id(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))
    ok, msg = radio_start.add_hat(svc, "waveshare-sx1262", "868")
    assert ok, msg
    assert msg == "r2 added: waveshare-sx1262 on 868 MHz" and _ids(tmp_path)[-1] == ("r2", "waveshare-sx1262", "868", "")


def test_a_hat_with_two_variants_needs_one(tmp_path, monkeypatch):
    # the one radio is a USB stick: it claims no Pi line, so the lines alone decide nothing here
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "stick", "meshstick-1262", "868", usb_port="1-1.3"),))
    before = _ids(tmp_path)
    ok, msg = radio_start.add_hat(svc, "uputronics", "868")
    assert not ok and msg == "Not added: uputronics needs a variant (ce0, ce1)" and _ids(tmp_path) == before
    ok, msg = radio_start.add_hat(svc, "uputronics", "868", "ce1")
    assert ok and msg == "r2 added: uputronics (ce1) on 868 MHz" and _ids(tmp_path)[-1] == ("r2", "uputronics", "868", "ce1")


def test_a_hat_the_list_refuses_writes_nothing(tmp_path, monkeypatch):
    # the uputronics ce0 lines collide with the enabled loraham 433 module (BCM 8, 25): the list's own check refuses
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    ok, msg = radio_start.add_hat(svc, "uputronics", "433", "ce0")
    assert (ok, msg) == (False, "Not added: r2: BCM line 8 (cs) is already claimed by r1 (cs); r2: BCM line 25 (irq) is "
                                "already claimed by r1 (irq); r2: BCM line 24 (aux) is already claimed by r1 (gpio)")
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_a_band_the_model_does_not_run_and_a_usb_model_are_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))
    assert radio_start.add_hat(svc, "uputronics", "915", "ce1") == (False, "Not added: uputronics has no variant for 915 MHz")
    assert radio_start.add_hat(svc, "meshstick-1262", "868") == (False, "Not added: meshstick-1262 is not a board (SPI) model")
    assert radio_start.add_hat(svc, "nope", "868") == (False, "Not added: there is no model 'nope'")


def test_a_hat_never_takes_a_reserved_id(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "low", "loraham", "433"),))
    state = radios.load(svc._paths)
    assert radios.save(svc._paths, state.radios, retired=("r2",)) == []
    ok, msg = radio_start.add_hat(svc, "waveshare-sx1262", "868")
    assert (ok, msg) == (True, "r3 added: waveshare-sx1262 on 868 MHz")


def test_an_explicit_variant_is_kept_even_when_every_variant_carries_a_band(tmp_path, monkeypatch):
    # G1-C19: loraham's variants each carry their band; the operator's pick is still the row's `ce`
    svc = _svc(tmp_path, monkeypatch, (radios.Radio("r1", "stick", "meshstick-1262", "868", usb_port="1-1.3"),))
    ok, msg = radio_start.add_hat(svc, "loraham", "433", "433")
    assert (ok, msg) == (True, "r2 added: loraham (433) on 433 MHz")
    assert _ids(tmp_path)[-1] == ("r2", "loraham", "433", "433")

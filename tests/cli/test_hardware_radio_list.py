"""A26 (0.14.0 box H): `lhpc hardware` without an argument printed the setup only; the refusal of `lhpc daemon 433`
says "lhpc hardware lists them". It now prints, after its three lines, one line per radio of the hardware list: rN,
type (model), band, USB port, and "disabled" (his word). Through the CLI entry, stdout and exit status."""
import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Field, Probe, Scan, UsbDevice

ROWS = (radios.Radio("r1", "loraham 433", "loraham", "433"), radios.Radio("r2", "loraham 868", "loraham", "868"),
        radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),
        radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1"))


def _dev(port, serial="", unknown=False):
    p = Field(Probe.PRESENT, "")
    ser = Field(Probe.UNKNOWN) if unknown else (Field(Probe.PRESENT, serial) if serial else Field(Probe.ABSENT))
    return UsbDevice(port, Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p, Field(Probe.ABSENT), ser)


def _run(tmp_path, monkeypatch, capsys, rows=ROWS, devices=(), listing=Probe.PRESENT, raw=None):
    if raw is not None:
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "radios.toml").write_text(raw)
    elif rows is not None:
        radios_fixture.write(tmp_path, rows)
    scans = []
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: (scans.append(1), Scan(listing, tuple(devices)))[1])
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)
    rc = cli.main(["hardware"])
    return rc, capsys.readouterr().out.splitlines(), len(scans)


def _radio_lines(out):
    return [" ".join(x.split()) for x in out if x.split()[:1] and x.split()[0][:1] == "r" and x.split()[0][1:].isdigit()]


def test_the_radio_list_follows_the_setup(tmp_path, monkeypatch, capsys):
    # red-before (b3fc6e60): no radio lines at all
    rc, out, scans = _run(tmp_path, monkeypatch, capsys, devices=(_dev("3-2", "CHE1W433"), _dev("1-1")))
    assert rc == 0 and scans == 1
    assert _radio_lines(out) == ["r1 loraham 433 MHz -", "r2 loraham 868 MHz -", "r3 ch341-rfm98pw-433 433 MHz 3-2",
                                 "r4 pinedio-usb-1262 868 MHz 1-1"]


def test_the_first_three_lines_stay(tmp_path, monkeypatch, capsys):
    _rc, out, _s = _run(tmp_path, monkeypatch, capsys, devices=(_dev("3-2", "CHE1W433"), _dev("1-1")))
    assert out[0].split()[:3] == ["OK", "hardware", "setup:"] or "hardware setup:" in out[0]
    assert any("served band(s):" in x for x in out[:3]) and any("choose from:" in x for x in out[:3])


def test_missing_sticks_and_an_unreadable_list_are_named(tmp_path, monkeypatch, capsys):
    _rc, out, _s = _run(tmp_path, monkeypatch, capsys, devices=())
    assert _radio_lines(out)[2:] == ["r3 ch341-rfm98pw-433 433 MHz not attached", "r4 pinedio-usb-1262 868 MHz not attached"]
    _rc, out, _s = _run(tmp_path, monkeypatch, capsys, listing=Probe.UNKNOWN)
    assert _radio_lines(out)[2:] == ["r3 ch341-rfm98pw-433 433 MHz USB list unreadable",
                                     "r4 pinedio-usb-1262 868 MHz USB list unreadable"]


def test_an_unreadable_serial_of_the_rows_model_is_named(tmp_path, monkeypatch, capsys):
    _rc, out, _s = _run(tmp_path, monkeypatch, capsys, devices=(_dev("3-2", unknown=True), _dev("1-1")))
    assert "r3 ch341-rfm98pw-433 433 MHz serial unreadable" in _radio_lines(out)


def test_two_sticks_with_the_rows_serial_are_named(tmp_path, monkeypatch, capsys):
    _rc, out, _s = _run(tmp_path, monkeypatch, capsys, devices=(_dev("3-2", "CHE1W433"), _dev("3-3", "CHE1W433"), _dev("1-1")))
    assert "r3 ch341-rfm98pw-433 433 MHz more than one stick reports serial CHE1W433" in _radio_lines(out)
    # G1-B3-CODE N1: a third stick of the model with an unreadable serial does not hide the ambiguity
    _rc, out, _s = _run(tmp_path, monkeypatch, capsys, devices=(_dev("3-2", "CHE1W433"), _dev("3-3", "CHE1W433"),
                                                                 _dev("3-4", unknown=True), _dev("1-1")))
    assert "r3 ch341-rfm98pw-433 433 MHz more than one stick reports serial CHE1W433" in _radio_lines(out)


def test_a_disabled_radio_stays_listed(tmp_path, monkeypatch, capsys):
    rows = (ROWS[0], radios.Radio("r2", "loraham 868", "loraham", "868", enabled=False))
    _rc, out, scans = _run(tmp_path, monkeypatch, capsys, rows=rows)
    assert _radio_lines(out) == ["r1 loraham 433 MHz -", "r2 loraham 868 MHz - disabled"] and scans == 0


@pytest.mark.no_default_hardware                                    # the suite's baseline list stays out
def test_no_list(tmp_path, monkeypatch, capsys):
    rc, out, _s = _run(tmp_path, monkeypatch, capsys, rows=None)
    assert rc == 0 and any(x.strip() == "no hardware list" for x in out)


def test_an_invalid_list(tmp_path, monkeypatch, capsys):
    rc, out, _s = _run(tmp_path, monkeypatch, capsys, raw="not toml [")
    assert rc == 0 and any("hardware list unreadable: radios.toml is not valid TOML (TOMLDecodeError" in x for x in out)

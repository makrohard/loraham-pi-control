"""DEC-W43 W1/W2 (W5, the radio side): Detect through its public entry points, radio_detect.run.detect_radio /
detect_stick / detect_board. A radio whose daemon runs is refused FIRST, before any bus opens; then the direct-radio
and selection refusals; then the model's chip is searched on its own bus, which is closed in every case. The buses
are fakes behind the module's seams (run.spi_bus, ch341.select until P2 lands, ch341.Ch341Bus); the daemon check is
the real radio_start.daemon_running over a faked /proc listing."""
from __future__ import annotations

import pytest
import radios_fixture

from lhpc.core import radio_daemon, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.radio_detect import DetectRefused, ch341, run
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Field, Probe, Scan, UsbDevice

ROWS = (radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r2", "stick", "meshstick-1262", "868", usb_serial="26073012"),
        radios.Radio("r3", "spare", "meshstick-1262", "868", enabled=False, usb_port="1-1.4"))


class _Chip:
    """A fake chip on a fake bus: an SX127x answering `version` at 0x42, or an SX1262 answering its version."""

    def __init__(self, chip, version=0x12, text=b"SX1261 V2D 2D02"):
        self.chip, self.version, self.text, self.closed, self.t = chip, version, text, False, 0.0

    def reset(self, level):
        pass

    def busy(self):
        return False

    def transfer(self, out):
        if self.chip == "sx127x":
            return bytes([0, self.version])
        if out[0] == 0x80:
            return bytes([0, 0x22])
        if out[0] == 0x1D:
            return bytes(4) + self.text.ljust(16, b"\0")
        return bytes(len(out))

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t

    def close(self):
        self.closed = True


def _dev(port, serial=""):
    p = Field(Probe.PRESENT, "")
    return UsbDevice(port, Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p,
                     Field(Probe.ABSENT), Field(Probe.PRESENT, serial) if serial else Field(Probe.ABSENT))


class _Box:
    """The box under test: the list, a faked /proc daemon listing, the USB listing and the buses' seams."""

    def __init__(self, tmp_path, monkeypatch, procs=(), devices=(), chip=None, owner=None):
        (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state").mkdir(exist_ok=True)
        radios_fixture.write(tmp_path, ROWS)
        self.opened, self.selected, self.bus = [], [], chip
        monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: list(procs))
        monkeypatch.setattr(ControllerService, "_direct_radio_owners",
                            lambda self, bands: {b: owner for b in bands} if owner else {})
        monkeypatch.setattr(run, "_scan", lambda: Scan(Probe.PRESENT, tuple(devices)))

        def spi_bus(pins, chip_, lock):
            self.opened.append(("spi", dict(pins), chip_, str(lock)))
            return self.bus
        monkeypatch.setattr(run, "spi_bus", spi_bus)

        class _Usb:
            def close(inner):
                self.opened.append(("usb-closed",))

            def release(inner, h):
                pass

        monkeypatch.setattr(run, "libusb", _Usb)

        def select(usb, vid, pid, *, serial="", port=()):
            self.selected.append((vid, pid, serial, port))
            return "handle"
        monkeypatch.setattr(ch341, "select", select)

        def ch341_bus(usb, handle, pins):
            self.opened.append(("usb", dict(pins)))
            return self.bus
        monkeypatch.setattr(ch341, "Ch341Bus", ch341_bus)
        self.svc = ControllerService(system=FakeSystem(files={"/proc/uptime": "100.0 200.0\n"}).system,
                                     paths=Paths(runtime_root=tmp_path))


RUNNING_R1 = [radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")]
RUNNING_R2 = [radio_daemon.DaemonProc(4243, "id", rid="r2", band="868")]


def test_a_running_daemon_is_refused_first_and_nothing_opens(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=RUNNING_R1, chip=_Chip("sx127x"), owner="meshtastic")
    res = run.detect_radio(box.svc, "r1")
    assert res == run.Detected("refused", "Detect refused: r1's daemon is running. Stop it first.")
    assert box.opened == [] and box.selected == []


def test_a_running_stick_daemon_is_refused_before_the_usb_selection(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=RUNNING_R2, devices=[_dev("1-1.2", "26073012")], chip=_Chip("sx1262"))
    assert run.detect_radio(box.svc, "r2").line == "Detect refused: r2's daemon is running. Stop it first."
    assert box.opened == [] and box.selected == []


def test_an_unknown_process_counts_as_the_daemon(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(9, "unknown", reason="?")], chip=_Chip("sx127x"))
    assert run.detect_radio(box.svc, "r1").outcome == "refused" and box.opened == []


def test_a_board_radio_is_found_on_its_own_lines_and_the_bus_is_closed(tmp_path, monkeypatch):
    chip = _Chip("sx127x", version=0x12)
    box = _Box(tmp_path, monkeypatch, chip=chip)
    res = run.detect_radio(box.svc, "r1")
    assert res == run.Detected("found", "r1: SX127x found (version 0x12)", "sx127x", "0x12")
    assert box.opened == [("spi", {"cs": 8, "irq": 25, "rst": 5, "gpio": 24, "led_pin": 13}, "sx127x",
                           str(tmp_path / "state" / "loraham" / "spi0.lock"))]
    assert chip.closed


def test_no_answer_is_not_found(tmp_path, monkeypatch):
    chip = _Chip("sx127x", version=0x22)
    box = _Box(tmp_path, monkeypatch, chip=chip)
    res = run.detect_radio(box.svc, "r1")
    assert (res.outcome, res.line) == ("not-found", "r1: no SX127x answered") and chip.closed


def test_a_direct_radio_stack_on_the_band_is_refused(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, chip=_Chip("sx127x"), owner="meshtastic-433")
    res = run.detect_radio(box.svc, "r1")
    assert res.outcome == "refused" and "drives the 433 MHz radio itself" in res.line and box.opened == []


def test_a_held_line_is_the_refusal_line(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch)

    def held(*a):
        raise DetectRefused("GPIO line(s) [8, 5] held by another process")
    monkeypatch.setattr(run, "spi_bus", held)
    assert run.detect_radio(box.svc, "r1").line == "Detect refused: r1: GPIO line(s) [8, 5] held by another process"


def test_a_failing_search_still_closes_the_bus(tmp_path, monkeypatch):
    class _Broken(_Chip):
        def transfer(self, out):
            raise OSError(5, "EIO")
    chip = _Broken("sx127x")
    box = _Box(tmp_path, monkeypatch, chip=chip)
    res = run.detect_radio(box.svc, "r1")
    assert res.outcome == "refused" and "failed during the search" in res.line and chip.closed


def test_a_stick_is_selected_by_its_serial_on_its_current_port(tmp_path, monkeypatch):
    chip = _Chip("sx1262")
    box = _Box(tmp_path, monkeypatch, devices=[_dev("1-1.4"), _dev("3-2.4", "26073012")], chip=chip)
    res = run.detect_radio(box.svc, "r2")
    assert res == run.Detected("found", "r2: SX1262 found (SX1261 V2D 2D02)", "sx1262", "SX1261 V2D 2D02")
    assert box.selected == [(0x1A86, 0x5512, "26073012", (3, 2, 4))]          # DEC-W35: the current port
    assert box.opened[0] == ("usb", {"cs": 0, "irq": 6, "rst": 2, "gpio": 4, "rxen": 1}) and chip.closed


def test_a_serialless_stick_is_selected_by_its_port(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, devices=[_dev("1-1.4")], chip=_Chip("sx1262"))
    assert run.detect_radio(box.svc, "r3").outcome == "found"
    assert box.selected == [(0x1A86, 0x5512, "", (1, 1, 4))]


def test_a_stick_not_plugged_in_is_refused_without_a_selection(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, devices=[_dev("1-1.4")], chip=_Chip("sx1262"))
    res = run.detect_radio(box.svc, "r2")
    assert res.line == "Detect refused: r2: no stick with serial 26073012 is plugged in" and box.selected == []


def test_a_selection_refusal_closes_the_usb_context(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, devices=[_dev("1-1.2", "26073012")], chip=_Chip("sx1262"))

    def busy(usb, vid, pid, **kw):
        raise DetectRefused("the stick is in use by another process (its daemon?)")
    monkeypatch.setattr(ch341, "select", busy)
    res = run.detect_radio(box.svc, "r2")
    assert res.line == "Detect refused: r2: the stick is in use by another process (its daemon?)"
    assert box.opened == [("usb-closed",)]


def test_an_unknown_radio_and_an_invalid_list_are_refused(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, chip=_Chip("sx127x"))
    assert run.detect_radio(box.svc, "r9").outcome == "refused"
    (tmp_path / "config" / "radios.toml").write_text("not toml [")
    assert run.detect_radio(box.svc, "r1").line == "Detect refused: r1: the hardware list is not valid"
    assert box.opened == []


def test_add_a_stick_detects_a_listed_unheld_stick(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, devices=[_dev("1-1.3", "MSTICK02")], chip=_Chip("sx1262"))
    res = run.detect_stick(box.svc, "meshstick-1262", "1-1.3")
    assert res.line == "meshstick-1262 on port 1-1.3: SX1262 found (SX1261 V2D 2D02)"
    assert box.selected == [(0x1A86, 0x5512, "MSTICK02", (1, 1, 3))]


def test_add_a_stick_held_by_a_running_radio_is_refused_first(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=RUNNING_R2, devices=[_dev("1-1.2", "26073012")], chip=_Chip("sx1262"))
    assert run.detect_stick(box.svc, "meshstick-1262", "1-1.2").line == \
        "Detect refused: r2's daemon is running. Stop it first."
    assert box.selected == []


def test_add_a_board_is_refused_while_its_bands_daemon_runs(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=RUNNING_R1, chip=_Chip("sx127x"))
    assert run.detect_board(box.svc, "uputronics", "ce0", "433").line == \
        "Detect refused: r1's daemon is running. Stop it first."
    assert box.opened == []


def test_add_a_board_detects_on_the_variants_lines(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, chip=_Chip("sx127x", version=0x12))
    res = run.detect_board(box.svc, "uputronics", "ce1", "868")
    assert res.line == "uputronics 868 MHz: SX127x found (version 0x12)"
    assert box.opened[0][1:3] == ({"cs": 7, "irq": 16, "aux": 12, "led_pin": 13}, "sx127x")


def test_the_waveshare_row_takes_the_generic_sx1262_spi_path(tmp_path, monkeypatch):
    # W255: no Waveshare board to prove live; the same SX1262 SPI path as every SX1262 row, with its row's pins
    box = _Box(tmp_path, monkeypatch, chip=_Chip("sx1262"))
    res = run.detect_board(box.svc, "waveshare-sx1262", "default", "868")
    assert res.line == "waveshare-sx1262 868 MHz: SX1262 found (SX1261 V2D 2D02)"
    assert box.opened[0][1:3] == ({"cs": 21, "irq": 16, "rst": 18, "gpio": 20, "txen": 6}, "sx1262")


# ---- G1-C5: an invalid list, an unreadable serial and an unknown daemon refuse Detect before an Add -------------

def _unread(port):
    p = Field(Probe.PRESENT, "")
    return UsbDevice(port, Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p,
                     Field(Probe.ABSENT), Field(Probe.UNKNOWN))


def test_detect_before_an_add_refuses_an_invalid_list(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, devices=[_dev("1-1.3", "MSTICK02")], chip=_Chip("sx1262"))
    (tmp_path / "config" / "radios.toml").write_text("not toml [")
    assert run.detect_stick(box.svc, "meshstick-1262", "1-1.3").line == \
        "Detect refused: meshstick-1262 on port 1-1.3: the hardware list is not valid"
    assert run.detect_board(box.svc, "uputronics", "ce1", "868").line == \
        "Detect refused: uputronics 868 MHz: the hardware list is not valid"
    assert box.opened == [] and box.selected == []


def test_a_stick_whose_serial_cannot_be_read_is_checked_against_the_serial_radios(tmp_path, monkeypatch):
    # r2's stick (serial 26073012) is listed nowhere by its serial, and the stick on 3-1 answers no serial: it may be
    # r2's, whose daemon runs: refused, nothing opened
    box = _Box(tmp_path, monkeypatch, procs=RUNNING_R2, devices=[_unread("3-1")], chip=_Chip("sx1262"))
    assert run.detect_stick(box.svc, "meshstick-1262", "3-1").line == \
        "Detect refused: r2's daemon is running. Stop it first."
    assert box.opened == [] and box.selected == []


def test_a_stick_found_by_its_serial_on_another_port_is_that_radios(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=RUNNING_R2, devices=[_dev("3-1", "26073012")], chip=_Chip("sx1262"))
    assert run.detect_stick(box.svc, "meshstick-1262", "3-1").line == \
        "Detect refused: r2's daemon is running. Stop it first."


@pytest.mark.no_default_hardware                  # the list is really absent (conftest's baseline list off)
def test_a_daemon_of_unknown_identity_refuses_a_board_detect_with_no_radio_on_the_band(tmp_path, monkeypatch):
    box = _Box(tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(9, "unknown", reason="?")], chip=_Chip("sx127x"))
    (tmp_path / "config" / "radios.toml").unlink()                                  # no radio on any band
    assert run.detect_board(box.svc, "uputronics", "ce0", "433").line == \
        "Detect refused: a daemon process of unknown identity is running. Stop it first."
    assert box.opened == []


@pytest.mark.no_default_hardware                  # the list is really absent (conftest's baseline list off)
def test_a_0_13_daemon_with_no_list_refuses_a_board_detect_on_its_band_only(tmp_path, monkeypatch):
    # R7v6: an upgraded box (no radios.toml) still runs its no-id `--radio 433` daemon; Detect must not open its bus.
    box = _Box(tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(7, "no-id", band="433")], chip=_Chip("sx127x"))
    (tmp_path / "config" / "radios.toml").unlink()
    assert run.detect_board(box.svc, "uputronics", "ce0", "433").line == \
        "Detect refused: the 433 MHz radio's daemon is running. Stop it first."
    assert box.opened == []
    assert run.detect_board(box.svc, "uputronics", "ce1", "868").line == "uputronics 868 MHz: SX127x found (version 0x12)"


def test_a_daemon_of_a_radio_not_in_the_list_refuses_a_board_and_a_stick_detect(tmp_path, monkeypatch):
    # R7v6: `--id r9` runs but r9 has no row: it may hold the radio about to be detected.
    box = _Box(tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(8, "id", rid="r9")],
               devices=[_dev("1-1.3", "MSTICK02")], chip=_Chip("sx127x"))
    assert run.detect_board(box.svc, "uputronics", "ce0", "433").line == "Detect refused: r9's daemon is running. Stop it first."
    assert run.detect_stick(box.svc, "meshstick-1262", "1-1.3").line == "Detect refused: r9's daemon is running. Stop it first."
    assert box.opened == [] and box.selected == []

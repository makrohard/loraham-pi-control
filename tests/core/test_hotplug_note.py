"""The daemon's hotplug note in this start's log segment (B5-WIRING-TABLES-v1 Table K; INTERFACE-B5-W1
`offset_record`, `write_offset_record`, `hotplug_note`). New module: each test imports it itself."""
from __future__ import annotations

import os

import pytest

ACTIVE = "[CH341] unplug-wait active: a lost stick is waited for (libusb hotplug)"
NONE = "[CH341] note: libusb offers no usable hotplug here; a lost stick exits 5 as before"
BOOT = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
RUN = (4321, 123456, BOOT)


def _uf():
    from lhpc.core import usb_flow
    return usb_flow


def _probe():
    from lhpc.core.usb_access import Probe
    return Probe


def _start(tmp_path, before="old start\n", after="", run=RUN):
    """A log whose earlier part is `before` and this start's segment is `after`, with this start's record."""
    uf = _uf()
    log = tmp_path / "start-loraham-daemon.log"
    log.write_text(before + after, encoding="utf-8")
    line = uf.offset_record(len(before.encode()), run[0], run[1], run[2])
    assert uf.write_offset_record(log, line) is _probe().PRESENT
    return log


# ---- the record --------------------------------------------------------------------------------

def test_offset_record_is_one_line_of_the_fixed_form():
    line = _uf().offset_record(17, 4321, 123456, BOOT)
    assert line == f"17 4321 123456 {BOOT}\n"


@pytest.mark.parametrize("args", [(-1, 1, 1, BOOT), (True, 1, 1, BOOT), (1.0, 1, 1, BOOT), ("1", 1, 1, BOOT),
                                  (1, False, 1, BOOT), (1, 1, -2, BOOT), (1, 1, 1, "not-a-boot-id"),
                                  (1, 1, 1, BOOT.upper()), (1, 1, 1, BOOT + "\n")])
def test_offset_record_refuses_any_other_input(args):
    with pytest.raises(ValueError):
        _uf().offset_record(*args)


def test_write_offset_record_writes_atomically_and_never_raises(tmp_path):
    uf, P = _uf(), _probe()
    log = tmp_path / "start.log"
    log.write_text("", encoding="utf-8")
    assert uf.write_offset_record(log, uf.offset_record(0, 1, 2, BOOT)) is P.PRESENT
    assert (tmp_path / "start.log.offset").read_text(encoding="ascii") == f"0 1 2 {BOOT}\n"
    assert not [p for p in tmp_path.iterdir() if p.name not in ("start.log", "start.log.offset")]
    missing_dir = tmp_path / "gone" / "start.log"
    assert uf.write_offset_record(missing_dir, uf.offset_record(0, 1, 2, BOOT)) is P.UNKNOWN


# ---- the note in this start's segment (K-1 … K-5) --------------------------------------------------

def test_k1_the_unplug_wait_line_in_this_start_is_present(tmp_path):
    log = _start(tmp_path, after=f"x\n{ACTIVE}\ny\n")
    assert _uf().hotplug_note(log, RUN) is _probe().PRESENT


def test_k2_the_no_hotplug_line_in_this_start_is_absent(tmp_path):
    log = _start(tmp_path, after=f"{NONE}\n")
    assert _uf().hotplug_note(log, RUN) is _probe().ABSENT


def test_k1_a_trailing_cr_is_stripped(tmp_path):
    log = _start(tmp_path, after=f"{ACTIVE}\r\n")
    assert _uf().hotplug_note(log, RUN) is _probe().PRESENT


def test_k3_both_lines_the_last_one_wins(tmp_path):
    uf, P = _uf(), _probe()
    assert uf.hotplug_note(_start(tmp_path, after=f"{ACTIVE}\n{NONE}\n"), RUN) is P.ABSENT
    other = tmp_path / "b"
    other.mkdir()
    assert uf.hotplug_note(_start(other, after=f"{NONE}\n{ACTIVE}\n"), RUN) is P.PRESENT


def test_k3_a_note_only_in_an_earlier_start_is_unknown(tmp_path):
    log = _start(tmp_path, before=f"{ACTIVE}\n", after="this start printed nothing yet\n")
    assert _uf().hotplug_note(log, RUN) is _probe().UNKNOWN


@pytest.mark.parametrize("line", [f"{ACTIVE} ", f" {ACTIVE}", f"x{ACTIVE}", ACTIVE[:-1], f"{ACTIVE}\r\r",
                                  ACTIVE.lower(), f"{NONE}."])
def test_k5_only_a_whole_line_equal_to_the_text_counts(tmp_path, line):
    log = _start(tmp_path, after=f"{line}\n")
    assert _uf().hotplug_note(log, RUN) is _probe().UNKNOWN


def test_k4_no_log_or_an_unreadable_log_is_unknown(tmp_path):
    uf, P = _uf(), _probe()
    assert uf.hotplug_note(tmp_path / "absent.log", RUN) is P.UNKNOWN
    log = _start(tmp_path, after=f"{ACTIVE}\n")
    log.unlink()
    log.mkdir()
    assert uf.hotplug_note(log, RUN) is P.UNKNOWN


# ---- the record decides which segment is this start's (K-7) ----------------------------------------

def test_k7_no_running_daemon_is_unknown(tmp_path):
    log = _start(tmp_path, after=f"{ACTIVE}\n")
    assert _uf().hotplug_note(log, None) is _probe().UNKNOWN


@pytest.mark.parametrize("other", [(4322, 123456, BOOT), (4321, 123457, BOOT),
                                   (4321, 123456, "ffffffff-4f50-6172-8394-a5b6c7d8e9f0")],
                         ids=["pid", "start-time", "boot-id"])
def test_k7_a_record_of_another_process_is_unknown(tmp_path, other):
    log = _start(tmp_path, after=f"{ACTIVE}\n")
    assert _uf().hotplug_note(log, other) is _probe().UNKNOWN


@pytest.mark.parametrize("record", ["", "garbage\n", f"5 4321 123456 {BOOT}", f"+5 4321 123456 {BOOT}\n",
                                    f"5 4321 123456 {BOOT}\nextra\n"])
def test_k7_a_record_not_of_the_form_is_unknown(tmp_path, record):
    log = _start(tmp_path, after=f"{ACTIVE}\n")
    (tmp_path / (log.name + ".offset")).write_text(record, encoding="ascii")
    assert _uf().hotplug_note(log, RUN) is _probe().UNKNOWN


def test_k7_an_absent_record_is_unknown(tmp_path):
    log = _start(tmp_path, after=f"{ACTIVE}\n")
    os.unlink(tmp_path / (log.name + ".offset"))
    assert _uf().hotplug_note(log, RUN) is _probe().UNKNOWN


def test_k7_an_offset_beyond_the_log_is_unknown(tmp_path):
    """The maintenance cap truncated the log during this run: its offset no longer fits."""
    log = _start(tmp_path, before="x" * 100, after=f"{ACTIVE}\n")
    log.write_text(f"{ACTIVE}\n", encoding="utf-8")
    assert _uf().hotplug_note(log, RUN) is _probe().UNKNOWN


# ---- rows the B5 mutation sweep showed open (B39, B43) --------------------------------------------------

def test_offset_record_refuses_a_boot_id_of_a_str_subclass():
    class Text(str):
        pass
    with pytest.raises(ValueError):
        _uf().offset_record(1, 1, 1, Text(BOOT))


@pytest.mark.parametrize("line", ["", "garbage\n", f"0 1 2 {BOOT}", None, b"0 1 2 x\n"])
def test_write_offset_record_of_a_line_not_of_the_form_is_unknown_and_writes_nothing(tmp_path, line):
    log = tmp_path / "start.log"
    log.write_text("", encoding="utf-8")
    assert _uf().write_offset_record(log, line) is _probe().UNKNOWN
    assert not (tmp_path / "start.log.offset").exists()

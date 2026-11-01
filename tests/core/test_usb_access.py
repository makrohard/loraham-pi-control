"""The USB radio host library (lhpc/core/usb_access.py): the sysfs scan and its match against the model table,
the start pre-check data of a serial-less row, the node access check, the doctor's host-line data, the udev
and modprobe texts and the x86 install data. Every case runs on the fake sysfs tree
tests/data/usb_sysfs/meshstick (a root hub, a hub, the Meshstick 1a86:5512 on port 1-1.3 with no product and
no serial file, its interface bound to spi-ch341, spi_ch341 loaded) copied into tmp_path; no case reads the
real /sys or /dev."""
from __future__ import annotations

import ast
import dataclasses
import errno
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Hashable
from pathlib import Path

import pytest
import repo_paths

from lhpc.core import radio_models
from lhpc.core import usb_access as ua
from lhpc.core.usb_access import Probe

FIXTURE = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"
MODELS = radio_models.load()
STICK = MODELS["meshstick-1262"]
DEVICES = ("bus", "usb", "devices")

# The external-type axis (rule 3): one value per class, as file bytes for sysfs and as Python values for calls.
CONTENTS = {
    "bool": b"true\n", "int": b"6790\n", "float": b"1.5\n", "str": b"abcd\n", "null": b"", "list": b"[1, 2]\n",
    "object": b'{"a": 1}\n', "deep": b"[" * 64 + b"]" * 64, "duplicate": b"1a86\n1a86\n",
    "utf16": "1a86".encode("utf-16"), "bom": "\ufeff1a86".encode(), "nul": b"1a86\x00", "latin1": b"\xff\xfe",
    "upper": b"1A86\n", "lead-space": b" 1a86\n", "crlf": b"1a86\r\n", "short": b"1a8\n", "long": b"01a86\n",
    "zero": b"0\n", "two-newlines": b"1a86\n\n", "dotted": b"1.3\n", "number": b"5\n", "valid-hex": b"1a86\n",
}
VALUES = {"bool": True, "int": 1, "float": 1.5, "str": "x", "null": None, "list": ["plugdev"],
          "object": {"plugdev": 1}, "deep": [[[[["plugdev"]]]]], "duplicate": ("plugdev", "plugdev"),
          "bytes": b"plugdev", "bom": "\ufeffplugdev"}
class Text(str):
    """A str subclass: equal to a str, but not of type str (strict type checks refuse it)."""


# The grammar of each identity file (the design table's type axis), held here independently of the library.
GRAMMAR = {"idVendor": r"[0-9a-f]{4}\n?", "idProduct": r"[0-9a-f]{4}\n?", "busnum": r"[1-9][0-9]{0,2}\n?",
           "devpath": r"[0-9]+(\.[0-9]+)*\n?"}


@pytest.fixture
def root(tmp_path):
    tree = tmp_path / "sys"
    shutil.copytree(FIXTURE, tree, symlinks=True)
    return tree


@pytest.fixture
def inject(monkeypatch):
    """Make the library's reads of chosen paths fail with an errno (EACCES, EIO) without a permission change:
    Path.read_bytes, os.listdir, os.stat and os.readlink raise for those paths and pass every other path on."""
    failing = {}

    def wrap(fn):
        def call(path, *args, **kwargs):
            err = failing.get(os.fspath(path))
            if err is not None:
                raise OSError(err, os.strerror(err), os.fspath(path))
            return fn(path, *args, **kwargs)
        return call
    for name in ("listdir", "stat", "readlink"):
        monkeypatch.setattr(os, name, wrap(getattr(os, name)))
    monkeypatch.setattr(Path, "read_bytes", wrap(Path.read_bytes))

    def fail(path, err):
        failing[os.fspath(path)] = err
    return fail


ERRNOS = pytest.mark.parametrize("err", [errno.EACCES, errno.EIO], ids=["eacces", "eio"])


def dev(root, port):
    return root.joinpath(*DEVICES, port)


def device(root, port):
    return next(d for d in ua.scan(root).devices if d.port == port)


def model(**usb):
    return dataclasses.replace(STICK, usb=usb)


def unreadable(path):
    """Make `path` unreadable for root too: a directory in its place (EISDIR)."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()
    path.mkdir()


def loop(path):
    """A symlink loop in place of `path` (ELOOP)."""
    if path.exists() or path.is_symlink():
        path.unlink()
    os.symlink(path.name, path)


def add_stick(root, port, *, serial=None):
    shutil.copytree(dev(root, "1-1.3"), dev(root, port))
    (dev(root, port) / "devpath").write_text(port.split("-", 1)[1] + "\n")
    if serial is not None:
        (dev(root, port) / "serial").write_text(serial + "\n")


# --- scan and match (U1, U2, U8; T-U1, T-X9b) ---

def test_scan_reads_device_entries_and_skips_interface_entries(root):
    """T-U1: every device entry with its fields; the `<port>:<config>.<if>` entries are not devices."""
    found = ua.scan(root)
    assert found.listing is Probe.PRESENT
    assert [d.port for d in found.devices] == ["1-1", "1-1.3", "usb1"]
    stick = device(root, "1-1.3")
    assert (stick.vid, stick.pid, stick.busnum, stick.devpath, stick.devnum) == (
        ua.Field(Probe.PRESENT, "1a86"), ua.Field(Probe.PRESENT, "5512"), ua.Field(Probe.PRESENT, "1"),
        ua.Field(Probe.PRESENT, "1.3"), ua.Field(Probe.PRESENT, "5"))
    assert (stick.product, stick.serial, stick.identity) == (ua.Field(Probe.ABSENT), ua.Field(Probe.ABSENT),
                                                             Probe.PRESENT)
    assert device(root, "usb1").serial == ua.Field(Probe.PRESENT, "0000:01:00.0")


def test_a_productless_device_matches_its_row_by_vid_and_pid(root):
    """T-X9b: the shipped Meshstick row carries no product; the stick (no product file) matches by vid + pid,
    every other device does not."""
    assert "product" not in STICK.usb
    assert {d.port: ua.match(d, STICK) for d in ua.scan(root).devices} == {
        "1-1": Probe.ABSENT, "1-1.3": Probe.PRESENT, "usb1": Probe.ABSENT}


@pytest.mark.parametrize("row_product, file, expected", [
    ("Meshstick", b"Meshstick\n", Probe.PRESENT),
    ("Meshstick", b"Meshstick 2\n", Probe.ABSENT),          # differs: not matched
    ("Meshstick", b"meshstick\n", Probe.ABSENT),            # exact match, case counts
    ("Meshstick", None, Probe.ABSENT),                      # no product file on a row that needs one
    ("Meshstick", "unreadable", Probe.UNKNOWN),             # U8 proposal: uncertain, never "not matched"
    ("", "unreadable", Probe.PRESENT),                      # a row without product never reads it
    ("", b"Anything\n", Probe.PRESENT),
])
def test_the_product_is_filtered_only_where_the_row_carries_one(root, row_product, file, expected):
    """T-X9b, U8."""
    path = dev(root, "1-1.3") / "product"
    if file == "unreadable":
        unreadable(path)
    elif file is not None:
        path.write_bytes(file)
    row = model(vid="1a86", pid="5512", **({"product": row_product} if row_product else {}))
    assert ua.match(device(root, "1-1.3"), row) is expected


@pytest.mark.parametrize("field", ["idVendor", "idProduct", "busnum", "devpath"])
@pytest.mark.parametrize("break_it", [lambda p: p.unlink(), unreadable, loop], ids=["missing", "eisdir", "eloop"])
def test_an_unreadable_identity_field_keeps_the_device_as_unknown(root, field, break_it):
    """U1: the device stays in the scan with its port, identity UNKNOWN, match UNKNOWN: never absent."""
    break_it(dev(root, "1-1.3") / field)
    stick = device(root, "1-1.3")
    assert getattr(stick, {"idVendor": "vid", "idProduct": "pid"}.get(field, field)).state is Probe.UNKNOWN
    assert (stick.identity, ua.match(stick, STICK)) == (Probe.UNKNOWN, Probe.UNKNOWN)


@pytest.mark.needs_nonroot
def test_an_identity_field_without_read_permission_is_unknown(root):
    """U1 with EACCES (the chmod case; root reads through it, so it runs as a normal user)."""
    (dev(root, "1-1.3") / "idVendor").chmod(0)
    assert ua.match(device(root, "1-1.3"), STICK) is Probe.UNKNOWN


@pytest.mark.parametrize("field", sorted(GRAMMAR))
@pytest.mark.parametrize("kind", sorted(CONTENTS))
def test_identity_field_contents_outside_the_grammar_are_unknown(root, field, kind):
    """Rule 3 over every identity file: a value outside its grammar is UNKNOWN and never matches or is absent."""
    (dev(root, "1-1.3") / field).write_bytes(CONTENTS[kind])
    stick = device(root, "1-1.3")
    try:
        text = CONTENTS[kind].decode()
    except UnicodeDecodeError:
        text = None
    valid = text is not None and re.fullmatch(GRAMMAR[field], text) is not None
    assert stick.identity is (Probe.PRESENT if valid else Probe.UNKNOWN)
    if not valid:
        assert ua.match(stick, STICK) is Probe.UNKNOWN


@pytest.mark.parametrize("kind", sorted(CONTENTS))
def test_product_and_serial_contents_are_exact_text_or_unknown(root, kind):
    """Rule 3 over the free-text files: valid UTF-8, one line, no NUL is PRESENT as read; all else UNKNOWN;
    a product row matches only its exact string."""
    raw = CONTENTS[kind]
    for name in ("product", "serial"):
        (dev(root, "1-1.3") / name).write_bytes(raw)
    stick = device(root, "1-1.3")
    try:
        text = raw.decode()
        text = text.removesuffix("\n")
        ok = bool(text) and "\n" not in text and "\x00" not in text
    except UnicodeDecodeError:
        ok = False
    want = ua.Field(Probe.PRESENT, text) if ok else ua.Field(Probe.UNKNOWN)
    assert (stick.product, stick.serial) == (want, want)
    assert ua.match(stick, model(vid="1a86", pid="5512", product="1a86")) is (
        Probe.PRESENT if ok and text == "1a86" else Probe.ABSENT if ok else Probe.UNKNOWN)


@pytest.mark.parametrize("break_it", ["missing", "file", "eisdir-in-place"])
def test_an_unreadable_listing_is_unknown_not_empty(root, break_it):
    """U1: no listing is never "no stick"."""
    base = root.joinpath(*DEVICES)
    shutil.rmtree(base)
    if break_it == "file":
        base.write_text("x")
    elif break_it == "eisdir-in-place":
        loop(base)
    assert ua.scan(root) == ua.Scan(Probe.UNKNOWN, ())


def test_a_device_entry_that_is_a_broken_link_is_kept_as_unknown(root):
    """U1: a device entry pointing nowhere (as real entries are links into /sys/devices)."""
    os.symlink("../../../devices/gone", dev(root, "1-1.4"))
    gone = device(root, "1-1.4")
    assert (gone.identity, ua.match(gone, STICK)) == (Probe.UNKNOWN, Probe.UNKNOWN)


@pytest.mark.parametrize("key, kind", [(k, v) for k in ("vid", "pid", "product") for v in sorted(VALUES)
                                       if not (k == "product" and type(VALUES[v]) is str)])   # a str line is a product
def test_a_model_usb_value_of_the_wrong_type_or_form_is_refused(root, key, kind):
    """Rule 2 on the model row: vid/pid four lower-case hex digits, product one non-empty line; else a refusal
    naming the row, for the rule text and the match alike."""
    usb = {"vid": "1a86", "pid": "5512", key: VALUES[kind]}
    with pytest.raises(ua.UsbTableError, match="meshstick-1262"):
        ua.match(device(root, "1-1.3"), model(**usb))
    with pytest.raises(ua.UsbTableError, match="meshstick-1262"):
        ua.udev_rules_text({"m": model(**usb)})


@pytest.mark.parametrize("usb", [{"vid": "1A86", "pid": "5512"}, {"vid": "1a8", "pid": "5512"},
                                 {"vid": "1a86", "pid": "55120"}, {"vid": "1a86"}, {"vid": "1a86", "pid": "5512",
                                 "serial": "x"}, {"vid": "1a86", "pid": "5512", "product": "a\nb"}])
def test_a_model_usb_row_outside_the_form_is_refused(root, usb):
    with pytest.raises(ua.UsbTableError):
        ua.match(device(root, "1-1.3"), model(**usb))


@pytest.mark.parametrize("kind", sorted(VALUES))
def test_a_model_usb_value_that_is_not_a_table_is_refused(root, kind):
    with pytest.raises(ua.UsbTableError, match="meshstick-1262"):
        ua.match(device(root, "1-1.3"), dataclasses.replace(STICK, usb=VALUES[kind]))


@pytest.mark.parametrize("vid, pid", [("1a86", "7523"), ("1a87", "5512"), ("5512", "1a86")])
def test_a_device_differing_in_vid_or_pid_is_not_the_stick(root, vid, pid):
    """A:343: the same vendor's other bridge (1a86:7523) or a swapped pair is not matched."""
    (dev(root, "1-1.3") / "idVendor").write_text(vid + "\n")
    (dev(root, "1-1.3") / "idProduct").write_text(pid + "\n")
    assert ua.match(device(root, "1-1.3"), STICK) is Probe.ABSENT
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3").reason is ua.Reason.NO_STICK_ON_PORT


def test_an_spi_model_is_not_a_usb_model(root):
    with pytest.raises(ua.UsbTableError, match="not a USB model"):
        ua.match(device(root, "1-1.3"), MODELS["loraham"])


# --- start pre-check of a serial-less row (U1, U2, U3, U5) ---

@pytest.mark.parametrize("serial, state", [
    ("absent", Probe.ABSENT), ("eisdir", Probe.UNKNOWN), ("eacces", Probe.UNKNOWN), ("eio", Probe.UNKNOWN),
    ("empty", Probe.UNKNOWN), ("newline-only", Probe.UNKNOWN), ("latin1", Probe.UNKNOWN), ("nul", Probe.UNKNOWN),
])
def test_a_serialless_stick_on_its_port_proceeds_to_the_daemon(root, inject, serial, state):
    """U2, A9: a serial file that is absent (ABSENT) or unreadable (UNKNOWN) is no serial reported; the start is
    passed on to the daemon's check, never refused on that alone."""
    path = dev(root, "1-1.3") / "serial"
    if serial == "eisdir":
        unreadable(path)
    elif serial in ("eacces", "eio"):
        path.write_text("A1B2C3D4\n")
        inject(path, errno.EACCES if serial == "eacces" else errno.EIO)
    elif serial != "absent":
        path.write_bytes({"empty": b"", "newline-only": b"\n", "latin1": b"\xff\xfe\n", "nul": b"A1\x00\n"}[serial])
    assert device(root, "1-1.3").serial == ua.Field(state)
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3") == ua.Precheck(
        ua.Outcome.PROCEED, ua.Reason.STICK_ON_PORT, ("1-1.3",))
    assert ua.stick_on_port(ua.scan(root), STICK, "1-1.3") is Probe.PRESENT


@pytest.mark.parametrize("serial", ["A1B2C3D4E5", "A1B2C3D4E5\n", " \n", "\t\n", "x", "Zähler-1\n"])
def test_a_stick_on_a_serialless_rows_port_that_reports_a_serial_is_refused(root, serial):
    """A §2.11 F2 ("must exist and report no serial"), decision B4-D12: refused naming the port and the serial
    seen, so the row is re-bound by serial; never PROCEED, never re-bound here. Whitespace is a serial too."""
    (dev(root, "1-1.3") / "serial").write_text(serial)
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3") == ua.Precheck(
        ua.Outcome.REFUSE, ua.Reason.SERIAL_ON_SERIALLESS_ROW, ("1-1.3",), serial.removesuffix("\n"))


def test_a_serial_on_the_port_wins_over_a_second_serialless_stick(root):
    """B-7 before B-4: the row's own port is judged first."""
    (dev(root, "1-1.3") / "serial").write_text("A1B2C3D4\n")
    add_stick(root, "1-1.4")
    add_stick(root, "1-1.5")
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3").reason is ua.Reason.SERIAL_ON_SERIALLESS_ROW


@pytest.mark.parametrize("port", ["1-1.4", "1-1", "2-1"])
def test_no_stick_on_the_stored_port_refuses_the_start(root, port):
    """U5: nothing on the port, or a different device there, refuses naming the port."""
    assert ua.stick_on_port(ua.scan(root), STICK, port) is Probe.ABSENT
    assert ua.precheck_serialless(ua.scan(root), STICK, port) == ua.Precheck(
        ua.Outcome.REFUSE, ua.Reason.NO_STICK_ON_PORT, (port,))


@pytest.mark.parametrize("second", [None, "unreadable"])
def test_a_second_stick_without_sysfs_serial_refuses_the_start_naming_both(root, second):
    """U3: a second attached stick of the model without a sysfs serial (missing or unreadable)."""
    add_stick(root, "1-1.4")
    if second == "unreadable":
        unreadable(dev(root, "1-1.4") / "serial")
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3") == ua.Precheck(
        ua.Outcome.REFUSE, ua.Reason.SECOND_SERIALLESS, ("1-1.3", "1-1.4"))


def test_a_second_stick_with_a_serial_does_not_refuse(root):
    """U3 control: the other stick reports a serial, so it is not a second serial-less one."""
    add_stick(root, "1-1.4", serial="A1B2C3D4E5")
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3").outcome is ua.Outcome.PROCEED


@pytest.mark.parametrize("field", sorted(GRAMMAR))
def test_an_unreadable_device_makes_the_precheck_uncertain_naming_it(root, field):
    """U1 proposal: an unreadable device may be a second stick; the pre-check names it as uncertain."""
    add_stick(root, "1-1.4")
    unreadable(dev(root, "1-1.4") / field)
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3") == ua.Precheck(
        ua.Outcome.UNCERTAIN, ua.Reason.UNCERTAIN_DEVICE, ("1-1.4",))


def test_an_unreadable_device_on_the_stored_port_is_uncertain_not_refused(root):
    unreadable(dev(root, "1-1.3") / "idVendor")
    assert ua.stick_on_port(ua.scan(root), STICK, "1-1.3") is Probe.UNKNOWN
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3") == ua.Precheck(
        ua.Outcome.UNCERTAIN, ua.Reason.UNCERTAIN_DEVICE, ("1-1.3",))


def test_an_unknown_listing_makes_the_precheck_uncertain(root):
    shutil.rmtree(root.joinpath(*DEVICES))
    assert ua.stick_on_port(ua.scan(root), STICK, "1-1.3") is Probe.UNKNOWN
    assert ua.precheck_serialless(ua.scan(root), STICK, "1-1.3") == ua.Precheck(
        ua.Outcome.UNCERTAIN, ua.Reason.LISTING_UNKNOWN, ())


@pytest.mark.parametrize("port", [*VALUES.values(), "", "1-1.3:1.0", "../1-1.3", "usb1", "1-1.3/..", "0-1",
                                  "1-1.3\n", " 1-1.3", "１-1.3"])
def test_a_port_outside_the_form_is_uncertain_and_never_looked_up(root, port):
    assert ua.stick_on_port(ua.scan(root), STICK, port) is Probe.UNKNOWN
    assert ua.precheck_serialless(ua.scan(root), STICK, port) == ua.Precheck(
        ua.Outcome.UNCERTAIN, ua.Reason.BAD_PORT, ())


# --- access (U6) ---

def node_stat(mode, uid=0, gid=46):
    return os.stat_result((mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))


NAMES = {46: "plugdev", 0: "root"}


def access(root, st, groups=frozenset({"plugdev", "op"}), euid=1000):
    seen = []

    def stat_node(path):
        seen.append(path)
        if isinstance(st, BaseException):
            raise st
        return st

    def group_name(gid):
        return NAMES[gid]
    got = ua.node_access(device(root, "1-1.3"), groups, euid, dev_root=root / "dev", stat_node=stat_node,
                         group_name=group_name)
    return got, seen


CHR = 0o020000


@pytest.mark.parametrize("st, groups, euid, expected", [
    (node_stat(CHR | 0o660), frozenset({"plugdev"}), 1000, Probe.PRESENT),          # the rule's node
    (node_stat(CHR | 0o660), frozenset({"op"}), 1000, Probe.ABSENT),                # plugdev pending
    (node_stat(CHR | 0o664, gid=0), frozenset({"op"}), 1000, Probe.ABSENT),         # no rule: read-only
    (node_stat(CHR | 0o666, gid=0), frozenset({"op"}), 1000, Probe.PRESENT),        # world rw
    (node_stat(CHR | 0o460, uid=1000), frozenset({"plugdev"}), 1000, Probe.ABSENT),  # owner bits decide
    (node_stat(CHR | 0o640), frozenset({"plugdev"}), 1000, Probe.ABSENT),           # group read only
    (node_stat(CHR | 0o620), frozenset({"plugdev"}), 1000, Probe.ABSENT),           # group write only
    (node_stat(CHR | 0o600), frozenset(), 0, Probe.PRESENT),                         # root owns it
    (node_stat(0o100000 | 0o666), frozenset({"plugdev"}), 1000, Probe.UNKNOWN),     # not a char device
    (node_stat(CHR | 0o660, gid=4242), frozenset({"plugdev"}), 1000, Probe.UNKNOWN),  # gid without a name
    (FileNotFoundError(2, "gone"), frozenset({"plugdev"}), 1000, Probe.UNKNOWN),
    (PermissionError(13, "denied"), frozenset({"plugdev"}), 1000, Probe.UNKNOWN),
])
def test_node_access_from_mode_and_group_vs_effective_groups(root, st, groups, euid, expected):
    """U6: "no access" is ABSENT; a node that cannot be judged is UNKNOWN."""
    got, seen = access(root, st, groups, euid)
    assert got is expected
    assert seen == [root / "dev" / "bus" / "usb" / "001" / "005"]


@pytest.mark.parametrize("kind", sorted(VALUES))
def test_node_access_with_groups_or_euid_of_the_wrong_type_is_unknown(root, kind):
    st = node_stat(CHR | 0o666)
    assert access(root, st, groups=VALUES[kind])[0] is Probe.UNKNOWN
    if type(VALUES[kind]) is not int:
        assert access(root, st, euid=VALUES[kind])[0] is Probe.UNKNOWN
    if isinstance(VALUES[kind], Hashable):
        assert access(root, st, groups=frozenset({"plugdev", VALUES[kind]}))[0] is (
            Probe.PRESENT if type(VALUES[kind]) is str else Probe.UNKNOWN)


@pytest.mark.parametrize("field", ["devnum", "busnum"])
def test_node_access_without_the_node_numbers_is_unknown(root, field):
    unreadable(dev(root, "1-1.3") / field)
    assert access(root, node_stat(CHR | 0o666)) == (Probe.UNKNOWN, [])


# --- text generators (B1-B3, U7) ---

RULE = ('SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5512", MODE="0660", GROUP="plugdev"\n')
HEADER = "# Written by LHPC's bootstrap from its radio model table; a re-run replaces it.\n"


def test_the_udev_rule_has_one_line_per_usb_id_of_the_table():
    assert ua.udev_rules_text(MODELS) == HEADER + RULE


def test_a_new_usb_row_adds_its_line_and_a_repeated_id_does_not():
    other = dataclasses.replace(STICK, id="other", usb={"vid": "0403", "pid": "6014", "product": "X"})
    twin = dataclasses.replace(STICK, id="twin")
    assert ua.udev_rules_text({**MODELS, "twin": twin, "other": other}) == HEADER + RULE + (
        'SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6014", MODE="0660", GROUP="plugdev"\n')


def test_the_modprobe_text_blacklists_spi_ch341():
    """B2: the text the bootstrap prints before it writes."""
    assert ua.modprobe_text() == ("# Written by LHPC's bootstrap for its USB radios; applies to the whole host.\n"
                                  "blacklist spi_ch341\n")


def test_the_host_setup_is_the_one_generator_of_files_commands_and_group():
    """B3/W10: the script and the copybox read this one value."""
    assert ua.host_setup(MODELS) == ua.HostSetup(
        (("/etc/udev/rules.d/90-lhpc-usb-radio.rules", HEADER + RULE),
         ("/etc/modprobe.d/lhpc-usb-radio.conf", ua.modprobe_text())),
        (("udevadm", "control", "--reload-rules"), ("udevadm", "trigger", "--subsystem-match=usb", "--action=add"),
         ("modprobe", "-r", "spi_ch341")), "plugdev")


# --- doctor-line data (B4-B10; T-U2..T-U4) ---

def installed(tmp_path, text):
    path = tmp_path / "etc" / "udev" / "rules.d" / "90-lhpc-usb-radio.rules"
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_bytes(text)
    return path


@pytest.mark.parametrize("text, expected", [
    ((HEADER + RULE).encode(), ua.RuleState.CURRENT),
    (None, ua.RuleState.MISSING),
    (HEADER.encode(), ua.RuleState.STALE),                       # bootstrap ran before the row existed
    ((HEADER + RULE[:30]).encode(), ua.RuleState.STALE),         # a partial file
    ((HEADER + RULE + RULE).encode(), ua.RuleState.STALE),
    (b"", ua.RuleState.STALE),
])
def test_the_rule_is_current_missing_or_stale(tmp_path, text, expected):
    """T-U3, B4: each of the three states."""
    installed(tmp_path, text)
    assert ua.rule_state(MODELS, tmp_path) is expected


def test_the_rule_turns_stale_when_a_pin_bump_adds_a_usb_row(tmp_path):
    """T-U3, U7 (F24): stale until the bootstrap re-runs."""
    installed(tmp_path, ua.udev_rules_text(MODELS).encode())
    grown = {**MODELS, "other": dataclasses.replace(STICK, id="other", usb={"vid": "0403", "pid": "6014"})}
    assert (ua.rule_state(MODELS, tmp_path), ua.rule_state(grown, tmp_path)) == (
        ua.RuleState.CURRENT, ua.RuleState.STALE)
    installed(tmp_path, ua.udev_rules_text(grown).encode())
    assert ua.rule_state(grown, tmp_path) is ua.RuleState.CURRENT


@pytest.mark.parametrize("break_it", [unreadable, loop], ids=["eisdir", "eloop"])
def test_an_unreadable_rule_file_is_unknown(tmp_path, break_it):
    break_it(installed(tmp_path, b"x"))
    assert ua.rule_state(MODELS, tmp_path) is ua.RuleState.UNKNOWN


def test_spi_ch341_loaded_not_loaded_unknown(root):
    """T-U2, B5 (module): /sys/module/spi_ch341 present / absent; /sys/module itself unreadable is unknown."""
    assert ua.ch341_module(root) is Probe.PRESENT
    shutil.rmtree(root / "module" / "spi_ch341")
    assert ua.ch341_module(root) is Probe.ABSENT
    (root / "module" / "spi_ch341").write_text("x")
    assert ua.ch341_module(root) is Probe.UNKNOWN
    shutil.rmtree(root / "module")
    assert ua.ch341_module(root) is Probe.UNKNOWN
    (root / "module").write_text("x")
    assert ua.ch341_module(root) is Probe.UNKNOWN


def iface(root):
    return dev(root, "1-1.3:1.0")


@pytest.mark.parametrize("change, expected", [
    (lambda p: None, ua.Binding(Probe.PRESENT, "spi-ch341")),
    (lambda p: (p / "driver").unlink(), ua.Binding(Probe.ABSENT)),
    (lambda p: ((p / "driver").unlink(), os.symlink("../../../../bus/usb/drivers/usbfs", p / "driver")),
     ua.Binding(Probe.ABSENT, "usbfs")),
    (lambda p: ((p / "driver").unlink(), os.symlink("../../drivers/spi-ch341/", p / "driver")),
     ua.Binding(Probe.PRESENT, "spi-ch341")),
    (lambda p: ((p / "driver").unlink(), (p / "driver").write_text("spi-ch341")), ua.Binding(Probe.UNKNOWN)),
    (shutil.rmtree, ua.Binding(Probe.UNKNOWN)),
    (lambda p: (shutil.rmtree(p), p.write_text("x")), ua.Binding(Probe.UNKNOWN)),
    (lambda p: (shutil.rmtree(p), loop(p)), ua.Binding(Probe.UNKNOWN)),
], ids=["bound", "no-driver", "other-driver", "trailing-slash", "driver-not-a-link", "iface-absent",
        "iface-a-file", "iface-eloop"])
def test_the_spi_ch341_binding_of_the_interface(root, change, expected):
    """T-U4, B5 (interface): the driver link names spi-ch341 = bound; a readable interface without one = not
    bound; an absent or unreadable interface = unknown."""
    change(iface(root))
    assert ua.ch341_binding("1-1.3", root) == expected


@pytest.mark.parametrize("port", [*VALUES.values(), "", "../../../module/spi_ch341", "1-1.3:1.0", "usb1"])
def test_the_binding_of_a_port_outside_the_form_is_unknown(root, port):
    if type(port) is str and port:
        shutil.copytree(iface(root), dev(root, f"{port}:1.0"), symlinks=True)   # a bound interface by that name
    assert ua.ch341_binding(port, root) == ua.Binding(Probe.UNKNOWN)


@pytest.mark.parametrize("effective, configured, expected", [
    (frozenset({"op", "plugdev"}), frozenset({"op", "plugdev"}), ua.Membership.MEMBER),
    (frozenset({"op", "plugdev"}), frozenset({"op"}), ua.Membership.MEMBER),
    (frozenset({"op"}), frozenset({"op", "plugdev"}), ua.Membership.PENDING),
    (frozenset({"op"}), frozenset({"op"}), ua.Membership.ABSENT),
    ({"plugdev"}, set(), ua.Membership.MEMBER),
    (frozenset({"Plugdev"}), frozenset({"plugdev "}), ua.Membership.ABSENT),
])
def test_plugdev_member_pending_or_not(effective, configured, expected):
    """T-U2, B6."""
    assert ua.plugdev(effective, configured) is expected


@pytest.mark.parametrize("kind", sorted(VALUES))
def test_plugdev_with_groups_of_the_wrong_type_is_unknown(kind):
    bad = VALUES[kind]
    assert ua.plugdev(bad, frozenset({"plugdev"})) is ua.Membership.UNKNOWN
    assert ua.plugdev(frozenset({"plugdev"}), bad) is ua.Membership.UNKNOWN
    if isinstance(bad, Hashable):
        assert ua.plugdev(frozenset({"plugdev", bad}), frozenset()) is (
            ua.Membership.MEMBER if type(bad) is str else ua.Membership.UNKNOWN)


@pytest.mark.parametrize("note", [*Probe])
def test_libusb_hotplug_is_the_daemons_note(note):
    """T-U2, B7 (T-D6 is the daemon side)."""
    assert ua.hotplug(note) is note


@pytest.mark.parametrize("note", [*VALUES.values(), "present", "absent", ua.RuleState.CURRENT,
                                  ua.Membership.MEMBER])
def test_libusb_hotplug_without_a_note_is_unknown(note):
    assert ua.hotplug(note) is Probe.UNKNOWN


def test_libusb_present_absent_unknown():
    """B10: found and loads / not found and the soname does not load / found and does not load."""
    loaded = []

    def load_ok(name):
        loaded.append(name)

    def load_fails(name):
        raise OSError(f"{name}: cannot open shared object file")
    assert ua.libusb(lambda n: "libusb-1.0.so.0", load_ok) == ua.Library(Probe.PRESENT, "libusb-1.0.so.0")
    assert ua.libusb(lambda n: None, load_ok) == ua.Library(Probe.PRESENT, "libusb-1.0.so.0")
    assert ua.libusb(lambda n: None, load_fails) == ua.Library(
        Probe.ABSENT, "libusb-1.0.so.0: cannot open shared object file")
    assert ua.libusb(lambda n: "libusb-1.0.so.9", load_fails) == ua.Library(
        Probe.UNKNOWN, "libusb-1.0.so.9: cannot open shared object file")
    assert loaded == ["libusb-1.0.so.0", "libusb-1.0.so.0"]


def test_libusb_lookup_failure_is_unknown():
    def find(name):
        raise OSError("ldconfig failed")
    assert ua.libusb(find, lambda n: None) == ua.Library(Probe.UNKNOWN, "ldconfig failed")


@pytest.mark.parametrize("transports, expected", [
    (["usb-ch341"], ua.LineGroups(Probe.PRESENT, Probe.ABSENT)),
    (["spi", "usb-ch341"], ua.LineGroups(Probe.PRESENT, Probe.PRESENT)),
    (["spi", "spi"], ua.LineGroups(Probe.ABSENT, Probe.PRESENT)),
    ([], ua.LineGroups(Probe.ABSENT, Probe.ABSENT)),                       # B8: no stick, no USB lines
    (("usb-ch341",), ua.LineGroups(Probe.PRESENT, Probe.ABSENT)),
])
def test_usb_lines_only_with_a_usb_radio_and_spi_lines_only_with_an_spi_radio(transports, expected):
    """T-U2, B8, B9."""
    assert ua.line_groups(transports) == expected


@pytest.mark.parametrize("transports", [*VALUES.values(), "spi", ["USB-CH341"], ["usb"], [None], [["spi"]],
                                        ["spi", 1]])
def test_line_groups_of_an_unknown_transport_are_unknown(transports):
    assert ua.line_groups(transports) == ua.LineGroups(Probe.UNKNOWN, Probe.UNKNOWN)


# --- install differences (X1, X2) ---

@pytest.mark.parametrize("text, expected", [
    ('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\nVERSION_ID="13"\n', Probe.PRESENT),
    ("ID=ubuntu\nID_LIKE=debian\nVERSION_ID='24.04'\n", Probe.PRESENT),
    ("ID=fedora\nVERSION_ID=41\n", Probe.ABSENT),
    ("ID=debian\nVERSION_ID=12\n", Probe.ABSENT),
    ("ID=ubuntu\nVERSION_ID=24.10\n", Probe.ABSENT),
    ("ID=Debian\nVERSION_ID=13\n", Probe.ABSENT),
    ("ID=debian\n", Probe.UNKNOWN),
    ("", Probe.UNKNOWN),
    ("ID=debian\nVERSION_ID=13\nID=fedora\n", Probe.UNKNOWN),          # duplicate key
    ("ID=debian\nVERSION_ID=13\nVERSION_ID=13\n", Probe.UNKNOWN),
])
def test_x86_distro_in_the_supported_set(text, expected):
    """X1 proposal: REFUSE outside SUPPORTED_X86 before any write; the library gives the value."""
    assert ua.x86_distro(text) is expected


@pytest.mark.parametrize("kind", [k for k in sorted(VALUES) if type(VALUES[k]) is not str])
def test_x86_distro_of_the_wrong_type_is_unknown(kind):
    assert ua.x86_distro(VALUES[kind]) is Probe.UNKNOWN


@pytest.mark.parametrize("machine, expected", [
    ("x86_64", Probe.ABSENT), ("amd64", Probe.ABSENT), ("aarch64", Probe.PRESENT), ("arm64", Probe.PRESENT),
    ("armv7l", Probe.PRESENT), ("armv6l", Probe.PRESENT), ("riscv64", Probe.UNKNOWN), ("X86_64", Probe.UNKNOWN),
    ("", Probe.UNKNOWN), *[(v, Probe.UNKNOWN) for v in VALUES.values() if v != "x"],
    (Text("aarch64"), Probe.UNKNOWN), (Text("x86_64"), Probe.UNKNOWN),
])
def test_the_spi_gpio_grant_applies_on_a_pi_and_not_on_x86(machine, expected):
    """X2 proposal: PROCEED without the spi,gpio grant on x86."""
    assert ua.spi_gpio_grant(machine) is expected


# --- injected read errors (Table G) ---

def _identity(root, fail, err):
    fail(dev(root, "1-1.3") / "idVendor", err)
    stick = device(root, "1-1.3")
    return stick.vid.state, stick.identity, ua.match(stick, STICK)


def _product(root, fail, err):
    (dev(root, "1-1.3") / "product").write_text("Meshstick\n")
    fail(dev(root, "1-1.3") / "product", err)
    stick = device(root, "1-1.3")
    return stick.product.state, ua.match(stick, model(vid="1a86", pid="5512", product="Meshstick"))


def _serial(root, fail, err):
    (dev(root, "1-1.3") / "serial").write_text("A1B2C3D4\n")
    fail(dev(root, "1-1.3") / "serial", err)
    return device(root, "1-1.3").serial.state, ua.precheck_serialless(ua.scan(root), STICK, "1-1.3").outcome


def _listing(root, fail, err):
    fail(root.joinpath(*DEVICES), err)
    return ua.scan(root).listing, ua.stick_on_port(ua.scan(root), STICK, "1-1.3")


def _node(root, fail, err):
    def stat_node(path):
        raise OSError(err, os.strerror(err), os.fspath(path))
    return (ua.node_access(device(root, "1-1.3"), frozenset({"plugdev"}), 1000, dev_root=root / "dev",
                           stat_node=stat_node),)


def _rule(root, fail, err):
    path = installed(root, ua.udev_rules_text(MODELS).encode())
    fail(path, err)
    return (ua.rule_state(MODELS, root),)


def _module(root, fail, err):
    fail(root / "module" / "spi_ch341", err)
    return (ua.ch341_module(root),)


def _module_dir(root, fail, err):
    shutil.rmtree(root / "module" / "spi_ch341")
    fail(root / "module", err)
    return (ua.ch341_module(root),)


def _iface(root, fail, err):
    fail(iface(root), err)
    return (ua.ch341_binding("1-1.3", root),)


def _driver(root, fail, err):
    fail(iface(root) / "driver", err)
    return (ua.ch341_binding("1-1.3", root),)


U = Probe.UNKNOWN


@ERRNOS
@pytest.mark.parametrize("read, expected", [
    (_identity, (U, U, U)), (_product, (U, U)), (_serial, (U, ua.Outcome.PROCEED)), (_listing, (U, U)),
    (_node, (U,)), (_rule, (ua.RuleState.UNKNOWN,)), (_module, (U,)), (_module_dir, (U,)),
    (_iface, (ua.Binding(U),)), (_driver, (ua.Binding(U),)),
], ids=lambda v: v.__name__.strip("_") if callable(v) else "")
def test_an_injected_read_error_is_unknown_never_absent(root, inject, err, read, expected):
    """Table G, U1, U6: EACCES or EIO on each read the library makes, injected (no permission change), is
    UNKNOWN; it is never ABSENT, never "not matched", never "no access"."""
    assert read(root, inject, err) == expected


# --- no caller yet ---

def test_no_existing_module_imports_usb_access():  # rule-1 exception
    """A whole-package negative invariant no driven path proves: no module under lhpc/ imports
    lhpc.core.usb_access until the stage that wires it. Behavioural twin: the next test. A control: it
    passes on the parent too, which has no such module."""
    importers = []
    for path in (repo_paths.REPO / "lhpc").rglob("*.py"):
        if path.name == "usb_access.py" and path.parent.name == "core":
            continue
        for node in ast.walk(ast.parse(path.read_bytes())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [f"{node.module}.{a.name}" for a in node.names] if isinstance(node, ast.ImportFrom)
                     and node.module else [])
            if any("usb_access" in n for n in names):
                importers.append(str(path.relative_to(repo_paths.REPO)))
    assert importers == []


def test_loading_the_cli_web_and_bootstrap_modules_does_not_load_usb_access():
    """A control, like the previous case: loading the CLI, the service, the bootstrap renderer and the
    backend probes leaves lhpc.core.usb_access unloaded."""
    code = ("import sys, lhpc.adapters.cli.main, lhpc.core.services, lhpc.core.deps, lhpc.core.probes.backends; "
            "print(sorted(m for m in sys.modules if m.endswith('usb_access')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=repo_paths.REPO, capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "[]"

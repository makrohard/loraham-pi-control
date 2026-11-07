"""USB radios on the host: the udev rule text from the radio model table, the sysfs scan of
the sticks, the device-node access check and the data of the doctor's host lines. Pure functions over an
injectable root (sysfs defaults to /sys); unused until the USB bootstrap and doctor (stage L7).

Every probe returns an explicit `Probe` (PRESENT / ABSENT / UNKNOWN), never a bool, None or an empty
container, and an unreadable value is UNKNOWN, never ABSENT: only a missing `product` or `serial` file is
an absence (the Meshstick's bridge reports no product string). A device whose identity cannot be read is
kept in the scan with its port, so it is never counted as absent.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import enum
import grp
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from lhpc.core.radio_models import TRANSPORTS

RULES_PATH = "/etc/udev/rules.d/90-lhpc-usb-radio.rules"
GROUP = "plugdev"
CH341_MODULE = "spi_ch341"
CH341_DRIVER = "spi-ch341"
USB_TRANSPORT = "usb-ch341"
LIBUSB_SONAME = "libusb-1.0.so.0"
# The x86 install set (A:440); the Pi's set is decided elsewhere.
SUPPORTED_X86 = (("debian", "13"), ("ubuntu", "24.04"))
X86_MACHINES = ("x86_64", "amd64")
PI_MACHINES = ("aarch64", "arm64", "armv7l", "armv6l")
# No module blacklist and no `modprobe -r` (W226): the daemon's CH341 library detaches the kernel driver of a stick
# it opens (at libpinedio b0694ec8: of every 1a86:5512 it visits while it searches for its serial), so no host-wide
# blacklist is needed and CH341 bridges of other ids keep their driver.
RELOAD_COMMANDS = (("udevadm", "control", "--reload-rules"),
                   ("udevadm", "trigger", "--subsystem-match=usb", "--action=add"))

_HEX4 = re.compile(r"[0-9a-f]{4}")
_NUM = re.compile(r"[1-9][0-9]{0,2}")
_DEVPATH = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_PORT = re.compile(r"[1-9][0-9]*-[0-9]+(?:\.[0-9]+)*")


class Probe(enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class RuleState(enum.Enum):
    CURRENT = "current"
    MISSING = "missing"
    STALE = "stale"
    UNKNOWN = "unknown"


class Membership(enum.Enum):
    MEMBER = "member"
    PENDING = "pending"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class Outcome(enum.Enum):
    PROCEED = "proceed"
    REFUSE = "refuse"
    UNCERTAIN = "uncertain"      # an unreadable device may be a second stick: the caller defers or refuses


class Reason(enum.Enum):
    STICK_ON_PORT = "stick-on-port"
    BAD_PORT = "bad-port"
    LISTING_UNKNOWN = "listing-unknown"
    NO_STICK_ON_PORT = "no-stick-on-port"
    SERIAL_ON_SERIALLESS_ROW = "serial-on-serialless-row"
    SECOND_SERIALLESS = "second-serialless"
    UNCERTAIN_DEVICE = "uncertain-device"


class UsbTableError(ValueError):
    """A model row's `usb` values are outside the form the scan and the udev rule need; names the row."""


@dataclass(frozen=True)
class Field:
    state: Probe
    value: str = ""          # the text read; "" unless state is PRESENT


@dataclass(frozen=True)
class UsbDevice:
    port: str                # the sysfs entry name, e.g. "1-1.3"
    vid: Field
    pid: Field
    busnum: Field
    devpath: Field
    devnum: Field
    product: Field
    serial: Field

    @property
    def identity(self) -> Probe:
        """PRESENT when idVendor, idProduct, busnum and devpath all read; else UNKNOWN (never ABSENT)."""
        fields = (self.vid, self.pid, self.busnum, self.devpath)
        return Probe.PRESENT if all(f.state is Probe.PRESENT for f in fields) else Probe.UNKNOWN


@dataclass(frozen=True)
class Scan:
    listing: Probe           # PRESENT: the devices directory was read; UNKNOWN: it was not
    devices: tuple


@dataclass(frozen=True)
class Precheck:
    outcome: Outcome
    reason: Reason
    ports: tuple
    serial: str = ""         # SERIAL_ON_SERIALLESS_ROW: the sysfs serial the stick on the port reports


@dataclass(frozen=True)
class Binding:
    state: Probe             # PRESENT: spi-ch341 holds the interface; ABSENT: no driver or another one
    driver: str = ""


@dataclass(frozen=True)
class Library:
    state: Probe
    detail: str              # the loaded name, or the loader's error text


@dataclass(frozen=True)
class LineGroups:
    usb: Probe               # the USB host lines apply
    spi: Probe               # the SPI CS lines apply


@dataclass(frozen=True)
class HostSetup:
    files: tuple             # ((path, text), ...)
    commands: tuple          # argv tuples, run after the files
    group: str               # the group the operator joins


def _usb_ids(model) -> tuple:
    """(vid, pid, product or "") of a USB model row; UsbTableError for any other value."""
    where = f"model {getattr(model, 'id', '?')!r}"
    usb = getattr(model, "usb", None)
    if type(usb) is not dict:
        raise UsbTableError(f"{where}: not a USB model")
    if set(usb) - {"vid", "pid", "product"}:
        raise UsbTableError(f"{where}: usb holds a key other than vid, pid, product")
    for key in ("vid", "pid"):
        if type(usb.get(key)) is not str or not _HEX4.fullmatch(usb[key]):
            raise UsbTableError(f"{where}: usb {key} must be four lower-case hex digits")
    product = usb.get("product", "")
    if "product" in usb and (type(product) is not str or not _text_ok(product)):
        raise UsbTableError(f"{where}: usb product must be one non-empty line")
    return usb["vid"], usb["pid"], product


def _text_ok(text: str) -> bool:
    return bool(text) and "\n" not in text and "\x00" not in text


def _read(path: Path, pattern=None, *, missing: Probe = Probe.UNKNOWN) -> Field:
    """One sysfs attribute: `missing` when the file does not exist, UNKNOWN when it cannot be read or
    decoded or does not match `pattern` (one trailing newline is the kernel's and is dropped)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Field(missing)
    except OSError:
        return Field(Probe.UNKNOWN)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Field(Probe.UNKNOWN)
    text = text.removesuffix("\n")
    if not _text_ok(text) or (pattern is not None and not pattern.fullmatch(text)):
        return Field(Probe.UNKNOWN)
    return Field(Probe.PRESENT, text)


def _device(entry: Path) -> UsbDevice:
    return UsbDevice(entry.name, _read(entry / "idVendor", _HEX4), _read(entry / "idProduct", _HEX4),
                     _read(entry / "busnum", _NUM), _read(entry / "devpath", _DEVPATH),
                     _read(entry / "devnum", _NUM), _read(entry / "product", missing=Probe.ABSENT),
                     _read(entry / "serial", missing=Probe.ABSENT))


def scan(sys_root: Path | str = "/sys") -> Scan:
    """Every USB device entry of `<sys_root>/bus/usb/devices` (interface entries, whose names hold a `:`,
    are skipped), sorted by port. No descriptor is read from the device itself."""
    base = Path(sys_root) / "bus" / "usb" / "devices"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return Scan(Probe.UNKNOWN, ())
    return Scan(Probe.PRESENT, tuple(_device(base / name) for name in names if ":" not in name))


def match(device: UsbDevice, model) -> Probe:
    """Is `device` a stick of the USB `model`: vid + pid, and the product only where the row carries one.
    UNKNOWN when the identity, or the product a row asks for, cannot be read."""
    vid, pid, product = _usb_ids(model)
    if device.identity is not Probe.PRESENT:
        return Probe.UNKNOWN
    if (device.vid.value, device.pid.value) != (vid, pid):
        return Probe.ABSENT
    if not product:
        return Probe.PRESENT
    if device.product.state is Probe.UNKNOWN:
        return Probe.UNKNOWN
    return Probe.PRESENT if device.product == Field(Probe.PRESENT, product) else Probe.ABSENT


def _port_ok(port) -> bool:
    return type(port) is str and bool(_PORT.fullmatch(port))


def stick_on_port(found: Scan, model, port) -> Probe:
    """A stick of `model` on the stored `port`: PRESENT, ABSENT (nothing there, or a different device),
    UNKNOWN (the port, the listing or the device there cannot be judged)."""
    _usb_ids(model)
    if not _port_ok(port) or found.listing is not Probe.PRESENT:
        return Probe.UNKNOWN
    here = [d for d in found.devices if d.port == port]
    return match(here[0], model) if here else Probe.ABSENT


def precheck_serialless(found: Scan, model, port) -> Precheck:
    """The start pre-check of a serial-less radio row on its stored `port` (A:177-178): REFUSE when no stick
    of the model is there, when the stick there reports a sysfs serial (the row must be re-bound by that
    serial; nothing re-binds it here), or when a second stick without a sysfs serial is attached, naming the
    ports; UNCERTAIN when a device cannot be read (it may be a second stick); else PROCEED to the daemon's
    own check. A serial file that is absent or unreadable is no serial reported."""
    on_port = stick_on_port(found, model, port)
    if not _port_ok(port):
        return Precheck(Outcome.UNCERTAIN, Reason.BAD_PORT, ())
    if found.listing is not Probe.PRESENT:
        return Precheck(Outcome.UNCERTAIN, Reason.LISTING_UNKNOWN, ())
    if on_port is Probe.ABSENT:
        return Precheck(Outcome.REFUSE, Reason.NO_STICK_ON_PORT, (port,))
    here = next(d for d in found.devices if d.port == port)
    if on_port is Probe.PRESENT and here.serial.state is Probe.PRESENT:
        return Precheck(Outcome.REFUSE, Reason.SERIAL_ON_SERIALLESS_ROW, (port,), here.serial.value)
    judged = [(d, match(d, model)) for d in found.devices]
    serialless = tuple(d.port for d, m in judged if m is Probe.PRESENT and d.serial.state is not Probe.PRESENT)
    if len(serialless) > 1:
        return Precheck(Outcome.REFUSE, Reason.SECOND_SERIALLESS, serialless)
    uncertain = tuple(d.port for d, m in judged if m is Probe.UNKNOWN)
    if uncertain:
        return Precheck(Outcome.UNCERTAIN, Reason.UNCERTAIN_DEVICE, uncertain)
    return Precheck(Outcome.PROCEED, Reason.STICK_ON_PORT, (port,))


def _groups_ok(groups) -> bool:
    return type(groups) in (frozenset, set) and all(type(g) is str for g in groups)


def _group_name(gid: int) -> str:
    return grp.getgrgid(gid).gr_name


def node_access(device: UsbDevice, effective_groups, euid, *, dev_root: Path | str = "/dev",
                stat_node=os.stat, group_name=_group_name) -> Probe:
    """Read and write access to the device's node `<dev_root>/bus/usb/BBB/DDD` from its mode and group vs
    the effective group names (POSIX order: owner bits, else group bits, else other bits). PRESENT, ABSENT
    ("no access"), or UNKNOWN when the node, its group or an input cannot be judged."""
    if not _groups_ok(effective_groups) or type(euid) is not int or euid < 0:
        return Probe.UNKNOWN
    if device.identity is not Probe.PRESENT or device.devnum.state is not Probe.PRESENT:
        return Probe.UNKNOWN
    node = Path(dev_root) / "bus" / "usb" / f"{int(device.busnum.value):03d}" / f"{int(device.devnum.value):03d}"
    try:
        st = stat_node(node)
    except OSError:
        return Probe.UNKNOWN
    if not stat.S_ISCHR(st.st_mode):
        return Probe.UNKNOWN
    if st.st_uid == euid:
        bits = st.st_mode >> 6
    else:
        try:
            name = group_name(st.st_gid)
        except (KeyError, OSError):
            return Probe.UNKNOWN
        if type(name) is not str:
            return Probe.UNKNOWN
        bits = st.st_mode >> 3 if name in effective_groups else st.st_mode
    return Probe.PRESENT if bits & 0o6 == 0o6 else Probe.ABSENT


def udev_rules_text(models: dict) -> str:
    """The udev rule file: `MODE="0660", GROUP="plugdev"` for each distinct vid:pid of the USB rows."""
    lines, seen = [], set()
    for model in models.values():
        if getattr(model, "transport", None) != USB_TRANSPORT:
            continue
        vid, pid, _ = _usb_ids(model)
        if (vid, pid) not in seen:
            seen.add((vid, pid))
            lines.append(f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vid}", ATTR{{idProduct}}=="{pid}", '
                         f'MODE="0660", GROUP="{GROUP}"')
    return "# Written by LHPC's bootstrap from its radio model table; a re-run replaces it.\n" + \
        "".join(line + "\n" for line in lines)


def host_setup(models: dict) -> HostSetup:
    """The one source of the bootstrap's USB step and of the copybox: the udev rule file, the udev reload and
    trigger, and the group."""
    return HostSetup(((RULES_PATH, udev_rules_text(models)),), RELOAD_COMMANDS, GROUP)


def setup_needed(rule: RuleState, member: Membership) -> bool:
    """The USB host setup still has to run: the rule is not current, or plugdev is neither held nor granted."""
    return not (rule is RuleState.CURRENT and member in (Membership.MEMBER, Membership.PENDING))


def rule_state(models: dict, etc_root: Path | str = "/") -> RuleState:
    """The installed udev rule vs the text the table renders now: CURRENT, MISSING, STALE (a row added since
    the bootstrap ran, or an edited or partial file) or UNKNOWN (unreadable)."""
    want = udev_rules_text(models).encode()
    try:
        have = (Path(etc_root) / RULES_PATH.lstrip("/")).read_bytes()
    except FileNotFoundError:
        return RuleState.MISSING
    except OSError:
        return RuleState.UNKNOWN
    return RuleState.CURRENT if have == want else RuleState.STALE


def ch341_module(sys_root: Path | str = "/sys") -> Probe:
    """`spi_ch341` loaded: PRESENT (`/sys/module/spi_ch341` is a directory), ABSENT (it does not exist),
    UNKNOWN (`/sys/module` itself unreadable, or anything else)."""
    modules = Path(sys_root) / "module"
    try:
        st = os.stat(modules / CH341_MODULE)
    except FileNotFoundError:
        return _absent_if_dir(modules)
    except OSError:
        return Probe.UNKNOWN
    return Probe.PRESENT if stat.S_ISDIR(st.st_mode) else Probe.UNKNOWN


def _absent_if_dir(parent: Path) -> Probe:
    """A name missing from a readable directory is ABSENT; a missing or unreadable directory is UNKNOWN."""
    try:
        st = os.stat(parent)
    except OSError:
        return Probe.UNKNOWN
    return Probe.ABSENT if stat.S_ISDIR(st.st_mode) else Probe.UNKNOWN


def ch341_binding(port, sys_root: Path | str = "/sys") -> Binding:
    """Who holds interface `<port>:1.0`: PRESENT (spi-ch341), ABSENT (no driver, or another one, named),
    UNKNOWN (bad port, the interface directory absent or unreadable, `driver` not a link)."""
    if not _port_ok(port):
        return Binding(Probe.UNKNOWN)
    iface = Path(sys_root) / "bus" / "usb" / "devices" / f"{port}:1.0"
    try:
        os.listdir(iface)
    except OSError:
        return Binding(Probe.UNKNOWN)
    try:
        target = os.readlink(iface / "driver")
    except FileNotFoundError:
        return Binding(Probe.ABSENT)
    except OSError:
        return Binding(Probe.UNKNOWN)
    driver = os.path.basename(target.rstrip("/"))
    return Binding(Probe.PRESENT if driver == CH341_DRIVER else Probe.ABSENT, driver)


def plugdev(effective_groups, configured_groups) -> Membership:
    """MEMBER (the running process has the group), PENDING (granted, not yet effective), ABSENT, UNKNOWN."""
    if not (_groups_ok(effective_groups) and _groups_ok(configured_groups)):
        return Membership.UNKNOWN
    if GROUP in effective_groups:
        return Membership.MEMBER
    return Membership.PENDING if GROUP in configured_groups else Membership.ABSENT


def hotplug(note) -> Probe:
    """libusb hotplug as the daemon's startup note states it; UNKNOWN when no note is available."""
    return note if type(note) is Probe else Probe.UNKNOWN


def libusb(find=ctypes.util.find_library, load=ctypes.CDLL) -> Library:
    """libusb-1.0 loads through ctypes as radio_detect loads it: PRESENT, ABSENT (not found and the soname
    does not load), UNKNOWN (found but does not load, or the lookup failed), with the loader's text."""
    try:
        found = find("usb-1.0")
    except OSError as e:
        return Library(Probe.UNKNOWN, str(e))
    name = found if type(found) is str and found else LIBUSB_SONAME
    try:
        load(name)
    except OSError as e:
        return Library(Probe.UNKNOWN if name == found else Probe.ABSENT, str(e))
    return Library(Probe.PRESENT, name)


def line_groups(transports) -> LineGroups:
    """Which doctor host lines apply for the configured radios' transports: USB lines only with a USB radio,
    SPI CS lines only with an SPI radio; UNKNOWN for both on a transport outside the table's set."""
    try:
        seen = tuple(transports)
    except TypeError:
        return LineGroups(Probe.UNKNOWN, Probe.UNKNOWN)
    if not all(type(t) is str and t in TRANSPORTS for t in seen):
        return LineGroups(Probe.UNKNOWN, Probe.UNKNOWN)
    return LineGroups(Probe.PRESENT if USB_TRANSPORT in seen else Probe.ABSENT,
                      Probe.PRESENT if "spi" in seen else Probe.ABSENT)


def x86_distro(os_release) -> Probe:
    """An x86 install's distribution (the text of /etc/os-release) is in SUPPORTED_X86: PRESENT, ABSENT,
    or UNKNOWN when ID or VERSION_ID is missing or given twice."""
    if type(os_release) is not str:
        return Probe.UNKNOWN
    seen = {}
    for line in os_release.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("ID", "VERSION_ID"):
            if key in seen:
                return Probe.UNKNOWN
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            seen[key] = value
    if len(seen) != 2:
        return Probe.UNKNOWN
    return Probe.PRESENT if (seen["ID"], seen["VERSION_ID"]) in SUPPORTED_X86 else Probe.ABSENT


def spi_gpio_grant(machine) -> Probe:
    """The bootstrap's spi,gpio grant applies: PRESENT on a Pi architecture, ABSENT on x86 (no SPI radios
    there), UNKNOWN on any other value."""
    if type(machine) is str and machine in PI_MACHINES:
        return Probe.PRESENT
    if type(machine) is str and machine in X86_MACHINES:
        return Probe.ABSENT
    return Probe.UNKNOWN

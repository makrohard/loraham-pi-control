"""The consumers of the USB library (`usb_access`): the start pre-check, the bootstrap's USB block and its
copybox, the doctor's USB host lines, the hotplug note of a start's log segment and the Hardware section's USB
choices and bind (INTERFACE-B5-W1 a7f4b2dd, with the binding answers of INTERFACE-B5-W1-NOTE-1).

Every decision here is pure: it reads no file and runs no command unless its docstring says so. The service
and web wiring that calls it stays outside. Tri-state answers are `usb_access.Probe`; UNKNOWN is never taken
for ABSENT.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lhpc.core import radio_daemon, radios
from lhpc.core.usb_access import (
    CH341_MODULE,
    USB_TRANSPORT,
    Binding,
    Library,
    Membership,
    Outcome,
    Probe,
    Reason,
    RuleState,
    Scan,
    UsbTableError,
    host_setup,
    line_groups,
    match,
    precheck_serialless,
    stick_on_port,
)

USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SCAFFOLD_USER = "$OP"
BLOCK_EXIT = 13                 # the USB block's own file or command failure (B5-D2)
GROUPS_EXIT_LINE = '[ -z "$_GROUPS_FAILED" ] || exit 7'      # NOTE-2: rc 0 on success, 7 on a failed grant
SETUP_FILE = ("state", "usb-setup", "usb-setup.sh")
BOUND_CAP = 64                  # bytes of input bound_text writes
OFFSET_RE = re.compile(r"([0-9]+) ([0-9]+) ([0-9]+) ([0-9a-f-]{36})\n")
BOOT_ID_RE = re.compile(r"[0-9a-f-]{36}")
HOTPLUG_ACTIVE = "[CH341] unplug-wait active: a lost stick is waited for (libusb hotplug)"
HOTPLUG_NONE = "[CH341] note: libusb offers no usable hotplug here; a lost stick exits 5 as before"
_COPYBOX_PATH_RE = re.compile(r"[A-Za-z0-9._/+-]+")    # a path the copybox line can quote as it stands
_ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]*")


# ---- records ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    outcome: str                # "proceed" | "refuse"
    reason: str = ""
    ports: tuple = ()
    copybox: bool = False


@dataclass(frozen=True)
class Copybox:
    state: str                  # "ready" | "refused"
    path: str
    sha256: str
    line: str
    reason: str


@dataclass(frozen=True)
class UsbProbes:
    """The doctor's probes, each bound by the caller to LIB's function with its paths and groups."""
    scan: object
    rule_state: object
    ch341_module: object
    ch341_binding: object
    plugdev: object
    libusb: object
    node_access: object
    hotplug_for: object


@dataclass(frozen=True)
class Presence:
    state: Probe
    port: str = ""
    ports: tuple = ()
    note: str = ""


@dataclass(frozen=True)
class Choice:
    port: str
    serial: str
    selectable: bool
    label: str
    why: str


@dataclass(frozen=True)
class Choices:
    listing: Probe
    reason: str
    items: tuple


@dataclass(frozen=True)
class Bind:
    outcome: str                # "proceed" | "refuse"
    radios: tuple
    reason: str
    copybox: bool


def _proceed(reason="", ports=(), copybox=False) -> Verdict:
    return Verdict("proceed", reason, tuple(ports), copybox)


def _refuse(reason, ports=(), copybox=False) -> Verdict:
    return Verdict("refuse", reason, tuple(ports), copybox)


# ---- Table P: the start pre-check ---------------------------------------------------------------------

def bound_text(serial: str) -> str:
    """The serial as BOUND writes it: each byte outside 0x21-0x7E, and `%`, as `%XX`; at most 64 input bytes."""
    data = serial.encode("utf-8")[:BOUND_CAP]
    return "".join(chr(b) if 0x21 <= b <= 0x7E and b != 0x25 else f"%{b:02X}" for b in data)


def _node_text(device) -> str:
    try:
        return f"/dev/bus/usb/{int(device.busnum.value):03d}/{int(device.devnum.value):03d}"
    except ValueError:
        return f"the device node of port {device.port}"


def _precheck_verdict(pc, port) -> Verdict:
    """LIB's REFUSE and UNCERTAIN outcomes as a refusal."""
    ports = ", ".join(pc.ports)
    texts = {
        Reason.NO_STICK_ON_PORT: f"no stick on port {port}: plug the stick into port {port}",
        Reason.SECOND_SERIALLESS: f"more than one stick without a serial is attached (ports {ports}): "
                                  "unplug all but one",
        Reason.SERIAL_ON_SERIALLESS_ROW: f"the stick on port {port} has serial {bound_text(pc.serial)}; "
                                         "re-bind it by serial in the Hardware section",
        Reason.LISTING_UNKNOWN: "USB devices cannot be listed",
        Reason.BAD_PORT: f"the stored port {port} is not a port",
        Reason.UNCERTAIN_DEVICE: f"a USB device cannot be read (ports {ports}); it may be a second stick",
    }
    return _refuse(texts.get(pc.reason, f"USB pre-check: {pc.reason.value}"), pc.ports)


def start_precheck(radio, model, scan_fn, access_fn) -> Verdict:
    """The verdict on starting `radio` (Table P). Never raises."""
    try:
        transport = getattr(model, "transport", None)
        if transport == "spi":
            return _proceed()
        if transport != USB_TRANSPORT:
            return _refuse(f"unknown transport on {radio.id}")
        if radio.usb_serial:
            return _proceed()
        port = radio.usb_port
        found = scan_fn()
        pc = precheck_serialless(found, model, port)
        if pc.outcome is not Outcome.PROCEED:
            return _precheck_verdict(pc, port)
        device = next(d for d in found.devices if d.port == port)
        access = access_fn(device)
        if type(access) is not Probe:
            return _refuse(f"USB pre-check failed: the access check gave {access!r}, not a Probe")
        if access is Probe.ABSENT:
            return _refuse(f"no access to {_node_text(device)}", (port,), copybox=True)
        return _proceed(ports=(port,))          # PRESENT, or UNKNOWN: the daemon decides (P11)
    except UsbTableError as exc:
        return _refuse(f"the model table's row {getattr(model, 'id', '?')} is not usable: {exc}")
    except Exception as exc:
        return _refuse(f"USB pre-check failed: {exc}")


# ---- Table B / C: the bootstrap's USB block and the copybox ---------------------------------------------

_WRITE_FN = (
    "_lhpc_usb_write() {    # <dir> <path> <text>: a temp file in <dir>, mode 0644, renamed into place",
    '\t_lhpc_t=$(mktemp "$1/.lhpc-usb.XXXXXX") || return 1',
    '\tif printf \'%s\' "$3" > "$_lhpc_t" && chmod 0644 "$_lhpc_t" && mv -f "$_lhpc_t" "$2"; then',
    "\t\treturn 0",
    "\tfi",
    '\trm -f "$_lhpc_t"',
    "\treturn 1",
    "}",
)


def _account_ok(user) -> bool:
    return type(user) is str and bool(USER_RE.fullmatch(user)) and user != "root"


def usb_setup_text(user, models, *, root="/", standalone=False, root_env=None) -> str:
    """The USB block, built on `host_setup(models)`: the udev rule file, the udevadm reload and trigger, and the
    plugdev grant; no module blacklist (W226). `user` is "$OP" (the scaffold) or a literal account name, else
    ValueError. `root_env` (the bootstrap's form, "$OP" only) names a shell variable that prefixes every path at run
    time instead of `root`, and runs udevadm only on a running system (`$SYSTEMD_RUNDIR` exists): on an image build
    udev reads the rule at the first boot. Its own failures exit 13; a failed grant sets `_GROUPS_FAILED`, which only
    the standalone block turns into exit 7."""
    if user != SCAFFOLD_USER and not _account_ok(user):
        raise ValueError(f"{user!r} is not a usable account name")
    if root_env is not None and (user != SCAFFOLD_USER or not _ENV_NAME_RE.fullmatch(root_env)):
        raise ValueError(f"root_env {root_env!r} needs the scaffold user and a shell variable name")
    setup = host_setup(models)
    q = shlex.quote
    base = str(root).rstrip("/")
    prefix = base if root_env is None else f"${{{root_env}}}"     # the path's prefix as an echo shows it

    def sh(path):                                                 # the path as one shell word
        return q(base + path) if root_env is None else f'"${{{root_env}}}"{q(path)}'

    def shown(path):
        return prefix + path
    out = ["# --- USB radios: udev rule and plugdev grant (written by LHPC) ------------------------------",
           *_WRITE_FN]
    for path, text in setup.files:
        out += [f"if ! _lhpc_usb_write {sh(os.path.dirname(path))} {sh(path)} {q(text)}; then",
                f'\techo "ERROR: [usb-setup] could not write {shown(path)}" >&2',
                f"\texit {BLOCK_EXIT}",
                "fi",
                f'echo "[usb-setup] wrote {shown(path)}"']
    run = []
    for argv in setup.commands:
        cmd = " ".join(argv)
        run += [f"if ! {cmd}; then",
                f'\techo "ERROR: [usb-setup] {cmd} failed" >&2',
                f"\texit {BLOCK_EXIT}",
                "fi"]
    if root_env is None:
        out += run
    else:
        out += ['if [ -d "$SYSTEMD_RUNDIR" ]; then', *("\t" + line for line in run), "else",
                '\techo "[usb-setup] no running system (an image build): udev reads the rule at the first boot."',
                "fi"]
    out += [f'if usermod -aG {setup.group} "{user}"; then',
            f'\techo "[usb-setup] granted {setup.group} to {user}; it takes effect at the next log-in."',
            "else",
            f'\techo "ERROR: [usb-setup] could not grant {setup.group} to {user}; the USB radios stay out of '
            f'reach until it is granted." >&2',
            "\t_GROUPS_FAILED=1",
            "fi"]
    if standalone:
        out.append(GROUPS_EXIT_LINE)
    return "\n".join(out) + "\n"


def _copybox_refused(reason, path="") -> Copybox:
    return Copybox("refused", path, "", "", reason)


def _write_atomic(target: Path, data: bytes) -> None:
    """`data` to a temp file in the target's directory, mode 0644, fsync, renamed into place; OSError on
    failure, with the temp file removed."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        try:
            os.fchmod(fd, 0o644)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def copybox(paths, user, models) -> Copybox:
    """Write the standalone block to `<runtime>/state/usb-setup/usb-setup.sh` and give the one copybox line that
    checks its sha256 before it runs it. A refusal carries an empty line. Never raises for the file system."""
    if not _account_ok(user):
        return _copybox_refused(f"{user} is not a usable account name")
    try:
        text = usb_setup_text(user, models, standalone=True)
    except ValueError as exc:
        return _copybox_refused(f"the USB setup cannot be rendered: {exc}")
    try:
        target = paths.under(*SETUP_FILE)
    except ValueError as exc:            # PathContainmentError: outside the runtime root
        return _copybox_refused(f"the setup file has no safe path: {exc}")
    path = str(target)
    if not _COPYBOX_PATH_RE.fullmatch(path):
        return _copybox_refused(f"the setup file's path {path!r} cannot be quoted in the copybox line")
    data = text.encode("utf-8")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not paths.contains(target):
            return _copybox_refused(f"the setup file {path} lies outside the runtime root")
        _write_atomic(target, data)
    except OSError as exc:
        return _copybox_refused(f"{path} could not be written ({type(exc).__name__}: {exc})")
    sha = hashlib.sha256(data).hexdigest()
    body = (f't=$(mktemp) && cp "{path}" "$t" && echo "{sha}  $t" | sha256sum -c - && bash "$t"; '
            f'rc=$?; rm -f "$t"; exit "$rc"')
    return Copybox("ready", path, sha, f"sudo sh -c '{body}'", "")


# ---- Table D: the doctor's USB host lines -------------------------------------------------------------

DOCTOR_TEXTS = {
    "unknown_transport": "unknown transport on {rid}",
    "probe_error": "{what}: unknown ({error})",
    "no_usb": "USB: no USB radio in the hardware list",
    "rule_current": "USB udev rule: current",
    "rule_missing": "USB udev rule: missing (run the copybox)",
    "rule_stale": "USB udev rule: stale, the model table changed since it was written (run the copybox)",
    "rule_unknown": "USB udev rule: cannot be read",
    "module_present": f"{CH341_MODULE} kernel module: loaded (the daemon detaches it at start)",
    "module_absent": f"{CH341_MODULE} kernel module: not loaded",
    "module_unknown": f"{CH341_MODULE} kernel module: cannot be judged (/sys/module unreadable)",
    "plugdev_member": "group plugdev: the controller has it",
    "plugdev_pending": "group plugdev: granted, effective after the next log-in or restart",
    "plugdev_absent": "group plugdev: not granted (run the copybox)",
    "plugdev_unknown": "group plugdev: the groups cannot be read",
    "libusb_present": "libusb: loaded ({detail})",
    "libusb_absent": "libusb: missing ({detail}); detect is off",
    "libusb_unknown": "libusb: cannot be judged ({detail}); detect is off",
    "listing_unknown": "USB devices: the device list cannot be read",
    "device_unreadable": "port {port}: a device that cannot be read (it may be a stick)",
    "binding_present": f"port {{port}}: the kernel driver {CH341_MODULE} holds the stick (the daemon detaches it "
                       "at start)",
    "binding_absent": "port {port}: no kernel driver holds the stick",
    "binding_other": "port {port}: the kernel driver {driver} holds the stick",
    "binding_unknown": "port {port}: the stick's kernel driver cannot be judged",
    "access_present": "port {port}: the device node is readable and writable",
    "access_absent": "port {port}: no access to the device node (run the copybox)",
    "access_unknown": "port {port}: the device node's access cannot be judged",
    "presence_present": "{rid}: stick present on port {port}",
    "presence_present_note": "{rid}: stick present on port {port} ({note})",
    "presence_absent": "{rid}: stick not found",
    "presence_unknown": "{rid}: stick presence cannot be judged ({note})",
    "hotplug_present": "{rid}: libusb hotplug active (a lost stick is waited for)",
    "hotplug_absent": "{rid}: no libusb hotplug (a lost stick exits 5)",
    "hotplug_unknown": "{rid}: hotplug not known (no note from this start)",
}

_RULE_KEYS = {RuleState.CURRENT: "rule_current", RuleState.MISSING: "rule_missing", RuleState.STALE: "rule_stale",
              RuleState.UNKNOWN: "rule_unknown"}
_PLUGDEV_KEYS = {Membership.MEMBER: "plugdev_member", Membership.PENDING: "plugdev_pending",
                 Membership.ABSENT: "plugdev_absent", Membership.UNKNOWN: "plugdev_unknown"}


def _text(key, **fields) -> str:
    return DOCTOR_TEXTS[key].format(**fields)


def _ask(what, fn, *args, kind):
    """(value, None) from one probe, or (None, its error line) when it raises or returns no `kind`."""
    try:
        value = fn(*args)
    except Exception as exc:
        return None, _text("probe_error", what=what, error=exc)
    if not isinstance(value, kind):
        return None, _text("probe_error", what=what, error=f"not a {kind.__name__}: {value!r}")
    return value, None


def _probe_key(prefix, state) -> str:
    return f"{prefix}_{state.name.lower()}"


def _presence_line(rid, p) -> str:
    if p.state is Probe.PRESENT:
        if p.note:
            return _text("presence_present_note", rid=rid, port=p.port, note=p.note)
        return _text("presence_present", rid=rid, port=p.port)
    if p.state is Probe.ABSENT:
        return _text("presence_absent", rid=rid)
    return _text("presence_unknown", rid=rid, note=p.note or "not judged")


def _stick_lines(found, models_used, probes) -> list:
    """D-3 binding and D-7 node access per stick of a configured model, and each unreadable device."""
    out = []
    for device in found.devices:
        judged = [match(device, m) for m in models_used]
        if Probe.PRESENT not in judged:
            if Probe.UNKNOWN in judged:
                out.append(_text("device_unreadable", port=device.port))
            continue
        binding, err = _ask(f"port {device.port}: kernel driver", probes.ch341_binding, device.port, kind=Binding)
        if err:
            out.append(err)
        elif binding.state is Probe.ABSENT and binding.driver:
            out.append(_text("binding_other", port=device.port, driver=binding.driver))
        else:
            out.append(_text(_probe_key("binding", binding.state), port=device.port))
        access, err = _ask(f"port {device.port}: device node access", probes.node_access, device, kind=Probe)
        out.append(err or _text(_probe_key("access", access), port=device.port))
    return out


def doctor_usb_lines(rows, models, probes) -> list:
    """The doctor's USB host lines for the hardware list's rows, enabled or not, each one of DOCTOR_TEXTS. A probe
    that raises reads "unknown (<error>)" and the rest still print. Never raises."""
    try:
        return _doctor_lines(rows, models, probes)
    except Exception as exc:
        return [_text("probe_error", what="USB doctor", error=exc)]


def _doctor_lines(rows, models, probes) -> list:
    out, usb_rows, transports = [], [], []
    for row in rows:
        model = models.get(row.model)
        if model is None or line_groups((model.transport,)).usb is Probe.UNKNOWN:
            out.append(_text("unknown_transport", rid=row.id))
            continue
        transports.append(model.transport)
        if model.transport == USB_TRANSPORT:
            usb_rows.append((row, model))
    if line_groups(transports).usb is not Probe.PRESENT:
        return [*out, _text("no_usb")]
    rule, err = _ask("USB udev rule", probes.rule_state, kind=RuleState)
    out.append(err or _text(_RULE_KEYS[rule]))
    module, err = _ask(f"{CH341_MODULE} kernel module", probes.ch341_module, kind=Probe)
    out.append(err or _text(_probe_key("module", module)))
    member, err = _ask("group plugdev", probes.plugdev, kind=Membership)
    out.append(err or _text(_PLUGDEV_KEYS[member]))
    library, err = _ask("libusb", probes.libusb, kind=Library)
    out.append(err or _text(_probe_key("libusb", library.state), detail=library.detail))
    found, scan_err = _ask("USB devices", probes.scan, kind=Scan)
    if scan_err:
        out.append(scan_err)
    elif found.listing is not Probe.PRESENT:
        out.append(_text("listing_unknown"))
    else:
        models_used = list({m.id: m for _, m in usb_rows}.values())
        out += _stick_lines(found, models_used, probes)
    for row, model in usb_rows:
        if scan_err:
            out.append(_text("presence_unknown", rid=row.id, note="the USB scan failed"))
        else:
            try:
                out.append(_presence_line(row.id, radio_presence(row, model, found)))
            except Exception as exc:
                out.append(_text("probe_error", what=f"{row.id}: stick presence", error=exc))
        note, err = _ask(f"{row.id}: hotplug", probes.hotplug_for, row.id, kind=Probe)
        out.append(err or _text(_probe_key("hotplug", note), rid=row.id))
    return out


def radio_presence(radio, model, found) -> Presence:
    """Is the radio's stick attached (D-8): a serial-less row by its port, a serial row by the one stick whose
    serial starts with the row's first 8 characters. ABSENT only when every device is judged."""
    if not radio.usb_serial:
        state = stick_on_port(found, model, radio.usb_port)
        if state is Probe.PRESENT:
            return Presence(state, radio.usb_port)
        if state is Probe.ABSENT:
            return Presence(state)
        return Presence(state, note=f"port {radio.usb_port} or the device list cannot be read")
    if found.listing is not Probe.PRESENT:
        return Presence(Probe.UNKNOWN, note="USB devices cannot be listed")
    prefix = radio.usb_serial[:radios.SERIAL_PREFIX]
    hits, unreadable = [], False
    for device in found.devices:
        m = match(device, model)
        if m is Probe.PRESENT and device.serial.state is Probe.PRESENT:
            if device.serial.value.startswith(prefix):
                hits.append(device.port)
        elif m is Probe.UNKNOWN or (m is Probe.PRESENT and device.serial.state is Probe.UNKNOWN):
            unreadable = True
    if len(hits) > 1:
        return Presence(Probe.UNKNOWN, ports=tuple(hits), note="ambiguous")
    if hits:
        return Presence(Probe.PRESENT, hits[0], note="a device is unreadable" if unreadable else "")
    if unreadable:
        return Presence(Probe.UNKNOWN, note="a device is unreadable")
    return Presence(Probe.ABSENT)


def detect_allowed(library) -> Verdict:
    """Detect (D-6) runs only with libusb PRESENT; the sysfs scan is never gated by it."""
    if not isinstance(library, Library):
        return _refuse(f"libusb cannot be judged: {library!r} is no library state")
    if library.state is Probe.PRESENT:
        return _proceed()
    return _refuse(f"libusb is {library.state.value}: {library.detail}")


# ---- Table K: the hotplug note and its offset record -----------------------------------------------------

def _count_ok(value) -> bool:
    return type(value) is int and value >= 0


def offset_record(offset: int, pid: int, start_ticks: int, boot_id: str) -> str:
    """The record `<offset> <pid> <start ticks> <boot id>\\n` of a start's log segment; ValueError for any other
    input (exact ints >= 0, a boot id as read)."""
    if not all(_count_ok(v) for v in (offset, pid, start_ticks)):
        raise ValueError("offset, pid and start ticks must be integers >= 0")
    if type(boot_id) is not str or not BOOT_ID_RE.fullmatch(boot_id):
        raise ValueError(f"{boot_id!r} is not a boot id")
    line = f"{offset} {pid} {start_ticks} {boot_id}\n"
    if not OFFSET_RE.fullmatch(line):
        raise ValueError(f"{line!r} is not an offset record")
    return line


def _record_path(log_path) -> Path:
    log = Path(log_path)
    return log.with_name(log.name + ".offset")


def write_offset_record(log_path, line) -> Probe:
    """Write `<log>.offset` with a temp file and a rename: PRESENT when written, UNKNOWN when not. Never raises."""
    try:
        if type(line) is not str or not OFFSET_RE.fullmatch(line):
            return Probe.UNKNOWN
        _write_atomic(_record_path(log_path), line.encode("ascii"))
        return Probe.PRESENT
    except Exception:
        return Probe.UNKNOWN


def _running_ok(running) -> bool:
    return (type(running) is tuple and len(running) == 3 and _count_ok(running[0]) and _count_ok(running[1])
            and type(running[2]) is str)


def hotplug_note(log_path, running) -> Probe:
    """The daemon's hotplug note in this start's log segment: PRESENT (unplug-wait active), ABSENT (no usable
    hotplug), the last one winning; UNKNOWN when this start cannot be told apart or printed neither. `running` is
    (pid, start ticks, boot id) of the radio's verified running daemon, or None. Never raises."""
    try:
        if not _running_ok(running):
            return Probe.UNKNOWN
        m = OFFSET_RE.fullmatch(_record_path(log_path).read_bytes().decode("ascii"))
        if m is None:
            return Probe.UNKNOWN
        offset, pid, ticks, boot = int(m[1]), int(m[2]), int(m[3]), m[4]
        if (pid, ticks, boot) != running:
            return Probe.UNKNOWN
        with open(log_path, "rb") as f:
            if offset > os.fstat(f.fileno()).st_size:
                return Probe.UNKNOWN
            f.seek(offset)
            segment = f.read().decode("utf-8", errors="replace")
        note = Probe.UNKNOWN
        for raw in segment.split("\n")[:-1]:         # whole lines only: the last piece has no newline yet
            line = raw.removesuffix("\r")
            if line == HOTPLUG_ACTIVE:
                note = Probe.PRESENT
            elif line == HOTPLUG_NONE:
                note = Probe.ABSENT
        return note
    except Exception:
        return Probe.UNKNOWN


# ---- Table U: the Hardware section's USB choices and bind -------------------------------------------------

def _serial_refusals(serial, model) -> list:
    """Why the hardware list's save rules refuse `serial` on a row of `model` ([] when they take it)."""
    band = model.bands[0]
    keys = [v.key for v in model.variants if v.band in ("", band)]
    row = radios.Radio(id="r1", name="probe", model=model.id, band=band, ce=keys[0] if len(keys) > 1 else "",
                       usb_serial=serial)
    return [why.removeprefix("r1: ") for why in radios.check([row], {model.id: model})]


def _choice(device, model) -> Choice | None:
    port = device.port
    m = match(device, model)
    if m is Probe.ABSENT:
        return None
    if m is Probe.UNKNOWN:
        return Choice(port, "", False, f"unreadable (port {port})", "the device cannot be read; it may be a stick")
    if device.serial.state is Probe.ABSENT:
        return Choice(port, "", True, f"port {port}: no serial", "")
    if device.serial.state is Probe.UNKNOWN:
        return Choice(port, "", False, f"port {port}: serial unreadable", "the stick's serial cannot be read")
    serial = device.serial.value
    why = "; ".join(_serial_refusals(serial, model))
    return Choice(port, serial, not why, f"port {port}: serial {bound_text(serial)}", why)


def usb_choices(found, model) -> Choices:
    """The sticks of `model` the Hardware section offers to bind (U-1, U-2, U-3, U-8)."""
    if found.listing is not Probe.PRESENT:
        return Choices(Probe.UNKNOWN, "USB devices cannot be listed: the device directory cannot be read", ())
    try:
        items = tuple(c for c in (_choice(d, model) for d in found.devices) if c is not None)
    except UsbTableError as exc:
        return Choices(Probe.UNKNOWN, f"the model table's row {getattr(model, 'id', '?')} is not usable: {exc}", ())
    return Choices(Probe.PRESENT, "", items)


def _bind_refused(reason) -> Bind:
    return Bind("refuse", (), reason, False)


def bind_choice(state, rid, choice, users, procs=(), owned_pids=frozenset(), *, access=Probe.UNKNOWN) -> Bind:
    """Bind radio `rid` to the chosen stick: `usb_serial` for a serial stick, `usb_port` for a serial-less one, the
    other cleared. Refused by the hardware list's save rules or while anything uses the radio; a stick that is
    not attached now does not block it. `access` ABSENT (the chosen stick's node) offers the copybox."""
    if type(access) is not Probe:
        return _bind_refused(f"access must be a Probe, not {access!r}")
    if state.kind != radios.VALID:
        return _bind_refused("the hardware list is not valid: " + "; ".join(state.reasons))
    if not isinstance(choice, Choice) or not choice.selectable:
        label = getattr(choice, "label", repr(choice))
        return _bind_refused(f"{label} cannot be chosen: {getattr(choice, 'why', '') or 'not selectable'}")
    old = next((r for r in state.radios if r.id == rid), None)
    if old is None:
        return _bind_refused(f"there is no radio {rid}")
    new = dataclasses.replace(old, usb_serial=choice.serial, usb_port="" if choice.serial else choice.port)
    rows = radios.with_row(state.radios, new)
    reasons = radios.check(rows)
    if reasons:
        return _bind_refused("; ".join(reasons))
    try:
        blockers = radio_daemon.settings_save_refusals(old, new, users, procs, owned_pids)
    except ValueError as exc:
        return _bind_refused(str(exc))
    if blockers:
        return _bind_refused("; ".join(blockers))
    return Bind("proceed", rows, "", access is Probe.ABSENT)

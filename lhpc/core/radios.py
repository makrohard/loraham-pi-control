"""The hardware list: `config/radios.toml`, one `[[radio]]` row per radio (plan A §2.2, stage L5).

A row names a hidden id (`r1` … `r99`, the lowest free number at creation, never renamed), a name, a model of the
model table (`lhpc/core/radio_models.py`), a band, `enabled`, and the model's settings: `ce` (the variant of a
model with more than one), `usb_serial` and `usb_port` (USB sticks), `high_power`.

Every read is an explicit state, never a guess (QUALITY-RULES-v1 rule 2): `load` returns ABSENT (no file),
VALID (the rows) or INVALID (the reasons). Starts are refused unless the list is VALID (B4-D1 R1): the list alone
is the state, there is no second record. A save is checked whole and written whole: a temp file, fsync, rename
(B4-D1 H6); a write that fails leaves the old file byte-unchanged.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, replace

from . import radio_models

FILE = ("config", "radios.toml")
MAX_RADIOS = 99
SERIAL_PREFIX = 8          # pine64 and the daemon compare the first 8 serial characters (A:185, A:288)
_ID = re.compile(r"^r([1-9][0-9]?)$")
_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,12}$")
_KEYS = {"id", "name", "model", "band", "enabled", "ce", "usb_serial", "usb_port", "high_power"}
_PORT = re.compile(r"^[0-9]+-[0-9]+(\.[0-9]+)*$")      # sysfs bus-port, e.g. 1-1.3

ABSENT, VALID, INVALID = "absent", "valid", "invalid"


@dataclass(frozen=True)
class Radio:
    id: str
    name: str
    model: str
    band: str
    enabled: bool = True
    ce: str = ""
    usb_serial: str = ""
    usb_port: str = ""
    high_power: bool = False


@dataclass(frozen=True)
class State:
    """What the file holds: ABSENT, VALID with `radios`, or INVALID with `reasons` (each names its row)."""
    kind: str
    radios: tuple = ()
    reasons: tuple = field(default=())

    @property
    def startable(self) -> bool:
        """Starts are allowed only on a valid list (R1)."""
        return self.kind == VALID


def path(paths):
    return paths.under(*FILE)


def load(paths, models: dict | None = None) -> State:
    """The hardware list's state. An unreadable or unparsable file, a row outside the schema, or a list that fails a
    save check is INVALID with the reasons; a missing file is ABSENT."""
    p = path(paths)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return State(ABSENT)
    except (OSError, UnicodeDecodeError) as exc:
        return State(INVALID, reasons=(f"{FILE[-1]} cannot be read ({type(exc).__name__})",))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return State(INVALID, reasons=(f"{FILE[-1]} is not valid TOML ({exc})",))
    rows = data.get("radio", [])
    if set(data) - {"radio"} or not isinstance(rows, list):
        return State(INVALID, reasons=(f"{FILE[-1]} holds only [[radio]] rows",))
    radios, reasons = [], []
    for i, row in enumerate(rows, 1):
        r, why = _row(row, i)
        if why:
            reasons.append(why)
        else:
            radios.append(r)
    if not reasons:
        reasons = check(radios, models)
    return State(INVALID, reasons=tuple(reasons)) if reasons else State(VALID, radios=tuple(radios))


def _row(row, i):
    where = f"radio row {i}"
    if not isinstance(row, dict):
        return None, f"{where}: not a table"
    extra = set(row) - _KEYS
    if extra:
        return None, f"{where}: {', '.join(sorted(extra))} is not a radio field"
    types = {"id": str, "name": str, "model": str, "band": str, "enabled": bool, "ce": str, "usb_serial": str,
             "usb_port": str, "high_power": bool}
    for k in ("id", "name", "model", "band"):
        if k not in row:
            return None, f"{where}: {k} is required"
    for k, v in row.items():
        if type(v) is not types[k]:            # exact types: a bool is no int, an int no str (rule 3)
            return None, f"{where}: {k} must be {types[k].__name__}"
    return Radio(**row), ""


def check(radios, models: dict | None = None) -> list:
    """The save checks over the whole list (A §2.2); [] when the list holds. Each reason names its row."""
    models = radio_models.load() if models is None else models
    out = []
    if len(radios) > MAX_RADIOS:
        out.append(f"at most {MAX_RADIOS} radios")
    ids, names = {}, {}
    for r in radios:
        if not _ID.match(r.id):
            out.append(f"{r.id!r}: an id is r1 … r99")
        elif r.id in ids:
            out.append(f"{r.id}: the id is used twice")
        ids.setdefault(r.id, r)
        if not _NAME.match(r.name):
            out.append(f"{r.id}: the name {r.name!r} must be 1-12 characters of letters, digits, space . _ -")
        elif r.name.casefold() in names:
            out.append(f"{r.id}: the name {r.name!r} is already {names[r.name.casefold()]}'s")
        names.setdefault(r.name.casefold(), r.id)
        out += _model_checks(r, models)
    out += _claims(radios, models)
    return out


def _model_checks(r, models):
    m = models.get(r.model)
    if m is None:
        return [f"{r.id}: {r.model!r} is no model of the model table"]
    out = []
    if r.band not in m.bands:
        out.append(f"{r.id}: {m.label} has no band {r.band!r} (it has {', '.join(m.bands)})")
    v = variant(r, m)
    if v is None:
        keys = [x.key for x in m.variants if x.band in ("", r.band)]
        out.append(f"{r.id}: ce {r.ce!r} is no variant of {m.label} for band {r.band} ({', '.join(keys) or 'none'})")
    if m.transport == "usb-ch341":
        if r.usb_port and not _PORT.match(r.usb_port):
            out.append(f"{r.id}: usb_port {r.usb_port!r} is no bus-port (like 1-1.3)")
        if r.usb_serial and (len(r.usb_serial) > 64 or not r.usb_serial.isprintable() or r.usb_serial != r.usb_serial.strip()):
            out.append(f"{r.id}: usb_serial {r.usb_serial!r} is not a stick serial")
    else:
        for k in ("usb_serial", "usb_port"):
            if getattr(r, k):
                out.append(f"{r.id}: {k} is a USB stick's setting, and {m.label} is no USB stick")
    if r.high_power and not m.high_power_switch:
        out.append(f"{r.id}: {m.label} has no high-power switch")
    return out


def variant(r, m):
    """The model variant a row selects: by `ce` when the model has several for its band, else the only one."""
    fit = [v for v in m.variants if v.band in ("", r.band)]
    if r.ce:
        return next((v for v in fit if v.key == r.ce), None)
    return fit[0] if len(fit) == 1 else None


def _claims(radios, models):
    """Pins unique across every SPI row, enabled or not (W13, D24); serials unique in their first 8 characters;
    at most one USB row without a serial (W19)."""
    out, pins, serials, serialless = [], {}, {}, []
    for r in radios:
        m = models.get(r.model)
        v = variant(r, m) if m else None
        if m is None or v is None:
            continue
        if m.transport == "spi":
            for name, line in v.pins.items():
                if line in pins and pins[line][0] != r.id:
                    out.append(f"{r.id}: BCM line {line} ({name}) is already claimed by {pins[line][0]} ({pins[line][1]})")
                pins.setdefault(line, (r.id, name))
        else:
            key = r.usb_serial[:SERIAL_PREFIX]
            if not key:
                serialless.append(r.id)
            elif key in serials:
                out.append(f"{r.id}: the serial's first {SERIAL_PREFIX} characters {key!r} are {serials[key]}'s too")
            serials.setdefault(key, r.id)
    if len(serialless) > 1:
        out.append(f"{', '.join(serialless)}: at most one stick without a serial")
    return out


def next_id(radios) -> str:
    """The lowest free id (A:123); ValueError when r1 … r99 are all taken."""
    used = {r.id for r in radios}
    for n in range(1, MAX_RADIOS + 1):
        if f"r{n}" not in used:
            return f"r{n}"
    raise ValueError(f"all {MAX_RADIOS} radio ids are taken")


def default_name(radios, model) -> str:
    """`<short> <band>` (at most 11 characters), or `<short> <n>` (n = 2 …) when that is taken (A §2.2)."""
    taken = {r.name.casefold() for r in radios}
    for band in model.bands:
        name = f"{model.short} {band}"
        if name.casefold() not in taken:
            return name
    n = 2
    while f"{model.short} {n}".casefold() in taken:
        n += 1
    return f"{model.short} {n}"


def render(radios) -> str:
    """The file text for `radios`, one [[radio]] table each, fields in a fixed order, defaults omitted."""
    out = ["# The hardware list (LHPC): one [[radio]] per radio. Edited by the Hardware section and `lhpc hardware`.", ""]
    for r in radios:
        out.append("[[radio]]")
        for k in ("id", "name", "model", "band", "enabled", "ce", "usb_serial", "usb_port", "high_power"):
            v = getattr(r, k)
            if k in ("ce", "usb_serial", "usb_port") and v == "":
                continue
            if k == "high_power" and v is False:
                continue
            out.append(f"{k} = {_toml(v)}")
        out.append("")
    return "\n".join(out)


def _toml(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(paths, radios, models: dict | None = None) -> list:
    """Check the whole list, then write it whole (temp file, fsync, rename, fsync of the directory). Returns [] on
    success, else the reasons, and the file stays byte-unchanged (B4-D1 H6)."""
    reasons = check(radios, models)
    if reasons:
        return reasons
    text = render(radios)
    if load_text(text, models).kind != VALID:            # the rendered file must read back as this list
        return ["the rendered hardware list does not read back (a bug; nothing written)"]
    p = path(paths)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        try:
            data = text.encode("utf-8")
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("short write")
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, p)
        dfd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return [f"{FILE[-1]} could not be written ({type(exc).__name__}: {exc}); the old file is unchanged"]
    return []


def load_text(text: str, models: dict | None = None) -> State:
    """`load` on a text instead of the file (the read-back check of `save`)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return State(INVALID, reasons=(str(exc),))
    radios, reasons = [], []
    for i, row in enumerate(data.get("radio", []), 1):
        r, why = _row(row, i)
        (reasons.append(why) if why else radios.append(r))
    reasons = reasons or check(radios, models)
    return State(INVALID, reasons=tuple(reasons)) if reasons else State(VALID, radios=tuple(radios))


def with_row(radios, r) -> tuple:
    """`radios` with `r` replacing the row of its id, or appended."""
    out = [r if x.id == r.id else x for x in radios]
    return tuple(out) if any(x.id == r.id for x in radios) else (*radios, r)


def without(radios, rid) -> tuple:
    return tuple(r for r in radios if r.id != rid)


__all__ = [
    "ABSENT",
    "INVALID",
    "VALID",
    "Radio",
    "State",
    "check",
    "default_name",
    "load",
    "next_id",
    "render",
    "replace",
    "save",
    "variant",
    "with_row",
    "without",
]

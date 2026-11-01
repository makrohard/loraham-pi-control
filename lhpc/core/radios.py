"""The hardware list: `config/radios.toml`, one `[[radio]]` row per radio (plan A §2.2, stage L5).

A row names a hidden id (`r1` … `r99`, the lowest free number at creation, never renamed), a name, a model of the
model table (`lhpc/core/radio_models.py`), a band, `enabled`, the model's settings (`ce`, the variant of a model with
more than one; `usb_serial` and `usb_port`, USB sticks) and the per-radio daemon settings as fields of the row
(B4-D3a): `high_power`, `txmode`, `cadmon`, `cadrssi`, `ocp_ma` and `dp_<PARAM>`. A settings field that is absent is
"not set" (None) and is resolved at start (`radio_settings`); a set field, an explicit false included, wins.

Every read is an explicit state, never a guess (QUALITY-RULES-v1 rule 2): `load` returns ABSENT (no file), VALID
(the rows) or INVALID (the reasons), and never raises for the file's content or path. Starts are refused unless the
list is VALID (B4-D1 R1): the list alone is the state. A save is checked whole and written whole: a temp file, fsync,
rename, a directory fsync (B4-D1 H6). A failure before the rename leaves the old file byte-unchanged; a failure after
it is reported as written but not durable.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

from . import daemon_control, radio_models
from .daemon_params import ALL_PARAMS
from .paths import PathContainmentError

FILE = ("config", "radios.toml")
MAX_RADIOS = 99
SERIAL_PREFIX = 8          # pine64 and the daemon compare the first 8 serial characters (A:185, A:288)
# M7 (B4-D1): the old band params tx_/cadmon_/cadrssi_/hipower_<band> are carried to a radio whose row does not set
# them. One constant, so the maintainer's word on M7 changes one line.
CARRY_BAND_PARAMS = True
TXMODES = ("managed", "direct")
CADMONS = ("off", "on")
CADRSSI_RANGE = (-130, 0)
# Every pattern below is matched whole (re.fullmatch): `$` would also match before a trailing newline (Q10).
_ID = re.compile(r"r([1-9][0-9]?)")
_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,12}")
_PORT = re.compile(r"[0-9]+-[0-9]+(?:\.[0-9]+)*")      # sysfs bus-port, e.g. 1-1.3
_SERIAL = re.compile(rf"[\x21-\x7a\x7c\x7e]{{{SERIAL_PREFIX},64}}")   # bytes 0x21-0x7E without { and } (Q3, Q5)
_BASE_KEYS = ("id", "name", "model", "band", "enabled", "ce", "usb_serial", "usb_port")
_SETTING_KEYS = ("high_power", "txmode", "cadmon", "cadrssi", "ocp_ma")
_DP_KEYS = tuple(f"dp_{p}" for p in ALL_PARAMS)
_TYPES = {"id": (str,), "name": (str,), "model": (str,), "band": (str,), "enabled": (bool,), "ce": (str,),
          "usb_serial": (str,), "usb_port": (str,), "high_power": (bool,), "txmode": (str,), "cadmon": (str,),
          "cadrssi": (int,), "ocp_ma": (str, int), **dict.fromkeys(_DP_KEYS, (str,))}

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
    high_power: bool | None = None
    txmode: str | None = None
    cadmon: str | None = None
    cadrssi: int | None = None
    ocp_ma: str | int | None = None
    dp: tuple = ()             # ((PARAM, value), …) sorted: the row's dp_<PARAM> fields


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


@dataclass(frozen=True)
class RadioSettings:
    """The settings a radio's daemon starts with, each resolved from the row, the old band params or the defaults;
    `notes` names every legacy value that was not carried and why."""
    txmode: str
    cadmon: str
    cadrssi: int
    hipower: bool
    ocp: str
    dp: dict
    notes: tuple = ()


def path(paths):
    return paths.under(*FILE)


def load(paths, models: dict | None = None) -> State:
    """The hardware list's state; never raises for the file's content or its path (Q2)."""
    try:
        p = path(paths)
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return State(ABSENT)
    except (OSError, UnicodeDecodeError, PathContainmentError) as exc:
        return State(INVALID, reasons=(f"{FILE[-1]} cannot be read ({type(exc).__name__}: {exc})",))
    return load_text(text, models)


def load_text(text: str, models: dict | None = None) -> State:
    """`load` on a text: the same structure checks, the row checks and the save checks (Q6)."""
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError, ValueError) as exc:      # never BaseException (Q9)
        return State(INVALID, reasons=(f"{FILE[-1]} is not valid TOML ({type(exc).__name__}: {exc})",))
    rows = data.get("radio", [])
    if set(data) - {"radio"} or not isinstance(rows, list):
        return State(INVALID, reasons=(f"{FILE[-1]} holds only [[radio]] rows",))
    radios, reasons = [], []
    for i, row in enumerate(rows, 1):
        r, why = _row(row, i)
        (reasons.append(why) if why else radios.append(r))
    if not reasons:
        reasons = check(radios, models)
    return State(INVALID, reasons=tuple(reasons)) if reasons else State(VALID, radios=tuple(radios))


def _row(row, i):
    where = f"radio row {i}"
    if not isinstance(row, dict):
        return None, f"{where}: not a table"
    extra = set(row) - set(_TYPES)
    if extra:
        return None, f"{where}: {', '.join(sorted(extra))} is not a radio field"
    for k in ("id", "name", "model", "band"):
        if k not in row:
            return None, f"{where}: {k} is required"
    for k, v in row.items():
        if type(v) not in _TYPES[k]:            # exact types: a bool is no int, an int no str (rule 3)
            return None, f"{where}: {k} must be {' or '.join(t.__name__ for t in _TYPES[k])}"
    base = {k: v for k, v in row.items() if not k.startswith("dp_")}
    dp = tuple(sorted((k[3:], v) for k, v in row.items() if k.startswith("dp_")))
    return Radio(**base, dp=dp), ""


def check(radios, models: dict | None = None) -> list:
    """The save checks over the whole list (A §2.2, B4-D3a); [] when the list holds. Each reason names its row."""
    models = radio_models.load() if models is None else models
    out = []
    if len(radios) > MAX_RADIOS:
        out.append(f"at most {MAX_RADIOS} radios")
    ids, names = {}, {}
    for r in radios:
        if not (isinstance(r.id, str) and _ID.fullmatch(r.id)):
            out.append(f"{r.id!r}: an id is r1 … r99")
        elif r.id in ids:
            out.append(f"{r.id}: the id is used twice")
        ids.setdefault(r.id, r)
        if not (isinstance(r.name, str) and _NAME.fullmatch(r.name)):
            out.append(f"{r.id}: the name {r.name!r} must be 1-12 characters of letters, digits, space . _ -")
        elif r.name.casefold() in names:
            out.append(f"{r.id}: the name {r.name!r} is already {names[r.name.casefold()]}'s")
        else:
            names[r.name.casefold()] = r.id
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
    if variant(r, m) is None:
        keys = [x.key for x in m.variants if x.band in ("", r.band)]
        out.append(f"{r.id}: ce {r.ce!r} is no variant of {m.label} for band {r.band} ({', '.join(keys) or 'none'})")
    if m.transport == "usb-ch341":
        if r.usb_serial and not _SERIAL.fullmatch(r.usb_serial):
            out.append(f"{r.id}: usb_serial must be {SERIAL_PREFIX}-64 characters, each a byte 0x21-0x7E other than "
                       "{ and } (the daemon compares the first 8 bytes)")
        if r.usb_serial and r.usb_port:
            out.append(f"{r.id}: usb_port is recorded only for a stick without a serial")
        if not r.usb_serial and not r.usb_port:
            out.append(f"{r.id}: a stick without a serial needs its usb_port (like 1-1.3), recorded at configure time")
        if r.usb_port and not _PORT.fullmatch(r.usb_port):
            out.append(f"{r.id}: usb_port {r.usb_port!r} is no bus-port (like 1-1.3)")
    else:
        for k in ("usb_serial", "usb_port"):
            if getattr(r, k):
                out.append(f"{r.id}: {k} is a USB stick's setting, and {m.label} is no USB stick")
    if r.high_power is True and not m.high_power_switch:
        out.append(f"{r.id}: {m.label} has no high-power switch")
    out += [f"{r.id}: {why}" for why in _setting_errors(r.txmode, r.cadmon, r.cadrssi, r.ocp_ma, dict(r.dp), m.chip,
                                                         bool(r.high_power))]
    return out


def _setting_errors(txmode, cadmon, cadrssi, ocp, dp, chip, high_power) -> list:
    """The per-radio setting rules; a None value is "not set" and passes."""
    out = []
    if txmode is not None and txmode not in TXMODES:
        out.append(f"txmode {txmode!r} must be one of {', '.join(TXMODES)}")
    if cadmon is not None and cadmon not in CADMONS:
        out.append(f"cadmon {cadmon!r} must be one of {', '.join(CADMONS)}")
    if cadrssi is not None and not (type(cadrssi) is int and CADRSSI_RANGE[0] <= cadrssi <= CADRSSI_RANGE[1]):
        out.append(f"cadrssi {cadrssi!r} must be an integer {CADRSSI_RANGE[0]}..{CADRSSI_RANGE[1]}")
    if ocp is not None:
        why = _ocp_error(ocp, chip)
        if why:
            out.append(why)
    for name, value in sorted(dp.items()):
        why = daemon_control.validate_set(name, value, chip, high_power) if name in ALL_PARAMS else "unknown"
        if why:
            out.append(f"dp_{name} {value!r}: {why}")
    return out


def _ocp_error(ocp, chip):
    if ocp == "auto":
        return ""
    ma = ocp if type(ocp) is int else daemon_control.ocp_ma(ocp)
    if ma is None or not daemon_control.ocp_valid(ma, chip):
        return f"ocp_ma {ocp!r} must be \"auto\" or {daemon_control.OCP_LEGAL_TEXT[chip]} mA"
    return ""


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
    """`<short> <band>` (at most 11 characters), or `<short> <n>` (n = 2 …) when that is taken (A §2.2). ValueError
    when the result is no valid name (Q7)."""
    taken = {r.name.casefold() for r in radios}
    name = next((f"{model.short} {b}" for b in model.bands if f"{model.short} {b}".casefold() not in taken), None)
    if name is None:
        n = 2
        while f"{model.short} {n}".casefold() in taken:
            n += 1
        name = f"{model.short} {n}"
    if not _NAME.fullmatch(name):
        raise ValueError(f"the default name {name!r} is no valid radio name")
    return name


def render(radios) -> str:
    """The file text for `radios`, one [[radio]] table each, fields in a fixed order. Empty strings of the model
    settings and None settings are omitted; a set false is written (Q8)."""
    out = ["# The hardware list (LHPC): one [[radio]] per radio. Edited by the Hardware section and `lhpc hardware`.", ""]
    for r in radios:
        out.append("[[radio]]")
        for k in (*_BASE_KEYS, *_SETTING_KEYS):
            v = getattr(r, k)
            if v is None or (k in ("ce", "usb_serial", "usb_port") and v == ""):
                continue
            out.append(f"{k} = {_toml(v)}")
        out += [f"dp_{name} = {_toml(v)}" for name, v in r.dp]
        out.append("")
    return "\n".join(out)


def _toml(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(paths, radios, models: dict | None = None) -> list:
    """Check the whole list, then write it whole. Returns [] on success, else the reasons. Before the rename the old
    file stays byte-unchanged; a failure after it is reported as written, not durable (Q1)."""
    reasons = check(radios, models)
    if reasons:
        return reasons
    text = render(radios)
    if load_text(text, models).kind != VALID:            # the rendered file must read back as this list
        return ["the rendered hardware list does not read back (a bug; nothing written)"]
    try:
        p = path(paths)
    except PathContainmentError as exc:                  # Q2: nothing written
        return [f"{FILE[-1]} has no safe path ({exc}); nothing written"]
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        try:
            view = memoryview(text.encode("utf-8"))
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("short write")
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, p)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return [f"{FILE[-1]} could not be written ({type(exc).__name__}: {exc}); the old file is unchanged"]
    try:
        dfd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError as exc:
        return [f"{FILE[-1]} was written (the new list is in place) but is not durable: the directory sync failed "
                f"({type(exc).__name__}: {exc})"]
    return []


def radio_settings(radio, model, legacy: dict, legacy_dp: dict, defaults: dict) -> RadioSettings:
    """The settings a radio's daemon starts with. A field set on the row wins (an explicit false included); else, for
    txmode, cadmon, cadrssi and high power, the old band param when CARRY_BAND_PARAMS, else `defaults`; ocp: the row,
    else the old `ocp_<band>` always (RK11), else "auto"; each dp PARAM: the row, else the one value every client stack
    holding it agrees on. Nothing is written (W12). ValueError names a legacy value that fails its rule (a carried value
    is never silently replaced)."""
    band, chip, notes = radio.band, model.chip, []

    # (the legacy band key's prefix, the `defaults` key = the manifest param name): every pair named once (Q11)
    def legacy_or_default(row_value, prefix, default_key):
        if row_value is not None:
            return row_value, "row"
        if CARRY_BAND_PARAMS and f"{prefix}_{band}" in legacy:
            return legacy[f"{prefix}_{band}"], f"legacy {prefix}_{band}"
        return defaults[default_key], "default"
    txmode, src_tx = legacy_or_default(radio.txmode, "tx", "txmode")
    cadmon, src_cm = legacy_or_default(radio.cadmon, "cadmon", "cadmon")
    cadrssi, src_cr = legacy_or_default(radio.cadrssi, "cadrssi", "cadrssi")
    if radio.high_power is not None:
        hipower, src_hp = radio.high_power, "row"
    elif CARRY_BAND_PARAMS and f"hipower_{band}" in legacy:
        if model.high_power_switch:
            hipower, src_hp = legacy[f"hipower_{band}"], f"legacy hipower_{band}"
        else:                                             # the old key belonged to another board: off is the safe side
            hipower, src_hp = "off", "default"
            notes.append(f"hipower_{band}={legacy[f'hipower_{band}']!r} not carried: {model.label} has no high-power switch")
    else:
        hipower, src_hp = defaults["hipower"], "default"
    try:
        cadrssi_i = cadrssi if type(cadrssi) is int else int(daemon_control_int(cadrssi))
    except ValueError:
        raise ValueError(f"cadrssi from {src_cr}: {cadrssi!r} is not an integer") from None
    hp = _flag(hipower, src_hp)
    errs = [f"{e} (from {src})" for value_args, src in (((str(txmode), None, None), src_tx), ((None, str(cadmon), None), src_cm),
                                                       ((None, None, cadrssi_i), src_cr))
            for e in _setting_errors(*value_args, None, {}, chip, hp)]
    if errs:
        raise ValueError(f"{radio.id}: " + "; ".join(errs))
    if radio.ocp_ma is not None:
        ocp, src_ocp = radio.ocp_ma, "row"
    elif f"ocp_{band}" in legacy:
        ocp, src_ocp = legacy[f"ocp_{band}"], f"legacy ocp_{band}"
    else:
        ocp, src_ocp = "auto", "default"
    why = _ocp_error(ocp, chip)
    if why:
        raise ValueError(f"{radio.id}: {why} (from {src_ocp})")
    ocp = str(ocp) if type(ocp) is int else ocp
    dp = dict(radio.dp)
    held: dict = {}
    for stack, values in sorted(legacy_dp.items()):
        for name, value in values.items():
            if name in dp or name not in ALL_PARAMS:
                continue
            if value == "" or daemon_control.validate_set(name, value, chip, hp):
                notes.append(f"dp_{band}_{name}={value!r} of {stack} dropped (empty or not valid for {chip})")
                continue
            held.setdefault(name, {})[stack] = value
    for name, by_stack in held.items():
        if len(set(by_stack.values())) == 1:
            dp[name] = next(iter(by_stack.values()))
        else:
            notes.append(f"dp_{band}_{name} not carried: the client stacks disagree "
                         f"({', '.join(f'{s}={v!r}' for s, v in sorted(by_stack.items()))})")
    return RadioSettings(str(txmode), str(cadmon), cadrssi_i, hp, ocp, dp, tuple(notes))


def daemon_control_int(value) -> int:
    """An ASCII decimal integer string (a sign allowed), else ValueError; `int()` alone takes "1_0" and Unicode
    digits."""
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]{1,4}", value):
        return int(value)
    raise ValueError(value)


def _flag(value, source) -> bool:
    if type(value) is bool:
        return value
    if value in ("on", "off"):
        return value == "on"
    raise ValueError(f"hipower from {source}: {value!r} must be on or off")


def effective_ocp(settings: RadioSettings) -> int:
    """The mA passed as `--ocp`: the set value, or for "auto" 140 with high power, else 100 (A:295)."""
    if settings.ocp == "auto":
        return 140 if settings.hipower else 100
    ma = daemon_control.ocp_ma(settings.ocp)
    if ma is None:
        raise ValueError(f"ocp {settings.ocp!r} is no mA value")
    return ma


def with_row(radios, r) -> tuple:
    """`radios` with `r` replacing the row of its id, or appended."""
    out = [r if x.id == r.id else x for x in radios]
    return tuple(out) if any(x.id == r.id for x in radios) else (*radios, r)


def without(radios, rid) -> tuple:
    return tuple(r for r in radios if r.id != rid)

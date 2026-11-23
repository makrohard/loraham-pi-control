"""The radio model table: one row per board or stick model (`lhpc/data/radio_models.toml`).

A model names its transport, chip, bands, power default (and an optional raised top) and, per variant, the daemon `--hw` preset with
the BCM lines it claims. Loading validates every row against one schema, so a new model within the
supported chips and transports is one row and no code. Unused until the hardware list (stage L5).
"""
from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import known_bands

TABLE = Path(__file__).resolve().parents[1] / "data" / "radio_models.toml"
TRANSPORTS = ("spi", "usb-ch341")
CHIPS = ("sx127x", "sx1262")
PIN_FIELDS = ("cs", "irq", "rst", "gpio", "aux", "txen", "rxen", "led_pin")   # the daemon profile's names
_MODEL_KEYS = {"id", "label", "short", "transport", "chip", "bands", "power_default_dbm", "power_max_dbm",
               "high_power_switch", "variant", "usb", "direct"}
# The only per-model POWER top: an SX1262 board whose maker rates it above the chip table's 20 (the Meshstick, 22),
# equal to its daemon preset's ceiling. Every other model's range is its chip's (daemon_control.int_range).
_RAISED_TOP = {"sx1262": range(21, 23)}
_VARIANT_KEYS = {"key", "preset", "band", "pins"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# A USB stick's pins are its CH341 bridge's lines (T1.4b point 7): D0-D7, plus the two status pins the bridge
# reports in its input word: INT# (10) as an interrupt, SLCT (11) as a read line.
_USB_D_LINES = frozenset(range(8))
_USB_STATUS = {"irq": 10, "gpio": 11}


class ModelError(ValueError):
    """The table holds a row outside the schema; the message names the row and the field."""


@dataclass(frozen=True)
class Variant:
    key: str
    preset: str
    band: str              # "" = either band
    pins: dict


@dataclass(frozen=True)
class RadioModel:
    id: str
    label: str
    short: str
    transport: str
    chip: str
    bands: tuple
    power_default_dbm: int
    high_power_switch: bool
    variants: tuple
    usb: dict | None = None
    direct: dict | None = None
    power_max_dbm: int | None = None      # a raised POWER top (Meshstick 22), None = the chip range

    def variant(self, key: str) -> Variant:
        return next(v for v in self.variants if v.key == key)


def load(path: Path = TABLE) -> dict:
    """{id: RadioModel} in table order; ModelError on the first row outside the schema. Every call builds its own
    models from the parsed rows, so nothing a caller holds is shared with another."""
    models = {}
    for row in _rows(path.read_text(encoding="utf-8")):
        model = _model(row)
        if model.id in models:
            raise ModelError(f"duplicate model id {model.id!r}")
        models[model.id] = model
    return models


@functools.lru_cache(maxsize=2)
def _rows(text: str) -> tuple:
    """The table's rows, parsed once per text: one dashboard render reads the table hundreds of times. A changed file
    is a different text, so nothing is ever stale. The rows are never handed out: `_model` only reads them and copies
    what it keeps."""
    return tuple(tomllib.loads(text).get("model", []))


def _model(row: dict) -> RadioModel:
    where = f"model {row.get('id')!r}"

    def need(ok: bool, field: str, why: str) -> None:
        if not ok:
            raise ModelError(f"{where}: {field} {why}")
    need(not set(row) - _MODEL_KEYS, ", ".join(sorted(set(row) - _MODEL_KEYS)), "is not a model field")
    need(isinstance(row.get("id"), str) and bool(_ID.match(row["id"])), "id", "must be lower-case letters, digits, '-'")
    need(isinstance(row.get("label"), str) and bool(row["label"]), "label", "is required")
    need(isinstance(row.get("short"), str) and 0 < len(row["short"]) <= 7, "short", "must be 1-7 characters")
    need(row.get("transport") in TRANSPORTS, "transport", f"must be one of {', '.join(TRANSPORTS)}")
    need(row.get("chip") in CHIPS, "chip", f"must be one of {', '.join(CHIPS)}")
    bands = row.get("bands")
    need(isinstance(bands, list) and bool(bands) and set(bands) <= set(known_bands.ALL), "bands",
         f"must be some of {', '.join(known_bands.ALL)}")
    from .daemon_control import int_range
    lo, hi = int_range("POWER", row["chip"])
    need(type(row.get("power_default_dbm")) is int and lo <= row["power_default_dbm"] <= hi, "power_default_dbm",
         f"must be an integer in [{lo}, {hi}]")
    top = row.get("power_max_dbm")
    need(top is None or (type(top) is int and top in _RAISED_TOP.get(row["chip"], ())), "power_max_dbm",
         "may only raise an SX1262's top to 21 or 22")
    need(type(row.get("high_power_switch")) is bool, "high_power_switch", "must be true or false")
    usb = row.get("usb")
    need((usb is not None) == (row["transport"] == "usb-ch341"), "usb", "is required for a USB model and only there")
    need(usb is None or {"vid", "pid"} <= set(usb) <= {"vid", "pid", "product"}, "usb",
         "must hold vid and pid, and may hold product")
    variants = tuple(_variant(v, where, bands, row["transport"]) for v in row.get("variant", []))
    need(bool(variants), "variant", "is required")
    need(len({v.key for v in variants}) == len(variants), "variant", "keys must differ")
    return RadioModel(row["id"], row["label"], row["short"], row["transport"], row["chip"], tuple(bands),
                      row["power_default_dbm"], row["high_power_switch"], variants, _copy(usb), _copy(row.get("direct")), top)


def _copy(table):
    """A sub-table the model keeps, as its own dict (the parsed rows are shared by every load)."""
    return None if table is None else dict(table)


def _variant(v: dict, where: str, bands: list, transport: str = "spi") -> Variant:
    pins = v.get("pins", {})
    bad = (set(v) - _VARIANT_KEYS or not isinstance(v.get("key"), str) or not isinstance(v.get("preset"), str)
           or v.get("band", "") not in ("", *bands))
    if bad:
        raise ModelError(f"{where}: variant {v.get('key')!r} must hold key, preset, an optional band of the "
                         "model and pins")
    for name, pin in pins.items():
        if name not in PIN_FIELDS or type(pin) is not int:
            raise ModelError(f"{where}: variant {v['key']!r} pin {name}={pin!r}: a pin is one of "
                             f"{', '.join(PIN_FIELDS)}")
        if transport == "usb-ch341":
            if pin not in _USB_D_LINES and pin != _USB_STATUS.get(name):
                extra = f" or {_USB_STATUS[name]}" if name in _USB_STATUS else ""
                raise ModelError(f"{where}: variant {v['key']!r} pin {name}={pin}: a USB stick's {name} is a bridge "
                                 f"line D0-D7{extra}")
        elif not 0 <= pin <= 27:
            raise ModelError(f"{where}: variant {v['key']!r} pin {name}={pin!r}: a pin is one of "
                             f"{', '.join(PIN_FIELDS)} on a BCM line 0-27")
    return Variant(v["key"], v["preset"], v.get("band", ""), dict(pins))

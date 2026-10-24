"""The radio model table: one row per board or stick model (`lhpc/data/radio_models.toml`).

A model names its transport, chip, bands, power window and, per variant, the daemon `--hw` preset with
the BCM lines it claims. Loading validates every row against one schema, so a new model within the
supported chips and transports is one row and no code. Unused until the hardware list (stage L5).
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1] / "data" / "radio_models.toml"
TRANSPORTS = ("spi", "usb-ch341")
CHIPS = ("sx127x", "sx1262")
BANDS = ("433", "868")
PIN_FIELDS = ("cs", "irq", "rst", "gpio", "aux", "txen", "rxen", "led_pin")   # the daemon profile's names
_MODEL_KEYS = {"id", "label", "short", "transport", "chip", "bands", "power_max_dbm", "high_power_switch",
               "variant", "usb", "direct"}
_VARIANT_KEYS = {"key", "preset", "band", "pins"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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
    power_max_dbm: int
    high_power_switch: bool
    variants: tuple
    usb: dict | None = None
    direct: dict | None = None

    def variant(self, key: str) -> Variant:
        return next(v for v in self.variants if v.key == key)


def load(path: Path = TABLE) -> dict:
    """{id: RadioModel} in table order; ModelError on the first row outside the schema."""
    models = {}
    for row in tomllib.loads(path.read_text(encoding="utf-8")).get("model", []):
        model = _model(row)
        if model.id in models:
            raise ModelError(f"duplicate model id {model.id!r}")
        models[model.id] = model
    return models


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
    need(isinstance(bands, list) and bool(bands) and set(bands) <= set(BANDS), "bands", f"must be some of {', '.join(BANDS)}")
    need(type(row.get("power_max_dbm")) is int, "power_max_dbm", "must be an integer")
    need(type(row.get("high_power_switch")) is bool, "high_power_switch", "must be true or false")
    usb = row.get("usb")
    need((usb is not None) == (row["transport"] == "usb-ch341"), "usb", "is required for a USB model and only there")
    need(usb is None or set(usb) == {"vid", "pid", "product"}, "usb", "must hold exactly vid, pid, product")
    variants = tuple(_variant(v, where, bands) for v in row.get("variant", []))
    need(bool(variants), "variant", "is required")
    need(len({v.key for v in variants}) == len(variants), "variant", "keys must differ")
    return RadioModel(row["id"], row["label"], row["short"], row["transport"], row["chip"], tuple(bands),
                      row["power_max_dbm"], row["high_power_switch"], variants, usb, row.get("direct"))


def _variant(v: dict, where: str, bands: list) -> Variant:
    pins = v.get("pins", {})
    bad = (set(v) - _VARIANT_KEYS or not isinstance(v.get("key"), str) or not isinstance(v.get("preset"), str)
           or v.get("band", "") not in ("", *bands))
    if bad:
        raise ModelError(f"{where}: variant {v.get('key')!r} must hold key, preset, an optional band of the "
                         "model and pins")
    for name, pin in pins.items():
        if name not in PIN_FIELDS or type(pin) is not int or not 0 <= pin <= 27:
            raise ModelError(f"{where}: variant {v['key']!r} pin {name}={pin!r}: a pin is one of "
                             f"{', '.join(PIN_FIELDS)} on a BCM line 0-27")
    return Variant(v["key"], v["preset"], v.get("band", ""), dict(pins))

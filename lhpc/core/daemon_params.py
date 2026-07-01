"""Per-stack daemon radio-parameter catalogue and view logic (pure, no I/O).

Each daemon-client stack (chat/igate/kiss/voice/meshcom/meshcore) drives the LoRaHAM daemon's
radio via the CONF socket. lhpc applies this stack's configured values to the daemon ONCE, after
the daemon is up and before the stack's own components start. The client app then SETs its own
radio parameters (FREQ, SF, BW, CR, CRC, PREAMBLE, SYNC, POWER, TXMODE) — so those are marked
**app-owned**: lhpc still applies them, but the app overwrites them on connect (the panel greys
them only as a visual hint). The listen-before-talk timing (CADWAIT, CADIDLE) is operator-owned:
the app does not touch it, so it sticks.

Every value here is editable and persists per stack+band; the app-owned DEFAULTS are taken from
each app's ORIGINAL source (cross-checked against upstream) so lhpc's values match what the app
sets — see `docs/operations.md`. Values are validated by `daemon_control` before any apply; this
module is data + selection only and performs no I/O.
"""

from __future__ import annotations

# Display groups (ordered, coherent: modulation → channel → framing → power; then TX → CAD).
# Every name is a valid daemon CONF key (see daemon_control).
RADIO_PARAMS = ("MODE", "FREQ", "SF", "BW", "CR", "CRC", "LDRO", "PREAMBLE", "SYNC", "POWER")
LBT_PARAMS = ("TXMODE", "TXQUEUE", "CADMONITOR", "CADRSSI", "CADWAIT", "CADIDLE",
              "CADTXAFTERTIMEOUT")
ALL_PARAMS = RADIO_PARAMS + LBT_PARAMS

# Operator-owned params: the client app never SETs these, so they are never greyed/overwritten.
OPERATOR_PARAMS = ("CADWAIT", "CADIDLE", "TXQUEUE", "CADMONITOR", "CADRSSI", "CADTXAFTERTIMEOUT")

# Params rendered as a dropdown (fixed enum or small discrete range). Must match daemon_control.
DROPDOWN_CHOICES = {
    "MODE": ("LORA", "FSK"),
    "SF": ("7", "8", "9", "10", "11", "12"),
    "BW": ("7.8", "10.4", "15.6", "20.8", "31.25", "41.7", "62.5", "125.0", "250.0", "500.0"),
    "CR": ("5", "6", "7", "8"),
    "CRC": ("0", "1"),
    "LDRO": ("AUTO", "0", "1"),
    "TXMODE": ("MANAGED", "DIRECT"),
    "TXQUEUE": ("0", "1"),
    "CADMONITOR": ("0", "1"),
    "CADTXAFTERTIMEOUT": ("0", "1"),
}

# Params rendered as a bounded number input (min, max). Server-side validation is authoritative
# (daemon_control.validate_set); these bounds are a client-side convenience only.
NUMERIC_RANGE = {
    "FREQ": ("150", "960"),        # MHz (decimal)
    "PREAMBLE": ("6", "65535"),
    "POWER": ("0", "20"),
    "CADRSSI": ("-130", "0"),
    "CADWAIT": ("50", "5000"),
    "CADIDLE": ("0", "2000"),
}
# SYNC is free text (hex or decimal byte).

# Short per-field help shown next to each input.
PARAM_DESC = {
    "MODE": "Modulation — LoRa (FSK switches off LoRa and breaks the stacks).",
    "FREQ": "Centre frequency, MHz.",
    "SF": "Spreading factor 7–12 (higher = longer range, slower).",
    "BW": "Bandwidth, kHz.",
    "CR": "Coding rate 4/x, 5–8 (higher = more error correction).",
    "CRC": "Payload CRC: on (1) / off (0).",
    "LDRO": "Low-data-rate optimize: 0 / 1 / AUTO.",
    "PREAMBLE": "Preamble length, symbols.",
    "SYNC": "LoRa sync word / network id, e.g. 0x12.",
    "POWER": "TX power, dBm (0–20).",
    "TXMODE": "MANAGED = listen-before-talk; DIRECT = transmit immediately.",
    "TXQUEUE": "Queue outgoing frames instead of dropping when busy: 0 / 1.",
    "CADMONITOR": "Continuous channel-activity monitor: 0 / 1.",
    "CADRSSI": "Channel-busy RSSI threshold, dBm (-130..0).",
    "CADWAIT": "Longest listen-before-talk wait, ms.",
    "CADIDLE": "Confirmed channel-idle window before TX, ms.",
    "CADTXAFTERTIMEOUT": "Transmit anyway after a CAD/LBT timeout: 0 / 1.",
}

# Daemon base defaults for params no client forces. Band-specific under "433"/"868";
# band-independent under "_". Values match the daemon's own boot defaults.
_BASE = {
    "433": {"FREQ": "433.175"},
    "868": {"FREQ": "869.525"},
    "_": {"MODE": "LORA", "LDRO": "AUTO", "CADWAIT": "1500", "CADIDLE": "250",
          "TXQUEUE": "1", "CADMONITOR": "0", "CADRSSI": "-90", "CADTXAFTERTIMEOUT": "0"},
}

# The LoRaHAM amateur profile chat/igate/kiss all SET (band-independent).
_LORAHAM = {"TXMODE": "MANAGED", "SF": "12", "BW": "125.0", "CR": "5",
            "CRC": "1", "LDRO": "AUTO", "PREAMBLE": "8", "SYNC": "0x12", "POWER": "17"}

# Per-stack, per-band values the app SETs (from each app's source). "*" = any band.
STACK_DEFAULTS: dict[str, dict[str, dict[str, str]]] = {
    "chat":  {"*": dict(_LORAHAM)},
    "igate": {"*": dict(_LORAHAM)},
    "kiss":  {"*": dict(_LORAHAM)},
    "voice": {
        "433": {"TXMODE": "DIRECT", "FREQ": "434.700", "SF": "7", "BW": "125.0", "CR": "5",
                "CRC": "1", "LDRO": "AUTO", "PREAMBLE": "8", "SYNC": "0x12", "POWER": "17"},
        "868": {"TXMODE": "DIRECT", "FREQ": "869.525", "SF": "11", "BW": "250.0", "CR": "5",
                "CRC": "1", "LDRO": "AUTO", "PREAMBLE": "16", "SYNC": "0x2B", "POWER": "10"},
    },
    "meshcom": {
        # Shared across bands (MeshCom firmware: LoRa, MANAGED, CRC on, ~28 ms single-CAD).
        "*": {"MODE": "LORA", "TXMODE": "MANAGED", "CRC": "1", "LDRO": "AUTO",
              "SYNC": "0x2B", "PREAMBLE": "8", "CADIDLE": "28"},
        # DACH MeshCom 433 (firmware region OE/UK): SF10 / BW125 / CR4:6.
        "433": {"FREQ": "433.175", "SF": "10", "BW": "125.0", "CR": "6", "POWER": "17"},
        # MeshCom 868 (firmware region 5): SF11 / BW250 / CR4:6.
        "868": {"FREQ": "869.525", "SF": "11", "BW": "250.0", "CR": "6", "POWER": "10"},
    },
    "meshcore": {
        "868": {"TXMODE": "MANAGED", "FREQ": "869.618", "SF": "8", "BW": "62.5", "CR": "8",
                "CRC": "1", "LDRO": "AUTO", "PREAMBLE": "8", "SYNC": "0x12", "POWER": "20"},
    },
    # The daemon has no app overwriting it; its base config = the LoRaHAM amateur baseline
    # (the daemon boots with only FREQ set, so these give it a sane, editable starting radio
    # config). Never greyed. FREQ / CADWAIT / CADIDLE come from _BASE.
    "daemon": {"*": {"TXMODE": "MANAGED", "SF": "12", "BW": "125.0", "CR": "5",
                     "CRC": "1", "PREAMBLE": "8", "SYNC": "0x12", "POWER": "17"}},
}

# Stacks that talk to the daemon (get a panel). The `daemon` stack itself is handled per-radio
# (its base config, nothing greyed). Direct-SPI stacks (meshtastic) have no daemon panel.
CLIENT_STACKS = ("chat", "igate", "kiss", "voice", "meshcom", "meshcore")


def is_client(stack_id: str) -> bool:
    return stack_id in CLIENT_STACKS


def default_value(stack_id: str, band: str, name: str) -> str:
    """The value the app will set (or the daemon base) for `name` on `band` — the displayed
    default. Empty string when nothing defines it."""
    per = STACK_DEFAULTS.get(stack_id, {})
    for key in (band, "*"):
        if name in per.get(key, {}):
            return per[key][name]
    if name in _BASE.get(band, {}):
        return _BASE[band][name]
    return _BASE["_"].get(name, "")


def _app_owned(stack_id: str, band: str, name: str) -> bool:
    """True when the client app SETs this param after start (so the panel greys it, and it is
    overwritten). The daemon has no app (never greyed); LBT timing is never app-owned."""
    if not is_client(stack_id) or name in OPERATOR_PARAMS:
        return False
    per = STACK_DEFAULTS.get(stack_id, {})
    return any(name in per.get(key, {}) for key in (band, "*"))


def stack_view(stack_id: str, band: str, overrides: dict[str, str] | None = None) -> list[dict]:
    """Grouped, ordered parameter rows for one stack+band. Every param is editable; `overrides`
    are persisted operator values. Each row: name, group, value (override or default), default,
    app_owned (greyed hint — still applied, but the app overwrites it), desc."""
    overrides = overrides or {}
    rows: list[dict] = []
    for group, names in (("radio", RADIO_PARAMS), ("lbt", LBT_PARAMS)):
        for name in names:
            dv = default_value(stack_id, band, name)
            num = NUMERIC_RANGE.get(name)
            rows.append({"name": name, "group": group,
                         "value": overrides.get(name) or dv, "default": dv,
                         "app_owned": _app_owned(stack_id, band, name),
                         "desc": PARAM_DESC.get(name, ""),
                         "choices": list(DROPDOWN_CHOICES.get(name, ())),
                         "num": {"min": num[0], "max": num[1]} if num else None})
    return rows

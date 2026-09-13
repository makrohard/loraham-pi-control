"""RF logs — ONE registry behind the log page, its switches and Clear, and `lhpc rflog`.

Per stack, a persistent line-per-frame log of what the radio heard and sent, written at the
radio boundary by the stack's own process (the daemon, the KISS TNC, the MeshCom bridge, the
Reticulum LoRa driver, the MeshCore host) — meshtasticd writes its native per-packet JSON
trace instead. LHPC owns the switch, the file name and the viewer; it decodes nothing.

The table below is the ONLY authorization source: a `logs/<job>` is an RF log exactly when it
is listed here — never by prefix, so `rf-made-up.log` is neither viewable as one nor clearable.
Each entry names the SURFACE (the stack the log page and the CLI call it), the config OWNER
(the stack whose store holds the `rf_log` switch, in its Settings), the WRITER (the component
whose run state is the page's badge and whose `logs_view` target the links use) and the job
file(s). Graywolf is the one proxy: its log page shows and saves the kiss-owned switch, because
the TNC is where graywolf's frames cross the radio boundary.

`rf_log` is a STACK-level, band-less setting (`service_params._BANDLESS_STACK_PARAMS`): the
daemon's FILES are per band, its SWITCH is not.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

RF_LOG_PARAM = "rf_log"
GROUP = "RF-Logs"
ON, OFF = "on", "off"
MAX_BYTES = 5 * 1024 * 1024         # the writers' cap; LHPC applies it to meshtastic itself
PREVIOUS_SUFFIX = ".1"              # the one retained previous segment: <job>.1

# The meshtastic derivation: the public switch sets a HIDDEN `Logging.TraceFile` file param.
MESHTASTIC_TRACE_PARAM = "trace_file"


@dataclass(frozen=True)
class Entry:
    surface: str          # the stack name the log page and `lhpc rflog` use
    owner: str            # the stack whose config store holds the switch
    writer: str           # the component that writes the file; its run state is the badge
    key: str              # the switch's bundle key on the owner: run `rf_log` / file `file_rf_log`
    jobs: tuple           # ((band, job), …) — band "" for a single file
    native: bool = False  # the process's own trace (meshtasticd): LHPC rolls it opportunistically
    decoder: str = ""     # the offline decoder that can open this log's encrypted payloads ("" = plaintext)

    @property
    def kind(self) -> str:
        return "file" if self.key.startswith("file_") else "run"

    def job(self, band: str = "") -> str | None:
        """The job for `band` ("" for a single-file entry); None when the band does not fit."""
        for b, j in self.jobs:
            if b == (band or ""):
                return j
        return None

    @property
    def banded(self) -> bool:
        return any(b for b, _j in self.jobs)


REGISTRY: tuple[Entry, ...] = (
    Entry("daemon", "daemon", "loraham-daemon", "rf_log",
          (("433", "rf-daemon-433.log"), ("868", "rf-daemon-868.log"))),
    Entry("graywolf", "kiss", "loraham-kiss-tnc", "rf_log", (("", "rf-kiss.log"),)),
    Entry("meshcom", "meshcom", "meshcom-bridge", "rf_log", (("", "rf-meshcom.log"),)),
    Entry("meshtastic", "meshtastic", "meshtastic", "rf_log", (("", "rf-meshtastic.log"),),
          native=True, decoder="meshtastic"),
    Entry("meshcore", "meshcore", "meshcore-node", "file_rf_log", (("", "rf-meshcore.log"),),
          decoder="meshcore"),
    Entry("reticulum", "reticulum", "rns", "file_rf_log", (("", "rf-reticulum.log"),),
          decoder="reticulum"),
)


def entry(surface: str) -> Entry | None:
    return next((e for e in REGISTRY if e.surface == surface), None)


def by_job(job: str) -> tuple[Entry, str] | None:
    """(entry, band) for a registered job, else None — the viewer/Clear authorization."""
    for e in REGISTRY:
        for band, j in e.jobs:
            if j == job:
                return e, band
    return None


def resolve_job(surface: str, band: str = "") -> tuple[Entry, str]:
    """(entry, job) for a CLI surface and band — the daemon needs its band, nobody else takes
    one. Raises ValueError with the message the CLI prints; used by `rflog_tail` and by
    `lhpc rflog --clear`, which must never read (and so never roll) before clearing."""
    e = entry(surface)
    if e is None:
        raise ValueError(f"'{surface}' has no RF log")
    if e.banded and not band:
        raise ValueError(f"'{surface}' logs per band: add --band 433|868")
    if not e.banded and band:
        raise ValueError(f"'{surface}' has one RF log; --band does not apply")
    job = e.job(band)
    if job is None:
        raise ValueError(f"'{surface}' has no RF log for band {band}")
    return e, job


def previous(job: str) -> str:
    return job + PREVIOUS_SUFFIX


def is_switch_key(key: str) -> bool:
    """A persisted key that IS the switch, in any of its shapes: flat run `rf_log`, flat file
    `file_rf_log`, or component-scoped `__r__<comp>__rf_log` / `__f__<comp>__rf_log`."""
    return key in (RF_LOG_PARAM, f"file_{RF_LOG_PARAM}") or key.endswith(f"__{RF_LOG_PARAM}")


def switch_key(stack_id: str) -> str:
    """The owner stack's FLAT band-less key for the switch (run or file form per its manifest)."""
    for e in REGISTRY:
        if e.owner == stack_id:
            return e.key
    return RF_LOG_PARAM


def switch_default(stacks, stack_id: str) -> str:
    """The manifest-declared default of an owner stack's switch ("on" unless the manifest
    says otherwise; "on" for a stack without the param, so a missing key never reads as off)."""
    for s in stacks:
        if s.id != stack_id:
            continue
        for c in s.components:
            for p in c.run_params:
                if p.name == RF_LOG_PARAM:
                    return p.default or ON
            if c.config_file:
                for p in c.config_file.params:
                    if p.name == RF_LOG_PARAM:
                        return p.default or ON
    return ON


def is_on(value) -> bool:
    return str(value or "").strip().lower() != OFF


def meshtastic_trace_file(runtime: str, on: bool) -> str:
    """meshtasticd's `Logging.TraceFile`: the absolute registry path when the switch is on,
    empty (the key is omitted) when off. One stack-specific rule, applied where the config is
    generated — not a generic transform framework."""
    e = entry("meshtastic")
    return f"{runtime}/logs/{e.job()}" if (on and e is not None) else ""


# --- records: the line contract parsed once, server-side ------------------------------------
# A record is what the log page's table and the decoders work on. Every record carries `key`, the
# hash of its raw line: the decoders echo it (N in, N out, keys matching), and the decoded-result
# cache is keyed by it — so a line is the same record wherever it sits (live file, `.1`, after a
# copy-truncate), and no rotation state is needed anywhere.

_LINE_RE = re.compile(
    r"^(?P<ts>\S+) (?P<dir>RX|TX) rssi=(?P<rssi>-?[\d.]+|-) snr=(?P<snr>-?[\d.]+|-) len=(?P<len>\d+)"
    r"(?: outcome=(?P<outcome>\S+))?(?: band=(?P<band>\S+))?(?: tnc2=\"(?P<tnc2>[^\"]*)\")?"
    r" hex=(?P<hex>[0-9a-f]*) ascii=\"(?P<ascii>.*)\"$")

RECORD_FIELDS = ("key", "raw", "ts", "dir", "rssi", "snr", "len", "outcome", "band", "summary",
                 "hex", "ascii")


def record_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", "surrogateescape")).hexdigest()[:32]


def _num(text):
    if text in (None, "", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_line(line: str) -> dict:
    """One RF-log line -> one record (pure). The common contract (five writers) and meshtastic's
    JSON object both map onto the same keys; anything else is `{"key", "raw"}` only — shown as a
    raw line, never dropped and never an exception."""
    raw = line.rstrip("\n")
    rec = {"key": record_key(raw), "raw": raw}
    m = _LINE_RE.match(raw)
    if m:
        g = m.groupdict()
        rec.update({"ts": g["ts"], "dir": g["dir"], "rssi": _num(g["rssi"]), "snr": _num(g["snr"]),
                    "len": int(g["len"]), "outcome": g["outcome"] or "", "band": g["band"] or "",
                    "summary": g["tnc2"] or "", "hex": g["hex"], "ascii": g["ascii"]})
        return rec
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except ValueError:
            return rec
        if not isinstance(obj, dict):
            return rec
        ts = obj.get("timestamp")
        try:
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts))) if ts else ""
        except (ValueError, OverflowError, OSError):
            iso = ""

        def node(v):
            try:
                v = int(v)
            except (TypeError, ValueError):
                return ""
            return "^all" if v == 0xFFFFFFFF else ("local" if v == 0 else f"!{v:08x}")
        rec.update({"ts": iso, "dir": "RX" if "rssi" in obj else "TX",
                    "rssi": _num(obj.get("rssi")), "snr": _num(obj.get("snr")),
                    "len": _int(obj.get("size")), "outcome": "", "band": "",
                    "summary": f"{node(obj.get('from'))} \u2192 {node(obj.get('to'))}".strip(),
                    "hex": str(obj.get("bytes") or "").lower(), "ascii": ""})
    return rec


def parse_lines(lines) -> list:
    return [parse_line(ln) for ln in lines]

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

`rf_log` is each instance's own setting (K6, DEC-W384k: in its `<sid>@<rid>.toml`); the stack-wide switch
entries (`lhpc rflog`, the log page) set every instance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass

RF_LOG_PARAM = "rf_log"
RADIO_FILE = "rf-daemon.log"
_RID = re.compile(r"r[1-9][0-9]?")
# a radio's own RF log one level under logs/: the daemon's per radio, and a listed instance's of a stack that may run
# instances (7f: kiss and meshcore write logs/{instance_dir}rf-<x>.log; chat writes none)
RADIO_JOB = re.compile(r"(r[1-9][0-9]?)/(rf-daemon|rf-kiss|rf-meshcore|rf-meshcom|rf-meshtastic)\.log")   # K7: every instance
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
    per_radio: bool = False  # stage L5: the file is the radio's, `<rid>/rf-daemon.log` (W7-8a); `jobs` stay legacy
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

    def radio_job(self, rid) -> str | None:
        """The radio's own job `<rid>/rf-daemon.log` (W7-8a, B4-D23), only for r1..r99 and a per-radio entry."""
        if self.per_radio and isinstance(rid, str) and _RID.fullmatch(rid):
            return f"{rid}/{RADIO_FILE}"
        return None

    @property
    def banded(self) -> bool:
        return any(b for b, _j in self.jobs)


REGISTRY: tuple[Entry, ...] = (
    # the band files are the 0.13.x daemon's, kept read-only while they exist (I11); the live files are per radio
    Entry("daemon", "daemon", "loraham-daemon", "rf_log",
          (("433", "rf-daemon-433.log"), ("868", "rf-daemon-868.log")), per_radio=True),
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
    """(entry, band) for a registered job, (entry, rid) for a radio's own `<rid>/rf-daemon.log` (a fullmatch, one
    level under logs/, W7-8a), else None — the viewer/Clear authorization."""
    for e in REGISTRY:
        for band, j in e.jobs:
            if j == job:
                return e, band
    m = RADIO_JOB.fullmatch(job) if isinstance(job, str) else None
    if m:
        name = job.partition("/")[2]
        e = next((x for x in REGISTRY if (x.per_radio and name == RADIO_FILE) or (not x.banded and x.job() == name)), None)
        if e is not None:
            return e, m.group(1)
    return None


PRESENT, ABSENT, UNKNOWN = "present", "absent", "unknown"


def radio_dir_state(paths, job: str) -> str:
    """B4-D34 n9: a radio job's directory `logs/<rid>` is PRESENT (a real directory, never a symlink: the
    viewer's leaf read is O_NOFOLLOW, this covers the level above it), ABSENT (nothing there), or UNKNOWN (a
    symlink, a file, a containment refusal or any other error — never read as absent). PRESENT for every other
    job, which has no such level."""
    import os
    import stat
    m = RADIO_JOB.fullmatch(job) if isinstance(job, str) else None
    if not m:
        return PRESENT
    logs = paths.runtime_root / "logs"
    try:                                                 # logs/ itself first, before anything is looked up under it
        top = os.lstat(logs)
    except FileNotFoundError:
        return ABSENT
    except Exception:
        return UNKNOWN
    if not stat.S_ISDIR(top.st_mode):                    # a link (or a file): judged by where it leads (B5-D21)
        try:
            target = paths.under("logs")
        except Exception:                                # it escapes the runtime root
            return UNKNOWN
        if not os.path.isdir(target):                    # dangling, or not a directory
            return UNKNOWN
    try:
        st = os.lstat(logs / m.group(1))
    except FileNotFoundError:
        return ABSENT
    except Exception:                                    # unreadable (EACCES, ELOOP, ENOTDIR above, …)
        return UNKNOWN
    if not stat.S_ISDIR(st.st_mode):
        return UNKNOWN                                   # a symlink or a file is never this radio's directory
    try:
        paths.under("logs", m.group(1))                  # and it stays inside the runtime root
    except Exception:
        return UNKNOWN
    return PRESENT


def radio_dir_ok(paths, job: str) -> bool:
    """Only a PRESENT directory is listed or read (radio_dir_state)."""
    return radio_dir_state(paths, job) == PRESENT


def resolve_job(surface: str, rid: str = "") -> tuple[Entry, str]:
    """(entry, job) for a CLI surface and radio (K8, W308/W310: `<stack>@<rid>`, never a band): the daemon's file and an
    instance's are the radio's `<rid>/<file>`; a one-node stack (Reticulum) has one file. Raises ValueError with the
    message the CLI prints; used by `rflog_tail` and by `lhpc rflog --clear`, which must never read (and so never roll)
    before clearing."""
    e = entry(surface)
    if e is None:
        raise ValueError(f"'{surface}' has no RF log")
    job = e.radio_job(rid) if e.per_radio else (f"{rid}/{e.job()}" if rid else e.job())
    per_instance = e.per_radio or bool(RADIO_JOB.fullmatch(f"r1/{e.job()}"))
    if not rid and per_instance:
        raise ValueError(f"'{surface}' logs per radio: name one, lhpc rflog {surface}@<rid>")
    if job is None or (rid and not per_instance) or (rid and not _RID.fullmatch(rid)):
        raise ValueError(f"'{surface}@{rid}' has no RF log" if rid else f"'{surface}' has no RF log")
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


def meshtastic_trace_file(runtime: str, on: bool, rid: str = "") -> str:
    """meshtasticd's `Logging.TraceFile`: the absolute registry path when the switch is on (meshtastic@rN's own,
    logs/<rid>/rf-meshtastic.log, K7: two instances never share a trace), empty (the key is omitted) when off. One
    stack-specific rule, applied where the config is generated — not a generic transform framework."""
    e = entry("meshtastic")
    return f"{runtime}/logs/{rid + '/' if rid else ''}{e.job()}" if (on and e is not None) else ""


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
        v = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError, OverflowError):
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
                    "len": _int(g["len"]), "outcome": g["outcome"] or "", "band": g["band"] or "",
                    "summary": g["tnc2"] or "", "hex": g["hex"], "ascii": g["ascii"]})
        return rec
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            return rec
        if not isinstance(obj, dict):
            return rec
        ts = obj.get("timestamp")
        try:
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts))) if ts else ""
        except (TypeError, ValueError, OverflowError, OSError):
            iso = ""

        def node(v):
            try:
                v = int(v)
            except (TypeError, ValueError, OverflowError):
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


@dataclass(frozen=True)
class Job:
    key: str              # the radio id, or the band of a 0.13.x band file
    job: str              # the job under logs/
    label: str
    readonly: bool        # True for a 0.13.x band file: shown while it exists, never written again (I11)


def list_jobs(radios, logs_dir_entries) -> tuple:
    """The daemon's RF-log jobs to list (W7-8a, B4-D23/D24): one per enabled radio, in numeric id order
    ("daemon r1 (433)"), then each 0.13.x `rf-daemon-<band>.log` that exists among `logs_dir_entries` (the names
    in logs/), flagged read-only. Pure: the caller reads the directory and the hardware list."""
    e = next(x for x in REGISTRY if x.per_radio)
    names = set(logs_dir_entries)
    out = [Job(r.id, e.radio_job(r.id), f"{e.surface} {r.id} ({r.band})", False)
           for r in sorted(radios, key=lambda r: int(r.id[1:])) if r.enabled and e.radio_job(r.id)]
    out += [Job(band, job, f"{e.surface} {band} (0.13.x, read-only)", True) for band, job in e.jobs if job in names]
    return tuple(out)


def prune_targets(entries, keep_rule) -> list:
    """The names among `entries` a job-log prune may delete: never a registered RF log in either form (a band
    file or a radio's `<rid>/rf-daemon.log`, which is persistent by contract), and never what `keep_rule(name)`
    keeps. Pure; the caller deletes."""
    return [n for n in entries if by_job(n) is None and not keep_rule(n)]


# --- a listed copy's own RF log (moved from instances.py, G1-C27 #7: RF-log presentation, not instance identity) ---

def log_runs(svc, e, rid: str) -> bool:
    """7f: the RF log badge of a listed instance's own log `<rid>/rf-<x>.log`: that instance's run state."""
    from . import instances
    inst, why = instances.copy_of(svc, e.owner, rid)
    comp = svc.stack(e.owner).component(e.writer) if svc.stack(e.owner) else None
    return not why and comp is not None and instances.instance_runs(svc, comp, inst, inst.band)


def meshcore_decode_args(svc, sid: str, rid: str = "") -> list:
    """The MeshCore RF decoder's mode, secrets and stores: the instance's own (7f: its mode, config/secrets/<rid>,
    state/<rid>/…); without a radio, the stack's (a log from the slot builds)."""
    from . import instances
    own = (rid,) if rid else ()
    mode = svc._rflog_meshcore_mode(instances.copy_of(svc, sid, rid)[0] if rid else None)   # K8f: the job's node
    return ["--mode", mode, "--secrets", str(svc._paths.under("config", "secrets", *own)),
            "--store", str(svc._paths.under("state", *own, "meshcore", "companion.db")),
            "--repeater-store", str(svc._paths.under("state", *own, "openhop"))]


def radio_jobs(paths) -> tuple:
    """(jobs, unreadable): the per-radio RF logs on disk, `<rid>/<file>` (RADIO_JOB, W7-8a, K7: every instance), in name
    order, a lone previous segment `<file>.1` naming its job too (G1-W5-RE N3): what Clear all empties besides the
    registered band-less jobs (DEC-W384bo). A radio directory that vanishes meanwhile is skipped; an unreadable one is
    named, `rN (reason)` (N4, DEC-W384bs), never a silent success. An absent logs/ has none."""
    try:
        dirs = sorted(d for d in paths.under("logs").iterdir() if _RID.fullmatch(d.name) and d.is_dir() and not d.is_symlink())
    except FileNotFoundError:
        return [], []
    jobs, unreadable = [], []
    for d in dirs:
        try:
            names = sorted(f.name for f in d.iterdir())
        except FileNotFoundError:
            continue
        except OSError as exc:
            unreadable.append(f"{d.name} ({(exc.strerror or type(exc).__name__).lower()})")
            continue
        jobs += [j for j in dict.fromkeys(f"{d.name}/{n.removesuffix(PREVIOUS_SUFFIX)}" for n in names) if RADIO_JOB.fullmatch(j)]
    return jobs, unreadable


def switcher(svc, job: str, rid: str = "") -> dict:
    """W4 (DEC-W384bh): the RF log page's two rows for registered `job`: every enabled radio, and the RF logs of the
    shown radio, its daemon's `<rid>/rf-daemon.log` and every registered stack's `<rid>/<file>` (a stack whose declared
    bands hold the radio's band; its 0.13.x band-less file too). The shown radio: `rid` when it is an enabled radio,
    else the job's own radio, else one the shown stack runs on, else the first on its bands, else the first one."""
    from . import instances, radio_start
    e, key = by_job(job)
    rows, own = radio_start.view_radios(svc._paths), svc._declared_bands(svc.stack(e.surface))
    rids = [r for r, _n, _b in rows]
    shown = next((r for r in (rid, key, *sorted(instances.recorded(svc, e.surface)), *(r for r, _n, b in rows if b in own))
                  if r in rids), rids[0] if rids else "")
    band, stacks = radio_start.rf_band(svc, shown), []
    for x in svc._rflog_entries():
        if x.banded:
            jobs = [(x.radio_job(shown) or x.job(band), f"{x.surface} {shown}")]
        elif band and band in svc._declared_bands(svc.stack(x.surface)):
            jobs = [(x.job(), x.surface), (f"{shown}/{x.job()}", f"{x.surface} {shown}")]
        else:
            continue
        stacks += [{"target": x.writer, "job": j, "surface": x.surface, "label": label, "current": j == job}
                   for j, label in jobs if j and by_job(j)]

    def pill_job(r, b):                       # G1-W5 B1: a radio's pill opens that radio's own file, never the shown one's
        if e.banded:
            return e.radio_job(r) or e.job(b)
        own = f"{r}/{job.partition('/')[2]}"
        return (own if by_job(own) else e.job()) if RADIO_JOB.fullmatch(job) else job   # G1-W5-RE B1: by its shape
    radios = [{"rid": r, "band": b, "current": r == shown, "target": e.writer, "job": pill_job(r, b)} for r, _n, b in rows]
    return {"radio": shown, "radios": radios, "stacks": stacks}


def no_switch(target: str, stack) -> str:
    """Why `target` has no RF log switch of its own (DEC-W384ci): a `<stack>@<rid>` of a stack with one is that stack's
    one switch for all its radios, while `stack` (the service's lookup) knows its owner; anything else has no RF log."""
    sid = target.partition("@")[0]
    if sid != target and (e := entry(sid)) is not None and stack(e.owner) is not None:     # G1-K8F-S1 N1
        return f"Cannot switch the RF log of {target}: the switch is {sid}'s, one for all its radios (switch '{sid}')."
    return f"'{target}' has no RF log."

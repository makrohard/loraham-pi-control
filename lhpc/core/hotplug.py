"""W248/W249: ONE dashboard banner listing the radio hardware's hotplug changes since its ✕ was last pressed.

Each dashboard render compares the USB scan (usb_access.scan: the sticks of the model table's USB rows) with what
was seen at the previous render, and adds one short line per change: a new stick no radio holds, a bound stick
unplugged, back, or moved. No process watches USB: a change between two renders that undoes itself is not seen.
`state/hotplug.json` keeps what was seen and the lines not yet closed (the last 20); it is the one store, and its
writers hold its own lock (`hotplug.json.lock`, a flock taken without waiting: a dashboard GET never contends with a
config save; a busy lock skips that write and the render still shows the stored lines). The lock is a file of its
own because the store is replaced by rename, and a flock on a replaced file locks nothing (DEC-W42). With no file,
"seen" starts from the hardware list, each radio's stick at its binding, so the first render names the sticks no
radio holds and the radios whose stick is missing. The ✕ deletes exactly the lines it was shown from the list; a
later change shows only its own line. A listing that was not read, or a stick whose serial cannot be read, changes
nothing (it is never "unplugged").
"""
from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager

from . import radio_models, radios, runtime_fs
from .paths import PathContainmentError

FILE = ("state", "hotplug.json")
MAX_EVENTS = 20


class Busy(Exception):
    """Another writer holds the store's lock."""


@contextmanager
def _locked(paths):
    """The store's own lock, without waiting (DEC-W42): Busy when it is held."""
    fh = runtime_fs.open_lock(paths, paths.under(FILE[0], FILE[1] + ".lock"))
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy() from None
        yield
    finally:
        fh.close()


def _ident(serial: str, port: str) -> str:
    """A stick's identity: its serial's first characters as the daemon compares them, else its port."""
    return f"serial:{serial[:radios.SERIAL_PREFIX]}" if serial else f"port:{port}"


def observe(scan, models, rows=()) -> dict | None:
    """{ident: (port, serial)} of the sticks of a USB model on this scan; None when it cannot be trusted. A stick on
    the port a port-bound radio names is that port's, whatever serial it reports (G1-C9)."""
    from .usb_access import Probe
    if scan.listing is not Probe.PRESENT:
        return None
    ids = {(m.usb["vid"], m.usb["pid"]) for m in models.values() if m.usb}
    bound = {r.usb_port for r in rows if r.usb_port and not r.usb_serial}
    out = {}
    for d in scan.devices:
        if d.vid.state is not Probe.PRESENT or d.pid.state is not Probe.PRESENT:
            continue
        if (d.vid.value.lower(), d.pid.value.lower()) not in ids:
            continue
        if d.serial.state is Probe.UNKNOWN:
            return None
        serial = d.serial.value if d.serial.state is Probe.PRESENT else ""
        out[f"port:{d.port}" if d.port in bound else _ident(serial, d.port)] = (d.port, serial)
    return out


def _holder(rows, ident: str) -> str:
    for r in rows:
        if r.usb_serial and ident == _ident(r.usb_serial, ""):
            return r.id
        if not r.usb_serial and r.usb_port and ident == f"port:{r.usb_port}":
            return r.id
    return ""


def baseline(rows) -> dict:
    """"seen" without a file: each USB radio's stick at its binding."""
    out = {}
    for r in rows:
        if r.usb_serial:
            out[_ident(r.usb_serial, "")] = ("", r.usb_serial)
        elif r.usb_port:
            out[f"port:{r.usb_port}"] = (r.usb_port, "")
    return out


def changes(seen: dict, now: dict, rows) -> list:
    """The lines for the step from `seen` to `now` ({ident: (port, serial)}), in port order."""
    gone = {i: v for i, v in seen.items() if i not in now}
    came = {i: v for i, v in now.items() if i not in seen}
    lines = []
    moved = set()
    lost = [(i, _holder(rows, i)) for i in gone if i.startswith("port:") and _holder(rows, i)]
    fresh = [i for i in came if i.startswith("port:") and not _holder(rows, i)]
    if len(lost) == 1 and len(fresh) == 1:                  # a port-bound radio's stick on another port
        (old, rid), new = lost[0], fresh[0]
        lines.append((now[new][0], f"{rid} moved from port {seen[old][0]} to {now[new][0]} (no serial): re-bind it"))
        moved = {old, new}
    for i, (port, serial) in sorted(came.items(), key=lambda kv: kv[1][0]):
        if i in moved:
            continue
        rid = _holder(rows, i)
        what = f"serial {serial}" if serial else "no serial"
        lines.append((port, f"{rid} back on port {port}" if rid
                      else f"new stick: {what} on port {port}, not in the hardware list"))
    for i, (port, serial) in sorted(gone.items(), key=lambda kv: kv[1][0]):
        if i in moved:
            continue
        rid = _holder(rows, i)
        if rid:
            lines.append((port, f"{rid} unplugged" + (f" (serial {serial})" if serial else f" (port {port})")))
    for i, (port, _serial) in sorted(now.items(), key=lambda kv: kv[1][0]):
        if i in seen and seen[i][0] and seen[i][0] != port and (rid := _holder(rows, i)):
            lines.append((port, f"{rid} moved from port {seen[i][0]} to {port}"))
    return [line for _port, line in lines]


def _path(paths):
    return paths.under(*FILE)


def _read(paths) -> dict | None:
    try:
        data = json.loads(runtime_fs.read_text_regular(paths, _path(paths), max_bytes=65536))
    except FileNotFoundError:
        return None
    except (OSError, PathContainmentError, ValueError, RecursionError):
        return None                                  # corrupt reads as absent: rewritten from what is seen now
    if not (isinstance(data, dict) and isinstance(data.get("seen"), dict) and isinstance(data.get("events"), list)):
        return None
    seen = {i: tuple(v) for i, v in data["seen"].items()
            if isinstance(i, str) and isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v)}
    events = [e for e in data["events"] if isinstance(e, dict) and all(isinstance(e.get(k), str)
                                                                         for k in ("id", "at", "line"))]
    return {"seen": seen, "events": events, "seq": data.get("seq") if isinstance(data.get("seq"), int) else 0}


def _write(paths, state) -> bool:
    try:
        runtime_fs.atomic_write(paths, _path(paths), json.dumps(
            {"seen": {i: list(v) for i, v in state["seen"].items()}, "events": state["events"][-MAX_EVENTS:],
             "seq": state["seq"]}), 0o600)
        return True
    except (OSError, PathContainmentError):
        return False


def banner(svc, scan=None, now=None) -> dict:
    """The dashboard's banner: {"lines": [{"id", "at", "line"}]} of the changes not yet closed; [] = no banner.
    It never raises: it renders on the home page, so a source that fails (the scan, the model table, the hardware
    list) gives no banner this time, never an error page."""
    try:
        return _banner(svc, scan, now)
    except Exception:                                # the home page must render without it
        return {"lines": []}


def _banner(svc, scan, now) -> dict:
    """Scans, compares and records this render's changes under the store's own lock, so a scan is never recorded
    after a newer one (G1-C9); a busy lock records nothing this time, and a render with nothing to record writes
    nothing. A hardware list that does not read back skips the comparison: its radios cannot be told (G1-C9)."""
    from . import usb_access
    try:
        with _locked(svc._paths):
            state_list = radios.load(svc._paths)
            seen_now = (None if state_list.kind == radios.INVALID else
                        observe(usb_access.scan() if scan is None else scan, radio_models.load(), state_list.radios))
            state = _read(svc._paths) or {"seen": baseline(state_list.radios), "events": [], "seq": 0}
            rows = state_list.radios
            if seen_now is not None and seen_now != state["seen"]:
                at = time.strftime("%H:%MZ", time.gmtime(time.time() if now is None else now))
                for line in changes(state["seen"], seen_now, rows):
                    state["seq"] += 1
                    state["events"].append({"id": str(state["seq"]), "at": at, "line": line})
                state["seen"] = seen_now
                _write(svc._paths, state)
    except (Busy, OSError, PathContainmentError):
        state = _read(svc._paths) or {"events": []}         # nothing recorded this time; the render still shows
    return {"lines": state["events"][-MAX_EVENTS:]}


def dismiss(svc, ids) -> bool:
    """The ✕: delete exactly the lines it showed (their ids) from the list. False when the lock is busy or the write
    failed (the lines then stay, shown again)."""
    ids = {i for i in ids if isinstance(i, str) and i.isdigit()}
    try:
        with _locked(svc._paths):
            state = _read(svc._paths)
            if state is None:
                return True
            state["events"] = [e for e in state["events"] if e["id"] not in ids]
            return _write(svc._paths, state)
    except (Busy, OSError, PathContainmentError):
        return False

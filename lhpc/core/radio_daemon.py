"""The daemon per radio (plan A §2.7, stage L5): socket paths, the daemon's params, the /proc scan's decisions, the
start-time checks of its STATUS, the exit-code actions, the serial launch order, the old-config banner state and the
boot-restore plan. Pure functions over the hardware list (`lhpc/core/radios.py`); the lifecycle calls them.

Every answer that can be "cannot tell" is an explicit `Tri` (QUALITY-RULES-v1 rule 2); every pattern is matched whole.
"""
from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from . import radios as radios_mod
from .paths import PathContainmentError

SOCKET_PATH_MAX = 107                        # bytes, daemon v10 §8 (S1)
BUSY_PREFIX = "[CH341] refused: BUSY:"       # A:308
BUSY_DELAYS_S = (2.5, 5.0, 10.0)             # A:308
SPI_VERIFY_S = 4.0                           # A:301
DAEMON_BINARY = "loraham_daemon"
_RID = re.compile(r"r[1-9][0-9]?")
_BANDS = ("433", "868")


class Tri(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SocketPaths:
    conf: str            # <dir>/loraconf-<rid>.sock
    data: str            # <dir>/lora-<rid>.sock (the raw socket)
    frame: str           # <dir>/loraf-<rid>.sock (the framed socket)


@dataclass(frozen=True)
class DaemonProc:
    pid: int
    kind: str            # "id" | "no-id" | "unknown"
    rid: str = ""
    band: str = ""
    reason: str = ""


def socket_paths(rid: str, socket_dir: str) -> SocketPaths:
    """A radio's three sockets; ValueError when rid is no r1 … r99 or a path exceeds SOCKET_PATH_MAX bytes."""
    if not (isinstance(rid, str) and _RID.fullmatch(rid)):
        raise ValueError(f"{rid!r} is no radio id")
    paths = SocketPaths(*(os.path.join(socket_dir, f"{p}-{rid}.sock") for p in ("loraconf", "lora", "loraf")))
    for p in (paths.conf, paths.data, paths.frame):
        if len(p.encode()) > SOCKET_PATH_MAX:
            raise ValueError(f"{rid}: the socket path {p} is {len(p.encode())} bytes, more than {SOCKET_PATH_MAX}")
    return paths


def check_sockets(radios, socket_dir: str) -> list:
    """Every enabled radio's socket paths fit (S1); run at save and at start, never by load."""
    out = []
    for r in radios:
        if r.enabled:
            try:
                socket_paths(r.id, socket_dir)
            except ValueError as exc:
                out.append(str(exc))
    return out


def daemon_params(radio, model, settings, *, rf_log: str) -> dict:
    """The daemon component's per-process params for its run_argv (D13, T-D1, T-CO9)."""
    v = radios_mod.variant(radio, model)
    if v is None:
        raise ValueError(f"{radio.id}: no variant of {model.id} for band {radio.band}")
    return {"radio": radio.band, "hw": v.preset, "txmode": settings.txmode, "cadmon": settings.cadmon,
            "cadrssi": str(settings.cadrssi), "hipower": "on" if settings.hipower else "off",
            "ocp": str(radios_mod.effective_ocp(settings)), "rf_log": rf_log, "id": radio.id,
            "usb_serial": radio.usb_serial[:radios_mod.SERIAL_PREFIX] if radio.usb_serial else "",
            "rflog_path": "{runtime}/logs/" + radio.id + "/rf-daemon.log"}


def daemon_entry(exe, exe_err, argv, argv_err, comm, comm_err, binary: str = DAEMON_BINARY) -> Tri:
    """Whether one /proc entry is the daemon, from the caller's three reads (a value, or None with the errno name).
    ESRCH counts as ENOENT. YES when a readable source names the binary (the exe basename with one trailing
    " (deleted)" stripped, argv[0]'s basename, or comm), which covers a zombie whose argv is []; NO when every read
    vanished, for another program, or for a kernel thread; UNKNOWN otherwise."""
    errs = [e for e in (exe_err, argv_err, comm_err) if e]
    gone = lambda e: e in ("ENOENT", "ESRCH")          # noqa: E731
    names = []
    if isinstance(exe, str):
        base = os.path.basename(exe)
        names.append(base.removesuffix(" (deleted)"))
    if isinstance(argv, list) and argv and isinstance(argv[0], str):
        names.append(os.path.basename(argv[0]))
    if isinstance(comm, str):
        names.append(comm.strip())
    if binary in names:
        return Tri.YES
    if exe is None and argv is None and comm is None and errs and all(gone(e) for e in errs):
        return Tri.NO
    if names:                                           # a readable source names another program
        return Tri.NO
    if exe is None and argv == [] and not (isinstance(comm, str) and comm.strip() == binary):
        return Tri.NO                                   # a kernel thread
    return Tri.UNKNOWN


def parse_daemon_proc(pid: int, argv) -> DaemonProc:
    """A confirmed daemon's argv: exactly one --id <rid> (or --id=<rid>) → "id"; else exactly one --radio 433|868 →
    "no-id"; else "unknown" with the reason. Whole tokens only, never a substring."""
    if not isinstance(argv, list) or not argv:
        return DaemonProc(pid, "unknown", reason="the command line cannot be read")
    ids, bands = _option(argv, "--id"), _option(argv, "--radio")
    if ids:
        if len(ids) == 1 and _RID.fullmatch(ids[0]):
            return DaemonProc(pid, "id", rid=ids[0])
        return DaemonProc(pid, "unknown", reason=f"--id {ids!r} is not exactly one radio id")
    if len(bands) == 1 and bands[0] in _BANDS:
        return DaemonProc(pid, "no-id", band=bands[0])
    return DaemonProc(pid, "unknown", reason=f"neither --id nor one --radio 433|868 ({bands!r})")


def _option(argv, name):
    out = []
    for i, tok in enumerate(argv):
        if tok == name:
            out.append(argv[i + 1] if i + 1 < len(argv) else "")
        elif isinstance(tok, str) and tok.startswith(name + "="):
            out.append(tok[len(name) + 1:])
    return out


def start_refusal(radio, procs) -> str | None:
    """Why this radio's daemon may not start, or None. The caller has removed this rid's verified-owned daemon."""
    for p in procs:
        if p.kind == "id" and p.rid == radio.id:
            return f"this radio's daemon already runs elsewhere (pid {p.pid})"
    for p in procs:
        if p.kind == "no-id" and p.band == radio.band:
            return (f"a daemon without --id runs on band {radio.band} (pid {p.pid}, left from 0.13.x): stop it first")
    for p in procs:
        if p.kind == "unknown":
            return f"a daemon process cannot be identified (pid {p.pid}: {p.reason})"
    return None


def check_status_id(status: dict, rid: str) -> str | None:
    """`ID=` must equal rid (D10); the caller never signals the process on that socket."""
    got = status.get("ID")
    return None if got == rid else f"wrong daemon on this socket: expected {rid}, got {got or 'missing'}"


def _pct_decode(text: str):
    out, i = bytearray(), 0
    while i < len(text):
        if text[i] == "%":
            h = text[i + 1:i + 3]
            if len(h) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", h):
                return None
            out.append(int(h, 16))
            i += 3
        else:
            out += text[i].encode()
            i += 1
    return out.decode("latin-1")


def check_bound(status: dict, radio, model) -> str | None:
    """BOUND= after READY against the stored binding (H10, A:170-176)."""
    if model.transport == "spi":
        want = "-"
    elif radio.usb_serial:
        want = "serial:" + radio.usb_serial[:radios_mod.SERIAL_PREFIX]
    else:
        want = "port:" + radio.usb_port
    raw = status.get("BOUND")
    if raw is not None and raw == want == "-":
        return None
    if isinstance(raw, str) and ":" in raw:
        kind, _, value = raw.partition(":")
        decoded = _pct_decode(value)
        if decoded is not None and f"{kind}:{decoded}" == want:
            return None
    return f"wrong stick at start: expected {want}, got {raw if raw is not None else 'missing'}"


def start_exit_action(code: int, stdout_tail: str, attempt: int) -> tuple:
    """(action, delay, line) for a daemon that exited during its start; total over every code (D5, D11, D12)."""
    line = next((ln.strip() for ln in reversed((stdout_tail or "").splitlines()) if ln.strip()), "")
    if code == 4 and line.startswith(BUSY_PREFIX):
        return ("retry", BUSY_DELAYS_S[attempt], "") if 0 <= attempt < len(BUSY_DELAYS_S) else ("fail", 0.0, line)
    if code == 3:
        return ("fail", 0.0, "this radio's daemon already runs elsewhere")
    if code < 0:
        return ("fail", 0.0, f"the daemon was killed by signal {-code}")
    if code == 0:
        return ("fail", 0.0, "the daemon exited (0) during its start")
    if not line:
        return ("fail", 0.0, f"the daemon exited ({code}) without a refusal line")
    if code in (1, 4, 5):
        return ("fail", 0.0, line)
    return ("fail", 0.0, f"the daemon exited ({code}): {line}")


def verify_bound_s(transport: str, n_usb: int) -> float:
    """How long a start waits for READY (D8, A:301-305)."""
    if transport == "spi":
        return SPI_VERIFY_S
    if transport == "usb-ch341":
        return round(5.4 * max(n_usb, 1) + 6.2, 3)
    raise ValueError(f"unknown transport {transport!r}")


def run_starts(radios, launch: Callable) -> tuple:
    """Start the enabled radios one at a time in numeric id order; a failure is reported and the next starts anyway
    (D1, D2). An Exception from `launch` is that radio's failure; KeyboardInterrupt and SystemExit propagate."""
    lines, ok_all = [], True
    for r in sorted((r for r in radios if r.enabled), key=lambda r: int(r.id[1:])):
        try:
            ok, line = launch(r)
        except Exception as exc:
            ok, line = False, f"{r.id}: {type(exc).__name__}: {exc}"
        lines.append(line)
        ok_all = ok_all and bool(ok)
    return lines, ok_all


def old_config_state(paths) -> Tri:
    """Whether the W12 banner applies (M1): the old `[radio]` table in config/local.toml and no hardware list."""
    try:
        os.lstat(radios_mod.path(paths))
        return Tri.NO
    except FileNotFoundError:
        pass
    except (OSError, PathContainmentError):
        return Tri.NO                                   # present but unreadable: load's "invalid", not this banner
    try:
        text = paths.under("config", "local.toml").read_text(encoding="utf-8")
    except FileNotFoundError:
        return Tri.NO
    except (OSError, UnicodeDecodeError, PathContainmentError):
        return Tri.UNKNOWN
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError, ValueError):
        return Tri.UNKNOWN
    return Tri.YES if isinstance(data.get("radio"), dict) else Tri.NO


def restore_plan(items: list, radios) -> tuple:
    """(kept, refused) of boot-restore items (D14, D15): a pre-0.14.0 band item without `rid` is dropped silently
    (W12); a band-less item without `rid` is kept; an item whose radio is gone or disabled is refused with its
    reason; the rest are kept in order."""
    by_id = {r.id: r for r in radios}
    kept, refused = [], []
    for it in items:
        rid = it.get("rid")
        if not rid:
            if it.get("band"):
                continue
            kept.append(it)
            continue
        r = by_id.get(rid)
        if r is None:
            refused.append((it, f"radio {rid} no longer exists"))
        elif not r.enabled:
            refused.append((it, f"radio {rid} is disabled"))
        else:
            kept.append(it)
    return kept, refused

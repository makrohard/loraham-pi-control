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


def daemon_params(radio, model, settings, *, rf_log: str, usb_port: str = "") -> dict:
    """The daemon component's per-process params for its run_argv (D13, T-D1, T-CO9). A USB stick is opened by
    `usb_port`, the port its serial is listed on now (DEC-W35, daemon 1.4.0 `--usb-port`); "" for SPI."""
    v = radios_mod.variant(radio, model)
    if v is None:
        raise ValueError(f"{radio.id}: no variant of {model.id} for band {radio.band}")
    return {"radio": radio.band, "hw": v.preset, "txmode": settings.txmode, "cadmon": settings.cadmon,
            "cadrssi": str(settings.cadrssi), "hipower": "on" if settings.hipower else "off",
            "ocp": str(radios_mod.effective_ocp(settings)), "rf_log": rf_log, "id": radio.id,
            "usb_port": usb_port,
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
    """A confirmed daemon's argv: exactly one --id <rid> (or --id=<rid>) → "id", with its one --radio 433|868 as its
    band ("" when the argv names none or several: the caller refuses it by name); else exactly one --radio 433|868 →
    "no-id"; else "unknown" with the reason. Whole tokens only, never a substring."""
    if not isinstance(argv, list) or not argv:
        return DaemonProc(pid, "unknown", reason="the command line cannot be read")
    ids, bands = _option(argv, "--id"), _option(argv, "--radio")
    if ids:
        if len(ids) == 1 and _RID.fullmatch(ids[0]):
            return DaemonProc(pid, "id", rid=ids[0], band=bands[0] if len(bands) == 1 and bands[0] in _BANDS else "")
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


def check_bound(status: dict, radio, model, port: str = "") -> str | None:
    """BOUND= after READY against the binding (H10, A:170-176), in daemon 1.4.0's form `<serial>@<port>` (`-` no
    serial, `?` unreadable). `port` is the port the start resolved (DEC-W35). A serial row needs its serial (its
    first 8 characters), or `?` (the library could not read it) on exactly that port; a serial-less row `-` on it."""
    raw = status.get("BOUND")
    if model.transport == "spi":
        return None if raw == "-" else f"wrong stick at start: expected -, got {raw if raw is not None else 'missing'}"
    serial = radio.usb_serial[:radios_mod.SERIAL_PREFIX] if radio.usb_serial else "-"
    want = f"{serial}@{port}"
    if isinstance(raw, str) and "@" in raw:
        got, _, got_port = raw.rpartition("@")
        got_serial = _pct_decode(got)                  # bytes outside !..~, "%" and "@" come as %XX (conf-protocol.md)
        if got_serial is not None and got_port == port and port and (
                got_serial == serial or (radio.usb_serial and got_serial == "?")):
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
    lp = None
    try:
        lp = paths.under("config", "local.toml")
        text = lp.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Q14: a path that exists (a dangling symlink too) but cannot be read is unreadable, never "a fresh box"
        return Tri.UNKNOWN if lp is None or radios_mod.exists_at_all(lp) else Tri.NO
    except (OSError, UnicodeDecodeError, PathContainmentError):
        return Tri.UNKNOWN
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError, ValueError):
        return Tri.UNKNOWN
    return Tri.YES if isinstance(data.get("radio"), dict) else Tri.NO


def restore_plan(items: list, radios) -> tuple:
    """(kept, refused) of boot-restore items (D14, D15): a pre-0.14.0 band item without `rid` is dropped silently
    (W12); a band-less item without `rid` is kept; an item whose radio is gone, disabled or on another band now is
    refused with its reason. Every other item (`<stack>@<rid>`, K4b) starts on its own radio, so that is all it needs.
    The rest are kept in order."""
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
        elif (band := it.get("band")) and r.band != band:
            refused.append((it, f"radio {rid} is on {r.band} MHz now, not {band} MHz"))
        else:
            kept.append(it)
    return kept, refused


# --- the wiring's pure decisions (INTERFACE-B4-W5 b2beded0; B4-WIRING-TABLES-v1 d9cc1faa) ------------------------

@dataclass(frozen=True)
class Target:
    outcome: str                    # "proceed" | "refuse"
    rid: str                        # the chosen rid when "proceed"; "" when "refuse"
    reason: str                     # "" when an explicit rid proceeds; else why (incl. the chosen rid)
    candidates: tuple = ()          # the rids considered, radio order
    next_command: str = ""
    kind: str = ""                  # SEVERAL for "two or more radios can run it" (package item 24); else ""


SEVERAL = "several"


@dataclass(frozen=True)
class StopPlan:
    signal: tuple                   # pids to signal: only verified own `--id <rid>` processes
    named: tuple                    # ((pid, reason), …): present, related, NOT signalled
    ok: bool                        # False when anything is named


_KINDS = ("id", "no-id", "unknown")
_LOCKED_FIELDS = ("model", "band", "ce", "usb_serial", "usb_port")


def _refuse(reason: str, candidates=(), next_command: str = "", kind: str = "") -> Target:
    return Target("refuse", "", reason, tuple(candidates), next_command, kind)


def _order(radios):
    return sorted(radios, key=lambda r: int(r.id[1:]))


# W12 (A:201): the one wording of the old-config case, for the start refusal and the console banner alike.
OLD_CONFIG_TEXT = ("the old [radio] configuration is present and nothing was converted: set up the radios in the "
                   "hardware list first")
OLD_CONFIG_UNREADABLE = "the old configuration cannot be read"
# PKT-D20 / item 16: the one wording for a band two radios share, for a band-addressed refusal, daemon.md, the Hardware
# page and the CHANGELOG (tests/repo/test_l5_gates).
SHARED_BAND_TEXT = ("two radios on one band each run their own daemon: address one by its radio id "
                    "(lhpc stack stop daemon@rN)")


def _unstartable(state, old_config) -> Target:
    """The W1-8 reason rule, shared by resolve_target and reticulum_rid."""
    if state.kind == radios_mod.ABSENT:
        if old_config is Tri.YES:
            return _refuse(OLD_CONFIG_TEXT, next_command="lhpc hardware")
        if old_config is Tri.UNKNOWN:
            return _refuse(OLD_CONFIG_UNREADABLE, next_command="lhpc hardware")
        return _refuse("no hardware list", next_command="lhpc hardware")
    return _refuse("; ".join(state.reasons) if state.reasons else "the hardware list is invalid")


def _given(x):
    """None and "" mean "not given" (INTERFACE-B4-W5 §4.4); returns (given, ok-type)."""
    if x is None or (x == "" and type(x) is str):
        return False, True
    return True, type(x) is str


def _runnable(radio, models, can_run):
    """True / False, or None when it cannot be told (the model is missing, or can_run raised)."""
    m = models.get(radio.model)
    if m is None:
        return None
    try:
        return bool(can_run(radio, m))
    except Exception:                                    # "cannot tell", never propagated
        return None


def _pick(cands, scope: str, stack_hint: str = "<stack>") -> Target:
    ids = tuple(r.id for r in cands)
    if len(cands) == 1:
        return Target("proceed", cands[0].id, f"the only enabled radio {scope}: {cands[0].id}", ids)
    if not cands:
        return _refuse(f"no enabled radio {scope}", ids, "lhpc hardware")
    named = ", ".join(f"{r.id} ({r.name})" for r in cands)
    return _refuse(f"{len(cands)} radios can run it {scope}: {named}; name one", ids, f"{stack_hint}@<rid>", SEVERAL)


def resolve_target(state, models, can_run, *, rid=None, band=None, running=(), fallback_rid=None,
                   old_config=Tri.NO) -> Target:
    """Which radio a radio-stack verb acts on (W1-1…W1-6, W1-8…W1-11). Never raises; never first-wins."""
    if not isinstance(state, radios_mod.State):
        return _refuse("state is not a hardware-list state")
    if not isinstance(models, dict):
        return _refuse("models is not a dict")
    if not callable(can_run):
        return _refuse("can_run is not callable")
    if not isinstance(old_config, Tri):
        return _refuse(f"old_config {old_config!r} is not a Tri")
    rid_given, rid_ok = _given(rid)
    band_given, band_ok = _given(band)
    fb_given, fb_ok = _given(fallback_rid)
    if not (rid_ok and band_ok and fb_ok):
        return _refuse("rid, band and fallback_rid must be strings")
    if not (isinstance(running, (tuple, list)) and all(type(x) is str for x in running)):
        return _refuse("running must be a tuple or list of rids")
    if not state.startable:
        return _unstartable(state, old_config)
    by_id = {r.id: r for r in state.radios}
    if rid_given:
        if not _RID.fullmatch(rid):
            return _refuse(f"{rid!r} is not r1..r99")
        r = by_id.get(rid)
        if r is None:
            return _refuse(f"{rid} is unknown")
        if not r.enabled:
            return _refuse(f"{rid} is disabled")
        ok = _runnable(r, models, can_run)
        if ok is None:
            return _refuse(f"{rid}: cannot tell whether the stack can run on it")
        if not ok:
            return _refuse(f"{rid}: the stack cannot run on {r.model}")
        if band_given and r.band != band:
            return _refuse(f"{rid} is on {r.band} MHz, not {band} MHz")
        return Target("proceed", rid, "", (rid,))
    if band_given and band not in _BANDS:
        return _refuse(f"{band!r} is not 433 or 868")
    scope = [r for r in _order(state.radios) if r.enabled and (not band_given or r.band == band)]
    cands = []
    for r in scope:
        ok = _runnable(r, models, can_run)
        if ok is None:
            return _refuse(f"{r.id}: cannot tell whether the stack can run on it", tuple(x.id for x in scope))
        if ok:
            cands.append(r)
    where = f"on {band} MHz" if band_given else "for this stack"
    if band_given:
        return _pick(cands, where)
    ids = [r.id for r in cands]
    live = [x for x in running if x in ids]
    notes = [f"{x} is not a candidate (ignored)" for x in running if x not in ids]
    if len(set(live)) == 1:
        return Target("proceed", live[0], "; ".join([f"the running instance is on {live[0]}", *notes]), tuple(ids))
    if fb_given and fallback_rid in ids:
        return Target("proceed", fallback_rid, f"{fallback_rid} kept from the interactive marker", tuple(ids))
    if fb_given:
        notes.append(f"the interactive marker's {fallback_rid} is not a candidate (ignored)")
    t = _pick(cands, where)
    return Target(t.outcome, t.rid, "; ".join([t.reason, *notes]), t.candidates, t.next_command, t.kind)   # L20


def stop_targets(rid, procs, owned_pids) -> StopPlan:
    """What a per-rid stop signals (W4-7): only a verified own `--id <rid>` process; the rest is named."""
    if not (isinstance(rid, str) and _RID.fullmatch(rid)):
        raise ValueError(f"{rid!r} is not r1..r99")
    owned = {p for p in owned_pids if type(p) is int}
    signal, named = [], []
    for p in procs:
        if not isinstance(p, DaemonProc) or p.kind not in _KINDS:
            named.append((getattr(p, "pid", None), "unrecognised process entry"))
            continue
        if p.kind == "unknown":
            named.append((p.pid, f"a daemon process cannot be identified: {p.reason}"))
        elif p.kind == "id" and p.rid == rid:
            if type(p.pid) is int and p.pid in owned:
                signal.append(p.pid)
            else:
                named.append((p.pid, f"an unverified process claims {rid}"))
    return StopPlan(tuple(signal), tuple(named), not named)


def reticulum_rid(state, models, band, *, old_config=Tri.NO) -> Target:
    """The one enabled SPI radio of `band` that Reticulum's direct-SPI interface claims (W4-9, W1-8)."""
    if not isinstance(old_config, Tri):
        return _refuse(f"old_config {old_config!r} is not a Tri")
    if not isinstance(state, radios_mod.State):
        return _refuse("state is not a hardware-list state")
    if not isinstance(models, dict):
        return _refuse("models is not a dict")
    if not state.startable:
        return _unstartable(state, old_config)
    if not (type(band) is str and band in _BANDS):
        return _refuse(f"{band!r} is not 433 or 868")
    scope = [r for r in _order(state.radios) if r.enabled and r.band == band]
    for r in scope:
        if r.model not in models:
            return _refuse(f"{r.id}: model {r.model} is unknown", tuple(x.id for x in scope))
    spi = [r for r in scope if models[r.model].transport == "spi"]
    usb = [r.id for r in scope if models[r.model].transport != "spi"]
    if not spi and usb:
        return _refuse(f"Reticulum drives SPI radios only; {band} MHz has only USB radios: {', '.join(usb)}",
                       tuple(usb), "lhpc hardware")
    return _pick(spi, f"on {band} MHz", "reticulum")


def settings_save_refusals(old, new, users, procs=(), owned_pids=frozenset()) -> list:
    """Why an edit of one radio's row may not be saved now (W5-8, H3, H4); [] = save, effective at the next start."""
    if old.id != new.id:
        raise ValueError(f"the edit changes the id {old.id} -> {new.id}")
    if not isinstance(users, (tuple, list)) or not all(type(u) is str for u in users):
        raise ValueError("users must be a tuple or list of names")
    if not isinstance(procs, (tuple, list)):
        raise ValueError("procs must be a tuple or list")  # noqa: TRY004 - the save caller maps ValueError (W5)
    owned = {p for p in owned_pids if type(p) is int}
    blockers = [f"used by {u} — stop it?" for u in users]
    for p in procs:
        if not isinstance(p, DaemonProc) or p.kind not in _KINDS:
            raise ValueError(f"unrecognised process entry {p!r}")
        if p.kind == "id" and p.rid == old.id:
            who = f"daemon@{old.id}" if type(p.pid) is int and p.pid in owned else f"an unverified process claiming {old.id}"
            blockers.append(f"used by {who} (pid {p.pid}) — stop it?")
        elif p.kind == "no-id" and p.band == old.band:
            blockers.append(f"used by a daemon without --id on {old.band} MHz (pid {p.pid}) — stop it?")
        elif p.kind == "unknown":
            blockers.append(f"used by an unidentified daemon process (pid {p.pid}) — stop it?")
    changed = [f for f in _LOCKED_FIELDS if getattr(old, f) != getattr(new, f)]
    return blockers if changed and blockers else []

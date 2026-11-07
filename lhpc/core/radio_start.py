"""The start plan of one radio's daemon (stage L5): the hardware list, the radio's settings and the /proc scan composed
into the daemon's run params or a refusal. Unused until the wiring table lands (B4-D7); launch, verify (READY, ID=,
BOUND=) and the exit actions stay with the wiring. A module of its own, so the service family keeps its line budget.
"""
from __future__ import annotations

import dataclasses

from . import config as cfgmod
from . import daemon_params as _dp
from . import radio_daemon, radio_models, radios, rflog
from .paths import PathContainmentError


def start_plan(svc, rid: str) -> tuple:
    """L5 (unused until the wiring table lands, B4-D7): what a start of radio `rid`'s daemon would launch, from
    the hardware list alone. Returns (params, refusal, notes): `params` the daemon's run params
    (radio_daemon.daemon_params) or None, `refusal` the reason it may not start or "", `notes` the legacy values
    not carried. Launch, verify (READY, ID=, BOUND=) and the exit actions stay with the wiring."""
    state = radios.load(svc._paths)
    if not state.startable:
        why = "; ".join(state.reasons) if state.reasons else "no hardware list (config/radios.toml)"
        return None, f"the hardware list is {state.kind}: {why}", ()
    radio = next((r for r in state.radios if r.id == rid), None)
    if radio is None:
        return None, f"radio {rid} is not in the hardware list", ()
    if not radio.enabled:
        return None, f"radio {rid} is disabled", ()
    model = radio_models.load()[radio.model]
    try:
        settings = _settings(svc, radio, model)
    except ValueError as exc:
        return None, str(exc), ()
    refusal = radio_daemon.start_refusal(radio, _daemon_procs(svc)) or usb_precheck(radio, model) or ""
    rf_log = svc._resolved_param_value("daemon", "run", svc.DAEMON_ID, rflog.RF_LOG_PARAM, radio.band)
    params = radio_daemon.daemon_params(radio, model, settings, rf_log=rf_log)
    return params, refusal, settings.notes


def launch_plan(svc, rid: str) -> tuple:
    """start_plan for the launch itself (package item 26): the plan, then the radio's RF log directory (ensure_rf_dir)
    created right before its daemon is spawned, only for a plan with no refusal. A plan alone writes nothing."""
    params, refusal, notes = start_plan(svc, rid)
    if params is not None and not refusal:
        refusal = ensure_rf_dir(svc, rid)
    return params, refusal, notes


def _settings(svc, radio, model):
    """The radio's resolved settings (radios.radio_settings) from its row, the daemon's old band params and the
    client stacks' old `dp_<band>_<PARAM>` values; ValueError as radio_settings raises it."""
    legacy = {k: str(v) for k, v in svc.stack_config("daemon").items()}
    legacy_dp = {}
    for sid in _dp.CLIENT_STACKS:
        stored = cfgmod.load_stack_config(svc._paths, sid)
        vals = {name: str(stored[f"dp_{radio.band}_{name}"]) for name in _dp.ALL_PARAMS
                if f"dp_{radio.band}_{name}" in stored}
        if vals:
            legacy_dp[sid] = vals
    defaults = {"txmode": "managed", "cadmon": "off", "cadrssi": "-90", "hipower": "off"}   # manifest defaults
    return radios.radio_settings(radio, model, legacy, legacy_dp, defaults)


def row_dp(svc, band: str) -> dict:
    """W5-6/W5-7 (G2-0.14.0-1 item 1): the daemon params of `band`'s one enabled radio — its row's `dp_*` and the old
    client values every holding stack agrees on — which its daemon gets after READY and on Apply. {} when the list
    does not resolve the band to one radio, or its settings do not resolve (that radio's start is refused)."""
    t = band_rid(svc, band)
    row = next((r for r in radios.load(svc._paths).radios if r.id == t.rid), None) if t.outcome == "proceed" else None
    model = radio_models.load().get(row.model) if row else None
    if model is None:
        return {}
    try:
        return dict(_settings(svc, row, model).dp)
    except ValueError:
        return {}


def ensure_rf_dir(svc, rid: str) -> str:
    """LEG-D32: the radio's RF log directory `logs/<rid>/` exists before its daemon starts, because the daemon opens
    `--rflog-path` at init and exits when it cannot. "" when it is there or was created; else the refusal. It is
    judged as rflog.radio_dir_state judges it: PRESENT is used; ABSENT is created as a real directory under the
    contained logs/ (runtime_fs: no component is followed through a link); UNKNOWN (an escaping or dangling
    logs/ link, a file, an unreadable path) is refused and nothing is created."""
    from . import runtime_fs
    job = f"{rid}/rf-daemon.log"
    state = rflog.radio_dir_state(svc._paths, job)
    if state == rflog.PRESENT:
        return ""
    if state != rflog.ABSENT:
        return f"{rid}: logs/{rid} cannot hold its RF log (a link, a file or unreadable); fix or remove it"
    try:
        runtime_fs.mkdir(svc._paths, "logs", rid)
    except (OSError, PathContainmentError) as exc:
        return f"{rid}: logs/{rid} cannot be created for its RF log: {exc}"
    return "" if rflog.radio_dir_state(svc._paths, job) == rflog.PRESENT else f"{rid}: logs/{rid} is not usable"


USB_SETUP_HINT = "set up USB access first: the Hardware section's USB setup (a copy-paste command)"


def usb_precheck(radio, model) -> str:
    """Table P (B5): "" when the radio may start, else the refusal. An SPI row always passes; a USB row is checked
    against the attached sticks and its node's access (usb_flow.start_precheck, which never raises). A node this
    account cannot open adds the USB setup's pointer (P10)."""
    from . import usb_access, usb_flow
    effective, _configured, euid = _groups()
    v = usb_flow.start_precheck(radio, model, usb_access.scan,
                                lambda d: usb_access.node_access(d, effective, euid))
    if v.outcome == "proceed":
        return ""
    return f"{radio.id}: {v.reason}" + (f"; {USB_SETUP_HINT}" if v.copybox else "")


def _groups() -> tuple:
    """(effective group names, the account's configured group names, euid) for the USB access checks, the names
    as frozensets (the library's input type). A group that cannot be named makes that set None, which the library
    reads as UNKNOWN: never a guessed "no access"."""
    import grp
    import os
    import pwd

    from . import usb_access
    euid = os.geteuid()
    try:
        effective = frozenset(usb_access._group_name(g) for g in {os.getegid(), *os.getgroups()})
    except (KeyError, OSError):
        effective = None
    try:
        pw = pwd.getpwuid(euid)
        configured = frozenset({g.gr_name for g in grp.getgrall() if pw.pw_name in g.gr_mem}
                               | {usb_access._group_name(pw.pw_gid)})
    except (KeyError, OSError):
        configured = None
    return effective, configured, euid


def doctor_usb(svc) -> list:
    """Table D (B5): the doctor's USB host lines for the hardware list's rows (usb_flow.doctor_usb_lines, never
    raises); [] without a valid list. The hotplug note's start record (Table K) is not written yet, so each USB
    radio's hotplug line reads unknown (a known gap of the B5 minimum)."""
    from . import usb_access, usb_flow
    state = radios.load(svc._paths)
    if state.kind != radios.VALID:
        return []
    models = radio_models.load()
    effective, configured, euid = _groups()
    probes = usb_flow.UsbProbes(
        scan=usb_access.scan, rule_state=lambda: usb_access.rule_state(models),
        ch341_module=usb_access.ch341_module, ch341_binding=usb_access.ch341_binding,
        plugdev=lambda: usb_access.plugdev(effective, configured), libusb=usb_access.libusb,
        node_access=lambda d: usb_access.node_access(d, effective, euid),
        hotplug_for=lambda rid: usb_access.Probe.UNKNOWN)
    return usb_flow.doctor_usb_lines(state.radios, models, probes)


def _daemon_procs(svc) -> list:
    """Every process in /proc that is, or cannot be told from, the daemon, as radio_daemon reads it (id / no-id /
    unknown). G2-0.14.0-1 item 2: every pid is judged (the command lines and the /proc listing) from all three sources
    (argv, the exe link, comm). A pid any readable source names as another program is not the daemon (a kernel
    thread, another user's process, G2-D12); one whose three sources are all unreadable while it still exists is
    "unknown", which the start and save rules refuse; one gone by the reads is not counted."""
    fs, argvs = svc._system.fs, svc._system.procfs.cmdlines()
    pids = set(argvs) | {int(e) for e in fs.listdir("/proc", 100_000) if e.isdigit()}
    out = []
    for pid in sorted(pids):
        argv = argvs.get(pid)
        exe = fs.readlink(f"/proc/{pid}/exe") or None
        comm = fs.read_text(f"/proc/{pid}/comm", 64) or None
        unread = "EUNREADABLE"          # the readers answer "" for every failure, so an error is only "not read"
        tri = radio_daemon.daemon_entry(exe, "" if exe else unread, argv, "" if argv is not None else unread,
                                        comm, "" if comm else unread)
        if tri == radio_daemon.Tri.YES:
            out.append(radio_daemon.parse_daemon_proc(pid, argv))
        elif tri == radio_daemon.Tri.UNKNOWN and fs.exists(f"/proc/{pid}"):
            out.append(radio_daemon.DaemonProc(pid, "unknown", reason=f"cannot tell whether pid {pid} is the daemon "
                                               "(its command line, exe and comm cannot be read)"))
    return out


def _radio_users(svc, radio) -> list:
    """The running stacks on `radio`'s band other than the daemon (slot 0: a band's one radio, B4-D39)."""
    return sorted(s.id for s in svc.stacks() if s.main != svc.DAEMON_ID and svc.stack_running(s.id)
                  and radio.band in ({svc.running_band(s.id, "")} | {c.band for c in s.components if c.band}))


def _account() -> str:
    import os
    import pwd
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return ""


def hardware_view(svc) -> dict:
    """B5 Hardware section: the hardware list's rows, each with its daemon's state on its own socket and, for a
    USB row, the sticks it can be bound to (usb_flow.usb_choices); the copybox when a USB row is listed."""
    from . import daemon_control, usb_access, usb_flow
    state = radios.load(svc._paths)
    models = radio_models.load()
    found = usb_access.scan()                       # the sticks to bind, and (B) to add as new USB radios
    rows = []
    for r in state.radios:
        m = models.get(r.model)
        v = daemon_control.read_view(svc._system, r.id)
        usb = m is not None and m.transport != "spi"
        rows.append({"rid": r.id, "name": r.name, "model": r.model, "band": r.band, "enabled": r.enabled,
                     "transport": m.transport if m else "unknown", "usb_serial": r.usb_serial,
                     "usb_port": r.usb_port, "reachable": v.reachable, "ready": v.ready,
                     "choices": usb_flow.usb_choices(found, m) if usb else None})
    addable = [(m.id, c) for m in models.values() if m.transport != "spi"
               for c in usb_flow.usb_choices(found, m).items if c.selectable and not _holder(state.radios, c)]
    box = usb_flow.copybox(svc._paths, _account(), models) if any(r["choices"] is not None for r in rows) else None
    return {"kind": state.kind, "reasons": list(state.reasons), "rows": rows, "addable": addable, "copybox": box}


def _holder(rows, choice) -> str:
    """The row that already holds a listed stick: by the serial's first 8 characters, else a serial-less row on
    its port (B4); "" when none."""
    for r in rows:
        if choice.serial and r.usb_serial[:radios.SERIAL_PREFIX] == choice.serial[:radios.SERIAL_PREFIX]:
            return r.id
        if not choice.serial and not r.usb_serial and r.usb_port == choice.port:
            return r.id
    return ""


def _with_list_lock(paths, busy, fn, *args):
    """W217: every read-modify-write of the hardware list runs whole under the interprocess config lock, so two
    overlapping writers (two console requests, or the console and `lhpc hardware`) never both read the same list
    and the last save never drops the other's row. A busy lock is refused with `busy(reason)`; nothing is written."""
    try:
        with cfgmod.config_lock(paths):
            return fn(*args)
    except cfgmod.ConfigLockBusy as exc:
        return busy(f"the hardware list is busy ({exc}); nothing was changed — try again")


def add_usb(svc, model_id: str, port: str) -> tuple:
    """The Hardware page's add (below), under the hardware list's lock (W217)."""
    return _with_list_lock(svc._paths, lambda why: (False, why, False), _add_usb, svc, model_id, port)


def _add_usb(svc, model_id: str, port: str) -> tuple:
    """0.14.0 B (REL14-D2, DESIGN-AB-v2 §2): add the stick listed on `port` as a new USB radio: the lowest free id,
    the model's only band, the stick's serial (else its port), named `<short> <band>`. Fail-closed: no list, an
    invalid one, an unknown model, a stick not listed or held by a row, no free id, or the list's own save checks
    refuse it. (ok, message, copybox) — copybox when this account cannot open the stick's node."""
    from . import usb_access, usb_flow
    state = radios.load(svc._paths)
    if state.kind == radios.ABSENT:
        return False, "there is no hardware list: set it up first (lhpc hardware <setup>)", False
    if state.kind != radios.VALID:
        return False, "the hardware list is not valid: " + "; ".join(state.reasons), False
    models = radio_models.load()
    m = models.get(model_id) if isinstance(model_id, str) else None
    if m is None or m.transport == "spi":
        return False, f"unknown USB model {model_id!r}", False
    found = usb_access.scan()
    choice = next((c for c in usb_flow.usb_choices(found, m).items if c.port == port), None)
    if choice is None:
        return False, f"no stick of {m.id} is listed on port {port}", False
    if not choice.selectable:
        return False, f"the stick on port {port} cannot be added: {choice.why}", False
    if holder := _holder(state.radios, choice):
        return False, f"the stick on port {port} is already {holder}'s: re-bind {holder} in its card instead", False
    if len(m.bands) != 1:
        return False, f"{m.id} runs {len(m.bands)} bands; adding it needs a band choice", False
    try:
        rid = radios.next_id(state.radios)
    except ValueError as exc:
        return False, str(exc), False
    band = m.bands[0]
    name = f"{m.short} {band}"
    if any(r.name.casefold() == name.casefold() for r in state.radios):
        name = radios.default_name(state.radios, m)
    row = radios.Radio(rid, name, m.id, band, usb_serial=choice.serial, usb_port="" if choice.serial else port)
    reasons = radios.save(svc._paths, (*state.radios, row), models)
    if reasons:
        return False, "; ".join(reasons), False
    device = next((d for d in found.devices if d.port == port), None)
    effective, _configured, euid = _groups()
    access = usb_access.node_access(device, effective, euid) if device else usb_access.Probe.UNKNOWN
    return True, f"{rid} added: {m.id} on {band} MHz, " + (f"serial {choice.serial}" if choice.serial
                                                          else f"port {port} (no serial)"), access is usb_access.Probe.ABSENT


def bind_usb(svc, rid: str, port: str) -> tuple:
    """The Hardware page's bind (below), under the hardware list's lock (W217)."""
    return _with_list_lock(svc._paths, lambda why: (False, f"{rid}: {why}", False), _bind_usb, svc, rid, port)


def _bind_usb(svc, rid: str, port: str) -> tuple:
    """B5 U-4…U-7: bind radio `rid` to the stick listed on `port` now; (ok, message, copybox). The save is the
    hardware list's own (radios.save); while the radio is in use it is refused, and nothing re-binds by itself."""
    from . import usb_access, usb_flow
    state = radios.load(svc._paths)
    old = next((r for r in state.radios if r.id == rid), None)
    model = radio_models.load().get(old.model) if old else None
    if old is None or model is None:
        return False, f"there is no USB radio {rid}", False
    found = usb_access.scan()
    choice = next((c for c in usb_flow.usb_choices(found, model).items if c.port == port), None)
    if choice is None:
        return False, f"no stick of {model.id} is listed on port {port}", False
    device = next((d for d in found.devices if d.port == port), None)
    effective, _configured, euid = _groups()
    access = usb_access.node_access(device, effective, euid) if device else usb_access.Probe.UNKNOWN
    owned = frozenset(r["pid"] for r in svc._lifecycle().owned_records(svc.DAEMON_ID)
                      if svc._lifecycle().verify_owned(r)[0])
    b = usb_flow.bind_choice(state, rid, choice, _radio_users(svc, old), _daemon_procs(svc), owned, access=access)
    if b.outcome != "proceed":
        return False, f"{rid}: {b.reason}", False
    reasons = radios.save(svc._paths, b.radios)
    if reasons:
        return False, f"{rid}: " + "; ".join(reasons), False
    return True, f"{rid} bound to the stick on port {port}" + (f" ({choice.serial})" if choice.serial else ""), b.copybox


def setup_radios(paths, setup_id: str) -> tuple:
    """`lhpc hardware <setup>`'s list write (below), under the hardware list's lock (W217)."""
    return _with_list_lock(paths, lambda why: (False, f"hardware list: not written: {why}"), _setup_radios,
                           paths, setup_id)


def _setup_radios(paths, setup_id: str) -> tuple:
    """0.14.0 (REL14-D2 A): an explicit `lhpc hardware <setup>` also writes the hardware list when there is none:
    one row per band of the catalog setup in band order, each the model whose variant runs that band's preset. It
    runs BEFORE the setup itself is saved; a refused save changes nothing. A list that exists in any state (valid,
    invalid, unreadable, a dangling link) is never touched, and the setup is still saved: it names the served bands
    (B4-D26). `unset` writes no list. (ok, line)."""
    setup = cfgmod.HW_SETUPS.get(setup_id)
    try:
        present = radios.exists_at_all(radios.path(paths))
    except Exception:                                    # a path refusal: never read as "absent"
        present = True
    if present:
        return True, "hardware list: exists, not changed (config/radios.toml names the radios; a daemon starts for each band the board setup serves)"
    if setup is None or not setup[1]:
        return True, "hardware list: stays absent (no radio in this setup)"
    models, rows = radio_models.load(), []
    for band, preset in sorted(setup[1].items()):
        hit = [(m, v) for m in models.values() for v in m.variants if v.preset == preset and v.band in ("", band)]
        if len(hit) != 1:
            return False, f"hardware list: not written (preset {preset} on {band} MHz names {len(hit)} model variants)"
        m, v = hit[0]
        several = len([x for x in m.variants if x.band in ("", band)]) > 1
        name = f"{m.short} {band}"                   # the band's own name (default_name follows the model's bands)
        rows.append(radios.Radio(radios.next_id(rows), name, m.id, band, ce=v.key if several else ""))
    reasons = radios.save(paths, rows, models)
    if reasons:
        return False, "hardware list: not written: " + "; ".join(reasons)
    return True, "hardware list written: " + ", ".join(f"{r.id} {r.name!r} ({r.model}, {r.band} MHz)" for r in rows)


def band_rid(svc, band: str) -> radio_daemon.Target:
    """W1-3/4/5 for the daemon (every radio runs it): the one enabled radio of `band`, or a refusal naming why
    (none, or two or more). A fresh load of the hardware list (W1-10)."""
    state = radios.load(svc._paths)
    return radio_daemon.resolve_target(state, radio_models.load(), lambda _r, _m: True, band=band,
                                       old_config=radio_daemon.old_config_state(svc._paths))


def daemon_rid(svc, band: str) -> radio_daemon.Target:
    """band_rid for the daemon start: a shared band's refusal names its next step, the hardware list
    (radio_daemon.SHARED_BAND_TEXT), not the `<stack>@<rid>` form 0.14.0 does not have."""
    t = band_rid(svc, band)
    if t.outcome == "refuse" and t.kind == radio_daemon.SEVERAL:       # item 24: the refusal's kind, not its count
        reason = t.reason.removesuffix("; name one")
        return dataclasses.replace(t, reason=f"{reason}; {radio_daemon.SHARED_BAND_TEXT}",
                                   next_command="config/radios.toml")
    return t


def daemon_key(svc, band: str) -> str:
    """The key of a band's daemon sockets (daemon_control.conf_socket): the rid of the band's one enabled radio
    when the hardware list resolves the band (stage L5, `loraconf-<rid>.sock`); else the band itself, which
    reaches only a 0.13.x daemon's socket (a start without a hardware list is refused, W1-8)."""
    return key_for(svc._paths, band)


def key_for(paths, band: str) -> str:
    """daemon_key from the runtime paths alone (the lifecycle has no service)."""
    if band not in ("433", "868"):
        return band                                       # a radio id, or a key daemon_control refuses
    t = radio_daemon.resolve_target(radios.load(paths), radio_models.load(), lambda _r, _m: True, band=band)
    return t.rid if t.outcome == "proceed" else band


def rf_path(paths, job: str):
    """The RF log's path under logs/ (W7-8a): a radio's `<rid>/rf-daemon.log` only below a real `logs/<rid>`
    directory, never through a symlinked one; PathContainmentError otherwise, as for any other escape."""
    if not rflog.radio_dir_ok(paths, job):
        raise PathContainmentError(f"logs/{job.split('/', 1)[0]} is not a directory")
    return paths.under("logs", job)


def daemon_rf_job(svc, e, band: str):
    """A banded entry's job for `band`: the per-radio entry's file of the band's one radio when the hardware list
    resolves the band (W7-8a), else the 0.13.x band file (read-only legacy, I11)."""
    if e.per_radio:
        job = e.radio_job(daemon_key(svc, band))
        if job:
            return job
    return e.job(band)


def rf_band(svc, key: str) -> str:
    """The band behind a job's key: a radio id's band from the hardware list, else the key as given."""
    if isinstance(key, str) and key[:1] == "r":
        state = radios.load(svc._paths)
        r = next((x for x in state.radios if x.id == key), None)
        if r is not None:
            return r.band
    return key


def daemon_rf_links(svc, e, band: str) -> list:
    """The dashboard's daemon RF-log links for `band` (W7-8a): rflog.list_jobs over the hardware list and the
    names in logs/, kept to the radios of `band` and that band's 0.13.x file."""
    state = radios.load(svc._paths)
    try:
        names = [p.name for p in svc._paths.under("logs").iterdir()]
    except (OSError, PathContainmentError):
        names = []
    mine = {r.id for r in state.radios if r.band == band} if state.startable else set()
    return [{"label": j.label, "target": e.writer, "job": j.job, "readonly": j.readonly}
            for j in rflog.list_jobs(state.radios if state.startable else (), names) if j.key in mine or j.key == band]


def verify_identity(svc, rid: str) -> str:
    """After READY (A:170-176, W3-7): the socket's STATUS must name this radio (`ID=`) and its binding (`BOUND=`);
    the reason it does not, or "". The process on that socket is never signalled for it."""
    from . import daemon_control
    status = daemon_control.read_view(svc._system, rid).status
    why = radio_daemon.check_status_id(status, rid)
    if why:
        return why
    state = radios.load(svc._paths)
    radio = next((r for r in state.radios if r.id == rid), None)
    if radio is None:
        return f"radio {rid} is no longer in the hardware list"
    return radio_daemon.check_bound(status, radio, radio_models.load()[radio.model]) or ""


def shared_band_refusal(svc, target: str, band: str, op: str):
    """W1-11 (B4-D39): a per-band daemon stop on a band of two or more enabled radios is refused, naming them;
    nothing is signalled. No radio-addressed stop exists before the per-instance stage, so the next command is the
    whole daemon's. None for any other target, band or list."""
    from .service_base import ActionResult
    sid = svc.stack_of(target)
    stk = svc.stack(sid) if sid else None
    if stk is None or stk.main != svc.DAEMON_ID or band not in ("433", "868"):
        return None
    rows = [r for r in radios.load(svc._paths).radios if r.enabled and r.band == band]
    if len(rows) < 2:
        return None
    names = ", ".join(f"{r.id} ({r.name})" for r in rows)
    return ActionResult(False, f"Cannot {op} the daemon of {band} MHz alone: {len(rows)} radios share it: {names}",
                        details=[f"  [blocked] {band} MHz: one daemon per radio; a radio is not addressed by id "
                                 "before the per-instance stage"],
                        next_commands=[f"lhpc stack {op} {sid}"])


def radio_name(paths, band: str) -> str:
    """A band's radio as a message names it (W4-2): its one enabled radio's id; with two or more, the band and
    every radio sharing its key (B4-D39: "868 (radios r1, r2)"); else the band."""
    key = key_for(paths, band)
    rids = [r.id for r in radios.load(paths).radios if r.enabled and r.band == band] if key == band else []
    return f"{band} (radios {', '.join(rids)})" if len(rids) > 1 else key


def claim_key(paths, band: str, prefix: str = "loraham.radio.") -> str:
    """The radio claim key of `band` by name (W4-4): its one enabled radio's id, else the band (key_for)."""
    return f"{prefix}{key_for(paths, band)}"


def chip_family(svc, band: str):
    """W5-2: the chip family of `band`'s one enabled radio, from its row's model variant (the preset its daemon
    runs with); None when the hardware list does not resolve the band to one radio (0.13.x: the band's preset)."""
    t = band_rid(svc, band)
    row = next((r for r in radios.load(svc._paths).radios if r.id == t.rid), None) if t.outcome == "proceed" else None
    model = radio_models.load().get(row.model) if row else None
    v = radios.variant(row, model) if model else None
    return None if row is None else cfgmod.hw_preset_family(v.preset if v else "")


def band_radios(svc, band: str) -> list:
    """W6-2/W6-3: every enabled radio of `band` in list order, each with its own daemon's view (its rid's CONF
    socket), so two radios of one band are two entries, never one band view; [] without a valid list."""
    from . import daemon_control
    out = []
    for r in (r for r in radios.load(svc._paths).radios if r.enabled and r.band == band):
        v = daemon_control.read_view(svc._system, r.id)
        out.append({"rid": r.id, "name": r.name, "model": r.model, "reachable": v.reachable, "ready": v.ready})
    return out


def radio_endpoints(paths, specs) -> list:
    """Each `{rid}` endpoint once per enabled radio of the hardware list (W6-2: two radios of one band are two
    entries); none without a valid list. Every other endpoint passes through unchanged."""
    rids = [r.id for r in radios.load(paths).radios if r.enabled]
    return [dataclasses.replace(s, address=s.address.replace("{rid}", rid))
            for s in specs for rid in (rids if "{rid}" in s.address else ("",))]


def client_instance(svc, comp, band: str) -> tuple:
    """(instance, refusal) for a component's start (W3, stage L5): a daemon client — a run param whose value
    template names `{rid}` — runs as the band's one radio's instance (W1-3/4/5); a refusal when the band has no
    such radio or two. Any other component keeps slot 0 without a radio."""
    from . import commands
    if not any("{rid}" in str(getattr(p, "default", "") or "") for p in getattr(comp, "run_params", ())):
        return commands.SLOT0, ""
    t = band_rid(svc, band)
    if t.outcome != "proceed":
        return None, f"no radio for its daemon sockets: {t.reason}"
    return commands.Instance(0, t.rid), ""

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
    port = ""
    if model.transport != "spi":                       # DEC-W35: the daemon opens the stick by its port now
        port, why = stick_port(radio.usb_serial, radio.usb_port)
        refusal = refusal or (f"{radio.id}: {why}" if why else "")
    params = radio_daemon.daemon_params(radio, model, settings, rf_log=rf_log, usb_port=port)
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


def row_dp(svc, band: str, rid: str = "") -> dict:
    """W5-6/W5-7 (G2-0.14.0-1 item 1): the daemon params of radio `rid`, or of `band`'s one enabled radio — its row's
    `dp_*` and the old client values every holding stack agrees on — which its daemon gets after READY and on Apply.
    {} when the list does not resolve the band to one radio, or its settings do not resolve (that radio's start is
    refused)."""
    t = band_rid(svc, band) if not rid else radio_daemon.Target("proceed", rid, "")
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


def stick_port(serial: str, port: str, scan=None) -> tuple:
    """DEC-W35: (port, "") of a USB radio's stick now, or ("", why). A serial is resolved to the port sysfs lists it
    on (the stick may have been replugged anywhere); a serial-less radio's stick is its stored port, which must be
    listed. Start, verify and Detect all use this one answer."""
    from . import usb_access
    found = usb_access.scan() if scan is None else scan
    if found.listing is not usb_access.Probe.PRESENT:
        return "", "the USB device list cannot be read"
    if serial:
        ports = [d.port for d in found.devices if d.serial.state is usb_access.Probe.PRESENT
                 and d.serial.value[:radios.SERIAL_PREFIX] == serial[:radios.SERIAL_PREFIX]]
        if not ports:
            return "", f"no stick with serial {serial[:radios.SERIAL_PREFIX]} is plugged in"
        if len(ports) > 1:
            return "", f"more than one stick reports serial {serial[:radios.SERIAL_PREFIX]}"
        return ports[0], ""
    if not any(d.port == port for d in found.devices):
        return "", f"no stick is plugged in on port {port}"
    return port, ""


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
    """The running stacks on `radio`'s band other than the daemon (slot 0: a band's one radio, B4-D39), and each
    listed copy on `radio` that runs, `<stack>@<rid>` (C7: the snapshot's slot 0 is no copy's)."""
    from . import commands, instances
    copies = [f"{s.id}@{i.rid}" for s in svc.stacks() for i in commands.stack_instances(s, svc._paths)[1:]
              if i.rid == radio.id and any(instances.instance_runs(svc, c, i, radio.band) for c in s.components if c.process)]
    return sorted([s.id for s in svc.stacks() if s.main != svc.DAEMON_ID and svc.stack_running(s.id)
                   and radio.band in ({svc.running_band(s.id, "")} | {c.band for c in s.components if c.band})] + copies)


def radio_activity(svc, radio, procs=None) -> list:
    """DEC-W39: what runs on `radio`, the ONE answer the remove's refusal, its confirm page and the card's greyed
    button all use: "its daemon" when its CONF socket answers or a process is (or cannot be told from) its daemon,
    then every running stack _radio_users names. [] = nothing runs on it."""
    daemon = ["its daemon"] if daemon_running(svc, radio, procs) else []
    return [*_radio_users(svc, radio), *daemon]


def daemon_running(svc, radio, procs=None) -> bool:
    """Whether `radio`'s daemon runs, the one answer Remove and Detect use (DEC-W39, DEC-W43): its CONF socket
    answers, or a process is its daemon (`--id <rid>`, a no-id daemon on its band) or cannot be told from one."""
    from . import daemon_control
    procs = _daemon_procs(svc) if procs is None else procs
    if any(p.kind == "unknown" or (p.kind == "id" and p.rid == radio.id) or (p.kind == "no-id" and p.band == radio.band)
           for p in procs):
        return True
    return daemon_control.read_view(svc._system, radio.id).reachable


def stop_first(rid: str, activity: list, verb: str = "remove") -> str:
    """The line a running radio's card and refusal show: "stop meshcore and stop its daemon, then remove r2"."""
    return " and ".join(f"stop {a}" for a in activity) + f", then {verb} {rid}"


def set_enabled(svc, rid: str, enabled: bool) -> tuple:
    """W257: the list row's Disable / Enable, under the hardware list's lock (W217). (ok, message)."""
    word = "enabled" if enabled else "disabled"
    return _with_list_lock(svc._paths, lambda why: (False, f"Not {word}: {why}"), _set_enabled, svc, rid, bool(enabled))


def _set_enabled(svc, rid: str, enabled: bool) -> tuple:
    """A disable is refused while the radio runs (radio_activity, the answer Remove uses); an enable is the list's
    own save checks' to refuse. Only the row's `enabled` changes; save() keeps the reserved ids."""
    word = "enabled" if enabled else "disabled"
    state = radios.load(svc._paths)
    if state.kind != radios.VALID:
        return False, f"Not {word}: the hardware list is not valid: " + "; ".join(state.reasons)
    row = next((r for r in state.radios if r.id == rid), None)
    if row is None:
        return False, f"Not {word}: there is no radio {rid}"
    if not enabled and (activity := radio_activity(svc, row)):
        return False, f"Not disabled: {stop_first(rid, activity, 'disable')}"
    reasons = radios.save(svc._paths, radios.with_row(state.radios, dataclasses.replace(row, enabled=enabled)))
    if reasons:
        return False, f"Not {word}: " + "; ".join(reasons)
    return True, f"{rid} {word}"


def _restore_records(svc, rid: str) -> list:
    """The ownership records of radio `rid` the next boot's planner would read (role "" with that rid); read only."""
    from . import boot_restore
    valid, _issues, _state = svc._lifecycle().owned_inventory()
    return [r for r in valid if r.get("role", "") == "" and boot_restore.rid_of(r.get("rid")) == rid]


def remove_plan(svc, rid: str) -> dict:
    """DEC-W39: what `Remove rid` would do, for the confirm page and the dry run (nothing is written): the row,
    the activity that blocks it, what goes, what stays. `row` None with `reason` when there is nothing to remove."""
    state = radios.load(svc._paths)
    if state.kind != radios.VALID:
        why = ("there is no hardware list" if state.kind == radios.ABSENT
               else "the hardware list is not valid: " + "; ".join(state.reasons))
        return {"rid": rid, "row": None, "reason": why}
    row = next((r for r in state.radios if r.id == rid), None)
    if row is None:
        return {"rid": rid, "row": None, "reason": f"{rid} is already removed (its id stays reserved)"
                if rid in state.retired else f"there is no radio {rid}"}
    activity = radio_activity(svc, row)
    goes = ["its row in the hardware list (config/radios.toml) and its binding"
            + (f" to the stick {row.usb_serial or 'on port ' + row.usb_port}" if row.usb_serial or row.usb_port else ""),
            f"its own daemon settings (the settings on {rid}'s row)"]
    goes += [f"{r.get('stack') or '?'} will not be restored at the next boot: radio {rid} no longer exists"
             for r in _restore_records(svc, rid)]
    stays = [f"the id {rid} stays reserved, so logs and ports stay unambiguous (DEC-W39)",
             *([f"kept folder {k}" + (" (its RF logs already written)" if k.endswith(f"/logs/{rid}") else "")
                for k in _kept_folders(svc, rid)] or [f"no folder of {rid} is on disk"]),
             "the stick itself: plugged in, it shows up again as a new stick",
             "the other radios and their stacks; the per-band stack settings (config/stacks/)"]
    return {"rid": rid, "row": row, "reason": "", "activity": activity, "goes": goes, "stays": stays,
            "state": state, "facts": _remove_facts(svc, row)}


def _kept_folders(svc, rid: str) -> list:
    """W253: the folders of radio `rid` a remove keeps, as they are on disk: logs/<rid> and every state/<x>/<rid>."""
    root = svc._paths.runtime_root
    found = [root / "logs" / rid, *sorted(p / rid for p in (root / "state").glob("*") if p.is_dir())]
    return [str(p) for p in found if p.is_dir() and not p.is_symlink()]


def _remove_facts(svc, row) -> dict:
    """W253: the confirm page's small block: the radio's state, its binding and the folders that are kept."""
    binding = (f"serial {row.usb_serial}" if row.usb_serial else f"port {row.usb_port} (no serial)" if row.usb_port
               else "SPI (the board's lines)")
    return {"state": "enabled" if row.enabled else "disabled", "binding": binding,
            "kept": _kept_folders(svc, row.id)}


def remove_radio(svc, rid: str, confirm: str):
    """DEC-W39: `lhpc hardware remove <rid>` and the card's Remove, under the hardware list's lock (W217)."""
    from .service_base import ActionResult
    return _with_list_lock(svc._paths, lambda why: ActionResult(False, f"{rid}: {why}", data={"reason": "busy"}),
                           _remove_radio, svc, rid, confirm)


def _remove_radio(svc, rid: str, confirm: str):
    from .service_base import ActionResult
    plan = remove_plan(svc, rid)
    if plan["row"] is None:
        return ActionResult(False, f"Not removed: {plan['reason']}", data={"reason": "no-radio"})
    if plan["activity"]:
        return ActionResult(False, f"{rid} cannot be removed while it runs: {stop_first(rid, plan['activity'])}",
                            data={"reason": "running"})
    details = [f"  [goes] {g}" for g in plan["goes"]] + [f"  [stays] {s}" for s in plan["stays"]]
    phrase = f"remove-{rid}"
    if confirm != phrase:
        return ActionResult(False, f"Not removed (dry run): type the confirm phrase '{phrase}' to remove {rid}",
                            details=details, next_commands=[f"lhpc hardware remove {rid} --confirm-phrase {phrase}"],
                            data={"reason": "dry-run"})
    state = plan["state"]
    reasons = radios.save(svc._paths, radios.without(state.radios, rid), retired=(*state.retired, rid))
    if reasons:
        return ActionResult(False, f"Not removed: {'; '.join(reasons)}", data={"reason": "save-refused"})
    return ActionResult(True, f"{rid} removed; its id stays reserved", details=details)


_RELOGIN = (" — this account's plugdev membership is granted but takes effect at its next login (log out and in, or"
            " reboot)")


def _access_line(msg: str, no_access: bool) -> tuple:
    """(message, copybox) after an add or a bind: the USB setup box is pointed at only while the setup is still needed
    (the page shows it then); with the rule current and plugdev granted but not yet effective there is no box, so the
    line says the membership takes effect at the next login (W18-1)."""
    from . import usb_access
    if not no_access:
        return msg, False
    effective, configured, _euid = _groups()
    rule = usb_access.rule_state(radio_models.load())
    if usb_access.setup_needed(rule, usb_access.plugdev(effective, configured)):
        return msg, True
    return msg + _RELOGIN, False


def _account() -> str:
    import os
    import pwd
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return ""


def hardware_view(svc) -> dict:
    """B5 Hardware section: the hardware list's rows, each with its daemon's state on its own socket and, for a
    USB row, the sticks it can be bound to (usb_flow.usb_choices); the copybox when a USB row is listed and the
    host setup is not set (usb_access.setup_needed)."""
    from . import daemon_control, usb_access, usb_flow
    state = radios.load(svc._paths)
    models = radio_models.load()
    found = usb_access.scan()                       # the sticks to bind, and (B) to add as new USB radios
    procs = _daemon_procs(svc)                      # one /proc read for every card's Remove (DEC-W39)
    rows = []
    for r in state.radios:
        m = models.get(r.model)
        v = daemon_control.read_view(svc._system, r.id)
        usb = m is not None and m.transport != "spi"
        rows.append({"rid": r.id, "name": r.name, "model": r.model, "band": r.band, "enabled": r.enabled,
                     "transport": m.transport if m else "unknown", "usb_serial": r.usb_serial,
                     "usb_port": r.usb_port, "reachable": v.reachable, "ready": v.ready,
                     "choices": usb_flow.usb_choices(found, m) if usb else None,
                     "activity": radio_activity(svc, r, procs), "problems": (pr := _problems(r, usb, v, found)),
                     "attention": _attention(pr, _radio_users(svc, r))})
    addable = [(m.id, c) for m in models.values() if m.transport != "spi"
               for c in usb_flow.usb_choices(found, m).items if c.selectable and not _holder(state.radios, c)]
    box = None
    if any(r["choices"] is not None for r in rows):            # W226: only where the USB host setup is not set
        effective, configured, _ = _groups()
        if usb_access.setup_needed(usb_access.rule_state(models), usb_access.plugdev(effective, configured)):
            box = usb_flow.copybox(svc._paths, _account(), models)
    return {"kind": state.kind, "reasons": list(state.reasons), "rows": rows, "addable": addable, "copybox": box}


def _problems(radio, usb: bool, view, found) -> list:
    """W257: a row's short notes, read-only facts in a fixed order (no Detect, no bus): its daemon offline (enabled,
    the CONF socket does not answer), then its stick not plugged in. A wrong band or a USB row with no binding never
    reaches here: the list's own checks make such a list invalid, and an invalid list has no rows."""
    out = ["daemon offline"] if radio.enabled and not view.reachable else []
    if usb:
        _port, why = stick_port(radio.usb_serial, radio.usb_port, found)
        if why.startswith("no stick"):
            out.append(f"stick not plugged in: serial {radio.usb_serial[:radios.SERIAL_PREFIX]}" if radio.usb_serial
                       else f"stick not plugged in on port {radio.usb_port}")
        elif why:
            out.append(why)
    return out


def _attention(problems: list, users: list) -> list:
    """W273: the row's notes that need the operator now, for the dashboard's "Needs attention" banner, taken from
    `problems` (one computation): its daemon offline while a stack on it runs, then its stick not plugged in."""
    out = []
    if "daemon offline" in problems and users:
        out.append(f"daemon offline while {', '.join(users)} need{'s' if len(users) == 1 else ''} it")
    return out + [p for p in problems if p.startswith("stick not plugged in")]


def radio_detail(svc, rid: str):
    """W257: the detail page of radio `rid`: its hardware_view row plus what Remove would do and what to stop first,
    and whether Detect is offered; None when the list holds no such radio (the route's 404)."""
    row = next((r for r in hardware_view(svc)["rows"] if r["rid"] == rid), None)
    if row is None:
        return None
    plan = remove_plan(svc, rid)
    return {**row, "remove": {"facts": plan.get("facts", {}), "activity": plan.get("activity", [])},
            "stop_first": stop_first(rid, row["activity"]) if row["activity"] else "", "detect": True}


def add_hat(svc, model_id: str, band: str, variant: str = "") -> tuple:
    """W257: Add Hardware's HAT row: one SPI radio added to the list, under its lock (W217)."""
    return _with_list_lock(svc._paths, lambda why: (False, f"Not added: {why}"), _add_hat, svc, model_id, band, variant)


def _add_hat(svc, model_id: str, band: str, variant: str) -> tuple:
    """(ok, message). The model must be a board (SPI) model with a variant for `band`; `variant` names it when the
    model has more than one (Uputronics ce0 / ce1), stored as the row's `ce`. The new row takes the next free
    non-reserved id (DEC-W39) and `<short> <band>`; the list's own checks decide the rest (a claimed pin refuses)."""
    state = radios.load(svc._paths)
    if state.kind == radios.ABSENT:
        return False, "Not added: there is no hardware list: set it up first (lhpc hardware <setup>)"
    if state.kind != radios.VALID:
        return False, "Not added: the hardware list is not valid: " + "; ".join(state.reasons)
    models = radio_models.load()
    m = models.get(model_id) if isinstance(model_id, str) else None
    if m is None:
        return False, f"Not added: there is no model {model_id!r}"
    if m.transport != "spi":
        return False, f"Not added: {m.id} is not a board (SPI) model"
    fits = [v for v in m.variants if band in m.bands and (not v.band or v.band == band)]
    if not fits:
        return False, f"Not added: {m.id} has no variant for {band} MHz"
    if not variant and len(fits) > 1:
        return False, f"Not added: {m.id} needs a variant ({', '.join(v.key for v in fits)})"
    v = next((x for x in fits if x.key == variant), None) if variant else fits[0]
    if v is None:
        return False, f"Not added: {m.id} has no variant {variant!r} for {band} MHz"
    several = bool(variant) or len(fits) > 1          # G1-C19: an explicit variant is always kept as `ce`
    try:
        rid = radios.next_id(state.radios, state.retired)
    except ValueError as exc:
        return False, f"Not added: {exc}"
    name = f"{m.short} {band}"
    if any(r.name.casefold() == name.casefold() for r in state.radios):
        name = radios.default_name(state.radios, m)
    row = radios.Radio(rid, name, m.id, band, ce=v.key if several else "")
    reasons = radios.save(svc._paths, (*state.radios, row), models)
    if reasons:
        return False, "Not added: " + "; ".join(reasons)
    return True, f"{rid} added: {m.id}" + (f" ({v.key})" if several else "") + f" on {band} MHz"


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
    # DEC-W21: the stick's chip must answer as the picked model's before anything is written (Detect, on its bus).
    from .radio_detect import run as detect
    d = detect.detect_stick(svc, m.id, port)
    if d.outcome != "found":
        return False, "Not added: " + d.line + ("" if d.outcome == "refused" else
                                                " — check the model; nothing was written"), False
    try:
        rid = radios.next_id(state.radios, state.retired)
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
    return (True, *_access_line(f"{rid} added: {m.id} on {band} MHz, " + (f"serial {choice.serial}" if choice.serial
                                                                         else f"port {port} (no serial)"),
                                access is usb_access.Probe.ABSENT))


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
    return (True, *_access_line(f"{rid} bound to the stick on port {port}" + (f" ({choice.serial})" if choice.serial
                                                                             else ""), b.copybox))


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


def client_rid(svc, sid: str, band: str, inst=None) -> str:
    """Item 16: the radio whose daemon a start of stack `sid` on `band` needs — a listed instance's own radio, else
    slot 0's (DEC-W16: the band's first enabled radio not listed for the stack). "" for the daemon's own start, and
    for no single band: then every enabled radio of the needed bands."""
    from . import instances
    if sid == (svc.stack_of(svc.DAEMON_ID) or "daemon"):
        return inst.rid if inst is not None else ""                 # `--radio rN`: that radio's daemon alone
    if band not in ("433", "868"):
        return ""
    return inst.rid if inst is not None and inst.slot else instances.slot0_radio(svc._paths, sid, band)[0]


def daemon_radio(svc, rid: str) -> tuple:
    """`--radio rN` of a daemon start, stop or restart (item 16): (the daemon's Instance on that radio, its band, "")
    or (None, "", why) when rN is not an enabled radio of the hardware list."""
    row = next((r for r in radios.load(svc._paths).radios if r.id == rid), None)
    if row is None or not row.enabled:
        return None, "", f"{rid} is " + ("not a radio of the hardware list" if row is None else "disabled")
    return daemon_inst(rid), row.band, ""


def daemon_dependents(svc, sid: str, band, inst, stopped_bands) -> list:
    """The dependents a stop of `sid` stops first. For the daemon (`band` not None) by radio, as instance keys
    (radio_dependents): on `band`'s radios, or with band "" on every radio of the list (G1-C18), else, when the list
    names no radio there, by band as before item 16. For any other stack (`band` None): its running dependents."""
    if band is not None:
        bands = [band] if band else sorted({r.band for r in radios.load(svc._paths).radios if r.enabled})
        found = [radio_dependents(svc, b, inst) for b in bands]
        if found and None not in found:
            return list(dict.fromkeys(d for f in found for d in f))         # each key once, in band order
    return svc.stop_dependents(sid, bands=stopped_bands)


def radio_clients(svc, band: str, rid: str, sid: str) -> tuple:
    """Item 16 (G1-C18): (the other stacks' running clients, does stack `sid` itself run) on radio `rid`, the radio
    whose retained daemon a start would set: activity on another radio of the band keeps nothing. By band when no
    radio is named or the list names none."""
    on = radio_dependents(svc, band, daemon_inst(rid)) if rid else None
    if on is None:
        daemon_sid = svc.stack_of(svc.DAEMON_ID) or "daemon"
        return [d for d in svc.stop_dependents(daemon_sid, bands={band}) if d != sid], svc._stack_running_on_band(sid, band)
    return [d for d in on if d.partition("@")[0] != sid], any(d.partition("@")[0] == sid for d in on)


def start_log_name(comp_id: str, band: str = "", rid: str = "") -> str:
    """The one name of a start's captured log (item 16): `start-<comp>[-<band>][-<rid>].log`. Every start that runs
    on a radio names it, slot 0 of a single radio too, so a second radio on the band never renames a log."""
    return f"start-{comp_id}" + (f"-{band}" if band else "") + (f"-{rid}" if rid else "") + ".log"


def band_rids(paths, band: str) -> list:
    """The enabled radios of `band`, by rid ([] without a list)."""
    return [r[0] for r in view_radios(paths) if r[2] == band]


def dp_updates(svc, sid: str, band: str, values: dict, chip, high_power, shown=None) -> tuple:
    """The daemon-param edits of a settings save, ({PARAM: canonical value, or "" to clear}, ""), or ({}, why): a
    param not in `values` is left out; a blank clears; a value is validated for the chip, then canonicalised, and one
    equal to its default clears. A radio's save (`shown`: the values its panel shows) keeps only what the operator
    changed, a default included; a value as shown stays inherited (G1-C21 B2)."""
    from . import daemon_control, daemon_params
    out = {}
    for name in daemon_params.ALL_PARAMS:
        if name not in values:
            continue
        raw = str(values[name]).strip()
        if raw and (err := daemon_control.validate_set(name, raw, chip, high_power)):
            return {}, f"{name}: {err}"
        canon = daemon_control.canonical_value(name, raw) if raw else ""
        if shown is None:
            out[name] = "" if canon == daemon_params.default_value(sid, band, name) else canon
        elif not raw or canon != shown.get(name, daemon_params.default_value(sid, band, name)):
            out[name] = canon
    return out, ""


def radio_params_view(svc, target: str, rid: str) -> dict:
    """Item 16 (DEC-W53): the daemon settings panel of radio `rid`: its row's dp_* over the band's saved values over
    the defaults, for the daemon's own page only; {} for a client stack's page or a rid that is no enabled radio."""
    from . import daemon_control, daemon_params
    _inst, band, why = daemon_radio(svc, rid)
    if why or not svc._is_daemon_target(target):
        return {}
    sid = svc._owner_stack_id(target)
    ov = {**svc._daemon_param_overrides(target, band), **row_dp(svc, band, rid)}
    return {"stack": target, "band": band, "rid": rid, "bands": [band], "all_bands": list(svc.RADIO_BANDS),
            "is_daemon": True, "can_apply": True, "radios": view_radios(svc._paths),
            "high_power": svc.high_power_state(band, daemon_control.read_view(svc._system, rid)),
            "rows": daemon_params.stack_view(sid, band, ov, svc.chip_family_for_band(band, rid),
                                             radio_high_power(svc, rid))}


def save_radio_params(svc, target: str, rid: str, values: dict):
    """Item 16 (DEC-W53): save the daemon settings of radio `rid` into its row of the hardware list (its dp_* fields
    only), under the list's lock (W217, set_row_dp); for the daemon's own page only. Never applies live."""
    from .service_base import ActionResult
    if not svc._is_daemon_target(target):
        return ActionResult(False, f"Cannot save the settings of {rid}: a radio's settings are the daemon's")
    return set_row_dp(svc, rid, None, (target, values))


def _radio_updates(svc, target: str, rid: str, values: dict) -> tuple:
    """(updates, why) of a radio's save, judged on the list as it reads under its lock (G1-C21 B1): the radio's band,
    chip and high-power permission, and the values its panel shows."""
    from . import daemon_control
    _inst, band, why = daemon_radio(svc, rid)
    if why:
        return {}, f"Cannot save the settings of {rid}: {why}"
    sid = svc._owner_stack_id(target)
    shown = {**svc._daemon_param_overrides(target, band), **row_dp(svc, band, rid)}
    return dp_updates(svc, sid, band, values, svc.chip_family_for_band(band, rid), radio_high_power(svc, rid),
                      {k: daemon_control.canonical_value(k, str(v)) for k, v in shown.items()})


def radio_high_power(svc, rid: str) -> bool:
    """Radio `rid`'s own high-power permission, as its daemon starts with it (its row, else the old band switch:
    radios.radio_settings); False when its settings do not resolve (its start is refused then)."""
    row = next((r for r in radios.load(svc._paths).radios if r.id == rid), None)
    model = radio_models.load().get(row.model) if row else None
    try:
        return bool(model and _settings(svc, row, model).hipower)
    except ValueError:
        return False


def set_row_dp(svc, rid: str, updates, save=None):
    """Write `updates` ({PARAM: value, "" clears}) into the dp_* fields of radio `rid`'s row and nothing else, under
    the hardware list's lock (W217); radios.save checks the whole list, keeps its reserved ids and writes it whole.
    `save` = (target, values): the updates of a settings save, validated under that lock (_radio_updates)."""
    from .service_base import ActionResult
    return _with_list_lock(svc._paths, lambda why: ActionResult(False, f"the hardware list is busy ({why}); nothing was "
                                                               "changed — try again"), _set_row_dp, svc, rid, updates, save)


def _set_row_dp(svc, rid: str, updates, save=None):
    from .service_base import ActionResult
    if save is not None:
        updates, why = _radio_updates(svc, save[0], rid, save[1])
        if why:
            return ActionResult(False, why)
    state = radios.load(svc._paths)
    row = next((r for r in state.radios if r.id == rid), None) if state.kind == radios.VALID else None
    if row is None:
        return ActionResult(False, f"{rid}'s settings not saved: " + (f"{rid} is not in the hardware list"
                                   if state.kind == radios.VALID else "the hardware list does not read back"))
    dp = {k: v for k, v in {**dict(row.dp), **updates}.items() if v}
    reasons = radios.save(svc._paths, radios.with_row(state.radios, dataclasses.replace(row, dp=tuple(sorted(dp.items())))),
                          radio_models.load())
    if reasons:
        return ActionResult(False, f"{rid}'s settings not saved: " + "; ".join(reasons))
    return ActionResult(True, f"saved the daemon settings of {rid}")


def view_radio(svc, band: str, rid: str) -> tuple:
    """Item 16 batch 2 (DEC-W53): (the socket key, "") of a radio-addressed daemon view: radio `rid`, an enabled radio
    of the hardware list on `band`; no rid gives the band's key, as before. ("", why) when rid is not one."""
    if not rid:
        return daemon_key(svc, band), ""
    _inst, rband, why = daemon_radio(svc, rid)
    if not why and rband != band:
        why = f"{rid} is on {rband} MHz, not {band} MHz"
    return ("", why) if why else (rid, "")


def view_radios(paths) -> list:
    """Every enabled radio of the hardware list as (rid, name, band), by rid: the per-radio views' chooser."""
    return [(r.id, r.name, r.band) for r in sorted(radios.load(paths).radios, key=lambda r: int(r.id[1:])) if r.enabled]


def radio_dependents(svc, band: str, inst=None):
    """Item 16: the running clients a stop of `band`'s daemons leaves without a daemon, by instance key — `<stack>`
    for slot 0, `<stack>@<rid>` for a listed instance — on radio `inst.rid` alone when given, else on each enabled
    radio of the band. None when the hardware list names no enabled radio of the band (the band's dependents are then
    read by band, as before)."""
    from . import commands, instances
    rows = {r.id for r in radios.load(svc._paths).radios if r.enabled and r.band == band}
    rids = {inst.rid} if inst is not None and inst.rid else rows
    if not rows or not rids <= rows:
        return None
    daemon_sid = svc.stack_of(svc.DAEMON_ID) or "daemon"
    slot0 = set(svc.stop_dependents(daemon_sid, bands={band}))
    out = []
    for stk in svc.stacks():
        if stk.id == daemon_sid or not any(svc.DAEMON_ID in c.depends_on for c in stk.components):
            continue
        if stk.id in slot0 and instances.slot0_radio(svc._paths, stk.id, band)[0] in rids:
            out.append(stk.id)
        for i in commands.stack_instances(stk, svc._paths)[1:]:
            if i.rid in rids and any(instances.instance_runs(svc, c, i, band) for c in stk.components if c.process):
                out.append(f"{stk.id}@{i.rid}")
    return out


def daemon_units(svc, bands, rid: str = "") -> tuple:
    """Item 16: (the (band, rid) daemons a start makes ready, one per radio, in band then rid order; "") — `rid`
    alone on its band, else every enabled radio of each band. A band without one gives (band, ""), whose launch is
    refused with band_rid's reason (no hardware list, no enabled radio). A `rid` that is no enabled radio of the
    bands is refused: ([], why) (G1-C18), never every radio's start."""
    rows = radios.load(svc._paths).radios
    out = []
    for b in bands:
        on = [r.id for r in sorted(rows, key=lambda r: int(r.id[1:])) if r.enabled and r.band == b]
        out += [(b, rid)] if rid in on else [] if rid else [(b, x) for x in on] or [(b, "")]
    if rid and not out:
        return [], f"{rid} is no enabled radio of {' or '.join(bands)} MHz"
    return out, ""


def daemon_inst(rid: str):
    """The Instance a radio's daemon launches as: slot 0 on that radio, so its ownership record names the radio and
    one radio's daemon can be stopped alone (item 16)."""
    from . import commands
    return commands.Instance(0, rid)


def ensure_radio(svc, life, stack, comp, unit, label: str, lines: list, *, requested_target: str = "",
                 start_scope: str = "") -> bool:
    """Launch one radio's daemon (`unit` = (band, rid)) and wait for it READY, its lines appended; False when it
    is refused or fails. Stage L5 / item 16: the radio's start plan from the hardware list (--id, --hw from the
    model, the radio's own settings and RF-log path: W7-1, W7-7, W7-8); a band without a radio is refused with
    band_rid's reason. Its record names the radio (daemon_inst)."""
    band, rid = unit
    params, refusal, _notes = launch_plan(svc, rid) if rid else (None, band_rid(svc, band).reason, ())
    if params is None or refusal:
        lines.append(f"  [BLOCKED] daemon on {label}: {refusal}")
        return False
    res = life.start(stack, comp, {**dict(svc.stack_config("daemon")), **params}, band=band,
                     requested_target=requested_target, start_scope=start_scope, inst=daemon_inst(rid))
    base = f"start daemon {rid} --radio {band}"
    if not res.ok:
        lines.append(f"  [fail] {base}: {res.detail}")
        return False
    lines.append(f"  [ok] {base}")
    if not svc._verify_band_up(rid):
        lines.append(f"  [fail] {label} CONF socket never came up — the daemon failed to init on {label} "
                     "(radio/SPI busy or a stale lock); see its log.")
        return False
    if why := verify_identity(svc, rid):                                    # W3-7: refused, never signalled
        lines.append(f"  [fail] daemon {rid}: {why}")
        return False
    return True


def daemon_claimed(svc, band: str, rid: str) -> bool:
    """Item 16: does an observed daemon process hold radio `rid` (or, with no rid, `band`)? A process with its own
    `--id` holds that radio; one without (a 0.13.x daemon) holds its band; one that cannot be told holds every radio."""
    for p in _daemon_procs(svc):
        if p.kind == "unknown" or (p.kind == "id" and rid and p.rid == rid) or (
                (p.kind != "id" or not rid) and p.band in (band, "")):
            return True
    return False


def daemon_key(svc, band: str) -> str:
    """The key of a band's daemon sockets (daemon_control.conf_socket), every read and every SET: socket_key."""
    return socket_key(svc._paths, band)


def socket_key(paths, band: str) -> str:
    """The key of a band's daemon SOCKETS (CONF and data): the rid of the band's one enabled radio when the hardware
    list resolves the band (stage L5, `loraconf-<rid>.sock`); the band itself only when there is NO hardware list (a
    0.13.x daemon's `loraconf<band>.sock`, the upgrade case); "" — no socket at all — when a list exists and does not
    resolve the band to one radio (two enabled radios, none, or an invalid list: ledger L27), so no read and no SET
    ever reaches the 0.13.x band form there. A key that is not a band passes through (a radio id)."""
    if band not in ("433", "868"):
        return band
    state = radios.load(paths)
    if state.kind == radios.ABSENT:                      # no hardware list: a 0.13.x daemon's band socket
        return band
    t = radio_daemon.resolve_target(state, radio_models.load(), lambda _r, _m: True, band=band)
    return t.rid if t.outcome == "proceed" else ""


def socket_refusal(paths, band: str) -> str:
    """Why `band` has no daemon socket (socket_key ""), naming the radios; "" when it has one."""
    if band not in ("433", "868") or socket_key(paths, band):
        return ""
    state = radios.load(paths)
    t = radio_daemon.resolve_target(state, radio_models.load(), lambda _r, _m: True, band=band)
    reason = t.reason.removesuffix("; name one") + (f"; {radio_daemon.SHARED_BAND_TEXT}"
                                                    if t.kind == radio_daemon.SEVERAL else "")
    return f"the hardware list names no one radio for {band} MHz: {reason}"


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
        job = e.radio_job(key_for(svc._paths, band))           # the file's name, not a socket (L27)
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
    names in logs/, kept to the radios of `band` and that band's 0.13.x file. A logs/ that exists but cannot be
    listed is one entry naming that (`error`, no job), never "no files" (ledger L23); an absent logs/ has no files."""
    state = radios.load(svc._paths)
    try:
        names = [p.name for p in svc._paths.under("logs").iterdir()]
    except FileNotFoundError:
        names = []
    except (OSError, PathContainmentError) as exc:
        return [{"label": "", "target": e.writer, "job": "", "readonly": True,
                 "error": f"logs/ cannot be listed ({type(exc).__name__})"}]
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
    model = radio_models.load()[radio.model]
    port = stick_port(radio.usb_serial, radio.usb_port)[0] if model.transport != "spi" else ""
    return radio_daemon.check_bound(status, radio, model, port) or ""


def radio_name(paths, band: str) -> str:
    """A band's radio as a message names it (W4-2): its one enabled radio's id; with two or more, the band and
    every radio sharing its key (B4-D39: "868 (radios r1, r2)"); else the band."""
    key = key_for(paths, band)
    rids = [r.id for r in radios.load(paths).radios if r.enabled and r.band == band] if key == band else []
    return f"{band} (radios {', '.join(rids)})" if len(rids) > 1 else key


def claim_key(paths, band: str, prefix: str = "loraham.radio.") -> str:
    """The radio claim key of `band` by name (W4-4): its one enabled radio's id, else the band (key_for)."""
    return f"{prefix}{key_for(paths, band)}"


def chip_family(svc, band: str, rid: str = ""):
    """W5-2: the chip family of radio `rid`, or of `band`'s one enabled radio, from its row's model variant (the preset
    its daemon runs with); None when the hardware list does not resolve the band to one radio (0.13.x: its preset)."""
    t = band_rid(svc, band) if not rid else radio_daemon.Target("proceed", rid, "")
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


def client_instance(svc, comp, band: str, inst=None, out=None) -> tuple:
    """(instance, refusal) for a component's start (W3, stage L5; item 7c2). A listed instance `inst` (slot k, its
    radio) is used as given. Otherwise a daemon client — a run param whose value template names `{rid}` — runs as
    slot 0 on the first enabled radio of its band not listed for its stack (DEC-W16); with several radios there the
    choice is named in `out` (instances.slot0_note), and when every radio of the band is listed it is refused. Any
    other component keeps the given instance (default slot 0, without a radio)."""
    from . import commands, instances
    if inst is not None and inst.slot:
        return inst, ""
    if not any("{rid}" in str(getattr(p, "default", "") or "") for p in getattr(comp, "run_params", ())):
        return commands.SLOT0, ""
    sid = svc.stack_of(comp.id) or ""
    rid, why = instances.slot0_radio(svc._paths, sid, band)
    if why:
        return None, f"no radio for its daemon sockets: {why}"
    if rid:
        note = instances.slot0_note(svc._paths, sid, band, rid)
        if note and out is not None and f"  [radio] {note}" not in out:
            out.append(f"  [radio] {note}")
        return commands.Instance(0, rid), ""
    t = band_rid(svc, band)
    if t.outcome != "proceed":
        return None, f"no radio for its daemon sockets: {t.reason}"
    return commands.Instance(0, t.rid), ""

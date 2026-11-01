"""The start plan of one radio's daemon (stage L5): the hardware list, the radio's settings and the /proc scan composed
into the daemon's run params or a refusal. Unused until the wiring table lands (B4-D7); launch, verify (READY, ID=,
BOUND=) and the exit actions stay with the wiring. A module of its own, so the service family keeps its line budget.
"""
from __future__ import annotations

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
    legacy = {k: str(v) for k, v in svc.stack_config("daemon").items()}
    legacy_dp = {}
    for sid in _dp.CLIENT_STACKS:
        stored = cfgmod.load_stack_config(svc._paths, sid)
        vals = {name: str(stored[f"dp_{radio.band}_{name}"]) for name in _dp.ALL_PARAMS
                if f"dp_{radio.band}_{name}" in stored}
        if vals:
            legacy_dp[sid] = vals
    defaults = {"txmode": "managed", "cadmon": "off", "cadrssi": "-90", "hipower": "off"}   # manifest defaults
    try:
        settings = radios.radio_settings(radio, model, legacy, legacy_dp, defaults)
    except ValueError as exc:
        return None, str(exc), ()
    procs = []
    for pid, argv in svc._system.procfs.cmdlines().items():
        if radio_daemon.daemon_entry(None, "", argv, "", None, "") == radio_daemon.Tri.YES:
            procs.append(radio_daemon.parse_daemon_proc(pid, argv))
    refusal = radio_daemon.start_refusal(radio, procs) or ""
    rf_log = svc._resolved_param_value("daemon", "run", svc.DAEMON_ID, rflog.RF_LOG_PARAM, radio.band)
    params = radio_daemon.daemon_params(radio, model, settings, rf_log=rf_log)
    return params, refusal, settings.notes


def band_rid(svc, band: str) -> radio_daemon.Target:
    """W1-3/4/5 for the daemon (every radio runs it): the one enabled radio of `band`, or a refusal naming why
    (none, or two or more). A fresh load of the hardware list (W1-10)."""
    state = radios.load(svc._paths)
    return radio_daemon.resolve_target(state, radio_models.load(), lambda _r, _m: True, band=band,
                                       old_config=radio_daemon.old_config_state(svc._paths))


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
    import dataclasses
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

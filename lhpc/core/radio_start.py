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
    t = band_rid(svc, band)
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

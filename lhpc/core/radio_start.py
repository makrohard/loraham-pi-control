"""The start plan of one radio's daemon (stage L5): the hardware list, the radio's settings and the /proc scan composed
into the daemon's run params or a refusal. Unused until the wiring table lands (B4-D7); launch, verify (READY, ID=,
BOUND=) and the exit actions stay with the wiring. A module of its own, so the service family keeps its line budget.
"""
from __future__ import annotations

from . import config as cfgmod
from . import daemon_params as _dp
from . import radio_daemon, radio_models, radios, rflog


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

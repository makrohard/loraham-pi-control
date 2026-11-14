"""Stack instances (item 7, CLEANUP-DESIGN-v13 13.2, DEC-W327: no slots): every operator entry names `<stack>@<rid>`,
and a stack has one instance on each enabled radio of its bands (radios_of), each with its radio's paths and ports.
config/instances.toml and its two CLI verbs are gone (DEC-W344): nothing is listed first."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re

from . import commands, radios

_RID_RE = re.compile(r"r[1-9][0-9]?")


def stack_bands(stack) -> tuple:
    """The bands a stack can run on, from its manifest: a component's `bands`, else its components' fixed bands."""
    for c in stack.components:
        if c.bands:
            return tuple(c.bands)
    return tuple(sorted({c.band for c in stack.components if getattr(c, "band", "")}))


def fixed_ports_only(stack) -> bool:                          # transitional: per-radio Reticulum (next week)
    """DEC-W384h, by property: every TCP listener of the stack has a fixed port (no `{port:B}` template), so one set
    of ports serves whichever radio it runs on, and it gets ONE instance's pages and firewall rows."""
    eps = [e for c in stack.components for e in c.endpoints if e.kind == "tcp"]
    return bool(eps) and not any("{port" in (getattr(e, "address_template", "") or "") for e in eps)


def recorded_radio(paths, sid: str, rids) -> str:              # transitional: per-radio Reticulum (next week)
    """The radio of `sid`'s launch record among `rids` (the ownership records as written; their pids are verified
    by every reader that acts on a process), or ""."""
    from .lifecycle import Lifecycle
    recs = Lifecycle(paths, (), None, None).owned_inventory()[0]
    return next((r["rid"] for r in recs if r.get("stack") == sid and r.get("rid") in rids), "")


def radios_of(stack, paths, disabled: str = "") -> tuple:
    """The radios a stack has an instance on (K4b): every enabled radio of its bands (any radio for a stack without
    bands), in rid order; the hardware list
    decides, nothing is listed. `disabled`: one disabled radio counted as enabled (copy_of: the instance it would be,
    still addressed for a stop)."""
    if stack is None:
        return ()
    bands = stack_bands(stack)                     # a stack without bands runs on every radio
    return tuple(r.id for r in sorted((r for r in radios.load(paths).radios
                                       if (r.enabled or r.id == disabled) and (not bands or r.band in bands)),
                                      key=lambda r: int(r.id[1:])))


# ---- item 7c2: addressing an instance -------------------------------------------------------------------

def resolve(svc, target: str):
    """`<stack>@<rid>` -> (stack id, its instance, the radio's band, ""), or (stack id, None, "", why) when it cannot
    be started (see `copy_of`; a disabled radio's instance comes back WITH its why). A target without "@" ->
    (target, None, "", "")."""
    if not isinstance(target, str) or "@" not in target:
        return target, None, "", ""
    sid, _, rid = target.partition("@")
    if not rid:
        return sid, None, "", f"{sid}@ names no radio: use {sid}@<rid>"
    inst, why = copy_of(svc, sid, rid)
    return sid, inst, inst.band if inst is not None else "", why



def radios_able(svc, sid: str, band: str = "") -> list:
    """The enabled radios able to run `sid` (on `band` when given), by rid order: a form without an instance (the web's
    ?band= until W5, a bare `lhpc config <stack> --daemon-param`) addresses exactly ONE of them (G1-K8A 1, 2)."""
    rows = {r.id: r.band for r in radios.load(svc._paths).radios}
    return [r for r in radios_of(svc.stack(sid), svc._paths) if not band or rows.get(r) == band]


def one_radio(svc, sid: str, band: str) -> tuple:
    """(rid, "") of the ONE radio of `band` able to run `sid`, else ("", why): none or several is refused by name, never
    one picked (G1-K8A 1: the web's ?band= start until W5)."""
    rids = radios_able(svc, sid, band)
    if len(rids) == 1:
        return rids[0], ""
    return "", (f"no radio on {band} MHz runs '{sid}'" if not rids else
                f"'{sid}' runs on {', '.join(rids)} ({band} MHz): name the radio, {sid}@<rid>")


def can_run_on(svc, stack) -> list:
    """The enabled radios `stack` can run on, by rid order: those of its bands (any band for a stack without one)."""
    return list(radios_of(stack, svc._paths))


def running_keys(svc, sid: str) -> list:
    """`<sid>@<rid>` of each running instance of the stack, by rid order: by its verified ownership record's radio or
    its own argv token (instance_processes; W323: a bare stop or restart names them)."""
    rids = recorded(svc, sid) | {rid for s, rid, _c, _p in instance_processes(svc, svc._system.procfs.cmdlines())
                                 if s == sid and rid}
    return [f"{sid}@{r}" for r in sorted(rids, key=lambda x: int(x[1:]))]


def running_insts(svc, sid: str):
    """The running instances of `sid` when that is known in full: every running key resolves to its instance and no
    process of the stack runs unclaimed (no record, no token: `unclaimed`, one process-table read). Otherwise None,
    unknown (none told, a key that does not resolve, an unclaimed process): the GPS checks take an unknown stack as a
    GPS user (G1-K8F-S2 B1, DEC-W384de: fail-closed, never a partial or a saved set)."""
    told, stack, table = [copy_of(svc, sid, k.partition("@")[2])[0] for k in running_keys(svc, sid)], svc.stack(sid), \
        svc._system.procfs.cmdlines()
    procs = instance_processes(svc, table)
    whole = told and all(told) and not any(c.process and unclaimed(svc, c, table, procs) for c in (stack.components if stack else ()))
    return told if whole else None



def recorded(svc, sid: str) -> set:
    """The radios `sid`'s running instances were launched on, by their verified launch records (13.16): a running
    instance keeps its radio, whatever the hardware list says now."""
    return set(recorded_bands(svc, sid))


def recorded_bands(svc, sid: str) -> dict:
    """{rid: the band it was launched on} of `sid`'s running instances, by their verified launch records (13.16): the
    launch's own evidence, never the hardware row as it reads now (G1-K4B 3, 6). A record without a band: ""."""
    life, out = svc._lifecycle(), {}
    for rec in life.owned_inventory()[0]:
        if rec.get("rid") and not rec.get("role") and (rec.get("stack") or svc.stack_of(rec.get("component", ""))) == sid \
                and life.verify_owned(rec)[0]:
            band = str(rec.get("band") or "")
            out[rec["rid"]] = band if out.setdefault(rec["rid"], band) == band else ""   # G1-K4B-RE 3: disagree -> ""
    return out



def named(svc, target: str, inst=None, band: str = "") -> str:
    """`<target>@<rid>` for a command lhpc suggests (W323: a bare stack is refused): the instance's radio, else the ONE
    candidate: the band's one enabled radio when a band is given, else the stack's one running instance, else the one
    radio it can run on. Never a guess (handler DEC G1-C60-RE): with several candidates, or none, the suggestion keeps
    `<target>@<rid>` for the operator to fill in. A target that already names its radio is kept."""
    if not isinstance(target, str) or "@" in target:
        return target
    sid = target if svc.stack(target) is not None else (svc.stack_of(target) or "")
    stack = svc.stack(sid) if sid else None
    cands = ([inst.rid] if getattr(inst, "rid", "") else [] if stack is None else
             [r.id for r in radios.load(svc._paths).radios if r.enabled and r.band == band] if band else
             [k.partition("@")[2] for k in running_keys(svc, sid)] or can_run_on(svc, stack))
    return f"{target}@{cands[0] if len(cands) == 1 else '<rid>'}"


def instance_pids(svc, sid: str, inst, comp_id: str, get_argvs, pids) -> list:
    """Those of `pids` (component `comp_id`'s) that instance `inst` runs (instance_processes over the caller's one
    cmdline table, `get_argvs`: a callable or the table); all of them without an instance (K8f S4-4b1)."""
    if inst is None or not pids:
        return list(pids)
    table = get_argvs() if callable(get_argvs) else (get_argvs or {})
    procs = svc._request_memo(("instance-processes",), lambda: instance_processes(svc, table))   # once per render
    return [p for s, r, c, p in procs if (s, r, c) == (sid, inst.rid, comp_id) and p in pids]


def save_each(svc, sid: str, items, **kw):
    """Save each (instance, values, previous values) of stack `sid` in order (svc.save_config_bundle(.., inst=i, **kw)).
    A write that is refused or raises restores every instance written before it (restore_each) and is returned, naming
    each instance it could not restore. No item: nothing to write, ok (K8f S4-4b2, DEC-W409w)."""
    from .service_base import ActionResult
    done, r = [], ActionResult(True, "")
    for inst, values, previous in items:
        r = _save_one(svc, sid, inst, values, kw)
        if not r.ok:
            left = restore_each(svc, sid, done, **kw)
            return ActionResult(False, r.summary + (f" (not restored: {', '.join(left)})" if left else ""), details=r.details)
        done.append((inst, previous))
    return r


def restore_each(svc, sid: str, pairs, **kw) -> list:
    """Put each (instance, previous values) of stack `sid` back, best effort, latest first: the instance keys whose
    restore was refused or raised (G1-K8F-S4-4B2 B1-B3)."""
    return [key(sid, i) for i, prev in reversed(list(pairs)) if not _save_one(svc, sid, i, prev, kw).ok]


def _save_one(svc, sid: str, inst, values, kw):
    """One instance's save, a refusal named by its instance; an exception is its refusal (the caller restores what was
    written before). A failed save leaves its own file as it was: the commit is one journalled transaction that rolls
    every target back (config._apply_config_transaction_locked)."""
    from .service_base import ActionResult
    try:
        r = svc.save_config_bundle(sid, values=values, inst=inst, **kw)
    except Exception as exc:                             # any failure of one write is that write's refusal
        return ActionResult(False, f"{key(sid, inst)}: {exc}")
    return r if r.ok else ActionResult(False, f"{key(sid, inst)}: {r.summary}", details=r.details)


def launch_bands(svc, sid: str) -> set:
    """The bands `sid`'s running instances were LAUNCHED on (their verified records' bands; G1-K4B 6: an edited
    hardware row is no evidence). Empty, so the caller holds every band (fail-safe), when one running instance's
    band is not told by its record (a hand-started token-only process, or a record without a band)."""
    told = recorded_bands(svc, sid)
    keys = {k.partition("@")[2] for k in running_keys(svc, sid)}
    return set() if not keys or any(not told.get(r) for r in keys) else {told[r] for r in keys}


def stop_keys(svc, sid: str) -> list:
    """A stack-wide remedy's instances (G1-C60 NOTE): every running one; none when none runs (never a guess)."""
    return running_keys(svc, sid)


def holder_keys(svc, blocker: dict) -> list:
    """The instances holding a resource blocker: the one its claim's radio names (`loraham.radio.r2` -> daemon@r2),
    else every running instance of the holder on the claim's band, else the holder with `<rid>` for the operator to
    fill in (a process without an ownership record; G1-C60-RE: never a guess)."""
    sid = blocker.get("holder_stack", "")
    if m := re.search(r"\.(r[1-9][0-9]?)$", str(blocker.get("resource", ""))):
        return [f"{sid}@{m.group(1)}"]
    on = {r.id for r in radios.load(svc._paths).radios if r.band == blocker.get("band", "")}
    return [k for k in running_keys(svc, sid) if k.partition("@")[2] in on] or [f"{sid}@<rid>"]


def holder_stops(svc, blockers, daemon_sid: str) -> list:
    """The instances a start's stop_owners stops, in its order: the app holders first, then the daemon's radios;
    exactly the holders (holder_keys), a `<rid>` template only for a holder no other blocker names (G1-C60-RE)."""
    keys = dict.fromkeys(k for bl in sorted(blockers, key=lambda bl: bl["holder_stack"] == daemon_sid)
                         for k in holder_keys(svc, bl))
    known = {k.partition("@")[0] for k in keys if not k.endswith("@<rid>")}
    return [k for k in keys if not (k.endswith("@<rid>") and k.partition("@")[0] in known)]


def marker_keys(svc, target: str, inst=None) -> list:
    """The restart-required markers a start or stop of `target` satisfies: a radio's daemon its own `daemon@<rid>`
    (radio_start._mark_restart writes one per radio, G1-C60-RE), the whole daemon its own and every radio's, any
    other target its instance key."""
    if target != (svc.stack_of(svc.DAEMON_ID) or "daemon"):
        return [key(target, inst)]
    if getattr(inst, "rid", ""):
        return [f"{target}@{inst.rid}"]
    return [target, *(f"{target}@{r.id}" for r in radios.load(svc._paths).radios)]


def parse_target(svc, target: str, verb: str = "start"):
    """THE target of an operator entry (the CLI, then the console; CLEANUP-DESIGN-v13 13.2, W323/W326):
    `<stack>@<rid>` (or `<component>@<rid>`: its stack's instance) -> (the stack or component id, Instance, its
    radio's band, ""), or (that id, None, "", why). A bare `<stack>` is refused
    with the radios it can run on ("kiss can run on r1 or r3: use kiss@r1 or kiss@r3"), one radio included; a bare
    stop, restart or kill names its running instances. Every radio of the stack's bands is its instance there
    (copy_of). A disabled radio's instance comes back WITH its why (a stop still reaches
    it, G1-C7 B3)."""
    name, at, rid = target.partition("@") if isinstance(target, str) else ("", "", "")
    sid = name if svc.stack(name) is not None else (svc.stack_of(name) or "")
    stack = svc.stack(sid) if sid else None
    if stack is None:
        return name, None, "", f"unknown stack or component {name!r}"
    out = parse_instance(svc, stack, name, at, rid, verb)
    return (name, *out[1:])


def parse_instance(svc, stack, name: str, at: str, rid: str, verb: str):
    """parse_target's rule for `name` (a stack or one of its components) of `stack`."""
    sid = stack.id
    if not at and verb in ("stop", "restart", "kill") and (run := running_keys(svc, sid)):
        named = [f"{name}@{k.partition('@')[2]}" for k in run]
        return sid, None, "", f"{name} runs as {' and '.join(named)}: name one ({named[0]})"
    if not at:
        can = can_run_on(svc, stack)
        if not can:
            return sid, None, "", f"{name} can run on no enabled radio of the hardware list: add one on the Hardware page"
        return sid, None, "", (f"{name} can run on {' or '.join(can)}: use " + " or ".join(f"{name}@{r}" for r in can))
    if not rid:                                  # `kiss@`: never the band caller's instance (G1-C54)
        return sid, None, "", f"{name}@: name a radio ({name}@<rid>)"
    run = recorded(svc, sid)                     # ONE verified snapshot for the parse and its instance (G1-B1-RE)
    inst, why = copy_of(svc, sid, rid, run=run)
    if inst is None and why.endswith("is not in the hardware list"):
        state = radios.load(svc._paths)
        if state.kind == radios.ABSENT:          # no list (gate 2 (b), DEC-W409ax): the W1-8 reason, its next step
            from . import radio_daemon
            t = radio_daemon._unstartable(state, radio_daemon.old_config_state(svc._paths))
            why = f"{name}@{rid}: {t.reason} (next: {t.next_command})"
        else:
            why = f"{name}@{rid}: {rid or '(no radio)'} is not a radio of the hardware list"
    return sid, inst, inst.band if inst is not None else "", why



def radio_band(svc, target: str, inst, band: str = "") -> tuple:
    """K8d: an instance runs on its radio's band -> (band, why). A `<stack>@<rid>` start or restart takes its radio
    ROW's band, resolved here by the rid (G1-K8D1: never a band the caller or the instance carries); without a row it
    is refused by name (DEC-W384aj: "rN is not in the hardware list"), never run on another band; the daemon's own
    instance too (K8f: `daemon@r2` is radio r2, never every band). The template keeps the caller's band."""
    if inst is None or not inst.rid:
        return band, ""
    row = next((r for r in radios.load(svc._paths).radios if r.id == inst.rid), None)
    return (row.band, "") if row is not None else ("", f"{target}@{inst.rid}: {inst.rid} is not in the hardware list")

def key(target: str, inst) -> str:
    """The one instance-key formatter: `<target>@<rid>`; `<target>` only without a radio (the manifest's template)."""
    return f"{target}@{inst.rid}" if inst is not None and inst.rid else target



def keys(svc, sid: str) -> list:
    """The stack's instance keys, one per radio it has (`key`)."""
    return [key(sid, i) for i in commands.stack_instances(svc.stack(sid), svc._paths)]



def instance_processes(svc, table: dict) -> list:
    """[(sid, rid, component, pid)], sorted, of each process an instance runs: a verified record with its radio, or an
    instance's own argv token in `table` (the caller's one cmdline table; a hand-started one). The one
    instance-ownership collection (G1-C51): running_keys, running_instances_on and a radio's users read it."""
    from .probes.process import matches
    life, out = svc._lifecycle(), set()
    for rec in life.owned_inventory()[0]:
        if rec.get("rid") and not rec.get("role") and life.verify_owned(rec)[0]:   # a post runner is no instance process
            cid = rec.get("component", "")
            out.add((rec.get("stack") or svc.stack_of(cid) or "", rec["rid"], cid, rec["pid"]))
    # a hand-started instance without a record: its own token, rendered only for a spec a process matches (a token
    # narrows a spec match; DEC-W384j: no stack's settings read for nothing)
    live = {comp.id for stk in svc.stacks() for comp in stk.components
            if comp.process is not None and any(matches(comp.process, a, None) for a in table.values())}
    every = [(stk, inst) for stk in svc.stacks() if any(c.id in live for c in stk.components)
             for inst in commands.stack_instances(stk, svc._paths)]
    bands = {r.id: r.band for r in radios.load(svc._paths).radios} if every else {}
    for stk, inst in every:
        for comp in stk.components:
            tok = _token(svc, comp, inst, bands.get(inst.rid, "")) if comp.id in live else None
            if tok is not None:
                out.update((stk.id, inst.rid, comp.id, p) for p, a in table.items() if matches(comp.process, a, tok))
    return sorted(out)




def copy_of(svc, sid: str, rid: str = "", band: str = "", run=None):
    """THE stack instance (CLEANUP-DESIGN-v13) -> (Instance, why); (None, why) when there is none.
    `<sid>@<rid>`: its band is its radio row's, and an explicit band that contradicts it is refused, never
    substituted. A disabled radio's instance comes back WITH its why (still addressed: a stop; never started).
    Without `rid` (the band callers until K6): the operation band (explicit -> running -> fallback -> primary; a
    fixed-band stack's one band) and, on it, the radio the stack runs on by its launch record (13.16), else the band's
    first enabled one. `run` is the caller's recorded() snapshot when it has one (G1-B1-RE)."""
    stack = svc.stack(sid)
    if stack is None:
        return None, f"unknown stack {sid!r}"
    rows = radios.load(svc._paths).radios
    if not rid:
        run = recorded(svc, sid) if run is None else run
        bands = stack_bands(stack)                    # a fixed-band stack's operation band is "" (no store): its band
        band = svc.operation_band(sid, band) or (bands[0] if len(bands) == 1 else "")
        rid0 = next((r.id for r in rows if r.id in run and r.band == band), "") or band_radio(svc._paths, band)
        return commands.Instance(rid0, band=band), ""
    radio = next((r for r in rows if r.id == rid), None)
    bands = stack_bands(stack)
    # G1-K4B 3 / -RE 1: a running instance is its verified launch record's: when its radio left the list, or its row
    # now names another band than the launch's, it is addressed with its LAUNCH band and the why (a stop reaches it,
    # a start refuses on the why); the current row decides only for an instance that does not run
    launched = recorded_bands(svc, sid) if run is None or rid in run else {}
    moved = rid in launched and radio is not None and launched[rid] != radio.band
    if radio is None or (bands and radio.band not in bands) or moved:
        why = (f"{sid}@{rid}: {rid} is not in the hardware list" if radio is None else
               f"{sid}@{rid}: {sid} runs on {' or '.join(bands)} MHz, {rid} is on {radio.band} MHz"
               if bands and radio.band not in bands else
               f"{sid}@{rid}: it runs as launched on {launched[rid] or 'an unknown band'} MHz, {rid} is on "
               f"{radio.band} MHz now: stop it first")
        if rid in launched:
            return commands.Instance(rid, band=launched[rid]), why
        return None, why
    if band and band != radio.band:
        return None, f"{sid}@{rid}: {rid} is on {radio.band} MHz, not {band} MHz"
    return commands.Instance(rid, band=radio.band), ("" if radio.enabled else f"{sid}@{rid}: {rid} is disabled")



def own_rid(inst) -> str:
    """The radio whose own settings file `config/stacks/<sid>@<rid>.toml` the instance reads (DEC-W60, K6: every
    instance); "" without one (the stack's global `<sid>.toml`)."""
    return inst.rid if inst is not None and inst.rid else ""


def elsewhere(svc, target_rid: str):
    """K6, one app stack per RADIO (W286/W322, triage T9) -> predicate(stack_id, comp): the running `comp` of
    `stack_id` is told on radios that are not `target_rid`'s, so it does not block that instance. A radio not told (a
    process no instance claims, a daemon without --id) blocks the whole band, as before (fail-safe)."""
    held = {}
    if target_rid:
        from . import radio_start
        for s_id, r_id, c_id, _p in instance_processes(svc, svc._system.procfs.cmdlines()):
            held.setdefault((s_id, c_id), set()).add(r_id)          # G1-K6C 1: per component, not per stack
        dprocs = radio_start._daemon_procs(svc)
        if dprocs and all(p.kind == "id" for p in dprocs):
            held[(svc.stack_of(svc.DAEMON_ID), svc.DAEMON_ID)] = {p.rid for p in dprocs}

    def other(stack_id, comp) -> bool:
        rids = held.get((stack_id, comp.id), set())
        told = rids and (comp.id == svc.DAEMON_ID or not unclaimed(svc, comp))
        return bool(target_rid and told and target_rid not in rids)
    return other


def saved_insts(svc, sid: str) -> list:
    """The instances whose own settings a stack-wide entry means (each its own, K6): every radio of the stack's bands,
    a disabled one too (G1-K6C N3: its file is what it reads when it returns); [None] (the global store) without a
    hardware list; [] for a list without a radio of its bands (G1-K6C-RE 3: the caller refuses, never the global file)."""
    state, bands = radios.load(svc._paths), stack_bands(svc.stack(sid))
    return [None] if state.kind == radios.ABSENT else [
        commands.Instance(r.id, band=r.band) for r in sorted(state.radios, key=lambda r: int(r.id[1:]))
        if not bands or r.band in bands]


def port_of_default(svc, p, inst) -> str:
    """A `{port_of:STACK:BASE}` default for instance `inst` (K6S-2, G1-K7G): STACK's instance on the same radio
    listens on its saved base (the param whose default is `{port:BASE}`, in its own store) else BASE, + 133 x
    (n - 1); "" for any other default or without a radio. A saved value of `p` wins (the caller's)."""
    m = commands._PORT_OF_RE.fullmatch(getattr(p, "default_template", "") or "")
    other = svc.stack(m.group(1)) if m and getattr(inst, "rid", "") else None
    if other is None:
        return ""
    return str(with_bases(svc, other.id, commands.Instance(inst.rid, band=inst.band)).port(int(m.group(2))))


def with_bases(svc, sid, inst):
    """`inst` with `sid`'s saved port bases (DEC-W384ac): each run param whose default is `{port:B}` and that the
    instance's own store sets (a valid 1024..65535 port) moves base B, so its endpoint, its claim and a sibling's
    target (kiss-serial's socat, meshcom-qemu's --xr-redirect) follow the saved port; unchanged without one. `sid` may
    be several stacks (a start's run order: their bases are disjoint, T-P1)."""
    if inst is None or not inst.rid:
        return inst
    bases = dict(inst.bases)
    for stack in (svc.stack(s) for s in ([sid] if isinstance(sid, str) else sid)):
        if stack is not None:
            bases.update(_saved_bases(svc, stack, inst))
    return commands.Instance(inst.rid, band=inst.band, bases=bases) if bases != inst.bases else inst


def _saved_bases(svc, stack, inst) -> dict:
    stored, counts, bases = store(svc, stack.id, inst.band, inst), svc._owner_param_counts(stack)[0], {}
    for c in stack.components:
        for q in c.run_params:
            m = commands._PORT_RE.fullmatch(getattr(q, "default_template", "") or str(q.default))
            saved = str(svc._resolve_stored(stored, "r", c.id, q.name, counts.get(q.name, 0))[0] or "").strip() if m else ""
            if saved.isascii() and saved.isdigit() and 1024 <= int(saved) <= 65535:     # G1-K8A 10: not '²'
                bases[int(m.group(1))] = int(saved)
    return bases


def store_inst(svc, sid: str, band: str = "", inst=None):
    """The instance whose own settings a caller means: its own; a band caller (no radio; the web's ?band= until W5)
    its band's instance (copy_of: the running radio, else the band's first); TEMPLATE-like (no rid) without a list."""
    return inst if getattr(inst, "rid", "") else (copy_of(svc, sid, band=band)[0] or inst)


def saved_inst(svc, sid: str, band: str = "", inst=None):
    """store_inst for a save or a reset: a bare instance with its radio's band (G1-K6C N5: its band's defaults are
    what a value is compared with)."""
    inst = store_inst(svc, sid, band, inst)
    return (copy_of(svc, sid, inst.rid)[0] or inst) if getattr(inst, "rid", "") and not inst.band else inst



def store(svc, sid: str, cfg_band: str, inst=None) -> dict:
    """The saved settings a component of stack `sid` reads: the instance's own `config/stacks/<sid>@<rid>.toml`
    (K6, DEC-W60; the switches too, DEC-W384k); a band caller's, its band's instance's (store_inst); without a list,
    the global `<sid>.toml`. An instance without its file runs on the defaults."""
    return svc._stack_config_cached(sid, "", store_inst(svc, sid, cfg_band, inst))



def instance_rels(paths, stack, rels, keep: int, base: tuple = ()) -> list:
    """7d: `rels` plus, for every instance of `stack`, the same with `<rid>` after their first `keep` segments (the
    manifest's {instance_dir} layout: a state root state/<name> -> state/<rid>/<name> with keep=1; a secret file
    <name> -> <rid>/<name> with keep=0) that EXISTS on disk (`rels` are relative to the runtime directory `base`): a
    listed, disabled or removed radio's alike (G1-B1 (2)), so the purge and the backup take what is there and a box's
    set never depends on its hardware list. Read no-follow and contained (runtime_fs: a radio directory that is a link,
    in or out of the root, is never followed); a container that is unsafe adds nothing. The bare `rels` stay: the
    files of the slot builds, until the operator moves them under `<rid>/` (13.8)."""
    from . import runtime_fs
    from .paths import PathContainmentError
    parts, out = [r.split("/") for r in rels], list(rels)
    on_disk: dict = {}
    for q in parts:
        key = tuple(q[:keep])
        if key not in on_disk:
            try:
                on_disk[key] = [n for n, _link in runtime_fs.scandir_nofollow(paths, paths.under(*base, *key))
                                if commands._RID_RE.fullmatch(n)]
            except PathContainmentError:
                on_disk[key] = []

    def present(rel):                              # no-follow and contained: a link out of the root is not ours
        try:
            return runtime_fs.stat_leaf_nofollow(paths, paths.under(*base, *rel.split("/"))) is not None
        except PathContainmentError:
            return False
    for q in parts:
        out += [rel for rid in on_disk[tuple(q[:keep])] if present(rel := "/".join([*q[:keep], rid, *q[keep:]]))]
    return out


def band_radio(paths, band: str) -> str:                     # transitional: the band callers go in K6
    """The band's first enabled radio by rid order ("" when it has none): the instance a band-addressed caller means
    until K6 (the CLI and console name `<stack>@<rid>` already). Without a band (a stack without bands runs on any
    radio): the first enabled radio."""
    return next((r.id for r in sorted(radios.load(paths).radios, key=lambda r: int(r.id[1:]))
                 if r.enabled and (r.band == band or not band)), "")


def _token(svc, comp, inst, band: str):
    """The (option, value) that names `inst` in the argv of `comp`'s process (its spec's instance_arg, resolved as
    its start renders it; kill.py's matcher), or None when the spec has none or it cannot be resolved."""
    spec = comp.process
    if spec is None or not spec.instance_arg:
        return None
    opt, tpl = spec.instance_arg
    src = str(svc._paths.resolve_source(comp.source.path)) if comp.source else str(svc._paths.runtime_root)
    try:
        return opt, commands.instance_value(tpl, comp, svc.stack_config(comp.id, band, inst), str(svc._paths.runtime_root),
                                            src, band, inst=inst)
    except Exception:          # an unresolvable value: the instance cannot be told by argv
        return None


def instance_runs(svc, comp, inst, band: str) -> bool:
    """`comp` runs AS `inst`: a verified ownership record of the instance's radio (every launch records its rid),
    or a process whose argv carries the instance's own token. Never "any process of the spec" (that is every
    instance at once)."""
    from .probes.process import matches
    life = svc._lifecycle()
    if any(rec.get("rid", "") == inst.rid and life.verify_owned(rec)[0] for rec in life.owned_records(comp.id)):
        return True
    tok = _token(svc, comp, inst, band)
    return tok is not None and any(matches(comp.process, argv, tok)
                                   for argv in svc._system.procfs.cmdlines().values())


def already_healthy(svc, target: str, order, radio: str, inst, band: str) -> bool:
    """The start's already-healthy no-op (item 7c2): every service component of `order` runs as THAT instance (its
    run state and its own TCP endpoints in place of the snapshot's) and its radio's daemon is READY; an interactive
    main is never "already healthy" (its start pass always runs). Without an instance (a template caller): the
    snapshot check (`_order_already_healthy`)."""
    if inst is None or not inst.rid:
        return svc._order_already_healthy(order, radio)
    inst = with_bases(svc, {s.id for s, _c in order}, inst)     # DEC-W384ac: its endpoints at its saved ports
    snap = svc.build_snapshot()
    idx = {c.id: ss.components[c.id] for ss in snap.stacks for c in ss.stack.components}
    for _s, comp in order:
        cs = idx.get(comp.id)
        if cs is None or comp.id == svc.DAEMON_ID or comp.process is None:
            continue
        idx[comp.id] = dataclasses.replace(cs, run_state=instance_state(svc, comp, inst, band, cs.run_state, cs),
                                           endpoints=endpoint_statuses(svc, cs, inst))
    return svc._all_components_healthy(order, idx, radio, inst.rid)



def running_claim_keys(svc, sid: str, comp, claim) -> set:
    """G1-C7 B5: `claim`'s keys as stack `sid`'s running `comp` holds them: the key of each instance of it that runs
    (claim_key), so two stacks' instances on one derived key conflict; the declared key when it has no instances."""
    keys = {claim_key(claim, with_bases(svc, sid, i)) for i in commands.stack_instances(svc.stack(sid), svc._paths)
            if instance_runs(svc, comp, i, resolve(svc, f"{sid}@{i.rid}")[2])}
    return keys or {claim.key}



def endpoint_statuses(svc, cs, inst) -> list:
    """`cs`'s endpoint statuses as instance `inst` serves them: each TCP endpoint probed at its own address
    (endpoints_for); without an instance (a template caller): the snapshot's as they are."""
    if inst is None or not inst.rid:
        return list(cs.endpoints or ())
    from . import meshcore_mode as _mm
    from .model import EndpointObservation
    from .probes.endpoints import tcp_endpoint_match
    inst = _based(svc, svc.stack_of(cs.component_id), inst)        # G2 NOTE 11: at its saved ports, as a start judges
    if cs.component_id == _mm.NODE_ID:            # K8f S4-5 (DEC-W409ah): its own mode at its own addresses, probed
        return [EndpointObservation(e, *tcp_endpoint_match(svc._system, e.address)) for e in node_endpoints(svc, [inst])]
    own = [dataclasses.replace(o, spec=e, present=tcp_endpoint_match(svc._system, e.address)[0]) if e.kind == "tcp"
           else o for o in cs.endpoints or () for e in endpoints_for([o.spec], inst)]
    return list({(o.spec.kind, o.spec.address): o for o in own}.values())  # several instances' specs, one address


def endpoints_for(endpoints, inst) -> list:
    """The endpoints as instance `inst` serves them (item 7c2): each templated address rendered for its radio
    (inst.subst(address_template), e.g. 127.0.0.1:8267 on r3); without an instance: unchanged."""
    if inst is None or not inst.rid:
        return list(endpoints)
    return [dataclasses.replace(e, address=inst.subst(e.address_template)) if getattr(e, "address_template", "")
            else e for e in endpoints]


def instance_state(svc, comp, inst, band: str, snap_state, cs=None):
    """A component's run state for one instance: RUNNING when it runs as that instance (its own endpoints judge
    DEGRADED, endpoint_statuses), the snapshot's when a process of the spec runs that no instance claims, else
    STOPPED. The daemon, a library/firmware/oneshot and a component without a process spec keep the snapshot's."""
    from .model import ComponentKind, RunState
    if (comp.id == svc.DAEMON_ID or comp.process is None
            or comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE, ComponentKind.ONESHOT)):
        return snap_state
    inst = with_bases(svc, svc.stack_of(comp.id), inst)          # DEC-W384ac: its endpoints at its saved ports
    if instance_runs(svc, comp, inst, band):
        if str(getattr(comp, "readiness", "")) == "endpoint":     # its own endpoints judge it, never the template's
            if cs is not None:                                    # the start's one view (endpoint_statuses)
                absent = any(not o.present for o in endpoint_statuses(svc, cs, inst) if o.spec.kind == "tcp" and o.spec.ready)
            else:
                from .probes.endpoints import tcp_endpoint_match
                absent = any(not tcp_endpoint_match(svc._system, e.address)[0]
                             for e in endpoints_for(comp.endpoints, inst) if e.kind == "tcp" and e.ready)
            return RunState.DEGRADED if absent else RunState.RUNNING
        # another readiness (a GPS feed's): the snapshot's DEGRADED is this instance's when only it runs comp
        return RunState.DEGRADED if snap_state == RunState.DEGRADED and _alone(svc, comp, inst) else RunState.RUNNING
    # Fail-safe (G1-C48): a process of the spec that no instance claims (hand-started, no token, no record) keeps the
    # snapshot's state, so a start judges it (its own endpoints, else "foreign") and never launches a second one.
    return snap_state if snap_state in (RunState.RUNNING, RunState.DEGRADED) and unclaimed(svc, comp) else RunState.STOPPED


def _alone(svc, comp, inst) -> bool:
    """No other instance runs `comp` (instance_processes), so the snapshot's component state is this instance's."""
    return not any(c == comp.id and r != inst.rid for _s, r, c, _p in instance_processes(svc, svc._system.procfs.cmdlines()))


def unclaimed(svc, comp, table=None, procs=None) -> bool:
    """A process of `comp`'s spec runs that no instance claims (by record or token, instance_processes); `table` and
    `procs` are the caller's one cmdline table and its instance_processes, when it checks many components."""
    return bool(unclaimed_pids(svc, comp, table, procs))


_BAND_SOCKET = re.compile(r"/tmp/lora[a-z]*(433|868)\.sock")    # a 0.13 per-band socket in an argv


def _socket_bands(argv) -> set:
    """The bands of the 0.13 per-band sockets an argv names."""
    return {m.group(1) for t in argv if (m := _BAND_SOCKET.search(t))}

def unclaimed_pids(svc, comp, table=None, procs=None, band: str = "") -> list:
    """The pids of `unclaimed`: a process of the spec no instance's record or token claims (an older argv too: a
    0.13 band socket is no instance's token). `band`: one whose argv names ANOTHER band's 0.13 socket is not counted
    (G1-K8A 6: a stop of an instance on 433 never names a leftover of 868)."""
    from .probes.process import matches
    table = svc._system.procfs.cmdlines() if table is None else table
    procs = instance_processes(svc, table) if procs is None else procs
    claimed = {p for _s, _r, c, p in procs if c == comp.id}
    return sorted(p for p, a in table.items() if p not in claimed and matches(comp.process, a, None)
                  and not (band and _socket_bands(a) and band not in _socket_bands(a)))   # G1-K8A-RE A: another band's ONLY


def orphan_note(svc, comp, inst, own_pid=None) -> str:
    """K8 (G1-K8A 5/6, -RE 6): what a named stop of `inst` reports about unclaimed processes of `comp`'s spec on its
    band, "" when there are none. Another radio able to run the stack on that band makes such a process's radio
    unknown: it is named for checking, never with a kill instruction."""
    pids = [p for p in unclaimed_pids(svc, comp, band=inst.band) if p != own_pid]
    if not pids:
        return ""
    head, rids = "a matching process is running but no instance claims it (no record, an older argv) — ", \
        radios_able(svc, svc.stack_of(comp.id) or comp.id, inst.band)
    if len(rids) > 1:
        return head + (f"its radio is unknown ({inst.band} MHz has {', '.join(rids)}): check pid "
                       f"{' '.join(map(str, pids))} and stop it yourself if it is {inst.rid}'s")
    return head + f"stop it yourself: kill {' '.join(map(str, pids))}"



def start_state(svc, sid: str, comp, inst, band: str, snap_state, cs=None):
    """The state a start judges `comp` by (item 7c2): the instance's own (instance_state); the snapshot's without
    an instance (a template caller)."""
    if inst is None or not inst.rid:
        return snap_state
    return instance_state(svc, comp, inst, band, snap_state, cs)



def claim_key(claim, inst) -> str:
    """A resource claim's key for instance `inst` (item 7a): its own key from its template (`tcp.port.8267`,
    `serial.loraham-kiss-r3`); without an instance or for a claim without instance tokens: its key as declared."""
    if inst is None or not inst.rid or not getattr(claim, "key_template", ""):
        return claim.key
    return inst.subst(claim.key_template)



def stop_scope(svc, sid: str, comp, inst, band: str) -> dict:
    """The keyword arguments a component's Lifecycle.stop takes for one instance (item 7c2b): its rid, its process
    token and its own endpoints; without an instance (a template caller) none (the whole component)."""
    if inst is None or not inst.rid:
        return {}
    rband = next((r.band for r in radios.load(svc._paths).radios if r.id == inst.rid), band)
    from .meshcore_mode import NODE_ID
    return {"rid": inst.rid, "token": _token(svc, comp, inst, rband),                       # DEC-W384ac; S4-5: its mode
            "endpoints": node_endpoints(svc, [inst]) if comp.id == NODE_ID else endpoints_for(comp.endpoints,
                                                                                            with_bases(svc, sid, inst))}


def node_endpoints(svc, insts) -> list:
    """The MeshCore node endpoints instances `insts` serve, each by its own running mode at its own (saved-base)
    addresses (K8f S4-5, DEC-W409ah): status readiness, an instance's view and the stop check read them."""
    from . import meshcore_mode as _mm
    comp = svc.stack(_mm.STACK_ID).component(_mm.NODE_ID)
    return [e for inst in insts for e in endpoints_for(_mm.expected_endpoints(comp, svc.meshcore_running_mode(inst)),
                                                       _based(svc, _mm.STACK_ID, inst))]


def status_endpoints(svc, table, owned) -> dict:
    """{component id: the endpoints status probes} (A17, A24; G1-B1-PLAN rounds), from ONE process table `table` and the
    verified records' pids `owned` ({component: [pid]}): those of the instances the component runs as, each at its own
    (saved-base) addresses, the MeshCore node in its own mode; plus the template's when an unclaimed pid of it runs (its
    radio unknown: neither hides the other); a component with no process, in a stack that runs as instances, its running
    instances' (display only). Not listed: the template's."""
    from . import meshcore_mode as _mm
    procs = instance_processes(svc, table)
    claimed, runs = {(c, p) for _s, _r, c, p in procs}, {}
    for sid, rid, cid, _pid in procs:
        runs.setdefault(sid, {}).setdefault(cid, set()).add(rid)
    out = {}
    for stack in (svc.stack(sid) for sid in runs):
        by_comp, every = runs[stack.id], set().union(*runs[stack.id].values())
        for comp in (c for c in stack.components if c.endpoints):
            loose = bool(unclaimed_pids(svc, comp, table, procs)) or any((comp.id, p) not in claimed
                                                                           for p in owned.get(comp.id, ()))
            rids = by_comp.get(comp.id) or (set() if loose else every)
            eps = [e for r in sorted(rids, key=lambda x: int(x[1:])) if (i := copy_of(svc, stack.id, r)[0]) is not None
                   for e in (node_endpoints(svc, [i]) if comp.id == _mm.NODE_ID
                             else endpoints_for(comp.endpoints, _based(svc, stack.id, i)))]
            if rids:
                eps += _mm.expected_endpoints(comp, _mm.DEFAULT_MODE) if loose else []
                out[comp.id] = list({(e.kind, e.address): e for e in eps}.values())
    return out


def _based(svc, sid, inst):
    """with_bases for a read-only view: an unreadable store keeps the instance's static ports (status and a stop's
    check never break on a malformed file)."""
    from .config import ConfigError
    try:
        return with_bases(svc, sid, inst)
    except ConfigError:
        return inst



def settings_choice(svc, sid: str, band: str, inst=None) -> dict:
    """7f, DEC-W40: the Settings page's radio chooser of stack `sid`, one entry per enabled radio of its bands
    ("rN · band"), each opening that instance's own store (`radio=`, K6: every instance). A band caller's page shows
    its band's instance (store_inst), so that entry is the selected one. Nothing is added first (DEC-W344). `radio`
    is the shown instance's own rid."""
    stack = svc.stack(sid)
    rids = radios_of(stack, svc._paths) if not svc._is_daemon_target(sid) else ()     # the daemon: its panel; G2 2
    rows = [r for rid in rids for r in radios.load(svc._paths).radios if r.id == rid]
    shown = own_rid(store_inst(svc, sid, band, inst)) if rows else ""
    out = [{"label": f"{r.id} · {r.band}", "radio": r.id, "band": "", "sel": r.id == shown, "disabled": False}
           for r in rows]
    return {"radio": shown, "radio_choices": out if len(out) > 1 else []}


def plan_hash(svc, op: str, target: str, inst=None, band: str = "", plan=None) -> str:
    """W2: a confirm page's freshness token, the sha256 of the core's start/restart plan (no choice taken; `plan`: the
    one the page shows, G1-W2 1), the target's saved run config and its radio's row (the daemon starts with the row's
    settings and grant). The detached job recomputes it under every lock; another value refuses the run."""
    row = [dataclasses.asdict(r) for r in radios.load(svc._paths).radios if inst is not None and r.id == inst.rid]
    plan = dataclasses.asdict(plan or svc.run_action(op, target, apply=False, band=band, inst=inst))
    blob = json.dumps([plan, svc.stack_config(target, band, inst), row], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()

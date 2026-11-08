"""Stack instances (item 7, CLEANUP-DESIGN-v13 13.2): every operator entry names `<stack>@<rid>`. A stack runs as
slot 0 on the radio it is named on and, for a stack whose resources are all per instance (MAY), as a copy on each
other radio the hardware list gives it (copy_rids). config/instances.toml and add-instance/remove-instance are gone
(DEC-W344): nothing is listed first."""
from __future__ import annotations

import dataclasses
import re

from . import commands, radios

_RID_RE = re.compile(r"r[1-9][0-9]?")
# T2.2 (DEC-W4/W15): the daemon clients whose exclusive resources are all per instance run as a copy beyond slot 0.
# TRANSITIONAL (handler DEC on K4ac): the other stacks run once until their 13.6 work in K7 (meshtastic's pins,
# meshcom's ports, voice.ui, Reticulum's one node, graywolf 13.5); W330 removes MAY there, the K8 grep checks it.
MAY = ("kiss", "meshcore", "chat")


def stack_bands(stack) -> tuple:
    """The bands a stack can run on, from its manifest: a component's `bands`, else its components' fixed bands."""
    for c in stack.components:
        if c.bands:
            return tuple(c.bands)
    return tuple(sorted({c.band for c in stack.components if getattr(c, "band", "")}))


def copy_rids(stack, paths, disabled: str = "") -> tuple:
    """The radios a stack runs on as a copy (K4ac): a MAY stack's every enabled radio of its bands but each band's
    first (DEC-W16: slot 0's), in rid order; () for any other stack. The hardware list decides, nothing is listed.
    `disabled`: one disabled radio counted as enabled (copy_of: the copy it would be, still addressed for a stop)."""
    if stack is None or stack.id not in MAY:
        return ()
    rows = sorted((r for r in radios.load(paths).radios
                   if (r.enabled or r.id == disabled) and r.band in stack_bands(stack)),
                  key=lambda r: int(r.id[1:]))
    first = {}
    for r in rows:
        first.setdefault(r.band, r.id)
    return tuple(r.id for r in rows if first[r.band] != r.id)


# ---- item 7c2: addressing an instance -------------------------------------------------------------------

def resolve(svc, target: str):
    """`<stack>@<rid>` → (stack id, its listed copy, the radio's band, ""), or (stack id, None, "", why) when it
    cannot be started (see `copy_of`; a disabled radio's copy comes back WITH its why). A target without "@" →
    (target, None, "", "") — today's slot 0."""
    if not isinstance(target, str) or "@" not in target:
        return target, None, "", ""
    sid, _, rid = target.partition("@")
    if not rid:
        return sid, None, "", f"{sid}@ names no radio: use {sid}@<rid>"
    copy, why = copy_of(svc, sid, rid)
    return sid, copy, copy.band if copy is not None else "", why


def can_run_on(svc, stack) -> list:
    """The enabled radios `stack` can run on, by rid order: those of its bands (any band for a stack without one)."""
    bands = stack_bands(stack)
    return sorted((r.id for r in radios.load(svc._paths).radios if r.enabled and (not bands or r.band in bands)),
                  key=lambda x: int(x[1:]))


def running_keys(svc, sid: str) -> list:
    """`<sid>@<rid>` of each running instance of the stack, by rid order: slot 0 by its verified ownership record's
    radio, a copy by copy_processes (W323: a bare stop or restart names them)."""
    life, rids = svc._lifecycle(), set()
    for rec in life.owned_inventory()[0]:
        if (rec.get("stack") or svc.stack_of(rec.get("component", ""))) == sid and rec.get("rid") \
                and life.verify_owned(rec)[0]:
            rids.add(rec["rid"])
    rids |= {rid for s, rid, _c, _p in copy_processes(svc, svc._system.procfs.cmdlines()) if s == sid and rid}
    return [f"{sid}@{r}" for r in sorted(rids, key=lambda x: int(x[1:]))]


def recorded(svc, sid: str) -> dict:
    """{rid: slot} of `sid`'s running instances by their verified launch records (13.16, G1-B1): a running instance
    keeps the key it was launched as (slot 0 or a copy, 1), whatever the hardware list says now."""
    life, out = svc._lifecycle(), {}
    for rec in life.owned_inventory()[0]:
        if rec.get("rid") and (rec.get("stack") or svc.stack_of(rec.get("component", ""))) == sid \
                and life.verify_owned(rec)[0]:
            out[rec["rid"]] = 1 if rec.get("slot", 0) else 0
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
    stop, restart or kill names its running instances. A copy's radio (copy_rids) is that copy; any other radio of
    the stack is slot 0 on it (until K4b). A disabled radio's instance comes back WITH its why (a stop still reaches
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
    sid, target = stack.id, f"{name}{at}{rid}"
    if not at and verb in ("stop", "restart", "kill") and (run := running_keys(svc, sid)):
        named = [f"{name}@{k.partition('@')[2]}" for k in run]
        return sid, None, "", f"{name} runs as {' and '.join(named)}: name one ({named[0]})"
    if not at:
        can = can_run_on(svc, stack)
        if not can:
            return sid, None, "", f"{name} can run on no enabled radio of the hardware list: add one on the Hardware page"
        return sid, None, "", (f"{name} can run on {' or '.join(can)}: use " + " or ".join(f"{name}@{r}" for r in can))
    run = recorded(svc, sid)                     # ONE verified snapshot for the parse and its copy (G1-B1-RE)
    if run.get(rid) == 1 or (run.get(rid) is None and rid in copy_rids(stack, svc._paths)):
        copy, why = copy_of(svc, sid, rid, run=run)
        return sid, copy, copy.band if copy is not None else "", why
    radio = next((r for r in radios.load(svc._paths).radios if r.id == rid), None)
    if radio is None:
        return sid, None, "", f"{target}: {rid or '(no radio)'} is not a radio of the hardware list"
    if (bands := stack_bands(stack)) and radio.band not in bands:
        return sid, None, "", f"{target}: {sid} runs on {' or '.join(bands)} MHz, {rid} is on {radio.band} MHz"
    inst = commands.Instance(0, rid, band=radio.band)
    return sid, inst, radio.band, (f"{target}: {rid} is disabled" if not radio.enabled else "")


def key(target: str, inst) -> str:
    """The one copy-key formatter: `<target>@<rid>` for a listed copy, `<target>` for slot 0 (or no copy)."""
    return f"{target}@{inst.rid}" if inst is not None and inst.slot else target


def keys(svc, sid: str) -> list:
    """The stack's copy keys: slot 0's, then each listed copy's (`key`)."""
    return [key(sid, i) for i in commands.stack_instances(svc.stack(sid), svc._paths)]


def copy_processes(svc, table: dict) -> list:
    """[(sid, rid, component, pid)], sorted, of each process a copy runs: a verified slot-k record (listed now or not,
    G1-C48 (1)) or a listed copy's own argv token in `table` (the caller's one cmdline table). The one copy-ownership
    collection (G1-C51): copy_pids, running_copies_on and a radio's users read it."""
    from .probes.process import matches
    life, out = svc._lifecycle(), set()
    for rec in life.owned_inventory()[0]:            # a verified slot-k record, listed now or not (G1-C48 (1))
        if rec.get("slot", 0) and life.verify_owned(rec)[0]:
            cid = rec.get("component", "")
            out.add((rec.get("stack") or svc.stack_of(cid) or "", rec.get("rid", ""), cid, rec["pid"]))
    copies = [(stk, inst) for stk in svc.stacks() for inst in commands.stack_instances(stk, svc._paths)[1:]]
    bands = {r.id: r.band for r in radios.load(svc._paths).radios} if copies else {}
    for stk, inst in copies:                         # a hand-started copy without a record: its own token
        for comp in stk.components:
            tok = _token(svc, comp, inst, bands.get(inst.rid, "")) if comp.process is not None else None
            if tok is not None:
                out.update((stk.id, inst.rid, comp.id, p) for p, a in table.items() if matches(comp.process, a, tok))
    return sorted(out)


def copy_pids(svc, table: dict) -> dict:
    """{component: pids} of the copies' processes (copy_processes): the snapshot never counts them as slot 0's (C7,
    W286). `table` is the snapshot's own cmdline table."""
    out: dict = {}
    for _sid, _rid, cid, pid in copy_processes(svc, table):
        out.setdefault(cid, set()).add(pid)
    return out


def copy_of(svc, sid: str, rid: str = "", band: str = "", run=None):
    """THE stack copy (CLEANUP-DESIGN-v2 section 2) → (Instance, why); (None, why) when there is none.
    A copy (`rid`, one of copy_rids): its band is its radio row's, and an explicit band that contradicts it is
    refused, never substituted. A disabled radio's copy comes back WITH its why (still addressed: a stop; never
    started). Slot 0 (no `rid`): the operation band (explicit -> running -> fallback -> primary; a fixed-band
    stack's one band) and, on it, the radio slot 0 runs on by its launch record, else DEC-W16's (the band's first
    enabled one). A running instance keeps its recorded key (13.16, G1-B1): a record decides before copy_rids;
    `run` is the caller's recorded() snapshot when it has one."""
    stack = svc.stack(sid)
    if stack is None:
        return None, f"unknown stack {sid!r}"
    run = recorded(svc, sid) if run is None else run
    rows = radios.load(svc._paths).radios
    if not rid:
        bands = stack_bands(stack)                    # a fixed-band stack's operation band is "" (no store): its band
        band = svc.operation_band(sid, band) or (bands[0] if len(bands) == 1 else "")
        rid0 = next((r.id for r in rows if run.get(r.id) == 0 and r.band == band), "")
        rid0, why = (rid0, "") if rid0 else slot0_radio(svc._paths, sid, band) if band else ("", "")
        return commands.Instance(0, rid0, band=band), why
    radio = next((r for r in rows if r.id == rid), None)
    if radio is None:
        return None, f"{sid}@{rid}: {rid} is not in the hardware list"
    if band and band != radio.band:
        return None, f"{sid}@{rid}: {rid} is on {radio.band} MHz, not {band} MHz"
    if radio.band not in stack_bands(stack):
        return None, f"{sid}@{rid}: {sid} runs on {' or '.join(stack_bands(stack))} MHz, {rid} is on {radio.band} MHz"
    if run.get(rid) == 0:                     # slot 0 runs there (launched before the list gave it a copy)
        return commands.Instance(0, rid, band=radio.band), (f"{sid}@{rid}: {rid} is disabled" if not radio.enabled else "")
    if run.get(rid) is None and rid not in copy_rids(stack, svc._paths, "" if radio.enabled else rid):
        return None, f"{sid}@{rid} is not a copy of {sid}: the hardware list gives it none on {rid}"
    copy = commands.Instance(1, rid, band=radio.band)
    if not radio.enabled:                     # G1-C7 B3: still addressed (a stop, a running check), never started
        return copy, f"{sid}@{rid}: {rid} is disabled"
    return copy, ""


def own_rid(inst) -> str:
    """A listed instance's radio id, whose own settings file it has (DEC-W60); "" for slot 0 or no instance."""
    return inst.rid if inst is not None and inst.slot else ""


def stores(svc, sid: str, cfg_band: str, inst=None) -> tuple:
    """(stored, bandless): the saved settings a component of stack `sid` reads. Slot 0: its band's file and the
    band-less file (the stack-level params), as before; a listed instance: its own `config/stacks/<sid>@<rid>.toml`
    for both (DEC-W60) — nothing of slot 0's is shared, an instance without its file runs on the defaults."""
    if inst is not None and inst.slot:
        own = svc._stack_config_cached(sid, "", inst)
        return own, own
    stored = svc._stack_config_cached(sid, cfg_band)
    return stored, (svc._stack_config_cached(sid, "") if cfg_band else stored)


def instance_rels(paths, stack, rels, keep: int, base: tuple = ()) -> list:
    """7d: `rels` plus, for each copy of `stack` (copy_rids), the same with `<rid>` after their first `keep` segments
    (the manifest's {instance_dir} layout: a state root state/<name> -> state/<rid>/<name> with keep=1; a secret
    file <name> -> <rid>/<name> with keep=0). G1-B1 (2): also every one that EXISTS under a `<rid>/` directory on
    disk (`rels` are relative to the runtime directory `base`), so a disabled or removed radio's copy is still
    purged and backed up. Read no-follow and contained (runtime_fs); a container that is unsafe adds nothing."""
    from . import runtime_fs
    from .paths import PathContainmentError
    parts, out = [r.split("/") for r in rels], list(rels)
    listed = copy_rids(stack, paths)
    on_disk: dict = {}
    for q in parts:
        key = tuple(q[:keep])
        if key not in on_disk:
            try:
                on_disk[key] = [n for n, link in runtime_fs.scandir_nofollow(paths, paths.under(*base, *key))
                                if not link and commands._RID_RE.fullmatch(n) and n not in listed]
            except PathContainmentError:
                on_disk[key] = []
    def present(rel):                              # no-follow and contained: a link out of the root is not ours
        try:
            return runtime_fs.stat_leaf_nofollow(paths, paths.under(*base, *rel.split("/"))) is not None
        except PathContainmentError:
            return False
    for q in parts:
        out += ["/".join([*q[:keep], rid, *q[keep:]]) for rid in listed]
        out += [rel for rid in on_disk[tuple(q[:keep])] if present(rel := "/".join([*q[:keep], rid, *q[keep:]]))]
    return out


def slot0_radio(paths, sid: str, band: str):
    """Slot 0's radio on `band` (DEC-W16): the first enabled radio of the band, by rid order (the band's other radios
    are the copies, copy_rids). (rid, ""); ("", "") when the band has no enabled radio at all (the caller's own
    refusal names that)."""
    return slot0_of([r for r in radios.load(paths).radios if r.enabled and r.band == band], ()), ""


def slot0_of(rows, taken) -> str:
    """DEC-W16's choice among one band's enabled radios: the first by rid order not in `taken`, else ""."""
    return next((r.id for r in sorted(rows, key=lambda r: int(r.id[1:])) if r.id not in taken), "")


def slot0_note(paths, stack, band: str, rid: str) -> str:
    """The DEC-W16 line when slot 0's radio was chosen among several: "kiss starts on r1 (433 MHz); kiss@r3 is a
    separate instance"; "" when the band has one radio."""
    others = sorted(r.id for r in radios.load(paths).radios if r.enabled and r.band == band and r.id != rid)
    if not others:
        return ""
    sid, taken = stack.id, copy_rids(stack, paths)
    parts = [f"{sid}@{x} is a separate instance" if x in taken else f"{x} is not used by {sid}" for x in others]
    return f"{sid} starts on {rid} ({band} MHz); " + "; ".join(parts)


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
    """`comp` runs AS `inst`: a verified ownership record of the instance's radio (a slot-k launch records its rid),
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
    """The start's already-healthy no-op (item 7c2). For a listed instance, and for slot 0 once its stack has
    listed instances, every service component of `order` must run as THAT instance and the daemon of its band be
    READY; an interactive main is never "already healthy" (its start pass always runs). Otherwise the snapshot
    check as before (`_order_already_healthy`), unchanged for a box without listed instances."""
    sid = svc.stack_of(target) or target                 # the target's stack (the run order starts with the daemon)
    if (inst is None or not inst.slot) and not copy_rids(svc.stack(sid), svc._paths):   # slot 0 is scoped only beside listed
        return svc._order_already_healthy(order, radio)      # copies, then as its own copy (start's copy_of, DEC-W16)
    # G1-C7 B4: slot 0's own rule (`_all_components_healthy`: readiness endpoints, DEGRADED) over the instance's
    # statuses: its run state and its own TCP endpoints in place of the snapshot's.
    snap = svc.build_snapshot()
    idx = {c.id: ss.components[c.id] for ss in snap.stacks for c in ss.stack.components}
    for _s, comp in order:
        cs = idx.get(comp.id)
        if cs is None or comp.id == svc.DAEMON_ID or comp.process is None:
            continue
        idx[comp.id] = dataclasses.replace(cs, run_state=instance_state(svc, comp, inst, band, cs.run_state),
                                           endpoints=endpoint_statuses(svc, cs, inst))
    return svc._all_components_healthy(order, idx, radio, inst.rid if inst is not None else "")


def running_claim_keys(svc, sid: str, comp, claim) -> set:
    """G1-C7 B5: `claim`'s keys as stack `sid`'s running `comp` holds them: slot 0's key and the key of each listed
    instance of it that runs (claim_key), so two stacks' instances on one derived key conflict."""
    keys = {claim.key}
    for i in commands.stack_instances(svc.stack(sid), svc._paths):
        if i.slot and instance_runs(svc, comp, i, resolve(svc, f"{sid}@{i.rid}")[2]):
            keys.add(claim_key(claim, i))
    return keys


def endpoint_statuses(svc, cs, inst) -> list:
    """`cs`'s endpoint statuses as instance `inst` serves them: each TCP endpoint probed at its own address
    (endpoints_for); slot 0 / no instance: the snapshot's as they are."""
    if inst is None or not inst.slot:
        return list(cs.endpoints or ())
    from .probes.endpoints import tcp_endpoint_match
    return [dataclasses.replace(o, spec=e, present=tcp_endpoint_match(svc._system, e.address)[0]) if e.kind == "tcp"
            else o for o in cs.endpoints or () for e in endpoints_for([o.spec], inst)]


def endpoints_for(endpoints, inst) -> list:
    """The endpoints as instance `inst` serves them (item 7c2): a slot-k copy of each templated address
    (inst.subst(address_template), e.g. 127.0.0.1:8011); slot 0 / no instance: unchanged."""
    if inst is None or not inst.slot:
        return list(endpoints)
    return [dataclasses.replace(e, address=inst.subst(e.address_template)) if getattr(e, "address_template", "")
            else e for e in endpoints]


def instance_state(svc, comp, inst, band: str, snap_state):
    """A component's run state for one instance: RUNNING when it runs as that instance, else STOPPED. The daemon, a
    library/firmware/oneshot and a component without a process spec keep the snapshot's state."""
    from .model import ComponentKind, RunState
    if (comp.id == svc.DAEMON_ID or comp.process is None
            or comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE, ComponentKind.ONESHOT)):
        return snap_state
    if not instance_runs(svc, comp, inst, band):
        return RunState.STOPPED
    # G1-C7 B4: slot 0 keeps the snapshot's DEGRADED (judged on slot 0's endpoints); a listed instance's comes from its own
    return RunState.DEGRADED if snap_state == RunState.DEGRADED and not inst.slot else RunState.RUNNING


def start_state(svc, sid: str, comp, inst, band: str, snap_state):
    """The state a start judges `comp` by (item 7c2): the copy's own (instance_state) for a listed copy, and for slot
    0 (start's copy, on its DEC-W16 radio) while the stack has listed copies; else the snapshot's."""
    if inst is None or not inst.rid or (not inst.slot and not copy_rids(svc.stack(sid), svc._paths)):
        return snap_state
    return instance_state(svc, comp, inst, band, snap_state)


def claim_key(claim, inst) -> str:
    """A resource claim's key for instance `inst` (item 7a): slot k's own key from its template (`tcp.port.8011`,
    `serial.loraham-kiss-r3`); slot 0 or a claim without instance tokens: its key as declared."""
    if inst is None or not inst.slot or not getattr(claim, "key_template", ""):
        return claim.key
    return inst.subst(claim.key_template)


def stop_scope(svc, sid: str, comp, inst, band: str) -> dict:
    """The keyword arguments a component's Lifecycle.stop takes for one instance (item 7c2b): for `<stack>@<rid>`
    its rid, its process token and its own endpoints; for slot 0 of a stack with listed instances every record but
    the listed instances' (exclude_rids) and slot 0's token; otherwise none (today's whole-component stop)."""
    if inst is not None and inst.slot:
        rband = next((r.band for r in radios.load(svc._paths).radios if r.id == inst.rid), band)
        return {"rid": inst.rid, "token": _token(svc, comp, inst, rband),
                "endpoints": endpoints_for(comp.endpoints, inst)}
    run = svc._request_memo(("recorded", sid), lambda: recorded(svc, sid))   # one snapshot per stop (G1-B1-RE)
    taken = sorted({*copy_rids(svc.stack(sid), svc._paths), *(r for r, k in run.items() if k)}
                   - {inst.rid if inst is not None else "", *(r for r, k in run.items() if not k)})
    if not taken:
        return {}
    b = svc.running_band(sid, comp.band or band) or comp.band or band      # slot 0's copy (stop's copy_of, DEC-W16)
    return {"exclude_rids": tuple(taken), "token": _token(svc, comp, inst, b) if inst is not None and inst.rid else None}








def settings_choice(svc, sid: str, band: str, inst=None) -> dict:
    """7f, DEC-W40: the Settings page's radio chooser of stack `sid`, one entry per enabled radio of its bands
    ("rN · band"): a copy opens its own settings (`radio=`); slot 0's radio of a band opens slot 0's on that band
    (`band=`, so a band-switchable stack's slot 0 moves there). Nothing is added first (DEC-W344): a MAY stack has
    an entry on every radio. `radio` is the shown instance's rid ("" for slot 0)."""
    stack = svc.stack(sid)
    bands = stack_bands(stack) if stack is not None and not svc._is_daemon_target(sid) else ()   # the daemon: its panel
    rows = sorted((r for r in radios.load(svc._paths).radios if r.enabled and r.band in bands), key=lambda r: int(r.id[1:]))
    listed, shown = copy_rids(stack, svc._paths), inst.rid if inst is not None and inst.slot else ""
    out = []
    for r in rows:
        label = f"{r.id} · {r.band}"
        if r.id in listed:
            out.append({"label": label, "radio": r.id, "band": "", "sel": shown == r.id, "disabled": False})
        elif slot0_radio(svc._paths, sid, r.band)[0] == r.id:
            out.append({"label": label, "radio": "", "band": r.band, "sel": not shown and r.band == (band or r.band),
                        "disabled": False})
    return {"radio": shown, "radio_choices": out if len(out) > 1 or listed else []}

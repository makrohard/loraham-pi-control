"""Stack instances beyond slot 0 (item 7, DEC-W15): `config/instances.toml`, one append-only list per stack.

    kiss = ["r3", ""]

Slot k (k >= 1) is position k of the stack's list, and its value is the radio id that instance runs on. "" is a
removed instance's slot, kept reserved, so no other instance's ports (BASE + 10 x slot, commands.slot_port) move.
Slot 0 is today's instance and is never listed. Only LHPC writes the file; a file that does not read back gives
no instance beyond slot 0, with the reasons (fail-closed)."""
from __future__ import annotations

import dataclasses
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field

from . import commands, radios
from .paths import PathContainmentError

FILE = ("config", "instances.toml")
ABSENT, VALID, INVALID = "absent", "valid", "invalid"
MAX_LISTED = commands.SLOTS[-1]                     # slots 1..7: eight instances per stack (A §2.4)
_SID_RE = re.compile(r"[a-z][a-z0-9_-]*")
_RID_RE = re.compile(r"r[1-9][0-9]?")
# T2.2 (DEC-W4/W15): the daemon clients whose exclusive resources are all per-instance may have instances beyond
# slot 0; every other stack is refused with its reason.
MAY = ("kiss", "meshcore", "chat")
WHY_NOT = {"voice": "voice has one instance per box (one sound card, audio.default)",
           "meshcom": "meshcom has one instance per box in 0.14.0 (fixed ports, one firmware image)",
           "reticulum": "Reticulum runs one rnsd per box; several radios are interfaces of it (item 19)",
           "daemon": "the daemon runs per radio by the hardware list, not as a listed instance"}
HEADER = ("# Written by LHPC (lhpc stack add-instance / remove-instance). Slot k is position k of a stack's list;\n"
          "# \"\" keeps a removed instance's slot reserved, so no other instance's ports move.\n")


@dataclass(frozen=True)
class State:
    kind: str                                       # ABSENT | VALID | INVALID
    slots: dict = field(default_factory=dict)       # stack id -> (rid or "", ...), position k-1 = slot k
    reasons: tuple = ()


def path(paths):
    return paths.under(*FILE)


def load(paths) -> State:
    """The registry's state; never raises for the file's content or its path."""
    p = None
    try:
        p = path(paths)
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if p is None or radios.exists_at_all(p):    # a path that exists but cannot be read is not absent
            return State(INVALID, reasons=(f"{FILE[-1]} cannot be read ({type(exc).__name__}: {exc})",))
        return State(ABSENT)
    except (OSError, UnicodeDecodeError, PathContainmentError) as exc:
        return State(INVALID, reasons=(f"{FILE[-1]} cannot be read ({type(exc).__name__}: {exc})",))
    return load_text(text)


def load_text(text: str) -> State:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError, ValueError) as exc:
        return State(INVALID, reasons=(f"{FILE[-1]} is not valid TOML ({type(exc).__name__}: {exc})",))
    reasons = check(data)
    if reasons:
        return State(INVALID, reasons=tuple(reasons))
    return State(VALID, slots={sid: tuple(v) for sid, v in data.items()})


def check(slots) -> list:
    """Every stack id a name, every list at most MAX_LISTED long, each entry "" or r1..r99, no radio twice."""
    reasons = []
    for sid, listed_ in slots.items():
        if not isinstance(sid, str) or not _SID_RE.fullmatch(sid):
            reasons.append(f"{sid!r} is not a stack id")
            continue
        if not isinstance(listed_, (list, tuple)) or not all(isinstance(x, str) for x in listed_):
            reasons.append(f"{sid}: a list of radio ids")
            continue
        if len(listed_) > MAX_LISTED:
            reasons.append(f"{sid}: at most {MAX_LISTED} instances beyond slot 0")
        bad = [x for x in listed_ if x and not _RID_RE.fullmatch(x)]
        if bad:
            reasons.append(f"{sid}: {', '.join(map(repr, bad))} is not r1..r99")
        rids = [x for x in listed_ if x]
        if len(rids) != len(set(rids)):
            reasons.append(f"{sid}: a radio holds one instance of a stack")
    return reasons


def listed(state: State, sid: str) -> tuple:
    """((slot, rid), ...) of the stack's listed instances, reserved slots left out; () unless the state is VALID."""
    if state.kind != VALID:
        return ()
    return tuple((k, rid) for k, rid in enumerate(state.slots.get(sid, ()), 1) if rid)



def render(slots: dict) -> str:
    return HEADER + "".join(f"{sid} = [{', '.join(chr(34) + x + chr(34) for x in slots[sid])}]\n" for sid in sorted(slots))


def save(paths, slots: dict) -> list:
    """Check, then write the whole file (mkstemp, fsync, os.replace, as radios.save). [] on success, else why."""
    reasons = check(slots)
    if reasons:
        return reasons
    text = render(slots)
    if load_text(text).slots != {sid: tuple(v) for sid, v in slots.items()}:
        return ["the rendered instance list does not read back (a bug; nothing written)"]
    try:
        p = path(paths)
    except PathContainmentError as exc:
        return [f"{FILE[-1]} has no safe path ({exc}); nothing written"]
    tmp = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
        try:
            os.fchmod(fd, 0o644)
            view = memoryview(text.encode("utf-8"))
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, p)
    except OSError as exc:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return [f"{FILE[-1]} could not be written ({type(exc).__name__}: {exc}); the old file is unchanged"]
    return []


def stack_bands(stack) -> tuple:
    """The bands a stack can run on, from its manifest: a component's `bands`, else its components' fixed bands."""
    for c in stack.components:
        if c.bands:
            return tuple(c.bands)
    return tuple(sorted({c.band for c in stack.components if getattr(c, "band", "")}))


def add_instance(svc, sid: str, rid: str):
    """`lhpc stack add-instance <stack> <rid>` (item 7c): list `<stack>@<rid>` at the next free slot (append-only),
    under the config lock. Refused, writing nothing: a stack T2.2 does not allow, an unknown or disabled radio, a
    band the stack cannot run on, a radio already listed, a registry that does not read back, eight instances.
    Which radio slot 0 runs on is decided at its start (the first enabled radio of its band not listed, DEC-W16)."""
    from . import config
    from .service_base import ActionResult
    stack = svc.stack(sid) if isinstance(sid, str) else None
    if stack is None:
        return ActionResult(False, f"unknown stack {sid!r}")
    if sid not in MAY:
        return ActionResult(False, f"Cannot add an instance of {sid}: "
                                   + WHY_NOT.get(sid, f"{sid} has one instance in 0.14.0 (T2.2: kiss, meshcore, chat)"))
    try:
        with config.config_lock(svc._paths):
            hw = radios.load(svc._paths)
            radio = next((r for r in hw.radios if r.id == rid), None)
            if radio is None or not radio.enabled:
                return ActionResult(False, f"Cannot add {sid}@{rid}: {rid} is "
                                           + ("not an enabled radio of the hardware list" if radio is None else "disabled"),
                                    next_commands=["lhpc hardware"])
            if any(instance_runs(svc, c, commands.Instance(0, rid), radio.band) for c in stack.components
                   if c.process is not None):       # G1-C7 B1: slot 0 runs on that radio
                return ActionResult(False, f"Cannot add {sid}@{rid}: {sid} (slot 0) runs on {rid} — stop it first",
                                    next_commands=[f"lhpc stack stop {named(svc, sid)}"])
            bands = stack_bands(stack)
            if radio.band not in bands:
                return ActionResult(False, f"Cannot add {sid}@{rid}: {sid} runs on {' or '.join(bands)} MHz, "
                                           f"{rid} is on {radio.band} MHz")
            state = load(svc._paths)
            if state.kind == INVALID:
                return ActionResult(False, f"Cannot add {sid}@{rid}: {FILE[-1]} does not read back: "
                                           + "; ".join(state.reasons))
            slots = dict(state.slots)
            have = list(slots.get(sid, ()))
            if rid in have:
                return ActionResult(False, f"{sid}@{rid} exists already (slot {have.index(rid) + 1})")
            if len(have) >= MAX_LISTED:
                return ActionResult(False, f"Cannot add {sid}@{rid}: {sid} has {MAX_LISTED} instances beyond its own")
            slots[sid] = (*have, rid)
            reasons = save(svc._paths, slots)
    except config.ConfigLockBusy as exc:
        return ActionResult(False, f"the instance list is busy ({exc}); nothing was changed — try again")
    if reasons:
        return ActionResult(False, f"{sid}@{rid} not added: " + "; ".join(reasons))
    return ActionResult(True, f"{sid}@{rid} added: slot {len(have) + 1} on {radio.band} MHz",
                        next_commands=[f"lhpc stack start {sid}@{rid}"])


def remove_instance(svc, sid: str, rid: str):
    """`lhpc stack remove-instance <stack> <rid>`: its slot becomes reserved (""), so no other instance's ports move."""
    from . import config
    from .service_base import ActionResult
    try:
        with config.config_lock(svc._paths):
            state = load(svc._paths)
            if state.kind == INVALID:
                return ActionResult(False, f"Cannot remove {sid}@{rid}: {FILE[-1]} does not read back: "
                                           + "; ".join(state.reasons))
            have = list(state.slots.get(sid, ()) if isinstance(sid, str) else ())
            if not isinstance(rid, str) or not rid or rid not in have:
                return ActionResult(False, f"{sid}@{rid} is not a listed instance")
            _sid, inst, band, _why = resolve(svc, f"{sid}@{rid}")
            if inst is not None and any(instance_runs(svc, c, inst, band) for c in svc.stack(sid).components
                                        if c.process is not None):
                return ActionResult(False, f"Cannot remove {sid}@{rid}: it is running — stop it first",
                                    next_commands=[f"lhpc stack stop {sid}@{rid}"])
            k = have.index(rid)
            have[k] = ""
            reasons = save(svc._paths, {**state.slots, sid: tuple(have)})
    except config.ConfigLockBusy as exc:
        return ActionResult(False, f"the instance list is busy ({exc}); nothing was changed — try again")
    if reasons:
        return ActionResult(False, f"{sid}@{rid} not removed: " + "; ".join(reasons))
    # 7d: nothing is deleted, and purge takes only listed instances' folders, so the kept ones are named.
    comps = svc.stack(sid).components
    kept = [r for r in (*(f"state/{rid}/{c.state_root.split('/', 1)[1]}" for c in comps if c.state_root),
                        *(f"config/secrets/{rid}/{n}" for c in comps for n in c.secret_files))
            if (svc._paths.runtime_root / r).exists()]
    return ActionResult(True, f"{sid}@{rid} removed: slot {k + 1} stays reserved",
                        details=[f"  [kept] {r} (remove it by hand when it is no longer needed)" for r in sorted(set(kept))])


# ---- item 7c2: addressing an instance -------------------------------------------------------------------

def resolve(svc, target: str):
    """`<stack>@<rid>` → (stack id, its listed copy, the radio's band, ""), or (stack id, None, "", why) when it
    cannot be started (see `copy_of`; a disabled radio's copy comes back WITH its why). A target without "@" →
    (target, None, "", "") — today's slot 0."""
    if not isinstance(target, str) or "@" not in target:
        return target, None, "", ""
    sid, _, rid = target.partition("@")
    if not rid:
        return sid, None, "", f"{sid}@ is not a listed instance (lhpc stack add-instance {sid} <rid>)"
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


def named(svc, target: str, inst=None, band: str = "") -> str:
    """`<target>@<rid>` for a command lhpc suggests (W323: a bare stack is refused): the instance's radio, else the
    band's first enabled radio, else the stack's running instance, else the first radio it can run on; `<rid>` when
    none is known. A target that already names its radio is kept."""
    if not isinstance(target, str) or "@" in target:
        return target
    rid = getattr(inst, "rid", "") or ""
    sid = target if svc.stack(target) is not None else (svc.stack_of(target) or "")
    stack = svc.stack(sid) if sid else None
    if not rid and band and stack is not None:             # the band's first enabled radio, by rid order
        rid = next(iter(sorted((r.id for r in radios.load(svc._paths).radios if r.enabled and r.band == band),
                               key=lambda x: int(x[1:]))), "")
    if not rid and stack is not None:
        run = running_keys(svc, sid)
        rid = run[0].partition("@")[2] if run else next(iter(can_run_on(svc, stack)), "")
    return f"{target}@{rid or '<rid>'}"


def stop_keys(svc, sid: str) -> list:
    """A stack-wide remedy's instances (G1-C60 NOTE): every running one, else the one `named` names."""
    return running_keys(svc, sid) or [named(svc, sid)]


def restart_keys(svc, sid: str, marker) -> list:
    """The instances a restart-required marker asks to restart: the radios it names (the daemon's "r2 ocp_ma",
    G1-C60 B1), else stop_keys."""
    from . import restart_required
    return [f"{sid}@{r}" for r in restart_required.marker_rids(marker)] or stop_keys(svc, sid)


def holder_rid(svc, sid: str, blocker: dict) -> str:
    """The radio a resource blocker's holder holds: the one its claim names (`loraham.radio.r2`, G1-C60 B2), else its
    band's first radio (named)."""
    m = re.search(r"\.(r[1-9][0-9]?)$", str(blocker.get("resource", "")))
    return m.group(1) if m else named(svc, sid, band=blocker.get("band", "")).partition("@")[2]


def parse_target(svc, target: str, verb: str = "start"):
    """THE target of an operator entry (the CLI, then the console; CLEANUP-DESIGN-v13 13.2, W323/W326):
    `<stack>@<rid>` (or `<component>@<rid>`: its stack's instance) -> (the stack or component id, Instance, its
    radio's band, ""), or (that id, None, "", why). A bare `<stack>` is refused
    with the radios it can run on ("kiss can run on r1 or r3: use kiss@r1 or kiss@r3"), one radio included; a bare
    stop, restart or kill names its running instances. A radio with a listed copy is that copy; until the slots go
    (K4) any other radio of the stack is slot 0 on it. A disabled radio's instance comes back WITH its why (a stop
    still reaches it, G1-C7 B3)."""
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
    if rid in listed_rids(svc._paths, sid):
        copy, why = copy_of(svc, sid, rid)
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


def copy_of(svc, sid: str, rid: str = "", band: str = ""):
    """THE stack copy (CLEANUP-DESIGN-v2 section 2) → (Instance, why); (None, why) when there is none.
    A listed copy (`rid`): its band is its radio row's, and an explicit band that contradicts it is refused,
    never substituted. A disabled radio's copy comes back WITH its why (still addressed: a stop, a removal's
    running check; never started). Slot 0 (no `rid`): the operation band (explicit -> running -> fallback ->
    primary; a fixed-band stack's one band) and, on it, DEC-W16's radio; its why is DEC-W16's refusal when every radio of the band is listed."""
    stack = svc.stack(sid)
    if stack is None:
        return None, f"unknown stack {sid!r}"
    if not rid:
        bands = stack_bands(stack)                    # a fixed-band stack's operation band is "" (no store): its band
        band = svc.operation_band(sid, band) or (bands[0] if len(bands) == 1 else "")
        rid0, why = slot0_radio(svc._paths, sid, band) if band else ("", "")
        return commands.Instance(0, rid0, band=band), why
    inst = next((i for i in commands.stack_instances(stack, svc._paths) if i.slot and i.rid == rid), None)
    if inst is None:
        return None, f"{sid}@{rid} is not a listed instance (lhpc stack add-instance {sid} {rid})"
    radio = next((r for r in radios.load(svc._paths).radios if r.id == rid), None)
    if radio is None:
        return None, f"{sid}@{rid}: {rid} is not in the hardware list"
    if band and band != radio.band:
        return None, f"{sid}@{rid}: {rid} is on {radio.band} MHz, not {band} MHz"
    copy = commands.Instance(inst.slot, rid, inst.peer_slots, band=radio.band)
    if not radio.enabled:                     # G1-C7 B3: still addressed (a stop, a removal's running check), never started
        return copy, f"{sid}@{rid}: {rid} is disabled"
    if radio.band not in stack_bands(stack):
        return None, f"{sid}@{rid}: {sid} runs on {' or '.join(stack_bands(stack))} MHz, {rid} is on {radio.band} MHz"
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


def listed_rids(paths, sid: str) -> frozenset:
    """The radio ids the stack's listed instances run on (reserved slots left out)."""
    return frozenset(rid for _k, rid in listed(load(paths), sid))


def instance_rels(paths, sid: str, rels, keep: int) -> list:
    """7d: `rels` plus, for each listed instance of `sid`, the same with `<rid>` after their first `keep` segments
    (the manifest's {instance_dir} layout: a state root state/<name> -> state/<rid>/<name> with keep=1; a secret
    file <name> -> <rid>/<name> with keep=0)."""
    parts = [r.split("/") for r in rels]
    return [*rels, *("/".join([*q[:keep], rid, *q[keep:]]) for rid in sorted(listed_rids(paths, sid)) for q in parts)]


def slot0_radio(paths, sid: str, band: str):
    """Slot 0's radio on `band` (DEC-W16): the first enabled radio of the band, by rid order, that is not listed for
    the stack. (rid, "") or ("", why) when every radio of the band is listed; ("", "") when the band has no enabled
    radio at all (the caller's own refusal names that)."""
    rows = [r for r in radios.load(paths).radios if r.enabled and r.band == band]
    if not rows:
        return "", ""
    free = slot0_of(rows, listed_rids(paths, sid))
    if free:
        return free, ""
    return "", (f"every enabled radio of {band} MHz holds a listed instance of {sid} "
                f"({', '.join(sorted((r.id for r in rows), key=lambda x: int(x[1:])))}): start one of them as {sid}@<rid>")


def slot0_of(rows, taken) -> str:
    """DEC-W16's choice among one band's enabled radios: the first by rid order not in `taken`, else ""."""
    return next((r.id for r in sorted(rows, key=lambda r: int(r.id[1:])) if r.id not in taken), "")


def slot0_note(paths, sid: str, band: str, rid: str) -> str:
    """The DEC-W16 line when slot 0's radio was chosen among several: "kiss starts on r1 (433 MHz); kiss@r3 is a
    separate instance"; "" when the band has one radio."""
    others = sorted(r.id for r in radios.load(paths).radios if r.enabled and r.band == band and r.id != rid)
    if not others:
        return ""
    taken = listed_rids(paths, sid)
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
    if (inst is None or not inst.slot) and not listed_rids(svc._paths, sid):   # slot 0 is scoped only beside listed
        return svc._order_already_healthy(order, radio)      # copies, then as its own copy (start's copy_of, DEC-W16)
    # G1-C7 B4: slot 0's own rule (`_all_components_healthy`: readiness endpoints, DEGRADED) over the instance's
    # statuses: its run state and its own TCP endpoints in place of the snapshot's.
    from .probes.endpoints import tcp_endpoint_match
    snap = svc.build_snapshot()
    idx = {c.id: ss.components[c.id] for ss in snap.stacks for c in ss.stack.components}
    for _s, comp in order:
        cs = idx.get(comp.id)
        if cs is None or comp.id == svc.DAEMON_ID or comp.process is None:
            continue
        eps = [dataclasses.replace(o, spec=e, present=tcp_endpoint_match(svc._system, e.address)[0]) if e.kind == "tcp"
               else o for o in cs.endpoints or () for e in endpoints_for([o.spec], inst)]
        idx[comp.id] = dataclasses.replace(cs, run_state=instance_state(svc, comp, inst, band, cs.run_state), endpoints=eps)
    return svc._all_components_healthy(order, idx, radio)


def running_claim_keys(svc, sid: str, comp, claim) -> set:
    """G1-C7 B5: `claim`'s keys as stack `sid`'s running `comp` holds them: slot 0's key and the key of each listed
    instance of it that runs (claim_key), so two stacks' instances on one derived key conflict."""
    keys = {claim.key}
    for i in commands.stack_instances(svc.stack(sid), svc._paths):
        if i.slot and instance_runs(svc, comp, i, resolve(svc, f"{sid}@{i.rid}")[2]):
            keys.add(claim_key(claim, i))
    return keys


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
    if inst is None or not inst.rid or (not inst.slot and not listed_rids(svc._paths, sid)):
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
    taken = listed_rids(svc._paths, sid)
    if not taken:
        return {}
    b = svc.running_band(sid, comp.band or band) or comp.band or band      # slot 0's copy (stop's copy_of, DEC-W16)
    return {"exclude_rids": tuple(taken), "token": _token(svc, comp, inst, b) if inst is not None and inst.rid else None}








def settings_choice(svc, sid: str, band: str, inst=None) -> dict:
    """7f, DEC-W40: the Settings page's radio chooser of stack `sid`, one entry per enabled radio of its bands
    ("rN · band"): a listed instance opens its own settings (`radio=`); slot 0's radio of a band opens slot 0's on
    that band (`band=`, so a band-switchable stack's slot 0 moves there); any other radio of a stack that may run
    instances is greyed with "add instance". `radio` is the shown instance's rid ("" for slot 0)."""
    stack = svc.stack(sid)
    bands = stack_bands(stack) if stack is not None and not svc._is_daemon_target(sid) else ()   # the daemon: its panel
    rows = sorted((r for r in radios.load(svc._paths).radios if r.enabled and r.band in bands), key=lambda r: int(r.id[1:]))
    listed, shown = listed_rids(svc._paths, sid), inst.rid if inst is not None and inst.slot else ""
    out = []
    for r in rows:
        label = f"{r.id} · {r.band}"
        if r.id in listed:
            out.append({"label": label, "radio": r.id, "band": "", "sel": shown == r.id, "disabled": False})
        elif slot0_radio(svc._paths, sid, r.band)[0] == r.id:
            out.append({"label": label, "radio": "", "band": r.band, "sel": not shown and r.band == (band or r.band),
                        "disabled": False})
        elif sid in MAY:
            out.append({"label": f"{label} · add instance", "disabled": True, "sel": False,
                        "title": f"no {sid} instance on {r.id}: add one to use it"})
    return {"radio": shown, "radio_choices": out if len(out) > 1 or listed else []}

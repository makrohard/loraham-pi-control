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
    """`<stack>@<rid>` → (stack id, its listed Instance, the radio's band, ""), or (stack id, None, "", why) when it
    cannot be started: the stack has no listed instance on that radio, or the radio is unknown, disabled or on a
    band the stack does not run on. A target without "@" → (target, None, "", "") — today's slot 0."""
    if not isinstance(target, str) or "@" not in target:
        return target, None, "", ""
    sid, _, rid = target.partition("@")
    stack = svc.stack(sid)
    if stack is None:
        return sid, None, "", f"unknown stack {sid!r}"
    inst = next((i for i in commands.stack_instances(stack, svc._paths) if i.slot and i.rid == rid), None)
    if inst is None:
        return sid, None, "", f"{sid}@{rid} is not a listed instance (lhpc stack add-instance {sid} {rid})"
    radio = next((r for r in radios.load(svc._paths).radios if r.id == rid), None)
    if radio is None or not radio.enabled:
        return sid, None, "", f"{sid}@{rid}: {rid} is " + ("not in the hardware list" if radio is None else "disabled")
    if radio.band not in stack_bands(stack):
        return sid, None, "", f"{sid}@{rid}: {sid} runs on {' or '.join(stack_bands(stack))} MHz, {rid} is on {radio.band} MHz"
    return sid, inst, radio.band, ""


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
    rows = sorted((r for r in radios.load(paths).radios if r.enabled and r.band == band), key=lambda r: int(r.id[1:]))
    if not rows:
        return "", ""
    taken = listed_rids(paths, sid)
    free = [r.id for r in rows if r.id not in taken]
    if free:
        return free[0], ""
    return "", (f"every enabled radio of {band} MHz holds a listed instance of {sid} ({', '.join(r.id for r in rows)}): "
                f"start one of them as {sid}@<rid>")


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
        return opt, commands.instance_value(tpl, comp, svc.stack_config(comp.id, band), str(svc._paths.runtime_root),
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
    from . import daemon_control, radio_start
    from .model import ComponentKind
    sid = svc.stack_of(target) or target                 # the target's stack (the run order starts with the daemon)
    if inst is None or not inst.slot:                    # slot 0: scoped only while the stack has listed instances
        if not listed_rids(svc._paths, sid):
            return svc._order_already_healthy(order, radio)
        rid, _why = slot0_radio(svc._paths, sid, band)
        if not rid:
            return False
        inst = commands.Instance(0, rid)
    for stack, comp in order:
        if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE) or comp.kind == ComponentKind.ONESHOT:
            continue
        if comp.interactive:
            if comp.id == stack.main:
                return False
            continue
        if comp.id == svc.DAEMON_ID:
            if not daemon_control.read_view(svc._system, radio_start.daemon_key(svc, band)).ready:
                return False
            continue
        if not instance_runs(svc, comp, inst, band):
            return False
    return True


def endpoints_for(endpoints, inst) -> list:
    """The endpoints as instance `inst` serves them (item 7c2): a slot-k copy of each templated address
    (inst.subst(address_template), e.g. 127.0.0.1:8011); slot 0 / no instance: unchanged."""
    if inst is None or not inst.slot:
        return list(endpoints)
    return [dataclasses.replace(e, address=inst.subst(e.address_template)) if getattr(e, "address_template", "")
            else e for e in endpoints]


def scoped(svc, sid: str, inst, band: str):
    """The instance a start of stack `sid` concerns, when it is not just "the stack": the listed instance itself
    (slot k), or slot 0 on its radio while the stack has listed instances; None for a stack without any (today's
    by-component judgement stays)."""
    if inst is not None and inst.slot:
        return inst
    if not listed_rids(svc._paths, sid):
        return None
    rid, _why = slot0_radio(svc._paths, sid, band)
    return commands.Instance(0, rid) if rid else None


def instance_state(svc, comp, inst, band: str, snap_state):
    """A component's run state for one instance: RUNNING when it runs as that instance, else STOPPED. The daemon, a
    library/firmware/oneshot and a component without a process spec keep the snapshot's state."""
    from .model import ComponentKind, RunState
    if (comp.id == svc.DAEMON_ID or comp.process is None
            or comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE, ComponentKind.ONESHOT)):
        return snap_state
    return RunState.RUNNING if instance_runs(svc, comp, inst, band) else RunState.STOPPED


def start_state(svc, sid: str, comp, inst, band: str, snap_state):
    """The state a start judges `comp` by (item 7c2): its scoped instance's own (instance_state) when the start
    concerns one (scoped), else the snapshot's."""
    si = scoped(svc, sid, inst, band)
    return snap_state if si is None else instance_state(svc, comp, si, band, snap_state)


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
    b = svc.running_band(sid, comp.band or band) or comp.band or band
    rid, _why = slot0_radio(svc._paths, sid, b)
    return {"exclude_rids": tuple(taken), "token": _token(svc, comp, commands.Instance(0, rid), b) if rid else None}

"""Stack instances beyond slot 0 (item 7, DEC-W15): `config/instances.toml`, one append-only list per stack.

    kiss = ["r3", ""]

Slot k (k >= 1) is position k of the stack's list, and its value is the radio id that instance runs on. "" is a
removed instance's slot, kept reserved, so no other instance's ports (BASE + 10 x slot, commands.slot_port) move.
Slot 0 is today's instance and is never listed. Only LHPC writes the file; a file that does not read back gives
no instance beyond slot 0, with the reasons (fail-closed)."""
from __future__ import annotations

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
            k = have.index(rid)
            have[k] = ""
            reasons = save(svc._paths, {**state.slots, sid: tuple(have)})
    except config.ConfigLockBusy as exc:
        return ActionResult(False, f"the instance list is busy ({exc}); nothing was changed — try again")
    if reasons:
        return ActionResult(False, f"{sid}@{rid} not removed: " + "; ".join(reasons))
    return ActionResult(True, f"{sid}@{rid} removed: slot {k + 1} stays reserved")

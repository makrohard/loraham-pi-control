"""Stack instances beyond slot 0 (item 7, DEC-W15): `config/instances.toml`, one append-only list per stack.

    kiss = ["r3", ""]

Slot k (k >= 1) is position k of the stack's list, and its value is the radio id that instance runs on. "" is a
removed instance's slot, kept reserved, so no other instance's ports (BASE + 10 x slot, commands.slot_port) move.
Slot 0 is today's instance and is never listed. Only LHPC writes the file; a file that does not read back gives
no instance beyond slot 0, with the reasons (fail-closed)."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from . import commands, radios
from .paths import PathContainmentError

FILE = ("config", "instances.toml")
ABSENT, VALID, INVALID = "absent", "valid", "invalid"
MAX_LISTED = commands.SLOTS[-1]                     # slots 1..7: eight instances per stack (A §2.4)
_SID_RE = re.compile(r"[a-z][a-z0-9_-]*")
_RID_RE = re.compile(r"r[1-9][0-9]?")


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


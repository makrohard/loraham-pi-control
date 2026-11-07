"""Boot auto-restore: journal + pure planner (no service access in this module).

The JOURNAL (`state/boot-restore.json`) is the single durable record of a restore run: one
schema-versioned file with ONE canonical item list. An item is consumed the moment its state
leaves `pending` — there are no parallel plan/attempted/results structures. A malformed journal
must BLOCK automatic restoration (and gate-based evidence retirement), never look empty.

The PLANNER is deterministic and service-free: it consumes evidence records that a service-side
classifier has already validated (liveness, current-boot, manifest membership) plus marker data,
and produces plan items. Band policy: the ownership record's band is authoritative; running-band
and last-start markers are consistency checks (any disagreement between two valid sources is a
typed ambiguity — never a silent preference, never a fallback to another TX band).
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from . import procident, runtime_fs
from .daemon_control import ALLOWED_BANDS
from .paths import PathContainmentError, Paths

JOURNAL_VERSION = 1
JOURNAL_REL = ("state", "boot-restore.json")
JOURNAL_MAX_BYTES = 256 * 1024

ITEM_STATES = ("pending", "attempting", "succeeded", "failed", "cancelled")
ITEM_KINDS = ("stack", "daemon-reconcile")
# `running` is the only non-terminal run state. `truncated` is a PROJECTION (a `running` journal
# whose recorded driver is provably dead/foreign), never stored.
RUN_STATES = ("running", "done", "failed", "disabled", "no-plan", "unsafe")


def journal_path(paths: Paths) -> Path:
    return paths.under(*JOURNAL_REL)


def new_item(item_id: str, kind: str, *, target: str = "", band: str = "",
             bands: tuple[str, ...] = (), evidence_ids: tuple[str, ...] = (),
             components: tuple[str, ...] = (), rid: str = "") -> dict:
    """A plan item; `rid` (stage L5) is the radio its stack ran on, absent for a band-less or pre-0.14.0 item."""
    return {"id": item_id, "kind": kind, "target": target, "band": band,
            "bands": list(bands), "evidence_ids": list(evidence_ids),
            "components": list(components),
            "state": "pending", "result": None, "prune": None, **({"rid": rid} if rid else {})}


_RID = re.compile(r"r[1-9][0-9]?")


def slot_of(value):
    """A record's instance slot when it is one (a plain int 0..7, item 7e), else None: the record then reads by the
    instance list, as a record from before 7e does."""
    from .commands import SLOTS
    return value if type(value) is int and value in SLOTS else None


def rid_of(value) -> str:
    """A record's radio id when it is one (r1..r99), else "" (the record then reads as pre-0.14.0)."""
    return value if isinstance(value, str) and _RID.fullmatch(value) else ""


def instance_key(stack: str, rid: str, listed: dict, slot=None) -> str:
    """Item 7e: a record's instance key — `<stack>@<rid>` for a listed instance's, `<stack>` for slot 0's. The
    record's own slot decides; a record without one (before 7e) is a listed instance's when the stack lists one on
    its radio (the rule 7c2b's plain stop uses)."""
    if slot is not None:
        return f"{stack}@{rid}" if slot and rid else stack
    return f"{stack}@{rid}" if rid and rid in listed.get(stack, ()) else stack


def listed_instances(paths: Paths) -> tuple[dict, str]:
    """({stack: frozenset of listed rids}, "") from config/instances.toml, or ({}, why) when it does not read back."""
    from . import instances
    state = instances.load(paths)
    if state.kind == instances.INVALID:
        return {}, f"{instances.FILE[-1]} does not read back: " + "; ".join(state.reasons)
    return {sid: frozenset(rid for _k, rid in instances.listed(state, sid)) for sid in state.slots}, ""


def radio_filter(plan, rows, listed=None, unlisted_why: str = "") -> None:
    """D14/D15 (stage L5): a pre-0.14.0 band item (no `rid`) is not restored, W12 (nothing converted); an item whose
    radio is gone, disabled or no longer its instance's is refused with the reason (radio_daemon.restore_plan). When
    the instance list does not read back (`unlisted_why`), an item of a stack that may have instances is refused with
    that reason: its records cannot be told apart. Both move to `plan.skipped`, their evidence named."""
    from . import instances, radio_daemon
    unknown = {id(it) for it in plan.items if unlisted_why and it.get("rid")
               and it["target"].partition("@")[0] in instances.MAY}
    kept, refused = radio_daemon.restore_plan([it for it in plan.items if id(it) not in unknown], rows, listed)
    refused += [(it, unlisted_why) for it in plan.items if id(it) in unknown]
    why = {id(it): r for it, r in refused}
    for it in plan.items:
        if not any(it is k for k in kept):
            plan.skipped.append({"stack": it["target"], "evidence_ids": it["evidence_ids"],
                                 "reason": why.get(id(it), "a band item from before 0.14.0 (W12: nothing converted)")})
    plan.items = kept


def new_journal(*, boot_id: str, pid: int, process_start_time, items: list[dict]) -> dict:
    return {"version": JOURNAL_VERSION, "run_id": secrets.token_hex(8), "boot_id": boot_id,
            "pid": pid, "process_start_time": process_start_time, "state": "running",
            "items": items}


def _validate_item(it) -> str:
    if not isinstance(it, dict):
        return "item is not an object"
    if not isinstance(it.get("id"), str) or not it["id"]:
        return "item id missing"
    if it.get("kind") not in ITEM_KINDS:
        return f"unknown item kind {it.get('kind')!r}"
    if it.get("state") not in ITEM_STATES:
        return f"unknown item state {it.get('state')!r}"
    if not isinstance(it.get("target"), str) or not isinstance(it.get("band"), str):
        return "item target/band wrong type"
    if "rid" in it and not rid_of(it["rid"]):
        return "item rid is not r1..r99"
    bands = it.get("bands")
    if not isinstance(bands, list) or not all(b in ALLOWED_BANDS for b in bands):
        return "item bands invalid"
    ev = it.get("evidence_ids")
    if not isinstance(ev, list) or not all(isinstance(e, str) and e for e in ev):
        return "item evidence_ids invalid"
    comps = it.get("components")          # absent in an older journal: valid
    if comps is not None and (not isinstance(comps, list)
                              or not all(isinstance(c, str) and c for c in comps)):
        return "item components invalid"
    prune = it.get("prune")
    if prune is not None:
        # STRICT: recovery trusts `prune["ok"] is True` to skip cleanup, so an arbitrary
        # {"ok": true} object must never validate — shape and its relationship to
        # evidence_ids are enforced here.
        if (not isinstance(prune, dict) or set(prune) - {"ok", "left", "reason"}
                or "ok" not in prune or "left" not in prune):
            # BOTH keys mandatory: `left` defaulting to [] would let a bare {"ok": true}
            # validate — exactly the incomplete record recovery must never trust.
            return "item prune invalid"
        if not isinstance(prune["ok"], bool):
            return "item prune.ok invalid"
        pleft = prune["left"]
        if (not isinstance(pleft, list)
                or not all(isinstance(x, str) and x in ev for x in pleft)):
            return "item prune.left invalid"
        if prune["ok"] and pleft:
            return "item prune ok/left contradiction"
        if not prune["ok"] and not pleft and "reason" not in prune:
            return "item prune failure without left/reason"
        if "reason" in prune and not isinstance(prune["reason"], str):
            return "item prune.reason invalid"
    return ""


def validate_journal(obj) -> str:
    """"" when structurally valid, else the reason (=> UNSAFE, blocks automatic work)."""
    if not isinstance(obj, dict):
        return "journal is not an object"
    if obj.get("version") != JOURNAL_VERSION:
        return f"unknown journal version {obj.get('version')!r}"
    if not isinstance(obj.get("run_id"), str) or not obj["run_id"]:
        return "run_id missing"
    if not isinstance(obj.get("boot_id"), str):
        return "boot_id wrong type"
    # The writer's identity as its writer writes it (L24 F4): os.getpid(), and the kernel's
    # start time (an int below 2**64; 0 when it could not be read). NaN, ±inf, floats, negatives
    # and 401-digit numbers are no journal lhpc wrote.
    pid = obj.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= procident.PID_MAX_LIMIT:
        return "pid invalid"
    pst = obj.get("process_start_time")
    if not isinstance(pst, int) or isinstance(pst, bool) or not 0 <= pst < 1 << 64:
        return "process_start_time invalid"
    if obj.get("state") not in RUN_STATES:
        return f"unknown run state {obj.get('state')!r}"
    items = obj.get("items")
    if not isinstance(items, list):
        return "items missing"
    seen = set()
    for it in items:
        why = _validate_item(it)
        if why:
            return why
        if it["id"] in seen:
            return f"duplicate item id {it['id']!r}"
        seen.add(it["id"])
    return ""


def load_journal(paths: Paths) -> tuple[dict | None, str]:
    """(journal, state) with state one of "absent" | "valid" | "unsafe:<reason>". A journal that
    cannot be read or does not validate is UNSAFE — the caller must block automatic restoration
    AND gate-based evidence retirement (it cannot know what was previously consumed)."""
    p = journal_path(paths)
    try:
        state, why = runtime_fs.probe_exists(p)
        if state == "absent":
            return None, "absent"
        if state == "unknown":
            return None, f"unsafe:unreadable ({why})"
        raw = runtime_fs.read_text(paths, p, max_bytes=JOURNAL_MAX_BYTES)
    except (OSError, PathContainmentError) as exc:
        return None, f"unsafe:unreadable ({exc})"
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        return None, f"unsafe:malformed JSON ({exc})"
    why = validate_journal(obj)
    if why:
        return None, f"unsafe:{why}"
    return obj, "valid"


def write_journal(paths: Paths, journal: dict) -> bool:
    """Atomic, durable write. False on failure — callers must treat an unwritable journal as a
    driver INTEGRITY failure (exit nonzero), because crash semantics depend on durability."""
    why = validate_journal(journal)
    if why:
        return False
    try:
        runtime_fs.atomic_write(paths, journal_path(paths),
                                json.dumps(journal, indent=1), 0o600)
        return True
    except (OSError, PathContainmentError):
        return False


def unpruned_consumed(journal: dict) -> list[dict]:
    """Items whose evidence is CONSUMED (state left `pending`) but not yet successfully pruned —
    the recovery phase must clean these up (cleanup only, never start) before the journal may be
    replaced; otherwise their on-disk evidence would look foreign again after the next reboot."""
    out = []
    for it in journal.get("items", []):
        if it.get("state") == "pending":
            continue
        if not it.get("evidence_ids"):
            continue
        prune = it.get("prune") or {}
        if prune.get("ok") is True:
            continue
        out.append(it)
    return out


# --- pure planner --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    """ONE classified ownership record, as the service-side classifier hands it over. The
    classifier has already established: role == "", record verifies as NOT live, the record is
    foreign-boot (stamped) or provably stale (unstamped), and the identifiers are well-typed."""
    launch_id: str
    stack: str
    component: str
    band: str                    # the record's own band ("" for band-less)
    launched_at: float
    rid: str = ""                # stage L5: the radio the instance ran on ("" for band-less or pre-0.14.0)
    slot: int | None = None      # 7e: the instance's slot as recorded (None before 7e)
    start_scope: str = ""        # "stack" / "component" / "" (never widened)
    requested_target: str = ""


@dataclass(frozen=True)
class StackMeta:
    """Manifest facts for one stack, provided by the service side."""
    stack_id: str
    main: str
    interactive_main: bool
    declared_bands: tuple[str, ...]      # () = band-less/fixed by manifest
    fixed_band: str                      # the manifest band for non-switchable stacks ("" if none)
    # The stack's optional parts an operator may run on their own and boot restore brings back when
    # they did (`optional_role == "tickable"`, not interactive): the service fills it.
    restorable_parts: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarkerView:
    """Tri-state marker reads for one stack: state is "absent" | "valid" | "unsafe"."""
    running_band_state: str = "absent"
    running_band: str = ""
    last_start_state: str = "absent"
    last_start_band: str = ""
    last_start_at: float = 0.0           # finite when last_start_state == "valid"


@dataclass
class Plan:
    items: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)    # {stack, reason, evidence_ids}


def _band_candidates(ev: Evidence, meta: StackMeta, mk: MarkerView) -> tuple[str, str]:
    """Resolve the band for one stack item -> (band, "") or ("", ambiguity_reason).

    The ownership record's band is authoritative; valid markers are CONSISTENCY checks. Markers
    SUPPLY the candidate only for a record whose own band is empty. Any unsafe source
    blocks; any two valid disagreeing sources block. Never fall back to another band."""
    if mk.running_band_state == "unsafe" or mk.last_start_state == "unsafe":
        return "", "unsafe band marker"
    sources = []
    if ev.band:
        sources.append(("ownership", ev.band))
    if mk.running_band_state == "valid" and mk.running_band:
        sources.append(("running-marker", mk.running_band))
    if mk.last_start_state == "valid" and mk.last_start_band:
        sources.append(("last-start", mk.last_start_band))
    values = {b for _, b in sources}
    if len(values) > 1:
        return "", "band sources disagree (" + ", ".join(f"{n}={b}" for n, b in sources) + ")"
    if meta.fixed_band and not meta.declared_bands:
        # Fixed-band stack: manifest band rules; any recorded band must agree.
        if values and values != {meta.fixed_band}:
            return "", f"recorded band {values.pop()!r} contradicts manifest band {meta.fixed_band!r}"
        return meta.fixed_band, ""
    if not values:
        return "", "no valid band source for a band-switchable stack"
    band = values.pop()
    if meta.declared_bands and band not in meta.declared_bands:
        return "", f"recorded band {band!r} not declared by the stack"
    return band, ""


def derive_plan(evidence: list[Evidence], metas: dict[str, StackMeta],
                markers: dict[str, MarkerView], daemon_stack_id: str, listed=None) -> Plan:
    """Deterministic plan from CLASSIFIED evidence. One non-daemon item per instance key (7e: `<stack>` for slot 0,
    `<stack>@<rid>` for an instance `listed` names, {stack: rids}); conflicts are typed ambiguities. The band markers
    are slot 0's: a listed instance's band is its record's. Daemon evidence collapses into ONE `daemon-reconcile`
    item, LAST."""
    plan = Plan()
    by_stack: dict[str, list[Evidence]] = {}
    for ev in evidence:
        by_stack.setdefault(instance_key(ev.stack, ev.rid, listed or {}, ev.slot), []).append(ev)

    daemon_bands: set[str] = set()
    daemon_evidence: list[str] = []
    orderable: list[tuple[float, str, dict]] = []
    daemon_main = metas[daemon_stack_id].main if daemon_stack_id in metas else None

    for key, evs in sorted(by_stack.items()):
        ev_ids = [e.launch_id for e in evs]
        stack_id = evs[0].stack
        meta = metas.get(stack_id)
        if meta is None:
            plan.skipped.append({"stack": key, "reason": "unknown stack",
                                 "evidence_ids": ev_ids})
            continue
        if stack_id == daemon_stack_id:
            ok_bands = {e.band for e in evs if e.band in ALLOWED_BANDS}
            daemon_bands |= ok_bands
            daemon_evidence.extend(ev_ids)
            continue
        # An interactive main (chat) is never spawned, so it leaves no record of its own: its
        # start's evidence is the daemon that start ensured, recorded under the stack — that
        # record and no other (the scope rule below still applies). Restoring it prepares it as
        # the CLI does (config, dashboard marker, manual start required).
        mains = [e for e in evs if e.component == meta.main
                 or (meta.interactive_main and e.component == daemon_main)]
        if not mains:
            plan.skipped.append({"stack": key,
                                 "reason": "no main-component evidence",
                                 "evidence_ids": ev_ids})
            continue
        # scope rule: only an explicit full-stack scope restores the stack.
        scoped = [e for e in mains if e.start_scope == "stack"
                  and e.requested_target == stack_id]
        if not scoped:
            plan.skipped.append({"stack": key,
                                 "reason": "no full-stack-scoped evidence "
                                           "(component-scoped starts are never widened)",
                                 "evidence_ids": ev_ids})
            continue
        bands = {e.band for e in scoped}
        if len(bands) > 1 or len({e.rid for e in scoped}) > 1:
            plan.skipped.append({"stack": key,
                                 "reason": "conflicting main records on different bands or radios",
                                 "evidence_ids": ev_ids})
            continue
        ev0 = scoped[0]
        mk = markers.get(stack_id, MarkerView()) if key == stack_id else MarkerView()   # slot 0's markers
        band, why = _band_candidates(ev0, meta, mk)
        if why:
            plan.skipped.append({"stack": key, "reason": f"band ambiguity: {why}",
                                 "evidence_ids": ev_ids})
            continue
        order_key = mk.last_start_at if (mk.last_start_state == "valid"
                                         and mk.last_start_at > 0) else ev0.launched_at
        # The stack's eligible parts that ran on their OWN (a component-scoped start of that part
        # itself): started after the stack, as the operator did. A stopped part left no record.
        parts = sorted({e.component for e in evs
                        if e.start_scope == "component" and e.requested_target == e.component
                        and e.component in meta.restorable_parts and e.component != meta.main})
        orderable.append((order_key, key,
                          new_item(secrets.token_hex(6), "stack", target=key,
                                   band=band, evidence_ids=tuple(ev_ids),
                                   components=tuple(parts), rid=ev0.rid)))

    for _key, _sid, item in sorted(orderable, key=lambda t: (t[0], t[1])):
        plan.items.append(item)
    if daemon_evidence:
        plan.items.append(new_item(secrets.token_hex(6), "daemon-reconcile",
                                   bands=tuple(sorted(daemon_bands)),
                                   evidence_ids=tuple(daemon_evidence)))
    return plan

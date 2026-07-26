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
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from . import runtime_fs
from .paths import Paths, PathContainmentError

JOURNAL_VERSION = 1
JOURNAL_REL = ("state", "boot-restore.json")
JOURNAL_MAX_BYTES = 256 * 1024

ITEM_STATES = ("pending", "attempting", "succeeded", "failed", "cancelled")
ITEM_KINDS = ("stack", "daemon-reconcile")
# `running` is the only non-terminal run state. `truncated` is a PROJECTION (a `running` journal
# whose recorded driver is provably dead/foreign), never stored.
RUN_STATES = ("running", "done", "failed", "disabled", "no-plan", "unsafe")
_BANDS = ("433", "868")


def journal_path(paths: Paths) -> Path:
    return paths.under(*JOURNAL_REL)


def new_item(item_id: str, kind: str, *, target: str = "", band: str = "",
             bands: tuple[str, ...] = (), evidence_ids: tuple[str, ...] = ()) -> dict:
    return {"id": item_id, "kind": kind, "target": target, "band": band,
            "bands": list(bands), "evidence_ids": list(evidence_ids),
            "state": "pending", "result": None, "prune": None}


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
    bands = it.get("bands")
    if not isinstance(bands, list) or not all(b in _BANDS for b in bands):
        return "item bands invalid"
    ev = it.get("evidence_ids")
    if not isinstance(ev, list) or not all(isinstance(e, str) and e for e in ev):
        return "item evidence_ids invalid"
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
    pid = obj.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "pid invalid"
    pst = obj.get("process_start_time")
    if not isinstance(pst, (int, float)) or isinstance(pst, bool):
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
        import os
        if not os.path.lexists(p):
            return None, "absent"
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
    foreign-boot (stamped) or legacy-provably-stale, and the identifiers are well-typed."""
    launch_id: str
    stack: str
    component: str
    band: str                    # the record's own band ("" for band-less)
    launched_at: float
    start_scope: str = ""        # "" for legacy v0 records
    requested_target: str = ""


@dataclass(frozen=True)
class StackMeta:
    """Manifest facts for one stack, provided by the service side."""
    stack_id: str
    main: str
    interactive_main: bool
    declared_bands: tuple[str, ...]      # () = band-less/fixed by manifest
    fixed_band: str                      # the manifest band for non-switchable stacks ("" if none)


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
    SUPPLY the candidate only for a legacy record whose own band is empty. Any unsafe source
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
                markers: dict[str, MarkerView], daemon_stack_id: str) -> Plan:
    """Deterministic plan from CLASSIFIED evidence. One non-daemon item per stack; conflicts are
    typed ambiguities. Daemon evidence collapses into ONE `daemon-reconcile` item, LAST."""
    plan = Plan()
    by_stack: dict[str, list[Evidence]] = {}
    for ev in evidence:
        by_stack.setdefault(ev.stack, []).append(ev)

    daemon_bands: set[str] = set()
    daemon_evidence: list[str] = []
    orderable: list[tuple[float, str, dict]] = []

    for stack_id, evs in sorted(by_stack.items()):
        ev_ids = [e.launch_id for e in evs]
        meta = metas.get(stack_id)
        if meta is None:
            plan.skipped.append({"stack": stack_id, "reason": "unknown stack",
                                 "evidence_ids": ev_ids})
            continue
        if stack_id == daemon_stack_id:
            ok_bands = {e.band for e in evs if e.band in _BANDS}
            daemon_bands |= ok_bands
            daemon_evidence.extend(ev_ids)
            continue
        if meta.interactive_main:
            plan.skipped.append({"stack": stack_id, "reason": "interactive main — manual start",
                                 "evidence_ids": ev_ids})
            continue
        mains = [e for e in evs if e.component == meta.main]
        if not mains:
            plan.skipped.append({"stack": stack_id,
                                 "reason": "no main-component evidence",
                                 "evidence_ids": ev_ids})
            continue
        # scope rule: v1 requires an explicit full-stack scope; the classifier has already
        # resolved legacy records (start_scope=="" only survives classification when a matching
        # full-stack last-start record proved the scope).
        scoped = [e for e in mains if e.start_scope == "stack"
                  and e.requested_target == stack_id]
        if not scoped:
            plan.skipped.append({"stack": stack_id,
                                 "reason": "no full-stack-scoped evidence "
                                           "(component-scoped starts are never widened)",
                                 "evidence_ids": ev_ids})
            continue
        bands = {e.band for e in scoped}
        if len(bands) > 1:
            plan.skipped.append({"stack": stack_id,
                                 "reason": "conflicting main records on different bands",
                                 "evidence_ids": ev_ids})
            continue
        ev0 = scoped[0]
        mk = markers.get(stack_id, MarkerView())
        band, why = _band_candidates(ev0, meta, mk)
        if why:
            plan.skipped.append({"stack": stack_id, "reason": f"band ambiguity: {why}",
                                 "evidence_ids": ev_ids})
            continue
        order_key = mk.last_start_at if (mk.last_start_state == "valid"
                                         and mk.last_start_at > 0) else ev0.launched_at
        orderable.append((order_key, stack_id,
                          new_item(secrets.token_hex(6), "stack", target=stack_id,
                                   band=band, evidence_ids=tuple(ev_ids))))

    for _key, _sid, item in sorted(orderable, key=lambda t: (t[0], t[1])):
        plan.items.append(item)
    if daemon_evidence:
        plan.items.append(new_item(secrets.token_hex(6), "daemon-reconcile",
                                   bands=tuple(sorted(daemon_bands)),
                                   evidence_ids=tuple(daemon_evidence)))
    return plan

"""Radios joined to the running Reticulum node, restart-free (plan reti v21 §1c-§1d, B5 design note v2).

One node per box, owned by the radio it was started on; each further radio is a member: its saved `member_of` (the one
durable intent), its `[[LoRa <rid>]]` section in the node's config (B3), attached live through the node's own RPC
(`rnstatus --attach/--detach/--reload`) and verified in `rnstatus -j`, then its ownership record on the owner's process
(`member_of` in the record, written before the attach; never signalled, lifecycle.py). Every way out of the node — a
Stop, a failed attach, a twice-refused reload, a failed Connect — is `teardown`, which removes the block and the record
only once the node no longer lists the interface (or the node is gone). Every write of the node file and every rnstatus call runs
under the node lock (NODE_LOCK, the key every owner start and stop holds), so one member's block never interleaves
with an owner start's whole-file render.
"""
from __future__ import annotations

import json
import re
import time

from . import commands, instances, procident, reticulum_node, runtime_fs
from .config import ConfigError, update_stack_config
from .reticulum_interfaces import NODE_ID, STACK_ID

NODE_LOCK = "tcp.port.37428"          # the node's shared-instance port: an exclusive claim of every owner start/stop
TIMEOUT_S = 20.0                      # one rnstatus round (attach / detach / reload / -j)
VIEW_TIMEOUT_S = 3.0                  # a page's one `-j` (a hung RPC never holds a render)
RPC_RETRY_S = 2.0                     # the node's RPC after an owner start: retried until the start deadline
RPC_DEADLINE_S = 60.0                 # = rns's readiness_timeout: the bound of that wait (plan §1c, a25 N2)
_LISTED = re.compile(r"\[LoRa (r[1-9][0-9]?)\]$")


def _comp(svc):
    return svc.stack(STACK_ID).component(NODE_ID)


def _config_dir(svc):
    return svc._paths.under("state", "reticulum")


def rnstatus(svc, *args, timeout: float = TIMEOUT_S):
    """`rnstatus --config <the node's config dir> *args` from the node's venv -> CommandResult."""
    src = svc._lifecycle().source_dir(_comp(svc))
    return svc._system.runner.run([str(src / ".venv" / "bin" / "rnstatus"), "--config", str(_config_dir(svc)), *args],
                                  timeout=timeout, cwd=str(src))


def listed(svc, timeout: float = TIMEOUT_S):
    """The rids whose `LoRa <rid>` the running node lists (`rnstatus -j`), or None when it does not answer."""
    res = rnstatus(svc, "-j", timeout=timeout)
    if res.returncode != 0:
        return None
    try:
        ifaces = json.loads(res.stdout or "{}").get("interfaces", [])
    except (ValueError, AttributeError):
        return None
    return {m.group(1) for i in ifaces if isinstance(i, dict) and (m := _LISTED.search(str(i.get("name", ""))))}


def check(svc, rid: str, block: str) -> str:
    """Why the interface would refuse `block` ("" = it accepts it): the interface's own check of one section (the same
    code its constructor runs), from the node's venv, before the block is written or attached."""
    src = svc._lifecycle().source_dir(_comp(svc))
    path = _config_dir(svc) / f".check-{rid}"
    try:
        runtime_fs.atomic_write(svc._paths, path, block, 0o600)
        res = svc._system.runner.run([str(src / ".venv" / "bin" / "loraham-rns-check-section"), str(path)],
                                     timeout=TIMEOUT_S, cwd=str(src))
    finally:
        path.unlink(missing_ok=True)
    if res.returncode == 0:
        return ""
    line = (res.stdout or "").strip().splitlines()
    return line[0] if res.returncode == 1 and line else "the interface's section check is unavailable"


def owner_record(svc):
    """The running node's owner launch record (`node_owner`, B4), or None."""
    life = svc._lifecycle()
    return next((r for r in life.owned_records(NODE_ID, role=None)
                 if r.get(instances.NODE_OWNER) is True and life.verify_owned(r)[0]), None)


def member_record(svc, rid: str):
    """`rid`'s member record on the running node, or None."""
    life = svc._lifecycle()
    return next((r for r in life.owned_records(NODE_ID, role=None, rid=rid) if r.get(instances.MEMBER_OF)), None)


def _member_records(svc, rid: str) -> list:
    return [r for r in svc._lifecycle().owned_records(NODE_ID, role=None, rid=rid) if r.get(instances.MEMBER_OF)]


def _same_process(rec, own) -> bool:
    """`rec` names the running owner's process (pid + starttime): a record of a node that crashed does not."""
    return own is not None and (rec.get("pid"), rec.get("starttime")) == (own.get("pid"), own.get("starttime"))


def joining(svc, inst) -> str:
    """The radio a start of `inst` joins to the running node: its saved member_of names the running owner (and it is
    not that owner), else "". A join attaches its rns step and starts none of the node's clients."""
    rid = getattr(inst, "rid", "") or ""
    owner = instances.member_of(svc, STACK_ID, rid) if rid else ""
    own = owner_record(svc) if owner else None
    return rid if own is not None and own.get("rid") == owner != rid else ""


def _block(svc, owner, rid: str, overrides=None, panel=None) -> tuple[str, str]:
    """(rid's block as the node file renders it, why not): the node's file rendered in memory (B3's renderer);
    `overrides` ({file param: value}) and `panel` ({rid: section values}): a save's values not committed yet."""
    inst = instances.copy_of(svc, STACK_ID, owner)[0]
    writes = svc.write_config_files(NODE_ID, getattr(inst, "band", ""), inst=inst, overrides=overrides,
                                    _render_only=True, _panel=panel)
    text = next((w.detail for w in writes if w.component == NODE_ID and w.status == "rendered"), "")
    block = reticulum_node.section_text(text, rid)
    why = next((w.detail for w in writes if w.component == NODE_ID and w.status == "member-skipped" and w.path == rid),
               "") or next((w.detail for w in writes if w.component == NODE_ID and w.status in ("failed", "no-base")), "")
    return block, ("" if block else why or f"{rid}'s section does not render")


def _node_file(svc):
    return _config_dir(svc) / "config"


def _has_block(svc, rid: str) -> bool:
    path = _node_file(svc)
    return path.exists() and bool(reticulum_node.section_text(path.read_text(), rid))


def _write_block(svc, rid: str, block: str) -> None:
    path = _node_file(svc)
    runtime_fs.atomic_write(svc._paths, path, reticulum_node.replace_section(path.read_text(), rid, block), 0o400)


def _save_intent(svc, rid: str, owner: str) -> None:
    update_stack_config(svc._paths, STACK_ID, {instances.MEMBER_OF: owner}, inst=commands.Instance(rid))
    svc._invalidate_config()


def attach(svc, owner: str, rid: str) -> str:
    """Bring `rid` up as a member of the running node on `owner` (its intent already saved): render its block, check it,
    write it, its member record, `--attach`, verify in `-j`. "" = connected, else why — the block and the record go
    through `teardown` (kept, the card "Not connected", while the node cannot confirm the interface is gone)."""
    own = owner_record(svc)
    if own is None or own.get("rid") != owner:
        return f"the Reticulum node on {owner} is not running"
    if any(_same_process(r, own) for r in _member_records(svc, rid)) and rid in (listed(svc) or set()):
        return ""                                   # already connected
    block, why = _block(svc, owner, rid)
    if not why:
        why = check(svc, rid, block)
    if why:
        return why
    _write_block(svc, rid, block)
    if not _one_record(svc, own, owner, rid):
        return _left("the member's ownership record could not be written", teardown(svc, rid, clear_intent=False))
    name = reticulum_node.name(rid)
    res = rnstatus(svc, "--attach", name)
    if (now := listed(svc) if res.returncode == 0 else set()) is not None and rid in now:
        return ""
    if res.returncode != 0:                                # refused / attached, not confirmed / attached, not listed
        why = f"the node did not attach {name}: {(res.stdout or res.stderr or '').strip()}".rstrip(": ")
    elif now is None:
        why = f"the node attached {name} but does not answer whether it runs"
    else:
        why = f"the node attached {name} but does not list it"
    kept = teardown(svc, rid, clear_intent=False)
    return _left(why, kept) if kept or now is not None else f"{why}; {name} is gone from the node"


def _one_record(svc, own, owner: str, rid: str) -> bool:
    """`rid`'s one member record on the running owner's process (plan v25 §9.2): one of that process (pid + starttime) is
    kept, one of any other process removed first, one written only when none is left. False = it could not be written."""
    life = svc._lifecycle()
    mine = False
    for rec in _member_records(svc, rid):
        if _same_process(rec, own):
            mine = True
        else:
            life._remove_record(rec)
    if mine:
        return True
    band = next((r.band for r in instances.radios.load(svc._paths).radios if r.id == rid), "")
    ident = procident.proc_identity(own["pid"])
    return ident is not None and ident.get("starttime") == own.get("starttime") and life.record_launch(
        svc.stack(STACK_ID), _comp(svc), own["pid"], band=band, ident=ident,
        extra={"rid": rid, instances.MEMBER_OF: owner})


def _left(why: str, kept: str) -> str:
    """A failure's text, with why its block and record stay (`teardown`'s refusal) when they do."""
    return f"{why}; {kept} — it may still be live and stays tracked" if kept else why


def failed_state(svc, rid: str) -> str:
    """How a failed attach of `rid` left it: "not connected" (the teardown confirmed it gone), or "not verified" (its
    record stays: the node could not confirm the interface is gone)."""
    return "not verified" if member_record(svc, rid) is not None else "not connected"


def reconcile(svc, owner: str, deadline: float) -> list[tuple[str, str]]:
    """THE node reconcile, after every owner start (manual and boot): wait for the node's RPC until `deadline`
    (time.monotonic), then attach each intended member not live yet. [(rid, "" | why)]; the owner stands either way."""
    out, own = [], owner_record(svc)
    members = [m.rid for m in instances.node_members(svc, STACK_ID, owner)              # a crashed node's records do
               if not any(_same_process(r, own) for r in _member_records(svc, m.rid))]  # not count (plan v25 §9.2)
    if not members:
        return out
    while listed(svc) is None and time.monotonic() < deadline:
        time.sleep(RPC_RETRY_S)
    for rid in members:
        out.append((rid, attach(svc, owner, rid)))
    return out


def teardown(svc, rid: str, clear_intent: bool = True) -> str:
    """THE one way a member leaves the node (plan v24 §9.1): a Stop, Disconnect or Remove, a failed attach, a twice-refused
    reload, a failed Connect. While the node runs, an interface it lists — or one it does not answer about — is detached,
    and the block and record go only once `-j` answers without it; no rnstatus is asked when the node is not running,
    nor for a radio with neither a record nor a block (a saved intent alone: the node cannot hold it). A restart's stop
    and an attach keep the intent (`clear_intent` False). "" = gone, else why (nothing changed)."""
    recs = _member_records(svc, rid)
    if (recs or _has_block(svc, rid)) and owner_record(svc) is not None:
        name = reticulum_node.name(rid)
        if (now := listed(svc)) is None or rid in now:
            res = rnstatus(svc, "--detach", name)
            if (now := listed(svc)) is None or rid in now:
                out = (res.stdout or res.stderr or "").strip() if res.returncode != 0 else ""
                return (f"the node did not detach {name}" + (f": {out}" if out else "")
                        + (" (the node does not answer)" if now is None else ""))
    if _node_file(svc).exists():
        _write_block(svc, rid, "")
    for rec in recs:                                       # every one, however many there are (a25 F218 N1)
        svc._lifecycle()._remove_record(rec)
    if clear_intent:
        update_stack_config(svc._paths, STACK_ID, {instances.MEMBER_OF: ""}, inst=commands.Instance(rid))
        svc._invalidate_config()
    return ""


def end_node(svc, owner: str, intents: bool = True) -> None:
    """After the owner's verified stop: every member record of the node removed (the process is gone); an operator's
    Stop also clears every intent naming it (it ends the memberships, plan §1d), a restart keeps them (`intents`
    False: its start reconciles them, once per node)."""
    life = svc._lifecycle()
    for rec in [r for r in life.owned_records(NODE_ID, role=None) if r.get(instances.MEMBER_OF) == owner]:
        life._remove_record(rec)
    if not intents:
        return
    for r in instances.radios.load(svc._paths).radios:          # every radio, not only the ones eligible now (G2 F3)
        if r.id != owner and instances.member_of(svc, STACK_ID, r.id) == owner:
            update_stack_config(svc._paths, STACK_ID, {instances.MEMBER_OF: ""}, inst=commands.Instance(r.id))
    svc._invalidate_config()


def _file_values(merged: dict) -> dict:
    """A stack settings dict's file-param values by param name (flat `file_<name>` and component-scoped)."""
    scoped = f"__f__{NODE_ID}__"
    return {**{k[len("file_"):]: v for k, v in merged.items() if k.startswith("file_")},
            **{k[len(scoped):]: v for k, v in merged.items() if k.startswith(scoped)}}


def save_refusal(svc, sid: str, current: dict, merged: dict, inst) -> str:
    """Why a save of a node member's settings is refused ("" = fine): while its node runs, its would-be section must pass
    the interface's own check BEFORE the save commits (plan §1c (b)); a node that does not run checks at its start."""
    rid = getattr(inst, "rid", "") or ""
    owner = str(merged.get(instances.MEMBER_OF, "") or "") if sid == STACK_ID and rid else ""
    own = owner_record(svc) if owner and _file_values(merged) != _file_values(current) else None
    if own is None or own.get("rid") != owner:
        return ""
    block, why = _block(svc, owner, rid, overrides=_file_values(merged))
    why = why or check(svc, rid, block)
    return f"{sid}@{rid}: the interface would refuse these settings: {why}" if why else ""


def panel_refusal(svc, rid: str, section: dict) -> tuple[str, bool]:
    """A daemon panel Save of `rid` (plan v22 §8.3 B2b): (why the interface would refuse its would-be `section` — the
    interface's own check BEFORE the save commits, "" = it accepts it; `rid` owns the running node). ("", False) when
    `rid` is neither the running node's owner nor a radio joined to it: its next start or connect renders and checks."""
    own = owner_record(svc)
    owner = str(own.get("rid", "")) if own is not None else ""
    if not owner or (rid != owner and instances.member_of(svc, STACK_ID, rid) != owner):
        return "", False
    block, why = _block(svc, owner, rid, panel={rid: section})
    why = why or check(svc, rid, block)
    return (f"the interface would refuse these settings: {why}" if why else ""), rid == owner


def reload(svc, rid: str) -> str:
    """After a member's saved settings: its section re-rendered and reloaded (`--reload`, verified in `-j`); not
    confirmed (refused, not listed, or `-j` unanswered: each named as such) -> the previous section back and reloaded;
    that is not confirmed either -> `teardown` (not connected; "not verified" with block and record kept while the node
    cannot confirm the interface is gone). "" = reloaded, else
    what happened (the saved settings stand: they passed the check, a failure here is the node's)."""
    owner = instances.member_of(svc, STACK_ID, rid)
    old = reticulum_node.section_text(_node_file(svc).read_text(), rid)
    new, why = _block(svc, owner, rid)
    if why:
        return f"not reloaded: {why}"

    def _round(block):                                 # "" = reloaded and listed, else what the node said
        _write_block(svc, rid, block)
        res = rnstatus(svc, "--reload", reticulum_node.name(rid))
        if res.returncode != 0:
            return f"refused: {(res.stdout or res.stderr or '').strip()}".rstrip(": ")
        now = listed(svc)
        return ("" if rid in now else "reloaded, the node does not list it") if now is not None else \
            "reloaded, the node does not answer whether it runs"
    if not (first := _round(new)):
        return ""
    if not (second := _round(old)):
        return f"the new settings were not confirmed ({first}); the previous section is back and attached"
    kept = teardown(svc, rid, clear_intent=False)
    why = f"neither the new settings ({first}) nor the previous ones ({second}) were confirmed"
    return f"not verified: {why}; {kept} — it may still be live and stays tracked" if kept else \
        f"not connected: {why}; {reticulum_node.name(rid)} is gone from the node"


def after_save(svc, target: str, inst) -> list[str]:
    """The detail lines of a saved member's live reload (none when it is no live member), under the node lock: a busy node
    leaves it saved, not reloaded."""
    from .reslock import ResourceBusy
    rid = getattr(inst, "rid", "") or ""
    if svc.stack_of(target) != STACK_ID or not rid or member_record(svc, rid) is None:
        return []
    try:
        with svc._keys_guard("reload", STACK_ID, [NODE_LOCK]):
            why = reload(svc, rid)
    except ResourceBusy:
        return [f"  [member] {rid}: saved, not reloaded (the node is busy) — it applies at its next connect"]
    return [f"  [member] {rid}: " + (why or "reloaded live (no restart)")]


def save_join(svc, sid: str, inst) -> tuple[bool, str]:
    """Connect, step 1 (`lhpc stack start reticulum@<rid> --join`, the card's [Connect]): save `member_of` = the running
    owner, so the ordinary start of the instance joins the node (`joining`). (ok, why not)."""
    rid = getattr(inst, "rid", "") or ""
    own = owner_record(svc)
    if sid != STACK_ID or not rid:
        return False, "--join connects a radio to the running Reticulum node: reticulum@<rid>"
    if own is None:
        return False, "Reticulum is not running: start it on one radio first (that radio owns the node)"
    if rid == own["rid"]:
        return False, f"{sid}@{rid} is the node's owner"
    try:
        _save_intent(svc, rid, own["rid"])
    except (OSError, ConfigError) as exc:
        return False, f"could not save {rid}'s membership: {exc}"
    return True, ""


def drop_join(svc, inst) -> None:
    """A failed Connect leaves nothing behind (plan §1c): no block, no record, no intent (a restore's failure keeps the
    intent instead: `reconcile` never calls this)."""
    if member_record(svc, inst.rid) is None:
        teardown(svc, inst.rid)


def listed_cached(svc):
    """`listed` once per request (a page's cards share it), with the view's short timeout (a25 B5 N1)."""
    return svc._request_memo(("reticulum-listed",), lambda: listed(svc, timeout=VIEW_TIMEOUT_S))


def member_entries(svc, band: str) -> list[dict]:
    """Home's entries for the member radios of `band` (B6 note C1): a radio with a member record or a saved intent, keyed
    by its rid (one card per radio); `member` = {node, live, why}. `rnstatus -j` only when such a radio exists."""
    from . import daemon_control
    stack = svc.stack(STACK_ID)
    if stack is None:
        return []
    cand = []
    for r in (r for r in instances.radios.load(svc._paths).radios if r.band == band):
        rec, intent = member_record(svc, r.id), instances.member_of(svc, STACK_ID, r.id)
        if rec is not None or intent:
            cand.append((r.id, rec, rec[instances.MEMBER_OF] if rec is not None else intent))
    if not cand:
        return []                                       # a member-less box never asks the node (a25 B6 C1)
    own = owner_record(svc)
    lst = listed_cached(svc) if own is not None else None
    out = []
    for rid, rec, node in cand:
        live = rec is not None and lst is not None and rid in lst
        if live:
            why = ""
        elif own is None or own.get("rid") != node:
            why = f"the node on {node} is not running"
        elif lst is None:
            why = "the node does not answer"
        elif (instances.backend_of(svc, STACK_ID, _comp(svc), rid) == "daemon"
              and not daemon_control.read_view(svc._system, rid).ready):
            why = f"daemon@{rid} is not ready"
        else:
            why = "not attached — see the Reticulum log"
        out.append({"id": f"{STACK_ID}@{rid}", "sid": STACK_ID, "rid": rid, "name": stack.name, "main": stack.main,
                    "components": [], "multi_band": False, "ident": "", "license": stack.license,
                    "member": {"node": node, "live": live, "why": why}})
    return out


def members_signature(svc) -> str:
    """The dashboard signature's part for the members (a25 B6 C2): each radio's saved intent and member record, from the
    config and the records alone — never the node's RPC."""
    facts = [(r.id, instances.member_of(svc, STACK_ID, r.id), member_record(svc, r.id) is not None)
             for r in instances.radios.load(svc._paths).radios]                  # one read of each (a25 B6 nit)
    return ",".join(f"{rid}>{intent}{'+' if rec else ''}" for rid, intent, rec in facts if intent or rec)


def owner_members(svc, rid: str) -> list[str]:
    """The live members an owner's Stop disconnects (its confirm names them, B6 note C4)."""
    own = owner_record(svc)
    if own is None or own.get("rid") != rid:
        return []
    life = svc._lifecycle()
    return sorted(r["rid"] for r in life.owned_records(NODE_ID, role=None) if r.get(instances.MEMBER_OF) == rid)

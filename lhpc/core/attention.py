"""W272/W273: what needs the operator's attention, read from what the pages already compute (no probe of its own),
and the Home banner's one ✕.

Every condition also shows in its own row (a radio's on the Hardware list, a stack's on the Apps page); the banner
lists the important ones, one line each, linked to that row:
- a band's +20 dBm permission enabled (never hidden while it lasts) and its saved/running mismatch;
- a radio's own important notes (radio_start: its daemon down while a stack needs it, its stick not plugged in);
- the USB access setup still due;
- a stack whose main component is degraded or failed (a stack the operator stopped is STOPPED, so never listed);
- an unsafe task (the task banner's unsafe items).
The ✕ stores the identity of the set the page showed (state/attention-dismissed); a changed or empty set clears it, so a
fault that recovers and returns shows again (gate 1 C23).
"""
from __future__ import annotations

import hashlib
import re

from .paths import PathContainmentError


def _item(key: str, line: str, kind: str, ref: str, sticky: bool = False) -> dict:
    return {"key": key, "line": line, "kind": kind, "ref": ref, "sticky": sticky}


def conditions(svc) -> list:
    """Every important condition as {key, line, kind ("radio" | "stack" | "usb" | "task"), ref, sticky}."""
    from . import radio_start, task_hide
    from .model import RunState
    from .status import component_note
    out = []
    view = radio_start.hardware_view(svc)
    rows = [r for r in view["rows"] if r["enabled"]]
    for r in rows:
        out += [_item(f"radio:{r['rid']}:{t}", f"{r['rid']}: {t}", "radio", r["rid"]) for t in r.get("attention", [])]
    for r in rows:
        rid, band = r["rid"], r["band"]
        hp = svc.high_power_state(band, rid=rid)
        if hp.get("warn"):
            out.append(_item(f"hp:{rid}", f"+20 dBm permission enabled on {rid} ({band} MHz)", "radio", rid, sticky=True))
        if hp.get("mismatch"):
            out.append(_item(f"hpmis:{rid}", f"high-power switch, {rid} ({band} MHz): {hp['mismatch']}", "radio", rid))
    if view.get("copybox") is not None:
        out.append(_item("usb-setup", "the USB access setup is needed", "usb", ""))
    for ss in svc.build_snapshot().stacks:
        main = ss.stack.main_component
        st = ss.components.get(main.id) if main is not None else None
        if st is not None and st.run_state in (RunState.DEGRADED, RunState.FAILED):
            note = component_note(st)
            out.append(_item(f"stack:{ss.stack.id}:{st.run_state.value}",
                             f"{ss.stack.name} is {st.run_state.value}" + (f": {note}" if note else ""), "stack", ss.stack.id))
    for t in svc.running_tasks():
        if t.get("state") == "unsafe":
            out.append(_item(task_hide.unsafe_key(t.get("kind", ""), t.get("run_id", ""), t.get("attempt_id", "")),
                             f"{t.get('label') or t.get('kind')}: unsafe — needs attention", "task", t.get("href", "")))
    return out


def set_key(items) -> str:
    """The identity of the dismissible part of a set (its keys, sorted): a ✕ hides exactly this set."""
    keys = "\n".join(sorted(c["key"] for c in items if not c["sticky"]))
    return "attention:" + hashlib.sha256(keys.encode()).hexdigest()[:16]


def _store(svc):
    return svc._paths.under("state", "attention-dismissed")


def closed(svc) -> str:
    """The identity of the set the ✕ closed, "" when none is stored."""
    from . import runtime_fs
    try:
        return runtime_fs.read_text_regular(svc._paths, _store(svc), max_bytes=128).strip()
    except (OSError, PathContainmentError, ValueError):
        return ""


def banner(svc) -> list:
    """The lines Home shows: every condition, unless the operator closed this very set; then the sticky ones only. A set
    that changed or emptied since the ✕ clears it (C23 B1)."""
    from . import runtime_fs
    items, shut = conditions(svc), closed(svc)
    if shut and shut != set_key(items):
        try:
            runtime_fs.unlink(svc._paths, _store(svc))
        except (OSError, PathContainmentError):
            pass
        shut = ""
    return [c for c in items if c["sticky"]] if items and shut else items


def dismiss(svc, shown: str) -> bool:
    """The ✕: store the identity of the set the page showed (C23 B2), never one computed now; True when it is stored."""
    from . import runtime_fs
    if not re.fullmatch(r"attention:[0-9a-f]{16}", shown or ""):
        return False
    try:
        runtime_fs.atomic_write(svc._paths, _store(svc), shown + "\n", 0o600)
        return True
    except (OSError, PathContainmentError):
        return False

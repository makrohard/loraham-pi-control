"""UX (UNSAFE-X): the ✕ of an UNSAFE banner hides that item; it never starts, recovers or cancels anything.

Plain functions over the service's banner feed and its existing capped dismiss store (state/task_dismissed.json,
the last 50 keys: X7). An unsafe item hides under the key `unsafe:<kind>:<run_id>:<attempt_id>`, so a new
run or attempt of the same task shows again (X10), and an entry the cap evicts shows again too (never lose the
unsafe state). The dismiss is idempotent (X8): it succeeds when the item is hidden after the call, including an
item hidden before; an item that is no longer listed as unsafe falls through to the failed-banner rules, so a
task that has gone away is still refused (409).
"""
from __future__ import annotations


def unsafe_key(kind: str, run_id: str, attempt_id: str = "") -> str:
    return f"unsafe:{kind}:{run_id}:{attempt_id or ''}"


def _key_of(item: dict) -> str:
    return unsafe_key(item.get("kind", ""), item.get("run_id", ""), item.get("attempt_id", ""))


def visible(items: list, dismissed: set) -> list:
    """The banner feed without the unsafe items the operator hid."""
    return [t for t in items if not (t.get("state") == "unsafe" and _key_of(t) in dismissed)]


def dismiss_unsafe(svc, kind: str, run_id: str, attempt_id: str = ""):
    """True when the named unsafe item is hidden after the call (already hidden, or hidden now), False when the
    hide could not be written, None when no such unsafe item is listed (the caller's failed rules apply)."""
    if not run_id:
        return None
    key = unsafe_key(kind, run_id, attempt_id)
    if key in svc._task_dismissed_ids():
        return True
    for t in svc.running_tasks():
        if t.get("state") == "unsafe" and _key_of(t) == key:
            return svc._task_dismiss_add(key)
    return None

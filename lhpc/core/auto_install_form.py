"""The Auto-Install page keeps the operator's selection (W206).

After Install the page came back with every box checked again, whatever was chosen. The selection a POST
submitted is kept in the console's signed cookie (`to_session`), and the page renders each row from it (`view`):
install, version, tests and TX per stack. A saved row that no longer fits (an unknown stack, a version the
stack does not offer, a value of the wrong type) is ignored, so that row shows today's default.
"""
from __future__ import annotations

SESSION_KEY = "_ai_selection"
VERSION_MAX = 32                 # a channel name; a longer value is kept as "" (the cookie stays small)


def to_session(selection: dict) -> dict:
    """The submitted per-stack selection, as plain JSON for the signed cookie. A version longer than
    VERSION_MAX is stored as "" (no channel is that long): a crafted POST cannot grow the cookie past
    the browser's limit, which would drop it and its CSRF token with it."""
    def _ver(v):
        ver = v.get("version", "")
        return ver if isinstance(ver, str) and len(ver) <= VERSION_MAX else ""
    return {sid: {"install": bool(v.get("install")), "version": _ver(v),
                  "tests": bool(v.get("tests")), "tx": bool(v.get("tx"))}
            for sid, v in selection.items() if isinstance(v, dict)}


def view(rows: list, saved) -> dict:
    """{"rows": {stack id: {install, version, tests, tx}} for each row the saved selection fits, "all_install":
    whether the All box is checked}. Without a saved selection: no rows (the template's defaults), All checked."""
    out: dict = {}
    for r in rows:
        v = saved.get(r["id"]) if isinstance(saved, dict) else None
        if not isinstance(v, dict):
            continue
        flags = (v.get("install"), v.get("tests"), v.get("tx"))
        if not all(isinstance(f, bool) for f in flags) or v.get("version") not in r["channels"]:
            continue
        out[r["id"]] = {"install": flags[0], "version": v["version"],
                        "tests": flags[1] and bool(r["testable"]), "tx": flags[2] and bool(r["tx_capable"])}
    return {"rows": out, "all_install": all(out.get(r["id"], {"install": True})["install"] for r in rows)}

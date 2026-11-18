"""U4 (W423, PLAN-UI-v4 K3): the Stacks page's submenus as ONE descriptor list per owner.

An owner is the controller row ("controller") or one stack (its id). Each descriptor names a level's
HTML id, its parent level, its label and the ONE partial (with that partial's own variables) the
Stacks page includes for it, so its own page renders the same markup. The tree page, the breadcrumb
and the page route all read these lists; a (owner, id) pair is valid only if the id is in that
owner's list. The conditions below mirror the templates' (tests compare both, level by level).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lhpc.core import rflog as _rflog
from lhpc.core.radio_start import link_kinds

CONTROLLER = "controller"


@dataclass(frozen=True)
class Dest:
    """An existing route a level opens as its page (U5 rule 21): its endpoint and fixed, whitelisted arguments.
    `radio`: the page's validated ?radio= goes along (a stack's program log); `rf`: an RF log, the stack that owns
    the registry entry (its writer and job come from the registry, never from the URL)."""
    endpoint: str
    args: tuple = ()
    radio: bool = False
    rf: str = ""


# U5 (rule 21): a level whose page is an existing route: its "page ›" and tree entry link that route, and its
# /stacks/<owner>/menu/<id> answers 302 there, so one block has one page. LOGS-IN-TREE: the log levels too; a
# program's log and a stack's RF log by their id prefix (own_page).
OWN_PAGES = {"stack-hardware-daemon": Dest("hardware_view"),
             "controller-logs": Dest("controller_logs"),
             "webserver-row--logs": Dest("webserver_logs"),
             "firewall-row--log": Dest("firewall_logs")}
_PROGRAM_LOG, _RF_LOG = "log-", "rflog-"


# DAEMON-MONITOR-TREE: a radio's live daemon views, each a level under its "Monitor · rN" (his words), in page order
DAEMON_VIEWS = (("status", "Get STATUS"), ("stats", "Get STATS"), ("socket", "View Socket"), ("rxtx", "RX/TX View"))


def daemon_views(sid: str, rid: str) -> list[dict]:
    """The views of the daemon stack `sid` on radio `rid`: {id, rid, kind, label}, the descriptors' and the
    Monitor template's one list."""
    return [{"id": f"stack-settings-{sid}--{rid}-{kind}", "rid": rid, "kind": kind, "label": f"{name} · {rid}"}
            for kind, name in DAEMON_VIEWS]


def rf_entry(sid: str):
    """The RF-log registry entry the stack `sid` owns (its config store holds the switch), or None."""
    return next((e for e in _rflog.REGISTRY if e.owner == sid), None)


def own_page(mid: str) -> Dest | None:
    """The route a level opens as its page, or None (its page is /stacks/<owner>/menu/<id>)."""
    if mid in OWN_PAGES:
        return OWN_PAGES[mid]
    if mid.startswith(_PROGRAM_LOG):
        return Dest("logs_view", (("target", mid[len(_PROGRAM_LOG):]),), radio=True)
    if mid.startswith(_RF_LOG) and rf_entry(mid[len(_RF_LOG):]) is not None:
        return Dest("logs_view", radio=True, rf=mid[len(_RF_LOG):])
    return None


def stack_logs(stack) -> list[dict]:
    """LOGS-IN-TREE: the entries of a stack's Logs level, in page order (the descriptors and the template read
    this one list): a "program" entry for each program that writes a log (the code's `writes_log`, as Home's log
    links; labelled with its manifest name), then the "rf" entry of the RF log of the registry entry the stack owns,
    or a "note" when another stack writes the RF log of this one's surface (Graywolf's: the KISS TNC writes it)."""
    sid = stack.id
    out = [{"id": _PROGRAM_LOG + c.id, "owner": sid, "label": c.name, "kind": "program"}
           for c in stack.components if link_kinds(c)["writes_log"]]
    own = rf_entry(sid)
    other = next((e for e in _rflog.REGISTRY if e.surface == sid and e.owner != sid), None)
    if own is not None:
        out.append({"id": _RF_LOG + sid, "owner": sid, "label": "RF log", "kind": "rf"})
    elif other is not None:
        out.append({"id": _RF_LOG + sid, "owner": sid, "label": "RF log", "kind": "note", "rf_owner": other.owner})
    return out


@dataclass(frozen=True)
class Submenu:
    id: str
    parent: str | None
    label: str
    template: str
    vars: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def page(self) -> Dest | None:
        """The level's own page when it is an existing route (own_page), else None."""
        return own_page(self.id)


def _radio_suffix(key: str) -> str:
    """" · rN" for a per-instance id (`<page>@rN`), else ""."""
    return f" · {key.rsplit('@', 1)[1]}" if "@" in key else ""


def controller_menus(ctx: dict) -> list[Submenu]:
    """The controller row and its submenus, from the Stacks page context `ctx`."""
    out = [Submenu("controller-row", None, ctx["controller"]["display_name"], "_controller_row.html")]

    def add(mid, parent, label, template, **kw):
        out.append(Submenu(mid, parent, label, template, kw))

    add("controller-update", "controller-row", "Update", "_ctl_update.html")
    add("controller-system-deps", "controller-row", "System dependencies", "_ctl_sysdeps.html")
    add("controller-backup-secrets", "controller-row", "Backup secrets", "_ctl_secrets.html")
    add("operator-callsign", "controller-row", "Global operator callsign", "_ctl_callsign.html")
    if ctx.get("network_view"):
        add("controller-network", "controller-row", "Network", "_network.html")
    if ctx.get("ws_mon"):
        ws = {"mon": ctx["ws_mon"], "certs": ctx["ws_certs"], "modes": ctx["ws_modes"],
              "loopback": ctx["ws_loopback"]}
        add("webserver-row", "controller-row", "Webserver and mTLS proxy", "_webserver.html", **ws)
        add("webserver-row--lhpc-webgui", "webserver-row", "LHPC Webserver", "_ws_console.html", **ws)
        add("ws-stackwebs", "webserver-row", "Stacks Web Proxies", "_ws_stackwebs.html", **ws)
        add("webserver-row--monitor", "webserver-row", "Monitor", "_ws_monitor.html", **ws)
        add("webserver-row--certificates", "webserver-row", "Certificates", "_ws_certs.html", **ws)
    if ctx.get("firewall_view"):
        add("firewall-row", "controller-row", "Firewall", "_firewall.html")
        add("firewall-row--policy", "firewall-row", "Policy", "_fw_policy.html")
        add("firewall-row--configure", "firewall-row", "Configure", "_fw_configure.html")
        add("firewall-apply", "firewall-row", "Apply & commands", "_fw_apply.html")
        add("firewall-apply--other-commands", "firewall-apply", "Other commands", "_fw_other.html")
    if ctx.get("gps_global"):
        add("gps-row", "controller-row", "Position (GPS)", "_gps.html")
        add("gps-monitor", "gps-row", "Monitor", "_gps_monitor.html")
        add("gps-settings", "gps-row", "Settings", "_gps_settings.html")
    # LOGS-IN-TREE: each at the end of its row; the page is the existing log page (OWN_PAGES)
    if ctx.get("ws_mon"):
        add("webserver-row--logs", "webserver-row", "Logs", "_log_panel.html", lp=LOG_PANELS["webserver-row--logs"])
    if ctx.get("firewall_view"):
        add("firewall-row--log", "firewall-row", "Log", "_log_panel.html", lp=LOG_PANELS["firewall-row--log"])
    add("controller-logs", "controller-row", "Logs", "_log_panel.html", lp=LOG_PANELS["controller-logs"])
    return out


# LOGS-IN-TREE B2: the controller's, the webserver's and the firewall's log panels, their preview sources
# (/api/logpreview/<source>, a fixed whitelist) in the order of their full page's selector
LOG_PANELS = {
    "controller-logs": {"id": "controller-logs", "owner": CONTROLLER, "label": "Logs",
                        "sources": (("web", "controller-web"), ("self-update", "controller-selfupdate"),
                                    ("boot-restore", "controller-boot-restore"))},
    "webserver-row--logs": {"id": "webserver-row--logs", "owner": CONTROLLER, "label": "Logs",
                            "sources": (("error", "webserver-error"), ("access", "webserver-access"))},
    "firewall-row--log": {"id": "firewall-row--log", "owner": CONTROLLER, "label": "Log",
                          "sources": (("firewall.log", "firewall"),)},
}


def stack_menus(g: dict) -> list[Submenu]:
    """One stack's row and its submenus, from its Stacks group `g` (built with its body fields)."""
    sid = g["stack"].id
    top, inst = f"stackrow-{sid}", f"stack-install-{sid}"
    out = [Submenu(top, None, g["stack"].name, "_stack_row.html", {"g": dict(g, inline=True)})]

    def add(mid, parent, label, template, **kw):
        out.append(Submenu(mid, parent, label, template, {"g": g, **kw}))

    def evidence(parent, st):
        if st.evidence:
            add(f"{parent}--evidence", parent, "Evidence", "_evidence.html", st=st, card_id=parent)

    add(inst, top, "Install", "_stack_install.html")
    if g["deps"]:
        deps = f"{inst}--dependencies"
        add(deps, inst, "Dependencies", "_stack_deps.html")
        for comp, st in g["deps"]:
            cid = f"comp-{comp.id}"
            add(cid, deps, comp.id, "_stack_comp.html", comp=comp, st=st)
            add(f"{cid}--info", cid, "Info", "_comp_info.html", comp=comp, st=st)
            evidence(f"{cid}--info", st)
    add(f"{inst}--system-dependencies", inst, "System dependencies", "_stack_sysdeps.html")
    if sid == "daemon":
        add("stack-hardware-daemon", top, "Hardware", "_stack_hardware.html")

    def info():
        add(f"stack-info-{sid}", top, "Info", "_stack_info.html")
        evidence(f"stack-info-{sid}", g["main"][1])

    if g["main"] and sid != "daemon":
        info()
    settings, view = f"stack-settings-{sid}", g["view"]
    sv = {"stack": g["stack"], "view": view, "config_groups": g["config_groups"], "settings_id": settings}
    add(settings, top, "Settings", "_stack_settings.html", **sv)
    if view.get("hw_setups") and view.get("high_power"):
        add("high-power-daemon", settings, "High-power +20 dBm permission (SX127x)",
            "_settings_high_power.html", **sv)
    for radio in view.get("radios") or ():
        if radio["reachable"]:
            rid = radio["rid"]
            add(f"{settings}--{rid}-monitor", settings, f"Monitor · {rid}", "_settings_radio_monitor.html",
                radio=radio, **sv)
            for dv in daemon_views(sid, rid):
                add(dv["id"], f"{settings}--{rid}-monitor", dv["label"], "_daemon_view.html", dv=dv, radio=radio, **sv)
            add(f"{settings}--{rid}-live", settings, f"Live radio settings · {rid}", "_settings_radio_live.html",
                radio=radio, **sv)
    if view.get("live_band"):
        add(f"{settings}--repository", settings, "Repositories", "_settings_repo.html", **sv)
    if g["daemon_params"]:
        add(f"stack-daemon-params-{sid}", top, "Daemon radio parameters", "_daemon_params.html",
            stack_id=sid, daemon_params=g["daemon_params"])
    if g["main"] and sid == "daemon":
        info()
    creds = g["ui_creds_list"]
    for c in creds:
        label = "Password" + (f" — {c['name']}" if len(creds) > 1 else "") + (f" — {c['instance']}" if c.get("instance") else "")
        add(f"stack-password-{c['component_id']}", top, label, "_stack_password.html", c=c)
    if g["stack_webs"]:                                  # U3 (K2): one submenu, a level per instance page under it
        add(f"stack-webservers-{sid}", top, "Web Proxies", "_stackwebs.html")
    for sw in g["stack_webs"]:
        wid, rs = f"stack-webserver-{sw['page_id']}", _radio_suffix(sw["page_id"])
        add(wid, f"stack-webservers-{sid}", sw["heading"], "_stackweb.html", stack_web=sw)
        add(f"{wid}--settings", wid, "Settings" + rs, "_stackweb_settings.html", stack_web=sw)
        add(f"{wid}--info", wid, "Info" + rs, "_stackweb_info.html", stack_web=sw)
    # LOGS-IN-TREE: every row ends with its Logs: a program's log and the RF log open their log page (own_page)
    logs = f"logs-{sid}"
    add(logs, top, "Logs", "_stack_logs.html")
    for lp in stack_logs(g["stack"]):
        add(lp["id"], logs, lp["label"], "_rflog_note.html" if lp["kind"] == "note" else "_log_panel.html", lp=lp)
    return out


def chain(menus: list[Submenu], mid: str) -> list[Submenu]:
    """The levels from the owner's row down to `mid` ([] when `mid` is not in `menus`)."""
    by_id = {m.id: m for m in menus}
    out, m = [], by_id.get(mid)
    while m is not None:
        out.append(m)
        m = by_id.get(m.parent) if m.parent else None
    return out[::-1]


def children(menus: list[Submenu]) -> dict:
    """parent id (None for the row) -> its levels, in page order (the tree page)."""
    kids: dict = {}
    for m in menus:
        kids.setdefault(m.parent, []).append(m)
    return kids

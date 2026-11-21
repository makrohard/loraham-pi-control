"""U4 (W423, PLAN-UI-v4 K3): the Stacks page's submenus as ONE descriptor list per owner.

An owner is the controller row ("controller") or one stack (its id). Each descriptor names a level's
HTML id, its parent level, its label and the ONE partial (with that partial's own variables) the
Stacks page includes for it, so its own page renders the same markup. The tree page, the breadcrumb
and the page route all read these lists; a (owner, id) pair is valid only if the id is in that
owner's list. The conditions below mirror the templates' (tests compare both, level by level).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lhpc.core import rflog as _rflog
from lhpc.core.radio_start import link_kinds

CONTROLLER = "controller"


@dataclass(frozen=True)
class Dest:
    """An existing route a level opens as its page (U5 rule 21): its endpoint and fixed, whitelisted arguments.
    `radio`: the page's validated ?radio= goes along (a stack's program log); `rf`: an RF log, the stack that owns
    the registry entry (its writer and job come from the registry, never from the URL); `build`: a component's
    install log (its newest clone/build/test job, from the service; none: no own page)."""
    endpoint: str
    args: tuple = ()
    radio: bool = False
    rf: str = ""
    build: bool = False


# U5 (rule 21): a level whose page is an existing route: its "page ›" and tree entry link that route, and its
# /stacks/<owner>/menu/<id> answers 302 there, so one block has one page. LOGS-IN-TREE: the log levels too; a
# program's log and a stack's RF log by their id prefix (own_page).
OWN_PAGES = {"stack-hardware-daemon": Dest("hardware_view"),
             "controller-logs": Dest("controller_logs"),
             "webserver-row--logs": Dest("webserver_logs"),
             "firewall-row--log": Dest("firewall_logs")}
_PROGRAM_LOG, _RF_LOG, _BUILD_LOG = "log-", "rflog-", "build-"


def daemon_label(ready, reachable, radio_state) -> str:
    """UI-REORG s9: the daemon pill's text, the ONE readiness -> label mapping (the badge macro renders it, the
    /api/daemon/<rid> answer carries it for dash.js)."""
    if ready:
        return "daemon READY"
    return f"daemon ({radio_state or 'NOT READY'})" if reachable else "daemon offline"


# DAEMON-MONITOR-TREE + F7: a daemon's live views, the tree nodes under its ONE "Monitor" (Q2 A: their display
# names), in page order; they show inline in the tree and have no page of their own (Q3: only Monitor has one)
# RADIO (daemon 1.5.0 `GET RADIO`): his word, the line "STATUS RADIO STATS SOCKET RX TX"
DAEMON_VIEWS = (("status", "STATUS"), ("radio", "RADIO"), ("stats", "STATS"), ("socket", "SOCKET"),
                ("rxtx", "RX/TX View"))


def monitor_id(sid: str) -> str:
    """F7: the id of the daemon stack `sid`'s one Monitor level (radio-less: it shows the Settings radio)."""
    return f"stack-settings-{sid}--monitor"


def daemon_views(sid: str, rid: str) -> list[dict]:
    """The views of the daemon stack `sid` on radio `rid`: {id, rid, kind, label}, the descriptors' and the
    Monitor template's one list (the label "STATUS · r1" is the view's heading)."""
    return [{"id": f"{monitor_id(sid)}-{kind}", "rid": rid, "kind": kind, "label": f"{name} · {rid}"}
            for kind, name in DAEMON_VIEWS]


# UI-REORG s3/s4: a per-radio level id of a daemon's Settings: `stack-settings-<sid>--<rid>-live` (F7: the Monitor
# and its views are radio-less now); the page radio comes from the id, and the radio switch links the same kind's id
_PER_RID = re.compile(r"^(stack-settings-[a-z0-9-]+?--)(r[0-9]+)-(live)$")


def id_radio(mid: str) -> str:
    """The radio a per-radio level id names ("" for any other id)."""
    m = _PER_RID.match(mid or "")
    return m.group(2) if m else ""


def with_radio(mid: str, rid: str) -> str:
    """The same level of radio `rid` (a per-radio id), else `mid` unchanged."""
    m = _PER_RID.match(mid or "")
    return f"{m.group(1)}{rid}-{m.group(3)}" if m else mid


# DAEMON-PARAMS-INTO-SETTINGS (#70, 11-18): the stacks whose "Daemon radio parameters" level stands under
# their Settings (after its per-radio levels), following the Settings radio switch; the template reads the same rule
PARAMS_IN_SETTINGS = ("daemon",)


def params_in_settings(sid: str) -> bool:
    return sid in PARAMS_IN_SETTINGS


def rf_entry(sid: str):
    """The RF-log registry entry the stack `sid` owns (its config store holds the switch), or None."""
    return next((e for e in _rflog.REGISTRY if e.owner == sid), None)


def own_page(mid: str, stack=None) -> Dest | None:
    """The route a level opens as its page, or None (its page is /stacks/<owner>/menu/<id>). UI-REORG s6: a program's
    log takes the page radio only when it is a per-radio log of `stack` (log_group); a single log links without it."""
    if mid in OWN_PAGES:
        return OWN_PAGES[mid]
    if mid.startswith(_PROGRAM_LOG):
        cid = mid[len(_PROGRAM_LOG):]
        comp = next((c for c in stack.components if c.id == cid), None) if stack is not None else None
        return Dest("logs_view", (("target", cid),), radio=comp is None or log_group(comp) == "per-radio")
    if mid.startswith(_BUILD_LOG):
        return Dest("logs_view", (("target", mid[len(_BUILD_LOG):]),), build=True)
    if mid.startswith(_RF_LOG) and rf_entry(mid[len(_RF_LOG):]) is not None:
        return Dest("logs_view", radio=True, rf=mid[len(_RF_LOG):])
    return None


_DAEMON_COMPONENT = "loraham-daemon"           # ControllerService.DAEMON_ID: the radio daemon writes one log per radio


def log_group(comp) -> str:
    """UI-REORG s6: "per-radio" for a program that runs once per radio (a process with an `instance_arg`, the daemon),
    else "single"; an RF log entry by its registry entry (_rf_group)."""
    per = comp.id == _DAEMON_COMPONENT or bool(comp.process and comp.process.instance_arg)
    return "per-radio" if per else "single"


def log_in_tree(comp) -> bool:
    """Whether a component's log is an entry of its stack's Logs (stack_logs: a program that writes a log)."""
    return bool(link_kinds(comp)["writes_log"])


def _rf_group(e) -> str:
    """An RF log's group: "per-radio" when it is one file per radio (per_radio, or an instance's: instanced)."""
    return "per-radio" if e.per_radio or e.instanced else "single"


def stack_logs(stack) -> list[dict]:
    """LOGS-IN-TREE: the entries of a stack's Logs level, in page order (the descriptors and the template read
    this one list): a "program" entry for each program that writes a log (the code's `writes_log`, as Home's log
    links; labelled with its manifest name), then the "rf" entry of the RF log of the registry entry the stack owns,
    or a "note" when another stack writes the RF log of this one's surface (Graywolf's: the KISS TNC writes it)."""
    sid = stack.id
    out = [{"id": _PROGRAM_LOG + c.id, "owner": sid, "label": c.name, "kind": "program", "group": log_group(c)}
           for c in stack.components if log_in_tree(c)]
    own = rf_entry(sid)
    other = next((e for e in _rflog.REGISTRY if e.surface == sid and e.owner != sid), None)
    if own is not None:
        out.append({"id": _RF_LOG + sid, "owner": sid, "label": "RF log", "kind": "rf", "group": _rf_group(own)})
    elif other is not None:
        out.append({"id": _RF_LOG + sid, "owner": sid, "label": "RF log", "kind": "note", "rf_owner": other.owner,
                    "group": _rf_group(other)})
    return out


# UI-REORG c13 (OPEN-UX2-3, waits his word): the lhpc-built-in GPS components (meshtastic-gps, meshcom-gps,
# meshcore-gps) have no source, build or test, so no install log and no entry; True gives them one ("no log yet").
_GPS_ENTRIES = False


def stack_build_logs(stack) -> list[dict]:
    """UI-REORG c13 (his OPEN-UX2-2 = A): Install › Build logs: one "build" entry per component of the stack, its
    dependencies included, in manifest order, for a component that can have an install log (a source, build steps
    or a host test); each shows that component's newest clone/build/test log (app: install_log)."""
    return [{"id": _BUILD_LOG + c.id, "owner": stack.id, "label": c.name, "kind": "build"}
            for c in stack.components
            if c.source or c.build_steps or c.test_argv or (_GPS_ENTRIES and c.id.endswith("-gps"))]


def log_groups(entries) -> list[tuple]:
    """UI-REORG s6: [(group level suffix, label, entries)] of a stack's Logs when it has a per-radio log (Per radio,
    then Single, each only with entries), else [] (a single-only stack: no switch, no groups)."""
    if not any(e["group"] == "per-radio" for e in entries):
        return []
    out = [("per-radio", "Per radio", [e for e in entries if e["group"] == "per-radio"]),
           ("single", "Single", [e for e in entries if e["group"] == "single"])]
    return [g for g in out if g[2]]


@dataclass(frozen=True)
class Submenu:
    id: str
    parent: str | None
    label: str
    template: str
    vars: dict = field(default_factory=dict, compare=False, hash=False)
    page_less: bool = False         # F7 (G1 B1): a tree node shown inline only: no page, no "page ›", no tree link

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

    add("controller-install", "controller-row", "Install", "_ctl_install.html")           # F1: his proposal C
    add("controller-update", "controller-install", "Update", "_ctl_update.html")
    add("controller-system-deps", "controller-install", "System dependencies", "_ctl_sysdeps.html")
    add("operator-callsign", "controller-row", "Global operator callsign", "_ctl_callsign.html")
    if ctx.get("network_view"):
        add("controller-network", "controller-row", "Network", "_network.html")
    if ctx.get("ws_mon"):
        ws = {"mon": ctx["ws_mon"], "certs": ctx["ws_certs"], "modes": ctx["ws_modes"],
              "loopback": ctx["ws_loopback"]}
        add("webserver-row", "controller-row", "Webserver and mTLS proxy", "_webserver.html", **ws)
        add("webserver-row--lhpc-webgui", "webserver-row", "LHPC Webserver", "_ws_console.html", **ws)
        add("ws-stackwebs", "webserver-row", "Web Proxies All Stacks", "_ws_stackwebs.html", **ws)
        add("webserver-row--monitor", "webserver-row", "Monitor", "_ws_monitor.html", **ws)
        add("webserver-row--certificates", "webserver-row", "Certificates", "_ws_certs.html", **ws)
        for part, label in (("pki", "PKI"), ("server", "Server certificate"), ("client", "Client certificate")):   # L78
            add(f"webserver-row--certificates--{part}", "webserver-row--certificates", label, f"_ws_cert_{part}.html", **ws)
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
    # UI-REORG s7: Backup secrets just before the controller's Logs
    add("controller-backup-secrets", "controller-row", "Backup secrets", "_ctl_secrets.html")
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
    add(f"{inst}--actions", inst, "Actions", "_stack_install_actions.html")         # UI-REORG s2
    if g["view"].get("sources"):
        add(f"{inst}--repositories", inst, "Repositories", "_stack_repos.html")
    if g["deps"]:
        deps = f"{inst}--dependencies"
        add(deps, inst, "Dependencies", "_stack_deps.html")
        for comp, st in g["deps"]:
            cid = f"comp-{comp.id}"
            add(cid, deps, comp.id, "_stack_comp.html", comp=comp, st=st)
            add(f"{cid}--info", cid, "Info", "_comp_info.html", comp=comp, st=st)
            evidence(f"{cid}--info", st)
    add(f"{inst}--system-dependencies", inst, "System dependencies", "_stack_sysdeps.html")
    builds = stack_build_logs(g["stack"])
    if builds:
        add(f"{inst}--build-logs", inst, "Build logs", "_stack_build_logs.html")
        for lp in builds:
            add(lp["id"], f"{inst}--build-logs", lp["label"], "_log_panel.html", lp=lp)
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
        add("high-power-daemon", settings, f"High-power +20 dBm permission · {view['high_power'][0]['rid']}",
            "_settings_high_power.html", **sv)
    # UI-REORG s3: the per-radio levels of EVERY radio (always present, daemon up or not); the Settings switch shows
    # the selected radio's. Only that one carries the read state; the others are rid-only (no daemon read).
    sel = (view.get("radios") or [None])[0]
    lives = (view.get("live_radios") or ()) if view.get("live_band") else ()
    if lives:                       # F7: ONE Monitor, on the radio the Settings switch picks; its four views page-less
        first = {"rid": lives[0][0], "band": lives[0][2]}
        radio = sel if sel and any(sel["rid"] == rid for rid, _n, _b in lives) else first
        add(monitor_id(sid), settings, "Monitor", "_settings_radio_monitor.html", radio=radio, **sv)
        for dv, (_kind, name) in zip(daemon_views(sid, radio["rid"]), DAEMON_VIEWS, strict=True):
            out.append(Submenu(dv["id"], monitor_id(sid), name, "_daemon_view.html", dict(g=g, dv=dv, radio=radio, **sv),
                               page_less=True))
    for rid, _name, band in lives:
        radio = sel if sel and sel["rid"] == rid else {"rid": rid, "band": band}
        add(f"{settings}--{rid}-live", settings, f"Live radio settings · {rid}", "_settings_radio_live.html",
            radio=radio, **sv)
    if g["daemon_params"]:
        add(f"stack-daemon-params-{sid}", settings if params_in_settings(sid) else top, "Daemon radio parameters",
            "_daemon_params.html", stack_id=sid, daemon_params=g["daemon_params"])
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
    entries = stack_logs(g["stack"])
    groups = log_groups(entries) or [("", "", entries)]
    for suffix, label, items in groups:
        parent = f"{logs}--{suffix}" if suffix else logs
        if suffix:
            add(parent, logs, label, "_stack_log_group.html", grp=(suffix, label, items))
        for lp in items:
            add(lp["id"], parent, lp["label"], "_rflog_note.html" if lp["kind"] == "note" else "_log_panel.html", lp=lp)
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

"""U4 (W423, PLAN-UI-v4 K3): the Stacks page's submenus as ONE descriptor list per owner.

An owner is the controller row ("controller") or one stack (its id). Each descriptor names a level's
HTML id, its parent level, its label and the ONE partial (with that partial's own variables) the
Stacks page includes for it, so its own page renders the same markup. The tree page, the breadcrumb
and the page route all read these lists; a (owner, id) pair is valid only if the id is in that
owner's list. The conditions below mirror the templates' (tests compare both, level by level).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONTROLLER = "controller"


@dataclass(frozen=True)
class Submenu:
    id: str
    parent: str | None
    label: str
    template: str
    vars: dict = field(default_factory=dict, compare=False, hash=False)


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
        add("webserver-row", "controller-row", "Webserver (HTTPS / mTLS)", "_webserver.html", **ws)
        add("webserver-row--lhpc-webgui", "webserver-row", "LHPC WebGUI", "_ws_console.html", **ws)
        add("ws-stackwebs", "webserver-row", "Stacks WebGUIs", "_ws_stackwebs.html", **ws)
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
    return out


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
    for sw in g["stack_webs"]:
        wid, rs = f"stack-webserver-{sw['page_id']}", _radio_suffix(sw["page_id"])
        label = "Webserver (web UI proxy)" + ("" if sw["primary"] else f" — {sw['name']}") + rs
        add(wid, top, label, "_stackweb.html", stack_web=sw)
        add(f"{wid}--settings", wid, "Settings" + rs, "_stackweb_settings.html", stack_web=sw)
        add(f"{wid}--monitor", wid, "Monitor" + rs, "_stackweb_monitor.html", stack_web=sw)
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

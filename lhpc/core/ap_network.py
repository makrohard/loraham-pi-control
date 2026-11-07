"""NA: the access point's IPv4 subnet as ONE setting (`[network] ap_cidr`), its apply, and the AP start's C9 gate.

Plain functions over the service (tests/README rule 13: new behaviour in a plain module, not the service family).
The setting drives the AP profile's `ipv4.addresses` (<first host>/<prefix>), the firewall's AP subnet and the
"open https://<first host>:8443" hint.

C9 (INTERFACE-NAUX-v3): the one apply moves `[firewall] ap_cidr` with the setting when it equals the PREVIOUS
effective setting, and keeps a value that differs from it. An earlier AP start saved the profile's subnet there,
and a 0.13.x box has the default saved, so both follow. A subnet chosen with `lhpc firewall --ap-cidr` that
differs is kept; one that happens to equal the old setting moves with it, because nothing records who saved it.
The AP start's own firewall step saves the candidate only when nothing is saved. Its gate then requires the saved
firewall AP subnet == the candidate AND the rules live for it, before the profile is modified. A refusal changes
nothing.
"""
from __future__ import annotations

from . import ap_subnet
from . import config as cfgmod
from .service_base import ActionResult

AP_CIDR_CONFIRM = "change-ap-subnet"
REASON_DIFFERS = "ap-firewall-subnet-differs"


def effective(svc) -> str:
    """The effective subnet text: the setting, else the default."""
    return svc.config().network.ap_cidr


def fw_ap_live(svc, iface: str, cidr: str) -> bool:
    """(Moved from service_network, unchanged.) The AP rules for (iface, cidr) are in force: the root helper's fresh, verified receipt
    is for the candidate it accepted, and that candidate enables exactly this AP. Unrelated
    firewall changes (a saved intent not yet applied, a stale helper revision) do not count
    against the AP, and every unreadable or mismatched piece reads as not live."""
    import json as _json

    from . import firewall as _fw
    receipt = svc._fw_read_receipt()
    if not receipt or receipt.get("verdict") != "verified" or not svc._fw_receipt_fresh(receipt):
        return False
    try:
        accepted = _json.loads(svc._system.fs.read_text(_fw.CANDIDATE_DEST, 256 * 1024) or "")
        if _fw.intent_hash(accepted) != receipt.get("intent_hash"):
            return False
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return accepted.get("ap") == {"enabled": True, "interface": iface, "cidr": cidr}


def first_host(svc) -> str:
    return str(ap_subnet.first_host(ap_subnet.validate(effective(svc))))


def set_ap_cidr(svc, value: str, confirm: str) -> ActionResult:
    """The one apply both surfaces call (console and `lhpc network --ap-cidr`): the confirm phrase first, then
    the value; stores the setting and moves the firewall's AP subnet when it was following the setting."""
    if confirm != AP_CIDR_CONFIRM:
        return ActionResult(False, f"Not changed: type the confirm phrase '{AP_CIDR_CONFIRM}' to change the AP "
                                   "subnet", data={"reason": "confirm-phrase"})
    try:
        cfg = svc.config()
    except cfgmod.ConfigError as exc:
        return _recover(svc, value, exc)
    if not cfg.network.valid:
        return ActionResult(False, "Not changed: the local configuration cannot be read, so network.ap_cidr is "
                                   "unknown", data={"reason": "config-unreadable"})
    previous = cfg.network.ap_cidr
    try:
        wanted = ap_subnet.DEFAULT if value == "default" else str(ap_subnet.validate(value))
    except ap_subnet.ApSubnetError as exc:
        return ActionResult(False, f"Not changed: {exc}", data={"reason": "invalid-value"})
    if wanted == previous:
        return ActionResult(True, f"The AP subnet is already {wanted}: nothing changed")
    try:
        stored = cfgmod.set_network(svc._paths, ap_cidr=value)
    except (ap_subnet.ApSubnetError, cfgmod.ConfigError) as exc:
        return ActionResult(False, f"Not changed: {exc}", data={"reason": "invalid-value"})
    svc._invalidate_config()
    fw = svc.config().firewall
    details = [f"  [setting] network.ap_cidr = {stored}"]
    if fw.ap_cidr and fw.ap_cidr == previous:
        res = svc.firewall_configure(ap_enabled=fw.ap_enabled, ap_interface=fw.ap_interface, ap_cidr=stored)
        svc._invalidate_config()
        if not res.ok:
            return ActionResult(False, f"The AP subnet is {stored}, but the firewall's AP subnet could not follow: "
                                       f"{res.summary}", details=details + res.details,
                                data={"reason": "firewall-not-followed"})
        details.append(f"  [firewall] the AP subnet follows: {previous} -> {stored} (apply the firewall to make it "
                       "live)")
    elif fw.ap_cidr:
        details.append(f"  [firewall] the AP subnet {fw.ap_cidr} was chosen apart from the setting and is kept; the "
                       "AP start refuses until they agree")
    return ActionResult(True, f"AP subnet set to {stored}; switch to AP mode to apply it to the access point",
                        details=details, next_commands=svc._fw_apply_lines())


def _recover(svc, value: str, exc: Exception) -> ActionResult:
    """The configuration does not load (for one, a bad stored network.ap_cidr): only `default` is applied, by
    clearing the key through the local-table writer, which never loads the effective configuration. The previous
    subnet is unknown, so the firewall's AP subnet is left as it is."""
    if value != "default":
        return ActionResult(False, f"Not changed: the configuration does not load: {exc}",
                            next_commands=[f"lhpc network --ap-cidr default --confirm-phrase {AP_CIDR_CONFIRM}"],
                            data={"reason": "config-unreadable"})
    try:
        cfgmod.set_network(svc._paths, ap_cidr="default")
    except cfgmod.ConfigError as exc2:
        return ActionResult(False, f"Not changed: {exc2}", data={"reason": "config-unreadable"})
    svc._invalidate_config()
    try:
        svc.config()
    except cfgmod.ConfigError as exc2:
        return ActionResult(False, f"network.ap_cidr cleared, but the configuration still does not load: {exc2}",
                            data={"reason": "config-unreadable"})
    return ActionResult(True, f"AP subnet set to {ap_subnet.DEFAULT} (the stored value was cleared)",
                        details=["  [setting] network.ap_cidr = default",
                                 "  [firewall] its AP subnet is left as it is: check it with `lhpc firewall`"])


def _ap_interface(svc) -> str:
    """The AP profile's interface, read with the same query ap_firewall_gap uses (one nmcli shape)."""
    rc, out, _err = svc._nmcli(["-g", "connection.interface-name,ipv4.method,ipv4.addresses",
                                "connection", "show", svc.AP_PROFILE])
    return (out.splitlines() or [""])[0].strip() if rc == 0 else ""


def ap_start_gate(svc) -> ActionResult | None:
    """C9 for the AP start: the firewall step for the candidate, the gate, then the profile modify. None when
    the switch may go on (the profile now carries the candidate), else the refusal; a refusal changes nothing."""
    cfg = svc.config()
    if not cfg.network.valid:
        return ActionResult(False, "Cannot switch to AP: the local configuration cannot be read, so the AP subnet "
                                   "is unknown", data={"reason": "config-unreadable"})
    net = ap_subnet.validate(cfg.network.ap_cidr)
    cand = str(net)
    if svc._fw_integration_present():
        iface = _ap_interface(svc)
        if iface and not cfg.firewall.ap_cidr:
            svc.firewall_configure(ap_enabled=True, ap_interface=iface, ap_cidr=cand)
            svc._invalidate_config()
        saved = svc.config().firewall.ap_cidr
        if iface and saved and saved != cand:
            cmd = f"lhpc firewall --ap on --ap-interface {iface} --ap-cidr {cand}"
            return ActionResult(False, f"Cannot switch to AP: the firewall's AP subnet is {saved}, the AP subnet "
                                       f"setting is {cand}: align them first",
                                details=[f"  {cmd}", "  then make the firewall's AP rules live"],
                                next_commands=[cmd, *svc._fw_apply_lines()], data={"reason": REASON_DIFFERS})
        if not iface or saved != cand or not svc._fw_ap_live(iface, cand):
            gap = svc.ap_firewall_gap()          # the profile already on the candidate: today's CO-16 wording
            line = (f"{gap['line']} — the box stays on its current network" if gap and gap["cidr"] == cand
                    else f"the firewall's AP rules are not saved and live for {cand}")
            return ActionResult(False, f"Cannot switch to AP: {line}",
                                details=["Make the firewall's AP rules live, then switch:"]
                                + [f"  {c}" for c in svc._fw_apply_lines()],
                                next_commands=svc._fw_apply_lines(), data={"reason": "ap-rules-not-live"})
    rc, _out, err = svc._nmcli(["connection", "modify", svc.AP_PROFILE, "ipv4.addresses", ap_subnet.ap_address(net)])
    if rc != 0:
        return ActionResult(False, f"Cannot switch to AP: the AP profile's address could not be set (nmcli rc {rc}: "
                                   f"{(err or '').strip()[:200]})", data={"reason": "profile-modify-failed"})
    return None

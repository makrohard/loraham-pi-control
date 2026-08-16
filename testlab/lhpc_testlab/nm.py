"""Stateful nmcli simulator. Answers the exact terse formats service_network parses;
state lives in `state/testlab/nm.json` and is RESEEDED whenever the scenario record
changes (deterministic scenarios; operator joins/forgets persist within one scenario).
The real nmcli is never reached — the lab runner routes every `nmcli` argv here.
"""
from __future__ import annotations

import json
import uuid as _uuid
from pathlib import Path

from lhpc.core import runtime_fs
from lhpc.core.probes.backends import CommandResult

from . import scenarios

_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")   # uuid5 namespace (DNS)
AP_NAME = "lhpc-ap"
_SCAN_LIST = (("LabNet", 87, "WPA2"), ("Suche...", 64, "WPA2"), ("CoffeeShop", 41, "WPA1"))


def _uid(name: str) -> str:
    return str(_uuid.uuid5(_NS, f"lhpc-testlab-{name}"))


def _path(paths) -> Path:
    return paths.under("state", "testlab", "nm.json")


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _seed(flags: dict) -> dict:
    profiles = [{"uuid": _uid(AP_NAME), "name": AP_NAME, "type": "802-11-wireless",
                 "ssid": "lhpc-lab", "autoconnect": "yes", "priority": "0",
                 "secured": False, "has_secret": True}]
    active = _uid(AP_NAME)
    if flags.get("wifi") == "connected":
        profiles.append({"uuid": _uid("LabNet"), "name": "LabNet",
                         "type": "802-11-wireless", "ssid": "LabNet",
                         "autoconnect": "no", "priority": "0",
                         "secured": True, "has_secret": True})
        active = _uid("LabNet")
    return {"profiles": profiles, "active_uuid": active}


def _load(paths) -> dict:
    flags = scenarios.effective_state(paths)
    stamp = f"{flags.get('_name')}"
    try:
        st = json.loads(runtime_fs.read_text_regular(paths, _path(paths),
                                                     max_bytes=1 << 20) or "")
        if isinstance(st, dict) and st.get("seeded_for") == stamp \
                and isinstance(st.get("profiles"), list):
            return st
    except Exception:
        pass
    st = _seed(flags)
    st["seeded_for"] = stamp
    _save(paths, st)
    return st


def _save(paths, st: dict) -> None:
    runtime_fs.atomic_write(paths, _path(paths), json.dumps(st, indent=1), 0o600)


def _ok(out: str = "") -> CommandResult:
    return CommandResult(0, out, "")


def _err(rc: int, msg: str) -> CommandResult:
    return CommandResult(rc, "", msg)


def _by_uuid(st: dict, uid: str) -> dict | None:
    return next((p for p in st["profiles"] if p["uuid"] == uid), None)


def simulate(paths, argv: list) -> CommandResult:
    args = [str(a) for a in argv[1:]]
    joined = " ".join(args)
    flags = scenarios.effective_state(paths)
    st = _load(paths)

    if "general permissions" in joined:
        return _ok("org.freedesktop.NetworkManager.network-control:yes\n"
                   "org.freedesktop.NetworkManager.settings.modify.system:yes\n")

    if "connection show --active" in joined:
        act = _by_uuid(st, st.get("active_uuid", ""))
        if act is None:
            return _ok("")
        return _ok(f"{act['uuid']}:{_esc(act['name'])}:802-11-wireless:wlan0\n")

    if "connection show" in joined and "-f" in args and "AUTOCONNECT" in joined:
        rows = [f"{p['uuid']}:{_esc(p['name'])}:{p['type']}:{p['autoconnect']}:"
                f"{p['priority']}" for p in st["profiles"]]
        return _ok("".join(r + "\n" for r in rows))

    if "802-11-wireless.ssid connection show" in joined:
        target = args[-1]
        p = _by_uuid(st, target) or next((p for p in st["profiles"]
                                          if p["name"] == target), None)
        return _ok(f"{p['ssid']}\n") if p else _err(10, "Error: unknown connection.")

    if "IP4.ADDRESS device show" in joined:
        act = _by_uuid(st, st.get("active_uuid", ""))
        if act is None:
            return _ok("")
        addr = "10.42.0.1/24" if act["name"] == AP_NAME else "192.168.87.42/24"
        return _ok(addr + "\n")

    if "DHCP4.OPTION device show" in joined:
        act = _by_uuid(st, st.get("active_uuid", ""))
        if act is None or act["name"] == AP_NAME:
            return _ok("")
        return _ok("requested_domain_name = yes\ndomain_name = lab.lan\n")

    if "device wifi list" in joined:
        act = _by_uuid(st, st.get("active_uuid", ""))
        if act is not None and act["name"] == AP_NAME:
            # Hosting the AP blinds the radio — production truth: the scan sees only
            # the box's own SSID.
            return _ok(f"{_esc(act['ssid'])}:100:WPA2\n")
        rows = [f"{_esc(s)}:{sig}:{sec}" for s, sig, sec in _SCAN_LIST]
        return _ok("".join(r + "\n" for r in rows))

    if args[:2] == ["connection", "add"]:
        kv = dict(zip(args[2::2], args[3::2], strict=False))
        name = kv.get("con-name") or kv.get("ssid") or "wifi"
        ssid = kv.get("ssid") or name
        uid = _uid(f"joined-{name}")
        if _by_uuid(st, uid) is None:
            st["profiles"].append({
                "uuid": uid, "name": name, "type": "802-11-wireless", "ssid": ssid,
                "autoconnect": kv.get("connection.autoconnect", "no"),
                "priority": kv.get("connection.autoconnect-priority", "0"),
                "secured": "802-11-wireless-security.key-mgmt" in kv,
                "has_secret": False})
            _save(paths, st)
        return _ok(f"Connection '{name}' ({uid}) successfully added.\n")

    if args[:2] == ["connection", "up"]:
        uid = args[2] if len(args) > 2 else ""
        p = _by_uuid(st, uid)
        if p is None:
            return _err(10, "Error: unknown connection.")
        pwfile = ""
        if "passwd-file" in args:
            pwfile = args[args.index("passwd-file") + 1]
        if p["name"] != AP_NAME:
            if flags.get("join_result") == "wrong-password" and p.get("secured"):
                return _err(4, "Error: Connection activation failed: Secrets were "
                               "required, but not provided.")
            if p.get("secured") and not p.get("has_secret"):
                # NM reads the secret DURING activation — the file must exist here.
                if not (pwfile and Path(pwfile).exists()):
                    return _err(4, "Error: Connection activation failed: Secrets were "
                                   "required, but not provided.")
                p["has_secret"] = True          # NM persists the activation secret
            if flags.get("wifi") != "connected":
                return _err(4, "Error: Connection activation failed: No suitable "
                               "access point found.")
        st["active_uuid"] = uid
        _save(paths, st)
        scenarios.log_event(paths, f"nm: up {p['name']}")
        return _ok("Connection successfully activated (D-Bus active path: "
                   "/org/freedesktop/NetworkManager/ActiveConnection/7)\n")

    if args[:2] == ["connection", "down"]:
        uid = args[2] if len(args) > 2 else ""
        if st.get("active_uuid") == uid:
            st["active_uuid"] = _uid(AP_NAME)       # NM autoconnect: the AP returns
            _save(paths, st)
        return _ok("Connection successfully deactivated.\n")

    if args[:2] == ["connection", "delete"]:
        uid = args[2] if len(args) > 2 else ""
        p = _by_uuid(st, uid)
        if p is None:
            return _err(10, "Error: unknown connection.")
        st["profiles"] = [q for q in st["profiles"] if q["uuid"] != uid]
        if st.get("active_uuid") == uid:
            st["active_uuid"] = _uid(AP_NAME)
        _save(paths, st)
        return _ok(f"Connection '{p['name']}' ({uid}) successfully deleted.\n")

    if args[:2] == ["connection", "modify"]:
        uid = args[2] if len(args) > 2 else ""
        p = _by_uuid(st, uid)
        if p is None:
            return _err(10, "Error: unknown connection.")
        kv = dict(zip(args[3::2], args[4::2], strict=False))
        if "connection.autoconnect" in kv:
            p["autoconnect"] = kv["connection.autoconnect"]
        if "connection.autoconnect-priority" in kv:
            p["priority"] = kv["connection.autoconnect-priority"]
        _save(paths, st)
        return _ok("")

    return _err(2, f"testlab nm: unhandled nmcli invocation: {joined[:120]}")

"""Console Wi-Fi client mode with AP fallback — the Network panel.

The Lite/AP box joins an existing WLAN from the console (scan, pick, password) while the
managed AP profile (`lhpc-ap`) stays the automatic safety net: NM-native semantics give
"AP on reboot" (client profiles default to autoconnect=no) and "AP within seconds of link
loss" (autoconnect re-evaluation). A single PREFERRED network flips its profile to
autoconnect=yes/priority=10 — NM then picks it at boot when visible — and a retry
watchdog periodically re-attempts it while the box sits on the AP, because NM never
abandons an active connection for a better one and AP-mode scanning is unreliable on
brcmfmac.

Identity is the NM profile UUID everywhere (SSIDs/names are not unique); the SSID is
display-only. The PSK never appears in argv/logs/state/responses: profiles are created
WITHOUT the secret and activated via `nmcli connection up <uuid> passwd-file <file>`
(0600, unlinked AFTER nmcli returns — nmcli reads it during activation); with
psk-flags=0 NM persists the supplied system secret into its own root-owned keyfile.

Serialization: one `controller-network-op` reslock + a durable pending record carrying a
random `op_id` resume token — the detached finalize helper is the only caller allowed
past its own fresh record; the helper's runtime is hard-bounded strictly below the
record TTL, it removes the record in `finally`, and the TTL only recovers crashes.

Visibility is capability-gated (the `lhpc-ap` profile exists → AP-managed box; desktops
never see the feature) and authorization-gated via the cached-verdict pattern on
`nmcli general permissions` (file presence cannot work: polkit's rules.d is unreadable
to this process — the power-controls lesson).
"""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import time
from typing import ClassVar

from . import runtime_fs
from .paths import PathContainmentError
from .service_base import ActionResult


class NetworkOpsMixin:
    AP_PROFILE: ClassVar[str] = "lhpc-ap"
    NETWORK_OP_KEY: ClassVar[str] = "controller-network-op"
    # Pending record TTL recovers ABANDONED/CRASHED helpers only (the helper removes the
    # record in `finally` on every completion path); the helper's own budget stays
    # strictly below it so the record can never expire under a live helper.
    NET_PENDING_TTL_S: ClassVar[float] = 180.0
    NET_HELPER_BUDGET_S: ClassVar[float] = 120.0
    # Preferred-network retry cadence: each attempt costs a bounded AP outage while the
    # WLAN is away, so this is deliberately generous. The panel offers "Retry now".
    NET_RETRY_INTERVAL_S: ClassVar[float] = 600.0
    # Capability/authorization verdicts re-probe at most this often — both ways.
    _NET_PROBE_TTL_S: ClassVar[float] = 60.0
    _NET_VIEW_TTL_S: ClassVar[float] = 10.0
    # NetworkManager actions the Network panel needs (scan/list are already unprivileged):
    # join/save a client profile, plus re-activate the box's own shared AP (wifi.share.*) —
    # without the latter the "Back to AP mode" way home is denied and the box strands.
    _NET_ACTIONS: ClassVar[tuple] = ("org.freedesktop.NetworkManager.network-control",
                                     "org.freedesktop.NetworkManager.settings.modify.system",
                                     "org.freedesktop.NetworkManager.wifi.share.open",
                                     "org.freedesktop.NetworkManager.wifi.share.protected")

    # ---- nmcli plumbing --------------------------------------------------------------

    @staticmethod
    def _nm_split(line: str) -> list[str]:
        """Split one `nmcli -t` line on UNESCAPED colons and unescape the fields — NM
        escapes literal colons in values as `\\:` (AUDIT: a plain split(":") broke every
        profile name / SSID containing a colon)."""
        fields: list[str] = []
        cur: list[str] = []
        esc = False
        for ch in line:
            if esc:
                cur.append(ch)
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == ":":
                fields.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        fields.append("".join(cur))
        return fields

    def _nmcli(self, args: list, timeout: float = 15.0):
        r = self._system.runner.run(["nmcli", *args], timeout)
        return (getattr(r, "returncode", 1), (getattr(r, "stdout", "") or ""),
                (getattr(r, "stderr", "") or ""))

    def _nm_connections(self) -> list[dict]:
        """All NM profiles as {uuid, name, type, autoconnect, priority} — terse parse."""
        rc, out, _err = self._nmcli(["-t", "-f",
                                     "UUID,NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY",
                                     "connection", "show"])
        rows = []
        if rc != 0:
            return rows
        for line in out.splitlines():
            parts = self._nm_split(line)
            if len(parts) < 5:
                continue
            rows.append({"uuid": parts[0], "name": parts[1], "type": parts[2],
                         "autoconnect": parts[3] == "yes",
                         "priority": parts[4]})
        return rows

    def _nm_active(self) -> dict:
        """The active wifi connection as {uuid, name} plus the device address, or {}."""
        rc, out, _err = self._nmcli(["-t", "-f", "UUID,NAME,TYPE,DEVICE",
                                     "connection", "show", "--active"])
        act: dict = {}
        if rc == 0:
            for line in out.splitlines():
                parts = self._nm_split(line)
                if len(parts) >= 4 and parts[2].startswith("802-11-wireless"):
                    act = {"uuid": parts[0], "name": parts[1], "device": parts[3]}
                    break
        if act:
            rc, out, _err = self._nmcli(["-g", "IP4.ADDRESS", "device", "show",
                                         act.get("device", "wlan0")])
            act["address"] = (out.splitlines() or [""])[0].strip() if rc == 0 else ""
        return act

    # ---- gates -----------------------------------------------------------------------

    def network_supported(self) -> bool:
        """Capability gate: this is an AP-managed box (the `lhpc-ap` NM profile exists) and
        nmcli is present. Cached both ways — the profile files themselves live in a
        root-only directory, so the LIST (unprivileged) is the observable truth."""
        fs = self._system.fs
        if not any(fs.exists(p) for p in ("/usr/bin/nmcli", "/bin/nmcli")):
            return False
        now = time.monotonic()
        cached = getattr(self, "_net_cap_cache", None)
        if cached is not None and now - cached[1] < self._NET_PROBE_TTL_S:
            return cached[0]
        ok = any(c["name"] == self.AP_PROFILE and c["type"].startswith("802-11-wireless")
                 for c in self._nm_connections())
        self._net_cap_cache = (ok, now)
        return ok

    def _network_authorized(self) -> bool:
        """Cached logind-style verdict on `nmcli general permissions`: both required NM
        actions report `yes` for this process's user. Bounded TTL both ways."""
        now = time.monotonic()
        cached = getattr(self, "_net_auth_cache", None)
        if cached is not None and now - cached[1] < self._NET_PROBE_TTL_S:
            return cached[0]
        rc, out, _err = self._nmcli(["-t", "-f", "PERMISSION,VALUE", "general",
                                     "permissions"])
        got = {}
        if rc == 0:
            for line in out.splitlines():
                perm, _, val = line.partition(":")
                got[perm.strip()] = val.strip()
        ok = all(got.get(a) == "yes" for a in self._NET_ACTIONS)
        self._net_auth_cache = (ok, now)
        return ok

    # ---- pending record (op_id resume token; TTL recovers crashes only) --------------

    def _net_pending_path(self):
        return self._paths.under("state", "network-pending.json")

    def _net_outcome_path(self):
        return self._paths.under("state", "network-outcome.json")

    def _net_retry_path(self):
        return self._paths.under("state", "network-retry.json")

    def _net_pending_blocked(self, token: str = "") -> tuple[str, str] | None:
        """(reason, tag) while a network operation is pending THIS boot and younger than
        the TTL — unless `token` matches the record's op_id (the detached helper resuming
        its OWN operation). Mirrors the power-pending validation: malformed refuses and is
        kept; valid stale/other-boot records are pruned; an unreadable boot id refuses."""
        import math as _math
        p = self._net_pending_path()
        try:
            if not p.exists():
                return None
        except OSError:
            return ("the network-pending marker could not be checked — refusing",
                    "network-pending")
        try:
            rec = json.loads(runtime_fs.read_text_regular(self._paths, p, max_bytes=4096)
                             or "")
            bid = rec["boot_id"]
            up0 = rec["requested_uptime"]
            op_id = rec["op_id"]
            if (not isinstance(bid, str) or not bid
                    or not isinstance(op_id, str) or not op_id
                    or isinstance(up0, bool) or not isinstance(up0, (int, float))
                    or not _math.isfinite(up0) or up0 < 0):
                raise ValueError("malformed network-pending marker")
            up0 = float(up0)
        except Exception:
            return (f"a network-pending marker is unreadable ({p}) — refusing network "
                    "actions; inspect it and delete it if it is stale", "network-pending")
        from .lifecycle import current_boot_id
        cur = current_boot_id()
        if not cur:
            return ("a network operation may be pending and this boot's identity cannot "
                    "be read — refusing", "network-pending")
        if bid != cur:
            self._safe_unlink(p)
            return None
        try:
            now_up = float((self._system.fs.read_text("/proc/uptime", 128) or "")
                           .split()[0])
        except (OSError, ValueError, IndexError):
            return ("a network operation is pending and its age cannot be determined — "
                    "refusing", "network-pending")
        if now_up - up0 >= self.NET_PENDING_TTL_S:
            self._safe_unlink(p)              # abandoned/crashed helper — TTL recovery
            return None
        if token and token == op_id:
            return None                       # the operation's OWN helper resumes
        return ("a network operation is in progress (connect/finalize) — retry in a "
                "moment", "network-pending")

    def _net_op_lock(self, op: str, target: str = "", token: str = ""):
        """ExitStack holding the network-op reslock, with the pending gate checked UNDER
        the lock. Returns (stack, err): err is a typed refusal string or None."""
        import contextlib

        from . import reslock
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(reslock.operation_lock(self._paths, self.NETWORK_OP_KEY,
                                                       op, target))
        except reslock.ResourceBusy as busy:
            stack.close()
            return None, f"another network operation is running ({busy})"
        blocked = self._net_pending_blocked(token=token)
        if blocked is not None:
            stack.close()
            return None, blocked[0]
        return stack, None

    # ---- views -----------------------------------------------------------------------

    def network_view(self) -> dict:
        """Cached panel/dashboard state (GET-safe beyond the bounded cache refresh)."""
        now = time.monotonic()
        cached = getattr(self, "_net_view_cache", None)
        if cached is not None and now - cached[1] < self._NET_VIEW_TTL_S:
            return cached[0]
        view: dict = {"supported": self.network_supported()}
        if not view["supported"]:
            self._net_view_cache = (view, now)
            return view
        view["authorized"] = self._network_authorized()
        if not view["authorized"]:
            import getpass

            from . import deps as deps_mod
            view["install"] = deps_mod.network_rule_install_cmd(getpass.getuser())
        act = self._nm_active()
        view["mode"] = ("ap" if act.get("name") == self.AP_PROFILE
                        else ("client" if act else "off"))
        view["active"] = act
        stored = [c for c in self._nm_connections()
                  if c["type"].startswith("802-11-wireless")
                  and c["name"] != self.AP_PROFILE]
        pref = self._net_preferred()
        for c in stored:
            c["preferred"] = c["uuid"] == pref.get("uuid")
        view["stored"] = stored
        view["preferred"] = pref
        view["hostname"] = socket.gethostname()
        try:
            view["outcome"] = json.loads(runtime_fs.read_text_regular(
                self._paths, self._net_outcome_path(), max_bytes=8192) or "")
        except Exception:
            view["outcome"] = {}
        view["scan"] = list(getattr(self, "_net_scan_cache", ((), 0.0))[0]) \
            if now - getattr(self, "_net_scan_cache", ((), 0.0))[1] < 120.0 else []
        view["pending"] = self._net_pending_blocked() is not None
        self._net_view_cache = (view, now)
        return view

    def _net_view_invalidate(self) -> None:
        self._net_view_cache = None

    def network_scan(self) -> ActionResult:
        """POST-triggered rescan. Results land in a short-lived cache the panel renders."""
        if not (self.network_supported() and self._network_authorized()):
            return ActionResult(False, "network controls are not available on this box")
        rc, out, err = self._nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi",
                                    "list", "--rescan", "yes"], timeout=30.0)
        if rc != 0:
            return ActionResult(False, f"Wi-Fi scan failed: {err.strip()[:200]}")
        best: dict = {}
        # The box's own broadcast SSID comes from the AP PROFILE (hostname is only the
        # image convention — not a reliable filter): ask NM, fall back to the hostname.
        _rc2, own_out, _e2 = self._nmcli(["-g", "802-11-wireless.ssid", "connection",
                                          "show", self.AP_PROFILE])
        own = (own_out.strip().splitlines() or [""])[0] if _rc2 == 0 else ""
        own = own or socket.gethostname()
        for line in out.splitlines():
            parts = self._nm_split(line)
            if len(parts) < 3:
                continue
            ssid, sig, sec = parts[0], parts[1], parts[2]
            if not ssid or ssid == own:
                continue
            try:
                sigv = int(sig)
            except ValueError:
                sigv = 0
            if ssid not in best or sigv > best[ssid]["signal"]:
                best[ssid] = {"ssid": ssid, "signal": sigv, "security": sec or "open"}
        rows = sorted(best.values(), key=lambda r: -r["signal"])
        self._net_scan_cache = (rows, time.monotonic())
        self._net_view_invalidate()
        if not rows and (self._nm_active() or {}).get("name") == self.AP_PROFILE:
            # LIVE-FOUND: while wlan0 HOSTS the AP the radio cannot survey other channels —
            # the scan sees only itself (fresh and cached alike). Manual entry is the
            # commissioning path; say so instead of a bare "found 0".
            return ActionResult(True, "found 0 networks — scanning is limited while this "
                                      "box runs its own AP; enter the network name below")
        return ActionResult(True, f"found {len(rows)} network(s)")

    # ---- preferred-network setting ---------------------------------------------------

    def _net_preferred_path(self):
        return self._paths.under("state", "network-preferred.json")

    def _net_preferred(self) -> dict:
        try:
            rec = json.loads(runtime_fs.read_text_regular(
                self._paths, self._net_preferred_path(), max_bytes=1024) or "")
            return rec if isinstance(rec, dict) and rec.get("uuid") else {}
        except Exception:
            return {}

    # ---- connect ---------------------------------------------------------------------

    def network_connect(self, *, ssid: str = "", uuid: str = "", psk: str = "",
                        allow_console: bool = True, apply: bool = False) -> ActionResult:
        """Join a WLAN: new (ssid [+psk]) or a stored profile (uuid). Respond-first: the
        operator's session dies with the AP, so the detached finalize helper performs the
        activation and writes the outcome the panel shows afterwards."""
        host = socket.gethostname()
        label = ssid or uuid
        if not apply:
            details = [
                f"  [warn] this box's AP goes DOWN the moment it joins '{label}' — your "
                "session ends",
                f"  [your device] your phone/PC STAYS on the dead AP until YOU switch its "
                f"Wi-Fi back to '{label}' (or your normal network) — do that first, THEN "
                "look for the box",
                f"  [find] PC/iPhone: https://{host}.local:8443. Android phones: the box "
                "asks your router for its local DNS name during the join (works on most "
                "home routers, whatever their domain) — the exact phone address appears "
                "in the join outcome on this panel, with the plain IP as the fallback",
                ("  [console] allowed from the joined network automatically — the nginx "
                 "allowlist and your client certificate stay the gate"
                 if allow_console else
                 "  [console] NOT allowed from the joined network (checkbox off) — SSH "
                 "port 22 stays reachable; the AP returns if the join fails"),
                "  [self-heal] wrong password / network gone -> the box is back as its "
                "own AP within about a minute",
            ]
            return ActionResult(True, f"Join Wi-Fi network '{label}'?", details=details)
        if not self.network_supported():
            return ActionResult(False, "network controls are not available on this box")
        if not self._network_authorized():
            import getpass

            from . import deps as deps_mod
            fix = deps_mod.network_rule_install_cmd(getpass.getuser())
            return ActionResult(False, "this box does not authorize the operator for "
                                       "NetworkManager control.",
                                details=["  install the authorization, then retry:",
                                         *(f"    {ln}" for ln in fix.splitlines())])
        # AUDIT: an EXISTING install may still run the OLD AP-scoped nft ruleset — joining
        # would strand the console on the new network (SSH-only), exactly the lockout the
        # de-scoping removed. Refuse BEFORE dropping the AP until the one-time firewall
        # migration has been applied; the gate names the exact command. Checkbox-off joins
        # skip this (the operator explicitly accepted an SSH-only box).
        if allow_console:
            try:
                allowed, gate_msg, gate_cmds = self.firewall_gate_activation(
                    self._prospective_nginx_ports())
            except Exception as exc:
                # RE-AUDIT: an unverifiable firewall fails CLOSED — dropping the AP on an
                # unknown ruleset is exactly the lockout this gate exists to prevent.
                allowed, gate_msg, gate_cmds = (False,
                                                f"firewall state unverifiable ({exc})", [])
            if not allowed:
                return ActionResult(False,
                                    "Cannot join yet: this box's firewall still runs "
                                    "rules that would block the console on the new "
                                    "network. Apply the migrated firewall ONCE (over SSH "
                                    "or from this AP session), then join.",
                                    details=[f"  {gate_msg}"],
                                    next_commands=gate_cmds)
        from .lifecycle import current_boot_id
        boot_id = current_boot_id()
        if not boot_id:
            return ActionResult(False, "Cannot join: the boot id is unavailable — the "
                                       "pending guard cannot be scoped to this boot")
        stack, err = self._net_op_lock("network-connect", label)
        if err:
            return ActionResult(False, f"Cannot join '{label}': {err}")
        with stack:
            if not uuid:
                if not ssid:
                    return ActionResult(False, "Cannot join: no network given")
                args = ["connection", "add", "type", "wifi", "ifname", "wlan0",
                        "con-name", ssid, "ssid", ssid,
                        "connection.autoconnect", "no",
                        "connection.autoconnect-priority", "0"]
                if psk:
                    args += ["802-11-wireless-security.key-mgmt", "wpa-psk"]
                rc, out, cerr = self._nmcli(args)
                m = re.search(r"\(([0-9a-f-]{36})\)", out or "")
                if rc != 0 or not m:
                    return ActionResult(False, f"Cannot create the Wi-Fi profile: "
                                               f"{(cerr or out).strip()[:200]}")
                uuid = m.group(1)
            else:
                if not any(c["uuid"] == uuid for c in self._nm_connections()):
                    return ActionResult(False, "Cannot join: no stored network with that "
                                               "id — rescan and pick again")
            op_id = secrets.token_hex(16)
            pwfile = None
            if psk:
                # passwd-file format: `setting.property:secret`. Written 0600; the HELPER
                # unlinks it AFTER nmcli returns (nmcli reads it during activation).
                pwfile = self._paths.under("state", f"network-psk-{op_id}")
                try:
                    runtime_fs.atomic_write(self._paths, pwfile,
                                            f"802-11-wireless-security.psk:{psk}\n",
                                            0o600)
                except (OSError, PathContainmentError) as exc:
                    return ActionResult(False, f"Cannot join: secret handoff failed "
                                               f"({exc})")
            try:
                up0 = float((self._system.fs.read_text("/proc/uptime", 128) or "")
                            .split()[0])
            except (OSError, ValueError, IndexError):
                if pwfile is not None:
                    self._safe_unlink(pwfile)
                return ActionResult(False, "Cannot join: /proc/uptime is unreadable — "
                                           "the pending guard cannot be timed")
            marker = self._net_pending_path()
            try:
                runtime_fs.atomic_write(self._paths, marker, json.dumps(
                    {"op": "connect", "uuid": uuid, "ssid": ssid, "boot_id": boot_id,
                     "requested_uptime": up0, "op_id": op_id,
                     "allow_console": bool(allow_console),
                     "pwfile": str(pwfile) if pwfile is not None else ""}), 0o600)
            except (OSError, PathContainmentError) as exc:
                if pwfile is not None:
                    self._safe_unlink(pwfile)
                return ActionResult(False, f"Cannot join: could not record the pending "
                                           f"operation ({exc})")
            import sys
            argv = [sys.executable, "-m", "lhpc", "_network-finalize",
                    "--uuid", uuid, "--op-id", op_id, "--delay", "1.5"]
            # RE-AUDIT: pwfile and allow_console travel ONLY in the pending record (the
            # helper ignores argv for both) — no secrets-adjacent path in a command line.
            log_path = self._paths.under("logs", "network-connect.log")
            try:
                pid = self._lifecycle()._spawn(argv, log_path)
            except (OSError, PathContainmentError) as exc:
                pid = None
                spawn_err = str(exc)
            else:
                spawn_err = ""
            if pid is None:
                self._safe_unlink(marker)
                if pwfile is not None:
                    self._safe_unlink(pwfile)
                return ActionResult(False, f"Cannot join: the connect helper could not "
                                           f"be spawned ({spawn_err or 'no pid'}) — "
                                           "nothing was changed")
            self._net_view_invalidate()
            return ActionResult(True,
                                f"Joining '{label}' — this box's AP goes down NOW; find "
                                f"the console at https://{host}.local:8443 on that "
                                "network (outcome appears on the Network panel).",
                                details=[f"  [log] {log_path}"])

    # ---- the detached finalize helper (CLI plumbing `lhpc _network-finalize`) --------

    def network_finalize(self, *, uuid: str, op_id: str, pwfile: str = "",
                         allow_console: bool = False, delay: float = 1.5) -> int:
        """Runs DETACHED after the connect response: activation + lease + console CIDR.
        Hard-bounded strictly below NET_PENDING_TTL_S; removes the pending record in
        `finally` (the TTL recovers crashes only). Exit code is for the log."""
        time.sleep(max(0.0, delay))
        deadline = time.monotonic() + self.NET_HELPER_BUDGET_S
        marker = self._net_pending_path()
        # AUDIT (fail-open): the gate must REQUIRE a record that this helper OWNS — op_id
        # AND uuid must both match (absence is a refusal, never a pass) — and RE-AUDIT: it
        # must be FRESH and CURRENT-BOOT, checked HERE (never via _net_pending_blocked,
        # which PRUNES stale records and passes — a revived helper past the TTL must not
        # act). Every option, including the secrets-file path, comes from the record —
        # never from argv. A helper that does not own a fresh record touches NOTHING.
        try:
            rec = json.loads(runtime_fs.read_text_regular(self._paths, marker,
                                                          max_bytes=4096) or "")
        except Exception:
            rec = None
        if (not isinstance(rec, dict) or rec.get("op_id") != op_id
                or rec.get("uuid") != uuid):
            return 1
        from .lifecycle import current_boot_id
        cur_boot = current_boot_id()
        try:
            now_up = float((self._system.fs.read_text("/proc/uptime", 128) or "")
                           .split()[0])
        except (OSError, ValueError, IndexError):
            return 1
        up0 = rec.get("requested_uptime")
        if (not cur_boot or rec.get("boot_id") != cur_boot
                or isinstance(up0, bool) or not isinstance(up0, (int, float))
                or not (0.0 <= now_up - float(up0) < self.NET_PENDING_TTL_S)):
            return 1                          # stale / other-boot / malformed: never act
        # RE-AUDIT: the record is the SOLE authority (argv is ignored), so its shape is
        # validated strictly — no coercion (bool("false") is True), no record-supplied
        # secrets path (it rides nmcli and gets unlinked; only the canonical per-op path
        # this service itself writes is acceptable), and only the two known ops.
        op = rec.get("op")
        allow_console = rec.get("allow_console")
        pwfile = rec.get("pwfile", "")
        canonical_pw = str(self._paths.under("state", f"network-psk-{op_id}"))
        if (op not in ("connect", "ap") or not isinstance(allow_console, bool)
                or not isinstance(pwfile, str) or pwfile not in ("", canonical_pw)
                or (op == "ap" and (allow_console or pwfile))):
            return 1                                    # malformed record: never act
        # Hold the network-op lock for the WHOLE finalize (others are already refused by
        # the fresh record; the lock additionally excludes a duplicate helper).
        import contextlib as _ctx

        from . import reslock as _reslock
        _lockstack = _ctx.ExitStack()
        try:
            _lockstack.enter_context(_reslock.operation_lock(
                self._paths, self.NETWORK_OP_KEY, "network-finalize", uuid))
        except _reslock.ResourceBusy:
            _lockstack.close()
            return 1
        outcome: dict = {"ok": False, "uuid": uuid, "console": "off"}
        try:
            # Activation. The passwd-file is read BY nmcli during `up`, so it is
            # unlinked AFTER the call returns — success and failure alike.
            args = ["connection", "up", uuid, "ifname", "wlan0"]
            if pwfile:
                args += ["passwd-file", pwfile]
            try:
                rc, _out, err = self._nmcli(args, timeout=45.0)
            finally:
                if pwfile:
                    from pathlib import Path as _P
                    self._safe_unlink(_P(pwfile))
            if rc != 0:
                outcome["error"] = (f"activation failed: {err.strip()[:300]} — the box "
                                    "returns as its own AP")
                return 1
            # Lease: poll until the active connection is OUR uuid with an address.
            addr = ""
            while time.monotonic() < deadline:
                act = self._nm_active()
                if act.get("uuid") == uuid and act.get("address"):
                    addr = act["address"]
                    break
                time.sleep(2.0)
            if not addr:
                outcome["error"] = "no address lease within the budget"
                return 1
            outcome["ok"] = True
            outcome["address"] = addr
            outcome["ssid"] = next((c["name"] for c in self._nm_connections()
                                    if c["uuid"] == uuid), "")
            cidr = str(ipaddress.ip_interface(addr).network)
            outcome["cidr"] = cidr
            # Router-DNS name: most home routers register the DHCP hostname in their local
            # DNS (fritz.box, .lan, …) — the ONE name phones can use (Android browsers do
            # not resolve .local, and raw IPs are undiscoverable without router access).
            host = socket.gethostname()
            _rcD, dom_out, _eD = self._nmcli(["-g", "DHCP4.OPTION", "device", "show",
                                              "wlan0"])
            domain = ""
            if _rcD == 0:
                for ln in dom_out.splitlines():
                    k, _, v = ln.partition(" = ")
                    if k.strip().endswith("domain_name") and "search" not in k:
                        domain = v.strip()
                        break
            if domain:
                outcome["fqdn"] = f"{host}.{domain}"
            # The join often brings the box its FIRST NTP sync — heal a CRL the clock jump
            # just expired before the operator's cert is refused (the watchdog re-checks).
            try:
                self.crl_refresh_if_expired()
            except Exception:
                pass
            if allow_console:
                _ip = addr.split("/")[0]
                _dns = [host, f"{host}.local"]
                if outcome.get("fqdn"):
                    _dns.append(outcome["fqdn"])
                state, pending_cmd, msg = self._network_extend_console(cidr, ip=_ip,
                                                                       extra_dns=_dns)
                outcome["console"] = state       # applied | pending | refused | error
                outcome["console_detail"] = msg
                if state == "pending" and pending_cmd:
                    outcome["firewall_cmd"] = pending_cmd
            return 0
        except Exception as exc:  # a detached helper must never die silently
            outcome["error"] = f"finalize failed: {exc}"
            return 1
        finally:
            try:
                up = float((self._system.fs.read_text("/proc/uptime", 128) or "0 0")
                           .split()[0])
            except (OSError, ValueError, IndexError):
                up = 0.0
            outcome["finished_uptime"] = up
            try:
                runtime_fs.atomic_write(self._paths, self._net_outcome_path(),
                                        json.dumps(outcome), 0o600)
            except (OSError, PathContainmentError):
                pass
            self._safe_unlink(marker)         # completion cleanup — TTL is for crashes
            _lockstack.close()

    def _network_extend_console(self, cidr: str, ip: str = "",
                                extra_dns=()) -> tuple[bool, str, str]:
        """(applied, pending_sudo_cmd, message). Phase 1 under the RAW config lock: fresh
        load -> union (CIDRs AND the joined address/names as certificate SANs — LIVE-FOUND:
        a phone reaching the box by LAN IP or router name got a cert with no matching SAN
        and refused the chain) -> exposure gate -> save via the lock-aware
        `hold_lock=False` save (neither `_config_stable` nor a re-acquiring save — they
        would self-contend). Between phases: reissue the server certificate for the new
        SANs (fail-soft). Phase 2 OUTSIDE any lock: `webserver_apply()`, which serves the
        fresh cert and fails closed at the firewall gate only until the one-time migration
        apply has run."""
        from . import config as _config
        from . import webserver as _ws
        from .config import WebserverConfig
        try:
            with _config.config_lock(self._paths):
                cfg = _config.load_config(self._paths).webserver
                union = list(dict.fromkeys([*cfg.allowed_cidrs, cidr]))
                ip_sans = list(dict.fromkeys([*cfg.ip_sans, *( [ip] if ip else [] )]))
                dns_sans = list(dict.fromkeys([*cfg.dns_sans,
                                               *(d for d in extra_dns if d)]))
                probe_kwargs = {"bind": "0.0.0.0", "port": cfg.port,
                                "scheme": cfg.scheme, "access_mode": cfg.access_mode,
                                "remote_exposed": True,
                                "allowed_cidrs": tuple(union)}
                plan = _ws.plan_exposure(WebserverConfig(**probe_kwargs))
                missing = self._exposure_missing(plan, confirm=True,
                                                 confirm_public=False,
                                                 cidr_flag="(console checkbox)")
                if missing:
                    # AUDIT: a POLICY refusal is "refused", never "pending" — nothing was
                    # saved, so there is nothing a later retry could legitimately apply.
                    return ("refused", "", "console not extended — unmet requirement(s): "
                            + "; ".join(missing))
                _config.save_webserver_config(self._paths, bind="0.0.0.0",
                                              remote_exposed=True,
                                              allowed_cidrs=union, ip_sans=ip_sans,
                                              dns_sans=dns_sans, hold_lock=False)
                # The STACK PROXIES (8444..8446) carry their OWN per-stack allowlists —
                # LIVE-FOUND: extending only the console left every proxy answering 403
                # from the joined network. Union the joined CIDR into each enabled remote
                # proxy under the same held lock.
                full = _config.load_config(self._paths)
                for sid, swc in (full.stackweb or {}).items():
                    if swc.enabled and swc.remote and cidr not in swc.allowed_cidrs:
                        _config.save_stackweb_config(
                            self._paths, sid,
                            allowed_cidrs=list(dict.fromkeys([*swc.allowed_cidrs, cidr])),
                            hold_lock=False)
        except Exception as exc:
            return ("error", "", f"console extension failed: {exc}")
        self._invalidate_config()
        # Reissue the server cert for the new SANs BEFORE the apply serves it. Fail-soft
        # (same contract as _expose_add_san_and_reissue): a missing CA must not undo the
        # console extension — the operator just keeps the by-name cert warning.
        try:
            from . import pki as _pki
            fresh = self.config().webserver
            _pki.issue_server_cert(self._paths, dns_sans=list(fresh.dns_sans),
                                   ip_sans=list(fresh.ip_sans),
                                   days=fresh.server_cert_days)
        except Exception:
            pass
        res = self.webserver_apply()
        if res.ok:
            return ("applied", "", f"console allowed from {cidr}")
        # Config IS saved here — "pending" means exactly "saved, apply gate-blocked": the
        # watchdog may legitimately retry the apply. No fabricated fallback command: only
        # the gate's real rendered command, or nothing.
        cmd = next((c for c in (res.next_commands or []) if "firewall-apply" in c), "")
        return (("pending", cmd, res.summary) if cmd
                else ("error", "", res.summary))

    def network_ap_now(self, apply: bool = False) -> ActionResult:
        """Switch back to the box's own AP (operator ruling: client mode needs a way home).
        Clears the preferred flag first — otherwise the watchdog would re-join the WLAN
        within the retry interval and the button would look broken. Respond-first via the
        SAME finalize helper (no secrets, no console step): activation, outcome, pending-
        record cleanup all ride the proven path."""
        ap = next((c for c in self._nm_connections()
                   if c["name"] == self.AP_PROFILE
                   and c["type"].startswith("802-11-wireless")), None)
        if not apply:
            details = [
                "  [warn] this box leaves the Wi-Fi NOW — your session on this network "
                "ends",
                f"  [your device] reconnect your phone/PC to the box's own Wi-Fi "
                f"('{socket.gethostname()}') and open https://10.42.0.1:8443",
            ]
            if self._net_preferred().get("uuid"):
                details.append("  [note] the preferred-network flag is CLEARED — the box "
                               "stays on its AP until you join a network again")
            return ActionResult(True, "Switch back to AP mode?", details=details)
        if not (self.network_supported() and self._network_authorized()):
            return ActionResult(False, "network controls are not available on this box")
        if ap is None:
            return ActionResult(False, "no AP profile found on this box")
        from .lifecycle import current_boot_id
        boot_id = current_boot_id()
        if not boot_id:
            return ActionResult(False, "Cannot switch: the boot id is unavailable")
        stack, err = self._net_op_lock("network-ap", self.AP_PROFILE)
        if err:
            return ActionResult(False, f"Cannot switch to AP: {err}")
        with stack:
            # Clear the preference FIRST (intent file + NM flags) so the watchdog never
            # yanks the box back onto the WLAN after this deliberate switch.
            self._safe_unlink(self._net_preferred_path())
            self._net_apply_preference(self._nm_connections(), "")
            try:
                up0 = float((self._system.fs.read_text("/proc/uptime", 128) or "")
                            .split()[0])
            except (OSError, ValueError, IndexError):
                return ActionResult(False, "Cannot switch: /proc/uptime is unreadable")
            op_id = secrets.token_hex(16)
            try:
                runtime_fs.atomic_write(self._paths, self._net_pending_path(), json.dumps(
                    {"op": "ap", "uuid": ap["uuid"], "ssid": self.AP_PROFILE,
                     "boot_id": boot_id, "requested_uptime": up0, "op_id": op_id,
                     "allow_console": False}), 0o600)
            except (OSError, PathContainmentError) as exc:
                return ActionResult(False, f"Cannot switch: could not record the pending "
                                           f"operation ({exc})")
            import sys
            argv = [sys.executable, "-m", "lhpc", "_network-finalize",
                    "--uuid", ap["uuid"], "--op-id", op_id, "--delay", "1.5"]
            log_path = self._paths.under("logs", "network-connect.log")
            try:
                pid = self._lifecycle()._spawn(argv, log_path)
            except (OSError, PathContainmentError):
                pid = None
            if pid is None:
                self._safe_unlink(self._net_pending_path())
                return ActionResult(False, "Cannot switch: the helper could not be "
                                           "spawned — nothing was changed")
            self._net_view_invalidate()
            return ActionResult(True, "Switching to AP mode NOW — reconnect your device "
                                      f"to '{socket.gethostname()}' and open "
                                      "https://10.42.0.1:8443")

    # ---- prefer / forget / retry -----------------------------------------------------

    def network_prefer(self, uuid: str, on: bool) -> ActionResult:
        if not (self.network_supported() and self._network_authorized()):
            return ActionResult(False, "network controls are not available on this box")
        conns = self._nm_connections()
        target = next((c for c in conns if c["uuid"] == uuid
                       and c["type"].startswith("802-11-wireless")
                       and c["name"] != self.AP_PROFILE), None)
        if target is None:
            return ActionResult(False, "no stored Wi-Fi network with that id")
        stack, err = self._net_op_lock("network-prefer", target["name"])
        if err:
            return ActionResult(False, f"Cannot change preference: {err}")
        with stack:
            # Intent FIRST (the watchdog reconciles NM to it after partial failures).
            try:
                if on:
                    runtime_fs.atomic_write(self._paths, self._net_preferred_path(),
                                            json.dumps({"uuid": uuid,
                                                        "ssid": target["name"]}), 0o600)
                else:
                    self._safe_unlink(self._net_preferred_path())
            except (OSError, PathContainmentError) as exc:
                return ActionResult(False, f"could not persist the preference ({exc})")
            problems = self._net_apply_preference(conns, uuid if on else "")
            self._net_view_invalidate()
            if problems:
                return ActionResult(False, "preference saved but NM profiles only partly "
                                           "updated — the watchdog will reconcile: "
                                           + "; ".join(problems))
            verb = "preferred" if on else "no longer preferred"
            return ActionResult(True, f"'{target['name']}' is {verb}. "
                                + ("The box will return to it whenever it is reachable "
                                   "(AP only while it is not)." if on else
                                   "The AP stays up after reboots and link loss."))

    def _net_apply_preference(self, conns: list, preferred_uuid: str) -> list:
        """Drive NM to the one-preferred invariant; returns human-readable problems."""
        problems = []
        for c in conns:
            if not c["type"].startswith("802-11-wireless"):
                continue
            if c["name"] == self.AP_PROFILE:
                # The AP is the way home: keep it armed (autoconnect=yes, priority 0) so a
                # disarmed profile — however it got that way — is repaired on every pass
                # instead of leaving the box unreachable after its next power cycle.
                if c["autoconnect"] and c.get("priority") == "0":
                    continue
                rc, _out, err = self._nmcli(
                    ["connection", "modify", c["uuid"],
                     "connection.autoconnect", "yes", "connection.autoconnect-priority", "0"])
                if rc != 0:
                    problems.append(f"{c['name']}: {err.strip()[:120]}")
                continue
            want_on = bool(preferred_uuid) and c["uuid"] == preferred_uuid
            in_shape = (c["autoconnect"] and c.get("priority") == "10") if want_on \
                else not c["autoconnect"]
            if in_shape:
                continue
            rc, _out, err = self._nmcli(
                ["connection", "modify", c["uuid"],
                 "connection.autoconnect", "yes" if want_on else "no",
                 "connection.autoconnect-priority", "10" if want_on else "0"])
            if rc != 0:
                problems.append(f"{c['name']}: {err.strip()[:120]}")
        return problems

    def network_forget(self, uuid: str) -> ActionResult:
        if not (self.network_supported() and self._network_authorized()):
            return ActionResult(False, "network controls are not available on this box")
        conns = self._nm_connections()
        target = next((c for c in conns if c["uuid"] == uuid), None)
        if target is None or target["name"] == self.AP_PROFILE \
                or not target["type"].startswith("802-11-wireless"):
            return ActionResult(False, "no forgettable Wi-Fi network with that id "
                                       "(the AP profile is never removable)")
        stack, err = self._net_op_lock("network-forget", target["name"])
        if err:
            return ActionResult(False, f"Cannot forget: {err}")
        with stack:
            rc, _out, cerr = self._nmcli(["connection", "delete", uuid])
            if rc != 0:
                return ActionResult(False, f"forget failed: {cerr.strip()[:200]}")
            if self._net_preferred().get("uuid") == uuid:
                self._safe_unlink(self._net_preferred_path())
            self._net_view_invalidate()
            return ActionResult(True, f"forgot '{target['name']}'")

    def _ap_idle(self, device: str) -> bool:
        """True ONLY when the AP provably has no associated station: `iw dev <dev> station
        dump` succeeded and printed nothing. Any station, a missing `iw`, or any failure is
        False — the automatic retry then stays deferred rather than tearing the AP down under
        a connected client (fail-safe by design)."""
        import shutil
        # PATH first, then the sbin locations: a shell-started `lhpc web` can have a PATH
        # without /usr/sbin (live-found: the ssh user's PATH lacks it, the user unit's has it).
        exe = shutil.which("iw") or "/usr/sbin/iw"
        try:
            r = self._system.runner.run([exe, "dev", device, "station", "dump"], 10.0)
        except Exception:
            return False
        return (getattr(r, "returncode", 1) == 0
                and not (getattr(r, "stdout", "") or "").strip())

    def network_retry_now(self) -> ActionResult:
        ok, msg = self._network_watch_tick(force=True)
        return ActionResult(ok, msg or "nothing to retry")

    # ---- watchdog tick (ONE worker thread calls this; started from run_server) -------

    def _network_watch_tick(self, force: bool = False) -> tuple[bool, str]:
        """One pass: prime caches, reconcile the one-preferred invariant, complete a
        pending console apply once the firewall is ok, and re-attempt the preferred
        network while the box sits on the AP. Every mutation under the network-op lock;
        a busy lock or fresh pending record just skips the tick (never an error)."""
        try:
            if not self.network_supported():
                return (False, "not an AP-managed box")
            view = None
            try:
                self._net_view_invalidate()
                view = self.network_view()          # primes capability + state caches
            except Exception:
                pass
            # Clock-jump heal: NTP arriving with a joined WLAN can expire the CRL and lock
            # every client cert out — rebuild it the moment that is observed (unprivileged).
            try:
                self.crl_refresh_if_expired()
            except Exception:
                pass
            if not self._network_authorized():
                return (False, "operator not authorized for NetworkManager")
            pref = self._net_preferred()
            # complete a console apply that waited for the operator's firewall step
            try:
                outcome = (view or {}).get("outcome") or {}
                if outcome.get("console") == "pending":
                    fst = self.firewall_status()
                    if fst.get("config_ok") and fst.get("live_ok"):
                        res = self.webserver_apply()
                        if res.ok:
                            outcome["console"] = "applied"
                            outcome["console_detail"] = "completed by the watchdog"
                            outcome.pop("firewall_cmd", None)
                            runtime_fs.atomic_write(self._paths,
                                                    self._net_outcome_path(),
                                                    json.dumps(outcome), 0o600)
                            self._net_view_invalidate()
            except Exception:
                pass
            stack, err = self._net_op_lock("network-watchdog", pref.get("ssid", ""))
            if err:
                return (False, f"skipped: {err}")
            with stack:
                conns = self._nm_connections()
                if pref.get("uuid") and not any(c["uuid"] == pref["uuid"]
                                                for c in conns):
                    self._safe_unlink(self._net_preferred_path())   # profile vanished
                    pref = {}
                problems = self._net_apply_preference(conns, pref.get("uuid", ""))
                if not force and problems:
                    # An unarmed AP must never be torn down by a retry: without the way home
                    # a failed attempt strands the box.
                    return (True, "network profile reconciliation failed — retry deferred")
                if not pref.get("uuid"):
                    return (True, "no preferred network")
                act = self._nm_active()
                if act.get("name") != self.AP_PROFILE:
                    return (True, "not on the AP — nothing to retry")
                try:
                    now_up = float((self._system.fs.read_text("/proc/uptime", 128)
                                    or "0 0").split()[0])
                except (OSError, ValueError, IndexError):
                    now_up = 0.0
                # No recorded attempt (fresh boot / first preference) means ATTEMPT NOW —
                # comparing against 0.0 would silently defer the first retry by up to the
                # whole interval (test-found). AUDIT: the stamp is uptime-based, so it is
                # bound to boot_id AND the preferred uuid — a stale stamp from a previous
                # boot (or another network), or a NEGATIVE delta, would otherwise postpone
                # reconnection for up to the previous boot's whole uptime.
                from .lifecycle import current_boot_id
                cur_boot = current_boot_id()
                last = None
                try:
                    rec = json.loads(runtime_fs.read_text_regular(
                        self._paths, self._net_retry_path(), max_bytes=512) or "")
                    if (isinstance(rec, dict) and "attempt_uptime" in rec
                            and rec.get("boot_id") == cur_boot and cur_boot
                            and rec.get("uuid") == pref.get("uuid")):
                        last = float(rec["attempt_uptime"])
                        if last > now_up:
                            last = None      # negative delta: stale, attempt now
                except Exception:
                    last = None
                if not force and last is not None \
                        and now_up - last < self.NET_RETRY_INTERVAL_S:
                    return (True, "retry interval not elapsed")
                # Single radio: the attempt takes the AP down. Never do that under a connected
                # client, and never on a guess — the stamp stays untouched, so the next minute
                # re-checks and the retry runs as soon as the AP is provably idle.
                if not force and not self._ap_idle(act.get("device") or "wlan0"):
                    return (True, "a client is connected to the AP (or its station table "
                                  "is unreadable) — retry deferred")
                try:
                    runtime_fs.atomic_write(self._paths, self._net_retry_path(),
                                            json.dumps({"attempt_uptime": now_up,
                                                        "boot_id": cur_boot,
                                                        "uuid": pref.get("uuid", "")}),
                                            0o600)
                except (OSError, PathContainmentError):
                    pass
                rc, _out, err2 = self._nmcli(["connection", "up", pref["uuid"],
                                              "ifname", "wlan0"], timeout=45.0)
                self._net_view_invalidate()
                if rc == 0:
                    return (True, f"reconnected to '{pref.get('ssid', '')}'")
                return (True, f"'{pref.get('ssid', '')}' still unreachable — AP stays up "
                              f"({err2.strip()[:120]})")
        except Exception as exc:
            return (False, f"watchdog tick failed: {exc}")

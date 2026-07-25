"""Managed Firewall — controller service mixin (FW-5 derivation + FW-4 status).

Turns the operator's saved lhpc configuration into the unprivileged CANDIDATE (the intent
half of the two-hash model), reads the root-written receipt into the three honest status
dimensions (declared / persistent / live-enforced), and renders the operator apply/reset
scripts. It never runs a privileged command and never probes on a GET beyond a bounded
receipt read — every real firewall mutation is the operator's sudo action.
"""

from __future__ import annotations

import ipaddress
import json
import os
import stat

from . import firewall as _fw
from .service_base import ActionResult

# Marker a self-update writes (old process) so the freshly-restarted (new-code) process reconciles
# the firewall integration — see `_fw_mark_post_update` / `firewall_post_update_reconcile`.
_FW_POSTUPDATE_MARKER = "state/firewall-postupdate.pending"


class FirewallOpsMixin:
    # ---- endpoint -> firewall scope resolution ----------------------------------------------

    def _fw_listener_endpoints(self):
        """Every manifest TCP LISTENER endpoint with firewall metadata, paired with its owning
        stack+component. A TCP listener WITHOUT metadata is surfaced as `unmapped` (fail
        closed — no allow derived) so a newly added listener can never be silently exposed."""
        mapped, unmapped = [], []
        for st in self.build_snapshot().stacks if hasattr(self, "build_snapshot") else []:
            for comp in st.stack.components:
                for ep in comp.endpoints:
                    if ep.kind != "tcp" or ep.role != "listener":
                        continue
                    (mapped if ep.firewall else unmapped).append((st.stack, comp, ep))
        return mapped, unmapped

    def _fw_resolve_scope(self, stk, comp, ep, overrides=None, band=None):
        """Resolve one endpoint into (endpoint_id, proto, family, addr, port, allow_cidrs,
        deny). Configured port/bind params OVERRIDE the static endpoint address (the MeshCom
        bridge's --port/--bind are the reason the static address is never authoritative). Bind
        family is derived from the resolved bind address so an IPv4-only listener never opens
        IPv6. `overrides` (keyed by `(kind, comp_id, name)`) are ephemeral per-launch values
        that take precedence over saved config. `band` selects which per-band config to read
        (None = the stack's default band) — the candidate resolves EACH band, and the gate
        resolves the actual LAUNCH band, so a per-band bind/port is never silently mis-scoped."""
        meta = ep.firewall
        band = self._config_band(stk.id, band or "") if hasattr(self, "_config_band") else ""
        static_host, _, static_port = str(ep.address).rpartition(":")
        port = self._fw_param_int(stk, comp, meta.port_param, band, static_port, overrides)
        bind = self._fw_param_str(stk, comp, meta.bind_param, band, static_host, overrides)
        allow = self._fw_param_str(stk, comp, meta.allow_param, band, "", overrides)
        cidrs = _split_cidrs(allow)
        # STABLE id: the STATIC manifest port never changes even when the configured port does,
        # so a ticked direct-access checkbox survives a port change (fragile-id fix).
        eid = _safe_id(f"{comp.id}.tcp-{static_port}")
        if meta.deny:
            # meshtasticd 4403/9443: binds 0.0.0.0 unconditionally, no knob — drop the port on
            # EVERY non-loopback address of BOTH families, regardless of the probe address.
            return {"id": eid, "proto": "tcp", "family": "dual", "addr": "*", "port": port,
                    "allow_cidrs": [], "deny": True, "auth": meta.auth, "loopback": False}
        if meta.bind_param:
            # A bind-address listener (kiss --kiss-host, bridge --bind): exposure from the
            # RESOLVED bind; the source allow-list (if any) narrows it.
            family, addr = _classify_bind(bind)
            return {"id": eid, "proto": "tcp", "family": family, "addr": addr, "port": port,
                    "allow_cidrs": cidrs, "deny": False, "auth": meta.auth, "loopback": _is_loopback(bind)}
        if meta.allow_param:
            # A source-allow-list listener with NO bind knob (meshcore wifi.allow): binds
            # LOOPBACK by default and only 0.0.0.0 once the allow-list admits a non-loopback
            # source — so its static loopback address is NOT authoritative for exposure.
            non_lo = [c for c in cidrs if not _cidr_is_loopback(c)]
            exposed = bool(non_lo)
            return {"id": eid, "proto": "tcp", "family": "dual", "addr": "*", "port": port,
                    "allow_cidrs": non_lo, "deny": False, "auth": meta.auth, "loopback": not exposed}
        family, addr = _classify_bind(static_host)
        return {"id": eid, "proto": "tcp", "family": family, "addr": addr, "port": port,
                "allow_cidrs": cidrs, "deny": False, "auth": meta.auth, "loopback": _is_loopback(static_host)}

    def _fw_param_int(self, stk, comp, name, band, default, overrides=None):
        if not name:
            return _to_int(default, 0)
        if overrides and (ov := overrides.get(("run", comp.id, name))) not in (None, ""):
            return _to_int(ov, _to_int(default, 0))
        v = self._resolved_param_value(stk.id, "run", comp.id, name, band)
        return _to_int(v, _to_int(default, 0))

    def _fw_param_str(self, stk, comp, name, band, default, overrides=None):
        # A firewall param may be a RUN param (kiss --kiss-host/--bind) OR a config-FILE param
        # (meshcore wifi.allow lives in the component's TOML) — resolve run first, then file, so
        # a file-param allow-list is never silently missed. Ephemeral per-launch overrides take
        # precedence (same run-then-file order) so a Start-confirm bind/allow change is honoured.
        if not name:
            return default
        if overrides:
            for kind in ("run", "file"):
                if (ov := overrides.get((kind, comp.id, name))) not in (None, ""):
                    return ov
        for kind in ("run", "file"):
            v = self._resolved_param_value(stk.id, kind, comp.id, name, band)
            if v not in (None, ""):
                return v
        return default

    # ---- candidate assembly -----------------------------------------------------------------

    def firewall_candidate(self, fwcfg=None) -> dict:
        """Build the current firewall-relevant candidate from saved config. Deny-default
        endpoints are always present (so their checkbox + warning render); non-loopback
        listeners get a selectable direct-access row; console + proxy ingress become
        proxy_ingress scopes following their configured bind + allowed CIDRs. `fwcfg` overrides
        the firewall section (a PROSPECTIVE FirewallConfig) so `firewall_configure` can build and
        validate the candidate BEFORE persisting anything."""
        cfg = self.config()
        if fwcfg is None:
            fwcfg = getattr(cfg, "firewall", None)
        mode = getattr(fwcfg, "mode", "secure-default")
        selected = set(getattr(fwcfg, "allow_endpoints", ()) or ())
        mapped, _unmapped = self._fw_listener_endpoints()

        endpoints = []
        for stk, comp, ep in mapped:
            # Resolve EACH band and keep DISTINCT scopes — never merge two bands into one row.
            # If every band yields the same scope (the common case), emit ONE band-agnostic row
            # (band="") so the model/hash are unchanged; only genuinely-diverging per-band scopes
            # get their own band-labelled row (so compatibility mode drops the real per-band port).
            bands = (self.stack_bands(stk.id) if hasattr(self, "stack_bands") else ()) or ("",)
            per_scope = {}
            for band in bands:
                sc = self._fw_resolve_scope(stk, comp, ep, band=band)
                if sc["loopback"] and not sc["deny"]:
                    continue                          # loopback-only, no knob -> nothing to do
                stup = (sc["proto"], sc["family"], sc["addr"], sc["port"],
                        tuple(sorted(sc["allow_cidrs"])), sc["deny"])
                per_scope.setdefault(stup, (band, sc))
            band_agnostic = len(per_scope) <= 1
            for band, sc in per_scope.values():
                key = sc["id"]
                endpoints.append(
                    {"id": key, "proto": sc["proto"], "family": sc["family"],
                     "addr": sc["addr"], "port": sc["port"], "allow_cidrs": sc["allow_cidrs"],
                     "selected": key in selected, "deny_default": sc["deny"],
                     "auth": sc.get("auth", "none"), "band": "" if band_agnostic else band})

        proxy = self._fw_proxy_ingress(cfg)
        ssh_ports = list(getattr(fwcfg, "ssh_ports", ()) or ())
        ap = {"enabled": bool(getattr(fwcfg, "ap_enabled", False)),
              "interface": getattr(fwcfg, "ap_interface", "") or "",
              "cidr": getattr(fwcfg, "ap_cidr", "") or ""}
        extra = [dict(e) for e in getattr(fwcfg, "extra_allow", ()) or ()]
        return {"schema": _fw.CANDIDATE_SCHEMA, "mode": mode, "endpoints": endpoints,
                "proxy_ingress": proxy, "ssh_ports": ssh_ports, "ap": ap,
                "extra_allow": extra}

    def _fw_proxy_ingress(self, cfg):
        """External nginx ingress the firewall must ALLOW: the console when remote-exposed,
        and each enabled+remote stack proxy — each following its configured bind (0.0.0.0
        when remote) and allowed CIDRs. Loopback-only backends are NEVER opened here."""
        out = []
        ws = cfg.webserver
        if ws.remote_exposed:
            # The console listens on its CONFIGURED bind — a concrete address (192.168.178.5)
            # must scope to THAT address, never every local address of the family.
            fam, addr = _classify_bind(ws.bind)
            out.append({"proto": "tcp", "family": fam, "addr": addr, "port": int(ws.port),
                        "allow_cidrs": _norm_cidrs(ws.allowed_cidrs, fam)})
        for _sid, swc in (cfg.stackweb or {}).items():
            if swc.enabled and swc.remote:
                # A remote stack proxy is rendered by nginx as `listen 0.0.0.0:<port>` (IPv4
                # wildcard) — independent of the console bind. Match that exactly.
                out.append({"proto": "tcp", "family": "ipv4", "addr": "*", "port": int(swc.port),
                            "allow_cidrs": _norm_cidrs(swc.allowed_cidrs, "ipv4")})
        return out

    # ---- status (FW-4): three honest dimensions ---------------------------------------------

    def firewall_status(self) -> dict:
        """GET-safe. Config dimension = saved intent vs the receipt's intent_hash. Boot +
        Live dimensions come from the root-written receipt (bounded, verified no-follow
        read). Green requires a fresh, current-boot, matching, VALID receipt — never
        declared+persistent alone. Foreign tables present => the reachability caveat."""
        try:
            candidate = self.firewall_candidate()
            intent = _fw.intent_hash(candidate)
        except Exception:                              # noqa: BLE001 — status must never raise
            candidate, intent = None, ""
        receipt = self._fw_read_receipt()
        installed = self._fw_integration_present()
        mode = (candidate or {}).get("mode", "secure-default")

        st = {"installed": installed, "mode": mode,
              "config_ok": False, "boot_ok": False, "live_ok": False,
              "transitional": False, "foreign": [], "reason": "", "line": "",
              "level": "warn", "candidate": candidate, "intent_hash": intent}

        if not installed:
            st.update(reason="setup-required",
                      line="Firewall: Verification unavailable — setup required",
                      level="warn")
            return st
        if receipt is None:
            st.update(reason="no-receipt",
                      line="Firewall: Verification unavailable — setup required")
            return st

        st["foreign"] = receipt.get("foreign_tables") or []
        st["transitional"] = bool(receipt.get("transitional"))
        fresh = self._fw_receipt_fresh(receipt)
        # The INSTALLED helper stamps its own source revision into the receipt; a mismatch with
        # the packaged helper means an lhpc update replaced the helper but the operator has not
        # re-applied — the old helper's "verified" must NOT read as green (setup/update required).
        rev_ok = receipt.get("integration_rev") == _fw.integration_rev()
        st["config_ok"] = bool(intent) and receipt.get("intent_hash") == intent
        st["boot_ok"] = installed and self._fw_units_enabled()
        # The live rules themselves are verified when a fresh receipt reports 'verified' for the
        # current intent — INDEPENDENT of the helper revision. `live_ok` additionally requires the
        # revision to match; when only the revision is stale we report 'update required' (the rules
        # are fine, the installed helper is a different build) rather than a false rule-mismatch.
        rules_verified = (fresh and receipt.get("verdict") == "verified"
                          and receipt.get("intent_hash") == intent)
        st["live_ok"] = rules_verified and rev_ok

        if st["live_ok"] and st["transitional"]:
            st.update(level="warn", reason="transition",
                      line=f"Firewall: Transition cleanup pending · {_mode_label(mode)}")
        elif st["config_ok"] and st["boot_ok"] and st["live_ok"]:
            extra = " · unwanted stack ports blocked" if mode == "compatibility" else ""
            st.update(level="ok", reason="active",
                      line=f"Firewall: Active — {_mode_label(mode)}{extra} · "
                           f"Config ✓ · Boot ✓ · Live ✓")
        elif not st["config_ok"]:
            st.update(level="warn", reason="changes-pending",
                      line="Firewall: Changes pending · Config ✗ · "
                           f"Boot {'✓' if st['boot_ok'] else '✗'} · Live ?")
        elif st["config_ok"] and st["boot_ok"] and rules_verified and not rev_ok:
            # Rules match the current intent, but the INSTALLED helper is a different revision (a
            # stale helper after an lhpc update) — re-apply, don't chase a phantom rule mismatch.
            st.update(level="warn", reason="update-required",
                      line="Firewall: Update required — re-apply the firewall after the update "
                           "· Config ✓ · Boot ✓ · Live ?")
        else:
            st.update(level="bad", reason="unverified",
                      line="Firewall: Live rules missing or mismatched — "
                           "LHPC protection unverified · Live ✗")
        return st

    # ---- receipt read (hardened) ------------------------------------------------------------

    def _fw_read_receipt(self, path=_fw.RECEIPT_PATH):
        """Bounded O_NOFOLLOW read + hard checks: regular file, ROOT owner, no group/other
        write, size bound, JSON shape, protocol match. Any failure => None (never green).
        A receipt the lhpc user could have forged must not be trusted."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            stx = os.fstat(fd)
            if not stat.S_ISREG(stx.st_mode):
                return None
            if stx.st_uid != 0:
                return None                            # not root-owned -> forgeable -> reject
            if stx.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return None
            if stx.st_size > 64 * 1024:
                return None
            raw = os.read(fd, 64 * 1024 + 1)
        except OSError:
            return None
        finally:
            os.close(fd)
        try:
            r = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(r, dict) or r.get("protocol") != _fw.PROTOCOL_VERSION:
            return None
        # EXACT-SHAPE fail-closed: the receipt must carry EXACTLY the expected fields, each with
        # the right type — a receipt missing a mandatory field OR carrying an unknown one is
        # rejected structurally (never trusted for a green verdict).
        req = {"protocol": int, "integration_rev": str, "verdict": str, "detail": str,
               "intent_hash": str, "model_hash": str, "boot_id": str, "boottime": (int, float),
               "walltime": (int, float), "transitional": bool, "foreign_tables": list}
        if set(r) != set(req):
            return None
        for key, typ in req.items():
            v = r[key]
            if typ is int and isinstance(v, bool):        # bool is not int here
                return None
            if not isinstance(v, typ):
                return None
        return r

    def firewall_log_tail(self, lines=300):
        """(path, tail_lines) of the firewall check log — the GET-safe diagnostic the root
        helper appends to /run/lhpc-firewall/firewall.log (bounded no-follow read; the root
        units otherwise log only to root journald). ('', []) when absent."""
        path = _fw.RECEIPT_PATH.rsplit("/", 1)[0] + "/firewall.log"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return "", []
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > 256 * 1024:
                return path, []
            data = os.read(fd, 256 * 1024).decode("utf-8", "replace")
        except OSError:
            return path, []
        finally:
            os.close(fd)
        return path, data.splitlines()[-lines:]

    def firewall_has_log(self) -> bool:
        return bool(self.firewall_log_tail(1)[1]) or os.path.exists(
            _fw.RECEIPT_PATH.rsplit("/", 1)[0] + "/firewall.log")

    def _fw_receipt_fresh(self, receipt) -> bool:
        """Fresh iff same boot AND CLOCK_BOOTTIME age within the window. Boot-relative time
        is suspend-inclusive and NTP-immune, so neither a clock jump nor a resume can fake
        freshness."""
        import time as _t
        if receipt.get("boot_id") != self._fw_boot_id():
            return False
        try:
            age = _t.clock_gettime(_t.CLOCK_BOOTTIME) - float(receipt.get("boottime"))
        except (TypeError, ValueError):
            return False
        return 0 <= age <= _fw.FRESH_WINDOW_S

    def _fw_boot_id(self):
        try:
            with open("/proc/sys/kernel/random/boot_id") as f:
                return f.read().strip()
        except OSError:
            return ""

    # ---- integration presence (root artifacts) ----------------------------------------------

    def _fw_unit_paths(self):
        return (f"/etc/systemd/system/{_fw.LOADER_UNIT}",
                f"/etc/systemd/system/{_fw.CHECKER_UNIT}",
                f"/etc/systemd/system/{_fw.CHECKER_TIMER}")

    def _fw_required_artifacts(self):
        """The artifacts that MUST all be present for a healthy 'present' integration: the root
        helper, the accepted candidate, the ownership metadata, the accepted snapshot, and the
        three systemd units. The transition record is OPTIONAL (present only mid-migration); the
        journal is normally ABSENT (its presence means an interrupted op — see below)."""
        return (_fw.HELPER_DEST, _fw.CANDIDATE_DEST, _fw.META_DEST, _fw.SNAPSHOT_DEST,
                *self._fw_unit_paths())

    def _fw_all_artifacts(self):
        """Every root artifact lhpc may leave behind — the required set PLUS the optional
        transition record and the journal. Used by uninstall so no residual (a stray journal,
        a half-written snapshot) is ever reported clean."""
        return (*self._fw_required_artifacts(), _fw.TRANSITION_DEST, _fw.JOURNAL_DEST)

    def _fw_integration_state(self) -> str:
        """'absent' (nothing installed), 'present' (every required artifact present AND no
        interrupted-op journal), or 'partial' (some required artifact missing, OR a journal is
        present meaning recovery is pending / the state is unsafe). The gate and uninstall both
        fail closed on 'partial' so a half-installed or mid-recovery integration is never
        mistaken for absent (which would ungate nginx) or for a healthy install."""
        req_present = [os.path.exists(p) for p in self._fw_required_artifacts()]
        journal_present = os.path.exists(_fw.JOURNAL_DEST)
        any_artifact = any(req_present) or journal_present or os.path.exists(_fw.TRANSITION_DEST)
        if not any_artifact:
            return "absent"
        if all(req_present) and not journal_present:
            return "present"
        return "partial"

    def _fw_integration_present(self) -> bool:
        return self._fw_integration_state() == "present"

    def _fw_units_enabled(self) -> bool:
        """Whether the loader + check timer are enabled at boot — read GET-SAFE from the
        systemd WantedBy symlinks (a bounded filesystem check), NEVER a `systemctl` subprocess
        (the frozen GET discipline forbids blocking subprocesses on status reads). An enabled
        WantedBy unit has a symlink under the target's `.wants` directory."""
        wants = ("/etc/systemd/system/multi-user.target.wants/" + _fw.LOADER_UNIT,
                 "/etc/systemd/system/timers.target.wants/" + _fw.CHECKER_TIMER)
        return all(os.path.exists(w) for w in wants)

    # ---- consolidated security pill (dashboard summary) -------------------------------------

    def security_pill(self, webservers=None) -> dict:
        """ONE pill for the collapsed Webserver box, worst-case across effective external
        exposure. GREEN: nothing no-auth is externally reachable without live-proven firewall
        containment. YELLOW: a no-auth port is reachable but LAN-CIDR-scoped with the firewall
        live-verified, or a state is declared-but-unattested. RED: pwless-remote exposure, or
        an unauth 0.0.0.0 port whose only containment (the firewall) is not live-proven.

        Reliance on filtering counts ONLY when live verification is current and matching —
        declared/persistent alone never turns the pill green (an lhpc allow is not proven
        reachability, and a declared drop is not proven live)."""
        rows = webservers if webservers is not None else self.dashboard_webservers()
        fw = self.firewall_status()
        fw_verified = bool(fw.get("live_ok")) and bool(fw.get("config_ok"))
        worst = "ok"

        def bump(level):
            nonlocal worst
            order = {"ok": 0, "warn": 1, "bad": 2}
            if order[level] > order[worst]:
                worst = level

        for w in rows:
            if w.get("kind") == "port":
                lvl = w.get("exposure", {}).get("level", "bad")
                if lvl == "ok":
                    continue                          # loopback — safe
                # CONSERVATIVE (P2-3): NEVER upgrade an externally-exposed port to green from
                # firewall evidence. Enforcement is scoped by proto/family/address/port(+CIDR),
                # so a drop on one scope does not prove a same-NUMBERED listener on a different
                # address/family is filtered. The listener keeps its own exposure colour; the
                # verified firewall state is reported SEPARATELY on the dashboard Firewall line.
                bump(lvl)
            elif w.get("posture"):
                sec = w["posture"].get("sec_level", w["posture"].get("auth_level", "ok"))
                if sec in ("warn", "bad"):
                    bump(sec)
        if worst == "ok":
            label, title = "secure", "No unauthenticated port is externally reachable."
        elif worst == "warn":
            label = "lan-exposed" if fw_verified else "review"
            title = ("A no-auth port is reachable but LAN-scoped and firewall-filtered."
                     if fw_verified else "Exposure present — verify the firewall / access mode.")
        else:
            label = "exposed"
            title = ("An unauthenticated port is reachable beyond your LAN, or its only "
                     "containment (the managed firewall) is not verified this boot.")
        return {"level": worst, "label": label, "title": title}

    # ---- exposure gating (FW-6, plan P1-B) --------------------------------------------------

    def firewall_gate_activation(self, prospective_ports, action_hint="Apply the webserver again"):
        """The receipt gate for any operation that CREATES, WIDENS or MOVES a non-loopback
        listener. `prospective_ports` = the COMPLETE set of TCP ports the pending activation
        will bind non-loopback (console + every enabled remote proxy — never just the edited
        one). `action_hint` closes the refusal message with the operation to retry after the
        firewall is applied. Returns (allowed: bool, message: str, next_commands: list).

        - Integration ABSENT  -> allowed (behavior preserved exactly when firewall is off).
        - Integration PARTIAL -> fail closed (a broken/half-installed firewall cannot be
          trusted to protect a remote listener).
        - No prospective non-loopback listener at all -> allowed (exposure-reducing / local).
        - A remote listener would activate -> allowed ONLY when the firewall is live-verified
          AND its receipt matches the CURRENT saved intent (config_ok — so a widened CIDR on an
          already-open port is covered too, since the intent hash changed). A live-but-stale
          receipt, or one that predates the current intent, does NOT pass. Otherwise refuse.
        """
        state = self._fw_integration_state()
        if state == "absent":
            return True, "", []
        base = self._paths.under("config/files/firewall/firewall-apply.sh")
        if state == "partial":
            return (False,
                    "Firewall integration is partially installed — refusing to activate a "
                    "remote listener until it is repaired.",
                    [f"sudo bash {base}"])
        if not prospective_ports:
            return True, "", []                            # nothing remote to protect
        st = self.firewall_status()
        # config_ok proves the receipt's intent == the CURRENT saved intent (ports AND CIDRs);
        # live_ok proves that intent is actually loaded this boot. Both are required — a
        # port that is already socket-exposed is NOT evidence of firewall protection.
        if st.get("config_ok") and st.get("live_ok"):
            return True, "", []
        # DEADLOCK FIX: regenerate the apply script NOW so it embeds the CURRENT candidate —
        # otherwise a firewall-relevant webserver change (new intent hash) would send the
        # operator to run a stale firewall-apply.sh that can never satisfy the new intent. The
        # gate is only ever called from a mutation (webserver apply/start), so this write is
        # GET-safe. Best-effort: a render failure still yields the command + a check hint.
        self.firewall_render()
        return (False,
                "Firewall changes pending — the remote listener was NOT activated. Apply the "
                f"firewall first, then {action_hint}.",
                [f"sudo bash {base}", "sudo systemctl start lhpc-firewall-check.service"])

    def firewall_boot_gate(self):
        """nginx ExecStartPre boot gate. Runs as the lhpc user before nginx binds. If firewall
        integration is installed, the desired config would expose a non-loopback listener, and
        the current-boot firewall receipt is NOT valid+matching (bounded wait for the loader to
        finish first), regenerate the ACTIVE nginx config LOOPBACK-ONLY so nginx never binds a
        remote port ahead of a verified firewall. The DESIRED intent lives in local.toml and is
        untouched; state/webserver.json (the applied-evidence) is written only by verify, so the
        remote config is never falsely marked applied. Returns an ActionResult whose `ok` the CLI
        maps to the exit code: True → nginx may start (verified, or safely loopback-only); False
        → FAIL CLOSED (the fallback could not be established, so nginx must NOT start rather than
        bind the promoted remote config)."""
        import time as _t

        from . import webserver as _ws
        from .config import WebserverConfig
        state = self._fw_integration_state()
        if state == "absent":
            return ActionResult(True, "firewall integration absent — no boot gate")
        cfg = self.config()
        prospective = self._prospective_nginx_ports(cfg)
        if not prospective:
            return ActionResult(True, "no remote listener desired — nothing to gate")
        # PARTIAL / unsafe (a required artifact missing, or an interrupted-op journal present):
        # the firewall cannot be trusted to protect a remote listener this boot, and a bounded
        # wait can never turn a half-installed integration green — go straight to loopback-only.
        # Only a fully 'present' integration is given the bounded wait for a verified receipt.
        if state == "present":
            deadline = _t.monotonic() + _fw.BOOT_GATE_WAIT_S
            while True:
                st = self.firewall_status()
                if st.get("config_ok") and st.get("live_ok"):
                    return ActionResult(True,
                                        "firewall verified this boot — remote listener allowed")
                if _t.monotonic() >= deadline:
                    break
                _t.sleep(1.0)
        # Fall back to a loopback-only ACTIVE config generated from the current desired config.
        ws = cfg.webserver
        loopback = WebserverConfig(bind="127.0.0.1", port=ws.port, access_mode=ws.access_mode,
                                   remote_exposed=False, allowed_cidrs=(), dns_sans=ws.dns_sans,
                                   ip_sans=ws.ip_sans, scheme=ws.scheme)
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, loopback, ())
        if ok:
            _ws.promote_config(self._paths)
            return ActionResult(True, "firewall not verified this boot — nginx started "
                                "LOOPBACK-ONLY; apply the firewall then re-apply the webserver")
        # FAIL CLOSED: could not establish the loopback-only fallback, so refuse to start nginx
        # (starting now would bind the promoted REMOTE config ahead of a verified firewall).
        return ActionResult(False, f"firewall boot gate: could not stage loopback fallback "
                            f"({msg}) — refusing to start nginx with the remote config")

    def _prospective_nginx_ports(self, cfg=None):
        """The complete non-loopback TCP port set `webserver_apply()` will activate: the
        console when remote-exposed + each enabled remote stack proxy. Loopback-only
        listeners are never in this set."""
        cfg = cfg or self.config()
        ports = set()
        if cfg.webserver.remote_exposed:
            ports.add(int(cfg.webserver.port))
        for _sid, swc in (cfg.stackweb or {}).items():
            if swc.enabled and swc.remote:
                ports.add(int(swc.port))
        return ports

    # ---- self-update integration (FW P1-2 B/C) ----------------------------------------------

    def _fw_remote_web_exposed(self) -> bool:
        """Whether nginx would bind a NON-loopback listener with the current config (console or a
        remote stack proxy). When false, a stale nginx unit can be migrated post-advance / on the
        next reboot without gating the update."""
        return bool(self._prospective_nginx_ports())

    def firewall_update_nginx_preflight(self):
        """Called right before self-update advances the checkout. If the managed firewall is
        installed AND remote web access is configured AND the nginx unit that carries the boot
        gate cannot be brought current THIS run, return an ActionResult to ABORT the update (never
        advance into a state where remote nginx could start ungated after reboot). Returns None to
        proceed. No-op when the firewall is absent or nothing is remotely exposed."""
        from . import updater_units
        if self._fw_integration_state() == "absent" or not self._fw_remote_web_exposed():
            return None
        integ = self.updater_integration()
        verdict = integ.get("per_unit", {}).get(updater_units.NGINX_UNIT)
        if verdict == updater_units.OK:
            return None                                    # unit already current -> gate present
        owned_stale = verdict in (updater_units.MISSING, updater_units.MODIFIED_OURS)
        managed = bool(integ.get("managed"))
        # Foreign/ambiguous unit (cannot be safely replaced), OR a managed (bus-blocked) updater
        # that cannot daemon-reload the file it writes: the gated unit cannot take effect this run
        # while remote is exposed -> require an explicit repair before updating.
        if not owned_stale or managed:
            cmd = "lhpc self-update --repair-integration"
            why = "is foreign/ambiguous" if not owned_stale else "needs a daemon-reload this updater cannot do"
            return ActionResult(
                False,
                "Self-update stopped: the managed firewall is installed and remote web access is "
                f"configured, but the nginx unit carrying the firewall boot gate {why}. Run "
                f"`{cmd}` first, then re-run the update (remote access stays protected).",
                data={"firewall_integration_incomplete": True}, next_commands=[cmd])
        return None                                        # interactive + owned-stale -> migrate post-advance

    def firewall_update_after_advance(self) -> list:
        """After a successful self-update advance: (B) regenerate the firewall operator scripts to
        match the new helper, and (C) bring the LHPC-owned nginx unit current on disk (interactive
        path also daemon-reloads; the bus-blocked managed path writes the file and reports that a
        reload/reboot is required). Returns operator notes. Never raises — an update is never
        failed by this best-effort follow-up."""
        from . import updater_units
        notes: list = []
        if self._fw_integration_state() == "absent":
            return notes
        try:
            self.firewall_render()                         # (B) scripts match the updated helper
        except Exception:                                  # noqa: BLE001 — best effort
            pass
        try:
            integ = self.updater_integration()
            verdict = integ.get("per_unit", {}).get(updater_units.NGINX_UNIT)
            if verdict == updater_units.OK:
                return notes
            if verdict not in (updater_units.MISSING, updater_units.MODIFIED_OURS):
                notes.append("The nginx unit is not lhpc-owned — the firewall boot gate may be "
                             "missing. Run `lhpc self-update --repair-integration`.")
                return notes
            ud, root = self._user_unit_dir(), str(self._paths.runtime_root)
            try:
                updater_units.write_set(ud, root)          # atomically writes the gated unit (bus-free)
            except ValueError:
                notes.append("Could not replace the nginx unit (a conflicting unit is present). "
                             "Run `lhpc self-update --repair-integration`.")
                return notes
            if integ.get("managed"):                       # bus-blocked -> file written, reload/reboot needed
                notes.append("Updated the nginx unit file (firewall boot gate) — a daemon-reload "
                             "or reboot is required to load it.")
            else:                                          # interactive -> we have the bus
                self._system.runner.run(["systemctl", "--user", "daemon-reload"], timeout=20.0)
                notes.append("Updated the nginx unit (firewall boot gate) and reloaded systemd.")
        except Exception:                                  # noqa: BLE001 — best effort
            pass
        return notes

    def _fw_mark_post_update(self) -> None:
        """Record (from the OLD process, right after a self-update advances the checkout) that the
        firewall integration must be reconciled. The reconciliation itself MUST run in the FRESH
        interpreter after restart — the current process imported the firewall/updater_units modules
        BEFORE the update, so regenerating scripts/units here would emit the PREVIOUS version's
        artifacts. Writing a plain marker carries no version dependency."""
        from . import runtime_fs
        try:
            runtime_fs.write_marker(self._paths,
                                    self._paths.under(_FW_POSTUPDATE_MARKER), "1")
        except Exception:                                  # noqa: BLE001 — best effort
            pass

    def firewall_post_update_reconcile(self) -> list:
        """Run at web-console STARTUP in the freshly-restarted (new-code) process. If a self-update
        left the post-update marker, regenerate the firewall scripts + migrate the LHPC-owned nginx
        unit using the CURRENT templates (via `firewall_update_after_advance`), then clear the
        marker. Best-effort; returns operator notes for the startup log."""
        from . import runtime_fs
        if not self._marker_present(_FW_POSTUPDATE_MARKER):
            return []
        notes = self.firewall_update_after_advance()
        try:
            runtime_fs.unlink(self._paths, self._paths.under(_FW_POSTUPDATE_MARKER))
        except Exception:                                  # noqa: BLE001 — best effort
            pass
        return notes

    # ---- stack-start exposure gate (FW-R8, plan P1-B) ---------------------------------------

    def _fw_prospective_stack_scopes(self, target, params=None, band="", file_overrides=None):
        """The complete set of non-loopback listener SCOPES the START of `target` will bind,
        resolved from the ACTUAL launch plan: the LAUNCH band's per-band config PLUS ephemeral
        run/file overrides for THIS launch. Each scope is a full tuple (id/proto/family/addr/
        port/allow_cidrs/band) — never reduced to a bare port. A deny-default listener counts as
        exposed; loopback-only listeners are excluded. An UNMAPPED tcp listener (no firewall
        metadata) is included and flagged `_unmapped` so it can never match a modeled scope
        (fail-closed). Empty when the target owns no externally-binding listener."""
        order = self._run_order(target)
        if not order:
            return []
        comp_ids = {c.id for _s, c in order}
        cfg_band = self._config_band(target, band)
        ov = {}                                            # (kind, comp_id, name) -> ephemeral value
        for kind, raw in (("run", params), ("file", file_overrides)):
            if not raw:
                continue
            for _s, comp in order:
                for name, val in self._overrides_for_comp(target, kind, raw, comp.id).items():
                    ov[(kind, comp.id, name)] = val
        mapped, unmapped = self._fw_listener_endpoints()
        scopes = []
        for stk, comp, ep in mapped:
            if comp.id not in comp_ids:
                continue
            sc = self._fw_resolve_scope(stk, comp, ep, overrides=ov, band=cfg_band)
            if sc["loopback"] and not sc["deny"]:
                continue                                   # loopback-only -> nothing to expose
            scopes.append({"id": sc["id"], "proto": sc["proto"], "family": sc["family"],
                           "addr": sc["addr"], "port": int(sc["port"]),
                           "allow_cidrs": list(sc["allow_cidrs"]), "band": cfg_band})
        for stk, comp, ep in unmapped:
            if comp.id not in comp_ids:
                continue
            host, _, sport = str(ep.address).rpartition(":")
            if _is_loopback(host):
                continue
            scopes.append({"id": _safe_id(f"{comp.id}.tcp-{sport}"), "proto": "tcp",
                           "family": "dual", "addr": "*", "port": _to_int(sport, 0),
                           "allow_cidrs": [], "band": cfg_band, "_unmapped": True})
        return scopes

    def _fw_scope_modeled(self, sc, candidate) -> bool:
        """True iff prospective launch scope `sc` EXACTLY matches a modeled candidate scope on
        endpoint id, protocol, family, address, port and band, with its applicable CIDRs covered.
        A modeled DROP (deny-default, or an unselected endpoint) covers any source; a modeled
        SELECTED allow must cover the launch's source CIDRs. A generic secure-default policy drop
        does NOT count — an unmodeled scope (unknown ephemeral bind/port, wrong-band, or unmapped
        listener) is never represented, so the gate refuses it."""
        if sc.get("_unmapped"):
            return False
        for e in candidate.get("endpoints", ()):
            if e["id"] != sc["id"]:
                continue
            eb = e.get("band", "")
            if eb and eb != sc["band"]:
                continue                                   # band-specific row for a different band
            if (e["proto"], e["family"], e["addr"], e["port"]) != \
               (sc["proto"], sc["family"], sc["addr"], sc["port"]):
                continue
            if e["deny_default"] or not e["selected"]:
                return True                                # modeled as a DROP -> firewall filters it
            return _cidrs_covered(sc["allow_cidrs"], e["allow_cidrs"])   # selected allow
        return False

    def firewall_gate_stack_start(self, target, params=None, band="", file_overrides=None):
        """Exposure gate for a stack/component START. Absent firewall -> allow (behaviour
        preserved). Partial -> refuse. No non-loopback scope -> allow. Otherwise the firewall
        must be live-verified against the current saved intent (config_ok+live_ok) AND every
        prospective non-loopback scope must EXACTLY match a modeled candidate scope (full
        proto/family/addr/port/band/CIDR) — an ephemeral widening/move or a wrong-band remote
        scope not in the model is refused, so a listener never binds non-loopback without a
        verified drop/allow. Returns (allowed, message, commands)."""
        state = self._fw_integration_state()
        if state == "absent":
            return True, "", []
        base = self._paths.under("config/files/firewall/firewall-apply.sh")
        if state == "partial":
            return (False,
                    "Firewall integration is partially installed — refusing to start a "
                    "non-loopback listener until it is repaired.",
                    [f"sudo bash {base}"])
        scopes = self._fw_prospective_stack_scopes(target, params, band, file_overrides)
        if not scopes:
            return True, "", []                            # nothing non-loopback -> nothing to gate
        st = self.firewall_status()
        if not (st.get("config_ok") and st.get("live_ok")):
            self.firewall_render()                         # keep the apply script current (mutation path)
            return (False,
                    "Firewall changes pending — the listener was NOT started. Apply the firewall "
                    f"first, then start '{target}' again.",
                    [f"sudo bash {base}", "sudo systemctl start lhpc-firewall-check.service"])
        modeled = st.get("candidate") or self.firewall_candidate()
        for sc in scopes:
            if not self._fw_scope_modeled(sc, modeled):
                self.firewall_render()
                return (False,
                        "Save the setting permanently, apply the firewall, then start.",
                        [f"sudo bash {base}"])
        return True, "", []

    # ---- settings view + configure (FW-7) ---------------------------------------------------

    def firewall_settings_view(self) -> dict:
        """Everything the Firewall settings section renders: mode, per-listener rows (stable
        id, resolved proto/port, deny flag, selection, whether it exposes non-loopback), the
        locked SSH + proxy-ingress rows, AP controls, foreign-firewall recommendation, and the
        three script paths for the cmdboxes."""
        cand = self.firewall_candidate()
        fwcfg = getattr(self.config(), "firewall", None)
        st = self.firewall_status()
        rows, seen_ids = [], set()
        for e in cand["endpoints"]:
            # One UI row per STABLE endpoint id — a stack whose per-band scopes diverge produces
            # multiple internal (id, band) rows, but the operator selects direct access by id.
            if e["id"] in seen_ids:
                continue
            seen_ids.add(e["id"])
            rows.append({"id": e["id"], "port": e["port"], "proto": e["proto"],
                         "family": e["family"], "deny": e["deny_default"],
                         "selected": e["selected"], "auth": e.get("auth", "none")})
        scripts = self._fw_script_paths()             # paths only — GET must not write files
        recommend_compat = bool(st.get("foreign"))
        return {
            "mode": cand["mode"],
            "endpoints": rows,
            "proxy_ingress": cand["proxy_ingress"],           # locked/informational
            "ssh_ports": list(getattr(fwcfg, "ssh_ports", ()) or ()),
            "ap": cand["ap"],
            "status": st,
            "recommend_compatibility": recommend_compat,
            "apply_cmd": f"sudo bash {scripts['firewall-apply.sh']}",
            "reset_cmd": f"sudo bash {scripts['firewall-reset.sh']}",
            "check_cmd": "sudo systemctl start lhpc-firewall-check.service",
            # Surfaced only while a transition is pending — completes a listener migration by
            # dropping the preserved old scopes (a normal apply keeps them until proven gone).
            "cleanup_cmd": (f"sudo bash {scripts['firewall-cleanup.sh']}"
                            if st.get("transitional") else ""),
            "installed": st.get("installed"),
        }

    def firewall_configure(self, *, mode=None, allow_endpoints=None, ssh_ports=None,
                           ap_enabled=None, ap_interface=None, ap_cidr=None,
                           recommended=False) -> ActionResult:
        """Save `[firewall]` intent and regenerate the apply/reset scripts. `recommended`
        applies the safe preset: secure-default, no direct-access exceptions, AP off — the
        one-click 'Use recommended settings'. Never runs a privileged command."""
        from . import config as _cfg
        # The recommended preset is a COMPLETE safe reset: secure-default, no direct-access
        # exceptions, AP off, AND the advanced escape hatches (manual SSH ports + extra_allow)
        # CLEARED — otherwise "Use recommended settings" could silently retain arbitrary inbound
        # allowances the operator forgot about.
        extra_allow = [] if recommended else None
        if recommended:
            mode, allow_endpoints, ap_enabled, ssh_ports = "secure-default", [], False, []
        # FAIL CLOSED BEFORE saving: a checked AP without an explicit interface AND CIDR is
        # REJECTED (never silently downgraded to disabled). Validate inputs before any write.
        if ap_enabled and (not (ap_interface or "").strip() or not (ap_cidr or "").strip()):
            return ActionResult(False, "Access-Point mode requires both an interface and an "
                                "IPv4 CIDR — nothing was saved.")
        # Build the PROSPECTIVE config, validate the candidate, render+replace the scripts, and
        # commit config — ALL under ONE exclusive config lock, so concurrent requests can never
        # interleave (leaving one request's scripts beside another's config). Nothing is persisted
        # until validation passes; SCRIPTS FIRST (atomic temp+fsync+rename), CONFIG LAST.
        from . import validators
        from .config import FirewallConfig
        try:
            with _cfg.config_lock(self._paths):
                cur = getattr(self.config(), "firewall", None) or FirewallConfig()
                try:
                    if mode is not None and mode not in _cfg.FIREWALL_MODES:
                        raise validators.ValidationError(f"invalid firewall mode {mode!r}")
                    p_ssh = (tuple(int(validators.port(x, field="firewall.ssh_ports"))
                                   for x in ssh_ports)
                             if ssh_ports is not None else cur.ssh_ports)
                    if ap_cidr is None:
                        p_cidr = cur.ap_cidr
                    elif str(ap_cidr).strip():
                        p_cidr = validators.cidr(ap_cidr, field="firewall.ap_cidr")
                    else:
                        p_cidr = ""
                except validators.ValidationError as exc:
                    return ActionResult(False, f"firewall settings invalid (not saved): {exc}")
                prospective = FirewallConfig(
                    mode=mode if mode is not None else cur.mode,
                    allow_endpoints=(tuple(dict.fromkeys(str(e).strip() for e in allow_endpoints
                                                         if str(e).strip()))
                                     if allow_endpoints is not None else cur.allow_endpoints),
                    ssh_ports=p_ssh,
                    ap_enabled=bool(ap_enabled) if ap_enabled is not None else cur.ap_enabled,
                    ap_interface=(str(ap_interface).strip() if ap_interface is not None
                                  else cur.ap_interface),
                    ap_cidr=p_cidr,
                    extra_allow=tuple(extra_allow) if extra_allow is not None else cur.extra_allow)
                cand = self.firewall_candidate(fwcfg=prospective)
                errs = _fw.validate_candidate(cand)
                if errs:
                    return ActionResult(False, "firewall settings invalid (not saved): "
                                        + "; ".join(errs[:5]))
                try:
                    self.firewall_scripts(candidate=cand)
                except OSError as exc:
                    return ActionResult(False,
                                        f"could not render firewall scripts (not saved): {exc}",
                                        next_commands=["lhpc firewall --script"])
                try:
                    _cfg.save_firewall_config(
                        self._paths, mode=mode, allow_endpoints=allow_endpoints,
                        ssh_ports=ssh_ports, ap_enabled=ap_enabled, ap_interface=ap_interface,
                        ap_cidr=ap_cidr, extra_allow=extra_allow, hold_lock=False)
                except _cfg.ConfigError as exc:
                    return ActionResult(False, f"firewall config rejected: {exc}")
        except _cfg.ConfigLockBusy as exc:
            return ActionResult(False, f"firewall settings not saved — {exc}")
        self._invalidate_config()
        details = []
        if not self.firewall_status().get("live_ok"):
            details = ["Apply the firewall to activate the change:",
                       f"  {self.firewall_settings_view()['apply_cmd']}"]
        return ActionResult(True, "firewall settings saved", details=details)

    def firewall_render(self) -> ActionResult:
        """Regenerate the operator scripts from current config. Never silent on failure."""
        try:
            self.firewall_scripts()
        except OSError as exc:
            return ActionResult(False, f"could not render firewall scripts: {exc}",
                                next_commands=["lhpc firewall --script"])
        return ActionResult(True, "firewall scripts regenerated")

    # ---- script rendering -------------------------------------------------------------------

    def _fw_script_paths(self) -> dict:
        """Just the on-disk paths of the operator scripts — NO writes (GET-safe). The settings
        view and dashboard use these for the cmdboxes; the files themselves are (re)written by
        `firewall_scripts()` on MUTATION only."""
        base = self._paths.under("config/files/firewall")
        return {name: os.path.join(base, name)
                for name in ("firewall-apply.sh", "firewall-reset.sh", "firewall-cleanup.sh")}

    def firewall_scripts(self, candidate=None) -> dict:
        """Render the three operator scripts into the runtime config dir (never executed by
        lhpc). WRITES files — call only from a mutation (configure/render), never a GET. Each
        file is written through a same-directory temp file + fsync + atomic rename, so an operator
        running a script never sees a truncated/half-written file. `candidate` (a prospective one)
        lets `firewall_configure` render the PROSPECTIVE scripts without first persisting config."""
        if candidate is None:
            candidate = self.firewall_candidate()
        cj = json.dumps(candidate, sort_keys=True)
        texts = {"firewall-apply.sh": _fw.render_apply_script(cj),
                 "firewall-reset.sh": _fw.render_reset_script(),
                 "firewall-cleanup.sh": _fw.render_cleanup_script()}
        base = self._paths.under("config/files/firewall")
        os.makedirs(base, exist_ok=True)
        out = {}
        for name, text in texts.items():                    # all rendered in memory ABOVE first
            out[name] = _atomic_write_script(os.path.join(base, name), text)
        return out


# --- helpers (pure) ------------------------------------------------------------------------

def _atomic_write_script(path, text, mode=0o755):
    """Write an executable script through a same-directory temp file + fsync + atomic rename —
    never truncate the live file in place (an operator could be executing it). Returns `path`."""
    import tempfile
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    try:
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return path


def meta_proto(ep):
    return "tcp"


def _mode_label(mode):
    return "Secure default" if mode == "secure-default" else "Compatibility"


def _to_int(v, default):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _is_loopback(host):
    h = (host or "").strip()
    if h in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _classify_bind(host):
    """(family, addr) for a resolved bind: wildcard -> ('dual'|'ipv4'|'ipv6', '*'); a concrete
    address -> its family + the address itself."""
    h = (host or "").strip()
    if h in ("", "0.0.0.0"):
        return ("ipv4", "*") if h == "0.0.0.0" else ("dual", "*")
    if h == "::":
        return ("ipv6", "*")
    try:
        ip = ipaddress.ip_address(h)
        return ("ipv4" if ip.version == 4 else "ipv6", h)
    except ValueError:
        return ("dual", "*")


def _split_cidrs(value):
    if not value:
        return []
    parts = [p.strip() for p in str(value).replace(",", " ").split()]
    return _norm_cidrs(parts)


def _cidr_is_loopback(cidr):
    try:
        return ipaddress.ip_network(str(cidr).strip(), strict=False).is_loopback
    except ValueError:
        return False


def _norm_cidrs(cidrs, family=None):
    """Normalize to CANONICAL CIDR strings (ALWAYS with a prefix — a bare address like
    `127.0.0.1` becomes `127.0.0.1/32`, which the renderer requires; a bare address crashed
    `_cidr_right()` before). Whole-internet prefixes are dropped (no narrowing). When `family`
    is ipv4/ipv6, CIDRs of the other family are dropped so a scope can never open the wrong
    family via its allow-list."""
    out = []
    for c in cidrs or ():
        c = str(c).strip()
        if not c:
            continue
        try:
            net = ipaddress.ip_network(c, strict=False)   # accepts a bare host as /32 or /128
        except ValueError:
            continue
        if int(net.prefixlen) == 0:                       # 0.0.0.0/0 or ::/0 -> no narrowing
            continue
        fam = "ipv4" if net.version == 4 else "ipv6"
        if family in ("ipv4", "ipv6") and fam != family:
            continue
        out.append(net.with_prefixlen)
    return sorted(set(out))


def _safe_id(text):
    import re
    t = re.sub(r"[^a-z0-9._-]", "-", text.lower())
    return t[:128] or "ep"


def _cidrs_covered(launch_cidrs, model_cidrs) -> bool:
    """True iff every source CIDR the LAUNCH would admit is covered by the MODEL's allow-list.
    An empty model allow-list means "allow from any source" (covers everything). A non-empty
    model with an empty launch (launch = any source) is NOT covered — an ephemeral widening to
    all sources must be refused. Each launch CIDR must be a subnet of some model CIDR."""
    model = _norm_cidrs(model_cidrs)
    if not model:
        return True                                        # model allows any source
    launch = _norm_cidrs(launch_cidrs)
    if not launch:
        return False                                       # launch admits any source; model restricts
    model_nets = [ipaddress.ip_network(c, strict=False) for c in model]
    for lc in launch:
        ln = ipaddress.ip_network(lc, strict=False)
        if not any(ln.version == mn.version and ln.subnet_of(mn) for mn in model_nets):
            return False
    return True

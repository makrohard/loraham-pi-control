"""nginx/TLS/mTLS console + per-stack web-UI proxy operations for ControllerService.

Mixin: these methods run on a ControllerService instance (state/constants live on the facade).
Adapters must import lhpc.core.services, never this module."""
from __future__ import annotations

from .paths import PathContainmentError
from .service_base import ActionResult


class WebserverOpsMixin:

    # ---- webserver (controller-owned component; NOT a managed stack) ----------
    #
    # Thin delegation to pki/webserver/config. Every mutation validates before writing and
    # fails closed; status reads cached evidence only. These are controller-owned and are
    # NEVER routed through the generic stack/component verbs (install/build/test/...): the
    # Webserver "component" is presentation only, so controller isolation is unaffected.

    def webserver_monitor(self, served_via_nginx: bool | None = None) -> "ActionResult":
        """READ-ONLY status (Monitor/GET): desired config + effective evidence + PKI state + warnings.
        No network/subprocess probe, no mutation — but the console listener SCOPE is read live from
        /proc (as the stack-proxy bypass warnings below already are), so the panel is accurate on load
        without a re-verify. `served_via_nginx` (request-scoped: is THIS session proxied through nginx?)
        drives the console running pill — the adapter supplies it from the nginx-set X-LHPC-Peer header."""
        from . import webserver as _ws
        cfg = self.config().webserver
        live_scope = _ws.listener_scope(self._system, cfg.port)   # "exposed" | "loopback" | "absent"
        view = _ws.monitor_view(self._paths, cfg, live_listener_scope=live_scope,
                                served_via_nginx=served_via_nginx)
        # The per-stack web-UI proxies are part of the config nginx loads — show them here too, with
        # the standing warning for any upstream that answers around this proxy.
        proxies = []
        for p in self._stack_web_proxies():
            v = self.stack_web_view(p.swc.stack_id)
            proxies.append({"stack_id": p.swc.stack_id, "port": p.swc.port, "mode": p.swc.mode,
                            "scheme": p.swc.scheme, "access_mode": p.swc.access_mode,
                            "upstream": p.upstream_address,
                            "bypassable": bool(v.get("bypassable"))})
        view["stack_proxies"] = proxies
        # monitor_view's warnings are {"level","text"} dicts (the template renders w.level/w.text) —
        # match that shape, or the panel shows an empty flash.
        for pr in proxies:
            if pr["bypassable"]:
                view.setdefault("warnings", []).append({
                    "level": "danger",
                    "text": (f"{pr['stack_id']}: its own port on {pr['upstream']} is listening on "
                             "all interfaces — reachable directly, bypassing this proxy's "
                             "authentication. Firewall it or accept the exposure.")})
        return ActionResult(True, "webserver monitor", data=view)

    def webserver_verify(self) -> "ActionResult":
        """Explicit verification: assemble + persist the effective-evidence checklist.

        Validates the SAME config `apply` would promote — stack web-UI proxies included. Verifying a
        console-only config and reporting "verified" would be a claim about a config nginx never loads."""
        from . import webserver as _ws
        ev = _ws.verify(self._system, self._paths, self.config().webserver,
                        self._stack_web_proxies())
        failed = [k for k, v in ev["checks"].items() if v == "failed"]
        ok = not failed
        summary = "webserver verified" if ok else f"verification found issues: {', '.join(failed)}"
        details = []
        for sid in ev["checks"].get("upstream_bypass_stacks", []):
            details.append(f"  WARNING: {sid}'s upstream port is listening on all interfaces — "
                           "reachable directly, bypassing this proxy's authentication.")
        return ActionResult(ok, summary, details=details, data=ev)

    def webserver_init(self, *, dns_sans=None, ip_sans=None, confirm=False) -> "ActionResult":
        """First-time bootstrap (correction #2): create BOTH CAs, the server leaf, and an
        initial (empty) CRL. Remote exposure stays disabled until explicitly enabled + proven.
        RE-initializing when a CA already exists is DESTRUCTIVE (invalidates every issued
        certificate) and requires explicit `confirm`."""
        from . import pki as _pki
        st = _pki.pki_status(self._paths)
        if (st["server_ca"].get("present") or st["client_ca"].get("present")) and not confirm:
            return ActionResult(False, "PKI already exists — recreating the CAs is DESTRUCTIVE "
                                "(invalidates all issued client/server certificates). Confirm to "
                                "proceed.", next_commands=["lhpc webserver init --confirm-recreate"])
        cfg = self.config().webserver
        dns = list(dns_sans) if dns_sans is not None else list(cfg.dns_sans)
        ips = list(ip_sans) if ip_sans is not None else list(cfg.ip_sans)
        if not dns and not ips:
            dns = ["localhost"]                    # usable loopback default SANs — must match the
            ips = ["127.0.0.1"]                    # advertised https://127.0.0.1:8443/ endpoint
        # Persist the SANs into DESIRED config (correction 3) so productive trusted-host
        # enforcement AND `tls-renew` use them. FAIL CLOSED (correction A): if persistence fails
        # for ANY reason (validation, ConfigError/lock, unsafe path, malformed local.toml, I/O)
        # we abort BEFORE touching any PKI material — no CA/cert/CRL/inventory is created or
        # replaced, and no success is reported.
        from . import config as _config
        try:
            _config.save_webserver_config(self._paths, dns_sans=dns, ip_sans=ips)
        except Exception as exc:
            return ActionResult(False, f"webserver init aborted — could not persist SANs to "
                                f"config ({exc}); no PKI was created or replaced")
        self._invalidate_config()
        try:
            _pki.init_server_ca(self._paths, force=True)
            _pki.init_client_ca(self._paths, force=True)
            _pki.issue_server_cert(self._paths, dns_sans=dns, ip_sans=ips,
                                   days=cfg.server_cert_days)
            _pki.build_crl(self._paths)
        except _pki.PKIError as exc:
            return ActionResult(False, f"webserver init failed: {exc}")
        return ActionResult(True, "webserver PKI initialized (two CAs + server cert + CRL)",
                            next_commands=["lhpc webserver verify"])

    def webserver_configure(self, **fields) -> "ActionResult":
        from . import config as _config
        from .validators import ValidationError
        try:
            _config.save_webserver_config(self._paths, **fields)
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid webserver config: {exc}")
        self._invalidate_config()
        return ActionResult(True, "webserver configuration saved (desired; run verify/apply)",
                            next_commands=["lhpc webserver verify"])

    def webserver_configure_apply(self, *, bind=None, port=None, scheme=None, access_mode=None,
                                  dns_sans=None, ip_sans=None, allowed_cidrs=None,
                                  confirm=False, confirm_public=False) -> "ActionResult":
        """Unified controller Settings action (the single 'Apply' button): derive `remote_exposed` from
        `bind`, gate remote exposure with `plan_exposure` (elevated confirm for public/no-auth/http), then
        — only on accept — save ALL fields in ONE write (incl. `remote_exposed` + `allowed_cidrs`), add the
        host IP SAN + reissue the server cert on exposure, and apply (staged validate + reload). On refusal
        it saves nothing and applies nothing. Folds in the former dedicated Remote-exposure form."""
        from . import config as _config, webserver as _ws
        from .config import WebserverConfig
        from .validators import ValidationError
        cur = self.config().webserver
        e_bind = cur.bind if bind is None else bind
        e_port = cur.port if port is None else int(port)
        e_scheme = cur.scheme if scheme is None else scheme
        e_access = cur.access_mode if access_mode is None else access_mode
        e_cidrs = tuple(cur.allowed_cidrs) if allowed_cidrs is None else tuple(allowed_cidrs)
        e_dns = tuple(cur.dns_sans) if dns_sans is None else tuple(dns_sans)
        e_ip = tuple(cur.ip_sans) if ip_sans is None else tuple(ip_sans)
        remote = not _ws._is_loopback_bind(e_bind)          # remote_exposed follows the bind
        probe = WebserverConfig(bind=e_bind, port=e_port, scheme=e_scheme, access_mode=e_access,
                                remote_exposed=remote, allowed_cidrs=e_cidrs,
                                dns_sans=e_dns, ip_sans=e_ip)
        plan = _ws.plan_exposure(probe)
        if plan["problems"]:
            return ActionResult(False, "cannot apply webserver configuration", details=plan["problems"])
        if plan["remote"]:
            if plan["danger"] == "elevated" and not confirm_public:
                what = ("a public source range (0.0.0.0/0)" if plan["public"]
                        else "no client authentication" if plan.get("no_auth")
                        else "an unencrypted (http) listener")
                return ActionResult(False, f"remote exposure with {what} needs elevated confirmation",
                                    details=["re-run with the elevated confirmation to proceed"])
            if not confirm:
                return ActionResult(False, "remote exposure needs explicit confirmation",
                                    details=["re-run with confirmation to proceed"])
        try:
            _config.save_webserver_config(self._paths, bind=e_bind, port=e_port, scheme=e_scheme,
                                          access_mode=e_access, remote_exposed=remote,
                                          allowed_cidrs=list(e_cidrs), dns_sans=list(e_dns),
                                          ip_sans=list(e_ip))
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid webserver config: {exc}")
        self._invalidate_config()
        san_notes = self._expose_add_san_and_reissue() if remote else []
        ar = self.webserver_apply()
        return ActionResult(ar.ok, ar.summary, details=[*san_notes, *ar.details],
                            next_commands=ar.next_commands, data=ar.data)

    # ---- per-stack web-UI reverse proxies -------------------------------------------------

    def stack_web_upstream(self, stack_id: str):
        """(address, scheme) of a stack's web UI from the MANIFEST, or None when it has none.

        The upstream is evidence, never operator input: an `EndpointSpec` with `client=true` and an
        http/https scheme. This is what keeps `upstream_scheme` independent of the listener scheme."""
        s = self.stack(stack_id)
        if s is None:
            return None
        for comp in s.components:
            for ep in comp.endpoints:
                if getattr(ep, "client", False) and ep.scheme in ("http", "https"):
                    return (ep.address, ep.scheme)
        return None

    def stack_web_eligible(self) -> list:
        """Stack ids that expose a web UI (derived from the manifest, never hardcoded)."""
        return [s.id for s in self.stacks() if self.stack_web_upstream(s.id) is not None]

    def _stack_web_proxies(self) -> list:
        """The `StackWebProxy` list for nginx rendering — only stacks with a port set (enabled)."""
        from . import webserver as _ws
        cfgs = self.config().stackweb
        out = []
        for sid in self.stack_web_eligible():
            swc = cfgs.get(sid)
            if swc is None or not swc.enabled:
                continue
            up = self.stack_web_upstream(sid)
            if up is None:                       # eligibility changed under us; skip, never render half
                continue
            out.append(_ws.StackWebProxy(swc, up[0], up[1]))
        return out

    def _stack_listen_scope(self, swc) -> str:
        """Effective network scope of a stackweb proxy's OWN nginx listen port, read live from
        /proc/net/tcp: "exposed" (answers off-loopback — reachable on the LAN), "loopback" (127.0.0.1
        only), or "absent" (nothing listening — proxy disabled, not applied, or nginx down).

        This is the GROUND TRUTH for what a browser can actually reach, independent of the DESIRED
        `mode`: a stale 0.0.0.0 listener left after a `local`-mode save without Apply reads "exposed",
        and a `public` mode not yet applied reads "loopback". Used so the dashboard link and the stack's
        Webserver header never lie about reachability."""
        from . import webserver as _ws
        if swc is None or not getattr(swc, "enabled", False) or not getattr(swc, "port", 0):
            return "absent"
        return _ws.listener_scope(self._system, swc.port)

    def stack_web_view(self, stack_id: str) -> dict:
        """READ-ONLY view for the stack's Webserver panel. Includes the raw-port warning, which is
        evidence from THIS host (/proc/net/tcp), not a hardcoded per-stack fact."""
        from . import webserver as _ws
        from .config import StackWebConfig, STACKWEB_MODES, WEBSERVER_ACCESS_MODES, WEBSERVER_SCHEMES
        up = self.stack_web_upstream(stack_id)
        if up is None:
            return {}
        address, upstream_scheme = up
        swc = self.config().stackweb.get(stack_id) or StackWebConfig(stack_id=stack_id)
        ws = self.config().webserver
        used = {c.port for sid, c in self.config().stackweb.items()
                if sid != stack_id and c.enabled}
        suggested = swc.port or self._default_stack_web_port(stack_id, ws.port)
        try:
            upstream_port = int(str(address).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            upstream_port = 0
        scope = _ws.listener_scope(self._system, upstream_port) if upstream_port else "absent"
        listen_scope = self._stack_listen_scope(swc)
        plan = _ws.plan_stack_exposure(swc, ws.port, used)
        return {
            "stack_id": stack_id, "cfg": swc, "upstream_address": address,
            "upstream_scheme": upstream_scheme, "upstream_port": upstream_port,
            "upstream_scope": scope, "suggested_port": suggested,
            "modes": STACKWEB_MODES, "access_modes": WEBSERVER_ACCESS_MODES,
            "schemes": WEBSERVER_SCHEMES, "plan": plan,
            "urls": _ws.stack_ui_urls(swc) if swc.enabled else [],
            # The raw upstream port answers on a non-loopback interface: our proxy's auth is
            # bypassable, whatever `mode` says. Standing fact, shown on every load.
            "bypassable": scope == "exposed",
            # EFFECTIVE listen scope of the proxy port + whether it disagrees with the desired mode
            # (i.e. an Apply is still pending to make the live listener match the saved intent).
            "listen_scope": listen_scope,
            "pending": bool(swc.enabled and (
                listen_scope == "absent" or swc.remote != (listen_scope == "exposed"))),
            # Security + running posture for the two summary pills. Security via posture(); the RUNNING
            # pill for a PROXY is: grey "offline" (stack not started — its web-UI upstream is down),
            # yellow "local-only" (started but nginx is not proxying it), green "proxied" (started + nginx).
            "posture": {
                **_ws.posture(local=swc.mode == "local", public=swc.mode == "public",
                              access_mode=swc.access_mode, has_cidrs=bool(swc.allowed_cidrs),
                              scheme=swc.scheme),
                "run": "offline" if scope == "absent" else (
                    "local-only" if listen_scope == "absent" else "proxied"),
                "run_level": "off" if scope == "absent" else (
                    "warn" if listen_scope == "absent" else "ok"),
            },
            # Same remote-exposure/auth/listener warnings the console shows (identical wording+values),
            # for an ENABLED proxy. A proxy binds 0.0.0.0 when remote, 127.0.0.1 when local.
            "warnings": _ws.exposure_warnings(
                remote=swc.remote, access_mode=swc.access_mode, allowed_cidrs=swc.allowed_cidrs,
                bind="0.0.0.0" if swc.remote else "127.0.0.1", port=swc.port,
                live_scope=listen_scope) if swc.enabled else [],
        }

    def dashboard_webservers(self, served_via_nginx: bool | None = None) -> list[dict]:
        """Rows for the dashboard Webserver box: the console (LHCP) ALWAYS, then each web-UI stack whose
        MAIN component is currently running/degraded. Structural evidence only — the adapter adds the
        request-scoped reached address. A running-but-not-proxied stack carries `direct_port`/
        `direct_scheme` so the box can show where it listens directly; the shared nginx log link lives
        in the box header (all proxied UIs share the one front-end log)."""
        from .model import RunState
        up = (RunState.RUNNING, RunState.DEGRADED)
        mon = self.webserver_monitor(served_via_nginx=served_via_nginx).data or {}
        rows: list[dict] = [{"kind": "console", "name": "LHCP", "posture": mon.get("posture"),
                             "port": mon.get("desired", {}).get("port"), "logs_component": None}]
        by_id = {ss.stack.id: ss for ss in self.build_snapshot().stacks}
        for sid in self.stack_web_eligible():
            ss, stk = by_id.get(sid), self.stack(sid)
            if ss is None or stk is None or stk.main_component is None:
                continue
            mst = ss.components.get(stk.main_component.id)
            if mst is None or mst.run_state not in up:      # not started -> no row (per the operator)
                continue
            v = self.stack_web_view(sid) or {}
            swc = v.get("cfg")
            enabled = bool(swc and swc.enabled)
            # The web-UI component carries a client http/https endpoint — used for the DIRECT address.
            web_ep = None
            for c in stk.components:
                for ep in c.endpoints:
                    if getattr(ep, "client", False) and ep.scheme in ("http", "https"):
                        web_ep = ep
                        break
                if web_ep:
                    break
            # DIRECT web-UI address (host:port from the client endpoint) — surfaced so a running but
            # NOT-proxied web UI still shows where it listens (the adapter reattaches the reached host,
            # local vs remote, exactly like the console pill).
            direct_port = web_ep.address.rsplit(":", 1)[-1] if (web_ep and ":" in web_ep.address) else ""
            rows.append({"kind": "stack", "name": stk.name, "sid": sid, "enabled": enabled,
                         "posture": v.get("posture") if enabled else None,
                         "port": swc.port if enabled else None,
                         "direct_port": direct_port, "direct_scheme": web_ep.scheme if web_ep else ""})
        return rows

    def _default_stack_web_port(self, stack_id: str, console_port: int) -> int:
        """A STABLE per-stack default port: `console_port + 1 + position`, where position is the
        stack's index among the eligible web-UI stacks sorted by id. So meshcom → 8444, meshtastic
        → 8445, deterministically and without colliding — the old 'first free above the console'
        gave every not-yet-enabled stack the SAME port (8444), so accepting two suggestions collided.

        A default is only ever WRITTEN when the operator saves the panel; an untouched stack keeps
        no port key, so a fresh deployment's rendered nginx stays unchanged."""
        eligible = sorted(self.stack_web_eligible())
        pos = eligible.index(stack_id) if stack_id in eligible else 0
        return min(max(console_port, 1023) + 1 + pos, 65535)

    def stack_web_configure(self, stack_id: str, *, mode=None, port=None, scheme=None,
                            access_mode=None, cidrs=None, confirm=False,
                            confirm_public=False) -> "ActionResult":
        """Persist ONE stack's web-UI proxy policy. Mirrors `webserver_expose`'s two-level
        confirmation. Writes INTENT only — activation is `lhpc webserver apply`."""
        from . import config as _config, webserver as _ws
        from .config import StackWebConfig
        from .validators import ValidationError
        if self.stack_web_upstream(stack_id) is None:
            return ActionResult(False, f"stack '{stack_id}' has no web UI to proxy")
        ws = self.config().webserver
        current = self.config().stackweb.get(stack_id) or StackWebConfig(stack_id=stack_id)
        used = {c.port for sid, c in self.config().stackweb.items() if sid != stack_id and c.enabled}
        probe = StackWebConfig(
            stack_id=stack_id,
            mode=current.mode if mode is None else mode,
            port=current.port if port is None else int(port),
            scheme=current.scheme if scheme is None else scheme,
            access_mode=current.access_mode if access_mode is None else access_mode,
            allowed_cidrs=current.allowed_cidrs if cidrs is None else tuple(cidrs))
        plan = _ws.plan_stack_exposure(probe, ws.port, used)
        if plan["problems"]:
            return ActionResult(False, f"cannot configure '{stack_id}' web UI",
                                details=plan["problems"])
        if plan["remote"]:
            if plan["danger"] == "elevated" and not confirm_public:
                what = ("a public source range (0.0.0.0/0)" if plan["public"]
                        else "no client authentication" if plan["no_auth"]
                        else "an unencrypted (http) listener")
                return ActionResult(False, f"remote exposure with {what} needs elevated confirmation",
                                    details=["re-run with the elevated confirmation to proceed"])
            if not confirm:
                return ActionResult(False, "remote exposure needs explicit confirmation",
                                    details=["re-run with confirmation to proceed"])
        try:
            _config.save_stackweb_config(self._paths, stack_id, mode=mode, port=port, scheme=scheme,
                                         access_mode=access_mode, allowed_cidrs=cidrs)
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid web-UI config: {exc}")
        self._invalidate_config()
        details = []
        view = self.stack_web_view(stack_id)
        if view.get("bypassable"):
            details.append(
                f"  WARNING: {stack_id}'s upstream port {view['upstream_port']} is listening on all "
                f"interfaces — it is reachable directly, bypassing this proxy's authentication.")
            details.append("  Firewall that port or accept the exposure; LHPC cannot close it.")
        if probe.enabled:
            details += [f"  {u}" for u in _ws.stack_ui_urls(probe)]
        details.append("lhpc webserver apply           # render + validate + reload nginx")
        return ActionResult(True, f"web UI proxy for '{stack_id}' saved (desired; run apply)",
                            details=details, next_commands=["lhpc webserver apply"])

    def stack_web_configure_apply(self, stack_id: str, **kwargs) -> "ActionResult":
        """Unified per-stack Settings action (the single 'Apply' button): save this proxy's policy (with
        its two-level typed confirmation) then apply (staged validate + reload). Save-only failures (incl.
        a needed confirmation) short-circuit — nothing is applied."""
        r = self.stack_web_configure(stack_id, **kwargs)
        if not r.ok:
            return r
        ar = self.webserver_apply()
        return ActionResult(ar.ok, ar.summary, details=[*r.details, *ar.details],
                            next_commands=ar.next_commands, data=ar.data)

    def webserver_expose(self, cidrs, *, access_mode=None, confirm=False,
                         confirm_public=False) -> "ActionResult":
        """Enable remote exposure. Requires >=1 CIDR; a public default route (0.0.0.0/0) or
        a no-auth remote mode needs elevated confirmation. Writes desired config only — the
        listener is not proven active until verify/apply."""
        from . import config as _config, webserver as _ws
        from .config import WebserverConfig
        from .validators import ValidationError
        cidrs = list(cidrs or [])
        mode = access_mode or self.config().webserver.access_mode
        probe = WebserverConfig(bind="0.0.0.0", port=self.config().webserver.port,
                                access_mode=mode, remote_exposed=True,
                                allowed_cidrs=tuple(cidrs))
        plan = _ws.plan_exposure(probe)
        if plan["problems"]:
            return ActionResult(False, "cannot enable remote exposure",
                                details=plan["problems"])
        if plan["danger"] == "elevated" and not confirm_public:
            what = "no client authentication" if plan.get("no_auth") else "a public source range (0.0.0.0/0)"
            return ActionResult(False, f"remote exposure with {what} needs elevated confirmation",
                                details=["re-run with the elevated confirmation to proceed"])
        if not confirm:
            return ActionResult(False, "remote exposure needs explicit confirmation",
                                details=["re-run with confirmation to proceed"])
        try:
            _config.save_webserver_config(self._paths, bind="0.0.0.0", remote_exposed=True,
                                          allowed_cidrs=cidrs, access_mode=mode)
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid exposure config: {exc}")
        self._invalidate_config()
        # The LAN address must reach BOTH the trusted-host allowlist and the server cert's SANs, or a
        # remote browser gets a 400 (unknown Host) and a certificate name mismatch. Nothing else adds
        # it — `local_ip()` was known and displayed, but never persisted.
        #
        # ORDERING: every step reads FRESHLY-loaded config. `self.config()` is memoized, so a `cfg`
        # captured before the write above would silently drop any ip_sans another writer persisted in
        # between, and would reissue the cert from pre-exposure state.
        san_notes = self._expose_add_san_and_reissue()
        return ActionResult(
            True, "remote exposure enabled (desired) — now APPLY to rebind the listener to "
            f"0.0.0.0:{self.config().webserver.port} and reload nginx (until then it stays on "
            "loopback and remote clients get connection refused)",
            details=[*san_notes,
                     "lhpc webserver apply           # reload nginx: new bind AND the reissued cert",
                     "lhpc webserver start-service   # if nginx is not running yet"],
            next_commands=["lhpc webserver apply"])

    def _expose_add_san_and_reissue(self) -> list:
        """Persist this host's LAN IP as an `ip_sans` entry and reissue the server cert from the FINAL
        persisted config. Returns truthful detail lines; never raises, never fails the exposure.

        FAIL-SOFT by contract: the exposure config is already written. `issue_server_cert` raises when
        the server CA is not initialized — rolling the exposure back over that would leave the operator
        strictly worse off than a missing SAN, so we keep ok=True and disclose."""
        from . import config as _config, pki as _pki, webserver as _ws
        cfg = self.config().webserver                    # FRESH: post-exposure-write state
        ip = _ws.local_ip()
        if not ip:
            return ["  SAN: this host's LAN address could not be determined — no SAN added; add it "
                    "by hand to [webserver] ip_sans, then: lhpc webserver tls-renew"]
        if ip in cfg.ip_sans:
            return [f"  SAN: {ip} is already an IP SAN — certificate left untouched"]
        try:
            _config.save_webserver_config(self._paths, ip_sans=[*cfg.ip_sans, ip])
        except Exception as exc:                         # noqa: BLE001 — never fail a done exposure
            return [f"  SAN: could not persist {ip} as an IP SAN ({exc}) — add it by hand, then: "
                    "lhpc webserver tls-renew"]
        self._invalidate_config()
        cfg = self.config().webserver                    # FRESH again: the cert follows what is on disk
        try:
            _pki.issue_server_cert(self._paths, dns_sans=list(cfg.dns_sans),
                                   ip_sans=list(cfg.ip_sans), days=cfg.server_cert_days)
        except Exception as exc:                         # noqa: BLE001 — incl. PKIError (no CA yet)
            return [f"  SAN: {ip} added to ip_sans, but the certificate was NOT reissued ({exc})",
                    "       run: lhpc webserver init   # then: lhpc webserver tls-renew"]
        return [f"  SAN: {ip} added to ip_sans and the server certificate was reissued for it"]

    def webserver_disable_remote(self) -> "ActionResult":
        from . import config as _config
        _config.save_webserver_config(self._paths, bind="127.0.0.1", remote_exposed=False)
        self._invalidate_config()
        return ActionResult(True, "remote exposure disabled (bind reset to loopback) — "
                            "verify to prove the remote listener has ceased",
                            next_commands=["lhpc webserver verify"])

    def webserver_reset_defaults(self) -> "ActionResult":
        """Reset to safe defaults AND prove remote exposure has ceased. Writes DESIRED defaults
        (loopback:8443, local unauthenticated, remote off, CIDRs cleared), stages + VALIDATES a
        loopback-only nginx config, and — if a proven LHPC-owned nginx master exists — reloads
        it (a successful reload of the loopback-only config is the cessation proof: the new
        config has no remote listener). Reports success ONLY when cessation is proven; otherwise
        stays truthful ('reset requested; remote cessation unproven'). NEVER deletes CA keys,
        certificates, CRL, revocation history, `.p12` exports, or the session secret."""
        from . import config as _config, webserver as _ws
        # scheme MUST be reset alongside access_mode, in the same save. `save_webserver_config`
        # resolves the patch over the STORED config, so resetting to a cert-based access mode while
        # leaving a stored scheme=http would raise ConfigError (http can't do client-cert auth) —
        # a valid http console could then not reset at all.
        _config.save_webserver_config(self._paths, bind="127.0.0.1", port=8443, scheme="https",
                                      access_mode="local-open-remote-auth",
                                      remote_exposed=False, allowed_cidrs=[])
        # ALSO disable every per-stack web-UI proxy. A `lan`/`public` stack proxy renders its own
        # `listen 0.0.0.0:<port>` block, so resetting only the console would leave remote listeners
        # active while this method proves "remote exposure ceased" — a false claim. port=0 removes
        # the block entirely; the operator's mode/CIDR choices are kept for an easy re-enable.
        disabled = []
        for sid, swc in self.config().stackweb.items():
            if swc.enabled:
                _config.save_stackweb_config(self._paths, sid, port=0)
                disabled.append(sid)
        self._invalidate_config()
        cfg = self.config().webserver
        proxies = self._stack_web_proxies()      # now empty of enabled entries
        # Stage + validate the loopback-only config; promote only on success (never clobber a
        # proven live config with an invalid one).
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg, proxies)
        ev = _ws.verify(self._system, self._paths, cfg, proxies)
        if not ok:
            return ActionResult(False, "reset requested; loopback config invalid — remote "
                                f"cessation UNPROVEN ({msg})", data=ev)
        # Defense in depth: NEVER claim full cessation while any enabled proxy would still bind
        # off-loopback. After the disable loop this must be empty; if a write silently failed, stay
        # honest rather than assert a listener is gone when it is not.
        remaining = [sid for sid, c in self.config().stackweb.items() if c.enabled and c.remote]
        detail = ([f"  disabled stack web-UI proxy: {sid}" for sid in disabled]
                  + [f"  STILL REMOTE (reset failed): {sid}" for sid in remaining])
        _ws.promote_config(self._paths)
        if _ws.nginx_master_active(self._paths):
            state, rmsg = _ws.reload(self._system, self._paths)
            if state == "reloaded":
                # RE-READ the console listener scope AFTER the reload — `ev` came from a verify() run
                # BEFORE promote+reload, so its `listener_scope` reflects the pre-reset (still
                # exposed) nginx. Write a CONSISTENT effective block, and only claim cessation when
                # BOTH no stack proxy remains remote AND the console is no longer bound off-loopback.
                console_scope = _ws.listener_scope(self._system, cfg.port)
                console_exposed = console_scope == "exposed"
                proven = (not remaining) and (not console_exposed)
                ev["effective"] = {**ev.get("effective", {}),
                                   "listener_scope": console_scope,
                                   "remote_listener": console_exposed,
                                   "remote_cessation_proven": proven}
                _ws.write_evidence(self._paths, ev)
                if console_exposed:
                    detail.append(f"  console listener still exposed on port {cfg.port}")
                if proven:
                    return ActionResult(True, "webserver reset to defaults — remote exposure ceased "
                                        "(loopback-only config reloaded and proven)",
                                        details=detail, data=ev)
                what = ("the console listener" if console_exposed and not remaining
                        else "a stack web-UI proxy" if remaining and not console_exposed
                        else "the console listener and a stack web-UI proxy")
                return ActionResult(False, f"config reset and nginx reloaded, but {what} is STILL "
                                    "bound remotely — cessation UNPROVEN", details=detail, data=ev)
            return ActionResult(False, f"reset requested; nginx reload failed — remote cessation "
                                f"UNPROVEN ({rmsg})", details=detail, data=ev)
        ev["effective"] = {**ev.get("effective", {}), "remote_cessation_proven": False}
        _ws.write_evidence(self._paths, ev)
        return ActionResult(False, "reset requested; no active nginx master to reload — remote "
                            "cessation UNPROVEN (start/repair the service to prove it)",
                            details=detail, next_commands=["lhpc webserver verify"], data=ev)

    def webserver_tls_renew(self) -> "ActionResult":
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.issue_server_cert(self._paths, dns_sans=list(cfg.dns_sans),
                                          ip_sans=list(cfg.ip_sans), days=cfg.server_cert_days)
        except _pki.PKIError as exc:
            return ActionResult(False, f"server certificate renewal failed: {exc}")
        return ActionResult(True, f"server certificate renewed (serial {summ['serial']})",
                            data=summ)

    def webserver_cert_issue(self, label, passphrase) -> "ActionResult":
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.issue_client_cert(self._paths, label, days=cfg.client_cert_days,
                                          passphrase=passphrase)
        except Exception as exc:
            return ActionResult(False, f"client certificate issue failed: {exc}")
        return ActionResult(True, f"issued client certificate '{summ['label']}'",
                            details=[f"export: {summ['export']}",
                                     f"sha256: {summ['export_sha256']}",
                                     f"expires: {summ['not_after']}"], data=summ)

    def webserver_cert_reissue(self, label, passphrase) -> "ActionResult":
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.reissue_client_cert(self._paths, label, days=cfg.client_cert_days,
                                            passphrase=passphrase)
        except Exception as exc:
            return ActionResult(False, f"reissue failed: {exc}")
        return ActionResult(True, f"reissued client certificate '{summ['label']}'", data=summ)

    def webserver_cert_list(self) -> "ActionResult":
        from . import pki as _pki
        return ActionResult(True, "client certificates",
                            data={"certs": _pki.list_client_certs(self._paths)})

    def webserver_cert_revoke(self, label) -> "ActionResult":
        from . import pki as _pki
        try:
            _pki.revoke_client_cert(self._paths, label)
        except Exception as exc:
            return ActionResult(False, f"revoke failed: {exc}")
        return ActionResult(True, f"revocation RECORDED for '{label}' and CRL regenerated — "
                            "not proven effective until the proxy reloads and rejects it",
                            next_commands=["lhpc webserver verify"])

    def webserver_cert_discard_export(self, label) -> "ActionResult":
        from . import pki as _pki
        removed = _pki.discard_export(self._paths, label)
        return ActionResult(True, f"export {'discarded' if removed else 'already absent'} for '{label}'")

    def webserver_cert_export_bytes(self, label) -> "bytes | None":
        """Raw `.p12` bytes for a label (or None). The WEB route must gate this on a
        loopback-origin session; the CLI locates the file directly."""
        from . import pki as _pki
        return _pki.read_export(self._paths, label)

    def webserver_apply(self) -> "ActionResult":
        """Activate the DESIRED config: render + validate the nginx config FIRST (never
        activate an invalid one), then reload an already-running LHPC-owned nginx master, then
        verify + persist evidence. A missing/inactive master returns a typed 'service not active /
        repair required' result — the web process performs no start and no package install. A
        reload cannot rebind a held listen socket, so on a BIND change (loopback <-> 0.0.0.0) whose
        effective scope does not match the desired exposure this RESTARTS the unit (`systemctl
        --user restart lhpc-nginx.service`) and re-verifies; it never reports a bind change that did
        not take effect (F3)."""
        from . import webserver as _ws
        if not _ws.nginx_installed(self._system):
            return ActionResult(False, "nginx is not installed — required system dependency for "
                                "the production webserver", details=[_ws.NGINX_INSTALL_CMD],
                                next_commands=[_ws.NGINX_INSTALL_CMD])
        cfg = self.config().webserver
        # Stage + validate BEFORE touching the live config; promote atomically only on success
        # (a failed nginx -t leaves the previous proven live config byte-for-byte intact).
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg,
                                                  self._stack_web_proxies())
        if not ok:
            return ActionResult(False, "nginx config validation failed; previous proven "
                                f"configuration remains active ({msg})")
        _ws.promote_config(self._paths)
        state, rmsg = _ws.reload(self._system, self._paths)
        ev = _ws.verify(self._system, self._paths, cfg, self._stack_web_proxies())
        if state == "repair_required":
            return ActionResult(False, "config valid but the nginx service is not active — "
                                "repair required (operator context)",
                                details=[rmsg], data=ev)
        if state == "failed":
            return ActionResult(False, f"nginx reload failed: {rmsg}", data=ev)
        # F3: a reload cannot rebind a held listen socket, so a bind change (loopback <-> 0.0.0.0)
        # can leave the OLD listener in place while reload reports success. When the effective scope
        # does not match the desired exposure, RESTART the unit (ExecStop releases the socket,
        # ExecStart rebinds) and re-verify — never report a bind change that did not take effect.
        if ev["checks"].get("remote_listener_matches") == "ok":
            return ActionResult(True, "webserver configuration applied and nginx reloaded", data=ev)
        rstate, rmsg2 = _ws.restart(self._system, self._paths)
        ev = _ws.verify(self._system, self._paths, cfg, self._stack_web_proxies())
        if ev["checks"].get("remote_listener_matches") == "ok":
            return ActionResult(True, "webserver configuration applied; nginx restarted to rebind "
                                "the listener (a bind change needs a restart, not a reload)", data=ev)
        scope = ev.get("effective", {}).get("listener_scope", "unknown")
        return ActionResult(
            False, f"configuration applied but the console listener is still '{scope}' "
            f"(desired remote_exposed={cfg.remote_exposed}) — the bind change did not take effect; "
            "restart the front-end manually", details=[rmsg2],
            next_commands=["systemctl --user restart lhpc-nginx.service"], data=ev)

    def webserver_start_service(self) -> "ActionResult":
        """OPERATOR-CONTEXT bootstrap (correction 1): generate + validate + promote the nginx
        config, then ENABLE + START the rootless nginx user unit via `systemctl --user`. This is
        the only path that STARTS nginx — it REFUSES to run from a managed unit (the web process
        never starts a listener), so after `init` the operator runs this once to bring the HTTPS
        console up. Prerequisites (nginx installed, server cert present, config valid) are
        checked and reported truthfully."""
        import os as _os
        from . import pki as _pki, webserver as _ws
        if _os.environ.get("INVOCATION_ID"):
            return ActionResult(False, "refusing to start nginx from a managed unit — run "
                                "`lhpc webserver start-service` from an interactive operator shell")
        if not _ws.nginx_installed(self._system):
            return ActionResult(False, "nginx is not installed", details=[_ws.NGINX_INSTALL_CMD],
                                next_commands=[_ws.NGINX_INSTALL_CMD])
        cfg = self.config().webserver
        proxies = self._stack_web_proxies()
        # A server certificate is a prerequisite only for a config that actually terminates TLS.
        # An all-http desired config (console AND every enabled proxy on `scheme=http`) needs no PKI;
        # demanding it unconditionally is why `scheme=http` was only half-functional.
        if _ws.tls_required(cfg, proxies) and not _pki.pki_status(
                self._paths)["server_cert"].get("present"):
            return ActionResult(False, "no HTTPS server certificate — run `lhpc webserver init` "
                                "first", next_commands=["lhpc webserver init"])
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg, proxies)
        if not ok:
            return ActionResult(False, f"nginx config invalid — not starting ({msg})")
        _ws.promote_config(self._paths)
        r = self._system.runner.run(
            ["systemctl", "--user", "enable", "--now", "lhpc-nginx.service"], 20.0)
        if getattr(r, "not_found", False) or r.returncode != 0:
            detail = (r.stderr or r.stdout or "systemctl failed").strip().splitlines()
            return ActionResult(False, "could not enable/start lhpc-nginx.service",
                                details=[detail[-1] if detail else "systemctl failed"],
                                next_commands=["systemctl --user enable --now lhpc-nginx.service"])
        ev = _ws.verify(self._system, self._paths, cfg, proxies)
        # The console's real URL, from its own scheme/exposure — never a hardcoded https, and never
        # `https://0.0.0.0:8443/`, which is a bind wildcard and not an address anyone can visit.
        urls = _ws.console_urls(cfg)
        return ActionResult(True, f"nginx enabled + started — console at {urls[0]}",
                            details=[f"  {u}" for u in urls[1:]], data=ev)

    def webserver_log_tail(self, source: str = "error", lines: int = 300):
        """Raw (path, lines) for the LHPC-managed nginx front-end's on-disk logs. `source`
        selects the access or (default) error log — an unknown selector degrades to the error
        log so it can never name an arbitrary path. Read-only: a bounded, O_NOFOLLOW disk tail
        (same guard as `log_tail`), no systemctl/network probe."""
        from . import runtime_fs, webserver as _ws
        const = _ws._ACC_LOG if source == "access" else _ws._ERR_LOG
        try:
            n = max(1, min(int(lines), 5000))             # clamp to a sane bounded range
        except (TypeError, ValueError):
            n = 300
        try:
            p = self._paths.under(*const)
        except PathContainmentError:
            return "", []
        if p.is_symlink() or (p.exists() and not p.is_file()):
            return str(p), []
        return str(p), runtime_fs.tail(self._paths, p, n)

"""nginx/TLS/mTLS console + per-stack web-UI proxy operations for ControllerService.

Mixin: these methods run on a ControllerService instance (state/constants live on the facade).
Adapters must import lhpc.core.services, never this module."""
from __future__ import annotations

import dataclasses as _dc

from .paths import PathContainmentError
from .service_base import ActionResult

# nginx-restart escape-hatch wait bounds (module-level so tests can shrink them): how long the web
# branch of `webserver apply` waits for the path-unit watcher to claim the request and for a fresh
# verify to prove the rebound listeners.
_RESTART_WATCH_WAIT_S = 15.0
_RESTART_WATCH_POLL_S = 0.5


class WebserverOpsMixin:

    # ---- webserver (controller-owned component; NOT a managed stack) ----------
    #
    # Thin delegation to pki/webserver/config. Every mutation validates before writing and
    # fails closed; status reads cached evidence only. These are controller-owned and are
    # NEVER routed through the generic stack/component verbs (install/build/test/...): the
    # Webserver "component" is presentation only, so controller isolation is unaffected.

    def webserver_monitor(self, served_via_nginx: bool | None = None,
                          listeners=None, fw_status=None) -> ActionResult:
        """READ-ONLY status (Monitor/GET): desired config + effective evidence + PKI state + warnings.
        No network/subprocess probe, no mutation — but the console listener SCOPE is read live from
        /proc (as the stack-proxy bypass warnings below already are), so the panel is accurate on load
        without a re-verify. `served_via_nginx` (request-scoped: is THIS session proxied through nginx?)
        drives the console running pill — the adapter supplies it from the nginx-set X-LHPC-Peer header.
        `listeners`/`fw_status` let a whole dashboard render share ONE /proc read and ONE firewall
        read (see `dashboard_webservers`)."""
        from . import webserver as _ws
        cfg = self.config().webserver
        listeners = self._listeners(listeners)
        applied = _ws.read_applied(self._paths)
        ac = applied.get("console") or {}
        # WHICH PORT is the console actually on? After a saved port move the running nginx still
        # holds the port it was APPLIED with, so probing only the desired port would report
        # "absent" and lose a live, still-exposed old listener entirely.
        ports = [int(cfg.port)]
        try:
            old = int(ac.get("port") or 0)
        except (TypeError, ValueError):
            old = 0
        if old and old != int(cfg.port):
            ports.append(old)
        scopes = {p: _ws.listener_scope(self._system, p, listeners) for p in ports}
        live_port = next((p for p in ports if scopes[p] == "exposed"),
                         next((p for p in ports if scopes[p] == "loopback"), int(cfg.port)))
        live_scope = scopes[live_port]
        # Does the VERIFIED firewall provably restrict the console's live listener? Only that may
        # soften an unauthenticated remote console from red to yellow — it is reachable, but only
        # from the allowed sources. `pending` (live listener disagreeing with desired intent) is
        # handled inside monitor_view; an unproven/stale/transitional firewall yields None.
        contained = (self._listener_restricted(live_port, listeners, fw_status)
                     if live_scope == "exposed" else None)
        view = _ws.monitor_view(self._paths, cfg, live_listener_scope=live_scope,
                                served_via_nginx=served_via_nginx,
                                firewall_contained=contained,
                                applied_console=ac, live_port=live_port)

        # The per-stack web-UI proxies are part of the config nginx loads — show them here too, with
        # the standing warning for any upstream that answers around this proxy.
        proxies = []
        for p in self._stack_web_proxies():
            v = self.stack_web_view(p.swc.stack_id, listeners=listeners, fw_status=fw_status,
                                    applied=applied)
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

    def _listeners(self, listeners=None):
        """One `tcp_listeners()` snapshot, or the caller's shared one. Fail-soft: [] reads as
        'nothing is listening', which is the conservative answer everywhere it is used."""
        if listeners is not None:
            return listeners
        try:
            return self._system.procfs.tcp_listeners()
        except Exception:
            return []

    def _listener_restricted(self, port, listeners=None, fw_status=None):
        """True when the VERIFIED firewall provably restricts the LIVE listener on `port` to
        allowed sources; None when nothing is proven, so the caller keeps its conservative colour.
        Deliberately tri-state-collapsed to True/None: only a proven restriction may soften a
        pill, and `denied`/`open`/`unknown` must each leave it alone here."""
        try:
            scopes = self.listener_scopes(int(port), self._listeners(listeners))
            return True if self.firewall_containment(scopes, fw_status) == "restricted" else None
        except Exception:
            return None

    def _upstream_bypassable(self, port, listeners=None, fw_status=None) -> bool:
        """Is a stack's OWN (unproxied) upstream port genuinely reachable AROUND this proxy?

        "Bound off-loopback" alone over-warned: Meshtastic's 4403/9443 are deny-default endpoints
        that the managed firewall drops by default, so they are bound wide and still unreachable —
        they bypass nothing. Containment decides:
          * `denied`  — not bypassable;
          * `restricted` — bypassable, for the allowed sources;
          * `open` / `unknown` / unverified / transitional — bypassable, nothing was proven;
          * loopback or absent — nothing to bypass.
        """
        try:
            if not port:
                return False
            scopes = self.listener_scopes(int(port), self._listeners(listeners))
            if not scopes or all(self.scope_is_loopback(s) for s in scopes):
                return False
            return self.firewall_containment(scopes, fw_status) != "denied"
        except Exception:
            return True                     # cannot prove containment -> keep the warning

    def _ws_verify(self, cfg, proxies, *, probe_console: bool = False) -> dict:
        """THE single seam through which this mixin calls `webserver.verify()`: it supplies the
        containment-aware bypass test, and (by NOT passing an applied snapshot) guarantees that
        verifying never advances one. Activation is recorded separately, by `_record_applied`."""
        from . import webserver as _ws
        return _ws.verify(self._system, self._paths, cfg, proxies, probe_console=probe_console,
                          bypassable=self._upstream_bypassable)

    def _record_applied(self, cfg, proxies) -> None:
        """Advance the applied-policy snapshot. Called ONLY where activation is proven: the config
        was promoted, the reload/restart succeeded, and the listener-match gate passed. Fail-soft —
        a policy record that cannot be written must not turn a successful apply into a failure
        (the stale snapshot then keeps the posture conservative, which is the safe direction)."""
        from . import webserver as _ws
        try:
            _ws.record_applied(self._paths, cfg, proxies)
        except Exception:
            pass

    # ---- Apply deferred by the firewall gate ---------------------------------------------------
    # The gate only DEFERS an activation the operator already confirmed. Without a record of that,
    # the refusal was a one-off flash: after the firewall step nothing completed the apply and
    # nothing at the firewall panel said it was still owed (live-found 2026-09-04). Same contract
    # as the network join's console apply: a marker, completed by the watchdog once verified.
    # The marker records the DEFERRAL only: it is cleared as soon as the gate lets an apply run
    # (whatever that apply's outcome — a later failure is shown in the Webserver panel, and must
    # not be retried unasked in the background). It names the POLICY whose Apply was deferred:
    # a save-only edit made meanwhile (e.g. `webserver configure --access-mode no-auth`, which the
    # firewall intent does not see) must not ride along — it needs its own Apply (audit P1).

    def _ws_apply_pending_path(self):
        return self._paths.under("state", "webserver-apply-pending.json")

    def webserver_apply_pending(self) -> bool:
        """True while an Apply refused by the firewall gate has not completed since."""
        try:
            return self._ws_apply_pending_path().is_file()
        except (OSError, PathContainmentError):
            return False

    def _ws_policy_now(self) -> dict:
        """The desired console + proxy policy, non-secret, in the applied-snapshot vocabulary."""
        from . import webserver as _ws
        snap = _ws.applied_snapshot_of(self.config().webserver, self._stack_web_proxies())
        snap.pop("at", None)
        return snap

    def _ws_apply_pending_set(self) -> None:
        import json

        from . import runtime_fs
        try:
            runtime_fs.atomic_write(self._paths, self._ws_apply_pending_path(),
                                    json.dumps({"policy": self._ws_policy_now()}), 0o600)
        except (OSError, PathContainmentError):
            pass                                       # fail-soft: the refusal itself still shows

    def _ws_apply_pending_policy(self) -> dict | None:
        """The deferred policy, or None when the marker is missing or unreadable."""
        import json

        from . import runtime_fs
        try:
            rec = json.loads(runtime_fs.read_text_regular(
                self._paths, self._ws_apply_pending_path(), max_bytes=1 << 16) or "")
        except (OSError, ValueError, PathContainmentError):
            return None
        return rec.get("policy") if isinstance(rec, dict) else None

    def _ws_apply_pending_clear(self) -> None:
        try:
            self._ws_apply_pending_path().unlink(missing_ok=True)
        except OSError:
            pass

    def webserver_apply_complete_pending(self):
        """Watchdog completion of a gate-deferred Apply: once the firewall is verified against the
        CURRENT intent, run the apply the operator already confirmed. Returns that ActionResult,
        or None when nothing is pending or the firewall is not ready yet. Completes ONLY the
        policy that was deferred: if the desired config changed since, the marker is discharged
        and that config waits for its own Apply. The apply itself clears the marker once the
        gate lets it run."""
        if not self.webserver_apply_pending():
            return None
        if self._ws_apply_pending_policy() != self._ws_policy_now():
            self._ws_apply_pending_clear()
            return None
        st = self.firewall_status()
        if not (st.get("config_ok") and st.get("live_ok")):
            return None
        return self.webserver_apply()

    def webserver_verify(self) -> ActionResult:
        """Explicit verification: assemble + persist the effective-evidence checklist.

        Validates the SAME config `apply` would promote — stack web-UI proxies included. Verifying a
        console-only config and reporting "verified" would be a claim about a config nginx never loads.
        It does NOT advance the applied-policy snapshot: proving the desired config is valid says
        nothing about whether nginx ever loaded it."""
        ev = self._ws_verify(self.config().webserver, self._stack_web_proxies(),
                             probe_console=True)
        failed = [k for k, v in ev["checks"].items() if v == "failed"]
        ok = not failed
        summary = "webserver verified" if ok else f"verification found issues: {', '.join(failed)}"
        details = []
        for sid in ev["checks"].get("upstream_bypass_stacks", []):
            details.append(f"  WARNING: {sid}'s upstream port is listening on all interfaces — "
                           "reachable directly, bypassing this proxy's authentication.")
        return ActionResult(ok, summary, details=details, data=ev)

    def webserver_init(self, *, dns_sans=None, ip_sans=None, confirm=False) -> ActionResult:
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

    def webserver_configure(self, **fields) -> ActionResult:
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
                                  confirm=False, confirm_public=False) -> ActionResult:
        """Unified controller Settings action (the single 'Apply' button): derive `remote_exposed` from
        `bind`, gate remote exposure with `plan_exposure` (elevated confirm for public/no-auth/http), then
        — only on accept — save ALL fields in ONE write (incl. `remote_exposed` + `allowed_cidrs`), add the
        host IP SAN + reissue the server cert on exposure, and apply (staged validate + reload). On refusal
        it saves nothing and applies nothing. Folds in the former dedicated Remote-exposure form."""
        from . import config as _config
        from . import webserver as _ws
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

    def stack_web_pages(self, stack_id: str) -> tuple:
        """The stack's proxied web PAGES (`model.web_pages`): one per component that declares
        a client http/https endpoint, in manifest order (main last). The first keeps the stack
        id as its page id; any further one is `<stack_id>-<component_id>`. Empty for a stack
        without a web UI.

        A page exists in EVERY mode, like every stack's page exists while the stack is stopped:
        the operator configures the MeshCore repeater dashboard's proxy before switching the
        mode, and in `chat` nginx answers 502 for it exactly as for any stopped upstream. The
        panel says when the upstream is served (`page_mode_note`)."""
        from .model import web_pages
        s = self.stack(stack_id)
        return tuple(web_pages(s)) if s is not None else ()

    def page_mode_note(self, page) -> str:
        """One sentence when the page's upstream is NOT served in the saved MeshCore mode (the
        repeater dashboard in `chat`, the Web UI in `repeater`); "" for every other page."""
        from . import meshcore_mode as _mm
        if page.stack_id != _mm.STACK_ID:
            return ""
        comp = self.stack(page.stack_id).component(page.component_id)

        def served(mode: str) -> bool:
            if page.component_id in _mm.CLIENT_IDS:
                return _mm.clients_available(mode)              # the Web UI needs the Companion
            return any(e.address == page.address for e in _mm.expected_endpoints(comp, mode))

        mode = self.meshcore_mode_display()
        if not mode or served(mode):
            return ""
        modes = " and ".join(m for m in _mm.MODES if served(m))
        return (f"Not served in the current mode ({mode}): this upstream runs in the {modes} "
                f"modes — change it with the Mode switch on the stack page, then restart the stack.")

    def web_pages(self) -> list:
        """Every proxied page on the box, in MANIFEST order (stacks, then each stack's pages) —
        the order every rendered/persisted list keeps (nginx blocks, the applied snapshot the
        pending-apply marker is compared against). Port POSITIONS are a separate rule:
        `_page_positions`."""
        return [p for s in self.stacks() for p in self.stack_web_pages(s.id)]

    def _page_positions(self) -> list:
        """Page ids in port-suggestion order: the stacks' first pages sorted by id, THEN any
        further pages sorted by id — so a second page appearing in one stack never shifts another
        stack's suggested port (graywolf 8444, meshcom 8445, meshcore 8446, meshtastic 8447)."""
        pages = self.web_pages()
        return (sorted(p.page_id for p in pages if p.primary)
                + sorted(p.page_id for p in pages if not p.primary))

    def web_page(self, page_id: str):
        """The `WebPage` a page id names (a stack id names that stack's first page), or None."""
        return next((p for p in self.web_pages() if p.page_id == page_id), None)

    def stack_web_upstream(self, page_id: str):
        """(address, scheme) of a proxied page's web UI from the MANIFEST, or None when the id
        names no page. A stack id names the stack's first page.

        The upstream is evidence, never operator input: an `EndpointSpec` with `client=true` and an
        http/https scheme. This is what keeps `upstream_scheme` independent of the listener scheme."""
        p = self.web_page(page_id)
        return (p.address, p.scheme) if p is not None else None

    def stack_web_deny_paths(self, page_id: str) -> tuple:
        """Request paths the page's proxy must refuse (from the SAME manifest endpoint
        stack_web_upstream reads). Empty when the page declares none."""
        p = self.web_page(page_id)
        return tuple(p.deny_paths) if p is not None else ()

    def stack_web_eligible(self) -> list:
        """Page ids that can be proxied (derived from the manifest, never hardcoded), in
        manifest order."""
        return [p.page_id for p in self.web_pages()]

    def _stack_web_proxies(self) -> list:
        """The `StackWebProxy` list for nginx rendering — only pages with a port set (enabled),
        in manifest order."""
        from . import webserver as _ws
        cfgs = self.config().stackweb
        out = []
        for p in self.web_pages():
            swc = cfgs.get(p.page_id)
            if swc is not None and swc.enabled:
                out.append(_ws.StackWebProxy(swc, p.address, p.scheme, p.deny_paths))
        return out

    def _stack_listen_scope(self, swc, listeners=None) -> str:
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
        return _ws.listener_scope(self._system, swc.port, listeners)

    def ui_credentials_list(self, stack_id: str) -> list:
        """`ui_credentials` for EVERY component of the stack that declares a password file, each
        naming its component — the stack's Password sub-sections. Independent of web pages: a
        backend that mints the login for a sibling's UI still gets its section."""
        st = self.stack(stack_id)
        out = []
        for c in (st.components if st else ()):
            creds = self.ui_credentials(stack_id, c.id) if c.ui_password_file else {}
            if creds:                                   # {} = no login in the current mode
                out.append({"component_id": c.id, "name": c.name, **creds})
        return out

    def ui_credentials(self, stack_id: str, component_id: str = "") -> dict:
        """Where a stack's SELF-GENERATED web-UI password lives, and how to read it.

        Some apps mint their own credential on first start (graywolf does). LHPC stores the
        file but must never surface the value: a rendered page is copied into chats and
        screenshots, and a log is world-readable for longer than anyone expects. So this
        returns the account name and a copyable command the operator runs ON THE BOX — the
        secret stays on the box. `{}` when no component declares one. With `component_id`,
        ONLY that component's file counts — a proxied page must show the login of the component
        behind it, never a sibling's.
        """
        st = self.stack(stack_id)
        for c in (st.components if st else ()):
            if component_id and c.id != component_id:
                continue
            if not c.ui_password_file:
                continue
            path = self._paths.runtime_root / c.ui_password_file
            # `exists` lets the panel say "not created yet" instead of offering a `cat` of a
            # file that a first start (of a repeater mode, for the MeshCore repeater) will mint.
            return {"user": c.ui_user or "admin",
                    "path": str(path),
                    "command": f"cat {path}",
                    "exists": path.is_file(),
                    "note": c.ui_password_note}
        return {}

    def stack_web_views(self, stack_id: str, listeners=None, fw_status=None,
                        applied=None) -> list:
        """One `stack_web_view` per proxied page of the stack, in manifest order — the stack's
        Webserver sub-panels. `[]` for a stack without a web UI. The shared reads (/proc
        listeners, the applied snapshot) happen ONCE for all pages."""
        from . import webserver as _ws
        pages = self.stack_web_pages(stack_id)
        if not pages:
            return []
        listeners = self._listeners(listeners)
        if applied is None:
            applied = _ws.read_applied(self._paths)
        return [self.stack_web_view(p.page_id, listeners=listeners, fw_status=fw_status,
                                    applied=applied) for p in pages]

    def stack_web_view(self, page_id: str, listeners=None, fw_status=None,
                       applied=None) -> dict:
        """READ-ONLY view for ONE proxied page's Webserver panel (a stack id names the stack's
        first page). Includes the raw-port warning, which is evidence from THIS host
        (/proc/net/tcp), not a hardcoded per-stack fact.

        The security pill follows the SAME desired-vs-applied rule as the console (`posture_for`):
        a saved mode/scheme/auth/CIDR change does not reach nginx until Apply, so a LIVE proxy
        listener is coloured by the policy last activated — and after a saved PORT move the old
        listener is looked for on the applied port, not lost. `listeners`/`fw_status`/`applied`
        are the render-wide shared reads."""
        from . import webserver as _ws
        from .config import (
            STACKWEB_MODES,
            WEBSERVER_ACCESS_MODES,
            WEBSERVER_SCHEMES,
            StackWebConfig,
        )
        page = self.web_page(page_id)
        if page is None:
            return {}
        address, upstream_scheme = page.address, page.scheme
        swc = self.config().stackweb.get(page_id) or StackWebConfig(stack_id=page_id)
        ws = self.config().webserver
        used = {c.port for sid, c in self.config().stackweb.items()
                if sid != page_id and c.enabled}
        suggested = swc.port or self._default_stack_web_port(page_id, ws.port)
        try:
            upstream_port = int(str(address).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            upstream_port = 0
        listeners = self._listeners(listeners)
        scope = (_ws.listener_scope(self._system, upstream_port, listeners)
                 if upstream_port else "absent")
        if applied is None:
            applied = _ws.read_applied(self._paths)
        ap = _ws.applied_proxy(applied, page_id)
        # WHICH PORT does this proxy actually listen on? The desired port only becomes real at
        # Apply; until then the running nginx still holds the applied one, so probe both or a
        # saved port move hides a live listener behind a freshly-saved "local" intent.
        listen_scope = self._stack_listen_scope(swc, listeners)
        live_port = swc.port
        try:
            old_port = int(ap.get("port") or 0)
        except (TypeError, ValueError):
            old_port = 0
        if listen_scope != "exposed" and old_port and old_port != swc.port:
            old_scope = _ws.listener_scope(self._system, old_port, listeners)
            if old_scope != "absent":
                listen_scope, live_port = old_scope, old_port
        contained = (self._listener_restricted(live_port, listeners, fw_status)
                     if listen_scope == "exposed" else None)
        plan = _ws.plan_stack_exposure(swc, ws.port, used)
        return {
            # The page and the component behind it (`page_id` is the key of every saved policy).
            "page_id": page_id, "stack": page.stack_id, "component_id": page.component_id,
            "name": page.name, "label": page.label, "primary": page.primary,
            "mode_note": self.page_mode_note(page),
            "cfg": swc, "upstream_address": address,
            "upstream_scheme": upstream_scheme, "upstream_port": upstream_port,
            "upstream_scope": scope, "suggested_port": suggested,
            "modes": STACKWEB_MODES, "access_modes": WEBSERVER_ACCESS_MODES,
            "schemes": WEBSERVER_SCHEMES, "plan": plan,
            # The port that ANSWERS, so a saved-but-unapplied move never advertises a dead URL.
            "urls": (_ws.stack_ui_urls(swc, live_port if listen_scope != "absent" else None)
                     if swc.enabled else []),
            # Is the raw upstream port genuinely reachable AROUND this proxy? Off-loopback alone
            # is not enough — a deny-default endpoint the VERIFIED firewall drops bypasses nothing.
            "bypassable": self._upstream_bypassable(upstream_port, listeners, fw_status),
            # EFFECTIVE listen scope of the proxy port + whether it disagrees with the desired mode
            # (i.e. an Apply is still pending to make the live listener match the saved intent).
            "listen_scope": listen_scope,
            "live_port": live_port,
            # A saved PORT move is pending exactly like a saved MODE move: the listener is live and
            # healthy, just not on the port the operator now asks for. Without this the panel read
            # "in sync" while nginx still served the old socket.
            "pending": bool(swc.enabled and (
                listen_scope == "absent" or swc.remote != (listen_scope == "exposed")
                or live_port != swc.port)),
            # Security + running posture for the two summary pills. Security via posture_for() — the
            # LIVE listener's applied policy, never a saved-but-unapplied one; the RUNNING pill for a
            # PROXY is: grey "offline" (stack not started — its web-UI upstream is down), yellow
            # "local-only" (started but nginx is not proxying it), green "proxied" (started + nginx).
            "posture": {
                **_ws.posture_for({"local": swc.mode == "local", "public": swc.mode == "public",
                                   "access_mode": swc.access_mode,
                                   "has_cidrs": bool(swc.allowed_cidrs), "scheme": swc.scheme},
                                  ap, listen_scope, firewall_contained=contained),
                "run": "offline" if scope == "absent" else (
                    "local-only" if listen_scope == "absent" else "proxied"),
                "run_level": "off" if scope == "absent" else (
                    "warn" if listen_scope == "absent" else "ok"),
            },
            # Same remote-exposure/auth/listener warnings the console shows (identical wording+values),
            # for an ENABLED proxy. A proxy binds 0.0.0.0 when remote, 127.0.0.1 when local.
            # Name the socket that EXISTS: with a live listener, its bind/port are the APPLIED ones.
            "warnings": _ws.exposure_warnings(
                remote=swc.remote, access_mode=swc.access_mode, allowed_cidrs=swc.allowed_cidrs,
                bind=(ap.get("bind") if (ap and listen_scope in ("exposed", "loopback"))
                      else ("0.0.0.0" if swc.remote else "127.0.0.1")),
                port=live_port, live_scope=listen_scope) if swc.enabled else [],
        }

    def dashboard_webservers(self, served_via_nginx: bool | None = None,
                             fw_status=None) -> list[dict]:
        """Rows for the dashboard Webserver box: the console (LHCP) ALWAYS, then — for each stack whose
        MAIN component is running/degraded — its web-UI row (http/https) followed by a row per OTHER
        open TCP port (kiss/meshcore/meshtastic; no auth). Structural evidence only — the adapter adds
        the request-scoped reached address. A running-but-not-proxied web UI carries `direct_port`/
        `direct_scheme`; a `kind="port"` row carries `exposure` (level+label from its bind allow-list)
        and `logs_component` for a per-service log link."""
        from .model import RunState
        up = (RunState.RUNNING, RunState.DEGRADED)
        try:
            # ONE /proc/net/tcp read for the whole REQUEST (shared with the component pins)
            snap = self._request_memo(("tcp-listeners",), self._system.procfs.tcp_listeners)
        except Exception:
            snap = []
        if fw_status is None:
            try:
                # ONE firewall read for the whole render, like `snap` above: firewall_status()
                # re-reads the receipt and re-hashes the intent, which is measurable per row on a
                # Zero 2W. The dashboard route hands in the read it already made for its own box.
                fw_status = self.firewall_status()
            except Exception:
                fw_status = None                            # -> containment "unknown", conservative
        # ONE /proc cmdline scan at most, and only if a row actually needs it: `cmdlines()` lists
        # /proc and reads a file per PID, and most rows are fixed-port endpoints that never ask.
        argv_cache: dict = {}

        def _argvs():
            if not argv_cache:
                try:
                    argv_cache.update(self._system.procfs.cmdlines() or {})
                except Exception:
                    pass
                argv_cache.setdefault(-1, [])               # mark as attempted
            return argv_cache
        mon = self.webserver_monitor(served_via_nginx=served_via_nginx,
                                     listeners=snap, fw_status=fw_status).data or {}
        rows: list[dict] = [{"kind": "console", "name": "LHCP", "posture": mon.get("posture"),
                             # The port a listener was actually FOUND on — after a saved port move
                             # that is the old one, and it is what a browser still reaches.
                             "port": mon.get("live_port") or mon.get("desired", {}).get("port"),
                             "logs_component": None}]
        by_id = {ss.stack.id: ss for ss in self.build_snapshot().stacks}
        for stk in self.stacks():
            ss = by_id.get(stk.id)
            if ss is None or stk.main_component is None:
                continue
            mst = ss.components.get(stk.main_component.id)
            if mst is None or mst.run_state not in up:      # not started -> no rows (per the operator)
                continue
            for page in self.stack_web_pages(stk.id):          # one row per proxied page
                rows.append(self._dashboard_web_row(stk, page, snap, fw_status))
            for comp in stk.components:                      # every OTHER open port (no-auth tcp)
                for ep in comp.endpoints:
                    if self._is_raw_endpoint(ep):
                        rows.append(self._dashboard_port_row(
                            stk, comp, ep, snap, fw_status,
                            comp_state=ss.components.get(comp.id), get_argvs=_argvs))
        return rows

    @staticmethod
    def _is_raw_endpoint(ep) -> bool:
        """Does this endpoint deserve its own RAW direct-listener security row?

        Network ports only (host:port). Non-network client endpoints — the KISS socat PTY
        (scheme="serial", a local device path like "state/loraham_kiss") — are filesystem devices,
        not interfaces to advertise in a network-exposure box.

        A web SCHEME is not a reason to skip the raw verdict. Meshtastic's :9443 is `https` and has
        no authentication and no bind knob, so it is exactly as directly reachable as its :4403
        API; excluding it by scheme left the box asserting containment it had never checked. The
        rule is METADATA, not a stack name: any no-auth endpoint carrying managed-firewall
        semantics gets a raw row, whatever its scheme. The friendly web-UI/proxy row is unaffected
        and still rendered separately — one is the front door, this is the listener itself."""
        if not getattr(ep, "client", False) or ":" not in ep.address:
            return False
        if ep.scheme not in ("http", "https"):
            return True
        meta = getattr(ep, "firewall", None)
        return meta is not None and getattr(meta, "auth", "") == "none"

    def _dashboard_web_row(self, stk, page, listeners=None, fw_status=None) -> dict:
        """The web-UI (http/https) box row for ONE proxied page: proxied port + posture, or the
        DIRECT listen address when the reverse proxy is not enabled (the adapter reattaches the
        reached host, like the console). A stack's further pages carry the component's name."""
        v = self.stack_web_view(page.page_id, listeners=listeners, fw_status=fw_status) or {}
        swc = v.get("cfg")
        enabled = bool(swc and swc.enabled)
        # The DIRECT (un-proxied) web port and its live bind scope — the view already derived both
        # from the same address and listener snapshot (`upstream_port`/`upstream_scope`). The adapter
        # links to the request host only when it is genuinely exposed, else 127.0.0.1 (a loopback-only
        # web UI must not be a dead remote link); the proxied path keys off posture instead.
        direct_port = str(v.get("upstream_port") or "")
        direct_scope = v.get("upstream_scope", "absent")
        # The port a browser can actually REACH. After a saved-but-unapplied port move the running
        # nginx still holds the old one, so `live_port` is what the address must name — advertising
        # the desired port would hand out a socket nobody listens on. Desired config is untouched
        # and still reaches Settings through `cfg`; with no live listener there is nothing to
        # correct, so the desired port stands.
        listen_scope = v.get("listen_scope") if enabled else None
        port = None
        if enabled:
            port = (v.get("live_port") or swc.port) if listen_scope != "absent" else swc.port
        return {"kind": "stack", "name": page.label, "sid": stk.id, "pid": page.page_id,
                "anchor": page.anchor, "enabled": enabled,
                "mode_note": v.get("mode_note", ""),      # "" unless the mode hides the upstream
                "posture": v.get("posture") if enabled else None,
                "port": port,
                # The proxy's LIVE listen scope (exposed|loopback|absent) — the adapter links to the
                # proxy socket only where it actually listens, so an enabled-but-local-only or inactive
                # proxy never renders a dead request-host link.
                "listen_scope": listen_scope,
                "direct_port": direct_port, "direct_scheme": page.scheme,
                "direct_scope": direct_scope}

    def _endpoint_bind_host(self, stk_id: str, comp, ep) -> str | None:
        """The component's configured BIND HOST, from the endpoint's declared firewall metadata
        (`bind_param`) — not from scanning for a `validator="bind"` param, which finds the
        ALLOW-LIST instead. KISS declares both: `kiss_host` is the listen address, `kiss_bind` the
        accepted-source filter. Returns None when the endpoint declares no bind_param (MeshCore
        declares only allow_param)."""
        meta = getattr(ep, "firewall", None)
        name = getattr(meta, "bind_param", "") if meta is not None else ""
        if not name:
            return None
        for kind, params in (("run", comp.run_params),
                             ("file", comp.config_file.params if comp.config_file else ())):
            for p in params:
                if p.name == name:
                    return self._resolved_param_value(
                        stk_id, kind, comp.id, p.name, self._config_band(stk_id, ""))
        return None

    def _bind_exposure(self, stk_id: str, comp) -> tuple[str, str, bool]:
        """Exposure (level, label, has_bind_control) from the component's bind ALLOW-LIST param
        (`validator="bind"`). Only one caller (`_dashboard_port_row`); the third element says the
        service has an allow-list control at all, which drives its log link and restart marker.

        This answers "who is permitted to connect", NOT "where is the socket". A loopback socket is
        unreachable off-box however broad the allow-list is, and a wildcard socket is reachable
        however narrow it is — so the caller must combine this with live listener evidence."""
        from .webserver import port_exposure
        for kind, params in (("run", comp.run_params),
                             ("file", comp.config_file.params if comp.config_file else ())):
            for p in params:
                if getattr(p, "validator", "") == "bind":
                    val = self._resolved_param_value(
                        stk_id, kind, comp.id, p.name, self._config_band(stk_id, ""))
                    level, label = port_exposure(val)
                    return level, label, True
        return "bad", "public", False

    def _endpoint_live_port(self, comp, ep, pids, get_argvs) -> tuple:
        """`(port, verified)` — the port this endpoint's component is ACTUALLY listening on.

        A `port_param` endpoint MOVES. `kiss_port` is a start-time parameter and Start-without-
        saving is supported, so the manifest address and the saved parameter are both DESIRED
        values: the running process keeps whatever it was launched with. Read the port from that
        component's own live argv instead, structurally — the RunParam's exact `arg` token, in
        either `--arg value` or `--arg=value` form. Never search a joined command line: a path,
        another flag's value, or a neighbouring process would match by accident.

        UNVERIFIED (`verified=False`) whenever the answer is not unambiguous: any live PID with
        unreadable argv, no live PID carrying the flag at all, a non-numeric value, or several
        live PIDs naming different ports. (One PID carrying it is enough — a sibling that does
        not, such as a helper sharing the component, does not make the answer ambiguous.)
        Guessing the saved port instead would attach a firewall verdict to a socket that may not
        exist, which is precisely the false-safe this replaces.

        A fixed-port endpoint (no `port_param`) keeps its manifest port, and so does a component
        with no live PIDs — there is no running listener to misrepresent."""
        static = ep.address.rsplit(":", 1)[-1] if ":" in ep.address else ep.address
        static_port = int(static) if str(static).isdigit() else None
        meta = getattr(ep, "firewall", None)
        name = getattr(meta, "port_param", "") if meta is not None else ""
        if not name or not pids:
            return static_port, True
        arg = next((p.arg for p in comp.run_params if p.name == name and p.arg), "")
        if not arg:
            return static_port, False       # declared movable, but not observable from argv
        argvs = get_argvs() if callable(get_argvs) else (get_argvs or {})
        found: set = set()
        for pid in pids:
            toks = list(argvs.get(pid) or ())
            if not toks:
                return static_port, False   # a live PID whose argv we cannot read
            for i, t in enumerate(toks):
                if t == arg and i + 1 < len(toks):
                    found.add(toks[i + 1])
                elif t.startswith(arg + "="):
                    found.add(t.split("=", 1)[1])
        if len(found) != 1:
            return static_port, False       # flag absent, or the live PIDs disagree
        val = found.pop()
        return (int(val), True) if val.isdigit() else (static_port, False)

    def _dashboard_port_row(self, stk, comp, ep, listeners=None, fw_status=None,
                            comp_state=None, get_argvs=None) -> dict:
        """A `kind="port"` box row for a no-authentication TCP service (any scheme — an https
        listener with no auth and no bind knob is exactly as directly reachable as a tcp one).

        The exposure verdict is decided HERE, once, and `security_pill()` only aggregates it.
        Order of truth:

        1. WHICH PORT — `_endpoint_live_port`. The static manifest address is desired config, not
           evidence, for any endpoint whose port is a start-time parameter. Ambiguity yields
           `live_scope="unverified"`: a conservative review that never reads green or local.
        2. WHICH SCOPE — the LIVE socket (/proc) on that port. Saved config can disagree (a
           Start-without-saving, an out-of-band edit, plain drift), so a live non-loopback
           listener is never labelled `local`, and a live loopback socket IS local however broad
           the allow-list is. A bind host that cannot be parsed (or `::`, which also accepts
           IPv4) counts as NON-loopback: never claim safety that has not been shown.
        3. WHICH COLOUR — a LIVE non-loopback no-auth listener is red `public` by default. Only
           the VERIFIED firewall may improve that: fully covering and denying the live scope
           reads green `firewalled`; covering and restricting it reads yellow `LAN`. Everything
           else — open, unknown, unverified, transitional, an overlapping `extra_allow` — stays
           red. The SAVED allow-list is NOT running enforcement: the process keeps its launch-time
           policy until it restarts, so the allow-list colours only an endpoint with NO live
           listener, where it is plainly desired-config information.

        `restart_required` and `logs_component` still key off the allow-list control, so the set
        of services with a log link and a restart hint is unchanged.
        These ports have no auth, so the colour is the whole warning."""
        from .model import RunState
        level, label, has_bind = self._bind_exposure(stk.id, comp)
        static = ep.address.rsplit(":", 1)[-1] if ":" in ep.address else ep.address
        live = (comp_state is not None
                and comp_state.run_state in (RunState.RUNNING, RunState.DEGRADED))
        pids = list(comp_state.pids or ()) if live else []
        port, verified = self._endpoint_live_port(comp, ep, pids, get_argvs)

        def row(port_shown, live_scope, level, label, warn_reason):
            return {"kind": "port", "name": stk.name, "sid": stk.id, "port": str(port_shown),
                    "anchor": f"#stack-webserver-{stk.id}",
                    "scheme": ep.scheme or "tcp", "live_scope": live_scope,
                    "exposure": {"level": level, "label": label},
                    "warn_reason": warn_reason,
                    "restart_required": bool(self.restart_required(stk.id)) if has_bind else False,
                    "logs_component": comp.id if has_bind else None}

        if not verified:
            # A RUNNING component whose live port cannot be pinned down. This is NOT "absent" —
            # something IS listening somewhere — and it is not safe either. Say so.
            return row(static, "unverified", "warn", "unverified", "review")

        scopes = self.listener_scopes(port, listeners) if port else []
        if not scopes:
            # Nothing is listening on it. The allow-list is all there is to show, and an absent
            # listener contributes no reachability at all to the summary.
            bind_host = self._endpoint_bind_host(stk.id, comp, ep)
            if (level == "ok" and bind_host is not None
                    and not self.scope_is_loopback({"addr": str(bind_host).strip()})):
                # The configured bind host is non-loopback (or unparseable/`::`) — do not
                # present it as local even while it is down.
                level, label = "warn", "LAN"
            return row(port or static, "absent", level, label, "")
        if all(self.scope_is_loopback(s) for s in scopes):
            return row(port, "loopback", "ok", "local", "")
        # Live and reachable off-box, with no authentication in front of it.
        verdict = self.firewall_containment(scopes, fw_status)
        if verdict == "denied":
            return row(port, "exposed", "ok", "firewalled", "")
        if verdict == "restricted":
            return row(port, "exposed", "warn", "LAN", "restricted_noauth")
        return row(port, "exposed", "bad", "public", "review")

    def _default_stack_web_port(self, page_id: str, console_port: int,
                                extra_taken=()) -> int:
        """A STABLE per-page default port: `console_port + 1 + position`, where position is the
        page's index in `_page_positions()` — the stacks' first pages sorted by id, then any
        further pages (so a stack growing a second page shifts nobody else). So graywolf → 8444,
        meshcom → 8445, meshcore → 8446, meshtastic → 8447, deterministically and without
        colliding — the old 'first free above the console' gave every not-yet-enabled stack the
        SAME port (8444), so accepting two suggestions collided.

        A default is only ever WRITTEN when the operator saves the panel; an untouched stack keeps
        no port key, so a fresh deployment's rendered nginx stays unchanged.

        Positional stability holds only while the eligible SET does not change. Adding a stack
        whose id sorts earlier shifts everyone after it, and on an upgraded box the shifted
        suggestion can land on a port another stack has already SAVED — the prefill would then be
        refused at Apply ("already used by another stack's web UI"), i.e. a one-click default that
        cannot be accepted. So ports already claimed by another stack are skipped: the positional
        value is the starting point, not the answer."""
        positions = self._page_positions()
        pos = positions.index(page_id) if page_id in positions else 0
        saved = self.config().stackweb
        taken = {int(cfg.port) for sid, cfg in saved.items()
                 if sid != page_id and getattr(cfg, "port", 0)}
        taken |= {int(p) for p in extra_taken}      # bulk path: candidates assigned so far
        port = min(max(console_port, 1023) + 1 + pos, 65535)
        while port in taken or port == console_port:
            if port >= 65535:
                return 65535
            port += 1
        return port

    @staticmethod
    def _exposure_missing(plan, *, confirm, confirm_public, cidr_flag) -> list:
        """Aggregate EVERY unmet exposure requirement into ONE list (a single refusal names them
        all — the old serial errors cost the operator repeated round-trips): the plan's problems
        (with the exact flag hint for the CIDR case) plus the confirmation the requested policy
        needs. The strong `enable-remote-danger` phrase covers public CIDRs (0.0.0.0/0), no-auth,
        AND remote plain HTTP; plain lan exposure takes `enable-remote`."""
        missing = []
        for p in plan["problems"]:
            missing.append(f"{p}: {cidr_flag}" if "source CIDR" in p else p)
        if plan["remote"]:
            if plan["danger"] == "elevated":
                if not confirm_public:
                    what = ("a public source range (0.0.0.0/0)" if plan["public"]
                            else "no client authentication" if plan["no_auth"]
                            else "an unencrypted (http) listener")
                    missing.append(f"elevated confirmation required ({what}): "
                                   "--confirm-phrase enable-remote-danger")
                    if plan["no_auth"]:
                        # The danger phrase is the wrong first answer here: the operator usually
                        # wants the listener AUTHENTICATED, not the warning waived. Name that way
                        # out too — live-found, where the documented proxy recipe hit this refusal
                        # and reaching for the danger phrase would have exposed an unauthenticated
                        # meshtasticd UI to the LAN.
                        missing.append("or keep the client-certificate requirement instead: "
                                       "--auth local-open-remote-auth")
            elif not confirm:
                missing.append("confirmation required: --confirm-phrase enable-remote")
        return missing

    def _stack_web_saved_details(self, page_id: str, saved) -> list:
        """THE post-save presentation for one stack's web-UI proxy — the standing bypass
        disclosure (warning + firewall remedy) and the proxy URLs. Single-stack and bulk
        saves both render through here, so the two can never diverge in what they name."""
        from . import webserver as _ws
        details: list = []
        view = self.stack_web_view(page_id)
        if view.get("bypassable"):
            details.append(
                f"  WARNING: {page_id}'s upstream port {view['upstream_port']} is listening on all "
                f"interfaces — it is reachable directly, bypassing this proxy's authentication.")
            details.append("  The managed firewall can close this port (Dashboard -> Webserver "
                           "-> Firewall), or accept the exposure.")
        if saved.enabled:
            details += [f"  {u}" for u in _ws.stack_ui_urls(saved)]
        return details

    def stack_web_configure(self, page_id: str, *, mode=None, port=None, scheme=None,
                            access_mode=None, cidrs=None, confirm=False,
                            confirm_public=False) -> ActionResult:
        """Persist ONE proxied page's web-UI proxy policy (a stack id names the stack's first
        page). Mirrors `webserver_expose`'s two-level confirmation. Writes INTENT only —
        activation is `lhpc webserver apply`."""
        from . import config as _config
        from . import webserver as _ws
        from .config import StackWebConfig
        from .validators import ValidationError
        if self.web_page(page_id) is None:
            return ActionResult(False, f"'{page_id}' names no web UI to proxy",
                                details=["proxied pages: " + (", ".join(self.stack_web_eligible())
                                                              or "none")])
        ws = self.config().webserver
        current = self.config().stackweb.get(page_id) or StackWebConfig(stack_id=page_id)
        used = {c.port for sid, c in self.config().stackweb.items() if sid != page_id and c.enabled}
        probe = StackWebConfig(
            stack_id=page_id,
            mode=current.mode if mode is None else mode,
            port=current.port if port is None else int(port),
            scheme=current.scheme if scheme is None else scheme,
            access_mode=current.access_mode if access_mode is None else access_mode,
            allowed_cidrs=current.allowed_cidrs if cidrs is None else tuple(cidrs))
        plan = _ws.plan_stack_exposure(probe, ws.port, used)
        missing = self._exposure_missing(plan, confirm=confirm, confirm_public=confirm_public,
                                         cidr_flag="--cidr <net>  (e.g. --cidr 192.168.0.0/24)")
        if missing:
            return ActionResult(False, f"cannot configure '{page_id}' web UI — unmet "
                                "requirement(s):", details=[f"  - {m}" for m in missing])
        try:
            _config.save_stackweb_config(self._paths, page_id, mode=mode, port=port, scheme=scheme,
                                         access_mode=access_mode, allowed_cidrs=cidrs)
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid web-UI config: {exc}")
        self._invalidate_config()
        details = self._stack_web_saved_details(page_id, probe)
        details.append("lhpc webserver apply           # render + validate + reload nginx")
        return ActionResult(True, f"web UI proxy for '{page_id}' saved (desired; run apply)",
                            details=details, next_commands=["lhpc webserver apply"])

    def stack_web_configure_apply(self, page_id: str, **kwargs) -> ActionResult:
        """Unified per-page Settings action (the single 'Apply' button): save this proxy's policy (with
        its two-level typed confirmation) then apply (staged validate + reload). Save-only failures (incl.
        a needed confirmation) short-circuit — nothing is applied."""
        r = self.stack_web_configure(page_id, **kwargs)
        if not r.ok:
            return r
        ar = self.webserver_apply()
        return ActionResult(ar.ok, ar.summary, details=[*r.details, *ar.details],
                            next_commands=ar.next_commands, data=ar.data)

    def _stack_webs_candidates(self) -> list:
        """THE candidate port walk for the 'Stacks WebGUIs' bulk policy — the ONE place that
        decides, per eligible stack, which port a bulk Apply uses: the saved nonzero port
        (kept), else the same suggested default the per-stack panel offers, candidate-aware so
        the assigned set stays unique. The form's overview and the bulk Apply both consume
        this, so the listed suggestions are STRUCTURALLY the exact ports an Apply assigns.
        Returns [(page_id, port, keeps)] in `_page_positions()` order (stacks' first pages
        first)."""
        ws = self.config().webserver
        cfgs = self.config().stackweb
        out, assigned = [], []
        for sid in self._page_positions():
            swc = cfgs.get(sid)
            port = int(getattr(swc, "port", 0) or 0)
            if not port:
                port = self._default_stack_web_port(sid, ws.port, extra_taken=assigned)
                assigned.append(port)
            out.append((sid, port, bool(getattr(swc, "port", 0))))
        return out

    def stack_webs_overview(self) -> dict:
        """READ-ONLY context for the 'Stacks WebGUIs' bulk form (the shared candidate walk)."""
        from .config import STACKWEB_MODES, WEBSERVER_ACCESS_MODES, WEBSERVER_SCHEMES
        pages = {p.page_id: p for p in self.web_pages()}
        return {"stacks": [{"sid": sid, "port": port, "keeps": keeps, "label": pages[sid].label}
                           for sid, port, keeps in self._stack_webs_candidates()],
                "modes": STACKWEB_MODES,
                "access_modes": WEBSERVER_ACCESS_MODES, "schemes": WEBSERVER_SCHEMES}

    def stack_webs_configure_apply(self, *, mode, scheme, access_mode, cidrs,
                                   confirm=False, confirm_public=False) -> ActionResult:
        """Apply ONE common web-UI proxy policy to EVERY eligible stack — the 'Stacks WebGUIs'
        bulk form. Ports stay per-stack: an existing nonzero port is NEVER changed; a missing one
        gets the same suggested default its own panel offers (candidate-aware, so the assigned
        set stays unique). The COMPLETE candidate set is validated through the same rules as a
        single-stack save (`plan_stack_exposure` + `_exposure_missing`, every problem collected,
        sid-prefixed); any problem refuses the whole set and saves NOTHING. On accept the
        per-stack patches are persisted in ONE locked atomic write (`save_stackweb_configs`),
        the config cache is invalidated once, and `webserver_apply()` runs exactly once."""
        from . import config as _config
        from . import webserver as _ws
        from .config import StackWebConfig
        from .validators import ValidationError
        eligible = self.stack_web_eligible()
        if not eligible:
            return ActionResult(False, "no stacks with a web UI to configure")
        cidrs = list(cidrs or [])
        # ONE config_lock spans candidate calculation, conflict validation AND the write, on a
        # FRESHLY-loaded snapshot (audit-found TOCTOU: candidates computed from the memoized
        # config could stamp a concurrently-edited port back to its stale value — "existing
        # nonzero ports are never changed" must hold against the configuration AT WRITE TIME).
        # webserver_apply stays outside the lock: it re-reads desired config itself.
        try:
            with _config.config_lock(self._paths):
                self._invalidate_config()          # every read below: fresh + lock-consistent
                ws = self.config().webserver
                cfgs = self.config().stackweb
                keeps_port = {}
                candidates: dict = {}
                for sid, port, keeps in self._stack_webs_candidates():
                    keeps_port[sid] = keeps
                    candidates[sid] = StackWebConfig(stack_id=sid, mode=mode, port=port,
                                                     scheme=scheme, access_mode=access_mode,
                                                     allowed_cidrs=tuple(cidrs))
                # Saved enabled entries of stacks OUTSIDE the candidate set still hold their
                # ports — the single-stack path counts every saved enabled entry, so the bulk
                # path must too (a lingering entry for a currently-ineligible stack would
                # otherwise collide later).
                saved_other = {c.port for o, c in cfgs.items()
                               if o not in candidates and c.enabled}
                missing_all: list = []
                for sid, probe in candidates.items():
                    others = saved_other | {c.port for o, c in candidates.items()
                                            if o != sid and c.enabled}
                    plan = _ws.plan_stack_exposure(probe, ws.port, others)
                    missing = self._exposure_missing(
                        plan, confirm=confirm, confirm_public=confirm_public,
                        cidr_flag="--cidr <net>  (e.g. --cidr 192.168.0.0/24)")
                    missing_all += [f"{sid}: {m}" for m in missing]
                if missing_all:
                    return ActionResult(False, "cannot configure the stack web UIs — unmet "
                                        "requirement(s):",
                                        details=[f"  - {m}"
                                                 for m in dict.fromkeys(missing_all)])
                try:
                    _config.save_stackweb_configs(self._paths, {
                        sid: {"mode": mode, "port": c.port, "scheme": scheme,
                              "access_mode": access_mode, "allowed_cidrs": cidrs}
                        for sid, c in candidates.items()}, hold_lock=False)
                except (ValidationError, _config.ConfigError) as exc:
                    return ActionResult(False, f"invalid web-UI config: {exc}")
        except _config.ConfigLockBusy as exc:
            return ActionResult(False, f"configuration is busy — try again shortly ({exc})")
        self._invalidate_config()
        details = [f"  {sid}: port {c.port}" + ("" if keeps_port[sid]
                                                else " (assigned default)")
                   for sid, c in candidates.items()]
        # Same post-save presentation as the single-stack path (shared helper).
        for sid, c in candidates.items():
            details += self._stack_web_saved_details(sid, c)
        ar = self.webserver_apply()
        return ActionResult(ar.ok, f"common policy saved for {len(candidates)} stack web "
                            f"UI(s). {ar.summary}",
                            details=[*details, *ar.details],
                            next_commands=ar.next_commands, data=ar.data)

    def webserver_expose(self, cidrs, *, access_mode=None, confirm=False,
                         confirm_public=False) -> ActionResult:
        """Enable remote exposure. Requires >=1 CIDR; a public default route (0.0.0.0/0) or
        a no-auth remote mode needs elevated confirmation. Writes desired config only — the
        listener is not proven active until verify/apply."""
        from . import config as _config
        from . import webserver as _ws
        from .config import WebserverConfig
        from .validators import ValidationError
        cidrs = list(cidrs or [])
        ws_now = self.config().webserver
        mode = access_mode or ws_now.access_mode
        # BUG FIX (live find): the probe MUST carry the CURRENT CONFIGURED scheme — the dataclass
        # default is https, so an http deployment's exposure used to be assessed as encrypted and
        # the cleartext elevation never fired.
        probe = WebserverConfig(bind="0.0.0.0", port=ws_now.port, scheme=ws_now.scheme,
                                access_mode=mode, remote_exposed=True,
                                allowed_cidrs=tuple(cidrs))
        plan = _ws.plan_exposure(probe)
        missing = self._exposure_missing(plan, confirm=confirm, confirm_public=confirm_public,
                                         cidr_flag="--cidr <net>  (e.g. --cidr 192.168.0.0/24)")
        if missing:
            return ActionResult(False, "cannot enable remote exposure — unmet requirement(s):",
                                details=[f"  - {m}" for m in missing])
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
        from . import config as _config
        from . import pki as _pki
        from . import webserver as _ws
        cfg = self.config().webserver                    # FRESH: post-exposure-write state
        ip = _ws.local_ip()
        if not ip:
            return ["  SAN: this host's LAN address could not be determined — no SAN added; add it "
                    "by hand to [webserver] ip_sans, then: lhpc webserver tls-renew"]
        if ip in cfg.ip_sans:
            return [f"  SAN: {ip} is already an IP SAN — certificate left untouched"]
        try:
            _config.save_webserver_config(self._paths, ip_sans=[*cfg.ip_sans, ip])
        except Exception as exc:
            return [f"  SAN: could not persist {ip} as an IP SAN ({exc}) — add it by hand, then: "
                    "lhpc webserver tls-renew"]
        self._invalidate_config()
        cfg = self.config().webserver                    # FRESH again: the cert follows what is on disk
        try:
            _pki.issue_server_cert(self._paths, dns_sans=list(cfg.dns_sans),
                                   ip_sans=list(cfg.ip_sans), days=cfg.server_cert_days)
        except Exception as exc:
            return [f"  SAN: {ip} added to ip_sans, but the certificate was NOT reissued ({exc})",
                    "       run: lhpc webserver init   # then: lhpc webserver tls-renew"]
        return [f"  SAN: {ip} added to ip_sans and the server certificate was reissued for it"]

    def webserver_disable_remote(self) -> ActionResult:
        from . import config as _config
        _config.save_webserver_config(self._paths, bind="127.0.0.1", remote_exposed=False)
        self._invalidate_config()
        return ActionResult(True, "remote exposure disabled (bind reset to loopback) — "
                            "verify to prove the remote listener has ceased",
                            next_commands=["lhpc webserver verify"])

    def webserver_reset_defaults(self) -> ActionResult:
        """Reset to safe defaults AND prove remote exposure has ceased. Writes DESIRED defaults
        (loopback:8443, local unauthenticated, remote off, CIDRs cleared), stages + VALIDATES a
        loopback-only nginx config, and — if a proven LHPC-owned nginx master exists — reloads
        it (a successful reload of the loopback-only config is the cessation proof: the new
        config has no remote listener). Reports success ONLY when cessation is proven; otherwise
        stays truthful ('reset requested; remote cessation unproven'). NEVER deletes CA keys,
        certificates, CRL, revocation history, `.p12` exports, or the session secret."""
        from . import config as _config
        from . import webserver as _ws
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
        ev = self._ws_verify(cfg, proxies)
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
                    # Reset is an ACTIVATION: the loopback-only config was promoted, reloaded and
                    # proven, so it becomes the applied policy. (After write_evidence above, which
                    # would otherwise overwrite it.)
                    self._record_applied(cfg, proxies)
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

    def webserver_tls_renew(self) -> ActionResult:
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.issue_server_cert(self._paths, dns_sans=list(cfg.dns_sans),
                                          ip_sans=list(cfg.ip_sans), days=cfg.server_cert_days)
        except _pki.PKIError as exc:
            return ActionResult(False, f"server certificate renewal failed: {exc}")
        return ActionResult(True, f"server certificate renewed (serial {summ['serial']})",
                            data=summ)

    def webserver_cert_issue(self, label, passphrase) -> ActionResult:
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

    def webserver_cert_reissue(self, label, passphrase) -> ActionResult:
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.reissue_client_cert(self._paths, label, days=cfg.client_cert_days,
                                            passphrase=passphrase)
        except Exception as exc:
            return ActionResult(False, f"reissue failed: {exc}")
        return ActionResult(True, f"reissued client certificate '{summ['label']}'", data=summ)

    def webserver_cert_list(self) -> ActionResult:
        from . import pki as _pki
        return ActionResult(True, "client certificates",
                            data={"certs": _pki.list_client_certs(self._paths)})

    def webserver_cert_revoke(self, label) -> ActionResult:
        from . import pki as _pki
        try:
            _pki.revoke_client_cert(self._paths, label)
        except Exception as exc:
            return ActionResult(False, f"revoke failed: {exc}")
        return ActionResult(True, f"revocation RECORDED for '{label}' and CRL regenerated — "
                            "not proven effective until the proxy reloads and rejects it",
                            next_commands=["lhpc webserver verify"])

    def webserver_cert_discard_export(self, label) -> ActionResult:
        from . import pki as _pki
        removed = _pki.discard_export(self._paths, label)
        return ActionResult(True, f"export {'discarded' if removed else 'already absent'} for '{label}'")

    def webserver_applied_access_mode(self) -> str:
        """The console access mode nginx last ACTIVATED — '' when unknown. The FALLBACK for
        the /stacks fetch-command gate when the monitor itself failed for unrelated reasons
        (the primary source is `applied_access_mode` in the monitor view); fail-closed either
        way. REVIEW-FOUND origin: gating on `desired` treated an unauthenticated remote client
        as trusted the moment the operator SAVED a cert policy, before Apply enforced it."""
        from . import webserver as _ws
        try:
            return str((_ws.read_applied(self._paths).get("console") or {})
                       .get("access_mode") or "")
        except (OSError, PathContainmentError):
            return ""

    def webserver_server_ca_bytes(self) -> bytes | None:
        """The server TLS CA certificate, or None. PUBLIC material (no key) — the browser
        download that phones can actually use; the scp copybox stays for PCs."""
        from . import runtime_fs
        try:
            return runtime_fs.read_bytes(
                self._paths, self._paths.under("config", "tls", "server-ca", "ca.crt"))
        except (OSError, PathContainmentError):
            return None

    def webserver_cert_export_bytes(self, label) -> bytes | None:
        """Raw `.p12` bytes for a label (or None). The WEB route must gate this on a
        loopback-origin session; the CLI locates the file directly."""
        from . import pki as _pki
        return _pki.read_export(self._paths, label)

    @staticmethod
    def _with_gate_note(res, gate_msg: str, gate_cmds) -> ActionResult:
        """Carry an ALLOWED gate's warning (and its remedy) onto whatever result the operation
        produced. The gate can permit an exposure REDUCTION while reporting that the firewall
        scripts could not be regenerated; without this the operator sees the applied change and
        never learns the apply script is stale (audit). No-op when the gate said nothing."""
        if not gate_msg:
            return res
        return _dc.replace(res, details=[gate_msg, *res.details],
                           next_commands=[*res.next_commands, *(gate_cmds or [])])

    def crl_refresh_if_expired(self) -> bool:
        """Rebuild the client-CA CRL when its nextUpdate lies in the past, then reload nginx
        via the normal apply. LIVE-FOUND (clock-jump class): an AP-isolated box gets NTP the
        moment it joins a WLAN, the clock jumps months forward past the CRL's nextUpdate,
        and nginx then rejects EVERY client cert ("The SSL certificate error") — a total
        console lockout with nothing actually revoked. lhpc owns the CRL file, so the heal
        is unprivileged. Fail-soft, returns True when it acted (rebuild or retry). AUDIT:
        a rebuild makes the FILE fresh even when the nginx reload fails — a reload-pending
        marker survives until an apply actually SUCCEEDS, so the heal is retried instead
        of silently skipped forever."""
        try:
            import datetime as _dt

            from cryptography import x509

            from . import pki as _pki
            from . import runtime_fs as _rfs
            pending = self._paths.under("state", "crl-reload-pending")
            if pending.exists():
                if self.webserver_apply().ok:
                    self._safe_unlink(pending)
                return True                  # acted (retried the reload)
            p = self._paths.under("config", "tls", "client-ca", "crl.pem")
            if not p.exists():
                return False
            pem = _rfs.read_text_regular(self._paths, p, max_bytes=262144)
            if not pem:
                return False
            crl = x509.load_pem_x509_crl(pem.encode())
            nu = getattr(crl, "next_update_utc", None) or crl.next_update
            if nu.tzinfo is None:
                nu = nu.replace(tzinfo=_dt.UTC)
            if nu > _dt.datetime.now(_dt.UTC):
                return False
            _pki.build_crl(self._paths)
            if not self.webserver_apply().ok:   # nginx must re-read the fresh CRL
                _rfs.atomic_write(self._paths, pending, "reload-pending\n", 0o600)
            return True
        except Exception:
            return False

    def webserver_apply(self) -> ActionResult:
        """Activate the DESIRED config: render + validate the nginx config FIRST (never
        activate an invalid one), then reload an already-running LHPC-owned nginx master, then
        verify + persist evidence. A missing/inactive master returns a typed 'service not active /
        repair required' result — the web process performs no start and no package install. A
        reload cannot rebind a held listen socket, so on a BIND change (loopback <-> 0.0.0.0) —
        the CONSOLE's or any STACK-PROXY listener's (`webserver proxy <stack> --mode ...`) — whose
        effective scope does not match the desired exposure this RESTARTS the unit automatically
        (`systemctl --user restart lhpc-nginx.service`, no operator action) and re-verifies; it
        never reports a bind change that did not take effect (F3)."""
        from . import webserver as _ws
        if not _ws.nginx_installed(self._system):
            return ActionResult(False, "nginx is not installed — required system dependency for "
                                "the production webserver", details=[_ws.NGINX_INSTALL_CMD],
                                next_commands=[_ws.NGINX_INSTALL_CMD])
        cfg = self.config().webserver
        # FW-6 exposure gate: never activate a NEW/WIDENED non-loopback listener before the
        # managed firewall verifiably protects it. No-op when firewall integration is absent
        # (behavior preserved) or when nothing new is exposed (exposure-reducing applies still
        # proceed). The config was already saved by the caller; only ACTIVATION is gated.
        allowed, gate_msg, gate_cmds = self.firewall_gate_activation(
            self._prospective_nginx_ports())
        if not allowed:
            self._ws_apply_pending_set()              # completed by the watchdog once verified
            return ActionResult(False, gate_msg, next_commands=gate_cmds,
                                data={"firewall_gate": "pending"})
        self._ws_apply_pending_clear()                # gate passed: the deferral is discharged
        # An ALLOWED gate can still carry a warning (exposure reduced, but the firewall scripts
        # could not be regenerated). Attaching it HERE — once, around the whole operation — is what
        # keeps it on every later outcome without touching a dozen return statements.
        return self._with_gate_note(self._webserver_apply_after_gate(cfg), gate_msg, gate_cmds)

    def _webserver_apply_after_gate(self, cfg) -> ActionResult:
        """Everything `webserver_apply()` does once the firewall gate has allowed activation.
        Split out so an allowed gate's warning attaches to EVERY outcome in one place."""
        from . import webserver as _ws
        # Stage + validate BEFORE touching the live config; promote atomically only on success
        # (a failed nginx -t leaves the previous proven live config byte-for-byte intact).
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg,
                                                  self._stack_web_proxies())
        if not ok:
            return ActionResult(False, "nginx config validation failed; previous proven "
                                f"configuration remains active ({msg})")
        _ws.promote_config(self._paths)
        state, rmsg = _ws.reload(self._system, self._paths)
        ev = self._ws_verify(cfg, self._stack_web_proxies())
        if state == "repair_required":
            return ActionResult(False, "config valid but the nginx service is not active — "
                                "repair required (operator context)",
                                details=[rmsg], data=ev)
        if state == "failed":
            return ActionResult(False, f"nginx reload failed: {rmsg}", data=ev)
        # F3: a reload cannot rebind a held listen socket, so a bind change (loopback <-> 0.0.0.0)
        # can leave the OLD listener in place while reload reports success — for the CONSOLE and
        # equally for every STACK-PROXY listener (`webserver proxy <stack> --mode public` flips the
        # rendered listen 127.0.0.1 <-> 0.0.0.0 the same way). When ANY effective scope does not
        # match its desired exposure, RESTART the unit automatically (ExecStop releases the sockets,
        # ExecStart rebinds; no operator action) and re-verify — never report a bind change that did
        # not take effect.
        def _listeners_ok(evd) -> bool:
            c = evd["checks"]
            return (c.get("remote_listener_matches") == "ok"
                    and c.get("stack_listener_matches", "ok") == "ok")

        if _listeners_ok(ev):
            # ACTIVATION PROVEN: promoted + reloaded + listeners match. Only here does the applied
            # policy advance — a failed validate/reload/restart keeps the previous one, so the
            # console and every proxy keep being rendered against what nginx is really serving.
            self._record_applied(cfg, self._stack_web_proxies())
            return ActionResult(True, "webserver configuration applied and nginx reloaded", data=ev)
        # PRIVILEGE BOUNDARY (live-found): inside the managed web unit the user bus is DELIBERATELY
        # inaccessible (`InaccessiblePaths=%t/bus %t/systemd/private` — the escape-proof updater
        # design: a compromised console must never command systemd). A direct restart from here can
        # only fail with a bus EPERM — so the web branch completes the bind change through the
        # nginx-restart ESCAPE HATCH instead (request marker -> lhpc-nginx-restart.path -> a
        # declarative Conflicts=/OnSuccess= stop/start; no bus anywhere).
        import os as _os
        if _os.environ.get("INVOCATION_ID"):
            return self._apply_via_restart_watcher(cfg, _listeners_ok)
        rstate, rmsg2 = _ws.restart(self._system, self._paths)
        if rstate != "restarted":
            # The restart itself FAILED — never proceed to a verification that could mask it (a dead
            # nginx yields "absent" listeners, which must not read as any kind of success).
            ev = self._ws_verify(cfg, self._stack_web_proxies())
            return ActionResult(False, f"nginx restart failed: {rmsg2}",
                                next_commands=["systemctl --user restart lhpc-nginx.service",
                                               "lhpc webserver logs"], data=ev)
        ev = self._ws_verify(cfg, self._stack_web_proxies())
        if _listeners_ok(ev):
            self._record_applied(cfg, self._stack_web_proxies())
            return ActionResult(True, "webserver configuration applied; nginx restarted to rebind "
                                "the listener (a bind change needs a restart, not a reload)", data=ev)
        scope = ev.get("effective", {}).get("listener_scope", "unknown")
        stuck = ev["checks"].get("stack_listener_mismatch_stacks", [])
        what = (f"the console listener is still '{scope}' (desired remote_exposed="
                f"{cfg.remote_exposed})" if ev["checks"].get("remote_listener_matches") != "ok"
                else f"stack proxy listener(s) did not rebind: {', '.join(stuck)}")
        return ActionResult(
            False, f"configuration applied but {what} — the bind change did not take effect even "
            "after an automatic restart; inspect the nginx error log", details=[rmsg2],
            next_commands=["systemctl --user restart lhpc-nginx.service",
                           "lhpc webserver logs"], data=ev)

    def _apply_via_restart_watcher(self, cfg, listeners_ok) -> ActionResult:
        """WEB-context completion of a listener BIND change via the nginx-restart escape hatch:
        exclusively create the request marker, then wait (bounded) for the static path unit to claim
        it and for a fresh verify to prove the listeners match. Falls back to the honest typed
        refusal when the hatch units are not the canonical set (old deployment / tampered), and on
        timeout SPLITS the failure by whether the request survived — an unclaimed request means the
        WATCHER is dead (integration remedy); a claimed one means the restart RAN but nginx never
        came good (nginx-side remedy). Never reports an unverified bind change."""
        import time as _time

        from . import runtime_fs, updater_units
        from . import webserver as _ws
        root = str(self._paths.runtime_root)
        _, checkout, venv = updater_units.deployment_paths(root)
        user_dir = self._user_unit_dir()
        hatch_ok = all(
            updater_units.verify(user_dir, k, root, checkout, venv) == updater_units.OK
            for k in (updater_units.RESTART_UNIT, updater_units.RESTART_PATH_UNIT))
        if not hatch_ok:
            return ActionResult(
                False, "configuration applied, but the bind change needs a front-end RESTART — and "
                "the web console deliberately cannot restart services (privilege boundary). The "
                "managed restart watcher is not installed/canonical on this deployment: run "
                "`lhpc self-update --repair-integration` once (installs it), or apply from an "
                "operator shell: `lhpc webserver apply`.",
                next_commands=["lhpc self-update --repair-integration", "lhpc webserver apply"])
        req = self._paths.under(*updater_units.NGINX_RESTART_REQUEST_REL)
        try:
            m = runtime_fs.open_marker_excl(self._paths, req, "restart\n")
            m.close()
        except FileExistsError:
            pass                                    # a restart is already queued/in flight — ride it
        except Exception as exc:
            return ActionResult(False, f"could not queue the front-end restart request: {exc}")
        deadline = _time.monotonic() + _RESTART_WATCH_WAIT_S
        ev = None
        while _time.monotonic() < deadline:
            _time.sleep(_RESTART_WATCH_POLL_S)
            if req.exists():
                continue                            # not yet claimed by the watcher
            if not _ws.nginx_master_active(self._paths):
                continue                            # between declarative stop and OnSuccess start
            ev = self._ws_verify(cfg, self._stack_web_proxies())
            if listeners_ok(ev):
                self._record_applied(cfg, self._stack_web_proxies())
                return ActionResult(
                    True, "webserver configuration applied; nginx restarted via the managed restart "
                    "watcher (a bind change needs a restart, not a reload)", data=ev)
        if req.exists():
            # The watcher never claimed the request: it is dead or not enabled. Remove OUR stale
            # marker (nobody will consume it) and point at the integration remedy.
            try:
                runtime_fs.unlink(self._paths, req)
            except OSError:
                pass
            return ActionResult(
                False, "configuration applied, but the restart watcher never picked up the request "
                "(lhpc-nginx-restart.path inactive?) — the bind change did not take effect. Run "
                "`lhpc self-update --repair-integration`, or apply from an operator shell.",
                next_commands=["lhpc self-update --repair-integration", "lhpc webserver apply"],
                data=ev or {})
        # Claimed, but nginx (or the listeners) never came good: the integration worked — the
        # problem is on the nginx side. Nothing to remove; point at the nginx evidence.
        return ActionResult(
            False, "configuration applied and the restart watcher ran, but the listeners still do "
            "not match the desired exposure — inspect logs/lhpc-nginx-restart.log and the nginx "
            "error log (logs/nginx-error.log) / journal.",
            next_commands=["lhpc webserver logs"], data=ev or {})

    def webserver_run_restart_service(self) -> ActionResult:
        """`lhpc-nginx-restart.service` ExecStart body: CLAIM the restart request (atomic
        no-overwrite rename request -> inflight; absent request = stray start -> typed no-op) and
        consume it. NO nginx interaction in-process — systemd's declarative unit relationships
        (Conflicts= stopped lhpc-nginx before this ran; OnSuccess= starts a fresh one after exit 0)
        perform the restart. Unlike the self-update helper there is no multi-step transaction to
        protect: a restart is idempotent, so a stale in-flight breadcrumb from a crashed prior run
        is removed and the claim retried rather than demanding recovery."""
        from . import runtime_fs, updater_units
        req = self._paths.under(*updater_units.NGINX_RESTART_REQUEST_REL)
        inflight = self._paths.under(*updater_units.NGINX_RESTART_INFLIGHT_REL)
        for attempt in (1, 2):
            try:
                runtime_fs.rename_leaf(self._paths, req, inflight, replace=False)
                break
            except FileNotFoundError:
                return ActionResult(True, "No nginx-restart request to service (stray start) — "
                                    "nothing consumed.", data={"noop": True})
            except FileExistsError:
                if attempt == 2:
                    return ActionResult(False, "Could not claim the restart request: a stale "
                                        "in-flight record persists.", data={"claim_failed": True})
                try:
                    runtime_fs.unlink(self._paths, inflight)   # crashed prior run's breadcrumb
                except OSError as exc:
                    return ActionResult(False, f"Could not clear a stale in-flight record: {exc}",
                                        data={"claim_failed": True})
            except Exception as exc:
                return ActionResult(False, f"Could not claim the restart request: {exc}",
                                    data={"claim_failed": True})
        try:
            runtime_fs.unlink(self._paths, inflight)
        except OSError:
            pass                                    # breadcrumb only; the restart still proceeds
        return ActionResult(True, "nginx-restart request consumed — systemd now starts a fresh "
                            "lhpc-nginx (declarative OnSuccess=).", data={"consumed": True})

    def webserver_start_service(self) -> ActionResult:
        """OPERATOR-CONTEXT bootstrap (correction 1): generate + validate + promote the nginx
        config, then ENABLE + START the rootless nginx user unit via `systemctl --user`. This is
        the only path that STARTS nginx — it REFUSES to run from a managed unit (the web process
        never starts a listener), so after `init` the operator runs this once to bring the HTTPS
        console up. Prerequisites (nginx installed, server cert present, config valid) are
        checked and reported truthfully."""
        import os as _os

        from . import pki as _pki
        from . import webserver as _ws
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
        allowed, gate_msg, gate_cmds = self.firewall_gate_activation(
            self._prospective_nginx_ports())
        if not allowed:
            return ActionResult(False, gate_msg, next_commands=gate_cmds,
                                data={"firewall_gate": "pending"})
        return self._with_gate_note(self._start_service_after_gate(cfg, proxies),
                                    gate_msg, gate_cmds)

    def _start_service_after_gate(self, cfg, proxies) -> ActionResult:
        """Everything `webserver_start_service()` does once the gate has allowed activation —
        same split as `_webserver_apply_after_gate`, for the same reason."""
        from . import webserver as _ws
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
        ev = self._ws_verify(cfg, proxies)
        # Same gate as apply: only a listener-verified start records the applied policy. A start
        # whose listeners do not match leaves the previous snapshot, so the pill stays conservative.
        if (ev["checks"].get("remote_listener_matches") == "ok"
                and ev["checks"].get("stack_listener_matches", "ok") == "ok"):
            self._record_applied(cfg, proxies)
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
        from . import runtime_fs
        from . import webserver as _ws
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

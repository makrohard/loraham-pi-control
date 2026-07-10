"""Local operator web console (Flask, server-rendered).

The web layer is thin: every route renders fresh state or dispatches an action
through `ControllerService` (the same service layer as the CLI — it never shells
out to the CLI). GET routes are read-only; state-changing actions are POST-only,
CSRF-protected, and show a plan + confirmation before applying.

Security posture:
  * loopback bind only (enforced in `run_server`); never 0.0.0.0;
  * GET = read-only; mutations = POST + CSRF token; unknown stack -> 404;
  * local-console security headers on every response (incl. CSP default-src 'self');
  * Jinja autoescaping escapes all untrusted data;
  * each request builds a fresh snapshot (no stale controller process state).
"""

from __future__ import annotations

import ipaddress as _ipaddress
import secrets as _secrets
import time
from typing import Callable

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)

from lhpc.core import validators
from lhpc.core.outcomes import manual_required_only
from lhpc.core.services import ControllerService
from lhpc.core.status import rollup_states, stack_dependencies, summarize
from lhpc.version import __version__

_RUNNING = ("running", "degraded")

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def peer_is_loopback() -> bool:
    """Loopback-vs-remote decision for loopback-only actions (e.g. a new `.p12` download).

    Trusts ONLY the nginx-set `X-LHPC-Peer` header — nginx strips any client-supplied copy
    and sets it from the real `$remote_addr`. When the header is absent the app is not behind
    nginx (bare interactive loopback-TCP mode / tests), which is loopback by construction, so
    treat it as loopback. A REMOTE client always traverses nginx, which always sets the header
    to `remote`, so a remote peer can never appear loopback here."""
    from flask import request
    hdr = request.headers.get("X-LHPC-Peer", "")
    return hdr == "loopback" if hdr else True

# --- trusted-host helpers (see `_trusted_host`) --------------------------------------------------
_HOST_MAX = 260                          # a Host header can never legitimately exceed this


def _host_only(raw: str) -> str:
    """The bare host from a `Host` header value: port stripped, IPv6 brackets stripped, lowercased.

    `"[::1]:8443"` -> `"::1"`, `"pi.local:8443"` -> `"pi.local"`. A naive `split(":")[0]` yields `"["`
    for the bracketed IPv6 form, which is why the hardcoded `::1` entry could never match. Bounded,
    because the result is echoed back in the 400 body.
    """
    h = (raw or "").strip()[:_HOST_MAX]
    if h.startswith("["):                # bracketed IPv6 literal: [::1] or [::1]:8443
        end = h.find("]")
        if end != -1:
            return h[1:end].lower()
    # A bare IPv6 literal (no brackets) has >1 colon and carries no port; anything else is host[:port].
    return h.split(":")[0].lower() if h.count(":") <= 1 else h.lower()


def _url_host(raw: str) -> str:
    """The host authority from `request.host`, ready to place in a URL — port stripped, and an IPv6
    literal RE-bracketed (`::1` -> `[::1]`). Used to point a proxied web-UI link at the exact host the
    operator reached the console at, rather than a guessed `local_ip()`."""
    host = _host_only(raw)
    if ":" in host and not host.startswith("["):     # bare IPv6 literal
        return f"[{host}]"
    return host


_HOST_ECHO_OK = set("abcdefghijklmnopqrstuvwxyz0123456789.:-_")


def _host_echo(host: str) -> str:
    """The rejected host, reduced to characters a hostname/IP can legally contain, for echoing in the
    400 body. The response is text/plain — so this is belt AND braces: even inert, a reflected
    `<script>` in an error page is a smell nobody should have to reason about."""
    kept = "".join(ch for ch in host[:80] if ch in _HOST_ECHO_OK)
    return kept or "(unprintable)"


def _as_ip(value: str):
    """The parsed IP address, or None when `value` is a name."""
    try:
        return _ipaddress.ip_address(value)
    except ValueError:
        return None


def _host_allowed(host: str, allowed: set, exposed: bool) -> bool:
    """Is `host` acceptable? A NAME must be in `allowed`. An IP is compared by PARSED value (so
    `2001:db8:0:0:0:0:0:1` matches an `ip_sans` entry of `2001:db8::1`), and ANY IP literal is
    accepted while the console is remotely exposed — rebinding needs a name, not an address."""
    if host in allowed:
        return True
    ip = _as_ip(host)
    if ip is None:
        return False                     # a NAME that is not configured -> rejected in every mode
    if exposed:
        return True                      # bare IP: cannot be rebound
    return any(ip == parsed for parsed in (_as_ip(a) for a in allowed) if parsed is not None)


_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Explicit directives so same-origin script + fetch (live polling) are
    # unambiguously allowed while everything else stays locked down.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; connect-src 'self'; "
        "style-src 'self'; img-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}

ServiceFactory = Callable[[], ControllerService]


def _parse_start_daemon_overrides(form):
    """STRICTLY parse the Start-confirm `dp_*` fields into a per-band map ``{band: {PARAM: value}}``
    (or None). Returns ``(per_band_or_None, error)``. Every field whose name starts with `dp_` must
    have the exact shape ``dp_<band>_<PARAM>`` (both parts non-empty) and appear once — a malformed
    or duplicated field name is rejected here (error != None) so the start fails BEFORE any launch.
    Unknown band / unknown parameter / value validity are enforced by the service normalizer
    (`_normalize_ephemeral_overrides`); this parser does not duplicate those rules. A blank value is
    carried through (the service treats blank/absent as "no override")."""
    per_band: dict = {}
    for name in form.keys():
        if not name.startswith("dp_"):
            continue
        if len(form.getlist(name)) > 1:                       # duplicated/conflicting field
            return None, f"duplicated daemon field {name!r}"
        parts = name.split("_", 2)                            # ["dp", band, PARAM]
        if len(parts) != 3 or not parts[1] or not parts[2]:   # malformed shape (dp_bad, dp_433_, dp_)
            return None, f"malformed daemon field {name!r}"
        _, band, param = parts
        per_band.setdefault(band, {})[param] = form[name]
    return (per_band or None), None


def create_app(service_factory: ServiceFactory | None = None) -> Flask:
    """Build the Flask app. `service_factory` is injectable for tests."""
    app = Flask(__name__)
    factory: ServiceFactory = service_factory or ControllerService
    service = factory()
    # PERSISTENT signed-session/CSRF secret (survives restarts; not reset by "Reset to
    # default"). Fail-safe: if the runtime secret can't be read/created, fall back to a
    # per-process secret so the console still starts (sessions just won't survive restart).
    try:
        app.secret_key = service.web_session_secret()
    except Exception:
        app.secret_key = _secrets.token_bytes(32)
    # Cookie hardening. Secure (HTTPS-only) cookies are correct behind the nginx TLS boundary;
    # disabled only when a caller explicitly marks the app non-productive (e.g. the bare
    # interactive TCP mode / tests) so an http test client still round-trips the session.
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=bool(app.config.get("LHPC_SECURE_COOKIES", False)))

    def _csrf_token() -> str:
        if "_csrf" not in session:
            session["_csrf"] = _secrets.token_hex(16)
        return session["_csrf"]

    def _csrf_ok() -> bool:
        sent = request.form.get("_csrf", "")
        return bool(sent) and _secrets.compare_digest(sent, session.get("_csrf", ""))

    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.context_processor
    def _inject_selfupdate():  # noqa: ANN202
        # Footer version/head indicator, on EVERY page. Reads the cached state marker ONLY — no git,
        # no network, no subprocess — so it never violates the no-network-GET rule. Fail-safe.
        try:
            return {"selfupdate": service.self_update_status()}
        except Exception:
            return {"selfupdate": {"version": __version__, "head_short": "", "available": False,
                                   "ver_color": "grey", "commit_color": "grey",
                                   "update_available": False}}

    @app.after_request
    def _set_headers(response):  # noqa: ANN001
        for key, value in _SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.before_request
    def _trusted_host():  # noqa: ANN202
        """Explicit trusted-host policy — ENFORCED only in productive HTTPS mode (Secure cookies), so
        the interactive loopback-TCP console and the test client (plain http) are unaffected. The real
        Host header is compared, never a client-supplied X-Forwarded-Host (nginx blanks those).

        It defends against DNS REBINDING / Host spoofing, which both require a *name*: the attacker's
        page lives at `evil.example`, its record is flipped to this host's address, and the browser
        then sends `Host: evil.example` — rejected, because names must be configured in `dns_sans`.
        A bare IP literal cannot be rebound (a browser only sends one when the operator typed an IP),
        so while the console is REMOTELY EXPOSED any IP-literal Host is accepted. That is what makes a
        multi-homed Pi (eth0 + wlan0) and IPv6 work without enumerating interfaces — `local_ip()` knows
        exactly one IPv4 address. Loopback-only deployments keep the strict allowlist.
        """
        # Enforced whenever we serve productively behind nginx. NOT keyed on Secure cookies alone:
        # an `http` console must drop those (browsers discard them) yet keep this policy.
        if not (app.config.get("SESSION_COOKIE_SECURE") or app.config.get("LHPC_PRODUCTIVE")):
            return None
        raw = request.host or ""
        host = _host_only(raw)
        allowed = {"localhost", "127.0.0.1", "::1"}
        exposed = False
        try:
            ws = service.config().webserver
            allowed |= {h.lower() for h in ws.dns_sans} | {str(i).lower() for i in ws.ip_sans}
            # `bind` is only a valid Host when it names a REAL address. The wildcards 0.0.0.0 / ::
            # are what an exposed console binds to, and no client ever sends them as a Host.
            if ws.bind and ws.bind not in ("0.0.0.0", "::"):
                allowed.add(ws.bind.lower())
            exposed = bool(ws.remote_exposed)
        except Exception:                                # noqa: BLE001 — unreadable config -> loopback only
            pass
        if not host or _host_allowed(host, allowed, exposed):
            return None
        # The RAW header never reaches the response: only the normalized host, as bounded text/plain.
        # The full diagnostic goes to the log (logs/lhpc-web.log via the unit's StandardError).
        app.logger.warning(
            "trusted-host: rejected Host %r (normalized %r); allowed=%s remote_exposed=%s",
            raw[:200], host, sorted(allowed), exposed)
        body = (f"400 Bad Request: this console does not answer to the host "
                f"\"{_host_echo(host)}\".\n"
                "Reach it by an address it serves on, or add that name to [webserver] dns_sans "
                "(or the IP to ip_sans) in config/local.toml. Remote exposure additionally accepts "
                "any bare IP address.\n")
        return Response(body, status=400, mimetype="text/plain")

    def _runtime_root() -> str:
        return str(service.runtime_root)  # display only

    @app.get("/")
    def dashboard():  # noqa: ANN202
        # Radio-centric overview: one column per band (433/868) with daemon/radio
        # config, live monitor, the stack running on it, and a start-stack control.
        radios = service.radio_overview()
        # An interactive app that's been launched (marked) but isn't detected running
        # yet -> the operator is about to run it in a terminal; poll faster so the
        # dash flips to "running" quickly instead of waiting for the slow refresh.
        pending_interactive = any(not s.get("running") and not s.get("blocker")
                                  for r in radios for s in r["interactive"])
        return render_template(
            "dashboard.html", version=__version__, runtime_root=_runtime_root(),
            radios=radios, pending_interactive=pending_interactive,
            # The host the browser used to reach the console — a proxied web-UI link points here on
            # the proxy's port, so it is correct however the operator got here (LAN IP / hostname).
            req_host=_url_host(request.host or ""),
            # Durable restart-required flags (file reads only): a yellow "Restart now"
            # action per flagged stack.
            restart_required=service.restart_required_stacks(),
            welcome=service.bulk_welcome(),
            dash_sig=service.dash_signature())

    @app.get("/api/dash-signature")
    def dash_signature_api():  # noqa: ANN202
        # Cheap structural-state signature; the dashboard reloads only when it changes.
        return jsonify(sig=service.dash_signature())

    _SRC_LABELS = (("pinned", "Known working"), ("dev", "Development"),
                   ("stable", "Latest stable"))

    def bulk_mod2_run_id_re():
        from lhpc.core import bulk as bulk_mod
        return bulk_mod.RUN_ID_RE

    def bulk_mod_terminal_ok():
        from lhpc.core import bulk as bulk_mod
        return bulk_mod.TERMINAL_OK

    @app.get("/install-all")
    def install_all_page():  # noqa: ANN202
        st = service.bulk_status()
        mode = service.bulk_mode()
        running = service.bulk_running()
        # A JUST-SPAWNED run: reservation live but the driver hasn't written its marker
        # yet — show a 'starting' card immediately (never a blank page after the POST).
        starting = False
        starting_run = ""
        # ... including when the previous run's TERMINAL marker is still on disk (a new
        # run may start over it without acknowledgement) and during the brief "spawning"
        # phase — the POST redirect lands within milliseconds of the spawn.
        if st is None or (not st.get("unsafe")
                          and st.get("state") in bulk_mod_terminal_ok()):
            from lhpc.core import bulk as bulk_mod, procident
            rstate, res = bulk_mod.read_reservation(service._paths)
            if (rstate == "valid"
                    and res.get("phase") in ("spawning", "spawned", "claimed")
                    and res.get("run_id") != (st or {}).get("run_id")
                    and procident.identity_matches(res.get("ident", {}),
                                                   res.get("pid", -1))):
                starting = True
                starting_run = res.get("run_id", "")
        # A spawned run that ended BEFORE claiming (typed preflight refusal): show its
        # actual output instead of silently falling back to the old run's card.
        spawn_failed = ""
        spawn_arg = request.args.get("spawn", "")
        if (spawn_arg and bulk_mod2_run_id_re().match(spawn_arg)
                and (st is None or st.get("run_id") != spawn_arg)):
            chunk = service.bulk_log_chunk(spawn_arg, 0)
            if chunk.get("data"):
                spawn_failed = chunk["data"][-4000:]
        gate = service._bulk_gate()
        recovery = service.bulk_recovery_reason()
        needs_ack = bool(recovery)
        orphan_risk = "ORPHAN RISK" in recovery
        chunk = {"offset": 0, "data": ""}
        complog_seed = ""
        if st and not st.get("unsafe"):
            chunk = service.bulk_log_chunk(st["run_id"], 0)
            # Historical (collapsed) run: seed the detailed per-component log window server-side,
            # exactly as log_seed seeds the rollup. Same condition the template uses for `collapsed`.
            if (not running and not needs_ack
                    and st.get("state") in ("completed", "completed-with-failures")):
                complog_seed = service.bulk_component_log_seed(st["run_id"])
        return render_template(
            "install_all.html", version=__version__, runtime_root=_runtime_root(),
            st=st, mode=mode, running=running, gate=gate, needs_ack=needs_ack,
            recovery=recovery, orphan_risk=orphan_risk, starting=starting, starting_run=starting_run,
            spawn_failed=spawn_failed,
            log_seed=chunk.get("data", ""), complog_seed=complog_seed, src_labels=_SRC_LABELS)

    _TX_CONFIRM_TTL_S = 300.0

    def _stage_tx_confirmation(source: str, tests: bool) -> str:
        """SERVER-SIDE single-use RF confirmation: session-bound token tied to the exact
        source/tests/TX choices, the CSRF context, and a short expiry. Consumed atomically
        by the confirming POST — hidden-field values are never trusted on their own."""
        token = _secrets.token_hex(16)
        session["_bulk_tx_confirm"] = {"token": token, "source": source,
                                       "tests": bool(tests), "tx": True,
                                       "csrf": session.get("_csrf", ""),
                                       "exp": time.time() + _TX_CONFIRM_TTL_S}
        return token

    def _consume_tx_confirmation(token: str, source: str, tests: bool) -> str:
        """Validate + CONSUME the staged confirmation in one step (popped before any
        spawn — replay-proof). Returns "" when valid, else the typed refusal."""
        staged = session.pop("_bulk_tx_confirm", None)          # single-use: always consumed
        if not isinstance(staged, dict):
            return "no valid RF confirmation is staged — start again from the form"
        try:
            if not (isinstance(staged.get("token"), str) and staged["token"]
                    and isinstance(staged.get("exp"), (int, float))):
                return "the staged RF confirmation is malformed — start again"
            if time.time() > staged["exp"]:
                return "the RF confirmation has expired — start again"
            if not (token and _secrets.compare_digest(token, staged["token"])):
                return "the RF confirmation token does not match — start again"
            if not _secrets.compare_digest(session.get("_csrf", ""),
                                           staged.get("csrf", "")):
                return "the RF confirmation belongs to a different session — start again"
            if staged.get("source") != source or staged.get("tests") != bool(tests)                     or staged.get("tx") is not True:
                return ("the confirmed choices (version/tests/TX) changed after "
                        "confirmation — start again")
        except (TypeError, KeyError):
            return "the staged RF confirmation is malformed — start again"
        return ""

    @app.post("/install-all/start")
    def install_all_start():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        source = request.form.get("source", "")
        tests = request.form.get("tests") == "yes"
        tx = request.form.get("tx") == "yes"
        if source not in service.SOURCE_CHOICES:
            flash("Unknown source choice.", "warn")
            return redirect(url_for("install_all_page"))
        if tx and not tests:
            flash("The TX test requires host tests to be enabled.", "warn")
            return redirect(url_for("install_all_page"))
        if tx:
            token = request.form.get("confirm_token", "")
            if not token:
                # FIRST TX-enabled POST: stage the server-side confirmation and render
                # the second page carrying the bound choices + one-time token.
                token = _stage_tx_confirmation(source, tests)
                return render_template(
                    "install_all_confirm.html", version=__version__,
                    runtime_root=_runtime_root(), source=source, tests=tests,
                    confirm_token=token,
                    src_label=dict(_SRC_LABELS).get(source, source))
            why = _consume_tx_confirmation(token, source, tests)
            if why:
                flash(f"RF confirmation refused: {why}.", "warn")
                return redirect(url_for("install_all_page"))
        job, err = service.spawn_bulk_job(source, tests, tx)
        if err:
            flash(err, "warn")
        else:
            flash("Bulk run started — this can take several minutes.", "ok")
        return redirect(url_for("install_all_page"))

    @app.post("/install-all/ack")
    def install_all_ack():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        res = service.bulk_ack(
            confirm_orphan=request.form.get("confirm_orphan") == "yes")
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("install_all_page"))

    @app.get("/api/install-all")
    def install_all_api():  # noqa: ANN202
        st = service.bulk_status()
        out = {"state": st if st is not None else {"absent": True},
               "running": service.bulk_running(), "run_id": "", "log": {}}
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            offset = -1
        # Whether a spawn reservation is LIVE (spawning/spawned/claimed, identity
        # proven): the starting card uses this to detect a child that ended BEFORE
        # writing its marker (e.g. the running-components preflight refusal).
        from lhpc.core import bulk as bulk_mod, procident
        rstate, res = bulk_mod.read_reservation(service._paths)
        out["spawn_live"] = bool(
            rstate == "valid"
            and res.get("phase") in ("spawning", "spawned", "claimed")
            and procident.identity_matches(res.get("ident", {}), res.get("pid", -1)))
        if st and not st.get("unsafe"):
            out["run_id"] = st["run_id"]
            out["log"] = service.bulk_log_chunk(st["run_id"], offset)
            # Second window: the sequential per-component build/test log stream.
            try:
                ci = int(request.args.get("ci", "0"))
                co = int(request.args.get("co", "0"))
            except ValueError:
                ci, co = 0, 0
            out["complog"] = service.bulk_component_log_chunk(st["run_id"], ci, co)
        return jsonify(**out)

    def _effective_freshness(entry, current_head: str) -> str:
        from lhpc.core import stackupdates
        return stackupdates.effective_status(entry, current_head or "")

    def _checked_ago(checked_at: int) -> str:
        """'Last checked' as a short human string — Jinja has no time formatter here."""
        if not checked_at:
            return ""
        secs = max(0, int(time.time()) - int(checked_at))
        if secs < 90:
            return "just now"
        if secs < 5400:
            return f"{secs // 60} min ago"
        if secs < 172800:
            return f"{secs // 3600} h ago"
        return f"{secs // 86400} d ago"

    def _stack_groups(band=""):
        """Per-stack overview rows for the Stacks page. Each row now carries the FULL per-stack
        detail (formerly the /stacks/<id> page): component statuses/evidence, system+build+runtime
        dependency diagnosis, needs-build, daemon parameters, known-working offer, restart-required
        and stack-scoped conflicts — all read-only, GET-safe. `band` is threaded (as the detail page
        did) into the band-aware views."""
        snapshot = service.build_snapshot()  # fresh, read-only evidence each load
        rollup = rollup_states(snapshot)
        stack_deps = stack_dependencies([ss.stack for ss in snapshot.stacks])
        all_conflicts = service.observed_conflicts(snapshot)
        index = {}
        for ss in snapshot.stacks:
            for comp in ss.stack.components:
                index[comp.id] = (comp, ss.components[comp.id])
        # CACHED source freshness (never the network on a GET — P0.6). Each cached verdict is
        # resolved against the component's CURRENT head, so a source updated since the last check
        # reads `unchecked`, not a stale green/yellow.
        _fresh = service.source_check_view()
        _fresh_comps = _fresh.get("components", {})

        groups = []
        for ss in snapshot.stacks:
            stack = ss.stack
            main = stack.main_component
            own = [(c, ss.components[c.id]) for c in stack.components
                   if not main or c.id != main.id]
            own.sort(key=lambda cs: (cs[0].start_order is None, cs[0].start_order or 0))
            cross_ids, seen = [], {c.id for c in stack.components}
            for c in stack.components:
                for dep in c.depends_on:
                    if dep not in seen and dep in index:
                        seen.add(dep)
                        cross_ids.append(dep)
            cross = [index[d] for d in cross_ids]
            main_status = index.get(main.id, (None, None))[1] if main else None
            has_source = bool(main and main.source)
            installed = bool(has_source and main_status and main_status.source_state.value
                             in ("match", "dirty", "differs", "unknown", "not-a-repo"))
            interactive = bool(main and main.interactive)
            member_ids = {c.id for c in stack.components}
            # Freshness, resolved per component against the head the verdict was computed from.
            # Scoped to the stack's OWN components: a cross-stack dependency (loraham-daemon inside
            # the chat row) belongs to its own stack's row and must not light up this one.
            upd_states = {c.id: _effective_freshness(_fresh_comps.get(c.id),
                                                     ss.components[c.id].source_head)
                          for c in stack.components}
            groups.append({
                "stack": stack,
                "main": index.get(main.id) if main else None,
                "main_status": main_status,
                "statuses": ss.components,            # dict[comp_id -> ComponentStatus] (evidence etc.)
                "update_states": upd_states,          # comp_id -> unchecked|unknown|up_to_date|behind|error
                "update_main": upd_states.get(main.id, "unchecked") if main else "unchecked",
                "update_available": any(v == "behind" for v in upd_states.values()),
                "update_checked_ago": _checked_ago(_fresh.get("checked_at", 0)),
                # {} for a stack with no web UI -> the Webserver sub-section is not rendered.
                "stack_web": service.stack_web_view(stack.id),
                "installed": installed,
                "has_source": has_source,
                "deps": own + cross,
                "state": rollup[stack.id],
                "running": rollup[stack.id] in _RUNNING,
                "dep_stacks": [(d, rollup.get(d, "unknown")) for d in stack_deps[stack.id]],
                "bands": sorted({c.band for c in stack.components if c.band}),
                "interactive": interactive,
                "command": service.manual_start_command(main) if interactive else "",
                # Per-stack Settings section rendered inline on the Apps list (same data the
                # stack-detail Settings uses): operator, component-scoped params, sources, autostart.
                "view": service.config_view(stack.id, band),
                "config_groups": service.config_param_groups(stack.id, band),
                # Folded-in detail sections (all read-only / GET-safe):
                "system_deps": service.system_deps(stack.id),
                "deps_report": service.deps_report(stack.id),
                "needs_build": service.unbuilt_components(stack.id),
                "daemon_params": service.daemon_params_view(stack.id, band),
                "kw_offer": service.known_working_offer(stack.id, snapshot),
                "restart_required": service.restart_required(stack.id),
                "conflicts": [c for c in all_conflicts
                              if any(h in member_ids for h in c.holders)],
            })
        jobs_by_stack = {}
        for job in service.active_jobs():
            jobs_by_stack.setdefault(job.get("stack"), []).append(job)
        for g in groups:
            g["jobs"] = jobs_by_stack.get(g["stack"].id, [])
        groups.sort(key=lambda g: (not g["running"], len(g["dep_stacks"]), g["stack"].id))
        return groups, snapshot

    def _render_stacks(**over):
        # The Apps page — also the home of the controller's embedded Update UI. `st`/`jobs`
        # are CACHED (status envelope + local job list); `confirm`/`result`/`apply_data` are
        # only set when the update apply flow renders back here. All controller data is
        # cached-only — no live git/network/identity on a GET.
        groups, snapshot = _stack_groups(request.args.get("band", ""))
        # Controller-owned Webserver component, rendered INLINE in the controller row. Cached
        # evidence only (monitor_view: no probing/mutation) — fail-safe so it never breaks /stacks.
        from lhpc.core.config import WEBSERVER_ACCESS_MODES as _WS_MODES
        try:
            _ws = service.webserver_monitor().data
        except Exception:
            _ws = None
        ctx = dict(
            version=__version__, runtime_root=_runtime_root(), snapshot=snapshot,
            summary=summarize(snapshot), groups=groups, bulk_mode=service.bulk_mode(),
            observed_conflicts=service.observed_conflicts(snapshot),   # band-aware
            controller=service.controller_status(),
            ws_mon=_ws, ws_certs=(_ws or {}).get("pki", {}).get("clients", []),
            ws_modes=list(_WS_MODES), ws_loopback=peer_is_loopback(),
            st=service.self_update_status(), jobs=service.active_jobs(),
            # One-click integration status (GET-safe file reads): gates the "Update now" button
            # and drives recovery/guidance in _update.html.
            updater=service.updater_integration(),
            confirm=None,
        )
        ctx.update(over)
        return render_template("stacks.html", **ctx)

    @app.get("/stacks")
    def stacks_overview():  # noqa: ANN202
        return _render_stacks()

    @app.post("/self-update/check")
    def self_update_check():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        res = service.self_update_check()          # NETWORK (explicit): git fetch, refresh cache
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("stacks_overview") + "#controller-row")

    @app.post("/source-check/<target>")
    def source_check(target: str):  # noqa: ANN202
        # DEDICATED route, deliberately NOT an /action op: probing remote freshness is non-mutating
        # and needs no confirm stage, so it must not inherit the lifecycle dispatch that
        # install/build/start/stop share. `target` is a stack id or a component id.
        if not _csrf_ok():
            abort(400)
        sid = service.stack_of(target)
        if sid is None:
            abort(404)                             # unknown target -> 404, and no network
        res = service.source_check(target)         # NETWORK (explicit): git ls-remote, refresh cache
        flash(f"{res.summary} {' '.join(res.details[:6])}", "ok" if res.ok else "warn")
        return _install_back(sid)                  # land on the stack's Install section

    @app.route("/self-update/apply", methods=["GET", "POST"])
    def self_update_apply():  # noqa: ANN202
        # One-click self-update. The running console cannot apply in-process (it holds the
        # controller-runtime lock SHARED so its own code is never mutated underneath it);
        # a confirmed request starts the PARAMETER-FREE updater unit, which stops this
        # service, applies (all gates), syncs the venv and starts the console again.
        if request.method == "GET":
            # Both stages render INLINE at this URL, so the browser tab stays on /self-update/apply.
            # A stray GET (reload, Back button, or the browser re-requesting through the restart
            # outage) must NOT 405 — send it to the controller row instead.
            return redirect(url_for("stacks_overview") + "#controller-row")
        if not _csrf_ok():
            abort(400)
        st = service.self_update_status()
        if request.form.get("confirmed") != "yes":
            # Stage 1 — confirm: warn about the automatic stop/update/restart. The dirty AND
            # diverged states are checked FRESH here (local git only, POST-time) — if either holds,
            # a normal update is refused and the discard-consent checkbox is the operator's explicit
            # agreement to force (reset --hard + clean). SHOW the evidence: the exact paths that a
            # force would discard, and how far the history diverged. Consent to a discard you cannot
            # see is not consent.
            _dirty = service.self_update_local_dirty()
            _diverged = service.self_update_ff_blocked()
            _ahead, _behind = service.self_update_divergence() if _diverged else (0, 0)
            return _render_stacks(confirm={"dirty": _dirty,
                                           "diverged": _diverged,
                                           "changes": service.self_update_local_changes() if _dirty else (),
                                           "ahead": _ahead, "behind": _behind,
                                           "branch": service.self_update_branch(),
                                           "update_available": st.get("update_available")})
        # Stage 2 — trigger. Consent only sets the request marker's payload bit
        # (normal|overwrite); a stale overwrite tick with a meanwhile-clean, fast-forwardable tree
        # drops to normal. repair_and_trigger delegates straight to the marker trigger when the units
        # are already canonical, and otherwise migrates a legacy same-root deployment (old/%h units,
        # no .path) to the canonical set first — all in this one click.
        overwrite = (request.form.get("overwrite") == "yes"
                     and (service.self_update_local_dirty() or service.self_update_ff_blocked()))
        res = service.self_update_repair_and_trigger(overwrite=overwrite)
        if not res.ok:
            flash(res.summary, "warn")
            return _render_stacks()
        return render_template("updating.html", version=__version__,
                               runtime_root=_runtime_root())

    @app.get("/stacks/<stack_id>")
    def stack_detail(stack_id: str):  # noqa: ANN202
        # The per-stack detail page was folded into the /stacks overview (collapsible sections
        # per stack). This URL is kept as a redirect so bookmarks/links survive: it opens the
        # stack's row on the overview. Unknown stack -> 404 (as before).
        if service.build_snapshot().stack(stack_id) is None:
            abort(404)
        return redirect(url_for("stacks_overview", open=stack_id) + "#stackrow-" + stack_id)

    @app.post("/stacks/<stack_id>/known-working/confirm")
    def known_working_confirm(stack_id: str):  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        if service.stack(stack_id) is None:
            abort(404)
        res = service.confirm_known_working(stack_id)
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("stacks_overview", open=stack_id) + "#stackrow-" + stack_id)

    @app.post("/interactive/<stack_id>/dismiss")
    def interactive_dismiss(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        service.dismiss_interactive(stack_id)
        return redirect(url_for("dashboard"))

    @app.get("/api/daemon/<band>")
    def daemon_api(band: str):  # noqa: ANN202
        if band not in ("433", "868"):
            abort(404)
        view = service.daemon_view(band)
        return jsonify(band=band, reachable=view.reachable, ready=view.ready,
                       radio_state=view.radio_state, status=view.status,
                       stats=view.stats, channel=view.channel,
                       feed=service.daemon_feed(band, 40))

    @app.get("/api/daemon/<band>/socket")
    def daemon_socket_api(band: str):  # noqa: ANN202
        # READ-ONLY live poll of the CONF socket for the "View Socket" monitor: one bounded,
        # sanitised status line per request (band validated -> never an arbitrary socket path;
        # fail-closed to '' when unreachable). The window/polling live entirely in the browser.
        if band not in ("433", "868"):
            abort(404)
        line = service.daemon_socket_line(band)
        return jsonify(band=band, line=line, reachable=bool(line))

    @app.post("/radio/<band>/set")
    def radio_set(band: str):  # noqa: ANN202
        # Apply a LIVE daemon setting (runtime) — same two-step plan + confirm as
        # every other mutation (P0.7). First POST shows the plan; a confirmed POST
        # applies. The key is whitelisted by the service; nothing transmits.
        if band not in ("433", "868"):
            abort(404)
        if not _csrf_ok():
            abort(400)
        key = request.form.get("key", "")
        value = request.form.get("value", "")
        if request.form.get("confirmed") != "yes":
            plan = service.daemon_set(band, key, value, apply=False)
            fsk = key.upper() == "MODE" and value.upper() == "FSK"
            return render_template("confirm_radio.html", version=__version__,
                                   runtime_root=_runtime_root(), band=band,
                                   key=key, value=value, plan=plan, fsk=fsk,
                                   warn_stacks=service.running_lora_stacks(band) if fsk else [])
        result = service.daemon_set(band, key, value, apply=True)
        # Truthful flash: the service summary already distinguishes "applied (confirmed)"
        # from "SENT (unconfirmed)". Never claim "Applied" for a setting the daemon does
        # not report back — an unconfirmed SET is flashed as a warning, not success.
        confirmed = bool(result.data.get("confirmed")) if result.data else False
        flash(result.summary, "ok" if (result.ok and confirmed) else "warn")
        return redirect(url_for("stacks_overview", band=band, cfg="daemon") + "#stack-settings-daemon")

    def _redirect_for(target: str):
        # Actions launched from the dashboard return to the dashboard; otherwise land on the
        # target's stack row on the /stacks overview (the detail page was folded in there).
        if request.form.get("from") == "dash":
            return redirect(url_for("dashboard"))
        sid = service.stack_of(target)
        return (redirect(url_for("stacks_overview", open=sid) + "#stackrow-" + sid)
                if sid else redirect(url_for("dashboard")))

    def _install_back(stack_id: str):
        # TARGET-SPECIFIC: reopen ONLY this stack's row + its Install panel and scroll there, so a
        # refused start lands on the Install/Build buttons instead of wherever the page last was.
        # Always /stacks — even for a dashboard-launched start, since the buttons only exist here.
        return redirect(url_for("stacks_overview", open=stack_id, inst=stack_id)
                        + "#stack-install-" + stack_id)

    # START-confirm run/file/daemon params share the daemon-flag hide set + the confirm form.
    _HIDE_RUN = {"radio", "tx_433", "tx_868", "cadmon_433", "cadmon_868",
                 "cadrssi_433", "cadrssi_868"}

    def _collect_start_params(target: str, band: str):
        """(params, file_overrides, have_form) from the submitted confirm form, keyed by component-
        aware API key (bare when unique, `component.name` when the name collides). `have_form` marks
        a submission of the confirm form itself (`_params=1`) vs the initial dashboard POST — only
        then are unchecked flag checkboxes read as OFF and `pf_*` file overrides collected."""
        have_form = request.form.get("_params") == "1"
        params: dict = {}
        file_over = {} if have_form else None
        for f in service.start_param_fields(target, band):
            if f["kind"] == "run":
                if f["flag"] and have_form:
                    params[f["key"]] = "1" if request.form.get(f["field"]) else ""
                else:
                    params[f["key"]] = request.form.get(f["field"], f["saved"])
            elif have_form:                                   # file overrides only on a real submit
                if f["flag"]:
                    file_over[f["key"]] = "1" if request.form.get(f["field"]) else ""
                else:
                    v = request.form.get(f["field"])
                    if v is not None:
                        file_over[f["key"]] = v
        return params, file_over, have_form

    def _stack_save_values(target: str, band: str, params: dict, file_over: dict | None) -> dict:
        """Map the confirm 'Stack parameters' rows into a `save_config_bundle` values dict, keyed by
        component-aware API key (run -> `<key>`, file -> `file_<key>`; `key` is bare when unique,
        `component.name` when duplicated). Only the SAVABLE section rows are persisted — a stack
        without a savable section (the daemon, whose run params are ephemeral start options) saves
        nothing here."""
        values: dict = {}
        for r in service.stack_start_params(target, band, params, file_over):
            if r["field"].startswith("pf_"):
                values[f"file_{r['key']}"] = r["value"]
            else:
                values[r["key"]] = r["value"]
        return values

    def _save_daemon_confirm(target: str) -> dict:
        """Persist the inline daemon-radio panel values (per band) from the confirm form. Returns a
        STRUCTURED per-band result — {"parse_error": str|None, "bands": {band: bool}, "ok": bool} —
        so the caller can report EXACTLY which bands persisted (truthful partial persistence) and
        never over-claim. `ok` is True only when there was no parse error and every submitted band
        saved (a submission with no daemon values is trivially ok)."""
        per_band, err = _parse_start_daemon_overrides(request.form)
        if err:
            return {"parse_error": err, "bands": {}, "ok": False}
        bands: dict = {}
        for b in sorted((per_band or {}).keys()):
            bands[b] = service.save_daemon_params(target, b, per_band[b]).ok
        return {"parse_error": None, "bands": bands, "ok": all(bands.values()) if bands else True}

    def _render_confirm(op: str, target: str, band: str, params: dict, file_over,
                        source: str, frm: str, enforce_field: str = ""):
        """Stage-1 (and post-Save / enforcement re-render) of the confirm page."""
        run_params = service.run_params_for(target) if op == "start" else []
        hidden_params = [p for p in run_params if p.name in _HIDE_RUN]
        stack_params = (service.stack_start_params(target, band, params, file_over)
                        if op == "start" else None)
        stack_param_groups = (service.stack_start_param_groups(target, band, params, file_over)
                              if op == "start" else None)
        # Run params covered by the savable 'Stack parameters' panel; the rest (e.g. the daemon's
        # ephemeral `debug` start flag) render as PLAIN inputs — start options, never persisted.
        covered = {r["name"] for r in (stack_params or []) if r["field"].startswith("p_")}
        plain_params = [p for p in run_params
                        if p.name not in _HIDE_RUN and p.name not in covered] if op == "start" else []
        # Submitted-but-unsaved daemon-panel values (best-effort parse; a malformed field is ignored
        # for DISPLAY only) so a re-render after a failed Save/enforcement keeps the operator's edits.
        _dp_display, _ = (_parse_start_daemon_overrides(request.form) if op == "start" else (None, ""))
        plan = service.run_action(op, target, apply=False, params=params, source=source, band=band)
        return render_template(
            "confirm.html", version=__version__, runtime_root=_runtime_root(),
            op=op, target=target, plan=plan, tx=("tx" in op),
            hidden_params=hidden_params, plain_params=plain_params,
            params=params, source=source, band=band,
            stack_params=stack_params, stack_param_groups=stack_param_groups,
            enforce_field=enforce_field,
            blockers=(plan.data.get("blockers") if op == "start" else None),
            stop_deps=(plan.data.get("dependents") if op == "stop" else None),
            other_bands=(plan.data.get("other_bands") if op == "stop" else None),
            commands=(plan.data.get("commands") if op in ("start", "stop") else None),
            missing_deps=(service.missing_system_deps(target)
                          if op in ("install", "build") else None),
            daemon_panels=(service.daemon_start_panels(target, params, band, _dp_display)
                           if op == "start" else None),
            optional_starts=(service.optional_start_components(target)
                             if op == "start" else None),
            frm=frm,
            source_choices=service.SOURCE_CHOICES if op in ("install", "update") else None)

    @app.post("/action")
    def action():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        op = request.form.get("op", "")
        target = request.form.get("target", "")
        if op not in service.WEB_ACTIONS:
            abort(400)
        band = request.form.get("band", "")    # chosen band for a band-switchable stack
        # Collect run + file params (only meaningful for start). The confirm 'Stack parameters'
        # panel submits p_<name> (run) / pf_<name> (file); defaults come from the saved config.
        params, file_over, _have = (_collect_start_params(target, band)
                                    if op == "start" else ({}, None, False))
        # Source version selector (only meaningful for install/update). A MISSING selector defaults
        # to 'dev' (the remote branch tip) — the confirm page preselects it, and every normal POST
        # carries the operator's explicit choice. An INVALID selector is rejected by run_action
        # (never silently rewritten). Note a 'dev' checkout of a component that declares a
        # `pin_commit` reads `src: differs` forever, by design: clean, just not at the pin.
        source = request.form.get("source", "dev")
        stop_owners = request.form.get("stop_owners") == "yes"
        cascade = request.form.get("cascade") == "yes"
        frm = request.form.get("from", "")     # origin page (e.g. "dash") for redirect
        save_mode = request.form.get("_save", "")           # "stack" | "daemon" | ""
        # Refuse to start an app that isn't installed or built yet — send the operator to THIS
        # stack's Install section (which has the Install/Build buttons, and repeats the reason in a
        # banner) with a warning and the CLI command, rather than spawning a doomed start.
        # `open=` forces the row, `inst=` forces + scrolls to the Install panel (cf. `_dp_back`).
        if op == "start" and service.stack(target) is not None:
            if not service.is_installed(target):
                flash(f"'{target}' is not installed yet — install it on this page "
                      f"(or run: lhpc install {target}).", "warn")
                return _install_back(target)
            unbuilt = service.unbuilt_components(target)
            if unbuilt:
                flash(f"'{target}' needs building before it can run — its binary is "
                      f"missing ({', '.join(unbuilt)}). Build it on this page "
                      f"(or run: lhpc build {target}).", "warn")
                return _install_back(target)
        # Panel "Save" / "Save & start". FAIL CLOSED: for Save & start the requested start happens
        # ONLY after EVERY selected persistence succeeds. Any failure (parse, stack or per-band
        # daemon write) blocks the start and re-renders the confirm with the SUBMITTED values +
        # visible errors — nothing is started, reconfigured, generated or recorded.
        if op == "start" and save_mode in ("stack", "daemon", "all"):
            then_start = request.form.get("_save_then_start") == "1"
            # Validate the daemon form BEFORE the first write, so a malformed field fails closed
            # without persisting anything.
            if save_mode in ("daemon", "all"):
                _pb, _derr = _parse_start_daemon_overrides(request.form)
                if _derr:
                    flash(f"Cannot save daemon parameters: {_derr}", "warn")
                    return _render_confirm(op, target, band, params, file_over, source, frm)
            stack_ok = True
            stack_values = _stack_save_values(target, band, params, file_over)
            if save_mode in ("stack", "all") and stack_values:
                res = service.save_config_bundle(target, values=stack_values, band=band)
                flash(res.summary + (" " + "; ".join(res.details) if res.details else ""),
                      "ok" if res.ok else "warn")
                stack_ok = res.ok
            # Save & start SHORT-CIRCUIT: a failed stack save must NOT trigger the daemon save and
            # must NOT start — re-render immediately with the submitted values. (`_save_daemon_confirm`
            # / `save_daemon_params` are never reached.)
            if then_start and not stack_ok:
                flash("Not started — the stack configuration could not be saved.", "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm)
            daemon_res = {"parse_error": None, "bands": {}, "ok": True}
            if save_mode in ("daemon", "all"):
                daemon_res = _save_daemon_confirm(target)
                if daemon_res["parse_error"]:
                    flash(f"Daemon parameters not saved: {daemon_res['parse_error']}", "warn")
                for _b, _bok in daemon_res["bands"].items():
                    flash(f"Daemon {_b} MHz: {'saved' if _bok else 'save FAILED'}",
                          "ok" if _bok else "warn")
            if not then_start:
                # Save-only: re-render with the (now saved) config — never starts.
                fresh = service.stack_config(target, band)
                return _render_confirm(op, target, band, dict(fresh), None, source, frm)
            if not (stack_ok and daemon_res["ok"]):
                # Save & start, but a later save failed (a per-band DAEMON save after a successful/
                # absent stack save) -> DO NOT start. Earlier successful saves are RETAINED. Report
                # PRECISELY what did and did not persist (never over-claim the stack save — during
                # `_save=daemon` no stack config is written), and re-render with the submitted values.
                saved, not_saved = [], []
                if save_mode in ("stack", "all") and stack_values:
                    (saved if stack_ok else not_saved).append("stack config")
                for _b, _bok in daemon_res["bands"].items():
                    (saved if _bok else not_saved).append(f"daemon {_b} MHz")
                parts = []
                if saved:
                    parts.append("saved: " + ", ".join(saved))
                if not_saved:
                    parts.append("NOT saved: " + ", ".join(not_saved))
                flash("Not started — a save failed" + (f" ({'; '.join(parts)})" if parts else "")
                      + ". Fix it and try again.", "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm)
            # every selected save succeeded -> fall through to the apply below
        if request.form.get("confirmed") != "yes":
            # Stage 1: show the dry-run plan, options and a confirmation form.
            return _render_confirm(op, target, band, params, file_over, source, frm)
        # Stage 2: apply.
        # Optional-component start choices (KISS Serial, GPS relay, …): the checkbox IS
        # the durable auto-start config option — persist a CHANGED choice before the
        # start, so `_run_order` includes/excludes the component for this and every
        # later run. A failed save blocks the start (never a silently ignored choice).
        if op == "start" and service.stack(target) is not None:
            opts = service.optional_start_components(target)
            changed = {f"autostart_{o['id']}":
                       ("on" if request.form.get(f"opt_start_{o['id']}") == "on" else "")
                       for o in opts
                       if (request.form.get(f"opt_start_{o['id']}") == "on")
                       != o["autostart"]}
            if changed:
                saved = service.save_config_bundle(target, values=changed)
                if not saved.ok:
                    flash(f"Not started — the optional-component choice could not be "
                          f"saved: {saved.summary}", "warn")
                    return _render_confirm(op, target, band, params, file_over, source,
                                           frm)
        # DESTRUCTIVE clean: additionally requires the operator to TYPE the stack id —
        # a mismatch re-renders the confirm with ZERO mutation.
        purge = False
        if op == "clean":
            typed = (request.form.get("confirm_text") or "").strip()
            if typed != target:
                flash(f"Clean not applied: type the stack id '{target}' exactly to confirm "
                      "the destructive purge.", "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm)
            purge = True
        # install/build/test run as detached jobs streaming to a log -> show it live.
        if op in ("install", "build", "test"):
            # Gate install/build on system dependencies: never proceed while a
            # required dev package / header / device node is missing.
            missing = service.missing_system_deps(target) if op in ("install", "build") else []
            if missing:
                flash("System dependencies missing — install them first: "
                      + "; ".join(d["install"] for d in missing if d["install"]), "warn")
                return _redirect_for(target)
            job, err = service.spawn_web_job(op, target, source=source)
            if err:
                flash(err, "warn")
                return _redirect_for(target)
            flash(f"{op} started — watch the live output below (it shows when it ends).", "ok")
            return redirect(url_for("logs_view", target=target, job=job))
        # Ephemeral PER-BAND daemon-param values from the confirm panel(s). STRICT parse of EVERY
        # dp_* field: a malformed/duplicated field shape is a visible start failure BEFORE any
        # launch; unknown band/param/value are validated by the service normalizer. Band-scoped
        # dp_<band>_<PARAM> keeps 433 and 868 separate; values are applied for THIS launch only.
        daemon_overrides = None
        if op == "start":
            daemon_overrides, dp_err = _parse_start_daemon_overrides(request.form)
            if dp_err:
                flash(f"Cannot start '{target}': {dp_err}", "warn")
                return _redirect_for(target)
            # CALL/node enforcement (UX): block the start and RE-RENDER the confirm with the
            # 'Stack parameters' panel expanded and the offending field highlighted, so the operator
            # can supply the call/node in place. The service layer enforces this authoritatively too.
            id_ok, id_field, id_msg = service.enforce_identity(target, band, params, file_over)
            if not id_ok:
                flash(id_msg, "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm,
                                       enforce_field=id_field)
        result = service.run_action(op, target, apply=True, params=params, source=source,
                                    stop_owners=stop_owners, cascade=cascade, band=band,
                                    daemon_overrides=daemon_overrides, file_overrides=file_over,
                                    purge=purge)
        # An interactive/systemd START whose ONLY non-success is the expected MANUAL_REQUIRED (e.g.
        # chat: daemon up + readied, operator runs the TUI) is a success, not a warning.
        # START-ONLY: on a stop/restart, MANUAL_REQUIRED means "a foreign process is still running,
        # kill it yourself" — a WARNING. `stop` already returns ok=False; only this display lied.
        ok_flash = result.ok or (op == "start" and manual_required_only(result.results))
        flash(f"{result.summary} {' '.join(result.details[:6])}",
              "ok" if ok_flash else "warn")
        # Start note(s) for a just-started component (boot expectations, connect hints):
        # YELLOW and LONG-LIVED (30 s) — a 1–2 min boot warning must outlast the quick
        # green flashes.
        if op == "start":
            for note in service.start_notes(result):
                flash(note, "warn transient-long")
        return _redirect_for(target)

    def _safe_job(value):
        # only a plain logs/<name>.log filename, never a path
        return value if value and "/" not in value and ".." not in value else None

    def _safe_band(value):
        # `band` picks the instance of a band-scoped component and reaches a FILENAME
        # (start-<id>-<band>.log), so it must pass the canonical whitelist, never raw input.
        # Empty/invalid -> "" (the resolver then picks the newest band's log).
        if not value:
            return ""
        try:
            return validators.band(value, allow_both=False)
        except validators.ValidationError:
            return ""

    @app.get("/logs/<target>")
    def logs_view(target: str):  # noqa: ANN202
        if service.stack_of(target) is None:
            abort(404)
        job = _safe_job(request.args.get("job"))
        band = _safe_band(request.args.get("band"))
        path, lines = service.log_tail(target, 300, job=job, band=band)
        return render_template("logs.html", version=__version__,
                               runtime_root=_runtime_root(), target=target, job=job,
                               stack_id=service.stack_of(target), path=path, lines=lines,
                               running=service.log_running(target, job))

    @app.get("/api/logs/<target>")
    def logs_api(target: str):  # noqa: ANN202
        if service.stack_of(target) is None:
            abort(404)
        job = _safe_job(request.args.get("job"))
        band = _safe_band(request.args.get("band"))
        path, lines = service.log_tail(target, 300, job=job, band=band)
        return jsonify(target=target, path=path, lines=lines,
                       running=service.log_running(target, job))

    @app.post("/stacks/<stack_id>/config")
    def stack_config_save(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        view = service.config_view(stack_id, band)
        # Fold each submitted Config-page field (`c_`/`f_`, component-qualified when the name
        # collides) into the canonical API key (`name` or `component.name`; file keys carry the
        # `file_` prefix) BEFORE save — so duplicate names never flatten and unqualified duplicates
        # are rejected by the canonical bundle path.
        values = {}
        for f in service.config_param_fields(stack_id, band):
            if f["flag"]:
                v = "1" if request.form.get(f["field"]) else ""
            else:
                v = request.form.get(f["field"], f["default"])
            values[f"file_{f['key']}" if f["kind"] == "file" else f["key"]] = v
        # Auto-start toggles for the stack's optional components.
        for opt in view["optional"]:
            values[f"autostart_{opt['id']}"] = (
                "on" if request.form.get("c_autostart_" + opt["id"]) else "")
        # Per-component GitHub remote overrides as a COMPLETE map (blank reverts to
        # the default). The whole submission is validated and persisted as ONE
        # all-or-recoverable transaction — no per-remote sequential writes.
        remotes = None
        if view["sources"]:
            remotes = {}
            for src in view["sources"]:
                field = request.form.get("remote_" + src["id"], src["remote"])
                remotes[src["id"]] = "" if field.strip() == src["default"] else field.strip()
        result = service.save_config_bundle(
            stack_id, values=values,
            callsign=request.form.get("op_callsign"),
            locator=request.form.get("op_locator"), band=band, remotes=remotes)
        flash(result.summary + (" " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", band=band or None, cfg=stack_id)
                        + "#stack-settings-" + stack_id)

    def _daemon_param_form():  # (band, {PARAM: value}) from the submitted panel
        from lhpc.core import daemon_params as _dp
        band = request.form.get("band", "")
        return band, {name: request.form.get("dp_" + name, "") for name in _dp.ALL_PARAMS}

    def _dp_back(stack_id: str, band: str):
        # TARGET-SPECIFIC: reopen ONLY this stack's row + its daemon-params panel (never every
        # daemon panel on the page). `dp=<stack_id>` opens just the matching per-stack panel; the
        # `open=<stack_id>` forces the row and the anchor scrolls to it.
        return redirect(url_for("stacks_overview", band=band or None,
                                open=stack_id, dp=stack_id)
                        + "#stack-daemon-params-" + stack_id)

    @app.post("/stacks/<stack_id>/daemon-params")
    def daemon_params_save(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band, values = _daemon_param_form()
        result = service.save_daemon_params(stack_id, band, values)
        flash(result.summary, "ok" if result.ok else "warn")
        return _dp_back(stack_id, band)

    @app.post("/stacks/<stack_id>/daemon-params/apply")
    def daemon_params_apply(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band, values = _daemon_param_form()
        save = service.save_daemon_params(stack_id, band, values)   # persist first
        if not save.ok:
            flash(save.summary, "warn")
            return _dp_back(stack_id, band)
        result = service.apply_daemon_params(stack_id, band)         # then push live
        # Truthful: partial/total apply failure flashes as a warning (never green), and the
        # timing override stays saved regardless.
        flash("Saved. Apply live: " + result.summary
              + (" — " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return _dp_back(stack_id, band)

    @app.post("/stacks/<stack_id>/daemon-params/reset")
    def daemon_params_reset(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        result = service.reset_daemon_params(stack_id, band)
        flash(result.summary, "ok" if result.ok else "warn")
        return _dp_back(stack_id, band)

    @app.post("/stacks/<stack_id>/config/reset")
    def stack_config_reset(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        result = service.reset_config(stack_id, band)
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", band=band or None, cfg=stack_id)
                        + "#stack-settings-" + stack_id)

    @app.get("/healthz")
    def healthz():  # noqa: ANN202
        # Cheap liveness: manifest parses; does NOT run the full probe sweep.
        stacks = service.stacks()
        return jsonify(status="ok", stacks=len(stacks), version=__version__)

    @app.errorhandler(404)
    def _not_found(_err):  # noqa: ANN001, ANN202
        return render_template("error.html", code=404, message="Not found"), 404

    @app.errorhandler(405)
    def _method_not_allowed(_err):  # noqa: ANN001, ANN202
        return render_template("error.html", code=405, message="Method not allowed"), 405

    @app.errorhandler(Exception)
    def _unexpected(err):  # noqa: ANN001, ANN202
        # HTTP errors (404/405/400 …) keep their own status/handling.
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException):
            return err
        # Last-resort boundary: an UNEXPECTED escape (e.g. a runtime-root FS/containment
        # error not already typed at the service layer) renders a clean, typed message —
        # never a traceback (debug/reloader are off). Expected failures are still typed
        # ActionResults upstream; this only stops a stray exception leaking a stack trace.
        app.logger.exception("unexpected error handling %s", request.path)
        return render_template("error.html", code=500,
                               message="Internal error — see the server log."), 500

    # ---- Webserver: controller-owned component, rendered INLINE in the controller row on
    # /stacks (no separate page). This route only redirects old bookmarks to that anchor.
    @app.route("/stacks/loraham-pi-control")
    def controller_webserver():  # noqa: ANN202
        return redirect(url_for("stacks_overview") + "#webserver-row")

    @app.get("/webserver/logs")
    def webserver_logs():  # noqa: ANN202
        # The LHPC-managed nginx front-end's on-disk access/error logs. Read-only, cached-only:
        # a bounded no-follow tail (no service probe). `src` is whitelisted to error|access.
        src = "access" if request.args.get("src") == "access" else "error"
        path, lines = service.webserver_log_tail(src, 300)
        return render_template("webserver_logs.html", version=__version__,
                               runtime_root=_runtime_root(), src=src, path=path, lines=lines)

    @app.get("/controller/logs")
    def controller_logs():  # noqa: ANN202
        # The controller's OWN process logs — on-disk files under logs/ (StandardOutput=append:).
        # Read-only, non-network, bounded no-follow file tail; `src` whitelisted to web|selfupdate.
        src = "selfupdate" if request.args.get("src") == "selfupdate" else "web"
        unit = "lhpc-selfupdate.service" if src == "selfupdate" else "lhpc-web.service"
        path, lines = service.controller_log_tail(src, 300)
        return render_template("controller_logs.html", version=__version__,
                               runtime_root=_runtime_root(), src=src, unit=unit,
                               path=path, lines=lines)

    def _ws_back():
        return redirect(url_for("stacks_overview") + "#webserver-row")

    @app.route("/webserver/configure", methods=["POST"])
    def webserver_configure():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        f = request.form
        fields = {}
        if f.get("bind"):
            fields["bind"] = f["bind"]
        if f.get("port"):
            fields["port"] = f["port"]
        if f.get("access_mode"):
            fields["access_mode"] = f["access_mode"]
        if f.get("scheme"):
            fields["scheme"] = f["scheme"]
        if "dns_sans" in f:
            fields["dns_sans"] = [x.strip() for x in f.get("dns_sans", "").split(",") if x.strip()]
        if "ip_sans" in f:
            fields["ip_sans"] = [x.strip() for x in f.get("ip_sans", "").split(",") if x.strip()]
        r = service.webserver_configure(**fields)
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.post("/stacks/<stack_id>/webserver")
    def stack_web_configure(stack_id: str):  # noqa: ANN202
        """Per-stack web-UI proxy policy. Same typed-confirmation contract as /webserver/expose."""
        if not _csrf_ok():
            abort(400)
        if service.stack(stack_id) is None or service.stack_web_upstream(stack_id) is None:
            abort(404)                                   # unknown stack, or it has no web UI
        f = request.form
        cidrs = [x.strip() for x in f.get("cidrs", "").split(",") if x.strip()]
        phrase = f.get("confirm_phrase", "").strip()
        r = service.stack_web_configure(
            stack_id,
            mode=(f.get("mode") or None),
            port=(f.get("port") or None),
            scheme=(f.get("scheme") or None),
            access_mode=(f.get("access_mode") or None),
            cidrs=cidrs if "cidrs" in f else None,
            confirm=phrase in ("enable-remote", "enable-remote-danger"),
            confirm_public=(phrase == "enable-remote-danger"))
        # A successful save is only INTENT — surface it as a warning (yellow), not a green "done",
        # so the operator sees it still needs Apply. Details (incl. any bypass warning) ride along.
        flash(r.summary + (" — click Apply to make it live." if r.ok else ""),
              "warn" if r.ok else "err")
        for d in r.details:
            flash(d, "warn" if r.ok else "err")
        return redirect(url_for("stacks_overview", cfg=stack_id)
                        + "#stack-webserver-" + stack_id)

    @app.route("/webserver/expose", methods=["POST"])
    def webserver_expose():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        f = request.form
        cidrs = [x.strip() for x in f.get("cidrs", "").split(",") if x.strip()]
        # Typed confirmation (not a checkbox): 'enable-remote' for a normal LAN range,
        # 'enable-remote-danger' for the elevated case (public 0.0.0.0/0 or no-auth remote).
        phrase = f.get("confirm_phrase", "").strip()
        r = service.webserver_expose(cidrs, access_mode=(f.get("access_mode") or None),
                                     confirm=phrase in ("enable-remote", "enable-remote-danger"),
                                     confirm_public=(phrase == "enable-remote-danger"))
        flash(r.summary, "ok" if r.ok else "err")
        for d in r.details:
            flash(d, "info" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/disable-remote", methods=["POST"])
    def webserver_disable_remote():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        r = service.webserver_disable_remote()
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/reset", methods=["POST"])
    def webserver_reset():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        if request.form.get("confirm_phrase", "") != "reset":
            flash("type 'reset' to confirm reset-to-default", "err")
            return _ws_back()
        r = service.webserver_reset_defaults()
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/verify", methods=["POST"])
    def webserver_verify():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        r = service.webserver_verify()
        flash(r.summary, "ok" if r.ok else "warn")
        return _ws_back()

    @app.route("/webserver/apply", methods=["POST"])
    def webserver_apply():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        r = service.webserver_apply()
        flash(r.summary, "ok" if r.ok else "err")
        for d in r.details:
            flash(d, "warn")
        return _ws_back()

    @app.route("/webserver/init", methods=["POST"])
    def webserver_init():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        # First-time init on a fresh PKI needs no phrase; RE-initializing (destructive) requires
        # the typed phrase 'recreate'.
        confirm = request.form.get("confirm_phrase", "").strip() == "recreate"
        r = service.webserver_init(confirm=confirm)
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/tls-renew", methods=["POST"])
    def webserver_tls_renew():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        r = service.webserver_tls_renew()
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/cert", methods=["POST"])
    def webserver_cert():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        f = request.form
        op, label = f.get("op", ""), f.get("label", "")
        if op in ("issue", "reissue"):
            pw = _secrets.token_urlsafe(18)     # one-time; shown once, never persisted/logged
            fn = service.webserver_cert_issue if op == "issue" else service.webserver_cert_reissue
            r = fn(label, pw)
            if r.ok:
                flash(f"{r.summary}. One-time passphrase (record it now): {pw}", "ok")
                if peer_is_loopback():
                    flash("Bundle ready — download it now from this loopback session.", "info")
                else:
                    flash("Bundle created; a NEW bundle can be downloaded only from a "
                          "loopback session (remote download is refused).", "warn")
            else:
                flash(r.summary, "err")
        elif op == "revoke":
            # Typed confirmation: the operator must type the exact certificate label to revoke it.
            if f.get("confirm_phrase", "").strip() != label:
                flash(f"type the certificate label '{label}' to confirm revocation", "err")
            else:
                r = service.webserver_cert_revoke(label)
                flash(r.summary, "ok" if r.ok else "err")
        elif op == "discard":
            flash(service.webserver_cert_discard_export(label).summary, "ok")
        else:
            flash("unknown certificate action", "err")
        return _ws_back()

    @app.route("/webserver/cert/<label>/download")
    def webserver_cert_download(label):  # noqa: ANN202
        # LOOPBACK-ONLY: a remotely-authenticated browser must never pull a new private key.
        if not peer_is_loopback():
            abort(403)
        try:
            blob = service.webserver_cert_export_bytes(label)
        except Exception:
            abort(404)
        if not blob:
            abort(404)
        from flask import Response
        safe = label.replace('"', "").replace("\\", "")
        return Response(blob, mimetype="application/x-pkcs12",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.p12"',
                                 "Cache-Control": "no-store"})

    return app


# Periodic background update check: default cadence + hard bounds. 0 disables the loop
# (the one startup check still runs). Clamped so a typo can neither hammer upstream
# (min 1h) nor silently never check (max 7 days).
UPDATE_CHECK_DEFAULT_HOURS = 12
UPDATE_CHECK_MIN_HOURS = 1
UPDATE_CHECK_MAX_HOURS = 168


def update_check_interval_s() -> float:
    """Resolve `[web] update_check_hours` from config/local.toml to seconds (0 = disabled).
    Bad type / out-of-range values fall back to the clamped default — never an exception
    (this runs on server startup)."""
    try:
        from lhpc.core.config import load_config
        from lhpc.core.paths import resolve_paths
        raw = load_config(resolve_paths()).get("web", "update_check_hours",
                                               UPDATE_CHECK_DEFAULT_HOURS)
    except Exception:
        raw = UPDATE_CHECK_DEFAULT_HOURS
    if not isinstance(raw, int) or isinstance(raw, bool):
        raw = UPDATE_CHECK_DEFAULT_HOURS
    if raw == 0:
        return 0.0
    return float(min(max(raw, UPDATE_CHECK_MIN_HOURS), UPDATE_CHECK_MAX_HOURS)) * 3600.0


def run_server(host: str = "127.0.0.1", port: int = 8770, socket: bool = False) -> int:
    """Run the console. Two serving modes:

      * PRODUCTIVE (``socket=True``): serve over a protected Unix-domain socket under the
        runtime root (``state/run/lhpc-web.sock``, 0600) — NO TCP listener. This is the
        backend behind the Nginx TLS/mTLS boundary. Waitress is MANDATORY here: if it is
        absent we FAIL CLOSED (never the Flask dev server) — productive serving must never
        silently degrade.
      * INTERACTIVE (default, ``socket=False``): a loopback-only TCP bind for bare local use;
        Waitress preferred, Flask dev server only as a loud non-productive fallback.
    """
    if not socket and host not in _LOOPBACK_HOSTS:
        print(
            f"ERR  refusing to bind '{host}': the operator console is loopback-only "
            f"(allowed: {', '.join(sorted(_LOOPBACK_HOSTS))}).\n"
            "     Non-loopback access would require explicit opt-in, auth and HTTPS\n"
            "     (or a documented trusted reverse proxy)."
        )
        return 1
    # Controller-runtime lock (B6): acquire it SHARED, NON-BLOCKING, BEFORE the startup
    # self-check or binding the socket, and hold it for the ENTIRE serving lifetime. If a
    # self-update holds it EXCLUSIVE, fail CLOSED here — never serve with unlocked source
    # that an apply could `reset --hard`/`clean` underneath the running process. The lock is
    # released in the contextmanager's finally on ALL exit paths, incl. startup failure.
    from lhpc.core import selfupdate as _su
    from lhpc.core.paths import resolve_paths as _resolve_paths
    try:
        _lock_cm = _su.controller_runtime_lock(_resolve_paths(), exclusive=False)
        _lock_cm.__enter__()
    except _su.ControllerRuntimeBusy:
        print("ERR  a self-update is in progress (controller-runtime lock held) — "
              "not starting the web server. Retry once self-update finishes.")
        return 1
    except _su.ControllerRuntimeLockError as exc:
        print(f"ERR  could not acquire the controller-runtime lock ({exc}) — not starting.")
        return 1
    try:
        app = create_app()
        if socket:
            # PRODUCTIVE mode (behind nginx). Two separate facts, deliberately not one flag:
            #   * the trusted-host policy must be enforced whenever we serve through nginx;
            #   * Secure cookies only make sense when the LISTENER is https — a browser DROPS a
            #     Secure cookie over plain http, which would silently break the CSRF session.
            # Gating `_trusted_host` on SESSION_COOKIE_SECURE (as before) would therefore have
            # switched the host allowlist OFF the moment an operator chose an http console.
            try:
                from lhpc.core.config import load_config as _load_config
                _scheme = _load_config(_resolve_paths()).webserver.scheme
            except Exception:                            # noqa: BLE001 — unreadable config -> https
                _scheme = "https"
            app.config.update(LHPC_PRODUCTIVE=True,
                              SESSION_COOKIE_SECURE=(_scheme == "https"))
        # Best-effort, NON-BLOCKING upstream freshness checks (process startup + a slow
        # periodic loop — NOT GET routes) so the footer's "Update →" indicator appears
        # without the operator pressing "Check for updates". One check right away (existing
        # behavior), then every `[web] update_check_hours` (config/local.toml; default 12,
        # clamped 1..168, 0 = disabled → startup check only). self_update_check serializes
        # via the update lock and defers when busy; failures are swallowed — the footer
        # simply keeps the last cached state.
        import threading

        interval_s = update_check_interval_s()

        def _selfcheck_loop():
            while True:
                try:
                    ControllerService().self_update_check()
                except Exception:
                    pass
                # Per-component source freshness, same cadence and same knob. Its OWN try/except:
                # a failing stack sweep must never kill the console's self-update cadence.
                try:
                    ControllerService().source_check()
                except Exception:
                    pass
                if interval_s <= 0:
                    return                       # periodic checks disabled: startup check only
                # Sleep in short slices so a daemon-thread teardown never blocks exit paths.
                deadline = time.monotonic() + interval_s
                while time.monotonic() < deadline:
                    time.sleep(min(30.0, max(0.1, deadline - time.monotonic())))

        threading.Thread(target=_selfcheck_loop, name="lhpc-selfcheck", daemon=True).start()
        try:
            from waitress import serve as _waitress_serve
        except ImportError:
            _waitress_serve = None
        if socket:
            # PRODUCTIVE: Unix socket only, Waitress MANDATORY. No TCP, no dev-server fallback.
            if _waitress_serve is None:
                print("ERR  waitress is required for productive (Unix-socket) serving but is "
                      "not installed — refusing to start (productive serving must not fall back "
                      "to the Flask development server). Install the declared 'waitress' "
                      "dependency in the venv.")
                return 1
            from lhpc.core import runtime_fs as _rfs
            from lhpc.core.webserver import WAITRESS_SOCK as _SOCK
            paths = _resolve_paths()
            _rfs.mkdir(paths, "state", "run")
            sock_path = str(paths.under(*_SOCK))
            # Clear a stale socket leaf (no-follow, contained) so bind() cannot fail on leftovers.
            try:
                _rfs.unlink(paths, paths.under(*_SOCK))
            except (FileNotFoundError, OSError):
                pass
            print(f"OK   LoRaHAM Pi Control console on unix socket {sock_path} (behind nginx).")
            print("     No TCP listener. Press Ctrl-C to stop.")
            # The unit's `status` output shows no journal lines (output goes to logs/lhpc-web.log),
            # and this process owns no TCP port — nginx does. Publish the reachable URL(s) as the
            # unit's Status: line via sd_notify. Best effort: any failure is swallowed, and
            # Type=simple means there is no readiness handshake to miss.
            # (No service control here: this is a datagram to $NOTIFY_SOCKET, not a bus call.)
            try:
                from lhpc.core import sdnotify
                from lhpc.core.config import load_config as _load_config
                from lhpc.core.webserver import console_urls as _console_urls
                _wcfg = _load_config(paths).webserver
                _urls = _console_urls(_wcfg)
                _where = "remote" if _wcfg.remote_exposed else "loopback-only"
                sdnotify.notify_status(f"{' · '.join(_urls)} ({_where})")
            except Exception:                        # noqa: BLE001 — never block startup
                pass
            _waitress_serve(app, unix_socket=sock_path, unix_socket_perms="600",
                            threads=8, ident="lhpc")
            return 0
        # INTERACTIVE loopback TCP (bare local use). Waitress preferred; Flask dev server only
        # as a loud, explicitly non-productive fallback.
        print(f"OK   LoRaHAM Pi Control console at http://{host}:{port}/")
        print("     Loopback-only. Press Ctrl-C to stop.")
        if _waitress_serve is not None:
            _waitress_serve(app, host=host, port=port, threads=8, ident="lhpc")
        else:
            print("WARN waitress not installed — using the Flask dev server (OK for local "
                  "interactive use only, NOT the supported productive mode).\n"
                  "     For the systemd deployment, install 'waitress' in the venv "
                  "(see docs/deployment.md).")
            app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return 0
    finally:
        _lock_cm.__exit__(None, None, None)

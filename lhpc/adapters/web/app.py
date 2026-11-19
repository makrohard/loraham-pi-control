"""Local operator web console (Flask, server-rendered).

The web layer is thin: every route renders fresh state or dispatches an action
through `ControllerService` (the same service layer as the CLI — it never shells
out to the CLI). GET routes are read-only; state-changing actions are POST-only and
CSRF-protected. Lifecycle actions (`/action`), power, Wi-Fi changes, live radio settings and
self-update show a plan + confirmation before applying (a routine Start/Restart runs directly);
settings saves apply on the first POST.

Security posture:
  * loopback bind only (enforced in `run_server`); never 0.0.0.0;
  * GET = read-only; mutations = POST + CSRF token; unknown stack -> 404;
  * local-console security headers on every response (incl. CSP default-src 'self');
  * Jinja autoescaping escapes all untrusted data;
  * each request builds a fresh snapshot (no stale controller process state).
"""

from __future__ import annotations

import ipaddress as _ipaddress
import json as _json
import logging as _logging
import re as _re
import secrets as _secrets
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

from flask import (
    Flask,
    Response,
    abort,
    flash,
    get_flashed_messages,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask import g as _g
from flask.globals import request_ctx
from markupsafe import Markup

from lhpc.adapters.web import submenus as _submenus
from lhpc.adapters.web.oneshot import OneShot
from lhpc.core import attention as _attention
from lhpc.core import auto_install_form, jobresult, stackupdates, web_client
from lhpc.core import config as _config
from lhpc.core import hotplug as _hotplug
from lhpc.core import instances as _instances
from lhpc.core import kill as _kill
from lhpc.core import meshcore_mode as _meshcore_mode
from lhpc.core import radios as _radios_mod
from lhpc.core import rflog as _rflog
from lhpc.core.ap_network import AP_CIDR_CONFIRM as _AP_CIDR_CONFIRM
from lhpc.core.services import ControllerService
from lhpc.core.status import (
    component_note,
    rollup_states,
    stack_dependencies,
)
from lhpc.version import __version__

_RUNNING = ("running", "degraded")

# The one-click update's request marker is written this long after the "Restarting" response was
# closed (time for any remaining output to flush): the marker starts the updater, whose
# Conflicts= stops this console at once, so a marker written before the page lost the page to
# nginx's 502 when the stop won.
_UPDATE_QUEUE_DELAY_S = 1.0


def _defer(delay: float, fn: Callable[[], None]) -> None:
    """Run `fn` once, `delay` seconds from now, on a daemon thread (tests replace this)."""
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def peer_is_loopback() -> bool:
    """Loopback-vs-remote decision for loopback-only actions (e.g. a new `.p12` download).

    Behind nginx, trusts ONLY the nginx-set `X-LHPC-Peer` header — nginx strips any
    client-supplied copy and sets it from the real `$remote_addr`. When the header is absent the
    app is not behind nginx (bare interactive TCP mode / tests): the socket's own peer address
    decides, so the header's absence alone never reads as loopback. A REMOTE client always
    traverses nginx, which always sets the header to `remote`."""
    from flask import request
    hdr = request.headers.get("X-LHPC-Peer", "")
    return hdr == "loopback" if hdr else request.remote_addr in _LOOPBACK_HOSTS


def served_via_nginx() -> bool:
    """True when THIS request reached the console THROUGH nginx (which sets `X-LHPC-Peer`), False when
    served directly by lhpc-web (the bare :8770 dev server / tests set no such header). Drives the
    console's running pill — a running nginx on another port must not colour a direct-dev session green."""
    from flask import request
    return bool(request.headers.get("X-LHPC-Peer"))


def _console_addr(console_port) -> str:
    """The reached `host:port` for the CONSOLE pill. On the raw dev server `request.host` already carries
    the port (e.g. 127.0.0.1:8770). Behind nginx `proxy_set_header Host $host` forwards a PORTLESS host, so
    reattach the nginx console listener port (the port the browser actually used)."""
    from flask import request
    if served_via_nginx():
        return f"{_url_host(request.host or '')}:{console_port}"
    return request.host or ""

# --- trusted-host helpers (see `_trusted_host`) --------------------------------------------------
_HOST_MAX = 260                          # a Host header can never legitimately exceed this


def _host_only(raw: str) -> str:
    """The bare host from a `Host` header value: port stripped, IPv6 brackets stripped, lowercased.

    `"[::1]:8443"` -> `"::1"`, `"pi.local:8443"` -> `"pi.local"`.  Bounded,
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


def _ws_fetch_commands(host: str, root: str, active_labels=None) -> dict:
    """Copy-paste `scp` commands (Linux PC as host, this box as guest) for the trust material:
    {"ca": <cmd>, "p12": {label: cmd}}. Built server-side so user, runtime root and the
    viewer-reached host are all REAL — the operator pastes, never edits. Only characters the
    host allow-list admits are echoed; a weird Host header yields no commands rather than a
    shell-hostile string.

    `active_labels` gates the .p12 commands to CURRENTLY-ACTIVE certificates, matching the
    browser Download link (a revoked cert's lingering export was still offered a
    fetch command — a bundle that cannot authenticate). None = no gating (kept for the direct
    unit tests of command construction)."""
    import getpass
    from pathlib import Path as _Path

    from lhpc.core import validators as _validators
    if not host or not set(host.lower()) <= (_HOST_ECHO_OK | {"[", "]"}):
        return {}
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        # Python <=3.12 raises KeyError when no login env var is set and the uid has no
        # passwd entry; 3.13 turned that into OSError. Either way: no commands, no 500.
        return {}
    out = {"ca": f"scp {user}@{host}:{root}/config/tls/server-ca/ca.crt lhpc-server-ca.crt",
           "p12": {}}
    try:
        exports = sorted(p.name[:-4]
                         for p in _Path(root, "config", "tls", "exports").glob("*.p12"))
    except OSError:
        exports = []
    allow = None if active_labels is None else set(active_labels)
    for label in exports:
        if allow is not None and label not in allow:
            continue                           # revoked/absent cert -> no fetch for its export
        try:
            # The same filename-safety the issuing path enforces — an on-disk name that fails
            # it is not ours and gets no shell-bound echo.
            _validators.path_component(label, field="cert label")
        except _validators.ValidationError:
            continue
        out["p12"][label] = f"scp {user}@{host}:{root}/config/tls/exports/{label}.p12 ."
    return out


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


def _row_inline(sid: str, jobs, restored_open, args) -> bool:
    """Whether the Stacks page renders `sid`'s settings body inline (open at first paint): a running
    job, the remembered-open cookie (unless another row is named), or `open`/`cfg`/`dp`/`inst`
    naming it. Every other row's body is fetched when it is opened (stacklazy.js)."""
    named = args.get("open") or args.get("cfg") or args.get("dp") or args.get("inst")
    return bool(jobs or (sid in restored_open and (not named or named == sid))
                or sid in (args.get("open"), args.get("cfg"), args.get("dp"), args.get("inst")))


_DETAILS_TAG = _re.compile(r"<details\b[^>]*>")


def _open_first_details(html: str) -> str:
    """U4: a level on its own page renders opened (` open` on its first <details> tag, if not there)."""
    m = _DETAILS_TAG.search(html)
    if m is None or _re.search(r"\sopen(?=[\s>])", m.group(0)):
        return html
    return html[:m.start()] + m.group(0)[:-1] + " open>" + html[m.end():]


class _FormScan(HTMLParser):
    """The POST forms of a rendered page: (action, {hidden field: [values]})."""

    def __init__(self):
        super().__init__()
        self.forms: list = []
        self._open = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and (a.get("method") or "").lower() == "post":
            self.forms.append((a.get("action") or "", {}))
            self._open = True
        elif tag == "input" and self._open and (a.get("type") or "").lower() == "hidden" and a.get("name"):
            self.forms[-1][1].setdefault(a["name"], []).append(a.get("value") or "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._open = False


def _renders_form(html: str, path: str, args, form) -> bool:
    """U4 (E3-1): whether `html` renders the form this POST came from: one posting to `path` (with
    the POST's own query) whose every hidden field, but the CSRF token and `return`, has the posted
    values. The fields a person fills in are not compared. Read on the page as it was before the
    action (`_menu_block_before`)."""
    scan = _FormScan()
    scan.feed(html)
    for action, hidden in scan.forms:
        act = urlsplit(action)
        if act.path != path or parse_qs(act.query) != {k: args.getlist(k) for k in args}:
            continue
        if all(form.getlist(k) == v for k, v in hidden.items() if k not in ("_csrf", "return")):
            return True
    return False


# U4: the Stacks redirect's query keys that only navigate; any other (`pw=` one-time passphrase, `bad=`
# marked fields) carries what the Stacks page shows once, so that redirect is kept.
_NAV_KEYS = frozenset({"open", "cfg", "dp", "inst", "fw", "comp", "wsg", "radio"})


def _row_needs_body(inline: bool, only_sid) -> bool:
    """Whether a Stacks row gets its settings-body fields built: only a row the page renders inline,
    or the one row a lazy body request asks for."""
    return inline or only_sid is not None


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
    # Cookie hardening. Secure is OFF here so the bare interactive http mode and the test clients
    # round-trip the session; run_server() turns it on for productive socket serving when the
    # configured listener scheme is https. (e.g. the bare
    # interactive TCP mode / tests) so an http test client still round-trips the session.
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=False)
    # The p12 passphrase, shown once: never in the session cookie (oneshot.py).
    _passphrases = app.extensions["lhpc_passphrases"] = OneShot()

    def _csrf_token() -> str:
        if "_csrf" not in session:
            session["_csrf"] = _secrets.token_hex(16)
        return session["_csrf"]

    def _csrf_ok() -> bool:
        # The stack web UIs are other ports of this host: the browser sends them this console's
        # (signed, readable) cookie and calls a form from them `same-site`, so the token alone does
        # not prove this console's page sent it. Without the header (the CLI, curl): token only.
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return False
        sent = request.form.get("_csrf", "")
        return bool(sent) and _secrets.compare_digest(sent, session.get("_csrf", ""))

    app.jinja_env.globals["csrf_token"] = _csrf_token

    # K4: an action result may name the panel it belongs to, `flash(text, "<severity> @<panel id>")`. The
    # flashes are read once per request into (severity, destination, text); a panel takes its own with
    # panel_results(<id>), and base.html shows every entry that no panel of this page took, so a result
    # whose panel is not on the page still shows (at the top). Without `@` an entry is global, as before.
    def _flash_entries() -> list:
        if "flash_entries" not in _g:
            _g.flash_entries, _g.flash_taken = [], set()
            for cat, msg in get_flashed_messages(with_categories=True):
                words = str(cat).split()
                dest = next((w[1:] for w in words if w.startswith("@")), "")
                _g.flash_entries.append((" ".join(w for w in words if not w.startswith("@")), dest, msg))
        return _g.flash_entries

    def _panel_results(dest: str) -> list:
        """(severity, text) of the results addressed to the panel `dest`, in flash order."""
        entries = _flash_entries()
        _g.flash_taken.add(dest)
        return [(sev, msg) for sev, d, msg in entries if d == dest]

    def _page_results() -> list:
        """(severity, text) of the results no panel took: the global ones and any whose panel is absent."""
        entries = _flash_entries()
        return [(sev, msg) for sev, d, msg in entries if not d or d not in _g.flash_taken]

    app.jinja_env.globals.update(panel_results=_panel_results, page_results=_page_results)

    @app.context_processor
    def _inject_selfupdate():
        # Footer version/head indicator, on EVERY page. Reads the cached state marker ONLY — no git,
        # no network, no subprocess — so it never violates the no-network-GET rule. Fail-safe.
        try:
            return {"selfupdate": service.self_update_status()}
        except Exception:
            return {"selfupdate": {"version": __version__, "head_short": "", "available": False,
                                   "ver_color": "grey", "commit_color": "grey",
                                   "update_available": False}}

    @app.context_processor
    def _inject_firewall_pending():
        # A Webserver Apply the firewall gate deferred leaves a durable marker until the watchdog
        # completes it after the firewall is verified. Every page shows a persistent notice with
        # the click path while that marker exists: one `stat`, no network, fail-safe.
        # CO-14: a self-update whose re-render of the stack proxies has not completed. The two notices are read
        # on their own, so one failing never hides the other; a re-render marker that cannot be read counts as
        # owed ("" = the generic reason).
        from lhpc.core import proxy_rerender
        try:
            apply_pending = bool(service.webserver_apply_pending())
        except Exception:
            apply_pending = False
        try:
            render_pending = proxy_rerender.pending(service._paths)
        except Exception:
            render_pending = ""
        return {"ws_apply_pending": apply_pending, "ws_render_pending": render_pending}

    @app.context_processor
    def _inject_old_radio_config():
        # W12 (stage L5): the old `[radio]` table with no hardware list -> a banner on EVERY page, nothing converted.
        # Tri-state: an unreadable old config is its own notice, never "no old config". Fails closed to a notice.
        from lhpc.core import radio_daemon
        try:
            st = radio_daemon.old_config_state(service._paths)
        except Exception:
            st = radio_daemon.Tri.UNKNOWN
        text = {radio_daemon.Tri.YES: radio_daemon.OLD_CONFIG_TEXT,
                radio_daemon.Tri.UNKNOWN: radio_daemon.OLD_CONFIG_UNREADABLE}.get(st, "")
        from lhpc.core import radio_start
        no_list = "" if text else radio_start.no_list_notice(service._paths)     # DEC-W384g: never silently empty
        return {"old_radio_notice": text[:1].upper() + text[1:] if text else "", "no_list_notice": no_list}

    @app.context_processor
    def _inject_disk_notice():
        # The disk-space warning on EVERY page while the worst filesystem is low or critical: the
        # ONE classifier's level, reason and figure (statvfs only). Fails closed to no notice.
        try:
            from lhpc.core.service_system import disk_figure, worst_disk_row
            worst = worst_disk_row(service.disk_health())
            if worst is None or worst["level"] == "ok":
                return {"disk_notice": None}
            return {"disk_notice": {"level": worst["level"], "figure": disk_figure(worst)}}
        except Exception:
            return {"disk_notice": None}

    @app.context_processor
    def _inject_hardware():
        # Read-only current radio-hardware setup for the footer, on EVERY page. Config read only.
        try:
            sid = service.hardware_setup()
            return {"hardware_footer": {"id": sid, "label": dict(service.hw_setups()).get(sid, sid),
                                        "configured": service.hardware_configured()}}
        except Exception:
            return {"hardware_footer": {"id": "unset", "label": "Not configured", "configured": False}}

    @app.after_request
    def _set_headers(response):
        for key, value in _SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.before_request
    def _fresh_snapshot():
        # The status snapshot is memoized WITHIN a request (a page render assesses it ~15×). Drop it
        # at the start of EVERY request so no request ever serves state from a previous one — freshness
        # is guaranteed; the memo only coalesces the repeated reads inside this one render.
        service.invalidate_snapshot()
        # Same boundary for the frozen `auto` GPS verdict: re-probe once per request so a gpsd
        # appearing/leaving is noticed, while every operation keeps ONE verdict internally.
        service.refresh_gps_auto()

    @app.before_request
    def _trusted_host():
        """Explicit trusted-host policy — ENFORCED IN EVERY SERVING MODE, including the plain-loopback
        interactive console (NOT keyed on Secure cookies or productive mode). It runs before any session
        is created, any CSRF token is used, and any mutation, so a rejected Host can never drive state.
        The REAL Host header is compared (Werkzeug `request.host` — the Host header or SERVER_NAME),
        NEVER a client-supplied X-Forwarded-Host (nginx blanks those and no ProxyFix trusts them).

        It defends against DNS REBINDING / Host spoofing, which both require a *name*: the attacker's
        page lives at `evil.example`, its record is flipped to this host's address, and the browser
        then sends `Host: evil.example` — rejected, because names must be configured in `dns_sans`.
        A bare IP literal cannot be rebound (a browser only sends one when the operator typed an IP),
        so while the console is REMOTELY EXPOSED any IP-literal Host is accepted. That is what makes a
        multi-homed Pi (eth0 + wlan0) and IPv6 work without enumerating interfaces. Loopback-only
        deployments keep the strict allowlist (localhost / 127.0.0.1 / [::1], with any port suffix).

        An empty, missing, malformed, or unrelated Host is rejected with 400.
        """
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
        except Exception:
            pass
        # A present, allowed Host passes; empty/missing/malformed/unrelated all fall through to 400.
        if host and _host_allowed(host, allowed, exposed):
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

    @app.errorhandler(_config.ConfigError)
    def _malformed_config(exc):
        # A stored config file is present but malformed/unreadable/oversized/wrong-typed. Fail closed:
        # a bounded, operator-repairable 409 — NEVER a traceback, and NEVER the malformed value echoed
        # back. The detail (path + parse error) goes only to the server log. The bad file is preserved.
        app.logger.warning("malformed stored config (409): %s", str(exc)[:500])
        body = ("409 Conflict: a stored configuration file is unreadable or malformed. It was left "
                "untouched for inspection. Repair or remove it (config/*.toml under the runtime root), "
                "then retry.\n")
        if getattr(exc, "reason", "") == _config.AP_CIDR_INVALID:   # W17: the recovery needs no console page
            body += ("The access point subnet (network.ap_cidr) is invalid: reset it on the box with\n"
                     f"  lhpc network --ap-cidr default --confirm-phrase {_AP_CIDR_CONFIRM}\n")
        return Response(body, status=409, mimetype="text/plain")

    def _runtime_root() -> str:
        return str(service.runtime_root)  # display only

    def _attention_banner() -> list:
        """W272: Home's "Needs attention" lines; a fault in the collector shows no banner, never an error page (the rows
        still show every condition)."""
        from lhpc.core import attention
        try:
            return attention.banner(service)
        except Exception:
            return []

    @app.post("/attention/dismiss")
    def attention_dismiss():
        """W273: the banner's one ✕ hides the set it shows until that set changes (+20 dBm stays)."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core import attention
        if not attention.dismiss(service, request.form.get("set", "")):
            flash("The attention list could not be closed right now; try again", "warn")
        return redirect(url_for("dashboard"))

    @app.get("/")
    def dashboard():
        # Radio-centric overview (W285/W286, DEC-W384bu/bv): one card per enabled radio with its own daemon, live
        # monitor, stacks and start control, then the §6 stray band daemons (their card + Stop kept, W290).
        from lhpc.core import radio_start as _radio_start
        strays: list = []                                # G1-K8D3 N2: one pass for the radio and the stray cards
        radios = [{**r, "log": _radio_start.start_log_name(service.DAEMON_ID, r["band"], r["rid"])}   # its daemon log
                  for r in service.radio_overview(strays)] + [{**x, "stray": True, "interactive": []} for x in strays]
        # W2 (G1-W2 3): an interactive app shows on the card of each radio its instance's marker names, with that
        # instance's Stop; a running entry without a rid is no instance lhpc can address (a stop reaches none of it).
        from lhpc.core import interactive_markers as _im
        marked = {e["id"] for r in radios for e in r["interactive"]}
        marked = {sid: [f.stem.partition("@")[2] for f in _im.markers(service, sid) if "@" in f.stem] for sid in marked}
        for r in radios:
            for e in r["interactive"]:
                e["rids"] = marked[e["id"]]
        # An interactive app that's been launched (marked) but isn't detected running yet, or a radio's daemon
        # start/restart job still running -> poll faster so the dash shows the outcome quickly.
        tasks, dsid = service.running_tasks(), service.stack_of(service.DAEMON_ID) or "daemon"
        jobs = {t.get("run_id") for t in tasks if t.get("kind") == "job" and t.get("state") == "running"}
        pending_interactive = (any(not s.get("running") and not s.get("blocker") for r in radios for s in r["interactive"])
                               or any(f"web-{op}-{dsid}-{r['rid']}.log" in jobs
                                      for r in radios if not r.get("stray") for op in ("start", "restart")))
        # Webserver box: LHPC console (always) + each running web-UI stack. Resolve the request-scoped
        # reached address + log href here (the service returns structural evidence only).
        webservers = []
        # ONE firewall read for the whole Dashboard: the webserver rows and the firewall box.
        try:
            firewall = service.firewall_status()
        except Exception:
            firewall = None
        try:
            for w in service.dashboard_webservers(served_via_nginx=served_via_nginx(),
                                                  fw_status=firewall):
                w_entry = w
                if w["kind"] == "console":
                    addr = _console_addr(w.get("port", ""))
                elif w["kind"] == "port":
                    # No-auth TCP service, from the LIVE listener scope (not the saved policy):
                    # exposed → reached host; loopback → 127.0.0.1; ABSENT → no address (the listener
                    # is DOWN — neither remote nor local-only; the template says 'listener absent').
                    ls = w.get("live_scope")
                    if ls == "exposed":
                        addr = f"{_url_host(request.host or '') or '0.0.0.0'}:{w['port']}"
                    elif ls in ("absent", "unverified"):
                        # DOWN, or running on a port we could not read — either way there is no
                        # address we can honestly hand out (the template says which it is).
                        addr = ""
                    else:                          # loopback (or unset) -> loopback address
                        addr = f"127.0.0.1:{w['port']}"
                elif w.get("enabled") or w.get("live"):
                    # Enabled (or still-live after a saved Disable) proxy: link to the proxy
                    # socket ONLY where it LIVE-listens. Exposed →
                    # reached host; loopback → 127.0.0.1 for a LOCAL viewer only; loopback-remote or
                    # absent → no service address (the template shows an internal Settings/Apply link).
                    ls = w.get("listen_scope")
                    if ls == "exposed":
                        addr = f"{_url_host(request.host or '')}:{w['port']}"
                    elif ls == "loopback" and peer_is_loopback():
                        addr = f"127.0.0.1:{w['port']}"
                    else:
                        addr = ""
                else:
                    addr = ""
                    # A running-but-not-proxied web UI: set direct_addr ONLY where the direct listener
                    # is up for THIS viewer — exposed → reached host; loopback → 127.0.0.1 for a LOCAL
                    # viewer. loopback-remote or ABSENT → no direct_addr (the template links internally,
                    # and never turns a down/absent listener into a clickable dead URL).
                    if w.get("direct_port"):
                        ds = w.get("direct_scope")
                        if ds == "exposed":
                            w_entry = {**w, "direct_addr": f"{_url_host(request.host or '')}:{w['direct_port']}"}
                        elif ds != "absent" and peer_is_loopback():   # loopback (or unset) + local viewer
                            w_entry = {**w, "direct_addr": f"127.0.0.1:{w['direct_port']}"}
                webservers.append({**w_entry, "addr": addr})
            merged = {w["merge_key"]: w for w in webservers if w.get("merged")}    # U3 (K2): one line per listener
            for w in webservers:
                if w.get("raw_key"):
                    w["raw"] = merged[w["raw_key"]]
        except Exception:
            webservers = []            # fail-safe: never break the dashboard over the webserver box
        try:
            if firewall is None:
                raise RuntimeError("firewall status unavailable")
            firewall["has_log"] = service.firewall_has_log()
            security_pill = service.security_pill(webservers)
        except Exception:
            firewall, security_pill = None, {"level": "warn", "label": "unknown", "title": ""}
        try:
            fw_reapply = service.firewall_reapply_notice(firewall) if firewall else None
        except Exception:
            fw_reapply = None
        restart_required = service.restart_required_stacks()      # read once: box + signature
        try:                           # the maintenance tasks whose last pass failed (file read)
            from lhpc.core import maintenance as _maintenance
            maintenance_failing = _maintenance.failing(service._paths)
        except Exception as exc:       # never shown as healthy: the read failure is the row
            maintenance_failing = [("record", "", f"{type(exc).__name__}: {exc}")]
        return render_template(
            "dashboard.html", version=__version__, runtime_root=_runtime_root(),
            radios=radios, pending_interactive=pending_interactive, webservers=webservers,
            firewall=firewall, security_pill=security_pill, fw_reapply=fw_reapply,
            maintenance_failing=maintenance_failing,
            # The host the browser used to reach the console — a proxied web-UI link points here on
            # the proxy's port, so it is correct however the operator got here (LAN IP / hostname).
            req_host=_url_host(request.host or ""),
            # Is the browser ON the Pi? (nginx-set X-LHPC-Peer; loopback when direct/no proxy.) A
            # loopback-only service is a working link ONLY for a local viewer; for a remote one the
            # pin must go to an internal page, never a dead 127.0.0.1 (which names the CLIENT there).
            peer_loopback=peer_is_loopback(),
            # Durable restart-required flags (file reads only): a yellow "Restart now"
            # action per flagged stack.
            restart_required=restart_required,
            welcome=service.auto_install_welcome(),
            welcome_dismissed=service.welcome_note_dismissed(),
            tasks=tasks,
            hardware_configured=service.hardware_configured(),
            radios_listed=bool(_radios_mod.load(service._paths).radios),       # DEC-W384cl: radios, none enabled
            hardware_changes=_hotplug.banner(service),            # W249/DEC-W42: the one hotplug banner
            attention=(_att := _attention_banner()),              # W272/W273: the "Needs attention" banner
            attention_set=_attention.set_key(_att),                         # C23 B2: the ✕ posts the identity of the set shown
            # Each power button renders ONLY when logind authorizes ITS action (cached
            # per-kind verdict — a box may allow reboot but not power-off); an unauthorized
            # box shows nothing, and the dep panel offers the copybox.
            power_ok={"reboot": service.power_supported("reboot"),
                      "poweroff": service.power_supported("poweroff")},
            network_row=_network_row_safe(service),
            autostart=_autostart_dash_safe(service),
            dash_sig=service.dash_signature(restart_required=restart_required))

    @app.get("/api/dash-signature")
    def dash_signature_api():
        # Cheap structural-state signature; the dashboard reloads only when it changes.
        return jsonify(sig=service.dash_signature())

    @app.get("/api/system")
    def system_api():
        # Host metrics for the System box: RAW procfs/sysfs counters + a monotonic ts; the
        # BROWSER computes rates between its own polls. File reads only — no network, no
        # server-side history — with ONE exception: on a chrony box the Time row reads one
        # `chronyc -n -c tracking`, cached 60 s (service_system._chrony_tracking): the source it
        # is synced to (NTP or GNSS), or whether it synced earlier. Polled only while the box is
        # expanded.
        return jsonify(service.system_stats())

    @app.get("/api/tasks")
    def tasks_api():
        # Running-task banner feed (auto-install + HMAC + build/test/install jobs). STRICTLY read-only.
        return jsonify(tasks=service.running_tasks())

    @app.post("/api/tasks/dismiss")
    def tasks_dismiss():
        # Close a FAILED banner, or hide an UNSAFE one (UX: never a running one). CSRF-guarded; durable.
        if not _csrf_ok():
            abort(400)
        ok = service.task_dismiss(request.form.get("kind", ""), request.form.get("run_id", ""),
                                  request.form.get("attempt_id", ""))
        return jsonify(ok=bool(ok)), (200 if ok else 409)

    @app.post("/api/tasks/recover")
    def tasks_recover():
        # Explicit-ack recovery of an UNSAFE build/test/install job → non-blocking failed. CSRF-guarded.
        if not _csrf_ok():
            abort(400)
        ok = service.task_recover(request.form.get("kind", ""), request.form.get("run_id", ""),
                                  request.form.get("attempt_id", ""))
        return jsonify(ok=bool(ok)), (200 if ok else 409)

    _SRC_LABELS = (("binary", "Binary (prebuilt)"), ("pinned", "Known working"),
                   ("dev", "Development"), ("stable", "Latest stable"))

    @app.get("/auto-install")
    def auto_install_page():
        st = service.auto_install_status()
        mode = service.auto_install_mode()
        running = service.auto_install_running()
        # A JUST-SPAWNED run: reservation live but the driver hasn't written its marker
        # yet — show a 'starting' card immediately (never a blank page after the POST),
        # also over the previous run's TERMINAL marker, whose outcome is then not the current one.
        from lhpc.core import auto_install as ai_mod
        starting_run = ai_mod.starting_run(service._paths, st)
        starting = bool(starting_run)
        # A spawned run that ended BEFORE claiming (typed preflight refusal): show its
        # actual output instead of silently falling back to the old run's card.
        spawn_failed = ""
        spawn_arg = request.args.get("spawn", "")
        if (spawn_arg and ai_mod.RUN_ID_RE.match(spawn_arg)
                and (st is None or st.get("run_id") != spawn_arg)):
            chunk = service.auto_install_log_chunk(spawn_arg, 0)
            if chunk.get("data"):
                spawn_failed = chunk["data"][-4000:]
        gate = service._auto_install_gate()
        recovery = service.auto_install_recovery_reason()
        needs_ack = bool(recovery)
        orphan_risk = "ORPHAN RISK" in recovery
        # The manual process-inspection checkbox must be reachable for EVERY unsafe case that needs
        # confirm_orphan: escaped-or-output, unknown/malformed scope, or a session-unverified whose
        # identity can't be reconstructed (i.e. NOT safely auto-recoverable).
        from lhpc.core import proctree
        needs_process_confirmation = bool(
            st and not st.get("unsafe") and st.get("state") == "unsafe"
            and (st.get("unsafe_scope") != "session-unverified"
                 or proctree.reconstruct_token(st.get("session_ident")) is None))
        chunk = {"offset": 0, "data": ""}
        complog_seed = ""
        if st and not st.get("unsafe"):
            chunk = service.auto_install_log_chunk(st["run_id"], 0)
            # Historical (collapsed) run: seed the detailed per-component log window server-side,
            # exactly as log_seed seeds the rollup. Same condition the template uses for `collapsed`.
            if (not running and not needs_ack
                    and st.get("state") in ("completed", "completed-with-failures", "aborted")):
                complog_seed = service.auto_install_component_log_seed(st["run_id"])
        return render_template(
            "auto_install.html", version=__version__,
            st=st, mode=mode, running=running, gate=gate, needs_ack=needs_ack,
            recovery=recovery, orphan_risk=orphan_risk,
            needs_process_confirmation=needs_process_confirmation,
            starting=starting, starting_run=starting_run,
            tests_off=bool(st and not st.get("unsafe") and ai_mod.tests_off(st)),
            spawn_failed=spawn_failed, dep_preflight=service.auto_install_dep_preflight(),
            log_seed=chunk.get("data", ""), complog_seed=complog_seed, src_labels=_SRC_LABELS,
            rows=(_rows := service.auto_install_rows()),
            ai_form=auto_install_form.view(_rows, session.get(auto_install_form.SESSION_KEY)))

    _TX_CONFIRM_TTL_S = 300.0
    # Single use of an RF confirmation, held in THIS web process (one process, waitress threads): the
    # session is a signed cookie, so popping the staged record cannot stop the old cookie being sent
    # again. `_tx_nonce` marks what this process staged; a record from before a restart is refused.
    # The used set depends on NO clock (a pruning by expiry let a stalled replay or a clock step back
    # revive a used token): it keeps the newest _TX_USED_MAX tokens and drops the oldest. THE LIMIT: a
    # used confirmation is remembered until _TX_USED_MAX later ones were consumed, whatever the time;
    # after that its own 300 s by the wall clock are its only guard, so a clock set back can make it
    # usable again.
    _TX_USED_MAX = 256
    _tx_used: dict = {}                                  # token -> None, oldest first
    _tx_used_lock = threading.Lock()
    _tx_nonce = _secrets.token_hex(8)
    app.extensions["lhpc_tx_used"] = _tx_used             # read by the tests only

    def _parse_ai_selection():
        """Build {sid: {install, version, tests, tx}} by iterating the KNOWN stack ids (never split a
        form name — a stray/invalid key is simply ignored). Returns (selection, canonical) where
        canonical is a stable string for RF-confirmation binding."""
        sel = {}
        for row in service.auto_install_rows():
            sid = row["id"]
            sel[sid] = {"install": request.form.get(f"install:{sid}") == "yes",
                        # per-stack default: binary where published, else "pinned"
                        "version": request.form.get(f"version:{sid}", row["default_channel"]),
                        "tests": request.form.get(f"tests:{sid}") == "yes",
                        "tx": request.form.get(f"tx:{sid}") == "yes"}
        canonical = ";".join(f"{sid}:{int(v['install'])}{v['version']}{int(v['tests'])}{int(v['tx'])}"
                             for sid, v in sorted(sel.items()))
        return sel, canonical

    def _stage_tx_confirmation(canonical: str) -> str:
        """An RF confirmation, used once (see THE LIMIT above): a session-bound token tied to the EXACT
        selection (canonical string), the CSRF context, a short expiry and this web process
        (`_tx_nonce`). Consumed by the confirming POST — hidden-field values are never trusted on
        their own."""
        token = _secrets.token_hex(16)
        session["_auto_install_tx_confirm"] = {"token": token, "canonical": canonical,
                                               "csrf": session.get("_csrf", ""),
                                               "exp": time.time() + _TX_CONFIRM_TTL_S,
                                               "nonce": _tx_nonce}
        return token

    def _consume_tx_confirmation(token: str, canonical: str) -> str:
        """Validate + CONSUME the staged confirmation before any spawn. Returns "" when valid, else the
        typed refusal. Single use within this web process: the token is recorded as used in the same
        locked step that finds it unused, so a replayed cookie or two simultaneous requests start once
        (a replay: until 256 later confirmations were consumed, see THE LIMIT above)."""
        now = time.time()
        staged = session.pop("_auto_install_tx_confirm", None)          # gone from THIS session
        if not isinstance(staged, dict):
            return "no valid RF confirmation is staged — start again from the form"
        try:
            if not (isinstance(staged.get("token"), str) and staged["token"]
                    and isinstance(staged.get("exp"), (int, float))):
                return "the staged RF confirmation is malformed — start again"
            if now > staged["exp"]:
                return "the RF confirmation has expired — start again"
            if not (token and _secrets.compare_digest(token, staged["token"])):
                return "the RF confirmation token does not match — start again"
            if not _secrets.compare_digest(session.get("_csrf", ""), staged.get("csrf", "")):
                return "the RF confirmation belongs to a different session — start again"
            if staged.get("canonical") != canonical:
                return ("the selection changed after confirmation — start again")
        except (TypeError, KeyError):
            return "the staged RF confirmation is malformed — start again"
        with _tx_used_lock:
            # Both refusals leave the set unchanged; only a consume that passes records (and drops
            # the oldest entry once the set is full).
            if staged.get("nonce") != _tx_nonce:
                return "the RF confirmation was made before the console restarted — start again"
            if staged["token"] in _tx_used:
                return "this RF confirmation was already used — start again from the form"
            _tx_used[staged["token"]] = None
            while len(_tx_used) > _TX_USED_MAX:
                del _tx_used[next(iter(_tx_used))]
        return ""

    @app.post("/auto-install/start")
    def auto_install_start():
        if not _csrf_ok():
            abort(400)
        selection, canonical = _parse_ai_selection()
        session[auto_install_form.SESSION_KEY] = auto_install_form.to_session(selection)   # W206: the page keeps it
        errs = service.auto_install_check_selection(selection)
        if errs:
            flash(errs[0], "warn")
            return redirect(url_for("auto_install_page"))
        if any(v["tx"] for v in selection.values()):
            token = request.form.get("confirm_token", "")
            if not token:
                # FIRST TX-enabled POST: stage the server-side confirmation and render the second
                # page carrying the bound selection + one-time token.
                token = _stage_tx_confirmation(canonical)
                return render_template(
                    "auto_install_confirm.html", version=__version__, selection=selection, canonical=canonical,
                    confirm_token=token, src_labels=_SRC_LABELS)
            why = _consume_tx_confirmation(token, canonical)
            if why:
                flash(f"RF confirmation refused: {why}.", "warn")
                return redirect(url_for("auto_install_page"))
        _job, err = service.spawn_auto_install_job(selection)
        if err:
            flash(err, "warn")
        else:
            flash("auto-install run started — this can take several minutes.", "ok")
        return redirect(url_for("auto_install_page"))

    @app.post("/auto-install/ack")
    def auto_install_ack():
        if not _csrf_ok():
            abort(400)
        res = service.auto_install_ack(
            confirm_orphan=request.form.get("confirm_orphan") == "yes")
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("auto_install_page"))

    @app.post("/auto-install/abort")
    def auto_install_abort_route():
        if not _csrf_ok():
            abort(400)
        res = service.auto_install_abort(request.form.get("run_id", ""))
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("auto_install_page"))

    @app.get("/api/auto-install")
    def auto_install_api():
        st = service.auto_install_status()
        out = {"state": st if st is not None else {"absent": True},
               "running": service.auto_install_running(), "run_id": "", "log": {}}
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            offset = -1
        # Whether a spawn reservation is LIVE (spawning/spawned/claimed, identity
        # proven): the starting card uses this to detect a child that ended BEFORE
        # writing its marker (e.g. the running-components preflight refusal).
        from lhpc.core import auto_install as ai_mod
        from lhpc.core import procident
        rstate, res = ai_mod.read_reservation(service._paths)
        out["spawn_live"] = bool(
            rstate == "valid"
            and res.get("phase") in ("spawning", "spawned", "claimed")
            and procident.identity_matches(res.get("ident", {}), res.get("pid", -1)))
        if st and not st.get("unsafe"):
            out["run_id"] = st["run_id"]
            out["log"] = service.auto_install_log_chunk(st["run_id"], offset)
            # Second window: the sequential per-component build/test log stream.
            try:
                ci = int(request.args.get("ci", "0"))
                co = int(request.args.get("co", "0"))
            except ValueError:
                ci, co = 0, 0
            out["complog"] = service.auto_install_component_log_chunk(st["run_id"], ci, co)
        return jsonify(**out)

    # ---- MeshCom HMAC password: warn+apply page (its own live progress run) --------------------
    _HMAC_ACTIONS = {
        "enable": ("Enable", "The bridge and firmware will require the shared password afterwards."),
        "disable": ("Disable", "Client authentication will be REMOVED — anyone in range can inject."),
        "renew": ("Renew", "The shared secret is rotated — every client must be re-provisioned."),
    }

    @app.get("/stacks/<sid>/hmac/<action>")
    def hmac_apply_page(sid, action):
        if action not in _HMAC_ACTIONS or not service.hmac_applies(sid):
            abort(404)
        st = service.hmac_apply_status()
        # `active` is computed HERE (not with a Jinja {% set %} inside a block — that would be
        # invisible to the sibling `scripts` block, so the live poller would never load).
        active = bool(st and not st.get("unsafe") and st.get("phase") == "running")
        log_seed = complog_seed = ""
        # Seed the windows ONLY for a TERMINAL run (no live poller then). While RUNNING, the poller fills
        # both from offset 0 — seeding too would double-render the head.
        if st and not st.get("unsafe") and not active:
            log_seed = service.hmac_apply_log_chunk(st["run_id"], 0).get("data", "")
            complog_seed = service.hmac_component_log_seed(st["run_id"])
        label, warning = _HMAC_ACTIONS[action]
        return render_template(
            "hmac_apply.html", version=__version__,
            sid=sid, action=action, action_label=label, warning=warning,
            disable_phrase=service.HMAC_DISABLE_CONFIRM,
            binary_block=service.hmac_binary_block(sid),
            st=st, active=active, log_seed=log_seed, complog_seed=complog_seed)

    @app.post("/stacks/<sid>/hmac/<action>/apply")
    def hmac_apply_run(sid, action):
        if not _csrf_ok():
            abort(400)
        if action not in _HMAC_ACTIONS or not service.hmac_applies(sid):
            abort(404)
        # `disable` additionally requires the typed phrase; the service refuses without it (a CSRF-valid
        # POST alone is not enough to remove client authentication).
        confirm = request.form.get("confirm_phrase", "").strip() == service.HMAC_DISABLE_CONFIRM
        res = service.hmac_apply_start(sid, action, confirm=confirm)
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("hmac_apply_page", sid=sid, action=action))

    @app.post("/stacks/<sid>/hmac/<action>/abort")
    def hmac_apply_abort_route(sid, action):
        if not _csrf_ok():
            abort(400)
        if action not in _HMAC_ACTIONS or not service.hmac_applies(sid):
            abort(404)
        res = service.hmac_apply_abort(sid, request.form.get("run_id", ""))
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("hmac_apply_page", sid=sid, action=action))

    @app.post("/stacks/<sid>/hmac/<action>/recover")
    def hmac_apply_recover_route(sid, action):
        if not _csrf_ok():
            abort(400)
        if action not in _HMAC_ACTIONS or not service.hmac_applies(sid):
            abort(404)
        res = service.hmac_apply_recover(sid, request.form.get("run_id", ""))
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("hmac_apply_page", sid=sid, action=action))

    @app.get("/api/hmac-apply")
    def hmac_apply_api():
        st = service.hmac_apply_status()
        out = {"state": st if st is not None else {"absent": True},
               "running": service.hmac_apply_running(), "run_id": "", "log": {}}
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            offset = -1
        try:
            ci = int(request.args.get("ci", "0"))
            co = int(request.args.get("co", "0"))
        except ValueError:
            ci, co = 0, 0
        if st and not st.get("unsafe"):
            out["run_id"] = st["run_id"]
            out["log"] = service.hmac_apply_log_chunk(st["run_id"], offset)
            out["complog"] = service.hmac_component_log_chunk(st["run_id"], ci, co)
        return jsonify(**out)

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

    def _render_wide_reads():
        """The evidence a whole Apps/Dashboard render shares — ONE firewall status and ONE
        /proc/net/tcp listener snapshot — passed down the existing `listeners=`/`fw_status=`
        seams instead of being re-read once per stack. Fail-soft."""
        try:
            fw = service.firewall_status()
        except Exception:
            fw = None
        try:
            lis = service._request_memo(("tcp-listeners",), service._system.procfs.tcp_listeners)
        except Exception:
            lis = None
        return fw, lis

    def _unconfirmed_post(stack):
        """A running stack's post-start setting the node never confirmed or never got (MeshCom's TX power: the node may
        still use its own value), and a node with no radio for its power above the panel's ceiling, on the stack's row
        like its other warnings, with the command that sets it (RADIO-POWER item 4, N2; G2 B1)."""
        return [(c.id, ln.removeprefix("post-start: ")) for c in stack.components
                if c.post_steps or "{daemon:power}" in c.run_argv
                for ln in service._post_start_outcomes(c.id)
                if " UNVERIFIED " in ln or " NOT applied " in ln or ln.startswith("radio: NO RADIO: ")]

    def _shown_instance(stack):
        """G4-web: the instance a stack's Settings view shows without a page radio it can use (what the band default
        picked before): one it runs on (its launch records), else the first radio able to run it; None without one (no
        list, the daemon: its global settings)."""
        if service._is_daemon_target(stack.id):
            return None
        running = sorted(_instances.recorded(service, stack.id), key=lambda r: int(r[1:]))
        return next((c for rid in (*running, *_instances.can_run_on(service, stack)) if (c := _settings_copy(stack.id, rid))),
                    None)

    def _stack_groups(only_sid=None, *, fw_status=None, listeners=None,
                      restored_open=frozenset(), bodies=False):
        """Per-stack overview rows for the Stacks page. Each row now carries the FULL per-stack
        detail: component statuses/evidence, system+build+runtime
        dependency diagnosis, needs-build, daemon parameters, restart-required
        and stack-scoped conflicts — all read-only, GET-safe. Every view is the stack's instance's (G4-web: the page
        radio's, else `_shown_instance`), never a band's. `only_sid` limits the heavy per-stack build to ONE row (the
        lazy-body partial) — the holistic snapshot/conflicts are still computed, but the ~14 per-stack
        service calls run for just that stack instead of all of them. On the whole page those calls
        run only for the rows rendered inline (`_row_inline`); a closed row carries what its summary
        and row actions show, and its body comes from the lazy route when it is opened. `bodies` builds
        every row's body fields (the U4 tree page lists each row's submenus)."""
        from lhpc.core import radio_start as _radio_start
        _live_radios = _radio_start.view_radios(service._paths)        # W3: one read of the enabled radios per render
        snapshot = service.build_snapshot()  # fresh, read-only evidence each load
        rollup = rollup_states(snapshot)
        from lhpc.core import instance_liveness as _liveness
        _inst_rows = _liveness.rows(service, snapshot)     # STATUS-DEAD-SOCKET: each instance's own state, one read
        stack_deps = stack_dependencies([ss.stack for ss in snapshot.stacks])
        all_conflicts = service.observed_conflicts(snapshot)
        index = {}
        for ss in snapshot.stacks:
            for comp in ss.stack.components:
                index[comp.id] = (comp, ss.components[comp.id])
        # CACHED source freshness (never the network on a GET — P0.6). Each cached verdict is
        # resolved against the component's CURRENT head and lane key, so a source updated since the last check
        # reads `unchecked`, not a stale green/yellow.
        _fresh = service.source_check_view()
        _lanes = stackupdates.verdicts(service, snapshot)      # cid -> (verdict, source words, profile words)
        _words = {cid: v[1:] for cid, v in _lanes.items()}
        jobs_by_stack = {}
        for job in service.active_jobs():
            jobs_by_stack.setdefault(job.get("stack"), []).append(job)

        groups = []
        for ss in snapshot.stacks:
            if only_sid is not None and ss.stack.id != only_sid:
                continue                              # lazy-body partial: build only the requested row
            stack = ss.stack
            _inst = _settings_copy(stack.id, _page_radio()[0])      # G1-W2-RE F2: every stack's view on the page radio
            _view_inst = _inst or _shown_instance(stack)            # G4-web: the Settings view's instance, never a band
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
            # A main component can have a build step WITHOUT a source: a package-managed stack
            # whose artifact is fetched rather than compiled (graywolf). Its Build affordance
            # must not hang off `installed`, which requires a source — otherwise the console
            # offers Run, the start gate refuses "not built", and no button can fix it.
            # SOURCE-LESS ONLY: a sourced stack must be installed (cloned) before Build makes
            # sense, and offering it earlier would just fail.
            buildable = bool(main and not main.source and main.build_steps)
            installed = bool(has_source and main_status and main_status.source_state.value
                             in ("match", "dirty", "differs", "unknown", "not-a-repo", "binary"))
            # A fetched-binary stack has no clone, so the source-gated Uninstall/Clean line never
            # rendered for it — yet `lhpc uninstall graywolf` works and DOES remove the fetched
            # artifact. The console has to offer the removal the CLI already accepts, otherwise a
            # stack can be installed from the console but only removed from a shell. Gated on
            # anything removable being ON DISK — not on "fully built", which hid the buttons for
            # an interrupted fetch or a stale pin, exactly the states where removal is the fix.
            interactive = bool(main and main.interactive)
            member_ids = {c.id for c in stack.components}
            # Freshness, resolved per component against the head the verdict was computed from.
            # Scoped to the stack's OWN components: a cross-stack dependency (loraham-daemon inside
            # the chat row) belongs to its own stack's row and must not light up this one.
            upd_states = {c.id: _lanes[c.id][0] for c in stack.components}
            # Deep-link target for the row's "Update": the behind component itself. The MAIN
            # (== the stack) links to its Install section; a behind DEPENDENCY links to its own
            # #comp-<id> subsection so the operator lands on the component that actually has the update.
            _behind = [cid for cid, v in upd_states.items() if v == "behind"]
            _main_id = main.id if main else None
            if _main_id is not None and upd_states.get(_main_id) == "behind":
                _upd_comp, _upd_is_main = _main_id, True
            elif _behind:
                _upd_comp, _upd_is_main = _behind[0], False
            else:
                _upd_comp, _upd_is_main = None, False
            _jobs = jobs_by_stack.get(stack.id, [])
            inline = _row_inline(stack.id, _jobs, restored_open, request.args)
            # W250: a running stack's tools; a radio's page lists them only while the stack runs on THAT radio
            _tool_comps = [c for c in stack.components if c.interactive and c.id != stack.main]
            _page_rid = _page_radio()[0]
            _tools_here = bool(_tool_comps and rollup[stack.id] in _RUNNING and (
                not _page_rid or f"{stack.id}@{_page_rid}" in _instances.running_keys(service, stack.id)))
            g = {
                "stack": stack,
                "main": index.get(main.id) if main else None,
                "main_status": main_status,
                "statuses": ss.components,            # dict[comp_id -> ComponentStatus] (evidence etc.)
                "instance_rows": {c.id: _inst_rows[c.id] for c in stack.components if c.id in _inst_rows},
                "source_words": _words,               # comp_id -> (source, profile): the info card's row
                "update_states": upd_states,          # comp_id -> unchecked|unknown|up_to_date|behind|error
                "update_main": upd_states.get(main.id, "unchecked") if main else "unchecked",
                "update_available": any(v == "behind" for v in upd_states.values()),
                "update_component": _upd_comp,            # the behind component to deep-link to
                "update_component_is_main": _upd_is_main,  # main -> Install section; dep -> #comp-<id>
                "update_checked_ago": _checked_ago(_fresh.get("checked_at", 0)),
                # The MeshCore stack's Mode switch on the stack body and its pill on the Apps row
                # ("" elsewhere): the saved mode, the choices, and the mode the running launch was
                # generated with (shown only while the stack runs and differs from the saved one).
                "meshcore_mode": (service.meshcore_mode_display(_view_inst)          # K8f S4-2: the shown radio's
                                  if stack.id == _meshcore_mode.STACK_ID else ""),
                "meshcore_modes": list(_meshcore_mode.MODES),
                "meshcore_running_mode": (service.meshcore_running_mode(_view_inst)
                                          if stack.id == _meshcore_mode.STACK_ID else ""),
                "meshcore_rid": getattr(_view_inst, "rid", "") if stack.id == _meshcore_mode.STACK_ID else "",
                "installed": installed,
                "has_source": has_source,
                "buildable": buildable,
                "web_client": web_client.view(service) if stack.id == web_client.DAEMON else {},   # item 12
                "fetched_ver": (_fv := (service.fetched_version_state(stack.id)
                                        if buildable else {})),
                # Reuse _fv's installed instead of a second fetched_version_state read.
                "upstream": (service.graywolf_upstream_state(
                    stack.id, _installed=_fv.get("installed", "")) if buildable else {}),
                "deps": own + cross,
                "state": rollup[stack.id],
                "running": rollup[stack.id] in _RUNNING,
                "dep_stacks": [(d, rollup.get(d, "unknown")) for d in stack_deps[stack.id]],
                "bands": sorted({c.band for c in stack.components if c.band}),
                "interactive": interactive,
                "conflicts": [c for c in all_conflicts
                              if any(h in member_ids for h in c.holders)],
                "jobs": _jobs,
                "notes": [(cid, n) for cid, cs in ss.components.items() if (n := component_note(cs))]   # W272: its row
                         + (_unconfirmed_post(stack) if rollup[stack.id] in _RUNNING else []),
                # W250: what Home's card showed for a running stack: its RF log (W4: its instance's), its tools' launch lines
                "tools": [(c.id, c.name, service.manual_start_command(c, _inst)) for c in _tool_comps] if _tools_here else [],
                "inline": inline,
            }
            if _row_needs_body(inline or bodies, only_sid):
                g.update({
                    "hardware_block": service.hardware_block(stack.id),  # "" unless no hardware configured
                    # U5: the daemon row's Hardware submenu shows the hardware list inline, the same view as /hardware
                    "hw_list": _hw_list_ctx(_radio_start.hardware_view(service)) if stack.id == "daemon" else None,
                    "radio_block": service.radio_mode_block(stack.id),   # "" unless the mode excludes it
                    # [] for a stack with no web UI -> no Webserver sub-section; else one entry per
                    # proxied PAGE (component with a client http/https endpoint).
                    "stack_webs": service.stack_web_views(stack.id, listeners=listeners,
                                                          fw_status=fw_status),
                    # One Password sub-section per component that declares where its self-generated
                    # UI password lives — independent of web pages.
                    "ui_creds_list": service.ui_credentials_list(stack.id, _inst),   # 7f: the shown instance's own
                    "pin_refusal": service.binary_install_refusal(stack.id),
                    "removable": bool(buildable and service.fetched_artifacts_present(stack.id)),
                    # W2: the Run line's ONE radio selector, the union of the runnable and the running instances
                    "run_on": sorted(set(_instances.can_run_on(service, stack)) | {k.partition("@")[2] for k in
                                     _instances.running_keys(service, stack.id)}, key=lambda r: int(r[1:])),
                    "command": service.manual_start_command(main, _inst) if interactive else "",   # 7f: the shown instance's
                    # Per-stack Settings section rendered inline on the Apps list (same data the
                    # stack-detail Settings uses): operator, component-scoped params, sources, autostart.
                    "view": (_cv := {**service.config_view(stack.id, _view_inst.band if _view_inst else "", rid=_view_rid(stack.id, "")),
                             **_instances.settings_choice(service, stack.id, "", _view_inst),   # 7f, DEC-W40
                             "live_radios": _live_radios}),   # W3: the daemon's radio links
                    "config_groups": service.config_param_groups(stack.id, inst=_view_inst),
                    # Folded-in detail sections (all read-only / GET-safe):
                    "system_deps": service.system_deps(stack.id),
                    "deps_report": service.deps_report(stack.id),
                    # U8: what refuses a start ([(cid, reason)]) and what starts but has a rebuild available
                    "start_ready": (_sr := service.start_readiness(stack.id, _view_inst)),   # its shown radio's run order
                    # a provider to build BEFORE the main, and only one whose start refusal is a source build (N1)
                    "needs_build_deps": [c for c in service.unbuilt_build_deps(stack.id)
                                         if dict(_sr[0]).get(c, "").startswith("not built")],
                    # UI-REORG s1/s3: in the daemon's Settings the panel follows the Settings switch: ITS radio
                    "daemon_params": service.daemon_params_view(stack.id, rid=(
                        _cv["radios"][0]["rid"] if _submenus.params_in_settings(stack.id) and _cv.get("radios")
                        else _dp_rid(stack.id, _live_radios, _inst))),
                    # None when HMAC does not apply here (no flag/row); else whether it is ENABLED.
                    "hmac_enabled": service.hmac_status(stack.id),
                    # K5: any instance's marker, with that instance's own button; not twice beside its settings view
                    "restart_required": (_rr if not (_inst and _rr and _rr.get("stack") == _instances.key(stack.id, _inst))
                                         else None) if (_rr := service.restart_required(stack.id)) else None,
                    # Cleanup C7: the shown listed copy's own marker (its settings view), keyed `<sid>@<rid>`.
                    "copy_key": _instances.key(stack.id, _inst),
                    "copy_restart_required": service.restart_required(_instances.key(stack.id, _inst)) if _inst else None,
                })
            groups.append(g)
        groups.sort(key=lambda g: (not g["running"], len(g["dep_stacks"]), g["stack"].id))
        return groups, snapshot

    def _firewall_view_safe(svc, fw=None):
        try:
            return svc.firewall_settings_view(fw)
        except Exception:
            return None                    # fail-safe: never break the Apps page over firewall

    def _autostart_dash_safe(svc):
        """Autostart state + last boot-restore result for the System box. Config + journal
        reads only (no privilege, no network), fail-soft: any failure renders the switch as
        unknown rather than breaking the dashboard."""
        import datetime as _dt
        out = {"on": False, "reason": "unavailable", "last": ""}
        try:
            out["on"], out["reason"] = svc.boot_restore_enabled()
        except Exception:
            return out
        try:
            st = svc.boot_restore_status()
        except Exception:
            return out
        if st is None:
            out["last"] = "no boot restore has run yet"
        elif st["state"] == "blocked":
            # LIVE condition, not a stored result — an older result here would hide why
            # nothing was restored this boot.
            out["last"] = "blocked: " + str(st.get("reason", ""))[:160]
        else:
            when = st.get("finished_at")
            ts = (_dt.datetime.fromtimestamp(when).strftime(" @ %Y-%m-%d %H:%M")  # noqa: DTZ006
                  if isinstance(when, (int, float)) else "")
            c = st.get("counts", {})
            out["last"] = (f"last run: {st['state']}{ts} — {c.get('succeeded', 0)} restored, "
                           f"{c.get('failed', 0)} failed, {st.get('skipped', 0)} skipped")
        return out

    def _network_row_safe(svc):
        """The System card's Network row, or None on a box without the managed network (as
        before). One connection is shown: wired > Wi-Fi client > own AP > none. `kind` picks
        the pill: `wifi` is filled with the signal by system.js (/api/system `wifi`); `eth`
        and `ap` are fixed green pills; `none` is a plain one. Fail-safe: never break the
        dashboard over the network."""
        try:
            nv = svc.network_view()
            if not nv.get("supported"):
                return None
            eth, act = nv.get("ethernet") or {}, nv.get("active") or {}
            if eth:
                return {"kind": "eth", "pill": "Eth",
                        "text": f"{eth.get('name', '?')} ({eth.get('address', '?')})"}
            if nv.get("mode") == "client":
                return {"kind": "wifi", "pill": "Wi-Fi", "dev": act.get("device", ""),
                        "text": f"{act.get('name', '?')} ({act.get('address', '?')})"}
            if nv.get("mode") == "ap":
                return {"kind": "ap", "pill": "AP",
                        "text": f"{nv.get('ap_ssid') or act.get('name', '?')} ({act.get('address', '?')})"}
            return {"kind": "none", "pill": "none", "text": "no active network"}
        except Exception:
            return None

    def _page_radio() -> tuple:
        """W2 (G1-W2-RE F2, DEC-W384ad): the page's radio, resolved ONCE per request from its row: (rid, band, why).
        Every per-stack view keys on that rid; no stack row is hidden. No `?radio=`: ("", "", ""); an empty one is a
        400 by name; a rid the list does not have: ("", "", why), reported on the page; a `?band=` is a 400 by name
        (open item (c) of G1-W3-RE, DEC-W384bi: the band is a radio's attribute)."""
        if "lhpc.page_radio" not in request.environ:
            if "band" in request.args:
                abort(make_response(_BAND_REFUSAL, 400))
            rid = request.args.get("radio")
            if rid == "":
                abort(make_response("?radio= is empty: name a radio (?radio=rN)", 400))
            request.environ["lhpc.page_radio"] = _radio_of(rid)
        return request.environ["lhpc.page_radio"]

    def _radio_of(rid) -> tuple:
        """(rid, band, why) of a page radio `rid` (see `_page_radio`)."""
        row = next((r for r in _radios_mod.load(service._paths).radios if r.id == rid), None) if rid else None
        return ((rid, row.band, "") if row else
                ("", "", f"{rid} is not a radio of the hardware list") if rid else ("", "", ""))

    def _stacks_context(*, only_sid=None, bodies=False):
        """The FULL Apps-page render context (all globals every per-stack body/include needs). Shared
        by the whole-page render AND the per-stack lazy-body partial, so the partial can never render
        with a missing var.
        `only_sid` (lazy partial) builds just that stack's group — the page-wide ctx below is O(1)."""
        fw_status, listeners = _render_wide_reads()
        # Rows the operator had open on their last visit (mirrored to the `lhpc_open` cookie by
        # stacks_state.js). Rendering them already-open with their body inline — like a ?open= row —
        # means a restored-open row is present at FIRST PAINT, so re-opening it never shifts the page
        # (CLS). One general mechanism: any top-level row id (a stack sid, or "controller-row").
        from urllib.parse import unquote as _unquote
        restored_open = {s for s in _unquote(request.cookies.get("lhpc_open", "")).split(",") if s}
        groups, snapshot = _stack_groups(only_sid, fw_status=fw_status, listeners=listeners,
                                         restored_open=restored_open, bodies=bodies)
        # Controller-owned Webserver component, rendered INLINE in the controller row. Cached
        # evidence only (monitor_view: no probing/mutation) — fail-safe so it never breaks /stacks.
        from lhpc.core.config import WEBSERVER_ACCESS_MODES as _WS_MODES
        try:
            _ws = service.webserver_monitor(served_via_nginx=served_via_nginx(),
                                            listeners=listeners, fw_status=fw_status).data
        except Exception:
            _ws = None
        ctx = {
            "version": __version__, "snapshot": snapshot,
            # W2 (G1-W2 2): the page's radio picks that radio in every instance selector; a rid it cannot use is said
            "page_radio": "?" if _page_radio()[2] else _page_radio()[0], "page_radio_why": _page_radio()[2],
            "groups": groups, "auto_install_mode": service.auto_install_mode(),
            "observed_conflicts": service.observed_conflicts(snapshot),   # band-aware
            "controller": service.controller_status(),
            "controller_system_deps": service.controller_system_deps(),
            "ws_mon": _ws, "ws_certs": (_ws or {}).get("pki", {}).get("clients", []),
            "ws_modes": list(_WS_MODES), "ws_loopback": peer_is_loopback(),
            "firewall_view": _firewall_view_safe(service, fw_status),
            "st": service.self_update_status(), "jobs": service.active_jobs(),
            "updater": service.updater_integration(),
            "dep_summary": (_dep := service.dependency_overview()),
            "dep_note_dismissed": service.dep_note_dismissed(_dep),
            "req_host": _url_host(request.host or ""),
            # Copy-paste fetch commands for the trust material, addressed at the BOX'S OWN
            # current network address (the monitor's local_ip — 10.42.0.1 in AP mode, the LAN
            # IP otherwise), per operator instruction; the viewer-reached host is only the
            # fallback when the box cannot name its address (e.g. a browser reaching it by
            # hostname would otherwise put that hostname into an scp command that only works
            # where the name resolves). Linux-PC-host assumption by design; the .p12 command
            # is the sanctioned REMOTE path (the browser download stays loopback-only). These
            # are operator conveniences (SSH user + paths for a box the operator already owns),
            # not secret material, so they render in every serving mode — the scp still needs
            # the operator's own SSH credentials to run, and only ACTIVE certs are listed.
            "ws_fetch": _ws_fetch_commands(str((_ws or {}).get("local_ip") or "")
                                           or _url_host(request.host or ""), _runtime_root(),
                                           active_labels=[c.get("label", "") for c in
                                                          ((_ws or {}).get("pki", {})
                                                           .get("clients", []))
                                                          if c.get("state") == "active"]),
            "ws_console_addr": _console_addr((_ws or {}).get("desired", {}).get("port", "")),
            # 'Stacks Web Proxies' bulk form (read-only overview; ?wsg=1 re-opens it after a POST)
            "ws_stackwebs": service.stack_webs_overview(),
            "wsg_open": bool(request.args.get("wsg")),
            "tasks": service.running_tasks(),
            "operator_callsign": service.config().operator.callsign,
            "operator_callsign_legacy": service.operator_callsign_legacy(),
            "operator_callsign_suggestion": service.operator_callsign_correction(),
            "restored_open": restored_open,
            "confirm": None,
        }
        return ctx, groups, snapshot

    def _controller_extras(ctx):
        """The controller row's own two views, added to a Stacks page context `ctx`."""
        # GPS is a GLOBAL box setting and belongs to NO stack — it is not a daemon setting and
        # not a per-stack one. Rendered as its own card on this page so it is visible without
        # expanding any row; the per-stack Settings carry only their own `use_gps` switch.
        try:
            ctx.setdefault("gps_global", service.gps_view())
        except (OSError, ValueError, AttributeError, KeyError):
            ctx.setdefault("gps_global", None)          # never break the page over a view
        # Network panel (controller-row only): cached view, absent on non-AP boxes.
        try:
            _nv = service.network_view()
            ctx.setdefault("network_view", _nv if _nv.get("supported") else None)
        except (OSError, ValueError, AttributeError, KeyError):
            ctx.setdefault("network_view", None)        # never break the page over a view

    def _render_stacks(**over):
        # The Apps page — also the home of the controller's embedded Update UI. All controller data is
        # cached-only — no live git/network/identity on a GET.
        ctx, _groups, _snapshot = _stacks_context()
        _controller_extras(ctx)
        ctx.update(over)
        new_passphrase = _passphrases.take(request.args.get("pw", "")) if request.args.get("pw") else None
        resp = make_response(render_template("stacks.html", new_passphrase=new_passphrase, **ctx))
        if new_passphrase:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/stacks/<sid>/body")
    def stack_body(sid: str):
        # Lazy per-stack body: the Apps page renders only OPEN stacks' bodies inline; a closed stack's
        # body (its settings/source/config forms) is fetched here when the operator expands it. Same
        # context as the whole page (so it can never miss a var), rendered read-only/GET-safe. A direct
        # `?open=<sid>` link still renders the body server-side, so this is a progressive enhancement.
        # only_sid scopes the heavy per-stack build to just this row (unknown sid -> empty -> 404).
        ctx, groups, _snapshot = _stacks_context(only_sid=sid)
        g = next((x for x in groups if x["stack"].id == sid), None)
        if g is None:
            abort(404)
        return render_template("_stack_body.html", g=g, **ctx)

    # ---- U4 (W423): every Stacks submenu level on its own page, and a tree of them all -------------
    # The descriptors (lhpc/adapters/web/submenus.py) name each level's id, parent, label and the ONE
    # partial the Stacks page includes for it; a page renders that partial with the same context.

    def _owner_menus(owner: str):
        """(Stacks context, descriptor list) of one owner: "controller" or a stack id; (None, []) if
        unknown. A stack builds only its own row (as its lazy body does); the controller none. Once
        per request and page radio."""
        memo = request.environ.setdefault("lhpc.owner_menus", {})
        key = (owner, _page_radio())
        if key not in memo:
            memo[key] = _build_owner_menus(owner)
        return memo[key]

    def _build_owner_menus(owner: str):
        if owner == _submenus.CONTROLLER:
            ctx, _groups, _snapshot = _stacks_context(only_sid=_submenus.CONTROLLER)   # no stack has that id
            _controller_extras(ctx)
            return ctx, _submenus.controller_menus(ctx)
        if service.stack(owner) is None:
            return None, []
        ctx, groups, _snapshot = _stacks_context(only_sid=owner)
        g = next((x for x in groups if x["stack"].id == owner), None)
        return (ctx, _submenus.stack_menus(g)) if g is not None else (None, [])

    def _menu_url(owner: str, mid: str) -> str:
        """A level's page URL; it keeps the page's radio (a radio switch stays on the page)."""
        return url_for("menu_page", owner=owner, mid=mid, radio=request.args.get("radio") or None)

    def _menu_return_field():
        """The hidden `return` field of a form shown on a level's page, or on a confirm page a POST
        from one renders ("" elsewhere, e.g. on the Stacks page)."""
        here = request.environ.get("lhpc.menu_return", "")
        if not here and request.method == "POST":
            here = (_menu_page_of(request.form.get("return", "")) or ("", "", "", ""))[3]
        return Markup('<input type="hidden" name="return" value="%s">') % here if here else ""

    app.jinja_env.globals["menu_return_field"] = _menu_return_field
    def _own_page(owner: str, mid: str, radio=None):
        """LOGS-IN-TREE B3: (endpoint, arguments) of a level whose page is an existing route, else None. The ONE
        rule for its tree link, its "page ›" and menu_page's redirect: the page's radio goes along only where the
        destination takes it and the stack runs on that radio; an RF log's writer and job come from the registry. An
        RF log without a page radio opens on the radio the stack runs on (W4: its first running instance's), a
        per-radio file else on the stack's first radio, else r1's (LEGACY-RFLOG: never a 0.13.x band file)."""
        d = _submenus.own_page(mid, service.stack(owner) if mid.startswith("log-") else None)
        if d is None:
            return None
        args = dict(d.args)
        stack = service.stack(owner) if d.radio else None
        rids = _instances.can_run_on(service, stack) if stack is not None else []
        rid = radio if radio in rids else ""
        if d.rf:
            e = _submenus.rf_entry(d.rf)
            rid = rid or min(_instances.recorded(service, owner), key=lambda r: int(r[1:]), default="")
            if e.per_radio or e.instanced:   # #75: a stack's RF log is its radio's file too, never the band-less one
                rid = rid or (rids[0] if rids else "")
                job = e.radio_job(rid or "r1") if e.per_radio else f"{rid or 'r1'}/{e.job()}"
            else:
                job = e.job()
            args.update(target=e.writer, job=job)
        if rid:
            args["radio"] = rid
        return d.endpoint, args

    def _own_page_url(owner: str, mid: str, radio=None) -> str:
        dest = _own_page(owner, mid, radio)
        return url_for(dest[0], **dest[1]) if dest else ""

    def _level_url(owner: str, mid: str, radio=None) -> str:
        """A level's link: its own route (_own_page), else its menu page (both keep the page's radio)."""
        return _own_page_url(owner, mid, radio) or url_for("menu_page", owner=owner, mid=mid, radio=radio or None)

    def _log_preview_url(owner: str, mid: str, radio=None, source: str = "") -> str:
        """LOGS-IN-TREE: what a log panel fetches: a fixed preview source (the controller's, the webserver's, the
        firewall's), else its own page's data: a program's /api/logs/<target>, an RF log's /api/rflog/<target>."""
        if source:
            return url_for("log_preview_api", source=source)
        _endpoint, args = _own_page(owner, mid, radio)
        if "job" in args:
            return url_for("rflog_records_api", target=args["target"], job=args["job"])
        return url_for("logs_api", **args)

    app.jinja_env.globals["own_page_url"] = _own_page_url
    app.jinja_env.globals["level_url"] = _level_url
    app.jinja_env.globals["log_preview_url"] = _log_preview_url
    app.jinja_env.globals["stack_logs"] = _submenus.stack_logs
    app.jinja_env.globals["log_groups"] = _submenus.log_groups
    app.jinja_env.globals["log_in_tree"] = _submenus.log_in_tree
    app.jinja_env.globals["daemon_views"] = _submenus.daemon_views
    app.jinja_env.globals["params_in_settings"] = _submenus.params_in_settings
    app.jinja_env.globals["with_radio"] = _submenus.with_radio
    app.jinja_env.globals["log_panels"] = _submenus.LOG_PANELS
    # U5: a page outside the Stacks page names a stack's row as the Stacks crumbs do (its stack's name)
    app.jinja_env.globals["stack_label"] = lambda sid: s.name if (s := service.stack(sid)) is not None else sid

    @app.get("/stacks/<owner>/menu/<mid>")
    def menu_page(owner: str, mid: str):
        """One level opened on its own page: the breadcrumb, a bar back to the Stacks page, then the
        level's own partial (the same markup as on the Stacks page), opened. Unknown owner, unknown
        id or an id that is not this owner's (e.g. another stack's component) -> 404."""
        rid = _page_radio()[0]                                             # an empty ?radio= is a 400
        if _submenus.id_radio(mid):         # UI-REORG s3: a per-radio level shows ITS radio (a differing ?radio= too)
            request.environ["lhpc.page_radio"] = _radio_of(_submenus.id_radio(mid))
        if mid == f"stack-settings-{owner}--repository":       # UI-REORG s2: the old Repositories URL, forwarded
            new = f"stack-install-{owner}--repositories"
            return redirect(url_for("menu_page", owner=owner, mid=new, radio=rid or None) + "#" + new)
        ctx, menus = _owner_menus(owner)
        path = _submenus.chain(menus, mid)
        if not path:
            abort(404)
        here = path[-1]
        own = _own_page_url(owner, mid, request.args.get("radio"))
        if own:
            return redirect(own)
        request.environ["lhpc.menu_return"] = _menu_url(owner, mid)
        body = _level_html(owner, here, ctx)
        if owner == _submenus.CONTROLLER:
            show = url_for("stacks_overview") + "#" + mid
        else:
            show = url_for("stacks_overview", open=owner, radio=request.args.get("radio") or None) + "#" + mid
        # `body` is the output of Jinja's own autoescaped render of the level's partial, not request text
        return render_template("menu_page.html", body=Markup(_open_first_details(body)), here=here,  # noqa: S704
                               page_rid=_submenus.id_radio(mid),          # UI-REORG s4: a Monitor/Live full page
                               crumbs=[(m.label, _level_url(owner, m.id, request.args.get("radio"))) for m in path],
                               show_url=show, **ctx)

    def _log_crumbs(owner: str, mid: str, fallback: tuple = ()):
        """LOGS-IN-TREE: a log page's crumbs below Apps, from the tree: the chain down to its level `mid`, the last
        crumb its one heading. A page that is no tree entry (v4 B4, G1-R2 N7): the tree-derived chain down to the
        nearest existing level, then a synthetic last crumb `fallback` = (anchor level id, its heading)."""
        _ctx, menus = _owner_menus(owner)
        radio = request.args.get("radio")
        path = _submenus.chain(menus, mid)
        last = None
        if not path and fallback:
            top = "controller-row" if owner == _submenus.CONTROLLER else f"stackrow-{owner}"
            path, last = _submenus.chain(menus, fallback[0]) or _submenus.chain(menus, top), fallback[1]
        crumbs = [(m.label, _level_url(owner, m.id, radio)) for m in path]
        if last:
            crumbs.append((last, ""))
        return crumbs

    @app.get("/stacks/menus")
    def menu_tree():
        """Every Stacks level as one tree (native <details>, no JS); each entry opens its page."""
        ctx, groups, _snapshot = _stacks_context(bodies=True)
        _controller_extras(ctx)
        owners = [(_submenus.CONTROLLER, _submenus.controller_menus(ctx))]
        owners += [(g["stack"].id, _submenus.stack_menus(g)) for g in groups]
        return render_template("menu_tree.html", owners=[(o, _submenus.children(m)) for o, m in owners], **ctx)

    def _level_html(owner: str, level, ctx: dict) -> str:
        """One level's partial rendered as on the Stacks page (its page shows this, opened)."""
        return render_template(level.template, **{**ctx, **level.vars, "menu_owner": owner, "menu_id": level.id})

    @app.before_request
    def _menu_block_before():
        """E3-1 (and gate-1 round 1 B2): BEFORE the view acts, which level holds the posted form, as
        the sent `return` page showed it; the action may remove or change the form."""
        ret = request.form.get("return", "") if request.method == "POST" else ""
        if ret:
            try:
                request.environ["lhpc.menu_block"] = _menu_block_of(ret)
            except Exception:                                              # never break the POST
                request.environ["lhpc.menu_block"] = None

    @app.after_request
    def _menu_return(response):
        """R3-B1, G2-B4/B5, ERRATA-UI-v5-1 + -3 E3-1: a POST sent from a level's page returns to the
        page of the level that holds its form (or that level's nearest ancestor on the way up from
        the sent page), with the action's owner and radio. Only when the view redirects to the Stacks
        page with navigation keys only (`_NAV_KEYS`); otherwise its own redirect stays, and a Stacks
        redirect that names no stack gets the action's `open` and `radio` (the K4 fallback)."""
        ret = request.form.get("return", "") if request.method == "POST" else ""
        if not ret or response.status_code not in (301, 302, 303, 307, 308):
            return response
        loc = urlsplit(response.headers.get("Location", ""))
        query = parse_qs(loc.query)
        if (loc.path != url_for("stacks_overview") or (loc.netloc and loc.netloc != request.host)
                or set(query) - _NAV_KEYS):
            return response
        try:
            block = request.environ.get("lhpc.menu_block")
            target = (_menu_return_target(block, query, loc.fragment) if block else "") \
                or _stacks_fallback(query, loc.fragment)
        except Exception:                                                  # never break the POST's answer
            target = ""
        if target:
            response.headers["Location"] = target
        return response

    @contextmanager
    def _as_page_radio(radio: str):
        """Build and render as the page of `radio` would (a POST has no page radio of its own: the
        levels of a per-radio Settings exist on that radio's page only)."""
        saved = request.environ.get("lhpc.page_radio")
        request.environ["lhpc.page_radio"] = _radio_of(radio)
        try:
            yield
        finally:
            if saved is None:
                request.environ.pop("lhpc.page_radio", None)
            else:
                request.environ["lhpc.page_radio"] = saved

    def _internal_url(url: str):
        """K4's internal-URL parse: the urlsplit parts of a path on this app, or None (a scheme, a
        host, `//`, a backslash, or a URL urlsplit refuses, e.g. `//[`)."""
        try:
            parts = urlsplit(url)
        except ValueError:                                                 # a malformed authority
            return None
        return None if parts.scheme or parts.netloc or "//" in url or "\\" in url else parts

    def _menu_page_of(ret: str):
        """(owner, id, radio, URL) of the level page `ret` names, or None: an internal path only
        (_internal_url, no fragment), `/stacks/<owner>/menu/<id>` of an id that owner has, and at
        most a `radio` query."""
        parts = _internal_url(ret)
        if parts is None or parts.fragment:
            return None
        segs = parts.path.split("/")
        if len(segs) != 5 or segs[:2] != ["", "stacks"] or segs[3] != "menu":
            return None
        query = parse_qs(parts.query, keep_blank_values=True)
        if set(query) - {"radio"} or any(len(v) != 1 or not v[0] for v in query.values()):
            return None
        owner, mid, radio = unquote(segs[2]), unquote(segs[4]), query.get("radio", [""])[0]
        with _as_page_radio(radio):
            if not _submenus.chain(_owner_menus(owner)[1], mid):
                return None
        return owner, mid, radio, url_for("menu_page", owner=owner, mid=mid, radio=radio or None)

    def _menu_block_of(ret: str):
        """(owner, level id, radio) of the level whose page holds the posted form: the first of the
        sent page's level and its ancestors whose partial renders a form with this POST's action,
        query and hidden fields (but the CSRF token and `return`); None if there is none."""
        page = _menu_page_of(ret)
        if page is None:
            return None
        owner, mid, radio, _url = page
        with _as_page_radio(radio), _flashes_untouched():
            ctx, menus = _owner_menus(owner)
            for level in reversed(_submenus.chain(menus, mid)):
                if _renders_form(_level_html(owner, level, ctx), request.path, request.args, request.form):
                    return owner, level.id, radio
        return None

    @contextmanager
    def _flashes_untouched():
        """The pre-action render reads no flash for good: a panel slot in it (panel_results) pops the
        pending flashes from the cookie store; they are put back, and the request's flash caches are
        reset, so the page after the POST still shows them."""
        saved = session.get("_flashes")
        try:
            yield
        finally:
            if saved is not None:
                session["_flashes"] = list(saved)
            request_ctx.flashes = None
            for name in ("flash_entries", "flash_taken"):
                _g.pop(name, None)

    def _action_owner(query: dict) -> str | None:
        """The stack the action's Stacks redirect opens (`open`/`cfg`/`dp`/`inst`), if any."""
        named = [v[0] for k in ("open", "cfg", "dp", "inst") for v in [query.get(k)] if v]
        return next((n for n in named if service.stack(n) is not None), None)

    def _menu_return_target(block, action_query: dict, dest: str) -> str:
        """The page URL (with its #id) of the level `block` = (owner, id, radio), if the action may
        return there, else "". The action's owner is the stack its redirect opens, else the owner one
        of whose levels its #anchor `dest` is; the level's owner must be it. The action's radio, when
        it names one, is the page's (a page without a radio gets it: it showed that radio's
        instance)."""
        owner, level_id, radio = block
        stack_owner = _action_owner(action_query)
        if stack_owner is not None and stack_owner != owner:
            return ""
        want = (action_query.get("radio") or [""])[0]
        if want and radio and want != radio:
            return ""                                                      # another radio's page
        with _as_page_radio(radio):
            menus = _owner_menus(owner)[1]
        if stack_owner is None and not _submenus.chain(menus, dest):
            return ""                                                      # not this owner's action
        return url_for("menu_page", owner=owner, mid=level_id, radio=radio or want or None) + "#" + level_id

    def _stacks_fallback(query: dict, dest: str) -> str:
        """K4 / ERRATA-UI-v5-1: a POST from a level page that does not return there lands on the Stacks
        page with the action's stack open and its radio. A view redirect that names no stack gets
        `open=<the stack whose level the #anchor is>` and, when it names no radio, the anchor's
        instance radio (`…@rN`); "" (the view's redirect stays) for a controller or unknown anchor."""
        owner = _action_owner(query)
        if owner is not None and query.get("radio"):
            return ""
        if owner is None:
            owner = next((s.id for s in service.stacks() if s.id in dest
                          and _submenus.chain(_owner_menus(s.id)[1], dest)), None)
        if owner is None:
            return ""
        args = {k: v[0] for k, v in query.items()}
        args.setdefault("open", owner)
        if "@" in dest and "radio" not in args:
            args["radio"] = dest.rsplit("@", 1)[1].split("--")[0]
        return url_for("stacks_overview", **args) + ("#" + dest if dest else "")

    @app.get("/stacks")
    def stacks_overview():
        _page_radio()                                                      # an empty ?radio= is a 400 (G1-W2-RE)
        if request.args.get("rid") and "radio" not in request.args:        # W2: W1's `dp=&rid=` link reads `?radio=`
            args = {k: v for k, v in request.args.items() if k != "rid"}
            return redirect(url_for("stacks_overview", **args, radio=request.args["rid"]))
        return _render_stacks()

    @app.get("/dependencies")
    def dependencies_page():
        # Read-only: every dependency of LHPC, the web server and each INSTALLED stack, with a fulfill
        # action or copyable command for each unmet one. No mutation / subprocess on load.
        return render_template("dependencies.html", overview=service.dependency_overview(),
                               version=__version__)

    @app.post("/self-update/check")
    def self_update_check():
        if not _csrf_ok():
            abort(400)
        res = service.self_update_check()          # NETWORK (explicit): git fetch, refresh cache
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("stacks_overview") + "#controller-update")

    @app.post("/source-check/<target>")
    def source_check(target: str):
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

    @app.post("/network/<op>")
    def network_action(op: str):
        # Wi-Fi Network panel (AP-managed boxes only): scan / connect / prefer / forget /
        # retry. POST + CSRF; 404 while the capability gate is unsatisfied (desktop boxes
        # and any box without the lhpc-ap profile never expose these). The connect flow is
        # two-stage (dedicated confirm page — the AP drops with the session) and
        # respond-first: the detached finalize helper does the activation.
        if not _csrf_ok():
            abort(400)
        if op not in ("scan", "connect", "prefer", "forget", "retry", "ap", "ap-cidr") \
                or not service.network_supported():
            abort(404)
        back = redirect(url_for("stacks_overview", open="network")
                        + "#controller-network")
        if op == "scan":
            res = service.network_scan()
            flash(res.summary, "ok" if res.ok else "warn")
            return back
        if op == "prefer":
            res = service.network_prefer(request.form.get("uuid", ""),
                                         request.form.get("on") == "1")
            flash(res.summary, "ok" if res.ok else "warn")
            return back
        if op == "forget":
            res = service.network_forget(request.form.get("uuid", ""))
            flash(res.summary, "ok" if res.ok else "warn")
            return back
        if op == "ap-cidr":
            # NA: the AP subnet setting through the one apply (phrase first, then the value; never stripped).
            res = service.network_set_ap_cidr(request.form.get("ap_cidr", ""),
                                              request.form.get("confirm_phrase", "").strip())
            flash(res.summary, "ok" if res.ok else "warn")
            return back
        if op == "retry":
            res = service.network_retry_now()
            flash(res.summary, "ok" if res.ok else "warn")
            return back
        if op == "ap":
            # back-to-AP: same two-stage confirm shape as connect (the session dies with
            # the switch, so everything the operator needs is said before the click).
            if request.form.get("confirmed") != "yes":
                plan = service.network_ap_now(apply=False)
                return render_template("network_confirm.html", op_target="ap",
                                       ssid="", uuid="", psk="", allow_console=False,
                                       question=plan.summary, details=plan.details)
            res = service.network_ap_now(apply=True)
            flash(res.summary, "ok" if res.ok else "warn")
            return back
        # connect: the PASSWORD is collected on the confirm page (stage 1 never sees it,
        # stage 2 reads it once and it is never echoed into any response — AUDIT).
        ssid = request.form.get("ssid", "")
        uuid = request.form.get("uuid", "")
        psk = request.form.get("psk", "")
        allow_console = request.form.get("allow_console") == "1"
        if request.form.get("confirmed") != "yes":
            plan = service.network_connect(ssid=ssid, uuid=uuid,
                                           allow_console=allow_console, apply=False)
            return render_template("network_confirm.html", op_target="connect",
                                   ssid=ssid, uuid=uuid,
                                   allow_console=allow_console,
                                   question=plan.summary, details=plan.details)
        res = service.network_connect(ssid=ssid, uuid=uuid, psk=psk,
                                      allow_console=allow_console, apply=True)
        flash(res.summary, "ok" if res.ok else "warn")
        return back

    @app.post("/power/<kind>")
    def power(kind: str):
        # Dashboard Reboot / Shut down. POST-only + CSRF; 404 for an unknown kind AND while the
        # polkit-rule dependency is unsatisfied (the hidden feature stays hidden — the buttons
        # only render when `power_supported()`). Stage 1 renders the dedicated confirm page
        # ("do you really want to…"); stage 2 applies via the service (handshake + pending
        # marker + bounded respond-first trigger) and redirects with the typed result.
        if not _csrf_ok():
            abort(400)
        if kind not in ("reboot", "poweroff") or not service.power_supported(kind):
            abort(404)
        if request.form.get("confirmed") != "yes":
            plan = service.power_action(kind, apply=False)
            return render_template("power_confirm.html", kind=kind,
                                   question=plan.summary, details=plan.details)
        # DEC-W11: the record names the client only by a certificate nginx verified (it sets both headers)
        _dn = request.headers.get("X-LHPC-Client-DN", "") if request.headers.get("X-LHPC-Client-Verify") == "SUCCESS" else ""
        res = service.power_action(kind, apply=True, client=_dn)
        flash(res.summary + ((" " + "; ".join(d.strip() for d in res.details))
                            if res.details else ""),
              "ok" if res.ok else "warn")
        return redirect(url_for("dashboard"))

    @app.post("/graywolf/upstream-check/<target>")
    def graywolf_upstream_check(target: str):
        # Fetched-package upstream check (GitHub releases API) — like source-check: non-mutating,
        # network, POST-only, its own route (never a GET, never the lifecycle dispatch).
        if not _csrf_ok():
            abort(400)
        sid = service.stack_of(target)
        if sid is None or not (service.graywolf_upstream_state(sid)):
            abort(404)
        res = service.graywolf_upstream_check(sid)
        flash(res.summary, "ok" if res.ok else "warn")
        return _install_back(sid)

    @app.post("/graywolf/upstream-update/<target>")
    def graywolf_upstream_update(target: str):
        # One-click update to the latest upstream release (checksums.txt verified). MUTATING,
        # so CSRF-protected; a normal POST applies (no separate confirm — the operator clicked
        # the Update button that only appears when upstream is genuinely ahead).
        if not _csrf_ok():
            abort(400)
        sid = service.stack_of(target)
        if sid is None or not (service.graywolf_upstream_state(sid)):
            abort(404)
        res = service.graywolf_upstream_update(sid, apply=True)
        flash(res.summary + (" " + "; ".join(res.details) if res.details else ""),
              "ok" if res.ok else "warn")
        return _install_back(sid)

    @app.route("/self-update/apply", methods=["GET", "POST"])
    def self_update_apply():
        # One-click self-update. The running console cannot apply in-process (it holds the
        # controller-runtime lock SHARED so its own code is never mutated underneath it);
        # a confirmed request starts the PARAMETER-FREE updater unit, which stops this
        # service, applies (all gates), syncs the venv and starts the console again.
        if request.method == "GET":
            # Both stages render INLINE at this URL, so the browser tab stays on /self-update/apply.
            # A stray GET (reload, Back button, or the browser re-requesting through the restart
            # outage) must NOT 405 — send it to the controller row instead.
            return redirect(url_for("stacks_overview") + "#controller-update")
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
            if st.get("have_upstream") and not st.get("update_available") and not (_dirty or _diverged):
                # Nothing to update (the panel says "Up to date."): no confirm to stop the console.
                flash("Already up to date — nothing to update.", "ok")
                return redirect(url_for("stacks_overview") + "#controller-update")
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
        # are already canonical, and otherwise migrates a non-canonical same-root deployment (old/%h units,
        # no .path) to the canonical set first — all in this one click — except compatible units
        # (another release's), which only the shell repair rewrites.
        overwrite = (request.form.get("overwrite") == "yes"
                     and (service.self_update_local_dirty() or service.self_update_ff_blocked()))
        # Every gate runs NOW (a refusal stays on /stacks); the marker itself is written only
        # after the response was closed, with a 1 s delay for any remaining flush — see
        # _UPDATE_QUEUE_DELAY_S.
        res = service.self_update_repair_and_trigger(overwrite=overwrite, queue=False)
        if not res.ok:
            flash(res.summary, "warn")
            return _render_stacks()

        def _queue_update() -> None:
            late = service.self_update_trigger(overwrite=overwrite)
            if not late.ok:
                print(f"WARN one-click update not queued after the Restarting page: {late.summary}",
                      file=sys.stderr, flush=True)

        resp = Response(render_template("updating.html", version=__version__))
        resp.call_on_close(lambda: _defer(_UPDATE_QUEUE_DELAY_S, _queue_update))
        return resp

    @app.get("/stacks/<stack_id>")
    def stack_detail(stack_id: str):
        # Redirects to the stack's row on the /stacks overview. Unknown stack -> 404.
        if service.build_snapshot().stack(stack_id) is None:
            abort(404)
        return redirect(url_for("stacks_overview", open=stack_id) + "#stackrow-" + stack_id)

    @app.post("/interactive/<stack_id>/dismiss")
    def interactive_dismiss(stack_id: str):
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        service.dismiss_interactive(stack_id)
        return redirect(url_for("dashboard"))

    @app.post("/hardware/changes/dismiss")
    def hardware_changes_dismiss():
        """W249/DEC-W42: the hotplug banner's ✕ deletes exactly the lines it showed."""
        if not _csrf_ok():
            abort(400)
        if not _hotplug.dismiss(service, request.form.getlist("id")):
            flash("The hardware changes could not be closed right now (busy); try again", "warn")
        return redirect(url_for("dashboard"))

    @app.post("/welcome/dismiss")
    def welcome_dismiss():
        if not _csrf_ok():
            abort(400)
        service.dismiss_welcome_note()
        return redirect(request.referrer or url_for("dashboard"))

    @app.post("/dependencies/dismiss")
    def dep_note_dismiss():
        # Only the OPTIONAL/GUI note is dismissable — the mandatory-missing and restart-pending
        # banners are a different branch of the same if/elif and stay permanent.
        if not _csrf_ok():
            abort(400)
        service.dismiss_dep_note(service.dependency_overview())
        return redirect(request.referrer or url_for("stacks_overview"))

    def _api_radio(rid: str) -> str:
        """The band of radio `rid` (the radio row's, CLEANUP-DESIGN-v13 13.3) for a per-radio daemon view or setting;
        a rid that is no enabled radio of the hardware list is a 404 with its reason (JSON), never a 500."""
        from lhpc.core import radio_start
        _inst, band, why = radio_start.daemon_radio(service, rid)
        if why:
            abort(make_response(jsonify(error=why), 404))
        return band

    def _dp_rid(stack_id: str, radios: list, inst=None) -> str:
        """W3, K8d (DEC-W384az): the radio a stack's daemon panel shows and saves: the page radio; without one, for the
        daemon's own panel its first enabled radio of `radios` (view_radios), for a client stack's the shown instance's,
        else its first radio able to run it; never a band's (G1-W3 N3)."""
        if rid := _view_rid(stack_id, ""):
            return rid
        if service._is_daemon_target(stack_id):
            return radios[0][0] if radios else ""
        return inst.rid if inst is not None and inst.rid else next(iter(_instances.radios_able(service, stack_id)), "")

    def _view_rid(stack_id: str, band: str) -> str:
        """The radio a stack's daemon panel shows: the page radio (W2's one selector, `?radio=rN`, every stack's panel:
        G1-W2-RE F2); "" (the band's view) without one, or for a rid that is no enabled radio (of `band` when one is
        given): the page never fails on it."""
        from lhpc.core import radio_start
        rid = _page_radio()[0]
        if not rid:
            return ""
        _inst, rband, why = radio_start.daemon_radio(service, rid)
        return "" if why or (band and rband != band) else rid

    @app.get("/api/daemon/<rid>")
    def daemon_api(rid: str):
        # The dashboard's per-radio column polls this every 3 s. It is a GET, so by this app's
        # contract it must be READ-ONLY — and it now genuinely is: the channel fields come from
        # the PASSIVE read, which never puts the radio into CAD. It used to call `GET CHANNEL`,
        # whose CAD scan destroyed frames in flight and cost ~46 % of reception at SF12/BW125.
        band = _api_radio(rid)
        view = service.daemon_view(band, rid)
        channel = service.daemon_channel(band, rid) if view.reachable else {}
        return jsonify(rid=rid, band=band, reachable=view.reachable, ready=view.ready,
                       radio_state=view.radio_state, status=view.status,
                       stats=view.stats, channel=channel,
                       feed=service.daemon_feed(band, 40, rid))

    @app.get("/api/daemon/<rid>/feed")
    def daemon_feed_api(rid: str):
        # The "RX/TX activity" window polls ONLY this. It used to poll /api/daemon/<radio> and
        # throw away everything except `feed`, dragging a full CAD scan behind a text log every
        # 3 s. Nothing here touches the radio.
        band = _api_radio(rid)
        return jsonify(rid=rid, band=band, feed=service.daemon_feed(band, 40, rid))

    @app.post("/api/daemon/<rid>/scan")
    def daemon_scan_api(rid: str):
        # A real CAD measurement, on explicit operator request only.
        #
        # POST + CSRF, NOT a GET: this is state-changing by this app's contract — it takes the
        # radio mutex, puts the chip into CAD and can destroy a frame that is arriving. Encoding
        # it as a nominally-safe GET would let a refresh, prefetch or retry fire it, which is in
        # miniature how a status read came to scan the channel in the first place. No confirm
        # page: the button click is sufficient operator intent.
        if not _csrf_ok():
            abort(400)
        band = _api_radio(rid)
        view = service.daemon_view(band, rid)
        channel = service.daemon_channel_scan(band, rid) if view.reachable else {}
        return jsonify(rid=rid, band=band, reachable=view.reachable, channel=channel)

    @app.get("/api/daemon/<rid>/socket")
    def daemon_socket_api(rid: str):
        # READ-ONLY live poll of the CONF socket for the "View Socket" monitor: one bounded,
        # sanitised status line per request (the radio validated -> never an arbitrary socket path;
        # fail-closed to '' when unreachable). The window/polling live entirely in the browser.
        band = _api_radio(rid)
        line = service.daemon_socket_line(band, rid)
        return jsonify(rid=rid, band=band, line=line, reachable=bool(line))

    @app.post("/radio/<rid>/set")
    def radio_set(rid: str):
        # Apply a LIVE daemon setting (runtime) on radio `rid` — a two-step plan + confirm, like install/update/clean..
        # First POST shows the plan; a confirmed POST applies. The key is whitelisted by the service; nothing transmits.
        if not _csrf_ok():
            abort(400)
        band = _api_radio(rid)
        key = request.form.get("key", "")
        value = request.form.get("value", "")
        if request.form.get("confirmed") != "yes":
            plan = service.daemon_set(rid, key, value, apply=False)
            fsk = key.upper() == "MODE" and value.upper() == "FSK"
            return render_template("confirm_radio.html", version=__version__, rid=rid, band=band,
                                   key=key, value=value, plan=plan, fsk=fsk,
                                   warn_stacks=service.lora_clients_on(rid) if fsk else [])
        result = service.daemon_set(rid, key, value, apply=True)
        # Truthful flash: the service summary already distinguishes "applied (confirmed)"
        # from "SENT (unconfirmed)". Never claim "Applied" for a setting the daemon does
        # not report back — an unconfirmed SET is flashed as a warning, not success.
        confirmed = bool(result.data.get("confirmed")) if result.data else False
        flash(result.summary, "ok" if (result.ok and confirmed) else "warn")
        return redirect(url_for("stacks_overview", cfg="daemon", radio=rid) + "#stack-settings-daemon")

    def _redirect_for(target: str, rid: str = ""):
        # Actions launched from the dashboard return to the dashboard; otherwise land on the
        # target's stack row on the /stacks overview, on its instance's radio (W2: `radio=`, never a band).
        if request.form.get("from") == "dash":
            return redirect(url_for("dashboard"))
        sid = service.stack_of(target)
        return (redirect(url_for("stacks_overview", open=sid, radio=rid or None) + "#stackrow-" + sid)
                if sid else redirect(url_for("dashboard")))

    def _install_back(stack_id: str):
        # TARGET-SPECIFIC: reopen ONLY this stack's row + its Install panel and scroll there, so a
        # refused start lands on the Install/Build buttons instead of wherever the page last was.
        # Always /stacks — even for a dashboard-launched start, since the buttons only exist here.
        return redirect(url_for("stacks_overview", open=stack_id, inst=stack_id)
                        + "#stack-install-" + stack_id + "--actions")

    def _settings_back(target: str, refusal, rid: str = ""):
        """A start/restart refused over the SAVED identity: flash the refusal and send the operator
        to the stack's Settings with the offending row(s) marked (`?cfg=` force-opens the section,
        `?bad=` names the `c_`/`f_` fields `_stack_settings.html` highlights). Nothing is persisted
        about the refusal — an ordinary refusal is an ordinary flash (settled decision)."""
        sid = service.stack_of(target) or target
        flash(refusal.summary, "warn")
        bad = ",".join(str(f) for f in (refusal.data.get("enforce_fields") or ()))
        # W2: the refused instance's own settings (`radio=`), where the saved value the refusal judged lives
        return redirect(url_for("stacks_overview", cfg=sid, bad=bad or None, radio=rid or None)
                        + "#stack-settings-" + sid)

    def _warned_heads() -> dict:
        """The HEADs the confirmed update's warnings named (STABLE-LANE R-3); none shown = {} (any warning refuses)."""
        try:
            heads = _json.loads(request.form.get("warned_heads") or "{}")
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in heads.items()} if isinstance(heads, dict) else {}

    def _render_confirm(op: str, target: str, source: str, frm: str, plan=None, inst=None, rid="", band=""):
        """The confirmation page. For start/restart it is rendered ONLY for a consequential choice
        (a resource conflict that stops another stack's owner, dependents a restart takes down) —
        a routine Start/Restart runs directly (Start means start). Install/update/clean
        keep their confirmation (source selector, typed clean). `band`: the §6 stray band daemon's Stop only (G4-web:
        every other plan names its instance, never a band)."""
        if plan is None:
            plan = service.run_action(op, target, apply=False, source=source, band=band, inst=inst)
        # Split the install/build system-dep gate: MANDATORY missing deps hard-block (missing_deps,
        # suppresses the Apply form); OPTIONAL missing deps only warn (optional_deps, form stays).
        # BUILD dependencies gate the SOURCE channel only — a download needs no toolchain
        # (the same channel-before-deps rule the CLI and the auto-install driver follow).
        _gate = (service.install_dep_gate(target)
                 if (op == "build" or (op == "install" and source != service.BINARY_CHANNEL))
                 else None)
        sid = service.stack_of(target)
        cancel_href = (url_for("dashboard") if (frm == "dash" or not sid)
                       else url_for("stacks_overview", open=sid) + "#stackrow-" + sid)
        return render_template(
            "confirm.html", version=__version__,
            op=op, target=target, plan=plan, tx=("tx" in op),
            source=source, band=band, rid=rid,
            shown_band=band or (getattr(inst, "band", "") if service._is_daemon_target(sid or target) else ""),   # K8f
            plan_hash=(_instances.plan_hash(service, op, target, inst, band, plan) if op in ("start", "restart") else ""),
            blockers=(plan.data.get("blockers") if op in ("start", "restart") else None),
            stop_labels=([lbl for lbl, _ in service.owner_stops(plan.data.get("blockers") or [])]
                         if op in ("start", "restart") else None),
            stop_deps=(plan.data.get("dependents") if op in ("stop", "restart") else None),
            other_bands=(plan.data.get("other_bands") if op in ("stop", "restart") else None),
            commands=(plan.data.get("commands") if op in ("start", "stop") else None),
            missing_deps=(_gate["block"] if _gate else None),
            optional_deps=(_gate["warn"] if _gate else None),
            frm=frm, cancel_href=cancel_href,
            source_choices=(web_client.SELECTORS if target == web_client.COMPONENT and op == "update" else
                            service.allowed_channels(service.stack_of(target) or target)
                            if op in ("install", "update") else None),
            # A refused binary install offers the source channel right on this page.
            offer_source=bool(plan.data.get("offer_source")),
            # No published binary this LHPC can install: the typed refusal.
            pin_refusal=op in ("install", "update") and _pin_refusal(plan))

    def _pin_refusal(plan) -> bool:
        """The confirm page's view of a `none_compatible` refusal, read from its typed data only
        (never its text)."""
        return jobresult.refusal_from(plan) is not None

    @app.post("/action")
    def action():
        if not _csrf_ok():
            abort(400)
        op = request.form.get("op", "")
        target = request.form.get("target", "")
        if op not in service.WEB_ACTIONS:
            abort(400)
        confirmed, rid, inst, band = request.form.get("confirmed") == "yes", "", None, ""   # band: the §6 stray Stop's
        if op in ("start", "stop", "restart"):
            # W2 (CLEANUP-DESIGN-v13 13.2): an instance, `<stack>@<rid>`, through the one parser; a `band` or `rid`
            # field is a second selector (obsolete, a bad request). A refusal says why and runs nothing; a disabled
            # radio's running instance still stops (G1-C7 B3). One exception, the §6 stray card: a band daemon no
            # radio names (a 0.13.x daemon still up) is stopped by its band, as before (W290: its Stop stays).
            stray = op == "stop" and target == "daemon" and "rid" not in request.form and \
                request.form.get("band") in set(service.RADIO_BANDS) - set(service.radio_bands()) and \
                any(r["band"] == request.form["band"] for r in service.stray_daemons())   # the card's rule
            if (("band" in request.form or "rid" in request.form) and not stray) or target.endswith("@"):
                abort(400)                                              # `kiss@` names no radio (G1-C54)
            sid, inst, band, why = (target, None, request.form["band"], "") if stray else \
                _instances.parse_target(service, target, op)
            # G4-web: the instance names its band (instances.radio_band, the daemon's too, K8f); kept only for the §6
            # stray Stop
            band = band if stray else ""
            # G1-W2 5: the parser hands an instance WITH a why only where a stop must reach it (instances.copy_of: a
            # disabled radio; a RUNNING instance whose radio left the list, whose row moved band — "stop it first" — or
            # whose band the stack cannot use); every other refusal comes without one and is refused for a stop too.
            if why and not (inst is not None and op == "stop"):
                flash(f"Cannot {op} {target}: {why}.", "warn")
                return _redirect_for(sid or target)
            target, rid = sid, (inst.rid if inst is not None else "")
        elif "rid" in request.form:                 # install, update, build, clean are stack-wide: no radio
            abort(400)
        elif "band" in request.form:                # G4-web: a stack-wide op names no band either (by name)
            abort(make_response(_BAND_REFUSAL, 400))
        # Source version selector (only meaningful for install/update). A MISSING selector defaults
        # to the CLI's `default_channel` — binary wherever it is published (a fresh Pi must not
        # silently start a four-hour compile because nobody touched the selector), "pinned"
        # everywhere else. An INVALID selector is rejected by run_action (never silently
        # rewritten). A binary plan does fetch the index, so an offline box renders a typed refusal
        # that offers the source channel — the honest outcome, not a source build nobody asked for.
        # An UPDATE follows the installed lane instead (`stack_lane`), as the CLI.
        _sid = service.stack_of(target) or target
        if op == "update":
            _dflt = service.stack_lane(target)
        else:
            _dflt = service.default_channel(_sid) if _sid else "pinned"
        source = request.form.get("source") or _dflt
        stop_owners = request.form.get("stop_owners") == "yes"
        cascade = request.form.get("cascade") == "yes"
        frm = request.form.get("from", "")     # origin page (e.g. "dash") for redirect
        # Refuse to start an app that isn't installed or built yet — send the operator to THIS
        # stack's Install section (which has the Install/Build buttons, and repeats the reason in a
        # banner) with a warning and the CLI command, rather than spawning a doomed start.
        # `open=` forces the row, `inst=` forces + scrolls to the Install panel (cf. `_dp_back`).
        if op == "start" and service.stack(target) is not None:
            if not service.is_installed(target):
                flash(f"'{target}' is not installed yet — install it on this page "
                      f"(or run: lhpc install {target}).", "warn")
                return _install_back(target)
            # The start readiness names each refusal with its own remedy (a build, the binary
            # reinstall, an unsafe install to resolve); changed build inputs alone start.
            blocked, _rebuild = service.start_readiness(target, inst)     # the radio it starts on
            if blocked:
                flash(f"'{target}' cannot start yet — "
                      + "; ".join(f"{cid}: {why}" for cid, why in blocked) + ".", "warn")
                return _install_back(target)
        if op in ("start", "restart") and not confirmed:
            # START MEANS START: plan the SAVED configuration (identity included — the plan and
            # the apply take every decision alike). A refusal flashes where the operator came
            # from; an identity refusal lands on the Settings row to fix; only a CONSEQUENTIAL
            # choice — another stack's owner to stop, dependents a restart takes down — asks for
            # confirmation. Everything else runs now.
            plan = service.run_action(op, target, apply=False, band=band, inst=inst)   # band: daemon/stray only
            if not plan.ok:
                if plan.data.get("enforce_fields"):
                    return _settings_back(target, plan, rid)
                flash(f"{plan.summary} {' '.join(plan.details[:6])}", "warn")
                return _redirect_for(target, rid)
            if plan.data.get("blockers") or plan.data.get("dependents"):
                return _render_confirm(op, target, source, frm, plan=plan, inst=inst, rid=rid)
        elif not confirmed:
            # Stage 1: show the dry-run plan, options and a confirmation form.
            return _render_confirm(op, target, source, frm, inst=inst, rid=rid, band=band)
        # Stage 2: apply.
        # DESTRUCTIVE clean: additionally requires the operator to TYPE the stack id —
        # a mismatch re-renders the confirm with ZERO mutation.
        purge = False
        if op == "clean":
            typed = (request.form.get("confirm_text") or "").strip()
            if typed != target:
                flash(f"Clean not applied: type the stack id '{target}' exactly to confirm "
                      "the destructive purge.", "warn")
                return _render_confirm(op, target, source, frm)
            purge = True
        # install/build/test run as detached jobs streaming to a log -> show it live.
        if op in ("install", "build", "test"):
            # Gate install/build on MANDATORY system dependencies: never proceed while a required
            # dev package / header / device node of a non-optional component is missing. Optional
            # deps only warn (surfaced on the confirm page), so they never block here.
            missing = (service.install_dep_gate(target)["block"]
                       if (op == "build" or (op == "install" and source != service.BINARY_CHANNEL))
                       else [])                     # a binary download needs no build toolchain
            if missing:
                flash("System dependencies missing — install them first: "
                      + "; ".join(d["install"] for d in missing if d["install"]), "warn")
                return _redirect_for(target, rid)
            job, admission, reason = service.spawn_web_job(op, target, source=source)
            if admission == "blocked":
                flash(reason or f"{op} could not start.", "warn")
                return _redirect_for(target, rid)
            if admission == "pending":
                flash(f"{op} starting — admission not yet confirmed; watch the live output below.", "warn")
            elif reason:                                # admitted, with a note (e.g. build-dep-first)
                flash(reason, "warn")
            else:                                       # admitted
                flash(f"{op} started — watch the live output below (it shows when it ends).", "ok")
            return redirect(url_for("logs_view", target=target, job=job))
        if op in ("start", "restart"):
            # DETACHED: the start/restart runs as a tracked job (the hidden `_stack-start` verb); the
            # page returns at once and the task banner follows it — a stack row shows "starting"
            # while the job runs and the page reloads once when it ends. Blocked = typed refusal.
            job, admission, reason = service.spawn_start_job(op, target, stop_owners=stop_owners,
                                                             cascade=cascade, rid=rid,
                                                             plan_hash=request.form.get("plan_hash", "") if confirmed else None)
            if job is None:                             # blocked, or admission_blocked (typed)
                flash(reason or f"{op} could not start.", "warn")
            elif admission == "pending":
                flash(f"{op.capitalize()} of '{target}' requested — admission not yet confirmed; "
                      "follow it in the banner.", "warn")
            else:
                flash(f"{'Restarting' if op == 'restart' else 'Starting'} '{target}' — follow it "
                      "in the banner.", "ok")
            return _redirect_for(target, rid)
        result = service.run_action(op, target, apply=True, source=source,
                                    cascade=cascade, band=band, purge=purge, inst=inst,
                                    expect_heads=_warned_heads() if op == "update" else None)
        if op == "update" and not result.ok and _pin_refusal(result):
            # The inline Update's pin refusal is shown on the confirm page itself, with the same
            # ways out (self-update first) as the dry run's — not flattened into a flash.
            return _render_confirm(op, target, source, frm, plan=result)
        if op == "update" and result.ok and result.data.get("needs_build"):
            # The updated sources need their build before the stack can start: the CLI's update
            # runs it inline, the console as its detached build job (the live output below). A
            # build that cannot start ends the update as the CLI's does when its build fails:
            # the failed outcome `needs-rebuild`, never a warning on a success.
            sid, *rest = result.data["needs_build"]
            job, admission, reason = service.spawn_web_job("build", sid)
            if admission == "blocked":
                res = service.needs_rebuild_result(
                    target, [sid, *rest], f"could not start its build ({reason or 'refused'})")
                flash(f"{res.summary} {' '.join(res.details[:6])}".strip(), "err")
                return _redirect_for(target, rid)
            flash(f"{result.summary} Building '{sid}' — watch the live output below.", "ok")
            if rest:
                flash(service.needs_rebuild_result(
                    target, rest, "is not built yet (one build job runs at a time)").summary, "warn")
            return redirect(url_for("logs_view", target=sid, job=job))
        # On a stop, MANUAL_REQUIRED means "a foreign process is still running, kill it yourself"
        # — a WARNING (`stop` already returns ok=False). Start notes travel in the start job's
        # marker detail (banner hint) since the start became detached.
        flash(f"{result.summary} {' '.join(result.details[:6])}",
              "ok" if result.ok else "warn")
        if op == "stop" and not result.ok and service.stack(target) is not None:
            # A failed, timed-out or refused stop offers the Kill of THAT instance (A §2.12, W44; gate 2 finding 1).
            return _render_kill(target, "", failed=result, rid=rid)
        return _redirect_for(target, rid)

    def _render_kill(sid: str, component: str, failed=None, rid: str = ""):
        # The kill confirm: one click, with the processes the kill would signal named first; an instance's kill keeps
        # its radio through the preview and the POST (gate 2 finding 1).
        plan = _kill.kill(service, f"{sid}@{rid}" if rid else sid, component)
        return render_template("kill_confirm.html", version=__version__, sid=sid, rid=rid,
                               component=component, plan=plan, failed=failed)

    @app.get("/kill/<sid>")
    def kill_page(sid: str):
        if service.stack(sid) is None:
            abort(404)
        return _render_kill(sid, request.args.get("component", ""), rid=request.args.get("radio", ""))

    @app.post("/kill/<sid>")
    def kill_action(sid: str):
        if not _csrf_ok():
            abort(400)
        if service.stack(sid) is None:
            abort(404)
        rid = request.form.get("radio", "")
        res = _kill.kill(service, f"{sid}@{rid}" if rid else sid, request.form.get("component", ""), apply=True)
        flash(f"{res.summary} {' '.join(res.details[:6])}", "ok" if res.ok else "warn")
        return _redirect_for(sid, rid)

    def _safe_job(value):
        # a plain logs/<name>.log filename, or exactly a radio's daemon RF log `r<1-99>/rf-daemon.log` (stage L5,
        # W7-8a; REL14-D33); never any other path
        if not isinstance(value, str) or not value:
            return None
        if _rflog.RADIO_JOB.fullmatch(value):
            return value
        return value if "/" not in value and ".." not in value else None

    def _log_radio() -> str:
        """W4 (DEC-W384bh): the radio a log view or an RF log form is addressed to (`radio`), "" without one; an empty
        value is a 400 and a rid that is no radio of the hardware list a 404, both by name; a `band` is a 400 by name
        (G1-W5, DEC-W384bn). A mutating route reads it BEFORE it changes anything (G1-W5 B2)."""
        if "band" in request.values:
            abort(make_response(_BAND_REFUSAL, 400))
        rid = request.values.get("radio")
        if rid is None:
            return ""
        if not any(r.id == rid for r in _radios_mod.load(service._paths).radios):
            abort(make_response(f"radio={rid!r} is not a radio of the hardware list", 404 if rid else 400))
        return rid

    @app.get("/logs/<target>")
    def logs_view(target: str):
        if service.stack_of(target) is None:
            abort(404)
        job, rid = _safe_job(request.args.get("job")), _log_radio()
        path, lines = service.log_tail(target, 300, job=job, rid=rid)
        rf = service.rflog_job(target, job)
        # An RF log (registry-authorized, never by name): the writer's run state is the badge,
        # the header carries the radio row and the stack row, the bottom the switches and Clear.
        switcher = service.rflog_switcher(job, rid) if rf else None
        return render_template("logs.html", version=__version__, target=target, job=job,
                               log_crumbs=_logs_view_crumbs(target, job, rf),
                               radio=switcher["radio"] if rf else rid,
                               stack_id=service.stack_of(target), path=path, lines=lines,
                               running=_log_running(target, job, rid, rf),
                               rflog=rf, switcher=switcher,
                               switch=service.rflog_switch(rf["surface"]) if rf else None,
                               logging=service.rflog_logging_state() if rf else "")

    def _logs_view_crumbs(target, job, rf):
        """/logs/<target>'s crumbs: an RF log's entry, a program's (a stack's own name: its main program's), else
        (v4 B4) a run's job log below the stack's Install, a program the tree does not list below its Dependencies
        row (each with a synthetic last crumb)."""
        if rf:
            return _log_crumbs(rf["owner"], f"rflog-{rf['owner']}")
        owner = service.stack_of(target)
        st = service.stack(owner)
        if job:
            return _log_crumbs(owner, "", (f"stack-install-{owner}--actions", "Job log"))
        comp = st.main_component.id if st is not None and target == st.id and st.main_component else target
        return _log_crumbs(owner, f"log-{comp}", (f"comp-{comp}", "Log"))

    def _log_running(target, job, rid, rf) -> bool:
        """The log page's badge: an RF log's writer; a radio's process log, that instance's verified launch record
        (G1-W5 N1: never another radio's instance); else the job's or the component's run state."""
        if rf:
            return service.rflog_running(job)
        if rid and not job:
            return rid in _instances.recorded(service, service.stack_of(target))
        return service.log_running(target, job)

    def _rflog_back(job, rid):
        # Back to the RF log page a bottom form came from — same job, same radio (a registered
        # job, else the dashboard). `rid`: _log_radio(), read before the form's change (G1-W5 B2).
        job = _safe_job(job)
        rf = _rflog.by_job(job or "")
        return redirect(url_for("logs_view", target=rf[0].writer, job=job, radio=rid or None) if rf
                        else url_for("dashboard"))

    @app.post("/rflog/all")
    def rflog_all():
        # Every stack's switch at once, through the same per-owner path as one switch.
        if not _csrf_ok():
            abort(400)
        rid = _log_radio()
        result = service.set_rflog_all(request.form.get("value", ""))
        flash(result.summary, "ok" if result.ok else "warn")
        return _rflog_back(request.form.get("job"), rid)

    @app.post("/rflog/clear-all")
    def rflog_clear_all():
        if not _csrf_ok():
            abort(400)
        rid = _log_radio()
        result = service.rflog_clear_all()
        flash(result.summary, "ok" if result.ok else "warn")
        return _rflog_back(request.form.get("job"), rid)

    @app.get("/api/rflog/<target>")
    def rflog_records_api(target: str):
        # The viewer's records: registry-authorized like everything RF.
        if service.stack_of(target) is None:
            abort(404)
        job = _safe_job(request.args.get("job"))
        rf = service.rflog_job(target, job)
        if rf is None:
            abort(404)
        path, records = service.rflog_records(target, job)
        return jsonify(target=target, job=job, path=path, records=records,
                       running=service.rflog_running(job))

    @app.get("/api/rflog/<target>/decoded")
    def rflog_decoded_api(target: str):
        # Decrypted in memory with the keys on this box; the response is never cached anywhere.
        if service.stack_of(target) is None:
            abort(404)
        job = _safe_job(request.args.get("job"))
        rf = service.rflog_job(target, job)
        if rf is None or not rf.get("decoder"):
            abort(404)
        path, records = service.rflog_records(target, job)
        res = service.rflog_decode(target, job, records)
        resp = jsonify(target=target, job=job, path=path, records=res["records"],
                       error=res["error"], running=service.rflog_running(job))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/logs/<target>/clear")
    def logs_clear(target: str):
        if service.stack_of(target) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        job, rid = _safe_job(request.form.get("job")), _log_radio()
        if service.rflog_job(target, job) is None:
            abort(404)                      # not a registered RF log of this writer
        result = service.rflog_clear(target, job)
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("logs_view", target=target, job=job, radio=rid or None))

    @app.get("/api/logs/<target>")
    def logs_api(target: str):
        if service.stack_of(target) is None:
            abort(404)
        job, rid = _safe_job(request.args.get("job")), _log_radio()
        path, lines = service.log_tail(target, 300, job=job, rid=rid)
        return jsonify(target=target, path=path, lines=lines,
                       running=_log_running(target, job, rid, service.rflog_job(target, job)))

    @app.post("/stacks/<stack_id>/config")
    def stack_config_save(stack_id: str):
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        inst = _settings_form_inst(stack_id)
        band = inst.band if inst else ""
        view = service.config_view(stack_id, band, rid=getattr(inst, "rid", ""))       # K8f S4-1: the instance's fields
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
        for opt in view["optional"]:                         # the radio's own ticks (Q-09 C)
            if not opt.get("startable", True):
                continue                       # listed as run-on-demand: no auto-start key
            values[f"autostart_{opt['id']}"] = (
                "on" if request.form.get("c_autostart_" + opt["id"]) else "")
        # PLAIN CONFIG EDITING may leave a stack without an identity — the operator is allowed to
        # clear a field, and a stopped stack may hold incomplete configuration. The protection is
        # at the LAUNCH boundary, which refuses to start without a resolvable identity; the row
        # here carries the problem note, and the next Start sends the operator back to this row.
        result = service.save_config_bundle(
            stack_id, values=values, inst=inst)                # G4-web: the instance names its band
        flash(result.summary + (" " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", radio=inst.rid if inst else None, cfg=stack_id)
                        + "#stack-settings-" + stack_id)

    @app.post("/stacks/<stack_id>/remotes")
    def stack_remotes_save(stack_id: str):
        """UI-REORG s2: Install › Repositories saves the source remotes ONLY (the config save would clear absent flag
        fields and auto-start ticks). A COMPLETE map (blank reverts to the default), one transaction."""
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        remotes = {}
        for src in service.config_view(stack_id, "")["sources"]:
            field = request.form.get("remote_" + src["id"], src["remote"])
            remotes[src["id"]] = "" if field.strip() == src["default"] else field.strip()
        result = service.save_config_bundle(stack_id, remotes=remotes)
        flash(result.summary + (" " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        rep = f"stack-install-{stack_id}--repositories"
        return redirect(url_for("stacks_overview", open=stack_id) + "#" + rep)

    def _settings_copy(stack_id, rid, refuse=False):
        """The instance `<stack>@<rid>` a Settings page or form names (instances.copy_of: its band is its radio's), None
        without a rid; a form naming a radio without an instance, or a disabled one, is a 404, a page shows its default."""
        copy, why = _instances.copy_of(service, stack_id, rid) if rid else (None, "")
        if why and refuse:
            abort(make_response(why, 404))
        return None if why else copy

    _BAND_REFUSAL = "band is a radio's attribute: use radio=<rid>"   # W3, G1-W3 B1/N3 (13.3, W312)

    def _settings_form_inst(stack_id):
        """W3 (13.2): the instance a Settings Save or Reset writes: its `radio`, required when the stack has one (no
        hardware list, or the daemon: the global settings); a `band` (form or query) is a 400 by name, never a radio's."""
        if "band" in request.values:
            abort(make_response(_BAND_REFUSAL, 400))
        rid = request.form.get("radio", "")
        if not rid and _instances.settings_choice(service, stack_id, "")["radio"]:
            abort(make_response(f"{stack_id}'s settings are per radio: name the radio (radio=rN)", 400))
        return _settings_copy(stack_id, rid, refuse=True)

    def _daemon_param_form(stack_id):  # (rid, {PARAM: value}) from the submitted panel
        """W3, G1-W3 B1: `radio` names the radio (its band is its row's; unknown or disabled: a 404 by name), and the
        daemon's own panel needs it. A `rid` (form or query), a `band` in the query, beside a radio or for the daemon
        is a 400 by name. K8d (DEC-W384az): every panel is one radio's, a client stack's too (its instance set there)."""
        from lhpc.core import daemon_params as _dp
        rid = request.form.get("radio", "")
        if "rid" in request.values:
            abort(make_response(jsonify(error="rid is no field here: name the radio (radio=<rid>)"), 400))
        if "band" in request.values:
            abort(make_response(jsonify(error=_BAND_REFUSAL), 400))
        if not rid:
            abort(make_response(jsonify(error="the daemon's radio parameters are per radio: name it (radio=<rid>)"), 400))
        _api_radio(rid)                                  # a rid that is no enabled radio: a 404 by name
        return rid, {name: request.form.get("dp_" + name, "") for name in _dp.ALL_PARAMS}

    def _dp_back(stack_id: str, rid: str):
        # TARGET-SPECIFIC: reopen ONLY this stack's row + its daemon-params panel (never every
        # daemon panel on the page). `dp=<stack_id>` opens just the matching per-stack panel; the
        # `open=<stack_id>` forces the row and the anchor scrolls to it.
        return redirect(url_for("stacks_overview", radio=rid, open=stack_id, dp=stack_id)    # W3, K8d: always its radio
                        + "#stack-daemon-params-" + stack_id)

    @app.get("/api/gps")
    def gps_monitor_api():
        # The GPS Monitor pane polls this only while open. READ-ONLY and it logs nothing: the
        # response carries coordinates by design (docs/gps.md, "Monitor"); no log line may.
        return jsonify(service.gps_monitor())

    @app.get("/api/gps/nmea")
    def gps_nmea_api():
        # gpsd sources only; for a direct receiver the sample's own tail rides in /api/gps and this
        # answers [] — it never opens a serial device (one reader, one sample).
        return jsonify(lines=service.gps_nmea())

    @app.post("/gps")
    def gps_set():
        """Set the GLOBAL position source. Same single entry point the CLI uses, so the two
        surfaces cannot drift: everything (validation, the running-consumer refusal, the
        gpsd-ownership warning) lives in `set_gps`, not here."""
        if not _csrf_ok():
            abort(400)
        fields = {}
        for form_key, api_key in (("gps_source", "source"), ("gps_host", "host"),
                                  ("gps_port", "port"), ("gps_device", "device"),
                                  ("gps_baud", "nmea_baud"), ("gps_lat", "fixed_lat"),
                                  ("gps_lon", "fixed_lon"), ("gps_alt", "fixed_alt")):
            raw = request.form.get(form_key)
            if raw is None:
                continue
            raw = raw.strip()
            if api_key in ("port", "nmea_baud"):
                if not raw:
                    continue
                try:
                    fields[api_key] = int(raw)
                except ValueError:
                    flash(f"{form_key.replace('gps_', '')} must be a number", "warn")
                    return redirect(url_for("stacks_overview", open="daemon", cfg="daemon")
                                    + "#stack-settings-daemon")
            elif api_key.startswith("fixed_") and not raw:
                # Blank means UNCHANGED, not cleared: the form never renders the current
                # coordinates back, so an empty box carries no intent to erase them. Switching
                # the source away from `fixed` is what clears a stored position.
                continue
            else:
                fields[api_key] = raw
        result = service.set_gps(**fields)
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", open="daemon", cfg="daemon")
                        + "#stack-settings-daemon")

    def _hw_list_ctx(view: dict) -> dict:
        """What `_hw_list.html` reads: the list `view`, the models, and each row's +20 dBm state (DEC-W57: a
        band's state shows in its radios' rows). /hardware and the daemon row's Hardware submenu share it."""
        from lhpc.core import radio_models
        return {"view": view, "models": radio_models.load(),
                "hp": {r["rid"]: service.high_power_state(r["band"], rid=r["rid"]) for r in view["rows"]}}

    @app.get("/hardware")
    def hardware_view():
        """B5: the Hardware section — each radio of the hardware list, its daemon on its own socket, the USB
        sticks a USB radio can be bound to, and the USB setup's copybox. No command and no network; with a USB row
        it writes the setup file the copybox line checks (state/usb-setup/usb-setup.sh, atomic) only when it is
        missing or differs (B6-D)."""
        from lhpc.core import radio_start
        return render_template("hardware.html", **_hw_list_ctx(radio_start.hardware_view(service)))

    @app.get("/hardware/radio/<rid>")
    def hardware_radio(rid: str):
        """W257: one radio's detail: its facts and problems, Re-bind (a USB radio), Detect and Remove."""
        from lhpc.core import radio_models, radio_start
        r = radio_start.radio_detail(service, rid)
        if r is None:
            abort(404)
        from lhpc.core import rflog
        dstat = next((ss.components.get(service.DAEMON_ID) for ss in service.build_snapshot().stacks
                      if service.DAEMON_ID in ss.components), None)     # F8: no RF-log link without an installed daemon
        rf = ([x for x in radio_start.daemon_rf_links(service, rflog.entry("daemon"), r["band"])
               if x.get("error") or x["job"].startswith(r["rid"] + "/")] if service._daemon_installed(dstat) else [])
        return render_template("hardware_radio.html", r=r, models=radio_models.load(), rflogs=rf,
                               hp=service.high_power_state(r["band"], rid=r["rid"]), detect=session.pop("hw_detect", None))

    @app.get("/hardware/add")
    def hardware_add_page():
        """W257: Add Hardware: each plugged-in stick no radio holds (its model picked here), and one HAT row."""
        from lhpc.core import radio_models, radio_start
        models, sticks, view = radio_models.load(), {}, radio_start.hardware_view(service)
        valid = view["kind"] in ("valid", "absent")      # the first radio is added here; an unreadable list offers nothing
        for mid, c in view["addable"] if valid else ():
            sticks.setdefault(c.port, {"port": c.port, "label": c.label, "models": []})["models"].append(models[mid])
        boards = []                     # (model:variant, label): a variant is named only where the band does not pick it
        for m in (m for m in models.values() if m.transport == "spi"):
            keyed = [v for v in m.variants if not v.band] if len(m.variants) > 1 else []
            boards += [(f"{m.id}:{v.key}", f"{m.label} ({v.key})") for v in keyed] or [(f"{m.id}:", m.label)]
        return render_template("hardware_add.html", sticks=list(sticks.values()), boards=boards, valid=valid,
                               list_state=view, detect=session.pop("hw_detect", None),
                               setups=service.hw_setups(), setup=service.hardware_setup())     # W292: the board setup

    @app.post("/hardware/add-hat")
    def hardware_add_hat():
        """W257: add one HAT (SPI) radio to the list (radio_start.add_hat: its own checks and lock)."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core import radio_start
        model, _, variant = request.form.get("board", "").partition(":")
        ok, msg = radio_start.add_hat(service, model, request.form.get("band", ""), variant)
        flash(msg, "ok" if ok else "warn")
        return redirect(url_for("hardware_view") if ok else url_for("hardware_add_page"))

    @app.post("/hardware/enabled")
    def hardware_enabled():
        """W257/W252: the list row's one button, Disable or Enable (radio_start.set_enabled: its own checks)."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core import radio_start
        ok, msg = radio_start.set_enabled(service, request.form.get("rid", ""), request.form.get("enabled") == "on")
        flash(msg, "ok" if ok else "warn")
        return redirect(_hardware_back(request.form.get("back", "")) or url_for("hardware_view"))

    def _hardware_back(back: str) -> str:
        """U5 / E3-6: the page a Hardware form returns to (its `back`), rebuilt, or "" (the caller's default):
        K4's internal-URL parse, then one of /hardware, /hardware/add, /hardware/radio/<rid> of a listed radio
        (no query, no fragment), or the daemon row's Stacks page `/stacks?open=daemon[&radio=<listed rid>]
        #stack-hardware-daemon`."""
        from lhpc.core import radios
        parts = _internal_url(back)
        if parts is None:
            return ""
        listed = {r.id for r in radios.load(service._paths).radios}
        if not parts.query and not parts.fragment:
            pages = {url_for("hardware_view"), url_for("hardware_add_page")}
            pages |= {url_for("hardware_radio", rid=rid) for rid in listed}
            return parts.path if parts.path in pages else ""
        query = parse_qs(parts.query, keep_blank_values=True)
        radio = query.pop("radio", [""])
        if (parts.path != url_for("stacks_overview") or query != {"open": ["daemon"]} or len(radio) != 1
                or (radio[0] and radio[0] not in listed) or parts.fragment != "stack-hardware-daemon"):
            return ""
        return url_for("stacks_overview", open="daemon", radio=radio[0] or None) + "#stack-hardware-daemon"

    @app.post("/hardware/detect")
    def hardware_detect():
        """DEC-W43 W3: Detect a chip without its daemon (radio_detect.run, whose refusals are the only guard); the
        result line shows once, next to the button it was pressed on (kept for one render, like the probe)."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core.radio_detect import run
        f, kind = request.form, request.form.get("kind", "")
        if kind == "radio":
            key, res = f.get("rid", ""), run.detect_radio(service, f.get("rid", ""))
        elif kind == "stick":
            key, res = "port-" + f.get("port", ""), run.detect_stick(service, f.get("model", ""), f.get("port", ""))
        elif kind == "board":
            model, _, variant = f.get("board", "").partition(":")
            key, res = "board", run.detect_board(service, model, variant, f.get("band", ""))
        else:
            abort(400)
        session["hw_detect"] = {"key": key, "outcome": res.outcome, "line": res.line,      # A18: the probed choice
                                "picked": {k: f.get(k, "") for k in ("model", "board", "band")}}  # stays shown
        back = _hardware_back(f.get("back", ""))         # W257: back to the page Detect was pressed on (E3-6)
        return redirect((back.partition("#")[0] or url_for("hardware_view")) + "#detect-" + key)

    @app.post("/hardware/bind")
    def hardware_bind():
        """B5 U-4…U-7: bind a USB radio to the stick the operator chose; the hardware list's own save checks."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core import radio_start
        rid = request.form.get("rid", "")
        ok, msg, box = radio_start.bind_usb(service, rid, request.form.get("port", ""))
        flash(msg + (" — run the USB setup on the Hardware page to give this account access" if box else ""),
              "ok" if ok else "warn")
        from lhpc.core import radios
        known = rid in {r.id for r in radios.load(service._paths).radios}      # C23: never a redirect to a 404
        return redirect(url_for("hardware_view") + "#usb-setup" if box
                        else url_for("hardware_radio", rid=rid) if known else url_for("hardware_view"))

    @app.post("/hardware/add")
    def hardware_add():
        """0.14.0 B: add a listed USB stick as a new radio of the hardware list (its own save checks)."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core import radio_start
        ok, msg, box = radio_start.add_usb(service, request.form.get("model", ""), request.form.get("port", ""))
        flash(msg + (" — run the USB setup below to give this account access" if box else ""), "ok" if ok else "warn")
        if not ok:
            return redirect(url_for("hardware_add_page"))
        return redirect(url_for("hardware_view") + ("#usb-setup" if box else ""))

    @app.get("/hardware/remove/<rid>")
    def hardware_remove_confirm(rid: str):
        """DEC-W39: the confirm step of Remove radio: what goes, what stays; the refusal instead while it runs."""
        from lhpc.core import radio_start
        return render_template("hardware_remove.html", plan=radio_start.remove_plan(service, rid),
                               stop_first=radio_start.stop_first)

    @app.post("/hardware/remove")
    def hardware_remove():
        """DEC-W39: remove one radio's row; only with its confirm phrase, else nothing changes."""
        if not _csrf_ok():
            abort(400)
        from lhpc.core import radio_start
        res = radio_start.remove_radio(service, request.form.get("rid", ""), request.form.get("confirm_phrase", ""))
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("hardware_view"))

    @app.post("/hardware")
    def hardware_set():
        if not _csrf_ok():
            abort(400)
        result = service.set_hardware_setup(request.form.get("hardware", ""))
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("hardware_add_page") + "#hw-setup")          # W292: the form is Add Hardware's

    @app.post("/hardware/high-power")
    def hardware_high_power():
        """A radio's +20 dBm permission (daemon 1.2.0 `--high-power`, W311) in its row of the hardware list; saves,
        restarts nothing."""
        if not _csrf_ok():
            abort(400)
        result = service.set_high_power(request.form.get("rid", ""), request.form.get("value", ""))
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", open="daemon", cfg="daemon")
                        + "#stack-settings-daemon")

    @app.post("/stacks/<stack_id>/rflog")
    def stack_rflog_save(stack_id: str):
        # ONE key on the config OWNER through the merging bundle path (never the whole Settings
        # form, whose parser fills every missing field with its default). Band-less by design.
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        rid = _log_radio() if request.form.get("from") == "logs" else ""      # G1-W5 B2: before the save
        result = service.set_rflog(stack_id, request.form.get("value", ""))
        flash(result.summary, "ok" if result.ok else "warn")
        if request.form.get("from") == "logs":       # the log page's bottom form
            return _rflog_back(request.form.get("job"), rid)
        return redirect(url_for("stacks_overview", open=stack_id) + "#stack-settings-" + stack_id)

    @app.post("/stacks/<stack_id>/daemon-params")
    def daemon_params_save(stack_id: str):
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        rid, values = _daemon_param_form(stack_id)
        result = service.save_daemon_params(stack_id, values, rid=rid)
        flash(result.summary, "ok" if result.ok else "warn")
        return _dp_back(stack_id, rid)

    @app.post("/stacks/<stack_id>/daemon-params/apply")
    def daemon_params_apply(stack_id: str):
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        rid, values = _daemon_param_form(stack_id)
        save = service.save_daemon_params(stack_id, values, rid=rid)   # persist first
        if not save.ok:
            flash(save.summary, "warn")
            return _dp_back(stack_id, rid)
        result = service.apply_daemon_params(stack_id, rid=rid)         # then push live
        # Truthful: partial/total apply failure flashes as a warning (never green), and the
        # timing override stays saved regardless.
        flash("Saved. Apply live: " + result.summary
              + (" — " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return _dp_back(stack_id, rid)

    @app.post("/stacks/<stack_id>/daemon-params/reset")
    def daemon_params_reset(stack_id: str):
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        rid, _values = _daemon_param_form(stack_id)
        result = service.reset_daemon_params(stack_id, rid=rid)
        flash(result.summary, "ok" if result.ok else "warn")
        return _dp_back(stack_id, rid)

    @app.post("/stacks/<stack_id>/mode")
    def stack_mode_save(stack_id: str):
        """The MeshCore stack's Mode, saved from the stack body: the SAME saved setting as the
        Settings card's row, through the same config API and the same save-time rules (a repeater
        mode needs the saved repeater name; a change is restart-required). Never a start value."""
        if stack_id != _meshcore_mode.STACK_ID or service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        rid = request.form.get("radio", "")              # K8f S4-2 (D2-CTX N3): the form's radio, kept on the way back
        inst, why = None, ""
        if not rid and _radios_mod.load(service._paths).kind != _radios_mod.ABSENT:   # G1-K8F-S4-2 B1: a box with a
            why = _instances.parse_target(service, stack_id, "start")[3] or "name the radio"   # list saves a radio's mode
        elif rid:                                                                         # only; refused by name
            _sid, inst, _band, why = _instances.parse_target(service, f"{stack_id}@{rid}", "start")
        if why:
            abort(make_response(f"Mode: {why}", 400))
        result = service.save_config(stack_id, {"file_mode": request.form.get("mode", "")}, inst=inst)
        flash(result.summary + (" " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", open=stack_id, **({"radio": rid} if rid else {})) + "#stack-mode-" + stack_id)

    @app.post("/stacks/<stack_id>/config/reset")
    def stack_config_reset(stack_id: str):
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        inst = _settings_form_inst(stack_id)
        result = service.reset_config(stack_id, inst.band if inst else "", inst)
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("stacks_overview", radio=inst.rid if inst else None, cfg=stack_id)
                        + "#stack-settings-" + stack_id)

    @app.get("/healthz")
    def healthz():
        # Cheap liveness: manifest parses; does NOT run the full probe sweep.
        stacks = service.stacks()
        return jsonify(status="ok", stacks=len(stacks), version=__version__)

    @app.errorhandler(404)
    def _not_found(_err):
        return render_template("error.html", code=404, message="Not found"), 404

    @app.errorhandler(405)
    def _method_not_allowed(_err):
        # A GET can only 405 on a POST-only ACTION URL — a browser stranded on the form target
        # (see /self-update/apply's stray-GET note). Never dead-end that reload; a non-GET 405
        # (a stray POST to a page URL) stays a real error page.
        if request.method == "GET":
            return redirect(url_for("stacks_overview"))
        return render_template("error.html", code=405, message="Method not allowed"), 405

    @app.errorhandler(Exception)
    def _unexpected(err):
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
    # /stacks (no separate page). This route redirects to that anchor.
    @app.route("/stacks/loraham-pi-control")
    def controller_webserver():
        return redirect(url_for("stacks_overview") + "#webserver-row")

    @app.get("/webserver/logs")
    def webserver_logs():
        # The LHPC-managed nginx front-end's on-disk access/error logs. Read-only, cached-only:
        # a bounded no-follow tail (no service probe). `src` is whitelisted to error|access.
        src = "access" if request.args.get("src") == "access" else "error"
        path, lines = service.webserver_log_tail(src, 300)
        return render_template("webserver_logs.html", version=__version__, src=src, path=path, lines=lines,
                               log_source="webserver-" + src,
                               log_crumbs=_log_crumbs(_submenus.CONTROLLER, "webserver-row--logs",
                                                      ("webserver-row", "Logs")))

    @app.get("/firewall/logs")
    def firewall_logs():
        # The managed firewall's per-check diagnostic log (/run/lhpc-firewall/firewall.log),
        # written by the root helper and read GET-safe (bounded no-follow). tmpfs — per boot.
        path, lines = service.firewall_log_tail(300)
        return render_template("firewall_logs.html", version=__version__, path=path, lines=lines, log_source="firewall",
                               log_crumbs=_log_crumbs(_submenus.CONTROLLER, "firewall-row--log", ("firewall-row", "Log")))

    @app.get("/controller/logs")
    def controller_logs():
        # The controller's OWN process logs — on-disk files under logs/ (StandardOutput=append:).
        # Read-only, non-network, bounded no-follow file tail; `src` is an EXPLICIT whitelist —
        # an unknown value normalizes to "web" here AND is rejected by the service map, so it can
        # never alias to the wrong log.
        _SRC_UNITS = {"web": "lhpc-web.service", "selfupdate": "lhpc-selfupdate.service",
                      "boot-restore": "lhpc-boot-restore.service"}
        src = request.args.get("src", "web")
        if src not in _SRC_UNITS:
            src = "web"
        unit = _SRC_UNITS[src]
        path, lines = service.controller_log_tail(src, 300)
        return render_template("controller_logs.html", version=__version__, src=src, unit=unit,
                               path=path, lines=lines, log_source="controller-" + src,
                               log_crumbs=_log_crumbs(_submenus.CONTROLLER, "controller-logs", ("controller-row", "Logs")))

    # LOGS-IN-TREE B2 (G1-R2 N5): the Apps page's preview of the controller's, the webserver's and the firewall's logs.
    # A fixed whitelist, each source the bounded tail reader its full page uses; any other value is 404. The answer
    # is the source and its LAST 30 lines (no file path); never cached (every answer's no-store, _SECURITY_HEADERS).
    # UI-REORG s5: `?lines=N` (1..300, the full pages' history depth) for a full log page's 3 s refresh.
    _PREVIEW = {"controller-web": lambda n: service.controller_log_tail("web", n),
                "controller-selfupdate": lambda n: service.controller_log_tail("selfupdate", n),
                "controller-boot-restore": lambda n: service.controller_log_tail("boot-restore", n),
                "webserver-error": lambda n: service.webserver_log_tail("error", n),
                "webserver-access": lambda n: service.webserver_log_tail("access", n),
                "firewall": lambda n: service.firewall_log_tail(lines=n)}
    _PREVIEW_LINES, _PAGE_LINES = 30, 300

    @app.get("/api/logpreview/<source>")
    def log_preview_api(source: str):
        read = _PREVIEW.get(source)
        if read is None:
            abort(404)
        n = max(1, min(request.args.get("lines", _PREVIEW_LINES, type=int), _PAGE_LINES))
        _path, lines = read(n)
        return jsonify(source=source, lines=list(lines)[-n:])

    def _ws_back():
        return redirect(url_for("stacks_overview") + "#webserver-row")

    @app.post("/boot-restore")
    def boot_restore_set():
        if not _csrf_ok():
            abort(400)
        res = service.set_boot_restore(request.form.get("restore") == "on")
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("dashboard"))          # the switch lives on the dashboard's System box

    @app.route("/webserver/configure", methods=["POST"])
    def webserver_configure():
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
        if "cidrs" in f:
            fields["allowed_cidrs"] = [x.strip() for x in f.get("cidrs", "").split(",") if x.strip()]
        # Single "Apply": save-and-apply. remote_exposed is derived from bind in the service; a remote/
        # public/no-auth/http config needs the typed confirmation (same contract as the per-stack proxy).
        phrase = f.get("confirm_phrase", "").strip()
        r = service.webserver_configure_apply(
            **fields, confirm=phrase in ("enable-remote", "enable-remote-danger"),
            confirm_public=(phrase == "enable-remote-danger"),
            accept_unverified=(f.get("accept_unverified_clock") == "1"),
            replace_digest=(f.get("replacement_digest") or None))
        if r.data.get("reason") == "certificate-replacement":
            # Nothing was saved. The same Apply, sent again with this replacement's digest, proceeds;
            # any change in between (a field, the LAN address, the installed certificate) shows the
            # replacement again instead (C04).
            echo = [(k, f[k]) for k in ("bind", "port", "scheme", "access_mode", "cidrs", "dns_sans",
                                        "ip_sans", "confirm_phrase", "accept_unverified_clock")
                    if k in f]
            return render_template("webserver_replace_confirm.html", version=__version__,
                                   summary=r.summary, details=r.details, echo=echo,
                                   digest=r.data["replacement"]["digest"])
        flash(r.summary, "ok" if r.ok else "err")
        for d in r.details:
            flash(d, "warn")
        return _ws_back()

    @app.post("/stacks/<stack_id>/webserver")
    def stack_web_configure(stack_id: str):
        """Web-UI proxy policy for ONE proxied page of the stack (`page`, `<page>@<rid>`, K4b). A bare page (none given,
        or one without `@<rid>`: the 0.13.x form) is the stack's instance on its lowest enabled radio (W290: kept, not
        removed); with no such radio the reason is shown (no hardware list, or no radio for the stack). Same
        typed-confirmation contract as /webserver/configure."""
        if not _csrf_ok():
            abort(400)
        f = request.form
        page_id = (f.get("page") or stack_id).strip()
        if "@" not in page_id and service.stack(stack_id) is not None:
            can = _instances.can_run_on(service, service.stack(stack_id))
            if not can:
                from lhpc.core import radio_start as _rs
                flash(_rs.no_list_notice(service._paths)
                      or f"{stack_id} can run on no enabled radio of the hardware list: add one on the Hardware page", "err")
                return redirect(url_for("stacks_overview") + "#stackrow-" + stack_id)
            page_id = f"{page_id}@{can[0]}"
        page = service.web_page(page_id)
        if service.stack(stack_id) is None or page is None or page.stack_id != stack_id:
            abort(404)                                   # unknown stack, or not one of its pages
        cidrs = [x.strip() for x in f.get("cidrs", "").split(",") if x.strip()]
        phrase = f.get("confirm_phrase", "").strip()
        # Single "Apply": save-and-apply in one action (autosave, then validate + reload).
        r = service.stack_web_configure_apply(
            page_id,
            mode=(f.get("mode") or None),
            port=(f.get("port") or None),
            scheme=(f.get("scheme") or None),
            access_mode=(f.get("access_mode") or None),
            cidrs=cidrs if "cidrs" in f else None,
            confirm=phrase in ("enable-remote", "enable-remote-danger"),
            confirm_public=(phrase == "enable-remote-danger"))
        # K4 (ERRATA-UI-v5-3 E3-1): the result belongs to the block that holds this form, the page's proxy Settings
        # (`stack-webserver-<page>--settings`); it shows there, on the Stacks page of that stack and radio (the panel is
        # server-rendered there, so the anchor resolves without JS); NOT ?cfg, which opens the stack's Settings.
        block = "stack-webserver-" + page_id + "--settings"
        flash(r.summary, ("ok" if r.ok else "err") + " @" + block)
        for d in r.details:
            flash(d, ("warn" if r.ok else "err") + " @" + block)
        return redirect(url_for("stacks_overview", open=stack_id, radio=page_id.partition("@")[2])
                        + "#stack-webserver-" + page_id)

    @app.post("/webserver/stacks")
    def stack_webs_configure():
        """ONE common policy for every eligible stack web UI ('Stacks Web Proxies'). Same typed-
        confirmation contract as the per-stack form; ports stay per-stack (preserved, or the
        suggested default where missing). Validation/persistence live in the service layer."""
        if not _csrf_ok():
            abort(400)
        f = request.form
        phrase = f.get("confirm_phrase", "").strip()
        # No field defaults here: the service refuses an unset one. A default would rewrite
        # EVERY page's policy from a select the operator never touched.
        r = service.stack_webs_configure_apply(
            mode=f.get("mode", ""),
            scheme=f.get("scheme", ""),
            access_mode=f.get("access_mode", ""),
            cidrs=[x.strip() for x in f.get("cidrs", "").split(",") if x.strip()],
            confirm=phrase in ("enable-remote", "enable-remote-danger"),
            confirm_public=(phrase == "enable-remote-danger"))
        # K4: ONE banner at the bulk form: the summary its title, each refused or noted item one line.
        flash(r.summary, ("ok" if r.ok else "err") + " @ws-stackwebs")
        for d in r.details:
            flash(d, ("warn" if r.ok else "err") + " @ws-stackwebs")
        return redirect(url_for("stacks_overview", wsg=1) + "#ws-stackwebs")

    @app.route("/webserver/reset", methods=["POST"])
    def webserver_reset():
        if not _csrf_ok():
            abort(400)
        if request.form.get("confirm_phrase", "") != "reset":
            flash("type 'reset' to confirm reset-to-default", "err")
            return _ws_back()
        r = service.webserver_reset_defaults()
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.post("/operator/callsign")
    def operator_callsign_save():
        """The GLOBAL operator callsign — its own deliberate card on the LHPC main card,
        never a stack-page side effect. Empty clears it (licensed stacks then require
        their local callsign)."""
        if not _csrf_ok():
            abort(400)
        r = service.set_operator_identity(callsign=request.form.get("callsign", "").strip())
        flash(r.summary + (" " + "; ".join(r.details) if r.details else ""),
              "ok" if r.ok else "err")
        return redirect(url_for("stacks_overview") + "#operator-callsign")

    @app.route("/webserver/verify", methods=["POST"])
    def webserver_verify():
        if not _csrf_ok():
            abort(400)
        r = service.webserver_verify()
        flash(r.summary, "ok" if r.ok else "warn")
        return _ws_back()

    def _fw_back():
        return redirect(url_for("stacks_overview") + "#firewall-row")

    @app.route("/firewall/configure", methods=["GET", "POST"])
    def firewall_configure():
        if request.method == "GET":
            return redirect(url_for("stacks_overview") + "#firewall-row")
        if not _csrf_ok():
            abort(400)
        f = request.form
        if f.get("recommended") == "yes":
            r = service.firewall_configure(recommended=True)
        else:
            allow = [k[len("allow_"):] for k in f if k.startswith("allow_")
                     and f.get(k) in ("on", "yes", "1")]
            ssh = [p.strip() for p in f.get("ssh_ports", "").replace(",", " ").split()
                   if p.strip()]
            # The firewall form ALWAYS submits ssh_ports; a blank field means "clear the
            # override back to automatic detection" (an empty list saves as ""), so pass the
            # list whenever the field is present — never coerce blank to None (unchanged).
            r = service.firewall_configure(
                mode=(f.get("mode") or None),
                allow_endpoints=allow,
                ssh_ports=ssh if "ssh_ports" in f else None,
                ap_enabled=(f.get("ap_enabled") in ("on", "yes", "1")),
                ap_interface=(f.get("ap_interface") or ""),
                ap_cidr=(f.get("ap_cidr") or ""))
        flash(r.summary, "ok" if r.ok else "err")
        for d in r.details:
            flash(d, "warn")
        return _fw_back()

    @app.route("/firewall/refresh", methods=["POST"])
    def firewall_refresh():
        # READ-ONLY: re-render the cached status from the root-written receipt. It cannot
        # inspect live nftables (unprivileged) — the immediate-check sudo command is shown
        # in the panel for that.
        if not _csrf_ok():
            abort(400)
        st = service.firewall_status()
        flash(st.get("line", "firewall status refreshed"),
              "ok" if st.get("live_ok") else "warn")
        return _fw_back()

    def _accept_unverified_clock() -> bool:
        """The WebGUI half of the one-shot clock override. Read per REQUEST and never stored,
        which is what makes it one-shot: there is no session flag and no setting to forget to
        clear. It is its own checkbox, deliberately separate from the typed destructive
        confirmations beside it -- "yes, recreate my CAs" and "yes, I accept certificates dated
        from a clock this box cannot verify" are different statements."""
        return request.form.get("accept_unverified_clock") == "1"

    @app.route("/webserver/init", methods=["POST"])
    def webserver_init():
        if not _csrf_ok():
            abort(400)
        # First-time init on a fresh PKI needs no phrase; RE-initializing (destructive) requires
        # the typed phrase 'recreate'.
        confirm = request.form.get("confirm_phrase", "").strip() == "recreate"
        # No clock override: init is not clock-gated (commissioning may not depend on a clock),
        # and an unverified clock yields the fixed provisional window, not dates from the clock.
        r = service.webserver_init(confirm=confirm)
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/tls-renew", methods=["POST"])
    def webserver_tls_renew():
        if not _csrf_ok():
            abort(400)
        r = service.webserver_tls_renew(_accept_unverified_clock())
        flash(r.summary, "ok" if r.ok else "err")
        return _ws_back()

    @app.route("/webserver/cert", methods=["POST"])
    def webserver_cert():
        if not _csrf_ok():
            abort(400)
        f = request.form
        op, label = f.get("op", ""), f.get("label", "")
        if op == "issue":
            pw = _secrets.token_urlsafe(18)     # one-time; shown once, never persisted/logged
            r = service.webserver_cert_issue(label, pw, _accept_unverified_clock())
            if r.ok:
                # The passphrase never enters the (readable) session cookie: a one-shot slot, the
                # redirect carries only its nonce, the next page shows it once.
                flash(f"{r.summary}. The one-time passphrase is shown below; record it now.", "ok")
                if peer_is_loopback():
                    flash("Bundle ready — download it now from this loopback session.", "info")
                else:
                    flash("Bundle created; a NEW bundle can be downloaded only from a "
                          "loopback session (remote download is refused).", "warn")
                return redirect(url_for("stacks_overview", pw=_passphrases.put(pw)) + "#webserver-row")
            flash(r.summary, "err")
        elif op == "revoke":
            # Typed confirmation: the operator must type the exact certificate label to revoke it.
            if f.get("confirm_phrase", "").strip() != label:
                flash(f"type the certificate label '{label}' to confirm revocation", "err")
            else:
                r = service.webserver_cert_revoke(label, _accept_unverified_clock())
                flash(r.summary, "ok" if r.ok else "err")
        else:
            flash("unknown certificate action", "err")
        return _ws_back()

    @app.route("/webserver/ca.crt")
    def webserver_ca_download():
        # The server CA CERTIFICATE is public material (no key), so no loopback gate — the
        # risk profile is trust-on-first-use, same as the scp path. This is also the import
        # path a phone can use. The .p12 below stays loopback-only: that one holds a key.
        blob = service.webserver_server_ca_bytes()
        if not blob:
            abort(404)
        from flask import Response
        return Response(blob, mimetype="application/x-x509-ca-cert",
                        headers={"Content-Disposition":
                                 'attachment; filename="lhpc-server-ca.crt"'})

    @app.route("/webserver/cert/<label>/download")
    def webserver_cert_download(label):
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
    A bad type falls back to the default; out-of-range values are clamped to 1..168 — never an exception
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


# The previous disk level per filesystem path, for ONE log line per change. Process-local and
# module-level: the web loop builds a NEW ControllerService for every pass (`_network_watch_loop`).
_DISK_LEVELS: dict[str, str] = {}


def _disk_level_log(svc) -> None:
    """Log one line per filesystem when its level CHANGES. At console start an ok level is silent
    and a low/critical one is logged once; after that, one line per transition, recovery included."""
    from lhpc.core.service_system import disk_figure
    for row in svc.disk_health():
        prev = _DISK_LEVELS.get(row["path"])
        _DISK_LEVELS[row["path"]] = row["level"]
        if prev == row["level"] or (prev is None and row["level"] == "ok"):
            continue
        print(f"disk {row['path']}: {prev or 'start'} -> {row['level']} ({disk_figure(row)})",
              flush=True)


def network_watch_pass(svc) -> float:
    """ONE pass of the console's network watchdog; returns the seconds to sleep before the next.
    The disk-level log and the maintenance pass (`lhpc.core.maintenance.run`) cannot starve one
    another or the AP tick; the AP probe and tick rely on the caller's loop-level catch:
      1. the disk-space level: one log line per change;
      2. the maintenance pass, in its order (`maintenance.TASKS`): an Apply the firewall gate
         deferred is completed; the client-CA CRL is rebuilt when its nextUpdate has passed — on
         EVERY box, every pass (LIVE-FOUND, box B, 2026-09-13: a box without the AP feature that
         kept running past the CRL's 30-day nextUpdate had nginx refuse every client certificate,
         a total remote-console lockout no tick healed); a PKI minted under an unverified clock is
         normalised once time arrives; every start log, the controller's own logs and the
         Meshtastic trace are tried against their trigger (a log another capper holds is left for
         the next pass; between two passes a log can grow past its trigger, there is no hard
         maximum). Each task's outcome is recorded (`state/maintenance.json`, shown by `lhpc
         doctor` and the dashboard) and a failed one is logged here; it is retried next pass;
      3. the AP-box tick (only where the Wi-Fi feature exists)."""
    try:                            # the disk-space level: one log line per change
        _disk_level_log(svc)
    except Exception:
        pass
    try:
        from lhpc.core import maintenance
        for task, (outcome, message) in maintenance.run(svc).items():
            if outcome is maintenance.Outcome.FAILED:
                _logging.getLogger(__name__).warning("maintenance %s failed: %s", task, message)
    except Exception:
        pass
    ap_box = svc.network_supported()
    # non-AP box: probe rarely, exit never — unless an Apply or a normalisation is still owed
    interval_s = (60.0 if (ap_box or svc.webserver_apply_pending()
                           or svc.pki_normalise_pending()) else 300.0)
    if ap_box:
        svc._network_watch_tick()
    return interval_s


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
    from lhpc.core.source_fs import arm_lease_break_handler
    arm_lease_break_handler()
    if not socket and host not in _LOOPBACK_HOSTS:
        print(
            f"ERR  refusing to bind '{host}': the operator console is loopback-only "
            f"(allowed: {', '.join(sorted(_LOOPBACK_HOSTS))}).\n"
            "     Non-loopback access would require explicit opt-in, auth and HTTPS\n"
            "     (or a documented trusted reverse proxy)."
        )
        return 1
    # Productive serving needs Waitress. Refuse FIRST, like the host check above: before the
    # runtime lock, the app, the post-update reconcile or any watchdog thread, so a refused
    # start writes nothing and leaves nothing running.
    try:
        from waitress import serve as _waitress_serve
    except ImportError:
        _waitress_serve = None
    if socket and _waitress_serve is None:
        print("ERR  waitress is required for productive (Unix-socket) serving but is "
              "not installed — refusing to start (productive serving must not fall back "
              "to the Flask development server). Install the declared 'waitress' "
              "dependency in the venv.")
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
        # Post-update firewall reconcile: this is the FRESH (new-code) process after a self-update
        # restart, so it can safely regenerate the firewall scripts + migrate the LHPC-owned nginx
        # unit with the CURRENT templates (the update itself only left a marker). Best-effort.
        try:
            for _fw_note in ControllerService().firewall_post_update_reconcile():
                print(f"INFO firewall: {_fw_note}")
        except Exception:
            pass
        if socket:
            # PRODUCTIVE mode (behind nginx). Two separate facts, deliberately not one flag:
            #   * the trusted-host policy must be enforced whenever we serve through nginx;
            #   * Secure cookies only make sense when the LISTENER is https — a browser DROPS a
            #     Secure cookie over plain http, which would silently break the CSRF session.
            #
            try:
                from lhpc.core.config import load_config as _load_config
                _scheme = _load_config(_resolve_paths()).webserver.scheme
            except Exception:
                _scheme = "https"
            app.config.update(SESSION_COOKIE_SECURE=(_scheme == "https"))
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

        # Network watchdog — the ONE worker for the Wi-Fi feature (started HERE only, never
        # from service construction: CLI runs and tests must not spawn threads). Each tick
        # reads the current preference/state fresh, so enabling Prefer later just takes
        # effect; the first pass primes the cached views for cold-process GETs. Same shape
        # as the self-check loop: ≤30 s sleep slices, every unit in its own try/except. The
        # pass itself is `network_watch_pass` (module level, so it is unit-tested) — it also
        # carries the CRL heal for every box, AP feature or not.
        def _network_watch_loop():
            interval_s = 60.0
            while True:
                try:
                    interval_s = network_watch_pass(ControllerService())
                except Exception:
                    pass
                deadline = time.monotonic() + interval_s
                while time.monotonic() < deadline:
                    time.sleep(min(30.0, max(0.1, deadline - time.monotonic())))
                    try:                        # a fresh deferred Apply: do not wait out 300 s
                        if interval_s > 60.0 and ControllerService().webserver_apply_pending():
                            break
                    except Exception:
                        pass

        threading.Thread(target=_network_watch_loop, name="lhpc-network-watch",
                         daemon=True).start()
        if socket:
            # PRODUCTIVE: Unix socket only, Waitress MANDATORY (checked first, at the top).
            # No TCP, no dev-server fallback.
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
            except Exception:
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

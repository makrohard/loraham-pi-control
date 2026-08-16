"""The lab web layer, registered ONTO a real lhpc Flask app by labweb (never inside
lhpc): a `/testlab` blueprint (panel + POST ops) and an after_request that injects the
permanent SIMULATED-HARDWARE banner and rewrites endpoint links to the Codespaces
forwarded URLs. lhpc's app.py has zero testlab code."""
from __future__ import annotations

import os
import re
import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from lhpc.core.services import ControllerService

from . import active, ops, scenarios

bp = Blueprint("testlab_bp", __name__, template_folder="templates")


def _svc() -> ControllerService:
    return ControllerService()


@bp.get("/testlab")
def page():
    svc = _svc()
    if not ops.is_active(svc):
        abort(404)
    return render_template("testlab.html", status=ops.status(svc),
                           scenario_names=sorted(scenarios.SCENARIOS),
                           current=scenarios.effective_state(svc._paths)["_name"],
                           presets=ops.INJECT_PRESETS)


def _csrf_ok() -> bool:
    # Same contract as lhpc's in-app check (session token vs form field); the token is
    # produced by lhpc's own `csrf_token` Jinja global on the rendered page.
    sent = request.form.get("_csrf", "")
    return bool(sent) and secrets.compare_digest(sent, session.get("_csrf", ""))


@bp.post("/testlab/<op>")
def action(op: str):
    svc = _svc()
    if not _csrf_ok():
        abort(400)
    if op not in ("reset", "scenario", "inject", "check") or not ops.is_active(svc):
        abort(404)
    if op == "reset":
        res = ops.reset(svc)
    elif op == "scenario":
        res = ops.scenario(svc, request.form.get("name", ""))
    elif op == "inject":
        res = ops.inject(svc, request.form.get("band", ""),
                         request.form.get("preset", ""))
    else:
        res = ops.check(svc)
    flash(f"{res.summary} {' '.join(res.details[:8])}", "ok" if res.ok else "warn")
    return redirect(url_for("testlab_bp.page"))


_LAB_PORTS = {8770, 8080, 5000, 18083, 8443, 8444, 8445, 8446, 4403, 9443, 8001, 7000}
# A lab port whose forwarded Codespace URL must point at a DIFFERENT port. meshtasticd's :9443
# is self-signed HTTPS (a Codespace's forwarding proxy 502s on it), so its dashboard link is
# sent to the plain-HTTP socat bridge on :9080 that start.sh runs.
_PORT_FORWARD = {9443: 9080}
_URL_RE = re.compile(rb"https?://([A-Za-z0-9._-]+)(?::(\d{2,5}))?")


def _codespace_base(port: int) -> bytes:
    name = os.environ["CODESPACE_NAME"]
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    return f"https://{name}-{port}.{domain}".encode()


_BANNER_CSS = (b".lab-banner{background:#7a2ca0;color:#fff;text-align:center;"
               b"padding:.3rem .6rem;font-weight:600;letter-spacing:.04em}"
               b".lab-banner a{color:#ffd}")


@bp.get("/_testlab/banner.css")
def banner_css():
    from flask import Response
    return Response(_BANNER_CSS, mimetype="text/css")


def _banner_html(base_url: str) -> bytes:
    # No inline styles (lhpc's CSP is style-src 'self') — a same-origin linked
    # stylesheet is allowed; the class lives in /_testlab/banner.css.
    return (b'<div class="lab-banner">TEST LAB \xe2\x80\x94 SIMULATED HARDWARE '
            b'\xe2\x80\x94 <a href="' + base_url.encode() +
            b'/testlab">Test Lab panel</a></div>')


def install(app):
    """Register the blueprint + the banner/link after_request on an lhpc app."""
    app.register_blueprint(bp)
    codespace = bool(os.environ.get("CODESPACE_NAME"))

    name = os.environ.get("CODESPACE_NAME", "")

    @app.after_request
    def _decorate(resp):
        if resp.direct_passthrough or "text/html" not in resp.headers.get(
                "Content-Type", ""):
            return resp
        body = resp.get_data()
        if codespace:
            def repl(m):
                host = m.group(1).decode("latin-1")
                port = int(m.group(2)) if m.group(2) else None
                # Only rewrite lhpc's OWN links — loopback, or this codespace's host —
                # never an arbitrary external URL that happens to share a lab port.
                own = host in ("127.0.0.1", "localhost") or host.startswith(name + "-")
                if not (own and port in _LAB_PORTS):
                    return m.group(0)
                # meshtastic's :9443 is self-signed HTTPS, which a Codespace's proxy 502s on —
                # forward the browser to the plain-HTTP socat bridge on :9080 instead (start.sh).
                return _codespace_base(_PORT_FORWARD.get(port, port))
            body = _URL_RE.sub(repl, body)
        # Inject the banner right after <body …> on lab-active pages.
        if _lab_active_safe():
            idx = body.lower().find(b"<body")
            if idx != -1:
                gt = body.find(b">", idx)
                if gt != -1:
                    base = request.host_url.rstrip("/")
                    body = body[:gt + 1] + _banner_html(base) + body[gt + 1:]
                    hi = body.lower().find(b"</head>")
                    if hi != -1:
                        link = (b'<link rel="stylesheet" '
                                b'href="/_testlab/banner.css">')
                        body = body[:hi] + link + body[hi:]
        resp.set_data(body)
        return resp
    return app


def _lab_active_safe() -> bool:
    try:
        from lhpc.core.paths import resolve_paths
        return active(resolve_paths())
    except Exception:
        return False

"""Production webserver orchestration: Nginx (TLS + mTLS + CIDR, the only TCP listener)
in front of Waitress over a protected Unix socket.

This module owns:
  * nginx config GENERATION from FIXED templates + already-validated typed data only
    (injection-resistant: every dynamic value is a `WebserverConfig` field that passed
    `validators`, or a fixed LHPC-owned runtime path);
  * the access-mode / allowed-CIDR POLICY (three modes; real $remote_addr; strip untrusted
    forwarded headers; loopback vs remote);
  * `nginx -t` VALIDATION before any activation (through the injected System.runner);
  * the exposure-policy gate (remote needs >=1 CIDR; 0.0.0.0/0 needs elevated confirmation).

It does NOT start/enable systemd units (operator-context only) and it does NOT decide, from
desired config, that anything is active — effective/exposed truth lives in state/webserver.json
(the verification evidence, written by services.py). Nginx reload/activation proof also lives
in the service layer.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path

from . import pki
from .config import WebserverConfig
from .paths import Paths

# Fixed runtime-owned locations (never user-controlled).
WAITRESS_SOCK = ("state", "run", "lhpc-web.sock")
NGINX_CONF = ("config", "nginx", "lhpc.conf")
NGINX_CONF_STAGED = ("config", "nginx", "lhpc.conf.staged")   # validated here before promotion
NGINX_PID = ("state", "run", "nginx.pid")
NGINX_TEMP = ("state", "run", "nginx")
_ERR_LOG = ("logs", "nginx-error.log")
_ACC_LOG = ("logs", "nginx-access.log")
# Static "console is restarting" page nginx serves on a 502/503/504 (e.g. during a self-update when
# the Waitress upstream is briefly gone) — no JS, no upstream, so it always renders.
_UPDATING_PAGE = ("config", "nginx", "_lhpc_updating.html")

_NGINX_VALIDATE_TIMEOUT_S = 15.0

# Declared SYSTEM (apt) dependencies of the production webserver. LHPC never installs system
# packages itself — each is surfaced with the exact operator command (matching deps.py's model).
NGINX_INSTALL_CMD = "sudo apt install -y nginx"
SYSTEM_DEPS = (
    {"name": "nginx", "install": NGINX_INSTALL_CMD,
     "purpose": "HTTPS/mTLS TLS front-end (the only network listener)"},
)


class WebserverError(Exception):
    """A webserver orchestration step failed (typed diagnostic, never a crash)."""


def nginx_installed(system) -> bool:
    """True iff the nginx system binary is present (via `nginx -v`)."""
    res = system.runner.run(["nginx", "-v"], _NGINX_VALIDATE_TIMEOUT_S)
    return not getattr(res, "not_found", False)


# --------------------------------------------------------------------------- exposure policy

def _is_loopback_bind(bind: str) -> bool:
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def plan_exposure(cfg: WebserverConfig) -> dict:
    """Pure policy evaluation of a DESIRED config (no side effects). Returns a dict describing
    whether remote exposure is permitted and what confirmation strength it needs. The caller
    (CLI/GUI) enforces the confirmation; this never mutates or activates anything."""
    problems: list = []
    bind_is_remote = not _is_loopback_bind(cfg.bind)

    if not cfg.remote_exposed:
        # Not exposing: a non-loopback bind is contradictory — refuse it to avoid an
        # accidental listener.
        if bind_is_remote:
            problems.append("remote_exposed is false but bind is non-loopback; refuse "
                            "(set bind=127.0.0.1 or enable remote exposure explicitly)")
        return {"remote": False, "problems": problems, "danger": "none",
                "public": False, "cidrs": list(cfg.allowed_cidrs)}

    # Exposing remotely:
    if not bind_is_remote:
        problems.append("remote_exposed is true but bind is loopback; set bind=0.0.0.0")
    if not cfg.allowed_cidrs:
        problems.append("remote exposure requires at least one allowed source CIDR")
    public = any(_is_public_default_route(c) for c in cfg.allowed_cidrs)
    danger = "elevated" if public else "normal"
    if cfg.access_mode == "no-auth":
        danger = "elevated"      # remote + no client auth is always a strong-confirmation case
    return {"remote": True, "problems": problems, "danger": danger,
            "public": public, "cidrs": list(cfg.allowed_cidrs),
            "no_auth": cfg.access_mode == "no-auth"}


def plan_stack_exposure(swc, console_port: int, other_ports=()) -> dict:
    """Pure policy evaluation of ONE stack's desired web-UI proxy. Mirrors `plan_exposure`:
    `problems` refuse the save outright; `danger` selects the confirmation strength."""
    from .config import STACKWEB_MIN_PORT
    problems: list = []
    if not swc.enabled:                          # port 0 = not proxied; nothing to police
        return {"remote": False, "problems": problems, "danger": "none",
                "public": False, "cidrs": [], "no_auth": False, "cleartext": False}

    if not (STACKWEB_MIN_PORT <= swc.port <= 65535):
        problems.append(f"port {swc.port} out of range — rootless nginx needs "
                        f"{STACKWEB_MIN_PORT}..65535")
    if swc.port == console_port:
        problems.append(f"port {swc.port} is already the console's port")
    if swc.port in tuple(other_ports):
        problems.append(f"port {swc.port} is already used by another stack's web UI")
    # Hard technical constraint, not a preference: a client certificate is presented during the TLS
    # handshake, so a plain-http listener has nothing to verify.
    if swc.scheme == "http" and swc.access_mode != "no-auth":
        problems.append("scheme=http cannot do client-certificate authentication; "
                        "access_mode must be 'no-auth'")
    if not swc.remote:
        return {"remote": False, "problems": problems, "danger": "none",
                "public": False, "cidrs": list(swc.allowed_cidrs), "no_auth": False,
                "cleartext": swc.scheme == "http"}

    if not swc.allowed_cidrs:
        problems.append("remote exposure requires at least one allowed source CIDR")
    public = any(_is_public_default_route(c) for c in swc.allowed_cidrs)
    no_auth = swc.access_mode == "no-auth"
    cleartext = swc.scheme == "http"
    # Remote + (public | no client auth | unencrypted) each demand the strong phrase.
    danger = "elevated" if (public or no_auth or cleartext) else "normal"
    return {"remote": True, "problems": problems, "danger": danger, "public": public,
            "cidrs": list(swc.allowed_cidrs), "no_auth": no_auth, "cleartext": cleartext}


def _is_public_default_route(cidr: str) -> bool:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return net.prefixlen == 0        # 0.0.0.0/0 (or ::/0) — the whole internet


# --------------------------------------------------------------------------- nginx config generation

def _abs(paths: Paths, parts) -> str:
    return str(paths.under(*parts))


@dataclass(frozen=True)
class StackWebProxy:
    """One stack's web-UI reverse proxy: the operator's LISTENER policy (`swc`) plus the UPSTREAM,
    which is read from the manifest endpoint and is NEVER operator-settable.

    Keeping `swc.scheme` (listener) and `upstream_scheme` apart is deliberate: MeshCom's upstream is
    plain http on loopback, and that must not talk anyone into dropping TLS on the public listener."""

    swc: "object"                    # config.StackWebConfig
    upstream_address: str            # "127.0.0.1:18083"
    upstream_scheme: str             # "http" | "https"


def nginx_token(stack_id: str) -> str:
    """A stack id reduced to `[a-z0-9_]` for use in nginx VARIABLE / `map` / `upstream` names.

    Stack ids may contain `-`, `.`, `@` (`validators.path_component`), none of which are legal in an
    nginx variable name. Never interpolate a raw id into one. Folding is lossy by design, so callers
    must collision-check across the set (`assert_distinct_tokens`)."""
    out = "".join(ch if ch.isalnum() and ch.isascii() else "_" for ch in stack_id.lower())
    return out or "_"


def assert_distinct_tokens(stack_ids) -> dict:
    """{stack_id: token}, or raise when two ids fold to the same nginx token.

    `mesh-com` and `mesh_com` both fold to `mesh_com`; silently merging their proxy blocks would
    hand one stack's UI the other's access policy. Refuse to render instead."""
    seen: dict = {}
    out: dict = {}
    for sid in stack_ids:
        tok = nginx_token(sid)
        if tok in seen:
            raise ValueError(f"stack ids {seen[tok]!r} and {sid!r} both map to the nginx "
                             f"identifier {tok!r} — rename one before proxying both")
        seen[tok] = sid
        out[sid] = tok
    return out


def tls_required(cfg: WebserverConfig, stack_webs=()) -> bool:
    """Does ANY public listener in the DESIRED config terminate TLS?

    The whole config's PKI needs follow from this, not from the console alone: an http console with
    an https stack proxy still needs a server certificate, and an all-http config needs none. Asking
    for TLS material nobody uses is how `scheme=http` ended up only half-working."""
    if cfg.scheme == "https":
        return True
    return any(p.swc.enabled and p.swc.scheme == "https" for p in stack_webs)


def client_auth_required(cfg: WebserverConfig, stack_webs=()) -> bool:
    """Does any TLS listener actually verify client certificates? (=> client CA + CRL must exist.)

    Gating this on the CONSOLE's access mode alone was wrong: a `no-auth` console with a cert-auth
    stack proxy still makes nginx load `ssl_client_certificate`/`ssl_crl`."""
    if cfg.scheme == "https" and cfg.access_mode != "no-auth":
        return True
    return any(p.swc.enabled and p.swc.scheme == "https" and p.swc.access_mode != "no-auth"
               for p in stack_webs)


def listener_scope(system, port: int) -> str:
    """"loopback" | "exposed" | "absent" for a local TCP `port`, from /proc/net/tcp.

    AUTHORITATIVE, and read on THIS host — a client-side probe cannot distinguish a loopback bind
    from a firewalled one. Used to tell the operator when an upstream (e.g. meshtasticd :9443, which
    has no bind knob) is reachable directly on the LAN, bypassing this proxy's authentication."""
    try:
        listeners = system.procfs.tcp_listeners()
    except Exception:                                    # noqa: BLE001 — evidence, never fatal
        return "absent"
    found = False
    for ln in listeners:
        if ln.port != port:
            continue
        found = True
        ip = (ln.ip or "").strip()
        # Anything not loopback answers to some other interface: 0.0.0.0, ::, or a concrete LAN IP.
        if not (ip.startswith("127.") or ip in ("::1", "0:0:0:0:0:0:0:1")):
            return "exposed"
    return "loopback" if found else "absent"


def _ssl_suffix(scheme: str) -> str:
    return " ssl" if scheme == "https" else ""


def _listen(cfg: WebserverConfig) -> str:
    # Bind is a validated host literal; port a validated int. FAIL-SAFE: unless remote
    # exposure is explicitly enabled we ALWAYS listen on loopback, regardless of a stale
    # `bind` value — so a leftover 0.0.0.0 can never open a remote listener by itself.
    bind = cfg.bind if cfg.remote_exposed else "127.0.0.1"
    return f"listen {bind}:{cfg.port}{_ssl_suffix(cfg.scheme)};"


def _need_auth_map(cfg: WebserverConfig) -> str:
    """A `map` producing $lhpc_need_auth (1 => reject) from peer + client-verify result.
    Revocation enforces here: a revoked cert yields $ssl_client_verify=FAILED => need_auth=1."""
    if cfg.access_mode == "no-auth":
        return ('map "$ssl_client_verify" $lhpc_need_auth {\n'
                '        default 0;\n'
                '    }')
    if cfg.access_mode == "auth-everywhere":
        return ('map "$ssl_client_verify" $lhpc_need_auth {\n'
                '        default 1;\n'
                '        "SUCCESS" 0;\n'
                '    }')
    # local-open-remote-auth: loopback always ok; remote requires SUCCESS.
    return ('map "$lhpc_peer:$ssl_client_verify" $lhpc_need_auth {\n'
            '        default 1;\n'
            '        "~^loopback:" 0;\n'
            '        "~^remote:SUCCESS$" 0;\n'
            '    }')


def _verify_client(cfg: WebserverConfig) -> str:
    if cfg.access_mode == "no-auth":
        return "ssl_verify_client off;"
    if cfg.access_mode == "auth-everywhere":
        # Prefer mandatory handshake-level verification where safe.
        return "ssl_verify_client on;"
    return "ssl_verify_client optional;"


def _allow_deny(cfg: WebserverConfig) -> str:
    """Source-address allow list on the REAL peer address. Loopback is always allowed; when
    exposed remotely, only the configured CIDRs; everything else denied. IPv4 CIDRs only
    (IPv6 remote rejected upstream)."""
    lines = ["allow 127.0.0.1;", "        allow ::1;"]
    if cfg.remote_exposed:
        for c in cfg.allowed_cidrs:      # already normalized IPv4 CIDRs
            lines.append(f"        allow {c};")
    lines.append("        deny all;")
    return "\n        ".join(lines)


def _stack_need_auth_map(swc, tok: str) -> str:
    """Per-stack `$lhpc_need_auth_<tok>` (1 => reject). Same policy vocabulary as the console."""
    if swc.access_mode == "no-auth":
        return (f'map "$ssl_client_verify" $lhpc_need_auth_{tok} {{\n'
                f'        default 0;\n'
                f'    }}')
    if swc.access_mode == "auth-everywhere":
        return (f'map "$ssl_client_verify" $lhpc_need_auth_{tok} {{\n'
                f'        default 1;\n'
                f'        "SUCCESS" 0;\n'
                f'    }}')
    return (f'map "$lhpc_peer:$ssl_client_verify" $lhpc_need_auth_{tok} {{\n'
            f'        default 1;\n'
            f'        "~^loopback:" 0;\n'
            f'        "~^remote:SUCCESS$" 0;\n'
            f'    }}')


def _stack_allow_deny(swc) -> str:
    lines = ["allow 127.0.0.1;", "        allow ::1;"]
    if swc.remote:
        for c in swc.allowed_cidrs:
            lines.append(f"        allow {c};")
    lines.append("        deny all;")
    return "\n        ".join(lines)


def _stack_blocks(paths: Paths, cfg: WebserverConfig, stack_webs) -> tuple:
    """(http_prelude, server_blocks) for the per-stack web-UI proxies. ('', '') when none are
    enabled — which is what keeps a default deployment's config byte-identical."""
    active = [s for s in stack_webs if s.swc.enabled]
    if not active:
        return "", ""
    tokens = assert_distinct_tokens([s.swc.stack_id for s in active])
    server_crt = _abs(paths, ("config", "tls", "server", "server.crt"))
    server_key = _abs(paths, ("config", "tls", "server", "server.key"))
    client_ca = _abs(paths, ("config", "tls", "client-ca", "ca.crt"))
    crl = _abs(paths, ("config", "tls", "client-ca", "crl.pem"))

    # Emitted ONLY when at least one proxy block exists; these UIs use websockets.
    prelude = ['    map $http_upgrade $lhpc_conn_upgrade {\n'
               '        default upgrade;\n'
               '        "" close;\n'
               '    }']
    blocks = []
    for s in active:
        swc, tok = s.swc, tokens[s.swc.stack_id]
        prelude.append(f"    upstream lhpc_ui_{tok} {{ server {s.upstream_address}; }}")
        prelude.append(f"    {_stack_need_auth_map(swc, tok)}")
        # LISTENER scheme is the operator's choice; UPSTREAM scheme comes from the manifest
        # endpoint. They are independent: a cleartext upstream must never disable outside TLS.
        #
        # The bind follows THIS stack's own mode, not the console's `cfg.bind`. The two are separately
        # confirmed policies: resetting the console to loopback must not silently relocate a mesh UI
        # the operator deliberately exposed, and vice versa. The CIDR gate below is the access control.
        listen_bind = "0.0.0.0" if swc.remote else "127.0.0.1"
        tls = ""
        if swc.scheme == "https":
            tls = (f"        ssl_certificate {server_crt};\n"
                   f"        ssl_certificate_key {server_key};\n"
                   f"        ssl_protocols TLSv1.2 TLSv1.3;\n")
            if swc.access_mode != "no-auth":
                # The SAME client CA as the console: one client certificate authenticates everywhere.
                tls += (f"        ssl_client_certificate {client_ca};\n"
                        f"        ssl_crl {crl};\n")
            tls += f"        {_verify_client(swc)}\n"
        # A self-signed loopback upstream (meshtasticd) cannot be verified and needs no verification.
        proxy_ssl = "            proxy_ssl_verify off;\n" if s.upstream_scheme == "https" else ""
        blocks.append(f"""
    # {swc.stack_id} web UI -> {s.upstream_scheme}://{s.upstream_address}
    server {{
        listen {listen_bind}:{swc.port}{_ssl_suffix(swc.scheme)};
        server_name _;
{tls}
        {_stack_allow_deny(swc)}

        location / {{
            if ($lhpc_need_auth_{tok}) {{ return 403; }}

            proxy_pass {s.upstream_scheme}://lhpc_ui_{tok};
{proxy_ssl}            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $lhpc_conn_upgrade;

            proxy_set_header X-Forwarded-For "";
            proxy_set_header X-Forwarded-Proto "";
            proxy_set_header X-Forwarded-Host "";
            proxy_set_header Forwarded "";
        }}
    }}
""")
    return "\n".join(prelude) + "\n", "".join(blocks)


def render_nginx_config(paths: Paths, cfg: WebserverConfig, stack_webs=()) -> str:
    """Generate the complete nginx config. ONLY validated typed data + fixed LHPC paths are
    interpolated; the structure is a fixed template. A rootless (user) nginx: writable pid,
    logs and temp dirs live under the runtime root; TLS terminates here; the backend is the
    Waitress Unix socket. Untrusted forwarded headers are stripped and replaced with
    nginx-set evidence headers.

    `stack_webs` is a sequence of `StackWebProxy` (per-stack web-UI reverse proxies). It defaults to
    empty, and an empty set renders BYTE-IDENTICALLY to the pre-feature config — the websocket `map`,
    the upstreams and the extra server blocks all appear only when a stack is actually proxied."""
    sock = _abs(paths, WAITRESS_SOCK)
    server_crt = _abs(paths, ("config", "tls", "server", "server.crt"))
    server_key = _abs(paths, ("config", "tls", "server", "server.key"))
    client_ca = _abs(paths, ("config", "tls", "client-ca", "ca.crt"))
    crl = _abs(paths, ("config", "tls", "client-ca", "crl.pem"))
    # No TLS => no client-cert material and no HSTS (which is meaningless over http and would
    # poison the host for a future https listener).
    if cfg.scheme == "http":
        tls_lines = ""
        mtls = ""
        verify = ""
        hsts = ""
    else:
        tls_lines = (f"        ssl_certificate {server_crt};\n"
                     f"        ssl_certificate_key {server_key};\n"
                     f"        ssl_protocols TLSv1.2 TLSv1.3;\n"
                     f"        ssl_prefer_server_ciphers on;\n"
                     f"        ssl_session_timeout 10m;\n")
        mtls = "" if cfg.access_mode == "no-auth" else (
            f"    ssl_client_certificate {client_ca};\n"
            f"    ssl_crl {crl};\n")
        verify = f"        {_verify_client(cfg)}\n"
        hsts = '        add_header Strict-Transport-Security "max-age=31536000" always;\n'
    ui_prelude, ui_blocks = _stack_blocks(paths, cfg, stack_webs)
    return f"""# Generated by LoRaHAM Pi Control — DO NOT EDIT (regenerated on Apply).
# Rootless nginx: TLS boundary on {cfg.bind}:{cfg.port}; backend = Waitress unix socket.
pid {_abs(paths, NGINX_PID)};
error_log {_abs(paths, _ERR_LOG)} warn;
worker_processes 1;
events {{ worker_connections 256; }}
http {{
    access_log {_abs(paths, _ACC_LOG)};
    client_body_temp_path {_abs(paths, NGINX_TEMP)}/body;
    proxy_temp_path {_abs(paths, NGINX_TEMP)}/proxy;
    fastcgi_temp_path {_abs(paths, NGINX_TEMP)}/fastcgi;
    uwsgi_temp_path {_abs(paths, NGINX_TEMP)}/uwsgi;
    scgi_temp_path {_abs(paths, NGINX_TEMP)}/scgi;
    server_tokens off;

    # Real peer classification — NEVER from a client-supplied header.
    geo $lhpc_peer {{
        default remote;
        127.0.0.0/8 loopback;
        ::1/128 loopback;
    }}
    {_need_auth_map(cfg)}

    upstream lhpc_waitress {{ server unix:{sock}; }}
{ui_prelude}
    server {{
        {_listen(cfg)}
        server_name _;
{tls_lines}{mtls}{verify}{hsts}
        # Source-address gate (real peer; loopback always, remote only from allowed CIDRs).
        {_allow_deny(cfg)}

        # When the Waitress upstream is gone (e.g. mid self-update restart), serve a branded static
        # page instead of nginx's raw "502 Bad Gateway". Served from disk, no upstream, no JS.
        error_page 502 503 504 /_lhpc_updating.html;
        location = /_lhpc_updating.html {{
            internal;
            alias {_abs(paths, _UPDATING_PAGE)};
        }}

        location / {{
            # Access-mode enforcement (revoked client cert => FAILED => rejected here).
            if ($lhpc_need_auth) {{ return 403; }}

            proxy_pass http://lhpc_waitress;
            proxy_http_version 1.1;
            proxy_set_header Host $host;

            # STRIP any client-supplied trust/forwarded headers, then set our own evidence
            # (the app trusts ONLY these nginx-set values).
            proxy_set_header X-Forwarded-For "";
            proxy_set_header X-Forwarded-Proto "";
            proxy_set_header X-Forwarded-Host "";
            proxy_set_header Forwarded "";
            proxy_set_header X-LHPC-Peer $lhpc_peer;
            proxy_set_header X-LHPC-Client-Verify $ssl_client_verify;
        }}
    }}
{ui_blocks}}}
"""


# --------------------------------------------------------------------------- validation (nginx -t)

def validate_config(system, paths: Paths, conf_path: str) -> tuple:
    """Run `nginx -t -c <conf_path>` through the injected runner. Returns (ok, message).
    Validation ALWAYS precedes activation; a config that fails `nginx -t` is never loaded."""
    res = system.runner.run(["nginx", "-t", "-c", conf_path], _NGINX_VALIDATE_TIMEOUT_S)
    if getattr(res, "not_found", False):
        return False, f"nginx is not installed — required system dependency: {NGINX_INSTALL_CMD}"
    ok = res.returncode == 0
    # nginx writes the test result to stderr as TWO lines: the '[emerg] …' root cause followed by a
    # generic 'nginx: configuration file … test failed' tail. On failure surface the CAUSE
    # (emerg/error line), not the useless tail. nginx -t output carries no secrets, so it's safe.
    lines = [ln.strip() for ln in (res.stderr or res.stdout or "").splitlines() if ln.strip()]
    if ok:
        return True, (lines[-1] if lines else "configuration OK")
    cause = next((ln for ln in lines if "[emerg]" in ln or "[error]" in ln),
                 lines[-1] if lines else "nginx -t failed")
    return False, cause[:300]


def stage_and_validate(system, paths: Paths, cfg: WebserverConfig, stack_webs=()) -> tuple:
    """Render the nginx config to a STAGED path and `nginx -t` it. NEVER touches the live
    config. Returns (ok, message, staged_path). On success the caller may `promote_config()`;
    on failure the previous live config is left byte-for-byte intact.

    `stack_webs` defaults to empty so every existing caller keeps rendering the console-only config."""
    from . import runtime_fs
    # Rootless nginx creates each `*_temp_path` with a SINGLE-level mkdir and writes its pid/logs
    # under the runtime root — so their parents must already exist or `nginx -t` dies with
    # `[emerg] mkdir() ".../state/run/nginx/body" failed`. Nothing else creates state/run/nginx, so
    # ensure it (and the pid/log parents) here, before every validation. Persistent on disk → the
    # systemd unit needs no runtime-dir setup of its own.
    runtime_fs.mkdir(paths, "state", "run", "nginx")
    runtime_fs.mkdir(paths, "logs")
    # The branded 502/503/504 fallback page nginx serves from disk (no upstream, no JS).
    runtime_fs.atomic_write(paths, paths.under(*_UPDATING_PAGE), _UPDATING_PAGE_HTML, mode=0o644)
    staged = paths.under(*NGINX_CONF_STAGED)
    runtime_fs.atomic_write(paths, staged, render_nginx_config(paths, cfg, stack_webs), mode=0o644)
    ok, msg = validate_config(system, paths, str(staged))
    return ok, msg, staged


# Standalone (no Jinja, no script) page nginx returns when the console upstream is briefly gone —
# e.g. while a self-update stops+restarts lhpc-web. The user clicks the link when it is back.
_UPDATING_PAGE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Console restarting — LoRaHAM Pi Control</title>
<style>
  body { font-family: system-ui, sans-serif; background: #f4f6f9; color: #223; margin: 0;
         display: flex; min-height: 100vh; align-items: center; justify-content: center; }
  .card { background: #fff; border: 1px solid #dbe1e8; border-radius: 12px; padding: 2rem 2.4rem;
          box-shadow: 0 2px 10px rgba(0,0,0,.06); max-width: 30rem; text-align: center; }
  h1 { font-size: 1.3rem; margin: 0 0 .6rem; }
  p { color: #556; line-height: 1.5; }
  a.btn { display: inline-block; margin-top: 1rem; padding: .7rem 1.4rem; background: #1a7f37;
          color: #fff; text-decoration: none; border-radius: 8px; font-weight: 600; }
</style></head>
<body><div class="card">
  <h1>The console is restarting&hellip;</h1>
  <p>LoRaHAM Pi Control is updating or restarting and will be back shortly (usually well under a
     minute). This page does not refresh on its own &mdash; click below when you are ready, and
     reload once more if it is still coming up.</p>
  <a class="btn" href="/">Return to the console &rarr;</a>
</div></body></html>
"""


def promote_config(paths: Paths) -> Path:
    """Atomically promote the validated staged config to the live path (same-dir rename over
    the previous file). Only ever called AFTER a successful `nginx -t` on the staged file."""
    from . import runtime_fs
    runtime_fs.rename_leaf(paths, paths.under(*NGINX_CONF_STAGED), paths.under(*NGINX_CONF),
                           replace=True)
    return paths.under(*NGINX_CONF)


# --------------------------------------------------------------------------- reload (correction #1)

def _read_pid(paths: Paths):
    from . import runtime_fs
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*NGINX_PID)).strip()
    except (FileNotFoundError, OSError):
        return None
    return int(raw) if raw.isdigit() else None


def nginx_master_active(paths: Paths) -> bool:
    """True iff an LHPC-owned nginx master appears to be running (pidfile present + a live
    process for that PID). NOTE: this is a pragmatic pidfile+liveness check; a full PID-reuse
    identity check (procident) is a hardening follow-up."""
    import os
    pid = _read_pid(paths)
    if pid is None or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def reload(system, paths: Paths) -> tuple:
    """Reload an ALREADY-RUNNING, LHPC-owned nginx master via `nginx -s reload` (the binary
    signals its own master through the pidfile). It NEVER calls systemctl and NEVER starts the
    unit — if no active master is present it returns ('repair_required', msg) and the caller
    must direct the operator to install/repair (operator context only). Returns one of:
    ('reloaded'|'failed'|'repair_required', message)."""
    if not nginx_master_active(paths):
        return "repair_required", ("nginx master not active — install/repair the webserver "
                                   "service in operator context (the web process never starts it)")
    conf = str(paths.under(*NGINX_CONF))
    res = system.runner.run(["nginx", "-s", "reload", "-c", conf], _NGINX_VALIDATE_TIMEOUT_S)
    if getattr(res, "not_found", False):
        return "failed", "nginx binary not found"
    if res.returncode == 0:
        return "reloaded", "nginx reloaded"
    return "failed", ((res.stderr or res.stdout or "reload failed").strip().splitlines() or ["reload failed"])[-1]


# --------------------------------------------------------------------------- effective evidence (M9)

EVIDENCE = ("state", "webserver.json")
EVIDENCE_SCHEMA = 1


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def read_evidence(paths: Paths) -> dict:
    """Fail-safe read of the last-proven EFFECTIVE evidence — the ONLY source Monitor/GET
    renders. Absent/unsafe/malformed/wrong-schema -> {} (shown as 'unknown / not proven').
    Never raises, never probes."""
    from . import runtime_fs
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*EVIDENCE))
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("schema") != EVIDENCE_SCHEMA:
        return {}
    return data


def write_evidence(paths: Paths, evidence: dict) -> None:
    """Persist effective evidence (0600). Written ONLY by an explicit verify/mutation — never
    during GET rendering."""
    from . import runtime_fs
    payload = {"schema": EVIDENCE_SCHEMA, **evidence}
    runtime_fs.write_marker(paths, paths.under(*EVIDENCE),
                            json.dumps(payload, indent=2, sort_keys=True), mode=0o600)


def local_ip() -> str:
    """Best-effort primary LAN IPv4 of this host, for DISPLAY only (so the operator sees the
    address to reach the console and which IP to add to SANs/CIDRs). Reads the kernel's chosen
    source address via a connected UDP socket to a TEST-NET address — NO packet is sent and no
    service is contacted (not a network probe). Fail-soft: '' when it can't be determined."""
    import socket
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))          # RFC 5737 TEST-NET-1 — never actually contacted
        ip = s.getsockname()[0]
    except OSError:
        return ""
    finally:
        if s is not None:
            s.close()
    return "" if ip.startswith("127.") else ip


def console_urls(cfg: WebserverConfig) -> list[str]:
    """The URL(s) the console is reachable at, most useful first — for DISPLAY only.

    nginx owns the only TCP listener, so these come from the DESIRED config, not from a probe. When
    the console is not remotely exposed the LAN address is omitted entirely: it would not answer,
    and offering it would be a lie. `local_ip()` fail-softs to '' (loopback-only host, or unknown).
    The scheme follows the configured LISTENER — never a hardcoded https.
    """
    loopback = f"{cfg.scheme}://127.0.0.1:{cfg.port}/"
    if not cfg.remote_exposed:
        return [loopback]
    ip = local_ip()
    return ([f"{cfg.scheme}://{ip}:{cfg.port}/", loopback] if ip else [loopback])


def stack_ui_urls(swc) -> list:
    """URLs a stack's proxied web UI answers on, most useful first — DISPLAY only.

    A `local` proxy really is loopback-only, so we never hand a remote browser a LAN URL it cannot
    reach, nor a loopback URL pretending to be reachable. `local_ip()` fail-softs to ''."""
    if not swc.enabled:
        return []
    loopback = f"{swc.scheme}://127.0.0.1:{swc.port}/"
    if not swc.remote:
        return [loopback]
    ip = local_ip()
    return ([f"{swc.scheme}://{ip}:{swc.port}/", loopback] if ip else [loopback])


def monitor_view(paths: Paths, cfg: WebserverConfig, live_listener_scope: str | None = None) -> dict:
    """READ-ONLY status for Monitor/GET. Merges DESIRED config (intent) with EFFECTIVE evidence +
    read-only PKI state. It never infers active/exposed from desired config, and makes NO
    network/subprocess probe — but the console's listener SCOPE is local /proc evidence, allowed here.

    `live_listener_scope` ("exposed" | "loopback" | "absent") is the freshly-read console-port scope;
    when the caller supplies it (the GUI path does) it is authoritative, so the panel is correct
    without a re-verify. When None, fall back to the scope persisted by the last `verify()`. It is a
    SCOPE STRING, not a bool, on purpose: a bool collapses `loopback` and `absent`, which would make
    "listener is still loopback-only" a lie when nginx is not running at all."""
    ev = read_evidence(paths)
    effective = ev.get("effective", {}) if isinstance(ev.get("effective"), dict) else {}
    scope = live_listener_scope if live_listener_scope is not None else effective.get("listener_scope")
    proven = (scope == "exposed") if scope is not None else bool(effective.get("remote_listener"))
    if live_listener_scope is not None:
        effective = {**effective, "remote_listener": proven, "listener_scope": scope}
    warnings = []
    if cfg.remote_exposed and cfg.access_mode == "no-auth":
        warnings.append({"level": "danger",
                         "text": "Remote access is enabled without client authentication."})
    if cfg.remote_exposed and not cfg.allowed_cidrs:
        warnings.append({"level": "warn",
                         "text": "Remote exposure desired but no allowed source CIDR is set."})
    if cfg.remote_exposed:
        if scope == "exposed":
            warnings.append({"level": "ok",
                             "text": f"Remote listener active on {cfg.bind}:{cfg.port}."})
        elif scope == "loopback":
            warnings.append({"level": "warn",
                             "text": "Remote exposure is enabled but the listener is still "
                                     "loopback-only — run Apply to rebind nginx to 0.0.0.0."})
        else:            # "absent" — and `scope is None` (nothing proven) honestly lands here too
            warnings.append({"level": "warn",
                             "text": "Remote exposure is enabled but no listener is active — "
                                     "start-service, or apply/repair the webserver."})
    elif scope == "exposed":
        # Desired says disabled, but the LIVE listener is still off-loopback — the common
        # saved-but-not-applied state (`webserver_disable_remote` writes intent, does not reload).
        # Reporting "disabled — loopback only" here would be a lie about what is actually reachable.
        warnings.append({"level": "warn",
                         "text": "Remote exposure is disabled in desired config, but the live "
                                 f"listener on port {cfg.port} is still exposed (off-loopback) — "
                                 "run Apply (or Reset to defaults) to cease it."})
    else:
        warnings.append({"level": "info",
                         "text": "Remote exposure is disabled — the console listens on loopback only."})
    # Declared system (apt) dependencies with their LAST-PROVEN present/absent status (from cached
    # verify evidence — nginx presence is not re-checked here; only the listener scope, if the caller
    # passed one, is live /proc evidence).
    checks = ev.get("checks", {})
    _st = {"ok": "present", "failed": "absent"}
    system_deps = [
        {"name": d["name"], "install": d["install"], "purpose": d["purpose"],
         "status": _st.get(checks.get("nginx_present"), "unknown")}
        for d in SYSTEM_DEPS
    ]
    if any(d["status"] == "absent" for d in system_deps):
        warnings.append({"level": "warn",
                         "text": f"nginx (system dependency) is not installed — {NGINX_INSTALL_CMD}"})
    return {
        "local_ip": local_ip(),
        "desired": {
            "bind": cfg.bind, "port": cfg.port, "access_mode": cfg.access_mode,
            "remote_exposed": cfg.remote_exposed, "allowed_cidrs": list(cfg.allowed_cidrs),
            "dns_sans": list(cfg.dns_sans), "ip_sans": list(cfg.ip_sans),
            "scheme": cfg.scheme,
        },
        "effective": effective,                 # last proven; empty => unknown
        "checks": checks,
        "system_deps": system_deps,
        "pki": pki.pki_status(paths),
        "last_verified": ev.get("checked_at"),
        "warnings": warnings,
    }


def verify(system, paths: Paths, cfg: WebserverConfig, stack_webs=()) -> dict:
    """Assemble the effective-state proof checklist and PERSIST it as evidence. Static + config
    checks are proven here (deps present, CA/cert present, nginx -t valid). The console's LISTENER
    SCOPE is proven from /proc/net/tcp (local evidence, not a network/subprocess probe); the
    HTTPS-cert / mTLS-behaviour / revocation-enforcement checks still need a real client handshake and
    stay 'unproven' unless run under opt-in integration. This never reports remote exposure as active
    unless the listener is actually bound off-loopback.

    `stack_webs` MUST be the same proxy set `apply` would promote. Validating a console-only config
    and then reporting "verified" would be a lie about a config that is never loaded."""
    from . import runtime_fs
    checks: dict = {}
    plan = plan_exposure(cfg)
    problems = list(plan["problems"])
    # Each stack proxy is part of the desired config; its problems are the config's problems.
    other_ports: list = []
    for p in stack_webs:
        if not p.swc.enabled:
            continue
        sp = plan_stack_exposure(p.swc, cfg.port, tuple(other_ports))
        problems += [f"{p.swc.stack_id}: {x}" for x in sp["problems"]]
        other_ports.append(p.swc.port)
    checks["config"] = "ok" if not problems else "failed"
    if problems:
        checks["config_problems"] = problems

    res = system.runner.run(["nginx", "-v"], _NGINX_VALIDATE_TIMEOUT_S)
    checks["nginx_present"] = "failed" if getattr(res, "not_found", False) else "ok"

    # PKI is required only by what the DESIRED config actually loads. An all-http config needs none.
    st = pki.pki_status(paths)
    needs_tls = tls_required(cfg, stack_webs)
    checks["tls_required"] = "yes" if needs_tls else "no"
    if needs_tls:
        checks["server_ca"] = "ok" if st["server_ca"].get("present") else "failed"
        checks["server_cert"] = "ok" if st["server_cert"].get("present") else "failed"
    if client_auth_required(cfg, stack_webs):
        checks["client_ca"] = "ok" if st["client_ca"].get("present") else "failed"
        checks["crl"] = "ok" if st["crl_present"] else "failed"

    # Render + validate a STAGED config (verify NEVER mutates the live config) — the SAME config
    # `apply` would promote, stack proxies included.
    ok, msg, _staged = stage_and_validate(system, paths, cfg, stack_webs)
    checks["nginx_config_valid"] = "ok" if ok else "failed"
    checks["nginx_config_message"] = msg

    # Per-stack proxy evidence, including the upstream's REAL bind scope. An upstream listening off
    # loopback is reachable around this proxy, so its access mode protects nothing — a standing
    # warning, never a config "failure" (nothing here can fix it).
    proxies: list = []
    for p in stack_webs:
        if not p.swc.enabled:
            continue
        try:
            up_port = int(str(p.upstream_address).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            up_port = 0
        scope = listener_scope(system, up_port) if up_port else "absent"
        proxies.append({
            "stack_id": p.swc.stack_id, "port": p.swc.port, "mode": p.swc.mode,
            "scheme": p.swc.scheme, "access_mode": p.swc.access_mode,
            "allowed_cidrs": list(p.swc.allowed_cidrs),
            "upstream": p.upstream_address, "upstream_scheme": p.upstream_scheme,
            "upstream_scope": scope, "bypassable": scope == "exposed",
        })
    if proxies:
        checks["stack_proxies"] = "ok"
    bypassed = [p["stack_id"] for p in proxies if p["bypassable"]]
    if bypassed:
        checks["upstream_bypass"] = "warn"        # not "failed": LHPC cannot close those ports
        checks["upstream_bypass_stacks"] = bypassed

    # The console's listener scope IS provable here — from /proc/net/tcp (local evidence, NOT a
    # network/subprocess probe), the same source already used for the stack-proxy upstreams above.
    # HTTPS-cert presentation / mTLS behaviour / revocation enforcement still need a real client
    # handshake, so those stay null (honestly unproven without opt-in integration).
    console_scope = listener_scope(system, cfg.port)         # "exposed" | "loopback" | "absent"
    effective = {
        "remote_listener": console_scope == "exposed",       # bound off-loopback == remotely reachable
        "listener_scope": console_scope,
        "https_presented_expected_cert": None,
        "access_mode_verified": None,
        "revocation_enforced": None,
        "note": "listener scope proven from /proc; HTTPS cert / mTLS / revocation require integration proof",
    }
    evidence = {
        "checked_at": _now_iso(),
        "checks": checks,
        "effective": effective,
        "stack_proxies": proxies,
        "desired_snapshot": {
            "bind": cfg.bind, "port": cfg.port, "access_mode": cfg.access_mode,
            "remote_exposed": cfg.remote_exposed, "scheme": cfg.scheme,
        },
    }
    write_evidence(paths, evidence)
    return evidence

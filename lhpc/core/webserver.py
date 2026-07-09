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


def _is_public_default_route(cidr: str) -> bool:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return net.prefixlen == 0        # 0.0.0.0/0 (or ::/0) — the whole internet


# --------------------------------------------------------------------------- nginx config generation

def _abs(paths: Paths, parts) -> str:
    return str(paths.under(*parts))


def _listen(cfg: WebserverConfig) -> str:
    # Bind is a validated host literal; port a validated int. FAIL-SAFE: unless remote
    # exposure is explicitly enabled we ALWAYS listen on loopback, regardless of a stale
    # `bind` value — so a leftover 0.0.0.0 can never open a remote listener by itself.
    bind = cfg.bind if cfg.remote_exposed else "127.0.0.1"
    return f"listen {bind}:{cfg.port} ssl;"


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


def render_nginx_config(paths: Paths, cfg: WebserverConfig) -> str:
    """Generate the complete nginx config. ONLY validated typed data + fixed LHPC paths are
    interpolated; the structure is a fixed template. A rootless (user) nginx: writable pid,
    logs and temp dirs live under the runtime root; TLS terminates here; the backend is the
    Waitress Unix socket. Untrusted forwarded headers are stripped and replaced with
    nginx-set evidence headers."""
    sock = _abs(paths, WAITRESS_SOCK)
    server_crt = _abs(paths, ("config", "tls", "server", "server.crt"))
    server_key = _abs(paths, ("config", "tls", "server", "server.key"))
    client_ca = _abs(paths, ("config", "tls", "client-ca", "ca.crt"))
    crl = _abs(paths, ("config", "tls", "client-ca", "crl.pem"))
    mtls = "" if cfg.access_mode == "no-auth" else (
        f"    ssl_client_certificate {client_ca};\n"
        f"    ssl_crl {crl};\n")
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

    server {{
        {_listen(cfg)}
        server_name _;
        ssl_certificate {server_crt};
        ssl_certificate_key {server_key};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_prefer_server_ciphers on;
        ssl_session_timeout 10m;
{mtls}        {_verify_client(cfg)}
        add_header Strict-Transport-Security "max-age=31536000" always;

        # Source-address gate (real peer; loopback always, remote only from allowed CIDRs).
        {_allow_deny(cfg)}

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
}}
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


def stage_and_validate(system, paths: Paths, cfg: WebserverConfig) -> tuple:
    """Render the nginx config to a STAGED path and `nginx -t` it. NEVER touches the live
    config. Returns (ok, message, staged_path). On success the caller may `promote_config()`;
    on failure the previous live config is left byte-for-byte intact."""
    from . import runtime_fs
    # Rootless nginx creates each `*_temp_path` with a SINGLE-level mkdir and writes its pid/logs
    # under the runtime root — so their parents must already exist or `nginx -t` dies with
    # `[emerg] mkdir() ".../state/run/nginx/body" failed`. Nothing else creates state/run/nginx, so
    # ensure it (and the pid/log parents) here, before every validation. Persistent on disk → the
    # systemd unit needs no runtime-dir setup of its own.
    runtime_fs.mkdir(paths, "state", "run", "nginx")
    runtime_fs.mkdir(paths, "logs")
    staged = paths.under(*NGINX_CONF_STAGED)
    runtime_fs.atomic_write(paths, staged, render_nginx_config(paths, cfg), mode=0o644)
    ok, msg = validate_config(system, paths, str(staged))
    return ok, msg, staged


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


def monitor_view(paths: Paths, cfg: WebserverConfig) -> dict:
    """READ-ONLY cached status for Monitor/GET. Merges DESIRED config (intent) with the
    last-proven EFFECTIVE evidence + read-only PKI state. It NEVER infers active/exposed from
    desired config and NEVER probes. Emits persistent warnings (incl. the no-auth-remote red
    warning, correction #4)."""
    ev = read_evidence(paths)
    effective = ev.get("effective", {}) if isinstance(ev.get("effective"), dict) else {}
    warnings = []
    if cfg.remote_exposed and cfg.access_mode == "no-auth":
        warnings.append({"level": "danger",
                         "text": "Remote access is enabled without client authentication."})
    if cfg.remote_exposed and not cfg.allowed_cidrs:
        warnings.append({"level": "warn",
                         "text": "Remote exposure desired but no allowed source CIDR is set."})
    proven_remote = bool(effective.get("remote_listener"))
    if cfg.remote_exposed and not proven_remote:
        warnings.append({"level": "warn",
                         "text": "Remote exposure requested but not active yet — run Apply to rebind "
                                 "nginx to 0.0.0.0 (or start-service if nginx is not running)."})
    if not proven_remote:
        warnings.append({"level": "info",
                         "text": "Remote exposure disabled or unproven: no remote listener is confirmed."})
    # Declared system (apt) dependencies with their LAST-PROVEN present/absent status (from
    # cached verify evidence — never probed here, so GET stays read-only).
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
        },
        "effective": effective,                 # last proven; empty => unknown
        "checks": checks,
        "system_deps": system_deps,
        "pki": pki.pki_status(paths),
        "last_verified": ev.get("checked_at"),
        "warnings": warnings,
    }


def verify(system, paths: Paths, cfg: WebserverConfig) -> dict:
    """Assemble the effective-state proof checklist and PERSIST it as evidence. Static +
    config checks are proven here (deps present, CA/cert present, nginx -t valid); the LIVE
    listener / HTTPS-cert / mTLS-behavior / revocation-enforcement checks are marked
    'unproven' unless run under opt-in integration with a real proxy. This NEVER reports
    remote exposure as active unless a live probe proved it."""
    from . import runtime_fs
    checks: dict = {}
    plan = plan_exposure(cfg)
    checks["config"] = "ok" if not plan["problems"] else "failed"
    if plan["problems"]:
        checks["config_problems"] = plan["problems"]

    res = system.runner.run(["nginx", "-v"], _NGINX_VALIDATE_TIMEOUT_S)
    checks["nginx_present"] = "failed" if getattr(res, "not_found", False) else "ok"

    st = pki.pki_status(paths)
    checks["server_ca"] = "ok" if st["server_ca"].get("present") else "failed"
    checks["server_cert"] = "ok" if st["server_cert"].get("present") else "failed"
    if cfg.access_mode != "no-auth":
        checks["client_ca"] = "ok" if st["client_ca"].get("present") else "failed"
        checks["crl"] = "ok" if st["crl_present"] else "failed"

    # Render + validate a STAGED config (verify NEVER mutates the live config).
    ok, msg, _staged = stage_and_validate(system, paths, cfg)
    checks["nginx_config_valid"] = "ok" if ok else "failed"
    checks["nginx_config_message"] = msg

    # LIVE effective checks require a real proxy + real client cert material — honestly
    # unproven in the static/unit context (see plan correction #6). Only a live probe sets
    # these true; absence => remote treated as NOT exposed.
    effective = {
        "remote_listener": False,
        "https_presented_expected_cert": None,
        "access_mode_verified": None,
        "revocation_enforced": None,
        "note": "live listener/HTTPS/mTLS/revocation require opt-in integration proof",
    }
    evidence = {
        "checked_at": _now_iso(),
        "checks": checks,
        "effective": effective,
        "desired_snapshot": {
            "bind": cfg.bind, "port": cfg.port, "access_mode": cfg.access_mode,
            "remote_exposed": cfg.remote_exposed,
        },
    }
    write_evidence(paths, evidence)
    return evidence

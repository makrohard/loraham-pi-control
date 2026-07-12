"""CLI adapter (`lhpc`).

Layered help:
    lhpc --help                 short top-level help
    lhpc help <topic>           detailed help on demand
    lhpc <command> --help       per-command help

Every command renders an ActionResult: a compact result line plus actionable
"Next:" commands. Routine commands (status/list/explain/doctor) are bounded and
read-only. Mutating commands (install/build/start/stop/test/…) print a plan and
apply only after confirmation (or --yes).
"""

from __future__ import annotations

import argparse
import sys

from lhpc.core.services import ActionResult, ControllerService
from lhpc.version import __version__

_TOPICS = {
    "safety": (
        "TX safety: the controller never auto-enables TX. A freshly installed or\n"
        "configured stack is RX-only until you explicitly enable TX. Host-side\n"
        "tests are TX-safe by default; TX-capable tests require --tx and an\n"
        "explicit confirmation (or --yes for unattended use)."
    ),
    "resources": (
        "Resource safety: one active stack owns one physical LoRa band (and the\n"
        "shared SPI bus) at a time. Conflicting starts are rejected with the\n"
        "current owner and the stop/status commands to resolve it."
    ),
    "profiles": (
        "Known-working compositions: after a healthy stack start, confirm it\n"
        "('Confirm this stack as working' on the stack page). The newest three\n"
        "operator-confirmed compositions (exact commits per component) are kept\n"
        "per stack; the 'Known working' install/update selector resolves to the\n"
        "newest one, falling back to the manifest pin (clearly labelled)."
    ),
}


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, OSError):
        # Non-interactive / closed stdin -> treat as "no" (safe default).
        return False


def _apply_flow(run, yes: bool) -> int:
    """Show a dry-run plan, then apply it after confirmation (or with --yes)."""
    plan = run(False)
    rc = _render(plan)
    if not plan.ok:
        return rc
    if plan.data.get("changes", 0) == 0:
        print("\nNothing to do.")
        return 0
    if not yes and not _confirm("\nApply these changes? [y/N] "):
        print("Aborted.")
        return 0
    return _render(run(True))


def _print_install_dep_gate(svc, stack, check: bool = False) -> bool:
    """Install-time system-dependency gate for the CLI `install`. Prints a WARN line for each
    missing OPTIONAL dep (advisory — install still proceeds) and, when a MANDATORY dep is missing,
    an ERR line plus the copyable install command(s). Returns True when a mandatory dep is missing.
    `check=False` (apply path) phrases it as a hard refusal; `check=True` (read-only preview) phrases
    it as a report — the caller renders the plan regardless and only reflects the block in the exit
    code. `stack` None → aggregate over all managed stacks."""
    ids = [stack] if stack else [s.id for s in svc.stacks()]
    block, warn = [], []
    for sid in ids:
        gate = svc.install_dep_gate(sid)
        block += [(sid, d) for d in gate["block"]]
        warn += [(sid, d) for d in gate["warn"]]
    for sid, d in warn:
        hint = f"  -> {d['install']}" if d.get("install") else ""
        print(f"WARN  optional dependency not installed for '{sid}': {d['what']}{hint}")
    if not block:
        return False
    if check:
        print(f"ERR   Install is blocked for '{stack or 'all'}': missing mandatory system dependencies")
    else:
        print(f"ERR   Refusing to install '{stack or 'all'}': missing mandatory system dependencies.")
    cmds = []
    for sid, d in block:
        print(f"  {sid}: {d['what']}")
        if d.get("install") and d["install"] not in cmds:
            cmds.append(d["install"])
    if cmds:
        print("\nInstall them, then retry:")
        for c in cmds:
            print(f"  {c}")
    return True


def _render_daemon(view) -> int:
    if not view.reachable:
        print(f"ERR   daemon {view.band}: not reachable ({view.error or 'no CONF socket'})")
        print(f"\nNext:\n  lhpc stack start loraham-daemon-{view.band}")
        return 1
    s, st, ch = view.status, view.stats, view.channel
    if view.ready:
        print(f"OK    daemon {view.band} monitor.")
    else:
        # Reachable but the radio is not usable: never present it as a serving band.
        print(f"WARN  daemon {view.band} live but RADIO={view.radio_state or 'unknown'} "
              "(NOT READY) — no usable radio; dependents cannot start.")
    print(f"Radio: {s.get('RADIO','?')}   TX mode: {s.get('TXMODE','?')}   "
          f"TX active: {s.get('TX','?')}")
    print(f"RSSI:  live {ch.get('LIVERSSI','?')} dBm   packet {ch.get('PACKETRSSI','?')} dBm   "
          f"CAD threshold {s.get('CADRSSI','?')} dBm")
    print(f"CAD:   state {ch.get('CADSTATE','?')}   CADWAIT {s.get('CADWAIT','?')}ms   "
          f"CADIDLE {s.get('CADIDLE','?')}ms")
    print(f"Stats: RX {st.get('RX','?')}  TXOK {st.get('TXOK','?')}  TXERR {st.get('TXERR','?')}  "
          f"uptime {st.get('UPTIME','?')}s")
    print(f"\nNext:\n  lhpc daemon {view.band} --feed\n  lhpc daemon {view.band} --set TXMODE=DIRECT")
    return 0


def _render(result: ActionResult) -> int:
    status = "OK   " if result.ok else "ERR  "
    print(f"{status} {result.summary}")
    for line in result.details:
        print(line)
    if result.next_commands:
        print("\nNext:")
        for cmd in result.next_commands:
            print(f"  {cmd}")
    return 0 if result.ok else 1


def _config_rows(svc, stack: str, band: str) -> list[dict]:
    """Flatten config_param_groups into rows (each carries component/name/value/default/is_identity)."""
    return [r for g in svc.config_param_groups(stack, band) for r in g["rows"]]


def _config_list(svc, stack: str, band: str) -> int:
    groups = svc.config_param_groups(stack, band)
    if not groups:
        print(f"OK    '{stack}' has no configurable parameters")
        return 0
    print(f"OK    {stack} settings" + (f" (band {band})" if band else "") + ":")
    has_identity = False
    for g in groups:
        print(f"  [{g['name']}]")
        for r in g["rows"]:
            has_identity = has_identity or r["is_identity"]
            mark = " *" if r["is_identity"] else ""
            val = r["value"] if r["value"] != "" else "(empty)"
            dflt = "" if r["value"] == r["default"] else f"   [default: {r['default'] or '(empty)'}]"
            print(f"    {r['name']}{mark} = {val}{dflt}")
    if has_identity:
        print("\n  * identity (callsign/node) — required to start a licensed stack")
    print(f"  set a value:  lhpc config {stack} <param> <value>")
    return 0


def _cmd_config(svc, args) -> int:
    """Dispatch `lhpc config` — operator identity, or per-stack settings/daemon params.
    Enforces: operator is a reserved subcommand; modes are mutually exclusive; no ambiguous set."""
    stack = args.stack
    # Split the trailing positional group (collected as `rest` so flags may precede it — see the parser)
    # into the optional <param> [<value>]. More than two is a usage error. (Tests that construct a
    # Namespace directly set param/value and omit `rest`; honour those unchanged.)
    rest = getattr(args, "rest", None)
    if rest is not None:
        if len(rest) > 2:
            print("ERR   too many arguments — usage: lhpc config <stack> [<param> [<value>]]")
            return 2
        args.param = rest[0] if rest else None
        args.value = rest[1] if len(rest) > 1 else None
    op_flags = args.callsign is not None or args.locator is not None

    # ----- operator (RESERVED — never a stack id) -----
    if stack == "operator":
        stray = [n for n, v in (("<param>", args.param), ("--reset", args.reset),
                                 ("--band", args.band), ("--daemon-param", args.daemon_param),
                                 ("--apply-daemon", args.apply_daemon),
                                 ("--reset-daemon", args.reset_daemon)) if v]
        if stray:
            print("ERR   'lhpc config operator' takes only --callsign/--locator "
                  f"(remove: {', '.join(stray)})")
            return 2
        if not op_flags:
            op = svc.config().operator
            print("OK    operator identity:")
            print(f"  callsign = {op.callsign or '(unset)'}")
            print(f"  locator  = {op.locator or '(unset)'}")
            return 0
        return _render(svc.set_operator_identity(callsign=args.callsign, locator=args.locator))

    # ----- stack config -----
    if op_flags:
        print("ERR   --callsign/--locator apply only to 'lhpc config operator'")
        return 2
    if svc.stack(stack) is None:
        print(f"ERR   unknown stack '{stack}'")
        print("\nNext:\n  lhpc list")
        return 1

    daemon_mode = bool(args.daemon_param or args.apply_daemon or args.reset_daemon)
    positional = args.param is not None and args.param != "list"
    active = [m for m, on in (("--reset", args.reset), ("daemon-params", daemon_mode),
                              ("<param>", positional)) if on]
    if len(active) > 1:
        print(f"ERR   conflicting options ({', '.join(active)}) — use one at a time")
        return 2

    if args.reset:
        if not args.yes and not _confirm(f"\nReset {stack} settings to defaults? [y/N] "):
            print("Aborted.")
            return 0
        return _render(svc.reset_config(stack, args.band))

    if daemon_mode:
        if sum(bool(x) for x in (args.daemon_param, args.apply_daemon, args.reset_daemon)) > 1:
            print("ERR   choose one of --daemon-param / --apply-daemon / --reset-daemon")
            return 2
        if args.apply_daemon:
            return _render(svc.apply_daemon_params(stack, args.band))
        if args.reset_daemon:
            return _render(svc.reset_daemon_params(stack, args.band))
        from lhpc.core import daemon_params as _dp
        vals = {}
        for kv in args.daemon_param:
            key, sep, val = kv.partition("=")
            key = key.strip()
            if not sep or not key:
                print(f"ERR   bad --daemon-param {kv!r} — expected KEY=VALUE")
                return 2
            if key not in _dp.ALL_PARAMS:
                print(f"ERR   unknown daemon parameter {key!r} — valid: {', '.join(_dp.ALL_PARAMS)}")
                return 2
            vals[key] = val.strip()
        return _render(svc.save_daemon_params(stack, args.band, vals))

    if args.param is None or args.param == "list":
        return _config_list(svc, stack, args.band)

    # show / set a single parameter — resolve the name to its canonical key (NO first-match).
    fields = svc.config_param_fields(stack, args.band)
    matches = [f for f in fields
               if f["name"] == args.param or f"{f['component']}.{f['name']}" == args.param]
    if not matches:
        print(f"ERR   unknown parameter '{args.param}' for '{stack}'")
        print(f"\nNext:\n  lhpc config {stack}")
        return 1
    if len(matches) > 1:
        forms = sorted({f"{f['component']}.{f['name']}" for f in matches})
        print(f"ERR   '{args.param}' is declared by multiple components — qualify it:")
        for form in forms:
            print(f"    lhpc config {stack} {form} <value>")
        return 1
    fld = matches[0]
    row = next((r for r in _config_rows(svc, stack, args.band)
                if r["component"] == fld["component"] and r["name"] == fld["name"]), None)

    if args.value is None:                                  # show
        print(f"OK    {stack} · {fld['component']} · {fld['name']}")
        if row is not None:
            print(f"  value   = {row['value'] or '(empty)'}")
            print(f"  default = {row['default'] or '(empty)'}")
            if row.get("choices"):
                print(f"  choices = {', '.join(row['choices'])}")
        print(f"\n  set:  lhpc config {stack} {args.param} <value>")
        return 0

    key = f"file_{fld['key']}" if fld["kind"] == "file" else fld["key"]
    res = svc.save_config_bundle(stack, values={key: args.value}, band=args.band)
    rc = _render(res)
    if res.ok and row is not None and row["is_identity"]:
        ok, _f, msg = svc.enforce_identity(stack, args.band)
        if not ok:
            print(f"WARN  {msg}")
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lhpc",
        description="LoRaHAM Pi Control — install, update, configure and "
        "orchestrate LoRaHAM Pi stacks.",
        epilog="Run 'lhpc help <topic>' for: safety, resources, profiles.",
    )
    parser.add_argument("--version", action="version", version=f"lhpc {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("list", help="List stacks defined in the manifest")

    p_status = sub.add_parser(
        "status", help="Show stack/component status (bounded, read-only)"
    )
    p_status.add_argument("stack", nargs="?", help="Limit to one stack")
    p_status.add_argument(
        "--versions", action="store_true", help="Show source/pin status instead"
    )

    p_explain = sub.add_parser("explain", help="Explain a stack and its components")
    p_explain.add_argument("stack", help="Stack id (e.g. loraham, meshcom, meshcore)")

    sub.add_parser("doctor", help="Bounded local health checks")

    p_boot = sub.add_parser("bootstrap", help="Create the runtime root and default config")
    p_boot.add_argument("--yes", action="store_true", help="Apply without confirmation")

    p_install = sub.add_parser("install", help="Adopt/verify managed sources into the runtime root")
    p_install.add_argument("stack", nargs="?", help="Limit to one stack")
    p_install.add_argument("--check", action="store_true", help="Dry run (plan only)")
    p_install.add_argument("--yes", action="store_true", help="Apply without confirmation")
    p_install.add_argument("--source", choices=("pinned", "dev", "stable"), default="dev",
                           help="Version to clone: latest dev / latest stable / pinned")

    p_ia = sub.add_parser("install-all",
                          help="Install/update, build and test ALL stacks in one guided "
                               "run (this can take several minutes)")
    p_ia.add_argument("--yes", action="store_true", help="Apply without confirmation")
    p_ia.add_argument("--source", choices=("pinned", "dev", "stable"), default="dev",
                      help="Version to clone (default: dev — latest development)")
    p_ia.add_argument("--no-tests", action="store_true", help="Skip host tests")
    p_ia.add_argument("--tx", action="store_true",
                      help="After the run, start the daemon TEMPORARILY and transmit ONE "
                           "bounded test frame per ready band (REAL RF — dummy loads!)")
    p_ia.add_argument("--run-id", default="", help=argparse.SUPPRESS)

    p_hmac = sub.add_parser("hmac",
                            help="MeshCom HMAC password: status, or enable/disable/renew "
                                 "(rebuilds the firmware — several minutes)")
    p_hmac.add_argument("action",
                        choices=("status", "enable", "disable", "renew", "abort", "recover"))
    p_hmac.add_argument("stack", nargs="?", default="",
                        help="Target stack (default: the meshcom stack)")
    p_hmac.add_argument("--yes", action="store_true",
                        help="Apply now (rebuilds the firmware + restarts the link)")

    # Internal: the detached HMAC-apply driver spawned by the web/CLI apply flow. Not for direct use.
    p_hd = sub.add_parser("_hmac-apply")
    p_hd.add_argument("stack")
    p_hd.add_argument("action", choices=("enable", "disable", "renew"))
    p_hd.add_argument("run_id")

    p_help = sub.add_parser("help", help="Detailed help on a topic")
    p_help.add_argument("topic", nargs="?", help="safety | resources | profiles")

    # Start/stop/restart a stack or component.
    p_stack = sub.add_parser("stack", help="Start/stop/restart a stack or component")
    stack_sub = p_stack.add_subparsers(dest="stack_action", metavar="<action>")
    for action in ("start", "stop", "restart"):
        sp = stack_sub.add_parser(action, help=f"{action.capitalize()} a stack/component")
        sp.add_argument("stack", help="Stack or component id")
        sp.add_argument("--yes", action="store_true", help="Apply without confirmation")

    # Per-stack settings (callsign/params/daemon params) and the global operator identity.
    p_cfg = sub.add_parser("config", help="View or set stack settings and operator identity")
    p_cfg.add_argument("stack", help="Stack id, or 'operator' for the global callsign/locator")
    # ONE greedy trailing group, not two nargs="?" positionals: on Python 3.12.x argparse cannot
    # backfill split-across-an-optional positionals (`config <stack> --reset <param> <value>` dies with
    # SystemExit(2) before the handler's graceful conflict check). `nargs="*"` collects them uniformly;
    # the handler splits into param/value.
    p_cfg.add_argument("rest", nargs="*", metavar="[param [value]]",
                       help="Parameter name (or 'list'); optionally a new value. Omit to list all. "
                            "Qualify a duplicated name as <component>.<param>.")
    p_cfg.add_argument("--band", default="", help="Band for band-switchable stacks (e.g. 433 or 868)")
    p_cfg.add_argument("--reset", action="store_true", help="Reset this stack's settings to defaults")
    p_cfg.add_argument("--yes", action="store_true", help="Apply --reset without confirmation")
    p_cfg.add_argument("--daemon-param", metavar="KEY=VALUE", action="append", dest="daemon_param",
                       help="Persist a band-scoped daemon parameter override, e.g. TXMODE=DIRECT "
                            "(repeatable)")
    p_cfg.add_argument("--apply-daemon", action="store_true", dest="apply_daemon",
                       help="Apply this stack's saved daemon params to the running daemon")
    p_cfg.add_argument("--reset-daemon", action="store_true", dest="reset_daemon",
                       help="Reset this stack's daemon parameters")
    p_cfg.add_argument("--callsign", help="(operator only) set the global operator callsign")
    p_cfg.add_argument("--locator", help="(operator only) set the global Maidenhead locator")

    p_build = sub.add_parser("build", help="Build a stack/component")
    p_build.add_argument("target", help="Stack/component id")
    p_build.add_argument("--yes", action="store_true", help="Apply without confirmation")

    p_logs = sub.add_parser("logs", help="Show a bounded tail of a component's log")
    p_logs.add_argument("target", help="Stack/component id")
    p_logs.add_argument("--lines", type=int, default=200, help="Tail length")

    p_daemon = sub.add_parser("daemon", help="Monitor a daemon band, or apply a live setting")
    p_daemon.add_argument("band", help="433 or 868")
    p_daemon.add_argument("--set", metavar="KEY=VALUE", dest="set_kv",
                          help="Apply a live CONF setting (e.g. TXMODE=DIRECT)")
    p_daemon.add_argument("--feed", action="store_true", help="Show recent RX/TX activity")
    p_daemon.add_argument("--yes", action="store_true", help="Apply --set without confirmation")

    for name in ("update", "uninstall"):
        sp = sub.add_parser(name, help=f"{name.capitalize()} a stack/component")
        sp.add_argument("target", nargs="?", default="", help="Stack/component id")
        sp.add_argument("--yes", action="store_true", help="Apply without confirmation")
        if name == "update":
            sp.add_argument("--source", choices=("pinned", "dev", "stable"), default="dev",
                            help="Version to fetch: latest dev / latest stable / pinned")

    p_clean = sub.add_parser("clean", help="DESTRUCTIVE: purge a stack (sources, config, "
                             "logs, history)")
    p_clean.add_argument("target", help="Stack id")
    p_clean.add_argument("--purge", action="store_true",
                         help="Required: confirm the destructive purge")
    p_clean.add_argument("--yes", action="store_true", help="Apply without interactive confirm")

    p_test = sub.add_parser("test", help="Run host tests, or a bounded TX test with --tx")
    p_test.add_argument("target", help="Stack/component id")
    p_test.add_argument("--tx", action="store_true", help="TX-capable test (real RF, dummy loads)")
    p_test.add_argument("--yes", action="store_true", help="Non-interactive confirm")

    p_sc = sub.add_parser("source-check",
                          help="Check managed sources for available upstream updates (read-only)")
    p_sc.add_argument("target", nargs="?", default="", help="Limit to one stack/component")

    p_kw = sub.add_parser("known-working",
                          help="Record a running stack's current commits as a known-good composition")
    p_kw.add_argument("stack", help="Stack id")

    p_su = sub.add_parser("self-update", help="Check for / apply lhpc's own update")
    p_su.add_argument("--apply", action="store_true",
                      help="Apply the update (fast-forward); restart the console afterwards")
    p_su.add_argument("--overwrite", action="store_true",
                      help="Reset the checkout to upstream: discard local changes (modified + "
                           "non-ignored untracked files) or a diverged history that can't fast-forward")
    p_su.add_argument("--yes", action="store_true", help="Apply without an interactive confirm")
    p_su.add_argument("--repair-integration", action="store_true",
                      help="Install/restore the managed web console + one-click updater units")
    p_su.add_argument("--recover-request", action="store_true",
                      help="Clear a stuck one-click update request/in-flight record (safe)")
    # PLUMBING, run ONLY by lhpc-selfupdate.service (claims the request; mode comes from it).
    # Operators use --apply; the unit is parameter-free by design.
    p_su.add_argument("--run-service", action="store_true", help=argparse.SUPPRESS)

    p_web = sub.add_parser("web", help="Start the local operator web console")
    p_web.add_argument("--host", default="127.0.0.1", help="Bind host (loopback only)")
    p_web.add_argument("--port", type=int, default=8770, help="Bind port")
    p_web.add_argument("--socket", action="store_true",
                       help="Productive mode: serve on the protected Unix socket behind nginx "
                            "(no TCP listener; requires waitress)")

    # Webserver (controller-owned): production Nginx + Waitress + TLS/mTLS control.
    # Vocabulary mirrors the service enums exactly (config.WEBSERVER_ACCESS_MODES / STACKWEB_MODES
    # / WEBSERVER_SCHEMES) so the CLI never diverges from the web UI / service.
    _WS_MODES = ["local-open-remote-auth", "auth-everywhere", "no-auth"]
    _STACKWEB_MODES = ["local", "lan", "public"]
    _WS_SCHEMES = ["https", "http"]
    p_ws = sub.add_parser("webserver", help="Production webserver (HTTPS/mTLS) control")
    ws_sub = p_ws.add_subparsers(dest="ws_cmd")
    ws_sub.add_parser("status", help="Cached webserver status (read-only)")
    ws_sub.add_parser("verify", help="Verify effective state + persist evidence")
    ws_sub.add_parser("apply", help="Validate + activate (reload) the current config")
    ws_sub.add_parser("start-service", help="Operator-context: generate config + enable/start nginx")
    ws_sub.add_parser("disable-remote", help="Disable remote exposure (bind loopback)")
    ws_sub.add_parser("reset-defaults", help="Reset desired config to safe defaults")
    ws_sub.add_parser("tls-renew", help="Renew the HTTPS server certificate")
    p_ws_logs = ws_sub.add_parser("logs", help="Tail the nginx front-end access/error log")
    p_ws_logs.add_argument("--access", action="store_true", help="Access log (default: error log)")
    p_ws_logs.add_argument("--lines", type=int, default=300, help="Lines to tail (default 300)")
    p_ws_init = ws_sub.add_parser("init", help="Bootstrap PKI (two CAs + server cert + CRL)")
    p_ws_init.add_argument("--dns", action="append", default=[], help="DNS SAN (repeatable)")
    p_ws_init.add_argument("--ip", action="append", default=[], help="IP SAN (repeatable)")
    p_ws_init.add_argument("--confirm-recreate", action="store_true",
                           help="Confirm DESTRUCTIVE re-init when a CA already exists")
    p_ws_cfg = ws_sub.add_parser("configure", help="Set desired webserver config")
    p_ws_cfg.add_argument("--bind", help="Listen address: 127.0.0.1 (loopback) or 0.0.0.0 (remote)")
    p_ws_cfg.add_argument("--port", type=int, help="HTTPS port (default 8443)")
    p_ws_cfg.add_argument("--access-mode", choices=_WS_MODES,
                          help="Client-certificate policy: " + " | ".join(_WS_MODES))
    p_ws_cfg.add_argument("--dns", action="append", help="DNS SAN for the server cert (repeatable)")
    p_ws_cfg.add_argument("--ip", action="append", help="IP SAN for the server cert (repeatable)")
    p_ws_exp = ws_sub.add_parser("expose", help="Enable remote exposure (opt-in)")
    p_ws_exp.add_argument("--cidr", action="append", default=[], help="Allowed source CIDR (repeatable)")
    p_ws_exp.add_argument("--access-mode", choices=_WS_MODES,
                          help="Client-certificate policy: " + " | ".join(_WS_MODES))
    p_ws_exp.add_argument("--confirm-phrase", default="",
                          help="Type 'enable-remote' to confirm; 'enable-remote-danger' for the "
                               "elevated case (public 0.0.0.0/0 or a no-auth remote mode)")
    # Per-stack web-UI reverse proxy exposure (mirrors `expose`'s two-level confirmation).
    p_ws_proxy = ws_sub.add_parser("proxy",
                                   help="Configure a stack's web-UI reverse proxy (intent; run apply)")
    p_ws_proxy.add_argument("stack", help="Stack id whose web UI to proxy")
    p_ws_proxy.add_argument("--mode", choices=_STACKWEB_MODES,
                            help="local = loopback only; lan = listen, only --cidr passes; "
                                 "public = 0.0.0.0/0 (elevated)")
    p_ws_proxy.add_argument("--port", type=int, help="Public listener port (0 = not proxied)")
    p_ws_proxy.add_argument("--scheme", choices=_WS_SCHEMES, help="Public listener scheme")
    p_ws_proxy.add_argument("--access-mode", choices=_WS_MODES,
                            help="Client-certificate policy: " + " | ".join(_WS_MODES))
    p_ws_proxy.add_argument("--cidr", action="append", default=[],
                            help="Allowed source CIDR for lan mode (repeatable)")
    p_ws_proxy.add_argument("--confirm-phrase", default="",
                            help="'enable-remote' to confirm lan/public; 'enable-remote-danger' for "
                                 "public 0.0.0.0/0, a no-auth mode, or an http listener")
    p_ws_cert = ws_sub.add_parser("cert", help="Client (device) certificate lifecycle")
    cert_sub = p_ws_cert.add_subparsers(dest="cert_cmd")
    cert_sub.add_parser("list", help="List client certificates")
    _CERT_HELP = {
        "issue": "Issue a new client certificate + a one-time .p12 passphrase",
        "reissue": "Rotate a client certificate + a new one-time .p12 passphrase",
        "discard-export": "Delete the stored .p12 export for a label (keeps the certificate)",
    }
    for _n in ("issue", "reissue", "discard-export"):
        _pc = cert_sub.add_parser(_n, help=_CERT_HELP[_n])
        _pc.add_argument("label", help="Client certificate label (device name)")
    p_cert_exp = cert_sub.add_parser("export",
                                     help="Write a device's PKCS#12 (.p12) bundle to a file")
    p_cert_exp.add_argument("label", help="Client certificate label to export")
    p_cert_exp.add_argument("path", help="Destination file (created mode 0600; refuses to overwrite)")
    p_cert_exp.add_argument("--force", action="store_true",
                            help="Overwrite an existing destination file")
    p_ws_rev = cert_sub.add_parser("revoke", help="Revoke a client certificate (updates the CRL)")
    p_ws_rev.add_argument("label", help="Client certificate label to revoke")
    p_ws_rev.add_argument("--confirm-label", default="",
                          help="Must equal <label> to confirm revocation")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        # argparse consumes a command's trailing positional chunk ONCE, so positionals split around an
        # optional (e.g. `config <stack> --reset <param> <value>`) cannot be back-filled into an
        # already-consumed nargs="*" `rest`; whether it errors is 3.12.x-point-release-dependent. Fold
        # such trailing POSITIONAL tokens back into config's `rest`; anything else is a genuine
        # unrecognized argument returned as an int (never an uncaught SystemExit — main() promises -> int).
        if getattr(args, "command", "") == "config" and all(not t.startswith("-") for t in extra):
            args.rest = list(getattr(args, "rest", []) or []) + list(extra)
        else:
            sys.stderr.write(f"{parser.prog}: error: unrecognized arguments: {' '.join(extra)}\n")
            return 2

    if not args.command:
        parser.print_help()
        return 0

    svc = ControllerService()

    if args.command == "list":
        return _render(svc.list_stacks())
    if args.command == "status":
        if args.versions:
            return _render(svc.status_versions())
        return _render(svc.status(args.stack))
    if args.command == "explain":
        return _render(svc.explain(args.stack))
    if args.command == "doctor":
        return _render(svc.doctor())
    if args.command == "bootstrap":
        return _apply_flow(lambda apply: svc.bootstrap(apply=apply), yes=args.yes)
    if args.command == "install":
        if args.check:
            # Read-only preview: render the plan FIRST (so the bootstrap precondition and adoptions
            # always show), then REPORT the dep gate — it never preempts the plan. Exit nonzero when
            # the plan itself failed, else when the gate blocks (still "not installable").
            rc = _render(svc.install(args.stack, apply=False, source=args.source))
            blocked = _print_install_dep_gate(svc, args.stack, check=True)
            return rc if rc != 0 else (1 if blocked else 0)
        if _print_install_dep_gate(svc, args.stack, check=False):
            return 1
        return _apply_flow(lambda apply: svc.install(args.stack, apply=apply, source=args.source),
                           yes=args.yes)
    if args.command == "install-all":
        if args.tx and args.no_tests:
            print("Refusing: --tx requires host tests (drop --no-tests).")
            return 2
        plan = svc.install_all(source=args.source, tests=not args.no_tests, tx=args.tx,
                               apply=False)
        rc = _render(plan)
        if not plan.ok or plan.data.get("changes", 0) == 0:
            return rc
        if not args.yes and not _confirm(
                "\nRun the full install/build"
                + ("" if args.no_tests else "/test")
                + (" + TX test (REAL RF — dummy loads!)" if args.tx else "")
                + " sequence for ALL stacks? [y/N] "):
            print("Aborted.")
            return 0
        return _render(svc.install_all(source=args.source, tests=not args.no_tests,
                                       tx=args.tx, run_id=args.run_id, apply=True))
    if args.command == "hmac":
        sid = args.stack or svc.hmac_default_stack()
        if not sid or not svc.hmac_applies(sid):
            print(f"HMAC password does not apply to '{args.stack or sid or 'any stack'}'.")
            return 1
        if args.action == "status":
            print(f"HMAC password: {'enabled' if svc.hmac_status(sid) else 'disabled'} ({sid})")
            return 0
        if args.action in ("abort", "recover"):
            st = svc.hmac_apply_status()
            run_id = st.get("run_id", "") if (st and not st.get("unsafe")) else ""
            r = (svc.hmac_apply_abort(sid, run_id) if args.action == "abort"
                 else svc.hmac_apply_recover(sid, run_id))
            print(r.summary)
            return 0 if r.ok else 1
        if not args.yes:
            print(f"'{args.action}' rebuilds the MeshCom firmware and restarts the link "
                  "(several minutes; the link is down until it finishes).")
            if args.action == "disable":
                print("It REMOVES client authentication.")
            elif args.action == "renew":
                print("It rotates the shared secret — every client must be re-provisioned.")
            print(f"Re-run to apply:  lhpc hmac {args.action} {sid} --yes")
            return 0
        return svc.hmac_apply_cli(sid, args.action, emit=print)
    if args.command == "_hmac-apply":
        # Detached driver: stdout/stderr are captured to the run log by spawn_job, so `print`
        # streams into the live log window. Never prints the secret (the step runner redacts).
        # GATE FIRST — prove the parent identity-tracked us BEFORE touching anything. During the gate
        # SIGTERM/SIGINT keep their DEFAULT action, so an untracked (orphan) driver the parent SIGTERMs
        # dies cleanly, having mutated nothing.
        if svc._hmac_verify_tracked(args.stack, args.run_id, emit=print) != 0:
            return 1
        # Verified tracked: only NOW install COOPERATIVE cancellation (the handler does ONLY a plain flag
        # assignment; the runner polls it and terminates the build via its local session token, and THIS
        # driver then writes the truthful terminal marker).
        import signal
        from lhpc.core.service_hmac import _request_hmac_abort
        signal.signal(signal.SIGTERM, _request_hmac_abort)
        signal.signal(signal.SIGINT, _request_hmac_abort)
        return svc._hmac_run_steps(args.stack, args.action, args.run_id, emit=print)
    if args.command == "help":
        if not args.topic:
            print("Topics: " + ", ".join(_TOPICS))
            print("Usage: lhpc help <topic>")
            return 0
        text = _TOPICS.get(args.topic)
        if text is None:
            print(f"Unknown topic '{args.topic}'. Topics: {', '.join(_TOPICS)}")
            return 1
        print(text)
        return 0

    if args.command == "stack":
        if args.stack_action in ("start", "stop", "restart"):
            return _apply_flow(
                lambda a: svc.run_action(args.stack_action, args.stack, apply=a), yes=args.yes)
        parser.parse_args(["stack", "--help"])
        return 1
    if args.command == "config":
        return _cmd_config(svc, args)
    if args.command == "build":
        return _apply_flow(lambda a: svc.build(args.target, apply=a), yes=args.yes)
    if args.command == "logs":
        return _render(svc.logs(args.target, lines=args.lines))
    if args.command == "daemon":
        if args.set_kv:
            key, _, value = args.set_kv.partition("=")
            return _apply_flow(lambda a: svc.daemon_set(args.band, key, value, apply=a),
                               yes=args.yes)
        if args.feed:
            feed = svc.daemon_feed(args.band)
            print("\n".join(feed) if feed else "(no recent RX/TX activity)")
            return 0
        return _render_daemon(svc.daemon_view(args.band))
    if args.command == "update":
        return _apply_flow(lambda a: svc.update(args.target, apply=a, source=args.source),
                           yes=args.yes)
    if args.command == "uninstall":
        return _apply_flow(lambda a: svc.uninstall(args.target, apply=a), yes=args.yes)
    if args.command == "clean":
        # DESTRUCTIVE: both --purge AND (--yes or interactive confirm) are required.
        return _apply_flow(lambda a: svc.clean(args.target, apply=a, purge=args.purge),
                           yes=args.yes)
    if args.command == "test":
        return _apply_flow(
            lambda a: svc.test(args.target, tx=args.tx, apply=a),
            yes=args.yes)
    if args.command == "source-check":
        return _render(svc.source_check(args.target))
    if args.command == "known-working":
        return _render(svc.confirm_known_working(args.stack))
    if args.command == "self-update":
        if args.run_service:
            # Unit plumbing (non-interactive) — mode is read from the claimed request marker.
            return _render(svc.self_update_run_service())
        if args.repair_integration:
            return _render(svc.self_update_repair_integration())
        if args.recover_request:
            return _render(svc.self_update_recover_request())
        if not args.apply:
            return _render(svc.self_update_check())          # explicit upstream check + status
        if not args.yes and not _confirm(
                "This fast-forwards lhpc to the upstream version. If the web console is running it "
                "will be STOPPED, updated, and STARTED again automatically. Proceed? [y/N] "):
            print("Aborted.")
            return 0
        return _render(svc.self_update_apply_operator(force=args.overwrite))
    if args.command == "web":
        from lhpc.adapters.web.app import run_server

        return run_server(host=args.host, port=args.port, socket=args.socket)

    if args.command == "webserver":
        import secrets as _secrets
        cmd = getattr(args, "ws_cmd", None)
        if cmd == "status":
            d = svc.webserver_monitor().data
            des = d["desired"]
            print("OK    webserver (cached status; no live probing)")
            print(f"  bind {des['bind']}:{des['port']}  mode {des['access_mode']}  "
                  f"remote_exposed {des['remote_exposed']}")
            print(f"  allowed CIDRs: {', '.join(des['allowed_cidrs']) or '(none)'}")
            eff = d.get("effective", {})
            print(f"  effective: remote_listener={eff.get('remote_listener')}  "
                  f"last_verified={d.get('last_verified')}")
            for dep in d.get("system_deps", []):
                extra = "" if dep["status"] == "present" else f"  -> {dep['install']}"
                print(f"  system dep {dep['name']}: {dep['status']}{extra}")
            for w in d.get("warnings", []):
                print(f"  [{w['level']}] {w['text']}")
            return 0
        if cmd == "verify":
            return _render(svc.webserver_verify())
        if cmd == "apply":
            return _render(svc.webserver_apply())
        if cmd == "start-service":
            return _render(svc.webserver_start_service())
        if cmd == "init":
            return _render(svc.webserver_init(dns_sans=args.dns or None, ip_sans=args.ip or None,
                                              confirm=args.confirm_recreate))
        if cmd == "configure":
            fields = {k: v for k, v in (
                ("bind", args.bind), ("port", args.port), ("access_mode", args.access_mode),
                ("dns_sans", args.dns), ("ip_sans", args.ip)) if v is not None}
            return _render(svc.webserver_configure(**fields))
        if cmd == "expose":
            phrase = (args.confirm_phrase or "").strip()
            return _render(svc.webserver_expose(
                args.cidr, access_mode=args.access_mode,
                confirm=phrase in ("enable-remote", "enable-remote-danger"),
                confirm_public=(phrase == "enable-remote-danger")))
        if cmd == "proxy":
            phrase = (args.confirm_phrase or "").strip()
            return _render(svc.stack_web_configure(
                args.stack, mode=args.mode, port=args.port, scheme=args.scheme,
                access_mode=args.access_mode, cidrs=(args.cidr or None),
                confirm=phrase in ("enable-remote", "enable-remote-danger"),
                confirm_public=(phrase == "enable-remote-danger")))
        if cmd == "disable-remote":
            return _render(svc.webserver_disable_remote())
        if cmd == "reset-defaults":
            return _render(svc.webserver_reset_defaults())
        if cmd == "tls-renew":
            return _render(svc.webserver_tls_renew())
        if cmd == "logs":
            path, ls = svc.webserver_log_tail("access" if args.access else "error", args.lines)
            print(path or "(no log file yet)")
            for line in ls:
                print(line)
            if not ls:
                print("(no output yet)")
            return 0
        if cmd == "cert":
            cc = getattr(args, "cert_cmd", None)
            if cc == "list":
                certs = svc.webserver_cert_list().data["certs"]
                if not certs:
                    print("  (no client certificates)")
                for c in certs:
                    print(f"  {c.get('state', '?'):8} {c.get('label', '?'):16} "
                          f"{c.get('serial', '')[:16]}  exp {c.get('not_after', '')}")
                return 0
            if cc in ("issue", "reissue"):
                pw = _secrets.token_urlsafe(18)   # one-time; shown once, never persisted/logged
                fn = svc.webserver_cert_issue if cc == "issue" else svc.webserver_cert_reissue
                res = fn(args.label, pw)
                if res.ok:
                    print(f"OK    {res.summary}")
                    for line in res.details:
                        print(f"  {line}")
                    print("\n  ONE-TIME bundle passphrase (not stored — record it now):"
                          f"\n    {pw}")
                    return 0
                return _render(res)
            if cc == "revoke":
                if (args.confirm_label or "").strip() != args.label:
                    print(f"ERR   revocation refused — pass --confirm-label {args.label} "
                          "to confirm revoking this exact certificate")
                    return 1
                return _render(svc.webserver_cert_revoke(args.label))
            if cc == "discard-export":
                return _render(svc.webserver_cert_discard_export(args.label))
            if cc == "export":
                import os as _os
                data = svc.webserver_cert_export_bytes(args.label)
                if not data:
                    print(f"ERR   no export bundle for '{args.label}' — issue or reissue it first")
                    return 1
                flags = _os.O_WRONLY | _os.O_CREAT | (_os.O_TRUNC if args.force else _os.O_EXCL)
                try:
                    fd = _os.open(args.path, flags, 0o600)
                except FileExistsError:
                    print(f"ERR   refusing to overwrite existing '{args.path}' — pass --force")
                    return 1
                except OSError as exc:
                    print(f"ERR   could not write '{args.path}': {exc}")
                    return 1
                # Enforce 0600 even when overwriting (O_CREAT mode applies only on creation), and
                # never echo the bundle bytes/passphrase — only the path + size.
                _os.fchmod(fd, 0o600)
                with _os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                print(f"OK    wrote {len(data)} bytes to {args.path} (mode 0600)")
                return 0
        print("Usage: lhpc webserver {status|verify|init|configure|expose|proxy|disable-remote|"
              "reset-defaults|tls-renew|logs|cert ...}")
        return 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

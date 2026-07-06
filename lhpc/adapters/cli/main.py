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

    p_help = sub.add_parser("help", help="Detailed help on a topic")
    p_help.add_argument("topic", nargs="?", help="safety | resources | profiles")

    # Start/stop a stack or component.
    p_stack = sub.add_parser("stack", help="Start/stop a stack or component")
    stack_sub = p_stack.add_subparsers(dest="stack_action", metavar="<action>")
    for action in ("start", "stop"):
        sp = stack_sub.add_parser(action, help=f"{action.capitalize()} a stack/component")
        sp.add_argument("stack", help="Stack or component id")
        sp.add_argument("--yes", action="store_true", help="Apply without confirmation")

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

    p_su = sub.add_parser("self-update", help="Check for / apply lhpc's own update")
    p_su.add_argument("--apply", action="store_true",
                      help="Apply the update (fast-forward); restart the console afterwards")
    p_su.add_argument("--overwrite", action="store_true",
                      help="Discard local changes (modified + non-ignored untracked files) if dirty")
    p_su.add_argument("--yes", action="store_true", help="Apply without an interactive confirm")
    # PLUMBING, called by lhpc-selfupdate[-overwrite].service (the one-click web update):
    # stop lhpc-web -> apply -> sync venv -> ALWAYS start lhpc-web again. Hidden: operators
    # use --apply; the units are parameter-free by design.
    p_su.add_argument("--run-service", action="store_true", help=argparse.SUPPRESS)

    p_web = sub.add_parser("web", help="Start the local operator web console")
    p_web.add_argument("--host", default="127.0.0.1", help="Bind host (loopback only)")
    p_web.add_argument("--port", type=int, default=8770, help="Bind port")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
            return _render(svc.install(args.stack, apply=False, source=args.source))
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
        if args.stack_action == "start":
            return _apply_flow(lambda a: svc.run_action("start", args.stack, apply=a),
                               yes=args.yes)
        if args.stack_action == "stop":
            return _apply_flow(lambda a: svc.run_action("stop", args.stack, apply=a),
                               yes=args.yes)
        parser.parse_args(["stack", "--help"])
        return 1
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
    if args.command == "self-update":
        if args.run_service:
            # Unit plumbing (non-interactive by nature) — see self_update_run_service.
            return _render(svc.self_update_run_service(force=args.overwrite))
        if not args.apply:
            return _render(svc.self_update_check())          # explicit upstream check + status
        if not args.yes and not _confirm(
                "This fast-forwards lhpc to the upstream version and then the web console must be "
                "restarted to load it. Proceed? [y/N] "):
            print("Aborted.")
            return 0
        return _render(svc.self_update_apply(force=args.overwrite))
    if args.command == "web":
        from lhpc.adapters.web.app import run_server

        return run_server(host=args.host, port=args.port)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

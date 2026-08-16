"""`lhpc-testlab` — the lab's own CLI (separate from `lhpc`). Constructs a real
ControllerService (which picks up the lab provider via LHPC_SYSTEM_PROVIDER) and calls
the ops functions. Also serves the two hidden helpers the lab spawns detached: `_gpsd`
(the fake gpsd) and `_power` (the simulated-power trigger)."""
from __future__ import annotations

import argparse
import sys

from lhpc.core.services import ControllerService

from . import ops


def _svc() -> ControllerService:
    return ControllerService()


def _render(result) -> int:
    print(result.summary)
    for line in result.details:
        print(line)
    if getattr(result, "next_commands", None):
        print("\nNext:")
        for c in result.next_commands:
            print(f"  {c}")
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lhpc-testlab",
                                description="LHPC test lab (simulated hardware)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create/adopt a lab root")
    sub.add_parser("status", help="lab state, scenario, fakes")
    sub.add_parser("reset", help="deterministic healthy baseline")
    ps = sub.add_parser("scenario", help="switch scenario")
    ps.add_argument("name")
    pi = sub.add_parser("inject", help="queue one RX frame")
    pi.add_argument("band", choices=("433", "868"))
    pi.add_argument("preset")
    sub.add_parser("check", help="lab health gate")
    pw = sub.add_parser("web", help="serve the console with the lab web panel")
    pw.add_argument("--port", type=int, default=8770)
    # hidden helpers spawned detached by the lab
    pg = sub.add_parser("_gpsd")
    pg.add_argument("--dummy", action="store_true")
    pp = sub.add_parser("_power")
    pp.add_argument("--kind", required=True, choices=("reboot", "poweroff"))
    sub.add_parser("_populate")   # background bring-up of the remaining headless stacks
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "_gpsd":
        from . import gpsd
        return gpsd.main(ControllerService()._paths)
    if args.cmd == "_power":
        rc = ops.power(_svc(), args.kind)
        print(f"[testlab-power] {args.kind} rc={rc}")
        return rc
    if args.cmd == "web":
        from .labweb import serve
        return serve(port=args.port)
    dispatch = {"init": ops.init, "status": ops.status, "reset": ops.reset,
                "check": ops.check, "_populate": ops.populate}
    if args.cmd in dispatch:
        return _render(dispatch[args.cmd](_svc()))
    if args.cmd == "scenario":
        return _render(ops.scenario(_svc(), args.name))
    if args.cmd == "inject":
        return _render(ops.inject(_svc(), args.band, args.preset))
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""systemctl / busctl / journalctl simulation. systemctl --user is backed by the
stateful supervisor (mutations TRANSITION unit state; lhpc-nginx drives a real
unprivileged nginx); system-scope queries answer honestly for a box whose stacks are
direct-spawned processes (no such units). busctl answers the logind power handshake from
the scenario's `power_auth` flag.
"""
from __future__ import annotations

from lhpc.core.probes.backends import CommandResult

from . import scenarios, supervisor


def _ok(out: str = "") -> CommandResult:
    return CommandResult(0, out, "")


def _err(rc: int, msg: str, out: str = "") -> CommandResult:
    return CommandResult(rc, out, msg)


def simulate_busctl(paths, argv: list) -> CommandResult:
    joined = " ".join(str(a) for a in argv)
    if "org.freedesktop.login1" in joined and ("CanReboot" in joined
                                               or "CanPowerOff" in joined):
        verdict = scenarios.effective_state(paths).get("power_auth", "yes")
        return _ok(f's "{verdict}"\n')
    return _err(1, "testlab busctl: unhandled call")


def simulate_journalctl(paths, argv: list) -> CommandResult:
    return _ok("-- testlab: journal simulated (see state/testlab/events.log) --\n")


def _show_props(active: bool, enabled: bool, loaded: bool = True) -> str:
    return ("ActiveState={}\nSubState={}\nLoadState={}\nUnitFileState={}\n".format(
        "active" if active else "inactive",
        "running" if active else "dead",
        "loaded" if loaded else "not-found",
        "enabled" if enabled else "disabled"))


def simulate_systemctl(paths, argv: list) -> CommandResult:
    args = [str(a) for a in argv[1:]]
    user_scope = "--user" in args
    words = [a for a in args if not a.startswith("-")]
    if not words:
        return _ok()
    verb, rest = words[0], words[1:]
    unit = rest[0] if rest else ""

    if verb == "daemon-reload":
        return _ok()

    if verb == "show":
        if not user_scope:
            # No foreign system units exist in the lab — stacks are direct spawns.
            return _ok(_show_props(False, False, loaded=False))
        if "FragmentPath" in " ".join(args):
            return _ok("FragmentPath=\nDropInPaths=\n")
        rec = supervisor.unit(paths, unit)
        return _ok(_show_props(rec["active"], rec["enabled"]))

    if verb == "is-active":
        rec = supervisor.unit(paths, unit)
        return _ok("active\n") if rec["active"] else _err(3, "", "inactive\n")

    if verb == "is-enabled":
        rec = supervisor.unit(paths, unit)
        return _ok("enabled\n") if rec["enabled"] else _err(1, "", "disabled\n")

    if verb in ("enable", "disable", "start", "stop", "restart", "reload"):
        if not user_scope:
            return _err(1, "testlab systemctl: system-scope mutations are refused")
        now = "--now" in args
        units = rest or ([unit] if unit else [])
        for u in units:
            if verb == "enable":
                supervisor.set_unit(paths, u, enabled=True,
                                    active=True if now else None)
            elif verb == "disable":
                supervisor.set_unit(paths, u, enabled=False,
                                    active=False if now else None)
            elif verb in ("start", "restart", "reload"):
                supervisor.set_unit(paths, u, active=True)
            elif verb == "stop":
                supervisor.set_unit(paths, u, active=False)
            wants_nginx = u.startswith("lhpc-nginx")
            if wants_nginx:
                action = {"enable": "start" if now else "", "disable": "stop" if now
                          else "", "start": "start", "restart": "restart",
                          "reload": "reload", "stop": "stop"}[verb]
                if action:
                    ok, detail = supervisor.nginx_ctl(paths, action)
                    scenarios.log_event(paths, f"nginx {action}: {detail}")
                    if not ok and action != "stop":
                        supervisor.set_unit(paths, u, active=False)
                        return _err(1, f"testlab nginx {action} failed: {detail}")
        return _ok()

    return _err(1, f"testlab systemctl: unhandled verb {verb!r}")

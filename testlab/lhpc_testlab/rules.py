"""Command classification for the lab runner and the spawn guard.

The DENY table is a UX layer, not the security boundary: it turns a DIRECT invocation of
a host-mutating command into a typed, logged refusal instead of a raw permission error.
The real boundary is the environment the lab runs in — an unprivileged user with no sudo
and no capabilities — which also stops NESTED invocations (a build script calling apt)
that no argv filter can see.
"""
from __future__ import annotations

import os

# Direct host mutators: refused unconditionally, in every scenario.
DENY_BASENAMES = frozenset({
    "sudo", "doas", "pkexec", "shutdown", "reboot", "halt", "poweroff",
    "nft", "iptables", "apt", "apt-get", "aptitude", "usermod", "loginctl",
})

# Simulated entirely — the real binaries are NEVER reached through the lab runner.
SIMULATED_BASENAMES = frozenset({"nmcli", "busctl", "systemctl", "journalctl"})

# Power-trigger detection for the spawn guard: EXACT match against the argv
# service_maintenance composes (single source of truth — power_trigger_argv). A unit
# test locks the two together, so neither a composition change nor an unrelated argv
# containing power words can slip past / be hijacked.


def classify(argv) -> tuple[str, str]:
    """('deny'|'simulate'|'pass', detail). detail = the offending/simulated basename."""
    if not argv:
        return ("pass", "")
    base = os.path.basename(str(argv[0]))
    if base in DENY_BASENAMES:
        return ("deny", base)
    if base == "dpkg" and any(a in ("-i", "--install", "-r", "--remove", "-P", "--purge")
                              for a in argv[1:]):
        return ("deny", "dpkg")
    if base in SIMULATED_BASENAMES:
        return ("simulate", base)
    return ("pass", base)


def power_kind_in(argv) -> str:
    """The power action a spawned trigger would perform, or '' — EXACT argv match."""
    from lhpc.core.service_maintenance import power_trigger_argv
    argv = [str(a) for a in argv]
    for kind in ("reboot", "poweroff"):
        if argv == power_trigger_argv(kind):
            return kind
    return ""

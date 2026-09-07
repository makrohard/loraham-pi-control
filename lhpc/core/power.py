"""Power controls (dashboard Reboot / Shut down): the mechanics with a meaning of their own.

The closed set of actions and logind's authorization method per action, the bounded trigger the
lab's spawn guard matches exactly, the structured parse of `busctl` output, and the schema of
the pending-power marker. Authorization caching, the system probes, admission, the pending
marker's lifetime decision and the action itself stay with the service (`service_maintenance`).
"""

from __future__ import annotations

import json
import math
import re

# kind -> the logind CanX query proving the operator is authorized WITHOUT interaction.
POWER_KINDS = {"reboot": "CanReboot", "poweroff": "CanPowerOff"}

POWER_TRIGGER_TEMPLATE = "sleep 1.5; exec timeout -k 5s 90s systemctl --no-block {kind}"


def power_trigger_argv(kind: str) -> list:
    """THE power trigger, single source of truth: the lab's spawn guard matches this
    exact shape (tests lock the two together), so a composition change here can never
    silently slip past the guard onto a real host."""
    return ["sh", "-c", POWER_TRIGGER_TEMPLATE.format(kind=kind)]


def parse_busctl_verdict(returncode: int, stdout: str) -> str:
    """logind's CanReboot/CanPowerOff answer from a `busctl call` — one of yes/no/challenge/na,
    or '' on any failure. Structured parse of the output (exactly `s "<verdict>"`), never
    substring."""
    if returncode != 0:
        return ""
    m = re.fullmatch(r'\s*s\s+"([a-z]+)"\s*', stdout or "")
    return m.group(1) if m else ""


def pending_marker_payload(kind: str, boot_id: str, requested_uptime: float) -> str:
    """The pending-power marker as written — the one shape `parse_pending_marker` reads back."""
    return json.dumps({"kind": kind, "boot_id": boot_id, "requested_uptime": requested_uptime})


def parse_pending_marker(raw: str) -> tuple[str, str, float]:
    """(kind, boot_id, requested_uptime) of a pending-power marker, or raise on ANY malformed
    content — the caller refuses conservatively and leaves the marker for the operator.

    Types are validated before use: `str()`/`float()` coercion once laundered a null boot_id
    ("None") and a NaN uptime (every comparison False) straight into the fail-open prune path.
    Closed-set kind, nonempty string boot id, finite nonnegative numeric uptime."""
    rec = json.loads(raw or "")
    kind = rec["kind"]
    bid = rec["boot_id"]
    up0 = rec["requested_uptime"]
    if (kind not in POWER_KINDS
            or not isinstance(bid, str) or not bid
            or isinstance(up0, bool) or not isinstance(up0, (int, float))
            or not math.isfinite(up0) or up0 < 0):
        raise ValueError("malformed power-pending marker")
    return kind, bid, float(up0)

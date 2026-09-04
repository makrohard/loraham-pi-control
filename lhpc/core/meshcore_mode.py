"""The MeshCore stack's ONE mode decision — reused by every seam that depends on it.

`mode` is a plain setting on `meshcore-node` (`[repeater] role` in the generated config):

    chat           today's Companion host on TCP 5000 — the default, every existing box
    chat+repeater  upstream openhop_repeater hosting the SAME Companion inside it (5000 + 8000)
    repeater       the repeater alone: no Companion, so no chat GUI / CLI (8000 only)

Exactly one openhop process runs on the radio in every mode. This module answers the questions
the controller asks about the mode in one place, so start readiness, ongoing status, identity
enforcement and the optional Companion clients can never disagree:

  * which of the node's DECLARED endpoints exist in this mode (`expected_endpoints`),
  * whether the chat node's identity (`node_name`) is required (`chat_identity_required`),
  * whether the Companion clients (webui, cli) can run (`clients_available`),
  * whether the repeater's own identity is required (`repeater_on`),
  * whether the stack consumes the box's position at all (`position_consumed`).

Deliberately MeshCore-specific: no generic conditional-endpoint or conditional-parameter
machinery in the manifest model (audited decision). The host application re-checks the same
rules at startup from the generated config.
"""

from __future__ import annotations

MODES = ("chat", "chat+repeater", "repeater")
DEFAULT_MODE = "chat"
BEHAVIOURS = ("forward", "monitor", "no_tx")     # upstream's own repeater `mode`

STACK_ID = "meshcore"
NODE_ID = "meshcore-node"
CLIENT_IDS = ("meshcore-webui", "meshcore-cli")   # Companion clients of TCP 5000
COMPANION_PORT = 5000
DASHBOARD_PORT = 8000


def normalize(value) -> str:
    """A mode value as stored/typed, reduced to one of MODES (anything else = the default)."""
    v = str(value or "").strip()
    return v if v in MODES else DEFAULT_MODE


def repeater_on(mode) -> bool:
    return normalize(mode) != "chat"


def chat_identity_required(mode) -> bool:
    """The Companion (chat node) runs — in `chat` and `chat+repeater`."""
    return normalize(mode) != "repeater"


def clients_available(mode) -> bool:
    """webui / cli connect to the Companion on TCP 5000, which repeater-only has not got."""
    return chat_identity_required(mode)


def position_consumed(mode) -> bool:
    """Only the Companion reads the box's position (the `meshcore-gps` feed, or fixed
    coordinates); the repeater's own config pins GPS off. So a repeater-only start must run
    no feed, claim no receiver and never be refused over a GPS setting it does not consult."""
    return chat_identity_required(mode)


def _port(ep) -> int | None:
    addr = str(getattr(ep, "address", "") or "")
    if getattr(ep, "kind", "") != "tcp" or ":" not in addr:
        return None
    tail = addr.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def expected_endpoints(comp, mode) -> list:
    """The endpoints of `comp` that EXIST in `mode`. Only the MeshCore node is mode-dependent:
    its Companion port (5000) is absent in `repeater`, its dashboard port (8000) is absent in
    `chat`. Every other component's endpoints pass through unchanged."""
    eps = list(getattr(comp, "endpoints", ()) or ())
    if getattr(comp, "id", "") != NODE_ID:
        return eps
    m = normalize(mode)
    out = []
    for ep in eps:
        port = _port(ep)
        if port == COMPANION_PORT and not chat_identity_required(m):
            continue
        if port == DASHBOARD_PORT and not repeater_on(m):
            continue
        out.append(ep)
    return out

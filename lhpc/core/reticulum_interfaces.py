"""Cross-field rules for the Reticulum stack's optional Internet interface (pure, no I/O).

Every other Reticulum setting stands alone: a frequency is valid or it is not. The Internet
interface is the first one whose fields only make sense together, and upstream RNS does not
check the combinations for us:

  * `TCPClientInterface` reads `target_host` and does `int(target_port)` while it is being
    constructed, so an enabled interface with a missing endpoint is a CONSTRUCTION failure —
    a different path from the merely-unreachable target the node is designed to tolerate
    (`panic_on_interface_error = No`);
  * it builds an IFAC from either `networkname` or `passphrase` ALONE, so a half-configured
    IFAC is not refused, it just silently passes nothing.

So the rules live here and are checked twice: when the operator SAVES (the endpoint half, which
is all the saved config can express) and when the config is GENERATED on the start path (both
halves — that is the only place the passphrase from `config/secrets.toml` is resolved). The LoRa
interface needs none of this: our own driver already refuses a half-configured IFAC there, and
duplicating that policy is what this module exists to avoid.
"""

STACK_ID = "reticulum"
NODE_ID = "rns"

ENABLED = "internet_enabled"
HOST = "internet_host"
PORT = "internet_port"
NETNAME = "internet_ifac_netname"
NETKEY = "internet_ifac_netkey"

_CONFIG_HINT = f"lhpc config {STACK_ID}"


def enabled(value) -> bool:
    """Whether the saved `internet_enabled` value turns the interface on. The param is an enum
    over `no`/`yes`, so nothing else can be stored through a supported route; a hand-edited
    value that is neither reads as OFF, because the interface is opt-in."""
    return str(value or "").strip().lower() == "yes"


def _get(values, name: str) -> str:
    return str(values.get(name, "") or "").strip()


def endpoint_problem(values) -> str:
    """Why the saved Internet endpoint cannot start ("" = fine). Presence only: the field
    formats are the `host`/`port` validators' job, and a stored value they reject is replaced
    by the empty default at generation time, which lands here as "missing"."""
    if not enabled(values.get(ENABLED)):
        return ""                                   # off: an incomplete endpoint is staging
    missing = [n for n in (HOST, PORT) if not _get(values, n)]
    if not missing:
        return ""
    return (f"the Internet interface is enabled but its endpoint is incomplete "
            f"({', '.join(missing)} not set) — RNS builds the interface at start and would "
            f"fail there. The console saves the whole form at once; from the CLI set "
            f"{HOST} and {PORT} first, then {ENABLED} — or set {ENABLED} back to 'no' "
            f"({_CONFIG_HINT})")


def ifac_problem(values) -> str:
    """Why the saved Internet IFAC cannot start ("" = fine) — BOTH or NEITHER. Needs the
    resolved passphrase, so only the generation path can ask this."""
    if not enabled(values.get(ENABLED)):
        return ""
    name, key = _get(values, NETNAME), _get(values, NETKEY)
    if bool(name) == bool(key):
        return ""
    if name:
        return (f"the Internet interface has an IFAC network name but no passphrase — RNS "
                f"would build an IFAC from the name alone and the link would pass nothing. "
                f"Set [reticulum] {NETKEY} in config/secrets.toml, or clear the network name "
                f"({_CONFIG_HINT})")
    return (f"the Internet interface has an IFAC passphrase in config/secrets.toml but no "
            f"network name — set {NETNAME} ({_CONFIG_HINT}), or remove the passphrase")


def problem(values) -> str:
    """Every rule, for the generation path: the endpoint first (it is what the operator most
    likely just changed), then the IFAC."""
    return endpoint_problem(values) or ifac_problem(values)

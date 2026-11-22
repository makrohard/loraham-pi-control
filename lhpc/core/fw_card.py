"""F23: what the Home card may say about a client address and the managed firewall."""
from __future__ import annotations


def _port(value):
    """A port number from `value`, or None: ASCII digits only (str.isdigit also takes "²", which int() refuses)."""
    s = str(value if value is not None else "")
    return int(s) if s.isascii() and s.isdigit() else None


def port_blocked(svc, port) -> bool:
    """The APPLIED firewall drops TCP `port` for every source: its applied rows for that port (proto tcp) are non-empty
    and EVERY one is deny-default, not selected for direct access and has no source allow list. No applied firewall,
    a missing or non-numeric port, or no such row = False (the card never guesses). Read once per request."""
    want = _port(port)
    if want is None:
        return False
    cand = svc._request_memo(("fw-applied-candidate",), svc._fw_applied_candidate)
    rows = [e for e in ((cand or {}).get("endpoints") or ()) if e.get("proto") == "tcp" and _port(e.get("port")) == want]
    return bool(rows) and all(e.get("deny_default") is True and not e.get("selected") and not e.get("allow_cidrs")
                              for e in rows)

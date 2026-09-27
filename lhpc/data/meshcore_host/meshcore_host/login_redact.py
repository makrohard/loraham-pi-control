"""Keep the MeshCore login secret out of every log.

The pinned openhop_core's login handler logs the decrypted login at INFO
(`openhop_core/node/handlers/login_server.py`): "[LoginServer] Plaintext hex: <timestamp + password>"
on every login, and "[LoginServer] Password hex: <password>" for room-server logins. The pinned
repeater passes `logger.info` of its "RepeaterDaemon" logger as that handler's `log_fn`, and runs
in THIS process, so the lines reach stderr (LHPC's start log, shown by the console) and upstream's
dashboard log buffer alike.

A filter on the ORIGINATING logger runs before any handler, so it covers every sink at once. It
rewrites the payload to REDACTED and keeps the rest of the line, so the log still shows that a
login was decrypted. The prefixes are the pinned core's exact text; LHPC's controller scrubs the
same prefixes from its existing start logs (lhpc/core/log_redact.py), and a test on the pinned
core fails if they ever disappear from it.
"""
from __future__ import annotations

import logging

PREFIXES = ("[LoginServer] Plaintext hex:", "[LoginServer] Password hex:")
REDACTED = "<redacted by LHPC>"
REPEATER_LOGGER = "RepeaterDaemon"          # the logger whose .info the pinned repeater passes

logger = logging.getLogger("meshcore-host.redact")


class LoginSecretFilter(logging.Filter):
    """Rewrite a login-secret record's payload; never raise into the repeater."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for prefix in PREFIXES:
                if prefix in msg:
                    record.msg = msg[:msg.index(prefix) + len(prefix)] + " " + REDACTED
                    record.args = ()
                    break
            return True
        except Exception:
            return False                      # unreadable: drop it rather than risk the secret


_FILTER = LoginSecretFilter()


def protect(target: logging.Logger | str) -> None:
    """Attach the filter to a logger (idempotent)."""
    lg = logging.getLogger(target) if isinstance(target, str) else target
    if _FILTER not in lg.filters:
        lg.addFilter(_FILTER)


def protect_login_helper(daemon) -> None:
    """After upstream built its login helper: attach the filter to the logger its `log_fn`
    really belongs to. A repin that passes another logger is covered too; one that passes
    something that is not a logger's method is said loudly."""
    fn = getattr(getattr(daemon, "login_helper", None), "log_fn", None)
    owner = getattr(fn, "__self__", None)
    if isinstance(owner, logging.Logger):
        protect(owner)
    elif fn is not None:
        logger.warning("login-log redaction cannot attach: the repeater's login log_fn is %r, not "
                       "a logger method; decrypted logins may reach the log", fn)

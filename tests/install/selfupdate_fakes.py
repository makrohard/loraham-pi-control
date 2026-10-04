"""Fakes shared by the self-update tests in this directory."""

from lhpc.core.probes.backends import CommandResult

# `systemctl --user is-active lhpc-web.service` answering "inactive": the console is not running.
WEB_INACTIVE = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"):
                CommandResult(1, "", "")}

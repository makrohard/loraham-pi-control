"""Fakes shared by the self-update tests in this directory."""

import types

from lhpc.core import selfupdate
from lhpc.core.probes.backends import CommandResult

# `systemctl --user is-active lhpc-web.service` answering "inactive": the console is not running.
WEB_INACTIVE = {("systemctl", "--user", "is-active", "--quiet", "lhpc-web.service"):
                CommandResult(1, "", "")}


def frozen_budget(monkeypatch, left: float) -> None:
    """The helper's budget with `left` seconds remaining on a clock that stands still: selfupdate reads a fake
    monotonic (1000.0) and its deadline is 1000.0 + left, so no test depends on how fast the box runs."""
    monkeypatch.setattr(selfupdate, "time", types.SimpleNamespace(monotonic=lambda: 1000.0,
                                                                  time=selfupdate.time.time))
    monkeypatch.setattr(selfupdate, "_BUDGET_END", [1000.0 + left])

"""Build the demo Flask app: the real lhpc console wired to the simulating DemoService.
Used by the Pyodide bootstrap (and tests)."""
from __future__ import annotations

import os

# The firewall's paths resolve once, when lhpc.core.firewall is imported (LHPC_FW_PATH_PREFIX): a demo-owned prefix,
# so the log fixture's firewall.log never lands in a host's /run when the demo runs under CPython (Pyodide's is its own).
os.environ.setdefault("LHPC_FW_PATH_PREFIX", "/tmp/lhpcroot/fw")

from . import shims

shims.install()   # fcntl no-op MUST precede any lhpc import that pulls it in


def build_app(service=None):
    from lhpc.adapters.web.app import create_app

    from . import logs_fixture
    from .service import DemoService
    svc = service or DemoService()
    logs_fixture.install(svc._paths)        # the log windows' fixed box logs
    return create_app(service_factory=lambda: svc), svc

"""Build the demo Flask app: the real lhpc console wired to the simulating DemoService.
Used by the Pyodide bootstrap (and tests)."""
from __future__ import annotations

from . import shims

shims.install()   # fcntl no-op MUST precede any lhpc import that pulls it in


def build_app(service=None):
    from lhpc.adapters.web.app import create_app

    from .service import DemoService
    svc = service or DemoService()
    return create_app(service_factory=lambda: svc), svc

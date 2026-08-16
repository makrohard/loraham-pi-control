"""The lab web launcher: wraps lhpc's real `create_app()` with the lab blueprint,
banner, and Codespaces link rewriting, then serves it. `lhpc-testlab web` runs this.
Nothing here lives inside lhpc."""
from __future__ import annotations

import os

from lhpc.adapters.web.app import create_app

from .web import install


def build_app():
    return install(create_app())


def serve(port: int = 8770) -> int:
    app = build_app()
    try:
        from waitress import serve as wserve
        wserve(app, host="127.0.0.1", port=port, threads=8)
    except ImportError:
        app.run(host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve(int(os.environ.get("LHPC_LAB_WEB_PORT", "8770"))))

"""Entry point: ``python -m meshcore_host <config.toml>``.

Exit codes (distinct so LHPC's lifecycle can tell configuration mistakes from
identity problems; both are non-retryable without operator action):
    2  unusable configuration
    3  identity unusable (missing/malformed/lax permissions) — NEVER minted here
    1  any other startup/runtime failure
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .app import HostApp
from .config import ConfigError, load_config
from .identity import IdentityError

EXIT_CONFIG = 2
EXIT_IDENTITY = 3


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if len(argv) != 1:
        print("usage: python -m meshcore_host <config.toml>", file=sys.stderr)
        return EXIT_CONFIG
    try:
        cfg = load_config(argv[0])
    except ConfigError as exc:
        logging.getLogger("meshcore-host").error("Configuration error: %s", exc)
        return EXIT_CONFIG
    if cfg.repeater_on:
        # `chat+repeater` / `repeater`: upstream's RepeaterDaemon (hosting the Companion inside it
        # in chat+repeater) on the SAME radio adapter — one openhop process, one argv.
        from .repeater import run_repeater
        return run_repeater(cfg)
    try:
        app = HostApp(cfg)
    except IdentityError as exc:
        logging.getLogger("meshcore-host").error("Identity error: %s", exc)
        return EXIT_IDENTITY
    except (ConfigError, ValueError) as exc:
        logging.getLogger("meshcore-host").error("Configuration error: %s", exc)
        return EXIT_CONFIG
    try:
        asyncio.run(app.run_until_signal())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.getLogger("meshcore-host").error("Fatal: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

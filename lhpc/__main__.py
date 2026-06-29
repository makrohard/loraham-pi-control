"""Allow `python -m lhpc ...` as an alias for the CLI adapter."""

from lhpc.adapters.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())

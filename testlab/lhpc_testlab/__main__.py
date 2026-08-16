"""`python -m lhpc_testlab …` — same entry as the `lhpc-testlab` console script.
Used by the lab's detached spawns (`-m lhpc_testlab _gpsd` / `_power`)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

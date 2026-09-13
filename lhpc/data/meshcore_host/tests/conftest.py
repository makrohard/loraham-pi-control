import sys
from pathlib import Path

# The one sanctioned sys.path edit (tests/README.md, rule 7): this suite runs under a stack's
# interpreter and imports meshcore_host from the source tree, never an installed copy. The
# helpers beside this file (fake_loraham_daemon, harness) need nothing: pytest puts the test
# directory itself on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sys
from pathlib import Path

# The decoders are scripts run by a stack's interpreter; the tests run under that same
# interpreter (a CI lane per stack) and import them from the source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

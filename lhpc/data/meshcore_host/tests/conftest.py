import sys
from pathlib import Path

# Make meshcore_host importable from the source tree without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

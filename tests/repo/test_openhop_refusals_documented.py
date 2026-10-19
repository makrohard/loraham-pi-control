"""The guide's list of start refusals is the host's refusal table (meshcore_host.openhop_file.REFUSALS):
every refusal's phrase appears in docs/openhop-on-lhpc.md, so the documented list cannot drift from the
code. A repository invariant: it reads a doc and imports the host's table."""
import re
import sys

import repo_paths

sys.path.insert(0, str(repo_paths.REPO / "lhpc" / "data" / "meshcore_host"))
from meshcore_host.openhop_file import REFUSALS  # noqa: E402


def test_every_refusal_is_in_the_guide():
    guide = re.sub(r"\s+", " ", (repo_paths.REPO / "docs" / "openhop-on-lhpc.md").read_text())
    missing = [phrase for _key, phrase in REFUSALS if phrase not in guide]
    assert not missing, f"refusals missing from docs/openhop-on-lhpc.md: {missing}"

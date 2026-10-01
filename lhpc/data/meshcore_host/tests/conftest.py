import importlib.util
import os
import sys
from pathlib import Path

import pytest

# The one sanctioned sys.path edit (tests/README.md, rule 7): this suite runs under a stack's
# interpreter and imports meshcore_host from the source tree, never an installed copy. The
# helpers beside this file (fake_loraham_daemon, harness) need nothing: pytest puts the test
# directory itself on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Without the pinned openHop libraries (the stack's venv) every module fails at import, and a run
# that also named other paths was interrupted by those collection errors. A module whose
# libraries are absent is skipped here instead, by name, before it is imported; under `CI` it
# FAILS with that name, so the lane stays red.
_NEEDS = ("openhop_core",)
_NEEDS_CLIENT = {"test_companion_integration.py", "test_persistence.py", "test_two_node_sim.py"}


class _LibraryMissing(pytest.File):
    def collect(self):
        if os.environ.get("CI"):        # a CI lane installs the library: its absence is a broken lane
            pytest.fail(self.reason, pytrace=False)
        pytest.skip(self.reason)


def pytest_pycollect_makemodule(module_path, parent):
    needs = _NEEDS + (("meshcore",) if module_path.name in _NEEDS_CLIENT else ())
    missing = [m for m in needs if importlib.util.find_spec(m) is None]
    if missing:
        node = _LibraryMissing.from_parent(parent, path=module_path)
        node.reason = (f"{', '.join(missing)} not importable: run under the MeshCore stack's "
                       "interpreter (see tests/README.md)")
        return node
    return None

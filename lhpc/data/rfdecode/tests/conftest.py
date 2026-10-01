import importlib.util
import os
import sys
from pathlib import Path

import pytest

# The decoders are scripts run by a stack's interpreter; the tests run under that same
# interpreter (a CI lane per stack) and import them from the source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Each decoder exits 3 at import when its stack's library is missing (`_proto.fail`: the wrong
# interpreter). A SystemExit while collecting aborts the WHOLE session, so a run that also named
# other paths never reached them. A module whose library is absent is skipped here instead,
# by name, before it is imported; under `CI` it FAILS with that name, so the lane stays red.
_NEEDS = {
    "test_meshcore_decoder.py": ("openhop_core",),
    "test_meshtastic_decoder.py": ("meshtastic", "Cryptodome"),
    "test_reticulum_decoder.py": ("RNS",),
}


class _LibraryMissing(pytest.File):
    def collect(self):
        if os.environ.get("CI"):        # a CI lane installs the library: its absence is a broken lane
            pytest.fail(self.reason, pytrace=False)
        pytest.skip(self.reason)


def pytest_pycollect_makemodule(module_path, parent):
    missing = [m for m in _NEEDS.get(module_path.name, ()) if importlib.util.find_spec(m) is None]
    if missing:
        node = _LibraryMissing.from_parent(parent, path=module_path)
        node.reason = (f"{', '.join(missing)} not importable: run under the stack's own interpreter "
                       "(see tests/README.md)")
        return node
    return None

"""The operator runs the shell scripts at the repository root directly (`./install.sh`,
`./bootstrap-deps.sh`, `./uninstall.sh`), so each must keep its exec bit.
A regenerated or rewritten file that drops the bit fails with "Permission denied" (rc 126)
before any of its own logic runs. The scripts under lhpc/data/scripts are always started
as `bash <script>` by the manifest, so their mode does not matter and they are not listed here.
"""

import os
import stat

import pytest

import repo_paths

_ROOT_SCRIPTS = sorted(repo_paths.REPO.glob("*.sh"))


def test_the_root_scripts_are_found():
    names = {p.name for p in _ROOT_SCRIPTS}
    assert {"install.sh", "uninstall.sh", "bootstrap-deps.sh"} <= names


@pytest.mark.parametrize("script", _ROOT_SCRIPTS, ids=lambda p: p.name)
def test_root_script_can_be_run_directly(script):
    mode = stat.S_IMODE(script.stat().st_mode)
    assert mode & stat.S_IXUSR, f"{script.name} lost its exec bit (mode {oct(mode)})"
    assert os.access(script, os.X_OK)

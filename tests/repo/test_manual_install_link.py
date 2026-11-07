"""The README's manual install block creates the same `lhpc` command link as install.sh.

Only install.sh linked `~/.local/bin/lhpc`; the manual block ended with an `export PATH` for its
own shell. Step 6 says the reboot puts `lhpc` on the PATH, so a reader who followed the manual
block got `lhpc: command not found` after it. Both READMEs are checked: the block runs as written,
with the network and build commands stubbed, and the link it leaves must equal the one install.sh's
own lines leave under the default target. The commands are language-independent, so the German block
must equal the English one (it had kept an unpinned `pip install -e` after the lock install landed).
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

import repo_paths

_REPO = repo_paths.REPO
_STUB = "#!/bin/sh\nexit 0\n"


def _manual_block(readme: str) -> str:
    m = re.search(r"<summary><em>(?:Manual|Manuell) — clone / venv / bootstrap</em></summary>\s*```bash\n(.*?)\n```",
                  readme, re.S)
    assert m, "no manual clone/venv/bootstrap block"
    return m.group(1)


def _stub(path, name):
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(_STUB)
    (path / name).chmod(0o755)


def _link(home):
    link = home / ".local" / "bin" / "lhpc"
    assert link.is_symlink(), f"{link} not created"
    return os.path.relpath(os.readlink(link), home)


def _install_sh_link(tmp_path):
    """The link install.sh's own lines make: its default target, its path constants and section 3b."""
    text = (_REPO / "install.sh").read_text()
    lines = ['TARGET_DIR="${HOME}/loraham-pi-control"']
    for pat in (r"^readonly VENV=.*$", r"^readonly LOCAL_BIN=.*$", r"^readonly LOCAL_BIN_LINK=.*$",
                r'^\s*mkdir -p "\$LOCAL_BIN"$', r'^\s*ln -sfn "\$\{VENV\}/bin/lhpc" "\$LOCAL_BIN_LINK"$'):
        found = re.findall(pat, text, re.M)
        assert len(found) == 1, pat
        lines.append(found[0].strip())
    assert lines[0] in text
    home = tmp_path / "install-sh"
    home.mkdir()
    subprocess.run(["bash", "-euo", "pipefail", "-c", "\n".join(lines)], check=True,
                   env={"HOME": str(home), "PATH": os.environ["PATH"]})
    return _link(home)


@pytest.mark.parametrize("name", ["README.md", "README.de.md"])
def test_the_manual_block_links_lhpc_as_install_sh_does(tmp_path, name):
    home, stubs = tmp_path / "home", tmp_path / "stubs"
    for cmd in ("git", "python3"):
        _stub(stubs, cmd)
    for cmd in ("pip", "lhpc"):
        _stub(home / "loraham-pi-control" / "venv" / "lhpc" / "bin", cmd)
    block = _manual_block((_REPO / name).read_text())
    subprocess.run(["bash", "-euo", "pipefail", "-c", block], check=True,
                   env={"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin"})
    assert _link(home) == _install_sh_link(tmp_path) == "loraham-pi-control/venv/lhpc/bin/lhpc"


def test_the_german_manual_block_equals_the_english_one():
    assert _manual_block((_REPO / "README.de.md").read_text()) == _manual_block((_REPO / "README.md").read_text())

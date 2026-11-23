"""A client that runs the node's Reticulum wheel takes rns from that wheel alone.

The meshchat build installs the node's wheel, then resolves its own packages against two constraints: the
wheel (`.venv/lhpc-rns-constraint.txt`, written by rns-wheel.sh from the rns pin) and its frozen PyPI set. A
version line for rns in that set repeats the pin and goes stale when the pin moves: the release bot moves the
Reticulum pin and never edits a constraints file, and the build then fails with ResolutionImpossible."""
from __future__ import annotations

import re

from lhpc.core import assets
from lhpc.core.manifest import load_manifest

NODE_CONSTRAINT = ".venv/lhpc-rns-constraint.txt"
ASSET_CONSTRAINTS = re.compile(r"^\{asset\}/(?P<name>[A-Za-z0-9._-]+-constraints\.txt)$")


def _wheel_clients():
    """(component id, its pip-install argvs) for every component whose build installs the node's wheel."""
    for st in load_manifest():
        for c in st.components:
            argvs = [s.get("argv", []) for s in c.build_steps]
            if any("{asset}/scripts/rns-wheel.sh" in a and "install" in a for a in argvs):
                yield c.id, [a for a in argvs if a and a[0].endswith("pip") and "install" in a]


def _constraint_files(argv):
    return [argv[i + 1] for i, tok in enumerate(argv[:-1]) if tok in ("-c", "--constraint")]


def test_a_wheel_clients_own_constraints_never_pin_rns_and_the_wheel_holds_it():
    seen = {}
    for cid, installs in _wheel_clients():
        for argv in installs:
            files = _constraint_files(argv)
            own = [ASSET_CONSTRAINTS.match(f)["name"] for f in files if ASSET_CONSTRAINTS.match(f)]
            if not own:
                continue
            assert NODE_CONSTRAINT in files, f"{cid}: resolves against {own} without the node's wheel"
            for name in own:
                pinned = [ln for ln in assets.asset_text(name).splitlines()
                          if re.match(r"\s*rns\s*(==|@|<|>|~|!|$)", ln, re.I)]
                assert not pinned, f"{cid}: {name} pins rns ({pinned}); the node's wheel holds it"
                seen[cid] = name
    assert seen.get("meshchat") == "meshchat-constraints.txt"     # the one such client today: the check ran

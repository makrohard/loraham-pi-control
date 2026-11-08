"""L1 (DEC-W339), through the lab's public path: on the aarch64 lane the reset installs meshcom and meshtastic from
the binary mirror the workflow filled before the lanes (LHPC_BINARY_MIRROR), and a reset whose mirror lacks the
files fails closed, naming the mirror, instead of fetching live. Elsewhere (no binary channel, or no mirror set)
these skip."""
from __future__ import annotations

import os
import platform

import pytest
from lhpc_testlab.testing import LabServer

pytestmark = pytest.mark.skipif(
    platform.machine() not in ("aarch64", "arm64") or not os.environ.get("LHPC_BINARY_MIRROR"),
    reason="the binary mirror is the aarch64 lane's (LHPC_BINARY_MIRROR unset or not aarch64)")


@pytest.mark.parametrize("stack", ["meshcom", "meshtastic"])
def test_the_reset_installed_the_binary_stack_from_the_mirror(lab, stack):
    from lhpc.core import binary_receipt
    from lhpc.core.paths import Paths
    state, _rec, why = binary_receipt.receipt_state(Paths(runtime_root=lab.root), stack)
    assert state == "valid", f"{stack}: binary receipt {state} — {why}"


def test_a_reset_with_an_empty_mirror_fails_closed_naming_it(tmp_path_factory):
    empty = tmp_path_factory.mktemp("emptymirror")
    server = LabServer(tmp_path_factory.mktemp("emptyroot") / "runtime")
    server.env["LHPC_BINARY_MIRROR"] = str(empty)
    with pytest.raises(RuntimeError, match=f"index-v3.json is not in the binary mirror {empty}"):
        server.init_and_reset()

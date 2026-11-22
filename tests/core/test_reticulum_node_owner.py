"""The Reticulum node's owner is a fact of its launch record (plan reti v21 §1d RED-1, B4): a one-node stack's start
writes `node_owner`, a member's record names its owner (`member_of`), and the owner is read from that fact in any
record order — pages, firewall rows and every later owner reader (instances.recorded_radio)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
import radios_fixture

from lhpc.core import commands, instances
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _record(root, rid, pid, **extra):
    radios_fixture.launch_record(root, "reticulum", "rns", rid, "868", pid)
    path = root / "state" / "owned" / f"rns__868__{pid}__{rid}.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **extra}))


@pytest.mark.parametrize("owner_pid,member_pid", [(200, 100), (100, 200)])     # the member's record first, then last
def test_the_owner_is_the_record_that_says_so_in_either_order(tmp_path, owner_pid, member_pid):
    radios_fixture.write(tmp_path, radios_fixture.BOX_H)
    _record(tmp_path, "r4", owner_pid, node_owner=True)
    _record(tmp_path, "r2", member_pid, member_of="r4")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    # red-before (d8e330cc): the first record by file name, r2 (the member) when its record sorts first
    assert instances.recorded_radio(svc._paths, "reticulum", ("r2", "r4")) == "r4"
    assert [i.rid for i in commands.stack_instances(svc.stack("reticulum"), svc._paths)] == ["r4"]   # pages, firewall


def test_a_one_node_stacks_launch_records_its_owner_and_another_stacks_does_not(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system)
        life.OBSERVE_TIMEOUT_S = 0
        for sid, cid in (("reticulum", "rns"), ("kiss", "loraham-kiss-tnc")):
            stk = svc.stack(sid)
            assert life._observe_and_record(stk, stk.component(cid), p.pid, "868", ["x"], rid="r2") == "ok"
        recs = {r["stack"]: r for r in life.owned_inventory()[0]}
        # red-before (d8e330cc): no record names its node's owner
        assert (recs["reticulum"].get("node_owner"), "node_owner" in recs["kiss"]) == (True, False)
        assert life._validate_owned_record(f"{recs['reticulum']['launch_id']}.json", recs["reticulum"]) == ""
    finally:
        p.kill()
        p.wait(timeout=5)

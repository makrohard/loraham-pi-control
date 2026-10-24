"""T-R6, the status rule across the update (A §2.3): a process named by a VERIFIED ownership record
counts as running although the probe's spec no longer matches its argv (a stack an older LHPC
started, before its argv gained an instance token)."""
import subprocess
import time

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import Component, ComponentKind, ProcessSpec, RunState, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import StatusProber

# The spec names an executable no process here runs: only an ownership record can say "running".
COMP = Component(id="x-node", name="x", kind=ComponentKind.SERVICE,
                 process=ProcessSpec(exec_name="x-node-binary"))


def _state(tmp_path, owned):
    prober = StatusProber(FakeSystem().system, Paths(runtime_root=tmp_path), owned_running=owned)
    return prober.assess_component(COMP)


def test_a_verified_owned_process_counts_as_running(tmp_path):
    st = _state(tmp_path, {"x-node": [4242]})
    assert st.run_state is RunState.RUNNING and st.pids == [4242]
    assert st.evidence["process.owned"] == "verified ownership record"


def test_without_a_verified_record_the_unmatched_spec_stays_stopped(tmp_path):
    assert _state(tmp_path, {}).run_state is RunState.STOPPED
    assert _state(tmp_path, {"other": [1]}).run_state is RunState.STOPPED


@pytest.mark.needs_session
def test_only_a_record_that_still_verifies_is_passed_to_status(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    p = subprocess.Popen(["sleep", "30"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):                                     # until /proc shows its identity
            ident = Lifecycle._proc_identity(p.pid)
            if ident and ident["exec"] == "sleep":
                break
            time.sleep(0.02)
        svc._lifecycle().record_launch(Stack(id="x", name="x", main="x-node"), COMP, p.pid,
                                       band="433")
        assert svc._lifecycle().owned_running() == {"x-node": [p.pid]}
        p.kill()
        p.wait(timeout=2)
        assert svc._lifecycle().owned_running() == {}                       # gone: the record no longer counts
    finally:
        if p.poll() is None:
            p.kill()

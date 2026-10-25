"""T-R6, the status rule across the update (A §2.3): a process named by a VERIFIED ownership record
counts as running although the probe's spec no longer matches its argv (a stack an older LHPC
started, before its argv gained an instance token)."""
import subprocess
import time

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import Component, ComponentKind, ProcessSpec, RunState, Stack
from lhpc.core.outcomes import Outcome
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


def _old_argv(svc, comp):
    """kiss-tnc's argv as an LHPC before L3 launched it: today's, without the instance token."""
    from lhpc.core import commands
    from lhpc.core.config import Config
    src = str(svc._paths.resolve_source(comp.source.path))
    argv = commands.expand_argv(comp.run_argv, comp, svc.stack_config(comp.id, "433"),
                                Config().operator, str(svc._paths.runtime_root), src, "433")
    i = argv.index(comp.process.instance_arg[0])
    return argv[:i] + argv[i + 2:]


def _box(tmp_path, monkeypatch):
    fake = FakeSystem()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "_ensure_daemon", lambda self, *a, **k: ([], True, ""))
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    return fake, svc, svc.stack("kiss"), svc.stack("kiss").component("loraham-kiss-tnc")


@pytest.mark.needs_session
def test_a_start_does_not_launch_a_second_one(tmp_path, monkeypatch):
    # T-R6 (A §2.3 across the update): a TNC an older LHPC started, recorded, whose argv lacks the
    # new instance token, counts as running, so a start does not launch a second one. At L3 status
    # still matches by the stack identity (exec + all_args), which this argv carries, so this holds
    # with or without the record rule; the rule (tests above) carries it once status matches by
    # instance token.
    fake, svc, stk, comp = _box(tmp_path, monkeypatch)
    p = subprocess.Popen(["sleep", "30"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            ident = Lifecycle._proc_identity(p.pid)
            if ident and ident["exec"] == "sleep":
                break
            time.sleep(0.02)
        svc._lifecycle().record_launch(stk, comp, p.pid, band="433")
        fake.cmdlines_data[p.pid] = _old_argv(svc, comp)
        launched = []
        monkeypatch.setattr(Lifecycle, "start", lambda self, s, c, *a, **k: launched.append(c.id))
        res = svc.start("kiss", apply=True, band="433")
        assert launched == []
        tnc = next(r for r in res.results if r.component == comp.id)
        assert tnc.outcome is Outcome.BLOCKED and "running" in tnc.summary, tnc.summary
    finally:
        p.kill()
        p.wait(timeout=2)


def test_the_next_start_renders_the_new_argv(tmp_path, monkeypatch):
    # With no such process, the start launches kiss-tnc with today's argv: the instance token in it.
    from lhpc.core.probes.process import has_instance_token
    _fake, svc, _stk, comp = _box(tmp_path, monkeypatch)
    spawned = []

    def spawn(self, argv, log, cwd=None, env=None):
        spawned.append(list(argv))
        raise OSError("captured (no process)")
    monkeypatch.setattr(Lifecycle, "_real_spawn", spawn)
    from lhpc.core import commands
    res = svc.start("kiss", apply=True, band="433")
    opt, tpl = comp.process.instance_arg
    value = commands.instance_value(tpl, comp, svc.stack_config(comp.id, "433"), str(tmp_path),
                                    str(svc._paths.resolve_source(comp.source.path)), "433")
    assert spawned and has_instance_token(spawned[0], opt, value), (value, spawned, res.summary)

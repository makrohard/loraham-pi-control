"""K8 (found by the web lane on 6f005b4a): a named instance stop never reports success over a process of the spec that
no instance claims. A kiss started by an older LHPC (its 0.13 band socket in the argv, no record) is no instance's;
`stop kiss@r1` matched nothing and said stopped, while the bare form refused MANUAL_REQUIRED and offered a kill."""
import radios_fixture

from lhpc.core import instances
from lhpc.core.commands import Instance
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, table):
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    return ControllerService(system=FakeSystem(cmdlines_data=table).system, paths=Paths(runtime_root=tmp_path))


def _tnc_argv(svc, extra):
    spec = svc.stack("kiss").component("loraham-kiss-tnc").process
    return [f"/x/{spec.exec_name}", *spec.all_args, *extra]


def _stop_outcome(svc):
    res = svc.stop("kiss", apply=True, inst=Instance("r1", band="433"))
    return next(r for r in res.results if r.component == "loraham-kiss-tnc")


def test_k8_a_named_stop_over_an_unclaimed_old_argv_is_manual_required(tmp_path):
    plain = _svc(tmp_path, {})
    svc = _svc(tmp_path, {300: _tnc_argv(plain, ["--data-socket", "/tmp/loradata433.sock"])})
    cr = _stop_outcome(svc)
    assert cr.outcome is Outcome.MANUAL_REQUIRED and cr.summary.endswith("stop it yourself: kill 300"), cr.summary


def test_k8_another_instances_own_process_is_no_orphan(tmp_path):
    plain = _svc(tmp_path, {})
    tnc = plain.stack("kiss").component("loraham-kiss-tnc")
    r2 = _tnc_argv(plain, instances._token(plain, tnc, Instance("r2", band="868"), "868"))
    svc = _svc(tmp_path, {301: r2})                                        # kiss@r2 runs, claimed by its token
    assert _stop_outcome(svc).outcome is Outcome.ALREADY_STOPPED


def test_k8_an_own_stop_beside_an_unclaimed_old_argv_is_manual_required(tmp_path, monkeypatch):
    # G1-K8A 5: kiss@r1's own process stops (the lifecycle's verified STOPPED), and a 0.13 kiss of the same band runs
    # unclaimed beside it: the stop names it, never a plain "stopped"
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.outcomes import CompResult
    plain = _svc(tmp_path, {})
    svc = _svc(tmp_path, {300: _tnc_argv(plain, ["--data-socket", "/tmp/loradata433.sock"])})
    monkeypatch.setattr(Lifecycle, "stop", lambda self, comp, **kw: CompResult(
        component=comp.id, action="stop", outcome=Outcome.STOPPED, summary="stopped", pid=200))
    cr = _stop_outcome(svc)
    assert cr.outcome is Outcome.MANUAL_REQUIRED, cr.summary
    assert cr.summary == ("stopped; a matching process is running but no instance claims it (no record, an older argv) "
                          "— stop it yourself: kill 300"), cr.summary


def test_k8_an_unclaimed_old_argv_of_another_band_is_not_this_instances(tmp_path):
    # G1-K8A 6: kiss@r1 is on 433; a leftover 0.13 kiss on 868's socket is not named by its stop
    plain = _svc(tmp_path, {})
    svc = _svc(tmp_path, {300: _tnc_argv(plain, ["--data-socket", "/tmp/loradata868.sock"])})
    assert _stop_outcome(svc).outcome is Outcome.ALREADY_STOPPED

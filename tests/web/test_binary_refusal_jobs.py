"""The typed `none_compatible` refusal travels from the detached web install job to the console (the job
result's `refusal` field)."""
import os

import pytest

from lhpc.core import jobresult, jobs, procident
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def _child(tmp_path, monkeypatch, install_result, *, dep_gate_blocked=False, extra=()):
    """Run the real web install CHILD (`lhpc install … --web-result`) against a stubbed install."""
    from lhpc.adapters.cli import main as cli_main
    svc = _svc(tmp_path)
    aid, web = "c" * 32, "install-daemon.log"
    ident = procident.proc_identity(os.getpid())
    assert jobs.write_job_marker(svc._paths, web, os.getpid(), "daemon", "install", ident=ident,
                                 attempt_id=aid)
    assert jobresult.reserve(svc._paths, web, aid, "install", "daemon", "daemon", [])
    seen = {}

    def fake_install(self, stack_id=None, apply=False, source="pinned", on_admit=None, **kw):
        seen.update(kw)
        if apply and on_admit:
            on_admit()
        return install_result
    monkeypatch.setattr(ControllerService, "install", fake_install)
    monkeypatch.setattr(cli_main, "_print_install_dep_gate",
                        lambda svc, stack, check=False: dep_gate_blocked)
    monkeypatch.setattr(cli_main, "ControllerService", lambda: svc)
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.5")
    source = "pinned" if dep_gate_blocked else "binary"
    cli_main.main(["install", "daemon", "--yes", "--source", source, "--web-result", web,
                   "--attempt-id", aid, *extra])
    return jobresult._read_raw(svc._paths, web), seen


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_web_install_job_result_carries_the_typed_refusal(tmp_path, monkeypatch):
    res = ActionResult(False, "Binary install of 'daemon' refused",
                       data={"binary_failed": True, "binary_refusal": "none_compatible"})
    d, _seen = _child(tmp_path, monkeypatch, res)
    assert d["state"] == "failed"
    assert d["refusal"] == "none_compatible"


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_a_generic_install_failure_carries_no_refusal(tmp_path, monkeypatch):
    d, _seen = _child(tmp_path, monkeypatch,
                      ActionResult(False, "download failed", data={"binary_failed": True}))
    assert d["state"] == "failed" and "refusal" not in d


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_web_install_dep_gate_blocked_terminalizes_without_refusal(tmp_path, monkeypatch):
    d, _seen = _child(tmp_path, monkeypatch, ActionResult(True, "x"), dep_gate_blocked=True)
    assert d["state"] == "failed" and "refusal" not in d


def test_spawn_web_job_puts_no_override_on_the_install_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = _svc(tmp_path)
    seen = {}

    def _spawn(self, name, argv, runtime):
        seen["argv"] = list(argv)
        return None, None                          # "could not start": the argv is what we check
    monkeypatch.setattr(type(svc._lifecycle()), "spawn_job", _spawn)
    svc.spawn_web_job("install", "daemon", source="binary")
    assert not any(a.startswith("--accept-pin-mismatch") for a in seen["argv"])  # rule-10: the option and its =value form are both absent


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_the_jobs_refusal_is_read_back_for_the_stack(tmp_path, monkeypatch):
    res = ActionResult(False, "Binary install of 'daemon' refused",
                       data={"binary_failed": True, "binary_refusal": "none_compatible"})
    _child(tmp_path, monkeypatch, res)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.binary_install_refusal("daemon") is True

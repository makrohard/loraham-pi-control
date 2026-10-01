"""A3: the typed pin refusal travels from the detached web install job to the console (the
job result's `refusal` field)."""
import os

import pytest

from lhpc.core import jobresult, jobs, procident
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

A, B = "a" * 40, "b" * 40


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
@pytest.mark.parametrize("refused", ["", "clone_required", "consent_stale"])
def test_web_install_job_result_carries_the_typed_refusal(tmp_path, monkeypatch, refused):
    res = ActionResult(False, "Binary install of 'daemon' refused",
                       data={"binary_failed": True, "pin_mismatch": {"loraham-daemon": [A, B]},
                             "override_refused": refused} if refused else
                       {"binary_failed": True, "pin_mismatch": {"loraham-daemon": [A, B]}})
    d, _seen = _child(tmp_path, monkeypatch, res)
    assert d["state"] == "failed"
    assert d["refusal"] == {"pin_mismatch": {"loraham-daemon": [A, B]},
                            "override_refused": refused}


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_a_generic_install_failure_carries_no_refusal(tmp_path, monkeypatch):
    d, _seen = _child(tmp_path, monkeypatch,
                      ActionResult(False, "download failed", data={"binary_failed": True}))
    assert d["state"] == "failed" and "refusal" not in d


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_web_install_dep_gate_blocked_terminalizes_without_refusal(tmp_path, monkeypatch):
    d, _seen = _child(tmp_path, monkeypatch, ActionResult(True, "x"), dep_gate_blocked=True)
    assert d["state"] == "failed" and "refusal" not in d


@pytest.mark.needs_session                 # the job marker carries this process's identity
def test_the_job_passes_the_consent_token_to_the_install(tmp_path, monkeypatch):
    token = "f" * 64
    _d, seen = _child(tmp_path, monkeypatch, ActionResult(True, "ok", data={"changes": 1}),
                      extra=[f"--accept-pin-mismatch={token}"])
    assert seen["accept_pin_mismatch"] == token


def test_spawn_web_job_puts_the_token_on_the_install_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = _svc(tmp_path)
    seen = {}

    def _spawn(self, name, argv, runtime):
        seen["argv"] = list(argv)
        return None, None                          # "could not start": the argv is what we check
    monkeypatch.setattr(type(svc._lifecycle()), "spawn_job", _spawn)
    svc.spawn_web_job("install", "daemon", source="binary", accept_pin_mismatch="f" * 64)
    assert "--accept-pin-mismatch=" + "f" * 64 in seen["argv"]
    seen.clear()
    svc.spawn_web_job("install", "daemon", source="binary")
    assert not any(a.startswith("--accept-pin-mismatch") for a in seen["argv"])



def test_a_stale_consent_with_no_mismatch_left_is_transported_typed():
    """Gate 2's P2: a stale consent whose mismatch has gone carries an EMPTY map; the job result
    keeps it (only for `consent_stale`), so the console can show the review-again notice."""
    ref = {"pin_mismatch": {}, "override_refused": "consent_stale"}
    assert jobresult.valid_refusal(ref)
    assert not jobresult.valid_refusal({"pin_mismatch": {}, "override_refused": ""})
    assert not jobresult.valid_refusal({"pin_mismatch": {}, "override_refused": "clone_required"})
    res = ActionResult(False, "x", data={"binary_failed": True, "pin_mismatch": {},
                                         "override_refused": "consent_stale"})
    assert jobresult.refusal_from(res) == ref

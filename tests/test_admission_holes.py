"""While ANOTHER service holds the task-admission lock (a stand-in for a self-update/uninstall in
progress), every newly-admitted task-start must return a TYPED refusal with zero mutation/spawn — never
a traceback/500. Covers install, auto-install spawn + applied driver, foreground HMAC, web-job spawn,
and self-update apply. A second ControllerService on the same runtime root contends on the same flock.
"""

import threading
from pathlib import Path

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


@pytest.fixture
def held_admission(tmp_path, monkeypatch):
    """Yields (svc_b) while a second service (svc_a) HOLDS admission on the same root."""
    monkeypatch.setattr(ControllerService, "_SELF_LOCK_WAIT_S", 0.2)   # fast contention
    a = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    a.bootstrap(apply=True)
    b = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    held, release = threading.Event(), threading.Event()

    def hold():
        with a._admission_guard("holder"):
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    assert held.wait(5)
    yield b
    release.set()
    t.join(5)


def _jobs(svc):
    d = svc._jobs_dir()
    return list(d.iterdir()) if d.exists() else []


def test_install_contends_typed(held_admission):
    r = held_admission.install("daemon", apply=True)
    assert not r.ok and (r.data.get("contended") or r.data.get("admission_blocked"))


def test_auto_install_driver_contends_typed(held_admission):
    r = held_admission.auto_install(apply=True, emit=lambda *_: None)
    assert not r.ok and (r.data.get("contended") or r.data.get("admission_blocked"))
    # zero reservation left behind
    assert not (held_admission._paths.under("state", "auto-install-start.json")).exists()


def test_auto_install_spawn_contends_typed(held_admission):
    log, error = held_admission.spawn_auto_install_job({"daemon": {"install": True}})
    assert log is None and error                # (log_name, error) tuple -> typed refusal, no spawn
    # zero reservation created under contention
    assert not (held_admission._paths.under("state", "auto-install-start.json")).exists()


def test_web_job_spawn_contends_typed(held_admission):
    log, admission, reason = held_admission.spawn_web_job("build", "daemon")
    assert log is None and admission == "blocked"


def test_hmac_cli_contends_typed(held_admission):
    msgs = []
    rc = held_admission.hmac_apply_cli("meshcom", "enable", emit=msgs.append)
    assert rc == 1


def test_self_update_apply_contends_typed(held_admission):
    r = held_admission.self_update_apply()
    assert not r.ok and (r.data.get("contended") or r.data.get("admission_blocked"))
    # no request/in-flight marker written
    assert not (held_admission._paths.under("state", "selfupdate.inflight")).exists()

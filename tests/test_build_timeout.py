"""Build/test timeouts must FAIL LOUD, and a killed build must never read "built".

Field-verified on a Pi Zero 2W: meshcore-pi's build (venv + pip install of 7 packages) overran the
600 s default and was silently TERM-killed mid-pip. The log ended with no error line, and because the
venv interpreter already existed, `is_built` read "built" — the failure only surfaced later as a
ModuleNotFoundError at start. This covers the fixes:

* every timed-out job writes an explicit "TIMED OUT after Ns" terminal line (log + tail);
* per-component build/test timeouts come from the manifest (hardware-realistic defaults otherwise);
* a `build_marker` written ONLY after the last step succeeds is what `is_built` gates on, so a
  half-built venv can never read "built".
"""

from pathlib import Path

from lhpc.core import lifecycle as lifecycle_mod
from lhpc.core.jobs import run_job, JobResult, JobState, tail_log
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))


def _meshcore(svc):
    return next(c for s in svc.stacks() for c in s.components if c.id == "meshcore-pi")


# --- explicit TIMED OUT terminal marker -----------------------------------------------------------

class _TimeoutRunner:
    def run(self, argv, timeout, cwd=None, env=None):
        return CommandResult(returncode=124, stdout="partial output\n", stderr="", timed_out=True)


def test_timed_out_job_writes_terminal_marker_to_log_and_tail(tmp_path):
    res = run_job(_TimeoutRunner(), name="build-x", argv=["slow"], cwd=None,
                  logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path), timeout=42.0)
    assert res.state is JobState.TIMEOUT
    # The log no longer just ends abruptly — it names WHY it stopped, with the timeout value.
    logged = "\n".join(tail_log(Path(res.log_path)))
    assert "TIMED OUT after 42s" in logged
    # ... and the tail carries it too (the run view / task banner shows the reason).
    assert any("TIMED OUT after 42s" in line for line in res.tail)


# --- per-component timeouts from the manifest -----------------------------------------------------

def _capture_run_job_timeout(monkeypatch, state=JobState.SUCCEEDED):
    seen = {}
    def fake(runner, **kw):
        seen["timeout"] = kw["timeout"]
        return JobResult(name=kw.get("name", "x"), state=state, returncode=0, log_path="", tail=[])
    monkeypatch.setattr(lifecycle_mod, "run_job", fake)
    return seen


def test_build_uses_manifest_build_timeout(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    assert comp.build_timeout == 1800.0        # declared for the slow venv+pip build
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().build(comp)
    assert seen["timeout"] == 1800.0           # honored, not the generic default


def test_host_test_uses_manifest_test_timeout(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    assert comp.test_timeout == 900.0 and comp.test_argv
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().host_test(comp)
    assert seen["timeout"] == 900.0


def test_default_build_timeout_is_hardware_realistic(tmp_path, monkeypatch):
    # A component WITHOUT a manifest override falls back to the class default (>= the old 600 s).
    svc = _svc(tmp_path)
    daemon = next(c for s in svc.stacks() for c in s.components if c.id == "loraham-daemon")
    assert daemon.build_timeout == 0.0
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().build(daemon)
    assert seen["timeout"] == lifecycle_mod.Lifecycle.BUILD_TIMEOUT_S >= 600.0


# --- completion marker: a killed build never reads "built" ----------------------------------------

def test_successful_build_stamps_marker_and_is_built_flips(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    src = svc._lifecycle().source_dir(comp)
    (src / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (src / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")   # interpreter exists (step 1 done)
    assert not svc.is_built(comp)                                  # ... but NOT built until the marker

    # All steps succeed -> build() stamps the marker -> is_built flips.
    monkeypatch.setattr(lifecycle_mod, "run_job",
                        lambda runner, **kw: JobResult(name="b", state=JobState.SUCCEEDED,
                                                       returncode=0, log_path="", tail=[]))
    res = svc._lifecycle().build(comp)
    assert res.ok
    assert (src / comp.build_marker).exists()
    assert svc.is_built(comp)


def test_rebuild_removes_stale_marker_before_running(tmp_path, monkeypatch):
    # A previously-built tree carries the marker; a re-build that then FAILS must not leave it behind
    # (else is_built would keep reporting the now-broken tree as built).
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    src = svc._lifecycle().source_dir(comp)
    (src / ".venv").mkdir(parents=True, exist_ok=True)
    (src / comp.build_marker).write_text("stale\n")
    assert svc.is_built(comp)

    monkeypatch.setattr(lifecycle_mod, "run_job",
                        lambda runner, **kw: JobResult(name="b", state=JobState.FAILED,
                                                       returncode=1, log_path="", tail=["boom"]))
    res = svc._lifecycle().build(comp)
    assert not res.ok
    assert not (src / comp.build_marker).exists()   # cleared up front -> is_built now False
    assert not svc.is_built(comp)


# --- runner PATH includes ~/.local/bin (pipx tools findable under the service) --------------------

def test_runner_path_appends_local_bin(monkeypatch):
    # The meshcom firmware build calls pipx-installed `pio` (~/.local/bin). The systemd --user service
    # inherits a PATH without ~/.local/bin, so the runner PATH must add it — else `pio` is found in the
    # operator's shell yet not under lhpc (same class as the qemu ~/.espressif mismatch).
    from lhpc.core.probes import backends
    monkeypatch.setenv("HOME", "/home/operator")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    path = backends._runner_path()
    assert "/home/operator/.local/bin" in path.split(":")
    assert "/usr/bin" in path.split(":")              # system entries preserved
    # System tools still win (append, not prepend).
    assert path.split(":").index("/usr/bin") < path.split(":").index("/home/operator/.local/bin")


def test_runner_path_no_duplicate_local_bin(monkeypatch):
    from lhpc.core.probes import backends
    monkeypatch.setenv("HOME", "/home/operator")
    monkeypatch.setenv("PATH", "/home/operator/.local/bin:/usr/bin")
    assert backends._runner_path().split(":").count("/home/operator/.local/bin") == 1

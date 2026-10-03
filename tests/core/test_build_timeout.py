"""Build/test timeouts must FAIL LOUD, and a killed build must never read "built".

Field-verified on a Pi Zero 2W: meshcore-pi's build (venv + pip install of 7 packages) overran the
600 s default and was silently TERM-killed mid-pip. The log ended with no error line, and because the
venv interpreter already existed, `is_built` read "built" — the failure only surfaced later as a
ModuleNotFoundError at start. This covers the fixes:

* every timed-out job writes an explicit "TIMED OUT after Ns" terminal line (log + tail);
* per-component test timeouts come from the manifest (hardware-realistic defaults otherwise); a build
  step ends on a stall or at the 24 h runaway guard instead (F42);
* a `build_marker` written ONLY after the last step succeeds is what `is_built` gates on, so a
  half-built venv can never read "built".
"""

from pathlib import Path

import pytest

from lhpc.core import lifecycle as lifecycle_mod
from lhpc.core import progress
from lhpc.core.jobs import JobResult, JobState, run_job, tail_log
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))


def _meshcore(svc):
    return next(c for s in svc.stacks() for c in s.components if c.id == "meshcore-node")


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
        seen["stall_s"] = kw.get("stall_s")
        seen["n"] = seen.get("n", 0) + 1
        return JobResult(name=kw.get("name", "x"), state=state, returncode=0, log_path="", tail=[])
    monkeypatch.setattr(lifecycle_mod, "run_job", fake)
    return seen


def test_build_gets_the_stall_rule_and_the_runaway_guard(tmp_path, monkeypatch):
    # F42: a build step ends after 10 min without activity, or at the 24 h guard — a manifest value
    # below the guard no longer cuts a slow build short.
    monkeypatch.delenv("LHPC_BUILD_STEP_TIMEOUT_S", raising=False)
    monkeypatch.delenv("LHPC_BUILD_STALL_S", raising=False)
    svc = _svc(tmp_path)
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().build(_meshcore(svc))
    assert seen["timeout"] == progress.BUILD_CEILING_S == 86400.0
    assert seen["stall_s"] == progress.STALL_S == 600.0


def test_host_test_uses_manifest_test_timeout(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    assert comp.test_timeout == 900.0 and comp.test_argv
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().host_test(comp)
    assert seen["timeout"] == 900.0


def test_no_shipped_build_timeout_sits_below_the_guard(tmp_path):
    # Under max(manifest, 24 h) a smaller value is inert and only misleads a reader (Q1).
    svc = _svc(tmp_path)
    low = {c.id: c.build_timeout for s in svc.stacks() for c in s.components
           if 0 < c.build_timeout <= progress.BUILD_CEILING_S}
    assert low == {}


def test_default_build_ceiling_is_the_runaway_guard(tmp_path, monkeypatch):
    # A component WITHOUT a manifest value gets the same 24 h guard and stall rule.
    monkeypatch.delenv("LHPC_BUILD_STEP_TIMEOUT_S", raising=False)
    monkeypatch.delenv("LHPC_BUILD_STALL_S", raising=False)
    svc = _svc(tmp_path)
    daemon = next(c for s in svc.stacks() for c in s.components if c.id == "loraham-daemon")
    assert daemon.build_timeout == 0.0
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().build(daemon)
    assert seen["timeout"] == progress.BUILD_CEILING_S and seen["stall_s"] == progress.STALL_S


@pytest.mark.parametrize("var", ["LHPC_BUILD_STALL_S", "LHPC_BUILD_STEP_TIMEOUT_S"])
@pytest.mark.parametrize("bad", ["0", "inf", "x"])
def test_malformed_env_fails_typed(tmp_path, monkeypatch, var, bad):
    # A malformed limit never means "no limit": the build is refused before any step.
    monkeypatch.setenv(var, bad)
    svc = _svc(tmp_path)
    seen = _capture_run_job_timeout(monkeypatch)
    res = svc._lifecycle().build(_meshcore(svc))
    assert res.state is JobState.FAILED and "n" not in seen
    assert "invalid build time limit" in " ".join(res.tail)


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf")])
def test_malformed_explicit_timeout_fails_typed(tmp_path, monkeypatch, bad):
    # DELTA 1: a caller's explicit value goes through the same check as the env.
    svc = _svc(tmp_path)
    seen = _capture_run_job_timeout(monkeypatch)
    res = svc._lifecycle().build(_meshcore(svc), timeout=bad)
    assert res.state is JobState.FAILED and "n" not in seen


def test_host_test_has_no_stall_rule(tmp_path, monkeypatch):
    # preservation (Q2): a host test keeps its plain wall-clock timeout, without the stall rule.
    monkeypatch.setenv("LHPC_BUILD_STALL_S", "60")
    svc = _svc(tmp_path)
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().host_test(_meshcore(svc))
    assert seen["timeout"] == 900.0 and seen["stall_s"] is None


@pytest.mark.slow
def test_busy_build_outlives_its_manifest_value(tmp_path, monkeypatch):
    # A step that keeps computing for 3 s is not ended by its 1 s manifest value: only a stall
    # (1 s here) or the 24 h guard would end it.
    from lhpc.core.probes.backends import RealCommandRunner
    monkeypatch.delenv("LHPC_BUILD_STEP_TIMEOUT_S", raising=False)
    monkeypatch.delenv("LHPC_BUILD_STALL_S", raising=False)
    monkeypatch.setattr(progress, "STALL_S", 1.0)
    monkeypatch.setattr(progress, "SAMPLE_S", 0.2)
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    loop = "import time\nt = time.time()\nwhile time.time() - t < 3: pass"
    object.__setattr__(comp, "build_timeout", 1.0)
    object.__setattr__(comp, "build_steps", ({"argv": ["python3", "-c", loop]},))
    object.__setattr__(comp, "build_marker", "")
    life = svc._lifecycle()
    life.system = type("S", (), {"runner": RealCommandRunner()})()
    life.source_dir(comp).mkdir(parents=True, exist_ok=True)
    res = life.build(comp)
    assert res.state is JobState.SUCCEEDED, res.tail


# --- completion marker: a killed build never reads "built" ----------------------------------------

def _receipt(svc, comp) -> str:
    """The exact marker content is_built expects: the static text plus the consumed-source lines
    (meshcore-node consumes the pinned repeater checkout via build_requires)."""
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT
    return BUILD_MARKER_TEXT + svc._consumed_source_lines(comp)


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
    # The lifecycle is driven directly: the public svc.build refuses meshcore-node until its build
    # dependency (the repeater checkout) is installed and owned, which is not this test's subject.
    # The two arguments are what svc.build hands the lifecycle.
    res = svc._lifecycle().build(comp, marker_extra=svc._consumed_source_lines(comp),
                                 inputs=svc._build_inputs_to_record(comp))
    assert res.ok
    assert (src / comp.build_marker).exists()
    assert svc.is_built(comp)


def test_rebuild_removes_stale_marker_before_running(tmp_path, monkeypatch):
    # A previously-built tree carries the marker; a re-build that then FAILS must not leave it behind
    # (else is_built would keep reporting the now-broken tree as built).
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    src = svc._lifecycle().source_dir(comp)
    (src / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (src / comp.build_marker).write_text(_receipt(svc, comp))
    svc.build_inputs_path(comp).write_text(svc.build_inputs_text(comp))
    assert svc.is_built(comp)

    monkeypatch.setattr(lifecycle_mod, "run_job",
                        lambda runner, **kw: JobResult(name="b", state=JobState.FAILED,
                                                       returncode=1, log_path="", tail=["boom"]))
    res = svc._lifecycle().build(comp, marker_extra=svc._consumed_source_lines(comp))
    assert not res.ok
    assert not (src / comp.build_marker).exists()   # cleared up front -> is_built now False
    assert not svc.is_built(comp)


def test_meshcore_cli_declares_build_budget_and_marker(tmp_path):
    # meshcore-cli's `pip install .` is the same slow venv build as its siblings: it gets their limits
    # (the stall rule and the 24 h guard, F42). And `.venv/bin/meshcli` appears after pip but
    # before the last step, so the marker, not `bin`, decides "built".
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.id == "meshcore-cli")
    assert progress.build_limits(comp.build_timeout, {}) == (600.0, 86400.0)
    assert comp.build_marker == ".venv/.lhpc-build-complete"
    src = svc._lifecycle().source_dir(comp)
    (src / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (src / ".venv" / "bin" / "meshcli").write_text("#!/bin/sh\n")   # pip finished, compileall not
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


# --- MeshCom realistic timeout + firmware-co-located completion marker ---------------------

def _meshcom(svc):
    return next(c for s in svc.stacks() for c in s.components if c.id == "meshcom-qemu")


def _flash_dir(svc, comp):
    # The build_steps hardcode --env qemu-headless-extradio-gpsd, so the firmware + marker live here.
    return svc._lifecycle().source_dir(comp) / ".work/MeshCom-Firmware/.pio/build/qemu-headless-extradio-gpsd"


def test_meshcom_timeout_exceeds_measured_cold_build(tmp_path, monkeypatch):
    # Cold `pio` build is ~26 min (~1560 s) on a Zero 2W; the 24 h guard clears it by far.
    monkeypatch.delenv("LHPC_BUILD_STEP_TIMEOUT_S", raising=False)
    svc = _svc(tmp_path)
    comp = _meshcom(svc)
    seen = _capture_run_job_timeout(monkeypatch)
    svc._lifecycle().build(comp)
    assert seen["timeout"] == progress.BUILD_CEILING_S > 1560.0


def test_meshcom_marker_is_colocated_with_flash_and_gates_is_built(tmp_path):
    # A stale flash.bin with NO completion marker (a failed/interrupted rebuild) must read NOT built.
    svc = _svc(tmp_path)
    comp = _meshcom(svc)
    assert comp.build_marker.endswith("qemu-headless-extradio-gpsd/.lhpc-build-complete")
    fd = _flash_dir(svc, comp); fd.mkdir(parents=True, exist_ok=True)
    (fd / "flash.bin").write_text("stale firmware\n")           # artifact present...
    assert not svc.is_built(comp)                               # ...but no marker -> NOT built
    # Removing/cleaning the firmware dir takes the marker with it (co-located): still not built.
    (svc._lifecycle().source_dir(comp) / comp.build_marker).write_text("lhpc build complete\n")
    svc.build_inputs_path(comp).write_text(svc.build_inputs_text(comp))   # 0.7.0 sidecar
    assert svc.is_built(comp)
    import shutil
    shutil.rmtree(fd)                                           # clean the firmware artifact + its marker
    assert not svc.is_built(comp)


def test_meshcom_successful_build_stamps_marker_only_after_last_step(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    comp = _meshcom(svc)
    fd = _flash_dir(svc, comp); fd.mkdir(parents=True, exist_ok=True)
    (fd / "flash.bin").write_text("firmware\n")
    marker = svc._lifecycle().source_dir(comp) / comp.build_marker
    steps_run = {"n": 0}
    def _fake_run_job(runner, **kw):
        steps_run["n"] += 1
        assert not marker.exists()          # marker must NOT exist during any step (only after the last)
        return JobResult(name="b", state=JobState.SUCCEEDED, returncode=0, log_path="", tail=[])
    monkeypatch.setattr(lifecycle_mod, "run_job", _fake_run_job)
    res = svc.build(comp.id, apply=True)
    assert res.ok and steps_run["n"] == len(comp.build_steps), res.summary   # every step ran
    assert marker.exists() and svc.is_built(comp)               # stamped only after the last step


def test_meshcom_failed_rebuild_leaves_no_marker(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    comp = _meshcom(svc)
    fd = _flash_dir(svc, comp); fd.mkdir(parents=True, exist_ok=True)
    (fd / "flash.bin").write_text("stale firmware\n")
    marker = svc._lifecycle().source_dir(comp) / comp.build_marker
    marker.write_text("lhpc build complete\n")                                # a prior build's marker
    svc.build_inputs_path(comp).write_text(svc.build_inputs_text(comp))     # ... and its sidecar (0.7.0)
    assert svc.is_built(comp)
    monkeypatch.setattr(lifecycle_mod, "run_job",
                        lambda runner, **kw: JobResult(name="b", state=JobState.FAILED,
                                                       returncode=1, log_path="", tail=["boom"]))
    res = svc._lifecycle().build(comp)
    assert not res.ok and not marker.exists() and not svc.is_built(comp)   # stale flash != built


# --- strict marker semantics (is_built regular-file+content; fail-closed invalidation) ----

import os as _os

import lhpc.core.runtime_fs as _rfs


def _mk(svc, comp):
    m = svc._lifecycle().source_dir(comp) / comp.build_marker
    m.parent.mkdir(parents=True, exist_ok=True)
    return m


def _counting_run_job(counter):
    def _rj(runner, **kw):
        counter["n"] += 1
        return JobResult(name="b", state=JobState.SUCCEEDED, returncode=0, log_path="", tail=[])
    return _rj


def test_is_built_missing_marker_is_not_built(tmp_path):
    svc = _svc(tmp_path); comp = _meshcore(svc)
    svc._lifecycle().source_dir(comp).mkdir(parents=True, exist_ok=True)
    assert not svc.is_built(comp)


def test_is_built_requires_exact_regular_marker_content(tmp_path):
    svc = _svc(tmp_path); comp = _meshcore(svc); m = _mk(svc, comp)
    side = svc.build_inputs_path(comp)                                          # 0.7.0 sidecar
    side.parent.mkdir(parents=True, exist_ok=True); side.write_text(svc.build_inputs_text(comp))
    m.write_text(_receipt(svc, comp));      assert svc.is_built(comp)           # exact -> built
    m.write_text(_receipt(svc, comp)[:-1]); assert not svc.is_built(comp)       # missing newline
    m.write_text("wrong\n");                assert not svc.is_built(comp)       # wrong content
    m.write_text("");                        assert not svc.is_built(comp)      # empty


def _oversize_marker(m):
    m.write_text("lhpc build complete\n" + "x" * 200)


def _directory_marker(m):
    m.mkdir()


def _fifo_marker(m):
    _os.mkfifo(m)


def _symlink_marker_to_valid_file(m):
    real = m.parent / "real-marker"; real.write_text("lhpc build complete\n")
    m.symlink_to(real)                                       # symlink -> a VALID regular file


@pytest.mark.parametrize("make_bad", [
    pytest.param(_oversize_marker, id="test_is_built_rejects_oversize_marker"),
    pytest.param(_directory_marker, id="test_is_built_rejects_directory_marker"),
    pytest.param(_fifo_marker, id="test_is_built_rejects_fifo_marker"),
    pytest.param(_symlink_marker_to_valid_file,
                 id="test_is_built_rejects_symlink_marker_even_to_valid_file"),
])
def test_is_built_rejects_bad_marker(tmp_path, make_bad):
    svc = _svc(tmp_path); comp = _meshcore(svc); m = _mk(svc, comp)
    make_bad(m)
    assert not svc.is_built(comp)                            # ...still NOT built (non-regular leaf)


def test_build_fails_closed_when_marker_is_symlink_and_runs_no_step(tmp_path, monkeypatch):
    svc = _svc(tmp_path); comp = _meshcore(svc); m = _mk(svc, comp)
    real = m.parent / "real-marker"; real.write_text("lhpc build complete\n"); m.symlink_to(real)
    ran = {"n": 0}
    monkeypatch.setattr(lifecycle_mod, "run_job", _counting_run_job(ran))
    res = svc._lifecycle().build(comp)
    assert not res.ok and ran["n"] == 0                     # typed failure, ZERO steps executed
    assert "marker" in " ".join(res.tail).lower()
    assert m.is_symlink() and real.exists()                 # unsafe marker never followed/removed


def test_build_fails_closed_when_marker_is_directory_and_runs_no_step(tmp_path, monkeypatch):
    svc = _svc(tmp_path); comp = _meshcore(svc); m = _mk(svc, comp); m.mkdir()
    ran = {"n": 0}
    monkeypatch.setattr(lifecycle_mod, "run_job", _counting_run_job(ran))
    res = svc._lifecycle().build(comp)
    assert not res.ok and ran["n"] == 0 and "marker" in " ".join(res.tail).lower()


def test_build_fails_closed_on_marker_permission_error_and_runs_no_step(tmp_path, monkeypatch):
    svc = _svc(tmp_path); comp = _meshcore(svc); m = _mk(svc, comp)
    m.write_text("lhpc build complete\n")
    def _boom(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(_rfs, "unlink", _boom)              # invalidation removal fails
    ran = {"n": 0}
    monkeypatch.setattr(lifecycle_mod, "run_job", _counting_run_job(ran))
    res = svc._lifecycle().build(comp)
    assert not res.ok and ran["n"] == 0                     # typed failure BEFORE any step
    assert "marker" in " ".join(res.tail).lower()


def test_is_built_never_raises_on_any_bad_marker(tmp_path):
    # Web-safety: a bad marker of ANY kind reads as not-built, never a traceback / 500.
    svc = _svc(tmp_path); comp = _meshcore(svc)
    for make in (lambda m: m.mkdir(),
                 lambda m: _os.mkfifo(m),
                 lambda m: m.symlink_to(m.parent / "nope"),
                 lambda m: m.write_text("x" * 5000)):
        m = svc._lifecycle().source_dir(comp) / comp.build_marker
        if m.exists() or m.is_symlink():
            if m.is_dir() and not m.is_symlink():
                import shutil; shutil.rmtree(m)
            else:
                m.unlink()
        m.parent.mkdir(parents=True, exist_ok=True)
        make(m)
        assert svc.is_built(comp) is False                  # bounded bool, no exception


# --- a local write failure is not evidence against the recipe -------------------------------------

def test_marker_write_failure_carries_no_step_identity(tmp_path, monkeypatch):
    """Every command succeeded and a LOCAL write failed. The release lane decides attribution from
    the failed job's log identity — a failure naming a step the recipe declares its own becomes an
    upstream regression and freezes that stack's pins. Reusing the last SUCCESSFUL step's log here
    let a full disk do exactly that: reproduced against Reticulum, it held five upstream inputs
    that had built perfectly.
    """
    svc = _svc(tmp_path)
    comp = _meshcore(svc)
    src = svc._lifecycle().source_dir(comp)
    (src / ".venv" / "bin").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(lifecycle_mod, "run_job",
                        lambda runner, **kw: JobResult(name="b", state=JobState.SUCCEEDED,
                                                       returncode=0,
                                                       log_path="/logs/build-meshcore-node-4.log",
                                                       tail=[]))

    def full_disk(paths, path, text, mode):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(lifecycle_mod.runtime_fs, "atomic_write", full_disk)
    res = svc._lifecycle().build(comp, marker_extra=svc._consumed_source_lines(comp))

    assert not res.ok, "an unstamped tree must never read built"
    assert res.log_path == "", \
        "a marker-write failure must not borrow a build step's identity — that is what attributes"
    assert any("completion marker could not be written" in ln for ln in res.tail)
    assert any("build-meshcore-node-4.log" in ln for ln in res.tail), \
        "the step log must stay discoverable as a diagnostic"
    assert not (src / comp.build_marker).exists()

"""§5/§10 — the detached build/test launcher's security behavior lives in the SHARED
`build_launcher_runtime` module (unit-tested here); the generated launcher is a thin wrapper.
Lock/journal access is descriptor-safe (Paths rebuild + full parent no-follow walk): a
symlinked/replaced parent ANYWHERE in the lock/journal path fails closed before source access."""

import os

import pytest

from lhpc.core import build_launcher_runtime as blr
from lhpc.core import commands
from lhpc.core.paths import Paths


def _spec(tmp_path, *, steps=(), lock_names=(), index="", ):
    return {"steps": list(steps), "cwd": str(tmp_path), "runtime_root": str(tmp_path / "rt"),
            "lock_names": list(lock_names), "index_lock_name": index}


def _locks_dir(tmp_path):
    d = Paths(runtime_root=tmp_path / "rt").under("state", "locks")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _txn_dir(tmp_path):
    d = Paths(runtime_root=tmp_path / "rt").under("state", "source-txn")
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_generated_launcher_is_thin():
    script = commands.render_build_launcher([{"argv": ["true"]}], "/rt", "/src")
    assert "build_launcher_runtime.run(" in script          # delegates to the shared module
    for banned in ("flock", "killpg", "pkg-config", "O_NOFOLLOW", "terminate_session",
                   "LOCK_EX", "Popen", "start_new_session"):
        assert banned not in script


def test_run_blocks_on_symlinked_lock_leaf(tmp_path):
    d = _locks_dir(tmp_path)
    outside = tmp_path / "target"; outside.write_text("x")
    os.symlink(outside, d / "src.lock")                     # symlinked lock LEAF
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path, lock_names=["src.lock"]))
    assert e.value.code == 3


def test_run_blocks_on_symlinked_lock_parent(tmp_path):
    # §5: a symlinked PARENT (state/locks -> outside) fails closed, not just a symlinked leaf.
    rt = Paths(runtime_root=tmp_path / "rt")
    rt.under("state").mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil"; outside.mkdir(); (outside / "src.lock").write_text("x")
    os.symlink(outside, rt.under("state", "locks"))         # symlinked lock PARENT
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path, lock_names=["src.lock"]))
    assert e.value.code == 3


def test_run_blocks_on_symlinked_index_parent(tmp_path):
    rt = Paths(runtime_root=tmp_path / "rt")
    rt.under("state").mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "evil"; outside.mkdir()
    os.symlink(outside, rt.under("state", "locks"))         # symlinked index-lock PARENT
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path, index="index.lock"))
    assert e.value.code == 3


def test_run_blocks_on_symlinked_journal_parent(tmp_path):
    # index lock is real; the source-txn PARENT is a symlink -> journal scan fails closed.
    _locks_dir(tmp_path)
    rt = Paths(runtime_root=tmp_path / "rt")
    outside = tmp_path / "elsewhere"; outside.mkdir()
    os.symlink(outside, rt.under("state", "source-txn"))    # symlinked journal-dir PARENT
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path, index="index.lock"))
    assert e.value.code == 3


def test_run_blocks_on_unresolved_journal(tmp_path):
    _locks_dir(tmp_path)
    (_txn_dir(tmp_path) / "pending.json").write_text("{}")
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path, index="index.lock"))
    assert e.value.code == 3


def test_run_malformed_timeout_fails_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("LHPC_BUILD_STEP_TIMEOUT_S", "not-a-number")
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path))
    assert e.value.code == 3                                # never unlimited


def test_run_executes_step_and_releases_locks(tmp_path):
    _locks_dir(tmp_path)
    marker = tmp_path / "ran"
    blr.run(_spec(tmp_path, steps=[{"argv": ["touch", str(marker)], "env": {}}],
                  lock_names=["src.lock"]))
    assert marker.exists()
    # lock released -> a fresh flock succeeds (no lingering hold)
    lf = _locks_dir(tmp_path) / "src.lock"
    import fcntl
    with open(lf, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)      # would raise if still held


def test_run_step_failure_propagates_exit_code(tmp_path):
    _locks_dir(tmp_path)
    with pytest.raises(SystemExit) as e:
        blr.run(_spec(tmp_path, steps=[{"argv": ["false"], "env": {}}], lock_names=["src.lock"]))
    assert e.value.code == 1


# --- Item 6: build children are biased toward the OOM killer (protect the lhpc-web controller) ------

def test_oom_score_adj_default_and_override_and_clamp(monkeypatch):
    monkeypatch.delenv("LHPC_BUILD_OOM_SCORE_ADJ", raising=False)
    assert blr._build_child_oom_score_adj() == 500                      # positive default
    monkeypatch.setenv("LHPC_BUILD_OOM_SCORE_ADJ", "250")
    assert blr._build_child_oom_score_adj() == 250
    monkeypatch.setenv("LHPC_BUILD_OOM_SCORE_ADJ", "5000")
    assert blr._build_child_oom_score_adj() == 1000                     # clamped to kernel max
    monkeypatch.setenv("LHPC_BUILD_OOM_SCORE_ADJ", "nonsense")
    assert blr._build_child_oom_score_adj() is None                     # bad value -> leave unchanged


def test_run_step_spawns_child_with_oom_preexec(monkeypatch, tmp_path):
    # The build child is spawned with a preexec_fn that raises its oom_score_adj — proving the wiring
    # without depending on kernel permissions.
    captured = {}
    real_popen = blr.subprocess.Popen

    class _Fake:
        def __init__(self, *a, **k):
            captured.update(k)
            self._p = real_popen(["true"])
            self.pid = self._p.pid
        def wait(self, timeout=None):
            return self._p.wait(timeout=timeout)
    monkeypatch.setattr(blr.subprocess, "Popen", _Fake)
    monkeypatch.setattr(blr.proctree, "capture_session_token", lambda pid: None)
    blr._run_step(["true"], str(tmp_path), {}, 30.0)
    assert captured.get("preexec_fn") is blr._bias_child_oom
    assert captured.get("start_new_session") is True


def _oom_writable() -> bool:
    import subprocess
    return subprocess.run(["sh", "-c", "echo 500 > /proc/self/oom_score_adj"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _oom_writable(), reason="oom_score_adj not writable in this environment")
def test_oom_preexec_actually_raises_child_score(tmp_path):
    # A REAL child spawned via _run_step ends up with the raised oom_score_adj (proves the preexec runs
    # in the child before exec). The child records its own score to a file.
    out = tmp_path / "score"
    blr._run_step(["sh", "-c", f"cat /proc/self/oom_score_adj > {out}"], str(tmp_path), {}, 30.0)
    assert out.read_text().strip() == "500"

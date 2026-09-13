"""§5/§10 — the detached build/test launcher's security behavior lives in the SHARED
`build_launcher_runtime` module (unit-tested here); the generated launcher is a thin wrapper.
Lock/journal access is descriptor-safe (Paths rebuild + full parent no-follow walk): a
symlinked/replaced parent ANYWHERE in the lock/journal path fails closed before source access."""

import fcntl
import os
import subprocess
import sys
import time

import pytest

from lhpc.core import build_launcher_runtime as blr
from lhpc.core import commands, reslock
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


# --- build children are biased toward the OOM killer (protect the lhpc-web controller) ------

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

def test_render_carries_substituted_announce_and_run_prints_it_before_argv(tmp_path, capfd):
    # Parity for the detached web build: the render-time-substituted `announce` is
    # printed BEFORE the `+ argv` echo, so the step log leads with what is happening.
    script = commands.render_build_launcher(
        [{"argv": ["true"], "announce": "[resolve] watch {runtime}/core grow"}], "/rt", "/src")
    assert "'announce': '[resolve] watch /rt/core grow'" in script
    _locks_dir(tmp_path)
    blr.run(_spec(tmp_path, steps=[{"argv": ["true"], "env": {},
                                    "announce": "[resolve] quiet step"}]))
    out = capfd.readouterr().out
    assert out.index("[resolve] quiet step") < out.index("+ true")


def test_run_step_without_announce_unchanged(tmp_path, capfd):
    _locks_dir(tmp_path)
    blr.run(_spec(tmp_path, steps=[{"argv": ["true"], "env": {}}]))
    assert "[resolve]" not in capfd.readouterr().out


# ---- build-completion marker (receipt) -------------------------------------

def _marker_spec(tmp_path, *, steps, marker_text="lhpc build complete\nconsumed x abc\n"):
    rt = tmp_path / "rt"
    marker = rt / "src" / "x" / ".venv" / ".lhpc-build-complete"
    marker.parent.mkdir(parents=True, exist_ok=True)
    spec = _spec(tmp_path, steps=steps)
    spec["marker_path"] = str(marker)
    spec["marker_text"] = marker_text
    return spec, marker


def test_detached_build_replaces_a_stale_receipt(tmp_path):
    """The web Build bypassed lifecycle.build(), so the steps succeeded, the job read
    done — and the stale marker survived: the component still read NOT built and the
    advertised web workflow could never complete a rebuild after source drift."""
    receipt = "lhpc build complete\nconsumed rns aaa\nconsumed rns-lora-interface bbb\n"
    spec, marker = _marker_spec(tmp_path, steps=[{"argv": ["true"]}], marker_text=receipt)
    marker.write_text("lhpc build complete\nconsumed rns OLD\n")   # stale receipt
    blr.run(spec)
    assert marker.read_text() == receipt, "the marker must be the EXACT current receipt"


def test_a_failed_detached_build_leaves_no_marker(tmp_path):
    """Invalidate before step one, write only after every step passes: a failed build
    must leave the marker ABSENT, never the stale one and never a fresh one."""
    spec, marker = _marker_spec(tmp_path, steps=[{"argv": ["false"]}])
    marker.write_text("lhpc build complete\n")                     # pre-receipt marker
    with pytest.raises(SystemExit) as e:
        blr.run(spec)
    assert e.value.code != 0
    assert not marker.exists(), "a failed build must not leave any completion marker"


def test_an_unsafe_marker_aborts_before_any_step(tmp_path):
    """A symlinked marker must abort the job before the first step runs — removing
    through it could delete a file outside the build tree."""
    spec, marker = _marker_spec(tmp_path, steps=[{"argv": ["touch", str(tmp_path / "ran")]}])
    outside = tmp_path / "outside"; outside.write_text("keep me")
    marker.symlink_to(outside)
    with pytest.raises(SystemExit):
        blr.run(spec)
    assert not (tmp_path / "ran").exists(), "no step may run behind an unsafe marker"
    assert outside.read_text() == "keep me"


# ---- the generated launcher as a REAL sub-process: lock contention and step timeouts ----------------
# The runtime module above is unit-tested in process; these prove the rendered script, run as the
# detached job would run it, contends for the same flock files the controller uses.

def _launch(launcher, **env):
    return subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True,
                          env={**os.environ, **env})


def test_build_launcher_acquires_and_blocks_on_source_lock(tmp_path):
    rt = tmp_path / "rt"
    locks = Paths(runtime_root=rt).under("state", "locks"); locks.mkdir(parents=True, exist_ok=True)
    lock = locks / "src.lock"; lock.touch()           # the runtime-structured source lock
    marker = tmp_path / "ran"
    # A step that creates a marker so we can prove it ran only when unlocked.
    steps = [{"argv": ["touch", str(marker)]}]
    script = commands.render_build_launcher(steps, str(rt), str(tmp_path), [str(lock)])
    launcher = tmp_path / "launch.py"; launcher.write_text(script)

    # 1) lock HELD by us -> launcher must fail fast (exit 3) and not run the step.
    fd = os.open(str(lock), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = _launch(launcher, LHPC_BUILD_LOCK_WAIT_S="0.4")
        assert r.returncode == 3 and "could not acquire source lock" in r.stderr
        assert not marker.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    # 2) lock FREE -> launcher acquires it, runs the step, exits 0.
    r = _launch(launcher)
    assert r.returncode == 0 and marker.exists()


def test_build_launcher_lock_contends_with_operation_lock(tmp_path):
    # The launcher's lock file is the SAME one reslock.operation_lock uses.
    paths = Paths(runtime_root=tmp_path)
    paths.under("state", "locks").mkdir(parents=True, exist_ok=True)
    lp = str(reslock.lock_file_path(paths, reslock.source_lock_key("src/app")))
    script = commands.render_build_launcher([{"argv": ["true"]}], str(tmp_path),
                                            str(tmp_path), [lp])
    launcher = tmp_path / "l.py"; launcher.write_text(script)
    with reslock.operation_lock(paths, reslock.source_lock_key("src/app"), "update", "x"):
        r = _launch(launcher, LHPC_BUILD_LOCK_WAIT_S="0.4")
    assert r.returncode == 3 and "another source operation is in progress" in r.stderr


def _render_launcher(tmp_path, marker, with_journal):
    paths = Paths(runtime_root=tmp_path)
    paths.under("state", "locks").mkdir(parents=True, exist_ok=True)
    txn = paths.under("state", "source-txn"); txn.mkdir(parents=True, exist_ok=True)
    if with_journal:
        (txn / "garbage.json").write_text("{ unresolved")
    idx = str(reslock.lock_file_path(paths, "source-txn-index"))
    script = commands.render_build_launcher([{"argv": ["touch", str(marker)]}], str(tmp_path),
                                            str(tmp_path), [], index_lock=idx)
    launcher = tmp_path / "l.py"; launcher.write_text(script)
    return launcher


def test_detached_launcher_blocks_on_pending_journal(tmp_path):
    marker = tmp_path / "ran"
    launcher = _render_launcher(tmp_path, marker, with_journal=True)
    r = _launch(launcher, LHPC_BUILD_LOCK_WAIT_S="0.4")
    assert r.returncode == 3 and "unresolved source-transaction journal" in r.stderr
    assert not marker.exists()                    # never touched the source


def test_detached_launcher_blocks_while_index_held(tmp_path):
    marker = tmp_path / "ran"
    launcher = _render_launcher(tmp_path, marker, with_journal=False)
    paths = Paths(runtime_root=tmp_path)
    with reslock.operation_lock(paths, "source-txn-index", "adopt", "x"):
        r = _launch(launcher, LHPC_BUILD_LOCK_WAIT_S="0.4")
    assert r.returncode == 3 and "index busy" in r.stderr
    assert not marker.exists()


def test_detached_launcher_runs_when_no_journal_and_index_free(tmp_path):
    # The positive half of the two blocks above: the launcher production renders always carries
    # an index lock, so a launcher that blocked on the index path unconditionally would pass
    # every blocking test and never build anything.
    marker = tmp_path / "ran"
    launcher = _render_launcher(tmp_path, marker, with_journal=False)
    r = _launch(launcher, LHPC_BUILD_LOCK_WAIT_S="0.4")
    assert r.returncode == 0 and marker.exists()


def _dead_or_zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            st = fh.read()
        return st[st.rindex(")") + 2] in ("Z", "X", "x")
    except (OSError, ValueError):
        return True


@pytest.mark.slow
def test_build_launcher_step_timeout_kills_child_group(tmp_path):
    prog = ("import subprocess, sys, time\n"
            "c = subprocess.Popen(['sleep', '60'])\n"
            "open(sys.argv[1], 'w').write(str(c.pid))\n"
            "time.sleep(60)\n")
    steps = [{"argv": [sys.executable, "-c", prog, str(tmp_path / "childpid")]}]
    launcher = tmp_path / "l.py"
    launcher.write_text(commands.render_build_launcher(steps, str(tmp_path), str(tmp_path), []))
    t0 = time.time()
    r = _launch(launcher, LHPC_BUILD_STEP_TIMEOUT_S="0.6")
    assert r.returncode == 124 and "step timed out" in r.stderr
    assert time.time() - t0 < 10
    child = int((tmp_path / "childpid").read_text())
    for _ in range(60):
        if _dead_or_zombie(child):
            break
        time.sleep(0.1)
    assert _dead_or_zombie(child)                          # step's child killed with the group

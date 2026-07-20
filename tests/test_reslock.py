"""§12 — controller resource coordination locks: contention blocks, the holder is
named, canonical keys are stable, and a dead holder's lock is automatically free."""

import multiprocessing as mp
import time

import pytest

from lhpc.core import reslock
from lhpc.core.paths import Paths


def test_canonical_key_is_stable_and_safe():
    assert reslock.canonical_key("LoRaHAM.Radio/433") == reslock.canonical_key("loraham.radio-433")
    assert "/" not in reslock.canonical_key("a/b") and ".." not in reslock.canonical_key("..")


def test_second_acquire_is_blocked_and_names_holder(tmp_path):
    p = Paths(runtime_root=tmp_path)
    with reslock.operation_lock(p, "radio.433", "build", "daemon"):
        with pytest.raises(reslock.ResourceBusy) as ei:
            with reslock.operation_lock(p, "radio.433", "start", "kiss"):
                pass
        # the conflict diagnostic names resource, holder operation, and target
        assert ei.value.key == "radio.433"
        assert ei.value.holder.get("operation") == "build"
        assert ei.value.holder.get("target") == "daemon"


def test_lock_released_after_operation(tmp_path):
    p = Paths(runtime_root=tmp_path)
    with reslock.operation_lock(p, "radio.433", "build", "daemon"):
        pass
    # now free again
    with reslock.operation_lock(p, "radio.433", "start", "kiss"):
        assert reslock.read_owner(p, "radio.433")["operation"] == "start"
    assert reslock.read_owner(p, "radio.433") is None      # owner record cleared


def _hold(root, key, secs):
    from lhpc.core import reslock as rl
    from lhpc.core.paths import Paths as P
    with rl.operation_lock(P(runtime_root=root), key, "build", "daemon"):
        time.sleep(secs)


def test_dead_holder_lock_is_free(tmp_path):
    # A separate process holds the lock, then DIES -> the flock is auto-released by the
    # kernel, so a new acquisition succeeds (stale-lock recovery is intrinsic).
    p = Paths(runtime_root=tmp_path)
    # spawn (not fork): forking a multi-threaded pytest process raises a Py3.13 DeprecationWarning
    # (fork-in-threaded -> possible child deadlock). spawn re-execs a clean interpreter — no warning.
    proc = mp.get_context("spawn").Process(target=_hold, args=(tmp_path, "radio.868", 30))
    proc.start()
    try:
        # `spawn` re-execs a fresh interpreter, which on a loaded box (a full matrix run on a Pi)
        # can take well over the old 2 s budget. Falling through a too-short poll made the
        # assertion below test an UNHELD lock and fail spuriously — so wait longer and prove the
        # holder actually published before asserting anything about contention.
        for _ in range(1500):
            if reslock.read_owner(p, "radio.868"):
                break
            time.sleep(0.02)
        assert reslock.read_owner(p, "radio.868"), "holder process never claimed the lock"
        with pytest.raises(reslock.ResourceBusy):           # still held
            with reslock.operation_lock(p, "radio.868", "start", "x"):
                pass
        proc.terminate(); proc.join()
        # holder gone -> lock free
        with reslock.operation_lock(p, "radio.868", "start", "x"):
            pass
    finally:
        if proc.is_alive():
            proc.kill(); proc.join()


def test_lock_file_rejects_symlink_leaf(tmp_path):
    import os
    from lhpc.core.paths import PathContainmentError
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "state" / "locks").mkdir(parents=True)
    outside = tmp_path / "evil.lock"; outside.write_text("")
    os.symlink(outside, tmp_path / "state" / "locks" / "radio.433.lock")
    with pytest.raises((OSError, PathContainmentError)):
        with reslock.operation_lock(p, "radio.433", "build", "daemon"):
            pass


# --- §12 lifecycle dispatch lock (re-entrancy-safe) --------------------------

def test_concurrent_lifecycle_op_is_blocked(tmp_path):
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    # hold the daemon's lifecycle lock -> an external start must be refused (not race)
    with reslock.operation_lock(svc._paths, "lifecycle.daemon", "stop", "daemon"):
        res = svc.run_action("start", "daemon", apply=True)
    assert not res.ok and "Cannot start" in res.summary


def test_restart_does_not_self_deadlock(tmp_path):
    # restart holds lifecycle.<x> then calls stop()/start() DIRECTLY (bypassing the
    # dispatch lock) — it must complete, not hang, even though it re-enters lifecycle.
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.run_action("restart", "daemon", apply=True)   # returns (no deadlock)
    assert res is not None and hasattr(res, "ok")

"""§12 — controller resource coordination locks: contention blocks, the holder is
named, canonical keys are stable, a dead holder's lock is automatically free, and the
controller's same-process claims serialize instead of refusing each other."""

import multiprocessing as mp
import time

import pytest

from lhpc.core import reslock
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


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
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 0.2          # fast contention (default 5.0s just delays the refusal)
    # hold the daemon's lifecycle lock -> an external start must be refused (not race)
    with reslock.operation_lock(svc._paths, "lifecycle.daemon", "stop", "daemon"):
        res = svc.run_action("start", "daemon", apply=True)
    assert not res.ok and "Cannot start" in res.summary


# --- §12 same-process claims: serialize, the publication window, the bounded grace -------

def _hold_lock_unpublished(root: str, key: str) -> None:
    """Hold ONLY the flock, never publishing an owner record — the unidentifiable-holder state.
    Module-level so `spawn` can pickle it; `spawn` (not fork) avoids the Py3.13
    fork-in-threaded-process warning the suite gates on."""
    import fcntl
    import time as _t
    from lhpc.core import reslock, runtime_fs
    from lhpc.core.paths import Paths as _P
    paths = _P(runtime_root=__import__("pathlib").Path(root))
    lockfile = paths.under("state", "locks", reslock.canonical_key(key) + ".lock")
    fh = runtime_fs.open_lock(paths, lockfile)
    fcntl.flock(fh, fcntl.LOCK_EX)
    _t.sleep(60)


def _lock_is_held(svc, key: str) -> bool:
    """True when the flock is taken, independently of whether ownership was published."""
    import fcntl
    from lhpc.core import reslock, runtime_fs
    path = svc._paths.under("state", "locks", reslock.canonical_key(key) + ".lock")
    try:
        fh = runtime_fs.open_lock(svc._paths, path)
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def test_same_process_claim_waits_then_succeeds(tmp_path, monkeypatch):
    """Two overlapping controller ops in DIFFERENT threads of the SAME process that share a claim
    must SERIALIZE (wait), not fail with "your own stack is busy". A different-process holder
    still fails fast (covered by reslock's external-contention tests).

    This owns the OTHER branch of `_acquire_key` from the test below: here ownership IS published,
    so the contender identifies the holder as our own pid and enters the bounded retry loop. The
    holder therefore waits for that contention to be OBSERVED before releasing — with a fixed
    sleep instead, a contender that arrived after the holder had already let go would find the
    lock free, never wait at all, and still satisfy the assertion.
    """
    import contextlib
    import threading
    from lhpc.core import reslock

    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 3.0
    key = "claim.loraham.daemon-socket.433"
    held, released, contended = threading.Event(), threading.Event(), threading.Event()

    real_lock = reslock.operation_lock

    @contextlib.contextmanager
    def watch_busy(paths, k, op, target, *a, **kw):
        # Every refusal of THIS key is one turn of the contender's retry loop.
        try:
            with real_lock(paths, k, op, target, *a, **kw) as v:
                yield v
        except reslock.ResourceBusy:
            if reslock.canonical_key(k) == key:
                contended.set()
            raise

    monkeypatch.setattr(reslock, "operation_lock", watch_busy)

    def hold():
        with real_lock(svc._paths, key, "stop", "meshcom"):
            # `operation_lock` publishes the owner record before it yields, so the state is
            # already true here — assert it rather than polling for it. (An UNPUBLISHED owner is
            # a different branch, exercised by the test below.)
            assert reslock.read_owner(svc._paths, key), "operation_lock yielded without an owner"
            held.set()
            contended.wait(10.0)      # ...and hold until the contender has actually been refused
            released.set()

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5.0), "holder never published its ownership record"
        with contextlib.ExitStack() as st:
            svc._acquire_key(st, key, "start", "kiss")    # waits for the same-process holder
            assert contended.is_set(), "the claim was never contended — nothing was serialized"
            assert released.is_set()                      # proved it waited past the release
    finally:
        contended.set()                                   # never leave the holder parked
        t.join(10.0)


def test_same_process_claim_retries_while_ownership_is_unpublished(tmp_path, monkeypatch):
    """REGRESSION: `operation_lock` takes the flock and only THEN writes its `.owner` record. A
    second same-process thread arriving inside that window got a ResourceBusy whose holder was
    unidentifiable, was treated as an EXTERNAL conflict, and failed immediately instead of
    serializing — intermittently, and most often under load (i.e. exactly when two controller
    threads overlap). Here publication is deliberately delayed to make that window deterministic."""
    import threading, contextlib
    from lhpc.core import reslock, runtime_fs
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 3.0
    key = "claim.loraham.daemon-socket.433"
    flocked = threading.Event()
    publish_now = threading.Event()
    released = threading.Event()

    real_write_marker = runtime_fs.write_marker

    def slow_publish(paths, path, text, *a, **k):
        # Only the OWNER record of this key is delayed; every other marker write is untouched.
        if path.name.endswith(".owner"):
            flocked.set()
            publish_now.wait(5.0)
        return real_write_marker(paths, path, text, *a, **k)

    monkeypatch.setattr(runtime_fs, "write_marker", slow_publish)

    def hold():
        with reslock.operation_lock(svc._paths, key, "stop", "meshcom"):
            released.set()          # ordered BEFORE the flock is dropped, so no wait is needed

    # The production seam this test is ABOUT: reslock serializes taking the flock with publishing
    # the owner record on a per-key mutex (reslock._PUBLISH_LOCKS), so no other thread here can
    # ever see one without the other. Wrap that mutex so the test can observe the contender
    # BLOCKING on it — the exact moment the serialization is doing its job. Waiting for that
    # instead of sleeping is what makes this deterministic, and asserting it is what keeps the
    # test able to fail: if the serialization were removed the contender would not block here at
    # all, it would flock-fail against an unpublished owner and be refused as an external holder,
    # which is the defect.
    contending = threading.Event()

    class _WatchedLock:
        def __init__(self, inner):
            self._inner = inner

        def acquire(self, *a, **kw):
            if self._inner.locked():          # held by the publisher -> we are about to WAIT
                contending.set()
            return self._inner.acquire(*a, **kw)

        def release(self):
            return self._inner.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_exc):
            self.release()

    # via monkeypatch: the registry is process-global and must not leak to other tests.
    monkeypatch.setitem(reslock._PUBLISH_LOCKS, key, _WatchedLock(threading.Lock()))

    t = threading.Thread(target=hold); t.start()
    try:
        assert flocked.wait(5.0), "holder never reached the publication window"
        # The flock IS held and the owner record does NOT exist yet — the ambiguous state.
        assert reslock.read_owner(svc._paths, key) is None
        contender = {}
        def acquire():
            try:
                with contextlib.ExitStack() as st:
                    svc._acquire_key(st, key, "start", "kiss")
                    contender["ok"] = released.is_set()      # serialized behind the holder
            except reslock.ResourceBusy as exc:
                contender["busy"] = str(exc)
        c = threading.Thread(target=acquire, name="contender"); c.start()
        assert contending.wait(10.0), (
            "the contender never blocked on the publish mutex — acquire and publish are no "
            "longer serialized, so an overlapping op of OURS can see a flock with no owner "
            "record and be refused as an external holder")
        assert reslock.read_owner(svc._paths, key) is None    # still the ambiguous window
        publish_now.set()                                     # ownership becomes visible
        c.join(10.0)
    finally:
        publish_now.set()
        t.join(10.0)
    assert "busy" not in contender, f"retried window still reported busy: {contender}"
    assert contender.get("ok") is True, contender


def test_unknown_owner_still_fails_after_the_bounded_grace(tmp_path, monkeypatch):
    """An UNIDENTIFIABLE holder must not become a five-second stall: it is retried only for the
    short publication grace and then surfaces the typed ResourceBusy. Proven with a lock held by a
    real external process whose owner record never appears."""
    import contextlib, time
    import multiprocessing as mp
    import pytest as _pytest
    from lhpc.core import reslock
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 5.0
    key = "claim.loraham.daemon-socket.433"
    proc = mp.get_context("spawn").Process(target=_hold_lock_unpublished,
                                           args=(str(tmp_path), key))
    proc.start()
    try:
        for _ in range(500):                                 # wait for the flock, NOT the owner
            if _lock_is_held(svc, key):
                break
            time.sleep(0.02)
        assert _lock_is_held(svc, key), "external holder never took the lock"
        assert reslock.read_owner(svc._paths, key) is None   # deliberately never published
        started = time.monotonic()
        with _pytest.raises(reslock.ResourceBusy):
            with contextlib.ExitStack() as st:
                svc._acquire_key(st, key, "start", "kiss")
        waited = time.monotonic() - started
        # bounded by the grace, nowhere near the same-process budget
        assert waited < svc._SELF_LOCK_WAIT_S / 2, f"waited {waited:.2f}s — grace not bounded"
    finally:
        proc.terminate(); proc.join(10)
        if proc.is_alive():
            proc.kill(); proc.join()

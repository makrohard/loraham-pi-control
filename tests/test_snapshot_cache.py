"""build_snapshot is memoized WITHIN a request/operation (a page render assesses it ~15×) but is
never reused across an HTTP request or a mutation — so status is fast yet always current."""

from __future__ import annotations

from lhpc.adapters.web.app import create_app
from lhpc.core import status as statusmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _count_assessments(monkeypatch):
    n = []
    orig = statusmod.StatusProber.assess_stacks
    monkeypatch.setattr(statusmod.StatusProber, "assess_stacks",
                        lambda self, stacks: (n.append(1), orig(self, stacks))[1])
    return n


def test_render_assesses_the_snapshot_once_per_request(tmp_path, monkeypatch):
    # The Apps page calls build_snapshot ~15× (one per stack helper). The memo must collapse that
    # to a SINGLE assessment — this is the whole performance fix.
    n = _count_assessments(monkeypatch)
    c = create_app(lambda: _svc(tmp_path)).test_client()
    n.clear(); c.get("/stacks")
    assert len(n) == 1, f"one render must assess once, got {len(n)}"


def test_each_request_reassesses_fresh(tmp_path, monkeypatch):
    # before_request drops the cache, so a second request never serves the first request's snapshot.
    n = _count_assessments(monkeypatch)
    c = create_app(lambda: _svc(tmp_path)).test_client()
    n.clear(); c.get("/stacks"); c.get("/stacks"); c.get("/")
    assert len(n) == 3, f"each request reassesses exactly once, got {len(n)}"


def test_memo_returns_same_object_until_invalidated(tmp_path):
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    assert svc.build_snapshot() is a                 # memoized within the operation
    svc.invalidate_snapshot()
    assert svc.build_snapshot() is not a             # invalidated -> recompute


def test_fresh_bypasses_cache_and_refreshes_it(tmp_path):
    # The authoritative under-lock rechecks pass fresh=True and must NEVER get a cached snapshot.
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    b = svc.build_snapshot(fresh=True)
    assert b is not a                                # fresh forced a recompute
    assert svc.build_snapshot() is b                 # and refreshed the cache for later readers


def test_mutating_ops_drop_the_memo(tmp_path):
    # A public mutating entry must never let a later read serve a pre-mutation snapshot, even in
    # the same process (CLI sequences, an outer op reading after an inner public stop). Entry+exit
    # invalidation also covers refusal paths, so this holds regardless of the op's outcome.
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    svc.stop("kiss", apply=False)                    # traverses the decorated public entry
    assert svc.build_snapshot() is not a


def test_nested_public_stop_refreshes_the_outer_readers(tmp_path):
    # The owner-stop window inside start(): after an inner public stop returns, the outer op's next
    # build_snapshot() must recompute (the inner exit-invalidation is what restores the guarantee).
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    try:
        svc.stop("kiss", apply=True)                 # outcome irrelevant; finally invalidates
    except Exception:                                # noqa: BLE001 — harness has no processes
        pass
    assert svc.build_snapshot() is not a


def test_snapshot_memo_is_thread_local(tmp_path):
    # The shared ControllerService is hit by concurrent Waitress worker threads. The memo must be
    # thread-local: one thread's invalidation must NOT clobber another thread's cached snapshot, and
    # each thread computes its own. Sequenced with events so the interleaving is deterministic.
    import threading
    svc = _svc(tmp_path)
    r = {}
    a_built, b_done = threading.Event(), threading.Event()

    def thread_a():
        r["a1"] = svc.build_snapshot()          # A memoizes in A's thread-local
        a_built.set()
        b_done.wait(5)                          # ... while B builds + invalidates on its own thread
        r["a2"] = svc.build_snapshot()          # must return A's SAME object (B could not clobber it)

    def thread_b():
        a_built.wait(5)
        r["b1"] = svc.build_snapshot()          # B memoizes in B's own thread-local (distinct object)
        svc.invalidate_snapshot()               # clears ONLY B's memo
        b_done.set()

    ta, tb = threading.Thread(target=thread_a), threading.Thread(target=thread_b)
    ta.start(); tb.start(); ta.join(5); tb.join(5)

    assert r["a1"] is r["a2"]                    # A's memo survived B's invalidate -> thread-local
    assert r["b1"] is not r["a1"]               # each thread assessed its own snapshot

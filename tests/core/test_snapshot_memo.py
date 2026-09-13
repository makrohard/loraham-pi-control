"""The controller's snapshot memo and the per-request memo behind it: one status assessment per
operation or web request, thread-local so concurrent Waitress workers never share or clobber
one another, dropped by every mutating entry and by `fresh=True` — and the once-per-request
read contracts a render relies on (firewall status, listeners, stack configs, source lines)."""

from __future__ import annotations

import threading

import pytest

from lhpc.adapters.web.app import create_app
from lhpc.core import status as statusmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
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


def _count_calls(monkeypatch, owner, name):
    n = []
    orig = getattr(owner, name)
    monkeypatch.setattr(owner, name, lambda self, *a, **k: (n.append(1), orig(self, *a, **k))[1])
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
    res = svc.stop("kiss", apply=True)               # nothing runs here: a typed result, never a raise
    assert res.ok is True
    assert svc.build_snapshot() is not a


def test_snapshot_memo_is_thread_local(tmp_path):
    # The shared ControllerService is hit by concurrent Waitress worker threads. The memo must be
    # thread-local: one thread's invalidation must NOT clobber another thread's cached snapshot, and
    # each thread computes its own. Sequenced with events so the interleaving is deterministic.
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


# --- the render contract "once per request" ---------------------------------------------------------

def test_a_render_reads_firewall_status_and_listeners_once(tmp_path, monkeypatch):
    # A /stacks render once called firewall_status() 10–13× and tcp_listeners() once per
    # TCP endpoint; both are now render-wide reads passed down the existing seams.
    fw = _count_calls(monkeypatch, ControllerService, "firewall_status")
    lis = _count_calls(monkeypatch, FakeSystem, "tcp_listeners")
    c = create_app(lambda: _svc(tmp_path)).test_client()
    for path in ("/stacks", "/", "/stacks/meshcore/body"):
        fw.clear(); lis.clear()
        assert c.get(path).status_code == 200
        assert len(fw) <= 2, (path, len(fw))          # the render-wide read (+ the settings view)
        assert len(lis) <= 2, (path, len(lis))        # the snapshot assessment + ONE shared read


def test_stack_config_is_loaded_once_per_stack_and_band_per_request(tmp_path, monkeypatch):
    # 467 `load_stack_config` reads per render (every parameter row) collapse to one per
    # (stack, band) through the thread-local request memo.
    from lhpc.core import service_params as _sp
    n = []
    orig = _sp.load_stack_config
    monkeypatch.setattr(_sp, "load_stack_config",
                        lambda *a, **k: (n.append((a[1], a[2] if len(a) > 2 else k.get("band", ""))),
                                         orig(*a, **k))[1])
    svc = _svc(tmp_path)
    c = create_app(lambda: svc).test_client()
    n.clear(); assert c.get("/stacks").status_code == 200
    assert len(n) == len(set(n)), "the same (stack, band) file was read more than once in a render"
    assert len(n) <= 3 * len(svc.stacks())


def test_request_memo_is_thread_local_and_cleared_with_the_snapshot(tmp_path):
    svc = _svc(tmp_path)
    a = svc._request_memo(("k",), object)
    assert svc._request_memo(("k",), object) is a            # memoized within the request
    svc.invalidate_snapshot()
    b = svc._request_memo(("k",), object)
    assert b is not a                                          # dropped with the snapshot
    svc._invalidate_config()
    assert svc._request_memo(("k",), object) is not b          # dropped by a config write too
    r = {}
    def other():
        r["t"] = svc._request_memo(("k",), object)
    t = threading.Thread(target=other); t.start(); t.join(5)
    assert r["t"] is not svc._request_memo(("k",), object)    # per thread, never shared
    # a failing compute is never memoized
    calls = []
    def boom():
        calls.append(1)
        raise ValueError("x")
    for _ in range(2):
        with pytest.raises(ValueError):
            svc._request_memo(("boom",), boom)
    assert calls == [1, 1]


def test_consumed_source_lines_run_git_once_per_component_per_request(tmp_path):
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.build_requires and c.build_marker)
    before = len(svc._system.runner.calls)
    first = svc._consumed_source_lines(comp)
    n_git = len(svc._system.runner.calls) - before
    assert n_git >= 1 and svc._consumed_source_lines(comp) == first
    assert len(svc._system.runner.calls) - before == n_git      # the second read hit the memo
    svc.invalidate_snapshot()
    svc._consumed_source_lines(comp)
    assert len(svc._system.runner.calls) - before == 2 * n_git  # a new request recomputes


def _git_src(src, sha):
    a = str(src)
    return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
            ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}


def test_components_sharing_a_checkout_and_pin_are_probed_once_per_snapshot(tmp_path):
    # kiss-tnc and kiss-serial both build from src/loraham-kiss-tnc at the same pin: one snapshot asks git
    # about that checkout ONCE (two subprocesses), not once per component.
    from lhpc.core.model import SourceSpec
    from lhpc.core.status import StatusProber
    src = tmp_path / "src" / "loraham-kiss-tnc"
    fake = FakeSystem(paths={str(src), str(src / ".git")}, commands=_git_src(src, "a" * 40))
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=fake.system, paths=paths)
    comps = [c for s in svc.stacks() for c in s.components
             if c.source and c.source.path == "src/loraham-kiss-tnc"]
    assert len(comps) >= 2 and len({c.source.pin_commit for c in comps}) == 1   # precondition
    prober = StatusProber(fake.system, paths)
    snap = prober.assess_stacks(svc.stacks())
    git = [c for c in fake.calls if c[:3] == ["git", "-C", str(src)]]
    assert len(git) == 2, git                                   # status + describe, once
    heads = {snap.stacks[i].components[c.id].source_head for i, s in enumerate(svc.stacks())
             for c in comps if c.id in snap.stacks[i].components}
    assert heads == {"a" * 40}
    # a DIFFERENT pin on the same path is a different question -> its own probe
    other = SourceSpec(path="src/loraham-kiss-tnc", pin_commit="b" * 40)
    prober2 = StatusProber(fake.system, paths)
    fake.calls.clear()
    prober2._assess_source(type("C", (), {"id": "x", "source": other})())
    prober2._assess_source(type("C", (), {"id": "y", "source": comps[0].source})())
    assert len([c for c in fake.calls if c[:1] == ["git"]]) == 4


def test_a_restart_plan_assesses_the_snapshot_once(tmp_path, monkeypatch, set_call):
    # The combined restart plan (start leg + stop collateral) goes through the INNER planners: the
    # public entries would drop the snapshot and the request memo between the two legs and assess
    # everything twice inside one web Restart click.
    n = _count_assessments(monkeypatch)
    svc = _svc(tmp_path)
    set_call(svc)
    n.clear()
    plan = svc.restart("kiss", apply=False)
    assert plan.ok and "dependents" in plan.data
    assert len(n) <= 2, f"restart plan assessed {len(n)}×"          # one memoized (+ one fresh recheck)

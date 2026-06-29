"""Workstream C — verified stop and orchestration propagation: stop truth requires
process cessation AND ready-endpoint disappearance; markers clear only on verified
stop; restart/owner-stop/cascade propagate failures."""

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.services import ControllerService
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.model import Component, ComponentKind, EndpointSpec
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener


def _life(tmp_path, **kw):
    from lhpc.core.config import Config, OperatorConfig
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem(**kw).system)


# --- lifecycle: ready-endpoint cessation -------------------------------------

def test_ready_endpoint_gone_when_absent(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    gone, lingering = _life(tmp_path)._ready_endpoints_gone(comp)
    assert gone and not lingering


def test_ready_endpoint_lingering_detected(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="endpoint",
                     endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:9999", ready=True),))
    life = _life(tmp_path, listeners=[Listener(family="ipv4", ip="127.0.0.1", port=9999, inode=1)])
    gone, lingering = life._ready_endpoints_gone(comp)
    assert not gone and "127.0.0.1:9999" in lingering


# --- services: stop aggregation, markers, restart, owner-stop ----------------

class _FakeLife:
    """A lifecycle stand-in whose stop() returns a scripted outcome per component."""
    def __init__(self, outcome): self._outcome = outcome
    def stop(self, comp, band=None):
        return CompResult(component=comp.id, action="stop", outcome=self._outcome,
                          summary="scripted")


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _patch_life(monkeypatch, svc, outcome):
    monkeypatch.setattr(type(svc), "_lifecycle", lambda self: _FakeLife(outcome))


def test_stop_unverified_keeps_markers(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc._set_running_band("daemon", "433")
    svc.mark_interactive("daemon", "433")
    _patch_life(monkeypatch, svc, Outcome.STILL_RUNNING)
    res = svc.stop("daemon", apply=True)
    assert not res.ok
    assert svc._band_marker("daemon").exists()                # marker NOT cleared
    assert svc._interactive_marker("daemon").exists()


def test_stop_verified_clears_markers(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc._set_running_band("daemon", "433")
    _patch_life(monkeypatch, svc, Outcome.STOPPED)
    res = svc.stop("daemon", apply=True)
    assert res.ok
    assert not svc._band_marker("daemon").exists()            # cleared after verified stop


def test_endpoint_still_present_is_non_success(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _patch_life(monkeypatch, svc, Outcome.ENDPOINT_STILL_PRESENT)
    res = svc.stop("kiss", apply=True)
    assert not res.ok and any("endpoint_still_present" in d for d in res.details)


def test_restart_aborts_after_unverified_stop(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _patch_life(monkeypatch, svc, Outcome.STILL_RUNNING)
    res = svc.restart("daemon", apply=True)
    assert not res.ok and "aborted" in "\n".join(res.details).lower()


def test_failed_owner_stop_blocks_start(tmp_path, monkeypatch):
    # A conflicting owner that won't verify-stop must block the target launch.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "run_blockers",
                        lambda self, t, b="": [{"resource": "radio 433", "holder_stack": "meshtastic",
                                                "holder": "meshtastic"}])
    _patch_life(monkeypatch, svc, Outcome.STILL_RUNNING)
    res = svc.start("kiss", apply=True, stop_owners=True)
    assert not res.ok and "could not be verified stopped" in res.summary


# --- §8.3 web job-log selector hardening -------------------------------------

@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "a/b.log",
                                  "secrets.toml", "x.txt", "..", "evil\x00.log"])
def test_log_tail_rejects_unsafe_job_names(tmp_path, bad):
    svc = _svc(tmp_path)
    path, lines = svc.log_tail("daemon", 50, job=bad)
    assert path == "" and lines == []


def test_log_tail_rejects_symlink_leaf(tmp_path):
    import os
    svc = _svc(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    outside = tmp_path / "secret.log"
    outside.write_text("top secret\n")
    os.symlink(outside, logs / "evil.log")
    path, lines = svc.log_tail("daemon", 50, job="evil.log")
    assert path == "" and lines == []          # symlink leaf refused


def test_log_tail_reads_approved_log(tmp_path):
    svc = _svc(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "build-x.log").write_text("line1\nline2\n")
    path, lines = svc.log_tail("daemon", 50, job="build-x.log")
    assert path.endswith("build-x.log") and "line2" in lines


# --- P1.5 stop/restart carry typed results in ActionResult.results -----------

def test_stop_attaches_typed_results(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _patch_life(monkeypatch, svc, Outcome.ENDPOINT_STILL_PRESENT)
    res = svc.stop("kiss", apply=True)
    assert res.results and all(hasattr(r, "outcome") for r in res.results)
    assert any(r.outcome == Outcome.ENDPOINT_STILL_PRESENT for r in res.results)


def test_restart_aborted_preserves_stop_results(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _patch_life(monkeypatch, svc, Outcome.STILL_RUNNING)
    res = svc.restart("daemon", apply=True)
    assert not res.ok and res.results
    assert any(r.outcome == Outcome.STILL_RUNNING for r in res.results)


# --- daemon per-band stop orphans ONLY that band's dependents ----------------

def test_daemon_band_stop_orphans_only_that_bands_dependents(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from lhpc.core.model import RunState
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("kiss", "433")             # kiss (band-switchable) running on 433
    smap = {s.id: s for s in svc.stacks()}

    def fake_snapshot(_self):
        stacks = []
        for sid in ("daemon", "kiss", "meshcore"):   # all running; meshcore is 868
            s = smap[sid]
            comps = {c.id: SimpleNamespace(run_state=RunState.RUNNING) for c in s.components}
            stacks.append(SimpleNamespace(stack=s, components=comps))
        return SimpleNamespace(stacks=stacks)
    monkeypatch.setattr(type(svc), "build_snapshot", fake_snapshot)

    # Stopping the daemon's 433 instance orphans kiss (433) but NOT meshcore (868).
    assert svc.stop_dependents("daemon", bands={"433"}) == ["kiss"]
    assert svc.stop_dependents("daemon", bands={"868"}) == ["meshcore"]
    # Stopping the whole daemon (no band) orphans both.
    assert set(svc.stop_dependents("daemon")) == {"kiss", "meshcore"}

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
    # A conflicting owner that won't verify-stop must block the target launch: the owner stop is
    # the start's first destructive step, so the start ends there (a launch would reach the stand-in
    # lifecycle, which serves stops only, and fail this test) and names the owner that stayed up.
    svc = _svc(tmp_path)
    monkeypatch.setattr(type(svc), "run_blockers",
                        lambda self, t, b="", radio="": [{"resource": "radio 433",
                                                          "holder_stack": "meshtastic",
                                                          "holder": "meshtastic"}])
    _patch_life(monkeypatch, svc, Outcome.STILL_RUNNING)
    res = svc.start("kiss", apply=True, stop_owners=True)
    assert not res.ok and "could not be verified stopped" in res.summary
    assert any("'meshtastic' did not stop" in d for d in res.details), res.details


class _Launched(Exception):
    """Raised at the first step after the owner stops: the start went past them."""


def test_a_band_owner_on_the_daemon_is_stopped_per_band(tmp_path, monkeypatch):
    # Finding 140: kiss on 433 and meshcore on 868 run on the daemon; a reticulum start (868)
    # with stop_owners must stop meshcore and the daemon on 868 ONLY — a whole-daemon stop took kiss and
    # the 433 daemon down with it. Stubbed collaborators: the blocker scan (once the app owner is
    # stopped it sees the daemon's 868 band only) and the stops themselves (recorded).
    from lhpc.core.services import ActionResult, ControllerService
    svc = _svc(tmp_path)
    stops = []
    meshcore = {"resource": "loraham.radio.868", "holder_stack": "meshcore",
                "holder": "meshcore-node", "band": "868"}
    daemon = {"resource": "loraham.radio.868", "holder_stack": "daemon",
              "holder": "loraham-daemon", "band": "868"}
    monkeypatch.setattr(ControllerService, "run_blockers", lambda self, t, b="", radio="": (
        [daemon] if ("meshcore", "") in stops else [meshcore, daemon]))
    monkeypatch.setattr(ControllerService, "stop", lambda self, t, apply=False, band="", **k: (
        stops.append((t, band)), ActionResult(True, "stopped"))[1])

    def launched(self, b):
        raise _Launched()
    monkeypatch.setattr(ControllerService, "clear_daemon_feed", launched)
    with pytest.raises(_Launched):
        svc.start("reticulum", apply=True, stop_owners=True)
    assert stops == [("meshcore", ""), ("daemon", "868")]


def _owners_on_868(monkeypatch, stops, meshcore_ok=True):
    """kiss on 433 and meshcore on 868, both on the daemon; records the owner stops."""
    from lhpc.core.services import ActionResult, ControllerService
    meshcore = {"resource": "loraham.radio.868", "holder_stack": "meshcore",
                "holder": "meshcore-node", "band": "868"}
    daemon = {"resource": "loraham.radio.868", "holder_stack": "daemon",
              "holder": "loraham-daemon", "band": "868"}
    monkeypatch.setattr(ControllerService, "run_blockers", lambda self, t, b="", radio="": (
        [daemon] if ("meshcore", "") in stops else [meshcore, daemon]))
    monkeypatch.setattr(ControllerService, "stop", lambda self, t, apply=False, band="", **k: (
        stops.append((t, band)),
        ActionResult(meshcore_ok or t != "meshcore", "stopped" if meshcore_ok else "unverified",
                     details=["  [unverified] meshcore-node: still running"]))[1])


def test_an_owner_that_does_not_stop_leaves_the_daemon_alone(tmp_path, monkeypatch):
    # A half-stopped owner set must not also lose its daemon: refuse before the daemon stop, and
    # the stop's own lines say why.
    stops = []
    _owners_on_868(monkeypatch, stops, meshcore_ok=False)
    res = _svc(tmp_path).start("kiss", apply=True, stop_owners=True)
    assert stops == [("meshcore", "")] and not res.ok
    assert "  [unverified] meshcore-node: still running" in res.details


def test_a_dual_band_daemon_is_not_stopped_under_the_other_bands_clients(tmp_path, monkeypatch):
    # One daemon process serving 433 and 868: its per-band stop would take 433 and kiss with it.
    # Stubbed: the topology resolver (that process) and the dependents running on 433.
    from lhpc.core.services import ControllerService
    stops = []
    _owners_on_868(monkeypatch, stops)
    real = ControllerService._operation_bands
    monkeypatch.setattr(ControllerService, "_operation_bands", lambda self, t, b="", radio="", op="": (
        {"433", "868"} if (t, b, op) == ("daemon", "868", "stop") else real(self, t, b, radio, op)))
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: (
        ["kiss"] if bands == {"433"} else []))
    res = _svc(tmp_path).start("reticulum", apply=True, stop_owners=True)
    assert stops == [("meshcore", "")] and not res.ok
    tail = res.summary.split("kiss", 1)[1]        # after the client it would have stopped
    assert "meshcore" in tail and "stopped" in tail and "daemon" in tail and "not" in tail
    assert res.next_commands == ["lhpc stack stop kiss", "lhpc stack start reticulum --yes"]


def test_the_band_owner_refusal_names_the_band_scoped_daemon_stop(tmp_path, monkeypatch):
    # Without stop_owners the preflight refuses; its remedy for a daemon holder stops that band
    # only, never the bare `lhpc stack stop daemon` (finding 140).
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "run_blockers", lambda self, t, b="", radio="": [
        {"resource": "loraham.radio.868", "holder_stack": "daemon", "holder": "loraham-daemon",
         "band": "868"},
        {"resource": "loraham.radio.868", "holder_stack": "meshcore", "holder": "meshcore-node",
         "band": "868"}])
    res = svc.start("kiss", apply=True)
    assert not res.ok and res.next_commands == ["lhpc stack stop meshcore",
                                                "lhpc stack stop daemon --band 868"]
    s = res.summary
    assert "meshcore" in s and "868" in s and s.index("meshcore") < s.index("daemon")


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


# --- stop/restart carry typed results in ActionResult.results -----------

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


def test_blocked_daemon_stop_names_the_interactive_dependent_and_its_pid(tmp_path, monkeypatch):
    # An interactive dependent (chat, meshcore-cli) is the operator's OWN process — LHPC never
    # signals one it does not own — so "a dependent is still running" alone leaves nothing to act
    # on. The hint existed but its input list was only populated on the AUTOMATIC-stop path, so it
    # was empty for exactly the interactive case it was added for.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.model import RunState
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            st.run_state = RunState.RUNNING if cid == "loraham-chat" else RunState.STOPPED
            if cid == "loraham-chat":
                st.pids = [12334]
    svc.build_snapshot = lambda fresh=False: snap
    hints = svc._still_running_hints(["chat"])
    assert any("kill 12334" in h and "loraham-chat" in h for h in hints), hints
    # a component with no known pid is still NAMED, without inventing a kill line
    for ss in snap.stacks:
        for cid, st in ss.components.items():
            if cid == "loraham-chat":
                st.pids = []
    hints = svc._still_running_hints(["chat"])
    assert hints and "kill" not in hints[0] and "loraham-chat" in hints[0]
